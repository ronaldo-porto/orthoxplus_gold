# SPDX-License-Identifier: MIT
"""v6.6.1: the deep bound scaled by the book's own volatility -- a book moving faster than the median book holds less.

Why (mainnet, sim 20260924_1653, validator gauges 20:19 JST 09-28):

* Two price spikes decided both miners' skill legs.  Book 23 ran 263 -> 317 -> 262 around 11:00 JST and book 2 jumped
  288 -> 316 at 18:18 JST before walking back.  In both the spread blew out, the deep and vacuum orders re-centred on
  the spiked mid, and the reverting flow (one background seller, agent -20, four times in 100 s on book 2) filled the
  adding side until the book held its whole bound: UID 104 book 23 -299, book 2 -76 (-152 in the spike alone); UID 94
  book 23 -72, book 2 -20.  Rebuilt print by print, these match the gauges to 0.2.  Book 23 alone takes UID 94's
  trading from 0.915 to 0.776 and UID 104's from 0.827 to 0.644 -- one book over the floor sinks the kappa.
* v6.6's add spacing cannot hold a spike: its unit is one deep depth (tens of ticks) against a move of thousands.
* Replay with the validator's arithmetic on three windows through 20:31 JST (the second and third contain both
  spikes), v6.6 as live vs the same with this bound, trading projected on the 20:19 field:
  UID 94 (1 base) mean 0.619 -> 0.845, worst book -189 -> -47; UID 104 (2 base) 0.601 -> 0.757, and at 1 base 0.797.
  Making moves -1..-4%.  Recording a position-flipping fill as an add (the other candidate) changed it by <= 0.012.

The rule (STRUCTURAL -- no tuned constant: the window is the validator's own sampling interval, the reference is the
median book of the same request):

* Each book's volatility is the sum of |change| of its log mid (bps), state to state, over the last sampling interval
  (600 s, scoring.activity.trade_volume_sampling_interval -- the interval the deep layer's paper record buckets by).
* Once per request the median of those sums over the books is read.  A book at or below the median keeps the whole
  deep bound; a book above it keeps bound x median / its own.  An adding order beyond the scaled bound is cancelled or
  not placed; the reducing side is never touched.  A clock that runs back an hour is a new simulation: all forgotten.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any

from research_v633_deep_layer import REBASE_MIN_JUMP_NS, SAMPLE_NS

V661_VOL_BOUND_VERSION = "vol_bound_v6_6_1"
VOL_WINDOW_NS = SAMPLE_NS
SIDE_BUY = "buy"
SIDE_SELL = "sell"


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def bound_scale(own: Any, median: Any) -> float:
    """The share of the deep bound a book keeps: 1 at or below the median volatility, median / own above it."""
    o, m = _finite(own), _finite(median)
    if o is None or m is None or m <= 0.0 or o <= m:
        return 1.0
    return m / o


def room(side: str, inventory: Any, limit: Any) -> bool:
    """May ``side`` add under ``limit`` (base)?  The deep layer's room() with the limit given."""
    inv, lim = _finite(inventory), _finite(limit)
    if inv is None or lim is None:
        return False
    if side == SIDE_BUY:
        return inv < lim - 1e-9
    if side == SIDE_SELL:
        return inv > -lim + 1e-9
    return False


class VolBound:
    """Every book's recent volatility and the request's median."""

    def __init__(self, *, window_ns: int = VOL_WINDOW_NS):
        self.window_ns = int(window_ns)
        self.rebases = 0
        self.reset()

    def reset(self) -> None:
        self.q: dict[int, deque] = {}
        self.sums: dict[int, float] = {}
        self.prev: dict[int, float] = {}
        self.last_ts: int | None = None
        self.median = 0.0
        self.scaled_now: dict[int, float] = {}

    def maybe_rebase(self, now_ns: Any) -> None:
        try:
            now = int(now_ns)
        except (TypeError, ValueError):
            return
        if self.last_ts is not None and now < self.last_ts - REBASE_MIN_JUMP_NS:
            rebases = self.rebases
            self.reset()
            self.rebases = rebases + 1
        self.last_ts = now if self.last_ts is None else max(self.last_ts, now)

    def begin_pass(self) -> float:
        """Once per request, before the books: the median volatility of the books with a sample in the window."""
        vals = sorted(self.sums[b] for b, q in self.q.items() if q)
        self.median = vals[len(vals) // 2] if vals else 0.0
        self.scaled_now = {}
        return self.median

    def observe(self, book_id: int, ts: int, log_mid_bps: Any) -> None:
        """One state's log mid (bps) for one book."""
        b, ts = int(book_id), int(ts)
        lm = _finite(log_mid_bps)
        if lm is None:
            return
        q = self.q.get(b)
        if q is None:
            q = deque()
            self.q[b] = q
            self.sums[b] = 0.0
        prev = self.prev.get(b)
        if prev is not None:
            d = abs(lm - prev)
            q.append((ts, d))
            self.sums[b] += d
        self.prev[b] = lm
        while q and q[0][0] < ts - self.window_ns:
            self.sums[b] -= q.popleft()[1]
        if not q or self.sums[b] < 0.0:
            self.sums[b] = max(0.0, sum(d for _t, d in q))

    def scale(self, book_id: int) -> float:
        b = int(book_id)
        q = self.q.get(b)
        s = bound_scale(self.sums.get(b), self.median) if q else 1.0
        if s < 1.0:
            self.scaled_now[b] = s
        return s

    def snapshot(self) -> dict[str, Any]:
        return {"version": V661_VOL_BOUND_VERSION, "books": sum(1 for q in self.q.values() if q),
                "median_bps": round(self.median, 3), "scaled_now": len(self.scaled_now),
                "min_scale": round(min(self.scaled_now.values()), 4) if self.scaled_now else 1.0,
                "rebases": int(self.rebases)}
