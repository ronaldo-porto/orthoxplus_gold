# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research inventory state machine V2."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)

from research_hybrid import hybrid_taker_decision
from research_inventory_state import (
    CAUTION_ENTRY_MULT,
    DEFENSIVE_ENTRY_MULT,
    STATE_CAUTION,
    STATE_DEFENSIVE,
    STATE_EMERGENCY,
    STATE_EXIT_ONLY,
    STATE_NORMAL,
    apply_exit_action_for_state,
    classify_inventory_state,
    inventory_pressure_v2,
    inventory_state_policy,
    side_size_multiplier,
)
from research_realization import (
    ACTION_AGGRESSIVE,
    ACTION_COMPETITIVE,
    ACTION_PASSIVE,
    ACTION_TAKER,
    evaluate_realization,
)


def _calm(**overrides):
    params = dict(
        inventory_size=0.08,
        inventory_ratio=0.07,
        inventory_age=2.0,
        unrealized_pnl=1.0,
        volatility=0.001,
        ofi=0.0,
        expected_markout=0.4,
        kappa_need=0.0,
        volume_cap_headroom=0.90,
        recent_realized_pnl=0.01,
        adverse_selection_risk=0.02,
        inventory_sign=1.0,
        band="LONG",
    )
    params.update(overrides)
    return params


def test_low_inventory_is_normal_or_caution():
    state = classify_inventory_state(**_calm())
    assert state in {STATE_NORMAL, STATE_CAUTION}
    policy = inventory_state_policy(state)
    assert policy.allow_maker_entry is True
    assert policy.allow_maker_exit is True
    assert policy.taker_eligible is False


def test_ratio_and_pressure_escalate_states():
    caution = classify_inventory_state(
        **_calm(
            inventory_age=28.0,
            unrealized_pnl=-10.0,
            expected_markout=-6.0,
            ofi=-0.55,
            volume_cap_headroom=0.20,
            recent_realized_pnl=-0.03,
            realization_failed=True,
        )
    )
    defensive = classify_inventory_state(**_calm(inventory_ratio=0.50, inventory_size=0.60))
    exit_only = classify_inventory_state(**_calm(inventory_ratio=0.72, inventory_size=0.90))
    emergency = classify_inventory_state(
        **_calm(band="MAX_LONG", inventory_ratio=0.99, hard_emergency=True),
    )
    assert caution in {STATE_CAUTION, STATE_DEFENSIVE}
    assert defensive == STATE_DEFENSIVE
    assert exit_only == STATE_EXIT_ONLY
    assert emergency == STATE_EMERGENCY


def test_recent_failure_raises_pressure_vs_success():
    success = inventory_pressure_v2(
        inventory_size=0.30, inventory_ratio=0.25, inventory_age=10.0,
        unrealized_pnl=-2.0, volatility=0.002, ofi=0.0, expected_markout=0.0,
        kappa_need=0.2, volume_cap_headroom=0.5, recent_realized_pnl=0.04,
        realization_failed=False, inventory_sign=1.0,
    )
    failure = inventory_pressure_v2(
        inventory_size=0.30, inventory_ratio=0.25, inventory_age=10.0,
        unrealized_pnl=-2.0, volatility=0.002, ofi=0.0, expected_markout=0.0,
        kappa_need=0.2, volume_cap_headroom=0.5, recent_realized_pnl=-0.04,
        realization_failed=True, inventory_sign=1.0,
    )
    assert failure > success


def test_state_policies_match_requested_behavior():
    normal = inventory_state_policy(STATE_NORMAL)
    caution = inventory_state_policy(STATE_CAUTION)
    defensive = inventory_state_policy(STATE_DEFENSIVE)
    exit_only = inventory_state_policy(STATE_EXIT_ONLY)
    emergency = inventory_state_policy(STATE_EMERGENCY)

    assert normal.same_side_entry_mult == 1.0
    assert caution.same_side_entry_mult == CAUTION_ENTRY_MULT
    assert caution.improve_exit is True
    assert defensive.same_side_entry_mult == DEFENSIVE_ENTRY_MULT
    assert defensive.allow_same_side_entry is False
    assert defensive.allow_inventory_increase is False
    assert defensive.allow_aggressive_maker is True
    assert exit_only.allow_inventory_increase is False
    assert exit_only.same_side_entry_mult == 0.0
    assert exit_only.taker_eligible is False
    assert emergency.allow_inventory_increase is False
    assert emergency.taker_eligible is True
    assert emergency.hard_taker_requires_safety is True

    long_sign = 1.0
    assert side_size_multiplier(side="buy", inventory_sign=long_sign, policy=caution) == CAUTION_ENTRY_MULT
    assert side_size_multiplier(side="sell", inventory_sign=long_sign, policy=caution) == caution.exit_side_mult
    assert side_size_multiplier(side="buy", inventory_sign=long_sign, policy=exit_only) == 0.0
    assert side_size_multiplier(side="sell", inventory_sign=long_sign, policy=exit_only) > 0.0


def test_exit_only_blocks_taker_unless_hard_safety():
    blocked, reason = apply_exit_action_for_state(
        state=STATE_EXIT_ONLY,
        selected_action=ACTION_TAKER,
        hard_safety=False,
    )
    assert blocked != ACTION_TAKER
    assert blocked == ACTION_AGGRESSIVE
    assert reason == "STATE_TAKER_BLOCKED"
    allowed, _ = apply_exit_action_for_state(
        state=STATE_EXIT_ONLY,
        selected_action=ACTION_TAKER,
        hard_safety=True,
    )
    assert allowed == ACTION_TAKER


def test_emergency_taker_still_uses_economic_gate_without_hard_safety():
    soft = evaluate_realization(
        book=1,
        inventory_size=1.05,
        inventory_ratio=0.96,
        inventory_age=8.0,
        unrealized_pnl=-6.0,
        expected_markout=-1.0,
        volatility=0.002,
        volume_cap_headroom=0.80,
        fee_bps=8.0,
        spread_bps=10.0,
        slippage_bps=6.0,
        band="LONG",
        hard_emergency=False,
        stop_loss_hit=False,
    )
    assert soft.state == STATE_EMERGENCY
    hard = evaluate_realization(
        book=1,
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
    assert hard.state == STATE_EMERGENCY
    assert hard.selected_action == ACTION_TAKER
    economic = hybrid_taker_decision(
        hard_emergency=False,
        unrealized_pnl_bps=-6.0,
        maker_exit_ev=2.0,
        crossing_cost_bps=12.0,
        inventory_age=8.0,
        fee_bps=8.0,
        spread_bps=10.0,
        slippage_bps=6.0,
    )
    assert economic.take is False


def test_caution_and_defensive_improve_maker_exits():
    caution, reason = apply_exit_action_for_state(
        state=STATE_CAUTION, selected_action=ACTION_PASSIVE,
    )
    assert caution == ACTION_COMPETITIVE
    assert reason == "STATE_EXIT_IMPROVED"
    defensive, dreason = apply_exit_action_for_state(
        state=STATE_DEFENSIVE, selected_action=ACTION_PASSIVE,
    )
    assert defensive == ACTION_AGGRESSIVE
    assert dreason == "STATE_DEFENSIVE_MAKER"


def test_research_wires_inventory_state_v2():
    assert "research_enable_inventory_state_v2" in RESEARCH_SRC
    assert "def _research_inventory_state(" in RESEARCH_SRC
    assert "side_size_multiplier(" in RESEARCH_SRC
    assert "[S1R_INVENTORY_STATE]" in RESEARCH_SRC
    assert "INVENTORY_STATE" in RESEARCH_SRC
