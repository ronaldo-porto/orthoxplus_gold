# SPDX-License-Identifier: MIT
"""v6.4: the deep layer rests inside a blown-out spread, and while its board pays it owns every book.

Why (mainnet, sim 20260924_1653, validator window sim 16,790-27,590, measured 09-26; scratchpad v63/r_top20, r640, r641):

* The skill is scored as kappa_of_alpha over the books whose |alpha| clears the floor, each alpha divided by the median
  absolute deviation of those alphas, its downside cubed: one book losing ten times the typical book sinks it.  Per-book
  alpha is sum (inventory - its mean) x price change, so a book pays for being light while it moves and holding after.
* The high-volume skill leaders (uids 224/45) take 70% of their fills while a book's spread is >= 10 ticks (median ~106
  ticks at their fills inside it): a sweep empties one side, the gap it leaves is where the reversion is largest, and they
  quote inside it (~34 ticks from the mid) for a +24-tick markout at every horizon.  Spreads >= 20 ticks are 1.6% of
  book-states, ~14 per book per sim-hour, 2 states long at the median.  The v6.3.3 deep price is clamped one tick behind
  the touch, so in that gap our order rested behind the far touch instead of inside it.
* UID 94's worst book in the window (-40.7, ten times its typical book) was a touch book: 782 fills of the v6.3 target
  and making layers while the deep layer had shut it; 45% of our fill quantity was touch orders.

The rules:

* S1 vacuum (``research_v64_vacuum``).  A deep order whose book's spread is at least VACUUM_MIN_SPREAD_TICKS rests inside
  the gap, VACUUM_FRACTION of the half-spread from the mid (strictly inside the touch), and is judged for repricing
  against that distance.  OBSERVED: 20 ticks and 0.8 beat 10/30 ticks and 0.5/0.64/0.9 in the replay.
* S2 board owns (``research_v64_board_owns``).  While the deep layer's board is open, no book trades at the touch: a book
  the layer opens is deep-only whatever its inventory (an inventory over the limit rests only on its reducing side,
  the room rule), and a book it does not open cancels its touch and deep orders and quotes nothing.  While the board is
  shut (a trending simulation: 4% open on 20260918_2028) v6.3.2 runs as before.

Replayed with the validator's arithmetic, partial fills by print size and the one-state side-ownership delay:
latest windows making 2,805-4,482 / skill 1.50-3.21 (v6.3.3 1,048-1,577 / 0.83-1.12); the simulation's first windows
3,242-3,583 / 1.81-2.72 (0.60-1.36); the trending simulation unchanged (the fallback).
"""
from __future__ import annotations

from research_v633_deep_layer import SIDE_BUY

V64_BOARD_VERSION = "board_v6_4"

VACUUM_MIN_SPREAD_TICKS = 20.0     # OBSERVED (replay 09-26: 20 over 10 and 30)
VACUUM_FRACTION = 0.8              # OBSERVED (replay 09-26: 0.8 over 0.5, 0.64 and 0.9)
CANCEL_BOARD_OWNS = "V64_BOARD_OWNS"


def vacuum_depth(bid: float, ask: float, tick: float, *, min_spread: float = VACUUM_MIN_SPREAD_TICKS,
                 fraction: float = VACUUM_FRACTION) -> float | None:
    """The distance from the mid (ticks) a deep order rests at inside a blown-out spread; None while it is normal."""
    t = float(tick)
    if not (t > 0.0):
        return None
    spread = (float(ask) - float(bid)) / t
    if spread < float(min_spread):
        return None
    return max(1.0, float(fraction) * 0.5 * spread)


def vacuum_price(mid: float, dist: float, side: str, *, bid: float, ask: float, tick: float, decimals: int) -> float:
    """dist ticks from the mid, strictly inside the touch (at least one tick better than its side's best price and one
    tick short of the other side's)."""
    t, dec = float(tick), int(decimals)
    if side == SIDE_BUY:
        px = round(float(mid) - float(dist) * t, dec)
        return round(min(max(px, round(float(bid) + t, dec)), round(float(ask) - t, dec)), dec)
    px = round(float(mid) + float(dist) * t, dec)
    return round(max(min(px, round(float(ask) - t, dec)), round(float(bid) + t, dec)), dec)
