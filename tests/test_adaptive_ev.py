# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Phase 3 Step 3: bounded Adaptive EV overlay. Low fill must not auto-tighten."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from adaptive_ev import EvSnapshot, choose_overlay, hold_proposal, project_snapshot


def _snap(**overrides) -> EvSnapshot:
    base = EvSnapshot(
        actionable_p=0.20,
        spread_capture_bps=4.0,
        markout_bps=0.0,
        fees_bps=0.5,
        completion_value=0.0,
        inventory_cost=0.01,
        dust_prob=0.05,
        latency_cost=0.0,
        learned_fill=None,
        learned_markout_bps=None,
        buy_fill=None,
        sell_fill=None,
        confidence=0.80,
        specialization=0.50,
    )
    data = base.__dict__.copy()
    data.update(overrides)
    return EvSnapshot(**data)


def test_low_fill_does_not_blindly_tighten():
    snap = _snap(
        actionable_p=0.05,
        learned_fill=0.04,
        markout_bps=-6.0,
        learned_markout_bps=-8.0,
        spread_capture_bps=2.0,
        confidence=0.90,
    )
    decision = choose_overlay(
        snap, phase="NORMAL", max_tighten=0.06, max_widen=0.18, max_size_cut=0.35
    )
    assert decision.proposal.spread_scale >= 1.0 - 1e-12
    assert decision.reason != "TIGHTEN_EV"


def test_tighten_only_when_ev_improves():
    snap = _snap(
        actionable_p=0.12,
        spread_capture_bps=8.0,
        markout_bps=2.0,
        learned_fill=0.35,
        learned_markout_bps=1.5,
        confidence=0.90,
        dust_prob=0.02,
        inventory_cost=0.0,
    )
    hold_u, *_ = project_snapshot(snap, hold_proposal())
    decision = choose_overlay(
        snap, phase="NORMAL", max_tighten=0.06, max_widen=0.18, max_size_cut=0.10
    )
    assert decision.proposal.spread_scale < 1.0
    assert decision.adaptive_ev > hold_u
    assert decision.reason == "TIGHTEN_EV"
    assert decision.accepted is True


def test_widen_when_adverse_selection_increases():
    snap = _snap(
        actionable_p=0.40,
        spread_capture_bps=3.0,
        markout_bps=-8.0,
        learned_markout_bps=-10.0,
        learned_fill=0.45,
        confidence=0.85,
        dust_prob=0.20,
    )
    decision = choose_overlay(
        snap, phase="NORMAL", max_tighten=0.06, max_widen=0.18, max_size_cut=0.35
    )
    assert decision.proposal.spread_scale >= 1.0 - 1e-12
    assert decision.reason in {
        "WIDEN_ADVERSE",
        "CUT_SIZE",
        "HOLD",
        "DRIFT_DEFENSIVE",
        "SPECIALIZATION",
    }


def test_drift_disables_aggressive_tightening():
    snap = _snap(
        actionable_p=0.12,
        spread_capture_bps=8.0,
        markout_bps=2.0,
        learned_fill=0.40,
        confidence=0.95,
    )
    decision = choose_overlay(
        snap, phase="DRIFT", max_tighten=0.06, max_widen=0.18, max_size_cut=0.35
    )
    assert decision.proposal.spread_scale >= 1.0 - 1e-12
    assert "TIGHTEN" not in decision.reason


def test_size_scale_never_exceeds_base():
    snap = _snap(markout_bps=-5.0, inventory_cost=0.08, dust_prob=0.30, confidence=0.8)
    decision = choose_overlay(
        snap, phase="NORMAL", max_tighten=0.06, max_widen=0.18, max_size_cut=0.35
    )
    assert decision.proposal.size_scale <= 1.0 + 1e-12


def test_observe_holds():
    snap = _snap(confidence=1.0, learned_fill=0.9, markout_bps=4.0)
    decision = choose_overlay(
        snap, phase="OBSERVE", max_tighten=0.06, max_widen=0.18, max_size_cut=0.35
    )
    assert decision.reason == "HOLD"
    assert decision.accepted is False
    assert decision.spread_delta == 0.0
