from pathlib import Path

from research_inventory_liveness import (
    BOUNDED_LOSS_ESCAPE_VERSION,
    bounded_loss_escape_applies,
    bounded_loss_escape_reason,
    counts_against_productive_open_cap,
)

ROOT = Path(__file__).parents[1]
SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_test_multi.sh").read_text()


def test_dust_is_risk_accounted_but_not_a_productive_slot():
    assert not counts_against_productive_open_cap(
        has_inventory=True, is_liveness_parked=False, is_dust=True
    )
    assert not counts_against_productive_open_cap(
        has_inventory=True, is_liveness_parked=True, is_dust=False
    )
    assert counts_against_productive_open_cap(
        has_inventory=True, is_liveness_parked=False, is_dust=False
    )
    assert "dust_nonflat_inventory" in SRC
    assert "productive_active_nonflat_inventory" in SRC


def test_soft_corridor_holds_profitable_maker_for_agent23_shapes():
    for taker, maker in [(-9.41, 170.68), (-12.45, 85.22), (-14.17, 84.98), (-16.46, 87.64)]:
        assert bounded_loss_escape_reason(
            taker_net_bps=taker, peak_taker_net_bps=-5.0, inventory_age=2,
            maker_net_bps=maker, failed_exit_count=1,
        ) == "SOFT_MAKER_HOLD"
        assert not bounded_loss_escape_applies(
            taker_net_bps=taker, peak_taker_net_bps=-5.0, inventory_age=2,
            maker_net_bps=maker, failed_exit_count=1,
        )


def test_hard_corridor_still_recycles_tail_risk_even_with_profitable_maker():
    assert bounded_loss_escape_reason(
        taker_net_bps=-21.72, peak_taker_net_bps=-5.0, inventory_age=2,
        maker_net_bps=72.35, failed_exit_count=1,
    ) == "HARD_ESCAPE"
    assert bounded_loss_escape_applies(
        taker_net_bps=-21.72, peak_taker_net_bps=-5.0, inventory_age=2,
        maker_net_bps=72.35, failed_exit_count=1,
    )


def test_soft_hold_is_bounded_by_existing_age_and_failure_knobs():
    assert bounded_loss_escape_reason(
        taker_net_bps=-14.0, peak_taker_net_bps=-5.0, inventory_age=8,
        maker_net_bps=80.0, failed_exit_count=1,
    ) == "SOFT_ESCAPE"
    assert bounded_loss_escape_reason(
        taker_net_bps=-14.0, peak_taker_net_bps=-5.0, inventory_age=2,
        maker_net_bps=80.0, failed_exit_count=4,
    ) == "SOFT_ESCAPE"


def test_v4143_wiring_is_minimal_and_keeps_hard_caps():
    assert BOUNDED_LOSS_ESCAPE_VERSION == "two_stage_bounded_loss_escape_v4_14_3"
    assert 'RESEARCH_POLICY_VERSION = "simplified_hybrid_authority_v4_16_1"' in SRC
    assert "research_bounded_loss_escape_hard_trigger_bps=-18.0" in LAUNCHER
    assert "research_max_active_open_books=6" in LAUNCHER
    assert "research_max_total_open_books=8" in LAUNCHER
    assert "research_max_total_abs_base=2.0" in LAUNCHER
    assert "choose_position_exit" in SRC
    assert "HARD_ESCAPE" in SRC
