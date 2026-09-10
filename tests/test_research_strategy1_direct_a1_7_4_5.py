from pathlib import Path

from research_direct_economics import DIRECT_MAKER_MIN_EDGE_BPS
from research_direct_quiet_entry import (
    DIRECT_QUIET_ENTRY_VERSION,
    DIRECT_A1745_QUIET_ZERO_REBATE_MIN_EDGE_BPS,
    quiet_zero_rebate_entry_gate,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "agents/strategy/Strategy1_Research_Simple.py").read_text()


def _gate(*, regime="QUIET", fee=0.0, spread=31.5, trade_rate=0.0):
    return quiet_zero_rebate_entry_gate(
        regime=regime,
        maker_fee_bps=fee,
        spread_bps=spread,
        trade_rate=trade_rate,
        base_min_edge_bps=DIRECT_MAKER_MIN_EDGE_BPS,
    )


def test_version_and_integration_markers_present():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_0_2"' in SRC
    assert DIRECT_QUIET_ENTRY_VERSION == "direct_quiet_entry_v4_16_2_a1_7_4_5"
    assert "quiet_zero_rebate_entry_gate(" in SRC
    assert "A1745_ENTRY_BLOCK_LOW_EDGE" in SRC
    assert "A1745_ENTRY_ALLOWED" in SRC
    assert "A1745_QUIET_ZERO_REBATE_GATE" in SRC
    assert "A1745_REGIME_BYPASS" in SRC


def test_resumed_testnet_signature_raises_edge_floor_to_15_bps():
    gate = _gate()
    assert gate.active
    assert gate.effective_min_edge_bps == DIRECT_A1745_QUIET_ZERO_REBATE_MIN_EDGE_BPS == 15.0


def test_rebate_regime_keeps_frozen_a16_floor():
    gate = _gate(fee=-109.5)
    assert not gate.active
    assert gate.reason == "MAKER_REBATE_PRESENT"
    assert gate.effective_min_edge_bps == DIRECT_MAKER_MIN_EDGE_BPS


def test_non_quiet_regime_keeps_frozen_floor():
    gate = _gate(regime="TRENDING")
    assert not gate.active
    assert gate.reason == "REGIME_NOT_QUIET"
    assert gate.effective_min_edge_bps == DIRECT_MAKER_MIN_EDGE_BPS


def test_healthy_trade_activity_keeps_frozen_floor():
    gate = _gate(trade_rate=0.11)
    assert not gate.active
    assert gate.reason == "TRADE_ACTIVITY_HEALTHY"
    assert gate.effective_min_edge_bps == DIRECT_MAKER_MIN_EDGE_BPS


def test_narrow_spread_keeps_frozen_floor():
    gate = _gate(spread=19.99)
    assert not gate.active
    assert gate.reason == "SPREAD_NOT_WIDE"
    assert gate.effective_min_edge_bps == DIRECT_MAKER_MIN_EDGE_BPS


def test_signed_positive_maker_fee_is_treated_as_no_rebate():
    gate = _gate(fee=2.0)
    assert gate.active
    assert gate.zero_rebate


def test_small_negative_fee_near_zero_is_still_no_meaningful_rebate():
    gate = _gate(fee=-0.5)
    assert gate.active
    assert gate.zero_rebate


def test_meaningful_negative_rebate_bypasses_gate():
    gate = _gate(fee=-1.01)
    assert not gate.active
    assert gate.reason == "MAKER_REBATE_PRESENT"


def test_gate_is_only_an_entry_floor_not_an_exit_or_size_retune():
    assert "maker_size = min_size" in SRC
    assert "apply_positive_maker_kappa_veto(" in SRC
    assert "research_max_total_abs_base=2.0" in (ROOT / "run_strategy1_research_simple_multi.sh").read_text()


def test_effective_floor_blocks_14_bps_but_allows_15_bps_in_target_regime():
    from research_direct_economics import ACTION_MAKER, ACTION_SKIP, choose_direct_execution

    gate = _gate()
    blocked = choose_direct_execution(
        maker_lifecycle_ev=1.0,
        maker_current_edge_bps=14.0,
        maker_min_edge_bps=gate.effective_min_edge_bps,
        directional_score=0.0,
        crossing_bps=14.0,
        maker_size=0.25,
        taker_clip=0.25,
        neutral_fallback=True,
    )
    allowed = choose_direct_execution(
        maker_lifecycle_ev=1.0,
        maker_current_edge_bps=15.0,
        maker_min_edge_bps=gate.effective_min_edge_bps,
        directional_score=0.0,
        crossing_bps=15.0,
        maker_size=0.25,
        taker_clip=0.25,
        neutral_fallback=True,
    )
    assert blocked.action == ACTION_SKIP
    assert allowed.action == ACTION_MAKER


def test_same_14_bps_entry_remains_allowed_outside_target_regime():
    from research_direct_economics import ACTION_MAKER, choose_direct_execution

    gate = _gate(regime="TRENDING")
    decision = choose_direct_execution(
        maker_lifecycle_ev=1.0,
        maker_current_edge_bps=14.0,
        maker_min_edge_bps=gate.effective_min_edge_bps,
        directional_score=0.0,
        crossing_bps=14.0,
        maker_size=0.25,
        taker_clip=0.25,
        neutral_fallback=True,
    )
    assert decision.action == ACTION_MAKER
