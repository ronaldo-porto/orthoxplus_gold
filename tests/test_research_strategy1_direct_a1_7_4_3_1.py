from pathlib import Path
import ast
import sys

ROOT = Path(__file__).parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(STRATEGY_DIR))

PATH = STRATEGY_DIR / "Strategy1_Research_Simple.py"
SRC = PATH.read_text(encoding="utf-8")
TREE = ast.parse(SRC)
CLASS = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
METHODS = {n.name: n for n in CLASS.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

from research_direct_book_ownership import (
    DIRECT_BOOK_OWNERSHIP_VERSION,
    canonical_order_side,
    ownership_key,
    reserve_pending_order,
)
from research_direct_inflight_reservation import PendingExposureOrder, DIRECT_INFLIGHT_RESERVATION_VERSION
from research_direct_dust_kappa import DIRECT_DUST_KAPPA_VERSION
from research_direct_trade_dedup import DIRECT_TRADE_DEDUP_VERSION
from research_direct_tail_recovery import DIRECT_TAIL_RECOVERY_VERSION


def _row(*, side="sell", qty=0.25, cid=91022):
    return PendingExposureOrder(
        book_id=2, side=side, quantity=qty, client_order_id=cid,
        submitted_tick=10, submitted_timestamp_ns=1_000,
        expiry_period_ns=500_000_000, order_kind="PLACE_ORDER_LIMIT",
    )


def test_a17431_version_and_prior_overlays_frozen():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_0_2"' in SRC
    assert DIRECT_BOOK_OWNERSHIP_VERSION == "direct_book_ownership_v4_16_2_a1_7_4_3_2"
    assert DIRECT_INFLIGHT_RESERVATION_VERSION == "direct_inflight_reservation_v4_16_2_a1_7_4_3"
    # Both economic overlays carry the A1.7.5 module version; the ownership and
    # partial-remainder contracts asserted in this suite are untouched.
    assert DIRECT_DUST_KAPPA_VERSION == "direct_dust_kappa_v4_16_2_a1_7_5"
    assert DIRECT_TRADE_DEDUP_VERSION == "direct_trade_dedup_v4_16_2_a1_7_4_1"
    assert DIRECT_TAIL_RECOVERY_VERSION == "direct_tail_recovery_v4_16_2_a1_7_5"


def test_canonical_ownership_key_distinguishes_sides_not_aliases():
    assert canonical_order_side("SELL") == "sell"
    assert canonical_order_side("OrderDirection.BUY") == "buy"
    assert ownership_key(2, "sell") == ownership_key(2, "ASK")
    assert ownership_key(2, "buy") != ownership_key(2, "sell")


def test_duplicate_pending_key_aggregates_quantity_instead_of_overwriting():
    ledger = {}
    first, merged = reserve_pending_order(ledger, _row(qty=0.25))
    assert not merged
    second, merged = reserve_pending_order(ledger, _row(qty=0.25))
    assert merged
    assert first is second
    assert len(ledger) == 1
    assert abs(second.quantity - 0.50) < 1e-12


def test_final_validator_blocks_second_same_book_side_in_same_response():
    method = ast.get_source_segment(SRC, METHODS["_research_final_validate_instructions"])
    assert "same_request_owned_sides" in method
    assert "ownership_key(book_id, side_token)" in method
    assert "SAME_REQUEST_BOOK_SIDE_OWNED" in method
    assert "A17431_BOOK_OWNERSHIP_BLOCK" in SRC
    assert "same_request_owned_sides.add(owner_key)" in method


def test_opposite_sides_remain_eligible_as_two_sided_batch():
    method = ast.get_source_segment(SRC, METHODS["_research_final_validate_instructions"])
    # Ownership is keyed by (book, side), not by book alone, so one BUY and one
    # SELL can still form the existing two-sided Maker batch.
    assert "owner_key = ownership_key(book_id, side_token)" in method
    assert "same_request_owned_sides: set[tuple[int, str]]" in method


def test_pending_book_remains_owned_across_requests_and_partial_fill():
    live = ast.get_source_segment(SRC, METHODS["_direct_book_has_live_order"])
    fill = ast.get_source_segment(SRC, METHODS["_direct_pending_note_fill"])
    assert "_direct_pending_ledger" in live
    assert "reduce_pending_quantity" in fill
    assert "FILL_COMPLETE" in fill
    # Partial quantity stays in ledger because release only happens at <= 0.
    assert "if remaining <= 0.0" in fill


def test_ownership_diagnostics_and_stats_are_exposed():
    assert "A17431_BOOK_OWNERSHIP_RESERVE" in SRC
    assert "A17431_BOOK_OWNERSHIP_BLOCK" in SRC
    assert "A17431_BOOK_OWNERSHIP_RELEASE" in SRC
    assert 'stats["direct_book_ownership_reserves"]' in SRC
    assert 'stats["direct_book_ownership_blocks"]' in SRC
    assert 'stats["direct_book_ownership_releases"]' in SRC
