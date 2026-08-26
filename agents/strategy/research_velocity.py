# SPDX-License-Identifier: MIT
"""Research velocity and hybrid-summary metrics.

Pure functions so unit tests do not import Strategy1 / bittensor.

    RoundTripVelocity = completed_round_trips / simulation_time
    RoundTripConversion = round_trip_volume / total_volume
    CoverageVelocity = new_active_books / simulation_time
    KappaQualificationVelocity = new_qualified_books / simulation_time
    InventoryRealizationTime = time from inventory creation until flat/reduced
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

VELOCITY_VERSION = "velocity_metrics_v1"

ACTION_PASSIVE = "PASSIVE_MAKER_EXIT"
ACTION_COMPETITIVE = "COMPETITIVE_MAKER_EXIT"
ACTION_AGGRESSIVE = "AGGRESSIVE_MAKER_EXIT"
ACTION_TAKER = "SELECTIVE_TAKER_EXIT"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def rate(count: Any, simulation_time: Any) -> float:
    """count / simulation_time. Zero when time is not positive."""
    try:
        n = max(0.0, float(count))
        t = float(simulation_time)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(n) or not math.isfinite(t) or t <= 0.0:
        return 0.0
    return n / t


def round_trip_velocity(completed_round_trips: int, simulation_time: float) -> float:
    return rate(completed_round_trips, simulation_time)


def round_trip_conversion(round_trip_volume: float, total_volume: float) -> float:
    """round_trip_volume / total_volume, clipped to [0, 1]."""
    num = max(0.0, _finite(round_trip_volume))
    den = _finite(total_volume)
    if den <= 0.0:
        return 0.0
    return min(1.0, num / den)


def coverage_velocity(new_active_books: int, simulation_time: float) -> float:
    return rate(new_active_books, simulation_time)


def kappa_qualification_velocity(new_qualified_books: int, simulation_time: float) -> float:
    return rate(new_qualified_books, simulation_time)


def inventory_realization_time(opened_ts: Any, closed_ts: Any) -> float | None:
    """Elapsed simulation time from inventory creation until flat/reduced."""
    opened = _finite(opened_ts, default=float("nan"))
    closed = _finite(closed_ts, default=float("nan"))
    if not math.isfinite(opened) or not math.isfinite(closed):
        return None
    elapsed = closed - opened
    if elapsed < 0.0:
        return None
    return elapsed


def percentile(samples: Sequence[float] | Iterable[float], q: float) -> float | None:
    values = sorted(_finite(v) for v in samples if math.isfinite(_finite(v, default=float("nan"))))
    if not values:
        return None
    p = min(1.0, max(0.0, _finite(q)))
    if len(values) == 1:
        return values[0]
    idx = p * (len(values) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return values[lo]
    w = idx - lo
    return values[lo] * (1.0 - w) + values[hi] * w


def classify_exit_bucket(action: str | None) -> str:
    token = str(action or "").upper()
    if token == ACTION_TAKER or "TAKER" in token:
        return "taker"
    if token == ACTION_AGGRESSIVE or "AGGRESSIVE" in token:
        return "aggressive_maker"
    if token == ACTION_COMPETITIVE or "COMPETITIVE" in token:
        return "competitive_maker"
    return "maker"


@dataclass
class ExitBucket:
    count: int = 0
    pnl: float = 0.0

    def add(self, pnl: float = 0.0) -> None:
        self.count += 1
        self.pnl += _finite(pnl)


@dataclass
class VelocityState:
    completed_round_trips: int = 0
    round_trip_volume: float = 0.0
    total_volume: float = 0.0
    new_active_books: int = 0
    new_qualified_books: int = 0
    seen_active: set[int] = field(default_factory=set)
    seen_qualified: set[int] = field(default_factory=set)
    open_ts: dict[int, float] = field(default_factory=dict)
    realization_times: list[float] = field(default_factory=list)
    realization_times_by_book: dict[int, list[float]] = field(default_factory=dict)
    maker: ExitBucket = field(default_factory=ExitBucket)
    competitive_maker: ExitBucket = field(default_factory=ExitBucket)
    aggressive_maker: ExitBucket = field(default_factory=ExitBucket)
    taker: ExitBucket = field(default_factory=ExitBucket)
    pending_exit_action: dict[int, str] = field(default_factory=dict)

    def note_volume(self, quantity: float) -> None:
        self.total_volume += max(0.0, _finite(quantity))

    def note_active_book(self, book_id: int) -> None:
        bid = int(book_id)
        if bid not in self.seen_active:
            self.seen_active.add(bid)
            self.new_active_books += 1

    def note_qualified_book(self, book_id: int, *, eligible: bool) -> None:
        if not eligible:
            return
        bid = int(book_id)
        if bid not in self.seen_qualified:
            self.seen_qualified.add(bid)
            self.new_qualified_books += 1

    def note_open(self, book_id: int, timestamp: float) -> None:
        self.open_ts[int(book_id)] = _finite(timestamp)
        self.note_active_book(book_id)

    def note_realized(
        self,
        book_id: int,
        timestamp: float,
        *,
        closed_qty: float = 0.0,
        round_trip: bool = False,
        flatten: bool = False,
    ) -> float | None:
        opened = self.open_ts.get(int(book_id))
        elapsed = None if opened is None else inventory_realization_time(opened, timestamp)
        if elapsed is not None:
            self.realization_times.append(elapsed)
            per_book = self.realization_times_by_book.setdefault(int(book_id), [])
            per_book.append(elapsed)
            if len(per_book) > 64:
                del per_book[:-64]
            if len(self.realization_times) > 2048:
                del self.realization_times[:-2048]
        if flatten:
            self.open_ts.pop(int(book_id), None)
        if round_trip:
            self.completed_round_trips += 1
            self.round_trip_volume += max(0.0, _finite(closed_qty))
        return elapsed

    def expected_realization_time(self, book_id: int | None = None) -> tuple[float | None, float | None]:
        """Return (book median, global median) empirical realization time.

        The caller can use the global median as a shrinkage/reference prior for
        cold books rather than assuming instant realization.
        """
        global_med = percentile(self.realization_times, 0.50)
        if book_id is None:
            return None, global_med
        values = self.realization_times_by_book.get(int(book_id), [])
        book_med = percentile(values, 0.50)
        return book_med, global_med

    def note_exit_intent(self, book_id: int, action: str) -> None:
        self.pending_exit_action[int(book_id)] = str(action or "")

    def note_exit_fill(self, book_id: int, pnl: float, action: str | None = None) -> str:
        token = action if action is not None else self.pending_exit_action.pop(int(book_id), None)
        bucket = classify_exit_bucket(token)
        getattr(self, bucket).add(pnl)
        return bucket

    def snapshot(
        self,
        *,
        simulation_time: float,
        inventory_ages: Sequence[float] | None = None,
        completed_round_trips: int | None = None,
    ) -> dict[str, Any]:
        ages = list(inventory_ages or ())
        trips = (
            int(self.completed_round_trips)
            if completed_round_trips is None
            else max(0, int(completed_round_trips))
        )
        return {
            "velocity_version": VELOCITY_VERSION,
            "simulation_time": max(0.0, _finite(simulation_time)),
            "completed_round_trips": trips,
            "round_trip_volume": self.round_trip_volume,
            "total_volume": self.total_volume,
            "round_trip_velocity": round_trip_velocity(
                trips, simulation_time,
            ),
            "round_trip_conversion": round_trip_conversion(
                self.round_trip_volume, self.total_volume,
            ),
            "new_active_books": int(self.new_active_books),
            "coverage_velocity": coverage_velocity(
                self.new_active_books, simulation_time,
            ),
            "new_qualified_books": int(self.new_qualified_books),
            "kappa_qualification_velocity": kappa_qualification_velocity(
                self.new_qualified_books, simulation_time,
            ),
            "inventory_realization_time_median": percentile(self.realization_times, 0.50),
            "inventory_realization_time_p90": percentile(self.realization_times, 0.90),
            "inventory_age_median": percentile(ages, 0.50),
            "inventory_age_p90": percentile(ages, 0.90),
            "maker_exit_count": int(self.maker.count),
            "maker_exit_pnl": self.maker.pnl,
            "competitive_maker_count": int(self.competitive_maker.count),
            "competitive_maker_pnl": self.competitive_maker.pnl,
            "aggressive_maker_count": int(self.aggressive_maker.count),
            "aggressive_maker_pnl": self.aggressive_maker.pnl,
            "taker_exit_count": int(self.taker.count),
            "taker_exit_pnl": self.taker.pnl,
        }
