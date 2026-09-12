"""A1.9.5 step 3 (F2 behaviour): venue truth seeds the local tracker.

The measured state this exists for, at tick 1 of log 20260912_121832:
120 of 128 books diverged, local_abs 0.0, venue_abs 8.8520.
"""
import ast
from pathlib import Path

from research_direct_dust_capacity import dust_class_ceiling_abs, dust_capacity_report
from research_direct_inventory_truth import (
    A195_INVENTORY_TRUTH_VERSION,
    A195_MAX_SEED_ABS_BASE,
    A195_MAX_SEED_BOOKS,
    SEED_LEGACY_DUST,
    SEED_REAL,
    build_seed_plan,
    legacy_dust_ceiling_bonus,
)
from research_direct_reconcile import reconcile_books

ROOT = Path(__file__).parents[1]
SIMPLE = (ROOT / "agents" / "strategy" / "Strategy1_Research_Simple.py").read_text()
MODULE = (ROOT / "agents" / "strategy" / "research_direct_inventory_truth.py").read_text()

# The measured tick-1 residue, top 12 books by magnitude.
LIVE_RESIDUE = {
    101: -0.6581, 99: -0.6524, 39: -0.5058, 92: 0.4945, 12: -0.4276,
    119: 0.4071, 33: -0.4055, 7: -0.2558, 86: -0.2530, 20: -0.2500,
    84: 0.2499, 117: 0.2498,
}
MID = 274.13


def _method_source(name: str) -> str:
    tree = ast.parse(SIMPLE)
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    defs = [n for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    assert defs, name
    return ast.get_source_segment(SIMPLE, defs[-1])


def _plan(venue, local=None, mid=None, **kw):
    return build_seed_plan(
        venue_net_by_book=venue,
        local_net_by_book=local or {},
        mid_by_book=mid or {b: MID for b in venue},
        min_order=0.25, tick=1, **kw,
    )


# ---- the classification that makes the seed survivable -------------------

def test_the_live_residue_splits_into_ten_real_and_two_dust():
    plan = _plan(LIVE_RESIDUE)
    assert plan.real_books == 10
    assert plan.dust_books == 2
    assert abs(plan.real_abs_base - 4.3098) < 1e-4
    assert abs(plan.dust_abs_base - 0.4997) < 1e-4


def test_a_position_at_exactly_min_order_is_real_not_dust():
    """0.2500 is exitable; 0.2499 is not.  The boundary decides the class."""
    plan = _plan({1: 0.25, 2: 0.2499})
    classes = {lot.book_id: lot.residue_class for lot in plan.lots}
    assert classes[1] == SEED_REAL
    assert classes[2] == SEED_LEGACY_DUST


def test_seeding_everything_would_brick_the_agent_without_the_split():
    """Why the two classes exist: 8.85 BASE against a 2.0 cap is admission 0."""
    naive = dust_capacity_report(
        total_abs=8.8520, dust_abs=4.54, min_order=0.25, max_abs=2.0,
    )
    assert naive["productive_abs_base"] > 2.0        # over cap -> no admission
    with_legacy = dust_capacity_report(
        total_abs=8.8520, dust_abs=4.54, min_order=0.25, max_abs=2.0,
        legacy_bonus_abs=4.54,
    )
    # The real half still counts -- the agent must work it off -- but the
    # unexitable half no longer blocks acquisition forever.
    assert abs(with_legacy["productive_abs_base"] - 4.312) < 1e-9
    assert with_legacy["dust_class_overflow_abs"] == 0.0


def test_the_legacy_bonus_is_exactly_the_dust_imported():
    plan = _plan(LIVE_RESIDUE)
    assert legacy_dust_ceiling_bonus(plan) == plan.dust_abs_base


def test_dust_created_after_startup_still_competes_for_the_original_ceiling():
    """The bonus must not become a licence for unbounded new dust."""
    base = dust_class_ceiling_abs(min_order=0.25, max_abs=2.0)
    bumped = dust_class_ceiling_abs(min_order=0.25, max_abs=2.0, legacy_bonus_abs=0.4997)
    assert bumped == base + 0.4997
    # And with no seed it is unchanged, so a build without F2 keeps F3 exactly.
    assert dust_class_ceiling_abs(min_order=0.25, max_abs=2.0, legacy_bonus_abs=0.0) == base


# ---- the two things that would do real damage ----------------------------

def test_seeded_lots_are_priced_at_mid_so_unrealized_pnl_is_zero():
    """The venue reports a balance, never a cost basis.  Any invented entry
    price fabricates PnL that the exit machinery would act on."""
    plan = _plan({101: -0.6581})
    lot = plan.lots[0]
    assert lot.price == MID
    _ts, qty, price, fee = lot.as_tuple()
    assert price == MID and fee == 0.0
    assert qty == 0.6581          # unsigned; direction is the deque
    assert not lot.is_long


def test_seeded_lots_are_not_backdated():
    """Backdating 120 positions would fire ABSOLUTE_PROTECTION on all of them
    at once -- the path carrying 97% of cubic downside."""
    plan = build_seed_plan(
        venue_net_by_book=LIVE_RESIDUE, local_net_by_book={},
        mid_by_book={b: MID for b in LIVE_RESIDUE}, min_order=0.25, tick=4200,
    )
    assert all(lot.tick == 4200 for lot in plan.lots)
    assert all(lot.as_tuple()[0] == 4200 for lot in plan.lots)


def test_a_book_the_tracker_already_holds_is_never_overwritten():
    """Local lots carry a real cost basis and a real age; a synthetic
    mid-priced lot would destroy that information."""
    plan = _plan(LIVE_RESIDUE, local={101: -0.6581, 99: 0.25})
    seeded = {lot.book_id for lot in plan.lots}
    assert 101 not in seeded and 99 not in seeded
    assert set(plan.skipped_already_held) == {101, 99}


def test_a_book_without_a_price_is_skipped_not_seeded_at_zero():
    plan = _plan({101: -0.6581, 99: -0.6524}, mid={101: MID, 99: 0.0})
    assert [lot.book_id for lot in plan.lots] == [101]
    assert plan.skipped_no_price == (99,)


def test_float_noise_is_not_a_position():
    plan = _plan({1: 1e-12, 2: 0.25})
    assert [lot.book_id for lot in plan.lots] == [2]


# ---- bounds ---------------------------------------------------------------

def test_the_book_count_is_bounded():
    plan = _plan({i: 0.1 for i in range(500)}, max_books=10)
    assert len(plan.lots) == 10
    assert plan.truncated


def test_the_aggregate_base_is_bounded():
    plan = _plan({i: 1.0 for i in range(500)}, max_abs_base=5.0)
    assert plan.total_abs_base <= 5.0
    assert plan.truncated


def test_biggest_positions_are_seeded_first_so_truncation_keeps_what_matters():
    plan = _plan(LIVE_RESIDUE, max_books=3)
    assert [lot.book_id for lot in plan.lots] == [101, 99, 39]


def test_the_defaults_cover_the_measured_live_state():
    assert A195_MAX_SEED_BOOKS >= 120
    assert A195_MAX_SEED_ABS_BASE >= 8.86


def test_empty_and_hostile_inputs_are_safe():
    assert build_seed_plan(
        venue_net_by_book={}, local_net_by_book={}, mid_by_book={},
        min_order=0.25, tick=0,
    ).lots == ()
    plan = _plan({1: float("nan"), 2: None, 3: "x", 4: 0.5})
    assert [lot.book_id for lot in plan.lots] == [4]


def test_log_payload_is_json_safe():
    import json
    json.dumps(_plan(LIVE_RESIDUE).as_log())
    assert _plan(LIVE_RESIDUE).as_log()[
        "a195_inventory_truth_version"] == A195_INVENTORY_TRUTH_VERSION


# ---- the reconcile map that makes the seed possible ----------------------

def test_every_diverged_book_is_carried_not_just_the_ranked_rows():
    """Step 1 capped detail at 12 worst-first rows, so on a 120-book
    divergence the other 108 were never identifiable."""
    class _Bal:
        def __init__(self, t, i): self.total, self.initial = t, i; self.free = self.reserved = None
    class _Acct:
        def __init__(self, b): self.base_balance = b
    accounts = {i: _Acct(_Bal(10.0 + i * 0.01, 10.0)) for i in range(40)}
    report = reconcile_books({i: 0.0 for i in range(40)}, accounts, max_detail_rows=5)
    payload = report.as_log()
    assert len(payload["diverged"]) == 5
    assert len(payload["venue_net_map"]) == 39
    assert report.venue_net_by_book[39] != 0.0


def test_the_map_is_absent_when_everything_agrees():
    report = reconcile_books({1: 0.25}, {})
    assert "venue_net_map" not in report.as_log() or not report.as_log().get("venue_net_map")


# ---- wiring ---------------------------------------------------------------

def test_the_seed_runs_before_the_frozen_chain():
    """Capacity, exit and ownership all read the tracker during super()."""
    body = _method_source("respond")
    assert "_a195_seed_inventory_from_venue(state)" in body
    assert body.index("_a195_seed_inventory_from_venue") < body.index("super().respond(state)")


def test_orphan_cancels_go_out_after_the_chain_has_budgeted():
    body = _method_source("respond")
    assert body.index("super().respond(state)") < body.index("_a195_cancel_orphan_orders")


def test_the_seed_is_one_shot_even_if_it_fails_partway():
    body = _method_source("_a195_seed_inventory_from_venue")
    assert "self._a195_seed_done = True" in body
    # Marked done BEFORE any mutation, or a retry double-counts what landed.
    assert body.index("self._a195_seed_done = True") < body.index("_open_positions")


def test_the_seed_reads_venue_net_not_the_raw_balance():
    body = _method_source("_a195_venue_net_by_book")
    assert "venue_net_base(" in body
    assert "reconcile_account_base" not in body


def test_the_seed_does_not_age_positions():
    body = _method_source("_a195_seed_inventory_from_venue")
    assert "_net_inventory(" not in body


def test_shutdown_cancel_became_startup_cancel_and_says_why():
    body = _method_source("_a195_cancel_orphan_orders")
    assert "atexit" in body
    assert "cancel_orders(" in body


def test_orphan_cancel_respects_the_per_book_instruction_budget():
    body = _method_source("_a195_cancel_orphan_orders")
    assert "max_instructions_per_book" in body


def test_switches_exist_for_both_halves():
    assert "research_a195_inventory_truth_enabled" in SIMPLE
    assert "research_a195_startup_orphan_cancel" in SIMPLE


def test_summary_stats_are_exported():
    for key in (
        "direct_a195_inventory_truth", "direct_a195_seed_books",
        "direct_a195_seed_real_books", "direct_a195_seed_real_abs",
        "direct_a195_seed_dust_books", "direct_a195_seed_dust_abs",
        "direct_a195_legacy_ceiling_bonus",
        "direct_a195_orphan_orders_cancelled", "direct_a195_orphan_books",
    ):
        assert key in SIMPLE, key


def test_module_records_the_vwap_and_age_hazards():
    assert "VWAP" in MODULE
    assert "AGE" in MODULE
    assert "8.85" in MODULE
