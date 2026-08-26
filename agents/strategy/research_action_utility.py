# SPDX-License-Identifier: MIT
"""SN79 action utility for hybrid maker/taker realization.

This module deliberately optimizes *combined* subnet utility rather than
standalone execution PnL only.  A taker close may be rational when a small
execution concession buys materially faster round-trip completion, Kappa
qualification, inventory/capital release, and lower downside exposure.

The policy is still bounded:
- catastrophic hard risk may override utility;
- normal-risk score subsidy has a configurable maximum negative net-PnL floor;
- maker wait value includes fill probability and expected maker economics;
- all non-PnL terms are normalized and bounded before weighting.

Pure functions only so it can be unit-tested without bittensor/TAOS runtime.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

SN79_ACTION_UTILITY_VERSION = "sn79_action_utility_v1"

REASON_SN79_TAKER = "TAKER_SN79_UTILITY"
REASON_SN79_WAIT = "MAKER_SN79_UTILITY"
REASON_SN79_LOSS_FLOOR = "TAKER_SN79_LOSS_FLOOR"
REASON_SN79_MARGIN = "TAKER_SN79_MARGIN"


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


def _tanh_signed(value: Any, scale: float) -> float:
    return math.tanh(_finite(value) / max(1e-9, float(scale)))


def kappa_completion_value(observations_remaining: int, required_observations: int) -> float:
    """Bounded value of completing another realized observation now."""
    remaining = max(0, int(observations_remaining))
    required = max(1, int(required_observations))
    if remaining <= 0:
        return 0.0
    if remaining == 1:
        return 1.0
    if remaining == 2:
        return 0.65
    if remaining >= required:
        # Uncovered/cold book: first useful realization has breadth value.
        return 0.35
    return max(0.20, min(0.55, 1.0 / float(remaining)))


def coverage_redeploy_value(observations_remaining: int, required_observations: int) -> float:
    """Value of freeing inventory capacity so coverage can rotate to new books."""
    remaining = max(0, int(observations_remaining))
    required = max(1, int(required_observations))
    if remaining >= required:
        return 1.0
    if remaining > 0:
        return 0.65
    return 0.30


@dataclass(frozen=True)
class SN79ActionUtilityDecision:
    take: bool
    reason: str
    taker_utility: float
    wait_utility: float
    utility_margin: float
    taker_net_pnl_bps: float
    maker_expected_pnl_bps: float
    pnl_utility_taker: float
    pnl_utility_wait: float
    round_trip_value: float
    kappa_value: float
    coverage_value: float
    capital_release_value: float
    risk_reduction_value: float
    velocity_value: float
    downside_penalty: float
    p_maker_fill_horizon: float
    max_score_subsidy_loss_bps: float
    recommended_qty_frac: float

    def as_log(self) -> dict[str, Any]:
        return {
            "sn79_action_utility_version": SN79_ACTION_UTILITY_VERSION,
            "sn79_take": int(bool(self.take)),
            "sn79_reason": self.reason,
            "sn79_taker_utility": self.taker_utility,
            "sn79_wait_utility": self.wait_utility,
            "sn79_utility_margin": self.utility_margin,
            "sn79_taker_net_pnl_bps": self.taker_net_pnl_bps,
            "sn79_maker_expected_pnl_bps": self.maker_expected_pnl_bps,
            "sn79_pnl_utility_taker": self.pnl_utility_taker,
            "sn79_pnl_utility_wait": self.pnl_utility_wait,
            "sn79_round_trip_value": self.round_trip_value,
            "sn79_kappa_value": self.kappa_value,
            "sn79_coverage_value": self.coverage_value,
            "sn79_capital_release_value": self.capital_release_value,
            "sn79_risk_reduction_value": self.risk_reduction_value,
            "sn79_velocity_value": self.velocity_value,
            "sn79_downside_penalty": self.downside_penalty,
            "sn79_p_maker_fill_horizon": self.p_maker_fill_horizon,
            "sn79_max_score_subsidy_loss_bps": self.max_score_subsidy_loss_bps,
            "sn79_recommended_qty_frac": self.recommended_qty_frac,
        }


def evaluate_sn79_action_utility(
    *,
    taker_net_pnl_bps: float,
    maker_expected_pnl_bps: float,
    p_maker_fill_horizon: float,
    observations_remaining: int,
    required_observations: int = 3,
    inventory_ratio: float = 0.0,
    holding_cost_bps: float = 0.0,
    exit_urgency: float = 0.0,
    volume_cap_headroom: float = 1.0,
    pnl_scale_bps: float = 8.0,
    pnl_weight: float = 1.0,
    round_trip_weight: float = 0.30,
    kappa_weight: float = 0.35,
    coverage_weight: float = 0.15,
    capital_release_weight: float = 0.15,
    risk_reduction_weight: float = 0.20,
    velocity_weight: float = 0.25,
    downside_weight: float = 0.45,
    min_utility_margin: float = 0.03,
    max_score_subsidy_loss_bps: float = -2.0,
) -> SN79ActionUtilityDecision:
    """Compare immediate taker realization against waiting for maker exit.

    PnL remains the anchor, but subnet-value terms reward a close that converts
    open inventory into realized round-trip/Kappa activity quickly.  The maker
    branch receives the same score terms discounted by its probability of
    filling before the risk horizon, so this is a speed-aware comparison rather
    than a blanket taker bonus.
    """
    scale = max(1e-6, float(pnl_scale_bps))
    taker_net = _finite(taker_net_pnl_bps)
    maker_pnl = _finite(maker_expected_pnl_bps)
    p_fill = _clip01(p_maker_fill_horizon)
    inv = _clip01(abs(_finite(inventory_ratio)))
    urgency = _clip01(exit_urgency)
    headroom = _clip01(volume_cap_headroom)
    holding = max(0.0, _finite(holding_cost_bps))

    rt_value = 1.0  # any genuine inventory-reducing close contributes RT turnover
    kappa_value = kappa_completion_value(observations_remaining, required_observations)
    coverage_value = coverage_redeploy_value(observations_remaining, required_observations)
    capital_release = _clip01(0.70 * inv + 0.30 * (1.0 - headroom))
    risk_reduction = _clip01(math.tanh(holding / scale))
    # Immediate completion is most valuable when maker fill is uncertain and
    # urgency is rising. This is the explicit round-trip/score-velocity term.
    velocity_value = _clip01((1.0 - p_fill) * (0.45 + 0.55 * urgency))

    pnl_taker = _tanh_signed(taker_net, scale)
    pnl_wait = _tanh_signed(maker_pnl, scale)

    # Extra downside penalty prevents score utility from routinely subsidizing
    # meaningfully bad exits. A small negative close can still win if it buys
    # enough RT/Kappa/velocity value and remains inside the hard subsidy floor.
    downside = 0.0
    if taker_net < 0.0:
        downside = min(1.5, abs(taker_net) / scale)

    immediate_score = (
        max(0.0, float(round_trip_weight)) * rt_value
        + max(0.0, float(kappa_weight)) * kappa_value
        + max(0.0, float(coverage_weight)) * coverage_value
        + max(0.0, float(capital_release_weight)) * capital_release
        + max(0.0, float(risk_reduction_weight)) * risk_reduction
        + max(0.0, float(velocity_weight)) * velocity_value
    )
    # Waiting can eventually obtain RT/Kappa value too, but only with maker-fill
    # probability. Capital/risk release are likewise delayed.
    wait_score = p_fill * (
        max(0.0, float(round_trip_weight)) * rt_value
        + max(0.0, float(kappa_weight)) * kappa_value
        + max(0.0, float(coverage_weight)) * coverage_value
        + max(0.0, float(capital_release_weight)) * capital_release
        + max(0.0, float(risk_reduction_weight)) * risk_reduction
    )

    taker_utility = (
        max(0.0, float(pnl_weight)) * pnl_taker
        + immediate_score
        - max(0.0, float(downside_weight)) * downside
    )
    wait_utility = max(0.0, float(pnl_weight)) * pnl_wait + wait_score
    margin = taker_utility - wait_utility

    hard_floor = float(max_score_subsidy_loss_bps)
    loss_floor_ok = taker_net + 1e-12 >= hard_floor
    margin_ok = margin > max(0.0, float(min_utility_margin)) + 1e-12
    take = bool(loss_floor_ok and margin_ok)
    if not loss_floor_ok:
        reason = REASON_SN79_LOSS_FLOOR
    elif not margin_ok:
        reason = REASON_SN79_MARGIN
    else:
        reason = REASON_SN79_TAKER

    # Larger score advantage and one-away Kappa state justify a faster flatten.
    margin_strength = _clip01(max(0.0, margin) / 0.75)
    kappa_strength = 1.0 if int(observations_remaining) == 1 else kappa_value
    qty_frac = _clip01(0.55 + 0.25 * margin_strength + 0.20 * kappa_strength)
    if not take:
        qty_frac = 0.0

    return SN79ActionUtilityDecision(
        take=take,
        reason=reason,
        taker_utility=taker_utility,
        wait_utility=wait_utility,
        utility_margin=margin,
        taker_net_pnl_bps=taker_net,
        maker_expected_pnl_bps=maker_pnl,
        pnl_utility_taker=pnl_taker,
        pnl_utility_wait=pnl_wait,
        round_trip_value=rt_value,
        kappa_value=kappa_value,
        coverage_value=coverage_value,
        capital_release_value=capital_release,
        risk_reduction_value=risk_reduction,
        velocity_value=velocity_value,
        downside_penalty=downside,
        p_maker_fill_horizon=p_fill,
        max_score_subsidy_loss_bps=hard_floor,
        recommended_qty_frac=qty_frac,
    )
