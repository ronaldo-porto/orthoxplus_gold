# SPDX-License-Identifier: MIT
"""v6.47 C2S2: the class-2 second touch slot -- the live form of the replay engine's c2rq (round5/vm_r5c12.py 7f3b39e4, the
tested arm S4P10P = q_R7C2RQ: c2rq = a class-2 refresh cancel is a REPLACE, the new quote placed in the same state;
c2dfc counters only) through the two client-id ranges of research_v617_touch_quote (slot A 9, slot B 8), in the
engine's c2rq emit order when research_v647_c2s2p = 1 (option P: the replacement placement BEFORE the cancel).  Design:
scratchpad round5/s1r7/c2s2_design.md sec. 1-3 and 5.  The rules only (pure, no agent state); simple_parts/v6_47_part.py
applies them in C2T.

* The replace kinds (research_v647_c2s2): 1 = exactly c2rq's two kinds -- the A1.9 size cancel of a resting REDUCING touch
  quote (``C2S2_SIZE``: remaining + 1e-12 < 0.80 x |net|, A1.9 classify_resting_maker_exit's size clause / the engine's
  a19x) and C2RF's one-state refresh of the ADDING quote (``C2T_REFRESH``); 2 = the size cancel only.  Any other cancel
  of the side (LEFT, the plan's reasons, another layer, RETRY, EXTRA, a cancel of another pass) keeps v6.46's
  cancel-seen gap.
* Dying rows: a touch row C2T cancelled in the PREVIOUS request and still in the account holds its slot, is never kept
  and is not cancelled again; from the second request on it is cancelled again (``C2S2_RETRY``, the side waits: the
  one-slot rule).  More than one live row on a side: the newest (highest order id) is evaluated, the others are cancelled
  (``C2S2_EXTRA``, the side waits).
* The slot of a new quote: a REPLACE side takes a touch slot no live / pending / dying order holds (both held: none, the
  side waits -- ``c2s2_no_free_slot``); a side with nothing cancelled this request places only while every touch slot
  held on it is a dying row's (any other holder: ``slot_in_flight``, v6.46's rule), into the free slot (A first); an
  order of ours that holds the whole side (not a touch / deep id) refuses both.  At most two orders per (book, side)
  ever: the final validation refuses a placement into a held slot (per slot) and a second placement on the side in one
  response, exactly as before.
* The emit order (research_v647_c2s2p): 0 = every doomed row in ONE cancel BEFORE the placements (R7C2S2's form, no
  overlap); 1 = the engine's c2rq order -- cancels of other reasons first (the engine's g1 / g3), the placements (p4),
  then the replaced rows' cancel (g5): the new quote lands at the book's base delay, the old one dies 5-25 ms later.

STRUCTURAL: the engine's rule and emit order, A1.9's own 0.80, v6.9's slots; no constant is sized from a sample."""
from __future__ import annotations

import math
from typing import Any, Iterable

from research_v617_touch_quote import TOUCH_SLOT, TOUCH_SLOT_B, TOUCH_SLOTS, touch_slot
from research_v63_trend_target import SIDE_BUY, SIDE_SELL
from research_v646_rules import CANCEL_C2RF
from research_v69_deep_ladder import WHOLE_SIDE

V647_C2S2_VERSION = "c2s2_v6_47"
EPS = 1e-9                               # the engine's EPS
MIN_QTY_FRACTION = 0.80                  # A1.9 classify_resting_maker_exit's min_qty_fraction (the engine's a19x 0.8)
CANCEL_SIZE = "C2S2_SIZE"                # the A1.9 size clause applied by C2T itself (a replace kind under 1 and 2)
CANCEL_RETRY = "C2S2_RETRY"              # a row C2T cancelled two or more requests ago and still in the account
CANCEL_EXTRA = "C2S2_EXTRA"              # an older second live row on a side
HOLD_TAG = "V647_C2T_OWNS"               # A1.9's REPRICE on a C2T class-2 book set aside (C2T owns every order there)

WHY_WAIT = "wait_cancel_seen"
WHY_IN_FLIGHT = "slot_in_flight"
WHY_NO_FREE = "c2s2_no_free_slot"
WHY_DEFERRED = "c2s2_cancel_deferred"
WHY_REPLACE = "c2s2_replace"
WHY_PLAIN = "c2s2_plain"

__all__ = ["CANCEL_EXTRA", "CANCEL_RETRY", "CANCEL_SIZE", "EPS", "HOLD_TAG", "MIN_QTY_FRACTION", "V647_C2S2_VERSION",
           "WHY_DEFERRED", "WHY_IN_FLIGHT", "WHY_NO_FREE", "WHY_PLAIN", "WHY_REPLACE", "WHY_WAIT", "cancel_plan",
           "dying", "reducing", "replace_kinds", "retry", "size_shortfall", "slot_decision", "split_rows"]


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def replace_kinds(mode: Any) -> frozenset:
    """The cancel kinds that are a REPLACE: 1 -> C2RF's refresh and the size cancel (c2rq's two), 2 -> the size cancel
    only, anything else -> none."""
    try:
        m = int(mode or 0)
    except (TypeError, ValueError):
        m = 0
    if m == 1:
        return frozenset((CANCEL_C2RF, CANCEL_SIZE))
    if m == 2:
        return frozenset((CANCEL_SIZE,))
    return frozenset()


def reducing(side: str, net_base: Any) -> bool:
    """``side`` reduces the TRUE position (a sell while net > EPS, a buy while net < -EPS) -- the engine's a19x side."""
    n = _finite(net_base)
    if n is None:
        return False
    return (side == SIDE_SELL and n > EPS) or (side == SIDE_BUY and n < -EPS)


def size_shortfall(remaining: Any, net_base: Any) -> bool:
    """A1.9's size clause: remaining + 1e-12 < 0.80 x |net| (the engine: ``o.rem + 1e-12 < 0.8 * abs(B.inv)``)."""
    r, n = _finite(remaining), _finite(net_base)
    if r is None or n is None:
        return False
    return r + 1e-12 < MIN_QTY_FRACTION * abs(n)


def dying(sent_tick: Any, now_tick: Any) -> bool:
    """C2T sent this row's cancel in the previous request (it still holds its slot, is not kept, not cancelled again)."""
    try:
        return sent_tick is not None and int(now_tick) - int(sent_tick) == 1
    except (TypeError, ValueError):
        return False


def retry(sent_tick: Any, now_tick: Any) -> bool:
    """C2T sent this row's cancel two or more requests ago and it is still live: cancelled again (C2S2_RETRY)."""
    try:
        return sent_tick is not None and int(now_tick) - int(sent_tick) >= 2
    except (TypeError, ValueError):
        return False


def _side_of_row(row: Any) -> str:
    return SIDE_BUY if int(getattr(row, "side", 1)) == 0 else SIDE_SELL


def split_rows(rows: Iterable[Any], *, sent: dict, book_id: int, now_tick: int, already: Iterable[int] = ()) -> dict:
    """C2T's touch rows of one book this request, split: ``evaluate`` (per side the newest non-dying row, in the rows'
    order), ``extra`` (older non-dying rows: C2S2_EXTRA), ``retry`` (rows whose cancel is two or more requests old:
    C2S2_RETRY), ``dying_slots`` {side: slots of dying rows}, ``already_sides`` (sides of rows a cancel of this response
    already names -- another pass's -- they wait), ``live`` (the order ids seen).  ``sent`` = {(book, order id): tick
    of C2T's cancel}."""
    gone = set(int(x) for x in (already or ()))
    b = int(book_id)
    out = {"evaluate": [], "extra": [], "retry": [], "dying_slots": {SIDE_BUY: set(), SIDE_SELL: set()},
           "already_sides": set(), "live": set()}
    per_side: dict[str, list] = {SIDE_BUY: [], SIDE_SELL: []}
    for row in rows or ():
        oid = int(row.order_id)
        out["live"].add(oid)
        side = _side_of_row(row)
        if oid in gone:
            out["already_sides"].add(side)
            continue
        st = (sent or {}).get((b, oid))
        if dying(st, now_tick):
            out["dying_slots"][side].add(touch_slot(getattr(row, "client_id", None)) or TOUCH_SLOT)
            continue
        if retry(st, now_tick):
            out["retry"].append(row)
            continue
        per_side[side].append(row)
    keep = set()
    for side, rs in per_side.items():
        if not rs:
            continue
        newest = max(rs, key=lambda r: int(r.order_id))
        keep.add(id(newest))
        out["extra"].extend(r for r in rs if r is not newest)
    out["evaluate"] = [r for r in rows or () if id(r) in keep]
    return out


def cancel_plan(doomed: Iterable[tuple[Any, str]], mode: Any, order_p: Any) -> dict:
    """The cancels of one C2T book: ``replace`` = the sides whose every doomed row is a touch row of a replace kind; with
    order_p 1 their rows go ``after`` the placements (the engine's g5) and every other doomed row ``first``; with 0 every
    doomed row goes ``first`` (one cancel before the placements).  Rows are kept in their doomed order."""
    kinds = replace_kinds(mode)
    by_side: dict[str, list] = {}
    for row, why in doomed or ():
        by_side.setdefault(_side_of_row(row), []).append(str(why))
    replace = {s for s, whys in by_side.items() if whys and all(w in kinds for w in whys)}
    first, after = [], []
    for item in doomed or ():
        (after if (int(order_p or 0) == 1 and _side_of_row(item[0]) in replace) else first).append(item)
    return {"replace": replace, "first": first, "after": after, "sides": set(by_side)}


def slot_decision(held: Iterable[Any], dying_slots: Iterable[Any] = (), *, wait: bool, replace: bool,
                  cancel_sent: bool = True) -> tuple[int | None, str]:
    """(the touch slot a new quote on the side takes, why) -- or (None, why) when the side places nothing:
    ``wait`` (a non-replace cancel on the side this request) -> wait_cancel_seen; an order holding the whole side ->
    slot_in_flight; ``replace`` -> its cancel must be sent (else c2s2_cancel_deferred) and a touch slot free of every
    holder (the doomed row's included) -> that slot, else c2s2_no_free_slot; otherwise every held touch slot must be a
    dying row's (else slot_in_flight) and a free one is taken (slot A first), else c2s2_no_free_slot."""
    h = set(held or ())
    if wait:
        return None, WHY_WAIT
    if WHOLE_SIDE in h:
        return None, WHY_IN_FLIGHT
    taken = {s for s in h if s in TOUCH_SLOTS}
    free = [s for s in (TOUCH_SLOT, TOUCH_SLOT_B) if s not in taken]
    if replace:
        if not cancel_sent:
            return None, WHY_DEFERRED
        if not free:
            return None, WHY_NO_FREE
        return free[0], WHY_REPLACE
    if taken - set(dying_slots or ()):
        return None, WHY_IN_FLIGHT
    if not free:
        return None, WHY_NO_FREE
    return free[0], WHY_PLAIN
