from pathlib import Path
import ast
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
    choose_direct_execution,
    direct_lifecycle_breakdown,
    maker_economic_ev,
    taker_economic_ev,
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
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_1"' in SRC
    assert 'RESEARCH_POLICY_VERSION = SIMPLE_POLICY_VERSION' in SRC
    assert DIRECT_ECONOMICS_VERSION == 'direct_economics_v4_16_2_a1_1'
    assert DIRECT_EXECUTION_CONTROLLER_VERSION == 'direct_execution_controller_v4_16_2_a1_1'


def test_direct_candidate_is_small_overlay():
    assert len(SRC.splitlines()) < 650
    assert set(METHODS) == {
        'initialize', '_research_score_ev_for_book', '_inventory_needs_management',
        '_simple_place_maker', '_place_skewed_quotes', 'build_mm_strategy_instructions',
    }


def test_hot_entry_path_has_one_direct_execution_controller():
    assert 'choose_direct_execution(' in SRC
    assert 'choose_execution(' not in SRC
    assert 'Strategy1._place_skewed_quotes' not in SRC
    assert 'super()._place_skewed_quotes' not in SRC
    assert '_research_execute_entry_taker' in SRC
    assert '_simple_place_maker' in SRC


def test_legacy_entry_authorities_not_in_direct_hot_path():
    forbidden = [
        'admit_lane_candidate(', 'admit_scheduler_candidate(',
        '_schedule_maintenance_books(', '_place_directional_round_trip(',
        'research_enable_quote_hysteresis', 'research_enable_adaptive_ttl',
        'stale_maker_rescue', 'positive_maker_veto', 'required_entry_ev',
    ]
    for token in forbidden:
        assert token not in SRC


def test_direct_lifecycle_removes_latency_and_duplicate_adverse_as_hard_vetoes():
    # Baseline V4.16.2 rejects this despite positive TradingEV because 100ms+
    # latency saturates the old 0.04 penalty and markout is charged again as
    # adverse_selection_risk.
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
    assert direct.latency_cost > 0.0  # telemetry preserved
    assert direct.adverse_selection_risk > 0.0  # telemetry preserved
    assert direct.latency_penalty == 0.0
    assert direct.adverse_penalty == 0.0


def test_direct_lifecycle_does_not_rescue_negative_trading_economics():
    base = _ev(spread_capture_bps=1.0, fees_bps=5.0, latency_ms=0.0)
    assert base.trading_ev < 0.0
    direct = direct_lifecycle_breakdown(base)
    assert direct.eligible is False
    assert direct.reject_reason == 'NEGATIVE_EV'


def test_maker_lifecycle_is_not_multiplied_by_fill_probability_twice():
    life = 0.04
    # The Direct maker economic value takes already-fill-weighted LifecycleEV.
    assert abs(maker_economic_ev(lifecycle_ev=life, maker_fee_bps=0.0) - life) < 1e-12


def test_book14_style_wide_spread_cannot_be_kappa_subsidized_into_taker():
    # Replays the important shape of the losing Agent-68 Book 14 entries:
    # LifecycleEV positive, but half-spread crossing ~16.5 bps. Even a full-scale
    # directional signal cannot pay the crossing cost. There is intentionally no
    # Kappa/score input to choose_direct_execution.
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
    assert d.action != ACTION_TAKER
    assert d.action == ACTION_MAKER


def test_taker_requires_positive_directional_crossing_economics():
    ev, move, cost = taker_economic_ev(
        directional_score=0.95,
        crossing_bps=1.0,
        taker_fee_bps=0.5,
        slippage_bps=0.5,
        expected_markout_bps=-1.0,
    )
    assert move > cost
    assert ev > 0.0
    d = choose_direct_execution(
        maker_lifecycle_ev=0.01,
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
    assert d.action == ACTION_MAKER
    assert d.taker_utility <= -1e8


def test_no_positive_execution_economics_skips():
    d = choose_direct_execution(
        maker_lifecycle_ev=-0.01,
        directional_score=0.10,
        crossing_bps=4.0,
        maker_size=0.25,
        taker_clip=0.25,
        taker_fee_bps=3.0,
        slippage_bps=0.75,
        expected_markout_bps=-2.0,
    )
    assert d.action == ACTION_SKIP



def test_execution_log_is_finite_json_safe():
    import math
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


def test_position_exit_controller_is_preserved_by_inheritance():
    assert 'choose_position_exit(' not in SRC
    assert 'def _manage_inventory(' not in SRC
    assert '_manage_inventory(' in SRC


def test_final_contract_validation_is_last_authority():
    build_pos = SRC.index('def build_mm_strategy_instructions')
    sanitize_pos = SRC.index('self._research_sanitize_maker_instructions', build_pos)
    validate_pos = SRC.index('self._research_final_validate_instructions', build_pos)
    assert validate_pos > sanitize_pos
