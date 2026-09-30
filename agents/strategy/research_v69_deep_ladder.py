# SPDX-License-Identifier: MIT
"""v6.9: a deep ladder -- two more deep orders per side at the far end of each book's own sweep record, a larger
bound, a paced book that keeps only its deepest order, and the pooled board as the regime gate again.

Why (mainnet, sim 20260924_1653, replayed 09-29/30 JST on the recorded prints with the validator's arithmetic --
proportional_both pay, 0.5 x making share + 0.5 x net-alpha share of the eligible uids, against the field at six
snapshot times; scratchpad fleet8/vm69y.py, r69b, r69c, r69t2).  The v6.8 model reproduces live UID 94 in the
00:05-00:15 JST burst (making 245 / alpha 594 / 1.03M quote against 194 / 539 / 1.05M).

* Sweeps reach well past where the deep layer rests, and they come back: reversion after multi-book sweeps in
  sim-hours 0-19 is +6.6 bps at 5-10 bps deep, +10 at 10-20, +20 at 20-40, +26 at 40-80, ~+55 at 80-160.  Our p97
  order fills on the first ticks of a sweep; the rest of the sweep passes it.
* A level that does not retreat is refuted: over a whole simulation an order that never leaves the mid's way becomes
  a touch order whenever the mid drifts into it and spends the capped volume in ordinary hours (-40..-85% pay).  The
  0.5-1.5 x depth band is what tells a sweep (it fills before the retreat lands) from a drift (the order retreats).
* Whole simulation with the daily cap consumed from its start (the setting that decides pay): the v6.8 model 1.112%
  mean pay (windows 1.83/1.72/1.25/1.13/0.62); the ladder below 1.585% (2.30/2.24/1.85/1.73/1.25), +42.6%, kept books
  124-127, kappa 2.1-4.8 with the worst book -100..-170 (the best margin of every ladder tried), mean inventory 2.9
  base (max 16).  Refuted beside it: 4/8-base levels (+39%, worst book -305), bound 16, three levels, 8/8 sizes, a
  vacuum-only level 0 (-34%), a post-fill room test (-14%).
* On the previous (trending) simulation the pooled board keeps the ladder out (0.50M quote over the whole record);
  a gate per book traded it at a loss (A0 -16k alpha / 1.7M, the ladder -79k / 3.6M).

The rules (one causal change each):

* S1 ``research_v69_deep_ladder``: per side, two more deep orders at the book's own sweep-record p99 and max, sized
  one and two deep clips, each judged by the same 0.5-1.5 band on its own depth and living the same 50 sim-s.  A
  level no deeper than level 0 (or, in a blown-out spread, than the vacuum order) is not placed.  Level 0 is v6.3.3's
  order unchanged (vacuum, spacing, band).  Client ids 40000 + 10 x book + 3/4 (p99) and 5/6 (max); ownership is per
  (book, side, level) for deep orders in the final validator, so a resting level does not refuse the next; still one
  new order per book side per request (the maker sanitizer's and A1.7.4.3.1's same-response rule), shallow first.  The exposure cap counts every level in
  flight on top of the bound.  STRUCTURAL: the depths are the book's own record, the sizes multiples of the clip;
  OBSERVED: the quantiles (p99, max) and the multiples (1, 2) won the r69 grids.
* S2 ``research_v65_deep_max_clips`` 4.0: the bound is four deep clips (8 base at the 2-base clip) -- room for one
  fill of every level.  The room test stays pre-fill (a post-fill test lost 14%).  OBSERVED: the r69c grid (8 vs 16).
* S3 ``research_v69_deep_first_pace``: a book ahead of its volume line withholds level 0 and the p99 level on the side
  that adds and keeps its deepest level (v6.4.1 withheld every adding order).  STRUCTURAL: under the cap the deepest
  fills are the ones worth the volume (+8.7% on the same ladder, less inventory).
* S4 ``research_v68_book_gate`` 0: the pooled board (mean paper alpha of the books with a paper fill above 0) is the
  regime gate again; each book's own floor stays.  v6.8 S3 (the record in the session file) keeps it through a
  restart, which is why v6.8 had retired it.  STRUCTURAL.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from research_v633_deep_layer import deep_level
from research_v65_deep_clips import book_bound
from research_v6215_order_life import SIDE_BUY, SIDE_SELL, order_side_token

V69_DEEP_LADDER_VERSION = "deep_ladder_v6_9"
V691_FREE_BASE_VERSION = "deep_ladder_free_base_v6_9_1"

LADDER_QUANTILES = (0.99, 1.0)      # OBSERVED (r69 grids): levels 1 and 2 at the sweep record's p99 and max
LADDER_CLIPS = (1.0, 2.0)           # OBSERVED (r69c): their sizes in deep clips (2 and 4 base at the 2-base clip)
DEEP_MAX_CLIPS = 4.0                # OBSERVED (r69c): the bound in deep clips, launched as research_v65_deep_max_clips
LEVEL_GAP_TICKS = 0.5               # STRUCTURAL: a level must rest at least a price step deeper than level 0
DEEPEST_LEVEL = len(LADDER_QUANTILES)
WHOLE_SIDE = -1                     # an order that is not a deep order (or cannot be read) holds its whole side
CANCEL_LEVEL_OFF = "DEEP_LEVEL_OFF"


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def ladder_levels(depths: Iterable[Any] | None, level0_depth: Any) -> list[tuple[int, float, float]]:
    """(level, depth in ticks, size in deep clips) of every ladder level resting deeper than level 0 by at least
    LEVEL_GAP_TICKS; ``depths`` are the book's record at LADDER_QUANTILES (None until the record is long enough)."""
    if depths is None:
        return []
    d0 = _finite(level0_depth)
    out = []
    for i, (d, clips) in enumerate(zip(list(depths), LADDER_CLIPS)):
        dk = _finite(d)
        if dk is None or (d0 is not None and dk <= d0 + LEVEL_GAP_TICKS):
            continue
        out.append((i + 1, dk, float(clips)))
    return out


def level_quantity(clip: Any, level: int) -> float:
    """A level's order size in base: level 0 is one deep clip, the ladder's levels their multiples of it."""
    c = max(0.0, _finite(clip) or 0.0)
    k = int(level)
    if k <= 0 or k > len(LADDER_CLIPS):
        return c
    return c * float(LADDER_CLIPS[k - 1])


def quantity_within_free(q: Any, free: Any, min_order: Any, volume_decimals: Any) -> float:
    """C2 (v6.9.1): an order no larger than what the account can reserve for it, floored to the volume grid; 0.0 when
    what is free is below the venue's minimum order (a size the venue would refuse anyway).

    STRUCTURAL: the venue reserves a limit sell's base (a buy's quote) at placement and refuses the order outright
    when the free balance is short (OrderPlacementValidator: ``!baseBalance.canReserve(volume)`` ->
    INSUFFICIENT_BASE); ``free`` already excludes what our resting orders on the book reserve.  A miner's base on a
    book is its Pareto endowment ``wealth / ((1 + r) * price)`` with ``r = scale * (1 - u) ** (-1 / shape)``
    (Balances::fromXML, type="pareto"; scale 1, shape 2, wealth 50,000, price 300 -> at most 83.3 base, median
    69, below 12 base on 0.6% of books, below 20 on 1.9%).  The 2/2/4 ladder reserves up to 8 base of sells on top
    of a short bound of 8, so on such a book the level's whole size is refused every request (09-30: UID 94 books
    15/62, 251 book 78, 165 books 49/100, 104 book 6, 88 book 98 -- 5,000 refusals, the books one-sided for
    7-62 min).  The level keeps its identity (client id, ownership slot, depth); only its size shrinks.
    ``free``/``q`` that cannot be read leave the size alone (a bad read never stops the layer)."""
    size = _finite(q)
    if size is None or size <= 0.0:
        return 0.0
    avail = _finite(free)
    if avail is None:
        return float(size)
    if avail < size:
        try:
            d = max(0, int(volume_decimals))
        except (TypeError, ValueError):
            d = 4
        scale = 10.0 ** d
        size = math.floor(max(0.0, avail) * scale + 1e-9) / scale
    floor = max(0.0, _finite(min_order) or 0.0)
    return float(size) if size + 1e-12 >= floor and size > 0.0 else 0.0


def deep_first_keeps(level: int) -> bool:
    """S3: the order a book ahead of its volume line keeps on its adding side -- the deepest level only."""
    return int(level) == DEEPEST_LEVEL


def live_slots(rows: Iterable[tuple[Any, Any]]) -> dict[str, frozenset]:
    """Per side, what our live orders hold: the deep level of each deep order, WHOLE_SIDE for any other order.

    ``rows`` are (side, client id) pairs -- acknowledged orders and local reservations.  An order whose side cannot
    be read holds both sides whole, as v6.2.15 S2's live_sides treats it."""
    out: dict[str, set] = {SIDE_BUY: set(), SIDE_SELL: set()}
    for side, cid in rows:
        token = order_side_token(side)
        if not token:
            out[SIDE_BUY].add(WHOLE_SIDE)
            out[SIDE_SELL].add(WHOLE_SIDE)
            continue
        level = deep_level(cid)
        out[token].add(WHOLE_SIDE if level is None else int(level))
    return {k: frozenset(v) for k, v in out.items()}


def slot_taken(slots: dict | None, side: Any, level: int) -> bool:
    """Does a live order already hold this deep level (or the whole side) on this side?"""
    held = (slots or {}).get(order_side_token(side)) or frozenset()
    return WHOLE_SIDE in held or int(level) in held


def caps_for(book_count: int, *, clip: Any, max_clips: Any) -> dict[str, float]:
    """The exposure bound the ladder implies: every book may hold its bound (pre-fill, so up to the bound with every
    level of one side still resting) plus every level of that side in flight."""
    n = max(1, int(book_count))
    c = max(0.0, _finite(clip) or 0.0)
    in_flight = c * (1.0 + sum(float(x) for x in LADDER_CLIPS))
    total = float(n) * (book_bound(c, max_clips) + in_flight)
    return {"research_max_total_abs_base": total, "research_a195_max_seed_abs_base": total}
