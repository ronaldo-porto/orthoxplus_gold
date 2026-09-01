from hashlib import sha256
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "agents" / "strategy"))

from research_clean_authority import (
    CLEAN_AUTHORITY_VERSION,
    execution_reject_cooldown,
)
from research_scheduler_retry import SchedulerRetryGuard
from research_execution_lanes import (
    COMPLETION_ECONOMICS_VERSION,
    LANE_COMPLETION,
    LANE_COVERAGE,
    LaneBook,
    admit_lane_candidate,
    classify_execution_lane,
    completion_sort_key,
    normalize_lane_budgets,
    select_lane_candidates,
)
from research_fresh_feasibility import (
    DEFAULT_CHEAP_SHORTLIST,
    evaluate_fresh_feasibility,
    reserve_rank_score,
    shortlist_fresh_candidates,
)
from research_inventory_liveness import counts_against_productive_open_cap
from research_lane_funnel import compact_log, empty_funnel, bump, bump_reject
from research_lifecycle_ev import (
    RESEARCH_LIFECYCLE_ENTRY_VERSION,
    required_entry_ev,
)
from research_projected_completion import (
    PROJECTED_COMPLETION_VERSION,
    REASON_HEALTHY,
    REASON_UNHEALTHY,
    project_completion_quality,
)
from research_realnet_exit_authority import (
    ACTION_PARK,
    ACTION_TAKER_ESCAPE,
    REALNET_EXIT_AUTHORITY_VERSION,
    arbitrate_realnet_exit,
)
from research_score_ev import SCORE_EV_VERSION, compute_score_ev
from research_total_score_frontier import (
    TOTAL_SCORE_FRONTIER_VERSION,
    apply_total_score_frontier,
    phase_budget_tuple,
    scoring_pivot_indices,
)

STRATEGY = (ROOT / "agents/strategy/Strategy1_Research.py").read_text()
BASE = ROOT / "agents/strategy/BaseStrategy.py"
ADAPTIVE = ROOT / "agents/strategy/AdaptiveAgent.py"
VALIDATOR_TRADE = ROOT / "taos/im/validator/trade.py"
VALIDATOR_TRADE_SHA256 = "137a4a7f26de9395a0028539a95411992c6ed0fa16ddd21682c04838121af0b8"


def _ev(**kwargs):
    defaults = dict(
        book=1,
        side="BUY",
        alpha=0.30,
        fill_prob_old=0.80,
        learned_actionable_p=0.50,
        learned_actionable_samples=20,
        spread_capture_bps=2.0,
        fees_bps=0.5,
        markout_mean_bps=0.0,
        markout_samples=20,
        realized_observation_count=0,
        required=3,
        min_trading_ev=0.0,
    )
    defaults.update(kwargs)
    return compute_score_ev(**defaults)


def test_release_versions_and_pipeline_contract():
    assert 'RESEARCH_POLICY_VERSION = "simplified_hybrid_authority_v4_16_1"' in STRATEGY
    assert 'RESEARCH_ENGINE_VERSION = "simplified_hybrid_authority_v4_16_1"' in STRATEGY
    assert 'RESEARCH_ENGINE_REVISION = "simplified_hybrid_authority_v4_16_1"' in STRATEGY
    assert TOTAL_SCORE_FRONTIER_VERSION == "total_score_frontier_v4_15_2"
    assert COMPLETION_ECONOMICS_VERSION == "total_score_completion_v4_15_2"
    assert RESEARCH_LIFECYCLE_ENTRY_VERSION == "lifecycle_ev_v4_16_0"
    assert PROJECTED_COMPLETION_VERSION == "projected_completion_v4_15_2"
    assert SCORE_EV_VERSION == "simplified_hybrid_authority_v4_16_0"
    assert DEFAULT_CHEAP_SHORTLIST == 22
    assert "shortlist_fresh_candidates" in STRATEGY
    assert "project_completion_quality" in STRATEGY
    assert "[S1R_FUNNEL]" in STRATEGY
    assert "apply_total_score_frontier" in STRATEGY
    for legacy in (
        "update_sticky_cohort",
        "_research_productivity_core_ids",
        "_research_flywheel_core_ids",
    ):
        assert legacy not in STRATEGY


def test_one_away_healthy_candidate_enters_completion():
    row = LaneBook(
        book_id=1,
        rolling_observation_count=2,
        observations_remaining=1,
        economics_ok=True,
        completion_ev_ok=True,
        lifecycle_ev=0.20,
        projected_completion_healthy=True,
        projected_completion_reason=REASON_HEALTHY,
        projected_completion_quality=0.80,
    )
    planned, _ = apply_total_score_frontier([row], qualified_books=10)
    assert planned[0].total_score_due is True
    assert classify_execution_lane(planned[0]) == LANE_COMPLETION
    alloc = select_lane_candidates(
        planned,
        normalize_lane_budgets(coverage_slots=0, completion_slots=1, realization_slots=0, shared_overflow_slots=0),
        max_candidates=1,
    )
    assert alloc.by_lane[LANE_COMPLETION] == [1]


def test_one_away_projected_negative_is_not_forced_completion():
    bad = project_completion_quality(
        observations_remaining=1,
        realized_sum=-0.40,
        realized_count=2,
        expected_next_rt_pnl=-0.10,
        lifecycle_ev=-0.12,
        p_fill=0.40,
    )
    assert bad.projected_completion_healthy is False
    assert bad.projected_completion_reason == REASON_UNHEALTHY
    row = LaneBook(
        book_id=2,
        rolling_observation_count=2,
        observations_remaining=1,
        economics_ok=True,
        completion_ev_ok=True,
        lifecycle_ev=-0.12,
        projected_completion_healthy=False,
        projected_completion_reason=REASON_UNHEALTHY,
        projected_completion_quality=0.10,
    )
    planned, _ = apply_total_score_frontier([row], qualified_books=10)
    assert planned[0].total_score_due is False
    assert classify_execution_lane(planned[0]) == LANE_COVERAGE


def test_healthy_two_away_ranks_below_healthy_one_away():
    one = LaneBook(
        book_id=1,
        rolling_observation_count=2,
        observations_remaining=1,
        economics_ok=True,
        completion_ev_ok=True,
        completion_ev_known=True,
        maker_ev=0.20,
        lifecycle_ev=0.20,
        projected_completion_healthy=True,
        projected_completion_reason=REASON_HEALTHY,
        projected_completion_quality=0.80,
        total_score_due=True,
        total_score_value=1.00,
    )
    two = LaneBook(
        book_id=2,
        rolling_observation_count=1,
        observations_remaining=2,
        economics_ok=True,
        completion_ev_ok=True,
        completion_ev_known=True,
        maker_ev=0.40,
        lifecycle_ev=0.40,
        projected_completion_healthy=True,
        projected_completion_reason=REASON_HEALTHY,
        projected_completion_quality=0.75,
        total_score_due=True,
        total_score_value=0.72,
    )
    planned, _ = apply_total_score_frontier([one, two], qualified_books=10)
    by_id = {row.book_id: row for row in planned}
    assert by_id[1].total_score_value > by_id[2].total_score_value
    assert completion_sort_key(by_id[1]) < completion_sort_key(by_id[2])
    alloc = select_lane_candidates(
        planned,
        normalize_lane_budgets(coverage_slots=0, completion_slots=1, realization_slots=0, shared_overflow_slots=0),
        max_candidates=1,
    )
    assert alloc.by_lane[LANE_COMPLETION] == [1]


def test_high_taker_probability_raises_entry_ev_bar():
    low = required_entry_ev(taker_exit_probability=0.30)
    high = required_entry_ev(taker_exit_probability=0.78)
    assert high > low
    weak_low = _ev(taker_exit_probability=0.30)
    weak_high = _ev(taker_exit_probability=0.78)
    # V4.16: required_entry_ev remains a diagnostic, not a live ScoreEV gate.
    assert weak_low.eligible is True
    assert weak_high.eligible is True
    assert weak_high.required_entry_ev <= 0.12 + 1e-12
    assert weak_high.reject_reason is None


def test_high_taker_probability_is_not_a_hard_veto():
    strong = _ev(
        spread_capture_bps=12.0,
        learned_actionable_p=0.80,
        taker_exit_probability=0.90,
    )
    assert strong.required_entry_ev >= 0.0
    assert strong.eligible is True
    assert strong.reject_reason is None
    a = required_entry_ev(taker_exit_probability=0.70)
    b = required_entry_ev(taker_exit_probability=0.71)
    assert b > a
    assert abs(b - a) < 0.05


def test_failed_primary_does_not_consume_lane_success_capacity():
    budgets = normalize_lane_budgets(
        coverage_slots=0, completion_slots=1, realization_slots=0, shared_overflow_slots=0,
    )
    used = {LANE_COMPLETION: 0}
    ok, reason = admit_lane_candidate(
        lane=LANE_COMPLETION, used=used, overflow_used=0, budgets=budgets,
    )
    assert ok is True
    assert reason is None
    assert used[LANE_COMPLETION] == 0
    blocked, cap = admit_lane_candidate(
        lane=LANE_COMPLETION, used={LANE_COMPLETION: 1}, overflow_used=0, budgets=budgets,
    )
    assert blocked is False
    assert cap == "KAPPA_COMPLETION_SLOT_CAP"


def test_good_reserve_backfills_failed_primary():
    rows = [
        LaneBook(
            book_id=i,
            rolling_observation_count=2,
            observations_remaining=1,
            economics_ok=True,
            completion_ev_ok=True,
            total_score_due=True,
            total_score_value=1.0 - 0.01 * i,
            lifecycle_ev=0.20,
            fresh_feasible=True,
            projected_completion_healthy=True,
            projected_completion_reason=REASON_HEALTHY,
            projected_completion_quality=0.80,
        )
        for i in range(1, 5)
    ]
    alloc = select_lane_candidates(
        rows,
        normalize_lane_budgets(coverage_slots=0, completion_slots=1, realization_slots=0, shared_overflow_slots=0),
        max_candidates=1,
    )
    assert alloc.by_lane[LANE_COMPLETION] == [1]
    assert alloc.pool_by_lane[LANE_COMPLETION][:3] == [1, 2, 3]
    budgets = alloc.budgets
    used = {LANE_COMPLETION: 0}
    # Primary 1 fails to place: used stays 0, reserve 2 is still admissible.
    ok, _ = admit_lane_candidate(
        lane=LANE_COMPLETION, used=used, overflow_used=0, budgets=budgets,
    )
    assert ok is True
    used[LANE_COMPLETION] = 1
    blocked, _ = admit_lane_candidate(
        lane=LANE_COMPLETION, used=used, overflow_used=0, budgets=budgets,
    )
    assert blocked is False


def test_lifecycle_ev_contains_no_score_completion_bonuses():
    one = _ev(book=1, realized_observation_count=2)
    fresh = _ev(book=2, realized_observation_count=0)
    assert one.completion_value > fresh.completion_value
    assert abs(one.lifecycle_ev - fresh.lifecycle_ev) < 1e-12
    expected = (
        one.trading_ev - one.dust_cost - one.inventory_cost
        - one.latency_cost - one.adverse_selection_risk
    )
    assert abs(one.lifecycle_ev - expected) < 1e-12
    assert one.lifecycle_ev == one.trading_ev - one.dust_cost - one.inventory_cost - one.latency_cost - one.adverse_selection_risk


def test_total_score_value_contains_no_trading_economics():
    one = _ev(book=1, realized_observation_count=2)
    fresh = _ev(book=2, realized_observation_count=0)
    assert abs(one.trading_ev - fresh.trading_ev) < 1e-12
    assert one.total_score_component == one.completion_value + one.activity_deficit_value
    assert abs(fresh.total_score_component - (fresh.completion_value + fresh.activity_deficit_value)) < 1e-12
    assert one.total_score_component > fresh.total_score_component


def test_hard_risk_v4144_exit_authority_unchanged():
    assert REALNET_EXIT_AUTHORITY_VERSION == "realnet_exit_authority_v4_14_4"
    assert "arbitrate_realnet_exit" in STRATEGY
    hard = arbitrate_realnet_exit(
        taker_net_bps=-20.0, maker_net_bps=3.0, maker_executable=True,
        failed_exit_count=0, inventory_age=0, liveness_park=True, liveness_floor_bps=-12.0,
    )
    assert hard.action == ACTION_TAKER_ESCAPE
    park = arbitrate_realnet_exit(
        taker_net_bps=-25.01, maker_net_bps=-5.0, maker_executable=True,
        failed_exit_count=100, inventory_age=100, adverse_evidence=True,
    )
    assert park.action == ACTION_PARK
    assert phase_budget_tuple("IGNITION") == (4, 3, 3, 1)
    assert scoring_pivot_indices(41) == (0, 1)


def test_dust_and_parked_inventory_do_not_consume_productive_capacity():
    assert not counts_against_productive_open_cap(
        has_inventory=True, is_liveness_parked=True, is_dust=False,
    )
    assert not counts_against_productive_open_cap(
        has_inventory=True, is_liveness_parked=False, is_dust=True,
    )
    assert counts_against_productive_open_cap(
        has_inventory=True, is_liveness_parked=False, is_dust=False,
    )
    dust = LaneBook(book_id=9, is_dust=True, has_inventory=True, fresh_feasible=True)
    parked_like = evaluate_fresh_feasibility(
        has_inventory=True, is_dust=True, is_hard_risk=False, entry_feasible=False,
    )
    assert parked_like.feasible is True
    assert dust.has_inventory and dust.is_dust
    assert "dust_first_seen_tick" in STRATEGY
    assert "dust_last_progress_tick" in STRATEGY
    assert "dust_compaction_attempts" in STRATEGY


def test_validator_and_frozen_agents_are_unchanged():
    digest = sha256(VALIDATOR_TRADE.read_bytes()).hexdigest()
    assert digest == VALIDATOR_TRADE_SHA256
    assert "Preserve EVERY timestamp" in VALIDATOR_TRADE.read_text(encoding="utf-8")
    base_src = BASE.read_text(encoding="utf-8")
    adaptive_src = ADAPTIVE.read_text(encoding="utf-8")
    assert "simplified_hybrid_authority_v4_16_0" not in base_src
    assert "simplified_hybrid_authority_v4_16_0" not in adaptive_src
    assert "simplified_hybrid_authority_v4_16_1" not in base_src
    assert "simplified_hybrid_authority_v4_16_1" not in adaptive_src
    assert "acquisition_quality_v4_15_2" not in base_src
    assert "acquisition_quality_v4_15_2" not in adaptive_src
    assert "projected_completion_v4_15_2" not in base_src
    assert "lane_funnel_v4_15_2" not in adaptive_src


def test_fresh_infeasible_book_does_not_take_primary_priority():
    good = LaneBook(
        book_id=1, observations_remaining=3, economics_ok=True, fresh_feasible=True,
        entry_feasible=True, lifecycle_ev=0.20, cheap_score=0.10, is_uncovered=True,
    )
    bad = LaneBook(
        book_id=2, observations_remaining=3, economics_ok=True, fresh_feasible=False,
        entry_feasible=False, lifecycle_ev=0.90, cheap_score=0.99, is_uncovered=True,
    )
    kept = shortlist_fresh_candidates([good, bad], cheap_shortlist=22)
    assert 1 in {row.book_id for row in kept}
    assert 2 not in {row.book_id for row in kept}
    alloc = select_lane_candidates(
        kept,
        normalize_lane_budgets(coverage_slots=1, completion_slots=0, realization_slots=0, shared_overflow_slots=0),
        max_candidates=1,
    )
    assert alloc.by_lane[LANE_COVERAGE] == [1]


def test_reserve_rank_uses_fresh_lifecycle_projected_and_score():
    weak = LaneBook(
        book_id=1, fresh_feasible=True, lifecycle_ev=0.01,
        projected_completion_quality=0.10, total_score_value=0.10, observations_remaining=1,
    )
    strong = LaneBook(
        book_id=2, fresh_feasible=True, lifecycle_ev=0.40,
        projected_completion_quality=0.80, total_score_value=1.00, observations_remaining=1,
    )
    assert reserve_rank_score(strong) > reserve_rank_score(weak)


def test_funnel_compact_record_has_lane_and_reject_fields():
    funnel = empty_funnel()
    bump(funnel, "KAPPA_COMPLETION", "lane_total_score_selected")
    bump(funnel, "COMPLETION", "lane_quote_created")
    bump_reject(funnel, "TTL_STALE")
    bump_reject(funnel, "NEGATIVE_EV")
    rec = compact_log(funnel, tick=12, lane="COMPLETION")
    assert rec["event"] == "S1R_FUNNEL"
    assert rec["lane"] == "COMPLETION"
    assert rec["selected"] == 1
    assert rec["quoted"] == 1
    assert rec["reject_ttl"] == 1
    assert rec["reject_negative_ev"] == 1
    assert rec["reject_required_entry_ev"] == 0
    assert rec["reject_lifecycle_ev"] == 0


def test_one_away_stays_due_when_entry_feasible_is_false():
    blocked = LaneBook(
        book_id=10,
        rolling_observation_count=2,
        observations_remaining=1,
        economics_ok=True,
        completion_ev_ok=True,
        entry_feasible=False,
        projected_completion_healthy=True,
        projected_completion_reason=REASON_HEALTHY,
        projected_completion_quality=0.80,
    )
    planned, _ = apply_total_score_frontier([blocked], qualified_books=36)
    assert planned[0].total_score_due is True
    assert classify_execution_lane(planned[0]) == LANE_COMPLETION
    alloc = select_lane_candidates(
        planned,
        normalize_lane_budgets(coverage_slots=0, completion_slots=1, realization_slots=0, shared_overflow_slots=0),
        max_candidates=1,
    )
    assert alloc.by_lane[LANE_COMPLETION] == [10]
    assert alloc.demand[LANE_COMPLETION] == 1


def test_negative_ev_retry_does_not_hide_one_away():
    g = SchedulerRetryGuard(negative_ev_base_ticks=8, toxic_base_ticks=16, max_cooldown_ticks=64)
    fp = ("NEGATIVE_EV", -3.0, 0.0, 0, 0)
    g.record_reject(7, tick=100, reason="NEGATIVE_EV", fingerprint=fp)
    assert g.should_skip(7, tick=101, fingerprint=fp).blocked
    assert not g.should_skip(
        7, tick=101, fingerprint=fp, observations_remaining=1,
    ).blocked
    assert not g.should_skip(
        7, tick=101, fingerprint=fp, observations_remaining=2,
    ).blocked
    assert g.should_skip(
        7, tick=101, fingerprint=fp, observations_remaining=3,
    ).blocked
    g.reset()
    toxic = SchedulerRetryGuard(negative_ev_base_ticks=8, toxic_base_ticks=16, max_cooldown_ticks=64)
    tfp = ("TOXIC", 0.0, 0.0, 1, 0)
    toxic.record_reject(8, tick=100, reason="TOXIC", fingerprint=tfp)
    assert toxic.should_skip(
        8, tick=101, fingerprint=tfp, observations_remaining=1,
    ).blocked


def test_edge_cooldown_exempt_for_completion_requote():
    assert CLEAN_AUTHORITY_VERSION == "clean_authority_v4_15_2_completion_due"
    rec = {"tick": 10, "reason": "NON_POSITIVE_EDGE"}
    assert not execution_reject_cooldown(
        rec, tick=11, observations_remaining=1,
    ).blocked
    assert execution_reject_cooldown(
        rec, tick=11, observations_remaining=3,
    ).blocked
    assert execution_reject_cooldown(
        {"tick": 10, "reason": "ZERO_ORDER_SIZE"}, tick=11, observations_remaining=1,
    ).blocked


def test_shortlist_keeps_infeasible_one_away():
    one = LaneBook(
        book_id=4,
        observations_remaining=1,
        rolling_observation_count=2,
        economics_ok=True,
        completion_ev_ok=True,
        entry_feasible=False,
        fresh_feasible=False,
        projected_completion_healthy=True,
        cheap_score=0.01,
    )
    coverage = LaneBook(
        book_id=5,
        observations_remaining=3,
        economics_ok=True,
        fresh_feasible=True,
        entry_feasible=True,
        cheap_score=0.90,
        is_uncovered=True,
    )
    kept = shortlist_fresh_candidates([one, coverage], cheap_shortlist=22)
    ids = {row.book_id for row in kept}
    assert 4 in ids
    assert 5 in ids
