# SPDX-License-Identifier: MIT
"""v6.1: the fee-inclusive FIFO break-even of the lot a close would hit.

Measured on mainnet UID 34 (v6.0.2, log 20260917_214432, ticks 0-3,647, 2026-09-18):

* 149 ABSOLUTE_PROTECTION_REDUCE takers, 148 losing, realized median -39.4 bps; every one of
  the 149 decisions already sat below the -25 bps floor when the trigger fired.
* At those same evaluations the agent's own fee-inclusive maker close read median +119.8 bps,
  and a >= +1 bps maker close was available on 128 of the 149.
* 315 A1.9.9.1 R4 rows converted a positive maker (median +130 bps) into a taker.

The cause is one arithmetic.  ``classify_risk_band`` reads the A1.7.1 mid MTM, which excludes
fees.  The validator's ``match_trade_fifo`` charges BOTH legs' fees when a lot closes -- and on
mainnet both legs are rebates (maker fee median -32.5 bps at 4,017 fills; entry rebate on the
stopped lots median +56 bps).  Every A1.7.x / A1.9.x loss rule was calibrated on testnet, where
the maker fee is ~0 and the two arithmetics coincide.  On mainnet they differ by two rebates, and
the rules turn lots that would close positive into realized losses.

This module is that arithmetic, on the validator's own lot tuple ``(timestamp, quantity, price,
open_fee)`` (``research_v5_validator_fifo.match_trade_fifo``; fee in quote, positive = cost,
negative = rebate, the opening fee attached to the lot and charged at the close).

* ``fifo_close_pnl``      what the validator realizes for closing a lot at a price and a fee.
* ``break_even_price``    the price where that is exactly zero (plus a target in bps).
* ``floor_price``         break-even snapped onto the price grid, never past it.
* ``taker_close_pnl``     the same arithmetic for a market close that may span several lots.

Pure functions only; the strategy owns the deques.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

V61_LOT_FLOOR_VERSION = "lot_floor_v6_1_0"
V61_STATE_EVERY_TICKS = 100

# The validator's lot tuple, by position.
LOT_TS, LOT_QTY, LOT_PRICE, LOT_FEE = 0, 1, 2, 3

_BPS = 1e-4


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def lots_closed_by(positions: Any, *, long_position: bool) -> Sequence[Any]:
    """The deque a close of this side walks: longs for a long position, shorts for a short."""
    if not positions:
        return ()
    try:
        return positions["longs" if long_position else "shorts"]
    except (KeyError, TypeError):
        return ()


def head_lot(positions: Any, *, long_position: bool) -> tuple[Any, float, float, float] | None:
    """The oldest open lot of the side -- the one the validator closes first."""
    lots = lots_closed_by(positions, long_position=long_position)
    for lot in lots:
        try:
            ts, qty, price, fee = lot
        except (TypeError, ValueError):
            continue
        q = _finite(qty)
        if q > 0.0:
            return ts, q, _finite(price), _finite(fee)
    return None


def fifo_close_pnl(
    lot: Any, *, close_price: Any, close_fee_bps: Any, long_position: bool, qty: Any = None,
) -> float:
    """Quote realized by the validator for closing ``qty`` (default: the whole lot) of ``lot``.

    ``close_fee_bps`` is the closing leg's fee as basis points of the closing notional
    (negative = rebate).  A partial close prorates the stored opening fee by quantity, exactly as
    ``match_trade_fifo`` does.
    """
    try:
        _ts, lot_qty, lot_price, open_fee = lot
    except (TypeError, ValueError):
        return 0.0
    q0 = _finite(lot_qty)
    if q0 <= 0.0:
        return 0.0
    q = q0 if qty is None else min(max(_finite(qty), 0.0), q0)
    if q <= 0.0:
        return 0.0
    price = _finite(close_price)
    if price <= 0.0:
        return 0.0
    p0 = _finite(lot_price)
    fee_open = _finite(open_fee) * (q / q0)
    fee_close = _finite(close_fee_bps) * _BPS * price * q
    price_pnl = (price - p0) * q if long_position else (p0 - price) * q
    return price_pnl - fee_open - fee_close


def fifo_close_net_bps(
    lot: Any, *, close_price: Any, close_fee_bps: Any, long_position: bool, qty: Any = None,
) -> float:
    """``fifo_close_pnl`` as basis points of the closing notional; NaN when there is no notional."""
    try:
        _ts, lot_qty, _p, _f = lot
    except (TypeError, ValueError):
        return float("nan")
    q0 = _finite(lot_qty)
    q = q0 if qty is None else min(max(_finite(qty), 0.0), q0)
    price = _finite(close_price)
    if q <= 0.0 or price <= 0.0:
        return float("nan")
    pnl = fifo_close_pnl(
        lot, close_price=price, close_fee_bps=close_fee_bps, long_position=long_position, qty=q,
    )
    return pnl / (price * q) / _BPS


def break_even_price(
    lot: Any, *, close_fee_bps: Any, long_position: bool, target_bps: Any = 0.0,
) -> float | None:
    """The close price at which the validator realizes exactly ``target_bps`` of notional.

    Long (closed by a sell):  (P - p0) q - f - r P q = t P q  ->  P = (p0 q + f) / (q (1 - r - t))
    Short (closed by a buy):  (p0 - P) q - f - r P q = t P q  ->  P = (p0 q - f) / (q (1 + r + t))

    with r and t as fractions.  None when the lot is empty or the fee/target leave no price.
    """
    try:
        _ts, lot_qty, lot_price, open_fee = lot
    except (TypeError, ValueError):
        return None
    q = _finite(lot_qty)
    p0 = _finite(lot_price)
    if q <= 0.0 or p0 <= 0.0:
        return None
    f = _finite(open_fee)
    r = _finite(close_fee_bps) * _BPS
    t = _finite(target_bps) * _BPS
    if long_position:
        denom = q * (1.0 - r - t)
        numer = p0 * q + f
    else:
        denom = q * (1.0 + r + t)
        numer = p0 * q - f
    if denom <= 0.0 or numer <= 0.0:
        return None
    return numer / denom


def snap_to_grid(price: Any, *, price_decimals: Any, long_position: bool) -> float:
    """Move a price onto the venue grid without crossing the floor: up for a sell, down for a buy.

    The frozen placement rounds ``close_price`` to ``priceDecimals`` with ``round``; a floor
    rounded the wrong way is a loss by exactly one grid step, so the snap is directional here.
    """
    p = _finite(price)
    try:
        dec = max(0, int(price_decimals))
    except (TypeError, ValueError):
        dec = 0
    step = 10.0 ** (-dec)
    # A price already on the grid must not move by a floating-point hair.
    units = p / step
    nearest = round(units)
    if abs(units - nearest) < 1e-9:
        return round(nearest * step, dec)
    snapped = math.ceil(units) if long_position else math.floor(units)
    return round(snapped * step, dec)


def floor_price(
    lot: Any, *, close_fee_bps: Any, long_position: bool, target_bps: Any, price_decimals: Any,
) -> float | None:
    """The lowest sell (highest buy) that closes ``lot`` at or above ``target_bps``, on the grid."""
    raw = break_even_price(
        lot, close_fee_bps=close_fee_bps, long_position=long_position, target_bps=target_bps,
    )
    if raw is None:
        return None
    return snap_to_grid(raw, price_decimals=price_decimals, long_position=long_position)


def floored_close_price(desired: Any, floor: Any, *, long_position: bool) -> float:
    """The maker exit price: never below the floor for a sell, never above it for a buy."""
    d = _finite(desired)
    if floor is None:
        return d
    f = _finite(floor)
    if d <= 0.0:
        return f
    return max(d, f) if long_position else min(d, f)


def taker_close_pnl(
    positions: Any, *, qty: Any, touch_price: Any, taker_fee_bps: Any, long_position: bool,
) -> tuple[float, float]:
    """Quote the validator would realize for a market close of ``qty`` at ``touch_price``.

    Walks the FIFO deque the way ``match_trade_fifo`` does, without mutating it.  Returns
    ``(pnl, closed_qty)``; quantity beyond the open lots opens a new position and realizes nothing.
    """
    remaining = max(_finite(qty), 0.0)
    price = _finite(touch_price)
    total = 0.0
    closed = 0.0
    if remaining <= 0.0 or price <= 0.0:
        return 0.0, 0.0
    for lot in lots_closed_by(positions, long_position=long_position):
        if remaining <= 0.0:
            break
        try:
            _ts, lot_qty, _p, _f = lot
        except (TypeError, ValueError):
            continue
        q0 = _finite(lot_qty)
        if q0 <= 0.0:
            continue
        q = min(q0, remaining)
        total += fifo_close_pnl(
            lot, close_price=price, close_fee_bps=taker_fee_bps, long_position=long_position, qty=q,
        )
        closed += q
        remaining -= q
    return total, closed


def taker_is_acceptable(
    positions: Any, *, qty: Any, touch_price: Any, taker_fee_bps: Any, long_position: bool,
) -> bool:
    """v6.1's executor rule: a market close may leave only if the validator realizes >= 0 on it."""
    pnl, closed = taker_close_pnl(
        positions, qty=qty, touch_price=touch_price, taker_fee_bps=taker_fee_bps,
        long_position=long_position,
    )
    if closed <= 0.0:
        # Nothing to close: the order opens a position, which is not this module's question.
        return True
    return pnl >= 0.0


# ---- the exit decision -------------------------------------------------------------------------

REWRITE_NONE = "NONE"                    # the decision stands.
REWRITE_TAKER_TO_MAKER = "TAKER_TO_MAKER"  # a loss-realizing taker becomes a floored maker.
REWRITE_IDLE_TO_MAKER = "IDLE_TO_MAKER"    # WAIT/PARK on a full lot: rest the floor instead.

ACTION_MAKER = "MAKER_EXIT"
ACTION_TAKER = "TAKER_EXIT"
ACTION_WAIT = "WAIT"
ACTION_PARK = "PARK"


def rewrite_exit_action(
    *,
    enabled: bool,
    action: Any,
    taker_acceptable: bool,
    floor_available: bool,
    inventory_qty: Any,
    min_order: Any,
) -> str:
    """What v6.1 does with the exit the A1.7.x/A1.9.x stack settled on.

    A taker that the validator would book at a loss is refused at the executor anyway, so leaving
    the decision as TAKER means nothing rests and the lot simply ages.  The rewrite makes the
    resting floor the exit.  WAIT and PARK are rewritten for the same reason: on mainnet the touch
    keeps moving away after a stop-out (t60 median +104 bps), so idling is not a recovery plan --
    a maker at the break-even is.

    Anything below one lot is dust and keeps its own path; the floor needs a lot to price against.
    """
    if not enabled or not floor_available:
        return REWRITE_NONE
    name = "" if action is None else str(action)
    if name == ACTION_TAKER:
        return REWRITE_NONE if taker_acceptable else REWRITE_TAKER_TO_MAKER
    if name in (ACTION_WAIT, ACTION_PARK):
        if abs(_finite(inventory_qty)) + 1e-12 >= _finite(min_order, 0.25):
            return REWRITE_IDLE_TO_MAKER
    return REWRITE_NONE
