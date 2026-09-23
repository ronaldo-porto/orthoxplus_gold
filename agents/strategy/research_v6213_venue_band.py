# SPDX-License-Identifier: MIT
"""v6.2.13: the exposure band bounds the VENUE's position, because that is the position the validator marks.

Why (UID 68, v6.2.12, its own window ticks 1-3,480, 2026-09-23; scratchpad ``skill6212/``):

* The validator's skill leg is kappa over per-book alpha, and alpha is the sum over every print of
  ``(inventory - window-mean inventory) * dp`` with inventory the venue's fill-based position.  It is linear
  in that position: on a trending book every lot held on the wrong side loses in proportion.
* The band -- two minimum orders, v6.2.5's ``BAND_CLIPS`` times the R2 held-book order -- is checked in
  ``sides_verdict`` against ``_direct_signed_inventory``, the ledger.  The ledger is not the venue.  The
  startup seed imported 188.0 of the venue's 197.8 base (121 lots grid-snapped, 14 books as dust), so every
  book began 0.1-0.24 off; the v6.1 divergence check then flagged 581 standing books and rebuilt none.  At
  the end of the window venue - ledger was median -0.10, p10 -0.63.  Venue positions ran to 0.75-1.1 (book
  6: 2.10) while the ledger's band read 0.5; 87% of the over-band adds were on books with a residue >= 0.3.
* Alpha by venue |position| on the eight qualifying-negative books (-394 of alpha): within one lot +37,
  (0.25, 0.5] -257, (0.5, 0.75] -84, (0.75, 1] -14, over 1 -76.  The first lot is fine; the second and
  beyond lose, and five books were 93% of the downside cube.  An exact replay of the same prints with every
  fill that would take the venue position past two lots refused: skill 0.419 -> 1.294 at -5.6% making
  (UID 67 0.459 -> 1.088, UID 82 0.143 -> 0.495).  Every clock-based cross (age or print caps) was
  catastrophic -- crossing at the far touch in a trend is a martingale -- and the paced flat entries were
  irrelevant (refusing only held adds is byte-identical).

The rule: every quote is sized so that its FULL fill keeps the venue's position inside the band.  For a
side with direction ``d`` (+1 buy, -1 sell) the room is ``band - d * venue_net``; the quote is the largest
whole number of minimum orders inside the room, and a side with no whole order of room is refused
(``VENUE_BAND``).  A flat book's entry is at most the band; the reducing side may flip through zero to at
most the band on the other side; a book already past the band gets no adding quote at all.  The ledger
still decides which side is the exit path's (R2, S4, the pacer and the exit path are untouched): this rule
only ever shrinks or refuses a quote the ledger would have placed, never adds one.  When the venue does not
report a position (no account, no initial balance) nothing changes.  No constant is sized from an
observation: the band is v6.2.5's in the venue's unit, and the position is the venue's own.
"""
from __future__ import annotations

import math
from typing import Any

V6213_VENUE_BAND_VERSION = "venue_band_v6_2_13"

SIDE_BUY = "buy"
SIDE_SELL = "sell"
# The side's smallest order would take the venue's position past the band.
REASON_VENUE_BAND = "VENUE_BAND"

_DIRECTION = {SIDE_BUY: 1.0, SIDE_SELL: -1.0}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def venue_room(*, venue_net: Any, band: Any, side: str) -> float:
    """How much this side may trade before the venue's position leaves the band: ``band - d * net``."""
    d = _DIRECTION.get(str(side or "").lower())
    if d is None:
        return 0.0
    return max(0.0, _finite(band)) - d * _finite(venue_net)


def lots_within(room: Any, min_order: Any) -> float:
    """The largest whole number of minimum orders inside ``room``; zero when not even one fits."""
    unit = max(1e-12, _finite(min_order, 0.25))
    r = _finite(room)
    if r < unit - 1e-9:
        return 0.0
    return unit * float(math.floor((r + 1e-9) / unit))


def venue_band(
    *,
    venue_net: Any,
    band: Any,
    min_order: Any,
    sides: dict,
    side_qty: dict,
    ok_token: str,
    refuse_token: str = REASON_VENUE_BAND,
) -> tuple[dict, dict, int, int]:
    """Size (or refuse) each allowed side so its full fill keeps the venue's position inside the band.

    Returns ``(sides, side_qty, refused, capped)`` -- new dicts, the number of sides refused and the number
    whose quote was cut.  A side the caller did not allow is passed through untouched.
    """
    out_sides = dict(sides or {})
    out_qty = dict(side_qty or {})
    refused = capped = 0
    for side in (SIDE_BUY, SIDE_SELL):
        if out_sides.get(side) != ok_token:
            continue
        room = lots_within(venue_room(venue_net=venue_net, band=band, side=side), min_order)
        if room <= 0.0:
            out_sides[side] = refuse_token
            refused += 1
            continue
        if _finite(out_qty.get(side)) > room + 1e-12:
            out_qty[side] = room
            capped += 1
    return out_sides, out_qty, refused, capped
