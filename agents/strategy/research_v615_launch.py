# SPDX-License-Identifier: MIT
"""v6.15: a new UID's launch -- spend one making window of its empty volume window early, then pay it back.

Why (mainnet 10-05 JST; scratchpad k18/launch_sim.py, v615/run6.sh):

* v6.8 S5's rolling line starts every book at the volume the venue reports and lets it rise at the sustainable rate
  (the cap per assessment period).  A new UID's window is empty, so every book is held to 1x from its first state, while
  v6.14's books trade 0.05-2.9x of that rate on their own (mean 1.31x): a new UID averages ~0.70x, and its emission when
  its 24-h immunity ends sits far below the prune line (fresh makers in the field reach 0.32-0.89 per tempo by 21-25 h).
* Replayed with the validator's arithmetic (a fresh UID from sim 46,800, simulation 20260929_2015): a 2x line for one
  making window with 2x the deep clip raised the making share of its first windows 0.29/0.53/0.70% -> 0.49/0.89/1.22%,
  making per 1M unchanged (16-19), mean inventory 6.9 -> 9.7 base; the line alone +7-10% (v6.14's books rarely reach it).

The rule (one switch, ``research_v615_launch``):

* A book whose first venue report is an empty window (no volume of ours in the assessment period) launches: its budget
  line rises at LAUNCH_RATE x the sustainable rate for one making window, then at the payback rate that brings it to
  the cap exactly at the end of the assessment period; from there on it is the v6.8 line (the cap).
* While its launch window is open the book's deep orders are LAUNCH_RATE x the clip, so its own fills can reach the
  line.  The inventory bound stays in base (max clips x the layer's clip) and so do the exposure caps.
* A book first seen with volume in its window -- every running UID, every restart -- keeps the v6.8 line and its clip.
  A new simulation shifts the launch start with the line, as the validator shifts its volume history.

STRUCTURAL: the validator's two windows -- one making window of sustainable volume spent early is exactly what the rest
of the assessment window gives back; the size factor is the line factor.  No fitted number.
"""
from __future__ import annotations

import math
from typing import Any

from research_v641_pace import ASSESSMENT_NS, PACE_WINDOW_NS
from research_v68_deep_owns import RollingPaceLine

V615_LAUNCH_VERSION = "launch_v6_15"
MAKING_WINDOW_NS = 10_800_000_000_000      # the validator's making window (10,800 sim-s)
LAUNCH_RATE = 2.0                           # one extra making window of sustainable volume, spent in the first one
EMPTY_WINDOW_QUOTE = 1e-9                   # "no volume of ours": the venue reports zero


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def payback_rate(*, rate: float = LAUNCH_RATE, making_ns: int = MAKING_WINDOW_NS,
                 assessment_ns: int = ASSESSMENT_NS) -> float:
    """The line's rate after the launch window, in multiples of the sustainable rate: it reaches the cap exactly at
    the end of the assessment period."""
    a, m = float(assessment_ns), float(making_ns)
    if a <= m:
        return 1.0
    return max(0.0, (a - float(rate) * m) / (a - m))


def launch_line(cap: Any, start_ns: int, now_ns: int, *, rate: float = LAUNCH_RATE, making_ns: int = MAKING_WINDOW_NS,
                assessment_ns: int = ASSESSMENT_NS, window_ns: int = PACE_WINDOW_NS) -> float:
    """The volume a launching book may have in the validator's window by ``now``: LAUNCH_RATE x the sustainable rate
    for one making window, then the payback rate, one sampling window early, never above the cap."""
    c = max(0.0, _finite(cap) or 0.0)
    a = float(max(1, int(assessment_ns)))
    m = float(max(0, int(making_ns)))
    x = max(0.0, float(int(now_ns) - int(start_ns) + int(window_ns)))
    spent = float(rate) * min(x, m) + payback_rate(rate=rate, making_ns=int(m), assessment_ns=int(a)) * max(0.0, x - m)
    return min(c, c * spent / a)


class LaunchPaceLine(RollingPaceLine):
    """v6.8's rolling line, with the launch line for every book first seen on an empty window."""

    def __init__(self, *, rate: float = LAUNCH_RATE, making_ns: int = MAKING_WINDOW_NS, **kwargs: Any):
        self.rate = float(rate)
        self.making_ns = int(making_ns)
        super().__init__(**kwargs)

    def reset(self) -> None:
        super().reset()
        self.launching: set[int] = set()

    def observe(self, book_id: int, ts: int, used: Any) -> None:
        b = int(book_id)
        new = b not in self.start
        super().observe(book_id, ts, used)
        if new and b in self.start and self.start[b][1] <= EMPTY_WINDOW_QUOTE:
            self.launching.add(b)

    def line(self, book_id: int, now_ns: int, cap: Any, sim_duration_ns: int | None = None) -> float | None:
        b = int(book_id)
        if b not in self.launching:
            return super().line(book_id, now_ns, cap, sim_duration_ns)
        t0, _u0 = self.start[b]
        return launch_line(cap, t0, now_ns, rate=self.rate, making_ns=self.making_ns, assessment_ns=self.assessment_ns,
                           window_ns=self.window_ns)

    def launch_open(self, book_id: int, now_ns: int) -> bool:
        """Is this book inside its launch window (first seen on an empty window, less than one making window ago)?"""
        b = int(book_id)
        if b not in self.launching:
            return False
        t0, _u0 = self.start[b]
        return int(now_ns) - int(t0) < self.making_ns

    def snapshot(self) -> dict[str, Any]:
        out = super().snapshot()
        now = self.last_ts
        out["launch"] = {"version": V615_LAUNCH_VERSION, "rate": self.rate, "books": len(self.launching),
                         "open_now": (sum(1 for b in self.launching if self.launch_open(b, now)) if now is not None else 0)}
        return out


def launch_clip(clip: Any, pace: Any, book_id: int, now_ns: int, *, rate: float = LAUNCH_RATE) -> float:
    """The deep clip for this book now: LAUNCH_RATE x the clip while its launch window is open, the clip otherwise."""
    c = max(0.0, _finite(clip) or 0.0)
    is_open = getattr(pace, "launch_open", None)
    if callable(is_open) and bool(is_open(book_id, now_ns)):
        return c * float(rate)
    return c
