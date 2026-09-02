from pathlib import Path
import ast

ROOT = Path(__file__).parents[1]
PATH = ROOT / 'agents/strategy/Strategy1_Research_Simple.py'
SRC = PATH.read_text(encoding='utf-8')
TREE = ast.parse(SRC)
CLASS = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == 'Strategy1_Research_Simple')
METHODS = {n.name: n for n in CLASS.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_direct_candidate_version():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1"' in SRC
    assert 'RESEARCH_POLICY_VERSION = SIMPLE_POLICY_VERSION' in SRC


def test_direct_candidate_is_small_overlay():
    assert len(SRC.splitlines()) < 650
    assert set(METHODS) == {
        'initialize', '_inventory_needs_management', '_simple_place_maker',
        '_place_skewed_quotes', 'build_mm_strategy_instructions',
    }


def test_hot_entry_path_has_one_execution_controller():
    assert 'choose_execution(' in SRC
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


def test_total_score_and_lifecycle_remain_upstream_of_execution():
    assert '_global_book_rank(' in SRC
    assert '_research_score_ev_last' in SRC
    assert 'getattr(ev, "eligible"' in SRC
    assert 'getattr(ev, "lifecycle_ev"' in SRC
    assert 'life < 0.0' in SRC


def test_position_exit_controller_is_preserved_by_inheritance():
    assert 'choose_position_exit(' not in SRC
    assert 'def _manage_inventory(' not in SRC
    assert '_manage_inventory(' in SRC


def test_final_contract_validation_is_last_authority():
    build_pos = SRC.index('def build_mm_strategy_instructions')
    sanitize_pos = SRC.index('self._research_sanitize_maker_instructions', build_pos)
    validate_pos = SRC.index('self._research_final_validate_instructions', build_pos)
    assert validate_pos > sanitize_pos
