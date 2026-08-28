from pathlib import Path

from research_inventory_liveness import (
    BOUNDED_LOSS_ESCAPE_VERSION,
    bounded_loss_escape_applies,
)

ROOT = Path(__file__).parents[1]
SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_test_multi.sh").read_text()


def test_book24_and_book25_long_run_traps_escape_while_small():
    # Long-run evidence: both positions had a cheap loss at age 2, then became
    # multi-hour parked tails. V4.14 must recycle them before that happens.
    assert bounded_loss_escape_applies(
        taker_net_bps=-16.28,
        peak_taker_net_bps=-11.43,
        inventory_age=2,
    )
    assert bounded_loss_escape_applies(
        taker_net_bps=-14.82,
        peak_taker_net_bps=-10.49,
        inventory_age=2,
    )


def test_escape_is_bounded_not_a_panic_dump():
    assert not bounded_loss_escape_applies(
        taker_net_bps=-7.9, peak_taker_net_bps=-4.0, inventory_age=3
    )
    assert not bounded_loss_escape_applies(
        taker_net_bps=-25.01, peak_taker_net_bps=-10.0, inventory_age=3
    )
    assert not bounded_loss_escape_applies(
        taker_net_bps=-15.0, peak_taker_net_bps=-14.0, inventory_age=3
    )
    assert not bounded_loss_escape_applies(
        taker_net_bps=-15.0, peak_taker_net_bps=-10.0, inventory_age=1
    )


def test_escape_is_inclusive_at_configured_corridor_edges():
    assert bounded_loss_escape_applies(
        taker_net_bps=-8.0, peak_taker_net_bps=-5.0, inventory_age=2
    )
    assert bounded_loss_escape_applies(
        taker_net_bps=-25.0, peak_taker_net_bps=-20.0, inventory_age=2
    )


def test_escape_can_be_disabled():
    assert not bounded_loss_escape_applies(
        taker_net_bps=-15.0,
        peak_taker_net_bps=-10.0,
        inventory_age=5,
        enabled=False,
    )


def test_v4140_wiring_and_long_run_capacity_profile():
    assert BOUNDED_LOSS_ESCAPE_VERSION == "bounded_loss_escape_v4_14_0"
    assert 'RESEARCH_POLICY_VERSION = "wide_kappa_wave_v4_14_2"' in SRC
    assert 'reason="BOUNDED_LOSS_ESCAPE"' in SRC
    assert 'pressure_reason = "PARKED_RECYCLE"' not in SRC
    assert "research_max_total_open_books=8" in LAUNCHER
    assert "research_max_parked_open_books=4" in LAUNCHER
    assert "research_max_total_abs_base=2.0" in LAUNCHER
    assert "research_positive_maker_veto_max_failed_exits=4" in LAUNCHER
    assert "research_bounded_loss_escape_enabled=1" in LAUNCHER
    assert "research_bounded_loss_escape_min_age_ticks=2" in LAUNCHER
    assert "research_bounded_loss_escape_floor_bps=-25.0" in LAUNCHER
    assert "research_bounded_loss_escape_drawdown_bps=2.0" in LAUNCHER
