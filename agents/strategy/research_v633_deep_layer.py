# SPDX-License-Identifier: MIT
"""v6.3.3: a gated deep layer -- both sides of a book rest deep in the book where the book's own sweeps reach, while
that book's paper record says those fills pay.

Why (mainnet, sim 20260924_1653, measured 09-26 on UID 94's recorder; scratchpad v63/r631, r633-r635):

* The clean-window skill leaders are makers that fill THROUGH the touch: 74-88% of the fills of uids 71/228/243/180/121
  land beyond the previous state's best price, a median 11-50 ticks from the mid, and the price comes back (markout
  +8..+68 ticks after 10 states, +10..+58 after 300).  Touch makers (UID 94 among them) fill at the touch for +1.3..+1.9.
* Sweeps are the book's own flow: ~300 per book-hour reach >= 5 ticks past the touch (depth p25/p50/p75/p90 8/17/39/74
  ticks, size p50 49 base); about half are the simulator's background takers, the rest diverse miners.
* Replayed with the validator's arithmetic on this simulation (orders at a quantile of each book's own sweep depths,
  filled only when a print goes through them): p25 alpha -3,155, p50 -1,097, p75 +1,923, p90 +3,071 over the last 3-h
  window.  On the previous (trending) simulation every depth loses (p90 -20,750): sweeps there do not come back.

The rules (one switch, ``research_v633_deep_layer``):

* Depth.  Per book, the SWEEP_QUANTILE of the sweep depths seen on that book (ticks a taker's prints in one state
  reached past the previous touch), once SWEEP_MIN have been seen.  OBSERVED: p90 beat p75/p50/p25 here; the gate below,
  not the quantile, is what protects another regime.
* Paper record.  Per book, paper orders at that depth on both sides, filled only by a print through them, one clip,
  inventory within DEEP_MAX_CLIPS; scored on the validator's alpha arithmetic (sum inv x dp over prints, the fill marked
  to the print that went through it, minus mean inventory x drift) over the validator's window (600-s keys, pruned at
  now - lookback every 60 s).  It runs whether or not the layer trades.
* Gate.  The board is open while the mean paper alpha of the books with a paper fill is positive; a book is open when
  the board is, its depth is known, its own paper alpha is not under -half the skill floor, and its inventory is within
  DEEP_MAX_CLIPS.  A clock that goes back more than an hour starts every record over.
* Deep-only.  An open book's two sides are the deep layer's (one order per side, as the A1.7.4.3 contract requires):
  its touch orders are cancelled, and a filled position unwinds when a sweep reaches the other side.  A shut book runs
  v6.3.2 unchanged.  Replayed on this simulation: making 1,951 (v6.3.2: 339), alpha +2,025, skill +2.36 on the clean
  floor and +2.98 on the validator's published 30; on the previous one the board stays shut 97% of the time.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any, Iterable

V633_DEEP_LAYER_VERSION = "deep_layer_v6_3_3"

SWEEP_QUANTILE = 0.9
SWEEP_MIN = 20
SWEEP_KEEP = 400
DEEP_CLIPS = 1.0
DEEP_MAX_CLIPS = 2.0
REPRICE_LOW = 0.5
REPRICE_HIGH = 1.5
OWN_FLOOR_FRACTION = 0.5
SAMPLE_NS = 600_000_000_000            # scoring.activity.trade_volume_sampling_interval
LOOKBACK_NS = 10_800_000_000_000       # scoring.kappa.lookback
PRUNE_EVERY_NS = 60_000_000_000
REBASE_MIN_JUMP_NS = 3_600_000_000_000
CLIENT_BASE = 40000                    # 40001..41272: v6.3's role_of owns 60000-69999, entries 70000+, exits 80000+
SIDE_BUY = "buy"
SIDE_SELL = "sell"
CANCEL_REPRICE = "DEEP_REPRICE"
CANCEL_NO_ROOM = "DEEP_NO_ROOM"
CANCEL_SHUT = "DEEP_SHUT"
CANCEL_OWNS_BOOK = "DEEP_OWNS_BOOK"


def client_ids(book_id: int) -> tuple[int, int]:
    b = int(book_id)
    return CLIENT_BASE + 10 * b + 1, CLIENT_BASE + 10 * b + 2


def own_client_ids(book_id: int) -> set[int]:
    return set(client_ids(book_id))


def is_deep_client_id(cid: Any) -> bool:
    try:
        c = int(cid)
    except (TypeError, ValueError):
        return False
    return CLIENT_BASE <= c < CLIENT_BASE + 2000 and c % 10 in (1, 2)


def sampled_key(ts: int, sample_ns: int = SAMPLE_NS) -> int:
    s = max(1, int(sample_ns))
    return (int(ts) // s) * s


def trade_of(event: Any) -> dict[str, Any] | None:
    """A state trade event (pydantic or dict) as {p, q, s, Ma, Ta}, or None."""
    if isinstance(event, dict):
        if event.get("y", "t") != "t":
            return None
        p, q, s, ma, ta = event.get("p"), event.get("q"), event.get("s"), event.get("Ma"), event.get("Ta")
    else:
        if getattr(event, "y", "t") != "t":
            return None
        p, q, s = getattr(event, "p", None), getattr(event, "q", None), getattr(event, "s", None)
        ma, ta = getattr(event, "Ma", None), getattr(event, "Ta", None)
    try:
        pf, qf, si = float(p), float(q), int(s)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(pf) and math.isfinite(qf)) or qf <= 0.0:
        return None
    return {"p": pf, "q": qf, "s": si, "Ma": -1 if ma is None else int(ma), "Ta": -1 if ta is None else int(ta)}


def sweep_depths(trades: Iterable[dict], prev_bid: float, prev_ask: float, tick: float) -> list[float]:
    """Per taker and side in one state's prints: how many ticks past the previous touch the prints reached (>= 1)."""
    t_ = float(tick)
    if not (t_ > 0.0) or prev_bid is None or prev_ask is None:
        return []
    far: dict[tuple[int, int], float] = {}
    for t in trades:
        d = ((float(prev_bid) - t["p"]) if t["s"] == 1 else (t["p"] - float(prev_ask))) / t_
        key = (t["Ta"], t["s"])
        if d > far.get(key, 0.0):
            far[key] = d
    return [d for d in far.values() if d >= 1.0]


def quantile(values: Iterable[float], q: float) -> float | None:
    v = sorted(float(x) for x in values)
    if not v:
        return None
    return v[min(len(v) - 1, int(float(q) * len(v)))]


def deep_price(mid: float, depth: float, side: str, *, bid: float, ask: float, tick: float, decimals: int) -> float:
    """depth ticks from the mid, and never inside the touch (strictly behind the best price on its side)."""
    if side == SIDE_BUY:
        return round(min(float(mid) - float(depth) * float(tick), float(bid) - float(tick)), int(decimals))
    return round(max(float(mid) + float(depth) * float(tick), float(ask) + float(tick)), int(decimals))


def needs_reprice(price: float, mid: float, depth: float, tick: float) -> bool:
    d = abs(float(price) - float(mid)) / float(tick)
    return d < REPRICE_LOW * float(depth) or d > REPRICE_HIGH * float(depth)


class PaperDeep:
    """One book's paper deep orders and their windowed alpha, bucketed like the validator."""

    __slots__ = ("bid", "ask", "inv", "p_last", "buckets", "fills")

    def __init__(self) -> None:
        self.bid: float | None = None
        self.ask: float | None = None
        self.inv = 0.0
        self.p_last: float | None = None
        self.buckets: dict[int, list[float]] = {}     # key -> [sum inv*dp (+ fill marks), sum inv, prints, sum dp]
        self.fills = 0

    def on_print(self, key: int, p: float, s: int, clip: float, limit: float) -> None:
        b = self.buckets.get(key)
        if b is None:
            b = [0.0, 0.0, 0.0, 0.0]
            self.buckets[key] = b
        if self.p_last is not None:
            dp = p - self.p_last
            b[0] += self.inv * dp
            b[3] += dp
        self.p_last = p
        if self.bid is not None and s == 1 and p < self.bid - 1e-9 and self.inv < limit - 1e-9:
            self.inv += clip
            b[0] += clip * (p - self.bid)
            self.bid = None
            self.fills += 1
        elif self.ask is not None and s == 0 and p > self.ask + 1e-9 and self.inv > -limit + 1e-9:
            self.inv -= clip
            b[0] += clip * (self.ask - p)
            self.ask = None
            self.fills += 1
        b[1] += self.inv
        b[2] += 1.0

    def place(self, mid: float, depth: float, *, bid: float, ask: float, tick: float, decimals: int) -> None:
        if self.bid is None or needs_reprice(self.bid, mid, depth, tick):
            self.bid = deep_price(mid, depth, SIDE_BUY, bid=bid, ask=ask, tick=tick, decimals=decimals)
        if self.ask is None or needs_reprice(self.ask, mid, depth, tick):
            self.ask = deep_price(mid, depth, SIDE_SELL, bid=bid, ask=ask, tick=tick, decimals=decimals)

    def prune(self, now: int, lookback_ns: int = LOOKBACK_NS) -> None:
        threshold = int(now) - int(lookback_ns)
        for k in [k for k in self.buckets if k < threshold]:
            del self.buckets[k]

    def alpha(self) -> float:
        m = s_ = n = d = 0.0
        for b in self.buckets.values():
            m += b[0]; s_ += b[1]; n += b[2]; d += b[3]
        return 0.0 if n <= 0 else m - (s_ / n) * d


class DeepBook:
    __slots__ = ("sweeps", "paper", "prev_touch")

    def __init__(self) -> None:
        self.sweeps: deque = deque(maxlen=SWEEP_KEEP)
        self.paper = PaperDeep()
        self.prev_touch: tuple[float, float] | None = None


class DeepLayer:
    """Every book's sweep depths, paper deep record and gate."""

    def __init__(self, *, lookback_ns: int = LOOKBACK_NS, sample_ns: int = SAMPLE_NS,
                 prune_every_ns: int = PRUNE_EVERY_NS, clip: float = DEEP_CLIPS):
        self.lookback_ns = int(lookback_ns)
        self.sample_ns = int(sample_ns)
        self.prune_every_ns = int(prune_every_ns)
        self.clip = float(clip)
        self.rebases = 0
        self.reset()

    def reset(self) -> None:
        self.books: dict[int, DeepBook] = {}
        self.board_open = False
        self.board_alpha = 0.0
        self.board_opens = 0
        self.last_prune_ts: int | None = None

    def maybe_prune(self, now: int) -> None:
        now = int(now)
        if self.last_prune_ts is not None and now < self.last_prune_ts - REBASE_MIN_JUMP_NS:
            rebases = self.rebases
            self.reset()
            self.rebases = rebases + 1
        if self.last_prune_ts is not None and now - self.last_prune_ts < self.prune_every_ns:
            return
        for db in self.books.values():
            db.paper.prune(now, self.lookback_ns)
        self.last_prune_ts = now

    def update_board(self) -> bool:
        """Once per request, before the books: the mean paper alpha of the books with a paper fill, open above 0."""
        al = [db.paper.alpha() for db in self.books.values() if db.paper.fills > 0]
        self.board_alpha = (sum(al) / len(al)) if al else 0.0
        was = self.board_open
        self.board_open = bool(al) and self.board_alpha > 0.0
        if self.board_open and not was:
            self.board_opens += 1
        return self.board_open

    def depth(self, book_id: int) -> float | None:
        db = self.books.get(int(book_id))
        if db is None or len(db.sweeps) < SWEEP_MIN:
            return None
        return quantile(db.sweeps, SWEEP_QUANTILE)

    def observe(self, book_id: int, ts: int, trades: list, *, bid: float, ask: float, tick: float,
                decimals: int) -> float | None:
        """One state for one book: sweeps against the previous touch, prints into the paper record, then the paper
        orders re-placed for the next state at this state's depth.  Returns the depth (None until SWEEP_MIN)."""
        b = int(book_id)
        db = self.books.get(b)
        if db is None:
            db = DeepBook()
            self.books[b] = db
        key = sampled_key(ts, self.sample_ns)
        if db.prev_touch is not None:
            db.sweeps.extend(sweep_depths(trades, db.prev_touch[0], db.prev_touch[1], tick))
        limit = DEEP_MAX_CLIPS * self.clip
        for t in trades:
            db.paper.on_print(key, t["p"], t["s"], self.clip, limit)
        db.prev_touch = (float(bid), float(ask))
        d = self.depth(b)
        if d is not None and bid < ask:
            db.paper.place(0.5 * (bid + ask), d, bid=bid, ask=ask, tick=tick, decimals=decimals)
        return d

    def book_open(self, book_id: int, floor: float, inventory: float, *, inventory_bound: bool = True) -> bool:
        """v6.4 S2 passes inventory_bound=False: the layer keeps a book over the limit, on its reducing side (room)."""
        db = self.books.get(int(book_id))
        if not self.board_open or db is None or self.depth(book_id) is None:
            return False
        if inventory_bound and abs(float(inventory)) > DEEP_MAX_CLIPS * self.clip + 1e-9:
            return False
        return db.paper.alpha() >= -OWN_FLOOR_FRACTION * float(floor)

    def room(self, side: str, inventory: float) -> bool:
        lim = DEEP_MAX_CLIPS * self.clip
        return float(inventory) < lim - 1e-9 if side == SIDE_BUY else float(inventory) > -lim + 1e-9

    def snapshot(self) -> dict[str, Any]:
        al = [db.paper.alpha() for db in self.books.values()]
        depths = [d for d in (self.depth(b) for b in self.books) if d is not None]
        return {
            "version": V633_DEEP_LAYER_VERSION, "books": len(self.books), "board_open": int(self.board_open),
            "board_alpha": round(self.board_alpha, 3), "board_opens": self.board_opens, "rebases": self.rebases,
            "books_with_depth": len(depths), "depth_p50": (sorted(depths)[len(depths) // 2] if depths else None),
            "paper_alpha_sum": round(sum(al), 3), "paper_fills": sum(db.paper.fills for db in self.books.values()),
        }
