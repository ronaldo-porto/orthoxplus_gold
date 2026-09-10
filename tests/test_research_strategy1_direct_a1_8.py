from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agents" / "strategy"
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))

from research_direct_exit_refresh import (
    DIRECT_EXIT_REFRESH_VERSION,
    DIRECT_A18_LEGACY_PROFITABLE_EXIT_TTL_MS,
    cycle_bounded_profitable_exit_ttl_ms,
)

SRC = (AGENT / "Strategy1_Research_Simple.py").read_text()
BASE_SRC = (AGENT / "Strategy1_Research.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()


def test_a18_version_and_scope_are_explicit():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_8"' in SRC
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1_8"' in SRC
    assert DIRECT_EXIT_REFRESH_VERSION == "direct_exit_refresh_v4_16_2_a1_8"
    assert 'a18_size_change=0' in SRC
    assert 'a18_active_book_change=0' in SRC
    assert 'a18_taker_logic_change=0' in SRC
    assert 'a18_entry_gate_change=0' in SRC


def test_cycle_bound_reuses_existing_exit_ttls_not_a_new_observed_threshold():
    out = cycle_bounded_profitable_exit_ttl_ms(
        legacy_persistent_ttl_ms=3000.0,
        quiet_exit_ttl_ms=950.0,
        one_away_exit_ttl_ms=975.0,
    )
    assert out == 975.0
    assert out < DIRECT_A18_LEGACY_PROFITABLE_EXIT_TTL_MS


def test_cycle_bound_never_lengthens_an_existing_shorter_ttl():
    assert cycle_bounded_profitable_exit_ttl_ms(
        legacy_persistent_ttl_ms=700.0,
        quiet_exit_ttl_ms=950.0,
        one_away_exit_ttl_ms=975.0,
    ) == 700.0


def test_cycle_bound_tracks_existing_exit_cycle_if_config_changes():
    assert cycle_bounded_profitable_exit_ttl_ms(
        legacy_persistent_ttl_ms=3000.0,
        quiet_exit_ttl_ms=800.0,
        one_away_exit_ttl_ms=900.0,
    ) == 900.0


def test_direct_overlay_applies_ttl_after_base_initialize_only():
    assert 'self.research_profitable_exit_ttl_ms = cycle_bounded_profitable_exit_ttl_ms(' in SRC
    assert 'A18_EXIT_REFRESH_CONFIG' in SRC
    assert 'A18_EXIT_REFRESH_CONFIG' in SRC
    # The shared Research implementation remains unchanged in authority; A1.8
    # is enabled only by the Direct subclass attribute assignment.
    assert 'DIRECT_EXIT_REFRESH_VERSION' not in BASE_SRC


def test_a18_does_not_raise_parallelism_or_size():
    assert 'mm_base_size=0.25' in LAUNCHER
    assert 'research_max_open_books=6' in LAUNCHER
    assert 'research_max_active_open_books=6' in LAUNCHER
    assert 'research_max_total_abs_base=2.0' in LAUNCHER
    assert 'mm_base_size=0.30' not in LAUNCHER
    assert 'research_max_active_open_books=7' not in LAUNCHER


def test_a18_does_not_change_quiet_entry_authority():
    assert 'DIRECT_A1745_QUIET_ZERO_REBATE_MIN_EDGE_BPS' in SRC
    assert 'a1745_global_maker_edge_retune=0' in SRC
    assert 'a18_entry_gate_change=0' in SRC


def test_a18_does_not_change_taker_authority():
    assert 'taker_entry_enabled=int(DIRECT_TAKER_ENTRY_ENABLED)' in SRC
    assert 'a18_taker_logic_change=0' in SRC
    assert 'negative_aggressive_maker_block=1' in SRC


def test_a18_summary_exposes_effective_and_legacy_ttl():
    assert 'direct_a18_profitable_exit_ttl_ms' in SRC
    assert 'direct_a18_legacy_profitable_exit_ttl_ms' in SRC


def test_launcher_preflight_targets_a18():
    assert 'strategy1_direct_v4_16_2_a1_8' in LAUNCHER
    assert 'test_research_strategy1_direct_a1_8.py' in LAUNCHER
