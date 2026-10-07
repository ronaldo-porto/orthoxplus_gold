# SPDX-License-Identifier: MIT
"""v6.20: spend the room a book has left over one assessment period, and slow down by trading smaller -- research_v620_room_pace.

Why (mainnet 10-07 JST; scratchpad k30/, k31/: replays vm629b / vm630-vm633 with the validator's arithmetic and the
true pay rule):

* The validator caps each book's volume over a rolling assessment period (86,400 sim-s).  Volume a book trades rolls
  off only one assessment period later, so a UID whose window is young (UIDs 172 and 10, registered 10-06) gets no room
  back until then.  v6.8's line lets every book rise at the sustainable rate (cap / assessment period) from its first
  report, and v6.4.1's pacing holds only ADDING orders above it: reducing orders, the kept deepest ladder level and the
  touch quote keep filling.  Live, 172 and 10 burned their caps at 2.3x sustainable (4.2x before the 09:18 relaunch)
  and reach 95% of the cap ~3 days before anything rolls off: dark for days, below the prune line.
* Holding orders back instead (every order above the line, or a paced book that only unwinds) halves the making per
  unit of volume (k30: fresh 27.3 -> 13.9 per 1M, aged -51%): the validator pays 2 x min(buy, sell) capture per book
  and window, and a held side leaves one-sided fills.  The venue's minimum order only while above the line is no
  better (k31 P1: 20.9 -> 13.7 per 1M): a book that drops back behind the line trades full-size one-sided bursts.
  Orders kept small throughout keep both sides filling (k30: every size x0.3 -> 21.8 per 1M at 0.60x sustainable),
  and a size that shrinks smoothly with the lead does the same without a fitted factor.
* The same line for every book was REFUTED for aged UIDs (k31 P5): over the whole previous simulation an aged UID held
  to it lost 18.6% of its making share (0/7 validator windows ahead; same volume, 25% of book-states paced instead of
  free under v6.8's line, which reaches the cap within hours of a start).  The cliff belongs to young windows only: a
  window that rolls off gives room back continuously.
* Replays of this rule (k31 P6, every arm against v6.19 on the same tape; 3-h validator windows):
    172-like (young window, first report at 49 / 67% of the cap)  burn 1.87x -> 0.80x / 1.47x -> 0.67x sustainable over
                                                                   the 26-h tape (last third 0.45x / 0.30x, toward the
                                                                   line); cap used at the end 66.6% / 81.7% (v6.19 89.8%
                                                                   / 99.2%, at the cap); making per 1M 20.9 -> 23.7 / 21.1
                                                                   -> 24.2
    aged UID, the previous simulation (7 full windows)             making share -0.0%, traffic x1.00 (young for its first
                                                                   0.4% of book-states, then v6.19 exactly)
    aged UID entering a new simulation (seam)                      making share +8.0% (4/5 windows ahead), traffic x1.01
    fresh UID / with launch mode                                   2.00x -> 1.33x / 3.41x -> 2.33x sustainable, making per
                                                                   1M 25.3 -> 27.4 / 23.6 -> 23.8; making in the first
                                                                   window -30% (the leak beyond the launch line is gone)

The rule (one switch):

* Young.  A book is young while the venue's volume for it has not fallen since its first report; the first fall is
  its window rolling off, and from then on (for the life of the process) it is aged: the v6.15 / v6.8 line and full
  sizes, v6.19 exactly.
* The line (young books).  A book first seen with volume spends the room between that volume and the cap-bound
  threshold T = cap - cap x making window / assessment period (v6.19's) over one assessment period from its first
  report; one first seen at or above T keeps a flat line there.  A launching book (empty window, v6.15) keeps the
  launch line, never above T.
* The size (young books).  Every order the deep layer and the touch quote place is scaled by
  f = 1 - (volume - line) / (cap x making window / assessment period), clamped to [0, 1]: full size at or behind the
  line, the venue's minimum order one making window of the cap ahead of it.  Pacing still holds adding orders above
  the line (v6.4.1).  The line aims at T, so the line plus the band stays below the cap.

STRUCTURAL: the validator's volume cap, its assessment period and making window, and the venue's minimum order; no
fitted constant.
"""
from __future__ import annotations

import math
from typing import Any

from research_v641_pace import ASSESSMENT_NS
from research_v615_launch import MAKING_WINDOW_NS, LaunchPaceLine, launch_line

V620_ROOM_PACE_VERSION = "room_pace_v6_20"


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def band(cap: Any, *, making_ns: int = MAKING_WINDOW_NS, assessment_ns: int = ASSESSMENT_NS) -> float:
    """The cap's share of one making window: the size band, and the room v6.19 calls cap-bound."""
    c = max(0.0, _finite(cap) or 0.0)
    return c * float(making_ns) / float(max(1, int(assessment_ns)))


def threshold(cap: Any, *, making_ns: int = MAKING_WINDOW_NS, assessment_ns: int = ASSESSMENT_NS) -> float:
    """The cap-bound threshold T: the cap less one making window of it."""
    c = max(0.0, _finite(cap) or 0.0)
    return c - band(c, making_ns=making_ns, assessment_ns=assessment_ns)


def room_line(cap: Any, used0: Any, start_ns: int, now_ns: int, *, making_ns: int = MAKING_WINDOW_NS,
              assessment_ns: int = ASSESSMENT_NS, window_ns: int) -> float:
    """The volume a book first seen at ``used0`` may have by ``now``: the room up to T spread over one assessment
    period (one sampling window early), flat at ``used0`` when it starts at or above T, never above the cap."""
    c = max(0.0, _finite(cap) or 0.0)
    u0 = max(0.0, _finite(used0) or 0.0)
    t = threshold(c, making_ns=making_ns, assessment_ns=assessment_ns)
    if u0 >= t:
        return min(c, u0)
    x = max(0.0, float(int(now_ns) - int(start_ns) + int(window_ns)))
    return min(t, u0 + (t - u0) * x / float(max(1, int(assessment_ns))))


def size_scale(used: Any, line: Any, cap: Any, *, making_ns: int = MAKING_WINDOW_NS,
               assessment_ns: int = ASSESSMENT_NS) -> float:
    """The factor on every order's size: 1 at or behind the line, 0 one making window of the cap ahead of it."""
    u = _finite(used)
    lv = _finite(line)
    w = band(cap, making_ns=making_ns, assessment_ns=assessment_ns)
    if u is None or lv is None or w <= 0.0:
        return 1.0
    return min(1.0, max(0.0, 1.0 - max(0.0, u - lv) / w))


def scaled_quantity(quantity: Any, scale: Any, min_order: Any) -> float:
    """``quantity`` x ``scale``, never below the venue's minimum order (a quantity already below it is left alone)."""
    q = max(0.0, _finite(quantity) or 0.0)
    s = _finite(scale)
    m = max(0.0, _finite(min_order) or 0.0)
    if s is None or s >= 1.0 or q < m:
        return q
    return max(m, q * max(0.0, s))


class RoomPaceLine(LaunchPaceLine):
    """v6.8's rolling bookkeeping (first report, a new simulation shifts the start) and v6.15's launch line, with the
    room line and the size factor for every book whose window is still young: its volume has not fallen since its first
    report.  The first fall is volume rolling off; from then on the book is aged and keeps the v6.15 / v6.8 line and
    full-size orders."""

    def __init__(self, *, launch: bool = True, **kwargs: Any):
        self.launch_on = bool(launch)
        super().__init__(**kwargs)

    def reset(self) -> None:
        super().reset()
        self.aged: set[int] = set()

    def observe(self, book_id: int, ts: int, used: Any) -> None:
        b = int(book_id)
        prev = self.last_used.get(b)
        u = _finite(used)
        if prev is not None and u is not None and 0.0 < u < prev - max(1e-6, 1e-9 * prev):
            # the venue's volume fell: this window rolls off -- not young.  A zero is no reading (a missing account
            # reports 0, and a young window cannot empty in one step).
            self.aged.add(b)
        super().observe(book_id, ts, used)
        if not self.launch_on:
            self.launching.discard(b)

    def young(self, book_id: int) -> bool:
        """Has this book's volume never fallen since its first report?"""
        b = int(book_id)
        return b in self.start and b not in self.aged

    def line(self, book_id: int, now_ns: int, cap: Any, sim_duration_ns: int | None = None) -> float | None:
        b = int(book_id)
        st = self.start.get(b)
        if st is None:
            return None
        if b in self.aged:
            return super().line(book_id, now_ns, cap, sim_duration_ns)     # v6.15 / v6.8 exactly
        t0, u0 = st
        if b in self.launching:
            lv = launch_line(cap, t0, now_ns, rate=self.rate, making_ns=self.making_ns, assessment_ns=self.assessment_ns,
                             window_ns=self.window_ns)
            return min(lv, threshold(cap, making_ns=self.making_ns, assessment_ns=self.assessment_ns))
        return room_line(cap, u0, t0, now_ns, making_ns=self.making_ns, assessment_ns=self.assessment_ns,
                         window_ns=self.window_ns)

    def scale(self, book_id: int, now_ns: int, used: Any, cap: Any, sim_duration_ns: int | None = None) -> float:
        """This book's order-size factor now: 1 for an aged book or one without a line yet."""
        if not self.young(book_id):
            return 1.0
        return size_scale(used, self.line(book_id, now_ns, cap, sim_duration_ns), cap, making_ns=self.making_ns,
                          assessment_ns=self.assessment_ns)

    def snapshot(self) -> dict[str, Any]:
        out = super().snapshot()
        out["room"] = {"version": V620_ROOM_PACE_VERSION, "launch_on": int(self.launch_on),
                       "young": sum(1 for b in self.start if b not in self.aged), "aged": len(self.aged)}
        return out


__all__ = ["V620_ROOM_PACE_VERSION", "RoomPaceLine", "band", "room_line", "scaled_quantity", "size_scale", "threshold"]
