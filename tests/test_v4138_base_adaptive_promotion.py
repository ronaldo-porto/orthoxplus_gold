from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / 'agents/strategy/BaseStrategy.py').read_text(encoding='utf-8')
ADAPTIVE = (ROOT / 'agents/strategy/AdaptiveAgent.py').read_text(encoding='utf-8')
RESEARCH = (ROOT / 'agents/strategy/Strategy1_Research.py').read_text(encoding='utf-8')
BASE_LAUNCH = (ROOT / 'run_base_strategy_multi.sh').read_text(encoding='utf-8')
ADAPT_LAUNCH = (ROOT / 'run_adaptive_agent_multi.sh').read_text(encoding='utf-8')


def _sig(src: str, name: str) -> tuple[str, ...]:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return tuple(a.arg for a in node.args.args)
    raise AssertionError(name)


def test_base_is_frozen_v4138_promotion():
    assert 'class BaseStrategy(Strategy1_Research)' in BASE
    assert 'DEPLOY_POLICY_VERSION = "base_v4_13_8_champion"' in BASE
    assert 'BASE_CHAMPION_PARENT = "simplified_kappa_productivity_v4_13_8"' in BASE
    assert 'PROFITABLE_EXIT_PERSISTENCE_VERSION = "profitable_maker_exit_persistence_v4_13_8"' in BASE


def test_base_launcher_carries_frozen_research_engine_contracts():
    for token in (
        'research_density_priority_enabled=1',
        'research_qualified_core_exact_min_enabled=1',
        'research_qualified_core_stale_ttl_enabled=1',
        'research_profitable_exit_persistence_enabled=1',
        'research_positive_maker_veto_enabled=1',
        'research_enable_lane_scheduler=1',
        'research_max_active_open_books=6',
    ):
        assert token in BASE_LAUNCH


def test_adaptive_rebased_on_v4138_base_only():
    assert 'class AdaptiveAgent(BaseStrategy)' in ADAPTIVE
    assert 'from BaseStrategy import' in ADAPTIVE
    assert 'from Strategy1_Research import' not in ADAPTIVE
    assert 'ADAPTIVE_VERSION = "adaptive_v4_13_8_realtime"' in ADAPTIVE
    assert '_research_hazard_last' in ADAPTIVE
    assert '_research_score_ev_last' in ADAPTIVE
    assert '_research_market_regime' in ADAPTIVE
    assert '_research_score_regime' in ADAPTIVE


def test_adaptive_keeps_base_kappa_authoritative():
    method = ADAPTIVE.split('def _completion_observation_count', 1)[1].split('\n    #', 1)[0]
    assert 'return int(super()._completion_observation_count(book_id))' in method
    assert 'max(local, episode)' not in method


def test_adaptive_startup_does_not_disable_density_engine():
    block = ADAPTIVE.split('def _adaptive_apply_phase_controls', 1)[1].split('def handle', 1)[0]
    assert 'research_kappa_completion_enabled = False' not in block
    assert 'score_ev_one_away_weight = 0.0' not in block
    assert 'self.max_mm_books_per_tick = self._adaptive_base_max_mm_books' in block


def test_adaptive_contract_hooks_match_research_signatures():
    for name in ('estimate_fill_probability', 'dynamic_order_size', '_place_skewed_quotes', '_global_book_rank', '_select_dust_compaction_books', 'onTrade'):
        assert _sig(ADAPTIVE, name) == _sig(RESEARCH, name)


def test_realtime_launcher_has_full_base_and_adaptive_contracts():
    for token in (
        'base_v4_13_8_champion',
        'adaptive_v4_13_8_realtime',
        'research_density_priority_enabled=1',
        'research_profitable_exit_persistence_enabled=1',
        'adaptive_observe_requests=100',
        'adaptive_normal_after_requests=400',
        'adaptive_hjb_overlay_enabled=1',
    ):
        assert token in ADAPT_LAUNCH
