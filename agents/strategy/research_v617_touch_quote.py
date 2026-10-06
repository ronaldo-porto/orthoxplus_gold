# SPDX-License-Identifier: MIT
"""v6.17: the fair-value touch quote -- the anchored makers' toward-side order, added to the deep layer.

Why (mainnet 10-06 JST; scratchpad k21/, both simulations seen):

* The makers that beat us on BOTH halves of the pay in both simulations (5GFD family 113/250/162/91/213/203/138/188/128,
  5Dyy family 50/51/30/18, 93) were reconstructed order by order: their client-id counters attribute every order of
  theirs that reached the touch (hold-out accuracy 99.6-100%).  94-99.7% of those orders sit on the side that trades
  toward the validator-published fundamental, at the touch (they improve or join it), 4-16 base, re-priced within 1-2
  states, and never inside the fundamental (edge at placement p5 +4..+10 bps).  Their position points toward the
  fundamental (p50 ~30 base per book) and is unwound only by the same side after the fundamental flips.
* Their fills capture +1.0..+1.5 bps against the 31-print centred mean with an alpha weight of +7..+13 bps of notional;
  ours capture +0.9 bps with -2..-3 bps.  67% of our orders that ever reach the touch are on the side trading AGAINST the
  fundamental (we reach it only when the touch is consumed down to our deep levels) -- the takers trading toward the
  fundamental fill us; nothing of ours rests where the noise flow trading away from it arrives first.
* Replayed with the validator's arithmetic (k21/vm617.py: v6.16 as built plus this quote under the live order rules --
  the deep layer places first on a side, a cancelled quote waits until the cancel is seen, the deep layer's bound and
  volume line), fresh UID, making share and alpha against the field in the same windows:
  new simulation (the gap flips ~12 times per book per hour; sim 658-5,830): making share +32.0% (opening burst +38.9%,
  then +28.0% and +36.9%), net alpha +556 against +8, volume +24%;
  previous simulation (quiet trend, persistent gap; the 7 windows with the fundamental): making share +3.1%, ahead in
  5 of 7 (the 3 partly covered windows +12.6%, 3 of 3), net alpha -16.2k against -26.7k.
* Refuted on the same data: improving the touch by a tick (previous simulation -33..-47%: it buys the trend's flow a tick
  worse), two to four deep clips (-2..-11% there), a quote outside the volume line (spends a day's cap in 4-6 sim-hours
  in the flipping regime), and the other side's price limit again (-64.5% making there for +7.1k alpha).

The rule (one switch, ``research_v617_touch_quote``), on a book the deep layer trades whose fundamental page of this
simulation is fresh (v6.16's feed):

* ONE post-only order joins the touch on the side that trades toward the fundamental (a bid at the best bid when the
  fundamental is above the mid, an offer at the best ask when it is below), one deep clip, while its own price is at
  least ``THRESHOLD_BPS`` on the safe side of the fundamental (bid <= fp x (1 - h), offer >= fp x (1 + h)).
* It rests while the touch is less than ``REST_TICKS`` better than it; it is cancelled when the fundamental's side
  flips, its own edge falls below the threshold, the touch moved ``REST_TICKS`` past it, its side would add while the
  book runs ahead of its volume line, or the add would pass the deep layer's bound.  A cancelled side is re-placed
  only after the cancel is seen (the A1.7.4.3.1 rule), and never on a side a placement of this response already took
  (the deep layer places first).
* Its client ids are their own slot: it never holds a deep ladder level, nor a level it.
Without a fresh page of this simulation, on a book the deep layer does not trade, or with the switch off, the book
is v6.16 exactly.

STRUCTURAL: the side is the simulator's own reversion target; the threshold is the validator's size-neutral alpha hurdle
(0.6.2 ``hurdle_bps`` = 2.3, the bar a book's alpha must clear per unit of filled notional); the clip, the bound, the
volume line and the order rules are the deep layer's.  OBSERVED: the rest band (k21: 1, 2 and 3 ticks equal within
noise; 2 spends fewer cancels).
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from research_v633_deep_layer import SIDE_BUY, SIDE_SELL
from research_v6215_order_life import order_side_token
from research_v641_pace import adds as v641_adds
from research_v69_deep_ladder import live_slots as v69_live_slots

V617_TOUCH_QUOTE_VERSION = "touch_quote_v6_17"
CLIENT_BASE = 43000          # 43001..44272: (buy, sell) = 43000 + 10 x book + 1 | 2 (the deep layer owns 40000-41999)
CLIENT_SPAN = 2000
THRESHOLD_BPS = 2.3          # STRUCTURAL: the validator's alpha hurdle (bps of the uid's own filled notional per book)
CLIP_DEEP_CLIPS = 1.0        # STRUCTURAL: the deep layer's own clip (k21: 2-4 clips won the flipping simulation, lost the quiet trend)
REST_TICKS = 2               # OBSERVED (k21): re-placed once the touch is this many ticks better than the quote
TOUCH_SLOT = 9               # its slot in v6.9's per-(side, level) ownership (deep levels are 0-2)
PLACEMENT_TYPES = ("PLACE_ORDER_LIMIT", "PLACE_ORDER_MARKET")

CANCEL_FLIPPED = "TOUCH_FLIPPED"      # the fundamental is now on the other side of the mid
CANCEL_EDGE = "TOUCH_EDGE"            # the quote's own price is no longer THRESHOLD_BPS on the safe side
CANCEL_LEFT = "TOUCH_LEFT"            # the touch moved REST_TICKS or more past the quote
CANCEL_PACED = "TOUCH_PACED"          # its side adds while the book runs ahead of its volume line
CANCEL_BOUND = "TOUCH_BOUND"          # its fill would pass the deep layer's bound
CANCEL_NO_FP = "TOUCH_NO_FP"          # no fresh fundamental of this simulation for the book
CANCEL_SHUT = "TOUCH_SHUT"            # the deep layer does not trade the book


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def client_ids(book_id: int) -> tuple[int, int]:
    """(buy, sell) client ids of this book's touch quote."""
    base = CLIENT_BASE + 10 * int(book_id)
    return base + 1, base + 2


def is_touch_client_id(cid: Any) -> bool:
    try:
        c = int(cid)
    except (TypeError, ValueError):
        return False
    return CLIENT_BASE <= c < CLIENT_BASE + CLIENT_SPAN and c % 10 in (1, 2)


def side_of_client_id(cid: Any) -> str | None:
    if not is_touch_client_id(cid):
        return None
    return SIDE_BUY if int(cid) % 10 == 1 else SIDE_SELL


def toward_side(mid: Any, fundamental: Any) -> str | None:
    """The side that trades toward the fundamental: buy when it is above the mid, sell below; None at the mid."""
    m, f = _finite(mid), _finite(fundamental)
    if m is None or f is None or m <= 0.0 or f <= 0.0 or f == m:
        return None
    return SIDE_BUY if f > m else SIDE_SELL


def edge_bps(side: str, price: Any, fundamental: Any) -> float | None:
    """How far the price sits on the safe side of the fundamental, in bps (a bid below it, an offer above it > 0)."""
    p, f = _finite(price), _finite(fundamental)
    if p is None or f is None or p <= 0.0 or f <= 0.0:
        return None
    return (math.log(f / p) if side == SIDE_BUY else math.log(p / f)) * 1e4


def join_price(side: str, bid: Any, ask: Any, tick: Any, safety_ticks: Any = 1) -> float | None:
    """The touch on ``side`` -- the best bid for a buy, the best ask for a sell -- when the post-only sanitizer keeps it
    there (at least ``safety_ticks`` from the other touch); None otherwise."""
    b, a, t = _finite(bid), _finite(ask), _finite(tick)
    if b is None or a is None or t is None or b <= 0.0 or a <= b or t <= 0.0:
        return None
    cushion = max(1, int(safety_ticks or 1)) * t
    if a - b < cushion - 0.5 * t:
        return None
    return b if side == SIDE_BUY else a


def wanted_price(side: str, *, bid: Any, ask: Any, fundamental: Any, tick: Any, safety_ticks: Any = 1,
                 threshold_bps: float = THRESHOLD_BPS) -> float | None:
    """The price a new quote on ``side`` takes this request, or None (no postable touch, or its edge is short)."""
    px = join_price(side, bid, ask, tick, safety_ticks)
    if px is None:
        return None
    e = edge_bps(side, px, fundamental)
    if e is None or e < float(threshold_bps) - 1e-9:
        return None
    return px


def verdict(side: str, price: Any, *, toward: str | None, bid: Any, ask: Any, fundamental: Any, tick: Any,
            threshold_bps: float = THRESHOLD_BPS, rest_ticks: Any = REST_TICKS) -> str | None:
    """None when a resting quote stays; otherwise the reason it is cancelled (FLIPPED, EDGE, LEFT)."""
    if toward is None or side != toward:
        return CANCEL_FLIPPED
    e = edge_bps(side, price, fundamental)
    if e is None or e < float(threshold_bps) - 1e-9:
        return CANCEL_EDGE
    p, b, a, t = _finite(price), _finite(bid), _finite(ask), _finite(tick)
    if p is None or b is None or a is None or t is None or t <= 0.0:
        return CANCEL_LEFT
    ahead = (b - p) / t if side == SIDE_BUY else (p - a) / t      # ticks the touch is better than the quote
    if ahead > float(rest_ticks) - 0.5:
        return CANCEL_LEFT
    return None


def room(side: str, inventory: Any, clip: Any, bound: Any) -> bool:
    """A fill of ``clip`` on ``side`` keeps the position inside ``bound``; a side that reduces it (v6.4.1's ``adds``
    is False) always has room."""
    inv, c, lim = _finite(inventory) or 0.0, _finite(clip), _finite(bound)
    if c is None or lim is None or c <= 0.0:
        return False
    if not v641_adds(side, inv):
        return True
    return abs(inv) + c <= lim + 1e-9


def placed_sides(instructions: Iterable[Any] | None, book_id: Any) -> set[str]:
    """The sides of ``book_id`` a placement among ``instructions`` (this response so far) already takes: one maker order
    per book side per response (the maker sanitizer's and A1.7.4.3.1's rule), so the quote never takes such a side."""
    out: set[str] = set()
    for ix in instructions or ():
        def get(name, _ix=ix):
            return _ix.get(name) if isinstance(_ix, dict) else getattr(_ix, name, None)
        kind = get("type")
        if str(getattr(kind, "value", kind) or "").upper() not in PLACEMENT_TYPES:
            continue
        book = get("bookId") if get("bookId") is not None else get("book_id")
        try:
            if int(book) != int(book_id):
                continue
        except (TypeError, ValueError):
            continue
        token = order_side_token(get("direction") if get("direction") is not None else get("side"))
        if token:
            out.add(token)
    return out


def live_slots(rows: Iterable[tuple[Any, Any]]) -> dict[str, frozenset]:
    """v6.9's per-side slots of our live orders, with a touch quote holding TOUCH_SLOT instead of its whole side."""
    rows = list(rows)
    plain = [(s, c) for s, c in rows if not is_touch_client_id(c)]
    out = {k: set(v) for k, v in v69_live_slots(plain).items()}
    for _side, cid in rows:
        if is_touch_client_id(cid):
            out.setdefault(side_of_client_id(cid), set()).add(TOUCH_SLOT)
    return {k: frozenset(v) for k, v in out.items()}


def caps_extra(book_count: Any, clip: Any) -> float:
    """The exposure the quotes add to the universe's cap: one quote in flight per book (its fill stays inside the bound)."""
    n = max(0, int(_finite(book_count) or 0))
    c = max(0.0, _finite(clip) or 0.0)
    return float(n) * c
