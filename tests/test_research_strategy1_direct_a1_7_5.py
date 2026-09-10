# SPDX-License-Identifier: MIT
"""A1.7.5 relative tail authority, dust escape and QUIET shadow measurement.

Every threshold asserted here is tied to the A1.7.4.5 runtime evidence in
`STRATEGY1_DIRECT_V4_16_2_A1_7_5.md` (agent-67, 2,900 ticks).
"""
import inspect
from pathlib import Path

from research_direct_dust_kappa import (
    DIRECT_A175_DUST_ESCALATION_TICKS,
    DIRECT_A175_DUST_MAX_FLOOR_BPS,
    DIRECT_A175_DUST_PATIENCE_TICKS,
    DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS,
    DIRECT_DUST_KAPPA_VERSION,
    REASON_AGE_ESCALATED,
    REASON_LOSS_BUDGET,
    REASON_NON_CROSS,
    REASON_UNKNOWN_COST,
    a175_age_escalated_floor_bps,
    decide_kappa_safe_dust_compaction,
)
from research_direct_positive_maker_kappa import (
    DIRECT_A1744_STRONG_MAKER_FLOOR_BPS,
    DIRECT_A175_MAKER_ADVANTAGE_BPS,
    DIRECT_POSITIVE_MAKER_KAPPA_VERSION,
    a175_band_floor_bps,
    a175_relative_arm_allows,
    apply_positive_maker_kappa_veto,
    classify_a1744_outcome,
)
from research_direct_tail_recovery import (
    DIRECT_RECOVERY_MAKER_MAX_FAILED_EXITS,
    DIRECT_TAIL_RECOVERY_VERSION,
    a175_failed_exit_override,
    choose_tail_recovery_override,
)
from research_position_exit import (
    ACTION_MAKER_EXIT,
    ACTION_TAKER_EXIT,
    ACTION_WAIT,
    BAND_ABSOLUTE,
    BAND_DEFENSIVE,
    BAND_HARD_ESCAPE,
    PositionExitDecision,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "agents/strategy/Strategy1_Research_Simple.py").read_text()

_SIG = inspect.signature(PositionExitDecision)


def _decision(band, reason, action=ACTION_TAKER_EXIT):
    kwargs = {}
    for name, param in _SIG.parameters.items():
        if param.default is not inspect.Parameter.empty:
            continue
        kwargs[name] = "" if param.annotation is str else 0.0
    kwargs.update(risk_band=band, action=action, reason=reason)
    return PositionExitDecision(**kwargs)


def _veto(band, reason, maker, taker, *, catastrophic=False, budget=False):
    base = _decision(band, reason)
    final = apply_positive_maker_kappa_veto(
        base_decision=base,
        maker_net_bps=maker,
        taker_net_bps=taker,
        maker_executable=True,
        catastrophic_hard_risk=catastrophic,
        inventory_qty=0.25,
        tail_budget_exhausted=budget,
    )
    label = classify_a1744_outcome(
        base_decision=base,
        final_decision=final,
        maker_net_bps=maker,
        taker_net_bps=taker,
        maker_executable=True,
        catastrophic_hard_risk=catastrophic,
        tail_budget_exhausted=budget,
    )
    return final, label


# ----------------------------------------------------------------------
# Version / integration markers
# ----------------------------------------------------------------------


def test_version_and_integration_markers_present():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_0_1"' in SRC
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1_9_0_1"' in SRC
    assert DIRECT_POSITIVE_MAKER_KAPPA_VERSION == "direct_positive_maker_kappa_v4_16_2_a1_7_5"
    assert DIRECT_TAIL_RECOVERY_VERSION == "direct_tail_recovery_v4_16_2_a1_7_5"
    assert DIRECT_DUST_KAPPA_VERSION == "direct_dust_kappa_v4_16_2_a1_7_5"
    assert "A175_TAIL_BUDGET_EXHAUSTED" in SRC
    assert "A175_QUIET_SHADOW_OUTCOME" in SRC
    assert "tail_budget_exhausted=budget_exhausted" in SRC


# ----------------------------------------------------------------------
# Change 1 -- relative tail authority
# ----------------------------------------------------------------------


def test_observed_median_forced_crossing_is_now_vetoed():
    """A1.7.4.5 median forced crossing: Maker -19.0 vs Taker -37.5 in HARD."""
    final, label = _veto(BAND_HARD_ESCAPE, "HARD_ESCAPE_CLIP", -19.0, -37.5)
    assert final.action == ACTION_MAKER_EXIT
    assert label == "A175_RELATIVE_MAKER_RISK_VETO"
    assert final.corridor_action == "DIRECT_POSITIVE_MAKER_KAPPA_A175_RELATIVE"


def test_frozen_absolute_arm_still_fires_and_is_labelled_separately():
    final, label = _veto(BAND_HARD_ESCAPE, "HARD_ESCAPE_CLIP", 171.4, -26.7)
    assert final.action == ACTION_MAKER_EXIT
    assert label == "A1744_POSITIVE_MAKER_RISK_VETO"
    assert final.corridor_action == "DIRECT_POSITIVE_MAKER_KAPPA_A1744"


def test_maker_below_band_floor_still_crosses():
    """Advantage is large (+41) but Maker -35 is below the HARD floor -30."""
    final, label = _veto(BAND_HARD_ESCAPE, "HARD_ESCAPE_CLIP", -35.0, -76.0)
    assert final.action == ACTION_TAKER_EXIT
    assert label == "A1744_TAKER_ALLOWED_MAKER_NOT_STRONG"


def test_marginal_advantage_below_floor_still_crosses():
    final, _ = _veto(BAND_HARD_ESCAPE, "HARD_ESCAPE_CLIP", -20.0, -30.0)
    assert final.action == ACTION_TAKER_EXIT


def test_catastrophic_always_bypasses_both_arms():
    final, label = _veto(BAND_HARD_ESCAPE, "HARD_ESCAPE_CLIP", -19.0, -37.5, catastrophic=True)
    assert final.action == ACTION_TAKER_EXIT
    assert label == "A1744_TAKER_ALLOWED_CATASTROPHIC"


def test_absolute_band_uses_its_own_floor():
    assert a175_band_floor_bps(BAND_DEFENSIVE) == -25.0
    assert a175_band_floor_bps(BAND_HARD_ESCAPE) == -30.0
    assert a175_band_floor_bps(BAND_ABSOLUTE) == -35.0
    assert a175_band_floor_bps("NORMAL") is None
    final, _ = _veto(BAND_ABSOLUTE, "ABSOLUTE_PROTECTION_REDUCE", -30.0, -47.0)
    assert final.action == ACTION_MAKER_EXIT


def test_non_risk_taker_reasons_are_untouched():
    base = _decision(BAND_HARD_ESCAPE, "NORMAL_TAKER_NONNEGATIVE")
    out = apply_positive_maker_kappa_veto(
        base_decision=base, maker_net_bps=50.0, taker_net_bps=-40.0,
        maker_executable=True, catastrophic_hard_risk=False, inventory_qty=0.25,
    )
    assert out is base


def test_non_negative_taker_is_not_the_defect():
    final, _ = _veto(BAND_HARD_ESCAPE, "HARD_ESCAPE_CLIP", 50.0, 1.0)
    assert final.action == ACTION_TAKER_EXIT


def test_relative_arm_boundary_is_exactly_the_observed_median():
    assert DIRECT_A175_MAKER_ADVANTAGE_BPS == 15.0
    assert a175_relative_arm_allows(
        maker_net_bps=-20.0, taker_net_bps=-35.0, risk_band=BAND_HARD_ESCAPE)
    assert not a175_relative_arm_allows(
        maker_net_bps=-20.0, taker_net_bps=-34.9, risk_band=BAND_HARD_ESCAPE)


# ----------------------------------------------------------------------
# Change 1 -- bounded hold
# ----------------------------------------------------------------------


def test_exhausted_tail_budget_releases_the_hold():
    final, label = _veto(BAND_HARD_ESCAPE, "HARD_ESCAPE_CLIP", -19.0, -37.5, budget=True)
    assert final.action == ACTION_TAKER_EXIT
    assert label == "A175_TAIL_BUDGET_EXHAUSTED"


def test_tail_budget_is_bounded_and_survives_until_flat():
    assert "DIRECT_A175_TAIL_BUDGET_TICKS = 60" in SRC
    # The exhausted flag must not be cleared by the generic release path.
    assert 'if label == "A175_TAIL_BUDGET_EXHAUSTED":' in SRC
    # It is cleared only when the book is actually flat.
    assert "_direct_a175_tail_budget_exhausted.discard" in SRC
    assert "_execution_flat_epsilon()" in SRC


# ----------------------------------------------------------------------
# Change 1 -- quiet-book non-fills must not spend the recovery budget
# ----------------------------------------------------------------------


def test_failed_exit_override_requires_material_advantage():
    assert a175_failed_exit_override(maker_net_bps=-19.0, taker_net_bps=-37.5)
    assert not a175_failed_exit_override(maker_net_bps=-19.0, taker_net_bps=-30.0)


def test_recovery_maker_survives_quiet_book_failed_exits():
    """The exact A1.7.4.5 chain: failed_exit_count >= 2 previously killed this."""
    base = _decision(BAND_HARD_ESCAPE, "HARD_ESCAPE_CLIP")
    out = choose_tail_recovery_override(
        base_decision=base,
        maker_net_bps=-19.0,
        taker_net_bps=-37.5,
        position_risk_bps=-22.0,
        risk_velocity_bps_per_tick_value=-1.0,
        inventory_qty=0.25,
        inventory_age=46.0,
        failed_exit_count=12,
        catastrophic_hard_risk=False,
        reduction_executable=True,
    )
    assert out.action == ACTION_MAKER_EXIT
    assert out.reason == "HARD_RECOVERY_MAKER_EXIT"


def test_recovery_maker_still_bounded_by_its_floor_when_failed():
    base = _decision(BAND_HARD_ESCAPE, "HARD_ESCAPE_CLIP")
    out = choose_tail_recovery_override(
        base_decision=base,
        maker_net_bps=-40.0,          # below the HARD floor of -30
        taker_net_bps=-90.0,
        position_risk_bps=-22.0,
        risk_velocity_bps_per_tick_value=-1.0,
        inventory_qty=0.25,
        inventory_age=46.0,
        failed_exit_count=12,
        catastrophic_hard_risk=False,
        reduction_executable=True,
    )
    assert out is base


def test_defensive_recovery_maker_survives_failed_exits():
    base = _decision(BAND_DEFENSIVE, "DEFENSIVE_HOLD", action=ACTION_WAIT)
    out = choose_tail_recovery_override(
        base_decision=base,
        maker_net_bps=-15.0,
        taker_net_bps=-40.0,
        position_risk_bps=-13.0,
        risk_velocity_bps_per_tick_value=-1.0,
        inventory_qty=0.25,
        inventory_age=10.0,
        failed_exit_count=DIRECT_RECOVERY_MAKER_MAX_FAILED_EXITS + 5,
        catastrophic_hard_risk=False,
        reduction_executable=True,
    )
    assert out.action == ACTION_MAKER_EXIT
    assert out.reason == "RECOVERY_MAKER_EXIT"


def test_catastrophic_still_bypasses_recovery():
    base = _decision(BAND_HARD_ESCAPE, "HARD_ESCAPE_CLIP")
    out = choose_tail_recovery_override(
        base_decision=base, maker_net_bps=-19.0, taker_net_bps=-37.5,
        position_risk_bps=-22.0, risk_velocity_bps_per_tick_value=-1.0,
        inventory_qty=0.25, inventory_age=46.0, failed_exit_count=12,
        catastrophic_hard_risk=True, reduction_executable=True,
    )
    assert out is base


# ----------------------------------------------------------------------
# Change 2 -- age-escalated dust escape
# ----------------------------------------------------------------------


def test_floor_is_flat_through_the_patience_window():
    for age in (0, 10, 300, DIRECT_A175_DUST_PATIENCE_TICKS):
        assert a175_age_escalated_floor_bps(age) == DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS


def test_floor_widens_monotonically_then_saturates():
    prev = a175_age_escalated_floor_bps(DIRECT_A175_DUST_PATIENCE_TICKS)
    for age in range(DIRECT_A175_DUST_PATIENCE_TICKS + 1,
                     DIRECT_A175_DUST_ESCALATION_TICKS + 1, 50):
        cur = a175_age_escalated_floor_bps(age)
        assert cur <= prev + 1e-9
        prev = cur
    assert a175_age_escalated_floor_bps(DIRECT_A175_DUST_ESCALATION_TICKS) == DIRECT_A175_DUST_MAX_FLOOR_BPS
    assert a175_age_escalated_floor_bps(99_999) == DIRECT_A175_DUST_MAX_FLOOR_BPS


def test_book80_clears_at_its_first_block_instead_of_freezing():
    """Book 80 was refused at -60.18 bps with dust_age 949 and then decayed to -320."""
    out = decide_kappa_safe_dust_compaction(
        net_base=0.2414, min_order=0.25, vwap_entry=290.81,
        maker_close_price=289.06, age_ticks=949,
    )
    assert out.allow
    assert out.reason == REASON_AGE_ESCALATED
    assert out.age_escalated
    assert out.maker_realization_bps < DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS
    assert out.maker_realization_bps >= out.effective_floor_bps


def test_fresh_dust_keeps_the_frozen_60bps_budget():
    out = decide_kappa_safe_dust_compaction(
        net_base=0.2414, min_order=0.25, vwap_entry=290.81,
        maker_close_price=289.06, age_ticks=10,
    )
    assert not out.allow
    assert out.reason == REASON_LOSS_BUDGET
    assert not out.age_escalated
    assert out.effective_floor_bps == DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS


def test_unknown_cost_basis_still_fails_closed_at_any_age():
    out = decide_kappa_safe_dust_compaction(
        net_base=0.2414, min_order=0.25, vwap_entry=None,
        maker_close_price=289.06, age_ticks=99_999,
    )
    assert not out.allow
    assert out.reason == REASON_UNKNOWN_COST


def test_non_cross_compaction_is_still_always_allowed():
    out = decide_kappa_safe_dust_compaction(
        net_base=0.60, min_order=0.25, vwap_entry=290.81,
        maker_close_price=289.06, age_ticks=0,
    )
    assert out.allow
    assert out.reason == REASON_NON_CROSS


def test_escalation_never_tightens_the_budget():
    for age in (0, 700, 1500, 5000):
        assert a175_age_escalated_floor_bps(age) <= DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS


def test_best_reachable_realization_is_tracked():
    out = decide_kappa_safe_dust_compaction(
        net_base=0.2414, min_order=0.25, vwap_entry=290.81,
        maker_close_price=289.06, age_ticks=10, best_realization_bps=-12.0,
    )
    # A better value seen earlier must survive a worse current observation.
    assert out.best_realization_bps == -12.0
    assert "_direct_a175_dust_best_realization" in SRC


# ----------------------------------------------------------------------
# Change 3 -- QUIET shadow measurement is execution-inert
# ----------------------------------------------------------------------


def test_quiet_floor_authority_is_unchanged():
    from research_direct_economics import DIRECT_MAKER_MIN_EDGE_BPS
    from research_direct_quiet_entry import (
        DIRECT_A1745_QUIET_ZERO_REBATE_MIN_EDGE_BPS,
        DIRECT_QUIET_ENTRY_VERSION,
        quiet_zero_rebate_entry_gate,
    )
    assert DIRECT_QUIET_ENTRY_VERSION == "direct_quiet_entry_v4_16_2_a1_7_4_5"
    assert DIRECT_A1745_QUIET_ZERO_REBATE_MIN_EDGE_BPS == 15.0
    gate = quiet_zero_rebate_entry_gate(
        regime="QUIET", maker_fee_bps=0.0, spread_bps=31.5, trade_rate=0.0,
        base_min_edge_bps=DIRECT_MAKER_MIN_EDGE_BPS,
    )
    assert gate.active and gate.effective_min_edge_bps == 15.0


def test_shadow_ledger_is_bounded_and_diagnostic_only():
    assert "DIRECT_A175_SHADOW_LEDGER_MAX = 256" in SRC
    assert "DIRECT_A175_SHADOW_HORIZON_TICKS = 200" in SRC
    assert "_direct_a175_shadow_record(" in SRC
    assert "_direct_a175_shadow_resolve(" in SRC
    assert "ledger.popitem(last=False)" in SRC


def test_shadow_resolution_runs_after_the_floor_is_fixed():
    gate_idx = SRC.index("effective_maker_min_edge_bps = float(a1745_gate.effective_min_edge_bps)")
    resolve_idx = SRC.index("self._direct_a175_shadow_resolve(int(book_id), book)")
    assert gate_idx < resolve_idx


def test_shadow_helpers_never_mutate_the_effective_floor():
    start = SRC.index("def _direct_a175_shadow_record(")
    end = SRC.index("def _place_skewed_quotes(")
    body = SRC[start:end]
    assert "effective_maker_min_edge_bps" not in body
    assert "response" not in body
    assert "instructions" not in body
