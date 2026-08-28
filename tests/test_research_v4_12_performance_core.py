# SPDX-License-Identifier: MIT
"""V4.12 simple performance-core regression tests."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY_DIR))

from research_realization import ACTION_AGGRESSIVE, evaluate_realization

SRC = (STRATEGY_DIR / "Strategy1_Research.py").read_text(encoding="utf-8")
LAUNCHER = (ROOT / "run_strategy1_research_test.sh").read_text(encoding="utf-8")
MULTI = (ROOT / "run_strategy1_research_test_multi.sh").read_text(encoding="utf-8")


def _maker_only(**overrides):
    params = dict(
        book=1,
        inventory_size=0.25,
        inventory_ratio=0.20,
        inventory_age=4.0,
        unrealized_pnl=1.0,
        expected_markout=0.2,
        volatility=0.0002,
        imbalance=0.0,
        observations_remaining=3,
        required_observations=3,
        volume_cap_headroom=1.0,
        recent_realized_pnl=0.01,
        adverse_selection_risk=0.01,
        fee_bps=1.0,
        spread_bps=4.0,
        slippage_bps=1.0,
        band="LONG",
        enable_sn79_action_utility=False,
        allow_score_taker_direct=False,
        allow_economic_taker_direct=False,
        allow_risk_taker_direct=False,
        allow_aggressive_positive_ev_taker=False,
    )
    params.update(overrides)
    return params


def test_repeated_failed_exits_force_aggressive_maker():
    d = evaluate_realization(
        **_maker_only(
            failed_exit_count=8,
            maker_escalate_failed_exit_count=8,
        )
    )
    assert d.selected_action == ACTION_AGGRESSIVE
    assert d.exit_urgency >= 0.30


def test_one_away_escalates_after_three_failed_maker_exits():
    d = evaluate_realization(
        **_maker_only(
            observations_remaining=1,
            failed_exit_count=3,
            one_away_maker_escalate_failed_exit_count=3,
        )
    )
    assert d.selected_action == ACTION_AGGRESSIVE
    assert d.exit_urgency >= 0.30


def test_raw_trade_timestamp_is_not_inserted_into_kappa_authority():
    start = SRC.index("def _research_note_realized_observation")
    end = SRC.index("def _research_save_session", start)
    method = SRC[start:end]
    assert "realized_pnl_history buckets are the canonical" in method
    assert "rows.append(stamp)" not in method
    assert "_research_persisted_observation_timestamps.setdefault" not in method


def test_open_inventory_cap_is_enforced_in_screen_and_order_build():
    assert 'research_max_open_books", 6' in SRC
    assert '"ACTIVE_OPEN_BOOK_CAP"' in SRC
    assert '"TOTAL_OPEN_BOOK_CAP"' in SRC
    assert '"TOTAL_ABS_BASE_CAP"' in SRC
    assert "open_cap_saturated" in SRC
    assert "planned_open_books" in SRC


def test_generic_positive_ev_min_order_override_is_off_but_one_away_stays_on():
    for launcher in (LAUNCHER, MULTI):
        assert "research_positive_ev_min_order_override=0" in launcher
        assert "research_one_away_exact_min_enabled=1" in launcher


def test_v412_runtime_is_concentrated():
    for launcher in (LAUNCHER, MULTI):
        assert "research_candidate_count=10" in launcher
        assert "research_cohort_size=8" in launcher
        assert "research_max_open_books=6" in launcher
        assert "research_maker_escalate_failed_exit_count=8" in launcher
        assert "research_one_away_maker_escalate_failed_exit_count=3" in launcher


def test_policy_version_is_v412():
    assert 'RESEARCH_POLICY_VERSION = "long_run_recycling_v4_14_1"' in SRC
