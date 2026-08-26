# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research dust economics: quarantine tiny dust, block loss-making CROSS_DUST."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)
MANAGE = RESEARCH_SRC.split("def _manage_inventory(")[1].split(
    "def _research_volume_cap_quote("
)[0]
SKEWED = RESEARCH_SRC.split("def _place_skewed_quotes(")[1].split(
    "def _place_directional_round_trip("
)[0]
ESCAPE = RESEARCH_SRC.split("def _research_try_dust_escape(")[1].split(
    "def _dust_fill_matches_recent_compaction("
)[0]

from research_dust_economics import (
    ACTION_COMPETITIVE_MAKER,
    ACTION_PASSIVE_MAKER,
    ACTION_QUARANTINE,
    ACTION_REJECT_CROSS,
    ACTION_TAKER,
    BAND_MODERATE,
    BAND_TINY,
    DUST_ECON_VERSION,
    REASON_CATASTROPHIC,
    REASON_HOLDING_EXCEEDS_COST,
    REASON_MAKER_PROFITABLE,
    REASON_OLDER_COMPETITIVE,
    REASON_REJECT_UNECONOMIC_CROSS,
    REASON_TINY,
    classify_dust_band,
    evaluate_dust_action,
    is_cross_dust_cleanup,
    quote_would_create_dust,
)
from research_exit_quantity import choose_reduce_quantity


def _moderate(**overrides):
    params = dict(
        inventory=0.18,
        min_order=0.25,
        reduce_qty=0.25,
        age_ticks=20.0,
        unrealized_pnl=2.0,
        spread_bps=8.0,
        fee_bps=1.0,
        slippage_bps=0.5,
        expected_markout=0.4,
        volatility=0.001,
        inventory_ratio=0.15,
    )
    params.update(overrides)
    return evaluate_dust_action(**params)


def test_tiny_dust_is_quarantined():
    sized = choose_reduce_quantity(
        inventory=0.10, desired=0.10, min_order=0.25, volume_decimals=4,
    )
    decision = evaluate_dust_action(
        inventory=0.10,
        min_order=0.25,
        reduce_qty=sized.quantity,
        age_ticks=800.0,
        unrealized_pnl=-40.0,
        spread_bps=12.0,
        fee_bps=8.0,
        slippage_bps=6.0,
        inventory_ratio=0.08,
        stop_loss_hit=True,
        band="LONG",
    )
    assert classify_dust_band(0.10, 0.25) == BAND_TINY
    assert decision.allow is False
    assert decision.action == ACTION_QUARANTINE
    assert decision.reason == REASON_TINY
    assert decision.band == BAND_TINY


def test_profitable_maker_reduction_uses_maker_cleanup():
    decision = _moderate()
    assert decision.band == BAND_MODERATE
    assert decision.allow is True
    assert decision.action == ACTION_PASSIVE_MAKER
    assert decision.reason == REASON_MAKER_PROFITABLE
    assert decision.maker_ev_bps > 0.0


def test_older_moderate_uses_competitive_maker_when_economics_positive():
    decision = _moderate(age_ticks=500.0)
    assert decision.allow is True
    assert decision.action == ACTION_COMPETITIVE_MAKER
    assert decision.reason == REASON_OLDER_COMPETITIVE


def test_min_size_cleanup_of_moderate_dust_is_cross_dust():
    assert is_cross_dust_cleanup(
        inventory=0.18, reduce_qty=0.25, min_order=0.25,
    ) is True
    assert is_cross_dust_cleanup(
        inventory=0.40, reduce_qty=0.25, min_order=0.25,
    ) is False


def test_loss_making_cross_dust_is_blocked():
    decision = _moderate(
        spread_bps=1.0,
        fee_bps=8.0,
        slippage_bps=6.0,
        expected_markout=-12.0,
        unrealized_pnl=-20.0,
        age_ticks=80.0,
        volatility=0.008,
        inventory_ratio=0.15,
    )
    assert decision.maker_ev_bps < 0.0
    assert decision.cross_dust is True
    assert decision.allow is False
    assert decision.action == ACTION_REJECT_CROSS
    assert decision.reason == REASON_REJECT_UNECONOMIC_CROSS
    assert decision.expected_net_bps < 0.0


def test_taker_cleanup_requires_holding_above_cost_and_non_losing_cross():
    blocked = _moderate(
        spread_bps=1.0,
        fee_bps=8.0,
        slippage_bps=6.0,
        expected_markout=-2.0,
        unrealized_pnl=1.0,
        age_ticks=4.0,
        volatility=0.001,
        inventory_ratio=0.10,
    )
    assert blocked.allow is False
    assert blocked.action in {ACTION_QUARANTINE, ACTION_REJECT_CROSS}

    allowed = _moderate(
        spread_bps=1.0,
        fee_bps=1.0,
        slippage_bps=0.2,
        expected_markout=-8.0,
        unrealized_pnl=20.0,
        age_ticks=80.0,
        volatility=0.010,
        ofi=-0.80,
        inventory_ratio=0.90,
        kappa_need=0.80,
        volume_cap_headroom=0.05,
    )
    assert allowed.maker_ev_bps < 0.0
    assert allowed.holding_cost_bps > allowed.cleanup_cost_bps
    assert allowed.expected_net_bps >= 0.0
    assert allowed.allow is True
    assert allowed.action == ACTION_TAKER
    assert allowed.reason == REASON_HOLDING_EXCEEDS_COST


def test_catastrophic_hard_risk_may_cross_dust():
    decision = _moderate(
        spread_bps=1.0,
        fee_bps=8.0,
        slippage_bps=6.0,
        expected_markout=-12.0,
        unrealized_pnl=-40.0,
        stop_loss_hit=True,
        band="MAX_LONG",
        inventory_ratio=0.98,
    )
    assert decision.allow is True
    assert decision.action == ACTION_TAKER
    assert decision.reason == REASON_CATASTROPHIC
    assert decision.catastrophic is True


def test_quote_prevention_blocks_dust_creation_not_reduction():
    assert quote_would_create_dust(
        inventory_before=0.0, signed_fill_qty=0.10, min_order_size=0.25,
    ) is True
    assert quote_would_create_dust(
        inventory_before=0.0, signed_fill_qty=0.25, min_order_size=0.25,
    ) is False
    assert quote_would_create_dust(
        inventory_before=0.18, signed_fill_qty=-0.25, min_order_size=0.25,
    ) is False


def test_research_wires_dust_economics_and_prevention():
    assert "RESEARCH_DUST_ECON_VERSION" in RESEARCH_SRC
    assert DUST_ECON_VERSION in RESEARCH_SRC
    assert "research_enable_dust_economics" in RESEARCH_SRC
    assert "evaluate_dust_action(" in RESEARCH_SRC
    assert "_research_manage_dust(" in MANAGE
    assert "quote_would_create_dust(" in SKEWED
    assert "[S1R_DUST_ECON]" in RESEARCH_SRC
    assert "DUST_ECON" in RESEARCH_SRC
    assert "_research_dust_econ_on(" in ESCAPE or "research_enable_dust_economics" in ESCAPE
