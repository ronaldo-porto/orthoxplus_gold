"""A1.9.6: legacy dust as a baseline, inherited parked capacity, quantity grid.

A1.9.5 step 3 made inventory true and broke throughput.  Measured on log
20260912_203501 (2,060 ticks): completed round trips 442 -> 10, RANK rows
11,771 -> 39, admission slots zero in 82 of 87 samples.
"""
import ast
import json
import math
import re
from pathlib import Path

from research_direct_dust_capacity import dust_class_exempt_abs
from research_direct_inventory_truth import SEED_LEGACY_DUST, SEED_REAL, build_seed_plan
from research_direct_legacy_baseline import (
    A196_INHERITED_PARKED_MAX_FRACTION,
    A196_LEGACY_BASELINE_VERSION,
    NO_INHERITED_EXEMPTION,
    admission_decomposition,
    inherited_parked_exemption,
    snap_instruction_quantities,
    snap_quantity,
    split_seed_plan,
    venue_executed_quantity,
    wire_quantity,
)
from research_direct_liveness import admission_slots

ROOT = Path(__file__).parents[1]
SIMPLE = (ROOT / "agents" / "strategy" / "Strategy1_Research_Simple.py").read_text()
MODULE = (ROOT / "agents" / "strategy" / "research_direct_legacy_baseline.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()

# The measured tick-1 residue of 20260912_121832, top 12 books by magnitude.
LIVE_RESIDUE = {
    101: -0.6581, 99: -0.6524, 39: -0.5058, 92: 0.4945, 12: -0.4276,
    119: 0.4071, 33: -0.4055, 7: -0.2558, 86: -0.2530, 20: -0.2500,
    84: 0.2499, 117: 0.2498,
}
MID = 274.13


def _plan(venue, **kw):
    return build_seed_plan(
        venue_net_by_book=venue, local_net_by_book={},
        mid_by_book={b: MID for b in venue}, min_order=0.25, tick=1, **kw,
    )


def _class_defs(name):
    tree = ast.parse(SIMPLE)
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    return [ast.get_source_segment(SIMPLE, n) for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]


def _method_source(name):
    defs = _class_defs(name)
    assert defs, name
    return defs[-1]


# ---- F11: the grid ----------------------------------------------------------

def test_the_live_values_execute_one_unit_short_without_the_grid():
    """The residue mechanism, under both conversions the venue could perform."""
    for exact in (True, False):
        assert venue_executed_quantity(0.3499999999999943, 4, exact_binary=exact) == 0.3499
        assert venue_executed_quantity(0.25009999999999827, 4, exact_binary=exact) == 0.25


def test_every_grid_value_executes_the_unit_it_names_under_either_conversion():
    for units in range(1, 30001):                 # every 4-dp quantity to 3.0 BASE
        target = units / 1e4
        for q in (target, math.nextafter(target, 0.0), math.nextafter(target, 9.0),
                  target + 3e-14, target - 3e-14):
            wired = wire_quantity(q, 4)
            assert venue_executed_quantity(wired, 4, exact_binary=True) == target, (q, wired)
            assert venue_executed_quantity(wired, 4, exact_binary=False) == target, (q, wired)


def test_a_clean_value_below_its_decimal_is_lifted_by_one_ulp_and_no_more():
    """0.2501 is stored as 0.25009999999999998...; an exact-binary Decimal128
    would truncate it to 0.2500 even with no arithmetic noise at all."""
    assert venue_executed_quantity(0.2501, 4, exact_binary=True) == 0.25
    wired = wire_quantity(0.2501, 4)
    assert wired == math.nextafter(0.2501, math.inf)
    assert abs(wired - 0.2501) < 1e-15
    assert wire_quantity(0.25, 4) == 0.25         # exact in binary: untouched


def test_a_genuinely_off_grid_quantity_is_never_moved():
    assert snap_quantity(0.25005, 4) == 0.25005
    assert wire_quantity(0.25005, 4) == 0.25005
    assert wire_quantity(0.123456, 4) == 0.123456


def test_the_grid_is_safe_on_hostile_input():
    assert snap_quantity(float("nan"), 4) == 0.0
    assert snap_quantity(None, 4) == 0.0
    assert snap_quantity(0.3499999999999943, "x") == 0.3499999999999943
    assert snap_quantity(0.3499999999999943, -1) == 0.3499999999999943
    assert math.copysign(1.0, snap_quantity(-1e-15, 4)) == 1.0
    assert wire_quantity(0.0, 4) == 0.0
    assert wire_quantity(-0.2501, 4) == -0.2501


class _Instruction:
    def __init__(self, kind, quantity):
        self.type = kind
        self.quantity = quantity


def test_only_noisy_placements_move_and_they_are_counted():
    items = [
        _Instruction("PLACE_ORDER_LIMIT", 0.25009999999999827),
        _Instruction("PLACE_ORDER_MARKET", 0.25),
        _Instruction("CANCEL_ORDERS", 0.3499999999999943),
        {"type": "PLACE_ORDER_LIMIT", "quantity": 0.3499999999999943},
        _Instruction("PLACE_ORDER_LIMIT", 0.25005),
    ]
    assert snap_instruction_quantities(items, 4) == 2
    assert venue_executed_quantity(items[0].quantity, 4) == 0.2501
    assert items[1].quantity == 0.25
    assert items[2].quantity == 0.3499999999999943     # not a placement
    assert venue_executed_quantity(items[3]["quantity"], 4) == 0.35
    assert items[4].quantity == 0.25005


# ---- F9: the seed split -------------------------------------------------------

def test_real_lots_go_to_the_tracker_and_legacy_dust_to_the_ledger():
    split = split_seed_plan(_plan(LIVE_RESIDUE), volume_decimals=4, min_order=0.25)
    assert {lot.book_id for lot in split.tracker_lots} == {101, 99, 39, 92, 12, 119, 33, 7, 86, 20}
    assert all(lot.residue_class == SEED_REAL for lot in split.tracker_lots)
    assert set(split.ledger) == {84, 117}
    assert abs(split.ledger_abs - 0.4997) < 1e-12
    assert split.inherited_real[39] == -0.5058


def test_the_regression_itself_dust_books_leave_the_tracker():
    """The entry builder skips every non-FLAT band; 109 dust books in the
    tracker is 109 books the agent can no longer enter."""
    venue = {b: 0.0003 * (1 + b % 7) for b in range(200, 309)}
    venue[39] = -0.7139
    on = split_seed_plan(_plan(venue), volume_decimals=4, min_order=0.25)
    off = split_seed_plan(_plan(venue), volume_decimals=4, min_order=0.25, ledger_enabled=False)
    assert len(on.tracker_lots) == 1 and len(on.ledger) == 109
    assert len(off.tracker_lots) == 110 and not off.ledger


def test_ledger_and_grid_off_is_exactly_the_a195_routing():
    plan = _plan(LIVE_RESIDUE)
    split = split_seed_plan(plan, volume_decimals=4, min_order=0.25,
                            ledger_enabled=False, grid_snap=False)
    assert [(l.book_id, l.net_base, l.residue_class) for l in split.tracker_lots] == \
        [(l.book_id, l.net_base, l.residue_class) for l in plan.lots]
    assert split.legacy_dust_abs == plan.dust_abs_base
    assert split.snapped_lots == 0 and split.reclassified_lots == 0


def test_the_class_boundary_is_read_on_the_grid_value():
    """0.24999999999999994 is an exitable 0.25; A1.9.5 filed it as dust."""
    plan = _plan({12: 0.24999999999999994})
    assert plan.lots[0].residue_class == SEED_LEGACY_DUST
    split = split_seed_plan(plan, volume_decimals=4, min_order=0.25)
    lot = split.tracker_lots[0]
    assert (lot.net_base, lot.residue_class) == (0.25, SEED_REAL)
    assert split.reclassified_lots == 1 and not split.ledger


def test_seeded_quantities_are_snapped_so_the_exit_flattens():
    split = split_seed_plan(_plan({36: 0.3499999999999943}), volume_decimals=4, min_order=0.25)
    lot = split.tracker_lots[0]
    assert lot.net_base == 0.35 and split.snapped_lots == 1
    assert venue_executed_quantity(wire_quantity(abs(lot.net_base), 4), 4) == 0.35


def test_the_legacy_bonus_is_the_imported_dust_in_both_modes():
    plan = _plan(LIVE_RESIDUE)
    on = split_seed_plan(plan, volume_decimals=4, min_order=0.25)
    off = split_seed_plan(plan, volume_decimals=4, min_order=0.25, ledger_enabled=False)
    assert abs(on.legacy_dust_abs - on.ledger_abs) < 1e-15
    assert abs(off.legacy_dust_abs - plan.dust_abs_base) < 1e-12


def test_the_ledger_is_charged_then_excused_and_session_dust_still_competes():
    ledger = 4.5422          # legacy dust measured at tick 1 of 20260912_121832
    assert abs(dust_class_exempt_abs(dust_abs=ledger, min_order=0.25, max_abs=2.0,
                                     legacy_bonus_abs=ledger) - ledger) < 1e-12
    base = dust_class_exempt_abs(dust_abs=50.0, min_order=0.25, max_abs=2.0)
    both = dust_class_exempt_abs(dust_abs=ledger + 50.0, min_order=0.25, max_abs=2.0,
                                 legacy_bonus_abs=ledger)
    assert abs(both - (base + ledger)) < 1e-12


def test_the_split_log_is_json_safe_and_versioned():
    row = split_seed_plan(_plan(LIVE_RESIDUE), volume_decimals=4, min_order=0.25).as_log()
    json.dumps(row)
    assert row["a196_legacy_baseline_version"] == A196_LEGACY_BASELINE_VERSION
    assert row["a196_ledger_books"] == 2 and row["a196_tracker_lots"] == 10


# ---- F10: inherited parked allowance -----------------------------------------

def _exempt(inherited, net, parked, cap=1.0):
    return inherited_parked_exemption(
        inherited_real=inherited, net_by_book=net, parked_books=parked, cap_abs=cap, eps=5e-5,
    )


def test_book_39_parked_is_excused_up_to_what_it_inherited():
    r = _exempt({39: -0.7139}, {39: -0.7139}, [39])
    assert r.exempt_abs == 0.7139 and r.books == (39,)
    assert not r.capped and r.retired == ()


def test_an_inherited_lot_that_is_not_parked_still_counts():
    r = _exempt({39: -0.7139}, {39: -0.7139}, [])
    assert r.exempt_abs == 0.0 and r.retired == ()


def test_the_allowance_is_capped_at_half_the_acquisition_budget():
    assert A196_INHERITED_PARKED_MAX_FRACTION == 0.5
    r = _exempt({39: -0.7139, 101: -0.6581}, {39: -0.7139, 101: -0.6581}, [39, 101],
                cap=A196_INHERITED_PARKED_MAX_FRACTION * 2.0)
    assert r.exempt_abs == 1.0 and r.capped
    assert abs(r.uncapped_abs - 1.372) < 1e-12


def test_a_flattened_or_crossed_book_is_retired():
    r = _exempt({39: -0.7139, 12: -0.4276, 7: 0.2558},
                {39: 0.0, 12: 0.25, 7: 0.2558}, [39, 12, 7])
    assert set(r.retired) == {39, 12}
    assert r.books == (7,) and r.exempt_abs == 0.2558


def test_a_session_add_on_is_never_excused():
    assert _exempt({39: -0.7139}, {39: -0.9639}, [39], cap=2.0).exempt_abs == 0.7139


def test_a_partial_exit_shrinks_the_allowance_with_the_position():
    assert _exempt({39: -0.7139}, {39: -0.4639}, [39]).exempt_abs == 0.4639


def test_missing_data_neither_excuses_nor_retires():
    r = _exempt({39: -0.7139}, {}, [39])
    assert r.exempt_abs == 0.0 and r.retired == () and r.books == ()


def test_the_empty_exemption_is_the_a195_charge():
    assert NO_INHERITED_EXEMPTION.exempt_abs == 0.0
    assert not NO_INHERITED_EXEMPTION.capped and NO_INHERITED_EXEMPTION.retired == ()


# ---- the admission row --------------------------------------------------------

def test_the_decomposition_is_the_gate_across_a_grid_of_states():
    for raw in (0.0, 0.5, 1.3663, 2.0, 6.8459):
        for dust_exempt in sorted({0.0, min(raw, 0.25), min(raw, 5.4796)}):
            for inherited in (0.0, 0.7139, 1.0):
                for reserved in (0.0, 0.25, 0.5):
                    for active in range(0, 8):
                        for open_books in (active, active + 1):
                            for dust_count in (0, 1, 7):
                                d = admission_decomposition(
                                    raw_total_abs=raw, ledger_abs=0.0, dust_abs=dust_exempt,
                                    dust_exempt_abs=dust_exempt, inherited_exempt_abs=inherited,
                                    reserved_abs=reserved, active_books=active,
                                    effective_open_books=open_books, dust_count=dust_count,
                                    max_abs=2.0, max_active=6, max_open=8, min_order=0.25,
                                )
                                effective = raw - dust_exempt + reserved
                                effective -= inherited
                                gate = admission_slots(
                                    effective_abs=effective, active_books=active,
                                    effective_open_books=open_books, dust_count=dust_count,
                                    max_abs=2.0, max_active=6, max_open=8, min_order=0.25,
                                )
                                assert d.portfolio_slots == gate
                                assert d.portfolio_slots == min(d.abs_slots, d.active_slots, d.open_slots)


def test_the_tick_2025_sample_reopens_under_f10():
    """20260912_203501 at tick 2025: productive 1.3663 BASE, Book 39 parked at
    -0.7139, two active books, dust present.  reserved_abs is not logged and is
    taken as zero."""
    common = dict(
        raw_total_abs=6.8459, ledger_abs=0.0, dust_abs=5.4796, dust_exempt_abs=5.4796,
        reserved_abs=0.0, active_books=2, effective_open_books=2, dust_count=1,
        max_abs=2.0, max_active=6, max_open=8, min_order=0.25,
    )
    before = admission_decomposition(inherited_exempt_abs=0.0, **common)
    after = admission_decomposition(inherited_exempt_abs=0.7139, **common)
    assert (before.portfolio_slots, before.binding) == (1, "ABS")
    assert (after.portfolio_slots, after.binding) == (3, "ACTIVE")
    assert abs(before.abs_headroom - 0.3837) < 1e-9
    assert abs(after.abs_headroom - 1.0976) < 1e-9


def test_the_admission_row_is_json_safe_and_versioned():
    row = admission_decomposition(
        raw_total_abs=1.0, ledger_abs=0.5, dust_abs=0.5, dust_exempt_abs=0.5,
        inherited_exempt_abs=0.0, reserved_abs=0.0, active_books=1, effective_open_books=1,
        dust_count=0, max_abs=2.0, max_active=6, max_open=8, min_order=0.25, enterable_books=115,
    ).as_log()
    json.dumps(row)
    assert row["a196_legacy_baseline_version"] == A196_LEGACY_BASELINE_VERSION
    assert row["enterable_books"] == 115


# ---- wiring -------------------------------------------------------------------

def test_the_version_names_a196():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_0_0"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_0_0"' in SIMPLE


def test_switches_exist_and_default_on():
    for flag in ("research_a196_legacy_dust_ledger",
                 "research_a196_inherited_parked_allowance",
                 "research_a196_quantity_grid_snap"):
        assert f'"{flag}", True' in SIMPLE, flag


def test_the_seed_routes_through_the_split_and_never_appends_plan_lots():
    body = _method_source("_a195_seed_inventory_from_venue")
    assert "split_seed_plan(" in body
    assert "for lot in split.tracker_lots:" in body
    assert "for lot in plan.lots:" not in body
    assert "self._a196_legacy_dust_ledger = dict(split.ledger)" in body
    assert body.index("self._a195_seed_done = True") < body.index("_open_positions")
    assert "_net_inventory(" not in body


def test_the_seed_reads_the_grid_from_the_state_before_the_unsynced_default():
    body = _method_source("_a196_volume_decimals")
    assert body.index('"volumeDecimals"') < body.index('"_research_volume_decimals"')


def test_reconcile_compares_the_venue_against_tracker_plus_ledger():
    assert "_a196_legacy_dust_ledger" in _method_source("_a195_local_base_by_book")


def test_admission_charges_the_ledger_and_applies_f10_before_the_gate():
    body = _method_source("build_mm_strategy_instructions")
    assert "abs_now += a196_ledger_abs_now" in body
    assert "dust_abs_now += a196_ledger_abs_now" in body
    pinned = "effective_abs_now = abs_now - dust_exempt_abs + float(reserved_abs)"
    f10 = "effective_abs_now -= float(a196_inherited.exempt_abs)"
    assert pinned in body and f10 in body
    assert body.index("abs_now += a196_ledger_abs_now") < body.index("dust_exempt_abs = self._a195_dust_exempt_abs(")
    assert body.index(pinned) < body.index(f10) < body.index("portfolio_slots = direct_liveness_admission_slots(")


def test_the_live_validator_gets_the_same_two_corrections():
    defs = _class_defs("_research_final_validate_instructions")
    assert len(defs) == 2
    dead, live = defs
    assert "_a196_" not in dead
    assert "filled_abs += a196_ledger_abs_now" in live
    assert "filled_dust_abs += a196_ledger_abs_now" in live
    assert live.index("filled_abs += a196_ledger_abs_now") < \
        live.index("filled_abs = max(0.0, filled_abs - self._a195_dust_exempt_abs(")
    assert live.index("_a196_inherited_parked_exempt(max_abs=max_abs)") < \
        live.index("shadow_abs = filled_abs + reserved_abs")


def test_the_wire_snap_runs_after_the_chain_and_before_every_post_pass():
    body = _method_source("respond")
    snap = body.index("self._a196_snap_outgoing_quantities(response, state)")
    assert body.index("super().respond(state)") < snap
    assert snap < body.index("_a195_cancel_orphan_orders")
    assert snap < body.index("_a191_service_reprice_cancels")


def test_the_inherited_allowance_fails_closed_and_never_ages_positions():
    body = _method_source("_a196_inherited_parked_report")
    assert "return NO_INHERITED_EXEMPTION" in body
    assert "except Exception:" in body
    assert "_net_inventory(" not in body


def test_the_admission_row_reports_the_gate_it_describes():
    assert "a196_gate_slots = int(portfolio_slots)" in _method_source("build_mm_strategy_instructions")
    emit = _method_source("_a196_emit_admission")
    assert '"A196_ADMISSION"' in emit
    assert "decomposition_matches_gate" in emit


def test_summary_stats_are_exported():
    for key in ("direct_a196_ledger_books", "direct_a196_ledger_abs",
                "direct_a196_inherited_exempt_abs", "direct_a196_inherited_exempt_max",
                "direct_a196_inherited_retired_books", "direct_a196_wire_quantities_moved",
                "direct_a196_admission_zero_samples"):
        assert key in SIMPLE, key


def test_module_records_the_measured_mechanisms():
    for text in ("442", "11,771", "82 of 87", "397 of the 442", "-0.7139",
                 "0.3499999999999943", "DecimalUtil::trunc"):
        assert text in MODULE, text


# ---- launcher -------------------------------------------------------------------

def test_launcher_recognises_a196_and_keeps_every_a195_guard():
    assert "strategy1_direct_v4_16_2_a1_9_6) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1" in LAUNCHER
    assert "strategy1_direct_v4_16_2_a1_9_5) A19X_BUILD=1; A195_BUILD=1" in LAUNCHER


def test_launcher_ships_the_a196_switches_in_the_params_value():
    params = re.search(r'^PARAMS="(.*?)"$', LAUNCHER, flags=re.S | re.M).group(1)
    for flag in ("research_a196_legacy_dust_ledger=1",
                 "research_a196_inherited_parked_allowance=1",
                 "research_a196_quantity_grid_snap=1",
                 "research_a195_dust_capacity_class=1"):
        assert flag in params, flag


def test_launcher_pins_the_a196_invariants():
    for text in ("for lot in split.tracker_lots:",
                 "abs_now \\+= a196_ledger_abs_now$",
                 "filled_abs \\+= a196_ledger_abs_now$",
                 "_a196_inherited_parked_report(max_abs=max_abs)",
                 "_a196_inherited_parked_exempt(max_abs=max_abs)",
                 "A196_INHERITED_PARKED_MAX_FRACTION = 0.5",
                 "self._a196_snap_outgoing_quantities(response, state)",
                 "math.nextafter(q, math.inf)",
                 "[preflight] A1.9.6 legacy baseline / inherited parked / quantity grid PASS"):
        assert text in LAUNCHER, text


def test_launcher_ledger_guard_cannot_be_satisfied_by_the_dust_line():
    """`abs_now += ...` is a substring of `dust_abs_now += ...`; the guard must
    match whole lines or deleting the real charge would still pass."""
    guard = re.compile(r"^[ \t]+abs_now \+= a196_ledger_abs_now$", re.M)
    assert guard.search(SIMPLE)
    assert not guard.search(SIMPLE.replace("        abs_now += a196_ledger_abs_now\n", ""))
    assert "grep -qE" in LAUNCHER


def test_launcher_reports_the_version_it_detected():
    assert 'version=${POLICY_VER}' in LAUNCHER
    assert "version=strategy1_direct_v4_16_2_a1_9_4" not in LAUNCHER
