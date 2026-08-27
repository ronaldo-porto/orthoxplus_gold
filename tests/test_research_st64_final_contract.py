# SPDX-License-Identifier: MIT
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY_DIR))

from research_execution_lanes import LaneBook, apply_breadth_rotation_gate
from research_hybrid import hybrid_taker_decision, TAKER_AUTH_SCORE
from research_fill_hazard import HazardPrediction

SRC = (STRATEGY_DIR / "Strategy1_Research.py").read_text()
RUNNER = (ROOT / "run_strategy1_research_test_multi.sh").read_text()

def _hazard():
    return HazardPrediction(any_fill=0.02, actionable_fill=0.01, dust=0.0, source="cell", usable=True, n_at_risk=40, ttl_ms=500.0, remaining_any_fill=0.02)

def test_st64_policy_and_operational_breadth_target():
    assert 'RESEARCH_POLICY_VERSION = "inventory_state_decoupling_v4_12_18"' in SRC
    assert 'research_score_target_books", 88' in SRC
    assert 'and score_deficit > 0' in SRC
    assert 'research_score_target_books=88' in RUNNER
    assert 'research_qualified_suppression_min_incomplete=1' in RUNNER

def test_single_productive_incomplete_rotates_stable_qualified():
    rows = [
        LaneBook(book_id=1, score_qualified=True, observations_remaining=0),
        LaneBook(book_id=2, observations_remaining=1, entry_feasible=True, economics_ok=True),
    ]
    gated, suppressed, productive = apply_breadth_rotation_gate(rows, enabled=True)
    assert productive == 1
    assert suppressed == {1}
    assert not {x.book_id: x for x in gated}[1].entry_feasible

def test_score_taker_requires_in_progress_kappa_and_maker_evidence():
    cold = hybrid_taker_decision(
        unrealized_pnl_bps=8.0, maker_exit_ev=-10.0, crossing_cost_bps=2.0,
        observations_remaining=3, required_observations=3, maker_fill_hazard=0.02,
        allow_economic_taker=False, allow_economic_taker_direct=False, allow_risk_taker_direct=False,
    )
    assert cold.score_authorized is False
    no_evidence = hybrid_taker_decision(
        unrealized_pnl_bps=-4.0, maker_exit_ev=-10.0, crossing_cost_bps=2.0,
        observations_remaining=1, required_observations=3, maker_fill_hazard=None, hazard=None,
        allow_economic_taker=False, allow_economic_taker_direct=False, allow_risk_taker_direct=False,
    )
    assert no_evidence.score_authorized is False
    one_away = hybrid_taker_decision(
        unrealized_pnl_bps=5.0, maker_exit_ev=1.9, crossing_cost_bps=4.0,
        observations_remaining=1, required_observations=3, maker_fill_hazard=0.02,
        allow_economic_taker=False, allow_economic_taker_direct=False,
        allow_aggressive_positive_ev_taker=False, allow_risk_taker_direct=False,
    )
    assert one_away.score_authorized is True
    assert one_away.direct_authorized is True
    assert one_away.taker_authority == TAKER_AUTH_SCORE
