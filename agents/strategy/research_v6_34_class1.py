# SPDX-License-Identifier: MIT
"""v6.34: the class-1 entry -- research_v634_class1_entry.

Why (10-17 JST; memory score-drop-064-class-weights-1017, scratchpad k98):

* Validator 0.6.4 (live ~00:30 JST 10-17) pays each asset class its own share of BOTH pool halves at the class weights
  0.75 / 0.25 (was 0.95 / 0.05).  Our agents trade no class-1 book (v6.23 S2 holds every book of another class), so
  every UID's share fell ~21 % at the switch.  The markout quality q is per UID over ALL books
  (debeta.markout_quality on the whole sums), so class-1 fills whose realized / captured is above the UID's class-0
  q also lift the class-0 credit.
* Replay k98 (engine k98/vm703.py, the C3f arm pre-registered 02:05 JST): one join-only touch quote per class-1 book in
  the class's own units, on the side the fundamental moved toward (S9), held 20 sim-s on the adding side after a miner
  taker's print, paced per book from its own entry, S14 on its own class-1 estimate, D4 on: joint 0.6.4 pay +27.9 /
  +33.5 / +2.5 / +6.2 % (cells cA / oA / f115 / A115, d40) against class 0 alone, positive at d30 / d50 and with both D4
  draws tested; the class-1 making credit itself is 0 on the two thin tapes (strict Q1 fails there).  Each component
  adds pay on its own (ablations: join +16-20 %, the miner hold +11-44 %, fresh state fp vs page fp x4-6).

The rule (one switch, ``research_v634_class1_entry``), on the books of class 1 of the validator's class map -- the
state configuration's ``classes`` list, one class per background in document order (taos 0.6.4 config.py
``_classes_from_backgrounds``; reward.class_map: book -> background index), class 1 = its second entry.  No class map,
fewer than two classes, an unreadable one or a class without its minimum / grids: no book is class 1 and the switch
does nothing (the state row says why).  On a class-1 book (``side_plan``, the replay's c2 order):

1. Units: the class's own minimum order, price grid and volume grid; every size is v6.32's class-0 touch clip and touch
   bound scaled by min1 / min0 (2.5364 / 0.25 on mainnet: 2.0 -> 20.2912 base, 48 -> 486.99 base).  No deep layer, no
   lean, no v6.23 S1, no fundamental-level edge hurdle.
2. Each side takes one touch quote at the touch's own price (JOIN, never improve -- the leaders' rule L1, k98 TASK A)
   whenever the book is not crossed, a 1-tick spread included (coordinator 03:35 JST: the replay joins at 1 tick and a
   third of the live class-1 book-states have a 1-tick spread; post-only is the protection -- the venue refuses a
   crossing order -- and the refusals are counted in V6_34_STATE), sized at the clip when it adds and
   max(clip, |inventory|) when it reduces; a side is not quoted while it adds at or past the bound, adds while the book
   is paced, is the side S9 holds (the fundamental's move over the look-back is against it -- with no move both sides
   quote), adds while the class-1 fee gate is shut, the book's D4 draw is 0 for this UID, or (S17-R) it adds within
   HOLD_NS of a miner taker's print on that side.  A resting quote is cancelled when
   its side is no longer quoted, when the touch moved REST_TICKS past it (v6.17's rest band) and, paced, when it is
   larger than the unwind size (re-placed at that size); a cancelled side is re-placed once the cancel is seen.
3. Pacing (the Q5 fix, k98 'B'): line(t) = min(T, u0 + cap x (t - t0 + PACE_WINDOW) / ASSESSMENT), T = cap - cap x
   MAKING_WINDOW / ASSESSMENT, (t0, u0) the book's first class-1 state and rolling volume; above the line the adding
   side is withheld and the reducing quote is min(size, |inventory|) -- never across flat, none below the class
   minimum.  At the cap (rolling volume >= cap) the book holds no quote.
4. S14 per class: the v6.32 realized tracker over CLASS-1 fills only (class 0 keeps its own pooled estimate).

STRUCTURAL: the validator's class map, class grids and minimum, cap, sampling interval, assessment period, making window
and making horizon; the subnet's UID slots (a taker id 0 .. 255 is a miner, the background is negative); v6.32's class-0
clip and bound in the class's own minimum.  OBSERVED (k98 TASK A, both samples): the leaders join and never improve --
the rule itself has no threshold.  No constant here is sized from an observed metric.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from research_v633_deep_layer import SIDE_BUY, SIDE_SELL
from research_v641_pace import ASSESSMENT_NS, PACE_WINDOW_NS, adds as _v641_adds
from research_v6_32_fee_making import HORIZON_NS, WINDOW_NS
from research_v617_touch_quote import (CLIENT_BASE as TOUCH_CLIENT_BASE, CLIENT_SPAN as TOUCH_CLIENT_SPAN,
                                       REST_TICKS)

V6_34_CLASS1_ENTRY_VERSION = "class1_entry_v6_34"
CLASS1 = 1                       # the validator's class id this switch trades: the layout's second background
MINER_UID_SLOTS = 256            # the subnet's UID slots: a taker id 0 .. 255 is a miner (the background's are < 0)
HOLD_NS = HORIZON_NS             # S17-R: the validator's making horizon (debeta making_horizon_s 20.0)
MAKING_WINDOW_NS = WINDOW_NS     # the validator's making window, 10,800 sim-s (v6.22 B1: one window of room kept)

STATUS_OK = "ok"                 # class 1 read: its books and units
STATUS_SINGLE = "single"         # no class map: a single market or a validator before 0.6.3
STATUS_NO_CLASS1 = "no_class1"   # a class map with no second class
STATUS_UNREADABLE = "unreadable"  # a class map that cannot be read
STATUS_NO_UNITS = "no_units"     # class 1 without its minimum order or grids (or class 0 without its minimum)

WHY_CAPPED = "C1_CAPPED"          # the book's rolling volume is at the cap: the validator drops every placement
WHY_BOUND = "C1_BOUND"            # the side adds at or past the bound
WHY_PACED = "C1_PACED"            # the side adds while the book runs above its line
WHY_FP_MOVE = "C1_FP_MOVE"        # S9: the fundamental's move over the look-back is against the side
WHY_FEE_GATE = "C1_FEE_GATE"      # S14 (class 1): the side adds while the class-1 estimate does not cover W x the fee
WHY_TOUCH_IDENTITY = "C1_TOUCH_IDENTITY"   # D4: this UID keeps no touch quote on the book
WHY_PACED_FLAT = "C1_PACED_FLAT"  # paced: the unwind size is below the class minimum
WHY_MINER_HOLD = "C1_MINER_HOLD"  # S17-R: the side adds within HOLD_NS of a miner taker's print on it
CANCEL_LEFT = "C1_LEFT"           # the touch moved REST_TICKS or more past the resting quote
CANCEL_RESIZE = "C1_RESIZE"       # paced: the resting quote is larger than the unwind size (re-placed at it)
CANCEL_OTHER = "C1_OTHER_ORDER"   # an order of a class-0 layer on a class-1 book
WHYS = (WHY_CAPPED, WHY_BOUND, WHY_PACED, WHY_FP_MOVE, WHY_FEE_GATE, WHY_TOUCH_IDENTITY, WHY_PACED_FLAT, WHY_MINER_HOLD)
CANCELS = WHYS + (CANCEL_LEFT, CANCEL_RESIZE, CANCEL_OTHER)

_EPS = 1e-9


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def _whole(value: Any) -> int | None:
    v = _finite(value)
    if v is None or v != math.floor(v):
        return None
    return int(v)


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


# ---- the class map ---------------------------------------------------------------------------------------------------

def class1_layout(config: Any) -> dict[str, Any]:
    """The class-1 books and units of a state's configuration: {status, books, classes, min0, min1, scale,
    price_decimals, volume_decimals, tick}.  ``books`` is empty unless status is STATUS_OK.  min0 is class 0's own
    minimum order (its configuration's; the top-level one when it carries none -- the top level is the first class's),
    min1 class 1's; the grids are class 1's own (the class entry's, else its configuration's); scale = min1 / min0."""
    out: dict[str, Any] = {"status": STATUS_SINGLE, "books": frozenset(), "classes": 0, "min0": None, "min1": None,
                           "scale": None, "price_decimals": None, "volume_decimals": None, "tick": None}
    classes = _get(config, "classes")
    if not classes:
        return out
    try:
        if isinstance(classes, (str, bytes, dict)):
            raise TypeError("classes is not a list")
        classes = list(classes)
        out["classes"] = len(classes)
        if len(classes) <= CLASS1:
            out["status"] = STATUS_NO_CLASS1
            return out
        c0, c1 = classes[0], classes[CLASS1]
        books = _get(c1, "books")
        if books is None or isinstance(books, (str, bytes, dict)):
            raise TypeError("class 1 without books")
        ids = frozenset(int(b) for b in books)
        books0 = _get(c0, "books")
        if books0 is not None and not isinstance(books0, (str, bytes, dict)) and ids & {int(b) for b in books0}:
            raise ValueError("class 0 and class 1 share a book")
        cfg0, cfg1 = _get(c0, "config"), _get(c1, "config")
        min0 = _finite(_get(cfg0, "min_order_size"))
        if min0 is None:
            min0 = _finite(_get(config, "min_order_size"))
        min1 = _finite(_get(cfg1, "min_order_size"))
        pd1 = _whole(_get(c1, "priceDecimals"))
        if pd1 is None:
            pd1 = _whole(_get(cfg1, "priceDecimals"))
        vd1 = _whole(_get(c1, "volumeDecimals"))
        if vd1 is None:
            vd1 = _whole(_get(cfg1, "volumeDecimals"))
    except Exception:
        out["status"] = STATUS_UNREADABLE
        return out
    out.update(min0=min0, min1=min1, price_decimals=pd1, volume_decimals=vd1)
    if (not ids or min0 is None or min1 is None or min0 <= 0.0 or min1 <= 0.0 or pd1 is None or vd1 is None
            or pd1 < 0 or vd1 < 0):
        out["status"] = STATUS_NO_UNITS
        return out
    out.update(status=STATUS_OK, books=ids, scale=min1 / min0, tick=10.0 ** (-pd1))
    return out


def scaled(value0: Any, scale: Any) -> float | None:
    """A class-0 size in the class-1 minimum's units (value0 x min1 / min0); None when either cannot be read."""
    v, s = _finite(value0), _finite(scale)
    if v is None or s is None or s <= 0.0:
        return None
    return v * s


def join(side: str, bid: Any, ask: Any, tick: Any) -> float | None:
    """The touch's own price on ``side`` -- the best bid for a buy, the best ask for a sell -- whenever the book is not
    crossed (bid < ask, a 1-tick spread included) on a positive grid; None otherwise.  No safety cushion: a post-only
    order that would cross is refused by the venue (the replay's post-only check after its delay)."""
    b, a, t = _finite(bid), _finite(ask), _finite(tick)
    if b is None or a is None or t is None or b <= 0.0 or t <= 0.0 or a - b < 0.5 * t:
        return None
    return b if side == SIDE_BUY else a if side == SIDE_SELL else None


def refusal(event: Any, books: Any) -> str | None:
    """A placement refusal of a class-1 entry quote among this state's notices: None, or "post_only" (the venue's
    post-only contract reject, CONTRACT_VIOLATION) / the refusal's own message (upper case, at most 40 characters) for
    a failed limit placement (ERDPOL) of a touch-slot client id on one of ``books``.  Never raises."""
    try:
        get = event.get if isinstance(event, dict) else (lambda k, _e=event: getattr(_e, k, None))
        kind = get("type") if not isinstance(event, dict) else (event.get("type") or event.get("y"))
        if str(kind or "").upper() not in ("ERDPOL", "ERROR_RESPONSE_DISTRIBUTED_PLACE_ORDER_LIMIT"):
            return None
        book = get("bookId") if not isinstance(event, dict) else event.get("bookId", event.get("b"))
        cid = get("clientOrderId") if not isinstance(event, dict) else event.get("clientOrderId", event.get("c"))
        if book is None or cid is None or int(book) not in books:
            return None
        c = int(cid)
        if not (TOUCH_CLIENT_BASE <= c < TOUCH_CLIENT_BASE + TOUCH_CLIENT_SPAN and c % 10 in (1, 2)):
            return None
        msg = str((get("message") if not isinstance(event, dict) else event.get("message", event.get("m"))) or "")
        msg = msg.strip().upper()
        return "post_only" if "CONTRACT_VIOLATION" in msg else (msg[:40] or "unknown")
    except Exception:
        return None


def lot(quantity: Any, volume_decimals: Any) -> float:
    """A size on the class's volume grid, floored (an unwind never crosses flat); 0.0 when it cannot be read."""
    q, d = _finite(quantity), _whole(volume_decimals)
    if q is None or q <= 0.0 or d is None or d < 0:
        return 0.0
    f = 10.0 ** d
    return math.floor(q * f + 1e-6) / f


# ---- pacing (the Q5 fix) ---------------------------------------------------------------------------------------------

def pace_line(cap: Any, u0: Any, t0_ns: Any, now_ns: Any) -> float | None:
    """The book's line at ``now_ns``: min(T, u0 + cap x (now - t0 + PACE_WINDOW) / ASSESSMENT), T = cap - cap x
    MAKING_WINDOW / ASSESSMENT -- the validator's sustainable rate from the book's own first class-1 state (t0, u0 its
    rolling volume then) plus one 600-s sampling interval, one making window of room kept (v6.22 B1).  None when an
    input cannot be read or the cap is not positive."""
    c, u, t0, now = _finite(cap), _finite(u0), _finite(t0_ns), _finite(now_ns)
    if c is None or c <= 0.0 or u is None or t0 is None or now is None:
        return None
    top = c - c * float(MAKING_WINDOW_NS) / float(ASSESSMENT_NS)
    return min(top, u + c * (now - t0 + float(PACE_WINDOW_NS)) / float(ASSESSMENT_NS))


def paced(used: Any, line: Any) -> bool:
    """Above the line: the rolling volume exceeds it (no line: never paced)."""
    u, ln = _finite(used), _finite(line)
    return u is not None and ln is not None and u > ln + _EPS


# ---- S17-R: the miner-taker hold -------------------------------------------------------------------------------------

def miner_hit(event: Any, own_uid: Any) -> str | None:
    """The side of the book a trade print hit when its TAKER is a miner -- 0 <= Ta < MINER_UID_SLOTS, not this agent and
    not a self-trade (Ta != Ma): the sell side for a buy-initiated print (s == 0 takes the asks), the buy side for
    s == 1; else None (a background taker, this agent, a self-trade or an unreadable print)."""
    try:
        me = None if own_uid is None or isinstance(own_uid, bool) else int(own_uid)
    except (TypeError, ValueError, OverflowError):
        me = None
    try:
        e = event if isinstance(event, dict) else {k: getattr(event, k, None) for k in ("y", "s", "Ta", "Ma")}
        if e.get("y") not in (None, "t"):
            return None
        ta, ma, s = e.get("Ta"), e.get("Ma"), e.get("s")
        if isinstance(ta, bool) or ta is None or isinstance(s, bool) or s not in (0, 1):
            return None
        ta = int(ta)
        if ta < 0 or ta >= MINER_UID_SLOTS:
            return None
        if me is not None and ta == me:
            return None
        if ma is not None and not isinstance(ma, bool) and ta == int(ma):
            return None
        return SIDE_SELL if s == 0 else SIDE_BUY
    except Exception:
        return None


class MinerHolds:
    """S17-R's clock per (book, side): until when a miner taker's last print on it holds the side's ADDING quote (its
    print time + HOLD_NS; the state's time for a print without its own).  A state clock that ran back (a new run) clears
    every hold, and a hold whose print lies after the clock's now is never read.  Bounded: one entry per book side."""

    def __init__(self) -> None:
        self.until: dict[tuple[int, str], int] = {}
        self.last_ts: int | None = None
        self.counts = {"miner_prints": 0, "malformed": 0, "resets": 0}

    def observe(self, book_id: Any, prints: Iterable[Any], own_uid: Any, ts: Any = None) -> int:
        """One book's trade prints of this state; returns the miner-taker prints among them."""
        now = _whole(ts)
        if now is not None:
            if self.last_ts is not None and now < self.last_ts:
                self.until.clear()                       # the clock ran back: a new run, no hold carries over
                self.counts["resets"] += 1
            self.last_ts = now
        try:
            b = int(book_id)
        except (TypeError, ValueError, OverflowError):
            self.counts["malformed"] += 1
            return 0
        n = 0
        for e in prints or ():
            side = miner_hit(e, own_uid)
            if side is None:
                continue
            t = _whole(_get(e, "t"))
            if t is None or t <= 0:
                t = _whole(ts)
            if t is None:
                self.counts["malformed"] += 1
                continue
            until = int(t) + int(HOLD_NS)
            if until > self.until.get((b, side), -1):
                self.until[(b, side)] = until
            n += 1
        self.counts["miner_prints"] += n
        return n

    def held(self, book_id: Any, side: str, now_ns: Any) -> bool:
        """Is the side held at ``now_ns``: its last miner print at or before now, now before print + HOLD_NS."""
        try:
            now = _finite(now_ns)
            until = self.until.get((int(book_id), side))
            return now is not None and until is not None and until - int(HOLD_NS) <= now < until
        except (TypeError, ValueError, OverflowError):
            return False

    def snapshot(self, now_ns: Any = None) -> dict[str, Any]:
        now = _finite(now_ns)
        active = sum(1 for u in self.until.values() if now is not None and u - int(HOLD_NS) <= now < u)
        return {"sides_tracked": len(self.until), "sides_held": active, **self.counts}


# ---- the decisions ---------------------------------------------------------------------------------------------------

def side_plan(side: str, *, inventory: Any, clip: Any, bound: Any, min_size: Any, is_paced: bool, s9_held: Any,
              fee_gated: bool, d4_off: bool, miner_held: bool) -> tuple[float, str | None]:
    """(size, None) when the side takes a quote of that size this request, else (0.0, why) -- the replay's c2 order:
    the bound (adding at or past it), pacing (adding), S9 (the held side, adding or reducing), the class-1 fee gate
    (adding), D4 (both sides), the paced unwind below the minimum, S17-R (adding).  An adding quote is the clip; a
    reducing one max(clip, |inventory|), paced min(that, |inventory|).  ``adds``: v6.4.1's (a flat book adds on both
    sides)."""
    inv = _finite(inventory)
    inv = 0.0 if inv is None else inv
    c, lim, m = _finite(clip), _finite(bound), _finite(min_size)
    if c is None or c <= 0.0 or lim is None or m is None:
        return 0.0, WHY_BOUND
    add = bool(_v641_adds(side, inv))
    if add and abs(inv) >= lim - _EPS:
        return 0.0, WHY_BOUND
    if is_paced and add:
        return 0.0, WHY_PACED
    if s9_held is not None and side == s9_held:
        return 0.0, WHY_FP_MOVE
    if fee_gated and add:
        return 0.0, WHY_FEE_GATE
    if d4_off:
        return 0.0, WHY_TOUCH_IDENTITY
    size = c if add else max(c, abs(inv))
    if is_paced and not add:
        size = min(size, abs(inv))
        if size < m - _EPS:
            return 0.0, WHY_PACED_FLAT
    if miner_held and add:
        return 0.0, WHY_MINER_HOLD
    return float(size), None


def resting_verdict(side: str, price: Any, remaining: Any, want: Any, why_unwanted: Any, *, is_paced: bool, bid: Any,
                    ask: Any, tick: Any, rest_ticks: Any = REST_TICKS) -> str | None:
    """None when a resting class-1 quote stays; else why it is cancelled: its side takes no quote (the side's reason),
    paced and larger than the unwind size (CANCEL_RESIZE), or the touch moved ``rest_ticks`` past it (CANCEL_LEFT)."""
    w = _finite(want)
    if w is None or w <= _EPS:
        return str(why_unwanted) if why_unwanted else WHY_BOUND
    r = _finite(remaining)
    if is_paced and r is not None and r > w + _EPS:
        return CANCEL_RESIZE
    p, b, a, t = _finite(price), _finite(bid), _finite(ask), _finite(tick)
    if p is None or b is None or a is None or t is None or t <= 0.0:
        return CANCEL_LEFT
    ahead = (b - p) / t if side == SIDE_BUY else (p - a) / t      # ticks the touch is better than the quote
    if ahead > float(rest_ticks) - 0.5:
        return CANCEL_LEFT
    return None


def p90(values: Iterable[Any]) -> float | None:
    """The 90th percentile (nearest rank) of the readable values, or None."""
    xs = sorted(v for v in (_finite(x) for x in values or ()) if v is not None)
    if not xs:
        return None
    return xs[min(len(xs) - 1, max(0, int(math.ceil(0.9 * len(xs))) - 1))]


__all__ = ["CANCELS", "CANCEL_LEFT", "CANCEL_OTHER", "CANCEL_RESIZE", "CLASS1", "HOLD_NS", "MAKING_WINDOW_NS",
           "MINER_UID_SLOTS", "MinerHolds", "STATUS_NO_CLASS1", "STATUS_NO_UNITS", "STATUS_OK", "STATUS_SINGLE",
           "STATUS_UNREADABLE", "V6_34_CLASS1_ENTRY_VERSION", "WHYS", "WHY_BOUND", "WHY_CAPPED", "WHY_FEE_GATE",
           "WHY_FP_MOVE", "WHY_MINER_HOLD", "WHY_PACED", "WHY_PACED_FLAT", "WHY_TOUCH_IDENTITY", "class1_layout",
           "join", "lot", "miner_hit", "p90", "pace_line", "paced", "refusal", "resting_verdict", "scaled", "side_plan"]
