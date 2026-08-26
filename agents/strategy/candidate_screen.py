# SPDX-License-Identifier: MIT
"""Production cheap two-stage candidate screening and feature cache.

Standalone copy inlined into BaseStrategy. No Strategy1 / Research runtime
imports.

128 books → cheap screen → always keep inventory / Kappa-completion /
risk-critical books → fill remaining slots by cheap score → full
Strategy1 prediction on the selected set (default 20).

Unchanged top-of-book features may be reused. Callers keep a full-universe
predict fallback when the screen is off, empty, or raises.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_CANDIDATE_COUNT = 20
MIN_CANDIDATE_COUNT = 8
MAX_CANDIDATE_COUNT = 64
P95_TARGET_MS = 150.0
REQUIRED_TIMING_KEYS = (
    "screen_ms",
    "full_predict_ms",
    "ranking_ms",
    "build_orders_ms",
    "logging_ms",
    "total_response_ms",
)


@dataclass(frozen=True)
class ScreenBook:
    book_id: int
    has_inventory: bool = False
    is_dust: bool = False
    observations_remaining: int = 0
    is_hard_risk: bool = False
    has_live_quote: bool = False
    cheap_score: float = 0.0


@dataclass
class ScreenResult:
    selected: list[int]
    forced: list[int]
    forced_inventory: list[int]
    forced_dust: list[int]
    forced_kappa: list[int]
    forced_hard_risk: list[int]
    forced_live: list[int]
    screened_extra: list[int]
    candidate_count: int
    universe: int

    def as_log(self) -> dict[str, int]:
        return {
            "candidate_count": len(self.selected),
            "forced_inventory_count": len(self.forced_inventory),
            "forced_kappa_count": len(self.forced_kappa),
            "forced_dust_count": len(self.forced_dust),
            "forced_hard_risk_count": len(self.forced_hard_risk),
            "forced_live_count": len(self.forced_live),
            "screened_extra_count": len(self.screened_extra),
            "universe": self.universe,
        }


def clamp_candidate_count(
    value: Any,
    *,
    default: int = DEFAULT_CANDIDATE_COUNT,
    minimum: int = MIN_CANDIDATE_COUNT,
    maximum: int = MAX_CANDIDATE_COUNT,
) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = int(default)
    lo = max(1, int(minimum))
    hi = max(lo, int(maximum))
    return max(lo, min(hi, count))


def is_one_away_kappa(observations_remaining: int) -> bool:
    return int(observations_remaining) == 1


def is_forced(book: ScreenBook) -> bool:
    """Inventory, Kappa completion (1–2 remaining), and risk books always survive."""
    if book.has_inventory or book.is_dust or book.is_hard_risk or book.has_live_quote:
        return True
    remaining = int(book.observations_remaining)
    return remaining == 1 or remaining == 2


def book_touch_fingerprint(book: Any) -> tuple[Any, ...] | None:
    bids = getattr(book, "bids", None) or []
    asks = getattr(book, "asks", None) or []
    if not bids or not asks:
        return None
    try:
        bid = bids[0]
        ask = asks[0]
        return (
            float(getattr(bid, "price")),
            float(getattr(bid, "quantity", 0.0) or 0.0),
            float(getattr(ask, "price")),
            float(getattr(ask, "quantity", 0.0) or 0.0),
            int(len(getattr(book, "events", None) or [])),
            int(len(bids)),
            int(len(asks)),
        )
    except (TypeError, ValueError, AttributeError, IndexError):
        return None


def deep_book_fingerprint(book: Any, *, depth: int = 5) -> tuple[Any, ...] | None:
    bids = getattr(book, "bids", None) or []
    asks = getattr(book, "asks", None) or []
    if not bids or not asks:
        return None
    try:
        bid_lvls = tuple(
            (float(level.price), float(getattr(level, "quantity", 0.0) or 0.0))
            for level in list(bids)[: max(1, int(depth))]
        )
        ask_lvls = tuple(
            (float(level.price), float(getattr(level, "quantity", 0.0) or 0.0))
            for level in list(asks)[: max(1, int(depth))]
        )
    except (TypeError, ValueError, AttributeError):
        return None
    return (bid_lvls, ask_lvls)


@dataclass(frozen=True)
class CachedTouch:
    fingerprint: tuple[Any, ...]
    spread_bps: float | None
    imbalance: float | None
    trade_events: int


class FeatureCache:
    """Reuse unchanged top-of-book / L2–L5 features. Misses recompute."""

    def __init__(self) -> None:
        self._touch: dict[int, CachedTouch] = {}
        self._keep: dict[int, tuple[Any, ...]] = {}
        self._deep: dict[int, tuple[tuple[Any, ...], float]] = {}
        self.hits = 0
        self.misses = 0

    def lookup_touch(self, book_id: int, fingerprint: tuple[Any, ...] | None) -> CachedTouch | None:
        if fingerprint is None:
            self.misses += 1
            return None
        row = self._touch.get(int(book_id))
        if row is None or row.fingerprint != fingerprint:
            self.misses += 1
            return None
        self.hits += 1
        return row

    def store_touch(
        self,
        book_id: int,
        fingerprint: tuple[Any, ...] | None,
        *,
        spread_bps: float | None,
        imbalance: float | None,
        trade_events: int,
    ) -> None:
        if fingerprint is None:
            return
        self._touch[int(book_id)] = CachedTouch(
            fingerprint=fingerprint,
            spread_bps=spread_bps,
            imbalance=imbalance,
            trade_events=int(trade_events),
        )

    def keep_unchanged(self, book_id: int, fingerprint: tuple[Any, ...] | None) -> bool:
        """True when this book already received keep-state for the same touch."""
        if fingerprint is None:
            return False
        prev = self._keep.get(int(book_id))
        self._keep[int(book_id)] = fingerprint
        return prev == fingerprint

    def lookup_deep(self, book_id: int, fingerprint: tuple[Any, ...] | None) -> float | None:
        if fingerprint is None:
            return None
        row = self._deep.get(int(book_id))
        if row is None or row[0] != fingerprint:
            return None
        self.hits += 1
        return float(row[1])

    def store_deep(
        self,
        book_id: int,
        fingerprint: tuple[Any, ...] | None,
        value: float,
    ) -> None:
        if fingerprint is None:
            return
        self._deep[int(book_id)] = (fingerprint, float(value))


def timing_payload(raw: dict[str, Any] | None = None) -> dict[str, float]:
    src = raw or {}
    return {
        "screen_ms": float(src.get("screen_ms", src.get("screen_all_books_ms", 0.0)) or 0.0),
        "full_predict_ms": float(src.get("full_predict_ms", 0.0) or 0.0),
        "ranking_ms": float(src.get("ranking_ms", src.get("selection_ms", 0.0)) or 0.0),
        "build_orders_ms": float(src.get("build_orders_ms", 0.0) or 0.0),
        "logging_ms": float(src.get("logging_ms", 0.0) or 0.0),
        "total_response_ms": float(src.get("total_response_ms", 0.0) or 0.0),
    }


def cheap_book_score(
    *,
    spread_bps: float | None = None,
    trade_events: int = 0,
    top_imbalance: float | None = None,
    fill_rate: float = 0.0,
    last_alpha: float = 0.0,
    specialization: float = 0.0,
) -> float:
    """Inexpensive ranking from already-available top-of-book / memory."""
    spread_term = 0.0
    if spread_bps is not None and spread_bps > 0.0:
        spread_term = max(0.0, 1.0 - min(float(spread_bps), 20.0) / 20.0)
    trade_term = min(1.0, max(0, int(trade_events)) / 8.0)
    imb_term = 0.0 if top_imbalance is None else min(1.0, abs(float(top_imbalance)))
    return (
        0.28 * max(0.0, min(1.0, float(fill_rate)))
        + 0.18 * max(0.0, min(1.0, float(specialization)))
        + 0.18 * max(0.0, min(1.0, float(last_alpha)))
        + 0.16 * trade_term
        + 0.12 * spread_term
        + 0.08 * imb_term
    )


def select_fast_candidates(
    books: list[ScreenBook],
    candidate_count: int,
) -> ScreenResult:
    """Forced books always survive. Extra slots fill by cheap_score descending."""
    cap = max(1, int(candidate_count))
    forced_inv: list[int] = []
    forced_dust: list[int] = []
    forced_kappa: list[int] = []
    forced_hard: list[int] = []
    forced_live: list[int] = []
    forced: list[ScreenBook] = []
    rest: list[ScreenBook] = []
    seen: set[int] = set()
    for book in books:
        bid = int(book.book_id)
        if bid in seen:
            continue
        seen.add(bid)
        if is_forced(book):
            forced.append(book)
            if book.has_inventory:
                forced_inv.append(bid)
            if book.is_dust:
                forced_dust.append(bid)
            if 0 < int(book.observations_remaining) <= 2:
                forced_kappa.append(bid)
            if book.is_hard_risk:
                forced_hard.append(bid)
            if book.has_live_quote:
                forced_live.append(bid)
        else:
            rest.append(book)
    forced_ids = [b.book_id for b in forced]
    extra_n = max(0, cap - len(forced_ids))
    rest.sort(key=lambda b: float(b.cheap_score), reverse=True)
    extra = [b.book_id for b in rest[:extra_n]]
    selected = forced_ids + extra
    return ScreenResult(
        selected=selected,
        forced=forced_ids,
        forced_inventory=forced_inv,
        forced_dust=forced_dust,
        forced_kappa=forced_kappa,
        forced_hard_risk=forced_hard,
        forced_live=forced_live,
        screened_extra=extra,
        candidate_count=cap,
        universe=len(seen),
    )
