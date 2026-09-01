from pathlib import Path

from research_clean_authority import (
    CLEAN_AUTHORITY_VERSION,
    execution_reject_cooldown,
    posterior_taker_exit_probability,
)
from research_execution_lanes import (
    LANE_COMPLETION,
    LANE_COVERAGE,
    LaneBook,
    normalize_lane_budgets,
    select_lane_candidates,
)
from research_execution_quality import ExecutionQualitySnapshot
from research_rolling_economics import note_realized_pnl_event, rolling_book_economics
from research_scheduler_retry import SchedulerRetryGuard
from research_total_score_frontier import (
    TOTAL_SCORE_FRONTIER_VERSION,
    PHASE_IGNITION,
    PHASE_SURVIVAL,
    PHASE_FRONTIER,
    apply_total_score_frontier,
    phase_budget_tuple,
    scoring_pivot_indices,
)

ROOT = Path(__file__).parents[1]
STRATEGY = (ROOT / "agents/strategy/Strategy1_Research.py").read_text()
LANES = (ROOT / "agents/strategy/research_execution_lanes.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_test_multi.sh").read_text()


def test_release_versions_and_single_authority_contract():
    assert 'RESEARCH_POLICY_VERSION = "simplified_hybrid_authority_v4_16_1"' in STRATEGY
    assert TOTAL_SCORE_FRONTIER_VERSION == "total_score_frontier_v4_15_2"
    assert "apply_total_score_frontier" in STRATEGY
    for legacy in (
        "update_sticky_cohort",
        "apply_breadth_rotation_gate",
        "apply_kappa_conversion_pressure_gate",
        "density_priority_budgets",
        "execution_completion_candidate",
        "_research_productivity_core_ids",
        "_research_flywheel_core_ids",
        "_research_score_qualified_ids",
    ):
        assert legacy not in STRATEGY
    for legacy in ("density_due", "core_candidate", "recycling_candidate", "core_probe_candidate", "cohort_member"):
        assert legacy not in LANES


def test_removed_legacy_modules_and_launcher_knobs():
    strategy_dir = ROOT / "agents/strategy"
    for module in (
        "research_cohort.py",
        "research_kappa_flywheel.py",
        "research_kappa_productivity.py",
        "research_capacity_saturation.py",
    ):
        assert not (strategy_dir / module).exists()
    for knob in (
        "research_total_score_frontier_enabled",
        "research_enable_lane_scheduler",
        "research_coverage_slots",
        "research_completion_slots",
        "research_kappa_flywheel_enabled",
        "research_kappa_productivity_enabled",
        "research_cohort_size",
        "research_core_probe_enabled",
        "research_qualified_core_exact_min_enabled",
        "research_qualified_core_stale_ttl_enabled",
    ):
        assert knob not in LAUNCHER


def test_total_score_phase_geometry_and_lane_ownership():
    assert phase_budget_tuple(PHASE_IGNITION) == (4, 3, 3, 1)
    assert phase_budget_tuple(PHASE_SURVIVAL) == (2, 5, 3, 1)
    assert phase_budget_tuple(PHASE_FRONTIER) == (2, 4, 3, 1)
    assert scoring_pivot_indices(41) == (0, 1)
    assert scoring_pivot_indices(80) == (39, 40)
    rows = [
        LaneBook(book_id=1, rolling_observation_count=2, observations_remaining=1, economics_ok=True, completion_ev_ok=True),
        LaneBook(book_id=2, rolling_observation_count=0, observations_remaining=3, economics_ok=True, completion_ev_ok=True, is_uncovered=True),
    ]
    rows, plan = apply_total_score_frontier(rows, qualified_books=10)
    by_id = {r.book_id: r for r in rows}
    assert plan.phase == PHASE_IGNITION
    assert by_id[1].total_score_due is True
    assert by_id[2].total_score_due is False
    alloc = select_lane_candidates(rows, normalize_lane_budgets(coverage_slots=1, completion_slots=1, realization_slots=0, shared_overflow_slots=0), max_candidates=2)
    assert alloc.by_lane[LANE_COMPLETION] == [1]
    assert alloc.by_lane[LANE_COVERAGE] == [2]


def test_execution_cooldown_and_success_backfill_pool():
    assert CLEAN_AUTHORITY_VERSION == "clean_authority_v4_15_2_completion_due"
    assert execution_reject_cooldown({"tick": 10, "reason": "ZERO_ORDER_SIZE"}, tick=11).blocked
    assert not execution_reject_cooldown({"tick": 10, "reason": "TOXIC"}, tick=11).blocked
    rows = [LaneBook(book_id=i, total_score_phase="IGNITION", total_score_due=True, economics_ok=True) for i in range(1, 5)]
    alloc = select_lane_candidates(rows, normalize_lane_budgets(coverage_slots=0, completion_slots=1, realization_slots=0, shared_overflow_slots=0), max_candidates=1)
    assert alloc.by_lane[LANE_COMPLETION] == [1]
    assert alloc.pool_by_lane[LANE_COMPLETION] == [1, 2, 3, 4]


def test_ttl_and_low_fill_cooldown_exempt_one_away_and_two_away():
    rec_ttl = {"tick": 10, "reason": "TTL_STALE"}
    rec_fill = {"tick": 10, "reason": "LOW_FILL_PROBABILITY"}
    rec_size = {"tick": 10, "reason": "ZERO_ORDER_SIZE"}
    rec_edge = {"tick": 10, "reason": "NON_POSITIVE_EDGE"}
    for remaining in (1, 2):
        assert not execution_reject_cooldown(
            rec_ttl, tick=11, observations_remaining=remaining,
        ).blocked
        assert not execution_reject_cooldown(
            rec_fill, tick=11, observations_remaining=remaining,
        ).blocked
        assert execution_reject_cooldown(
            rec_size, tick=11, observations_remaining=remaining,
        ).blocked
        assert not execution_reject_cooldown(
            rec_edge, tick=11, observations_remaining=remaining,
        ).blocked
    assert execution_reject_cooldown(
        rec_ttl, tick=11, observations_remaining=3,
    ).blocked
    assert execution_reject_cooldown(
        rec_fill, tick=11, observations_remaining=0,
    ).blocked
    one_away = LaneBook(
        book_id=9,
        rolling_observation_count=2,
        observations_remaining=1,
        economics_ok=True,
        completion_ev_ok=True,
        entry_feasible=True,
    )
    blocked = LaneBook(
        book_id=10,
        rolling_observation_count=2,
        observations_remaining=1,
        economics_ok=True,
        completion_ev_ok=True,
        entry_feasible=False,
    )
    rows, _plan = apply_total_score_frontier([one_away, blocked], qualified_books=10)
    by_id = {r.book_id: r for r in rows}
    assert by_id[9].total_score_due is True
    assert by_id[10].total_score_due is True


def test_lifecycle_taker_probability_prices_observed_exit_path():
    p = posterior_taker_exit_probability(maker_exits=28, taker_exits=117, prior=0.30, prior_strength=8, min_samples=4, cap=0.90)
    assert 0.75 < p < 0.82
    assert posterior_taker_exit_probability(maker_exits=0, taker_exits=0, prior=0.30, min_samples=4) == 0.30


def test_rolling_economics_is_restart_safe_and_execution_quality_is_soft():
    events = {}
    events = note_realized_pnl_event(events, book_id=7, timestamp=100, realized_pnl=0.10, now=100, lookback_ns=1000)
    events = note_realized_pnl_event(events, book_id=7, timestamp=200, realized_pnl=-0.03, now=200, lookback_ns=1000)
    econ = rolling_book_economics(events, 7, now=200, lookback_ns=1000)
    assert econ.nonzero_count == 2
    assert abs(econ.realized_sum - 0.07) < 1e-12
    q = ExecutionQualitySnapshot(book_id=7, maker_quotes=20, maker_fills=4, contract_rejects=0, realized_pnl=0.07, positive_count=1, negative_count=1, maker_fee_bps=0.0, fill_rate_hint=0.2)
    assert q.execution_tier in {"PRODUCTIVE", "UNKNOWN", "INEFFICIENT"}
    # It is a ranking signal only: lane identity is determined by total_score_due.
    row = LaneBook(book_id=7, execution_quality_tier="INEFFICIENT", total_score_phase="IGNITION", total_score_due=True, economics_ok=True)
    alloc = select_lane_candidates([row], normalize_lane_budgets(coverage_slots=0, completion_slots=1, realization_slots=0, shared_overflow_slots=0), max_candidates=1)
    assert alloc.by_lane[LANE_COMPLETION] == [7]


def test_scheduler_retry_quarantine_remains_separate_from_mechanical_cooldown():
    g = SchedulerRetryGuard(negative_ev_base_ticks=8, toxic_base_ticks=16, max_cooldown_ticks=64)
    d = g.record_reject(7, tick=100, reason="NEGATIVE_EV", fingerprint=("NEGATIVE_EV", -3.0))
    assert d.blocked
    assert g.should_skip(7, tick=101, fingerprint=("NEGATIVE_EV", -3.0)).blocked
    assert not g.should_skip(
        7, tick=101, fingerprint=("NEGATIVE_EV", -3.0), observations_remaining=1,
    ).blocked
    g.reset()
    assert not g.should_skip(7, tick=101, fingerprint=("NEGATIVE_EV", -3.0)).blocked


def test_total_score_privileges_do_not_use_legacy_core_vocabulary():
    entry = (ROOT / "agents/strategy/research_entry_size.py").read_text()
    ttl = (ROOT / "agents/strategy/research_quote_hysteresis.py").read_text()
    assert "productive_qualified_core" not in STRATEGY
    assert "qualified_core_stale_completion_ttl" not in STRATEGY
    assert "QUALIFIED_CORE_EXACT_MIN" not in entry
    assert "qualified_core" not in ttl.lower()
    assert "research_total_score_exact_min_enabled" in STRATEGY
    assert "total_score_stale_completion_ttl" in STRATEGY
