# SPDX-License-Identifier: MIT
"""Fill-hazard maker-vs-taker exit values.

Preserves the existing FillHazardModel. Uses its P(any fill),
P(actionable fill), and P(dust) with shrinkage when samples are
sparse.

    ExpectedMakerExitValue
        = P(fill before risk horizon) * maker_profit
        - expected_holding_cost_while_waiting

    ExpectedTakerExitValue
        = immediate_realization_value - taker_cost

That comparison is the primary maker-vs-taker decision. V2 additionally
penalizes stale WAIT value after repeated failed maker exits / aged inventory.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from research_fill_hazard import HazardPrediction

EXIT_HAZARD_EV_VERSION = "exit_hazard_ev_v2"

REASON_MAKER_EV = "MAKER_EXIT_EV"
REASON_TAKER_EV = "TAKER_EXIT_EV"

PRIOR_ANY = 0.12
PRIOR_ACTIONABLE_GIVEN_FILL = 0.55
PRIOR_DUST_GIVEN_FILL = 0.25
DEFAULT_MIN_SAMPLES = 12
DEFAULT_PRIOR_STRENGTH = 8.0
DUST_HAIRCUT_BPS = 4.0


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _clip01(value: Any) -> float:
    return max(0.0, min(1.0, _finite(value)))


def _blend(observed: float, prior: float, samples: int, min_samples: int, strength: float) -> float:
    n = max(0, int(samples))
    if n >= max(1, int(min_samples)):
        return _clip01(observed)
    k = max(0.0, float(strength))
    return _clip01((n * _clip01(observed) + k * _clip01(prior)) / max(n + k, 1e-9))


@dataclass(frozen=True)
class ExitHazardProbs:
    any_fill: float
    actionable_fill: float
    dust: float
    fill_before_horizon: float
    usable: bool
    n_at_risk: int
    source: str

    def as_log(self) -> dict[str, Any]:
        return {
            "exit_p_any": self.any_fill,
            "exit_p_actionable": self.actionable_fill,
            "exit_p_dust": self.dust,
            "exit_p_fill_horizon": self.fill_before_horizon,
            "exit_hazard_usable": int(bool(self.usable)),
            "exit_hazard_n": self.n_at_risk,
            "exit_hazard_source": self.source,
        }


@dataclass(frozen=True)
class MakerTakerExitEV:
    expected_maker_exit_value: float
    expected_taker_exit_value: float
    maker_profit: float
    holding_cost_while_waiting: float
    immediate_realization_value: float
    taker_cost: float
    p_fill_horizon: float
    probs: ExitHazardProbs
    prefer_taker: bool
    reason: str
    wait_penalty_bps: float = 0.0
    failed_exit_count: int = 0
    inventory_age: float = 0.0

    def as_log(self) -> dict[str, Any]:
        payload = {
            "exit_hazard_ev_version": EXIT_HAZARD_EV_VERSION,
            "expected_maker_exit_value": self.expected_maker_exit_value,
            "expected_taker_exit_value": self.expected_taker_exit_value,
            "maker_profit": self.maker_profit,
            "holding_cost_while_waiting": self.holding_cost_while_waiting,
            "immediate_realization_value": self.immediate_realization_value,
            "exit_taker_cost": self.taker_cost,
            "prefer_taker": int(bool(self.prefer_taker)),
            "exit_ev_reason": self.reason,
            "wait_penalty_bps": self.wait_penalty_bps,
            "failed_exit_count": int(self.failed_exit_count),
            "time_since_first_exit_attempt": self.inventory_age,
        }
        payload.update(self.probs.as_log())
        return payload


def shrink_exit_hazard(
    prediction: HazardPrediction | None,
    *,
    scalar_fill: float | None = None,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    prior_strength: float = DEFAULT_PRIOR_STRENGTH,
    prior_any: float = PRIOR_ANY,
    prior_actionable_given_fill: float = PRIOR_ACTIONABLE_GIVEN_FILL,
    prior_dust_given_fill: float = PRIOR_DUST_GIVEN_FILL,
) -> ExitHazardProbs:
    """Shrink P(any)/P(actionable)/P(dust) toward priors until min samples."""
    prior_act = _clip01(prior_any) * _clip01(prior_actionable_given_fill)
    prior_dust = _clip01(prior_any) * _clip01(prior_dust_given_fill)
    if prediction is None:
        p_any = _clip01(scalar_fill) if scalar_fill is not None else _clip01(prior_any)
        if scalar_fill is None:
            return ExitHazardProbs(
                any_fill=_clip01(prior_any),
                actionable_fill=prior_act,
                dust=prior_dust,
                fill_before_horizon=_clip01(prior_any),
                usable=False,
                n_at_risk=0,
                source="fallback",
            )
        return ExitHazardProbs(
            any_fill=p_any,
            actionable_fill=p_any * _clip01(prior_actionable_given_fill),
            dust=p_any * _clip01(prior_dust_given_fill),
            fill_before_horizon=p_any,
            usable=False,
            n_at_risk=0,
            source="scalar",
        )

    n = max(0, int(getattr(prediction, "n_at_risk", 0) or 0))
    usable = bool(getattr(prediction, "usable", False))
    p_any = _clip01(getattr(prediction, "any_fill", prior_any))
    p_act = _clip01(getattr(prediction, "actionable_fill", prior_act))
    p_dust = _clip01(getattr(prediction, "dust", prior_dust))
    remaining = _finite(getattr(prediction, "remaining_any_fill", 0.0), 0.0)
    p_h = remaining if remaining > 0.0 else p_any
    if (not usable) or n < max(1, int(min_samples)):
        p_any = _blend(p_any, prior_any, n, min_samples, prior_strength)
        p_act = _blend(p_act, prior_act, n, min_samples, prior_strength)
        p_dust = _blend(p_dust, prior_dust, n, min_samples, prior_strength)
        p_h = _blend(p_h, prior_any, n, min_samples, prior_strength)
        source = "shrunk" if n > 0 else str(getattr(prediction, "source", "fallback") or "fallback")
        usable = False
    else:
        source = str(getattr(prediction, "source", "cell") or "cell")
    cap = max(p_any, 1e-9)
    if p_act + p_dust > cap:
        scale = cap / (p_act + p_dust)
        p_act *= scale
        p_dust *= scale
    return ExitHazardProbs(
        any_fill=p_any,
        actionable_fill=p_act,
        dust=p_dust,
        fill_before_horizon=_clip01(p_h),
        usable=usable,
        n_at_risk=n,
        source=source,
    )


def failed_exit_wait_penalty_bps(
    *,
    failed_exit_count: int = 0,
    inventory_age: float = 0.0,
    failed_exit_penalty_bps: float = 0.75,
    age_penalty_bps_per_tick: float = 0.03,
    grace_age_ticks: float = 8.0,
    max_penalty_bps: float = 12.0,
) -> float:
    """Bounded cost of repeatedly waiting for a maker realization.

    The first few ticks remain a normal maker opportunity window.  After that,
    repeated failed exits and position age reduce WAIT EV so stale inventory is
    not favored simply because the nominal maker edge is still positive.
    """
    failures = max(0, int(failed_exit_count))
    age = max(0.0, _finite(inventory_age))
    age_excess = max(0.0, age - max(0.0, _finite(grace_age_ticks)))
    penalty = (
        failures * max(0.0, _finite(failed_exit_penalty_bps))
        + age_excess * max(0.0, _finite(age_penalty_bps_per_tick))
    )
    return min(max(0.0, _finite(max_penalty_bps, 12.0)), max(0.0, penalty))


def expected_maker_exit_value(
    *,
    p_fill_horizon: float,
    maker_profit: float,
    holding_cost: float,
) -> float:
    p = _clip01(p_fill_horizon)
    wait = (1.0 - p) * max(0.0, _finite(holding_cost))
    return p * _finite(maker_profit) - wait


def expected_taker_exit_value(
    *,
    immediate_realization_value: float,
    taker_cost: float,
) -> float:
    return _finite(immediate_realization_value) - max(0.0, _finite(taker_cost))


def maker_profit_from_hazard(
    *,
    base_maker_profit: float,
    probs: ExitHazardProbs,
    dust_haircut_bps: float = DUST_HAIRCUT_BPS,
) -> float:
    """Actionable fills keep maker profit; dust fills are haircut."""
    p_any = max(probs.any_fill, 1e-9)
    quality = _clip01(probs.actionable_fill / p_any)
    dust_frac = _clip01(probs.dust / p_any)
    return _finite(base_maker_profit) * quality - max(0.0, _finite(dust_haircut_bps)) * dust_frac


def compare_maker_taker_exit(
    *,
    prediction: HazardPrediction | None = None,
    scalar_fill: float | None = None,
    maker_profit: float,
    holding_cost: float,
    immediate_realization_value: float,
    taker_cost: float,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    dust_haircut_bps: float = DUST_HAIRCUT_BPS,
    failed_exit_count: int = 0,
    inventory_age: float = 0.0,
    failed_exit_penalty_bps: float = 0.75,
    age_penalty_bps_per_tick: float = 0.03,
    max_wait_penalty_bps: float = 12.0,
) -> MakerTakerExitEV:
    probs = shrink_exit_hazard(
        prediction, scalar_fill=scalar_fill, min_samples=min_samples,
    )
    profit = maker_profit_from_hazard(
        base_maker_profit=maker_profit,
        probs=probs,
        dust_haircut_bps=dust_haircut_bps,
    )
    maker_ev = expected_maker_exit_value(
        p_fill_horizon=probs.fill_before_horizon,
        maker_profit=profit,
        holding_cost=holding_cost,
    )
    wait_penalty = failed_exit_wait_penalty_bps(
        failed_exit_count=failed_exit_count,
        inventory_age=inventory_age,
        failed_exit_penalty_bps=failed_exit_penalty_bps,
        age_penalty_bps_per_tick=age_penalty_bps_per_tick,
        max_penalty_bps=max_wait_penalty_bps,
    )
    maker_ev -= wait_penalty
    taker_ev = expected_taker_exit_value(
        immediate_realization_value=immediate_realization_value,
        taker_cost=taker_cost,
    )
    prefer = taker_ev > maker_ev + 1e-12
    return MakerTakerExitEV(
        expected_maker_exit_value=maker_ev,
        expected_taker_exit_value=taker_ev,
        maker_profit=profit,
        holding_cost_while_waiting=(1.0 - probs.fill_before_horizon) * max(0.0, _finite(holding_cost)),
        immediate_realization_value=_finite(immediate_realization_value),
        taker_cost=max(0.0, _finite(taker_cost)),
        p_fill_horizon=probs.fill_before_horizon,
        probs=probs,
        prefer_taker=prefer,
        reason=REASON_TAKER_EV if prefer else REASON_MAKER_EV,
        wait_penalty_bps=wait_penalty,
        failed_exit_count=max(0, int(failed_exit_count)),
        inventory_age=max(0.0, _finite(inventory_age)),
    )
