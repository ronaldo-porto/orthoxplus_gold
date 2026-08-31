# SPDX-License-Identifier: MIT
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY_DIR))

from research_rt_phase_timing import (
    RT_PHASE_VERSION,
    RoundTripPhaseState,
    elapsed_s,
)

SRC = (STRATEGY_DIR / "Strategy1_Research.py").read_text(encoding="utf-8")

S = 1_000_000_000  # one simulation second in nanoseconds


def _full_round_trip(state, book_id, *, submit, fill, exit_submit, flat):
    state.note_entry_submit(book_id, submit)
    state.note_entry_fill(book_id, fill)
    state.note_exit_submit(book_id, exit_submit)
    return state.note_round_trip(book_id, flat)


def test_elapsed_rejects_backwards_and_nonfinite():
    assert elapsed_s(0, 5 * S) == 5.0
    assert elapsed_s(5 * S, 0) is None
    assert elapsed_s(None, 5 * S) is None
    assert elapsed_s("x", 5 * S) is None
    assert elapsed_s(float("nan"), 5 * S) is None


def test_three_phases_split_a_round_trip():
    state = RoundTripPhaseState()
    sample = _full_round_trip(
        state, 7, submit=0, fill=10 * S, exit_submit=100 * S, flat=130 * S,
    )
    assert sample["entry_wait_s"] == 10.0
    assert sample["hold_s"] == 90.0
    assert sample["exit_wait_s"] == 30.0
    assert sample["total_s"] == 130.0
    # The three phases must account for the whole lifecycle.
    assert (
        sample["entry_wait_s"] + sample["hold_s"] + sample["exit_wait_s"]
        == sample["total_s"]
    )


def test_first_submission_anchors_each_phase_not_the_latest():
    """Requoting is charged to the phase it delays, not reset by it."""
    state = RoundTripPhaseState()
    state.note_entry_submit(3, 0)
    state.note_entry_submit(3, 5 * S)  # requote while still flat
    state.note_entry_fill(3, 20 * S)
    state.note_exit_submit(3, 50 * S)
    state.note_exit_submit(3, 70 * S)  # exit requote
    sample = state.note_round_trip(3, 90 * S)
    assert sample["entry_wait_s"] == 20.0
    assert sample["hold_s"] == 30.0
    assert sample["exit_wait_s"] == 40.0


def test_entry_submit_ignored_once_position_is_open():
    state = RoundTripPhaseState()
    state.note_entry_submit(1, 0)
    state.note_entry_fill(1, 10 * S)
    state.note_entry_submit(1, 20 * S)  # an add, not an entry
    sample = state.note_round_trip(1, 30 * S)
    assert sample["entry_wait_s"] == 10.0


def test_lifecycle_resets_between_round_trips():
    state = RoundTripPhaseState()
    _full_round_trip(state, 4, submit=0, fill=1 * S, exit_submit=2 * S, flat=3 * S)
    second = _full_round_trip(
        state, 4, submit=100 * S, fill=140 * S, exit_submit=150 * S, flat=200 * S,
    )
    assert second["entry_wait_s"] == 40.0
    assert second["hold_s"] == 10.0
    assert second["exit_wait_s"] == 50.0
    assert state.completed == 2


def test_cross_reopens_with_the_crossing_fill_as_entry():
    state = RoundTripPhaseState()
    state.note_entry_submit(9, 0)
    state.note_entry_fill(9, 10 * S)
    state.note_exit_submit(9, 20 * S)
    state.note_round_trip(9, 40 * S, reopen=True)
    # The residual is already open; there is no entry submission to anchor.
    state.note_exit_submit(9, 60 * S)
    second = state.note_round_trip(9, 70 * S)
    assert second["entry_wait_s"] is None
    assert second["hold_s"] == 20.0
    assert second["exit_wait_s"] == 10.0
    assert second["total_s"] == 30.0


def test_unanchored_round_trips_are_counted_not_silently_dropped():
    state = RoundTripPhaseState()
    state.note_entry_fill(2, 0)  # no entry submission seen
    sample = state.note_round_trip(2, 10 * S)  # no exit submission seen
    assert sample["entry_wait_s"] is None
    assert sample["exit_wait_s"] is None
    assert sample["total_s"] == 10.0
    assert state.missing_entry_submit == 1
    assert state.missing_exit_submit == 1


def test_snapshot_reports_rate_shares_and_implied_concurrency():
    state = RoundTripPhaseState()
    for book_id in range(10):
        _full_round_trip(
            state, book_id,
            submit=0, fill=10 * S, exit_submit=100 * S, flat=130 * S,
        )
    snap = state.snapshot(simulation_time=3600.0)

    assert snap["rt_phase_version"] == RT_PHASE_VERSION
    assert snap["rt_phase_samples"] == 10
    assert snap["rt_per_sim_hour"] == 10.0
    assert snap["rt_hold_s_median"] == 90.0
    # hold is 90 of the 130s lifecycle and must read as the dominant phase.
    assert snap["rt_hold_share"] > snap["rt_exit_wait_share"] > snap["rt_entry_wait_share"]
    assert abs(sum([
        snap["rt_entry_wait_share"],
        snap["rt_hold_share"],
        snap["rt_exit_wait_share"],
    ]) - 1.0) < 1e-9
    # Little's Law: 10 RT/h x 130s = 0.361 concurrent books.
    assert abs(snap["rt_implied_concurrency"] - (10.0 * 130.0 / 3600.0)) < 1e-9


def test_snapshot_is_safe_before_any_round_trip():
    snap = RoundTripPhaseState().snapshot(simulation_time=0.0)
    assert snap["rt_phase_samples"] == 0
    assert snap["rt_per_sim_hour"] == 0.0
    assert snap["rt_hold_s_median"] is None
    assert snap["rt_implied_concurrency"] is None
    assert snap["rt_hold_share"] is None


def test_books_in_flight_tracks_open_positions_only():
    state = RoundTripPhaseState()
    state.note_entry_submit(1, 0)
    assert state.books_in_flight() == 0  # quoted but not filled
    state.note_entry_fill(1, 1 * S)
    state.note_entry_fill(2, 1 * S)
    assert state.books_in_flight() == 2
    state.note_round_trip(1, 2 * S)
    assert state.books_in_flight() == 1


def test_sample_lists_stay_bounded():
    state = RoundTripPhaseState()
    for i in range(2100):
        _full_round_trip(
            state, i, submit=0, fill=1 * S, exit_submit=2 * S, flat=3 * S,
        )
    assert state.completed == 2100
    assert len(state.hold) == 2048


# --- wiring contract: the hooks must stay attached to the lifecycle ---


def test_strategy_wires_all_four_lifecycle_hooks():
    assert "from research_rt_phase_timing import RoundTripPhaseState" in SRC
    assert "def _research_rt_phase_state(self)" in SRC
    for hook in (
        "note_entry_submit(book_id, now)",
        "phases.note_entry_fill(int(book_id), ts)",
        "note_exit_submit(",
        "phases.note_round_trip(",
    ):
        assert hook in SRC, hook


def test_entry_submit_hook_is_gated_on_being_flat():
    """An add-on quote must not be mistaken for an entry."""
    idx = SRC.index("note_entry_submit(book_id, now)")
    window = SRC[idx - 400:idx]
    assert "_execution_flat_epsilon()" in window


def test_cross_passes_reopen_so_the_residual_is_tracked():
    idx = SRC.index("phases.note_round_trip(")
    window = SRC[idx:idx + 200]
    assert 'reopen=transition == "CROSS"' in window


def test_round_trip_durations_reach_position_and_hybrid_summary():
    assert "rt_entry_wait_s=(rt_phase_sample or {}).get(\"entry_wait_s\")" in SRC
    assert "rt_hold_s=(rt_phase_sample or {}).get(\"hold_s\")" in SRC
    assert "_research_rt_phase_state().snapshot(" in SRC
    assert "rt_per_h=" in SRC  # console formatter
