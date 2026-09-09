# SPDX-License-Identifier: MIT
"""A1.7.4 observation-driven genuine tail-risk recovery helpers.

The A1.7.1 risk source remains true mid-mark MTM.  This module does not
reclassify risk from spread/fee/crossing economics.  It adds an overlay
recovery corridor before/at HARD_ESCAPE so a deteriorating position can try a
bounded Maker concession (or a tightly bounded recovery Taker after failed
Maker attempts) before the much larger forced HARD/ABSOLUTE loss observed in
A1.7.2/A1.7.3 runs.

Initial constants are deliberately tied to observed runtime distributions:
* HARD onset clustered near -19.5 bps true MTM;
* median forced HARD/ABS Taker completion was roughly -45 to -50 bps;
* pre-HARD deterioration velocity was commonly around -2 bps/tick;
* a Maker completion around -20 to -30 bps was frequently materially cheaper
  than the eventual forced Taker.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from research_position_exit import (
    ACTION_MAKER_EXIT,
    ACTION_TAKER_EXIT,
    ACTION_WAIT,
    BAND_ABSOLUTE,
    BAND_DEFENSIVE,
    BAND_HARD_ESCAPE,
    PositionExitDecision,
)

DIRECT_TAIL_RECOVERY_VERSION = "direct_tail_recovery_v4_16_2_a1_7_5"
RECOVERY_STAGE = "RECOVERY"

# Observation-driven initial corridor.  These are A/B-test parameters, not a
# learned model and not changes to the frozen A1.7.1 MTM band thresholds.
DIRECT_RECOVERY_TRIGGER_BPS = -8.0
DIRECT_RECOVERY_FORCE_BPS = -12.0
DIRECT_RECOVERY_MIN_AGE_TICKS = 4.0
DIRECT_RECOVERY_WORSENING_BPS_PER_TICK = -0.50
DIRECT_RECOVERY_MAKER_FLOOR_BPS = -25.0
DIRECT_HARD_RECOVERY_MAKER_FLOOR_BPS = -30.0
DIRECT_ABSOLUTE_RECOVERY_MAKER_FLOOR_BPS = -35.0
DIRECT_RECOVERY_MAKER_ADVANTAGE_BPS = 10.0
DIRECT_RECOVERY_MAKER_MAX_FAILED_EXITS = 2
DIRECT_ABSOLUTE_RECOVERY_MAKER_MAX_FAILED_EXITS = 1
DIRECT_RECOVERY_TAKER_FLOOR_BPS = -25.0
DIRECT_RECOVERY_TAKER_MIN_FAILED_EXITS = 2
DIRECT_RECOVERY_TAKER_TRIGGER_BPS = -10.0
DIRECT_EXPECTED_HARD_TAKER_LOSS_BPS = -48.0
DIRECT_TAIL_HISTORY_MAX = 64

# A1.7.5: `failed_exit_count` conflates "Maker is unachievable" with "the book
# is quiet". In the A1.7.4.5 QUIET runtime (trade_rate ~= 0) it grew with time
# rather than with information, and 154 of 168 forced-crossing rows had already
# exceeded DIRECT_RECOVERY_MAKER_MAX_FAILED_EXITS. The reason to stop retrying
# Maker is that Maker is unachievable, not that nobody has traded yet, so a
# Maker still materially better than crossing keeps its recovery attempt.
DIRECT_A175_MAKER_ADVANTAGE_BPS = 15.0


def a175_failed_exit_override(
    *,
    maker_net_bps: float,
    taker_net_bps: float,
    advantage_bps: float = DIRECT_A175_MAKER_ADVANTAGE_BPS,
) -> bool:
    """True when Maker is far enough ahead of crossing to keep retrying."""
    maker = _finite(maker_net_bps)
    taker = _finite(taker_net_bps)
    advantage = max(0.0, _finite(advantage_bps, DIRECT_A175_MAKER_ADVANTAGE_BPS))
    return (maker - taker) + 1e-12 >= advantage


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if v == v and abs(v) != float("inf") else default


def risk_velocity_bps_per_tick(
    history: list[dict[str, float | int]] | None,
    *,
    tick: int,
    current_risk_bps: float,
    lookback_observations: int = 3,
) -> float:
    """Return recent MTM slope in bps/tick using only earlier observations."""
    rows = list(history or [])
    if not rows:
        return 0.0
    usable = [r for r in rows if int(r.get("tick", -1)) < int(tick)]
    if not usable:
        return 0.0
    anchor = usable[max(0, len(usable) - max(1, int(lookback_observations)))]
    dt = max(1, int(tick) - int(anchor.get("tick", tick - 1)))
    return (_finite(current_risk_bps) - _finite(anchor.get("risk_bps"))) / float(dt)


def recovery_maker_allowed(
    *,
    maker_net_bps: float,
    taker_net_bps: float,
    maker_floor_bps: float,
    advantage_bps: float = DIRECT_RECOVERY_MAKER_ADVANTAGE_BPS,
) -> bool:
    """Maker recovery must be bounded and materially better than crossing now."""
    maker = _finite(maker_net_bps)
    taker = _finite(taker_net_bps)
    floor = _finite(maker_floor_bps)
    advantage = max(0.0, _finite(advantage_bps))
    return maker + 1e-12 >= floor and maker + 1e-12 >= taker + advantage


def _pack_like(
    base: PositionExitDecision,
    *,
    action: str,
    reason: str,
    qty: float,
) -> PositionExitDecision:
    return replace(
        base,
        action=action,
        selected_qty=max(0.0, _finite(qty)),
        reason=reason,
        corridor_action="DIRECT_TAIL_RECOVERY_A174",
        corridor_stage=RECOVERY_STAGE,
    )


def choose_tail_recovery_override(
    *,
    base_decision: PositionExitDecision,
    maker_net_bps: float,
    taker_net_bps: float,
    position_risk_bps: float,
    risk_velocity_bps_per_tick_value: float,
    inventory_qty: float,
    inventory_age: float,
    failed_exit_count: int,
    catastrophic_hard_risk: bool,
    reduction_executable: bool,
) -> PositionExitDecision:
    """Overlay A1.7.4 recovery without weakening catastrophic protection.

    Ordering:
      1. preserve profitable Maker and non-negative normal Taker decisions;
      2. in DEFENSIVE deterioration, attempt bounded Maker recovery;
      3. after repeated failed Maker attempts, allow a bounded pre-HARD Taker
         only when it is materially smaller than observed forced-tail losses;
      4. in HARD/MTM-only ABSOLUTE, allow a bounded Maker grace when it is at
         least 10 bps better than crossing now;
      5. catastrophic/MAX exposure always keeps the frozen base decision.
    """
    if bool(catastrophic_hard_risk):
        return base_decision

    maker = _finite(maker_net_bps)
    taker = _finite(taker_net_bps)
    risk = _finite(position_risk_bps)
    velocity = _finite(risk_velocity_bps_per_tick_value)
    age = max(0.0, _finite(inventory_age))
    failed = max(0, int(failed_exit_count or 0))
    qty = abs(_finite(inventory_qty))
    band = str(getattr(base_decision, "risk_band", "") or "")

    # Existing good decisions remain authoritative.
    if str(getattr(base_decision, "action", "") or "") == ACTION_MAKER_EXIT and maker >= 1.0 - 1e-12:
        return base_decision
    if str(getattr(base_decision, "action", "") or "") == ACTION_TAKER_EXIT and taker >= -1e-12:
        return base_decision

    worsening = velocity <= DIRECT_RECOVERY_WORSENING_BPS_PER_TICK + 1e-12
    recovery_active = (
        risk <= DIRECT_RECOVERY_TRIGGER_BPS + 1e-12
        and age + 1e-12 >= DIRECT_RECOVERY_MIN_AGE_TICKS
        and (worsening or risk <= DIRECT_RECOVERY_FORCE_BPS + 1e-12 or failed > 0)
    )

    # A1.7.5: a quiet book must not spend the Maker recovery budget.
    maker_still_far_ahead = a175_failed_exit_override(
        maker_net_bps=maker, taker_net_bps=taker,
    )

    if band == BAND_DEFENSIVE and recovery_active:
        if (
            (failed < DIRECT_RECOVERY_MAKER_MAX_FAILED_EXITS or maker_still_far_ahead)
            and recovery_maker_allowed(
                maker_net_bps=maker,
                taker_net_bps=taker,
                maker_floor_bps=DIRECT_RECOVERY_MAKER_FLOOR_BPS,
            )
        ):
            return _pack_like(
                base_decision,
                action=ACTION_MAKER_EXIT,
                reason="RECOVERY_MAKER_EXIT",
                qty=qty,
            )

        # A tightly bounded early reduction is permitted only after failed
        # Maker recovery and only before the loss reaches the much worse
        # observed HARD/ABS tail.  This is recovery authority, not entry Taker.
        if (
            bool(reduction_executable)
            and failed >= DIRECT_RECOVERY_TAKER_MIN_FAILED_EXITS
            and risk <= DIRECT_RECOVERY_TAKER_TRIGGER_BPS + 1e-12
            and taker >= DIRECT_RECOVERY_TAKER_FLOOR_BPS - 1e-12
        ):
            return _pack_like(
                base_decision,
                action=ACTION_TAKER_EXIT,
                reason="RECOVERY_TAKER_REDUCE",
                qty=qty,
            )

        # If neither bounded recovery action is attractive, preserve the frozen
        # A1.7.2 WAIT token rather than emitting a new per-tick recovery state.
        # This keeps runtime/log overhead low while the recovery corridor remains
        # available on the next observation.
        if str(getattr(base_decision, "action", "") or "") == ACTION_WAIT:
            return base_decision

    if band == BAND_HARD_ESCAPE:
        if (
            age + 1e-12 >= DIRECT_RECOVERY_MIN_AGE_TICKS
            and (failed < DIRECT_RECOVERY_MAKER_MAX_FAILED_EXITS or maker_still_far_ahead)
            and recovery_maker_allowed(
                maker_net_bps=maker,
                taker_net_bps=taker,
                maker_floor_bps=DIRECT_HARD_RECOVERY_MAKER_FLOOR_BPS,
            )
        ):
            return _pack_like(
                base_decision,
                action=ACTION_MAKER_EXIT,
                reason="HARD_RECOVERY_MAKER_EXIT",
                qty=qty,
            )

    if band == BAND_ABSOLUTE:
        if (
            failed < DIRECT_ABSOLUTE_RECOVERY_MAKER_MAX_FAILED_EXITS
            and recovery_maker_allowed(
                maker_net_bps=maker,
                taker_net_bps=taker,
                maker_floor_bps=DIRECT_ABSOLUTE_RECOVERY_MAKER_FLOOR_BPS,
            )
        ):
            return _pack_like(
                base_decision,
                action=ACTION_MAKER_EXIT,
                reason="ABSOLUTE_RECOVERY_MAKER_EXIT",
                qty=qty,
            )

    return base_decision


def recovery_maker_floor_for_reason(reason: str) -> float | None:
    token = str(reason or "")
    if token == "RECOVERY_MAKER_EXIT":
        return DIRECT_RECOVERY_MAKER_FLOOR_BPS
    if token == "HARD_RECOVERY_MAKER_EXIT":
        return DIRECT_HARD_RECOVERY_MAKER_FLOOR_BPS
    if token == "ABSOLUTE_RECOVERY_MAKER_EXIT":
        return DIRECT_ABSOLUTE_RECOVERY_MAKER_FLOOR_BPS
    return None


def is_recovery_maker_reason(reason: str) -> bool:
    return recovery_maker_floor_for_reason(reason) is not None


def is_recovery_taker_reason(reason: str) -> bool:
    return str(reason or "") == "RECOVERY_TAKER_REDUCE"
