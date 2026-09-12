"""A1.9.5 step 2 (F3): parked dust as its own capacity class.

The measured defect these tests pin down, from tick 10,000 of the A1.9.4 run:
max_abs 2.0, total_abs 1.5758, dust 1.3257, recovery reserve 0.25 -> the
acquisition budget had 0.1742 left, which is zero clips, while book slots sat
at active 1/6 and open 1/8.  Dust alone closed the agent.
"""
import ast
import math
from pathlib import Path

from research_direct_dust_capacity import (
    A195_DUST_CAPACITY_VERSION,
    A195_DUST_CLASS_MAX_CLIPS,
    dust_capacity_report,
    dust_class_ceiling_abs,
    dust_class_exempt_abs,
    productive_abs_base,
)
from research_direct_liveness import admission_slots

ROOT = Path(__file__).parents[1]
SIMPLE = (ROOT / "agents" / "strategy" / "Strategy1_Research_Simple.py").read_text()
MODULE = (ROOT / "agents" / "strategy" / "research_direct_dust_capacity.py").read_text()

# The live configuration at the moment of the measurement.
LIVE = dict(total_abs=1.5758, dust_abs=1.3257, min_order=0.25, max_abs=2.0)


def _method_body(source: str, name: str) -> str:
    """Source of one method, from its `def` to the next method at same indent.

    Marker-to-marker slicing is not safe in this file -- the A1.9.3 comment
    occurs three times, and an `index()` pair silently yields an empty string
    that every assertion then passes against.
    """
    needle = f"    def {name}("
    start = source.index(needle)
    nxt = source.find("\n    def ", start + len(needle))
    body = source[start:] if nxt == -1 else source[start:nxt]
    assert len(body) > 200, f"{name} body looks truncated ({len(body)} chars)"
    return body


# ---- the measured defect --------------------------------------------------

def test_live_state_had_zero_admission_slots_before_the_class():
    """Reproduce the block exactly, so the fix is measured against it."""
    slots = admission_slots(
        effective_abs=1.5758, active_books=1, effective_open_books=1,
        dust_count=7, max_abs=2.0, max_active=6, max_open=8, min_order=0.25,
    )
    assert slots == 0


def test_the_class_restores_admission_at_the_live_state():
    productive = productive_abs_base(**LIVE)
    slots = admission_slots(
        effective_abs=productive, active_books=1, effective_open_books=1,
        dust_count=7, max_abs=2.0, max_active=6, max_open=8, min_order=0.25,
    )
    # abs_slots rises 0 -> 5, but admission is a min() and the active-book
    # lane now binds at 6 - 1 active - 1 recovery reserve.  Four real slots,
    # not five: the BASE budget stops being the constraint, which is the point.
    assert slots == 4


def test_the_binding_constraint_moves_from_base_to_book_slots():
    """After the class, abs is no longer what limits entry."""
    productive = productive_abs_base(**LIVE)
    abs_headroom = 2.0 - productive - 0.25  # minus the recovery reserve
    abs_slots = int(math.floor((abs_headroom + 1e-12) / 0.25))
    active_slots = 6 - 1 - 1
    assert abs_slots == 5
    assert active_slots == 4
    assert active_slots < abs_slots


def test_book_slots_were_never_the_constraint():
    """A1.6.1 already fixed the book count; only abs was binding."""
    active_slots = 6 - 1 - 1  # max_active - active - recovery reserve
    open_slots = 8 - 1 - 1
    assert min(active_slots, open_slots) > 0


def test_productive_abs_matches_the_measured_non_dust_base():
    assert abs(productive_abs_base(**LIVE) - 0.2501) < 1e-9


# ---- the class is bounded -------------------------------------------------

def test_exemption_is_capped_by_one_clip_per_book_slot():
    ceiling = dust_class_ceiling_abs(min_order=0.25, max_abs=100.0, max_clips=8.0)
    assert ceiling == 2.0
    assert dust_class_exempt_abs(
        dust_abs=50.0, min_order=0.25, max_abs=100.0, max_clips=8.0,
    ) == 2.0


def test_exemption_never_exceeds_the_productive_cap():
    """Total exposure is capped at 2x max_abs, whatever the clip count."""
    ceiling = dust_class_ceiling_abs(min_order=0.25, max_abs=2.0, max_clips=1000.0)
    assert ceiling == 2.0


def test_overflow_past_the_ceiling_still_counts_against_acquisition():
    """Graceful degradation, not a cliff -- this is the purge pressure."""
    over = productive_abs_base(
        total_abs=3.0, dust_abs=2.6, min_order=0.25, max_abs=2.0, max_clips=8.0,
    )
    # ceiling is 2.0, so 0.6 of dust overflow stays charged.
    assert abs(over - (3.0 - 2.0)) < 1e-12
    rep = dust_capacity_report(
        total_abs=3.0, dust_abs=2.6, min_order=0.25, max_abs=2.0, max_clips=8.0,
    )
    assert abs(rep["dust_class_overflow_abs"] - 0.6) < 1e-12
    assert rep["dust_class_headroom_abs"] == 0.0


def test_growing_dust_monotonically_tightens_the_budget():
    """Dust is part of total_abs, so growing it grows total too.

    Below the ceiling the productive charge is flat -- that is the whole
    fix.  Above it, every further unit of dust is charged again, so the
    budget tightens without ever jumping.
    """
    non_dust = 0.5
    seen = []
    for dust in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
        seen.append(productive_abs_base(
            total_abs=non_dust + dust, dust_abs=dust,
            min_order=0.25, max_abs=2.0, max_clips=8.0,
        ))
    assert all(b >= a - 1e-12 for a, b in zip(seen, seen[1:])), seen
    # Flat while the class absorbs it (ceiling 2.0)...
    assert all(abs(v - non_dust) < 1e-12 for v in seen[:5]), seen[:5]
    # ...then rising once dust overflows.
    assert seen[-1] > seen[0] + 1e-12
    assert abs(seen[-1] - (non_dust + 4.0 - 2.0)) < 1e-12


def test_exemption_never_exceeds_actual_inventory():
    """A stale diag must not manufacture headroom out of nothing."""
    p = productive_abs_base(
        total_abs=0.3, dust_abs=99.0, min_order=0.25, max_abs=2.0,
    )
    assert p == 0.0  # clamped to total, never negative


def test_zero_dust_changes_nothing():
    assert productive_abs_base(
        total_abs=1.5, dust_abs=0.0, min_order=0.25, max_abs=2.0,
    ) == 1.5


def test_garbage_inputs_do_not_raise_or_invent_headroom():
    for bad in (None, float("nan"), float("inf"), "x"):
        p = productive_abs_base(
            total_abs=1.5, dust_abs=bad, min_order=0.25, max_abs=2.0,
        )
        assert 0.0 <= p <= 1.5
        assert math.isfinite(p)


def test_report_is_versioned_and_json_safe():
    import json
    rep = dust_capacity_report(**LIVE)
    assert rep["a195_dust_capacity_version"] == A195_DUST_CAPACITY_VERSION
    json.dumps(rep)
    assert abs(rep["dust_class_exempt_abs"] - 1.3257) < 1e-12
    assert abs(rep["productive_abs_base"] - 0.2501) < 1e-9


# ---- wiring ---------------------------------------------------------------

def test_switch_exists_and_defaults_on():
    assert "research_a195_dust_capacity_class" in SIMPLE
    assert '"research_a195_dust_capacity_class", True' in SIMPLE


def test_dust_abs_is_accumulated_in_the_inventory_scan():
    assert "dust_abs_base += qty" in SIMPLE
    assert '"dust_abs_base_inventory": float(dust_abs_base)' in SIMPLE


def test_gate_a_charges_acquisition_against_productive_base_only():
    body = _method_body(SIMPLE, "build_mm_strategy_instructions")
    assert "effective_abs_now = abs_now - dust_exempt_abs + float(reserved_abs)" in body


def _validator_defs():
    """Both `_research_final_validate_instructions` bodies, in source order.

    This method is defined TWICE in the class, so a name lookup silently
    returns whichever the harness happens to pick.  The A1.6.2 guard tests
    have been inspecting the shadowed one ever since A1.6.3 landed.
    """
    tree = ast.parse(SIMPLE)
    cls = next(
        n for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple"
    )
    return [
        ast.get_source_segment(SIMPLE, n) for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "_research_final_validate_instructions"
    ]


def test_the_validator_is_defined_twice_and_the_last_one_wins():
    """Pin the shadowing, so F3 cannot drift back into the dead copy.

    Python rebinds the class attribute on the second `def`, so the A1.6.2
    wrapper at the first definition never executes.
    """
    defs = _validator_defs()
    assert len(defs) == 2, len(defs)
    assert "A1.6.3 final placement validator" in defs[-1]
    assert "dust_exempt_count(dust)" in defs[0]


def test_gate_b_lands_in_the_live_validator_not_the_shadowed_one():
    """Relaxing admission alone would just move the block downstream."""
    dead, live = _validator_defs()
    assert "_a195_dust_exempt_abs(" in live
    assert "filled_dust_abs" in live
    # The shadowed A1.6.2 wrapper must stay untouched -- editing dead code
    # reads as a fix while changing nothing.
    assert "_a195_dust_exempt_abs(" not in dead
    assert "research_max_total_abs_base" not in dead


def test_gate_b_discounts_dust_from_the_shadow_abs_budget():
    _dead, live = _validator_defs()
    assert "filled_abs = max(0.0, filled_abs - self._a195_dust_exempt_abs(" in live
    # The book count already excluded dust; only BASE was still charging it.
    assert "filled_active = sum(1 for net in shadow_net.values()" in live
    # The reservation must still be added after the discount.
    assert live.index("filled_abs = max(0.0") < live.index("shadow_abs = filled_abs + reserved_abs")


def test_gate_b_dust_predicate_matches_the_admission_one():
    """Same definition of dust on both gates, or they disagree at the margin."""
    _dead, live = _validator_defs()
    assert "eps < abs(float(net)) + 1e-12 < min_size" in live


def test_disabled_switch_is_exactly_the_old_behaviour():
    body = _method_body(SIMPLE, "_a195_dust_exempt_abs")
    assert "if not self._a195_dust_capacity_enabled():" in body
    assert "return 0.0" in body


def test_capacity_accounting_fails_closed():
    body = _method_body(SIMPLE, "_a195_dust_exempt_abs")
    tail = body[body.index("except Exception:"):]
    assert "return 0.0" in tail


def test_telemetry_reports_the_counterfactual_slot_count():
    assert "slots_without_class" in SIMPLE
    assert "slots_with_class" in SIMPLE
    assert "slots_recovered" in SIMPLE
    assert '"A195_DUST_CAPACITY"' in SIMPLE


def test_summary_stats_are_exported():
    for key in (
        "direct_a195_dust_capacity_class",
        "direct_a195_dust_capacity_version",
        "direct_a195_dust_slots_recovered",
        "direct_a195_dust_max_exempt_abs",
        "direct_a195_dust_overflow_ticks",
    ):
        assert key in SIMPLE, key


def test_module_records_the_measurement_that_motivated_it():
    assert "1.3257" in MODULE
    assert "abs_slots" in MODULE


def test_default_clip_count_is_the_total_book_cap():
    assert A195_DUST_CLASS_MAX_CLIPS == 8.0
