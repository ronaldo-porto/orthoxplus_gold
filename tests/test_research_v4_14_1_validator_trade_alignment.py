from pathlib import Path

from research_rolling_economics import sanitize_realized_pnl_events
from research_session_state import (
    VALIDATOR_HISTORY_ALIGNMENT_VERSION,
    rebase_observation_timestamps,
    rebase_realized_pnl_events,
    rebase_sparse_pnl_history,
)

ROOT = Path(__file__).parents[1]
SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text()
TRADE = (ROOT / "taos" / "im" / "validator" / "trade.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_test_multi.sh").read_text()


def test_uploaded_validator_trade_preserves_empty_pnl_timestamps_at_crossover():
    assert "self.realized_pnl_history[uid][ts] = dict(books)" in TRADE
    assert "Preserve EVERY timestamp" in TRADE


def test_validator_rebase_keeps_negative_pre_zero_kappa_observations():
    shifted = rebase_observation_timestamps(
        {1: [600, 800, 1000], 2: [400]},
        old_ts=1000,
        new_ts=0,
        lookback_ns=500,
    )
    assert shifted == {1: [-400, -200, 0]}


def test_validator_rebase_keeps_realized_pnl_and_empty_sparse_buckets():
    events = rebase_realized_pnl_events(
        {1: [(600, 0.2), (800, -0.1), (1000, 0.3)]},
        old_ts=1000,
        new_ts=0,
        lookback_ns=500,
    )
    assert events == {1: [(-400, 0.2), (-200, -0.1), (0, 0.3)]}
    assert sanitize_realized_pnl_events({"1": events[1]}) == events

    history = rebase_sparse_pnl_history(
        {600: {}, 800: {1: 0.2}, 1000: {}},
        old_ts=1000,
        new_ts=0,
        lookback_ns=500,
    )
    assert history == {-400: {}, -200: {1: 0.2}, 0: {}}


def test_v4141_carries_scoring_evidence_but_keeps_long_run_risk_profile():
    assert VALIDATOR_HISTORY_ALIGNMENT_VERSION == "validator_history_alignment_v4_14_1"
    assert 'RESEARCH_POLICY_VERSION = "simplified_hybrid_authority_v4_16_0"' in SRC
    assert '== REASON_SIM_ID_CHANGE' in SRC
    assert '"VALIDATOR_HISTORY_CARRY"' in SRC
    assert "_research_preserve_validator_pnl_history_on_start" in SRC

    # V4.14.0 long-run recycling economics remain frozen.
    assert "research_max_total_open_books=8" in LAUNCHER
    assert "research_max_parked_open_books=4" in LAUNCHER
    assert "research_max_total_abs_base=2.0" in LAUNCHER
    assert "research_positive_maker_veto_max_failed_exits=4" in LAUNCHER
    assert "research_bounded_loss_escape_floor_bps=-25.0" in LAUNCHER
