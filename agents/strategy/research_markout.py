# SPDX-License-Identifier: MIT
"""Bounded timestamped mid-price history for delayed maker markout.

Pending fills are scored at 100 / 250 / 500 / 1000 ms using the nearest
valid future mid. Missing markout is a conservative adverse fallback,
never zero adverse selection.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from typing import Any, Mapping

MARKOUT_VERSION = "markout_history_v1"
MARKOUT_HORIZONS_MS = (100, 250, 500, 1000)
NS_PER_MS = 1_000_000
DEFAULT_MAX_SAMPLES = 256
DEFAULT_MAX_BOOKS = 512
DEFAULT_RETAIN_NS = 5_000_000_000
CONSERVATIVE_MARKOUT_FALLBACK_BPS = -2.0
MIN_MARKOUT_SAMPLES = 8


def ms_to_ns(ms: float | int) -> int:
    return int(float(ms) * NS_PER_MS)


def extract_book_mid(book: Any) -> float | None:
    bids = getattr(book, "bids", None) or []
    asks = getattr(book, "asks", None) or []
    if not bids or not asks:
        return None
    try:
        bid = float(getattr(bids[0], "price"))
        ask = float(getattr(asks[0], "price"))
    except (TypeError, ValueError, AttributeError):
        return None
    if bid <= 0.0 or ask <= 0.0 or ask < bid:
        return None
    mid = 0.5 * (bid + ask)
    return mid if mid > 0.0 else None


class MidHistory:
    """Per-book ring of (timestamp_ns, mid). Record is O(1) amortized."""

    def __init__(
        self,
        *,
        max_samples_per_book: int = DEFAULT_MAX_SAMPLES,
        max_books: int = DEFAULT_MAX_BOOKS,
        retain_ns: int = DEFAULT_RETAIN_NS,
    ) -> None:
        self.max_samples_per_book = max(8, int(max_samples_per_book))
        self.max_books = max(8, int(max_books))
        self.retain_ns = max(ms_to_ns(1000), int(retain_ns))
        self._books: OrderedDict[int, deque[tuple[int, float]]] = OrderedDict()

    def clear(self) -> None:
        self._books.clear()

    def record(self, book: int, timestamp_ns: int | float | None, mid: float | None) -> None:
        if timestamp_ns is None or mid is None:
            return
        try:
            ts = int(timestamp_ns)
            px = float(mid)
            bid = int(book)
        except (TypeError, ValueError):
            return
        if ts < 0 or px <= 0.0:
            return
        series = self._books.get(bid)
        if series is None:
            while len(self._books) >= self.max_books:
                self._books.popitem(last=False)
            series = deque()
            self._books[bid] = series
        else:
            self._books.move_to_end(bid)
        if series and series[-1][0] == ts:
            series[-1] = (ts, px)
        elif series and series[-1][0] > ts:
            series.clear()
            series.append((ts, px))
        else:
            series.append((ts, px))
        cutoff = ts - self.retain_ns
        while series and (
            series[0][0] < cutoff or len(series) > self.max_samples_per_book
        ):
            series.popleft()

    def record_books(
        self,
        books: Mapping[Any, Any] | None,
        timestamp_ns: int | float | None,
    ) -> int:
        """Record mids for a snapshot. Returns how many books were stored."""
        if not isinstance(books, Mapping) or timestamp_ns is None:
            return 0
        stored = 0
        for key, book in books.items():
            try:
                bid = int(key)
            except (TypeError, ValueError):
                continue
            mid = extract_book_mid(book)
            if mid is None:
                continue
            self.record(bid, timestamp_ns, mid)
            stored += 1
        return stored

    def nearest_future_mid(
        self,
        book: int,
        target_ts: int,
    ) -> tuple[int, float] | None:
        """First sample with timestamp >= target. None if history has no future mid."""
        try:
            bid = int(book)
            target = int(target_ts)
        except (TypeError, ValueError):
            return None
        series = self._books.get(bid)
        if not series:
            return None
        for ts, mid in series:
            if ts >= target:
                return ts, mid
        return None


def conservative_expected_markout_bps(
    *,
    mean_bps: float | None,
    samples: int,
    min_samples: int = MIN_MARKOUT_SAMPLES,
    fallback_bps: float = CONSERVATIVE_MARKOUT_FALLBACK_BPS,
    prior_strength: float = 8.0,
    clip_abs: float = 20.0,
) -> float:
    """Sparse / missing markout shrinks toward a slightly adverse prior.

    Zero is not the missing-data value: that would treat unknown
    selection as harmless.
    """
    n = max(0, int(samples))
    fb = float(fallback_bps)
    if mean_bps is None or n <= 0:
        return fb
    mean = max(-clip_abs, min(clip_abs, float(mean_bps)))
    if n >= max(1, int(min_samples)):
        return mean
    strength = max(0.0, float(prior_strength))
    blended = (n * mean + strength * fb) / (n + strength)
    return max(-clip_abs, min(clip_abs, blended))
