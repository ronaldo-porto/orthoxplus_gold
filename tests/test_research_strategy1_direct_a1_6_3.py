from pathlib import Path
import ast
import sys

ROOT = Path(__file__).parents[1]
STRATEGY_DIR = ROOT / 'agents' / 'strategy'
sys.path.insert(0, str(STRATEGY_DIR))

PATH = STRATEGY_DIR / 'Strategy1_Research_Simple.py'
SRC = PATH.read_text(encoding='utf-8')
TREE = ast.parse(SRC)
CLASS = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == 'Strategy1_Research_Simple')
METHODS = {n.name: n for n in CLASS.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

from research_direct_exposure import (
    DIRECT_EXPOSURE_VERSION,
    add_order_to_batch,
    outstanding_reservation,
    worst_case_abs_inventory,
)
from research_direct_fastpath import DIRECT_FASTPATH_CANDIDATE_COUNT, DIRECT_FASTPATH_DEEP_COUNT
from research_direct_economics import DIRECT_TAKER_ENTRY_ENABLED
from research_direct_execution_quality import DIRECT_MAKER_MAX_TTL_MS, DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS


def test_a163_version_contract():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_6_3"' in SRC
    assert DIRECT_EXPOSURE_VERSION == 'direct_exposure_v4_16_2_a1_6_3'


def test_a163_keeps_trading_parameters_frozen():
    assert DIRECT_FASTPATH_CANDIDATE_COUNT == 20
    assert DIRECT_FASTPATH_DEEP_COUNT == 16
    assert DIRECT_MAKER_MAX_TTL_MS == 75.0
    assert DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS == 6.0
    assert DIRECT_TAKER_ENTRY_ENABLED is False


def test_over_cap_reduction_reserves_no_new_abs():
    # +0.50 long with a 0.25 SELL can only stay at 0.50 (no fill) or fall to 0.25.
    d = add_order_to_batch(
        net=0.50, buy_before=0.0, sell_before=0.0,
        side='sell', quantity=0.25, min_order=0.25,
    )
    assert d.risk_reducing
    assert d.delta_worst_abs == 0.0


def test_risk_increasing_buy_reserves_full_increment():
    d = add_order_to_batch(
        net=0.50, buy_before=0.0, sell_before=0.0,
        side='buy', quantity=0.25, min_order=0.25,
    )
    assert not d.risk_reducing
    assert abs(d.delta_worst_abs - 0.25) < 1e-12


def test_symmetric_pair_reserves_one_sided_fill_not_net_zero():
    first = add_order_to_batch(
        net=0.0, buy_before=0.0, sell_before=0.0,
        side='buy', quantity=0.25, min_order=0.25,
    )
    second = add_order_to_batch(
        net=0.0, buy_before=0.25, sell_before=0.0,
        side='sell', quantity=0.25, min_order=0.25,
    )
    assert abs(first.new_worst_abs - 0.25) < 1e-12
    assert abs(second.new_worst_abs - 0.25) < 1e-12
    assert second.delta_worst_abs == 0.0


def test_third_same_side_order_after_pair_increases_worst_case():
    d = add_order_to_batch(
        net=0.0, buy_before=0.25, sell_before=0.25,
        side='buy', quantity=0.25, min_order=0.25,
    )
    assert abs(d.previous_worst_abs - 0.25) < 1e-12
    assert abs(d.new_worst_abs - 0.50) < 1e-12
    assert abs(d.delta_worst_abs - 0.25) < 1e-12


def test_outstanding_flat_buy_reserves_abs_and_open_slot():
    reserve_abs, reserve_open = outstanding_reservation(
        0.0, 0.25, 0.0, min_order=0.25,
    )
    assert abs(reserve_abs - 0.25) < 1e-12
    assert reserve_open == 1


def test_outstanding_long_exit_reserves_no_new_abs():
    reserve_abs, reserve_open = outstanding_reservation(
        0.50, 0.0, 0.25, min_order=0.25,
    )
    assert reserve_abs == 0.0
    assert reserve_open == 0


def test_worst_case_handles_both_sides_without_false_netting():
    assert worst_case_abs_inventory(0.0, 0.25, 0.25) == 0.25
    assert worst_case_abs_inventory(0.25, 0.25, 0.25) == 0.50


def test_source_has_directional_final_validation_and_inflight_reservation():
    validate = ast.get_source_segment(SRC, METHODS['_research_final_validate_instructions'])
    assert '_direct_outstanding_exposure_reservation(state)' in validate
    assert 'INFLIGHT_BOOK_ORDER' in validate
    assert 'add_order_to_batch(' in validate
    assert 'risk_reducing_batch' in validate
    assert 'projected_total_abs > max_abs' in validate
    assert 'if not risk_reducing_batch:' in validate


def test_build_reserves_open_orders_before_new_entry_capacity():
    build = ast.get_source_segment(SRC, METHODS['build_mm_strategy_instructions'])
    assert '_direct_outstanding_exposure_reservation(state)' in build
    assert 'effective_abs_now = abs_now + float(reserved_abs)' in build
    assert 'effective_open_now += int(reserved_open)' in build
    assert '_direct_book_has_live_order(book_id)' in build


def test_dust_compaction_isolated_from_existing_live_order():
    compact = ast.get_source_segment(SRC, METHODS['_direct_compact_selected_dust'])
    assert 'if self._direct_book_has_live_order(book_id):' in compact
    assert compact.index('_direct_book_has_live_order(book_id)') < compact.index('_dust_compaction_safe_for_any_fill')
