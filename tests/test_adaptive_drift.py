# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Phase 3 Step 4: persistent fast-vs-slow Adaptive drift detection."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from adaptive_drift import (
    DriftConfig,
    DriftObservation,
    DriftTracker,
    PhaseClocks,
    current_phase,
    enter_or_extend_drift,
    phase_transition_reason,
)

CFG = DriftConfig(
    fast_alpha=0.15,
    slow_alpha=0.03,
    window_requests=10,
    min_windows=2,
    min_samples=40,
    min_window_samples=8,
    min_signals=1,
    fill_abs=0.02,
    fill_rel=0.25,
    markout_delta_bps=2.0,
    spread_ratio=1.25,
    spread_delta_bps=4.0,
    pnl_hard_floor=-0.02,
    pnl_ratio=0.35,
    pnl_baseline_min=0.03,
    dust_abs=0.05,
    hold_requests=20,
    recovery_requests=15,
)


def _feed(tracker: DriftTracker, n: int, start_req: int, **values) -> list:
    verdicts = []
    obs = DriftObservation(**values)
    req = start_req
    for _ in range(n):
        req += 1
        tracker.observe(obs)
        verdict = tracker.maybe_close_window(req)
        if verdict is not None:
            verdicts.append(verdict)
    return verdicts, req


def test_single_deteriorating_window_does_not_trigger():
    tracker = DriftTracker(CFG)
    _feed(tracker, 40, 0, fill_hazard=0.20)
    verdicts, _req = _feed(tracker, 10, 40, fill_hazard=0.05)
    assert len(verdicts) == 1
    assert verdicts[0].deteriorated is True
    assert verdicts[0].consecutive_deteriorating == 1
    assert verdicts[0].trigger_drift is False
    assert "fill_hazard" in verdicts[0].reason


def test_two_windows_and_min_samples_trigger_drift():
    tracker = DriftTracker(CFG)
    _feed(tracker, 40, 0, fill_hazard=0.20)
    first, req = _feed(tracker, 10, 40, fill_hazard=0.05)
    second, _req = _feed(tracker, 10, req, fill_hazard=0.05)
    assert first[0].trigger_drift is False
    assert second[0].deteriorated is True
    assert second[0].consecutive_deteriorating >= 2
    assert second[0].trigger_drift is True
    assert second[0].total_samples >= CFG.min_samples


def test_single_bad_observation_is_not_enough():
    tracker = DriftTracker(CFG)
    _feed(tracker, 40, 0, fill_hazard=0.20)
    tracker.observe(DriftObservation(fill_hazard=0.0))
    verdict = tracker.maybe_close_window(41)
    assert verdict is None
    verdicts, _req = _feed(tracker, 9, 41, fill_hazard=0.20)
    assert verdicts
    assert verdicts[-1].trigger_drift is False
    assert "fill_hazard" not in (verdicts[-1].reason or "")


def test_actionable_fill_and_markout_channels():
    tracker = DriftTracker(CFG)
    _feed(tracker, 40, 0, actionable_fill=0.18, markout_bps=1.0)
    _v1, req = _feed(tracker, 10, 40, actionable_fill=0.04, markout_bps=-6.0)
    v2, _req = _feed(tracker, 10, req, actionable_fill=0.04, markout_bps=-6.0)
    assert v2[0].trigger_drift is True
    hits = {name for name, sig in v2[0].signals.items() if sig.deteriorated}
    assert "actionable_fill" in hits
    assert "markout" in hits


def test_spread_expansion_and_dust_and_pnl():
    tracker = DriftTracker(CFG)
    _feed(tracker, 40, 0, spread_bps=8.0, dust_rate=0.04, maker_pnl=0.05)
    _v1, req = _feed(
        tracker, 10, 40, spread_bps=20.0, dust_rate=0.18, maker_pnl=-0.04
    )
    v2, _req = _feed(
        tracker, 10, req, spread_bps=20.0, dust_rate=0.18, maker_pnl=-0.04
    )
    assert v2[0].trigger_drift is True
    hits = {name for name, sig in v2[0].signals.items() if sig.deteriorated}
    assert "spread" in hits
    assert "dust_rate" in hits
    assert "maker_pnl" in hits


def test_stable_series_stays_out_of_drift():
    tracker = DriftTracker(CFG)
    verdicts, _req = _feed(tracker, 80, 0, fill_hazard=0.20, markout_bps=0.5)
    assert verdicts
    assert all(not v.trigger_drift for v in verdicts)
    assert tracker.consecutive_deteriorating == 0


def test_recovery_is_drift_then_bootstrap_then_normal():
    clocks = PhaseClocks(
        observe_requests=1000,
        normal_after_requests=3000,
        total_requests=4000,
    )
    assert current_phase(clocks) == "NORMAL"
    clocks = enter_or_extend_drift(clocks, hold_requests=500, recovery_requests=400)
    assert clocks.drift_until_request == 4500
    assert clocks.recovery_until_request == 4900
    clocks.total_requests = 4200
    assert current_phase(clocks) == "DRIFT"
    clocks.total_requests = 4500
    assert current_phase(clocks) == "BOOTSTRAP"
    assert phase_transition_reason("DRIFT", "BOOTSTRAP") == "DRIFT_RECOVER_BOOTSTRAP"
    clocks.total_requests = 4900
    assert current_phase(clocks) == "NORMAL"
    assert phase_transition_reason("DRIFT", "NORMAL") == "DRIFT_SKIPPED_BOOTSTRAP"


def test_extending_drift_moves_recovery_with_it():
    clocks = PhaseClocks(
        observe_requests=1000,
        normal_after_requests=3000,
        total_requests=4000,
    )
    clocks = enter_or_extend_drift(clocks, hold_requests=500, recovery_requests=400)
    clocks.total_requests = 4400
    clocks = enter_or_extend_drift(clocks, hold_requests=500, recovery_requests=400)
    assert clocks.drift_until_request == 4900
    assert clocks.recovery_until_request == 5300
    clocks.total_requests = 4900
    assert current_phase(clocks) == "BOOTSTRAP"
    clocks.total_requests = 5300
    assert current_phase(clocks) == "NORMAL"


def test_observe_still_blocks_early_drift_clock():
    clocks = PhaseClocks(
        observe_requests=1000,
        normal_after_requests=3000,
        drift_until_request=5000,
        recovery_until_request=5500,
        total_requests=10,
    )
    assert current_phase(clocks) == "OBSERVE"
