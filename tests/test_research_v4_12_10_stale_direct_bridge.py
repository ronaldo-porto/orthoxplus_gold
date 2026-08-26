from pathlib import Path
import sys

STRATEGY_DIR = Path(__file__).resolve().parents[1] / "agents" / "strategy"
if str(STRATEGY_DIR) not in sys.path:
    sys.path.insert(0, str(STRATEGY_DIR))

from research_unified_exit import (
    ACTION_TAKER_STALE_BRIDGE,
    UNIFIED_EXIT_VERSION,
    bounded_stale_direct_bridge,
)


def bridge(**overrides):
    args = dict(
        legacy_direct_authorized=True,
        positive_ev_authorized=True,
        sn79_take=True,
        legacy_taker_ev_bps=0.10,
        legacy_wait_ev_bps=-20.0,
        actual_roundtrip_taker_net_bps=-5.2,
        p_maker_fill=0.02,
        maker_fill_evidence=True,
        failed_exit_count=20,
        inventory_age=30.0,
        min_failed_exits=8,
        min_age_ticks=16.0,
        max_maker_fill=0.08,
        ev_advantage_bps=0.50,
        roundtrip_loss_floor_bps=-12.0,
    )
    args.update(overrides)
    return bounded_stale_direct_bridge(**args)


def test_v41210_contract_marker():
    assert UNIFIED_EXIT_VERSION == "bounded_stale_bridge_v4_12_10"
    assert ACTION_TAKER_STALE_BRIDGE == "TAKER_STALE_BRIDGE"


def test_log_replay_like_stale_positive_ev_case_is_bridged():
    # Mirrors the V4.12.9 book-8 blocker: incremental exit EV is non-negative,
    # Maker fill is ~1%, waiting is far worse, while total RT net is ~-5 bps.
    assert bridge(
        legacy_taker_ev_bps=0.0044,
        legacy_wait_ev_bps=-19.89,
        actual_roundtrip_taker_net_bps=-5.17,
        p_maker_fill=0.0129,
        failed_exit_count=188,
        inventory_age=190.0,
    )


def test_hard_roundtrip_floor_blocks_large_loss():
    assert not bridge(actual_roundtrip_taker_net_bps=-12.01)


def test_config_cannot_widen_hard_minus_12_floor():
    assert not bridge(
        actual_roundtrip_taker_net_bps=-15.0,
        roundtrip_loss_floor_bps=-30.0,
    )


def test_requires_real_fill_evidence_and_low_fill():
    assert not bridge(maker_fill_evidence=False)
    assert not bridge(p_maker_fill=0.081)


def test_requires_both_stale_age_and_repeated_failures():
    assert not bridge(failed_exit_count=7)
    assert not bridge(inventory_age=15.99)


def test_requires_existing_positive_ev_direct_authority_and_sn79_agreement():
    assert not bridge(legacy_direct_authorized=False)
    assert not bridge(positive_ev_authorized=False)
    assert not bridge(sn79_take=False)


def test_requires_incremental_nonnegative_ev_and_advantage_over_wait():
    assert not bridge(legacy_taker_ev_bps=-0.001)
    assert not bridge(legacy_taker_ev_bps=0.1, legacy_wait_ev_bps=-0.2, ev_advantage_bps=0.5)


def test_strategy_wires_bridge_into_final_keep_maker_veto():
    src = (STRATEGY_DIR / "Strategy1_Research.py").read_text(encoding="utf-8")
    assert "bridge = bounded_stale_direct_bridge(" in src
    assert "unified.action == UNIFIED_TAKER_STALE_BRIDGE" in src
    assert 'research_unified_stale_bridge_roundtrip_floor_bps", -12.0' in src
    assert 'reason="BOUNDED_STALE_DIRECT_GT_WAIT"' in src
