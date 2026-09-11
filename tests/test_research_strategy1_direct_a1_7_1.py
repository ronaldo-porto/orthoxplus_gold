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

from research_direct_exit import (
    DIRECT_OBSERVABLE_EXIT_VERSION,
    DIRECT_HARD_ESCAPE_MIN_AGE_TICKS,
    DIRECT_POSITIVE_MAKER_VETO_MAX_FAILED_EXITS,
    choose_observable_position_exit,
)
from research_direct_fastpath import DIRECT_FASTPATH_CANDIDATE_COUNT, DIRECT_FASTPATH_DEEP_COUNT
from research_direct_economics import DIRECT_MAKER_MIN_EDGE_BPS, DIRECT_TAKER_ENTRY_ENABLED
from research_direct_execution_quality import DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS
from research_position_exit import ACTION_MAKER_EXIT, ACTION_TAKER_EXIT, ACTION_WAIT


def test_a171_version_contract():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_1_1"' in SRC
    assert DIRECT_OBSERVABLE_EXIT_VERSION == 'direct_observable_exit_v4_16_2_a1_7_2'


def test_a171_keeps_productivity_and_entry_authority_frozen():
    assert DIRECT_FASTPATH_CANDIDATE_COUNT == 20
    assert DIRECT_FASTPATH_DEEP_COUNT == 16
    assert DIRECT_MAKER_MIN_EDGE_BPS == 2.5
    assert DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS == 6.0
    assert DIRECT_TAKER_ENTRY_ENABLED is False


def test_wide_spread_negative_taker_does_not_fabricate_hard_risk():
    d = choose_observable_position_exit(
        maker_net_bps=3.0,
        taker_net_bps=-20.0,
        p_maker_fill=0.05,
        unrealized_bps=0.0,
        inventory_qty=0.25,
        inventory_age=1.0,
    )
    assert d.risk_band == 'NORMAL'
    assert d.action == ACTION_MAKER_EXIT
    assert d.reason == 'NORMAL_MAKER_NET'


def test_unknown_mtm_never_falls_back_to_negative_taker_net_for_risk():
    d = choose_observable_position_exit(
        maker_net_bps=-1.0,
        taker_net_bps=-20.0,
        p_maker_fill=0.05,
        unrealized_bps=None,
        inventory_qty=0.25,
        inventory_age=10.0,
    )
    assert d.risk_band == 'NORMAL'
    assert d.action == ACTION_WAIT


def test_true_hard_loss_at_age_one_gets_fresh_grace():
    d = choose_observable_position_exit(
        maker_net_bps=-2.0,
        taker_net_bps=-22.0,
        p_maker_fill=0.05,
        unrealized_bps=-19.0,
        inventory_qty=0.25,
        inventory_age=1.0,
        failed_exit_count=0,
        hard_escape_min_age_ticks=DIRECT_HARD_ESCAPE_MIN_AGE_TICKS,
    )
    assert d.risk_band == 'HARD_ESCAPE'
    assert d.action == ACTION_WAIT
    assert d.reason == 'HARD_ESCAPE_FRESH_GRACE'


def test_true_hard_loss_when_mature_allows_taker_escape():
    d = choose_observable_position_exit(
        maker_net_bps=-2.0,
        taker_net_bps=-22.0,
        p_maker_fill=0.05,
        unrealized_bps=-19.0,
        inventory_qty=0.25,
        inventory_age=2.0,
        failed_exit_count=0,
    )
    assert d.action == ACTION_TAKER_EXIT
    assert d.reason == 'HARD_ESCAPE_CLIP'


def test_failed_exit_unlocks_hard_escape_even_before_age_floor():
    d = choose_observable_position_exit(
        maker_net_bps=-2.0,
        taker_net_bps=-22.0,
        p_maker_fill=0.05,
        unrealized_bps=-19.0,
        inventory_qty=0.25,
        inventory_age=1.0,
        failed_exit_count=1,
    )
    assert d.action == ACTION_TAKER_EXIT
    assert d.reason == 'HARD_ESCAPE_CLIP'


def test_positive_maker_veto_beats_hard_negative_taker_until_retry_limit():
    d = choose_observable_position_exit(
        maker_net_bps=3.0,
        taker_net_bps=-22.0,
        p_maker_fill=0.05,
        unrealized_bps=-19.0,
        inventory_qty=0.25,
        inventory_age=10.0,
        failed_exit_count=DIRECT_POSITIVE_MAKER_VETO_MAX_FAILED_EXITS - 1,
        positive_maker_veto_floor_bps=1.0,
        positive_maker_veto_max_failed_exits=DIRECT_POSITIVE_MAKER_VETO_MAX_FAILED_EXITS,
    )
    assert d.action == ACTION_MAKER_EXIT
    assert d.reason == 'HARD_ESCAPE_POSITIVE_MAKER_VETO'


def test_positive_maker_veto_releases_at_retry_limit():
    d = choose_observable_position_exit(
        maker_net_bps=3.0,
        taker_net_bps=-22.0,
        p_maker_fill=0.05,
        unrealized_bps=-19.0,
        inventory_qty=0.25,
        inventory_age=10.0,
        failed_exit_count=DIRECT_POSITIVE_MAKER_VETO_MAX_FAILED_EXITS,
        positive_maker_veto_floor_bps=1.0,
        positive_maker_veto_max_failed_exits=DIRECT_POSITIVE_MAKER_VETO_MAX_FAILED_EXITS,
    )
    assert d.action == ACTION_TAKER_EXIT
    assert d.reason == 'HARD_ESCAPE_CLIP'


def test_absolute_true_mtm_loss_with_negative_maker_reduces_immediately():
    d = choose_observable_position_exit(
        maker_net_bps=-4.0,
        taker_net_bps=-30.0,
        p_maker_fill=0.05,
        unrealized_bps=-26.0,
        inventory_qty=0.25,
        inventory_age=0.0,
        failed_exit_count=0,
    )
    assert d.risk_band == 'ABSOLUTE_PROTECTION'
    assert d.action == ACTION_TAKER_EXIT
    assert d.reason == 'ABSOLUTE_PROTECTION_REDUCE'


def test_catastrophic_inventory_emergency_keeps_immediate_taker_authority():
    d = choose_observable_position_exit(
        maker_net_bps=4.0,
        taker_net_bps=-5.0,
        p_maker_fill=0.05,
        unrealized_bps=0.0,
        inventory_qty=0.25,
        inventory_age=0.0,
        catastrophic_hard_risk=True,
    )
    assert d.action == ACTION_TAKER_EXIT
    assert d.reason == 'ABSOLUTE_PROTECTION_REDUCE'


def test_overlay_injects_true_inventory_mtm_and_existing_veto_parameters():
    method = ast.get_source_segment(SRC, METHODS['_research_apply_unified_exit'])
    assert 'true_unrealized = getattr(inventory, "unrealized_bps", None)' in method
    assert 'exit_kwargs["unrealized_bps"] = true_unrealized' in method
    assert 'research_bounded_loss_escape_min_age_ticks' in method
    assert 'research_positive_maker_veto_floor_bps' in method
    assert 'research_positive_maker_veto_max_failed_exits' in method
    assert 'A171_EXIT_DIAGNOSTIC' in method
    assert 'risk_source="MID_MTM_EXCLUDES_CROSSING_COST"' in method


def test_frozen_base_still_untouched_by_a171_overlay():
    base_src = (STRATEGY_DIR / 'Strategy1_Research.py').read_text(encoding='utf-8')
    assert 'A171_EXIT_DIAGNOSTIC' not in base_src
    assert 'direct_observable_exit_v4_16_2_a1_7_1' not in base_src
