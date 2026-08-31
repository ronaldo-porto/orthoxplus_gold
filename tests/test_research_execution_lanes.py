# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research execution lanes: coverage / completion / realization budgets."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)

from research_execution_lanes import (
    LANE_COMPLETION,
    LANE_COVERAGE,
    LANE_REALIZATION,
    LaneBook,
    admit_lane_candidate,
    classify_execution_lane,
    normalize_lane_budgets,
    select_lane_candidates,
)


def _budgets(coverage=8, completion=4, realization=4, overflow=4):
    return normalize_lane_budgets(
        coverage_slots=coverage,
        completion_slots=completion,
        realization_slots=realization,
        shared_overflow_slots=overflow,
    )


def test_inventory_cannot_consume_every_candidate_slot():
    books = [
        LaneBook(book_id=i, has_inventory=True, exit_urgency=0.20 + i * 0.01)
        for i in range(20)
    ]
    books.extend(
        LaneBook(book_id=100 + i, is_uncovered=True, cheap_score=0.80)
        for i in range(10)
    )
    books.append(
        LaneBook(book_id=200, observations_remaining=1, economics_ok=True, cheap_score=0.50, total_score_phase="IGNITION", total_score_due=True)
    )
    books.append(
        LaneBook(book_id=201, observations_remaining=2, economics_ok=True, cheap_score=0.40, total_score_phase="IGNITION", total_score_due=True)
    )
    result = select_lane_candidates(books, _budgets())
    log = result.as_log()
    assert log["coverage_used"] == 8
    assert log["completion_used"] == 2
    assert log["realization_used"] <= 10
    assert log["realization_used"] < log["selected_count"]
    assert 200 in result.selected
    assert 201 in result.selected
    assert set(range(10)).isdisjoint(set(result.by_lane[LANE_COVERAGE]))
    coverage_ids = set(result.by_lane[LANE_COVERAGE])
    assert coverage_ids <= set(range(100, 110))
    assert len(coverage_ids) == 8


def test_realization_orders_by_exit_urgency():
    books = [
        LaneBook(book_id=1, has_inventory=True, exit_urgency=0.20),
        LaneBook(book_id=2, has_inventory=True, exit_urgency=0.90, is_hard_risk=True),
        LaneBook(book_id=3, has_inventory=True, exit_urgency=0.70),
        LaneBook(book_id=4, has_inventory=True, exit_urgency=0.40),
    ]
    result = select_lane_candidates(books, _budgets(realization=3, overflow=0, coverage=0, completion=0))
    assert result.by_lane[LANE_REALIZATION] == [2, 3, 4]
    assert 1 not in result.selected


def test_completion_prefers_one_remaining_then_two_then_new_via_spill():
    books = [
        LaneBook(book_id=11, observations_remaining=2, economics_ok=True, cheap_score=0.90, total_score_phase="IGNITION", total_score_due=True),
        LaneBook(book_id=10, observations_remaining=1, economics_ok=True, cheap_score=0.10, total_score_phase="IGNITION", total_score_due=True),
        LaneBook(book_id=12, is_uncovered=True, observations_remaining=3, cheap_score=0.99),
        LaneBook(book_id=13, observations_remaining=2, economics_ok=False, cheap_score=1.0),
    ]
    result = select_lane_candidates(books, _budgets(coverage=0, completion=2, realization=0, overflow=0))
    assert result.by_lane[LANE_COMPLETION] == [10, 11]
    assert classify_execution_lane(books[3]) == LANE_COVERAGE
    leftover = select_lane_candidates(
        books, _budgets(coverage=0, completion=3, realization=0, overflow=0),
    )
    assert leftover.by_lane[LANE_COMPLETION] == [10, 11]
    assert 12 in leftover.by_lane[LANE_COVERAGE] or leftover.as_log()["coverage_used"] == 0
    spilled = select_lane_candidates(
        books, _budgets(coverage=0, completion=4, realization=0, overflow=0),
    )
    assert 12 in spilled.selected
    assert spilled.as_log()["coverage_spilled"] >= 1


def test_unused_coverage_spills_into_realization():
    books = [
        LaneBook(book_id=i, has_inventory=True, exit_urgency=float(i))
        for i in range(8)
    ]
    books.append(LaneBook(book_id=50, is_uncovered=True, cheap_score=1.0))
    result = select_lane_candidates(
        books, _budgets(coverage=6, completion=2, realization=2, overflow=0),
    )
    assert result.as_log()["coverage_used"] == 1
    assert result.as_log()["realization_used"] == 8
    assert result.as_log()["realization_spilled"] == 6
    assert 50 in result.selected


def test_coverage_prefers_productive_ev_before_uncovered_bad_economics():
    books = [
        LaneBook(book_id=1, maker_ev=2.0, cheap_score=0.90, economics_ok=True),
        LaneBook(book_id=2, is_stale=True, maker_ev=0.5, cheap_score=0.10, economics_ok=True),
        LaneBook(book_id=3, is_uncovered=True, maker_ev=-0.2, cheap_score=0.99, economics_ok=True),
        LaneBook(book_id=4, is_uncovered=True, maker_ev=0.8, cheap_score=0.20, economics_ok=True),
    ]
    result = select_lane_candidates(
        books, _budgets(coverage=3, completion=0, realization=0, overflow=0),
    )
    assert result.by_lane[LANE_COVERAGE] == [4, 2, 1]
    assert 3 not in result.selected


def test_admit_lane_uses_reserved_then_overflow():
    budgets = _budgets(coverage=1, completion=1, realization=1, overflow=1)
    used = {LANE_COVERAGE: 1, LANE_COMPLETION: 0, LANE_REALIZATION: 0}
    ok, reason = admit_lane_candidate(
        lane=LANE_COVERAGE, used=used, overflow_used=0, budgets=budgets,
    )
    assert ok is True
    assert reason is None
    blocked, cap = admit_lane_candidate(
        lane=LANE_COVERAGE, used=used, overflow_used=1, budgets=budgets,
    )
    assert blocked is False
    assert cap == "COVERAGE_SLOT_CAP"


def test_research_wires_three_execution_lanes():
    assert "total_score_phase_budget_tuple" in RESEARCH_SRC
    assert "research_coverage_slots" not in RESEARCH_SRC
    assert "research_completion_slots" not in RESEARCH_SRC
    assert "research_realization_slots" not in RESEARCH_SRC
    assert "research_shared_overflow_slots" not in RESEARCH_SRC
    assert "select_lane_candidates" in RESEARCH_SRC
    assert "[S1R_LANES]" in RESEARCH_SRC
    screen = RESEARCH_SRC.split("def _research_fast_screen(")[1].split(
        "def _research_full_predict_fallback("
    )[0]
    assert "select_lane_candidates" in screen
    assert "select_fast_candidates(rows, self.research_candidate_count)" not in screen
