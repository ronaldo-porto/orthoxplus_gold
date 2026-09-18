# SPDX-License-Identifier: MIT
"""v6.1.1: the venue truncates prices, and the reprice seed ignored the v6.1 floor.

Found by the v6.1.0 testnet read (UID 82, log 20260918_151435, ticks 1-1,014, 2026-09-18).

1. The venue truncates PRICES exactly as it truncates volume (see research_direct_legacy_baseline
   ``wire_quantity``): ``json_util.cpp`` builds a Bloomberg Decimal128 from the double and
   ``util::round`` is ``trunc``.  So a price whose double sits below its 2-dp decimal -- 228.13 is
   stored as 228.12999999999999545... -- is placed ONE TICK LOWER.  Every SUBMITTED limit order
   was matched to its LimitOrderPlacementEvent by (book, clientOrderId):

   * testnet UID 82:  1,838 of 3,915 placements one tick low (buys 851, sells 987);
   * mainnet UID 34:  1,804 of 3,573 (v6.0.2, the live build);
   * in both logs, every one of them is a double below its decimal, and none of the rest is.

   A sell therefore rests one tick more aggressive than decided and a buy one tick less.  A v6.1
   floored sell rested one tick UNDER its floor, and the A1.9.1 classifier cancelled it as
   NET_BELOW_FLOOR on the next request, 783 times in 1,014 ticks.  ``wire_price`` sends the
   double that truncates to the intended tick: snap to the grid, then lift one ulp when the double
   sits below its decimal -- ``wire_quantity``'s rule, applied to price.

2. The A1.9.1.1 seed in ``_a191_service_reprice_cancels`` judges every resting exit the placement
   path did not visit this request against the bare passive touch.  A floored exit on a held lot
   sits hundreds of ticks behind that touch by design (book 81: floor 204.32 against a bid of
   215.45 -- 1,114 ticks), so it was cancelled as STALE_BEHIND_TOUCH one request after it was
   placed, every time: 2,265 such cancels, and a held lot's exit was live about half the time.
   The comparand must be the price the placement path would itself send -- the passive touch
   floored at the lot's break-even (``research_v61_lot_floor.floored_close_price``).
"""
from __future__ import annotations

import math
from decimal import ROUND_DOWN, Decimal
from typing import Any, Iterable

V611_VERSION = "wire_floor_v6_1_1"
V611_STATE_EVERY_TICKS = 100
V611_SNAP_TOLERANCE = 1e-9
PRICED_ORDER_TYPES = ("PLACE_ORDER_LIMIT",)
DIRECTION_BUY = 0
DIRECTION_SELL = 1


def _decimals(decimals: Any) -> int | None:
    try:
        d = int(decimals)
    except (TypeError, ValueError):
        return None
    return d if d >= 0 else None


def wire_price(price: Any, decimals: Any, *, tolerance: float = V611_SNAP_TOLERANCE) -> Any:
    """The double to send so the venue's truncation lands on the intended tick.

    A price on the grid whose double sits below its decimal is lifted one ulp; every other value
    -- on the grid at or above its decimal, off the grid, non-finite, non-positive -- is returned
    unchanged.  Idempotent: a lifted price sits above its decimal and is not lifted again.
    """
    d = _decimals(decimals)
    try:
        p = float(price)
    except (TypeError, ValueError):
        return price
    if d is None or not math.isfinite(p) or p <= 0.0:
        return price
    snapped = round(p, d)
    if abs(p - snapped) > abs(float(tolerance)):
        return price                          # off the grid: truncates as before
    target = Decimal(format(snapped, f".{d}f"))
    if Decimal(p) >= target:
        return price                          # already lands on the intended tick
    lifted = math.nextafter(snapped, math.inf)
    if Decimal(snapped) >= target:
        lifted = snapped                      # p was a hair under a double that is not
    return lifted if Decimal(lifted) >= target else price


def venue_placed_price(price: Any, decimals: Any) -> float:
    """What the simulator places: a Decimal128 from the double's exact binary value, truncated."""
    d = _decimals(decimals)
    p = float(price)
    if d is None:
        return p
    return float(Decimal(p).quantize(Decimal(1).scaleb(-d), rounding=ROUND_DOWN))


def _direction(instruction: Any, is_dict: bool) -> int | None:
    raw = instruction.get("direction") if is_dict else getattr(instruction, "direction", None)
    try:
        return int(getattr(raw, "value", raw))
    except (TypeError, ValueError):
        return None


def lift_instruction_prices(
    instructions: Iterable[Any], decimals: Any, *, tolerance: float = V611_SNAP_TOLERANCE,
) -> dict[str, int]:
    """Put queued limit-order prices on the wire so each lands on its own tick, in place.

    Returns how many moved, by side.  Market orders and cancels carry no price and are skipped.
    """
    moved = {"buy": 0, "sell": 0, "other": 0}
    for instruction in list(instructions or ()):
        is_dict = isinstance(instruction, dict)
        kind = instruction.get("type") if is_dict else getattr(instruction, "type", "")
        if str(kind or "").upper() not in PRICED_ORDER_TYPES:
            continue
        raw = instruction.get("price") if is_dict else getattr(instruction, "price", None)
        if raw is None:
            continue
        wired = wire_price(raw, decimals, tolerance=tolerance)
        try:
            if float(wired) == float(raw):
                continue
        except (TypeError, ValueError):
            continue
        try:
            if is_dict:
                instruction["price"] = wired
            else:
                instruction.price = wired
        except Exception:
            continue
        side = _direction(instruction, is_dict)
        moved["buy" if side == DIRECTION_BUY else "sell" if side == DIRECTION_SELL else "other"] += 1
    return moved
