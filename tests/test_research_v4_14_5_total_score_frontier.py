# SPDX-License-Identifier: MIT
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY_DIR))

from research_execution_lanes import (
    LaneBook,
    LANE_COMPLETION,
    LANE_COVERAGE,
    LANE_REALIZATION,
    classify_execution_lane,
    select_lane_candidates,
    normalize_lane_budgets,
)
from research_total_score_frontier import (
    TOTAL_SCORE_FRONTIER_VERSION,
    PHASE_IGNITION,
    PHASE_SURVIVAL,
    PHASE_FRONTIER,
    REASON_FRONTIER,
    REASON_ROTATE,
    apply_total_score_frontier,
    phase_budget_tuple,
    phase_for_qualified,
    scoring_pivot_indices,
)

SRC = (STRATEGY_DIR / "Strategy1_Research.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_strategy1_research_test_multi.sh").read_text(encoding="utf-8")


def _row(book_id, obs, *, kappa=None, tier="UNKNOWN", pnl=0.1, pos=3, neg=0):
    return LaneBook(
        book_id=book_id,
        rolling_observation_count=obs,
        observations_remaining=max(0, 3 - obs),
        kappa_eligible=obs >= 3,
        economics_ok=True,
        completion_ev_ok=True,
        completion_ev_known=True,
        maker_ev=1.0,
        maker_ev_known=True,
        raw_kappa=kappa,
        recent_realized_pnl=pnl,
        rolling_positive_count=pos,
        rolling_negative_count=neg,
        kappa_productivity_tier=tier,
    )


def test_version_and_phase_boundaries():
    assert TOTAL_SCORE_FRONTIER_VERSION == "total_score_frontier_v4_14_5"
    assert phase_for_qualified(0) == PHASE_IGNITION
    assert phase_for_qualified(40) == PHASE_IGNITION
    assert phase_for_qualified(41) == PHASE_SURVIVAL
    assert phase_for_qualified(79) == PHASE_SURVIVAL
    assert phase_for_qualified(80) == PHASE_FRONTIER


def test_fixed_phase_budgets_never_collapse_ignition_coverage():
    assert phase_budget_tuple(PHASE_IGNITION) == (4, 3, 3, 1)
    assert phase_budget_tuple(PHASE_SURVIVAL) == (2, 5, 3, 1)
    assert phase_budget_tuple(PHASE_FRONTIER) == (2, 4, 3, 1)


def test_scoring_pivot_math_matches_effective_80_book_vector():
    assert scoring_pivot_indices(39) is None
    assert scoring_pivot_indices(40) is None
    assert scoring_pivot_indices(41) == (0, 1)
    assert scoring_pivot_indices(50) == (9, 10)
    assert scoring_pivot_indices(59) == (18, 19)
    assert scoring_pivot_indices(79) == (38, 39)
    assert scoring_pivot_indices(80) == (39, 40)
    assert scoring_pivot_indices(81) == (40, 40)


def test_ignition_one_away_owns_completion_but_qualified_does_not():
    rows = [_row(1, 2), _row(2, 3, kappa=1.0), _row(3, 0)]
    planned, plan = apply_total_score_frontier(rows, qualified_books=10)
    by_id = {r.book_id: r for r in planned}
    assert plan.phase == PHASE_IGNITION
    assert by_id[1].total_score_due
    assert classify_execution_lane(by_id[1]) == LANE_COMPLETION
    assert not by_id[2].total_score_due
    assert not by_id[2].density_due
    assert not by_id[2].core_candidate
    assert not by_id[2].recycling_candidate
    assert not by_id[2].core_probe_candidate
    assert classify_execution_lane(by_id[2]) == LANE_COVERAGE
    assert classify_execution_lane(by_id[3]) == LANE_COVERAGE


def test_inefficient_one_away_rotates_out_of_completion():
    rows = [_row(1, 2, tier="INEFFICIENT")]
    planned, _ = apply_total_score_frontier(rows, qualified_books=20)
    row = planned[0]
    assert not row.total_score_due
    assert row.total_score_reason == REASON_ROTATE
    assert classify_execution_lane(row) == LANE_COVERAGE


def test_survival_frontier_is_narrow_not_all_qualified_books():
    # 50 qualified -> pivot valid ranks 9/10. With band=2 only five-ish rows
    # around that lower-tail pivot receive explicit qualified repair pressure.
    rows = [_row(i, 3, kappa=-2.0 + 0.08 * i) for i in range(50)]
    planned, plan = apply_total_score_frontier(rows, qualified_books=50, frontier_band=2)
    due = [r for r in planned if r.total_score_due and r.total_score_reason == REASON_FRONTIER]
    assert plan.phase == PHASE_SURVIVAL
    assert plan.pivot_low == 9 and plan.pivot_high == 10
    assert 1 <= len(due) <= 6
    assert len(due) < len(rows) // 4


def test_frontier_phase_does_not_reward_fresh_breadth_over_qualified_economics():
    qualified = _row(1, 3, kappa=1.0)
    fresh = _row(2, 0)
    planned, plan = apply_total_score_frontier([qualified, fresh], qualified_books=80)
    by_id = {r.book_id: r for r in planned}
    assert plan.phase == PHASE_FRONTIER
    assert by_id[2].total_score_value < by_id[1].total_score_value


def test_live_selection_ignores_legacy_core_specials_when_total_score_annotated():
    rows = [
        _row(1, 2, kappa=0.0),
        _row(2, 1, kappa=0.0),
        _row(3, 3, kappa=-0.1),
    ]
    planned, _ = apply_total_score_frontier(rows, qualified_books=45)
    # Re-introduce bogus legacy flags to prove live total-score annotation wins.
    planned[2] = planned[2].__class__(**{**planned[2].__dict__, "core_candidate": True, "density_due": True})
    allocation = select_lane_candidates(
        planned,
        normalize_lane_budgets(coverage_slots=2, completion_slots=2, realization_slots=0, shared_overflow_slots=0),
        max_candidates=4,
    )
    # Qualified legacy-core book must not steal completion merely via old flags.
    assert 3 not in allocation.by_lane[LANE_COMPLETION]
    assert 1 in allocation.by_lane[LANE_COMPLETION]
    assert 2 in allocation.by_lane[LANE_COMPLETION]


def test_live_noncritical_legacy_refresh_cannot_bypass_total_score_authority():
    row = _row(7, 3, kappa=1.2)
    row = row.__class__(**{**row.__dict__, "needs_refresh": True, "deadline_critical": False})
    planned, plan = apply_total_score_frontier([row], qualified_books=10)
    live = planned[0]
    assert plan.phase == PHASE_IGNITION
    assert not live.total_score_due
    assert classify_execution_lane(live) == LANE_COVERAGE


def test_strategy_hidden_core_privileges_are_bound_to_total_score_due_set():
    src = SRC
    assert "self._research_total_score_due_ids = {" in src
    assert src.count('int(book_id) in (getattr(self, "_research_total_score_due_ids", set()) or set())') >= 2


def test_ignition_coverage_reserve_survives_the_global_candidate_cap():
    """Gate A item 3: completion/realization overflow may not eat COVERAGE's reserve.

    Shared overflow is spilled into REALIZATION/COMPLETION before the global
    candidate cap truncates the lanes, so a cap below ``total_cap`` silently
    turned IGNITION's documented 4-slot coverage reserve into 3.
    """
    cov, comp, real, over = phase_budget_tuple(PHASE_IGNITION)
    budgets = normalize_lane_budgets(
        coverage_slots=cov, completion_slots=comp,
        realization_slots=real, shared_overflow_slots=over,
    )
    rows = [LaneBook(book_id=100 + i, has_inventory=True, exit_urgency=0.5) for i in range(5)]
    rows += [
        _row(200 + i, 2) for i in range(6)
    ]
    rows += [
        LaneBook(
            book_id=300 + i, rolling_observation_count=0, observations_remaining=3,
            kappa_eligible=False, economics_ok=True, completion_ev_ok=True,
            maker_ev=1.0, maker_ev_known=True, is_uncovered=True,
        )
        for i in range(40)
    ]
    planned, plan = apply_total_score_frontier(rows, qualified_books=10)
    assert plan.phase == PHASE_IGNITION

    starved = select_lane_candidates(planned, budgets, max_candidates=10)
    assert starved.used[LANE_COVERAGE] == 3, "regression fixture no longer reproduces the defect"

    honored = select_lane_candidates(planned, budgets, max_candidates=budgets.total_cap)
    assert honored.used[LANE_COVERAGE] == cov
    assert honored.used[LANE_COMPLETION] == comp
    assert honored.used[LANE_REALIZATION] >= real

    # The strategy must raise the cap to total_cap rather than trust the launcher.
    assert "cap = max(int(cap), int(screen_budgets.total_cap))" in SRC


def test_launcher_candidate_count_covers_every_phase_total_cap():
    required = max(sum(phase_budget_tuple(p)) for p in (PHASE_IGNITION, PHASE_SURVIVAL, PHASE_FRONTIER))
    match = re.search(r"research_candidate_count=(\d+)", RUNNER)
    assert match is not None
    assert int(match.group(1)) >= required


def test_disabled_flag_does_not_leave_a_half_wired_hybrid_authority():
    """The enable flag must gate the annotation, not only the lane budgets.

    Annotating rows while serving legacy budgets would run the new lane
    authority against the old slot plan -- neither V4.14.4 nor V4.14.5.
    """
    gate = 'if bool(getattr(self, "research_total_score_frontier_enabled", True)):'
    assert SRC.count(gate) >= 2
    annotate = SRC.index("lane_rows, total_score_plan = apply_total_score_frontier(")
    assert SRC.rindex(gate, 0, annotate) > SRC.index("def _research_execution_lane_budgets")
    # A disabled authority must not report itself as IGNITION.
    assert '"phase", "DISABLED"' in SRC
    assert "TOTAL_SCORE_PHASE_IGNITION))" not in SRC


def test_entry_infeasible_book_never_becomes_total_score_due():
    """Retry-quarantined candidates must not collect exact-min/stale-TTL privileges."""
    one_away = _row(1, 2)
    blocked = one_away.__class__(**{**one_away.__dict__, "entry_feasible": False})
    planned, _ = apply_total_score_frontier([blocked], qualified_books=10)
    assert not planned[0].total_score_due
    assert classify_execution_lane(planned[0]) == LANE_COVERAGE

    critical = _row(2, 3, kappa=1.0)
    critical = critical.__class__(**{
        **critical.__dict__,
        "needs_refresh": True, "deadline_critical": True, "entry_feasible": False,
    })
    planned, _ = apply_total_score_frontier([critical], qualified_books=50)
    assert not planned[0].total_score_due


def test_inefficient_rotated_counts_only_score_scheduler_rotations():
    inventory = LaneBook(
        book_id=1, has_inventory=True, kappa_productivity_tier="INEFFICIENT",
        rolling_observation_count=0, observations_remaining=3, kappa_eligible=False,
    )
    flat = _row(2, 1, tier="INEFFICIENT")
    _, inventory_only = apply_total_score_frontier([inventory], qualified_books=10)
    assert inventory_only.inefficient_rotated == 0
    _, both = apply_total_score_frontier([inventory, flat], qualified_books=10)
    assert both.inefficient_rotated == 1


def test_legacy_completion_sort_key_has_no_stranded_docstring():
    lanes = (STRATEGY_DIR / "research_execution_lanes.py").read_text(encoding="utf-8")
    body = lanes.split("def completion_sort_key(")[1].split("\ndef realization_sort_key(")[0]
    assert body.count('"""') == 2, "legacy branch still carries a no-op string expression"
