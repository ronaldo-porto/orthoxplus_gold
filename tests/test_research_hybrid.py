# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research hybrid maker + taker: take only when economically justified."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)

from research_hybrid import (
    REASON_AVOID_ADVERSE,
    REASON_EMERGENCY_HARD,
    REASON_KAPPA,
    REASON_LOCK_PROFIT,
    REASON_MAKER,
    REASON_MAKER_EV,
    REASON_REJECT_CAP,
    REASON_REJECT_DUST,
    REASON_REJECT_TRANSITION,
    REASON_STALE,
    REASON_TAKER_EV,
    hybrid_taker_decision,
    hybrid_taker_qty_frac,
    maker_fill_unreliable,
    taker_crossing_cost_bps,
    taker_lock_pnl_bps,
)
from research_taker_economics import (
    REASON_CATASTROPHIC,
    REASON_HOLDING_EXCEEDS_COST,
    REASON_REJECT_ECONOMICS,
)


def test_crossing_cost_is_half_spread_not_full_spread():
    cost = taker_crossing_cost_bps(fee_bps=1.0, spread_bps=4.0, slippage_bps=1.0)
    assert abs(cost - 4.0) < 1e-12


def test_low_edge_inventory_stays_maker():
    decision = hybrid_taker_decision(
        unrealized_pnl_bps=1.0,
        maker_exit_ev=1.4,
        crossing_cost_bps=4.0,
        inventory_age=2.0,
        urgency=0.10,
        maker_fill_hazard=0.40,
    )
    assert decision.take is False
    assert decision.reason == REASON_REJECT_ECONOMICS
    assert decision.qty_frac == 0.0
    assert decision.lock_pnl_bps < 0.0


def test_lock_profit_takes_when_taker_ev_beats_maker_ev():
    decision = hybrid_taker_decision(
        unrealized_pnl_bps=10.0,
        maker_exit_ev=0.40,
        crossing_cost_bps=4.0,
        inventory_age=6.0,
        urgency=0.30,
        maker_fill_hazard=0.35,
        min_lock_bps=1.0,
        maker_ev_gap_bps=0.50,
        fee_bps=2.0,
        spread_bps=4.0,
        slippage_bps=2.0,
        inventory_ratio=0.80,
        inventory_size=0.50,
        volatility=0.004,
        expected_markout=-4.0,
        kappa_need=0.80,
    )
    assert decision.lock_pnl_bps == 6.0
    assert decision.take is True
    assert decision.reason == REASON_TAKER_EV
    assert 0.0 < decision.qty_frac < 1.0
    assert decision.maker_taker_ev is not None
    assert (
        decision.maker_taker_ev.expected_taker_exit_value
        > decision.maker_taker_ev.expected_maker_exit_value
    )


def test_does_not_take_just_because_urgency_is_high():
    decision = hybrid_taker_decision(
        unrealized_pnl_bps=-2.0,
        maker_exit_ev=1.2,
        crossing_cost_bps=6.0,
        inventory_age=40.0,
        urgency=0.90,
        maker_fill_hazard=0.40,
        adverse_allowed=False,
    )
    assert decision.take is False
    assert decision.reason == REASON_REJECT_ECONOMICS


def test_adverse_take_only_when_already_allowed():
    blocked = hybrid_taker_decision(
        unrealized_pnl_bps=-8.0,
        maker_exit_ev=-1.0,
        crossing_cost_bps=8.0,
        adverse_allowed=False,
        urgency=0.70,
    )
    allowed = hybrid_taker_decision(
        unrealized_pnl_bps=-8.0,
        maker_exit_ev=-1.0,
        crossing_cost_bps=8.0,
        adverse_allowed=True,
        urgency=0.70,
    )
    assert blocked.take is False
    assert allowed.take is False
    assert allowed.reason == REASON_REJECT_ECONOMICS


def test_kappa_remaining_is_not_an_independent_take_gate():
    kwargs = dict(
        unrealized_pnl_bps=3.0,
        maker_exit_ev=6.0,
        crossing_cost_bps=3.0,
        maker_fill_hazard=0.80,
        min_lock_bps=1.0,
    )
    one_away = hybrid_taker_decision(observations_remaining=1, **kwargs)
    two_away = hybrid_taker_decision(observations_remaining=2, **kwargs)
    done = hybrid_taker_decision(observations_remaining=0, **kwargs)
    losing = hybrid_taker_decision(
        unrealized_pnl_bps=1.0,
        maker_exit_ev=6.0,
        crossing_cost_bps=3.0,
        observations_remaining=1,
        maker_fill_hazard=0.80,
    )
    assert one_away.take is False
    assert two_away.take is False
    assert done.take is False
    assert losing.take is False
    assert one_away.reason == REASON_REJECT_ECONOMICS
    assert two_away.reason == REASON_REJECT_ECONOMICS


def test_stale_profitable_dead_maker_takes_partial():
    decision = hybrid_taker_decision(
        unrealized_pnl_bps=6.0,
        maker_exit_ev=2.2,
        crossing_cost_bps=12.0,
        inventory_age=20.0,
        maker_fill_hazard=0.04,
        stale_age_ticks=16.0,
        min_lock_bps=1.0,
        fee_bps=6.0,
        spread_bps=8.0,
        slippage_bps=4.0,
    )
    young = hybrid_taker_decision(
        unrealized_pnl_bps=6.0,
        maker_exit_ev=2.2,
        crossing_cost_bps=12.0,
        inventory_age=4.0,
        maker_fill_hazard=0.04,
        stale_age_ticks=16.0,
        min_lock_bps=1.0,
        fee_bps=6.0,
        spread_bps=8.0,
        slippage_bps=4.0,
    )
    assert decision.take is False
    assert young.take is False


def test_missing_hazard_does_not_count_as_dead_maker():
    assert maker_fill_unreliable(None) is False
    assert maker_fill_unreliable(0.40) is False
    assert maker_fill_unreliable(0.05) is True
    decision = hybrid_taker_decision(
        unrealized_pnl_bps=6.0,
        maker_exit_ev=2.2,
        crossing_cost_bps=12.0,
        inventory_age=30.0,
        observations_remaining=1,
        maker_fill_hazard=None,
        min_lock_bps=1.0,
        fee_bps=6.0,
        spread_bps=8.0,
        slippage_bps=4.0,
    )
    assert decision.take is False
    assert decision.reason == REASON_REJECT_ECONOMICS


def test_volume_cap_and_dust_block_non_emergency():
    cap = hybrid_taker_decision(
        unrealized_pnl_bps=12.0,
        maker_exit_ev=0.1,
        crossing_cost_bps=3.0,
        volume_capped=True,
    )
    dust = hybrid_taker_decision(
        unrealized_pnl_bps=12.0,
        maker_exit_ev=0.1,
        crossing_cost_bps=3.0,
        dust=True,
    )
    emergency = hybrid_taker_decision(
        hard_emergency=True,
        volume_capped=True,
        dust=True,
        unrealized_pnl_bps=-40.0,
        crossing_cost_bps=8.0,
        stop_loss_hit=True,
        band="MAX_LONG",
        inventory_ratio=0.98,
        inventory_size=1.10,
        fee_bps=8.0,
        spread_bps=10.0,
        slippage_bps=6.0,
    )
    assert cap.take is False
    assert cap.reason == REASON_REJECT_CAP
    assert dust.take is False
    assert dust.reason == REASON_REJECT_DUST
    assert emergency.take is True
    assert emergency.reason == REASON_CATASTROPHIC
    assert emergency.qty_frac == 1.0
    transition = hybrid_taker_decision(
        hard_emergency=True,
        volume_capped=True,
        dust=True,
        unrealized_pnl_bps=-40.0,
        crossing_cost_bps=8.0,
        stop_loss_hit=True,
        band="MAX_LONG",
        inventory_ratio=0.98,
        transition_quarantine=True,
    )
    assert transition.take is False
    assert transition.reason == REASON_REJECT_TRANSITION


def test_qty_frac_never_increases_and_emergency_is_full():
    assert hybrid_taker_qty_frac(
        reason=REASON_CATASTROPHIC, urgency=0.2, lock_pnl_bps=-4.0, emergency=True,
    ) == 1.0
    frac = hybrid_taker_qty_frac(
        reason=REASON_LOCK_PROFIT, urgency=0.3, lock_pnl_bps=4.0,
    )
    assert 0.0 < frac < 1.0
    assert taker_lock_pnl_bps(unrealized_pnl_bps=None, crossing_cost_bps=5.0) == -5.0


def test_research_wires_hybrid_exits_without_taker_entries():
    assert "research_enable_hybrid_taker" in RESEARCH_SRC
    assert "def _research_exit_fill_hazard" in RESEARCH_SRC
    assert "def _research_exit_hazard_prediction" in RESEARCH_SRC
    assert "enable_hybrid=bool(getattr(self, \"research_enable_hybrid_taker\", True))" in RESEARCH_SRC
    start = RESEARCH_SRC.index("def _place_skewed_quotes(")
    end = RESEARCH_SRC.index("def _place_directional_round_trip(")
    assert "market_order" not in RESEARCH_SRC[start:end]
    assert "HYBRID" in RESEARCH_SRC
    assert "compare_maker_taker_exit(" in (
        (ROOT / "agents" / "strategy" / "research_hybrid.py").read_text(encoding="utf-8")
    )
