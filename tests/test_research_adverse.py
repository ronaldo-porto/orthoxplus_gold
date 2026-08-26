# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research V4.3 Phase 1.7: OFI, delayed markout, adverse selection."""
from dataclasses import dataclass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from research_adverse import (
    BookTouch,
    OfiTracker,
    entry_adverse_blocked,
    expected_markout_bps,
    extract_touch,
    ofi_against_position,
    ofi_increment,
    quote_width_multiplier,
)
from research_score_ev import compute_score_ev
from research_quote_lifecycle import (
    MARKOUT_HORIZONS_MS,
    QuoteLifecycleStore,
    ms_to_ns,
    side_markout_bps,
)


@dataclass
class _Level:
    price: float
    quantity: float


@dataclass
class _Book:
    bids: list[_Level]
    asks: list[_Level]


@dataclass
class _PriceOnly:
    price: float


def test_maker_buy_sell_markout_sign():
    assert side_markout_bps("buy", 100.0, 101.0) == 100.0
    assert side_markout_bps("buy", 100.0, 99.0) == -100.0
    assert side_markout_bps("sell", 100.0, 99.0) == 100.0
    assert side_markout_bps("sell", 100.0, 101.0) == -100.0


def test_delayed_markout_evaluation():
    store = QuoteLifecycleStore(missing_after_ms=2500)
    store.schedule_markouts(
        quote_id=7, book=3, side="buy", fill_price=100.0, fill_ts=0,
    )
    assert store.evaluate(now_ts=ms_to_ns(50), mids={3: 101.0}) == []
    first = store.evaluate(now_ts=ms_to_ns(100), mids={3: 101.0})
    assert [row.horizon_ms for row in first] == [100]
    assert first[0].markout_bps == 100.0
    later = store.evaluate(now_ts=ms_to_ns(250), mids={3: 101.0})
    assert [row.horizon_ms for row in later] == [250]


def test_missing_future_data():
    store = QuoteLifecycleStore(missing_after_ms=2500)
    store.schedule_markouts(
        quote_id=9, book=4, side="sell", fill_price=50.0, fill_ts=0,
    )
    assert store.evaluate(now_ts=ms_to_ns(100), mids={}) == []
    missing = store.evaluate(now_ts=ms_to_ns(2500), mids={})
    assert missing
    assert all(row.status == "MISSING_FUTURE" for row in missing)
    assert all(row.markout_bps is None for row in missing)
    assert {row.horizon_ms for row in missing} == set(MARKOUT_HORIZONS_MS)


def test_ofi_requires_consecutive_price_and_size():
    assert extract_touch(_Book(bids=[_PriceOnly(10.0)], asks=[_PriceOnly(10.1)])) is None
    tracker = OfiTracker()
    first = tracker.update(1, BookTouch(10.0, 5.0, 10.1, 5.0))
    assert first.supported is False
    assert first.ofi_raw is None
    assert first.source == "UNSUPPORTED"


def test_ofi_is_not_static_imbalance():
    prev = BookTouch(10.0, 8.0, 10.2, 2.0)
    curr = BookTouch(10.0, 8.0, 10.2, 2.0)
    # Static imbalance is (8-2)/10 = 0.6, but OFI increment is 0.
    assert ofi_increment(prev, curr) == 0.0
    imb = (8.0 - 2.0) / 10.0
    assert imb != 0.0


def test_ofi_calculation():
    # Bid size increase at the same price is buy flow.
    up = ofi_increment(
        BookTouch(10.0, 4.0, 10.2, 4.0),
        BookTouch(10.0, 7.0, 10.2, 4.0),
    )
    assert up == 3.0
    # Ask size increase at the same price is sell flow.
    down = ofi_increment(
        BookTouch(10.0, 4.0, 10.2, 4.0),
        BookTouch(10.0, 4.0, 10.2, 7.0),
    )
    assert down == -3.0
    # Bid improvement is buy flow equal to the new bid size.
    lift = ofi_increment(
        BookTouch(10.0, 4.0, 10.2, 4.0),
        BookTouch(10.1, 2.0, 10.2, 4.0),
    )
    assert lift == 2.0

    tracker = OfiTracker(fast_alpha=0.50)
    tracker.update(2, BookTouch(10.0, 4.0, 10.2, 4.0))
    snap = tracker.update(2, BookTouch(10.0, 8.0, 10.2, 4.0))
    assert snap.supported is True
    assert snap.source == "OFI"
    assert snap.ofi_raw == 4.0
    assert abs(snap.ofi_normalized - (4.0 / 12.0)) < 1e-12
    assert snap.ofi_fast == snap.ofi_normalized


def test_expected_markout_and_width_use_adverse_signals():
    expected = expected_markout_bps({
        100: {"n": 8, "sum": -16.0},
        250: {"n": 8, "sum": -40.0},
        500: {"n": 8, "sum": -64.0},
        1000: {"n": 8, "sum": -80.0},
    })
    assert expected < 0.0
    assert entry_adverse_blocked(
        expected_markout_bps=expected,
        adverse_selection_risk=0.80,
    ) is True
    assert quote_width_multiplier(
        adverse_selection_risk=0.50, ofi_normalized=0.40,
    ) > 1.0
    assert quote_width_multiplier(
        adverse_selection_risk=0.0, ofi_normalized=None,
    ) == 1.0


def test_ofi_against_position_is_side_corrected():
    assert ofi_against_position(-0.40, 1.0) == 0.40
    assert ofi_against_position(0.40, 1.0) == 0.0
    assert ofi_against_position(0.40, -1.0) == 0.40
    assert ofi_against_position(-0.40, -1.0) == 0.0
    assert ofi_against_position(None, 1.0) == 0.0


def test_ofi_enters_ranking_not_as_imbalance():
    calm = compute_score_ev(
        book=1,
        fill_prob_old=0.80,
        spread_capture_bps=6.0,
        markout_mean_bps=-2.0,
        markout_samples=20,
        min_trading_ev=-1.0,
    )
    toxic = compute_score_ev(
        book=1,
        fill_prob_old=0.80,
        spread_capture_bps=6.0,
        markout_mean_bps=-2.0,
        markout_samples=20,
        ofi_against=1.0,
        min_trading_ev=-1.0,
    )
    assert toxic.adverse_selection_risk > calm.adverse_selection_risk
    assert toxic.eligible and calm.eligible
    assert toxic.final_score < calm.final_score
