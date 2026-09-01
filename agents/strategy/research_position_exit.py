# SPDX-License-Identifier: MIT
"""V4.16.1 PositionExitController: continuation-aware Maker/Taker/Wait + corridor.

Normal exits remain a three-way utility comparison. The four-band RealNet
loss corridor is the only override. ABSOLUTE_PROTECTION reduces executable
inventory; PARK is only for mechanically non-executable positions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from research_action_utility import coverage_redeploy_value, kappa_completion_value
from research_realnet_exit_authority import (
    ACTION_KEEP_MAKER,
    ACTION_PARK,
    ACTION_TAKER_ESCAPE,
    REALNET_EXIT_AUTHORITY_VERSION,
    STAGE_BELOW_FLOOR,
    STAGE_HARD_ESCAPE,
    STAGE_NONE,
    STAGE_SOFT_ESCAPE,
    STAGE_SOFT_HOLD,
    arbitrate_realnet_exit,
)

POSITION_EXIT_VERSION = "position_exit_v4_16_1"

BAND_NORMAL = "NORMAL"
BAND_DEFENSIVE = "DEFENSIVE"
BAND_HARD_ESCAPE = "HARD_ESCAPE"
BAND_ABSOLUTE = "ABSOLUTE_PROTECTION"

ACTION_MAKER_EXIT = "MAKER_EXIT"
ACTION_TAKER_EXIT = "TAKER_EXIT"
ACTION_WAIT = "WAIT"
ACTION_PARK_EXIT = "PARK"

TAKER_CLIP = 0.25


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _clip01(value: Any) -> float:
    return max(0.0, min(1.0, _finite(value)))


def _tanh_bps(value: Any, scale: float = 8.0) -> float:
    return math.tanh(_finite(value) / max(1e-6, float(scale)))


def classify_risk_band(unrealized_bps: float) -> str:
    pnl = _finite(unrealized_bps, 0.0)
    if pnl < -25.0:
        return BAND_ABSOLUTE
    if pnl <= -18.0:
        return BAND_HARD_ESCAPE
    if pnl <= -8.0:
        return BAND_DEFENSIVE
    return BAND_NORMAL


def wait_utility(
    *,
    maker_net_bps: float,
    p_maker_fill: float,
    holding_bps: float = 0.0,
    adverse_risk: float = 0.0,
    inventory_age: float = 0.0,
    failed_exit_count: int = 0,
    expiry_urgency: float = 0.0,
    capital_lock: float = 0.0,
    age_ref: float = 16.0,
) -> float:
    """Expected future improvement minus holding, age, failure, and lock costs.

    All terms are on the tanh-normalized utility scale. Endless waiting is
    not valuable: age and failed exits monotonically reduce this number.
    """
    p = _clip01(p_maker_fill)
    maker_term = _tanh_bps(maker_net_bps)
    expected_future = p * max(0.0, maker_term)
    holding = math.tanh(max(0.0, _finite(holding_bps)) / 8.0)
    age = math.tanh(max(0.0, _finite(inventory_age)) / max(1.0, float(age_ref)))
    fails = math.tanh(max(0.0, float(failed_exit_count)) / 4.0)
    away = max(0.0, -maker_term)
    lock = _clip01(capital_lock)
    return (
        expected_future
        - 0.25 * holding
        - 0.25 * _clip01(adverse_risk)
        - 0.30 * age
        - 0.35 * fails
        - 0.20 * _clip01(expiry_urgency)
        - 0.15 * away
        - 0.15 * lock
    )


def maker_fill_value(
    *,
    maker_net_bps: float,
    observations_remaining: int = 0,
    required_observations: int = 3,
    capital_release: float = 0.0,
) -> float:
    kappa = kappa_completion_value(observations_remaining, required_observations)
    return (
        _tanh_bps(maker_net_bps)
        + 0.20 * kappa
        + 0.15 * _clip01(capital_release)
    )


def continuation_value(
    *,
    wait_u: float,
    inventory_age: float = 0.0,
    failed_exit_count: int = 0,
    adverse_risk: float = 0.0,
    age_ref: float = 16.0,
) -> float:
    """Value of remaining in inventory if the Maker exit does not fill."""
    delay = 0.12 * math.tanh(max(0.0, _finite(inventory_age)) / max(1.0, float(age_ref)))
    fail = 0.40 * math.tanh(max(0.0, float(failed_exit_count)) / 4.0)
    adverse = 0.20 * _clip01(adverse_risk)
    return _finite(wait_u) - delay - fail - adverse


def maker_exit_utility(
    *,
    maker_net_bps: float,
    p_maker_fill: float,
    wait_u: float,
    observations_remaining: int = 0,
    required_observations: int = 3,
    capital_release: float = 0.0,
    inventory_age: float = 0.0,
    failed_exit_count: int = 0,
    adverse_risk: float = 0.0,
) -> float:
    """p * fill_value + (1-p) * continuation. Low fill is not near-certain execution."""
    p = _clip01(p_maker_fill)
    fill_v = maker_fill_value(
        maker_net_bps=maker_net_bps,
        observations_remaining=observations_remaining,
        required_observations=required_observations,
        capital_release=capital_release,
    )
    cont = continuation_value(
        wait_u=wait_u,
        inventory_age=inventory_age,
        failed_exit_count=failed_exit_count,
        adverse_risk=adverse_risk,
    )
    return p * fill_v + (1.0 - p) * cont


def taker_exit_utility(
    *,
    taker_net_bps: float,
    observations_remaining: int = 0,
    required_observations: int = 3,
    capital_release: float = 0.0,
    expiry_urgency: float = 0.0,
    inventory_risk: float = 0.0,
    crossing_bps: float = 0.0,
) -> float:
    kappa = kappa_completion_value(observations_remaining, required_observations)
    coverage = coverage_redeploy_value(observations_remaining, required_observations)
    return (
        _tanh_bps(taker_net_bps)
        + 0.20 * kappa
        + 0.10 * coverage
        + 0.15 * _clip01(capital_release)
        + 0.20 * _clip01(expiry_urgency)
        + 0.15 * _clip01(inventory_risk)
        - 0.05 * math.tanh(max(0.0, _finite(crossing_bps)) / 8.0)
    )


def new_exposure_allowed(risk_band: str) -> bool:
    """ABSOLUTE_PROTECTION blocks new exposure; it does not park executable inventory."""
    return str(risk_band or "").upper() != BAND_ABSOLUTE


def reduction_is_executable(
    *,
    inventory_qty: float,
    min_order: float = TAKER_CLIP,
    reduction_executable: bool = True,
    is_dust: bool = False,
    valid_opposite_touch: bool = True,
) -> bool:
    qty = abs(_finite(inventory_qty))
    floor = max(0.0, _finite(min_order, TAKER_CLIP))
    if is_dust or qty + 1e-12 < floor:
        return False
    if not valid_opposite_touch:
        return False
    return bool(reduction_executable)


def taker_clip_qty(
    *,
    inventory_qty: float,
    min_order: float = TAKER_CLIP,
    taker_clip: float = TAKER_CLIP,
) -> float:
    qty_abs = max(0.0, abs(_finite(inventory_qty)))
    clip = max(0.0, _finite(taker_clip, TAKER_CLIP))
    floor = max(0.0, _finite(min_order, TAKER_CLIP))
    qty = min(qty_abs, clip) if clip > 0.0 else 0.0
    if 0.0 < qty + 1e-12 < floor:
        qty = min(qty_abs, floor)
    return qty


@dataclass(frozen=True)
class PositionExitDecision:
    action: str
    risk_band: str
    maker_exit_utility: float
    taker_exit_utility: float
    wait_utility: float
    selected_qty: float
    reason: str
    corridor_action: str | None = None
    corridor_stage: str | None = None
    continuation_penalty: float = 0.0
    low_fill_maker_rejected: int = 0

    def as_log(self) -> dict[str, Any]:
        return {
            "position_exit_version": POSITION_EXIT_VERSION,
            "realnet_exit_authority_version": REALNET_EXIT_AUTHORITY_VERSION,
            "selected_action": self.action,
            "risk_band": self.risk_band,
            "maker_exit_utility": self.maker_exit_utility,
            "taker_exit_utility": self.taker_exit_utility,
            "wait_utility": self.wait_utility,
            "selected_qty": self.selected_qty,
            "reason": self.reason,
            "corridor_action": self.corridor_action,
            "corridor_stage": self.corridor_stage,
            "continuation_penalty": self.continuation_penalty,
            "low_fill_maker_rejected": int(self.low_fill_maker_rejected),
        }


def choose_position_exit(
    *,
    maker_net_bps: float,
    taker_net_bps: float,
    p_maker_fill: float,
    unrealized_bps: float | None = None,
    inventory_qty: float = 0.25,
    inventory_age: float = 0.0,
    failed_exit_count: int = 0,
    observations_remaining: int = 0,
    required_observations: int = 3,
    holding_bps: float = 0.0,
    adverse_risk: float = 0.0,
    expiry_urgency: float = 0.0,
    capital_release: float = 0.0,
    inventory_risk: float = 0.0,
    crossing_bps: float = 0.0,
    maker_executable: bool = True,
    stop_loss_hit: bool = False,
    catastrophic_hard_risk: bool = False,
    min_order: float = TAKER_CLIP,
    taker_clip: float = TAKER_CLIP,
    reduction_executable: bool = True,
    is_dust: bool = False,
    valid_opposite_touch: bool = True,
    allow_new_exposure: bool = True,
) -> PositionExitDecision:
    pnl = _finite(unrealized_bps, _finite(taker_net_bps))
    band = classify_risk_band(pnl)
    wait_u = wait_utility(
        maker_net_bps=maker_net_bps,
        p_maker_fill=p_maker_fill,
        holding_bps=holding_bps,
        adverse_risk=adverse_risk,
        inventory_age=inventory_age,
        failed_exit_count=failed_exit_count,
        expiry_urgency=expiry_urgency,
        capital_lock=max(0.0, 1.0 - _clip01(capital_release)),
    )
    maker_u = maker_exit_utility(
        maker_net_bps=maker_net_bps,
        p_maker_fill=p_maker_fill,
        wait_u=wait_u,
        observations_remaining=observations_remaining,
        required_observations=required_observations,
        capital_release=capital_release,
        inventory_age=inventory_age,
        failed_exit_count=failed_exit_count,
        adverse_risk=adverse_risk,
    )
    taker_u = taker_exit_utility(
        taker_net_bps=taker_net_bps,
        observations_remaining=observations_remaining,
        required_observations=required_observations,
        capital_release=capital_release,
        expiry_urgency=expiry_urgency,
        inventory_risk=inventory_risk,
        crossing_bps=crossing_bps,
    )
    if not maker_executable:
        maker_u = -1e9

    fill_v = maker_fill_value(
        maker_net_bps=maker_net_bps,
        observations_remaining=observations_remaining,
        required_observations=required_observations,
        capital_release=capital_release,
    )
    cont = continuation_value(
        wait_u=wait_u,
        inventory_age=inventory_age,
        failed_exit_count=failed_exit_count,
        adverse_risk=adverse_risk,
    )
    continuation_penalty = max(0.0, fill_v - maker_u)
    p = _clip01(p_maker_fill)
    low_fill_rejected = int(p < 0.20 and maker_u < taker_u)

    corridor = arbitrate_realnet_exit(
        taker_net_bps=taker_net_bps,
        maker_net_bps=maker_net_bps,
        maker_executable=bool(maker_executable),
        failed_exit_count=int(failed_exit_count),
        inventory_age=float(inventory_age),
        stop_loss_hit=bool(stop_loss_hit),
        catastrophic_hard_risk=bool(catastrophic_hard_risk),
        adverse_evidence=bool(stop_loss_hit or _finite(adverse_risk) >= 0.25),
        wait_ev_bps=wait_u,
    )

    qty_abs = max(0.0, abs(_finite(inventory_qty)))
    taker_qty = taker_clip_qty(
        inventory_qty=qty_abs, min_order=min_order, taker_clip=taker_clip,
    )
    maker_qty = qty_abs
    can_reduce = reduction_is_executable(
        inventory_qty=qty_abs,
        min_order=min_order,
        reduction_executable=reduction_executable,
        is_dust=is_dust,
        valid_opposite_touch=valid_opposite_touch,
    )

    def _pack(action, band_token, wait_value, qty, reason, stage=None):
        return PositionExitDecision(
            action, band_token, maker_u, taker_u, wait_value, qty, reason,
            corridor.action, stage or corridor.stage,
            continuation_penalty, low_fill_rejected,
        )

    if band == BAND_ABSOLUTE or corridor.action == ACTION_PARK or corridor.stage == STAGE_BELOW_FLOOR:
        if can_reduce:
            return _pack(
                ACTION_TAKER_EXIT, BAND_ABSOLUTE, -1e9, taker_qty,
                "ABSOLUTE_PROTECTION_REDUCE", STAGE_BELOW_FLOOR,
            )
        return _pack(
            ACTION_PARK_EXIT, BAND_ABSOLUTE, wait_u, 0.0,
            "ABSOLUTE_PROTECTION_PARK", STAGE_BELOW_FLOOR,
        )

    if band == BAND_HARD_ESCAPE or corridor.stage == STAGE_HARD_ESCAPE:
        if can_reduce:
            return _pack(
                ACTION_TAKER_EXIT, BAND_HARD_ESCAPE, -1e9, taker_qty,
                "HARD_ESCAPE_CLIP", STAGE_HARD_ESCAPE,
            )
        return _pack(
            ACTION_PARK_EXIT, BAND_HARD_ESCAPE, wait_u, 0.0,
            "HARD_ESCAPE_NON_EXECUTABLE", STAGE_HARD_ESCAPE,
        )

    if band == BAND_DEFENSIVE:
        wait_u *= 0.35
        taker_u += 0.20
        if corridor.action == ACTION_KEEP_MAKER and corridor.stage == STAGE_SOFT_HOLD:
            wait_u = max(wait_u, maker_u)
        reason_prefix = "DEFENSIVE"
        stage = corridor.stage or STAGE_SOFT_ESCAPE
    else:
        reason_prefix = "NORMAL"
        stage = corridor.stage or STAGE_NONE

    best = max(maker_u, taker_u, wait_u)
    if maker_u >= best and maker_u > wait_u and maker_u > taker_u:
        return _pack(
            ACTION_MAKER_EXIT, band, wait_u, maker_qty,
            reason_prefix + "_MAKER", stage,
        )
    if taker_u >= best and taker_u > wait_u:
        return _pack(
            ACTION_TAKER_EXIT, band, wait_u, taker_qty,
            reason_prefix + "_TAKER", stage,
        )
    return _pack(
        ACTION_WAIT, band, wait_u, 0.0,
        reason_prefix + "_WAIT", stage,
    )
