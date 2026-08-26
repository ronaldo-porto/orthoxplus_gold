# SPDX-License-Identifier: MIT
"""V4.11 aggressive-performance regression tests."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY_DIR))

from research_cohort import CohortCandidate, update_sticky_cohort
from research_entry_size import admit_minimum_order, ADMISSION_NEAR_SAFE
from research_execution_lanes import LaneBook, LaneBudgets, select_lane_candidates
from research_lifecycle_ev import lifecycle_entry_cost_bps

SRC = (STRATEGY_DIR / "Strategy1_Research.py").read_text(encoding="utf-8")
LAUNCHER = (ROOT / "run_strategy1_research_test_multi.sh").read_text(encoding="utf-8")


def test_v411_contract_is_enabled():
    assert 'RESEARCH_POLICY_VERSION = "kappa_conversion_v4_12_16_predeploy"' in SRC
    assert ("research_candidate_count=12" in LAUNCHER or "research_candidate_count=10" in LAUNCHER)
    assert ("research_cohort_size=10" in LAUNCHER or "research_cohort_size=8" in LAUNCHER)
    assert ("research_positive_ev_min_order_override=1" in LAUNCHER or "research_positive_ev_min_order_override=0" in LAUNCHER)
    assert "research_quiet_ttl_ms=1000" in LAUNCHER
    assert "research_allow_score_loss_subsidy=0" in LAUNCHER


def test_lane_scheduler_obeys_hard_global_cap():
    books = []
    # 4 realization, 6 completion, 10 coverage = demand 20.
    for bid in range(1, 5):
        books.append(LaneBook(book_id=bid, has_inventory=True, exit_urgency=1.0))
    for bid in range(5, 11):
        books.append(LaneBook(book_id=bid, observations_remaining=1, economics_ok=True))
    for bid in range(11, 21):
        books.append(LaneBook(book_id=bid, observations_remaining=3, cheap_score=1.0))
    allocation = select_lane_candidates(
        books,
        LaneBudgets(coverage_slots=6, completion_slots=6, realization_slots=4, shared_overflow_slots=4),
        max_candidates=12,
    )
    assert len(allocation.selected) == 12
    assert allocation.by_lane["REALIZATION"] == [1, 2, 3, 4]
    assert len(allocation.by_lane["KAPPA_COMPLETION"]) == 6
    assert len(allocation.by_lane["COVERAGE"]) == 2


def test_expiring_book_is_pre_screen_completion_candidate():
    allocation = select_lane_candidates(
        [
            LaneBook(book_id=1, observations_remaining=0, needs_refresh=True, refresh_urgency=0.9),
            LaneBook(book_id=2, observations_remaining=3, cohort_member=True, cheap_score=2.0),
        ],
        LaneBudgets(coverage_slots=1, completion_slots=1, realization_slots=0, shared_overflow_slots=0),
        max_candidates=2,
    )
    assert allocation.by_lane["KAPPA_COMPLETION"] == [1]


def test_sticky_cohort_keeps_work_in_progress_and_finishes_first():
    rows = [
        CohortCandidate(book_id=1, observations_remaining=2, cheap_score=0.1),
        CohortCandidate(book_id=2, observations_remaining=3, cheap_score=0.9),
        CohortCandidate(book_id=3, observations_remaining=1, cheap_score=0.2),
        CohortCandidate(book_id=4, observations_remaining=3, cheap_score=1.0),
    ]
    cohort = update_sticky_cohort([1, 2], rows, target_size=3, exploration_slots=1)
    assert 1 in cohort and 2 in cohort
    assert 3 in cohort  # one-away progress beats another fresh book
    assert 4 not in cohort


def test_score_qualified_book_rotates_out_unless_expiring():
    rows = [
        CohortCandidate(book_id=1, observations_remaining=0, score_qualified=True),
        CohortCandidate(book_id=2, observations_remaining=0, score_qualified=True, needs_refresh=True, refresh_urgency=0.9, deadline_urgency=0.9, deadline_critical=True),
        CohortCandidate(book_id=3, observations_remaining=2, cheap_score=0.5),
    ]
    cohort = update_sticky_cohort([1, 2], rows, target_size=2, exploration_slots=1)
    assert 1 not in cohort
    assert 2 in cohort
    assert 3 in cohort


def test_lifecycle_cost_includes_probable_exit_cost():
    cost = lifecycle_entry_cost_bps(
        maker_fee_bps=-1.0,
        taker_fee_bps=2.0,
        spread_bps=20.0,
        taker_exit_probability=0.30,
        slippage_bps=1.0,
        holding_risk_bps=0.5,
    )
    # entry maker -1 + expected exit fee (-0.7 + 0.6) + cross 3 + slip .3 + hold .5
    assert abs(cost.total_bps - 2.7) < 1e-12


def test_positive_lifecycle_ev_can_promote_one_minimum_clip():
    d = admit_minimum_order(
        safe_size=0.10,
        min_order=0.25,
        tolerance=0.20,
        trading_ev=0.08,
        inventory_risk=0.10,
        exit_capacity=0.125,
        volume_headroom=1.0,
        remaining_inventory=1.2,
        enable_positive_ev_override=True,
        positive_ev_min_safe_fraction=0.35,
        positive_ev_min_exit_fraction=0.45,
        positive_ev_min_trading_ev=0.05,
    )
    assert d.allow is True
    assert d.band == ADMISSION_NEAR_SAFE
    assert d.size == 0.25
    assert d.trigger == "POSITIVE_EV_OVERRIDE"


def test_negative_ev_never_uses_minimum_clip_override():
    d = admit_minimum_order(
        safe_size=0.10,
        min_order=0.25,
        trading_ev=-0.01,
        inventory_risk=0.10,
        exit_capacity=0.125,
        volume_headroom=1.0,
        remaining_inventory=1.2,
        enable_positive_ev_override=True,
        positive_ev_min_trading_ev=0.05,
    )
    assert d.allow is False


def test_v411_lifecycle_and_quiet_ttl_are_wired_before_execution():
    assert "fees_bps=self._research_lifecycle_entry_cost_bps" in SRC
    assert "needs_refresh=needs_refresh" in SRC
    assert "max_candidates=cap" in SRC
    assert 'reason = "QUIET_LONG"' in SRC
    assert "POSITIVE_EV_OVERRIDE" in (STRATEGY_DIR / "research_entry_size.py").read_text(encoding="utf-8")
