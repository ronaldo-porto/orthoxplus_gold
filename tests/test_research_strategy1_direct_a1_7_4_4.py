from pathlib import Path

from research_direct_exit import choose_observable_position_exit
from research_direct_positive_maker_kappa import (
    DIRECT_A1744_STRONG_MAKER_FLOOR_BPS,
    DIRECT_POSITIVE_MAKER_KAPPA_VERSION,
    apply_positive_maker_kappa_veto,
    classify_a1744_outcome,
)
from research_position_exit import ACTION_MAKER_EXIT, ACTION_TAKER_EXIT

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "agents/strategy/Strategy1_Research_Simple.py").read_text()


def _base(*, unrealized_bps, maker_net_bps, taker_net_bps, failed_exit_count=100,
          catastrophic_hard_risk=False):
    return choose_observable_position_exit(
        maker_net_bps=maker_net_bps,
        taker_net_bps=taker_net_bps,
        p_maker_fill=0.05,
        unrealized_bps=unrealized_bps,
        inventory_qty=0.25,
        inventory_age=100,
        failed_exit_count=failed_exit_count,
        maker_executable=True,
        catastrophic_hard_risk=catastrophic_hard_risk,
        min_order=0.25,
        taker_clip=0.25,
        reduction_executable=True,
        is_dust=False,
        valid_opposite_touch=True,
        positive_maker_veto_enabled=True,
        positive_maker_veto_floor_bps=1.0,
        positive_maker_veto_max_failed_exits=4,
        absolute_positive_maker_veto_enabled=True,
        absolute_positive_maker_veto_floor_bps=1.0,
        absolute_positive_maker_veto_max_failed_exits=1,
    )


def _apply(base, *, maker, taker, catastrophic=False, maker_executable=True):
    return apply_positive_maker_kappa_veto(
        base_decision=base, maker_net_bps=maker, taker_net_bps=taker,
        maker_executable=maker_executable,
        catastrophic_hard_risk=catastrophic, inventory_qty=0.25,
    )


def test_version_and_integration_markers_present():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_7_4_4"' in SRC
    assert DIRECT_POSITIVE_MAKER_KAPPA_VERSION == "direct_positive_maker_kappa_v4_16_2_a1_7_4_4"
    assert "apply_positive_maker_kappa_veto(" in SRC
    assert "A1744_POSITIVE_MAKER_RISK_VETO" in SRC
    assert "A1744_TAKER_ALLOWED_CATASTROPHIC" in SRC
    assert "A1744_TAKER_ALLOWED_MAKER_NOT_STRONG" in SRC
    assert "A1744_VETO_RELEASE" in SRC


def test_hard_escape_strong_positive_maker_veto_ignores_failed_exit_escalation():
    base = _base(unrealized_bps=-25.0, maker_net_bps=54.2, taker_net_bps=-27.8, failed_exit_count=103)
    assert base.action == ACTION_TAKER_EXIT
    assert base.reason == "HARD_ESCAPE_CLIP"
    final = _apply(base, maker=54.2, taker=-27.8)
    assert final.action == ACTION_MAKER_EXIT
    assert final.reason == "A1744_POSITIVE_MAKER_RISK_VETO"


def test_absolute_strong_positive_maker_veto_ignores_failed_exit_escalation():
    base = _base(unrealized_bps=-60.0, maker_net_bps=190.1, taker_net_bps=-54.9, failed_exit_count=21)
    assert base.action == ACTION_TAKER_EXIT
    assert base.reason == "ABSOLUTE_PROTECTION_REDUCE"
    final = _apply(base, maker=190.1, taker=-54.9)
    assert final.action == ACTION_MAKER_EXIT
    assert final.reason == "A1744_POSITIVE_MAKER_RISK_VETO"


def test_catastrophic_authority_is_never_vetoed():
    base = _base(unrealized_bps=-60.0, maker_net_bps=200.0, taker_net_bps=-80.0,
                 failed_exit_count=100, catastrophic_hard_risk=True)
    assert base.action == ACTION_TAKER_EXIT
    final = _apply(base, maker=200.0, taker=-80.0, catastrophic=True)
    assert final.action == ACTION_TAKER_EXIT
    assert classify_a1744_outcome(
        base_decision=base, final_decision=final, maker_net_bps=200.0,
        taker_net_bps=-80.0, maker_executable=True,
        catastrophic_hard_risk=True,
    ) == "A1744_TAKER_ALLOWED_CATASTROPHIC"


def test_marginal_positive_maker_does_not_create_new_liveness_policy():
    maker = DIRECT_A1744_STRONG_MAKER_FLOOR_BPS - 0.01
    base = _base(unrealized_bps=-25.0, maker_net_bps=maker, taker_net_bps=-30.0,
                 failed_exit_count=100)
    assert base.action == ACTION_TAKER_EXIT
    final = _apply(base, maker=maker, taker=-30.0)
    assert final.action == ACTION_TAKER_EXIT
    assert classify_a1744_outcome(
        base_decision=base, final_decision=final, maker_net_bps=maker,
        taker_net_bps=-30.0, maker_executable=True,
        catastrophic_hard_risk=False,
    ) == "A1744_TAKER_ALLOWED_MAKER_NOT_STRONG"


def test_non_executable_maker_does_not_veto():
    base = _base(unrealized_bps=-25.0, maker_net_bps=100.0, taker_net_bps=-30.0,
                 failed_exit_count=100)
    final = _apply(base, maker=100.0, taker=-30.0, maker_executable=False)
    assert final.action == ACTION_TAKER_EXIT


def test_nonnegative_taker_is_outside_kappa_tail_scope():
    # Construct via the same dataclass shape but force the economic input used by
    # A1.7.4.4 to non-negative. The patch must not veto profitable crossing.
    base = _base(unrealized_bps=-25.0, maker_net_bps=100.0, taker_net_bps=-1.0,
                 failed_exit_count=100)
    final = _apply(base, maker=100.0, taker=0.5)
    assert final.action == ACTION_TAKER_EXIT


def test_unrelated_decisions_are_unchanged():
    base = _base(unrealized_bps=-2.0, maker_net_bps=20.0, taker_net_bps=-5.0,
                 failed_exit_count=100)
    assert base.action == ACTION_MAKER_EXIT
    final = _apply(base, maker=20.0, taker=-5.0)
    assert final == base
