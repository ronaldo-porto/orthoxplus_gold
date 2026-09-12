# SPDX-License-Identifier: MIT
"""A1.9.5 step 2.5 (F8): make the declared taker loss floor actually bind.

``allowed_loss_floor_bps`` has been computed and logged on every risk-authorized
taker exit since A1.6, and it has never bound anything.  It appears only in
dataclass fields, ``replace()`` assignments and log emitters -- nothing reads it
to constrain an order.  The exit itself goes out through
``_execute_aggressive_close`` as ``response.market_order(...)`` with
``max_slippage`` omitted, and a market order has no limit price, so the floor
cannot bind by construction.

Measured on log 20260912_121832 at tick 4,233, split by trigger:

    trigger                      n   floor   median    worst   breach
    ABSOLUTE_PROTECTION_REDUCE  93   -25.0    -50.4   -213.9     100%
    HARD_ESCAPE_CLIP            51   -25.0    -32.3    -49.2      84%
    RECOVERY_TAKER_REDUCE       19   -25.0    -20.1    -27.0      16%
    NORMAL_TAKER_NONNEGATIVE    12     0.0    +11.3     +2.0       0%

RECOVERY_TAKER_REDUCE is the control: identical declared floor, but it exits
maker-first rather than through the market order, and it respects the floor.
The two triggers that go straight to the unbounded market order carry 99.8% of
the run's cubic downside.  ABSOLUTE is not the kappa killer because its policy
is too aggressive -- it is the kappa killer because it is the trigger wired to
the one exit path with no brake.

THE TRAP IN THIS WIRING.  ``PlaceMarketOrderInstruction.payload()`` serializes
``max_slippage if self.max_slippage is not None else 0.0``, and the evidence
above -- -213.9 bps realized while the payload carried 0.0 -- shows 0.0 does not
bound.  So 0.0 on the wire means unbounded, and a floor of exactly 0.0
(NORMAL_TAKER_NONNEGATIVE declares one) must NOT be forwarded as 0.0: that would
turn "no loss permitted" into "any loss permitted", the precise inversion of
intent.  Every bound produced here is therefore clamped to a strictly positive
minimum.  The same asymmetry exists upstream in the framework itself:
``taos/im/agents/__init__.py`` substitutes 0.01 for an omitted ``max_slippage``
on the exchange path but passes ``None`` through on the simulator path, and the
research agent is on the path that disables the guard.

Bounding an exit can mean it does not fill, which leaves the position open
rather than realizing the loss.  That is the intended trade -- maker-ending
round trips run 95.2% positive against 6.9% for taker-ending ones -- but it is a
real behaviour change, so it is switchable and counted.
"""
from __future__ import annotations

import math
from typing import Any

A195_TAKER_BOUND_VERSION = "direct_taker_bound_v4_16_2_a1_9_5"

# The floor the risk-authorized taker path has always declared.
A195_DEFAULT_TAKER_FLOOR_BPS = -25.0

# 0.0 on the wire means "unbounded" (see the module docstring), so no bound is
# ever emitted below this.  One basis point is tighter than any floor the
# strategy declares and still a real constraint.
A195_MIN_SLIPPAGE_FRACTION = 1e-4

# A bound looser than this is not a bound.  500 bps is already 20x the declared
# floor; anything past it means the caller passed something unintended.
A195_MAX_SLIPPAGE_FRACTION = 5e-2


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def slippage_fraction_for_floor(
    floor_bps: Any,
    *,
    fallback_bps: float = A195_DEFAULT_TAKER_FLOOR_BPS,
    min_fraction: float = A195_MIN_SLIPPAGE_FRACTION,
    max_fraction: float = A195_MAX_SLIPPAGE_FRACTION,
) -> float:
    """Convert a declared loss floor in bps to a ``max_slippage`` fraction.

    The floor is a signed bps figure (-25.0 means "at most 25 bps of loss").
    The venue wants a positive fraction, so the sign is dropped and the
    magnitude scaled.  A missing, non-finite or zero floor falls back to
    ``fallback_bps`` rather than to zero, because zero means unbounded.
    """
    floor = _finite(floor_bps, default=0.0)
    magnitude = abs(floor)
    if magnitude <= 0.0:
        # Includes the NORMAL_TAKER_NONNEGATIVE floor of exactly 0.0.  "No loss
        # permitted" cannot be sent as 0.0, so it takes the tightest real bound
        # the strategy ever declares instead of becoming unbounded.
        magnitude = abs(_finite(fallback_bps, default=A195_DEFAULT_TAKER_FLOOR_BPS))
    fraction = magnitude / 1e4
    lo = max(0.0, _finite(min_fraction, A195_MIN_SLIPPAGE_FRACTION))
    hi = max(lo, _finite(max_fraction, A195_MAX_SLIPPAGE_FRACTION))
    return min(hi, max(lo, fraction))


def taker_bound_report(
    *,
    book_id: Any,
    floor_bps: Any,
    authority: Any = None,
    trigger: Any = None,
    fallback_bps: float = A195_DEFAULT_TAKER_FLOOR_BPS,
) -> dict[str, Any]:
    """Log payload for one bounded taker exit."""
    fraction = slippage_fraction_for_floor(floor_bps, fallback_bps=fallback_bps)
    declared = _finite(floor_bps, default=0.0)
    return {
        "a195_taker_bound_version": A195_TAKER_BOUND_VERSION,
        "book": int(_finite(book_id, -1)),
        "declared_floor_bps": declared,
        "bound_slippage_fraction": fraction,
        "bound_effective_bps": -fraction * 1e4,
        "floor_was_zero": int(abs(declared) <= 0.0),
        "taker_authority": None if authority is None else str(authority),
        "trigger": None if trigger is None else str(trigger),
    }
