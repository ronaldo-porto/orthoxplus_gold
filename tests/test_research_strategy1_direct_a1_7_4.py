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

from research_direct_exit import choose_observable_position_exit
from research_direct_economics import DIRECT_MAKER_MIN_EDGE_BPS, DIRECT_TAKER_ENTRY_ENABLED
from research_direct_fastpath import DIRECT_FASTPATH_CANDIDATE_COUNT, DIRECT_FASTPATH_DEEP_COUNT
from research_direct_liveness import DIRECT_LIVENESS_VERSION
from research_direct_tail_recovery import (
    DIRECT_TAIL_RECOVERY_VERSION,
    DIRECT_RECOVERY_TRIGGER_BPS,
    DIRECT_RECOVERY_FORCE_BPS,
    DIRECT_RECOVERY_MAKER_FLOOR_BPS,
    DIRECT_HARD_RECOVERY_MAKER_FLOOR_BPS,
    DIRECT_ABSOLUTE_RECOVERY_MAKER_FLOOR_BPS,
    DIRECT_RECOVERY_TAKER_FLOOR_BPS,
    choose_tail_recovery_override,
    risk_velocity_bps_per_tick,
)
from research_position_exit import ACTION_MAKER_EXIT, ACTION_TAKER_EXIT, ACTION_WAIT


def _base(*, maker, taker, risk, age=10, failed=0, catastrophic=False):
    return choose_observable_position_exit(
        maker_net_bps=maker, taker_net_bps=taker, p_maker_fill=0.10,
        unrealized_bps=risk, inventory_qty=0.25, inventory_age=age,
        failed_exit_count=failed, catastrophic_hard_risk=catastrophic,
    )


def _tail(*, maker, taker, risk, velocity, age=10, failed=0, catastrophic=False):
    base = _base(maker=maker, taker=taker, risk=risk, age=age, failed=failed, catastrophic=catastrophic)
    return choose_tail_recovery_override(
        base_decision=base, maker_net_bps=maker, taker_net_bps=taker,
        position_risk_bps=risk, risk_velocity_bps_per_tick_value=velocity,
        inventory_qty=0.25, inventory_age=age, failed_exit_count=failed,
        catastrophic_hard_risk=catastrophic, reduction_executable=True,
    )


def test_a174_version_and_frozen_entry_liveness_contract():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_7_4_3"' in SRC
    assert DIRECT_TAIL_RECOVERY_VERSION == "direct_tail_recovery_v4_16_2_a1_7_4"
    assert DIRECT_LIVENESS_VERSION == "direct_partial_liveness_v4_16_2_a1_7_3_1"
    assert DIRECT_FASTPATH_CANDIDATE_COUNT == 20
    assert DIRECT_FASTPATH_DEEP_COUNT == 16
    assert DIRECT_MAKER_MIN_EDGE_BPS == 2.5
    assert DIRECT_TAKER_ENTRY_ENABLED is False


def test_observation_driven_initial_recovery_corridor_constants():
    assert DIRECT_RECOVERY_TRIGGER_BPS == -8.0
    assert DIRECT_RECOVERY_FORCE_BPS == -12.0
    assert DIRECT_RECOVERY_MAKER_FLOOR_BPS == -25.0
    assert DIRECT_HARD_RECOVERY_MAKER_FLOOR_BPS == -30.0
    assert DIRECT_ABSOLUTE_RECOVERY_MAKER_FLOOR_BPS == -35.0
    assert DIRECT_RECOVERY_TAKER_FLOOR_BPS == -25.0


def test_recent_mtm_velocity_uses_true_risk_history_only():
    history = [
        {"tick": 10, "risk_bps": -5.0},
        {"tick": 11, "risk_bps": -7.0},
        {"tick": 12, "risk_bps": -9.0},
    ]
    v = risk_velocity_bps_per_tick(history, tick=13, current_risk_bps=-11.0)
    assert v < -1.0


def test_defensive_worsening_uses_bounded_recovery_maker():
    d = _tail(maker=-20.0, taker=-42.0, risk=-10.0, velocity=-1.5, age=20, failed=0)
    assert d.action == ACTION_MAKER_EXIT
    assert d.reason == "RECOVERY_MAKER_EXIT"
    assert d.corridor_stage == "RECOVERY"


def test_stable_shallow_defensive_wait_does_not_force_recovery():
    d = _tail(maker=-20.0, taker=-42.0, risk=-9.0, velocity=0.0, age=20, failed=0)
    assert d.action == ACTION_WAIT
    assert d.reason == "DEFENSIVE_WAIT_NEGATIVE_TAKER"


def test_deeper_defensive_forces_recovery_even_without_negative_velocity():
    d = _tail(maker=-20.0, taker=-42.0, risk=-13.0, velocity=0.0, age=20, failed=0)
    assert d.action == ACTION_MAKER_EXIT
    assert d.reason == "RECOVERY_MAKER_EXIT"


def test_recovery_maker_must_be_materially_better_than_crossing():
    d = _tail(maker=-20.0, taker=-25.0, risk=-13.0, velocity=-1.0, age=20, failed=0)
    assert d.action == ACTION_WAIT
    assert d.reason == "DEFENSIVE_WAIT_NEGATIVE_TAKER"


def test_failed_recovery_can_take_bounded_pre_hard_loss():
    d = _tail(maker=-40.0, taker=-22.0, risk=-12.0, velocity=-1.0, age=30, failed=2)
    assert d.action == ACTION_TAKER_EXIT
    assert d.reason == "RECOVERY_TAKER_REDUCE"


def test_pre_hard_recovery_taker_never_exceeds_loss_floor():
    d = _tail(maker=-40.0, taker=-30.0, risk=-12.0, velocity=-1.0, age=30, failed=2)
    assert d.action == ACTION_WAIT
    assert d.reason == "DEFENSIVE_WAIT_NEGATIVE_TAKER"


def test_hard_escape_gets_bounded_negative_maker_grace_when_much_better_than_taker():
    d = _tail(maker=-28.0, taker=-48.0, risk=-19.5, velocity=-1.0, age=20, failed=0)
    assert d.action == ACTION_MAKER_EXIT
    assert d.reason == "HARD_RECOVERY_MAKER_EXIT"


def test_fresh_hard_escape_keeps_a171_fresh_position_protection():
    d = _tail(maker=-28.0, taker=-48.0, risk=-19.5, velocity=-2.0, age=1, failed=0)
    assert d.action == ACTION_WAIT
    assert d.reason == "HARD_ESCAPE_FRESH_GRACE"


def test_mtm_absolute_gets_one_bounded_recovery_maker_when_materially_better():
    d = _tail(maker=-32.0, taker=-50.0, risk=-30.0, velocity=-1.0, age=20, failed=0)
    assert d.action == ACTION_MAKER_EXIT
    assert d.reason == "ABSOLUTE_RECOVERY_MAKER_EXIT"


def test_catastrophic_absolute_never_delays_taker_for_recovery():
    d = _tail(maker=-20.0, taker=-50.0, risk=-30.0, velocity=-1.0, age=20, failed=0, catastrophic=True)
    assert d.action == ACTION_TAKER_EXIT
    assert d.reason == "ABSOLUTE_PROTECTION_REDUCE"


def test_negative_recovery_maker_exception_is_bounded_at_final_placement_boundary():
    method = ast.get_source_segment(SRC, METHODS["_research_place_maker_exit"])
    assert '"A174_RECOVERY_MAKER_PLACE"' in method
    assert '"A174_RECOVERY_MAKER_BLOCK"' in method
    assert 'recovery_maker_authorized' in method
    assert 'recovery_maker_floor_bps' in method
    assert 'and not recovery_maker_ok' in method
    assert '"A172_NEGATIVE_AGGRESSIVE_BLOCK"' in method


def test_recovery_taker_is_reclassified_as_bounded_recovery_not_normal_economic():
    method = ast.get_source_segment(SRC, METHODS["_research_apply_unified_exit"])
    assert 'is_recovery_taker_reason(reason_token)' in method
    assert 'taker_authority="RECOVERY"' in method
    assert 'allowed_loss_floor_bps=DIRECT_RECOVERY_TAKER_FLOOR_BPS' in method
    assert 'economic_taker_authorized=False' in method
    assert 'risk_taker_authorized=True' in method


def test_a174_emits_recovery_counterfactual_and_outcome_diagnostics():
    apply_src = ast.get_source_segment(SRC, METHODS["_research_apply_unified_exit"])
    fill_src = ast.get_source_segment(SRC, METHODS["_research_on_own_fill"])
    assert '"A174_RECOVERY_DECISION"' in apply_src
    assert '_direct_tail_emit_counterfactual' in apply_src
    assert '"A174_RECOVERY_OUTCOME"' in fill_src
    counter_src = ast.get_source_segment(SRC, METHODS["_direct_tail_emit_counterfactual"])
    assert '"A174_TAIL_COUNTERFACTUAL"' in counter_src
    assert 'potential_avoided_loss_bps' in counter_src


def test_frozen_base_exit_liveness_and_fastpath_files_have_no_a174_patch():
    for name in [
        "Strategy1_Research.py", "research_direct_exit.py",
        "research_direct_liveness.py", "research_direct_fastpath.py",
    ]:
        src = (STRATEGY_DIR / name).read_text(encoding="utf-8")
        assert "A174_" not in src
        assert "a1_7_4" not in src
