# SPDX-License-Identifier: MIT
"""v5.0.0: the validator's trading score, computed from the miner's own history.

A pure-Python replica of ``taos/im/utils/kappa.py`` ``kappa_3`` (per book) and of
``taos/im/validator/reward.py`` ``calculate_kappa_score``, ``calculate_pnl_score`` and
``score_uid``, with the scoring defaults from ``taos/im/config/__init__.py``.  Validators
may run other values; every one is a parameter here.  Telemetry only: nothing in the
strategy reads it.

Three properties of that score decide what a productive book is for this agent:

  * Books are combined by their MEDIAN, not summed.  On the A1.9.9.1 run (log
    20260914_141324, ticks 1-8,000) the top 3 books made 87% of realized PnL and 46 of
    the 101 traded books were positive, so the total said little about the score.
  * A book has a Kappa-3 only with 3 realized observations inside the 3 h window.  Up to
    int(0.375 x book_count) books without one are ignored; each one past that counts as 0.
  * Kappa-3 is taken over every scoring round in the window, traded or not: the validator
    writes an empty bucket for each round (``trade.py``).  The agent's own
    ``realized_pnl_history`` holds only rounds that realized PnL, so a Kappa-3 computed on
    it is on a different scale.  The mirror counts every round the agent was sent.

Realized PnL itself matches: both sides match fills FIFO, fee-adjusted, and the validator
stores each round's value rounded to the volume decimals.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

V500_SCORE_MIRROR_VERSION = "direct_score_mirror_v5_0_0"

# taos/im/config/__init__.py, scoring.* defaults.
VALIDATOR_SCORING_DEFAULTS: dict[str, float] = {
    "kappa_weight": 0.79,
    "pnl_weight": 0.21,
    "kappa_lookback_ns": 10_800_000_000_000,
    "kappa_min_lookback_ns": 5_400_000_000_000,
    "kappa_tau": 0.0,
    "kappa_min_realized_observations": 3,
    "kappa_normalization_min": -2.5,
    "kappa_normalization_max": 2.5,
    "pnl_lookback_ns": 10_800_000_000_000,
    "pnl_min_daily_return": -1.0,
    "pnl_max_daily_return": 1.0,
    "max_inactive_books_ratio": 0.375,
}
DAILY_NS = 86_400_000_000_000
MAD_FLOOR = 1e-6

STATUS_SCORED = "SCORED"
STATUS_IGNORED = "NO_KAPPA_IGNORED"
STATUS_ZERO = "NO_KAPPA_COUNTED_ZERO"


# ---- numpy semantics, without numpy ------------------------------------------------------------

def _median_sorted(values: list[float]) -> float:
    n = len(values)
    if n == 0:
        return float("nan")
    mid = n // 2
    return values[mid] if n % 2 else 0.5 * (values[mid - 1] + values[mid])


def _percentile_sorted(values: list[float], q: float) -> float:
    """numpy.percentile, default linear method."""
    n = len(values)
    pos = (n - 1) * (q / 100.0)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def _kth_with_zeros(neg: list[float], zeros: int, pos: list[float], k: int) -> float:
    if k < len(neg):
        return neg[k]
    k -= len(neg)
    if k < zeros:
        return 0.0
    return pos[k - zeros]


def _median_with_zeros(nonzero: Iterable[float], zeros: int) -> float:
    """Median of ``nonzero`` plus ``zeros`` implicit 0.0 entries."""
    values = list(nonzero)
    neg = sorted(v for v in values if v < 0.0)
    pos = sorted(v for v in values if v >= 0.0)
    n = len(neg) + zeros + len(pos)
    if n == 0:
        return float("nan")
    if n % 2:
        return _kth_with_zeros(neg, zeros, pos, n // 2)
    return 0.5 * (_kth_with_zeros(neg, zeros, pos, n // 2 - 1) + _kth_with_zeros(neg, zeros, pos, n // 2))


def _cbrt(x: float) -> float:
    return math.copysign(abs(x) ** (1.0 / 3.0), x)


# ---- kappa_3, per book -------------------------------------------------------------------------

def kappa3_book(
    all_values: list[float],
    n_columns: int,
    kept_values: list[float],
    n_kept: int,
    *,
    tau: float = 0.0,
    min_observations: int = 3,
) -> float | None:
    """One row of kappa_3's matrix: MAD over every column, statistics over kept columns."""
    if n_columns <= 0 or n_kept <= 0:
        return None
    zeros_all = n_columns - len(all_values)
    med = _median_with_zeros(all_values, zeros_all)
    if med == 0.0:
        mad = _median_with_zeros([abs(v) for v in all_values], zeros_all)
    else:
        mad = _median_sorted(sorted([abs(v - med) for v in all_values] + [abs(med)] * zeros_all))
    mad = max(mad, MAD_FLOOR)
    returns = [v / mad for v in kept_values]
    if sum(1 for r in returns if r != 0.0) < int(min_observations):
        return None
    zeros = n_kept - len(returns)
    mean = sum(returns) / n_kept
    var = sum(r * r for r in returns) / n_kept - mean * mean
    std = math.sqrt(max(var, 0.0))
    downside = sum(max(tau - r, 0.0) ** 3 for r in returns) + zeros * max(tau, 0.0) ** 3
    lpm3 = downside / n_kept
    regularization = (0.1 * (abs(mean) + std)) ** 3
    return (mean - tau) / _cbrt(lpm3 + regularization)


@dataclass(frozen=True)
class KappaWindow:
    books: dict[int, float | None]
    observations: dict[int, int]
    oldest_observation_ts: dict[int, int]
    n_rounds: int
    n_kept: int
    span_ns: int
    newest_ts: int


def kappa3_books(
    history: Mapping[Any, Mapping[Any, Any]],
    rounds: Iterable[int],
    *,
    book_count: int,
    lookback_ns: int,
    min_lookback_ns: int,
    min_observations: int,
    tau: float,
    grace_period_ns: int,
    volume_decimals: int | None = None,
) -> KappaWindow | None:
    """kappa_3 over the rounds the validator would hold: every round sent, traded or not."""
    keys: dict[int, None] = {}
    for ts in rounds:
        keys[int(ts)] = None
    for ts in history:
        keys[int(ts)] = None
    if not keys:
        return None
    timestamps = sorted(keys)
    if lookback_ns and lookback_ns > 0:
        cutoff = timestamps[-1] - int(lookback_ns)
        if timestamps[0] < cutoff:
            timestamps = [ts for ts in timestamps if ts >= cutoff]
    span = timestamps[-1] - timestamps[0]
    if span < int(min_lookback_ns):
        return None
    dropped: set[int] = set()
    if grace_period_ns and grace_period_ns > 0:
        for prev, cur in zip(timestamps, timestamps[1:]):
            if cur - prev >= grace_period_ns:
                dropped.add(cur)
    window_start = timestamps[0]
    all_values: dict[int, list[float]] = {}
    kept_values: dict[int, list[float]] = {}
    oldest: dict[int, int] = {}
    for raw_ts, books in history.items():
        ts = int(raw_ts)
        if ts < window_start or ts > timestamps[-1]:
            continue
        for raw_book, raw_pnl in (books or {}).items():
            try:
                book = int(raw_book)
                pnl = float(raw_pnl)
            except (TypeError, ValueError):
                continue
            if volume_decimals is not None:
                pnl = round(pnl, int(volume_decimals))
            if not 0 <= book < int(book_count) or pnl == 0.0:
                continue
            all_values.setdefault(book, []).append(pnl)
            if ts not in dropped:
                kept_values.setdefault(book, []).append(pnl)
                oldest[book] = min(oldest.get(book, ts), ts)
    n_columns = len(timestamps)
    n_kept = n_columns - len(dropped)
    books: dict[int, float | None] = {}
    observations: dict[int, int] = {}
    for book in range(int(book_count)):
        kept = kept_values.get(book, [])
        observations[book] = len(kept)
        books[book] = (
            kappa3_book(all_values.get(book, []), n_columns, kept, n_kept, tau=tau,
                        min_observations=min_observations)
            if kept else None
        )
    return KappaWindow(books=books, observations=observations, oldest_observation_ts=oldest,
                       n_rounds=n_columns, n_kept=n_kept, span_ns=span, newest_ts=timestamps[-1])


# ---- reward.py composition --------------------------------------------------------------------

def outlier_penalty(data: list[float]) -> float:
    """reward.py _outlier_penalty: the 1.5 x IQR left tail."""
    if not data:
        return 0.0
    ordered = sorted(data)
    q1 = _percentile_sorted(ordered, 25.0)
    q3 = _percentile_sorted(ordered, 75.0)
    iqr = q3 - q1
    lower = q1 - 1.5 * max(iqr, 0.01)
    outliers = sorted(x for x in ordered if x < lower)
    if outliers and _median_sorted(outliers) < 0.5:
        return ((0.5 - _median_sorted(outliers)) / 1.5) * (1.0 - math.exp(-5.0 * iqr))
    return 0.0


def _kappa_aggregate(weighted: Mapping[int, float | None], max_inactive: int) -> tuple[float, float, float, int, list[int]]:
    scores = [w for w in weighted.values() if w is not None]
    no_kappa = [book for book, w in weighted.items() if w is None]
    if len(no_kappa) <= max_inactive:
        data = list(scores)
        zeroed: list[int] = []
    else:
        data = list(scores) + [0.0] * (len(no_kappa) - max_inactive)
        zeroed = no_kappa[max_inactive:]
    if not data:
        return 0.0, 0.0, float("nan"), 0, zeroed
    penalty = abs(outlier_penalty(data))
    median = _median_sorted(sorted(data))
    return max(median - penalty, 0.0), penalty, median, len(data), zeroed


def normalized_kappa(kappa: float | None, norm_min: float, norm_max: float) -> float | None:
    if kappa is None:
        return None
    span = norm_max - norm_min
    inv = 1.0 / span if span != 0 else 0.0
    return max(0.0, min(1.0, (kappa - norm_min) * inv))


def weighted_kappa(norm: float | None, combined_factor: float = 1.0) -> float | None:
    if norm is None:
        return None
    if combined_factor < 1 or norm > 0.5:
        weighted = combined_factor * norm
    else:
        weighted = (2 - combined_factor) * norm
    return min(weighted, 1)


def pnl_score(
    book_pnl: Mapping[int, float],
    *,
    book_count: int,
    miner_wealth: float,
    lookback_ns: int,
    max_inactive_ratio: float,
    min_daily: float = -1.0,
    max_daily: float = 1.0,
) -> tuple[float, dict[int, float]]:
    """calculate_pnl_score: median per-book daily return over scored books, halved."""
    if not book_pnl:
        return 0.0, {}
    reference = float(miner_wealth) * (float(lookback_ns) / DAILY_NS)
    if reference == 0:
        return 0.0, {}
    returns: dict[int, float] = {}
    inactive = 0
    for book in range(int(book_count)):
        pnl = float(book_pnl.get(book, 0.0))
        if pnl == 0.0:
            inactive += 1
            continue
        returns[book] = max(min_daily, min(max_daily, pnl / reference))
    max_inactive = int(max_inactive_ratio * book_count)
    data = list(returns.values())
    if inactive > max_inactive:
        data += [0.0] * (inactive - max_inactive)
    if not data:
        return 0.0, returns
    return _median_sorted(sorted(data)) / 2.0, returns


@dataclass(frozen=True)
class ScoreMirror:
    trading_score: float
    kappa_score: float
    pnl_score: float
    kappa_available: bool
    kappa_median: float
    kappa_penalty: float
    scored_books: int
    no_kappa_books: int
    zeroed_books: int
    max_inactive_books: int
    n_rounds: int
    span_ns: int
    books: dict[int, dict[str, Any]]
    kappa_score_all_active: float | None = None
    activated_scored: int | None = None

    def as_log(self) -> dict[str, Any]:
        row = {
            "trading_score": round(self.trading_score, 6),
            "kappa_score": round(self.kappa_score, 6),
            "pnl_score": round(self.pnl_score, 6),
            "kappa_available": int(self.kappa_available),
            "kappa_median": None if math.isnan(self.kappa_median) else round(self.kappa_median, 6),
            "kappa_penalty": round(self.kappa_penalty, 6),
            "scored_books": self.scored_books,
            "no_kappa_books": self.no_kappa_books,
            "zeroed_books": self.zeroed_books,
            "max_inactive_books": self.max_inactive_books,
            "n_rounds": self.n_rounds,
            "span_s": round(self.span_ns / 1e9, 1),
        }
        if self.activated_scored is not None:
            # v5.0.1: scored with the activity factor.  The v5.0.0 number, every factor 1.0, is kept
            # beside it: it is the score this history earns once every scored book is activated.
            row["activity_weighted"] = 1
            row["activated_scored"] = self.activated_scored
            row["cold_scored"] = self.scored_books - self.activated_scored
            row["cliff_needed"] = max(0, self.scored_books // 2 + 1 - self.activated_scored)
            row["kappa_score_all_active"] = (
                None if self.kappa_score_all_active is None else round(self.kappa_score_all_active, 6)
            )
        return row


def mirror_score(
    history: Mapping[Any, Mapping[Any, Any]],
    rounds: Iterable[int],
    *,
    now_ts: int,
    book_count: int,
    miner_wealth: float,
    grace_period_ns: int = 0,
    volume_decimals: int | None = None,
    params: Mapping[str, float] | None = None,
    marginal: bool = True,
    activity_factors: Mapping[int, float] | None = None,
) -> ScoreMirror:
    p = dict(VALIDATOR_SCORING_DEFAULTS)
    p.update(params or {})
    book_count = int(book_count)
    max_inactive = int(p["max_inactive_books_ratio"] * book_count)
    rounds = list(rounds)
    window = kappa3_books(
        history, rounds, book_count=book_count, lookback_ns=int(p["kappa_lookback_ns"]),
        min_lookback_ns=int(p["kappa_min_lookback_ns"]),
        min_observations=int(p["kappa_min_realized_observations"]), tau=float(p["kappa_tau"]),
        grace_period_ns=int(grace_period_ns), volume_decimals=volume_decimals,
    )
    books: dict[int, dict[str, Any]] = {}
    kappa_score_value = 0.0
    penalty = 0.0
    median = float("nan")
    scored = no_kappa = zeroed_n = 0
    all_active: float | None = None
    activated_scored: int | None = None
    if window is not None:
        weighted: dict[int, float | None] = {}
        for book in range(book_count):
            norm = normalized_kappa(window.books.get(book), p["kappa_normalization_min"], p["kappa_normalization_max"])
            # v5.0.1: the validator's activity factor, when the caller knows it.  None keeps
            # v5.0.0's assumption of 1.0 for every book.
            factor = 1.0 if activity_factors is None else float(activity_factors.get(book, 0.0))
            weighted[book] = weighted_kappa(norm, factor)
            books[book] = {
                "obs": window.observations.get(book, 0),
                "kappa": window.books.get(book),
                "norm": norm,
                "oldest_ts": window.oldest_observation_ts.get(book),
                "activity": factor,
            }
        kappa_score_value, penalty, median, _, zeroed = _kappa_aggregate(weighted, max_inactive)
        if activity_factors is not None:
            everyone = {b: weighted_kappa(row["norm"]) for b, row in books.items()}
            all_active = _kappa_aggregate(everyone, max_inactive)[0]
            activated_scored = sum(
                1 for b, w in weighted.items() if w is not None and books[b]["activity"] >= 1.0
            )
        zeroed_set = set(zeroed)
        for book, row in books.items():
            if weighted[book] is not None:
                row["status"] = STATUS_SCORED
                scored += 1
            elif book in zeroed_set:
                row["status"] = STATUS_ZERO
                no_kappa += 1
            else:
                row["status"] = STATUS_IGNORED
                no_kappa += 1
        zeroed_n = len(zeroed)
        if marginal:
            for book, w in weighted.items():
                if w is None:
                    continue
                without = dict(weighted)
                without[book] = None
                books[book]["marginal_kappa"] = kappa_score_value - _kappa_aggregate(without, max_inactive)[0]
    # PnL score: its own window, relative to the current round.
    threshold = int(now_ts) - int(p["pnl_lookback_ns"])
    book_pnl: dict[int, float] = {}
    for raw_ts, row in history.items():
        if int(raw_ts) < threshold:
            continue
        for raw_book, raw_pnl in (row or {}).items():
            try:
                book = int(raw_book)
                pnl = float(raw_pnl)
            except (TypeError, ValueError):
                continue
            if volume_decimals is not None:
                pnl = round(pnl, int(volume_decimals))
            if pnl != 0.0:
                book_pnl[book] = book_pnl.get(book, 0.0) + pnl
    pnl_value, daily = pnl_score(
        book_pnl, book_count=book_count, miner_wealth=miner_wealth, lookback_ns=int(p["pnl_lookback_ns"]),
        max_inactive_ratio=p["max_inactive_books_ratio"], min_daily=p["pnl_min_daily_return"],
        max_daily=p["pnl_max_daily_return"],
    )
    for book, pnl in book_pnl.items():
        if 0 <= book < book_count:
            books.setdefault(book, {})["pnl_window"] = pnl
            books[book]["daily_return"] = daily.get(book)
    trading = max(0.0, min(1.0, p["kappa_weight"] * kappa_score_value + p["pnl_weight"] * pnl_value))
    return ScoreMirror(
        trading_score=trading, kappa_score=kappa_score_value, pnl_score=pnl_value,
        kappa_available=window is not None, kappa_median=median, kappa_penalty=penalty,
        scored_books=scored, no_kappa_books=no_kappa, zeroed_books=zeroed_n,
        max_inactive_books=max_inactive, n_rounds=window.n_rounds if window else len(set(rounds)),
        span_ns=window.span_ns if window else 0, books=books,
        kappa_score_all_active=all_active, activated_scored=activated_scored,
    )
