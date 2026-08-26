# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Production candidate screen: forced inventory / Kappa / risk + cache."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from candidate_screen import (
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


def test_forced_kappa_and_risk_always_included():
    books = [
        ScreenBook(book_id=10, is_hard_risk=True, cheap_score=0.0),
        ScreenBook(book_id=11, observations_remaining=1, cheap_score=0.0),
        ScreenBook(book_id=12, observations_remaining=2, cheap_score=0.0),
        ScreenBook(book_id=13, observations_remaining=0, cheap_score=1.0),
        ScreenBook(book_id=14, observations_remaining=3, cheap_score=0.95),
    ]
    result = select_fast_candidates(books, candidate_count=3)
    assert 10 in result.selected
    assert 11 in result.selected
    assert 12 in result.selected
    assert 10 in result.forced_hard_risk
    assert set(result.forced_kappa) >= {11, 12}


def test_forced_can_exceed_candidate_count():
    books = [
        ScreenBook(book_id=i, has_inventory=True, cheap_score=0.0)
        for i in range(10)
    ] + [ScreenBook(book_id=99, cheap_score=1.0)]
    result = select_fast_candidates(books, candidate_count=4)
    assert set(range(10)).issubset(set(result.selected))
    assert 99 not in result.selected


def test_flag_off_selects_entire_universe():
    books = [ScreenBook(book_id=i, cheap_score=float(i)) for i in range(5)]
    result = select_fast_candidates(books, candidate_count=10_000)
    assert set(result.selected) == {0, 1, 2, 3, 4}


def test_one_away_kappa_is_forced():
    assert is_one_away_kappa(1) is True
    assert is_forced(ScreenBook(book_id=7, observations_remaining=1)) is True
    assert is_forced(ScreenBook(book_id=8, cheap_score=1.0)) is False


def test_feature_cache_reuses_unchanged_touch():
    class _Lvl:
        def __init__(self, price, quantity):
            self.price = price
            self.quantity = quantity

    class _Book:
        def __init__(self, bid, ask):
            self.bids = [_Lvl(*bid)]
            self.asks = [_Lvl(*ask)]
            self.events = []

    book = _Book((10.0, 4.0), (10.2, 5.0))
    fingerprint = book_touch_fingerprint(book)
    cache = FeatureCache()
    assert cache.lookup_touch(1, fingerprint) is None
    cache.store_touch(1, fingerprint, spread_bps=20.0, imbalance=0.1, trade_events=2)
    hit = cache.lookup_touch(1, fingerprint)
    assert hit is not None
    assert hit.spread_bps == 20.0
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


def test_cheap_score_prefers_liquid_tight_books():
    tight = cheap_book_score(spread_bps=2.0, trade_events=8, fill_rate=0.6)
    wide = cheap_book_score(spread_bps=18.0, trade_events=0, fill_rate=0.1)
    assert tight > wide


def test_candidate_count_is_clamped():
    assert clamp_candidate_count(16) == 16
    assert clamp_candidate_count(3) == 8
    assert clamp_candidate_count(99) == 64
