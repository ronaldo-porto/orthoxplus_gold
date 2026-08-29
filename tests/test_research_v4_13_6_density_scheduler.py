from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from research_execution_lanes import (
    COMPLETION_ECONOMICS_VERSION,
    LANE_COMPLETION,
    LANE_COVERAGE,
    LaneBook,
    LaneBudgets,
    classify_execution_lane,
    completion_sort_key,
    density_priority_budgets,
    select_lane_candidates,
)
from research_kappa_productivity import KAPPA_PRODUCTIVITY_VERSION

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_test_multi.sh").read_text()


def _positive(book_id: int, remaining: int, **kwargs) -> LaneBook:
    return LaneBook(
        book_id=book_id,
        observations_remaining=remaining,
        economics_ok=True,
        maker_ev=kwargs.pop("maker_ev", 0.05),
        maker_ev_known=True,
        completion_ev_known=True,
        completion_ev_ok=True,
        **kwargs,
    )


def test_v4136_version_contract():
    assert COMPLETION_ECONOMICS_VERSION == "completion_density_v4_13_6"
    assert KAPPA_PRODUCTIVITY_VERSION == "wide_kappa_productivity_v4_14_2"
    assert 'RESEARCH_POLICY_VERSION = "total_score_frontier_v4_14_5"' in SRC
    assert "research_completion_ev_cache_ticks=20" in LAUNCHER
    assert "research_density_priority_enabled=0" in LAUNCHER
    assert "research_total_score_frontier_enabled=1" in LAUNCHER


def test_known_negative_one_away_does_not_consume_completion_lane():
    row = LaneBook(
        book_id=1,
        observations_remaining=1,
        economics_ok=True,
        maker_ev=-0.05,
        maker_ev_known=True,
        completion_ev_known=True,
        completion_ev_ok=False,
    )
    assert classify_execution_lane(row) == LANE_COVERAGE


def test_unknown_one_away_remains_fail_open_for_rediscovery():
    row = LaneBook(
        book_id=2,
        observations_remaining=1,
        economics_ok=True,
        completion_ev_known=False,
        completion_ev_ok=True,
    )
    assert classify_execution_lane(row) == LANE_COMPLETION


def test_known_positive_one_away_leads_productive_core_then_two_away():
    one = _positive(10, 1, maker_ev=0.03)
    core = _positive(
        11, 0, maker_ev=0.10, core_candidate=True,
        kappa_productivity_tier="PRODUCTIVE", kappa_productivity_score=0.9,
    )
    two = _positive(12, 2, maker_ev=0.20)
    assert completion_sort_key(one) < completion_sort_key(core)
    assert completion_sort_key(core) < completion_sort_key(two)
    alloc = select_lane_candidates(
        [two, core, one],
        LaneBudgets(coverage_slots=0, completion_slots=3, realization_slots=0, shared_overflow_slots=0),
        max_candidates=3,
    )
    assert alloc.by_lane[LANE_COMPLETION] == [10, 11, 12]


def test_known_positive_beats_unknown_even_if_unknown_is_one_away():
    unknown = LaneBook(book_id=20, observations_remaining=1, economics_ok=True)
    core = _positive(
        21, 0, maker_ev=0.02, recycling_candidate=True,
        kappa_productivity_tier="PRODUCTIVE", kappa_productivity_score=0.5,
    )
    assert completion_sort_key(core) < completion_sort_key(unknown)


def test_density_demand_shifts_bootstrap_budget_from_coverage_to_completion():
    base = LaneBudgets(
        coverage_slots=4, completion_slots=3, realization_slots=3, shared_overflow_slots=1
    )
    shifted = density_priority_budgets(
        [_positive(30, 1), _positive(31, 2)],
        base,
        enabled=True,
        min_candidates=1,
        aggressive_coverage=True,
    )
    assert shifted.coverage_slots == 1
    assert shifted.completion_slots == 6
    assert shifted.realization_slots == 3
    assert shifted.shared_overflow_slots == 1


def test_known_negative_backlog_does_not_trigger_density_budget_shift():
    base = LaneBudgets(
        coverage_slots=4, completion_slots=3, realization_slots=3, shared_overflow_slots=1
    )
    negative = LaneBook(
        book_id=40,
        observations_remaining=1,
        economics_ok=True,
        maker_ev=-0.2,
        maker_ev_known=True,
        completion_ev_known=True,
        completion_ev_ok=False,
    )
    unchanged = density_priority_budgets(
        [negative], base, enabled=True, min_candidates=1, aggressive_coverage=True
    )
    assert unchanged == base


def test_recycling_bridge_is_preserved_but_does_not_preempt_positive_one_away():
    one = _positive(50, 1, maker_ev=0.03)
    bridge = LaneBook(
        book_id=51,
        observations_remaining=0,
        economics_ok=True,
        recycling_candidate=True,
        kappa_productivity_tier="PRODUCTIVE",
        kappa_productivity_score=0.5,
        completion_ev_known=False,
        completion_ev_ok=True,
    )
    other = LaneBook(book_id=52, observations_remaining=1, economics_ok=True)
    alloc = select_lane_candidates(
        [other, bridge, one],
        LaneBudgets(coverage_slots=0, completion_slots=2, realization_slots=0, shared_overflow_slots=0),
        max_candidates=2,
    )
    assert alloc.by_lane[LANE_COMPLETION] == [50, 51]
