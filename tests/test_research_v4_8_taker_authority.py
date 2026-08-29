# SPDX-License-Identifier: MIT
"""V4.8 bounded direct Taker authority regression tests."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY_DIR))

from research_exit_hazard_ev import compare_maker_taker_exit
from research_fill_hazard import HazardPrediction
from research_hybrid import (
    TAKER_AUTH_ECONOMIC,
    TAKER_AUTH_SCORE,
    hybrid_taker_decision,
)
from research_realization import ACTION_TAKER, evaluate_realization
from research_realization_ladder import apply_realization_ladder, classify_realization_rung
from research_taker_economics import (
    HoldingCostBreakdown,
    REASON_HOLDING_EXCEEDS_COST,
    TakerCostBreakdown,
    TakerEconomicsDecision,
)


def _pred(p: float = 0.05) -> HazardPrediction:
    return HazardPrediction(
        any_fill=p,
        actionable_fill=p * 0.7,
        dust=p * 0.1,
        source="cell",
        usable=True,
        n_at_risk=40,
        ttl_ms=500.0,
        remaining_any_fill=p,
    )


def _econ(*, take: bool, holding: float = 8.0, taker: float = 2.0) -> TakerEconomicsDecision:
    return TakerEconomicsDecision(
        take=take,
        reason=REASON_HOLDING_EXCEEDS_COST if take else "TAKER_REJECTED_ECONOMICS",
        holding=HoldingCostBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, holding),
        taker=TakerCostBreakdown(0.0, 0.0, 0.0, 0.0, taker),
        expected_net_realization_pnl=0.0,
        net_floor_bps=0.0,
        economic_ok=take,
        floor_ok=True,
        catastrophic=False,
    )


def test_score_taker_loss_subsidy_is_removed():
    decision = hybrid_taker_decision(
        economics=_econ(take=False),
        unrealized_pnl_bps=-4.0,
        maker_exit_ev=-10.0,
        crossing_cost_bps=2.0,
        hazard=_pred(),
        observations_remaining=1,
        required_observations=3,
        inventory_ratio=0.20,
        urgency=0.20,
        allow_economic_taker=False,
        allow_economic_taker_direct=False,
    )
    assert decision.allowed_loss_floor_bps == 0.0
    assert decision.score_authorized is False
    assert decision.direct_authorized is False


def test_one_away_score_taker_rejects_negative_six_bps_concession():
    decision = hybrid_taker_decision(
        economics=_econ(take=False),
        unrealized_pnl_bps=-4.0,
        maker_exit_ev=-10.0,
        crossing_cost_bps=2.0,
        hazard=_pred(),
        observations_remaining=1,
        required_observations=3,
        inventory_ratio=0.20,
        urgency=0.20,
        allow_economic_taker=False,
        allow_economic_taker_direct=False,
    )
    assert decision.maker_taker_ev is not None
    assert decision.maker_taker_ev.expected_taker_exit_value == -6.0
    assert decision.score_authorized is False
    assert decision.direct_authorized is False
    assert decision.allowed_loss_floor_bps == 0.0


def test_one_away_score_taker_rejects_beyond_eight_bps_floor():
    decision = hybrid_taker_decision(
        economics=_econ(take=False),
        unrealized_pnl_bps=-7.0,
        maker_exit_ev=-10.0,
        crossing_cost_bps=2.0,
        hazard=_pred(),
        observations_remaining=1,
        required_observations=3,
        inventory_ratio=0.20,
        urgency=0.20,
        allow_economic_taker=False,
        allow_economic_taker_direct=False,
    )
    assert decision.maker_taker_ev is not None
    assert decision.maker_taker_ev.expected_taker_exit_value == -9.0
    assert decision.score_authorized is False
    assert decision.direct_authorized is False


def test_bounded_economic_taker_direct_bypasses_low_urgency_ladder():
    decision = evaluate_realization(
        book=7,
        inventory_size=0.20,
        inventory_ratio=0.18,
        inventory_age=10.0,
        unrealized_pnl=12.0,
        expected_markout=0.2,
        fee_bps=1.0,
        spread_bps=2.0,
        slippage_bps=1.0,
        volume_cap_headroom=0.80,
        maker_fill_hazard=0.35,
        enable_sn79_action_utility=False,
    )
    assert decision.taker_eligible is False
    assert decision.economic_taker_authorized is True
    assert decision.direct_taker_authorized is True
    assert decision.taker_authority == TAKER_AUTH_ECONOMIC
    assert decision.selected_action == ACTION_TAKER


def test_economic_direct_hard_loss_bound_prevents_direct_bypass():
    decision = hybrid_taker_decision(
        economics=_econ(take=True, holding=60.0, taker=5.0),
        unrealized_pnl_bps=-30.0,
        maker_exit_ev=-100.0,
        crossing_cost_bps=5.0,
        hazard=_pred(),
        enable_sn79_action_utility=False,
        economic_direct_max_loss_bps=-20.0,
    )
    assert decision.maker_taker_ev is not None
    assert decision.maker_taker_ev.prefer_taker is True
    assert decision.maker_taker_ev.expected_taker_exit_value < -20.0
    assert decision.economic_authorized is False
    assert decision.direct_authorized is False
    rung = classify_realization_rung(0.20)
    action, _ = apply_realization_ladder(
        rung=rung,
        hybrid_take=decision.take,
        hybrid_reason=decision.reason,
        hard_safety=False,
        direct_taker_authorized=decision.direct_authorized,
        transition_quarantine=False,
        cost=5.0,
        risk=60.0,
        state="NORMAL",
    )
    assert action != ACTION_TAKER


def test_legacy_direct_risk_authority_is_removed():
    decision = hybrid_taker_decision(
        economics=_econ(take=False, holding=20.0, taker=2.0),
        unrealized_pnl_bps=-10.0,
        maker_exit_ev=-100.0,
        crossing_cost_bps=2.0,
        hazard=_pred(),
        enable_sn79_action_utility=False,
        allow_economic_taker=False,
        allow_economic_taker_direct=False,
        allow_score_taker_direct=False,
        inventory_state="CAUTION",
        failed_exit_count=3,
        time_since_first_exit_attempt=20.0,
        inventory_age=20.0,
    )
    assert decision.risk_authorized is False
    assert decision.direct_authorized is False
    assert decision.take is False

def test_failed_exit_and_age_penalty_reduce_wait_ev():
    baseline = compare_maker_taker_exit(
        prediction=_pred(0.20),
        maker_profit=10.0,
        holding_cost=2.0,
        immediate_realization_value=0.0,
        taker_cost=2.0,
        failed_exit_count=0,
        inventory_age=0.0,
    )
    stale = compare_maker_taker_exit(
        prediction=_pred(0.20),
        maker_profit=10.0,
        holding_cost=2.0,
        immediate_realization_value=0.0,
        taker_cost=2.0,
        failed_exit_count=4,
        inventory_age=40.0,
    )
    assert stale.wait_penalty_bps > 0.0
    assert stale.expected_maker_exit_value < baseline.expected_maker_exit_value
