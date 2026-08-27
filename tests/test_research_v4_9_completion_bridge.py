# SPDX-License-Identifier: MIT
"""V4.10 Completion/INACTIVE bridge regression tests."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY_DIR))

from research_execution_lanes import (
    LANE_COMPLETION,
    LANE_COVERAGE,
    LaneAllocation,
    LaneBudgets,
    LaneBook,
    completion_sort_key,
    normalize_lane_budgets,
    score_acquisition_granted,
    score_acquisition_grants,
    score_acquisition_mode,
    select_lane_candidates,
)

RESEARCH_SRC = (STRATEGY_DIR / "Strategy1_Research.py").read_text(encoding="utf-8")
LAUNCHER_SRC = (ROOT / "run_strategy1_research_test_multi.sh").read_text(encoding="utf-8")


def test_policy_version_and_lane_defaults_are_v410():
    assert 'RESEARCH_POLICY_VERSION = "simplified_kappa_productivity_v4_13_9"' in RESEARCH_SRC
    budgets = normalize_lane_budgets()
    assert budgets.coverage_slots == 6
    assert budgets.completion_slots == 6
    assert budgets.realization_slots == 4
    assert budgets.shared_overflow_slots == 4
    assert ("research_coverage_slots=6 research_completion_slots=6" in LAUNCHER_SRC or "research_coverage_slots=3 research_completion_slots=5" in LAUNCHER_SRC)


def test_completion_score_regime_enables_acquisition_without_old_overlay():
    assert score_acquisition_mode(score_regime="COMPLETION", scoring_overlay=None)
    assert score_acquisition_mode(score_regime="COVERAGE", scoring_overlay=None)
    assert score_acquisition_mode(score_regime="NORMAL", scoring_overlay="SCORING_PRESSURE")
    assert not score_acquisition_mode(score_regime="NORMAL", scoring_overlay=None)


def test_completion_bridge_is_selected_only_with_lane_allocation():
    allocation = LaneAllocation(
        by_lane={LANE_COMPLETION: [4, 17], LANE_COVERAGE: [93]},
        budgets=LaneBudgets(coverage_slots=6, completion_slots=6, realization_slots=4, shared_overflow_slots=4),
    )
    assert score_acquisition_granted(
        4, allocation=allocation, score_regime="COMPLETION", scoring_overlay=None,
    )
    assert score_acquisition_granted(
        93, allocation=allocation, score_regime="COMPLETION", scoring_overlay=None,
    )
    assert not score_acquisition_granted(
        57, allocation=allocation, score_regime="COMPLETION", scoring_overlay=None,
    )
    assert score_acquisition_grants(allocation) == {4, 17, 93}


def test_completion_without_lane_allocation_fails_closed():
    # Independent COMPLETION must never unlock all 128 inactive books when the
    # candidate allocator is unavailable.
    assert not score_acquisition_granted(
        4, allocation=None, score_regime="COMPLETION", scoring_overlay=None,
    )
    # Preserve the pre-V4.9 explicit overlay fallback for deployments with the
    # fast lane scheduler intentionally disabled.
    assert score_acquisition_granted(
        4, allocation=None, score_regime="COVERAGE", scoring_overlay="SCORING_PRESSURE",
    )


def test_lane_completion_order_is_one_away_then_two_away():
    one = LaneBook(book_id=9, observations_remaining=1, cheap_score=0.1)
    two = LaneBook(book_id=8, observations_remaining=2, cheap_score=99.0)
    assert completion_sort_key(one) < completion_sort_key(two)


def test_quiet_inactive_completion_candidates_receive_completion_capacity():
    completion_books = [
        LaneBook(
            book_id=i,
            is_inactive=True,
            observations_remaining=(1 if i < 3 else 2),
            cheap_score=float(100 - i),
            economics_ok=True,
        )
        for i in range(20)
    ]
    coverage_books = [
        LaneBook(
            book_id=100 + i,
            is_inactive=True,
            is_uncovered=True,
            observations_remaining=3,
            cheap_score=float(50 - i),
            economics_ok=True,
        )
        for i in range(23)
    ]
    books = completion_books + coverage_books
    allocation = select_lane_candidates(
        books,
        LaneBudgets(coverage_slots=6, completion_slots=6, realization_slots=4, shared_overflow_slots=4),
    )
    # With coverage demand present and no realization demand, completion gets
    # 6 reserved + 4 unused realization slots + 4 shared overflow = 14.
    assert allocation.demand[LANE_COMPLETION] == 20
    assert allocation.demand[LANE_COVERAGE] == 23
    assert len(allocation.by_lane[LANE_COMPLETION]) == 14
    # The first three one-away books lead even when two-away books have high cheap scores.
    assert allocation.by_lane[LANE_COMPLETION][:3] == [0, 1, 2]


def test_strategy_uses_bridge_in_archetype_and_inactive_gate_and_filters_maintenance():
    assert "score_acquisition_granted(" in RESEARCH_SRC
    assert "score_acquisition_mode(" in RESEARCH_SRC
    assert "selection.maintenance_books = [" in RESEARCH_SRC
    assert "if int(bid) in acquisition_grants" in RESEARCH_SRC
    # Regression: the old exact overlay-only gate must not survive in the Research source.
    assert 'and overlay == "SCORING_PRESSURE"\n            and tier == "INACTIVE"' not in RESEARCH_SRC
