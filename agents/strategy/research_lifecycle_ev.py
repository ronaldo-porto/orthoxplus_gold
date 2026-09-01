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

RESEARCH_LIFECYCLE_ENTRY_VERSION = "lifecycle_ev_v4_15_3"

LIFECYCLE_TAKER_PRIOR = 0.30
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


@dataclass(frozen=True)
class LifecycleCost:
    maker_entry_fee_bps: float
    expected_exit_fee_bps: float
    expected_cross_bps: float
    expected_slippage_bps: float
    holding_risk_bps: float
    taker_exit_probability: float

    @property
    def total_bps(self) -> float:
        return (
            self.maker_entry_fee_bps
            + self.expected_exit_fee_bps
            + self.expected_cross_bps
            + self.expected_slippage_bps
            + self.holding_risk_bps
        )

    def as_log(self) -> dict[str, float]:
        return {
            "lifecycle_entry_fee_bps": self.maker_entry_fee_bps,
            "lifecycle_exit_fee_bps": self.expected_exit_fee_bps,
            "lifecycle_cross_bps": self.expected_cross_bps,
            "lifecycle_slippage_bps": self.expected_slippage_bps,
            "lifecycle_holding_bps": self.holding_risk_bps,
            "lifecycle_taker_prob": self.taker_exit_probability,
            "lifecycle_cost_bps": self.total_bps,
        }


def lifecycle_entry_cost_bps(
    *,
    maker_fee_bps: float,
    taker_fee_bps: float,
    spread_bps: float,
    taker_exit_probability: float = 0.30,
    slippage_bps: float = 0.75,
    holding_risk_bps: float = 0.50,
) -> LifecycleCost:
    """Expected maker-entry + mixed maker/taker realization cost.

    Maker rebates are preserved. A taker exit pays its live fee and roughly half
    the spread plus slippage; a maker exit pays the live maker fee.  This is a
    bounded expectation, not a promise to cross the spread.
    """
    p = max(0.0, min(1.0, _finite(taker_exit_probability, 0.30)))
    maker = _finite(maker_fee_bps)
    taker = max(0.0, _finite(taker_fee_bps))
    spread = max(0.0, _finite(spread_bps))
    slip = max(0.0, _finite(slippage_bps))
    hold = max(0.0, _finite(holding_risk_bps))
    expected_exit_fee = (1.0 - p) * maker + p * taker
    expected_cross = p * 0.5 * spread
    expected_slip = p * slip
    return LifecycleCost(
        maker_entry_fee_bps=maker,
        expected_exit_fee_bps=expected_exit_fee,
        expected_cross_bps=expected_cross,
        expected_slippage_bps=expected_slip,
        holding_risk_bps=hold,
        taker_exit_probability=p,
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
