# SPDX-License-Identifier: MIT
"""v6.28: S8 the flat launch -- a launch that starts under v6.28 spends at the sustainable rate, a launch already
running keeps the rate it started with -- research_v628_flat_launch.

Why (mainnet 10-13 JST; scratchpad k70: UID 115's tape, replay vm670 = k65's vm665 with the launch rate and the launch
clip as parameters, the validator's arithmetic and the true pay rule):

* v6.15 S1 launches a book first seen on an empty volume window (a fresh UID): its line rises at LAUNCH_RATE (2) x the
  sustainable rate for one making window (10,800 sim-s) with LAUNCH_RATE x the deep clip, then pays it back (v6.21 S3:
  evenly to the aim at its own roll-off).  UID 115 (launched 10-12) earned the burst during its 24-h immunity, when the
  slot cannot be lost, and its payback began as the immunity ended: score E* 1.50 -> ~0.9.
* Replayed from its window start at launch rate 1 without the launch clip, everything else v6.27: post-immunity E +11.4%
  (+9..+21% over start delays and thirds), launch-window making -35% -- the volume moves from the immune window into the
  payback.  User decision 10-13: new UIDs launch at 1x.

The rule (one switch, research_v628_flat_launch):

* A book whose launch starts under this build -- first seen on an empty window by this process -- launches at
  FLAT_LAUNCH_RATE: v6.15's launch line at that rate (its payback rate is that rate too, so the line is v6.8's rolling
  line from zero) and, with v6.21 S3, the even line from one making window of the cap's sustainable share to the aim at
  its roll-off; its deep clip is the layer's clip (v6.15's launch clip at that rate).
* The rate rides with the book's launch record: v6.21 S2's pace state carries it in the book's row (RATE_KEY), and a
  restart of the same UID and simulation brings it back.  A launch record without a rate -- every one saved before
  v6.28 (UIDs 115 / 78 / 161 on 10-13: all 128 books) -- keeps v6.15's LAUNCH_RATE, line and clip exactly as v6.27 runs
  them; so does a book this process first saw with volume (its launch, if any, started before this process: v6.21's
  launch line rebuilt from the UID's window start).  A saved rate this build cannot run is no record.
* Nothing else changes: the young window (v6.20 / v6.21), pacing, the size factor, the roll-off guard, the aim.  The
  agent keeps every error to the book's v6.27 behaviour.  Off: v6.27.

STRUCTURAL: the sustainable rate itself (the cap per assessment period: a launch at it spends no making window early)
and v6.15's LAUNCH_RATE for a launch that started under it -- no fitted number.  OBSERVED: the k70 replay above (UID
115's tape, one simulation).
"""
from __future__ import annotations

import math
from typing import Any

from research_v615_launch import EMPTY_WINDOW_QUOTE, LAUNCH_RATE
from research_v615_launch import launch_clip as v615_launch_clip
from research_v621_young_window import EVEN_LAUNCH, RESTORE_REKEYED, RESTORE_RESTORED, YoungWindowLine

V628_FLAT_LAUNCH_VERSION = "flat_launch_v6_28"
FLAT_LAUNCH_RATE = 1.0           # STRUCTURAL: the sustainable rate -- a launch at it spends no making window early
LEGACY_LAUNCH_RATE = LAUNCH_RATE  # STRUCTURAL: v6.15's -- a launch that started under it, or whose record has no rate
RATE_KEY = "rate"                # the book's launch rate in its v6.21 pace-state row


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def recorded_rate(value: Any) -> float | None:
    """A saved launch rate this build runs: FLAT_LAUNCH_RATE (v6.28's) or LEGACY_LAUNCH_RATE (v6.15's).  Anything
    else -- missing, not a number, a bool, any other number -- is no record (None)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        r = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(r) or r not in (FLAT_LAUNCH_RATE, LEGACY_LAUNCH_RATE):
        return None
    return r


class FlatLaunchLine(YoungWindowLine):
    """v6.21's young-window line, each book's launch at the rate it started with: ``flat_rate`` for a launch this
    process starts (a book first seen on an empty window) or one a v6.28 record brings back, the line's own rate
    (v6.15's LAUNCH_RATE) for every other.  v6.15 / v6.20 / v6.21 read the launch rate from the line's ``rate``; each
    book's line and size factor run their code unchanged with ``rate`` set to the book's for the call."""

    def __init__(self, *, flat_rate: float = FLAT_LAUNCH_RATE, **kwargs: Any):
        self.flat_rate = float(flat_rate)
        super().__init__(**kwargs)
        self.legacy_rate = float(self.rate)

    def reset(self) -> None:
        super().reset()
        self.launch_rates: dict[int, float] = {}     # book -> its launch's rate (started here, or from a record)
        self.errors = 0

    def rate_of(self, book_id: int) -> float:
        """The rate this book's launch runs at: its own record, else v6.15's (the line's rate).  Never raises."""
        try:
            r = self.launch_rates.get(int(book_id))
        except Exception:
            self.errors += 1
            r = None
        return self.legacy_rate if r is None else float(r)

    def observe(self, book_id: int, ts: int, used: Any, fall_floor: Any = 0.0) -> None:
        b = int(book_id)
        new = b not in self.start
        super().observe(book_id, ts, used, fall_floor)
        if new and b in self.start and self.start[b][1] <= EMPTY_WINDOW_QUOTE:
            try:
                self.launch_rates[b] = self.flat_rate        # its launch starts here, under v6.28
            except Exception:
                self.errors += 1                            # no record: v6.15's rate, as v6.27

    def _at_own_rate(self, book_id: int, fn: Any, *args: Any) -> Any:
        rest = self.rate
        try:
            self.rate = self.rate_of(book_id)
            return fn(*args)
        finally:
            self.rate = rest

    def line(self, book_id: int, now_ns: int, cap: Any, sim_duration_ns: int | None = None) -> float | None:
        return self._at_own_rate(book_id, super().line, book_id, now_ns, cap, sim_duration_ns)

    def scale(self, book_id: int, now_ns: int, used: Any, cap: Any, sim_duration_ns: int | None = None) -> float:
        return self._at_own_rate(book_id, super().scale, book_id, now_ns, used, cap, sim_duration_ns)

    def launch_books(self) -> list[int]:
        """Books on a launch: v6.15's launching set and v6.21's launch line."""
        return sorted(set(self.launching) | {b for b, st in self.even.items() if st and st[0] == EVEN_LAUNCH})

    def rates_summary(self) -> dict[str, int]:
        """Books on a launch at the flat rate and at v6.15's, and of them those inside their launch window now."""
        now = self.last_ts
        out = {"flat": 0, "legacy": 0, "open_flat": 0, "open_legacy": 0}
        for b in self.launch_books():
            key = "flat" if self.rate_of(b) == self.flat_rate else "legacy"
            out[key] += 1
            if now is not None and self.launch_open(b, now):
                out["open_" + key] += 1
        return out

    def snapshot(self) -> dict[str, Any]:
        out = super().snapshot()
        out["launch"]["flat_launch"] = dict(self.rates_summary(), version=V628_FLAT_LAUNCH_VERSION,
                                            flat_rate=self.flat_rate, legacy_rate=self.legacy_rate,
                                            errors=int(self.errors))
        return out


def launch_clip(clip: Any, pace: Any, book_id: int, now_ns: int) -> float:
    """The deep clip for this book now: v6.15's launch clip at the rate the book's launch runs at -- the layer's clip in
    a flat launch window, LEGACY_LAUNCH_RATE x it in a v6.15 one (v6.27), the clip outside one."""
    rate_of = getattr(pace, "rate_of", None)
    rate = float(rate_of(book_id)) if callable(rate_of) else LEGACY_LAUNCH_RATE
    return v615_launch_clip(clip, pace, book_id, now_ns, rate=rate)


def save_rates(line: Any, state: Any) -> int:
    """v6.21 S2's pace state (``state``, of ``line``) with each recorded launch rate in its book's row (RATE_KEY); a
    book without a record keeps v6.21's row (no rate: v6.15's after a restart).  Returns the rows written."""
    rates = getattr(line, "launch_rates", None)
    books = state.get("books") if isinstance(state, dict) else None
    if not isinstance(rates, dict) or not isinstance(books, dict):
        return 0
    n = 0
    for b, r in rates.items():
        row = books.get(str(int(b)))
        rate = recorded_rate(r)
        if isinstance(row, dict) and rate is not None:
            row[RATE_KEY] = rate
            n += 1
    return n


def restore_rates(line: Any, payload: Any, how: Any) -> dict[str, int]:
    """After v6.21 S2 restored (or re-keyed) ``payload`` into ``line`` (``how``): each restored book's launch rate from
    its row.  A rate this build runs is the book's (flat / legacy); a row without one (unrecorded: every row saved
    before v6.28) or with one it cannot run (invalid) leaves the book on v6.15's rate.  Anything else restores
    nothing."""
    out = {"flat": 0, "legacy": 0, "unrecorded": 0, "invalid": 0}
    if how not in (RESTORE_RESTORED, RESTORE_REKEYED) or not isinstance(payload, dict):
        return out
    books = payload.get("books")
    rates = getattr(line, "launch_rates", None)
    start = getattr(line, "start", None)
    if not isinstance(books, dict) or not isinstance(rates, dict) or not isinstance(start, dict):
        return out
    for key, row in books.items():
        b = _int(key)
        if b is None or b not in start or not isinstance(row, dict):
            continue                                    # a row v6.21 did not restore
        if RATE_KEY not in row:
            out["unrecorded"] += 1
            continue
        rate = recorded_rate(row.get(RATE_KEY))
        if rate is None:
            out["invalid"] += 1
            continue
        rates[b] = rate
        out["flat" if rate == FLAT_LAUNCH_RATE else "legacy"] += 1
    return out


__all__ = ["FLAT_LAUNCH_RATE", "FlatLaunchLine", "LEGACY_LAUNCH_RATE", "RATE_KEY", "V628_FLAT_LAUNCH_VERSION",
           "launch_clip", "recorded_rate", "restore_rates", "save_rates"]
