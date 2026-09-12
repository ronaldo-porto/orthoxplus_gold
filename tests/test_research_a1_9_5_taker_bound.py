"""A1.9.5 step 2.5 (F8): the declared taker loss floor must actually bind.

The defect these tests pin is not a wrong number -- it is a number that was
computed, logged, and then read by nothing.  So most of these assert on the
wiring (does the bound reach the instruction?) rather than on arithmetic.
"""
import ast
from pathlib import Path

from research_direct_taker_bound import (
    A195_DEFAULT_TAKER_FLOOR_BPS,
    A195_MAX_SLIPPAGE_FRACTION,
    A195_MIN_SLIPPAGE_FRACTION,
    A195_TAKER_BOUND_VERSION,
    slippage_fraction_for_floor,
    taker_bound_report,
)

ROOT = Path(__file__).parents[1]
SIMPLE = (ROOT / "agents" / "strategy" / "Strategy1_Research_Simple.py").read_text()
MODULE = (ROOT / "agents" / "strategy" / "research_direct_taker_bound.py").read_text()
BASE = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text()


def _method_source(name: str) -> str:
    """Last definition of `name` in the Simple class body.

    Resolved by position, not by name: this class defines
    `_research_final_validate_instructions` twice and Python keeps the second,
    so a name->node dict silently reads the dead one.
    """
    tree = ast.parse(SIMPLE)
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    defs = [n for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    assert defs, f"{name} is not defined in Strategy1_Research_Simple"
    return ast.get_source_segment(SIMPLE, defs[-1])


# ---- the zero-floor inversion --------------------------------------------

def test_a_zero_floor_never_becomes_an_unbounded_order():
    """0.0 on the wire means unbounded, so 0.0 must never be forwarded.

    NORMAL_TAKER_NONNEGATIVE declares exactly 0.0 ("no loss permitted").
    Forwarding it verbatim would turn the strictest floor into no floor.
    """
    assert slippage_fraction_for_floor(0.0) > 0.0
    assert slippage_fraction_for_floor(0.0) == abs(A195_DEFAULT_TAKER_FLOOR_BPS) / 1e4
    assert taker_bound_report(book_id=1, floor_bps=0.0)["floor_was_zero"] == 1


def test_missing_or_broken_floors_fall_back_to_a_real_bound_not_zero():
    for bad in (None, "", float("nan"), float("inf"), "not-a-number", []):
        assert slippage_fraction_for_floor(bad) >= A195_MIN_SLIPPAGE_FRACTION


def test_no_bound_is_ever_emitted_below_the_minimum():
    assert slippage_fraction_for_floor(-1e-9) == A195_MIN_SLIPPAGE_FRACTION
    assert A195_MIN_SLIPPAGE_FRACTION > 0.0


# ---- arithmetic -----------------------------------------------------------

def test_the_declared_floor_maps_to_its_own_magnitude():
    assert slippage_fraction_for_floor(-25.0) == 0.0025
    assert slippage_fraction_for_floor(-50.0) == 0.0050


def test_sign_is_irrelevant_because_the_venue_wants_a_magnitude():
    assert slippage_fraction_for_floor(-25.0) == slippage_fraction_for_floor(25.0)


def test_an_absurd_floor_is_capped():
    assert slippage_fraction_for_floor(-1e9) == A195_MAX_SLIPPAGE_FRACTION
    assert A195_MAX_SLIPPAGE_FRACTION < 1.0


def test_the_bound_reproduces_the_measured_breach():
    """-213.9 bps realized against a -25 bps floor was a 8.6x overshoot."""
    bound = slippage_fraction_for_floor(-25.0)
    assert bound * 1e4 == 25.0
    assert abs(-213.9) > bound * 1e4 * 8


def test_report_is_json_safe_and_versioned():
    import json
    payload = taker_bound_report(
        book_id=23, floor_bps=-25.0,
        authority="RISK", trigger="ABSOLUTE_PROTECTION_REDUCE",
    )
    assert payload["a195_taker_bound_version"] == A195_TAKER_BOUND_VERSION
    assert payload["bound_effective_bps"] == -25.0
    json.dumps(payload)


# ---- wiring: the part that was missing before ----------------------------

def test_the_frozen_base_still_sends_the_order_unbounded():
    """If this ever fails, the base was changed and F8 may be redundant."""
    close = BASE.split("def _execute_aggressive_close(")[1].split("\n    def ")[0]
    assert "market_order(" in close
    assert "max_slippage" not in close


def test_simple_overrides_the_close_and_delegates_to_the_base():
    body = _method_source("_execute_aggressive_close")
    assert "super()._execute_aggressive_close(" in body
    # The override must not reimplement the fee gate / volume cap / balance
    # checks -- duplicating them is how the two paths drift apart.
    for reimplemented in ("_passes_fee_gate", "_research_can_add_volume",
                          "base_balance", "market_order("):
        assert reimplemented not in body, reimplemented


def test_the_bound_is_attached_to_the_queued_market_order():
    body = _method_source("_a195_bind_taker_slippage")
    assert "PLACE_ORDER_MARKET" in body
    assert "instruction.max_slippage = fraction" in body
    # Only orders this call queued, and only for this book.
    assert "first_new" in body
    assert "bookId" in body


def test_an_already_bounded_instruction_is_left_alone():
    body = _method_source("_a195_bind_taker_slippage")
    assert 'getattr(instruction, "max_slippage", None) is not None' in body


def test_a_stale_authority_row_does_not_bound_this_order():
    """A floor from a previous tick describes a different decision."""
    body = _method_source("_a195_declared_floor_bps")
    assert '_tick' in body and 'tick' in body
    assert "return default" in body


def test_the_floor_recorded_is_the_one_that_ships():
    """The authority row is written before the recovery/WAIT reclassifications.

    Reading `result` after them is what makes the recorded floor match the
    decision that actually goes out.
    """
    assert 'self._direct_exit_authority_last[book_id]["allowed_loss_floor_bps"]' in SIMPLE
    idx_row = SIMPLE.index('self._direct_exit_authority_last[book_id] = {')
    idx_floor = SIMPLE.index(
        'self._direct_exit_authority_last[book_id]["allowed_loss_floor_bps"]')
    idx_wait = SIMPLE.index('taker_authority="NONE", trigger=str(getattr(decision, "reason", "WAIT"))')
    assert idx_row < idx_wait < idx_floor


def test_failure_to_bind_does_not_cancel_the_exit():
    body = _method_source("_execute_aggressive_close")
    assert "except Exception:" in body
    assert "return placed" in body


def test_the_switch_turns_it_off_completely():
    body = _method_source("_execute_aggressive_close")
    assert "_a195_taker_floor_enabled()" in body
    assert "research_a195_taker_floor_enforce" in SIMPLE


def test_summary_stats_are_exported():
    for key in (
        "direct_a195_taker_floor_enforce",
        "direct_a195_taker_bound_version",
        "direct_a195_taker_bound_applied",
        "direct_a195_taker_bound_zero_floor",
        "direct_a195_taker_bound_min_fraction",
    ):
        assert key in SIMPLE, key


def test_module_records_why_zero_is_dangerous():
    assert "unbounded" in MODULE
    assert "0.0" in MODULE
    assert "RECOVERY_TAKER_REDUCE" in MODULE

# ---- the launcher must refuse a build that regresses any of this ---------

def test_the_version_advances_so_the_guards_can_tell_the_builds_apart():
    """Steps 1+2 ran 4,688 ticks reporting a1_9_4.  That is the A1.9.3
    failure shape: a log that names the wrong revision cannot be attributed."""
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_5"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1_9_5"' in SIMPLE


def test_launcher_guards_the_new_phase():
    sh = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
    # A1.9.5 is recognised as its own build, and the cumulative A1.9.x guards
    # still run against it rather than silently switching themselves off.
    assert "strategy1_direct_v4_16_2_a1_9_5) A19X_BUILD=1; A195_BUILD=1" in sh
    assert 'if [[ "$A19X_BUILD" == "1" ]]; then' in sh
    for flag in ("research_a195_taker_floor_enforce=1",
                 "research_a195_inventory_truth_enabled=1",
                 "research_a195_breadth_lane_enabled=1"):
        assert flag in sh, flag


def test_launcher_refuses_an_unbounded_taker_exit():
    """The two properties that make F8 real: the close delegates to the frozen
    base, and the slippage clamp is strictly positive so a declared floor of
    0.0 can never ship as max_slippage=0.0, which the wire reads as
    UNBOUNDED."""
    sh = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
    assert "placed = super()._execute_aggressive_close(" in sh
    assert "A195_MIN_SLIPPAGE_FRACTION = 1e-4" in sh


def test_launcher_keeps_breadth_authority_somewhere():
    """A1.9.3's hook is retired, so the guard inverts rather than vanishing:
    the dead site must be gone AND the relocation must be present.  Without
    this, a later edit could delete breadth authority in silence."""
    sh = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
    assert "RETIRED in A1.9.5 step 4" in sh
    assert "_a195_breadth_relief(" in sh
