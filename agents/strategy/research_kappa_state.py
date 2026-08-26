# SPDX-License-Identifier: MIT
"""Authoritative per-book Kappa observation state.

Scheduler, ScoreRegime, realization, telemetry, and the run summary
previously derived eligibility from different maps and formulas
(realized==1 vs remaining==1, ScoreEV-eligible vs Kappa-qualified).

Every consumer reads the same ``KappaBookState``:

    realized_observation_count
    required_observations
    observations_remaining
    eligible

``eligible`` means Kappa-qualified: realized >= required.
It is not ScoreEV ranking eligibility.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

KAPPA_STATE_VERSION = "kappa_state_v2_rolling"

CONSUMER_SCORE_REGIME = "ScoreRegime"
CONSUMER_COVERAGE = "coverage"
CONSUMER_COMPLETION = "completion"
CONSUMER_REALIZATION = "realization"
CONSUMER_TELEMETRY = "telemetry"
CONSUMER_SUMMARY = "summary"
KAPPA_CONSUMERS = (
    CONSUMER_SCORE_REGIME,
    CONSUMER_COVERAGE,
    CONSUMER_COMPLETION,
    CONSUMER_REALIZATION,
    CONSUMER_TELEMETRY,
    CONSUMER_SUMMARY,
)


def _count(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def kappa_progress(
    realized_observation_count: Any,
    required_observations: Any,
) -> tuple[int, int, int]:
    required = max(1, _count(required_observations, 1))
    realized = max(0, _count(realized_observation_count, 0))
    remaining = max(0, required - realized)
    return realized, required, remaining


def kappa_eligible(realized_observation_count: Any, required_observations: Any) -> bool:
    realized, required, remaining = kappa_progress(
        realized_observation_count, required_observations,
    )
    return remaining == 0 and realized >= required


@dataclass(frozen=True)
class KappaBookState:
    book: int
    realized_observation_count: int
    required_observations: int
    observations_remaining: int
    eligible: bool

    @property
    def one_away(self) -> bool:
        return self.observations_remaining == 1

    @property
    def two_away(self) -> bool:
        return self.observations_remaining == 2

    @property
    def uncovered(self) -> bool:
        return self.realized_observation_count <= 0

    def as_log(self) -> dict[str, Any]:
        return {
            "kappa_state_version": KAPPA_STATE_VERSION,
            "book": int(self.book),
            "realized_observation_count": self.realized_observation_count,
            "required_observations": self.required_observations,
            "observations_remaining": self.observations_remaining,
            "eligible": int(bool(self.eligible)),
        }


def kappa_book_state(
    book: int,
    realized_observation_count: Any,
    required_observations: Any,
) -> KappaBookState:
    realized, required, remaining = kappa_progress(
        realized_observation_count, required_observations,
    )
    return KappaBookState(
        book=int(book),
        realized_observation_count=realized,
        required_observations=required,
        observations_remaining=remaining,
        eligible=remaining == 0 and realized >= required,
    )


@dataclass(frozen=True)
class KappaUniverseState:
    required_observations: int
    books: tuple[KappaBookState, ...]

    def book(self, book_id: int) -> KappaBookState:
        bid = int(book_id)
        for row in self.books:
            if row.book == bid:
                return row
        return kappa_book_state(bid, 0, self.required_observations)

    @property
    def tracked_count(self) -> int:
        return len(self.books)

    @property
    def eligible_count(self) -> int:
        return sum(1 for row in self.books if row.eligible)

    @property
    def eligible_ids(self) -> tuple[int, ...]:
        return tuple(row.book for row in self.books if row.eligible)

    @property
    def zero_obs_count(self) -> int:
        return sum(1 for row in self.books if row.uncovered)

    @property
    def one_remaining_count(self) -> int:
        return sum(1 for row in self.books if row.one_away)

    @property
    def two_remaining_count(self) -> int:
        return sum(1 for row in self.books if row.two_away)

    @property
    def pending_count(self) -> int:
        return sum(
            1 for row in self.books
            if row.realized_observation_count > 0 and not row.eligible
        )

    def bucket_counts(self) -> dict[str, int]:
        return {
            "books_zero_obs": self.zero_obs_count,
            "books_0_obs": self.zero_obs_count,
            "books_one_remaining": self.one_remaining_count,
            "books_1_remaining": self.one_remaining_count,
            "books_two_remaining": self.two_remaining_count,
            "books_2_remaining": self.two_remaining_count,
            "eligible_books": self.eligible_count,
            "books_eligible": self.eligible_count,
            "tracked_books": self.tracked_count,
            "required_observation_count": int(self.required_observations),
            "pending_kappa": self.pending_count,
        }

    def as_log(self) -> dict[str, Any]:
        payload = {
            "kappa_state_version": KAPPA_STATE_VERSION,
            "required_observations": int(self.required_observations),
            "kappa_eligible": self.eligible_count,
            "kappa_pending_1": self.one_remaining_count,
            "kappa_pending_2": self.two_remaining_count,
            "kappa_zero_obs": self.zero_obs_count,
            "kappa_pending": self.pending_count,
            "kappa_tracked": self.tracked_count,
        }
        payload.update(self.bucket_counts())
        return payload


def build_kappa_universe(
    observation_counts: Mapping[Any, Any] | None,
    required_observations: Any,
    *,
    universe_ids: Iterable[Any] | None = None,
) -> KappaUniverseState:
    required = max(1, _count(required_observations, 1))
    counts: dict[int, int] = {}
    for key, value in dict(observation_counts or {}).items():
        try:
            bid = int(key)
        except (TypeError, ValueError):
            continue
        counts[bid] = max(0, _count(value, 0))
    if universe_ids is not None:
        for key in universe_ids:
            try:
                bid = int(key)
            except (TypeError, ValueError):
                continue
            counts.setdefault(bid, 0)
    books = tuple(
        kappa_book_state(book, realized, required)
        for book, realized in sorted(counts.items())
    )
    return KappaUniverseState(required_observations=required, books=books)


def score_regime_kappa(universe: KappaUniverseState) -> dict[str, int]:
    return {
        "eligible": universe.eligible_count,
        "pending_kappa": universe.pending_count,
        "books_1_remaining": universe.one_remaining_count,
        "books_2_remaining": universe.two_remaining_count,
        "books_0_obs": universe.zero_obs_count,
    }


def coverage_kappa(universe: KappaUniverseState) -> dict[str, int]:
    uncovered = sum(1 for row in universe.books if row.uncovered)
    return {
        "eligible": sum(1 for row in universe.books if row.eligible),
        "uncovered": uncovered,
    }


def completion_kappa(universe: KappaUniverseState) -> dict[str, int]:
    return {
        "eligible": sum(
            1 for row in universe.books
            if row.realized_observation_count >= row.required_observations
        ),
        "one_away": universe.one_remaining_count,
        "two_away": universe.two_remaining_count,
    }


def realization_kappa(universe: KappaUniverseState, book_id: int) -> dict[str, Any]:
    row = universe.book(book_id)
    return {
        "eligible": int(bool(row.eligible)),
        "realized_observation_count": row.realized_observation_count,
        "required_observations": row.required_observations,
        "observations_remaining": row.observations_remaining,
    }


def telemetry_kappa(row: KappaBookState) -> dict[str, Any]:
    return {
        "eligible": int(bool(row.eligible)),
        "realized_observation_count": row.realized_observation_count,
        "required_observations": row.required_observations,
        "observations_remaining": row.observations_remaining,
    }


def summary_kappa(universe: KappaUniverseState) -> dict[str, int]:
    """Run-summary Kappa fields. pending_1 is remaining==1, not realized==1."""
    return {
        "eligible": universe.eligible_count,
        "pending_1": universe.one_remaining_count,
        "pending_2": universe.two_remaining_count,
        "zero_obs": universe.zero_obs_count,
        "realized_total": sum(row.realized_observation_count for row in universe.books),
    }


def consumer_eligible_counts(universe: KappaUniverseState) -> dict[str, int]:
    return {
        CONSUMER_SCORE_REGIME: score_regime_kappa(universe)["eligible"],
        CONSUMER_COVERAGE: coverage_kappa(universe)["eligible"],
        CONSUMER_COMPLETION: completion_kappa(universe)["eligible"],
        CONSUMER_REALIZATION: sum(
            realization_kappa(universe, row.book)["eligible"] for row in universe.books
        ),
        CONSUMER_TELEMETRY: sum(
            telemetry_kappa(row)["eligible"] for row in universe.books
        ),
        CONSUMER_SUMMARY: summary_kappa(universe)["eligible"],
    }


@dataclass(frozen=True)
class KappaExpiryState:
    book: int
    recent_observation_count: int
    required_observations: int
    qualified: bool
    oldest_required_timestamp: int | None
    expires_at: int | None
    time_to_expiry_ns: int | None
    expiry_urgency: float

    def as_log(self) -> dict[str, Any]:
        return {
            "book": int(self.book),
            "recent_observation_count": int(self.recent_observation_count),
            "required_observations": int(self.required_observations),
            "qualified": int(bool(self.qualified)),
            "oldest_required_timestamp": self.oldest_required_timestamp,
            "expires_at": self.expires_at,
            "time_to_expiry_ns": self.time_to_expiry_ns,
            "expiry_urgency": float(self.expiry_urgency),
        }


def rolling_observation_timestamps(
    realized_pnl_history: Mapping[Any, Mapping[Any, Any]] | None,
    *,
    now: Any,
    lookback_ns: Any,
) -> dict[int, tuple[int, ...]]:
    """Validator-aligned non-zero realized-PnL observation timestamps.

    Kappa counts non-zero realized-PnL buckets inside the explicit rolling
    lookback. Lifetime/session round-trip counters must not be used for score
    qualification because old observations expire.
    """
    try:
        current = int(now)
    except (TypeError, ValueError):
        current = 0
    try:
        lookback = max(0, int(lookback_ns))
    except (TypeError, ValueError):
        lookback = 0
    cutoff = current - lookback if lookback > 0 else None
    rows: dict[int, list[int]] = {}
    for raw_ts, books in dict(realized_pnl_history or {}).items():
        try:
            ts = int(raw_ts)
        except (TypeError, ValueError):
            continue
        if cutoff is not None and ts < cutoff:
            continue
        if ts > current and current > 0:
            continue
        for raw_book, raw_pnl in dict(books or {}).items():
            try:
                pnl = float(raw_pnl)
                book = int(raw_book)
            except (TypeError, ValueError):
                continue
            if pnl == 0.0:
                continue
            rows.setdefault(book, []).append(ts)
    return {book: tuple(sorted(ts_list)) for book, ts_list in rows.items()}


def rolling_observation_counts(
    realized_pnl_history: Mapping[Any, Mapping[Any, Any]] | None,
    *,
    now: Any,
    lookback_ns: Any,
) -> dict[int, int]:
    return {
        int(book): len(timestamps)
        for book, timestamps in rolling_observation_timestamps(
            realized_pnl_history, now=now, lookback_ns=lookback_ns,
        ).items()
    }




def kappa_expiry_from_timestamps(
    book: Any,
    timestamps: Iterable[Any] | None,
    *,
    now: Any,
    lookback_ns: Any,
    required_observations: Any,
    warning_horizon_frac: float = 0.20,
) -> KappaExpiryState:
    bid = int(book)
    current = int(now or 0)
    lookback = max(1, int(lookback_ns or 1))
    required = max(1, _count(required_observations, 1))
    clean: list[int] = []
    cutoff = current - lookback
    for value in timestamps or ():
        try:
            ts = int(value)
        except (TypeError, ValueError):
            continue
        if cutoff <= ts <= current:
            clean.append(ts)
    clean.sort()
    count = len(clean)
    qualified = count >= required
    oldest = None
    expires_at = None
    time_to_expiry = None
    urgency = 0.0
    if clean:
        # V4.12.8: expose a deadline for incomplete progress as well as for
        # already-qualified books.  For qualified books the relevant timestamp
        # remains the oldest of the newest ``required`` observations (the one
        # whose expiry would drop qualification).  For incomplete books, any
        # retained observation is valuable progress, so the oldest observation
        # is the next progress point at risk of rolling out.
        retained = clean[-required:] if qualified else clean
        oldest = int(retained[0])
        expires_at = oldest + lookback
        time_to_expiry = max(0, expires_at - current)
        horizon = max(1.0, float(lookback) * max(0.01, min(0.95, float(warning_horizon_frac))))
        urgency = max(0.0, min(1.0, 1.0 - float(time_to_expiry) / horizon))
    return KappaExpiryState(
        book=bid,
        recent_observation_count=count,
        required_observations=required,
        qualified=qualified,
        oldest_required_timestamp=oldest,
        expires_at=expires_at,
        time_to_expiry_ns=time_to_expiry,
        expiry_urgency=urgency,
    )

def kappa_expiry_state(
    book: Any,
    realized_pnl_history: Mapping[Any, Mapping[Any, Any]] | None,
    *,
    now: Any,
    lookback_ns: Any,
    required_observations: Any,
    warning_horizon_frac: float = 0.20,
) -> KappaExpiryState:
    bid = int(book)
    current = int(now or 0)
    lookback = max(1, int(lookback_ns or 1))
    required = max(1, _count(required_observations, 1))
    timestamps = rolling_observation_timestamps(
        realized_pnl_history, now=current, lookback_ns=lookback,
    ).get(bid, ())
    count = len(timestamps)
    qualified = count >= required
    oldest = None
    expires_at = None
    time_to_expiry = None
    urgency = 0.0
    if qualified:
        # Only the most recent `required` observations are needed to stay qualified.
        retained = timestamps[-required:]
        oldest = int(retained[0])
        expires_at = oldest + lookback
        time_to_expiry = max(0, expires_at - current)
        horizon = max(1.0, float(lookback) * max(0.01, min(0.95, float(warning_horizon_frac))))
        urgency = max(0.0, min(1.0, 1.0 - float(time_to_expiry) / horizon))
    return KappaExpiryState(
        book=bid,
        recent_observation_count=count,
        required_observations=required,
        qualified=qualified,
        oldest_required_timestamp=oldest,
        expires_at=expires_at,
        time_to_expiry_ns=time_to_expiry,
        expiry_urgency=urgency,
    )
