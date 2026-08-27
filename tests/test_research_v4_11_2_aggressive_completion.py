# SPDX-License-Identifier: MIT
"""V4.11.2 aggressive positive-EV Taker + ONE_AWAY exact-min tests."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY_DIR))

from research_entry_size import ADMISSION_NEAR_SAFE, admit_minimum_order
from research_hybrid import (
    REASON_AGGRESSIVE_POSITIVE_EV,
    TAKER_AUTH_ECONOMIC,
    TAKER_AUTH_NONE,
    hybrid_taker_decision,
)
from research_taker_economics import (
    HoldingCostBreakdown,
    TakerCostBreakdown,
    TakerEconomicsDecision,
)

SRC = (STRATEGY_DIR / "Strategy1_Research.py").read_text(encoding="utf-8")
LAUNCHER = (ROOT / "run_strategy1_research_test_multi.sh").read_text(encoding="utf-8")


def _econ_reject(*, holding_cost: float = 0.5, taker_cost: float = 2.0) -> TakerEconomicsDecision:
    """Legacy economics says wait; V4.11.2 may still authorize positive superior take."""
    return TakerEconomicsDecision(
        take=False,
        reason="TAKER_REJECTED_ECONOMICS",
        holding=HoldingCostBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, holding_cost),
        taker=TakerCostBreakdown(0.0, 0.0, 0.0, 0.0, taker_cost),
        expected_net_realization_pnl=0.0,
        net_floor_bps=0.0,
        economic_ok=False,
        floor_ok=False,
        catastrophic=False,
    )


def test_one_away_positive_taker_bypasses_legacy_econ_gate_but_keeps_zero_loss_floor():
    d = hybrid_taker_decision(
        economics=_econ_reject(),
        unrealized_pnl_bps=6.0,
        crossing_cost_bps=2.0,
        maker_exit_ev=1.0,
        maker_fill_hazard=0.40,
        observations_remaining=1,
        inventory_age=2.0,
        urgency=0.10,
        enable_sn79_action_utility=False,
        allow_score_taker_direct=False,
        allow_risk_taker_direct=False,
        allow_economic_taker_direct=True,
        economic_direct_max_loss_bps=0.0,
        allow_aggressive_positive_ev_taker=True,
        aggressive_positive_ev_min_net_bps=0.0,
        aggressive_positive_ev_one_away_margin_bps=0.0,
    )
    assert d.maker_taker_ev is not None
    assert d.maker_taker_ev.expected_taker_exit_value >= 0.0
    assert d.maker_taker_ev.expected_taker_exit_value > d.maker_taker_ev.expected_maker_exit_value
    assert d.aggressive_positive_ev_authorized is True
    assert d.economic_authorized is True
    assert d.direct_authorized is True
    assert d.taker_authority == TAKER_AUTH_ECONOMIC
    assert d.reason == REASON_AGGRESSIVE_POSITIVE_EV
    assert d.aggressive_positive_ev_trigger == "ONE_AWAY"
    assert d.aggressive_positive_ev_advantage_bps > 0.0
    assert d.aggressive_positive_ev_floor_bps == 0.0
    assert abs(d.qty_frac - 0.90) < 1e-12
    assert d.take is True


def test_aggressive_positive_taker_requires_explicit_realization_trigger():
    d = hybrid_taker_decision(
        economics=_econ_reject(),
        unrealized_pnl_bps=6.0,
        crossing_cost_bps=2.0,
        maker_exit_ev=1.0,
        maker_fill_hazard=0.40,
        observations_remaining=3,
        inventory_age=2.0,
        urgency=0.10,
        failed_exit_count=0,
        enable_sn79_action_utility=False,
        allow_score_taker_direct=False,
        allow_risk_taker_direct=False,
        allow_economic_taker_direct=True,
        allow_aggressive_positive_ev_taker=True,
        aggressive_positive_ev_min_age_ticks=16.0,
        aggressive_positive_ev_failed_exit_count=8,
        aggressive_positive_ev_max_maker_fill=0.05,
        aggressive_positive_ev_min_urgency=0.30,
    )
    assert d.aggressive_positive_ev_authorized is False
    assert d.direct_authorized is False
    assert d.taker_authority == TAKER_AUTH_NONE
    assert d.take is False


def test_aggressive_positive_taker_never_subsidizes_negative_taker_ev_even_one_away():
    d = hybrid_taker_decision(
        economics=_econ_reject(taker_cost=4.0),
        unrealized_pnl_bps=2.0,
        crossing_cost_bps=4.0,
        maker_exit_ev=-5.0,
        maker_fill_hazard=0.01,
        observations_remaining=1,
        inventory_age=30.0,
        urgency=0.90,
        failed_exit_count=20,
        enable_sn79_action_utility=False,
        allow_score_taker_direct=False,
        allow_risk_taker_direct=False,
        allow_economic_taker_direct=True,
        economic_direct_max_loss_bps=0.0,
        allow_aggressive_positive_ev_taker=True,
        aggressive_positive_ev_min_net_bps=0.0,
    )
    assert d.maker_taker_ev is not None
    assert d.maker_taker_ev.expected_taker_exit_value < 0.0
    assert d.aggressive_positive_ev_authorized is False
    assert d.take is False
    assert d.taker_authority == TAKER_AUTH_NONE


def test_failed_exits_activate_positive_ev_taker_when_it_beats_wait():
    d = hybrid_taker_decision(
        economics=_econ_reject(),
        unrealized_pnl_bps=7.0,
        crossing_cost_bps=2.0,
        maker_exit_ev=2.0,
        maker_fill_hazard=0.20,
        observations_remaining=2,
        inventory_age=5.0,
        urgency=0.10,
        failed_exit_count=8,
        enable_sn79_action_utility=False,
        allow_score_taker_direct=False,
        allow_risk_taker_direct=False,
        allow_economic_taker_direct=True,
        allow_aggressive_positive_ev_taker=True,
        aggressive_positive_ev_switch_margin_bps=0.50,
        aggressive_positive_ev_failed_exit_count=8,
    )
    assert d.aggressive_positive_ev_authorized is True
    assert d.take is True
    assert d.reason == REASON_AGGRESSIVE_POSITIVE_EV


def test_one_away_exact_min_allows_book43_shape():
    # Mirrors the V4.11.1 log shape: safe≈0.183, exit≈0.243, min=0.25,
    # positive lifecycle EV, but soft risk_factor shrank the raw clip.
    d = admit_minimum_order(
        safe_size=0.18280026,
        min_order=0.25,
        tolerance=0.20,
        trading_ev=0.03551001,
        inventory_risk=0.00406109,
        exit_capacity=0.24338123,
        volume_headroom=1.0,
        remaining_inventory=1.2,
        observations_remaining=1,
        enable_one_away_exact_min=True,
        one_away_min_trading_ev=0.0,
        one_away_min_exit_fraction=0.20,
    )
    assert d.allow is True
    assert d.band == ADMISSION_NEAR_SAFE
    assert d.size == 0.25
    assert d.promoted is True
    assert d.trigger == "ONE_AWAY_EXACT_MIN"


def test_one_away_exact_min_rejects_negative_ev_or_insufficient_exit_capacity():
    neg = admit_minimum_order(
        safe_size=0.18,
        min_order=0.25,
        trading_ev=-0.001,
        inventory_risk=0.01,
        exit_capacity=0.245,
        volume_headroom=1.0,
        remaining_inventory=1.2,
        observations_remaining=1,
        enable_one_away_exact_min=True,
        one_away_min_trading_ev=0.0,
        one_away_min_exit_fraction=0.20,
    )
    thin = admit_minimum_order(
        safe_size=0.18,
        min_order=0.25,
        trading_ev=0.03,
        inventory_risk=0.01,
        exit_capacity=0.045,
        volume_headroom=1.0,
        remaining_inventory=1.2,
        observations_remaining=1,
        enable_one_away_exact_min=True,
        one_away_min_trading_ev=0.0,
        one_away_min_exit_fraction=0.20,
    )
    assert neg.allow is False
    assert thin.allow is False


def test_v4112_wiring_and_launcher_contract():
    assert 'RESEARCH_POLICY_VERSION = "simplified_kappa_productivity_v4_13_3"' in SRC
    assert "research_enable_aggressive_positive_ev_taker=1" in LAUNCHER
    assert "research_aggressive_positive_ev_min_net_bps=0.0" in LAUNCHER
    assert "research_one_away_exact_min_enabled=1" in LAUNCHER
    assert "research_one_away_exact_min_safe_fraction=0.15" in LAUNCHER
    assert "research_one_away_exact_min_exit_fraction=0.20" in LAUNCHER
    assert "aggressive_positive_ev_taker_authorized" in SRC
    assert "observations_remaining=kappa_remaining" in SRC
