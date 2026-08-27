# SPDX-License-Identifier: MIT
"""V4.7 score-taker and inventory-lane regression contracts."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY_DIR))

RESEARCH_SRC = (STRATEGY_DIR / "Strategy1_Research.py").read_text(encoding="utf-8")

from research_hybrid import REASON_SN79_TAKER
from research_realization import ACTION_TAKER, evaluate_realization
from research_realization_ladder import (
    ACTION_COMPETITIVE,
    apply_realization_ladder,
    classify_realization_rung,
)


def test_v47_policy_and_phantom_inventory_contract():
    assert 'RESEARCH_POLICY_VERSION = "simplified_kappa_productivity_v4_13_7"' in RESEARCH_SRC
    screen = RESEARCH_SRC.split("def _research_fast_screen(self, state)", 1)[1].split(
        "def _research_full_predictions", 1
    )[0]
    assert "has_inv = qty > flat" in screen
    assert "qty > flat or bid in" not in screen
    assert "stale_empty_position_keys" in screen
    assert "research_enable_score_taker_direct" in RESEARCH_SRC
    assert "research_cancel_before_taker" in RESEARCH_SRC


def test_direct_score_taker_bypasses_maker_urgency_rung():
    rung = classify_realization_rung(0.20)
    assert rung.taker_eligible is False
    action, trigger = apply_realization_ladder(
        rung=rung,
        hybrid_take=True,
        hybrid_reason=REASON_SN79_TAKER,
        hard_safety=False,
        direct_taker_authorized=True,
        transition_quarantine=False,
        cost=4.0,
        risk=2.0,
        state="NORMAL",
    )
    assert action == ACTION_TAKER
    assert trigger == REASON_SN79_TAKER


def test_non_authorized_hybrid_take_still_respects_ladder():
    rung = classify_realization_rung(0.20)
    action, _ = apply_realization_ladder(
        rung=rung,
        hybrid_take=True,
        hybrid_reason="TAKER_LOCK_PROFIT",
        hard_safety=False,
        direct_taker_authorized=False,
        transition_quarantine=False,
        cost=4.0,
        risk=2.0,
        state="NORMAL",
    )
    assert action in {rung.maker_action, ACTION_COMPETITIVE}
    assert action != ACTION_TAKER


def test_score_utility_can_execute_taker_at_low_urgency():
    # Fast score completion: immediate close is only slightly negative but maker
    # fill is poor and the combined RT/Kappa/velocity utility dominates waiting.
    decision = evaluate_realization(
        book=4,
        inventory_size=0.25,
        inventory_ratio=0.20,
        inventory_age=2.0,
        unrealized_pnl=5.0,
        expected_markout=-1.0,
        volatility=0.0001,
        ofi=-0.1,
        observations_remaining=1,
        required_observations=3,
        volume_cap_headroom=1.0,
        recent_realized_pnl=0.0,
        adverse_selection_risk=0.10,
        fee_bps=1.0,
        spread_bps=4.0,
        slippage_bps=1.0,
        maker_fill_hazard=0.02,
        enable_hybrid=True,
        enable_sn79_action_utility=True,
        sn79_max_score_subsidy_loss_bps=-2.0,
        ladder_bands=None,
    )
    assert decision.action_utility is not None
    if decision.action_utility.take:
        assert decision.score_taker_authorized is True
        assert decision.direct_taker_authorized is True
        assert decision.selected_action == ACTION_TAKER


def test_transition_quarantine_still_blocks_direct_taker():
    decision = evaluate_realization(
        book=4, inventory_size=0.25, inventory_ratio=0.20, inventory_age=20.0,
        unrealized_pnl=10.0, expected_markout=-2.0, observations_remaining=1,
        fee_bps=1.0, spread_bps=2.0, slippage_bps=1.0, maker_fill_hazard=0.01,
        transition_quarantine=True, enable_sn79_action_utility=True,
    )
    assert decision.selected_action != ACTION_TAKER


def test_score_taker_direct_is_feature_gated():
    decision = evaluate_realization(
        book=4, inventory_size=0.25, inventory_ratio=0.20, inventory_age=2.0,
        unrealized_pnl=5.0, expected_markout=-1.0, volatility=0.0001, ofi=-0.1,
        observations_remaining=1, required_observations=3, volume_cap_headroom=1.0,
        recent_realized_pnl=0.0, adverse_selection_risk=0.10, fee_bps=1.0,
        spread_bps=4.0, slippage_bps=1.0, maker_fill_hazard=0.02,
        enable_hybrid=True, enable_sn79_action_utility=True,
        allow_score_taker_direct=False,
        allow_economic_taker_direct=False,
        allow_aggressive_positive_ev_taker=False,
        sn79_max_score_subsidy_loss_bps=-2.0,
    )
    assert decision.action_utility is not None
    assert decision.action_utility.take is True
    assert decision.score_taker_authorized is True
    assert decision.direct_taker_authorized is False
    assert decision.taker_eligible is False
    assert decision.selected_action != ACTION_TAKER
