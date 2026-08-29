from research_quote_hysteresis import (
    PROFITABLE_EXIT_PERSISTENCE_VERSION,
    profitable_maker_exit_ttl_ms,
    hold_existing_profitable_maker_exit,
)


def test_version():
    assert PROFITABLE_EXIT_PERSISTENCE_VERSION == "profitable_maker_exit_persistence_v4_13_8"


def test_profitable_exit_extends_ttl():
    ttl, active = profitable_maker_exit_ttl_ms(
        baseline_ttl_ms=950.0, maker_net_bps=0.08, market_regime="NORMAL",
        persistent_ttl_ms=3000.0,
    )
    assert active is True
    assert ttl == 3000.0


def test_nonpositive_or_toxic_exit_does_not_extend():
    ttl, active = profitable_maker_exit_ttl_ms(
        baseline_ttl_ms=950.0, maker_net_bps=0.0, market_regime="NORMAL",
        persistent_ttl_ms=3000.0,
    )
    assert active is False and ttl == 950.0
    ttl, active = profitable_maker_exit_ttl_ms(
        baseline_ttl_ms=950.0, maker_net_bps=5.0, market_regime="TOXIC",
        persistent_ttl_ms=3000.0,
    )
    assert active is False and ttl == 950.0


def test_hold_existing_profitable_exit_inside_reprice_band():
    assert hold_existing_profitable_maker_exit(
        existing_price=100.00, desired_price=100.02, tick_size=0.01,
        existing_qty=0.25, desired_qty=0.25, maker_net_bps=0.08,
        reprice_ticks=3.0,
    ) is True


def test_reprice_when_touch_moves_materially_or_size_insufficient():
    assert hold_existing_profitable_maker_exit(
        existing_price=100.00, desired_price=100.04, tick_size=0.01,
        existing_qty=0.25, desired_qty=0.25, maker_net_bps=0.08,
        reprice_ticks=3.0,
    ) is False
    assert hold_existing_profitable_maker_exit(
        existing_price=100.00, desired_price=100.01, tick_size=0.01,
        existing_qty=0.10, desired_qty=0.25, maker_net_bps=0.08,
        reprice_ticks=3.0,
    ) is False


def test_nonpositive_exit_never_held():
    assert hold_existing_profitable_maker_exit(
        existing_price=100.00, desired_price=100.01, tick_size=0.01,
        existing_qty=0.25, desired_qty=0.25, maker_net_bps=-0.01,
        reprice_ticks=3.0,
    ) is False


def test_strategy_wires_profitable_exit_persistence_contract():
    from pathlib import Path
    src = Path("agents/strategy/Strategy1_Research.py").read_text(encoding="utf-8")
    assert 'RESEARCH_POLICY_VERSION = "realnet_authority_rotation_v4_14_4"' in src
    assert 'research_profitable_exit_ttl_ms' in src
    assert 'PROFITABLE_EXIT_HOLD' in src
    assert 'PROFITABLE_EXIT_PERSIST' in src
    assert 'maker_net_bps=exit_maker_net_bps' in src
