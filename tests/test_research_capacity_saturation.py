# SPDX-License-Identifier: MIT
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY_DIR))

from research_capacity_saturation import (
    CAPACITY_SATURATION_VERSION,
    CAP_ACTIVE,
    CAP_EXPOSURE,
    CAP_NONE,
    CAP_TOTAL,
    CapacitySaturationState,
)

SRC = (STRATEGY_DIR / "Strategy1_Research.py").read_text(encoding="utf-8")

LAUNCHER_CAPS = {"active": 6, "total": 8, "abs_base": 2.0}


def _observe(state, n, *, active=False, total=False, exposure=False, **kw):
    for _ in range(n):
        state.observe(
            active_saturated=active,
            total_saturated=total,
            exposure_saturated=exposure,
            max_active_open_books=LAUNCHER_CAPS["active"],
            max_total_open_books=LAUNCHER_CAPS["total"],
            max_total_abs_base=LAUNCHER_CAPS["abs_base"],
            **kw,
        )
    return state


def test_rates_are_per_tick_fractions():
    state = CapacitySaturationState()
    _observe(state, 30, active=True)
    _observe(state, 70)
    snap = state.snapshot()
    assert snap["cap_version"] == CAPACITY_SATURATION_VERSION
    assert snap["cap_ticks"] == 100
    assert snap["cap_active_saturated_rate"] == 0.30
    assert snap["cap_any_saturated_rate"] == 0.30


def test_any_saturated_does_not_double_count_overlapping_caps():
    state = CapacitySaturationState()
    _observe(state, 10, active=True, total=True, exposure=True)
    snap = state.snapshot()
    assert snap["cap_active_saturated_rate"] == 1.0
    assert snap["cap_total_saturated_rate"] == 1.0
    assert snap["cap_any_saturated_rate"] == 1.0


def test_binding_cap_names_the_dominant_constraint():
    state = CapacitySaturationState()
    _observe(state, 80, total=True)
    _observe(state, 10, active=True)
    _observe(state, 10)
    assert state.binding_cap() == CAP_TOTAL


def test_binding_cap_distinguishes_exposure_from_slots():
    state = CapacitySaturationState()
    _observe(state, 90, exposure=True)
    _observe(state, 10)
    assert state.binding_cap() == CAP_EXPOSURE


def test_binding_cap_reports_active_slots_when_they_dominate():
    state = CapacitySaturationState()
    _observe(state, 75, active=True)
    _observe(state, 25)
    assert state.binding_cap() == CAP_ACTIVE


def test_minority_saturation_is_a_starved_pipeline_not_a_cap():
    """Raising a cap that binds on a minority of ticks buys nothing."""
    state = CapacitySaturationState()
    _observe(state, 40, total=True)
    _observe(state, 60)
    assert state.binding_cap() == CAP_NONE
    assert state.snapshot()["cap_any_saturated_rate"] == 0.40


def test_dust_slot_share_measures_the_budget_dust_burns():
    state = CapacitySaturationState()
    # 2 of the 8 total slots held by dust on every tick.
    _observe(state, 50, total_open=8, dust_open=2, active_open=6)
    snap = state.snapshot()
    assert snap["cap_mean_dust_open"] == 2.0
    assert snap["cap_dust_slot_share"] == 0.25
    assert snap["cap_max_total_open_books"] == 8


def test_headroom_exposes_idle_slots():
    state = CapacitySaturationState()
    _observe(state, 10, total_open=3)
    snap = state.snapshot()
    assert snap["cap_mean_total_open"] == 3.0
    assert snap["cap_total_headroom_mean"] == 5.0


def test_starved_signature_is_high_headroom_and_low_saturation():
    state = CapacitySaturationState()
    _observe(state, 100, total_open=2, active_open=2)
    snap = state.snapshot()
    assert snap["cap_any_saturated_rate"] == 0.0
    assert snap["cap_binding"] == CAP_NONE
    assert snap["cap_total_headroom_mean"] == 6.0


def test_snapshot_is_safe_before_any_observation():
    snap = CapacitySaturationState().snapshot()
    assert snap["cap_ticks"] == 0
    assert snap["cap_any_saturated_rate"] is None
    assert snap["cap_binding"] == CAP_NONE
    assert snap["cap_dust_slot_share"] is None
    assert snap["cap_total_headroom_mean"] is None


def test_non_numeric_inputs_do_not_corrupt_the_accumulator():
    state = CapacitySaturationState()
    state.observe(
        active_saturated=False, total_saturated=False, exposure_saturated=False,
        total_open="junk", dust_open=None, abs_base=float("nan"),
        max_total_open_books=8,
    )
    snap = state.snapshot()
    assert snap["cap_ticks"] == 1
    assert snap["cap_mean_total_open"] == 0.0
    assert snap["cap_mean_abs_base"] == 0.0


# --- wiring contract ---


def test_strategy_observes_at_the_point_the_flags_are_computed():
    assert "from research_capacity_saturation import CapacitySaturationState" in SRC
    assert "def _research_capacity_state(self)" in SRC
    idx = SRC.index("self._research_capacity_state().observe(")
    window = SRC[max(0, idx - 600):idx]
    # Must read the live flags, not recompute them.
    for flag in ("active_cap_saturated", "total_cap_saturated", "exposure_cap_saturated"):
        assert flag in window, flag


def test_dust_and_parked_counts_are_recorded_separately():
    idx = SRC.index("self._research_capacity_state().observe(")
    window = SRC[idx:idx + 800]
    assert "dust_open=dust_nonflat" in window
    assert "parked_open=parked_nonflat" in window
    assert "total_open=actual_nonflat" in window


def test_saturation_reaches_hybrid_summary_and_console():
    assert "_research_capacity_state().snapshot()" in SRC
    assert "binding={r.get('cap_binding')}" in SRC
    assert "dust_slots=" in SRC
