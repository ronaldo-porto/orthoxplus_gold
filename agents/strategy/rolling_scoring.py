# SPDX-FileCopyrightText: 2026
# SPDX-License-Identifier: MIT
"""
Lightweight rolling scoring diagnostics for SN79 miner agents.

Mirrors practical July 2026 validator rules without network calls:
  trading_score ≈ 0.79 * Kappa + 0.21 * realized PnL
  Kappa lookback and PnL lookback are independent.
  Soft floor uses an internal median stand-in when network median is unavailable.

All math is pure Python / bounded loops over the agent's existing
realized_pnl_history. Safe to call once per respond() tick.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class RollingScoringConfig:
    """Independent Kappa / PnL rolling windows (do not assume equality)."""

    kappa_lookback_ns: int = 10_800_000_000_000  # 3 sim hours
    pnl_lookback_ns: int = 10_800_000_000_000  # 3 sim hours (independent)
    kappa_min_history_ns: int = 5_400_000_000_000  # 1.5 sim hours
    min_observations: int = 3
    kappa_weight: float = 0.79
    pnl_weight: float = 0.21
    floor_percentile: float = 50.0
    floor_softness: float = 0.5
    score_floor_guard_ratio: float = 1.05
    weak_book_score_quantile: float = 0.35
    left_tail_score_quantile: float = 0.20
    expiry_horizon_frac: float = 0.15
    pnl_scale: float = 0.02
    kappa_norm_min: float = -2.5
    kappa_norm_max: float = 2.5
    kappa_tau: float = 0.0


@dataclass
class BookRollingDiagnostics:
    book_id: int
    rolling_pnl: float = 0.0
    rolling_pnl_obs: int = 0
    kappa_obs: int = 0
    kappa_eligible: bool = False
    kappa_proxy: float = 0.0
    pnl_proxy: float = 0.0
    trading_score: float = 0.0
    oldest_positive_ts: int | None = None
    newest_positive_ts: int | None = None
    near_expiry: bool = False
    is_strong: bool = False


@dataclass
class RollingDiagnosticsSnapshot:
    latest_pnl_ts: int = 0
    books: dict[int, BookRollingDiagnostics] = field(default_factory=dict)
    rolling_kappa_proxy: float = 0.0
    rolling_pnl_proxy: float = 0.0
    trading_score_proxy: float = 0.0
    soft_floor_factor: float = 1.0
    soft_floor_score_proxy: float = 0.0
    floor_threshold: float = 0.0
    score_to_internal_median: float = 0.0  # ratio; 0 if no median yet
    score_to_internal_median_delta: float = 0.0
    eligible_books: set[int] = field(default_factory=set)
    weak_books: set[int] = field(default_factory=set)
    left_tail_books: set[int] = field(default_factory=set)
    expiring_strong_books: set[int] = field(default_factory=set)
    below_guard: bool = False
    book_scores: dict[int, float] = field(default_factory=dict)

    def to_stats(self) -> dict:
        return {
            "rolling_kappa_proxy": round(self.rolling_kappa_proxy, 6),
            "rolling_pnl_proxy": round(self.rolling_pnl_proxy, 6),
            "trading_score_proxy": round(self.trading_score_proxy, 6),
            "soft_floor_score_proxy": round(self.soft_floor_score_proxy, 6),
            "score_to_internal_median": round(self.score_to_internal_median, 6),
            "score_to_internal_median_delta": round(
                self.score_to_internal_median_delta, 6
            ),
            "floor_threshold": round(self.floor_threshold, 6),
            "eligible_books": len(self.eligible_books),
            "weak_books": len(self.weak_books),
            "left_tail_books": len(self.left_tail_books),
            "expiring_strong_books": len(self.expiring_strong_books),
            "below_guard": bool(self.below_guard),
            "eligible_book_ids": sorted(self.eligible_books)[:24],
            "weak_book_ids": sorted(self.weak_books)[:24],
            "left_tail_book_ids": sorted(self.left_tail_books)[:24],
            "expiring_strong_book_ids": sorted(self.expiring_strong_books)[:24],
        }


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = _clip(pct, 0.0, 100.0) / 100.0 * (len(ordered) - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return ordered[lo]
    w = rank - lo
    return ordered[lo] * (1.0 - w) + ordered[hi] * w


def soft_floor_factor(
    score: float,
    threshold: float,
    softness: float = 0.5,
) -> float:
    """Mirror rewarding.floor: linear taper from thr*(1-softness) to thr."""
    if threshold <= 0.0:
        return 1.0
    soft = _clip(softness, 1e-6, 1.0)
    lower = threshold * (1.0 - soft)
    if threshold <= lower:
        return 1.0 if score >= threshold else 0.0
    return _clip((score - lower) / (threshold - lower), 0.0, 1.0)


def _slice_window(
    history: dict[int, dict[int, float]],
    latest_ts: int,
    lookback_ns: int,
) -> dict[int, dict[int, float]]:
    if not history or lookback_ns <= 0:
        return {}
    cutoff = latest_ts - lookback_ns
    return {ts: books for ts, books in history.items() if ts >= cutoff}


def _book_series(
    window: dict[int, dict[int, float]],
    book_id: int,
) -> list[tuple[int, float]]:
    return [
        (ts, float(window[ts].get(book_id, 0.0)))
        for ts in sorted(window.keys())
    ]


def _normalize_pnl(pnl: float, scale: float) -> float:
    s = max(scale, 1e-9)
    return _clip(0.5 + 0.5 * (pnl / s), 0.0, 1.0)


def _book_kappa_raw(values: list[float], tau: float) -> float | None:
    """Lightweight per-book Kappa-3 style proxy (no numpy)."""
    n = len(values)
    if n == 0:
        return None
    # MAD scale for local scale-invariance.
    ordered = sorted(values)
    mid = ordered[n // 2] if n % 2 == 1 else 0.5 * (ordered[n // 2 - 1] + ordered[n // 2])
    abs_dev = sorted(abs(v - mid) for v in values)
    mad = abs_dev[n // 2] if n % 2 == 1 else 0.5 * (abs_dev[n // 2 - 1] + abs_dev[n // 2])
    mad = max(mad, 1e-6)
    returns = [v / mad for v in values]
    mean_r = sum(returns) / n
    lpm3 = sum(max(tau - r, 0.0) ** 3 for r in returns) / n
    upm3 = sum(max(r - tau, 0.0) ** 3 for r in returns) / n
    if lpm3 > 1e-18:
        return (mean_r - tau) / (lpm3 ** (1.0 / 3.0))
    if upm3 > 1e-18:
        return (mean_r - tau) / (upm3 ** (1.0 / 3.0))
    return mean_r - tau


def _normalize_kappa(raw: float | None, lo: float, hi: float) -> float:
    if raw is None:
        return 0.0
    span = max(hi - lo, 1e-12)
    return _clip((raw - lo) / span, 0.0, 1.0)


def compute_rolling_diagnostics(
    realized_pnl_history: dict[int, dict[int, float]],
    book_ids: Iterable[int],
    config: RollingScoringConfig,
    latest_ts_hint: int | None = None,
) -> RollingDiagnosticsSnapshot:
    """
    Build rolling Kappa / PnL proxies with independent lookbacks.

    Kappa window:  latest_pnl_ts - kappa_lookback_ns
    PnL window:    latest_pnl_ts - pnl_lookback_ns
    """
    snap = RollingDiagnosticsSnapshot()
    if not realized_pnl_history:
        return snap

    latest_ts = max(realized_pnl_history.keys())
    if latest_ts_hint is not None and latest_ts_hint > latest_ts:
        # Prefer history-relative latest; hint only fills when history empty.
        pass
    snap.latest_pnl_ts = int(latest_ts)

    kappa_window = _slice_window(
        realized_pnl_history, latest_ts, config.kappa_lookback_ns
    )
    pnl_window = _slice_window(
        realized_pnl_history, latest_ts, config.pnl_lookback_ns
    )
    if not kappa_window and not pnl_window:
        return snap

    kappa_timestamps = sorted(kappa_window.keys())
    kappa_span_ok = False
    if kappa_timestamps:
        kappa_span_ok = (
            kappa_timestamps[-1] - kappa_timestamps[0]
        ) >= config.kappa_min_history_ns

    kappa_cutoff = latest_ts - config.kappa_lookback_ns
    expiry_edge = kappa_cutoff + int(
        max(0.0, min(1.0, config.expiry_horizon_frac)) * config.kappa_lookback_ns
    )

    ids = list(dict.fromkeys(int(b) for b in book_ids))
    if not ids and kappa_window:
        discovered: set[int] = set()
        for books in kappa_window.values():
            discovered.update(books.keys())
        for books in pnl_window.values():
            discovered.update(books.keys())
        ids = sorted(discovered)

    weight_sum = max(config.kappa_weight + config.pnl_weight, 1e-12)
    k_w = config.kappa_weight / weight_sum
    p_w = config.pnl_weight / weight_sum

    kappa_proxies: list[float] = []
    pnl_proxies: list[float] = []

    for book_id in ids:
        diag = BookRollingDiagnostics(book_id=book_id)

        # Independent PnL lookback sum / observation count.
        for ts, pnl in _book_series(pnl_window, book_id):
            if pnl == 0.0:
                continue
            diag.rolling_pnl += pnl
            diag.rolling_pnl_obs += 1
            if pnl > 0.0:
                if diag.oldest_positive_ts is None:
                    diag.oldest_positive_ts = ts
                diag.newest_positive_ts = ts

        # Kappa window series (include zeros at timestamps for interval shape).
        kappa_series = _book_series(kappa_window, book_id)
        kappa_values = [pnl for _, pnl in kappa_series]
        diag.kappa_obs = sum(1 for v in kappa_values if v != 0.0)
        diag.kappa_eligible = bool(
            kappa_span_ok and diag.kappa_obs >= config.min_observations
        )

        raw_kappa = (
            _book_kappa_raw(kappa_values, config.kappa_tau)
            if diag.kappa_eligible
            else None
        )
        diag.kappa_proxy = _normalize_kappa(
            raw_kappa, config.kappa_norm_min, config.kappa_norm_max
        )
        diag.pnl_proxy = _normalize_pnl(diag.rolling_pnl, config.pnl_scale)
        diag.trading_score = k_w * diag.kappa_proxy + p_w * diag.pnl_proxy

        # Near-expiry: oldest still-in-window positive obs is close to cutoff.
        if (
            diag.oldest_positive_ts is not None
            and diag.rolling_pnl > 0.0
            and diag.oldest_positive_ts <= expiry_edge
        ):
            diag.near_expiry = True

        diag.is_strong = (
            diag.kappa_eligible
            and diag.rolling_pnl > 0.0
            and diag.trading_score > 0.0
            and diag.kappa_proxy >= 0.45
        )

        snap.books[book_id] = diag
        snap.book_scores[book_id] = diag.trading_score
        if diag.kappa_eligible:
            snap.eligible_books.add(book_id)
            kappa_proxies.append(diag.kappa_proxy)
        if diag.rolling_pnl_obs > 0 or diag.kappa_obs > 0:
            pnl_proxies.append(diag.pnl_proxy)
        if diag.is_strong and diag.near_expiry:
            snap.expiring_strong_books.add(book_id)

    snap.rolling_kappa_proxy = (
        percentile(kappa_proxies, 50.0) if kappa_proxies else 0.0
    )
    snap.rolling_pnl_proxy = (
        percentile(pnl_proxies, 50.0) if pnl_proxies else 0.0
    )
    snap.trading_score_proxy = (
        k_w * snap.rolling_kappa_proxy + p_w * snap.rolling_pnl_proxy
    )

    positive_scores = [s for s in snap.book_scores.values() if s > 0.0]
    snap.floor_threshold = (
        percentile(positive_scores, config.floor_percentile)
        if len(positive_scores) >= 2
        else (positive_scores[0] if len(positive_scores) == 1 else 0.0)
    )
    snap.soft_floor_factor = soft_floor_factor(
        snap.trading_score_proxy, snap.floor_threshold, config.floor_softness
    )
    snap.soft_floor_score_proxy = (
        snap.trading_score_proxy * snap.soft_floor_factor
    )
    if snap.floor_threshold > 0.0:
        snap.score_to_internal_median = (
            snap.trading_score_proxy / snap.floor_threshold
        )
        snap.score_to_internal_median_delta = (
            snap.trading_score_proxy - snap.floor_threshold
        )
    snap.below_guard = (
        snap.floor_threshold > 0.0
        and snap.score_to_internal_median < config.score_floor_guard_ratio
    )

    # Weak / left-tail from per-book trading scores.
    if snap.book_scores:
        values = list(snap.book_scores.values())
        weak_cut = percentile(values, config.weak_book_score_quantile * 100.0)
        left_cut = percentile(values, config.left_tail_score_quantile * 100.0)
        internal_median = percentile(values, 50.0)
        if snap.floor_threshold > 0.0:
            left_cut = min(
                left_cut, snap.floor_threshold * (1.0 - config.floor_softness)
            )
        for book_id, score in snap.book_scores.items():
            diag = snap.books[book_id]
            if score <= weak_cut or (
                internal_median > 0.0 and score < internal_median
            ):
                snap.weak_books.add(book_id)
            negative = diag.rolling_pnl < 0.0
            far_left = (
                score <= left_cut
                and internal_median > 0.0
                and score < 0.85 * internal_median
            )
            if negative or far_left:
                snap.left_tail_books.add(book_id)

    return snap
