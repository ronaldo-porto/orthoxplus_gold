from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY_DIR))

from research_inventory_liveness import (
    INVENTORY_LIVENESS_VERSION,
    classify_liveness_stage,
    evaluate_bounded_rescue,
    evaluate_protected_parking,
    parked_refresh_due,
)
from research_kappa_flywheel import (
    KAPPA_FLYWHEEL_VERSION,
    PNL_CONFIDENCE_FULL,
    PNL_CONFIDENCE_PARTIAL,
    PNL_CONFIDENCE_UNKNOWN,
    pnl_confidence,
    pnl_confidence_multiplier,
)
from research_execution_lanes import LaneBook, apply_kappa_conversion_pressure_gate

SRC = (STRATEGY_DIR / "Strategy1_Research.py").read_text(encoding="utf-8")


def test_v418_version_contract():
    assert INVENTORY_LIVENESS_VERSION == "inventory_state_decoupling_v4_12_18"
    assert KAPPA_FLYWHEEL_VERSION == "kappa_flywheel_v4_12_18"
    assert 'RESEARCH_POLICY_VERSION = "simplified_kappa_productivity_v4_13_1"' in SRC
    assert 'RESEARCH_LANES_VERSION = "execution_lanes_v7_inventory_decoupled"' in SRC


def test_one_away_is_loss_protected_but_park_eligible():
    stage = classify_liveness_stage(
        observations_remaining=1,
        failed_exit_count=4,
        inventory_age=8,
        inventory_state="NORMAL",
        stop_loss_hit=False,
    )
    assert stage.protected
    assert not stage.loss_rescue_eligible
    assert stage.park_eligible
    assert stage.protected_park_armed
    assert not stage.maker_rescue_armed
    assert not stage.taker_rescue_armed


def test_qualified_emergency_can_park_without_taker_subsidy():
    stage = classify_liveness_stage(
        observations_remaining=0,
        failed_exit_count=1,
        inventory_age=2,
        inventory_state="EMERGENCY",
        stop_loss_hit=True,
    )
    park = evaluate_protected_parking(
        stage,
        executable_maker_net_bps=-25.0,
        protected_floor_bps=0.0,
    )
    rescue = evaluate_bounded_rescue(
        stage,
        taker_net_bps=-5.0,
        wait_ev_bps=-100.0,
        expected_markout_bps=-10.0,
        adverse_selection_risk=1.0,
        stop_loss_hit=True,
        inventory_state="EMERGENCY",
    )
    assert park.park and park.reason == "PROTECTED_STALE_NON_EXECUTABLE"
    assert not rescue.authorized and not rescue.park
    assert rescue.reason == "SCORE_STATE_PROTECTED"


def test_protected_book_stays_active_if_touch_respects_floor():
    stage = classify_liveness_stage(
        observations_remaining=1,
        failed_exit_count=10,
        inventory_age=100,
        inventory_state="EMERGENCY",
        stop_loss_hit=True,
    )
    park = evaluate_protected_parking(
        stage,
        executable_maker_net_bps=-0.5,
        protected_floor_bps=-1.0,
    )
    assert not park.park
    assert park.reason == "PROTECTED_TOUCH_EXECUTABLE"


def test_parked_refresh_default_is_bounded_25_ticks():
    due, reason = parked_refresh_due(
        current_tick=24,
        last_refresh_tick=0,
        current_mid=300.0,
        last_mid=300.0,
    )
    assert not due
    due, reason = parked_refresh_due(
        current_tick=25,
        last_refresh_tick=0,
        current_mid=300.0,
        last_mid=300.0,
    )
    assert due and reason == "INTERVAL"


def test_strategy_cancels_old_resting_order_when_newly_parked():
    assert "_research_cancel_resting_for_park" in SRC
    assert 'reason="NEWLY_PARKED_CANCEL_AND_HOLD"' in SRC
    assert '"PARK_CANCEL"' in SRC


def test_every_parked_maker_refresh_is_floor_checked_against_touch():
    assert "_research_parked_touch_exit" in SRC
    assert 'reason="PARK_FLOOR_BLOCK"' in SRC
    assert 'protected_floor_bps=floor' in SRC
    assert 'reason="UNPARK_EXECUTABLE"' in SRC
    assert '"PROTECTED_REFRESH_SUBMIT"' in SRC


def test_migrated_kappa_history_uses_confidence_not_false_pnl():
    assert pnl_confidence(3, 3) == PNL_CONFIDENCE_FULL
    assert pnl_confidence(3, 1) == PNL_CONFIDENCE_PARTIAL
    assert pnl_confidence(3, 0) == PNL_CONFIDENCE_UNKNOWN
    assert pnl_confidence_multiplier(PNL_CONFIDENCE_FULL) == 1.0
    assert pnl_confidence_multiplier(PNL_CONFIDENCE_PARTIAL) == 0.85
    assert pnl_confidence_multiplier(PNL_CONFIDENCE_UNKNOWN) == 0.70


def test_flywheel_core_uses_kappa_eligibility_not_full_pnl_history():
    assert 'bool(getattr(row, "kappa_eligible", False))' in SRC
    assert "productivity_phase = productivity_scheduler_phase(len(kappa_eligible_ids))" in SRC
    assert "pnl_confidence=str(pnl_conf)" in SRC
    assert "pnl_confidence_mult=float(pnl_conf_mult)" in SRC


def test_exploration_slot_remains_fail_open_under_pressure():
    rows = [
        LaneBook(book_id=1, observations_remaining=1, economics_ok=True),
        LaneBook(book_id=2, observations_remaining=3, is_uncovered=True, economics_ok=True, maker_ev=2.0, maker_ev_known=True),
        LaneBook(book_id=3, observations_remaining=3, is_uncovered=True, economics_ok=True, maker_ev=1.0, maker_ev_known=True),
    ]
    gated, suppressed, productive, reason = apply_kappa_conversion_pressure_gate(
        rows,
        parked_open_books=9,
        max_parked_open_books=6,
        total_open_books=10,
        max_total_open_books=12,
        reserve_total_slots=3,
        exploration_slots=1,
        enabled=True,
    )
    fresh = [r for r in gated if r.observations_remaining >= 3 and r.entry_feasible]
    assert reason == "TOTAL_HEADROOM"
    assert productive == 1
    assert len(fresh) == 1
    assert fresh[0].book_id == 2


def test_no_park_cap_reintroduced():
    assert 'reason="PARK_CAP"' not in SRC
    assert 'park_state="PARKED_PROTECTED"' in SRC
    assert 'park_state="PARKED_LIVENESS"' in SRC
