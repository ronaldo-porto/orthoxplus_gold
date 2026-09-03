from pathlib import Path
import ast
import math
import sys
import pytest

ROOT = Path(__file__).parents[1]
STRATEGY_DIR = ROOT / 'agents' / 'strategy'
sys.path.insert(0, str(STRATEGY_DIR))

PATH = STRATEGY_DIR / 'Strategy1_Research_Simple.py'
SRC = PATH.read_text(encoding='utf-8')
if 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_5_1"' not in SRC:
    pytest.skip('A1.5.1 historical contract superseded by current Direct candidate', allow_module_level=True)
TREE = ast.parse(SRC)
CLASS = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == 'Strategy1_Research_Simple')
METHODS = {n.name: n for n in CLASS.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

from research_direct_economics import (
    ACTION_MAKER,
    ACTION_SKIP,
    ACTION_TAKER,
    DIRECT_ECONOMICS_VERSION,
    DIRECT_EXECUTION_CONTROLLER_VERSION,
    DIRECT_MAKER_MIN_EV,
    DIRECT_TAKER_MIN_EV,
    DIRECT_TAKER_MIN_EDGE_BPS,
    DIRECT_TAKER_ENTRY_ENABLED,
    TAKER_ALPHA_SCALE_BPS,
    choose_direct_execution,
    direct_lifecycle_breakdown,
    maker_economic_ev,
    maker_lifecycle_fee_cost_bps,
    taker_economic_ev,
)
from research_direct_quality import (
    COLD_START_TAKER_RATE,
    DIRECT_QUALITY_VERSION,
    MIGRATED_QUALITY_INITIAL_WEIGHT,
    MakerLifecycleStats,
    maker_quality_adjustment,
    maker_realization_cost_estimate,
)
from research_direct_execution_quality import (
    DIRECT_DUST_EXEMPT_CAP,
    DIRECT_EXECUTION_QUALITY_VERSION,
    DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS,
    DIRECT_MAKER_MAX_TTL_MS,
    cap_maker_quote_geometry,
    direct_maker_expiry_ns,
    effective_total_open_books,
)
from research_direct_fastpath import (
    DIRECT_FASTPATH_VERSION, DIRECT_FASTPATH_CANDIDATE_COUNT,
    DIRECT_MAX_PRE_SUBMIT_AGE_MS, FastPathRow, cheap_priority, select_fastpath_rows,
)
from research_score_ev import compute_score_ev


def _ev(**kwargs):
    defaults = dict(
        book=1,
        side='MM',
        alpha=0.4,
        fill_prob_old=0.5,
        learned_actionable_p=0.5,
        learned_actionable_samples=20,
        spread_capture_bps=10.0,
        expected_markout_override=-2.0,
        fees_bps=2.0,
        realized_observation_count=0,
        required=3,
        min_trading_ev=0.0,
    )
    defaults.update(kwargs)
    return compute_score_ev(**defaults)


def test_direct_candidate_version():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_5_1"' in SRC
    assert 'RESEARCH_POLICY_VERSION = SIMPLE_POLICY_VERSION' in SRC
    assert DIRECT_ECONOMICS_VERSION == 'direct_economics_v4_16_2_a1_5_1'
    assert DIRECT_EXECUTION_CONTROLLER_VERSION == 'direct_execution_controller_v4_16_2_a1_5_1'
    assert DIRECT_QUALITY_VERSION == 'direct_maker_quality_v4_16_2_a1_5_1'
    assert abs(DIRECT_MAKER_MIN_EV - 0.030) < 1e-12
    assert abs(DIRECT_TAKER_MIN_EV - 0.20) < 1e-12
    assert abs(DIRECT_TAKER_MIN_EDGE_BPS - 2.0) < 1e-12
    assert abs(TAKER_ALPHA_SCALE_BPS - 4.0) < 1e-12
    assert DIRECT_EXECUTION_QUALITY_VERSION == 'direct_execution_quality_v4_16_2_a1_3'
    assert DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS == 6.0
    assert DIRECT_MAKER_MAX_TTL_MS == 75.0
    assert DIRECT_DUST_EXEMPT_CAP == 8
    assert abs(COLD_START_TAKER_RATE - 0.55) < 1e-12


def test_direct_candidate_remains_small_overlay():
    assert len(SRC.splitlines()) < 1400
    assert {
        'initialize', 'onTrade', '_research_on_own_fill', '_direct_quality_for_book',
        '_research_score_ev_for_book', '_research_lifecycle_entry_cost_bps', '_inventory_needs_management',
        'respond', '_simple_place_maker', '_place_skewed_quotes', '_direct_dust_count',
        '_research_fast_screen', 'select_books_for_trading', '_research_final_validate_instructions',
        '_research_read_session', '_research_save_session',
        '_research_clear_session_observations', 'build_mm_strategy_instructions',
    }.issubset(set(METHODS))


def test_hot_entry_path_still_has_one_execution_controller():
    assert 'choose_direct_execution(' in SRC
    assert 'choose_execution(' not in SRC
    assert 'Strategy1._place_skewed_quotes' not in SRC
    assert 'super()._place_skewed_quotes' not in SRC
    assert '_research_execute_entry_taker' in SRC
    assert '_simple_place_maker' in SRC


def test_legacy_entry_authorities_not_reintroduced():
    forbidden = [
        'admit_lane_candidate(', 'admit_scheduler_candidate(',
        '_schedule_maintenance_books(', '_place_directional_round_trip(',
        'research_enable_quote_hysteresis', 'research_enable_adaptive_ttl',
        'stale_maker_rescue', 'positive_maker_veto', 'required_entry_ev',
    ]
    for token in forbidden:
        assert token not in SRC


def test_a11_lifecycle_fix_is_preserved():
    base = _ev(
        learned_actionable_p=0.05,
        fill_prob_old=0.05,
        spread_capture_bps=16.0,
        fees_bps=6.0,
        latency_ms=150.0,
        latency_weight=0.04,
        adverse_weight=0.05,
    )
    assert base.trading_ev > 0.0
    assert base.eligible is False
    direct = direct_lifecycle_breakdown(base)
    assert direct.eligible is True
    assert direct.lifecycle_ev > 0.0
    assert direct.latency_cost > 0.0
    assert direct.adverse_selection_risk > 0.0
    assert direct.latency_penalty == 0.0
    assert direct.adverse_penalty == 0.0


def test_direct_lifecycle_still_does_not_rescue_negative_trading_economics():
    base = _ev(spread_capture_bps=1.0, fees_bps=5.0, latency_ms=0.0)
    assert base.trading_ev < 0.0
    direct = direct_lifecycle_breakdown(base)
    assert direct.eligible is False
    assert direct.reject_reason == 'NEGATIVE_EV'


def test_maker_lifecycle_is_not_multiplied_by_fill_probability_twice():
    life = 0.04
    assert abs(maker_economic_ev(lifecycle_ev=life, maker_fee_bps=0.0) - life) < 1e-12


def test_a12_maker_requires_model_error_margin():
    weak = choose_direct_execution(
        maker_lifecycle_ev=0.020,
        directional_score=0.0,
        crossing_bps=6.0,
        maker_size=0.25,
        taker_clip=0.25,
        neutral_fallback=True,
    )
    assert weak.action == ACTION_SKIP
    assert weak.maker_economic_ev > 0.0
    assert weak.maker_ev_margin < 0.0

    strong = choose_direct_execution(
        maker_lifecycle_ev=0.040,
        directional_score=0.0,
        crossing_bps=6.0,
        maker_size=0.25,
        taker_clip=0.25,
        neutral_fallback=True,
    )
    assert strong.action == ACTION_MAKER
    assert strong.reason == 'MAKER_MARGIN_EV'
    assert strong.maker_ev_margin > 0.0


def test_book14_style_wide_spread_cannot_be_subsidized_into_taker():
    d = choose_direct_execution(
        maker_lifecycle_ev=0.139,
        directional_score=1.0,
        crossing_bps=16.55,
        maker_size=0.25,
        taker_clip=0.25,
        taker_fee_bps=0.0,
        slippage_bps=0.75,
        expected_markout_bps=-2.0,
    )
    assert d.taker_economic_ev < 0.0
    assert d.action == ACTION_MAKER


def test_a14_moderate_directional_taker_is_rejected_after_calibration_haircut():
    # A1.3-style full-score trade with ~2.4 bps costs previously looked strongly
    # positive under the 8 bps alpha scale. A1.5.1 maps full score to 4 bps and
    # requires >=2 bps net edge, so this acquisition is skipped.
    ev, move, cost = taker_economic_ev(
        directional_score=1.0,
        crossing_bps=1.0,
        taker_fee_bps=0.4,
        slippage_bps=0.75,
        expected_markout_bps=-0.25,
    )
    assert abs(move - 4.0) < 1e-12
    assert move > cost and ev > 0.0
    d = choose_direct_execution(
        maker_lifecycle_ev=0.010,
        directional_score=1.0,
        crossing_bps=1.0,
        maker_size=0.25,
        taker_clip=0.25,
        taker_fee_bps=0.4,
        slippage_bps=0.75,
        expected_markout_bps=-0.25,
    )
    assert d.action == ACTION_SKIP
    assert d.taker_net_edge_bps < DIRECT_TAKER_MIN_EDGE_BPS


def test_a151_directional_taker_entry_is_disabled_even_when_counterfactual_ev_is_strong():
    d = choose_direct_execution(
        maker_lifecycle_ev=0.010,
        directional_score=1.0,
        crossing_bps=0.20,
        maker_size=0.25,
        taker_clip=0.25,
        taker_fee_bps=0.0,
        slippage_bps=0.20,
        expected_markout_bps=0.0,
    )
    assert DIRECT_TAKER_ENTRY_ENABLED is False
    assert d.taker_economic_ev > 0.0
    assert d.action == ACTION_SKIP
    assert d.taker_utility <= -1e8

def test_neutral_forecast_can_never_create_taker_entry():
    d = choose_direct_execution(
        maker_lifecycle_ev=0.02,
        directional_score=1.0,
        crossing_bps=0.1,
        maker_size=0.25,
        taker_clip=0.25,
        neutral_fallback=True,
    )
    assert d.action == ACTION_SKIP
    assert d.taker_utility <= -1e8


def test_sparse_book_has_no_quality_penalty():
    q = maker_quality_adjustment(stats=None)
    assert q.total_penalty == 0.0
    assert q.realization_drift_penalty == 0.0
    assert q.productivity_penalty == 0.0


def test_repeated_maker_to_taker_losses_create_bounded_quality_penalty():
    s = MakerLifecycleStats()
    for gross in (-7.0, -12.0, -5.0, -9.0, -6.0, -11.0):
        s.observe(gross_bps=gross, exit_is_taker=True)
    q = maker_quality_adjustment(
        stats=s,
        rolling_samples=8,
        rolling_loss_rate=0.875,
        rolling_realized_mean=-0.09,
    )
    assert q.realization_drift_penalty > 0.0
    assert q.productivity_penalty > 0.0
    assert 0.02 < q.total_penalty <= 0.04
    assert q.taker_exit_rate == 1.0


def test_good_maker_lifecycles_are_not_penalized():
    s = MakerLifecycleStats()
    for gross in (6.0, 8.0, 4.0, 10.0):
        s.observe(gross_bps=gross, exit_is_taker=False)
    q = maker_quality_adjustment(
        stats=s,
        rolling_samples=6,
        rolling_loss_rate=0.20,
        rolling_realized_mean=0.08,
    )
    assert q.total_penalty == 0.0


def test_a14_profitable_taker_exits_are_not_badness_even_when_frequent():
    s = MakerLifecycleStats()
    for gross in (7.7, 6.3, 3.7, 9.6, 3.7, 1.5, 64.0, 59.9):
        s.observe(gross_bps=gross, exit_is_taker=True)
    assert s.taker_exit_rate == 1.0
    assert s.taker_shortfall_bps_ewma == 0.0
    q = maker_quality_adjustment(
        stats=s,
        rolling_samples=8,
        rolling_loss_rate=0.0,
        rolling_realized_mean=0.08,
    )
    assert q.total_penalty == 0.0
    estimate = maker_realization_cost_estimate(
        stats=s, global_stats=s, taker_fee_bps=0.0, holding_risk_bps=0.5,
    )
    assert estimate.effective_taker_exit_rate > 0.9
    assert estimate.expected_negative_shortfall_bps < 0.2
    assert estimate.total_cost_bps < 0.7


def test_a14_losing_taker_exits_price_expected_negative_shortfall():
    good = MakerLifecycleStats()
    bad = MakerLifecycleStats()
    for gross in (6.0, 8.0, 4.0, 10.0, 5.0, 7.0):
        good.observe(gross_bps=gross, exit_is_taker=True)
    for gross in (-8.0, -12.0, -6.0, -10.0, -7.0, -9.0):
        bad.observe(gross_bps=gross, exit_is_taker=True)
    good_cost = maker_realization_cost_estimate(
        stats=good, global_stats=good, taker_fee_bps=0.0, holding_risk_bps=0.5,
    )
    bad_cost = maker_realization_cost_estimate(
        stats=bad, global_stats=bad, taker_fee_bps=0.0, holding_risk_bps=0.5,
    )
    assert good_cost.taker_exit_rate == bad_cost.taker_exit_rate == 1.0
    assert good_cost.expected_negative_shortfall_bps < 0.2
    assert bad_cost.expected_negative_shortfall_bps > 5.0
    assert bad_cost.total_cost_bps > good_cost.total_cost_bps + 5.0


def test_quality_learning_is_single_bounded_authority_not_blacklist():
    assert 'DIRECT_MAKER_LIFECYCLE' in SRC
    assert 'maker_quality_adjustment(' in SRC
    assert 'maker_lifecycle_ev = life -' in SRC
    assert 'final_score=final -' in SRC
    assert 'blacklist' not in SRC.lower()
    assert 'cooldown' not in SRC.lower()


def test_early_portfolio_headroom_prevents_doomed_entry_builds():
    assert 'portfolio_open_slots' in SRC
    assert 'portfolio_headroom_stop' in SRC
    assert 'candidate_ids = set()' in SRC
    assert 'success_cap = min(' in SRC


def test_a14_maker_geometry_caps_inside_touch_at_six_bps():
    bid, ask = 100.0, 100.20
    bid_px, ask_px, meta = cap_maker_quote_geometry(
        bid=bid, ask=ask, bid_px=100.15, ask_px=100.05, price_decimals=4,
    )
    assert meta['raw_buy_improvement_bps'] > 6.0
    assert meta['raw_sell_improvement_bps'] > 6.0
    assert meta['buy_improvement_bps'] <= 6.01
    assert meta['sell_improvement_bps'] <= 6.01
    assert bid_px < ask_px


def test_a14_maker_ttl_caps_old_500ms_quote_at_75ms():
    assert direct_maker_expiry_ns(500_000_000) == 75_000_000
    assert direct_maker_expiry_ns(50_000_000) == 50_000_000


def test_a14_dust_does_not_consume_productive_total_open_capacity():
    assert effective_total_open_books(actual_nonflat=8, dust_nonflat=8) == 0
    assert effective_total_open_books(actual_nonflat=10, dust_nonflat=8) == 2
    # Exemption is bounded: ninth dust key starts consuming total-open capacity.
    assert effective_total_open_books(actual_nonflat=9, dust_nonflat=9) == 1


def test_a14_cold_start_uses_weak_hierarchical_taker_prior_without_hard_penalty():
    q = maker_quality_adjustment(stats=None)
    assert q.total_penalty == 0.0
    assert abs(q.effective_taker_exit_rate - 0.55) < 1e-12
    s = MakerLifecycleStats()
    for gross in (6.0, 5.0, 7.0, 8.0):
        s.observe(gross_bps=gross, exit_is_taker=False)
    q2 = maker_quality_adjustment(stats=s)
    assert q2.effective_taker_exit_rate < q.effective_taker_exit_rate


def test_a14_quality_state_is_session_persistent():
    assert 'direct_maker_quality_a1_5_1' in SRC
    assert 'direct_maker_quality_a1_5' in SRC
    assert 'direct_maker_quality_a1_4' in SRC
    assert 'direct_maker_quality_a1_3' in SRC  # migration fallback
    assert 'DIRECT_QUALITY_RESTORE' in SRC
    assert 'MakerLifecycleStats.from_state' in SRC
    assert '.as_state()' in SRC


def test_a14_dust_is_not_sent_to_impossible_position_exit():
    text = ast.get_source_segment(SRC, METHODS['_inventory_needs_management'])
    assert '_research_exchange_min_order_size' in text
    assert 'qty + 1e-12 < min_size' in text
    build = ast.get_source_segment(SRC, METHODS['build_mm_strategy_instructions'])
    assert 'direct_dust_skipped_management' in build
    assert 'qty_abs + 1e-12 < min_size_local' in build


def test_a151_preserves_base_final_validator_but_uses_direct_fast_screen():
    text = ast.get_source_segment(SRC, METHODS['_research_final_validate_instructions'])
    assert 'super()._research_final_validate_instructions(response, state)' in text
    assert 'dust_exempt_count(dust)' in text
    fast = ast.get_source_segment(SRC, METHODS['_research_fast_screen'])
    assert 'super()._research_fast_screen(state)' not in fast
    assert 'select_fastpath_rows(' in fast
    assert 'cheap_priority(' in fast

def test_execution_log_is_finite_json_safe():
    d = choose_direct_execution(
        maker_lifecycle_ev=-0.01,
        directional_score=0.0,
        crossing_bps=3.0,
        maker_size=0.25,
        taker_clip=0.25,
        neutral_fallback=True,
    )
    for value in d.as_log().values():
        if isinstance(value, float):
            assert math.isfinite(value)


def test_a14_strategy_overrides_fixed_taker_cost_with_learned_shortfall():
    text = ast.get_source_segment(SRC, METHODS['_research_lifecycle_entry_cost_bps'])
    assert 'maker_realization_cost_estimate(' in text
    assert 'expected_negative_shortfall_bps' in text
    assert 'expected_cross_bps=0.0' in text
    assert 'expected_slippage_bps=0.0' in text
    assert 'super()._research_lifecycle_entry_cost_bps' not in text


def test_total_score_and_lifecycle_remain_upstream_of_execution():
    assert '_global_book_rank(' in SRC
    assert '_research_score_ev_last' in SRC
    assert 'getattr(ev, "eligible"' in SRC
    assert 'getattr(ev, "lifecycle_ev"' in SRC
    assert 'life < 0.0' in SRC
    assert 'total_score_value=' in SRC


def test_position_exit_controller_is_unchanged_by_direct_overlay():
    assert 'choose_position_exit(' not in SRC
    assert 'def _manage_inventory(' not in SRC
    assert '_manage_inventory(' in SRC


def test_fill_learning_preserves_inherited_accounting_first():
    node = METHODS['_research_on_own_fill']
    text = ast.get_source_segment(SRC, node)
    assert 'super()._research_on_own_fill(' in text
    assert text.index('super()._research_on_own_fill(') < text.index('MakerLifecycleStats()')


def test_final_contract_validation_is_last_authority():
    build_pos = SRC.index('def build_mm_strategy_instructions')
    sanitize_pos = SRC.index('self._research_sanitize_maker_instructions', build_pos)
    validate_pos = SRC.index('self._research_final_validate_instructions', build_pos)
    assert validate_pos > sanitize_pos


def test_a151_fastpath_prioritizes_incomplete_books_and_bounds_qualified_share():
    rows = [
        FastPathRow(book_id=i, priority=100-i, observations_remaining=1 if i < 8 else 0,
                    qualified=(i >= 8))
        for i in range(16)
    ]
    selected = select_fastpath_rows(rows, candidate_count=12, score_deficit=10, tick=1)
    assert set(range(8)).issubset(set(selected))
    assert sum(1 for i in selected if i >= 8) <= 3


def test_a151_fastpath_completion_priority_beats_qualified_book():
    incomplete = cheap_priority(observations_remaining=1, qualified=False, spread_bps=5.0, score_deficit=10)
    qualified = cheap_priority(observations_remaining=0, qualified=True, spread_bps=20.0, cached_alpha_rank=1.0, score_deficit=10)
    assert incomplete > qualified


def test_a151_net_realized_shortfall_catches_fee_loss_hidden_by_positive_gross():
    s = MakerLifecycleStats()
    s.observe(gross_bps=3.0, net_bps=-2.0, exit_is_taker=True)
    assert s.taker_gross_bps_ewma > 0.0
    assert s.taker_net_bps_ewma < 0.0
    assert s.taker_net_shortfall_bps_ewma == 2.0
    cost = maker_realization_cost_estimate(stats=s, global_stats=s, taker_fee_bps=0.0, holding_risk_bps=0.0)
    assert cost.expected_negative_shortfall_bps > 0.0


def test_a151_cubic_downside_penalizes_large_tail_more_than_equal_mean_small_losses():
    small = MakerLifecycleStats()
    tail = MakerLifecycleStats()
    for x in (-5.0, -5.0, -5.0, -5.0):
        small.observe(net_bps=x, gross_bps=x, exit_is_taker=True)
    for x in (0.0, 0.0, 0.0, -20.0):
        tail.observe(net_bps=x, gross_bps=x, exit_is_taker=True)
    a = maker_realization_cost_estimate(stats=small, global_stats=small, taker_fee_bps=0.0, holding_risk_bps=0.0)
    b = maker_realization_cost_estimate(stats=tail, global_stats=tail, taker_fee_bps=0.0, holding_risk_bps=0.0)
    assert b.downside_lpm3_bps > a.downside_lpm3_bps


def test_a151_pre_submit_freshness_budget_is_authoritative():
    text = ast.get_source_segment(SRC, METHODS['_simple_place_maker'])
    assert 'DIRECT_MAX_PRE_SUBMIT_AGE_MS' in text
    assert 'DIRECT_FRESHNESS_SKIP' in text
    assert DIRECT_MAX_PRE_SUBMIT_AGE_MS == 100.0


def test_a151_selected_only_profile_build_avoids_full_universe_profile_ranking():
    text = ast.get_source_segment(SRC, METHODS['select_books_for_trading'])
    assert 'build_all_book_profiles' not in text
    assert 'for bid in selected_ids' in text
    assert 'BookSelection(' in text


def test_a151_fastpath_version_and_bounded_candidate_count():
    assert DIRECT_FASTPATH_VERSION == 'direct_fastpath_v4_16_2_a1_5_1'
    assert DIRECT_FASTPATH_CANDIDATE_COUNT == 12

def test_a151_fastpath_uses_80_book_breadth_target_not_three_observations_per_book():
    method = ast.get_source_segment(SRC, METHODS['_research_fast_screen']) or ''
    assert 'research_score_target_books' in method
    assert 'research_total_score_full_breadth_books' in method
    assert 'getattr(self, "research_kappa_completion_target"' not in method


def test_a151_true_maker_lifecycle_fees_reject_book10_style_false_profit():
    fee = maker_lifecycle_fee_cost_bps(
        maker_entry_fee_bps=12.23,
        maker_exit_fee_bps=12.25,
        taker_exit_fee_bps=2.30,
        taker_exit_probability=0.0,
        learned_net_shortfall_bps=0.0,
        holding_risk_bps=0.50,
    )
    # Explicit two-sided Maker fees dominate the ~11.18 bps gross capture.
    assert fee.explicit_fee_cost_bps > 24.0
    assert fee.residual_downside_bps == 0.0
    assert 11.18 - fee.total_cost_bps < 0.0


def test_a151_maker_fee_is_not_compressed_and_subtracted_twice():
    # LifecycleEV already contains the direct fee budget in A1.5.1.
    assert abs(maker_economic_ev(lifecycle_ev=0.04, maker_fee_bps=12.0) - 0.04) < 1e-12


def test_a151_migrated_quality_authority_can_be_softened_without_erasing_history():
    s = MakerLifecycleStats()
    for _ in range(8):
        s.observe(exit_is_taker=True, net_bps=-10.0, gross_bps=-2.0)
    full = maker_quality_adjustment(stats=s, authority_scale=1.0)
    soft = maker_quality_adjustment(stats=s, authority_scale=MIGRATED_QUALITY_INITIAL_WEIGHT)
    assert full.total_penalty > 0.0
    assert 0.0 < soft.total_penalty < full.total_penalty
    assert abs(soft.total_penalty / full.total_penalty - MIGRATED_QUALITY_INITIAL_WEIGHT) < 1e-9


def test_a151_migrated_shortfall_authority_is_tempered_but_live_fee_is_not():
    s = MakerLifecycleStats()
    for _ in range(8):
        s.observe(exit_is_taker=True, net_bps=-12.0, gross_bps=-4.0)
    full = maker_realization_cost_estimate(stats=s, taker_fee_bps=2.0, authority_scale=1.0)
    soft = maker_realization_cost_estimate(
        stats=s, taker_fee_bps=2.0, authority_scale=MIGRATED_QUALITY_INITIAL_WEIGHT
    )
    assert soft.expected_negative_shortfall_bps < full.expected_negative_shortfall_bps
    assert abs(soft.expected_taker_fee_bps - full.expected_taker_fee_bps) < 1e-12


def test_a151_directional_taker_entry_remains_disabled():
    assert DIRECT_TAKER_ENTRY_ENABLED is False
