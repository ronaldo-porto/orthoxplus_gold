# SPDX-License-Identifier: MIT
"""Research inventory-reducing quantity.

Simulator rules (ExchangeConfig / OrderPlacementValidator):

- volume is rounded to ``volumeDecimals``
- ``minOrderSize = max(configured, 10**(-volumeDecimals))``
- BASE volume below min is ``MINIMUM_ORDER_SIZE_VIOLATION``

Base ``_round_order_size`` promotes every clip to min (0.25). That can
leave leftover inventory or flip the book. Realization exits instead
choose a legal quantity that minimizes ``abs(inventory_after)``.

If the exact reducing quantity is legal, use it. If only the exchange
minimum is legal, use it only when it strictly cuts absolute exposure.
Never create a larger opposite-side position just to satisfy min size.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

EXIT_QTY_VERSION = "exit_quantity_v1"

REASON_EXACT = "EXACT_REDUCE"
REASON_MIN_LEGAL = "MIN_LEGAL_REDUCE"
REASON_SAFER_RESIDUAL = "SAFER_RESIDUAL"
REASON_REJECT_ZERO = "REJECT_ZERO"
REASON_REJECT_UNSIZABLE = "REJECT_UNSIZABLE"
REASON_REJECT_LARGER_OPPOSITE = "REJECT_LARGER_OPPOSITE"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def volume_increment(volume_decimals: int) -> float:
    return 10.0 ** (-max(0, int(volume_decimals)))


def exchange_min_order_size(
    configured_min: float,
    volume_decimals: int,
) -> float:
    """Match simulator ``max(minOrderSize, 10**(-volumeDecimals))``."""
    tick = volume_increment(volume_decimals)
    return max(max(0.0, _finite(configured_min)), tick)


def round_volume(quantity: float, volume_decimals: int) -> float:
    """Round to exchange volume decimals. Does not promote to min size."""
    return round(max(0.0, _finite(quantity)), max(0, int(volume_decimals)))


def inventory_after_reduce(inventory: float, reduce_qty: float) -> float:
    before = _finite(inventory)
    qty = max(0.0, _finite(reduce_qty))
    if abs(before) <= 1e-18:
        return 0.0
    sign = 1.0 if before > 0.0 else -1.0
    return before - sign * qty


def _legal(quantity: float, min_order: float, volume_decimals: int) -> bool:
    qty = round_volume(quantity, volume_decimals)
    if qty <= 1e-18:
        return False
    if abs(qty - quantity) > 10.0 ** (-max(0, int(volume_decimals)) - 2):
        qty = round_volume(quantity, volume_decimals)
    return qty + 1e-12 >= max(0.0, _finite(min_order))


@dataclass(frozen=True)
class ReduceQuantityDecision:
    quantity: float
    inventory_before: float
    inventory_after: float
    desired: float
    min_order: float
    volume_decimals: int
    reason: str

    def as_log(self) -> dict[str, Any]:
        return {
            "exit_qty_version": EXIT_QTY_VERSION,
            "exit_qty": self.quantity,
            "exit_qty_desired": self.desired,
            "exit_qty_reason": self.reason,
            "inventory_before": self.inventory_before,
            "inventory_after": self.inventory_after,
            "min_order": self.min_order,
            "volume_decimals": int(self.volume_decimals),
        }


def _decision(
    *,
    quantity: float,
    inventory: float,
    desired: float,
    min_order: float,
    volume_decimals: int,
    reason: str,
) -> ReduceQuantityDecision:
    qty = round_volume(quantity, volume_decimals)
    after = inventory_after_reduce(inventory, qty)
    return ReduceQuantityDecision(
        quantity=qty,
        inventory_before=_finite(inventory),
        inventory_after=after,
        desired=round_volume(desired, volume_decimals),
        min_order=min_order,
        volume_decimals=volume_decimals,
        reason=reason,
    )


def choose_reduce_quantity(
    *,
    inventory: float,
    desired: float,
    min_order: float,
    volume_decimals: int = 4,
) -> ReduceQuantityDecision:
    """Pick a legal reducing quantity. Exact size wins when the exchange allows it."""
    inv = _finite(inventory)
    vol_dec = max(0, int(volume_decimals))
    floor = exchange_min_order_size(min_order, vol_dec)
    flatten = round_volume(abs(inv), vol_dec)
    want = round_volume(min(max(0.0, _finite(desired)), abs(inv)), vol_dec)

    def reject(reason: str) -> ReduceQuantityDecision:
        return _decision(
            quantity=0.0,
            inventory=inv,
            desired=want,
            min_order=floor,
            volume_decimals=vol_dec,
            reason=reason,
        )

    if flatten <= 1e-18:
        return reject(REASON_REJECT_ZERO)

    def after_abs(qty: float) -> float:
        return abs(inventory_after_reduce(inv, qty))

    def reduces(qty: float) -> bool:
        leftover = after_abs(qty)
        return leftover <= 1e-12 or leftover + 1e-12 < abs(inv)

    def larger_opposite(qty: float) -> bool:
        after = inventory_after_reduce(inv, qty)
        return after * inv < 0.0 and abs(after) + 1e-12 >= abs(inv)

    leftover = max(0.0, flatten - want)
    dust_leftover = leftover > 1e-18 and leftover + 1e-12 < floor

    # Exact intended reduce, if the exchange will accept it.
    # If that would leave unsizable dust, flatten when flatten is legal.
    if want > 1e-18 and _legal(want, floor, vol_dec) and reduces(want) and not larger_opposite(want):
        if dust_leftover and _legal(flatten, floor, vol_dec):
            return _decision(
                quantity=flatten,
                inventory=inv,
                desired=want,
                min_order=floor,
                volume_decimals=vol_dec,
                reason=REASON_SAFER_RESIDUAL,
            )
        return _decision(
            quantity=want,
            inventory=inv,
            desired=want,
            min_order=floor,
            volume_decimals=vol_dec,
            reason=REASON_EXACT,
        )

    # Flatten when a legal exact clip would leave unsizable dust.
    if dust_leftover and _legal(flatten, floor, vol_dec):
        return _decision(
            quantity=flatten,
            inventory=inv,
            desired=want,
            min_order=floor,
            volume_decimals=vol_dec,
            reason=REASON_SAFER_RESIDUAL,
        )

    # Same-side exchange minimum. Better than sending nothing; does not flip.
    if _legal(floor, floor, vol_dec) and floor <= flatten + 1e-12 and reduces(floor):
        return _decision(
            quantity=floor,
            inventory=inv,
            desired=want,
            min_order=floor,
            volume_decimals=vol_dec,
            reason=REASON_MIN_LEGAL,
        )

    # Min overshoots and flips. Allow only when residual is strictly smaller.
    if _legal(floor, floor, vol_dec) and floor > flatten + 1e-12:
        if larger_opposite(floor) or not reduces(floor):
            return reject(REASON_REJECT_LARGER_OPPOSITE)
        return _decision(
            quantity=floor,
            inventory=inv,
            desired=want,
            min_order=floor,
            volume_decimals=vol_dec,
            reason=REASON_SAFER_RESIDUAL,
        )

    return reject(REASON_REJECT_UNSIZABLE)
