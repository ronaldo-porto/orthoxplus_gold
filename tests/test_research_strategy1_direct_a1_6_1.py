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

from research_direct_economics import (
    ACTION_MAKER,
    ACTION_SKIP,
    DIRECT_ECONOMICS_VERSION,
    DIRECT_EXECUTION_CONTROLLER_VERSION,
    DIRECT_MAKER_MIN_EDGE_BPS,
    DIRECT_TAKER_ENTRY_ENABLED,
    choose_direct_execution,
)
from research_direct_fastpath import (
    DIRECT_FASTPATH_VERSION,
    DIRECT_FASTPATH_CANDIDATE_COUNT,
    DIRECT_FASTPATH_DEEP_COUNT,
    DIRECT_FASTPATH_MAX_CANDIDATES,
    DIRECT_EDGE_FAIL_STREAK,
    DIRECT_EDGE_COOLDOWN_TICKS,
    FastPathRow,
    cheap_priority,
    observable_maker_edge_bps,
    select_fastpath_rows,
)
from research_direct_exit import (
    DIRECT_OBSERVABLE_EXIT_VERSION,
    DIRECT_MAKER_EXIT_TARGET_BPS,
    choose_observable_position_exit,
)
from research_position_exit import (
    ACTION_MAKER_EXIT,
    ACTION_TAKER_EXIT,
    ACTION_WAIT,
    ACTION_PARK_EXIT,
)
from research_direct_execution_quality import (
    DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS,
    DIRECT_MAKER_MAX_TTL_MS,
    DIRECT_DUST_EXEMPT_CAP,
)


def test_a161_version_contract():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_6_1"' in SRC
    assert DIRECT_ECONOMICS_VERSION == 'direct_economics_v4_16_2_a1_6_0'
    assert DIRECT_EXECUTION_CONTROLLER_VERSION == 'direct_execution_controller_v4_16_2_a1_6_0'
    assert DIRECT_FASTPATH_VERSION == 'direct_fastpath_v4_16_2_a1_6_1'
    assert DIRECT_OBSERVABLE_EXIT_VERSION == 'direct_observable_exit_v4_16_2_a1_6_0'


def test_a160_keeps_proven_execution_safety_constants():
    assert DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS == 6.0
    assert DIRECT_MAKER_MAX_TTL_MS == 75.0
    assert DIRECT_DUST_EXEMPT_CAP == 8
    assert DIRECT_TAKER_ENTRY_ENABLED is False


def test_a160_widens_fastpath_but_caps_deep_work():
    assert DIRECT_FASTPATH_CANDIDATE_COUNT == 20
    assert DIRECT_FASTPATH_DEEP_COUNT == 16
    assert DIRECT_FASTPATH_MAX_CANDIDATES == 24
    assert DIRECT_FASTPATH_DEEP_COUNT < DIRECT_FASTPATH_CANDIDATE_COUNT


def test_observable_edge_is_half_spread_minus_signed_maker_fee():
    assert observable_maker_edge_bps(spread_bps=18.0, maker_fee_bps=2.0) == 7.0
    # Signed Maker rebate increases current edge.
    assert observable_maker_edge_bps(spread_bps=10.0, maker_fee_bps=-1.0) == 6.0


def test_current_economics_can_beat_kappa_need_in_fast_priority():
    weak_one_away = cheap_priority(
        observations_remaining=1, qualified=False, spread_bps=8.0,
        maker_fee_bps=6.0, liquidity_quality=1.0, score_deficit=20,
    )
    strong_qualified = cheap_priority(
        observations_remaining=0, qualified=True, spread_bps=24.0,
        maker_fee_bps=1.0, liquidity_quality=1.0, score_deficit=20,
    )
    assert strong_qualified > weak_one_away


def test_fastpath_excludes_known_negative_edge_acquisition_rows():
    rows = [
        FastPathRow(1, 100.0, 1, False, observable_edge_bps=-1.0),
        FastPathRow(2, 10.0, 2, False, observable_edge_bps=3.0),
        FastPathRow(3, 9.0, 0, True, observable_edge_bps=8.0),
    ]
    selected = select_fastpath_rows(rows, candidate_count=16, score_deficit=20, tick=1)
    assert 1 not in selected
    assert 2 in selected
    assert 3 in selected


def test_fastpath_forces_inventory_even_if_current_edge_is_negative_or_cooled():
    rows = [FastPathRow(9, -99.0, 0, True, has_inventory=True, observable_edge_bps=-20.0, cooled=True)]
    assert select_fastpath_rows(rows, candidate_count=16, score_deficit=20, tick=1) == [9]


def test_fastpath_reserves_some_room_for_productive_qualified_books():
    rows = []
    for i in range(1, 30):
        rows.append(FastPathRow(i, 5.0, 1, False, observable_edge_bps=3.0))
    # Qualified books with much better current economics must not be starved.
    for i in range(101, 106):
        rows.append(FastPathRow(i, 20.0, 0, True, observable_edge_bps=10.0))
    selected = select_fastpath_rows(rows, candidate_count=20, score_deficit=20, tick=1)
    assert any(i >= 101 for i in selected)
    assert len(selected) == 20


def test_entry_authority_uses_current_edge_threshold_not_learned_quality():
    below = choose_direct_execution(
        maker_lifecycle_ev=0.8, maker_current_edge_bps=DIRECT_MAKER_MIN_EDGE_BPS - 0.01,
        directional_score=0.0, crossing_bps=5.0, maker_size=0.25, taker_clip=0.25,
        neutral_fallback=True,
    )
    above = choose_direct_execution(
        maker_lifecycle_ev=0.1, maker_current_edge_bps=DIRECT_MAKER_MIN_EDGE_BPS + 0.01,
        directional_score=0.0, crossing_bps=5.0, maker_size=0.25, taker_clip=0.25,
        neutral_fallback=True,
    )
    assert below.action == ACTION_SKIP
    assert above.action == ACTION_MAKER
    assert above.reason == 'MAKER_CURRENT_EDGE'


def test_source_has_no_learned_quality_in_entry_authority():
    entry = ast.get_source_segment(SRC, METHODS['_place_skewed_quotes'])
    assert 'maker_quality_adjustment' not in entry
    assert '_direct_quality_for_book' not in entry
    assert 'maker_realization_cost_estimate' not in entry
    assert 'maker_lifecycle_fee_cost_bps' not in entry
    assert 'maker_current_edge_bps=current_edge_bps' in entry
    assert 'learned_entry_authority=0' in entry


def test_score_authority_has_no_markout_fill_or_latency_learning():
    score_src = ast.get_source_segment(SRC, METHODS['_research_score_ev_for_book'])
    forbidden = [
        '_research_markout_snapshot', '_actionable_fill_snapshot',
        '_research_rolling_book_economics', '_research_strategy_latency_ms',
        '_direct_quality_for_book', 'maker_realization_cost_estimate',
    ]
    for token in forbidden:
        assert token not in score_src
    assert 'capture_bps - maker_fee' in score_src


def test_failed_edge_cooldown_is_small_and_deterministic():
    assert DIRECT_EDGE_FAIL_STREAK == 3
    assert DIRECT_EDGE_COOLDOWN_TICKS == 4
    assert '_direct_edge_fail_streak' in SRC
    assert '_direct_edge_cooldown_until' in SRC


def test_normal_negative_taker_exit_waits_instead_of_dumping():
    d = choose_observable_position_exit(
        maker_net_bps=-1.0, taker_net_bps=-4.0, p_maker_fill=0.05,
        unrealized_bps=-4.0, inventory_qty=0.25,
    )
    assert d.action == ACTION_WAIT
    assert d.reason == 'NORMAL_WAIT_NEGATIVE_TAKER'


def test_normal_profitable_maker_exit_is_preferred():
    d = choose_observable_position_exit(
        maker_net_bps=DIRECT_MAKER_EXIT_TARGET_BPS + 0.1,
        taker_net_bps=0.2, p_maker_fill=0.01,
        unrealized_bps=0.2, inventory_qty=0.25,
    )
    assert d.action == ACTION_MAKER_EXIT


def test_normal_nonnegative_taker_allowed_only_if_maker_target_not_met():
    d = choose_observable_position_exit(
        maker_net_bps=0.5, taker_net_bps=0.1, p_maker_fill=0.01,
        unrealized_bps=0.1, inventory_qty=0.25,
    )
    assert d.action == ACTION_TAKER_EXIT
    assert 'TAKER_NONNEGATIVE' in d.reason


def test_defensive_small_loss_waits_instead_of_utility_taker():
    d = choose_observable_position_exit(
        maker_net_bps=-2.0, taker_net_bps=-10.0, p_maker_fill=0.01,
        unrealized_bps=-10.0, inventory_qty=0.25,
    )
    assert d.action == ACTION_WAIT
    assert d.risk_band == 'DEFENSIVE'


def test_hard_escape_still_allows_negative_taker_for_risk():
    d = choose_observable_position_exit(
        maker_net_bps=-12.0, taker_net_bps=-19.0, p_maker_fill=0.01,
        unrealized_bps=-19.0, inventory_qty=0.25,
    )
    assert d.action == ACTION_TAKER_EXIT
    assert d.reason == 'HARD_ESCAPE_CLIP'


def test_absolute_non_executable_dust_parks():
    d = choose_observable_position_exit(
        maker_net_bps=-30.0, taker_net_bps=-30.0, p_maker_fill=0.01,
        unrealized_bps=-30.0, inventory_qty=0.1, min_order=0.25,
        is_dust=True, reduction_executable=False,
    )
    assert d.action == ACTION_PARK_EXIT


def test_direct_overlay_temporarily_substitutes_exit_chooser_without_editing_base():
    exit_src = ast.get_source_segment(SRC, METHODS['_research_apply_unified_exit'])
    assert 'choose_observable_position_exit' in exit_src
    assert 'finally' in exit_src
    base_src = (STRATEGY_DIR / 'Strategy1_Research.py').read_text(encoding='utf-8')
    assert 'choose_observable_position_exit' not in base_src



def test_a161_dust_inventory_does_not_consume_fastpath_slots():
    rows = [
        FastPathRow(7, 999.0, 0, True, has_inventory=True, is_dust=True,
                    observable_edge_bps=50.0),
        FastPathRow(8, -99.0, 0, True, has_inventory=True, is_dust=False,
                    observable_edge_bps=-20.0, cooled=True),
        FastPathRow(9, 5.0, 1, False, observable_edge_bps=3.0),
    ]
    selected = select_fastpath_rows(rows, candidate_count=16, score_deficit=20, tick=1)
    assert 7 not in selected
    assert 8 in selected
    assert 9 in selected


def test_a161_direct_compaction_is_explicit_and_theorem_safe():
    compact_src = ast.get_source_segment(SRC, METHODS['_direct_compact_selected_dust'])
    assert '_research_dust_compact_ids_this_tick' in compact_src
    assert '_dust_compaction_safe_for_any_fill' in compact_src
    assert 'super()._place_passive_inventory_exit' in compact_src
    assert '_research_dust_compact_attempts' in compact_src
    assert '_research_dust_compact_orders' in compact_src
    assert '_research_dust_compact_active' in compact_src
    assert 'DIRECT_DUST_COMPACT' in compact_src


def test_a161_build_services_dust_before_normal_inventory_management():
    build_src = ast.get_source_segment(SRC, METHODS['build_mm_strategy_instructions'])
    compact_ix = build_src.index('_direct_compact_selected_dust')
    manage_ix = build_src.index('# Inventory is never dependent on acquisition shortlist membership.')
    assert compact_ix < manage_ix
    assert 'direct_dust_compact_orders_delta' in build_src


def test_a161_final_validator_only_contract_checks_placements():
    validate_src = ast.get_source_segment(SRC, METHODS['_research_final_validate_instructions'])
    assert 'PLACE_ORDER_LIMIT' in validate_src
    assert 'PLACE_ORDER_MARKET' in validate_src
    assert 'super()._research_final_validate_instructions' in validate_src
    assert 'validated_ids' in validate_src
    assert 'merged.append(item)' in validate_src


def test_a161_keeps_a160_risk_and_execution_parameters_frozen():
    assert DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS == 6.0
    assert DIRECT_MAKER_MAX_TTL_MS == 75.0
    assert DIRECT_FASTPATH_CANDIDATE_COUNT == 20
    assert DIRECT_FASTPATH_DEEP_COUNT == 16
    assert DIRECT_TAKER_ENTRY_ENABLED is False



def test_a161_all_dust_is_excluded_from_productive_open_book_capacity_but_abs_risk_remains():
    screen_src = ast.get_source_segment(SRC, METHODS['_research_fast_screen'])
    build_src = ast.get_source_segment(SRC, METHODS['build_mm_strategy_instructions'])
    assert '"direct_effective_open_books": int(active_nonflat)' in screen_src
    assert 'total_abs_base += qty' in screen_src
    assert 'effective_open_now = int(active_now)' in build_src
    assert 'abs_now = float(diag.get("total_abs_base_inventory"' in build_src
    assert 'max_abs = float(getattr(self, "research_max_total_abs_base", 2.0)' in build_src
