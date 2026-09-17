# SPDX-License-Identifier: MIT
"""v6.0.0: a nearly full position is a lot, not dust.

Measured on UID 125 (v5.0.3, ticks 0 to 11,113, 2026-09-16):

* All six positions that stayed parked for 4,773 to 8,642 ticks were 0.19 to 0.24999 BASE: two
  entry fills a few base units off the volume grid (books 76, 116), one short entry fill (84), two
  partial exits (95, 101) and a residue under a full short (89).  The build loop skipped every
  position under the 0.25 minimum before exit handling, so these lost exit management and
  protection; the only exit left was a post-only compaction clip at the touch, which the loss guard
  refused from -67 to -167 bps on.  Clearing each at its first refusal would have cost -4.21 quote;
  they cost -146.6 at the end.
* Positions under half a lot cleared on their own (263 births, p90 of 24 ticks).  Stuck risk sits
  near a full lot: 2 of 11 off-grid lots and 3 of 168 lots between 0.1667 and 0.2498 stuck.

A **short lot** is a position with ``boundary <= |q| < min_order``, ``boundary = fraction *
min_order``.  It is exited like a full lot: the exit sends the minimum order, which leaves an
opposite leftover strictly smaller than the position whenever ``fraction > 0.5``
(``research_exit_quantity.choose_reduce_quantity`` already allows exactly that overshoot).  The
default fraction is two thirds, so every short-lot exit at least halves the exposure.

**Dust** is what remains: ``eps < |q| < boundary``.  A position within ``tolerance`` below a full
lot (the v5.0.2 clip tolerance, two base units) is always a short lot, whatever the fraction.

Pure functions only; the strategy owns the state.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

V600_SHORT_LOTS_VERSION = "short_lots_v6_0_0"

V600_SHORT_LOT_FRACTION_DEFAULT = 2.0 / 3.0
V600_STATE_EVERY_TICKS = 100
# A leftover this many base units or fewer joins the v5.0.2 residue ledger (the clip tolerance).
V600_LEFTOVER_UNITS = 2

CLASS_FLAT = "FLAT"
CLASS_DUST = "DUST"
CLASS_SHORT_LOT = "SHORT_LOT"
CLASS_FULL = "FULL"
CLASS_PARKED = "INHERITED_PARKED"

INHERITED_PARK = "park"
INHERITED_EXIT = "exit"
INHERITED_MODES = (INHERITED_PARK, INHERITED_EXIT)

SOURCE_LIVE = "LIVE"
SOURCE_OFF_GRID = "OFF_GRID"
SOURCE_INHERITED = "INHERITED"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def parse_fraction(value: Any) -> float | None:
    """The short-lot boundary as a fraction of the minimum order; None unless 0.5 < f < 1.0.

    At or below one half an exit clip would leave an opposite leftover at least as large as the
    position, which ``choose_reduce_quantity`` rejects, so such a lot could never exit.
    """
    f = _finite(value, float("nan"))
    if not math.isfinite(f) or not 0.5 < f < 1.0:
        return None
    return f


def inherited_mode(value: Any) -> str | None:
    token = str(value if value is not None else "").strip().lower()
    return token if token in INHERITED_MODES else None


def short_lot_boundary(min_order: Any, fraction: Any) -> float:
    m = abs(_finite(min_order))
    f = parse_fraction(fraction)
    if f is None:
        f = V600_SHORT_LOT_FRACTION_DEFAULT
    return m * f


def leftover_tolerance(decimals: Any, units: int = V600_LEFTOVER_UNITS) -> float:
    try:
        d = int(decimals)
    except (TypeError, ValueError):
        return 0.0
    if d < 0:
        return 0.0
    return max(0, int(units)) * 10.0 ** (-d)


def classify_position(
    qty: Any,
    *,
    min_order: Any,
    eps: Any,
    fraction: Any = V600_SHORT_LOT_FRACTION_DEFAULT,
    tolerance: Any = 0.0,
    enabled: bool = True,
) -> str:
    """FLAT, DUST, SHORT_LOT or FULL for one book's current position.

    ``enabled=False`` is v5.0.4: everything under the minimum order is dust.
    """
    q = abs(_finite(qty))
    m = abs(_finite(min_order))
    e = abs(_finite(eps))
    if q <= e:
        return CLASS_FLAT
    if m <= 0.0 or q + 1e-12 >= m:
        return CLASS_FULL
    if not enabled:
        return CLASS_DUST
    tol = abs(_finite(tolerance))
    if tol > 0.0 and m - q <= tol + 1e-12:
        return CLASS_SHORT_LOT
    if q + 1e-12 >= short_lot_boundary(m, fraction):
        return CLASS_SHORT_LOT
    return CLASS_DUST


def dust_ceiling(*, min_order: Any, fraction: Any, enabled: bool) -> float:
    """The size below which a position is dust (exclusive)."""
    m = abs(_finite(min_order))
    return short_lot_boundary(m, fraction) if enabled else m


def is_short_lot(qty: Any, **kwargs: Any) -> bool:
    return classify_position(qty, **kwargs) == CLASS_SHORT_LOT


def is_dust(qty: Any, **kwargs: Any) -> bool:
    return classify_position(qty, **kwargs) == CLASS_DUST


def leftover_to_residue(net_after: Any, *, before: Any, flat_eps: Any, tolerance: Any) -> float | None:
    """The leftover a short-lot exit leaves that belongs in the residue ledger, or None.

    Only a leftover on the far side of zero (the exit overshot) and no larger than ``tolerance``
    moves; a flat tracker is v5.0.2's case, and a larger leftover follows the ordinary dust path.
    """
    after = _finite(net_after)
    prior = _finite(before)
    eps = abs(_finite(flat_eps))
    tol = abs(_finite(tolerance))
    if tol <= 0.0 or abs(after) < eps or abs(after) > tol + 1e-12:
        return None
    if prior == 0.0 or prior * after >= 0.0:
        return None
    return after


@dataclass(frozen=True)
class ShortLotExit:
    """What a short-lot position did on one own fill."""

    book_id: int
    before: float
    after: float
    realized_quote: float
    notional_quote: float

    @property
    def realized_bps(self) -> float | None:
        if self.notional_quote <= 0.0:
            return None
        return self.realized_quote / self.notional_quote * 10_000.0


def exit_from_fill(
    book_id: Any,
    *,
    before: Any,
    after: Any,
    price: Any,
    realized_quote: Any,
    min_order: Any,
    eps: Any,
    fraction: Any,
    tolerance: Any,
) -> ShortLotExit | None:
    """A fill that reduced a short lot (the lot was a short lot before the fill), else None."""
    b = _finite(before)
    a = _finite(after)
    if classify_position(b, min_order=min_order, eps=eps, fraction=fraction, tolerance=tolerance) != CLASS_SHORT_LOT:
        return None
    if abs(a) + 1e-12 >= abs(b):
        return None
    closed = min(abs(b), abs(b - a))
    return ShortLotExit(
        book_id=int(book_id),
        before=b,
        after=a,
        realized_quote=_finite(realized_quote),
        notional_quote=closed * abs(_finite(price)),
    )


def median(values: list[float]) -> float | None:
    vals = sorted(float(v) for v in values if math.isfinite(float(v)))
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else 0.5 * (vals[mid - 1] + vals[mid])
