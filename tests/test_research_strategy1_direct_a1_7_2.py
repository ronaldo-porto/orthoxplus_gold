from pathlib import Path
import ast
import sys

ROOT = Path(__file__).parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY_DIR))

PATH = STRATEGY_DIR / "Strategy1_Research_Simple.py"
SRC = PATH.read_text(encoding="utf-8")
TREE = ast.parse(SRC)
CLASS = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
METHODS = {n.name: n for n in CLASS.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

from research_direct_exit import DIRECT_OBSERVABLE_EXIT_VERSION, choose_observable_position_exit
from research_direct_fastpath import DIRECT_FASTPATH_CANDIDATE_COUNT, DIRECT_FASTPATH_DEEP_COUNT
from research_direct_economics import DIRECT_MAKER_MIN_EDGE_BPS, DIRECT_TAKER_ENTRY_ENABLED
from research_position_exit import ACTION_MAKER_EXIT, ACTION_TAKER_EXIT


def test_a172_version_and_frozen_productivity_contract():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_1"' in SRC
    assert DIRECT_OBSERVABLE_EXIT_VERSION == "direct_observable_exit_v4_16_2_a1_7_2"
    assert DIRECT_FASTPATH_CANDIDATE_COUNT == 20
    assert DIRECT_FASTPATH_DEEP_COUNT == 16
    assert DIRECT_MAKER_MIN_EDGE_BPS == 2.5
    assert DIRECT_TAKER_ENTRY_ENABLED is False


def test_mtm_absolute_gets_one_positive_maker_grace():
    d = choose_observable_position_exit(
        maker_net_bps=105.0, taker_net_bps=-32.0, p_maker_fill=0.05,
        unrealized_bps=-26.0, inventory_qty=0.25, inventory_age=5.0,
        failed_exit_count=0,
    )
    assert d.action == ACTION_MAKER_EXIT
    assert d.reason == "ABSOLUTE_POSITIVE_MAKER_GRACE"


def test_mtm_absolute_positive_maker_grace_releases_after_one_attempt():
    d = choose_observable_position_exit(
        maker_net_bps=105.0, taker_net_bps=-32.0, p_maker_fill=0.05,
        unrealized_bps=-26.0, inventory_qty=0.25, inventory_age=5.0,
        failed_exit_count=1,
    )
    assert d.action == ACTION_TAKER_EXIT
    assert d.reason == "ABSOLUTE_PROTECTION_REDUCE"


def test_catastrophic_max_exposure_bypasses_absolute_maker_grace():
    d = choose_observable_position_exit(
        maker_net_bps=105.0, taker_net_bps=-32.0, p_maker_fill=0.05,
        unrealized_bps=-26.0, inventory_qty=0.25, inventory_age=0.0,
        failed_exit_count=0, catastrophic_hard_risk=True,
    )
    assert d.action == ACTION_TAKER_EXIT
    assert d.reason == "ABSOLUTE_PROTECTION_REDUCE"


def test_wait_is_rewritten_as_terminal_realization_action_at_overlay_boundary():
    method = ast.get_source_segment(SRC, METHODS["_research_apply_unified_exit"])
    assert '== ACTION_WAIT' in method
    assert "result = replace(" in method
    assert "action=ACTION_WAIT, selected_action=ACTION_WAIT" in method
    assert '"A172_EXIT_AUTHORITY"' in method
    assert "wait_is_terminal" in method


def test_wait_final_boundary_is_terminal_before_frozen_super_placement():
    method = ast.get_source_segment(SRC, METHODS["_research_place_maker_exit"])
    wait_pos = method.index('== ACTION_WAIT')
    wait_return = method.index('return 0', wait_pos)
    super_pos = method.index('return super()._research_place_maker_exit')
    assert wait_pos < wait_return < super_pos
    assert '"A172_WAIT_HOLD"' in method
    assert "new_maker_order=0" in method


def test_wait_cancels_only_unsafe_close_side_and_keeps_profitable_resting_exit():
    method = ast.get_source_segment(SRC, METHODS["_direct_cancel_unsafe_wait_exits"])
    assert "close_side = 1 if long_pos else 0" in method
    assert "order_net = unified_completion_net_bps(" in method
    assert "if float(order_net) + 1e-12 >= floor" in method
    assert "kept += 1" in method
    assert "cancel_ids.append(oid)" in method
    assert "response.cancel_orders(" in method
    assert '"A172_WAIT_CANCEL"' in method


def test_negative_aggressive_maker_is_blocked_before_frozen_super_placement():
    method = ast.get_source_segment(SRC, METHODS["_research_place_maker_exit"])
    guard_pos = method.index('== "AGGRESSIVE_MAKER_EXIT"')
    negative_pos = method.index("float(maker_net_bps) < -1e-12", guard_pos)
    block_event = method.index('"A172_NEGATIVE_AGGRESSIVE_BLOCK"', negative_pos)
    block_return = method.index("return 0", block_event)
    super_pos = method.index("return super()._research_place_maker_exit")
    assert guard_pos < negative_pos < block_event < block_return < super_pos


def test_positive_aggressive_path_is_not_globally_disabled():
    method = ast.get_source_segment(SRC, METHODS["_research_place_maker_exit"])
    assert 'float(maker_net_bps) < -1e-12' in method
    assert 'return super()._research_place_maker_exit(' in method
    assert 'maker_net_bps=maker_net_bps' in method


def test_frozen_research_base_has_no_a172_execution_patch():
    base_src = (STRATEGY_DIR / "Strategy1_Research.py").read_text(encoding="utf-8")
    assert "A172_WAIT_HOLD" not in base_src
    assert "A172_NEGATIVE_AGGRESSIVE_BLOCK" not in base_src
    assert "strategy1_direct_v4_16_2_a1_7_2" not in base_src
