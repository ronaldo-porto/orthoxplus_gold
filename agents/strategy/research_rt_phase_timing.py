# SPDX-License-Identifier: MIT
"""Round-trip phase timing.

Splits a completed round trip into the three waits that govern throughput, so a
round-trip-rate deficit can be attributed rather than guessed at:

    entry_wait = first entry quote submitted -> entry fills (position opens)
    hold       = position opens -> first exit order submitted
    exit_wait  = first exit order submitted -> position returns flat

    round_trips_per_hour = completed_round_trips / simulation_time * 3600

Entry/exit anchors are the *first* submission of each lifecycle, not the most
recent one, so requoting is charged to the phase it delays. Per-order fill
latency is already covered by ``quote_age_ms`` on the FILL event; these are the
complementary lifecycle-level durations.

Pure functions and plain dataclasses so unit tests do not import Strategy1 /
bittensor.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from research_velocity import percentile

RT_PHASE_VERSION = "rt_phase_timing_v1"

# Matches VelocityState.realization_times: bounded so a long session cannot grow
# the sample lists without limit.
MAX_SAMPLES = 2048

NS_PER_S = 1_000_000_000.0
S_PER_HOUR = 3600.0


def elapsed_s(start_ns: Any, end_ns: Any) -> float | None:
    """Simulation seconds between two nanosecond stamps, or None if unusable."""
    try:
        start = float(start_ns)
        end = float(end_ns)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(start) or not math.isfinite(end):
        return None
    delta = (end - start) / NS_PER_S
    if delta < 0.0:
        return None
    return delta


def _append_capped(values: list[float], sample: float) -> None:
    values.append(sample)
    if len(values) > MAX_SAMPLES:
        del values[:-MAX_SAMPLES]


@dataclass
class BookPhase:
    """Anchors for the round trip currently in flight on one book."""

    entry_submit_ts: float | None = None
    entry_fill_ts: float | None = None
    exit_submit_ts: float | None = None

    def clear(self) -> None:
        self.entry_submit_ts = None
        self.entry_fill_ts = None
        self.exit_submit_ts = None


@dataclass
class RoundTripPhaseState:
    books: dict[int, BookPhase] = field(default_factory=dict)
    entry_wait: list[float] = field(default_factory=list)
    hold: list[float] = field(default_factory=list)
    exit_wait: list[float] = field(default_factory=list)
    total: list[float] = field(default_factory=list)
    completed: int = 0
    missing_entry_submit: int = 0
    missing_exit_submit: int = 0
    # Little's Law needs the mean time in system, and the sample lists are
    # capped. Accumulate the mean separately so it stays unbiased over long runs.
    sum_total_s: float = 0.0
    count_total_s: int = 0

    def _book(self, book_id: int) -> BookPhase:
        return self.books.setdefault(int(book_id), BookPhase())

    def note_entry_submit(self, book_id: int, timestamp: Any) -> None:
        """First quote placed while flat. Ignored once the position is open."""
        book = self._book(book_id)
        if book.entry_fill_ts is not None or book.entry_submit_ts is not None:
            return
        try:
            ts = float(timestamp)
        except (TypeError, ValueError):
            return
        if math.isfinite(ts):
            book.entry_submit_ts = ts

    def note_entry_fill(self, book_id: int, timestamp: Any) -> None:
        """Position went flat -> open."""
        book = self._book(book_id)
        try:
            ts = float(timestamp)
        except (TypeError, ValueError):
            return
        if not math.isfinite(ts):
            return
        book.entry_fill_ts = ts
        book.exit_submit_ts = None

    def note_exit_submit(self, book_id: int, timestamp: Any) -> None:
        """First realization order of this lifecycle."""
        book = self._book(book_id)
        if book.exit_submit_ts is not None:
            return
        try:
            ts = float(timestamp)
        except (TypeError, ValueError):
            return
        if math.isfinite(ts):
            book.exit_submit_ts = ts

    def note_round_trip(
        self,
        book_id: int,
        timestamp: Any,
        *,
        reopen: bool = False,
    ) -> dict[str, Any]:
        """Record a completed round trip and start the next lifecycle.

        ``reopen`` is for CROSS fills, which close the old lifecycle and leave an
        opposite residual already open at the same instant.
        """
        book = self._book(book_id)
        try:
            ts = float(timestamp)
        except (TypeError, ValueError):
            ts = float("nan")

        entry_wait = elapsed_s(book.entry_submit_ts, book.entry_fill_ts)
        hold = elapsed_s(book.entry_fill_ts, book.exit_submit_ts)
        exit_wait = elapsed_s(book.exit_submit_ts, ts)
        total = elapsed_s(book.entry_submit_ts, ts)
        if total is None:
            total = elapsed_s(book.entry_fill_ts, ts)

        if book.entry_submit_ts is None:
            self.missing_entry_submit += 1
        if book.exit_submit_ts is None:
            self.missing_exit_submit += 1

        if entry_wait is not None:
            _append_capped(self.entry_wait, entry_wait)
        if hold is not None:
            _append_capped(self.hold, hold)
        if exit_wait is not None:
            _append_capped(self.exit_wait, exit_wait)
        if total is not None:
            _append_capped(self.total, total)
            self.sum_total_s += total
            self.count_total_s += 1
        self.completed += 1

        book.clear()
        if reopen and math.isfinite(ts):
            # The crossing fill is itself the entry for the residual, so there is
            # no separate submission to anchor entry_wait against.
            book.entry_fill_ts = ts

        return {
            "book_id": int(book_id),
            "entry_wait_s": entry_wait,
            "hold_s": hold,
            "exit_wait_s": exit_wait,
            "total_s": total,
        }

    def forget_book(self, book_id: int) -> None:
        self.books.pop(int(book_id), None)

    def books_in_flight(self) -> int:
        return sum(1 for b in self.books.values() if b.entry_fill_ts is not None)

    def snapshot(self, *, simulation_time: float) -> dict[str, Any]:
        try:
            sim_s = max(0.0, float(simulation_time))
        except (TypeError, ValueError):
            sim_s = 0.0

        entry_med = percentile(self.entry_wait, 0.50)
        hold_med = percentile(self.hold, 0.50)
        exit_med = percentile(self.exit_wait, 0.50)
        total_med = percentile(self.total, 0.50)

        per_hour = (self.completed / sim_s * S_PER_HOUR) if sim_s > 0.0 else 0.0
        total_mean = (
            self.sum_total_s / self.count_total_s if self.count_total_s > 0 else None
        )

        # Shares are normalized over the sum of the three phase medians rather
        # than total_med: medians are not additive, and the question these answer
        # is only "which phase dominates".
        parts = [entry_med or 0.0, hold_med or 0.0, exit_med or 0.0]
        denom = sum(parts)
        shares = [(p / denom) if denom > 0.0 else None for p in parts]

        return {
            "rt_phase_version": RT_PHASE_VERSION,
            "rt_phase_samples": int(self.completed),
            "rt_per_sim_hour": per_hour,
            "rt_entry_wait_s_median": entry_med,
            "rt_entry_wait_s_p90": percentile(self.entry_wait, 0.90),
            "rt_hold_s_median": hold_med,
            "rt_hold_s_p90": percentile(self.hold, 0.90),
            "rt_exit_wait_s_median": exit_med,
            "rt_exit_wait_s_p90": percentile(self.exit_wait, 0.90),
            "rt_total_s_median": total_med,
            "rt_total_s_mean": total_mean if total_mean is not None else 0.0,
            "rt_total_s_p90": percentile(self.total, 0.90),
            "rt_entry_wait_share": shares[0],
            "rt_hold_share": shares[1],
            "rt_exit_wait_share": shares[2],
            # Little's Law: L = lambda * W, with W the *mean* time in system.
            # Using the median would bias this low on a right-skewed hold-time
            # distribution and read as "starved" on a genuinely capped run.
            # Compare against cap_mean_total_open, which measures the same
            # quantity directly; disagreement means round trips are being missed.
            "rt_implied_concurrency": (
                per_hour * total_mean / S_PER_HOUR
                if total_mean is not None and per_hour > 0.0
                else None
            ),
            "rt_books_in_flight": self.books_in_flight(),
            "rt_missing_entry_submit": int(self.missing_entry_submit),
            "rt_missing_exit_submit": int(self.missing_exit_submit),
        }
