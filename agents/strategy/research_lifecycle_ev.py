# SPDX-License-Identifier: MIT
"""V4.15.3 bounded lifecycle entry economics.

LifecycleEV is trading economics only. Qualification / ONE_AWAY / TWO_AWAY /
coverage bonuses live in TotalScoreValue and are combined once at ranking.

The quote-time entry bar is expressed in the same units as trading_ev and is
capped so a high Taker posterior raises the hurdle without exploding past the
edge a typical Maker quote can earn.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

RESEARCH_LIFECYCLE_ENTRY_VERSION = "lifecycle_ev_v4_16_2"
LIFECYCLE_EV_MARGIN = 0.0

LIFECYCLE_TAKER_PRIOR = 0.30
LIFECYCLE_TAKER_PRIOR_STRENGTH = 8.0
LIFECYCLE_TAKER_MIN_SAMPLES = 4
TAKER_PENALTY_WEIGHT = 0.12
TAKER_COST_FLOOR = 0.20
TAKER_ENTRY_PENALTY_CAP = 0.06
HOLDING_PENALTY_CAP = 0.02
ADVERSE_PENALTY_CAP = 0.02
TOTAL_ENTRY_EV_CAP = 0.12
ONE_AWAY_ENTRY_MULT = 0.60
TWO_AWAY_ENTRY_MULT = 0.80
BPS_TO_EV_SCALE = 8.0


def _finite(value: float, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _bps_to_ev(bps: float, scale: float = BPS_TO_EV_SCALE) -> float:
    return math.tanh(max(0.0, _finite(bps)) / max(1e-6, _finite(scale, BPS_TO_EV_SCALE)))


def effective_taker_probability(
    *,
    prior: float = LIFECYCLE_TAKER_PRIOR,
    live: float | None = None,
    samples: int = 0,
    prior_strength: float = LIFECYCLE_TAKER_PRIOR_STRENGTH,
    min_samples: int = LIFECYCLE_TAKER_MIN_SAMPLES,
    cap: float = 0.90,
) -> float:
    """One posterior used by entry LifecycleEV.

    No samples → configured prior (never silent zero). Enough samples → the
    existing Bayesian shrinkage already used by Research realization.
    """
    from research_clean_authority import posterior_taker_exit_probability

    p0 = max(0.0, min(1.0, _finite(prior, LIFECYCLE_TAKER_PRIOR)))
    n = max(0, int(samples or 0))
    if live is None or n <= 0:
        taker_exits = 0
        maker_exits = 0
    else:
        p_live = max(0.0, min(1.0, _finite(live)))
        taker_exits = int(round(p_live * n))
        maker_exits = max(0, n - taker_exits)
    return posterior_taker_exit_probability(
        maker_exits=maker_exits,
        taker_exits=taker_exits,
        prior=p0,
        prior_strength=prior_strength,
        min_samples=min_samples,
        floor=p0,
        cap=max(p0, cap),
    )


def expected_future_taker_cost_bps(
    *,
    p_taker_effective: float,
    taker_fee_bps: float = 0.0,
    crossing_bps: float = 0.0,
    slippage_bps: float = 0.0,
    adverse_bps: float = 0.0,
) -> float:
    """p_effective × (taker fee + crossing + slippage + adverse).

    Maker rebate is excluded. Taker fee cannot go negative.
    """
    p = max(0.0, min(1.0, _finite(p_taker_effective)))
    taker_fee = max(0.0, _finite(taker_fee_bps))
    cross = max(0.0, _finite(crossing_bps))
    slip = max(0.0, _finite(slippage_bps))
    adverse = max(0.0, _finite(adverse_bps))
    return p * (taker_fee + cross + slip + adverse)


def lifecycle_is_executable(
    lifecycle_ev: float,
    *,
    margin: float = LIFECYCLE_EV_MARGIN,
) -> bool:
    """Single economics rule. Score bonuses never rescue a negative book."""
    return _finite(lifecycle_ev) >= _finite(margin, LIFECYCLE_EV_MARGIN)


@dataclass(frozen=True)
class LifecycleCost:
    maker_entry_fee_bps: float
    taker_fee_bps: float
    expected_exit_fee_bps: float
    expected_cross_bps: float
    expected_slippage_bps: float
    holding_risk_bps: float
    taker_exit_probability: float
    expected_future_taker_cost_bps: float

    @property
    def base_cost_bps(self) -> float:
        """LifecycleEV fee input: future Taker realization + holding.

        Maker rebate is excluded so it cannot subsidize TakerUtility.
        """
        return self.expected_future_taker_cost_bps + self.holding_risk_bps

    @property
    def total_bps(self) -> float:
        # Alias used by the entry scorer. Intentionally excludes maker rebate.
        return self.base_cost_bps

    def as_log(self) -> dict[str, float]:
        return {
            "lifecycle_entry_fee_bps": self.maker_entry_fee_bps,
            "lifecycle_taker_fee_bps": self.taker_fee_bps,
            "lifecycle_exit_fee_bps": self.expected_exit_fee_bps,
            "lifecycle_cross_bps": self.expected_cross_bps,
            "lifecycle_slippage_bps": self.expected_slippage_bps,
            "lifecycle_holding_bps": self.holding_risk_bps,
            "lifecycle_taker_prob": self.taker_exit_probability,
            "expected_future_taker_cost_bps": self.expected_future_taker_cost_bps,
            "lifecycle_cost_bps": self.total_bps,
        }


def lifecycle_entry_cost_bps(
    *,
    maker_fee_bps: float,
    taker_fee_bps: float,
    spread_bps: float,
    taker_exit_probability: float = LIFECYCLE_TAKER_PRIOR,
    slippage_bps: float = 0.75,
    holding_risk_bps: float = 0.50,
) -> LifecycleCost:
    """Maker-entry fee is recorded, not mixed into LifecycleEV.

    Expected future Taker cost is ``p_effective × (taker fee + half-spread +
    slippage)``. A missing/None probability falls back to the configured prior,
    never a silent zero.
    """
    if taker_exit_probability is None:
        p = LIFECYCLE_TAKER_PRIOR
    else:
        p = max(0.0, min(1.0, _finite(taker_exit_probability, LIFECYCLE_TAKER_PRIOR)))
    maker = _finite(maker_fee_bps)
    taker = max(0.0, _finite(taker_fee_bps))
    spread = max(0.0, _finite(spread_bps))
    slip = max(0.0, _finite(slippage_bps))
    hold = max(0.0, _finite(holding_risk_bps))
    expected_exit_fee = p * taker
    expected_cross = p * 0.5 * spread
    expected_slip = p * slip
    future = expected_future_taker_cost_bps(
        p_taker_effective=p,
        taker_fee_bps=taker,
        crossing_bps=0.5 * spread,
        slippage_bps=slip,
    )
    return LifecycleCost(
        maker_entry_fee_bps=maker,
        taker_fee_bps=taker,
        expected_exit_fee_bps=expected_exit_fee,
        expected_cross_bps=expected_cross,
        expected_slippage_bps=expected_slip,
        holding_risk_bps=hold,
        taker_exit_probability=p,
        expected_future_taker_cost_bps=future,
    )


@dataclass(frozen=True)
class RequiredEntryEV:
    base_entry_floor: float
    taker_prob_live: float
    taker_prob_prior: float
    taker_prob_excess: float
    expected_taker_cost: float
    raw_taker_penalty: float
    capped_taker_penalty: float
    adverse_penalty: float
    holding_penalty: float
    latency_penalty: float
    crossing_penalty: float
    completion_multiplier: float
    required_entry_ev: float

    def as_log(self) -> dict[str, float]:
        return {
            "base_entry_floor": self.base_entry_floor,
            "taker_prob_live": self.taker_prob_live,
            "taker_prob_prior": self.taker_prob_prior,
            "taker_prob_excess": self.taker_prob_excess,
            "expected_taker_cost": self.expected_taker_cost,
            "raw_taker_penalty": self.raw_taker_penalty,
            "capped_taker_penalty": self.capped_taker_penalty,
            "adverse_penalty": self.adverse_penalty,
            "holding_penalty": self.holding_penalty,
            "latency_penalty": self.latency_penalty,
            "crossing_penalty": self.crossing_penalty,
            "completion_multiplier": self.completion_multiplier,
            "required_entry_ev": self.required_entry_ev,
        }


def completion_entry_multiplier(
    *,
    observations_remaining: int,
    projected_completion_healthy: bool | None,
    trading_ev: float,
    recent_realized_pnl: float | None = None,
    one_away_mult: float = ONE_AWAY_ENTRY_MULT,
    two_away_mult: float = TWO_AWAY_ENTRY_MULT,
) -> float:
    """Discount the entry bar for healthy incomplete books with non-negative EV.

    Coverage (remaining 0 or >= 3) keeps multiplier 1.0. Negative trading_ev
    never receives a completion subsidy.
    """
    remaining = max(0, int(observations_remaining))
    if remaining not in {1, 2}:
        return 1.0
    if projected_completion_healthy is not True:
        return 1.0
    if _finite(trading_ev) < 0.0:
        return 1.0
    if recent_realized_pnl is not None and _finite(recent_realized_pnl) <= 0.0:
        return 1.0
    if remaining == 1:
        return max(0.0, min(1.0, _finite(one_away_mult, ONE_AWAY_ENTRY_MULT)))
    return max(0.0, min(1.0, _finite(two_away_mult, TWO_AWAY_ENTRY_MULT)))


def compute_required_entry_ev(
    *,
    base_required_ev: float = 0.0,
    taker_exit_probability: float = 0.30,
    expected_cross_bps: float = 0.0,
    holding_risk_bps: float = 0.0,
    adverse_selection_cost: float = 0.0,
    latency_cost: float = 0.0,
    taker_prob_prior: float = LIFECYCLE_TAKER_PRIOR,
    taker_penalty_weight: float = TAKER_PENALTY_WEIGHT,
    taker_cost_floor: float = TAKER_COST_FLOOR,
    taker_penalty_cap: float = TAKER_ENTRY_PENALTY_CAP,
    holding_penalty_cap: float = HOLDING_PENALTY_CAP,
    adverse_penalty_cap: float = ADVERSE_PENALTY_CAP,
    total_entry_ev_cap: float = TOTAL_ENTRY_EV_CAP,
    crossing_scale_bps: float = BPS_TO_EV_SCALE,
    holding_scale_bps: float = BPS_TO_EV_SCALE,
    completion_multiplier: float = 1.0,
) -> RequiredEntryEV:
    """Bounded Maker-entry hurdle in trading_ev units.

    High Taker probability raises the bar through excess-over-prior times the
    expected taker-cross cost. Crossing, hold, and adverse terms are each
    capped, then the sum is capped, then a completion multiplier may discount
    a healthy ONE_AWAY / TWO_AWAY book.
    """
    del latency_cost  # already subtracted inside LifecycleEV; do not double-count
    p = max(0.0, min(1.0, _finite(taker_exit_probability, LIFECYCLE_TAKER_PRIOR)))
    prior = max(0.0, min(1.0, _finite(taker_prob_prior, LIFECYCLE_TAKER_PRIOR)))
    excess = max(0.0, p - prior)
    cross_ev = _bps_to_ev(expected_cross_bps, crossing_scale_bps)
    expected_taker_cost = min(1.0, max(0.0, _finite(taker_cost_floor, TAKER_COST_FLOOR)) + cross_ev)
    weight = max(0.0, _finite(taker_penalty_weight, TAKER_PENALTY_WEIGHT))
    raw_taker = excess * expected_taker_cost * weight
    taker_cap = max(0.0, _finite(taker_penalty_cap, TAKER_ENTRY_PENALTY_CAP))
    capped_taker = min(taker_cap, raw_taker)
    holding = min(
        max(0.0, _finite(holding_penalty_cap, HOLDING_PENALTY_CAP)),
        _bps_to_ev(holding_risk_bps, holding_scale_bps),
    )
    adverse = min(
        max(0.0, _finite(adverse_penalty_cap, ADVERSE_PENALTY_CAP)),
        max(0.0, _finite(adverse_selection_cost)),
    )
    base = max(0.0, _finite(base_required_ev))
    total_cap = max(0.0, _finite(total_entry_ev_cap, TOTAL_ENTRY_EV_CAP))
    uncapped = base + capped_taker + holding + adverse
    capped_total = min(total_cap, uncapped)
    mult = max(0.0, min(1.0, _finite(completion_multiplier, 1.0)))
    return RequiredEntryEV(
        base_entry_floor=base,
        taker_prob_live=p,
        taker_prob_prior=prior,
        taker_prob_excess=excess,
        expected_taker_cost=expected_taker_cost,
        raw_taker_penalty=raw_taker,
        capped_taker_penalty=capped_taker,
        adverse_penalty=adverse,
        holding_penalty=holding,
        latency_penalty=0.0,
        crossing_penalty=cross_ev,
        completion_multiplier=mult,
        required_entry_ev=capped_total * mult,
    )


def required_entry_ev(
    *,
    base_required_ev: float = 0.0,
    taker_exit_probability: float = 0.30,
    expected_cross_bps: float = 0.0,
    holding_risk_bps: float = 0.0,
    adverse_selection_cost: float = 0.0,
    taker_penalty_weight: float = TAKER_PENALTY_WEIGHT,
    crossing_scale_bps: float = BPS_TO_EV_SCALE,
    holding_scale_bps: float = BPS_TO_EV_SCALE,
    completion_multiplier: float = 1.0,
) -> float:
    """Float wrapper around :func:`compute_required_entry_ev` for old callers."""
    return compute_required_entry_ev(
        base_required_ev=base_required_ev,
        taker_exit_probability=taker_exit_probability,
        expected_cross_bps=expected_cross_bps,
        holding_risk_bps=holding_risk_bps,
        adverse_selection_cost=adverse_selection_cost,
        taker_penalty_weight=taker_penalty_weight,
        crossing_scale_bps=crossing_scale_bps,
        holding_scale_bps=holding_scale_bps,
        completion_multiplier=completion_multiplier,
    ).required_entry_ev
