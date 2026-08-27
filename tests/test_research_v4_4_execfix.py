# SPDX-License-Identifier: MIT
"""Contracts for Research hybrid_score_utility_v4_7 correctness/orchestration fixes."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY_DIR))

RESEARCH_SRC = (STRATEGY_DIR / "Strategy1_Research.py").read_text(encoding="utf-8")
HYBRID_SRC = (STRATEGY_DIR / "research_hybrid.py").read_text(encoding="utf-8")

from research_execution_lanes import (
    LANE_COVERAGE,
    LaneBook,
    normalize_lane_budgets,
    select_lane_candidates,
)
from research_hybrid import hybrid_taker_decision
from research_taker_economics import evaluate_taker_economics


def test_policy_version_and_session_sync_fail_closed_contract():
    assert 'RESEARCH_POLICY_VERSION = "simplified_kappa_productivity_v4_13_8"' in RESEARCH_SRC
    handle = RESEARCH_SRC.split("def handle(self, state:", 1)[1].split("def respond(", 1)[0]
    assert "except Exception as exc:" in handle
    assert '"FAIL_CLOSED_QUARANTINE"' in handle
    assert "_research_transition_quarantine_remaining = max(" in handle


def test_sn79_utility_can_authorize_profitable_fast_realization_beyond_legacy_gate():
    econ = evaluate_taker_economics(
        inventory_ratio=0.05,
        inventory_size=0.05,
        inventory_age=1.0,
        fee_bps=4.0,
        spread_bps=8.0,
        slippage_bps=3.0,
        unrealized_pnl=20.0,
    )
    assert econ.take is False
    decision = hybrid_taker_decision(
        economics=econ,
        unrealized_pnl_bps=20.0,
        maker_exit_ev=-5.0,
        crossing_cost_bps=1.0,
        maker_fill_hazard=0.01,
        use_fill_hazard_ev=True,
    )
    assert decision.maker_taker_ev is not None
    assert decision.maker_taker_ev.prefer_taker is True
    assert decision.action_utility is not None
    assert decision.action_utility.take is True
    assert decision.take is True
    assert decision.economic_authorized is True
    assert decision.aggressive_positive_ev_authorized is True
    assert decision.reason == "TAKER_AGGRESSIVE_POSITIVE_EV"

    legacy = hybrid_taker_decision(
        economics=econ,
        unrealized_pnl_bps=20.0,
        maker_exit_ev=-5.0,
        crossing_cost_bps=1.0,
        maker_fill_hazard=0.01,
        use_fill_hazard_ev=True,
        enable_sn79_action_utility=False,
        allow_aggressive_positive_ev_taker=False,
    )
    assert legacy.take is False


def test_screen_allocation_is_single_lane_authority_in_strategy():
    # The pure helper remains available for legacy tests, but Research execution
    # must not call it after the screen already granted spill-aware lane slots.
    quote_fn = RESEARCH_SRC.split("def _place_skewed_quotes(", 1)[1].split(
        "def _place_directional_round_trip(", 1
    )[0]
    assert "admit_lane_candidate(" not in quote_fn
    assert '"LANE_NOT_GRANTED"' in quote_fn
    success = quote_fn.split("if self._research_backfill_active and placed:", 1)[1]
    assert "used[lane] = int(used.get(lane, 0) or 0) + 1" in success


def test_cold_uncovered_exploration_not_confused_with_known_negative_ev():
    books = [
        LaneBook(
            book_id=1,
            is_uncovered=True,
            maker_ev=0.0,
            maker_ev_known=False,
            cheap_score=0.70,
            economics_ok=True,
        ),
        LaneBook(
            book_id=2,
            is_uncovered=True,
            maker_ev=-0.5,
            maker_ev_known=True,
            cheap_score=0.99,
            economics_ok=True,
        ),
    ]
    result = select_lane_candidates(
        books,
        normalize_lane_budgets(
            coverage_slots=1,
            completion_slots=0,
            realization_slots=0,
            shared_overflow_slots=0,
        ),
    )
    assert result.by_lane[LANE_COVERAGE] == [1]


def test_markout_latency_and_persistence_contracts():
    assert "def _research_conservative_markout" in RESEARCH_SRC
    assert "conservative_expected_markout_bps(" in RESEARCH_SRC
    assert "latency_ms=self._research_strategy_latency_ms()" in RESEARCH_SRC
    assert "latency_ms=float(getattr(self, \"_research_markout_eval_ms\"" not in RESEARCH_SRC
    assert 'getattr(cfg, "research_session_save_every_n", 100)' in RESEARCH_SRC
