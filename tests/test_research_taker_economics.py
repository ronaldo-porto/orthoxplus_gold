# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research taker economics: holding cost vs cross cost, no auto-emergency."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)

from research_hybrid import hybrid_taker_decision
from research_realization import ACTION_TAKER, MAKER_ACTIONS, evaluate_realization
from research_realization_ladder import clamp_ladder_bands
from research_taker_economics import (
    REASON_CATASTROPHIC,
    REASON_HOLDING_EXCEEDS_COST,
    REASON_REJECT_ECONOMICS,
    REASON_REJECT_NET_FLOOR,
    evaluate_taker_economics,
    expected_holding_cost,
    expected_taker_cost,
    is_catastrophic_hard_risk,
)


def test_components_are_named_and_additive():
    holding = expected_holding_cost(
        inventory_ratio=0.80,
        inventory_size=0.90,
        volatility=0.008,
        inventory_age=30.0,
        expected_markout=-10.0,
        ofi=-0.60,
        inventory_sign=1.0,
        kappa_need=0.80,
        volume_cap_headroom=0.10,
    )
    taker = expected_taker_cost(
        fee_bps=2.0, spread_bps=4.0, slippage_bps=1.5, inventory_size=0.90, volatility=0.008,
    )
    assert holding.expected_holding_cost == (
        holding.inventory_risk
        + holding.expected_adverse_move
        + holding.inventory_age_cost
        + holding.kappa_opportunity_cost
        + holding.volume_cap_opportunity_cost
    )
    assert taker.expected_taker_cost == (
        taker.taker_fee
        + taker.spread_cross_cost
        + taker.slippage_buffer
        + taker.market_impact_buffer
    )
    assert abs(taker.spread_cross_cost - 2.0) <= 1e-12
    log = evaluate_taker_economics(
        inventory_ratio=0.80,
        inventory_size=0.90,
        volatility=0.008,
        inventory_age=30.0,
        expected_markout=-10.0,
        ofi=-0.60,
        inventory_sign=1.0,
        kappa_need=0.80,
        volume_cap_headroom=0.10,
        fee_bps=2.0,
        spread_bps=4.0,
        slippage_bps=1.5,
    ).as_log()
    for key in (
        "inventory_risk",
        "expected_adverse_move",
        "inventory_age_cost",
        "kappa_opportunity_cost",
        "volume_cap_opportunity_cost",
        "expected_holding_cost",
        "taker_fee",
        "spread_cross_cost",
        "slippage_buffer",
        "market_impact_buffer",
        "expected_taker_cost",
        "expected_net_realization_pnl",
        "taker_reason",
    ):
        assert key in log


def test_emergency_hard_does_not_auto_take():
    labeled = hybrid_taker_decision(
        hard_emergency=True,
        unrealized_pnl_bps=-8.0,
        crossing_cost_bps=12.0,
        inventory_age=8.0,
        fee_bps=8.0,
        spread_bps=10.0,
        slippage_bps=6.0,
        inventory_ratio=0.40,
        inventory_size=0.40,
        band="LONG",
    )
    assert labeled.take is False
    assert labeled.reason == "TAKER_REJECTED_ECONOMICS"

    maxed = evaluate_realization(
        book=1,
        inventory_size=0.70,
        inventory_ratio=0.55,
        inventory_age=8.0,
        unrealized_pnl=-8.0,
        fee_bps=8.0,
        spread_bps=10.0,
        slippage_bps=6.0,
        band="MAX_LONG",
        hard_emergency=True,
        stop_loss_hit=False,
    )
    assert maxed.selected_action != ACTION_TAKER
    assert maxed.taker_economics is not None
    assert maxed.taker_economics.catastrophic is False


def test_take_only_when_holding_exceeds_taker_cost():
    cheap = evaluate_taker_economics(
        inventory_ratio=0.85,
        inventory_size=0.95,
        volatility=0.010,
        inventory_age=40.0,
        expected_markout=-12.0,
        ofi=-0.70,
        inventory_sign=1.0,
        kappa_need=0.80,
        volume_cap_headroom=0.05,
        fee_bps=1.0,
        spread_bps=2.0,
        slippage_bps=1.0,
    )
    expensive = evaluate_taker_economics(
        inventory_ratio=0.20,
        inventory_size=0.20,
        volatility=0.001,
        inventory_age=4.0,
        expected_markout=0.4,
        fee_bps=8.0,
        spread_bps=10.0,
        slippage_bps=6.0,
    )
    assert cheap.economic_ok is True
    assert cheap.take is True
    assert cheap.reason == REASON_HOLDING_EXCEEDS_COST
    assert cheap.holding.expected_holding_cost > cheap.taker.expected_taker_cost
    assert expensive.economic_ok is False
    assert expensive.take is False
    assert expensive.reason == REASON_REJECT_ECONOMICS


def test_net_floor_can_block_an_otherwise_cheap_take():
    blocked = evaluate_taker_economics(
        inventory_ratio=0.85,
        inventory_size=0.95,
        volatility=0.010,
        inventory_age=40.0,
        expected_markout=-12.0,
        ofi=-0.70,
        inventory_sign=1.0,
        kappa_need=0.80,
        volume_cap_headroom=0.05,
        fee_bps=1.0,
        spread_bps=2.0,
        slippage_bps=1.0,
        net_floor_bps=50.0,
    )
    assert blocked.economic_ok is True
    assert blocked.floor_ok is False
    assert blocked.take is False
    assert blocked.reason == REASON_REJECT_NET_FLOOR


def test_only_catastrophic_hard_risk_overrides_economics():
    assert is_catastrophic_hard_risk(
        stop_loss_hit=True, band="MAX_LONG", inventory_ratio=0.40, unrealized_pnl=-8.0,
    ) is False
    assert is_catastrophic_hard_risk(
        stop_loss_hit=True, band="MAX_LONG", inventory_ratio=0.98, unrealized_pnl=-40.0,
    ) is True

    decision = evaluate_realization(
        book=2,
        inventory_size=1.10,
        inventory_ratio=0.98,
        inventory_age=12.0,
        unrealized_pnl=-40.0,
        fee_bps=8.0,
        spread_bps=10.0,
        slippage_bps=6.0,
        band="MAX_LONG",
        stop_loss_hit=True,
        hard_emergency=True,
    )
    assert decision.selected_action == ACTION_TAKER
    assert decision.trigger == REASON_CATASTROPHIC
    assert decision.taker_economics.catastrophic is True
    assert decision.taker_qty_frac == 1.0


def test_economic_take_still_needs_ladder_eligibility():
    cheap = evaluate_realization(
        book=3,
        inventory_size=0.95,
        inventory_ratio=0.85,
        inventory_age=40.0,
        unrealized_pnl=-12.0,
        expected_markout=-12.0,
        volatility=0.010,
        ofi=-0.70,
        kappa_need=0.80,
        volume_cap_headroom=0.05,
        fee_bps=1.0,
        spread_bps=2.0,
        slippage_bps=1.0,
        band="LONG",
        ladder_bands=clamp_ladder_bands(0.90, 0.93, 0.96),
        economic_direct_max_loss_bps=0.0,
        allow_risk_taker_direct=False,
        risk_direct_max_loss_bps=-10.0,
        sn79_max_score_subsidy_loss_bps=0.0,
        sn79_one_away_loss_floor_bps=0.0,
        sn79_two_away_loss_floor_bps=0.0,
        sn79_uncovered_loss_floor_bps=0.0,
    )
    assert cheap.taker_economics.take is True
    assert cheap.taker_eligible is False
    assert cheap.selected_action in MAKER_ACTIONS
    assert cheap.selected_action != ACTION_TAKER

    eligible = evaluate_realization(
        book=4,
        inventory_size=0.95,
        inventory_ratio=0.85,
        inventory_age=40.0,
        unrealized_pnl=-12.0,
        expected_markout=-12.0,
        volatility=0.010,
        ofi=-0.70,
        kappa_need=0.80,
        volume_cap_headroom=0.05,
        fee_bps=1.0,
        spread_bps=2.0,
        slippage_bps=1.0,
        band="LONG",
        ladder_bands=clamp_ladder_bands(0.05, 0.08, 0.10),
    )
    assert eligible.taker_eligible is True
    assert eligible.selected_action == ACTION_TAKER
    assert eligible.trigger == "TAKER_EXIT_EV"
    assert eligible.maker_taker_ev is not None
    assert eligible.maker_taker_ev.prefer_taker is True
    log = eligible.as_log()
    assert log["expected_holding_cost"] > log["expected_taker_cost"]


def test_research_wires_taker_decision_telemetry():
    assert "RESEARCH_TAKER_ECON_VERSION" in RESEARCH_SRC
    assert ("taker_economics_v2_live_fees" in RESEARCH_SRC or "taker_economics_v3_lifecycle" in RESEARCH_SRC)
    assert "research_taker_net_floor_bps" in RESEARCH_SRC
    assert "TAKER_DECISION" in RESEARCH_SRC
    assert "[S1R_TAKER_DECISION]" in RESEARCH_SRC
    for name in (
        "inventory_risk",
        "expected_adverse_move",
        "inventory_age_cost",
        "kappa_opportunity_cost",
        "volume_cap_opportunity_cost",
        "taker_fee",
        "spread_cross_cost",
        "slippage_buffer",
        "market_impact_buffer",
    ):
        assert name in RESEARCH_SRC
