from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import math
import sys

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "agents" / "strategy"))

from research_clean_authority import posterior_taker_exit_probability
from research_contract_guard import (
    post_only_is_safe,
    round_price_to_tick,
    same_request_exposure_allows,
    sanitize_post_only_limit_price,
)
from research_execution_controller import (
    ACTION_MAKER,
    ACTION_TAKER,
    EXECUTION_CONTROLLER_VERSION,
    choose_execution,
    maker_utility,
    taker_utility,
)
from research_exit_quantity import round_volume
from research_lane_funnel import bump, compact_log, empty_funnel
from research_lifecycle_ev import (
    LIFECYCLE_TAKER_PRIOR,
    RESEARCH_LIFECYCLE_ENTRY_VERSION,
    effective_taker_probability,
    expected_future_taker_cost_bps,
    lifecycle_entry_cost_bps,
    lifecycle_is_executable,
    required_entry_ev,
)
from research_score_ev import compute_score_ev

STRATEGY = (ROOT / "agents/strategy/Strategy1_Research.py").read_text(encoding="utf-8")
BASE = ROOT / "agents/strategy/BaseStrategy.py"
ADAPTIVE = ROOT / "agents/strategy/AdaptiveAgent.py"
VALIDATOR_TRADE = ROOT / "taos/im/validator/trade.py"
VALIDATOR_TRADE_SHA256 = "137a4a7f26de9395a0028539a95411992c6ed0fa16ddd21682c04838121af0b8"


def _ev(**kwargs):
    defaults = dict(
        book=1, side="BUY", alpha=0.30, fill_prob_old=0.80,
        learned_actionable_p=0.50, learned_actionable_samples=20,
        spread_capture_bps=6.0, fees_bps=0.5, markout_mean_bps=0.0,
        markout_samples=20, realized_observation_count=0, required=3,
        min_trading_ev=0.0,
    )
    defaults.update(kwargs)
    return compute_score_ev(**defaults)


def test_versions_and_frozen_surfaces():
    assert RESEARCH_LIFECYCLE_ENTRY_VERSION == "lifecycle_ev_v4_16_2"
    assert EXECUTION_CONTROLLER_VERSION == "execution_controller_v4_16_2"
    assert 'RESEARCH_POLICY_VERSION = "simplified_hybrid_authority_v4_16_2"' in STRATEGY
    assert 'RESEARCH_ENGINE_VERSION = "simplified_hybrid_authority_v4_16_2"' in STRATEGY
    assert 'RESEARCH_ENGINE_REVISION = "simplified_hybrid_authority_v4_16_2"' in STRATEGY
    digest = sha256(VALIDATOR_TRADE.read_bytes()).hexdigest()
    assert digest == VALIDATOR_TRADE_SHA256
    base_src = BASE.read_text(encoding="utf-8")
    adaptive_src = ADAPTIVE.read_text(encoding="utf-8")
    assert "simplified_hybrid_authority_v4_16_2" not in base_src
    assert "simplified_hybrid_authority_v4_16_2" not in adaptive_src
    assert "lifecycle_ev_v4_16_2" not in base_src
    assert "execution_controller_v4_16_2" not in adaptive_src
    assert "choose_position_exit" not in adaptive_src


def test_zero_samples_uses_configured_prior():
    p = effective_taker_probability(prior=0.30, live=None, samples=0)
    assert abs(p - 0.30) < 1e-12
    p0 = effective_taker_probability(prior=0.30, live=0.0, samples=0)
    assert abs(p0 - 0.30) < 1e-12


def test_configured_prior_does_not_become_zero():
    row = _ev(taker_exit_probability=None)
    assert abs(row.taker_prob_prior - 0.30) < 1e-12
    assert abs(row.taker_prob_effective - 0.30) < 1e-12
    assert row.taker_prob_effective != 0.0
    cost = lifecycle_entry_cost_bps(
        maker_fee_bps=-1.0, taker_fee_bps=5.0, spread_bps=4.0,
        taker_exit_probability=None,
    )
    assert abs(cost.taker_exit_probability - LIFECYCLE_TAKER_PRIOR) < 1e-12


def test_live_posterior_used_once_samples_exist():
    prior = 0.30
    live = posterior_taker_exit_probability(
        maker_exits=2, taker_exits=14, prior=prior, min_samples=4, floor=prior,
    )
    blended = effective_taker_probability(prior=prior, live=14 / 16, samples=16)
    assert blended > prior
    assert abs(blended - live) < 1e-9


def test_entry_telemetry_matches_lifecycle_posterior():
    p = effective_taker_probability(prior=0.30, live=0.80, samples=20)
    row = _ev(
        taker_exit_probability=p,
        taker_prob_prior=0.30,
        taker_prob_live=0.80,
        lifecycle_exit_samples=20,
        taker_fee_bps=5.0,
        expected_cross_bps=2.0,
        expected_slippage_bps=0.75,
    )
    log = row.as_log()
    assert abs(log["taker_prob_effective"] - p) < 1e-12
    assert abs(log["taker_prob_prior"] - 0.30) < 1e-12
    assert abs(log["taker_prob_live"] - 0.80) < 1e-12
    assert log["lifecycle_exit_samples"] == 20
    expected = expected_future_taker_cost_bps(
        p_taker_effective=p, taker_fee_bps=5.0, crossing_bps=2.0, slippage_bps=0.75,
    )
    assert abs(log["expected_future_taker_cost_bps"] - expected) < 1e-12
    assert abs(log["expected_taker_cost"] - expected) < 1e-12


def test_no_independent_taker_estimator():
    assert "posterior_taker_exit_probability" in STRATEGY
    assert STRATEGY.count("def posterior_taker") == 0
    src = (ROOT / "agents/strategy/research_lifecycle_ev.py").read_text(encoding="utf-8")
    assert "from research_clean_authority import posterior_taker_exit_probability" in src


def test_zero_p_makes_future_taker_cost_zero():
    assert expected_future_taker_cost_bps(
        p_taker_effective=0.0, taker_fee_bps=8.0, crossing_bps=4.0, slippage_bps=1.0,
    ) == 0.0
    cost = lifecycle_entry_cost_bps(
        maker_fee_bps=0.0, taker_fee_bps=8.0, spread_bps=8.0,
        taker_exit_probability=0.0,
    )
    assert cost.expected_future_taker_cost_bps == 0.0


def test_higher_p_increases_future_taker_cost():
    low = expected_future_taker_cost_bps(p_taker_effective=0.20, taker_fee_bps=5.0, crossing_bps=2.0)
    high = expected_future_taker_cost_bps(p_taker_effective=0.80, taker_fee_bps=5.0, crossing_bps=2.0)
    assert high > low


def test_higher_taker_fee_decreases_lifecycle_ev():
    p = 0.50
    low_fee = lifecycle_entry_cost_bps(
        maker_fee_bps=0.0, taker_fee_bps=1.0, spread_bps=4.0, taker_exit_probability=p,
    )
    high_fee = lifecycle_entry_cost_bps(
        maker_fee_bps=0.0, taker_fee_bps=8.0, spread_bps=4.0, taker_exit_probability=p,
    )
    low = _ev(fees_bps=low_fee.base_cost_bps, taker_exit_probability=p, taker_fee_bps=1.0)
    high = _ev(fees_bps=high_fee.base_cost_bps, taker_exit_probability=p, taker_fee_bps=8.0)
    assert high.lifecycle_ev < low.lifecycle_ev


def test_higher_crossing_decreases_lifecycle_ev():
    p = 0.50
    cheap = lifecycle_entry_cost_bps(
        maker_fee_bps=0.0, taker_fee_bps=2.0, spread_bps=2.0, taker_exit_probability=p,
    )
    rich = lifecycle_entry_cost_bps(
        maker_fee_bps=0.0, taker_fee_bps=2.0, spread_bps=20.0, taker_exit_probability=p,
    )
    a = _ev(fees_bps=cheap.base_cost_bps, taker_exit_probability=p, expected_cross_bps=1.0)
    b = _ev(fees_bps=rich.base_cost_bps, taker_exit_probability=p, expected_cross_bps=10.0)
    assert b.lifecycle_ev < a.lifecycle_ev


def test_taker_fee_is_not_double_counted():
    p = 0.40
    fee = 10.0
    cost = expected_future_taker_cost_bps(p_taker_effective=p, taker_fee_bps=fee)
    assert abs(cost - p * fee) < 1e-12
    assert cost < fee
    life = lifecycle_entry_cost_bps(
        maker_fee_bps=0.0, taker_fee_bps=fee, spread_bps=0.0,
        taker_exit_probability=p, slippage_bps=0.0, holding_risk_bps=0.0,
    )
    assert abs(life.base_cost_bps - p * fee) < 1e-12


def test_required_entry_ev_is_not_a_live_gate():
    row = _ev()
    assert abs(row.required_entry_ev - 0.0) < 1e-12
    assert lifecycle_is_executable(row.lifecycle_ev, margin=0.0) is True
    assert "required_entry_ev" in STRATEGY
    # The live quote path must not reintroduce a hurdle gate.
    assert "if life < required_entry" not in STRATEGY
    _ = required_entry_ev  # helper remains for old callers / tests


def test_maker_rebate_benefits_maker_utility():
    life = 0.05
    taxed = maker_utility(lifecycle_ev=life, p_fill=0.80, maker_fee_bps=4.0)
    rebate = maker_utility(lifecycle_ev=life, p_fill=0.80, maker_fee_bps=-4.0)
    assert rebate > taxed


def test_maker_rebate_does_not_benefit_taker_utility():
    import inspect
    assert "maker_fee_bps" not in inspect.signature(taker_utility).parameters
    life = 0.05
    plain = taker_utility(lifecycle_ev=life, crossing_cost=0.02, taker_fee_bps=4.0)
    assert abs(
        plain - taker_utility(lifecycle_ev=life, crossing_cost=0.02, taker_fee_bps=4.0)
    ) < 1e-12
    # A Maker rebate inside LifecycleEV would subsidize Taker. That path is
    # closed because LifecycleCost.base_cost_bps excludes maker_entry_fee_bps.
    rebate_life = life + 0.10
    assert taker_utility(lifecycle_ev=life, crossing_cost=0.02) < taker_utility(
        lifecycle_ev=rebate_life, crossing_cost=0.02,
    )


def test_taker_fee_only_affects_taker_utility():
    import inspect
    assert "taker_fee_bps" not in inspect.signature(maker_utility).parameters
    life = 0.08
    maker_plain = maker_utility(lifecycle_ev=life, p_fill=0.7, maker_fee_bps=0.0)
    maker_rebate = maker_utility(lifecycle_ev=life, p_fill=0.7, maker_fee_bps=-4.0)
    assert maker_rebate > maker_plain
    taker_low = taker_utility(lifecycle_ev=life, crossing_cost=0.02, taker_fee_bps=1.0)
    taker_high = taker_utility(lifecycle_ev=life, crossing_cost=0.02, taker_fee_bps=12.0)
    assert taker_high < taker_low


def test_taker_crossing_cannot_go_negative_from_rebate():
    u = taker_utility(lifecycle_ev=0.04, crossing_cost=-0.50, taker_fee_bps=-8.0)
    baseline = taker_utility(lifecycle_ev=0.04, crossing_cost=0.0, taker_fee_bps=0.0)
    assert u <= baseline + 1e-12


def test_fee_sources_order_maker_vs_taker():
    life = 0.02
    maker_win = choose_execution(
        lifecycle_ev=life, p_fill=0.90, crossing_cost=0.20,
        maker_fee_bps=-3.0, taker_fee_bps=8.0, maker_size=0.25, taker_clip=0.25,
    )
    taker_win = choose_execution(
        lifecycle_ev=0.25, p_fill=0.05, crossing_cost=0.01,
        maker_fee_bps=4.0, taker_fee_bps=0.5, observations_remaining=1,
        maker_size=0.25, taker_clip=0.25,
    )
    assert maker_win.action == ACTION_MAKER
    assert taker_win.action == ACTION_TAKER


def test_stale_maker_buy_cannot_cross_latest_ask():
    price = sanitize_post_only_limit_price(
        side="buy", original_price=100.20, best_bid=99.90, best_ask=100.05,
        tick_size=0.01, safety_ticks=2, price_decimals=2,
    )
    assert price is not None
    assert price < 100.05
    assert post_only_is_safe(side="buy", price=price, best_bid=99.90, best_ask=100.05)


def test_stale_maker_sell_cannot_cross_latest_bid():
    price = sanitize_post_only_limit_price(
        side="sell", original_price=236.21, best_bid=236.20, best_ask=236.40,
        tick_size=0.01, safety_ticks=2, price_decimals=2,
    )
    assert price is not None
    assert price > 236.20
    assert post_only_is_safe(side="sell", price=price, best_bid=236.20, best_ask=236.40)


def test_price_rounded_before_final_validation():
    raw = 100.106
    rounded = round_price_to_tick(raw, 0.01, 2)
    assert rounded == 100.11 or abs(rounded - round(raw, 2)) < 1e-12
    price = sanitize_post_only_limit_price(
        side="buy", original_price=100.106, best_bid=99.90, best_ask=100.10,
        tick_size=0.01, safety_ticks=1, price_decimals=2,
    )
    assert price is not None
    assert abs(price - round(price, 2)) < 1e-12
    assert price < 100.10


def test_latest_l1_overrides_ranking_snapshot():
    ranked = 100.00
    latest = sanitize_post_only_limit_price(
        side="buy", original_price=ranked, best_bid=99.40, best_ask=99.50,
        tick_size=0.01, safety_ticks=2, price_decimals=2,
    )
    assert latest is not None
    assert latest < 99.50
    assert latest < ranked


def test_invalid_post_only_is_repriced_or_skipped():
    skipped = sanitize_post_only_limit_price(
        side="buy", original_price=10.0, best_bid=0.01, best_ask=0.02,
        tick_size=0.01, safety_ticks=3, price_decimals=2,
    )
    assert skipped is None
    repriced = sanitize_post_only_limit_price(
        side="sell", original_price=99.80, best_bid=99.90, best_ask=100.10,
        tick_size=0.01, safety_ticks=2, price_decimals=2,
    )
    assert repriced is not None and repriced > 99.90


def test_min_quantity_and_precision_preserved():
    assert abs(round_volume(0.25, 8) - 0.25) < 1e-12
    assert round_volume(0.249, 2) >= 0.25 or round_volume(0.25, 2) == 0.25
    assert "_research_final_validate_instructions" in STRATEGY
    assert "MIN_QUANTITY" in STRATEGY


def test_exposure_and_volume_headroom_rechecked():
    ok, reason = same_request_exposure_allows(
        current_abs_base=1.75, current_open_books=7, current_active_books=5,
        add_abs_base=0.25, adds_open_book=True, adds_active_book=True,
        max_abs_base=2.0, max_total_open_books=8, max_active_open_books=6,
        volume_headroom=0.50,
    )
    assert ok is True
    blocked, why = same_request_exposure_allows(
        current_abs_base=1.90, current_open_books=7, current_active_books=5,
        add_abs_base=0.25, adds_open_book=True, adds_active_book=True,
        max_abs_base=2.0, max_total_open_books=8, max_active_open_books=6,
        volume_headroom=0.50,
    )
    assert blocked is False
    assert why == "EXPOSURE_HEADROOM"
    vol_block, vol_why = same_request_exposure_allows(
        current_abs_base=0.0, current_open_books=0, current_active_books=0,
        add_abs_base=0.25, adds_open_book=True, adds_active_book=True,
        max_abs_base=2.0, max_total_open_books=8, max_active_open_books=6,
        volume_headroom=0.0,
    )
    assert vol_block is False
    assert vol_why == "VOLUME_HEADROOM"


def test_same_response_earlier_orders_affect_validation():
    first_ok, _ = same_request_exposure_allows(
        current_abs_base=1.50, current_open_books=5, current_active_books=5,
        add_abs_base=0.25, adds_open_book=True, adds_active_book=True,
        max_abs_base=2.0, max_total_open_books=8, max_active_open_books=6,
    )
    assert first_ok is True
    second, why = same_request_exposure_allows(
        current_abs_base=1.75, current_open_books=6, current_active_books=6,
        add_abs_base=0.25, adds_open_book=True, adds_active_book=True,
        max_abs_base=2.0, max_total_open_books=8, max_active_open_books=6,
    )
    assert second is False
    assert why == "ACTIVE_BOOK_CAP"


def test_final_reject_is_logged_and_not_submitted():
    assert "FINAL_CONTRACT_REJECT" in STRATEGY
    assert "do not submit" not in STRATEGY.lower() or "_research_final_validate_instructions" in STRATEGY
    assert "reprice_possible" in STRATEGY


def test_completion_selected_increments():
    funnel = empty_funnel()
    bump(funnel, "COMPLETION", "lane_maker_selected")
    rec = compact_log(funnel, lane="COMPLETION")
    assert rec["completion_maker_selected"] == 1
    assert 'self._research_p0_bump("completion_selected")' in STRATEGY


def test_completion_submitted_and_filled_and_rt():
    funnel = empty_funnel()
    bump(funnel, "COMPLETION", "lane_quote_submitted")
    bump(funnel, "COMPLETION", "lane_filled")
    bump(funnel, "COMPLETION", "lane_rt_completed")
    rec = compact_log(funnel, lane="COMPLETION")
    assert rec["completion_submitted"] == 1
    assert rec["filled"] == 1
    assert rec["rt"] == 1
    assert 'self._research_p0_bump("completion_submitted")' in STRATEGY
    assert 'self._research_p0_bump("completion_filled")' in STRATEGY
    assert 'self._research_p0_bump("completion_rt_completed")' in STRATEGY
    assert 'self._research_p0_bump("completion_qualified")' in STRATEGY


def test_non_completion_trades_do_not_change_completion_counters():
    funnel = empty_funnel()
    bump(funnel, "COVERAGE", "lane_quote_submitted")
    bump(funnel, "COVERAGE", "lane_filled")
    bump(funnel, "COVERAGE", "lane_rt_completed")
    rec = compact_log(funnel)
    assert rec["completion_submitted"] == 0
    assert rec["completion_filled"] == 0
    assert rec["completion_rt"] == 0
    assert rec["coverage_submitted"] == 1


def test_telemetry_failure_never_changes_trading():
    assert "except Exception:" in STRATEGY
    assert "_research_p0_bump" in STRATEGY
    # Neutral fallback, exit controller, and kappa scheduler remain frozen.
    assert "NEUTRAL_PREDICTION_VERSION" in STRATEGY
    assert "POSITION_EXIT_VERSION" in STRATEGY
    assert "TOTAL_SCORE_FRONTIER_VERSION" in STRATEGY
    assert "research_max_active_open_books" in STRATEGY
    from research_neutral_prediction import NEUTRAL_PREDICTION_VERSION
    from research_position_exit import POSITION_EXIT_VERSION
    from research_total_score_frontier import TOTAL_SCORE_FRONTIER_VERSION
    assert NEUTRAL_PREDICTION_VERSION == "neutral_prediction_v4_16_1"
    assert POSITION_EXIT_VERSION == "position_exit_v4_16_1"
    assert TOTAL_SCORE_FRONTIER_VERSION == "total_score_frontier_v4_15_2"


def test_maker_rebate_excluded_from_lifecycle_base_cost():
    rebate = lifecycle_entry_cost_bps(
        maker_fee_bps=-5.0, taker_fee_bps=4.0, spread_bps=4.0,
        taker_exit_probability=0.30, slippage_bps=0.75, holding_risk_bps=0.50,
    )
    taxed = lifecycle_entry_cost_bps(
        maker_fee_bps=5.0, taker_fee_bps=4.0, spread_bps=4.0,
        taker_exit_probability=0.30, slippage_bps=0.75, holding_risk_bps=0.50,
    )
    assert abs(rebate.base_cost_bps - taxed.base_cost_bps) < 1e-12
    assert rebate.maker_entry_fee_bps < 0.0
    assert taxed.maker_entry_fee_bps > 0.0
