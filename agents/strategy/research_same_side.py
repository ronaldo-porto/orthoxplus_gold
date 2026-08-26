# SPDX-License-Identifier: MIT
"""Research same-side suppression.

Long inventory:
    BUY size ↓, BUY priority ↓, eventually BUY disabled
    SELL priority ↑, SELL quote competitiveness ↑

Short inventory is the mirror.

Suppression starts in CAUTION / DEFENSIVE. Same-side is disabled at
DEFENSIVE so the book does not wait until EMERGENCY to stop increasing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from research_inventory_state import (
    CAUTION_ENTRY_MULT,
    CAUTION_EXIT_MULT,
    DEFENSIVE_EXIT_MULT,
    STATE_CAUTION,
    STATE_DEFENSIVE,
    STATE_EMERGENCY,
    STATE_EXIT_ONLY,
    STATE_NORMAL,
    is_same_side_entry,
)

SAME_SIDE_VERSION = "same_side_v1"

CAUTION_SAME_PRIORITY = 0.50
CAUTION_EXIT_PRIORITY = 1.25
CAUTION_EXIT_TICKS = 1.0
CAUTION_SAME_PASSIVE_TICKS = 1.0

DEFENSIVE_EXIT_PRIORITY = 1.50
DEFENSIVE_EXIT_TICKS = 2.0

EXIT_ONLY_EXIT_PRIORITY = 1.75
EMERGENCY_EXIT_PRIORITY = 2.00
MAX_EXIT_TICKS = 2.0


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


@dataclass(frozen=True)
class SameSideSuppression:
    state: str
    same_side_size_mult: float
    same_side_priority: float
    exit_side_priority: float
    exit_side_size_mult: float
    exit_improve_ticks: float
    same_side_passive_ticks: float
    same_side_disabled: bool

    def as_log(self) -> dict[str, Any]:
        return {
            "same_side_version": SAME_SIDE_VERSION,
            "same_side_state": self.state,
            "same_side_size_mult": self.same_side_size_mult,
            "same_side_priority": self.same_side_priority,
            "exit_side_priority": self.exit_side_priority,
            "exit_side_size_mult": self.exit_side_size_mult,
            "exit_improve_ticks": self.exit_improve_ticks,
            "same_side_passive_ticks": self.same_side_passive_ticks,
            "same_side_disabled": int(bool(self.same_side_disabled)),
        }


def same_side_suppression(state: str) -> SameSideSuppression:
    """Discrete suppression. CAUTION starts it; DEFENSIVE disables same-side."""
    token = str(state or STATE_NORMAL).upper()
    if token == STATE_EMERGENCY:
        return SameSideSuppression(
            state=STATE_EMERGENCY,
            same_side_size_mult=0.0,
            same_side_priority=0.0,
            exit_side_priority=EMERGENCY_EXIT_PRIORITY,
            exit_side_size_mult=1.0,
            exit_improve_ticks=MAX_EXIT_TICKS,
            same_side_passive_ticks=0.0,
            same_side_disabled=True,
        )
    if token == STATE_EXIT_ONLY:
        return SameSideSuppression(
            state=STATE_EXIT_ONLY,
            same_side_size_mult=0.0,
            same_side_priority=0.0,
            exit_side_priority=EXIT_ONLY_EXIT_PRIORITY,
            exit_side_size_mult=1.0,
            exit_improve_ticks=MAX_EXIT_TICKS,
            same_side_passive_ticks=0.0,
            same_side_disabled=True,
        )
    if token == STATE_DEFENSIVE:
        return SameSideSuppression(
            state=STATE_DEFENSIVE,
            same_side_size_mult=0.0,
            same_side_priority=0.0,
            exit_side_priority=DEFENSIVE_EXIT_PRIORITY,
            exit_side_size_mult=DEFENSIVE_EXIT_MULT,
            exit_improve_ticks=DEFENSIVE_EXIT_TICKS,
            same_side_passive_ticks=0.0,
            same_side_disabled=True,
        )
    if token == STATE_CAUTION:
        return SameSideSuppression(
            state=STATE_CAUTION,
            same_side_size_mult=CAUTION_ENTRY_MULT,
            same_side_priority=CAUTION_SAME_PRIORITY,
            exit_side_priority=CAUTION_EXIT_PRIORITY,
            exit_side_size_mult=CAUTION_EXIT_MULT,
            exit_improve_ticks=CAUTION_EXIT_TICKS,
            same_side_passive_ticks=CAUTION_SAME_PASSIVE_TICKS,
            same_side_disabled=False,
        )
    return SameSideSuppression(
        state=STATE_NORMAL,
        same_side_size_mult=1.0,
        same_side_priority=1.0,
        exit_side_priority=1.0,
        exit_side_size_mult=1.0,
        exit_improve_ticks=0.0,
        same_side_passive_ticks=0.0,
        same_side_disabled=False,
    )


def apply_fill_priority(
    *,
    buy_fill: float,
    sell_fill: float,
    inventory_sign: float,
    suppression: SameSideSuppression,
) -> tuple[float, float]:
    """Lower same-side fill score, raise exit-side fill score.

    Parent admits a side when fill >= min_fill_prob. Scaling the estimate
    is the per-side priority knob without changing book rank.
    """
    buy = _clip01(buy_fill)
    sell = _clip01(sell_fill)
    sign = _finite(inventory_sign)
    if abs(sign) <= 1e-12:
        return buy, sell
    same_p = max(0.0, _finite(suppression.same_side_priority))
    exit_p = max(0.0, _finite(suppression.exit_side_priority))
    if sign > 0.0:
        buy = _clip01(buy * same_p)
        sell = _clip01(sell * exit_p)
        if suppression.same_side_disabled:
            buy = 0.0
    else:
        sell = _clip01(sell * same_p)
        buy = _clip01(buy * exit_p)
        if suppression.same_side_disabled:
            sell = 0.0
    return buy, sell


def apply_exit_competitiveness(
    *,
    bid_px: float,
    ask_px: float,
    best_bid: float,
    best_ask: float,
    tick_size: float,
    inventory_sign: float,
    suppression: SameSideSuppression,
    price_decimals: int,
) -> tuple[float, float]:
    """Pull the reducing quote toward touch; optionally fade the increasing quote."""
    tick = max(_finite(tick_size, 1e-9), 1e-9)
    bid = _finite(bid_px)
    ask = _finite(ask_px)
    best_bid_px = _finite(best_bid)
    best_ask_px = _finite(best_ask)
    sign = _finite(inventory_sign)
    dec = max(0, int(price_decimals))
    exit_ticks = max(0.0, _finite(suppression.exit_improve_ticks))
    same_ticks = max(0.0, _finite(suppression.same_side_passive_ticks))
    if abs(sign) > 1e-12 and exit_ticks > 0.0:
        if sign > 0.0:
            improved = best_ask_px - exit_ticks * tick
            floor = best_bid_px + tick
            ask = min(ask, max(floor, improved))
        else:
            improved = best_bid_px + exit_ticks * tick
            ceiling = best_ask_px - tick
            bid = max(bid, min(ceiling, improved))
    if abs(sign) > 1e-12 and same_ticks > 0.0 and not suppression.same_side_disabled:
        if sign > 0.0:
            bid = min(bid, best_bid_px - same_ticks * tick)
        else:
            ask = max(ask, best_ask_px + same_ticks * tick)
    bid = round(bid, dec)
    ask = round(ask, dec)
    if bid <= 0.0 or bid >= ask:
        return round(_finite(bid_px), dec), round(_finite(ask_px), dec)
    return bid, ask


def side_is_suppressed(
    *,
    side: str,
    inventory_sign: float,
    suppression: SameSideSuppression,
) -> bool:
    if abs(_finite(inventory_sign)) <= 1e-12:
        return False
    if not is_same_side_entry(side, inventory_sign):
        return False
    if suppression.same_side_disabled:
        return True
    return _finite(suppression.same_side_priority) <= 1e-12
