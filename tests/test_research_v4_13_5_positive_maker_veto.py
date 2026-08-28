from pathlib import Path

from research_inventory_liveness import (
    POSITIVE_MAKER_VETO_VERSION,
    positive_maker_rescue_veto_applies,
)

ROOT = Path(__file__).parents[1]
SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_test_multi.sh").read_text()


def veto(maker, taker, failed, **kw):
    args = dict(
        maker_net_bps=maker,
        taker_net_bps=taker,
        maker_executable=True,
        failed_exit_count=failed,
        stop_loss_hit=False,
        inventory_state="NORMAL",
        hard_risk=False,
        enabled=True,
        maker_positive_floor_bps=1.0,
        max_failed_exits=3,
    )
    args.update(kw)
    return positive_maker_rescue_veto_applies(**args)


def test_v4135_version_and_wiring_contract():
    assert POSITIVE_MAKER_VETO_VERSION == "positive_maker_veto_v4_13_5"
    assert 'RESEARCH_POLICY_VERSION = "wide_kappa_wave_v4_14_2"' in SRC
    assert "positive_maker_rescue_veto_applies(" in SRC
    assert 'reason="POSITIVE_MAKER_VETO"' in SRC
    assert "and not positive_maker_veto_active" in SRC
    assert "research_positive_maker_veto_enabled=1" in LAUNCHER
    assert "research_positive_maker_veto_floor_bps=1.0" in LAUNCHER
    assert "research_positive_maker_veto_max_failed_exits=4" in LAUNCHER


def test_exact_v4134_negative_taker_over_positive_maker_failures_are_vetoed():
    # Book14, Book44, Book41, Book70, Book55 from the 859-tick V4.13.4 run.
    cases = [
        (13.42, -10.79, 2),
        (18.57, -10.87, 1),
        (5.35, -6.63, 2),
        (13.89, -9.14, 1),
        (10.10, -4.74, 1),
    ]
    for maker, taker, failed in cases:
        assert veto(maker, taker, failed), (maker, taker, failed)


def test_book29_like_rescue_remains_available_when_maker_is_negative():
    assert not veto(-12.67, -10.05, 2)


def test_veto_releases_after_three_failed_exits():
    assert veto(10.0, -5.0, 2)
    assert not veto(10.0, -5.0, 3)
    assert not veto(10.0, -5.0, 8)


def test_veto_never_blocks_true_hard_risk_stop_or_emergency():
    assert not veto(10.0, -5.0, 1, hard_risk=True)
    assert not veto(10.0, -5.0, 1, stop_loss_hit=True)
    assert not veto(10.0, -5.0, 1, inventory_state="EXIT_ONLY")
    assert not veto(10.0, -5.0, 1, inventory_state="EMERGENCY")


def test_veto_requires_meaningful_executable_positive_maker_and_negative_taker():
    assert not veto(0.99, -5.0, 1)
    assert veto(1.0, -5.0, 1)
    assert not veto(10.0, 0.0, 1)
    assert not veto(10.0, 2.0, 1)
    assert not veto(10.0, -5.0, 1, maker_executable=False)


def test_veto_can_be_disabled_without_changing_existing_rescue_logic():
    assert not veto(10.0, -5.0, 1, enabled=False)
