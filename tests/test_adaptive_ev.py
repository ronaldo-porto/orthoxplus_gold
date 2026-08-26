# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Phase 3 Step 3.3: bounded Adaptive EV corrections. Low fill must not auto-tighten."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from adaptive_ev import (
    ACTION_AGGRESSIVE,
    ACTION_COMPETITIVE,
    ACTION_PASSIVE,
    ACTION_TAKER,
    SIDE_SUPPRESS_SCALE,
    EvSnapshot,
    apply_earlier_realization,
    choose_overlay,
    hold_proposal,
    project_snapshot,
)


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
        "EARLIER_EXIT",
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
    assert decision.exit_urgency_delta == 0.0
    assert decision.proposal.exit_urgency_scale == 1.0


def test_side_suppression_when_one_side_is_toxic():
    snap = _snap(
        buy_fill=0.40,
        sell_fill=0.10,
        confidence=0.80,
        markout_bps=0.0,
        inventory_cost=0.0,
    )
    decision = choose_overlay(
        snap,
        phase="NORMAL",
        max_tighten=0.0,
        max_widen=0.0,
        max_size_cut=0.0,
        max_exit_boost=0.0,
    )
    assert decision.reason == "SIDE_SUPPRESS"
    assert decision.proposal.sell_bias_scale <= SIDE_SUPPRESS_SCALE + 1e-12
    assert decision.proposal.buy_bias_scale + 1e-12 >= decision.proposal.sell_bias_scale
    assert decision.proposal.size_scale <= 1.0 + 1e-12


def test_earlier_exit_when_inventory_and_adverse_rise():
    snap = _snap(
        markout_bps=-6.0,
        learned_markout_bps=-7.0,
        inventory_cost=0.08,
        exit_urgency=0.30,
        inventory_ratio=0.40,
        actionable_p=0.22,
        confidence=0.85,
        dust_prob=0.18,
    )
    hold_u, *_ = project_snapshot(snap, hold_proposal())
    decision = choose_overlay(
        snap,
        phase="NORMAL",
        max_tighten=0.06,
        max_widen=0.18,
        max_size_cut=0.35,
        max_exit_boost=0.20,
    )
    assert decision.proposal.spread_scale + 1e-12 >= 1.0 or decision.reason != "TIGHTEN_EV"
    assert decision.proposal.exit_urgency_scale >= 1.0 - 1e-12
    if decision.reason == "EARLIER_EXIT":
        assert decision.adaptive_ev > hold_u
        assert decision.exit_urgency_delta > 0.0


def test_earlier_realization_never_enables_rejected_taker():
    urgency, action = apply_earlier_realization(
        base_urgency=0.70,
        scale=1.30,
        base_action=ACTION_AGGRESSIVE,
        taker_allowed=False,
        max_boost=0.20,
    )
    assert urgency >= 0.70
    assert urgency <= 0.70 * 1.20 + 1e-12
    assert action != ACTION_TAKER
    assert action == ACTION_AGGRESSIVE


def test_earlier_realization_cannot_go_later_than_base():
    urgency, action = apply_earlier_realization(
        base_urgency=0.40,
        scale=0.50,
        base_action=ACTION_PASSIVE,
        taker_allowed=True,
        max_boost=0.20,
    )
    assert urgency + 1e-12 >= 0.40
    assert action in {ACTION_PASSIVE, ACTION_COMPETITIVE, ACTION_AGGRESSIVE, ACTION_TAKER}


def test_exit_scale_never_below_base():
    snap = _snap(markout_bps=-8.0, inventory_cost=0.10, confidence=0.9)
    decision = choose_overlay(
        snap,
        phase="NORMAL",
        max_tighten=0.06,
        max_widen=0.18,
        max_size_cut=0.35,
        max_exit_boost=0.20,
    )
    assert decision.proposal.exit_urgency_scale >= 1.0 - 1e-12
    assert decision.proposal.size_scale <= 1.0 + 1e-12


def test_specialization_stays_inside_bounds():
    snap = _snap(specialization=0.05, confidence=0.80, markout_bps=-3.0)
    decision = choose_overlay(
        snap, phase="NORMAL", max_tighten=0.06, max_widen=0.18, max_size_cut=0.35
    )
    assert decision.proposal.size_scale <= 1.0 + 1e-12
    assert 1.0 - 0.15 - 1e-12 <= decision.proposal.spread_scale <= 1.0 + 0.50 + 1e-12
