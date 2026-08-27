from pathlib import Path

from research_inventory_liveness import (
    INVENTORY_LIVENESS_VERSION,
    SCORE_QUALIFIED,
    SCORE_ONE_AWAY,
    SCORE_TWO_AWAY,
    SCORE_UNCOVERED,
    classify_score_state,
    classify_liveness_stage,
    evaluate_bounded_rescue,
    parked_refresh_due,
)


def _stage(remaining, *, failed=0, age=0, state="NORMAL", stop=False):
    return classify_liveness_stage(
        observations_remaining=remaining,
        required_observations=3,
        failed_exit_count=failed,
        inventory_age=age,
        inventory_state=state,
        stop_loss_hit=stop,
        maker_failed_exits=3,
        maker_min_age_ticks=8,
        taker_failed_exits=8,
        taker_min_age_ticks=16,
        hard_failed_exits=12,
        hard_min_age_ticks=24,
        maker_floor_bps=-4,
        soft_taker_floor_bps=-8,
        hard_taker_floor_bps=-12,
    )


def _rescue(stage, *, taker, wait, markout=-2.0, risk=0.5, stop=False, state="NORMAL"):
    return evaluate_bounded_rescue(
        stage,
        taker_net_bps=taker,
        wait_ev_bps=wait,
        expected_markout_bps=markout,
        adverse_selection_risk=risk,
        stop_loss_hit=stop,
        inventory_state=state,
        min_ev_advantage_bps=0.5,
        adverse_markout_bps=1.0,
        adverse_risk_floor=0.25,
    )


def test_score_states_protect_qualified_and_one_away_only():
    assert classify_score_state(0) == SCORE_QUALIFIED
    assert classify_score_state(1) == SCORE_ONE_AWAY
    assert classify_score_state(2) == SCORE_TWO_AWAY
    assert classify_score_state(3) == SCORE_UNCOVERED
    assert _stage(0, failed=20, age=100).protected
    assert _stage(1, failed=20, age=100).protected
    assert not _stage(2, failed=20, age=100).protected
    assert not _stage(3, failed=20, age=100).protected


def test_maker_rescue_arms_at_three_failures_or_age_eight():
    assert not _stage(2, failed=2, age=7).maker_rescue_armed
    assert _stage(2, failed=3, age=0).maker_rescue_armed
    assert _stage(3, failed=0, age=8).maker_rescue_armed
    assert not _stage(1, failed=100, age=100).maker_rescue_armed


def test_taker_rescue_arms_at_eight_failures_or_age_sixteen():
    assert not _stage(2, failed=7, age=15).taker_rescue_armed
    assert _stage(2, failed=8, age=0).taker_rescue_armed
    assert _stage(3, failed=0, age=16).taker_rescue_armed


def test_soft_bounded_rescue_authorizes_minus_seven_when_wait_is_worse():
    stage = _stage(2, failed=8, age=16)
    decision = _rescue(stage, taker=-7.0, wait=-10.0)
    assert decision.authorized
    assert not decision.park
    assert decision.allowed_loss_floor_bps == -8.0
    assert decision.reason == "BOUNDED_RESCUE_GT_WAIT"


def test_hard_emergency_window_can_use_minus_twelve_not_more():
    stage = _stage(3, failed=12, age=24, state="EMERGENCY", stop=True)
    assert stage.hard_window
    assert stage.taker_floor_bps == -12.0
    decision = _rescue(stage, taker=-11.0, wait=-20.0, stop=True, state="EMERGENCY")
    assert decision.authorized
    assert not decision.park
    assert decision.allowed_loss_floor_bps == -12.0


def test_loss_beyond_absolute_floor_parks_instead_of_dumping():
    stage = _stage(2, failed=12, age=24, state="EMERGENCY", stop=True)
    decision = _rescue(stage, taker=-13.0, wait=-30.0, stop=True, state="EMERGENCY")
    assert not decision.authorized
    assert decision.park
    assert decision.reason == "LOSS_BEYOND_HARD_FLOOR"


def test_one_away_never_receives_liveness_loss_subsidy():
    stage = _stage(1, failed=50, age=500, state="EMERGENCY", stop=True)
    decision = _rescue(stage, taker=-5.0, wait=-20.0, stop=True, state="EMERGENCY")
    assert not decision.authorized
    assert not decision.park
    assert decision.reason == "SCORE_STATE_PROTECTED"


def test_rescue_requires_adverse_evidence():
    stage = _stage(2, failed=8, age=16)
    decision = _rescue(stage, taker=-5.0, wait=-10.0, markout=0.5, risk=0.1)
    assert not decision.authorized
    assert not decision.park
    assert decision.reason == "NO_ADVERSE_EVIDENCE"


def test_rescue_requires_taker_utility_to_beat_wait():
    stage = _stage(2, failed=8, age=16)
    decision = _rescue(stage, taker=-7.0, wait=-6.0)
    assert not decision.authorized
    assert decision.reason == "WAIT_EV_BETTER"


def test_v417_price_crossing_uses_hard_minus_twelve_floor_immediately():
    stage = _stage(2, failed=8, age=16)
    assert not stage.hard_window
    decision = _rescue(stage, taker=-9.0, wait=-20.0)
    assert decision.authorized
    assert not decision.park
    assert decision.allowed_loss_floor_bps == -12.0
    assert decision.reason == "PRICE_HARD_WINDOW_RESCUE"


def test_park_refresh_is_bounded_by_interval_touch_and_hard_risk():
    due, reason = parked_refresh_due(
        current_tick=110, last_refresh_tick=100,
        current_mid=100.01, last_mid=100.0,
        refresh_interval_ticks=20, material_touch_move_bps=8.0,
    )
    assert not due and reason == "PARKED_COOLDOWN"

    due, reason = parked_refresh_due(
        current_tick=120, last_refresh_tick=100,
        current_mid=100.01, last_mid=100.0,
        refresh_interval_ticks=20, material_touch_move_bps=8.0,
    )
    assert due and reason == "INTERVAL"

    due, reason = parked_refresh_due(
        current_tick=105, last_refresh_tick=100,
        current_mid=100.10, last_mid=100.0,
        refresh_interval_ticks=20, material_touch_move_bps=8.0,
    )
    assert due and reason == "TOUCH_MOVE"

    due, reason = parked_refresh_due(
        current_tick=101, last_refresh_tick=100,
        current_mid=100.0, last_mid=100.0,
        refresh_interval_ticks=20, material_touch_move_bps=8.0,
        hard_risk=True,
    )
    assert due and reason == "HARD_RISK"



def test_config_cannot_widen_absolute_loss_floor_beyond_minus_twelve():
    stage = classify_liveness_stage(
        observations_remaining=2, required_observations=3,
        failed_exit_count=20, inventory_age=50, inventory_state="EMERGENCY",
        stop_loss_hit=True, hard_taker_floor_bps=-50.0,
        soft_taker_floor_bps=-20.0, maker_floor_bps=-20.0,
    )
    assert stage.hard_floor_bps == -12.0
    assert stage.taker_floor_bps == -12.0
    assert stage.maker_floor_bps == -12.0

def test_strategy_contract_freezes_v41214_guard_and_other_engines():
    root = Path(__file__).parents[1]
    src = (root / "agents" / "strategy" / "Strategy1_Research.py").read_text()
    guard = (root / "agents" / "strategy" / "research_contract_guard.py").read_text()
    assert 'RESEARCH_POLICY_VERSION = "simplified_kappa_productivity_v4_13_3"' in src
    assert 'RESEARCH_INVENTORY_LIVENESS_VERSION = INVENTORY_LIVENESS_VERSION' in src
    assert 'CONTRACT_GUARD_VERSION = "authoritative_l1_contract_guard_v4_12_14"' in guard
    assert 'research_max_active_open_books' in src
    assert 'research_max_total_open_books' in src
    assert 'research_max_total_abs_base' in src
    assert 'INVENTORY_LIVENESS_BOUNDED_RESCUE' in src
    assert 'PARK_POSITION' in src and 'UNPARK_POSITION' in src
    assert 'park_hard_risk = bool((qty / max_inv) >= close_thr)' in src
    assert INVENTORY_LIVENESS_VERSION == "inventory_state_decoupling_v4_12_18"
