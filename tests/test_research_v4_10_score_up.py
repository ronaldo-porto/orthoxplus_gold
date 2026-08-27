# SPDX-License-Identifier: MIT
"""V4.10 score-up regression tests: rolling Kappa, live fees, hard Taker authority."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY_DIR))

from research_hybrid import TAKER_AUTH_NONE, hybrid_taker_decision
from research_kappa_state import kappa_expiry_state, rolling_observation_counts
from research_taker_economics import (
    HoldingCostBreakdown,
    TakerCostBreakdown,
    TakerEconomicsDecision,
    fee_rate_to_bps,
)
from research_execution_lanes import LaneBook, LaneBudgets, select_lane_candidates

RESEARCH_SRC = (STRATEGY_DIR / "Strategy1_Research.py").read_text(encoding="utf-8")
LAUNCHER_SRC = (ROOT / "run_strategy1_research_test_multi.sh").read_text(encoding="utf-8")


def _econ_take() -> TakerEconomicsDecision:
    return TakerEconomicsDecision(
        take=True,
        reason="TAKER_HOLDING_EXCEEDS_COST",
        holding=HoldingCostBreakdown(0, 0, 0, 0, 0, 100.0),
        taker=TakerCostBreakdown(0, 0, 0, 0, 2.0),
        expected_net_realization_pnl=-100.0,
        net_floor_bps=0.0,
        economic_ok=True,
        floor_ok=True,
        catastrophic=False,
    )


def test_v410_project_contract_uses_zero_loss_score_defaults():
    assert 'RESEARCH_POLICY_VERSION = "simplified_kappa_productivity_v4_13_4"' in RESEARCH_SRC
    assert "research_allow_score_loss_subsidy=0" in LAUNCHER_SRC
    assert "research_economic_direct_max_loss_bps=0.0" in LAUNCHER_SRC
    assert "research_enable_risk_taker_direct=0" in LAUNCHER_SRC
    assert "research_sn79_one_away_loss_floor_bps=0.0" in LAUNCHER_SRC
    assert ("research_candidate_count=12" in LAUNCHER_SRC or "research_candidate_count=10" in LAUNCHER_SRC)


def test_no_authority_none_taker_when_economic_floor_rejects():
    # Legacy economic preference can be true, but the hard direct floor rejects
    # the -100 bps realization. V4.10 must return take=False, authority=NONE.
    d = hybrid_taker_decision(
        economics=_econ_take(),
        unrealized_pnl_bps=-98.0,
        maker_exit_ev=-200.0,
        crossing_cost_bps=2.0,
        enable_sn79_action_utility=False,
        economic_direct_max_loss_bps=0.0,
        allow_risk_taker_direct=False,
    )
    assert d.economic_authorized is False
    assert d.direct_authorized is False
    assert d.taker_authority == TAKER_AUTH_NONE
    assert d.take is False


def test_live_fee_rate_conversion_preserves_maker_rebate_and_clamps_taker():
    assert fee_rate_to_bps(0.000229, allow_rebate=False) == 2.29
    assert fee_rate_to_bps(-0.0001, allow_rebate=True) == -1.0
    assert fee_rate_to_bps(-0.0001, allow_rebate=False) == 0.0


def test_rolling_kappa_drops_expired_observation_and_reports_expiry():
    lookback = 100
    history = {
        10: {7: 1.0},
        80: {7: 1.0},
        90: {7: 1.0},
        100: {7: 1.0},
    }
    counts = rolling_observation_counts(history, now=100, lookback_ns=lookback)
    assert counts[7] == 4
    expiry = kappa_expiry_state(
        7, history, now=100, lookback_ns=lookback,
        required_observations=3, warning_horizon_frac=0.20,
    )
    # Most recent required set is [80, 90, 100], so qualification starts to
    # fail when 80 rolls out at t=180.
    assert expiry.qualified is True
    assert expiry.oldest_required_timestamp == 80
    assert expiry.expires_at == 180
    assert expiry.time_to_expiry_ns == 80

    counts2 = rolling_observation_counts(history, now=191, lookback_ns=lookback)
    assert counts2[7] == 1


def test_recent_min_order_failure_does_not_consume_candidate_slots():
    books = [
        LaneBook(book_id=1, observations_remaining=1, entry_feasible=False, cheap_score=999),
        LaneBook(book_id=2, observations_remaining=1, entry_feasible=True, cheap_score=2),
        LaneBook(book_id=3, observations_remaining=2, entry_feasible=True, cheap_score=1),
    ]
    allocation = select_lane_candidates(
        books, LaneBudgets(coverage_slots=1, completion_slots=2, realization_slots=0, shared_overflow_slots=0)
    )
    assert 1 not in allocation.selected
    assert allocation.by_lane["KAPPA_COMPLETION"] == [2, 3]


def test_effective_exposure_guard_is_wired():
    assert "_research_effective_exposure_allows" in RESEARCH_SRC
    assert "_research_side_committed_qty" in RESEARCH_SRC
    assert "effective_exposure_block" in RESEARCH_SRC
