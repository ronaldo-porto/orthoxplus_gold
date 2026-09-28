# SPDX-License-Identifier: MIT
"""v6.6 S2: pace each book against a budget line -- the volume cap spread evenly until the simulation ends.

Why (mainnet, sim 20260924_1653, read 09-28 10:40 JST):

* UID 94 (v6.4.1) capped six more books under the v6.4.1 pacer (75, 6, 114, 63, 72, 21 -- four of them 08:52-10:33 JST
  09-28) and five more were within 18k quote, book 15 among them (alpha +119, one of its best).  The v6.4.1 rule
  compares the last 600 s of volume with the even-spend rate; on a book with little allowance left that rate is below
  one clip per 600 s, so each window the book is let go it spends a whole fill, and the next window has forgotten it.
* UID 104 (v6.5, <= 56% of any book's cap used) had up to 100 books held at once: every sweep and every restart unwind
  runs the 600-s rate over the even-spend rate however much allowance is left, while quiet hours bank nothing.
* Replay (validator arithmetic, six windows): with S1 and S3, the line raised making in every window and kept the
  skill leg (UID 104's lowest window 3.85 against v6.5's 0.99); on UID 94's allowance at its own 1-base size the
  lowest window went 1.28 -> 2.67 with making +20..35%.

The rule (STRUCTURAL -- all of it the validator's own arithmetic, ``taos/im/validator/query.py`` and the scoring config):

* The first time a book is seen (and again after a new simulation, or when the venue reports less volume than
  before), its line starts at the volume the venue reports for the account and runs straight to the cap at the
  simulation's end (never later than one assessment period, 86,400 sim-s).
* A book whose reported volume is above its line, plus one sampling interval (600 s) of the line's own rate as the
  burst allowance, is paced: it places only orders that reduce its inventory -- the v6.4.1 consumer, unchanged.
* Quiet hours leave the volume under the line, so the next sweep may spend them; overspending stays on the books until
  the line catches up.  A book reaches its cap at the simulation's end, not before.
"""
from __future__ import annotations

import math
from typing import Any

from research_v641_pace import ASSESSMENT_NS, PACE_WINDOW_NS, REBASE_MIN_JUMP_NS

V66_PACE_LINE_VERSION = "pace_line_v6_6"


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def line_end_ns(start_ns: int, sim_duration_ns: int | None, *, assessment_ns: int = ASSESSMENT_NS) -> int:
    """Where the line reaches the cap: the simulation's end, never more than one assessment period after its start."""
    cap_end = int(start_ns) + int(assessment_ns)
    if not sim_duration_ns:
        return cap_end
    return min(cap_end, int(sim_duration_ns))


def line_volume(cap: Any, used0: Any, start_ns: int, now_ns: int, end_ns: int, *, window_ns: int = PACE_WINDOW_NS) -> float:
    """The volume a book may have traded by ``now``: ``used0`` rising evenly to ``cap`` at ``end``, one window early."""
    c, u0 = _finite(cap) or 0.0, max(0.0, _finite(used0) or 0.0)
    span = int(end_ns) - int(start_ns)
    if span <= 0:
        return c
    frac = (int(now_ns) - int(start_ns) + int(window_ns)) / float(span)
    frac = max(0.0, min(1.0, frac))
    return u0 + (c - u0) * frac


class PaceLine:
    """Every book's budget line; the same observe / paced / snapshot face as v6.4.1's VolumePace."""

    def __init__(self, *, window_ns: int = PACE_WINDOW_NS, assessment_ns: int = ASSESSMENT_NS):
        self.window_ns = int(window_ns)
        self.assessment_ns = int(assessment_ns)
        self.rebases = 0
        self.restarts = 0
        self.reset()

    def reset(self) -> None:
        self.start: dict[int, tuple[int, float]] = {}
        self.last_used: dict[int, float] = {}
        self.last_ts: int | None = None
        self.paced_now: set[int] = set()

    def observe(self, book_id: int, ts: int, used: Any) -> None:
        """One state's reported volume for one book: starts its line, or restarts it after a reset of the volume."""
        b, ts = int(book_id), int(ts)
        u = max(0.0, _finite(used) or 0.0)
        if self.last_ts is not None and ts < self.last_ts - REBASE_MIN_JUMP_NS:
            rebases, restarts = self.rebases, self.restarts
            self.reset()
            self.rebases, self.restarts = rebases + 1, restarts
        self.last_ts = ts if self.last_ts is None else max(self.last_ts, ts)
        prev = self.last_used.get(b)
        if b not in self.start or (prev is not None and u < prev - 1e-9):
            if b in self.start:
                self.restarts += 1                  # the venue's volume went down: a new budget, a new line
            self.start[b] = (ts, u)
        self.last_used[b] = u

    def line(self, book_id: int, now_ns: int, cap: Any, sim_duration_ns: int | None) -> float | None:
        st = self.start.get(int(book_id))
        if st is None:
            return None
        t0, u0 = st
        end = line_end_ns(t0, sim_duration_ns, assessment_ns=self.assessment_ns)
        return line_volume(cap, u0, t0, now_ns, end, window_ns=self.window_ns)

    def paced(self, book_id: int, now_ns: int, used: Any, cap: Any, sim_duration_ns: int | None) -> bool:
        """Is this book's reported volume above its line?  Always False without a cap or a line yet."""
        b = int(book_id)
        paced = False
        c = _finite(cap) or 0.0
        if c > 0.0:
            lv = self.line(b, now_ns, c, sim_duration_ns)
            u = _finite(used)
            if lv is not None and u is not None:
                paced = u > lv + 1e-9
        (self.paced_now.add(b) if paced else self.paced_now.discard(b))
        return paced

    def snapshot(self) -> dict[str, Any]:
        return {"version": V66_PACE_LINE_VERSION, "books": len(self.start), "paced_now": len(self.paced_now),
                "rebases": int(self.rebases), "restarts": int(self.restarts)}
