# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Production quote lifecycle and fill-class semantics."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from execution_lifecycle import (
    FILL_CLASSES,
    QuoteLifecycleStore,
    QuoteRecord,
    classify_fill,
    is_actionable,
    is_dust,
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


def test_fill_class_tokens_are_frozen():
    assert FILL_CLASSES == (
        "FULL",
        "ACTIONABLE_PARTIAL",
        "DUST_PARTIAL",
        "FLAT",
        "CROSS_DUST",
    )


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


def test_dust_partial_uses_runtime_min_not_hardcoded_quarter():
    assert _cls(
        inventory_before=0.0,
        inventory_after=0.10,
        fill_quantity=0.10,
        requested_quantity=0.25,
        filled_quantity=0.10,
        min_order_size=MIN_025,
    ) == "DUST_PARTIAL"
    assert _cls(
        inventory_before=0.0,
        inventory_after=0.10,
        fill_quantity=0.10,
        requested_quantity=0.10,
        filled_quantity=0.10,
        min_order_size=MIN_010,
    ) == "FULL"


def test_flat_and_cross_dust():
    assert _cls(
        inventory_before=0.25,
        inventory_after=0.0,
        fill_quantity=0.25,
    ) == "FLAT"
    assert _cls(
        inventory_before=0.25,
        inventory_after=-0.10,
        fill_quantity=0.35,
        requested_quantity=0.35,
        filled_quantity=0.35,
        min_order_size=MIN_025,
    ) == "CROSS_DUST"


def test_runtime_min_changes_dust_vs_actionable():
    assert is_dust(0.10, MIN_025, EPS)
    assert not is_dust(0.10, MIN_010, EPS)
    assert is_actionable(0.10, MIN_010, EPS)
    assert not is_actionable(0.10, MIN_025, EPS)


def test_quote_store_tracks_requested_and_remaining():
    store = QuoteLifecycleStore(max_live=8, max_pending_markouts=8)
    rec = store.register_quote(
        QuoteRecord(
            quote_id=store.next_quote_id(),
            client_id=11,
            book=3,
            side="buy",
            requested_quantity=0.25,
            remaining_quantity=0.25,
        )
    )
    store.apply_fill(rec, fill_qty=0.10, fill_ts=1, flat_eps=EPS)
    assert rec.filled_quantity == 0.10
    assert rec.remaining_quantity == 0.15
    found = store.lookup(3, 11)
    assert found is rec
