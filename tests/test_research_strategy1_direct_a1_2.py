from pathlib import Path
import ast
import math
import sys

ROOT = Path(__file__).parents[1]
STRATEGY_DIR = ROOT / 'agents' / 'strategy'
sys.path.insert(0, str(STRATEGY_DIR))

PATH = STRATEGY_DIR / 'Strategy1_Research_Simple.py'
SRC = PATH.read_text(encoding='utf-8')
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
    choose_direct_execution,
    direct_lifecycle_breakdown,
    maker_economic_ev,
    taker_economic_ev,
)
from research_direct_quality import (
    DIRECT_QUALITY_VERSION,
    MakerLifecycleStats,
    maker_quality_adjustment,
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
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_2"' in SRC
    assert 'RESEARCH_POLICY_VERSION = SIMPLE_POLICY_VERSION' in SRC
    assert DIRECT_ECONOMICS_VERSION == 'direct_economics_v4_16_2_a1_2'
    assert DIRECT_EXECUTION_CONTROLLER_VERSION == 'direct_execution_controller_v4_16_2_a1_2'
    assert DIRECT_QUALITY_VERSION == 'direct_maker_quality_v4_16_2_a1_2'
    assert abs(DIRECT_MAKER_MIN_EV - 0.030) < 1e-12


def test_direct_candidate_remains_small_overlay():
    assert len(SRC.splitlines()) < 750
    assert set(METHODS) == {
        'initialize', '_research_on_own_fill', '_direct_quality_for_book',
        '_research_score_ev_for_book', '_inventory_needs_management',
        '_simple_place_maker', '_place_skewed_quotes', 'build_mm_strategy_instructions',
    }


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


def test_taker_remains_independent_of_maker_margin():
    ev, move, cost = taker_economic_ev(
        directional_score=0.95,
        crossing_bps=1.0,
        taker_fee_bps=0.5,
        slippage_bps=0.5,
        expected_markout_bps=-1.0,
    )
    assert move > cost and ev > 0.0
    d = choose_direct_execution(
        maker_lifecycle_ev=0.010,  # below Maker margin
        directional_score=0.95,
        crossing_bps=1.0,
        maker_size=0.25,
        taker_clip=0.25,
        taker_fee_bps=0.5,
        slippage_bps=0.5,
        expected_markout_bps=-1.0,
    )
    assert d.action == ACTION_TAKER
    assert d.reason == 'TAKER_POSITIVE_DIRECTIONAL_EV'


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
