"""v6.0.0: a nearly full position is a lot, not dust (S1).

Measured on UID 125 (v5.0.3, ticks 0 to 11,113, 2026-09-16): the six positions that stayed parked for
4,773 to 8,642 ticks were 0.19 to 0.24999 BASE.  The build loop skipped every position under the 0.25
minimum before exit handling, so they lost their exits and their protection; clearing each at its first
refusal would have cost -4.20 quote, and they cost -146.6.  A position between two thirds of a lot and a
full lot is now a short lot and exits with one minimum-order clip.
"""
import ast
import os
import subprocess
import textwrap
import time
import typing
from pathlib import Path
from types import SimpleNamespace

import research_v600_short_lots as sl
from research_direct_risk_state import ExitPending, authorize_exit
from research_exit_quantity import REASON_EXACT, REASON_SAFER_RESIDUAL, choose_reduce_quantity
from _harness import defs_extractor, extractor

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
RESEARCH = (STRATEGY / "Strategy1_Research.py").read_text()
MODULE = (STRATEGY / "research_v600_short_lots.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()

MIN = 0.25
EPS = 0.00005  # half a base unit at 4 volume decimals
TWO_THIRDS = 2.0 / 3.0

# UID 125's six stuck positions: book -> (position, cost at first refusal in quote).
UID125 = {
    76: (-0.2499857, -0.79),
    84: (0.2469, -0.72),
    89: (-0.2486, -0.49),
    95: (-0.19, -0.35),
    101: (-0.2277, -1.16),
    116: (-0.2499565, -0.69),
}


_class_defs = defs_extractor(SIMPLE, cls_name="Strategy1_Research_Simple")


_method_source = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")


V600_METHODS = [
    "_v600_on", "_v600_tolerance", "_v600_classify_qty", "_v600_is_inherited_parked",
    "_v600_counts_as_dust", "_v600_skip_management", "_is_dust_qty", "_dust_compaction_safe_for_any_fill",
    "_v600_executable_qty", "_v600_chooser_kwargs", "_v600_count", "_v600_note_inherited_clip",
    "_v600_note_class", "_v600_note_own_fill", "_v600_settle_leftover", "_v600_telemetry",
    "_inventory_needs_management", "_direct_dust_count",
]


class _Base:
    """The frozen Research semantics these overrides call through super()."""

    research_dust_safe_close = True
    _research_exchange_min_order_size = MIN
    _research_volume_decimals = 4
    _a1961_base_decimals = 4

    def _execution_flat_epsilon(self):
        return EPS

    def _is_dust_qty(self, net_base):
        a = abs(float(net_base))
        return self.research_dust_safe_close and a >= EPS and a + 1e-12 < MIN

    def _dust_compaction_safe_for_any_fill(self, net_base):
        q = abs(float(net_base))
        return q > 0.5 * MIN and q < MIN


def _agent(on=True, fraction=TWO_THIRDS, inherited=sl.INHERITED_PARK):
    body = "".join(textwrap.indent(textwrap.dedent(_method_source(n)), "    ") + "\n" for n in V600_METHODS)
    scope = {
        "_Base": _Base, "Any": typing.Any,
        "V600_CLASS_DUST": sl.CLASS_DUST, "V600_CLASS_FLAT": sl.CLASS_FLAT,
        "V600_CLASS_PARKED": sl.CLASS_PARKED, "V600_CLASS_SHORT_LOT": sl.CLASS_SHORT_LOT,
        "V600_INHERITED_EXIT": sl.INHERITED_EXIT, "V600_INHERITED_PARK": sl.INHERITED_PARK,
        "V600_SOURCE_INHERITED": sl.SOURCE_INHERITED, "V600_SOURCE_LIVE": sl.SOURCE_LIVE,
        "V600_SOURCE_OFF_GRID": sl.SOURCE_OFF_GRID,
        "V600_SHORT_LOT_FRACTION_DEFAULT": sl.V600_SHORT_LOT_FRACTION_DEFAULT,
        "V600_SHORT_LOTS_VERSION": sl.V600_SHORT_LOTS_VERSION,
        "V600_STATE_EVERY_TICKS": sl.V600_STATE_EVERY_TICKS,
        "v600_classify_position": sl.classify_position, "v600_exit_from_fill": sl.exit_from_fill,
        "v600_leftover_to_residue": sl.leftover_to_residue, "v600_leftover_tolerance": sl.leftover_tolerance,
        "v600_median": sl.median, "v600_short_lot_boundary": sl.short_lot_boundary,
    }
    exec("from __future__ import annotations\nclass Harness(_Base):\n" + body, scope)
    agent = scope["Harness"]()
    agent.research_v600_short_lots = on
    agent.research_v600_short_lot_min_fraction = fraction
    agent.research_v600_inherited_short_lots = inherited
    agent.rows = []
    agent._emit = lambda kind, **kw: agent.rows.append(dict(kw, type=kind))
    agent._tick = 0
    agent.net = {}
    agent._open_positions = {}
    agent.residue = {}
    agent._position_tracker_snapshot = lambda bid: SimpleNamespace(net_qty=agent.net.get(int(bid), 0.0))
    agent._research_abs_inventory = lambda bid: abs(agent.net.get(int(bid), 0.0))

    def add_residue(bid, amount):
        agent.residue[int(bid)] = agent.residue.get(int(bid), 0.0) + float(amount)
        return agent.residue[int(bid)]

    agent._v502_add_residue = add_residue
    return agent


def _old_dust(q):
    return q > EPS and q + 1e-12 < MIN


def _block(name, start, end):
    """The source lines of one live-validator block, for executing it on its own."""
    src = _method_source(name)
    i = src.index(start)
    i = src.rfind("\n", 0, i) + 1
    j = src.index(end, i)
    return textwrap.dedent("\n".join(line for line in src[i:j].splitlines()))


# ---- T1 classification ------------------------------------------------------------------------------

def test_t1_sizes_classify_at_the_boundary_and_the_clip_tolerance():
    kw = dict(min_order=MIN, eps=EPS, fraction=TWO_THIRDS, tolerance=sl.leftover_tolerance(4))
    expect = {
        0.0: sl.CLASS_FLAT, 0.00004: sl.CLASS_FLAT, 0.0014: sl.CLASS_DUST, 0.1249: sl.CLASS_DUST,
        0.1666: sl.CLASS_DUST, 0.1667: sl.CLASS_SHORT_LOT, 0.19: sl.CLASS_SHORT_LOT,
        0.2486: sl.CLASS_SHORT_LOT, 0.2497: sl.CLASS_SHORT_LOT, 0.2498: sl.CLASS_SHORT_LOT,
        0.2499857: sl.CLASS_SHORT_LOT, 0.25: sl.CLASS_FULL, 0.5: sl.CLASS_FULL,
    }
    for qty, cls in expect.items():
        assert sl.classify_position(qty, **kw) == cls, qty
        assert sl.classify_position(-qty, **kw) == cls, -qty
    # The clip tolerance makes an off-grid lot a short lot whatever the fraction.
    assert sl.classify_position(0.2499857, **dict(kw, fraction=0.99999)) == sl.CLASS_SHORT_LOT
    assert sl.classify_position(0.2497, **dict(kw, fraction=0.99999)) == sl.CLASS_DUST
    # Switch off is v5.0.4: everything under the minimum is dust.
    assert sl.classify_position(0.2497, **dict(kw, enabled=False)) == sl.CLASS_DUST


def test_t1_every_dust_site_gives_the_same_answer():
    agent = _agent()
    for qty in (0.0014, 0.1, 0.1249, 0.13, 0.1666, 0.1667, 0.19, 0.2486, 0.2497, 0.2498, 0.2499857):
        dust = sl.classify_position(qty, min_order=MIN, eps=EPS, fraction=TWO_THIRDS,
                                    tolerance=0.0002) == sl.CLASS_DUST
        assert agent._is_dust_qty(qty) == dust, qty
        assert agent._is_dust_qty(-qty) == dust, qty
        assert agent._v600_counts_as_dust(1, qty, eps=EPS, min_order=MIN) == dust, qty
        assert agent._v600_skip_management(1, qty, eps=EPS, min_order=MIN) == dust, qty
        inv = SimpleNamespace(band="LONG", net_base=qty, _research_book_id=1)
        assert agent._inventory_needs_management(inv) == (not dust), qty
        # The compactor only ever sees dust in (min/2, boundary); a short lot is never compacted.
        assert agent._dust_compaction_safe_for_any_fill(qty) == (dust and qty > 0.5 * MIN), qty


def test_t1_a_residue_growing_into_a_short_lot_changes_class_once():
    agent = _agent()
    for qty in (0.0014, 0.0014, 0.2486, 0.2486, 0.0):
        agent._v600_note_class(89, qty)
    rows = [(r["prior"], r["cls"]) for r in agent.rows if r["type"] == "V600_SHORT_LOT"]
    assert rows == [(sl.CLASS_FLAT, sl.CLASS_DUST), (sl.CLASS_DUST, sl.CLASS_SHORT_LOT),
                    (sl.CLASS_SHORT_LOT, sl.CLASS_FLAT)]
    short = [r for r in agent.rows if r["cls"] == sl.CLASS_SHORT_LOT][0]
    assert short["action"] == "EXIT_AS_LOT" and short["source"] == sl.SOURCE_LIVE
    agent._v600_note_class(76, 0.2499857)
    assert agent.rows[-1]["source"] == sl.SOURCE_OFF_GRID


# ---- T2 exit size -----------------------------------------------------------------------------------

def test_t2_a_short_lot_exits_with_one_minimum_clip_and_a_smaller_leftover():
    for qty in (0.1667, 0.19, 0.2277, 0.2469, 0.2486, 0.2499857):
        for sign in (1.0, -1.0):
            d = choose_reduce_quantity(inventory=sign * qty, desired=qty, min_order=MIN, volume_decimals=4)
            # 0.2499857 rounds to 0.25 at four decimals, so its clip is the exact reduce.
            assert d.quantity == MIN and d.reason in (REASON_SAFER_RESIDUAL, REASON_EXACT), (qty, d)
            assert abs(d.inventory_after) < qty and d.inventory_after * sign <= 0.0


def test_t2_full_lots_keep_exact_reduction_and_half_a_lot_cannot_exit():
    for qty, want in ((0.25, 0.25), (0.5, 0.25), (0.75, 0.5)):
        d = choose_reduce_quantity(inventory=qty, desired=want, min_order=MIN, volume_decimals=4)
        assert d.reason == REASON_EXACT and d.quantity == want
    # At or below half a lot the clip leaves an opposite leftover at least as large: never legal.
    assert choose_reduce_quantity(inventory=0.125, desired=0.125, min_order=MIN, volume_decimals=4).quantity == 0.0
    assert sl.parse_fraction(0.5) is None and sl.parse_fraction(1.0) is None
    assert sl.parse_fraction(0.6667) == 0.6667 and sl.parse_fraction("abc") is None


# ---- T3 leftover ------------------------------------------------------------------------------------

def _fill(agent, book, before, after):
    agent._open_positions[book] = {"longs": [("lot",)] if after > 0 else [],
                                   "shorts": [("lot",)] if after < 0 else []}
    agent.net[book] = after
    agent._v600_note_own_fill(SimpleNamespace(price=100.0), book_id=book, before=before, after=after)
    agent._v600_settle_leftover(book)


def test_t3_a_leftover_within_two_units_is_residue_and_leaves_no_dust():
    agent = _agent()
    _fill(agent, 7, 0.2499, -0.0001)
    assert agent.residue == {7: -0.0001}
    assert agent._open_positions[7] == {"longs": [], "shorts": []}
    assert agent._v600_counts["leftover_residue"] == 1
    agent.net[7] = 0.0
    assert not agent._v600_counts_as_dust(7, 0.0, eps=EPS, min_order=MIN)
    # No dust means no recovery reserve: admission's dust count stays at zero.
    state = SimpleNamespace(books={7: object()})
    assert agent._direct_dust_count(state) == 0


def test_t3_a_larger_leftover_follows_the_dust_path_unchanged():
    agent = _agent()
    _fill(agent, 95, -0.19, 0.06)
    assert agent.residue == {} and agent._open_positions[95]["longs"] == [("lot",)]
    assert agent._v600_counts["leftover_dust"] == 1
    assert agent._v600_counts_as_dust(95, 0.06, eps=EPS, min_order=MIN)
    state = SimpleNamespace(books={95: object()})
    agent.net[95] = 0.06
    assert agent._direct_dust_count(state) == 1


def test_t3_a_full_lot_exit_and_a_same_side_fill_move_nothing():
    agent = _agent()
    _fill(agent, 3, 0.25, -0.0001)
    _fill(agent, 4, 0.2469, 0.1)
    assert agent.residue == {}
    assert sl.leftover_to_residue(-0.00001, before=0.2, flat_eps=EPS, tolerance=0.0002) is None


# ---- T4 accounting ----------------------------------------------------------------------------------

def test_t4_a_short_lot_is_an_active_book_and_productive_base():
    agent = _agent()
    shadow_net = {1: 0.2469, 2: -0.1, 3: 0.25, 4: 0.0, 5: -0.24996}
    scope = {"self": agent, "shadow_net": shadow_net, "eps": EPS, "min_size": MIN}
    exec(_block("_research_final_validate_instructions", "if self._v600_on():\n", "# A1.9.5 F3, gate B."), scope)
    assert scope["filled_active"] == 3  # 1, 3 and 5; the dust on 2 is not a book
    exec(_block("_research_final_validate_instructions", "if self._v600_on():\n            filled_dust_abs", "# A1.9.6 F9"), scope)
    assert abs(scope["filled_dust_abs"] - 0.1) < 1e-12


def test_t4_admission_and_the_live_validator_agree_just_under_the_minimum():
    agent = _agent()
    qty = 0.24996  # the old validator counted this active (qty + eps >= min) while admission called it dust
    assert qty + EPS >= MIN and _old_dust(qty)
    screen = _block("_research_fast_screen", "has_inv = qty > eps\n", "if (has_inv or bid in")
    scope = {"self": agent, "qty": qty, "eps": EPS, "min_size": MIN, "bid": 5}
    exec(screen, scope)
    assert scope["is_dust"] is False
    scope = {"self": agent, "shadow_net": {5: qty}, "eps": EPS, "min_size": MIN}
    exec(_block("_research_final_validate_instructions", "if self._v600_on():\n", "# A1.9.5 F3, gate B."), scope)
    assert scope["filled_active"] == 1


# ---- T5 protection ----------------------------------------------------------------------------------

def test_t5_an_adverse_short_lot_reaches_the_same_protection_as_a_full_lot():
    agent = _agent()
    kwargs = dict(inventory_qty=0.2469, min_order=MIN, is_dust=True, valid_opposite_touch=True,
                  reduction_executable=False)
    agent._v600_chooser_kwargs(kwargs)
    assert kwargs["inventory_qty"] == MIN and kwargs["is_dust"] is False and kwargs["reduction_executable"] is True
    crossed = dict(inventory_qty=0.2469, min_order=MIN, valid_opposite_touch=False, reduction_executable=False)
    agent._v600_chooser_kwargs(crossed)
    assert crossed["reduction_executable"] is False  # a crossed touch still parks, as for a full lot
    for qty in (0.1, 0.25, 0.5):
        untouched = dict(inventory_qty=qty, min_order=MIN, reduction_executable=False)
        agent._v600_chooser_kwargs(untouched)
        assert untouched["inventory_qty"] == qty and untouched["reduction_executable"] is False
    # The position risk state: a pending ABSOLUTE short lot resting a loss maker is taken like a full lot.
    maker = SimpleNamespace(action="MAKER_EXIT", reason="X", risk_band="ABSOLUTE_PROTECTION")
    pending = ExitPending(sign=1, entry=100.0, since_tick=0)

    def run(qty):
        return authorize_exit(
            pending=pending, base_decision=maker, decision=maker, maker_net_bps=-80.0,
            taker_net_bps=-60.0, inventory_qty=qty, min_order=MIN, taker_clip=MIN, is_dust=False,
            touch_two_sided=True, a198_enabled=False,
        )

    assert run(0.25)[1] == "LOSS_MAKER"
    assert run(0.2469)[1] is None  # what v5.0.4 did: never executable
    assert run(agent._v600_executable_qty(0.2469, MIN))[1] == "LOSS_MAKER"
    assert agent._v600_executable_qty(0.1, MIN) == 0.1 and agent._v600_executable_qty(0.5, MIN) == 0.5


def test_t5_the_hooks_sit_where_the_frozen_caller_decides():
    chooser = _method_source("_research_apply_unified_exit")
    assert chooser.index("self._v600_chooser_kwargs(exit_kwargs)") < chooser.index("choose_observable_position_exit(**exit_kwargs)")
    auth = _method_source("_a199_authorize_exit")
    assert "inventory_qty = self._v600_executable_qty(inventory_qty, exit_kwargs.get(\"min_order\", 0.25))" in auth
    assert "inventory_qty=inventory_qty," in auth
    # The A1.9.5 taker bound and the Research exit path are the frozen ones.
    assert 'RESEARCH_POLICY_VERSION = "simplified_hybrid_authority_v4_16_2"' in RESEARCH
    assert "v600" not in RESEARCH


# ---- T6 inherited policy ----------------------------------------------------------------------------

def test_t6_a_rebuilt_clip_stays_parked_until_its_position_changes():
    agent = _agent()
    agent._v600_note_inherited_clip(116, MIN)
    assert agent._v600_counts_as_dust(116, 0.25, eps=EPS, min_order=MIN)
    assert agent._v600_skip_management(116, 0.25, eps=EPS, min_order=MIN)
    assert agent._v600_counts_as_dust(116, 0.2499, eps=EPS, min_order=MIN)  # within the clip tolerance
    assert agent.rows[-1]["cls"] == sl.CLASS_PARKED and agent.rows[-1]["source"] == sl.SOURCE_INHERITED
    # A new position on the book is a new lot.
    assert not agent._v600_counts_as_dust(116, 0.5, eps=EPS, min_order=MIN)
    assert 116 not in agent._v600_inherited_parked and agent._v600_counts["inherited_released"] == 1


def test_t6_exit_mode_lets_the_clip_trade_and_the_seed_calls_the_hook():
    agent = _agent(inherited=sl.INHERITED_EXIT)
    agent._v600_note_inherited_clip(76, MIN)
    assert not getattr(agent, "_v600_inherited_parked", {}) and agent._v600_counts["inherited_exit"] == 1
    assert not agent._v600_counts_as_dust(76, 0.25, eps=EPS, min_order=MIN)
    seed = "".join(_class_defs("_a196_seed_from_venue") or [SIMPLE])
    assert "self._v600_note_inherited_clip(int(clip_book), float(min_order))" in seed
    assert sl.inherited_mode("PARK") == "park" and sl.inherited_mode("adopt") is None


def test_t6_every_inherited_single_lot_is_parked_not_only_rebuilt_clips():
    """The startup dry run on UID 125 (validator gauges, 2026-09-17 02:54): buy fees are charged in base,
    so books 76 and 116 sit at -0.2716 and -0.2515 on the venue and the seed rebuilds them as whole lots
    priced at today's quote.  Without this they would be exited at startup, realizing about -41 quote."""
    seed = SIMPLE[SIMPLE.index("self._a196_inherited_real = inherited\n"):]
    hook = seed[:seed.index("# A1.9.6.1: books the seed could not price.")]
    assert "abs(float(inherited_net)) + 1e-12 < 2.0 * float(min_order)" in hook
    deferred = SIMPLE[SIMPLE.index("inherited[int(book_id)] = net\n"):][:400]
    assert "self._v600_note_inherited_clip(int(book_id), abs(net))" in deferred
    agent = _agent()
    for book, net in ((36, 0.2501), (76, -0.2716), (116, -0.2515)):
        agent._v600_note_inherited_clip(book, abs(net))
        assert agent._v600_skip_management(book, abs(net), eps=EPS, min_order=MIN), book
        assert not agent._inventory_needs_management(
            SimpleNamespace(band="SHORT", net_base=net, _research_book_id=book)), book
    # A partial exit or an add-on is a different position and trades normally again.
    assert not agent._v600_skip_management(76, 0.5216, eps=EPS, min_order=MIN)
    # A flat book (a new simulation) ends the inherited position: a fresh 0.25 lot on book 36 trades.
    agent._v600_note_class(36, 0.0)
    assert 36 not in agent._v600_inherited_parked
    assert not agent._v600_skip_management(36, 0.25, eps=EPS, min_order=MIN)
    # While parked, the class log says so even for a whole lot.
    agent._v600_note_class(116, 0.2515)
    assert agent._v600_class.get(116) == sl.CLASS_PARKED


# ---- T7 switch off ----------------------------------------------------------------------------------

def test_t7_switch_off_is_v5_0_4():
    agent = _agent(on=False)
    agent._v600_note_inherited_clip(116, MIN)
    assert not getattr(agent, "_v600_inherited_parked", {})
    for qty in (0.0, 0.00004, 0.0014, 0.1, 0.13, 0.1667, 0.2469, 0.24996, 0.2499857, 0.25, 0.5):
        assert agent._is_dust_qty(qty) == _Base()._is_dust_qty(qty), qty
        assert agent._dust_compaction_safe_for_any_fill(qty) == _Base()._dust_compaction_safe_for_any_fill(qty)
        assert agent._v600_counts_as_dust(1, qty, eps=EPS, min_order=MIN) == _old_dust(qty), qty
        assert agent._v600_executable_qty(qty, MIN) == qty
        kwargs = dict(inventory_qty=qty, min_order=MIN, reduction_executable=False)
        agent._v600_chooser_kwargs(kwargs)
        assert kwargs == dict(inventory_qty=qty, min_order=MIN, reduction_executable=False)
    shadow_net = {1: 0.2469, 2: -0.1, 3: 0.25, 5: -0.24996}
    scope = {"self": agent, "shadow_net": shadow_net, "eps": EPS, "min_size": MIN}
    exec(_block("_research_final_validate_instructions", "if self._v600_on():\n", "# A1.9.5 F3, gate B."), scope)
    assert scope["filled_active"] == sum(1 for n in shadow_net.values() if abs(n) + EPS >= MIN)
    _fill(agent, 7, 0.2499, -0.0001)
    agent._v600_telemetry(None)
    assert agent.residue == {} and agent.rows == []
    build = _method_source("build_mm_strategy_instructions")
    assert "dust_skip = qty_abs > eps and qty_abs + 1e-12 < min_size_local" in build


# ---- T8 UID 125's six cases -------------------------------------------------------------------------

def test_t8_each_of_uid_125s_six_positions_is_managed_and_sized_to_exit():
    agent = _agent()
    cost_then = 0.0
    for book, (qty, cost) in UID125.items():
        assert agent._v600_classify_qty(abs(qty), eps=EPS, min_order=MIN) == sl.CLASS_SHORT_LOT, book
        assert not agent._v600_skip_management(book, abs(qty), eps=EPS, min_order=MIN), book
        d = choose_reduce_quantity(inventory=qty, desired=abs(qty), min_order=MIN, volume_decimals=4)
        assert d.quantity == MIN and abs(d.inventory_after) <= 0.06 + 1e-9, (book, d)
        cost_then += cost
    # v5.0.3 parked them; clearing each at its first refusal would have cost this much.
    assert round(cost_then, 2) == -4.20
    # The two off-grid entries leave less than two base units: residue, not a new dust book.
    for book in (76, 116):
        qty = UID125[book][0]
        after = choose_reduce_quantity(inventory=qty, desired=abs(qty), min_order=MIN, volume_decimals=4).inventory_after
        assert abs(after) < EPS or sl.leftover_to_residue(after, before=qty, flat_eps=EPS, tolerance=0.0002) is not None


# ---- T9 live active cap -----------------------------------------------------------------------------

def test_t9_the_live_validator_counts_short_lots_against_the_active_cap():
    agent = _agent()
    # Six short lots fill the six-book active cap exactly as six full lots would.
    shadow_net = {b: q for b, (q, _cost) in UID125.items()}
    shadow_net.update({200: 0.1, 201: -0.05})
    scope = {"self": agent, "shadow_net": shadow_net, "eps": EPS, "min_size": MIN}
    exec(_block("_research_final_validate_instructions", "if self._v600_on():\n", "# A1.9.5 F3, gate B."), scope)
    assert scope["filled_active"] == 6
    exec(_block("_research_final_validate_instructions", "if self._v600_on():\n            filled_dust_abs", "# A1.9.6 F9"), scope)
    assert abs(scope["filled_dust_abs"] - 0.15) < 1e-12
    # A parked inherited clip is dust to both gates.
    agent._v600_note_inherited_clip(300, MIN)
    scope = {"self": agent, "shadow_net": {300: 0.25}, "eps": EPS, "min_size": MIN}
    exec(_block("_research_final_validate_instructions", "if self._v600_on():\n", "# A1.9.5 F3, gate B."), scope)
    assert scope["filled_active"] == 0


def test_t9_only_the_live_validator_was_changed():
    defs = _class_defs("_research_final_validate_instructions")
    assert len(defs) == 2, "the validator is still defined twice; the second definition is the live one"
    assert "_v600_counts_as_dust" not in defs[0] and "_v600_counts_as_dust" in defs[1]


# ---- T10 launcher -----------------------------------------------------------------------------------

def test_t10_the_arm_settings_params_and_guards():
    assert ("  strategy1_direct_v6_0_0) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; "
            "A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; "
            "V502_BUILD=1; V503_BUILD=1; V504_BUILD=1; V600_BUILD=1 ;;") in LAUNCHER
    assert "  strategy1_direct_v5_0_4) A19X_BUILD=1" in LAUNCHER, "earlier arms stay"
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_13"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_13"' in SIMPLE
    assert sl.V600_SHORT_LOTS_VERSION.endswith("v6_0_0")
    for default in ('HISTORY_ANCHOR="${HISTORY_ANCHOR:-auto}"', 'LEGACY_SESSION="${LEGACY_SESSION:-ignore}"',
                    'RECORDER_MAX_MB="${RECORDER_MAX_MB:-8192}"', 'SHORT_LOT_FRACTION="${SHORT_LOT_FRACTION:-0.6667}"',
                    'INHERITED_SHORT_LOTS="${INHERITED_SHORT_LOTS:-park}"'):
        assert default in LAUNCHER, default
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"', start + len('PARAMS="'))]
    for switch in ("research_v600_short_lots=1", "research_v600_short_lot_min_fraction=${SHORT_LOT_FRACTION}",
                   "research_v600_inherited_short_lots=${INHERITED_SHORT_LOTS}",
                   "research_v504_session_per_uid=1", "research_v504_disk_budget=1",
                   "research_v504_mirror_rounds=1"):
        assert switch in params, switch
    for switch in ("research_v600_short_lots", "research_v600_short_lot_min_fraction",
                   "research_v600_inherited_short_lots"):
        assert f'getattr(self.config, "{switch}"' in SIMPLE, switch
    guards = (
        "dust_skip = self._v600_skip_management(book_id, qty_abs, eps=eps, min_order=min_size_local)",
        "is_dust = bool(has_inv and self._v600_counts_as_dust(bid, qty, eps=eps, min_order=min_size))",
        "and not self._v600_counts_as_dust(bid, abs(float(net)), eps=eps, min_order=min_size)",
        'inventory_qty = self._v600_executable_qty(inventory_qty, exit_kwargs.get("min_order", 0.25))',
        "self._v600_chooser_kwargs(exit_kwargs)",
        "self._v600_settle_leftover(int(book_id))",
        "self._v600_note_inherited_clip(int(clip_book), float(min_order))",
        "self._v600_note_inherited_clip(int(inherited_book), abs(float(inherited_net)))",
    )
    for literal in guards:
        assert literal in SIMPLE and literal in LAUNCHER, literal
    assert "tests/test_research_v6_0_0_short_lots.py" in LAUNCHER
    assert "[preflight] v6.0.0 short lots PASS" in LAUNCHER
    for key in ("direct_v600_short_lots_version", "direct_v600_short_lots", "direct_v600_short_lot_min_fraction",
                "direct_v600_inherited_short_lots", "direct_v600_short_lot_books", "direct_v600_inherited_parked",
                "direct_v600_errors"):
        assert f'stats["{key}"]' in SIMPLE, key


def test_t10_the_value_checks_refuse_bad_settings():
    start = LAUNCHER.index('if [[ "$V600_BUILD" == "1" ]]; then')
    block = LAUNCHER[start:LAUNCHER.index("  grep -qF", start)]
    script = "set -u\nV600_BUILD=1\n" + block + "echo VALID\nfi\n"
    for fraction, inherited, ok in (
        ("0.6667", "park", True), ("0.5001", "exit", True), ("0.5", "park", False), ("1.0", "park", False),
        ("abc", "park", False), ("0.6667", "adopt", False), ("0.6667", "", False),
    ):
        env = dict(os.environ, SHORT_LOT_FRACTION=fraction, INHERITED_SHORT_LOTS=inherited)
        out = subprocess.run(["bash", "-c", 'SHORT_LOT_FRACTION="$SHORT_LOT_FRACTION"; '
                              'INHERITED_SHORT_LOTS="$INHERITED_SHORT_LOTS"\n' + script],
                             capture_output=True, text=True, env=env)
        assert ("VALID" in out.stdout) == ok, (fraction, inherited, out.stdout, out.stderr)


# ---- T11 cost ---------------------------------------------------------------------------------------

def test_t11_the_hooks_cost_under_a_fifth_of_a_millisecond_per_tick():
    agent = _agent()
    agent._v600_note_inherited_clip(300, MIN)
    held = [0.2469, 0.1, 0.25, 0.19, 0.0014, 0.5, 0.2499857, 0.75, 0.13, 0.2277, 0.25, 0.25]
    samples = []
    for _ in range(300):
        t0 = time.perf_counter()
        # One tick: admission and the validator test every held book, the build loop skips or manages it,
        # the fast screen logs its class.
        for bid, qty in enumerate(held):
            agent._v600_counts_as_dust(bid, qty, eps=EPS, min_order=MIN)
            agent._v600_counts_as_dust(bid, qty, eps=EPS, min_order=MIN)
            agent._v600_skip_management(bid, qty, eps=EPS, min_order=MIN)
            agent._v600_note_class(bid, qty)
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    assert samples[int(0.95 * len(samples))] < 0.2, samples[int(0.95 * len(samples))]


def test_t11_the_module_is_pure():
    tree = ast.parse(MODULE)
    assert {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} == {"__future__", "dataclasses", "typing"}
    assert {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} == {"math"}
