from research_action_utility import (
    REASON_SN79_LOSS_FLOOR,
    REASON_SN79_TAKER,
    evaluate_sn79_action_utility,
)


def test_positive_pnl_fast_round_trip_prefers_taker():
    d = evaluate_sn79_action_utility(
        taker_net_pnl_bps=4.0,
        maker_expected_pnl_bps=1.5,
        p_maker_fill_horizon=0.08,
        observations_remaining=1,
        required_observations=3,
        inventory_ratio=0.35,
        holding_cost_bps=2.0,
        exit_urgency=0.55,
    )
    assert d.take is True
    assert d.reason == REASON_SN79_TAKER
    assert d.taker_utility > d.wait_utility
    assert d.recommended_qty_frac >= 0.75


def test_small_execution_concession_can_be_subsidized_by_kappa_and_rt_value():
    d = evaluate_sn79_action_utility(
        taker_net_pnl_bps=-0.75,
        maker_expected_pnl_bps=0.5,
        p_maker_fill_horizon=0.05,
        observations_remaining=1,
        required_observations=3,
        inventory_ratio=0.55,
        holding_cost_bps=3.0,
        exit_urgency=0.70,
        max_score_subsidy_loss_bps=-2.0,
    )
    assert d.take is True
    assert d.taker_utility > d.wait_utility


def test_large_negative_exit_is_never_score_subsidized():
    d = evaluate_sn79_action_utility(
        taker_net_pnl_bps=-6.0,
        maker_expected_pnl_bps=-1.0,
        p_maker_fill_horizon=0.05,
        observations_remaining=1,
        required_observations=3,
        inventory_ratio=0.95,
        holding_cost_bps=8.0,
        exit_urgency=0.95,
        max_score_subsidy_loss_bps=-2.0,
    )
    assert d.take is False
    assert d.reason == REASON_SN79_LOSS_FLOOR


def test_high_maker_fill_and_good_maker_ev_prefers_wait():
    d = evaluate_sn79_action_utility(
        taker_net_pnl_bps=1.0,
        maker_expected_pnl_bps=7.0,
        p_maker_fill_horizon=0.95,
        observations_remaining=1,
        required_observations=3,
        inventory_ratio=0.20,
        holding_cost_bps=1.0,
        exit_urgency=0.20,
    )
    assert d.take is False
    assert d.wait_utility >= d.taker_utility
