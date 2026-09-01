# SPDX-License-Identifier: MIT
"""V4.16 PositionExitController: Maker / Taker / Wait + one loss corridor.

Normal exits are a three-way utility comparison. The only override is the
V4.14.4 four-band RealNet loss corridor.
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

POSITION_EXIT_VERSION = "position_exit_v4_16_0"

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


def maker_exit_utility(
    *,
    maker_net_bps: float,
    p_maker_fill: float,
    observations_remaining: int = 0,
    required_observations: int = 3,
    capital_release: float = 0.0,
) -> float:
    p = _clip01(p_maker_fill)
    kappa = kappa_completion_value(observations_remaining, required_observations)
    return (
        p * _tanh_bps(maker_net_bps)
        + 0.20 * kappa
        + 0.15 * _clip01(capital_release)
    )


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


def wait_utility(
    *,
    maker_net_bps: float,
    p_maker_fill: float,
    holding_bps: float = 0.0,
    adverse_risk: float = 0.0,
    inventory_age: float = 0.0,
    failed_exit_count: int = 0,
    expiry_urgency: float = 0.0,
    age_ref: float = 16.0,
) -> float:
    p = _clip01(p_maker_fill)
    expected = p * _tanh_bps(maker_net_bps)
    holding = math.tanh(max(0.0, _finite(holding_bps)) / 8.0)
    age = math.tanh(max(0.0, _finite(inventory_age)) / max(1.0, float(age_ref)))
    fails = math.tanh(max(0.0, float(failed_exit_count)) / 4.0)
    return (
        expected
        - 0.25 * holding
        - 0.20 * _clip01(adverse_risk)
        - 0.20 * age
        - 0.15 * fails
        - 0.20 * _clip01(expiry_urgency)
    )


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
) -> PositionExitDecision:
    pnl = _finite(unrealized_bps, _finite(taker_net_bps))
    band = classify_risk_band(pnl)
    maker_u = maker_exit_utility(
        maker_net_bps=maker_net_bps,
        p_maker_fill=p_maker_fill,
        observations_remaining=observations_remaining,
        required_observations=required_observations,
        capital_release=capital_release,
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
    wait_u = wait_utility(
        maker_net_bps=maker_net_bps,
        p_maker_fill=p_maker_fill,
        holding_bps=holding_bps,
        adverse_risk=adverse_risk,
        inventory_age=inventory_age,
        failed_exit_count=failed_exit_count,
        expiry_urgency=expiry_urgency,
    )
    if not maker_executable:
        maker_u = -1e9

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
    clip = max(0.0, _finite(taker_clip, TAKER_CLIP))
    floor = max(0.0, _finite(min_order, TAKER_CLIP))
    taker_qty = min(qty_abs, clip) if clip > 0.0 else 0.0
    if 0.0 < taker_qty + 1e-12 < floor:
        taker_qty = min(qty_abs, floor)
    maker_qty = qty_abs

    if corridor.action == ACTION_PARK or band == BAND_ABSOLUTE:
        return PositionExitDecision(
            ACTION_PARK_EXIT, BAND_ABSOLUTE, maker_u, taker_u, wait_u, 0.0,
            "ABSOLUTE_PROTECTION", corridor.action, corridor.stage or STAGE_BELOW_FLOOR,
        )

    if band == BAND_HARD_ESCAPE or corridor.stage == STAGE_HARD_ESCAPE:
        return PositionExitDecision(
            ACTION_TAKER_EXIT, BAND_HARD_ESCAPE, maker_u, taker_u, -1e9, taker_qty,
            "HARD_ESCAPE_CLIP", corridor.action, corridor.stage or STAGE_HARD_ESCAPE,
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
        return PositionExitDecision(
            ACTION_MAKER_EXIT, band, maker_u, taker_u, wait_u, maker_qty,
            reason_prefix + "_MAKER", corridor.action, stage,
        )
    if taker_u >= best and taker_u > wait_u:
        return PositionExitDecision(
            ACTION_TAKER_EXIT, band, maker_u, taker_u, wait_u, taker_qty,
            reason_prefix + "_TAKER", corridor.action, stage,
        )
    return PositionExitDecision(
        ACTION_WAIT, band, maker_u, taker_u, wait_u, 0.0,
        reason_prefix + "_WAIT", corridor.action, stage,
    )
