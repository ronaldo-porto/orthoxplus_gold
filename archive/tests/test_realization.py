# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Production ExitUrgency and selective realization."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from realization import (
    ACTION_AGGRESSIVE,
    ACTION_PASSIVE,
    ACTION_TAKER,
    MAKER_ACTIONS,
    evaluate_realization,
    exit_urgency,
    inventory_should_manage,
    selective_taker_allowed,
)


def _low_inventory(**overrides):
    params = dict(
        book=1,
        inventory_size=0.08,
        inventory_ratio=0.07,
        inventory_age=2.0,
        unrealized_pnl=1.0,
        expected_markout=0.4,
        volatility=0.001,
        imbalance=0.05,
        observations_remaining=0,
        volume_cap_headroom=0.80,
        recent_realized_pnl=0.01,
        adverse_selection_risk=0.02,
        fee_bps=1.0,
        spread_bps=2.5,
        slippage_bps=3.0,
        band="LONG",
    )
    params.update(overrides)
    return params


def test_low_inventory_is_maker_first():
    decision = evaluate_realization(**_low_inventory())
    assert 0.0 <= decision.exit_urgency <= 1.0
    assert decision.selected_action in MAKER_ACTIONS
    assert decision.selected_action == ACTION_PASSIVE
    assert decision.state in {"NORMAL", "CAUTION"}
    assert decision.selected_action != ACTION_TAKER


def test_aging_inventory_increases_urgency():
    young = evaluate_realization(**_low_inventory(inventory_age=2.0))
    aged = evaluate_realization(**_low_inventory(inventory_age=40.0, unrealized_pnl=4.0))
    assert aged.exit_urgency > young.exit_urgency
    assert aged.exit_urgency - young.exit_urgency >= 0.10


def test_toxic_holding_realizes_earlier():
    calm = evaluate_realization(**_low_inventory(
        inventory_size=0.25,
        inventory_ratio=0.22,
        inventory_age=8.0,
        unrealized_pnl=3.0,
        expected_markout=1.0,
        imbalance=0.05,
        adverse_selection_risk=0.02,
    ))
    toxic = evaluate_realization(**_low_inventory(
        inventory_size=0.25,
        inventory_ratio=0.22,
        inventory_age=8.0,
        unrealized_pnl=-18.0,
        expected_markout=-12.0,
        imbalance=-0.55,
        adverse_selection_risk=0.60,
        volatility=0.008,
    ))
    assert toxic.exit_urgency > calm.exit_urgency
    assert toxic.state in {"DEFENSIVE", "EXIT_ONLY", "EMERGENCY"}
    assert toxic.exit_urgency >= 0.48


def test_taker_rejected_when_cost_exceeds_risk():
    allowed, risk, cost = selective_taker_allowed(
        holding_risk=0.10,
        adverse_move=0.05,
        opportunity_cost=0.05,
        fee_bps=6.0,
        spread_bps=8.0,
        slippage_bps=5.0,
    )
    assert cost > risk
    assert allowed is False

    decision = evaluate_realization(
        **_low_inventory(
            inventory_size=0.90,
            inventory_ratio=0.82,
            inventory_age=50.0,
            unrealized_pnl=-16.0,
            expected_markout=-4.0,
            volatility=0.0002,
            imbalance=0.0,
            volume_cap_headroom=1.0,
            adverse_selection_risk=0.20,
            fee_bps=6.0,
            spread_bps=8.0,
            slippage_bps=5.0,
            band="LONG",
        )
    )
    assert decision.exit_urgency >= 0.50
    assert decision.taker_exit_cost > decision.taker_risk
    assert decision.selected_action != ACTION_TAKER
    assert decision.selected_action == ACTION_AGGRESSIVE
    assert decision.trigger == "TAKER_REJECTED_COST"


def test_emergency_reduction_works():
    hard = evaluate_realization(
        **_low_inventory(
            inventory_size=1.10,
            inventory_ratio=0.98,
            inventory_age=12.0,
            unrealized_pnl=-40.0,
            band="MAX_LONG",
            stop_loss_hit=True,
            hard_emergency=True,
            fee_bps=8.0,
            spread_bps=10.0,
            slippage_bps=6.0,
        )
    )
    assert hard.state == "EMERGENCY"
    assert hard.selected_action == ACTION_TAKER
    assert hard.trigger in {"EMERGENCY_HARD", "EMERGENCY_REDUCTION", "TAKER_RISK_EXCEEDS_COST"}

    maker_fallback = evaluate_realization(
        **_low_inventory(
            inventory_size=0.85,
            inventory_ratio=0.96,
            inventory_age=30.0,
            unrealized_pnl=-6.0,
            expected_markout=0.0,
            volatility=0.0001,
            imbalance=0.0,
            volume_cap_headroom=1.0,
            adverse_selection_risk=0.0,
            fee_bps=8.0,
            spread_bps=10.0,
            slippage_bps=6.0,
            band="LONG",
            stop_loss_hit=False,
            hard_emergency=False,
        )
    )
    assert maker_fallback.state == "EMERGENCY"
    assert maker_fallback.selected_action in {ACTION_AGGRESSIVE, ACTION_TAKER}
    if maker_fallback.taker_exit_cost > maker_fallback.taker_risk:
        assert maker_fallback.selected_action == ACTION_AGGRESSIVE


def test_exit_urgency_is_continuous_unit_interval():
    for age in (0, 5, 20, 80):
        score = exit_urgency(
            inventory_size=0.20,
            inventory_ratio=0.15,
            inventory_age=age,
            unrealized_pnl=2.0,
            expected_markout=-1.0,
            volatility=0.002,
            imbalance=-0.10,
            inventory_sign=1.0,
            kappa_need=0.40,
            volume_cap_headroom=0.60,
            recent_realized_pnl=0.0,
            adverse_selection_risk=0.10,
        )
        assert 0.0 <= score <= 1.0


def test_inventory_should_manage_profit_and_age():
    assert inventory_should_manage(
        inventory_ratio=0.10,
        inventory_age=2.0,
        unrealized_pnl=0.5,
        band="LONG",
    ) is False
    assert inventory_should_manage(
        inventory_ratio=0.10,
        inventory_age=2.0,
        unrealized_pnl=3.0,
        band="LONG",
    ) is True
    assert inventory_should_manage(
        inventory_ratio=0.10,
        inventory_age=12.0,
        unrealized_pnl=0.0,
        band="LONG",
    ) is True
    assert inventory_should_manage(
        inventory_ratio=0.98,
        inventory_age=1.0,
        unrealized_pnl=0.0,
        band="MAX_LONG",
    ) is True
