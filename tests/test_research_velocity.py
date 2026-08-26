# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research velocity metrics and HYBRID_SUMMARY telemetry."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)

from research_velocity import (
    VELOCITY_VERSION,
    VelocityState,
    coverage_velocity,
    inventory_realization_time,
    kappa_qualification_velocity,
    percentile,
    round_trip_conversion,
    round_trip_velocity,
)


def test_round_trip_velocity_and_conversion():
    assert round_trip_velocity(6, 2.0) == 3.0
    assert round_trip_velocity(6, 0.0) == 0.0
    assert round_trip_conversion(4.0, 10.0) == 0.4
    assert round_trip_conversion(4.0, 0.0) == 0.0
    assert round_trip_conversion(12.0, 10.0) == 1.0


def test_coverage_and_kappa_qualification_velocity():
    assert coverage_velocity(5, 10.0) == 0.5
    assert kappa_qualification_velocity(2, 8.0) == 0.25
    assert coverage_velocity(3, 0.0) == 0.0


def test_inventory_realization_time_and_percentiles():
    assert inventory_realization_time(100.0, 250.0) == 150.0
    assert inventory_realization_time(250.0, 100.0) is None
    ages = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(ages, 0.50) == 3.0
    assert percentile(ages, 0.90) == 4.6
    assert percentile([], 0.50) is None


def test_velocity_state_tracks_books_and_exit_buckets():
    state = VelocityState()
    state.note_open(1, 10.0)
    state.note_volume(0.40)
    state.note_exit_intent(1, "SELECTIVE_TAKER_EXIT")
    elapsed = state.note_realized(1, 40.0, closed_qty=0.40, round_trip=True, flatten=True)
    state.note_exit_fill(1, 1.25)
    state.note_qualified_book(1, eligible=True)
    state.note_qualified_book(1, eligible=True)
    assert elapsed == 30.0
    snap = state.snapshot(simulation_time=10.0, inventory_ages=[8.0, 12.0])
    assert snap["completed_round_trips"] == 1
    assert snap["round_trip_velocity"] == 0.1
    assert snap["round_trip_conversion"] == 1.0
    assert snap["coverage_velocity"] == 0.1
    assert snap["kappa_qualification_velocity"] == 0.1
    assert snap["new_qualified_books"] == 1
    assert snap["taker_exit_count"] == 1
    assert snap["taker_exit_pnl"] == 1.25
    assert snap["inventory_age_median"] == 10.0


def test_research_wires_hybrid_summary():
    assert VELOCITY_VERSION == "velocity_metrics_v1"
    assert "RESEARCH_VELOCITY_VERSION" in RESEARCH_SRC
    assert "def _research_emit_hybrid_summary" in RESEARCH_SRC
    assert "[S1R_HYBRID_SUMMARY]" in RESEARCH_SRC
    assert "HYBRID_SUMMARY" in RESEARCH_SRC
    handle = RESEARCH_SRC.split("def handle(")[1].split("def respond(")[0]
    assert "_research_emit_hybrid_summary" in handle
    console = RESEARCH_SRC.split('if typ == "HYBRID_SUMMARY":')[1].split("if typ ==")[0]
    assert "rt_velocity" in console
    assert "rt_conversion" in console
    assert "coverage_velocity" in console
    assert "kappa_qual_velocity" in console
    assert "inv_age_med" in console
    assert "inv_age_p90" in console
    for key in (
        "round_trip_velocity",
        "round_trip_conversion",
        "coverage_velocity",
        "kappa_qualification_velocity",
        "inventory_age_median",
        "inventory_age_p90",
        "maker_exit_count",
        "competitive_maker_count",
        "aggressive_maker_count",
        "taker_exit_count",
    ):
        assert key in RESEARCH_SRC


def test_velocity_state_tracks_per_book_realization_time_prior():
    state = VelocityState()
    state.note_open(1, 100.0)
    state.note_realized(1, 140.0, flatten=True)
    state.note_open(1, 200.0)
    state.note_realized(1, 260.0, flatten=True)
    state.note_open(2, 300.0)
    state.note_realized(2, 400.0, flatten=True)
    book_med, global_med = state.expected_realization_time(1)
    cold_med, cold_global = state.expected_realization_time(99)
    assert book_med == 50.0
    assert global_med == 60.0
    assert cold_med is None
    assert cold_global == 60.0


def test_research_round_trip_velocity_uses_runtime_delta_not_restored_lifetime_total():
    # Persisted _research_round_trip_closes may be restored (e.g. 65), but
    # current velocity must start at zero until this runtime observes a new RT.
    velocity_fn = RESEARCH_SRC.split("def _research_round_trip_velocity(self)")[1].split(
        "def _research_activity_deficit_ratio", 1
    )[0]
    assert "_research_velocity_state().completed_round_trips" in velocity_fn
    assert "_research_round_trip_closes" not in velocity_fn.split("return round_trip_velocity", 1)[1]

    summary_fn = RESEARCH_SRC.split("def _research_hybrid_summary_payload(self)")[1].split(
        "def _research_emit_hybrid_summary", 1
    )[0]
    assert 'completed_round_trips=int(getattr(self, "_research_round_trip_closes"' not in summary_fn


def test_ontrade_increments_velocity_round_trip_once():
    ontrade = RESEARCH_SRC.split("def onTrade(self, event", 1)[1].split(
        "def ", 1
    )[0]
    assert "round_trip=round_trip_event" in ontrade
    assert "vel.round_trip_volume +=" not in ontrade
