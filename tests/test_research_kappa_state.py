# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""One authoritative Kappa state shared by every Research consumer."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)
SCORE_EV_SRC = (ROOT / "agents" / "strategy" / "research_score_ev.py").read_text(
    encoding="utf-8"
)

from research_kappa_state import (
    CONSUMER_COMPLETION,
    CONSUMER_COVERAGE,
    CONSUMER_REALIZATION,
    CONSUMER_SCORE_REGIME,
    CONSUMER_SUMMARY,
    CONSUMER_TELEMETRY,
    KAPPA_CONSUMERS,
    KAPPA_STATE_VERSION,
    build_kappa_universe,
    consumer_eligible_counts,
    kappa_book_state,
    summary_kappa,
)
from research_score_ev import scheduler_bucket_counts


COUNTS = {1: 0, 2: 1, 3: 1, 4: 2, 5: 3, 6: 4}
REQUIRED = 3


def test_book_state_tracks_the_four_fields():
    row = kappa_book_state(4, 2, 3)
    assert row.realized_observation_count == 2
    assert row.required_observations == 3
    assert row.observations_remaining == 1
    assert row.eligible is False
    qualified = kappa_book_state(5, 3, 3)
    assert qualified.observations_remaining == 0
    assert qualified.eligible is True
    over = kappa_book_state(6, 4, 3)
    assert over.observations_remaining == 0
    assert over.eligible is True


def test_summary_pending_uses_remaining_not_realized():
    universe = build_kappa_universe(COUNTS, REQUIRED)
    summary = summary_kappa(universe)
    assert summary["pending_1"] == 1
    assert summary["pending_2"] == 2
    assert sum(1 for value in COUNTS.values() if value == 1) == 2
    assert summary["pending_1"] != sum(1 for value in COUNTS.values() if value == 1)
    assert summary["eligible"] == 2


def test_all_consumers_return_the_same_eligibility_count():
    universe = build_kappa_universe(COUNTS, REQUIRED)
    counts = consumer_eligible_counts(universe)
    assert set(counts) == set(KAPPA_CONSUMERS)
    assert len(set(counts.values())) == 1
    assert counts[CONSUMER_SCORE_REGIME] == 2
    assert counts[CONSUMER_COVERAGE] == 2
    assert counts[CONSUMER_COMPLETION] == 2
    assert counts[CONSUMER_REALIZATION] == 2
    assert counts[CONSUMER_TELEMETRY] == 2
    assert counts[CONSUMER_SUMMARY] == 2
    buckets = scheduler_bucket_counts(COUNTS, REQUIRED)
    assert buckets["books_eligible"] == universe.eligible_count
    assert buckets["eligible_books"] == universe.eligible_count
    extra = build_kappa_universe(COUNTS, REQUIRED, universe_ids=[7, 8, 9])
    extra_counts = consumer_eligible_counts(extra)
    assert extra.eligible_count == universe.eligible_count
    assert extra.zero_obs_count == universe.zero_obs_count + 3
    assert len(set(extra_counts.values())) == 1
    assert extra_counts[CONSUMER_SUMMARY] == universe.eligible_count


def test_score_ev_override_cannot_change_kappa_eligibility():
    universe = build_kappa_universe(COUNTS, REQUIRED)
    buckets = scheduler_bucket_counts(
        COUNTS, REQUIRED, eligible_ids={2, 3, 4},
    )
    assert buckets["books_eligible"] == universe.eligible_count
    assert buckets["books_eligible"] != 3


def test_research_wires_authoritative_kappa_state():
    assert "RESEARCH_KAPPA_STATE_VERSION = KAPPA_STATE_VERSION" in RESEARCH_SRC
    assert KAPPA_STATE_VERSION == "kappa_state_v2_rolling"
    assert "def _research_kappa_universe(" in RESEARCH_SRC
    assert "def _research_kappa_book(" in RESEARCH_SRC
    remaining = RESEARCH_SRC.split("def _research_observations_remaining(")[1].split(
        "def _is_kappa_completion_candidate("
    )[0]
    assert "_research_kappa_book(" in remaining
    realized = RESEARCH_SRC.split("def _completion_observation_count(")[1].split(
        "def _research_observations_remaining("
    )[0]
    assert "_research_kappa_book(" in realized
    regime = RESEARCH_SRC.split("def _research_regime_snapshot(")[1].split(
        "def _research_parent_regime("
    )[0]
    assert "_research_kappa_universe(" in regime
    sched = RESEARCH_SRC.split("def _research_emit_scheduler(")[1].split(
        "def _is_compactable_dust("
    )[0]
    assert "_research_kappa_universe(" in sched
    assert "eligible_ids" not in sched
    summary = RESEARCH_SRC.split(
        'payload.setdefault("research_forced_maker_quote_books"'
    )[1].split("research_parked_dust_abs_base")[0]
    assert "_research_kappa_universe(" in summary
    assert "summary_kappa(" in summary
    realize = RESEARCH_SRC.split("def _research_evaluate_realization(")[1].split(
        "def _research_place_maker_exit("
    )[0]
    assert "_research_kappa_book(" in realize
    screen = RESEARCH_SRC.split("def _research_fast_screen(")[1].split(
        "def _research_full_predict_fallback("
    )[0]
    assert "_research_kappa_book(" in screen
    rank = RESEARCH_SRC.split("def _global_book_rank(")[1].split(
        "def _research_legacy_book_rank("
    )[0]
    assert "_research_kappa_book(" in rank
    assert "[S1R_KAPPA]" in RESEARCH_SRC
    assert "build_kappa_universe(" in SCORE_EV_SRC
    buckets_src = SCORE_EV_SRC.split("def scheduler_bucket_counts(")[1]
    assert "build_kappa_universe(" in buckets_src
    assert "len(eligible_ids)" not in buckets_src
