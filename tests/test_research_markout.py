# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Bounded mid history, nearest-future markout, conservative missing fallback."""
from dataclasses import dataclass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)

from research_markout import (
    CONSERVATIVE_MARKOUT_FALLBACK_BPS,
    MARKOUT_HORIZONS_MS,
    MARKOUT_VERSION,
    MidHistory,
    conservative_expected_markout_bps,
    extract_book_mid,
    ms_to_ns,
)
from research_quote_lifecycle import QuoteLifecycleStore, side_markout_bps
from research_score_ev import adverse_selection_risk, compute_score_ev, conservative_markout_bps


@dataclass
class _Level:
    price: float


@dataclass
class _Book:
    bids: list[_Level]
    asks: list[_Level]


def test_nearest_future_mid_not_past():
    history = MidHistory()
    history.record(3, ms_to_ns(0), 100.0)
    history.record(3, ms_to_ns(1000), 101.0)
    hit = history.nearest_future_mid(3, ms_to_ns(100))
    assert hit is not None
    assert hit[0] == ms_to_ns(1000)
    assert hit[1] == 101.0
    assert history.nearest_future_mid(3, ms_to_ns(2000)) is None


def test_coarse_tick_uses_nearest_future_not_missing():
    store = QuoteLifecycleStore(missing_after_ms=2500)
    store.record_mid(3, 0, 100.0)
    store.schedule_markouts(
        quote_id=7, book=3, side="buy", fill_price=100.0, fill_ts=0,
    )
    store.record_mid(3, ms_to_ns(1000), 101.0)
    due = store.evaluate(now_ts=ms_to_ns(1000))
    horizons = {row.horizon_ms for row in due}
    assert horizons == {100, 250, 500, 1000}
    assert all(row.status == "OK" for row in due)
    assert all(row.future_mid == 101.0 for row in due)
    buy = [row for row in due if row.horizon_ms == 100][0]
    assert buy.markout_bps == 100.0


def test_side_corrected_markout_from_history():
    store = QuoteLifecycleStore()
    store.record_mid(4, 0, 50.0)
    store.schedule_markouts(
        quote_id=9, book=4, side="sell", fill_price=50.0, fill_ts=0,
    )
    store.record_mid(4, ms_to_ns(100), 49.5)
    due = store.evaluate(now_ts=ms_to_ns(100))
    assert len(due) == 1
    assert due[0].horizon_ms == 100
    assert due[0].markout_bps == side_markout_bps("sell", 50.0, 49.5)
    assert due[0].markout_bps == 100.0


def test_missing_future_only_after_timeout_without_sample():
    store = QuoteLifecycleStore(missing_after_ms=2500)
    store.schedule_markouts(
        quote_id=9, book=4, side="sell", fill_price=50.0, fill_ts=0,
    )
    assert store.evaluate(now_ts=ms_to_ns(100)) == []
    missing = store.evaluate(now_ts=ms_to_ns(2500))
    assert missing
    assert all(row.status == "MISSING_FUTURE" for row in missing)
    assert all(row.markout_bps is None for row in missing)


def test_late_sample_beats_missing_future():
    store = QuoteLifecycleStore(missing_after_ms=2500)
    store.schedule_markouts(
        quote_id=1, book=2, side="buy", fill_price=10.0, fill_ts=0,
    )
    store.record_mid(2, ms_to_ns(3000), 10.1)
    due = store.evaluate(now_ts=ms_to_ns(3000))
    assert due
    assert all(row.status == "OK" for row in due)
    assert all(row.future_mid == 10.1 for row in due)


def test_missing_markout_is_not_zero_adverse_selection():
    assert CONSERVATIVE_MARKOUT_FALLBACK_BPS < 0.0
    missing = conservative_markout_bps(mean_bps=None, samples=0)
    observed_zero = conservative_markout_bps(mean_bps=0.0, samples=20)
    assert missing == CONSERVATIVE_MARKOUT_FALLBACK_BPS
    assert observed_zero == 0.0
    assert missing < 0.0
    assert conservative_expected_markout_bps(mean_bps=None, samples=0) < 0.0
    sparse = compute_score_ev(book=1, fill_prob_old=0.40, spread_capture_bps=6.0)
    known_zero = compute_score_ev(
        book=1,
        fill_prob_old=0.40,
        spread_capture_bps=6.0,
        markout_mean_bps=0.0,
        markout_samples=20,
    )
    assert sparse.expected_markout_bps < 0.0
    assert sparse.adverse_selection_risk > known_zero.adverse_selection_risk
    assert known_zero.adverse_selection_risk == adverse_selection_risk(0.0)


def test_extract_book_mid():
    assert extract_book_mid(_Book(bids=[_Level(10.0)], asks=[_Level(10.2)])) == 10.1
    assert extract_book_mid(_Book(bids=[], asks=[_Level(10.2)])) is None


def test_research_wires_markout_history():
    assert "RESEARCH_MARKOUT_VERSION" in RESEARCH_SRC
    assert MARKOUT_VERSION == "markout_history_v1"
    eval_src = RESEARCH_SRC.split("def _research_evaluate_markouts(")[1].split(
        "def _log_submitted_instructions("
    )[0]
    assert "record_book_mids(" in eval_src
    assert "store.evaluate(now_ts=now)" in eval_src
    assert "mids=" not in eval_src.split("store.evaluate(")[1].split(")")[0]
    assert MARKOUT_HORIZONS_MS == (100, 250, 500, 1000)
