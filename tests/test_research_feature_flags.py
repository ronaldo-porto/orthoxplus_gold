# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Independent Research feature flags keep legacy A/B paths available."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)

from research_exit_urgency import compute_exit_urgency_v1, compute_exit_urgency_v2
from research_fill_hazard import HazardPrediction
from research_hybrid import REASON_MAKER, REASON_MAKER_EV, REASON_TAKER_EV, hybrid_taker_decision
from research_taker_economics import (
    HoldingCostBreakdown,
    REASON_CATASTROPHIC,
    REASON_HOLDING_EXCEEDS_COST,
    TakerCostBreakdown,
    TakerEconomicsDecision,
)


FLAGS = (
    "research_enable_lane_scheduler",
    "research_enable_aggressive_coverage",
    "research_enable_inventory_state_v2",
    "research_enable_exit_urgency_v2",
    "research_enable_hybrid_realization_v2",
    "research_enable_economic_taker",
    "research_enable_precise_reduction_qty",
    "research_enable_dust_economic_gate",
    "research_enable_authoritative_kappa_state",
    "research_enable_markout_v2",
    "research_enable_fill_hazard_exit_compare",
)


def _econ(*, holding: float, taker: float, take: bool, catastrophic: bool = False):
    reason = REASON_CATASTROPHIC if catastrophic else (
        REASON_HOLDING_EXCEEDS_COST if take else "TAKER_REJECTED_ECONOMICS"
    )
    return TakerEconomicsDecision(
        take=take or catastrophic,
        reason=reason,
        holding=HoldingCostBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, holding),
        taker=TakerCostBreakdown(0.0, 0.0, 0.0, 0.0, taker),
        expected_net_realization_pnl=0.0,
        net_floor_bps=0.0,
        economic_ok=take,
        floor_ok=True,
        catastrophic=catastrophic,
    )


def _pred(**kwargs):
    payload = dict(
        any_fill=0.08,
        actionable_fill=0.04,
        dust=0.02,
        source="cell",
        usable=True,
        n_at_risk=40,
        ttl_ms=500.0,
        remaining_any_fill=0.08,
    )
    payload.update(kwargs)
    return HazardPrediction(**payload)


def test_all_step16_flags_exist_and_default_on():
    for name in FLAGS:
        assert name in RESEARCH_SRC
        assert f'getattr(cfg, "{name}", True)' in RESEARCH_SRC
    config = RESEARCH_SRC.split("self._enqueue({")[1].split("})")[0]
    for name in FLAGS:
        key = name.replace("research_", "")
        assert key in config or name.replace("research_enable_", "enable_") in RESEARCH_SRC


def test_legacy_exit_urgency_v1_is_weaker_than_full_v2():
    v1 = compute_exit_urgency_v1(
        inventory_ratio=0.20, inventory_age=4.0, unrealized_pnl=-2.0,
        expected_markout=-12.0, volatility=0.02, ofi=-0.80, kappa_need=0.90,
        volume_cap_headroom=0.05, adverse_selection_risk=0.80,
    )
    v2 = compute_exit_urgency_v2(
        inventory_size=0.90, inventory_ratio=0.20, inventory_age=4.0,
        unrealized_pnl=-2.0, expected_markout=-12.0, volatility=0.02,
        ofi=-0.80, inventory_sign=1.0, kappa_need=0.90,
        volume_cap_headroom=0.05, adverse_selection_risk=0.80,
    )
    assert v1.urgency < v2.urgency
    assert v1.markout_pressure == 0.0
    assert v2.markout_pressure > 0.0


def test_economic_taker_flag_blocks_non_catastrophic_take():
    blocked = hybrid_taker_decision(
        unrealized_pnl_bps=8.0,
        maker_exit_ev=0.20,
        crossing_cost_bps=2.0,
        economics=_econ(holding=4.0, taker=2.0, take=False),
        hazard=_pred(),
        allow_economic_taker=False,
    )
    assert blocked.take is False
    assert blocked.reason == REASON_MAKER
    allowed = hybrid_taker_decision(
        unrealized_pnl_bps=-40.0,
        crossing_cost_bps=8.0,
        economics=_econ(holding=4.0, taker=2.0, take=False, catastrophic=True),
        allow_economic_taker=False,
        stop_loss_hit=True,
        band="MAX_LONG",
        inventory_ratio=0.98,
    )
    assert allowed.take is True
    assert allowed.reason == REASON_CATASTROPHIC


def test_fill_hazard_compare_flag_restores_holding_cost_gate():
    hazard = _pred(any_fill=0.90, actionable_fill=0.85, remaining_any_fill=0.90)
    ev = hybrid_taker_decision(
        unrealized_pnl_bps=6.0,
        maker_exit_ev=8.0,
        crossing_cost_bps=5.0,
        economics=_econ(holding=10.0, taker=5.0, take=True),
        hazard=hazard,
        use_fill_hazard_ev=True,
    )
    legacy = hybrid_taker_decision(
        unrealized_pnl_bps=6.0,
        maker_exit_ev=8.0,
        crossing_cost_bps=5.0,
        economics=_econ(holding=10.0, taker=5.0, take=True),
        hazard=hazard,
        use_fill_hazard_ev=False,
    )
    assert ev.take is False
    assert ev.reason == REASON_MAKER_EV
    assert legacy.take is True
    assert legacy.reason == REASON_HOLDING_EXCEEDS_COST


def test_research_wires_flag_gates():
    assert "_research_lanes_on(" in RESEARCH_SRC
    assert "research_enable_aggressive_coverage" in RESEARCH_SRC
    lanes_src = (ROOT / "agents" / "strategy" / "research_execution_lanes.py").read_text()
    assert "coverage_slots=0" in lanes_src
    assert "use_exit_urgency_v2" in RESEARCH_SRC
    assert "use_fill_hazard_ev" in RESEARCH_SRC
    assert "allow_economic_taker" in RESEARCH_SRC
    assert "research_enable_precise_reduction_qty" in RESEARCH_SRC
    assert "research_enable_markout_v2" in RESEARCH_SRC
    assert "_research_dust_econ_on(" in RESEARCH_SRC
    assert "research_enable_hybrid_realization_v2" in RESEARCH_SRC
