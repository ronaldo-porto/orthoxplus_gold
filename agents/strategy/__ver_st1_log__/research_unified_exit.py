# SPDX-License-Identifier: MIT
"""V4.12.6 unified hybrid Maker/Taker exit economics with early-escape guard.

V4.12.5's hard-risk protection is preserved: legacy/derived EMERGENCY state never
by itself authorizes an unlimited-loss Taker. V4.12.6 adds a narrow early-escape
window so a position can use the existing bounded protective Taker *before* its
executable economics deteriorate beyond the -2 bps protective floor.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

UNIFIED_EXIT_VERSION = "early_escape_guard_v4_12_6"

ACTION_KEEP_MAKER = "KEEP_MAKER"
ACTION_TAKER_PROFIT_LOCK = "TAKER_PROFIT_LOCK"
ACTION_TAKER_PROTECT = "TAKER_PROTECT"
ACTION_HARD_RISK_TAKER = "HARD_RISK_TAKER"


def _finite(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _clip01(v: Any) -> float:
    return max(0.0, min(1.0, _finite(v)))


def gross_exit_bps(*, entry_price: float, exit_price: float, long_position: bool) -> float:
    entry = _finite(entry_price)
    px = _finite(exit_price)
    if entry <= 0.0 or px <= 0.0:
        return 0.0
    if long_position:
        return (px - entry) / entry * 10_000.0
    return (entry - px) / entry * 10_000.0


def completion_net_bps(
    *,
    entry_price: float,
    exit_price: float,
    long_position: bool,
    entry_fee_bps: float = 0.0,
    exit_fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    impact_bps: float = 0.0,
) -> float:
    """Estimated round-trip completion net from VWAP to executable exit price."""
    return (
        gross_exit_bps(entry_price=entry_price, exit_price=exit_price, long_position=long_position)
        - _finite(entry_fee_bps)
        - _finite(exit_fee_bps)
        - max(0.0, _finite(slippage_bps))
        - max(0.0, _finite(impact_bps))
    )


def breakeven_price(
    *,
    entry_price: float,
    long_position: bool,
    round_trip_fee_bps: float,
    net_floor_bps: float = 0.0,
) -> float:
    """Exit price needed to realize the configured net floor after estimated fees."""
    entry = _finite(entry_price)
    required_gross = _finite(round_trip_fee_bps) + _finite(net_floor_bps)
    if entry <= 0.0:
        return 0.0
    if long_position:
        return entry * (1.0 + required_gross / 10_000.0)
    return entry * (1.0 - required_gross / 10_000.0)


def wait_value_bps(
    *,
    maker_net_bps: float,
    taker_net_bps: float,
    p_maker_fill: float,
    holding_cost_bps: float,
    reversal_cost_bps: float,
    failed_exit_count: int,
    failed_exit_penalty_bps: float,
    age_ticks: float,
    age_penalty_bps_per_tick: float,
    grace_age_ticks: float = 3.0,
    max_wait_penalty_bps: float = 12.0,
) -> float:
    """Value of waiting one Maker horizon then falling back to current Taker economics."""
    p = _clip01(p_maker_fill)
    fail = max(0, int(failed_exit_count))
    age_excess = max(0.0, _finite(age_ticks) - max(0.0, _finite(grace_age_ticks)))
    stale_penalty = min(
        max(0.0, _finite(max_wait_penalty_bps, 12.0)),
        fail * max(0.0, _finite(failed_exit_penalty_bps))
        + age_excess * max(0.0, _finite(age_penalty_bps_per_tick)),
    )
    no_fill_value = _finite(taker_net_bps) - max(0.0, _finite(reversal_cost_bps))
    return (
        p * _finite(maker_net_bps)
        + (1.0 - p) * no_fill_value
        - (1.0 - p) * max(0.0, _finite(holding_cost_bps))
        - stale_penalty
    )


@dataclass(frozen=True)
class UnifiedExitDecision:
    action: str
    reason: str
    maker_net_bps: float
    taker_net_bps: float
    wait_ev_bps: float
    maker_price: float
    taker_price: float
    breakeven_price: float
    p_maker_fill: float
    peak_taker_net_bps: float
    profit_drawdown_bps: float
    protective_trigger: bool
    profit_lock_trigger: bool
    hard_risk_trigger: bool
    emergency_state_only: bool
    stop_loss_hit: bool
    early_escape_trigger: bool
    early_escape_reason: str
    protective_loss_headroom_bps: float
    protective_margin_bps: float

    def as_log(self) -> dict[str, Any]:
        return {
            "unified_exit_version": UNIFIED_EXIT_VERSION,
            "unified_action": self.action,
            "unified_reason": self.reason,
            "maker_net_bps_actual": self.maker_net_bps,
            "taker_net_bps_actual": self.taker_net_bps,
            "wait_ev_bps_actual": self.wait_ev_bps,
            "maker_price_actual": self.maker_price,
            "taker_price_actual": self.taker_price,
            "breakeven_price": self.breakeven_price,
            "maker_fill_actual": self.p_maker_fill,
            "peak_taker_net_bps": self.peak_taker_net_bps,
            "profit_drawdown_bps": self.profit_drawdown_bps,
            "protective_trigger": int(bool(self.protective_trigger)),
            "profit_lock_trigger": int(bool(self.profit_lock_trigger)),
            "hard_risk_trigger": int(bool(self.hard_risk_trigger)),
            "emergency_state_only": int(bool(self.emergency_state_only)),
            "stop_loss_hit": int(bool(self.stop_loss_hit)),
            "early_escape_trigger": int(bool(self.early_escape_trigger)),
            "early_escape_reason": self.early_escape_reason,
            "protective_loss_headroom_bps": self.protective_loss_headroom_bps,
            "protective_margin_bps": self.protective_margin_bps,
        }


def choose_unified_exit(
    *,
    maker_net_bps: float,
    taker_net_bps: float,
    wait_ev_bps: float,
    maker_price: float,
    taker_price: float,
    breakeven_px: float,
    p_maker_fill: float,
    peak_taker_net_bps: float,
    failed_exit_count: int,
    inventory_age: float,
    observations_remaining: int,
    expected_markout_bps: float,
    adverse_selection_risk: float,
    inventory_state: str,
    stop_loss_hit: bool,
    hard_emergency: bool,
    profit_lock_min_bps: float = 1.0,
    profit_lock_drawdown_bps: float = 2.0,
    switch_margin_bps: float = 0.5,
    protective_enabled: bool = True,
    protective_loss_floor_bps: float = -2.0,
    protective_ev_advantage_bps: float = 1.0,
    protective_failed_exits: int = 6,
    protective_min_age_ticks: float = 8.0,
    protective_adverse_bps: float = 2.0,
    early_escape_enabled: bool = True,
    early_escape_failed_exits: int = 3,
    early_escape_min_age_ticks: float = 5.0,
    early_escape_drawdown_bps: float = 1.5,
    early_escape_floor_headroom_bps: float = 0.75,
    early_escape_ev_advantage_bps: float = 0.5,
) -> UnifiedExitDecision:
    maker = _finite(maker_net_bps)
    taker = _finite(taker_net_bps)
    wait = _finite(wait_ev_bps)
    peak = max(_finite(peak_taker_net_bps, taker), taker)
    drawdown = max(0.0, peak - taker)
    state = str(inventory_state or "NORMAL").upper()
    one_away = max(0, int(observations_remaining)) == 1
    failures = max(0, int(failed_exit_count))
    age = max(0.0, _finite(inventory_age))
    adverse = max(0.0, _finite(adverse_selection_risk))
    floor = min(0.0, _finite(protective_loss_floor_bps, -2.0))
    loss_headroom = taker - floor

    markout_bad = _finite(expected_markout_bps) <= -max(0.5, _finite(protective_adverse_bps))

    # V4.12.6: early escape does not relax the protective loss floor. It only
    # lowers the *timing* threshold while current executable Taker economics are
    # still inside the already-approved bounded-loss window.
    early_reason = ""
    early = False
    # Never call an already-outside-floor position an "early escape" candidate.
    # That keeps telemetry honest and ensures V4.12.6 cannot be misread as a
    # hidden relaxation of the V4.12.5 -2 bps safety invariant.
    inside_protective_window = taker >= floor - 1e-12
    if bool(early_escape_enabled) and inside_protective_window:
        if bool(stop_loss_hit):
            early, early_reason = True, "STOP_LOSS"
        elif failures >= max(1, int(early_escape_failed_exits)):
            early, early_reason = True, "FAILED_EXITS"
        elif age >= max(1.0, _finite(early_escape_min_age_ticks, 5.0)):
            early, early_reason = True, "INVENTORY_AGE"
        elif drawdown >= max(0.0, _finite(early_escape_drawdown_bps, 1.5)):
            early, early_reason = True, "TAKER_NET_DRAWDOWN"
        else:
            near_floor = loss_headroom <= max(0.0, _finite(early_escape_floor_headroom_bps, 0.75)) + 1e-12
            # A single failed Maker attempt / two ticks of age is enough to arm
            # the near-floor rescue. This prevents silently crossing the -2 bps
            # boundary while still giving a fresh Maker quote a brief chance.
            if near_floor and (failures >= 1 or age >= 2.0):
                early, early_reason = True, "PROTECTIVE_FLOOR_HEADROOM"

    deterioration = bool(
        early
        or failures >= max(1, int(protective_failed_exits))
        or age >= max(1.0, _finite(protective_min_age_ticks))
        or adverse >= max(0.0, _finite(protective_adverse_bps))
        or markout_bad
        or bool(stop_loss_hit)
        or state in {"DEFENSIVE", "EXIT_ONLY", "EMERGENCY"}
    )

    profit_lock = bool(
        taker >= max(0.0, _finite(profit_lock_min_bps))
        and taker > wait + max(0.0, _finite(switch_margin_bps)) + 1e-12
        and (
            drawdown >= max(0.0, _finite(profit_lock_drawdown_bps))
            or failures >= 3
            or one_away
            or deterioration
        )
    )

    # V4.12.5 safety invariant preserved exactly: legacy/derived state ==
    # EMERGENCY is not sufficient for an unlimited-loss market exit.
    hard = bool(hard_emergency)
    emergency_state_only = bool(state == "EMERGENCY" and not hard)

    standard_margin = max(0.0, _finite(protective_ev_advantage_bps, 1.0))
    early_margin = max(0.0, _finite(early_escape_ev_advantage_bps, 0.5))
    effective_margin = early_margin if early else standard_margin

    if hard:
        action, reason = ACTION_HARD_RISK_TAKER, "CATASTROPHIC_MAX_INVENTORY"
    elif profit_lock:
        action, reason = ACTION_TAKER_PROFIT_LOCK, "POSITIVE_TAKER_GT_WAIT"
    else:
        protect = bool(
            protective_enabled
            and deterioration
            and taker >= floor - 1e-12
            and taker > wait + effective_margin + 1e-12
        )
        if protect:
            reason = "EARLY_ESCAPE_GT_WAIT" if early else "CONTROLLED_LOSS_GT_WAIT"
            action = ACTION_TAKER_PROTECT
        else:
            action, reason = ACTION_KEEP_MAKER, "MAKER_OR_WAIT"

    return UnifiedExitDecision(
        action=action,
        reason=reason,
        maker_net_bps=maker,
        taker_net_bps=taker,
        wait_ev_bps=wait,
        maker_price=_finite(maker_price),
        taker_price=_finite(taker_price),
        breakeven_price=_finite(breakeven_px),
        p_maker_fill=_clip01(p_maker_fill),
        peak_taker_net_bps=peak,
        profit_drawdown_bps=drawdown,
        protective_trigger=deterioration,
        profit_lock_trigger=profit_lock,
        hard_risk_trigger=hard,
        emergency_state_only=emergency_state_only,
        stop_loss_hit=bool(stop_loss_hit),
        early_escape_trigger=early,
        early_escape_reason=early_reason,
        protective_loss_headroom_bps=loss_headroom,
        protective_margin_bps=effective_margin,
    )
