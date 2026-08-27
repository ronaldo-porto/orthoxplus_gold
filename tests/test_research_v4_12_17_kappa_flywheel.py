from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY_DIR))

from research_inventory_liveness import classify_liveness_stage, evaluate_bounded_rescue
from research_execution_lanes import (
    LANE_COMPLETION,
    LaneBook,
    apply_kappa_conversion_pressure_gate,
    classify_execution_lane,
    completion_sort_key,
)
from research_kappa_flywheel import (
    KAPPA_FLYWHEEL_VERSION,
    PHASE_BOOTSTRAP,
    PHASE_BREADTH,
    PHASE_DENSITY,
    density_state,
    flywheel_phase,
    note_realized_pnl_event,
    phase_density_target,
    rolling_book_economics,
    sanitize_realized_pnl_events,
)
from research_quote_hysteresis import (
    ONE_AWAY_CONVERSION_TTL_VERSION,
    one_away_stale_completion_ttl,
)

SRC = (STRATEGY_DIR / "Strategy1_Research.py").read_text(encoding="utf-8")
LIVENESS_SRC = (STRATEGY_DIR / "research_inventory_liveness.py").read_text(encoding="utf-8")
LAUNCHER = (ROOT / "run_strategy1_research_test_multi.sh").read_text(encoding="utf-8")


def test_version_contract_and_linux_launcher_are_v417():
    assert KAPPA_FLYWHEEL_VERSION == "kappa_flywheel_v4_12_18"
    assert 'RESEARCH_POLICY_VERSION = "simplified_kappa_productivity_v4_13_2"' in SRC
    assert 'RESEARCH_LANES_VERSION = "execution_lanes_v7_inventory_decoupled"' in SRC
    assert "\r" not in LAUNCHER


def test_event_driven_minus8_to_minus12_window_authorizes_now():
    stage = classify_liveness_stage(
        observations_remaining=2,
        failed_exit_count=3,
        inventory_age=5,
        inventory_state="NORMAL",
        stop_loss_hit=False,
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
    assert not stage.taker_rescue_armed
    assert not stage.hard_window
    decision = evaluate_bounded_rescue(
        stage,
        taker_net_bps=-9.1,
        wait_ev_bps=-10.0,
        expected_markout_bps=-2.0,
        adverse_selection_risk=0.5,
        stop_loss_hit=False,
        inventory_state="NORMAL",
        min_ev_advantage_bps=0.5,
    )
    assert decision.authorized
    assert not decision.park
    assert decision.allowed_loss_floor_bps == -12.0
    assert decision.reason == "PRICE_HARD_WINDOW_RESCUE"


def test_beyond_minus12_parks_not_dumps():
    stage = classify_liveness_stage(
        observations_remaining=2,
        failed_exit_count=20,
        inventory_age=30,
        inventory_state="EMERGENCY",
        stop_loss_hit=True,
    )
    decision = evaluate_bounded_rescue(
        stage,
        taker_net_bps=-12.1,
        wait_ev_bps=-100,
        expected_markout_bps=-5,
        adverse_selection_risk=1,
        stop_loss_hit=True,
        inventory_state="EMERGENCY",
    )
    assert not decision.authorized and decision.park
    assert decision.reason == "LOSS_BEYOND_HARD_FLOOR"


def test_park_is_classification_not_six_slot_blocker():
    assert 'reason="PARK_CAP"' not in SRC
    assert "Never refuse to park book #7" in SRC


def test_total_headroom_pressure_preserves_one_exploration_slot():
    rows = [
        LaneBook(book_id=1, observations_remaining=1, economics_ok=True),
        LaneBook(book_id=2, observations_remaining=2, economics_ok=True),
        LaneBook(book_id=10, observations_remaining=3, is_uncovered=True, economics_ok=True, maker_ev=2.0, maker_ev_known=True),
        LaneBook(book_id=11, observations_remaining=3, is_uncovered=True, economics_ok=True, maker_ev=1.0, maker_ev_known=True),
        LaneBook(book_id=12, observations_remaining=3, is_uncovered=True, economics_ok=True, maker_ev=0.5, maker_ev_known=True),
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
    by_id = {row.book_id: row for row in gated}
    assert productive == 2
    assert reason == "TOTAL_HEADROOM"
    assert by_id[10].entry_feasible
    assert not by_id[11].entry_feasible and not by_id[12].entry_feasible
    assert suppressed == {11, 12}


def test_park_count_alone_no_longer_triggers_pressure():
    rows = [
        LaneBook(book_id=1, observations_remaining=1),
        LaneBook(book_id=2, observations_remaining=3, is_uncovered=True),
    ]
    gated, suppressed, productive, reason = apply_kappa_conversion_pressure_gate(
        rows,
        parked_open_books=12,
        max_parked_open_books=6,
        total_open_books=2,
        max_total_open_books=12,
        reserve_total_slots=3,
        enabled=True,
    )
    assert productive == 1
    assert reason == "NO_PRESSURE"
    assert not suppressed
    assert all(row.entry_feasible for row in gated)


def test_qualified_density_book_reenters_completion_lane():
    row = LaneBook(
        book_id=42,
        observations_remaining=0,
        score_qualified=True,
        density_due=True,
        economics_ok=True,
        rolling_observation_count=5,
        density_state="QUALIFIED_LOW_DENSITY",
    )
    assert classify_execution_lane(row) == LANE_COMPLETION


def test_flywheel_phase_and_density_targets():
    assert flywheel_phase(0) == PHASE_BOOTSTRAP
    assert flywheel_phase(40) == PHASE_BOOTSTRAP
    assert flywheel_phase(41) == PHASE_BREADTH
    assert flywheel_phase(79) == PHASE_BREADTH
    assert flywheel_phase(80) == PHASE_DENSITY
    assert phase_density_target(PHASE_BOOTSTRAP) == 6
    assert phase_density_target(PHASE_BREADTH) == 12
    assert phase_density_target(PHASE_DENSITY) == 50
    assert density_state(realized_observations=3, required_observations=3) == "QUALIFIED_LOW_DENSITY"
    assert density_state(realized_observations=20, required_observations=3) == "QUALIFIED_DEVELOPING"
    assert density_state(realized_observations=60, required_observations=3) == "QUALIFIED_CORE"


def test_rolling_realized_pnl_is_restart_serializable_and_authoritative():
    events = {}
    events = note_realized_pnl_event(events, book_id=7, timestamp=100, realized_pnl=0.10, now=100, lookback_ns=1000)
    events = note_realized_pnl_event(events, book_id=7, timestamp=200, realized_pnl=-0.03, now=200, lookback_ns=1000)
    events = note_realized_pnl_event(events, book_id=7, timestamp=300, realized_pnl=0.02, now=300, lookback_ns=1000)
    stats = rolling_book_economics(events, 7, now=300, lookback_ns=1000)
    assert stats.nonzero_count == 3
    assert stats.positive_count == 2 and stats.negative_count == 1
    assert abs(stats.realized_sum - 0.09) < 1e-12
    raw = {"7": [[ts, pnl] for ts, pnl in events[7]]}
    restored = sanitize_realized_pnl_events(raw)
    assert restored == events
    assert 'payload["rolling_realized_pnl_events"]' in SRC
    assert "_research_restore_realized_pnl_events(disk)" in SRC


def test_one_away_velocity_stale_ttl_is_short_again():
    ttl, reason, used = one_away_stale_completion_ttl(
        chosen_ttl_ms=None,
        ttl_reason="STALE",
        completion_candidate=True,
        completion_samples=2,
        completion_target=3,
        trading_ev=0.05,
        market_regime="QUIET",
        min_ttl_ms=250.0,
        stale_ttl_ms=900.0,
    )
    assert ONE_AWAY_CONVERSION_TTL_VERSION == "one_away_velocity_stale_ttl_v4_12_17"
    assert used and ttl == 250.0
    assert reason == "ONE_AWAY_VELOCITY_STALE_SHORT"


def test_touch_aggressive_maker_is_not_reclamped_to_breakeven():
    assert "if liveness_maker_active:\n            maker_px = float(raw_maker_px)" in SRC
    assert 'reason="TOUCH_MAKER_BEYOND_FLOOR"' in SRC
    assert "touch_maker_blocked=int(bool(liveness_touch_maker_blocked))" in SRC


def test_maker_rebate_is_explicit_kappa_tie_breaker():
    rebate = LaneBook(
        book_id=1, observations_remaining=1, score_pnl_ready=True,
        flywheel_priority=1.0, recent_realized_pnl=1.0, maker_fee_bps=-8.0,
    )
    fee = LaneBook(
        book_id=2, observations_remaining=1, score_pnl_ready=True,
        flywheel_priority=1.0, recent_realized_pnl=1.0, maker_fee_bps=1.0,
    )
    assert completion_sort_key(rebate) < completion_sort_key(fee)
