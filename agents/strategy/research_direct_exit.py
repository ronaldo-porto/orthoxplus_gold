# SPDX-License-Identifier: MIT
"""Strategy1-Direct A1.7.2 observable position-exit authority.

A1.7.2 preserves A1.7.1 risk/economics separation and adds a bounded
positive-Maker grace for MTM-only ABSOLUTE_PROTECTION. Catastrophic/MAX
exposure still has immediate Taker reduction authority.

``unrealized_bps`` is the true mark-to-market move of the open inventory and is
therefore the only input used to classify NORMAL / DEFENSIVE / HARD_ESCAPE /
ABSOLUTE_PROTECTION.  ``taker_net_bps`` remains the executable completion
value after spread/fees/slippage/impact and is never used as a proxy for risk.

Fresh-position protection and the existing positive-Maker veto remain for
HARD_ESCAPE. MTM-only ABSOLUTE gets one bounded positive-Maker attempt; genuine
catastrophic/MAX exposure retains immediate reduction authority.
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

DIRECT_OBSERVABLE_EXIT_VERSION = "direct_observable_exit_v4_16_2_a1_7_2"
DIRECT_MAKER_EXIT_TARGET_BPS = 1.0
DIRECT_HARD_ESCAPE_MIN_AGE_TICKS = 2.0
DIRECT_POSITIVE_MAKER_VETO_MAX_FAILED_EXITS = 4
DIRECT_ABSOLUTE_POSITIVE_MAKER_VETO_MAX_FAILED_EXITS = 1


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
    hard_escape_min_age_ticks: float = DIRECT_HARD_ESCAPE_MIN_AGE_TICKS,
    positive_maker_veto_enabled: bool = True,
    positive_maker_veto_floor_bps: float = DIRECT_MAKER_EXIT_TARGET_BPS,
    positive_maker_veto_max_failed_exits: int = DIRECT_POSITIVE_MAKER_VETO_MAX_FAILED_EXITS,
    absolute_positive_maker_veto_enabled: bool = True,
    absolute_positive_maker_veto_floor_bps: float = DIRECT_MAKER_EXIT_TARGET_BPS,
    absolute_positive_maker_veto_max_failed_exits: int = DIRECT_ABSOLUTE_POSITIVE_MAKER_VETO_MAX_FAILED_EXITS,
) -> PositionExitDecision:
    """Choose Maker / Taker / Wait from true risk plus executable economics.

    A1.7.2 rules:

    * Risk bands come only from true mark-to-market ``unrealized_bps``.
      Spread crossing, taker fees, slippage and impact may make ``taker_net_bps``
      negative, but cannot by themselves create HARD_ESCAPE.
    * NORMAL/DEFENSIVE: prefer an executable Maker completion >= +1 bps;
      otherwise take only non-negative Taker completion, else WAIT.
    * HARD_ESCAPE: a fresh position must first age to the configured minimum (or
      have a failed exit attempt).  A positive executable Maker completion keeps
      veto authority until the configured failed-exit limit is reached.
    * ABSOLUTE_PROTECTION from MTM alone gets at most one positive-Maker
      attempt when an executable Maker completion is already >= the configured
      floor. Catastrophic/MAX exposure bypasses that grace and reduces now.
    """
    del (
        p_maker_fill, observations_remaining, required_observations, holding_bps,
        adverse_risk, expiry_urgency, capital_release, inventory_risk,
        crossing_bps, allow_new_exposure,
    )

    maker_net = _finite(maker_net_bps)
    taker_net = _finite(taker_net_bps)
    # Critical A1.7.x semantic: never fall back to taker_net.  Unknown MTM is
    # neutral/normal rather than fabricating a loss from crossing economics.
    position_risk_bps = _finite(unrealized_bps, 0.0)
    band = classify_risk_band(position_risk_bps)
    catastrophic = bool(catastrophic_hard_risk)
    if catastrophic:
        band = BAND_ABSOLUTE

    age = max(0.0, _finite(inventory_age))
    failed = max(0, int(failed_exit_count or 0))
    min_age = max(0.0, _finite(hard_escape_min_age_ticks, DIRECT_HARD_ESCAPE_MIN_AGE_TICKS))
    veto_floor = max(0.0, _finite(positive_maker_veto_floor_bps, DIRECT_MAKER_EXIT_TARGET_BPS))
    veto_max_failed = max(0, int(positive_maker_veto_max_failed_exits or 0))
    abs_veto_floor = max(
        0.0, _finite(absolute_positive_maker_veto_floor_bps, DIRECT_MAKER_EXIT_TARGET_BPS)
    )
    abs_veto_max_failed = max(
        0, int(absolute_positive_maker_veto_max_failed_exits or 0)
    )

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

    # Keep the dataclass interface stable.  These fields are telemetry only in
    # Direct mode and intentionally equal current executable net bps.
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
            corridor_action="DIRECT_OBSERVABLE_A172",
            corridor_stage=band,
            continuation_penalty=0.0,
            low_fill_maker_rejected=0,
        )

    # MTM-only ABSOLUTE gets one short positive-Maker opportunity when the
    # executable Maker completion is already profitable. Catastrophic/MAX
    # exposure is mechanically different and always bypasses this veto.
    if band == BAND_ABSOLUTE:
        absolute_maker_veto = (
            not catastrophic
            and bool(absolute_positive_maker_veto_enabled)
            and maker_executable
            and maker_net + 1e-12 >= abs_veto_floor
            and failed < abs_veto_max_failed
        )
        if absolute_maker_veto:
            return pack(ACTION_MAKER_EXIT, qty_abs, "ABSOLUTE_POSITIVE_MAKER_GRACE")
        if can_reduce:
            return pack(ACTION_TAKER_EXIT, taker_qty, "ABSOLUTE_PROTECTION_REDUCE")
        return pack(ACTION_PARK_EXIT, 0.0, "ABSOLUTE_PROTECTION_PARK")

    if band == BAND_HARD_ESCAPE:
        maker_veto = (
            bool(positive_maker_veto_enabled)
            and maker_executable
            and maker_net + 1e-12 >= veto_floor
            and failed < veto_max_failed
        )
        if maker_veto:
            return pack(ACTION_MAKER_EXIT, qty_abs, "HARD_ESCAPE_POSITIVE_MAKER_VETO")

        # A fresh hard-loss observation gets one chance to mature/attempt a
        # passive exit.  A failed prior attempt unlocks hard reduction even if
        # the numerical age has not advanced as expected.
        hard_mature = age + 1e-12 >= min_age or failed > 0
        if not hard_mature:
            if maker_executable and maker_net + 1e-12 >= DIRECT_MAKER_EXIT_TARGET_BPS:
                return pack(ACTION_MAKER_EXIT, qty_abs, "HARD_ESCAPE_FRESH_MAKER")
            return pack(ACTION_WAIT, 0.0, "HARD_ESCAPE_FRESH_GRACE")

        if can_reduce:
            return pack(ACTION_TAKER_EXIT, taker_qty, "HARD_ESCAPE_CLIP")
        return pack(ACTION_PARK_EXIT, 0.0, "HARD_ESCAPE_NON_EXECUTABLE")

    # Stop-loss alone does not authorize a negative Taker dump in Direct A1.7.2;
    # actual mark-to-market risk band remains the authority.
    _ = stop_loss_hit

    if maker_executable and maker_net + 1e-12 >= DIRECT_MAKER_EXIT_TARGET_BPS:
        return pack(ACTION_MAKER_EXIT, qty_abs, f"{band}_MAKER_NET")
    if can_reduce and taker_net + 1e-12 >= 0.0:
        return pack(ACTION_TAKER_EXIT, taker_qty, f"{band}_TAKER_NONNEGATIVE")
    return pack(ACTION_WAIT, 0.0, f"{band}_WAIT_NEGATIVE_TAKER")
