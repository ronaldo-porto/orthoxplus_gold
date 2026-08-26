# SPDX-License-Identifier: MIT
"""V4.11 state/authority regression tests.

The completion scheduler, quote telemetry, realization and session restore must
all use the same rolling timestamp-based Kappa authority. Lifetime counters are
not allowed to drive completion eligibility.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy" / "Strategy1_Research.py"
SRC = STRATEGY.read_text(encoding="utf-8")


def test_quote_telemetry_uses_authoritative_kappa_state():
    assert (
        '"kappa_observation_count_before": int(\n'
        '                        self._research_kappa_book(book_id).realized_observation_count'
    ) in SRC
    assert '"kappa_lifetime_observation_count_before"' in SRC


def test_fill_and_position_telemetry_use_authoritative_before_after():
    assert "kappa_before_authoritative = self._research_kappa_book" in SRC
    assert "kappa_after_authoritative = self._research_kappa_book" in SRC
    assert "kappa_after=int(kappa_after_authoritative)" in SRC
    assert "kappa_before=int(kappa_before_authoritative)" in SRC
    assert "realized_book_observations=int(kappa_after_authoritative)" in SRC


def test_realized_observation_invalidates_cache_without_injecting_raw_trade_timestamp():
    assert "def _research_note_realized_observation(self, book_id: int, timestamp=None)" in SRC
    block = SRC.split("def _research_note_realized_observation", 1)[1].split(
        "def _research_save_session", 1
    )[0]
    assert "self._research_kappa_roll_cache_key = None" in block
    assert "_research_persisted_observation_timestamps.setdefault" not in block
    assert "rows.append(stamp)" not in block
    assert "realized_pnl_history buckets are the canonical" in block
    assert 'timestamp=getattr(event, "timestamp", None)' in SRC


def test_session_persists_and_restores_rolling_timestamp_evidence():
    assert 'payload["rolling_observation_timestamps"]' in SRC
    assert "self._research_restore_observation_timestamps(disk)" in SRC
    assert "legacy snapshots without" in SRC


def test_rolling_authority_merges_live_and_persisted_without_lifetime_counts():
    block = SRC.split("def _research_refresh_rolling_kappa_cache", 1)[1].split(
        "def _research_rolling_observation_counts", 1
    )[0]
    assert "for source in (persisted, live):" in block
    assert "merged.setdefault(bid, set()).add(ts)" in block
    assert "_research_realized_observations_by_book" not in block
