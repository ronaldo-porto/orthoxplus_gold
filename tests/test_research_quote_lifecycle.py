# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research V4.3 Phase 2: fill class, markout sign, delayed evaluation."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from research_quote_lifecycle import (
    MARKOUT_HORIZONS_MS,
    QuoteLifecycleStore,
    QuoteRecord,
    classify_fill,
    ms_to_ns,
    optional_queue_metrics,
    remaining_quantity,
    side_markout_bps,
)

EPS = 5e-9
MIN_025 = 0.25
MIN_010 = 0.10


def _cls(**kwargs):
    defaults = dict(
        fill_quantity=0.0,
        requested_quantity=0.25,
        filled_quantity=None,
        min_order_size=MIN_025,
        flat_eps=EPS,
    )
    defaults.update(kwargs)
    if defaults["filled_quantity"] is None:
        defaults["filled_quantity"] = defaults["fill_quantity"]
    return classify_fill(**defaults)


def test_full_fill_opens_actionable_position():
    assert _cls(
        inventory_before=0.0,
        inventory_after=0.25,
        fill_quantity=0.25,
        requested_quantity=0.25,
        filled_quantity=0.25,
    ) == "FULL"


def test_actionable_partial():
    assert _cls(
        inventory_before=0.0,
        inventory_after=0.25,
        fill_quantity=0.25,
        requested_quantity=0.50,
        filled_quantity=0.25,
    ) == "ACTIONABLE_PARTIAL"


def test_dust_partial():
    assert _cls(
        inventory_before=0.0,
        inventory_after=0.10,
        fill_quantity=0.10,
        requested_quantity=0.25,
        filled_quantity=0.10,
    ) == "DUST_PARTIAL"


def test_flat_closes_inventory():
    assert _cls(
        inventory_before=0.25,
        inventory_after=0.0,
        fill_quantity=0.25,
        requested_quantity=0.25,
        filled_quantity=0.25,
    ) == "FLAT"


def test_cross_dust():
    assert _cls(
        inventory_before=0.25,
        inventory_after=-0.10,
        fill_quantity=0.35,
        requested_quantity=0.35,
        filled_quantity=0.35,
    ) == "CROSS_DUST"


def test_dynamic_min_order_changes_class():
    kwargs = dict(
        inventory_before=0.0,
        inventory_after=0.12,
        fill_quantity=0.12,
        requested_quantity=0.25,
        filled_quantity=0.12,
        flat_eps=EPS,
    )
    assert classify_fill(min_order_size=MIN_025, **kwargs) == "DUST_PARTIAL"
    assert classify_fill(min_order_size=MIN_010, **kwargs) == "ACTIONABLE_PARTIAL"


def test_remaining_uses_runtime_min_not_hardcoded():
    assert remaining_quantity(0.10, 0.10, EPS) == 0.0
    assert remaining_quantity(0.25, 0.10, EPS) == 0.15


def test_maker_buy_markout_sign():
    # Price rose after a maker buy: favorable.
    assert side_markout_bps("buy", 100.0, 101.0) == 100.0
    # Price fell after a maker buy: adverse.
    assert side_markout_bps("buy", 100.0, 99.0) == -100.0


def test_maker_sell_markout_sign():
    # Price fell after a maker sell: favorable.
    assert side_markout_bps("sell", 100.0, 99.0) == 100.0
    # Price rose after a maker sell: adverse.
    assert side_markout_bps("sell", 100.0, 101.0) == -100.0


def test_delayed_markout_evaluation():
    store = QuoteLifecycleStore(missing_after_ms=2500)
    store.schedule_markouts(
        quote_id=7, book=3, side="buy", fill_price=100.0, fill_ts=0,
    )
    early = store.evaluate(now_ts=ms_to_ns(50), mids={3: 101.0})
    assert early == []
    first = store.evaluate(now_ts=ms_to_ns(100), mids={3: 101.0})
    assert len(first) == 1
    assert first[0].horizon_ms == 100
    assert first[0].status == "OK"
    assert first[0].markout_bps == 100.0
    later = store.evaluate(now_ts=ms_to_ns(250), mids={3: 101.0})
    assert [row.horizon_ms for row in later] == [250]
    still = store.evaluate(now_ts=ms_to_ns(400), mids={3: 101.0})
    assert still == []


def test_missing_future_observation():
    store = QuoteLifecycleStore(missing_after_ms=2500)
    store.schedule_markouts(
        quote_id=9, book=4, side="sell", fill_price=50.0, fill_ts=0,
    )
    # Horizon is due but this book has no mid yet: keep pending.
    held = store.evaluate(now_ts=ms_to_ns(100), mids={})
    assert held == []
    missing = store.evaluate(now_ts=ms_to_ns(2500), mids={})
    assert missing
    assert all(row.status == "MISSING_FUTURE" for row in missing)
    assert all(row.future_mid is None and row.markout_bps is None for row in missing)
    assert {row.horizon_ms for row in missing} == set(MARKOUT_HORIZONS_MS)


def test_queue_metrics_omitted_without_orders():
    empty = optional_queue_metrics(level_quantity=None, orders=None)
    assert empty == {}
    depth_only = optional_queue_metrics(level_quantity=3.5, orders=None)
    assert depth_only == {"queue_depth_at_price": 3.5}
    assert "queue_ahead" not in depth_only
    genuine = optional_queue_metrics(
        level_quantity=3.5,
        orders=({"quantity": 1.0}, {"quantity": 2.0}),
    )
    assert genuine["queue_ahead"] == 3.0


def test_client_id_replace_closes_prior_live_quote():
    store = QuoteLifecycleStore()
    first = QuoteRecord(quote_id=1, client_id=70001, book=8, side="buy", submit_ts=1)
    second = QuoteRecord(quote_id=2, client_id=70001, book=8, side="buy", submit_ts=2)
    store.register_quote(first)
    store.register_quote(second)
    assert first.open is False
    assert store.lookup(8, 70001).quote_id == 2
