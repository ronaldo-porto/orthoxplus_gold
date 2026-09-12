from pathlib import Path
import ast
import sys

ROOT = Path(__file__).parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY_DIR))

SRC=(STRATEGY_DIR / "Strategy1_Research_Simple.py").read_text()
TREE=ast.parse(SRC)
CLASS=next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name=="Strategy1_Research_Simple")
METHODS={n.name:n for n in CLASS.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}

from research_direct_book_ownership import (
    DIRECT_BOOK_OWNERSHIP_VERSION, ExchangeOrderIdentity,
    register_exchange_identity, reduce_identity_quantity, cancellation_identity_decision,
)


def test_a17432_version():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_5"' in SRC
    assert DIRECT_BOOK_OWNERSHIP_VERSION == "direct_book_ownership_v4_16_2_a1_7_4_3_2"


def test_unknown_stale_cancel_has_no_release_authority():
    registry={}
    decision, identity=cancellation_identity_decision(
        registry, exchange_order_id=222903, notice_book_id=51, success=False
    )
    assert decision == "STALE_UNKNOWN"
    assert identity is None


def test_exact_successful_cancel_gets_release_authority():
    registry={}
    register_exchange_identity(
        registry, exchange_order_id=222963, book_id=51, client_order_id=91511,
        side="buy", remaining_quantity=0.25,
    )
    decision, identity=cancellation_identity_decision(
        registry, exchange_order_id=222963, notice_book_id=51, success=True
    )
    assert decision == "RELEASE"
    assert identity.pending_key() == (51, "91511", "buy")


def test_failed_cancel_of_known_old_order_keeps_owner():
    registry={}
    register_exchange_identity(
        registry, exchange_order_id=222903, book_id=51, client_order_id=90001,
        side="buy", remaining_quantity=0.25,
    )
    decision, identity=cancellation_identity_decision(
        registry, exchange_order_id=222903, notice_book_id=51, success=False
    )
    assert decision == "FAILED_KEEP"
    assert identity.client_order_id == "90001"


def test_cancel_book_mismatch_is_not_release_authority():
    registry={}
    register_exchange_identity(
        registry, exchange_order_id=222963, book_id=51, client_order_id=91511,
        side="buy", remaining_quantity=0.25,
    )
    decision, _=cancellation_identity_decision(
        registry, exchange_order_id=222963, notice_book_id=52, success=True
    )
    assert decision == "BOOK_MISMATCH"


def test_identity_quantity_reduces_only_exact_order():
    registry={}
    ident=register_exchange_identity(
        registry, exchange_order_id=222963, book_id=51, client_order_id=91511,
        side="buy", remaining_quantity=0.25,
    )
    assert isinstance(ident, ExchangeOrderIdentity)
    assert abs(reduce_identity_quantity(ident, 0.10) - 0.15) < 1e-12
    assert abs(reduce_identity_quantity(ident, 0.15)) < 1e-12


def test_runtime_cancellation_path_never_uses_book_only_release():
    method=ast.get_source_segment(SRC, METHODS["_direct_note_cancellation_identity_notice"])
    assert "cancellation_identity_decision" in method
    assert "A17432_STALE_CANCEL_IGNORED" in method
    assert "A17432_RELEASE_MISMATCH_BLOCK" in method
    assert "_direct_release_pending_exact" in method
    # The old unsafe pattern iterated all pending keys and matched only book id.
    assert "for key in ledger" not in method


def test_fill_path_is_exchange_identity_first_and_unambiguous_fallback_only():
    method=ast.get_source_segment(SRC, METHODS["_direct_pending_note_fill"])
    assert "makerOrderId" in method and "takerOrderId" in method
    assert "identity.pending_key()" in method
    assert "if len(matches) != 1" in method
    assert "FILL_COMPLETE_EXACT" in method


def test_account_snapshot_registers_exchange_to_client_identity():
    method=ast.get_source_segment(SRC, METHODS["_direct_reconcile_pending_exposure"])
    assert "_direct_register_exchange_identity" in method
    assert 'exchange_order_id=getattr(order, "id", None)' in method


def test_diagnostics_and_stats_are_exposed():
    for token in (
        "A17432_STALE_CANCEL_IGNORED", "A17432_IDENTITY_RELEASE",
        "A17432_RELEASE_MISMATCH_BLOCK", "direct_stale_cancels_ignored",
        "direct_identity_releases", "direct_release_mismatch_blocks",
        "direct_exchange_order_identities",
    ):
        assert token in SRC
