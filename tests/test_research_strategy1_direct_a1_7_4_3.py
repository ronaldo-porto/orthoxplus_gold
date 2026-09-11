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

from research_direct_inflight_reservation import (
    DIRECT_INFLIGHT_RESERVATION_VERSION,
    PendingExposureOrder,
    pending_order_live,
    reduce_pending_quantity,
)
from research_direct_dust_kappa import DIRECT_DUST_KAPPA_VERSION
from research_direct_trade_dedup import DIRECT_TRADE_DEDUP_VERSION
from research_direct_tail_recovery import DIRECT_TAIL_RECOVERY_VERSION


def test_a1743_version_and_frozen_economic_overlays():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_1_1"' in SRC
    assert DIRECT_INFLIGHT_RESERVATION_VERSION == "direct_inflight_reservation_v4_16_2_a1_7_4_3"
    # Both economic overlays carry the A1.7.5 module version; A1.7.5 extends them
    # without altering the A1.7.4.3 reservation contract asserted in this suite.
    assert DIRECT_DUST_KAPPA_VERSION == "direct_dust_kappa_v4_16_2_a1_7_5"
    assert DIRECT_TRADE_DEDUP_VERSION == "direct_trade_dedup_v4_16_2_a1_7_4_1"
    assert DIRECT_TAIL_RECOVERY_VERSION == "direct_tail_recovery_v4_16_2_a1_7_5"


def test_pending_limit_reservation_survives_snapshot_gap_then_expires_by_sim_ttl():
    row = PendingExposureOrder(
        book_id=10, side="buy", quantity=0.25, client_order_id=70101,
        submitted_tick=100, submitted_timestamp_ns=1_000_000_000,
        expiry_period_ns=500_000_000, order_kind="PLACE_ORDER_LIMIT",
    )
    assert pending_order_live(row, current_tick=101, current_timestamp_ns=1_200_000_000)
    assert not pending_order_live(row, current_tick=101, current_timestamp_ns=1_500_000_000)


def test_timestamp_reset_does_not_prematurely_release_pending_limit():
    row = PendingExposureOrder(
        book_id=10, side="sell", quantity=0.25, client_order_id=70102,
        submitted_tick=100, submitted_timestamp_ns=9_000_000_000,
        expiry_period_ns=500_000_000, order_kind="PLACE_ORDER_LIMIT",
    )
    assert pending_order_live(row, current_tick=101, current_timestamp_ns=1_000_000_000)
    assert not pending_order_live(row, current_tick=104, current_timestamp_ns=2_000_000_000)


def test_partial_fill_reduces_local_pending_reservation_exactly_once():
    row = PendingExposureOrder(
        book_id=5, side="buy", quantity=0.25, client_order_id=70051,
        submitted_tick=1, submitted_timestamp_ns=1, expiry_period_ns=500_000_000,
        order_kind="PLACE_ORDER_LIMIT",
    )
    assert abs(reduce_pending_quantity(row, 0.05) - 0.20) < 1e-12
    assert abs(row.quantity - 0.20) < 1e-12
    assert reduce_pending_quantity(row, 0.20) == 0.0


def test_final_validator_uses_local_pending_reservation_and_absolute_cap():
    validate = ast.get_source_segment(SRC, METHODS["_research_final_validate_instructions"])
    assert "_direct_outstanding_exposure_reservation(state)" in validate
    assert "STRICT_EXPOSURE_HEADROOM" in validate
    assert "A1743_STRICT_EXPOSURE_BLOCK" in validate
    assert "recovery_overflow" not in validate
    assert "max_abs + 1e-12" in validate


def test_outstanding_reservation_merges_account_and_pending_orders():
    method = ast.get_source_segment(SRC, METHODS["_direct_outstanding_exposure_reservation"])
    assert "_direct_reconcile_pending_exposure(state)" in method
    assert "_direct_account_orders(bid)" in method
    assert "ledger = self._direct_pending_ledger()" in method
    assert "outstanding_reservation(" in method


def test_live_order_guard_includes_unacknowledged_pending_book():
    method = ast.get_source_segment(SRC, METHODS["_direct_book_has_live_order"])
    assert "_direct_account_orders" in method
    assert "_direct_pending_ledger" in method


def test_final_emitted_placements_are_reserved_immediately():
    build = ast.get_source_segment(SRC, METHODS["build_mm_strategy_instructions"])
    assert "_research_final_validate_instructions(response, state)" in build
    assert "_direct_record_pending_placements(response, state)" in build
    assert build.index("_research_final_validate_instructions(response, state)") < build.index("_direct_record_pending_placements(response, state)")


def test_liveness_normalizer_no_longer_receives_overflow_budget():
    method = ast.get_source_segment(SRC, METHODS["_direct_normalize_irreducible_dust"])
    assert "recovery_overflow_abs=0.0" in method
    assert "temporary_recovery_overflow_abs=0.0" in method
