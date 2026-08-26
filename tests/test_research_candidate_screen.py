# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research V4.3 Phase 6: fast candidate screening."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from research_candidate_screen import (
    FeatureCache,
    REQUIRED_TIMING_KEYS,
    ScreenBook,
    book_touch_fingerprint,
    cheap_book_score,
    clamp_candidate_count,
    is_forced,
    is_one_away_kappa,
    select_fast_candidates,
    timing_payload,
)


def test_forced_inventory_always_included():
    books = [
        ScreenBook(book_id=1, has_inventory=True, cheap_score=0.01),
        ScreenBook(book_id=2, cheap_score=0.99),
        ScreenBook(book_id=3, cheap_score=0.98),
    ]
    result = select_fast_candidates(books, candidate_count=2)
    assert 1 in result.selected
    assert 1 in result.forced_inventory
    assert len(result.selected) >= 2


def test_forced_dust_and_kappa_always_included():
    books = [
        ScreenBook(book_id=10, is_dust=True, cheap_score=0.0),
        ScreenBook(book_id=11, observations_remaining=1, cheap_score=0.0),
        ScreenBook(book_id=12, observations_remaining=2, cheap_score=0.0),
        ScreenBook(book_id=13, observations_remaining=0, cheap_score=1.0),
        ScreenBook(book_id=14, observations_remaining=3, cheap_score=0.95),
    ]
    result = select_fast_candidates(books, candidate_count=3)
    assert 10 in result.selected
    assert 11 in result.selected
    assert 12 in result.selected
    assert 10 in result.forced_dust
    assert set(result.forced_kappa) >= {11, 12}
    # Forced set is larger than cap; extras may be dropped, forced are not.
    assert 10 in result.forced and 11 in result.forced and 12 in result.forced
    assert len(result.selected) >= 3


def test_hard_risk_and_live_quotes_forced():
    books = [
        ScreenBook(book_id=1, is_hard_risk=True, cheap_score=0.0),
        ScreenBook(book_id=2, has_live_quote=True, cheap_score=0.0),
        ScreenBook(book_id=3, cheap_score=1.0),
    ]
    result = select_fast_candidates(books, candidate_count=2)
    assert 1 in result.selected and 2 in result.selected
    assert is_forced(books[0]) and is_forced(books[1])
    assert not is_forced(books[2])


def test_candidate_cap_fills_with_cheap_score():
    books = [
        ScreenBook(book_id=1, cheap_score=0.10),
        ScreenBook(book_id=2, cheap_score=0.80),
        ScreenBook(book_id=3, cheap_score=0.40),
        ScreenBook(book_id=4, has_inventory=True, cheap_score=0.01),
    ]
    result = select_fast_candidates(books, candidate_count=3)
    assert result.selected[0] == 4
    assert 2 in result.selected
    assert 1 not in result.selected
    assert result.screened_extra[0] == 2


def test_forced_can_exceed_candidate_count():
    books = [
        ScreenBook(book_id=i, has_inventory=True, cheap_score=0.0)
        for i in range(10)
    ] + [ScreenBook(book_id=99, cheap_score=1.0)]
    result = select_fast_candidates(books, candidate_count=4)
    assert set(range(10)).issubset(set(result.selected))
    assert 99 not in result.selected
    assert len(result.selected) == 10
    assert result.as_log()["forced_inventory_count"] == 10


def test_flag_off_selects_entire_universe():
    books = [ScreenBook(book_id=i, cheap_score=float(i)) for i in range(5)]
    result = select_fast_candidates(books, candidate_count=10_000)
    assert set(result.selected) == {0, 1, 2, 3, 4}
    assert result.universe == 5


def test_cheap_score_prefers_liquid_tight_books():
    tight = cheap_book_score(spread_bps=2.0, trade_events=8, fill_rate=0.6)
    wide = cheap_book_score(spread_bps=18.0, trade_events=0, fill_rate=0.1)
    assert tight > wide


class _Lvl:
    def __init__(self, price, quantity):
        self.price = price
        self.quantity = quantity


class _Book:
    def __init__(self, bid, ask, events=None, extra_bids=None):
        self.bids = [_Lvl(*bid)] + list(extra_bids or [])
        self.asks = [_Lvl(*ask)]
        self.events = events or []


def test_one_away_kappa_is_forced():
    assert is_one_away_kappa(1) is True
    assert is_one_away_kappa(2) is False
    book = ScreenBook(book_id=7, observations_remaining=1, cheap_score=0.0)
    assert is_forced(book) is True
    result = select_fast_candidates(
        [book, ScreenBook(book_id=8, cheap_score=1.0)],
        candidate_count=1,
    )
    assert 7 in result.selected


def test_candidate_count_is_configurable():
    assert clamp_candidate_count(16) == 16
    assert clamp_candidate_count(24) == 24
    assert clamp_candidate_count(3) == 8
    assert clamp_candidate_count(99) == 64
    books = [ScreenBook(book_id=i, cheap_score=float(i)) for i in range(40)]
    result = select_fast_candidates(books, candidate_count=clamp_candidate_count(18))
    assert len(result.selected) == 18
    assert result.candidate_count == 18


def test_feature_cache_reuses_unchanged_touch():
    book = _Book((10.0, 4.0), (10.2, 5.0), events=[object(), object()])
    fingerprint = book_touch_fingerprint(book)
    cache = FeatureCache()
    assert cache.lookup_touch(1, fingerprint) is None
    cache.store_touch(1, fingerprint, spread_bps=20.0, imbalance=0.1, trade_events=2)
    hit = cache.lookup_touch(1, fingerprint)
    assert hit is not None
    assert hit.spread_bps == 20.0
    assert hit.trade_events == 2
    changed = _Book((10.01, 4.0), (10.2, 5.0), events=[object(), object()])
    assert cache.lookup_touch(1, book_touch_fingerprint(changed)) is None
    assert cache.keep_unchanged(2, fingerprint) is False
    assert cache.keep_unchanged(2, fingerprint) is True


def test_timing_payload_has_required_keys():
    payload = timing_payload({
        "screen_all_books_ms": 1.5,
        "full_predict_ms": 8.0,
        "selection_ms": 2.0,
        "build_orders_ms": 3.0,
        "logging_ms": 0.4,
        "total_response_ms": 20.0,
    })
    assert set(REQUIRED_TIMING_KEYS) <= set(payload)
    assert payload["screen_ms"] == 1.5
    assert payload["ranking_ms"] == 2.0
    assert payload["total_response_ms"] == 20.0
