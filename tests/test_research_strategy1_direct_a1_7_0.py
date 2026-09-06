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

from research_direct_quote_manager import (
    DIRECT_QUOTE_MANAGER_VERSION,
    DIRECT_QUOTE_QUIET_TTL_MS,
    DIRECT_QUOTE_TREND_TTL_MS,
    DIRECT_QUOTE_MAX_TTL_MS,
    ACTION_KEEP,
    ACTION_CANCEL_EDGE,
    ACTION_CANCEL_REPRICE,
    decide_quote_batch,
    keep_unselected_quote,
    maker_ttl_ms_for_regime,
)
from research_direct_fastpath import DIRECT_FASTPATH_CANDIDATE_COUNT, DIRECT_FASTPATH_DEEP_COUNT
from research_direct_economics import DIRECT_MAKER_MIN_EDGE_BPS, DIRECT_TAKER_ENTRY_ENABLED
from research_direct_execution_quality import DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS
from research_direct_exposure import DIRECT_EXPOSURE_VERSION


def test_a170_version_contract():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_7_0"' in SRC
    assert DIRECT_QUOTE_MANAGER_VERSION == 'direct_quote_manager_v4_16_2_a1_7_0'
    assert DIRECT_EXPOSURE_VERSION == 'direct_exposure_v4_16_2_a1_6_3'


def test_a170_keeps_strategy_authority_frozen():
    assert DIRECT_FASTPATH_CANDIDATE_COUNT == 20
    assert DIRECT_FASTPATH_DEEP_COUNT == 16
    assert DIRECT_MAKER_MIN_EDGE_BPS == 2.5
    assert DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS == 6.0
    assert DIRECT_TAKER_ENTRY_ENABLED is False


def test_a170_quote_manager_is_deterministic_not_learned():
    qsrc = (STRATEGY_DIR / 'research_direct_quote_manager.py').read_text(encoding='utf-8').lower()
    forbidden = ['makerlifecyclestats', 'posterior', 'ewma', 'rolling_pnl', 'learned_quality']
    for token in forbidden:
        assert token not in qsrc


def test_quiet_quotes_rest_longer_but_have_hard_ceiling():
    quiet = maker_ttl_ms_for_regime('QUIET')
    trend = maker_ttl_ms_for_regime('TREND_UP')
    assert quiet == DIRECT_QUOTE_QUIET_TTL_MS
    assert trend == DIRECT_QUOTE_TREND_TTL_MS
    assert quiet > trend
    assert quiet <= DIRECT_QUOTE_MAX_TTL_MS


def test_valid_quote_is_kept_without_new_gate():
    d = decide_quote_batch(
        existing=[('buy', 99.99), ('sell', 100.11)],
        desired_bid=99.99,
        desired_ask=100.11,
        best_bid=100.00,
        best_ask=100.10,
        current_edge_bps=4.0,
        min_edge_bps=2.5,
        tick_size=0.01,
    )
    assert d.action == ACTION_KEEP


def test_quote_is_canceled_when_existing_entry_edge_disappears():
    d = decide_quote_batch(
        existing=[('buy', 99.99)],
        desired_bid=99.99,
        desired_ask=100.11,
        best_bid=100.00,
        best_ask=100.10,
        current_edge_bps=2.49,
        min_edge_bps=2.5,
        tick_size=0.01,
    )
    assert d.action == ACTION_CANCEL_EDGE


def test_material_price_move_causes_cancel_then_later_reprice():
    d = decide_quote_batch(
        existing=[('buy', 99.90)],
        desired_bid=100.00,
        desired_ask=100.20,
        best_bid=100.01,
        best_ask=100.19,
        current_edge_bps=5.0,
        min_edge_bps=2.5,
        tick_size=0.01,
    )
    assert d.action == ACTION_CANCEL_REPRICE


def test_unselected_quote_can_survive_topk_rotation_when_still_valid():
    assert keep_unselected_quote(
        existing=[('buy', 99.99)],
        best_bid=100.00,
        best_ask=100.10,
        current_edge_bps=3.0,
        min_edge_bps=2.5,
    )


def test_unselected_quote_is_not_kept_when_too_far_from_touch():
    assert not keep_unselected_quote(
        existing=[('buy', 99.80)],
        best_bid=100.00,
        best_ask=100.10,
        current_edge_bps=3.0,
        min_edge_bps=2.5,
    )


def test_simple_place_uses_keep_reprice_cancel_and_regime_ttl():
    method = ast.get_source_segment(SRC, METHODS['_simple_place_maker'])
    assert 'decide_quote_batch(' in method
    assert 'QUOTE_ACTION_KEEP' in method
    assert '_direct_cancel_entry_quotes(' in method
    assert 'maker_expiry_ns_for_regime(' in method
    # Freshness must gate only a NEW placement, not KEEP/CANCEL maintenance.
    assert method.index('live_entry_orders =') < method.index('pre_submit_age_ms =')


def test_build_does_not_skip_flat_live_entry_before_quote_manager():
    method = ast.get_source_segment(SRC, METHODS['build_mm_strategy_instructions'])
    assert '_direct_maintain_unselected_entry_quotes(' in method
    assert '_direct_entry_quote_orders(book_id)' in method
    assert 'reason="INVENTORY_OPENED"' in method
    # A1.6.3 exposure safety remains in force.
    assert '_direct_outstanding_exposure_reservation(state)' in method


def test_nonflat_inventory_cancels_entry_quotes_before_exit_authority():
    method = ast.get_source_segment(SRC, METHODS['build_mm_strategy_instructions'])
    entry_cancel = method.index('if self._direct_entry_quote_orders(book_id):')
    manage_append = method.index('manage_queue.append(')
    assert entry_cancel < manage_append


def test_slow_request_logging_is_diagnostic_only():
    method = ast.get_source_segment(SRC, METHODS['respond'])
    assert 'DIRECT_SLOW_REQUEST' in method
    assert 'if elapsed_ms > 100.0:' in method
    assert 'return response' in method
