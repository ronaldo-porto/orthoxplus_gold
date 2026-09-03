# SPDX-License-Identifier: MIT
"""Strategy1-Direct A1.6.0 observable position-exit authority.

Normal and defensive exits use only current executable net economics.  Losing
Taker exits are reserved for hard risk bands.  This intentionally removes the
normal Maker/Taker/Wait utility race from Direct A1.6 while preserving the
existing PositionExitDecision interface used by Strategy1_Research.
"""
from __future__ import annotations

from typing import Any

from research_position_exit import (
    ACTION_MAKER_EXIT,
    ACTION_PARK_EXIT,
    ACTION_TAKER_EXIT,
    ACTION_WAIT,
    BAND_ABSOLUTE,
    BAND_DEFENSIVE,
    BAND_HARD_ESCAPE,
    BAND_NORMAL,
    PositionExitDecision,
    classify_risk_band,
    reduction_is_executable,
    taker_clip_qty,
)

DIRECT_OBSERVABLE_EXIT_VERSION = "direct_observable_exit_v4_16_2_a1_6_0"
DIRECT_MAKER_EXIT_TARGET_BPS = 1.0


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if v == v and abs(v) != float("inf") else default


def choose_observable_position_exit(
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
    min_order: float = 0.25,
    taker_clip: float = 0.25,
    reduction_executable: bool = True,
    is_dust: bool = False,
    valid_opposite_touch: bool = True,
    allow_new_exposure: bool = True,
) -> PositionExitDecision:
    """Choose Maker / Taker / Wait from current net economics and hard risk.

    * NORMAL/DEFENSIVE: Maker when current Maker completion is >= +1 bps;
      otherwise Taker only when its current net completion is non-negative;
      otherwise WAIT.
    * HARD_ESCAPE/ABSOLUTE (or catastrophic hard risk): Taker reduction when
      mechanically executable, else PARK.

    Other arguments are accepted to preserve the inherited call contract but do
    not create hidden utility authority.
    """
    del (
        p_maker_fill, inventory_age, failed_exit_count, observations_remaining,
        required_observations, holding_bps, adverse_risk, expiry_urgency,
        capital_release, inventory_risk, crossing_bps, allow_new_exposure,
    )

    maker_net = _finite(maker_net_bps)
    taker_net = _finite(taker_net_bps)
    pnl = _finite(unrealized_bps, taker_net)
    band = classify_risk_band(pnl)
    if catastrophic_hard_risk:
        band = BAND_ABSOLUTE

    qty_abs = max(0.0, abs(_finite(inventory_qty)))
    can_reduce = reduction_is_executable(
        inventory_qty=qty_abs,
        min_order=min_order,
        reduction_executable=reduction_executable,
        is_dust=is_dust,
        valid_opposite_touch=valid_opposite_touch,
    )
    taker_qty = taker_clip_qty(
        inventory_qty=qty_abs, min_order=min_order, taker_clip=taker_clip,
    )

    # Keep the dataclass interface stable. These fields are telemetry only in
    # A1.6 and intentionally equal the current net bps rather than modeled utility.
    maker_u = maker_net if maker_executable else -1e9
    taker_u = taker_net if can_reduce else -1e9
    wait_u = 0.0

    def pack(action: str, qty: float, reason: str) -> PositionExitDecision:
        return PositionExitDecision(
            action=action,
            risk_band=band,
            maker_exit_utility=maker_u,
            taker_exit_utility=taker_u,
            wait_utility=wait_u,
            selected_qty=qty,
            reason=reason,
            corridor_action="DIRECT_OBSERVABLE",
            corridor_stage=band,
            continuation_penalty=0.0,
            low_fill_maker_rejected=0,
        )

    if band in {BAND_HARD_ESCAPE, BAND_ABSOLUTE}:
        if can_reduce:
            reason = "ABSOLUTE_PROTECTION_REDUCE" if band == BAND_ABSOLUTE else "HARD_ESCAPE_CLIP"
            return pack(ACTION_TAKER_EXIT, taker_qty, reason)
        reason = "ABSOLUTE_PROTECTION_PARK" if band == BAND_ABSOLUTE else "HARD_ESCAPE_NON_EXECUTABLE"
        return pack(ACTION_PARK_EXIT, 0.0, reason)

    # Stop-loss alone does not authorize a small negative Taker dump in Direct
    # A1.6; the actual hard loss band remains the authority.
    _ = stop_loss_hit

    if maker_executable and maker_net + 1e-12 >= DIRECT_MAKER_EXIT_TARGET_BPS:
        return pack(ACTION_MAKER_EXIT, qty_abs, f"{band}_MAKER_NET")
    if can_reduce and taker_net + 1e-12 >= 0.0:
        return pack(ACTION_TAKER_EXIT, taker_qty, f"{band}_TAKER_NONNEGATIVE")
    return pack(ACTION_WAIT, 0.0, f"{band}_WAIT_NEGATIVE_TAKER")
