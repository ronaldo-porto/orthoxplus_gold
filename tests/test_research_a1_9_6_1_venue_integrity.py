"""A1.9.6.1: seed only from a real quote, mirror BASE-denominated fees, judge
taker exits against their own decision.

Measured on the first A1.9.6 run, log 20260913_010511 (stopped at tick 2,326).
"""
import ast
import collections
import json
import math
import re
import types
import typing
from pathlib import Path

from research_direct_legacy_baseline import inherited_parked_exemption
from research_direct_venue_integrity import (
    A1961_SEED_VALID_STREAK,
    A1961_VENUE_INTEGRITY_VERSION,
    SEED_DROP,
    SEED_NOW,
    SEED_WAIT,
    apply_fee_residue,
    base_fee_units,
    pending_seed_step,
    route_unpriced_books,
    taker_outcome,
    touch_mid,
    valid_touch,
)

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
RESEARCH = (STRATEGY / "Strategy1_Research.py").read_text()
BASE = (STRATEGY / "Strategy1.py").read_text()
MODULE = (STRATEGY / "research_direct_venue_integrity.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()


def _levels(*prices):
    return [types.SimpleNamespace(price=p, quantity=1.0) for p in prices]


def _class_defs(src, cls_name, name):
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and (cls_name is None or node.name == cls_name):
            out += [ast.get_source_segment(src, n) for n in node.body
                    if isinstance(n, ast.FunctionDef) and n.name == name]
    return out


def _method_source(name):
    defs = _class_defs(SIMPLE, "Strategy1_Research_Simple", name)
    assert defs, name
    return defs[-1]


# ---- the quote the seed may price from -------------------------------------------

def test_the_live_crossed_quotes_are_not_prices():
    assert valid_touch(_levels(330.10), _levels(183.73)) is None     # book 39, tick 1
    assert valid_touch(_levels(411.37), _levels(285.28)) is None     # book 99, tick 1
    assert touch_mid(_levels(411.37), _levels(285.28)) is None


def test_a_normal_and_a_locked_quote_are_prices():
    assert abs(touch_mid(_levels(238.09, 238.05), _levels(238.11, 238.2)) - 238.10) < 1e-9
    assert valid_touch(_levels(100.0), _levels(100.0)) == (100.0, 100.0)   # the frozen predicate allows it


def test_an_out_of_order_level_zero_is_not_a_price_even_when_uncrossed():
    """The strategy reads levels in payload order; level 0 must be the best."""
    assert valid_touch(_levels(330.10, 331.00), _levels(331.20)) is None
    assert valid_touch(_levels(330.10), _levels(331.20, 331.00)) is None


def test_a_top_level_with_no_size_is_not_a_price():
    empty_bid = [types.SimpleNamespace(price=195.86, quantity=0.0)]
    assert valid_touch(empty_bid, _levels(195.87)) is None
    assert valid_touch(_levels(195.86), [types.SimpleNamespace(price=195.87, quantity=float("nan"))]) is None
    assert valid_touch([types.SimpleNamespace(price=195.86)], [types.SimpleNamespace(price=195.87)]) == (195.86, 195.87)


def test_the_stale_bid_windows_seed_at_the_real_price():
    """Book 39 read 195.86 / 195.87 on ticks 99-107 between stale 330.12 bids."""
    streak, seeded = 0, None
    readings = [(330.12, 184.5)] * 5 + [(195.86, 195.87)] * 9 + [(330.12, 195.87)] * 5
    for tick, (bid, ask) in enumerate(readings, start=94):
        step = pending_seed_step(streak=streak, touch=valid_touch(_levels(bid), _levels(ask)),
                                 venue_net=-0.7139, local_net=0.0, eps=5e-5)
        streak = step.streak
        if step.action == SEED_NOW:
            seeded = (tick, step.mid)
            break
    assert seeded is not None and seeded[0] == 101 and abs(seeded[1] - 195.865) < 1e-9


def test_missing_or_hostile_quotes_are_not_prices():
    for bids, asks in (([], _levels(1.0)), (_levels(1.0), []), (None, _levels(1.0)),
                       (_levels(float("nan")), _levels(1.0)), (_levels(0.0), _levels(1.0)),
                       (_levels(-1.0), _levels(1.0)), ([object()], _levels(1.0))):
        assert valid_touch(bids, asks) is None


# ---- books the seed could not price -------------------------------------------------

def test_a_real_lot_without_a_price_waits_and_dust_needs_no_price():
    route = route_unpriced_books(
        books=(99, 7), venue_net_by_book={99: -0.6523999999999963, 7: 0.0003},
        min_order=0.25, volume_decimals=4, remaining_books=160, remaining_abs=24.0,
    )
    assert route.pending == {99: -0.6524} and route.ledger == {7: 0.0003}
    assert route.snapped == 1
    json.dumps(route.as_log())


def test_with_the_ledger_off_everything_waits_as_in_a195():
    route = route_unpriced_books(
        books=(99, 7), venue_net_by_book={99: -0.6524, 7: 0.0003}, min_order=0.25,
        volume_decimals=4, remaining_books=160, remaining_abs=24.0, ledger_enabled=False,
    )
    assert route.pending == {99: -0.6524, 7: 0.0003} and route.ledger == {}


def test_the_seeding_bounds_still_apply_biggest_first():
    route = route_unpriced_books(
        books=(1, 2, 3), venue_net_by_book={1: 0.3, 2: -0.9, 3: 0.5}, min_order=0.25,
        volume_decimals=4, remaining_books=2, remaining_abs=24.0,
    )
    assert set(route.pending) == {2, 3} and route.over_bound == (1,)
    route = route_unpriced_books(
        books=(1, 2), venue_net_by_book={1: 0.3, 2: -0.9}, min_order=0.25,
        volume_decimals=4, remaining_books=10, remaining_abs=1.0,
    )
    assert route.pending == {2: -0.9} and route.over_bound == (1,)


def test_a_deferred_seed_needs_consecutive_valid_readings():
    assert A1961_SEED_VALID_STREAK == 3
    good = (411.30, 411.40)
    streak = 0
    actions = []
    for touch in (None, good, good, None, good, good, good):
        step = pending_seed_step(streak=streak, touch=touch, venue_net=-0.6524, local_net=0.0, eps=5e-5)
        streak = step.streak
        actions.append(step.action)
    assert actions == [SEED_WAIT] * 6 + [SEED_NOW]
    assert abs(step.mid - 411.35) < 1e-9


def test_a_deferred_seed_never_double_counts_or_invents_a_position():
    held = pending_seed_step(streak=2, touch=(1.0, 1.1), venue_net=-0.6524, local_net=0.25, eps=5e-5)
    assert held.action == SEED_WAIT and held.streak == 0 and held.reason == "TRACKER_HOLDS_BOOK"
    assert pending_seed_step(streak=2, touch=(1.0, 1.1), venue_net=0.0, local_net=0.0, eps=5e-5).action == SEED_DROP
    blind = pending_seed_step(streak=2, touch=(1.0, 1.1), venue_net=None, local_net=0.0, eps=5e-5)
    assert blind.action == SEED_WAIT and blind.streak == 0


def test_waiting_lots_are_covered_by_the_inherited_allowance_inside_its_cap():
    covered = inherited_parked_exemption(
        inherited_real={}, net_by_book={}, parked_books=[], cap_abs=1.0, eps=5e-5, extra_abs=0.6524,
    )
    assert covered.exempt_abs == 0.6524 and not covered.capped
    both = inherited_parked_exemption(
        inherited_real={39: -0.7139}, net_by_book={39: -0.7139}, parked_books=[39],
        cap_abs=1.0, eps=5e-5, extra_abs=0.6524,
    )
    assert both.exempt_abs == 1.0 and both.capped and abs(both.uncapped_abs - 1.3663) < 1e-12


# ---- BASE-denominated fees ------------------------------------------------------------

def test_the_live_fee_paying_buys_each_cost_one_base_unit():
    """Books 43, 17 and 41, tick <= 30: each diverged by exactly -0.0001."""
    for fee, price in ((0.0243304559, 329.99), (0.0135633598, 335.74), (0.0218328239, 288.32)):
        assert base_fee_units(agent_buy=True, fee=fee, price=price, base_decimals=4) == 1


def test_the_round_up_is_exact_at_the_boundary_and_scales_with_the_fee():
    assert base_fee_units(agent_buy=True, fee=0.02, price=200.0, base_decimals=4) == 1
    assert base_fee_units(agent_buy=True, fee=0.0201, price=200.0, base_decimals=4) == 2
    assert base_fee_units(agent_buy=True, fee=0.1039, price=221.78, base_decimals=4) == 5


def test_sells_rebates_and_unknown_precision_take_nothing_in_base():
    assert base_fee_units(agent_buy=False, fee=0.1, price=1.0, base_decimals=4) == 0
    assert base_fee_units(agent_buy=True, fee=-0.0327718127, price=237.62, base_decimals=4) == 0
    assert base_fee_units(agent_buy=True, fee=0.1, price=1.0, base_decimals=None) == 0
    assert base_fee_units(agent_buy=True, fee=0.1, price=0.0, base_decimals=4) == 0


def test_the_residue_ledger_accrues_exactly_and_forgets_zero():
    ledger = {43: -0.0032}
    apply_fee_residue(ledger, book_id=43, units=1, base_decimals=4)
    assert ledger == {43: -0.0033}
    ledger = {9: 0.0001}
    apply_fee_residue(ledger, book_id=9, units=1, base_decimals=4)
    assert ledger == {}
    ledger = {}
    for _ in range(216):
        apply_fee_residue(ledger, book_id=1, units=1, base_decimals=4)
    assert ledger == {1: -0.0216}


# ---- taker exits against their own decision ----------------------------------------------

def test_book_41_was_a_late_trigger_not_a_slippage_breach():
    out = taker_outcome(book=41, tick=20, realized_net_bps=-52.826,
                        decision={"tick": 20, "taker_net_bps": -54.1906, "reason": "ABSOLUTE_PROTECTION_REDUCE"})
    assert out.late_trigger and not out.slippage_breach
    assert abs(out.slippage_bps - 1.3646) < 1e-9
    assert out.as_log()["matched"] == 1
    json.dumps(out.as_log())


def test_book_18_gap_move_is_late_not_unbounded():
    out = taker_outcome(book=18, tick=305, realized_net_bps=-197.017,
                        decision={"tick": 305, "taker_net_bps": -199.4695, "reason": "ABSOLUTE_PROTECTION_REDUCE"})
    assert out.late_trigger and not out.slippage_breach and out.slippage_bps > 0


def test_an_exit_that_slips_past_the_bound_is_a_breach():
    out = taker_outcome(book=1, tick=20, realized_net_bps=-50.0, decision={"tick": 19, "taker_net_bps": -20.0})
    assert out.slippage_breach and not out.late_trigger


def test_a_stale_or_missing_decision_is_unmatched_not_guessed():
    stale = taker_outcome(book=1, tick=30, realized_net_bps=-50.0, decision={"tick": 19, "taker_net_bps": -20.0})
    assert stale.decision_net_bps is None and not stale.slippage_breach and not stale.late_trigger
    assert taker_outcome(book=1, tick=30, realized_net_bps=-50.0).as_log()["matched"] == 0


# ---- the real methods, run -------------------------------------------------------------------

def _harness():
    wanted = {
        "_a195_inventory_truth_enabled", "_a195_mid_by_book", "_a195_venue_net_by_book",
        "_a195_seed_inventory_from_venue", "_a195_local_base_by_book", "_a195_legacy_ceiling_bonus",
        "_a196_ledger_enabled", "_a196_inherited_parked_enabled", "_a196_grid_snap_enabled",
        "_a196_volume_decimals", "_a196_ledger_abs", "_a196_inherited_parked_report",
        "_a196_inherited_parked_exempt", "_a1961_seed_quote_guard_enabled", "_a1961_fee_residue_enabled",
        "_a1961_pending_abs", "_a1961_fee_residue_abs", "_a1961_note_base_decimals",
        "_a1961_note_fee_residue", "_a1961_service_pending_seed", "_a1961_note_taker_decision",
        "_a1961_emit_taker_outcome",
    }
    ns = {"Any": typing.Any, "collections": collections, "math": math,
          "PositionTracker": collections.namedtuple("PositionTracker", "net_qty vwap opened_at long_qty short_qty")}
    for node in ast.parse(SIMPLE).body:
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("research_"):
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<imports>", "exec"), ns)
    methods = {}
    for name in wanted:
        methods[name] = "    " + _class_defs(SIMPLE, "Strategy1_Research_Simple", name)[-1]
    methods["_execution_flat_epsilon"] = "    " + _class_defs(RESEARCH, None, "_execution_flat_epsilon")[-1]
    methods["_position_tracker_snapshot"] = "    " + _class_defs(BASE, None, "_position_tracker_snapshot")[0]
    exec(compile("class Harness:\n" + "\n\n".join(methods.values()) + "\n", "<harness>", "exec"), ns)
    agent = ns["Harness"]()
    agent.events = []
    agent._emit = lambda typ, **kw: agent.events.append((typ, kw))
    agent._open_positions = collections.defaultdict(lambda: {"longs": collections.deque(), "shorts": collections.deque()})
    agent._research_volume_decimals = 8
    agent._research_exchange_min_order_size = 0.25
    agent._tick = 1
    agent._a195_seed_done = False
    agent._a196_inherited_retired = []
    agent._a196_inherited_exempt_max = 0.0
    agent.uid = 67
    for flag in ("research_a195_inventory_truth_enabled", "research_a196_legacy_dust_ledger",
                 "research_a196_inherited_parked_allowance", "research_a196_quantity_grid_snap",
                 "research_a195_dust_capacity_class", "research_a1961_seed_quote_guard",
                 "research_a1961_fee_residue_ledger"):
        setattr(agent, flag, True)
    agent.research_a195_max_seed_books = 160
    agent.research_a195_max_seed_abs_base = 24.0
    agent.research_a196_inherited_parked_max_fraction = 0.5
    return agent


class _Balance:
    def __init__(self, total, initial):
        self.total, self.initial, self.free, self.reserved = total, initial, None, None


class _Account:
    def __init__(self, net):
        self.base_balance = _Balance(80.5658 + net, 80.5658)


class _Book:
    def __init__(self, bid, ask):
        self.bids, self.asks = _levels(bid), _levels(ask)


def _state(books):
    return types.SimpleNamespace(books=books, config=types.SimpleNamespace(volumeDecimals=4, baseDecimals=4))


def test_the_seed_defers_a_crossed_book_and_prices_it_only_once_it_is_a_market():
    agent = _harness()
    venue = {99: -0.6524, 36: 0.35, 7: 0.0003, 8: -0.0021}
    agent.accounts = {b: _Account(n) for b, n in venue.items()}
    books = {99: _Book(411.37, 285.28), 36: _Book(219.06, 220.11), 7: _Book(330.10, 183.73), 8: _Book(10.0, 10.1)}
    agent._a195_seed_inventory_from_venue(_state(books))

    tracker = sorted(b for b, p in agent._open_positions.items() if p["longs"] or p["shorts"])
    assert tracker == [36]                                  # the priced REAL lot only
    assert agent._a196_legacy_dust_ledger == {7: 0.0003, 8: -0.0021}   # crossed dust still joins the ledger
    assert set(agent._a1961_pending_seed) == {99}
    assert abs(agent._a195_seed_legacy_ceiling_bonus - 0.0024) < 1e-12
    seed_row = [kw for typ, kw in agent.events if typ == "A195_INVENTORY_SEED"][0]
    assert seed_row["a1961_pending_books"] == [99]

    # Waiting is still exposure, covered by F10, and reconcile agrees everywhere.
    assert abs(agent._a1961_pending_abs() - 0.6524) < 1e-12
    assert abs(agent._a196_inherited_parked_exempt(max_abs=2.0) - 0.6524) < 1e-12
    local = agent._a195_local_base_by_book(books)
    seen = agent._a195_venue_net_by_book(books)
    assert max(abs(local[b] - seen[b]) for b in venue) < 1e-9

    # Still crossed: nothing happens.  Then a real market for three ticks.
    agent._a1961_service_pending_seed(_state(books))
    assert 99 in agent._a1961_pending_seed and agent._a1961_pending_seed[99]["streak"] == 0
    books[99] = _Book(411.30, 411.40)
    for tick in (2, 3):
        agent._tick = tick
        agent._a1961_service_pending_seed(_state(books))
        assert 99 in agent._a1961_pending_seed
    agent._tick = 4
    assert agent._a1961_service_pending_seed(_state(books)) == 1
    assert not agent._a1961_pending_seed
    lot = list(agent._open_positions[99]["shorts"])[0]
    assert lot[1] == 0.6524 and abs(lot[2] - 411.35) < 1e-9          # priced at the real mid
    assert agent._a196_inherited_real[99] == -0.6524
    resolved = [kw for typ, kw in agent.events if typ == "A1961_PENDING_SEED"][0]
    assert resolved["routed"] == "TRACKER" and resolved["waited_ticks"] == 3
    local = agent._a195_local_base_by_book(books)
    assert abs(local[99] - seen[99]) < 1e-9                              # no double count


class _Trade:
    def __init__(self, *, book, side, price, maker=None, taker=None, maker_fee=0.0, taker_fee=0.0):
        self.bookId, self.side, self.price = book, side, price
        self.makerAgentId, self.takerAgentId = maker, taker
        self.makerFee, self.takerFee = maker_fee, taker_fee


def test_fee_paying_buys_are_mirrored_and_nothing_else_is():
    agent = _harness()
    # Before the BASE precision is known, never guess.
    assert agent._a1961_note_fee_residue(_Trade(book=43, side=1, price=329.99, maker=67, maker_fee=0.0243304559)) == 0
    assert agent._a1961_fee_residue_skipped == 1
    agent._a1961_note_base_decimals(_state({}))
    # Resting buy (aggressor sold): pays in BASE.
    assert agent._a1961_note_fee_residue(_Trade(book=43, side=1, price=329.99, maker=67, maker_fee=0.0243304559)) == 1
    # Aggressing buy: pays in BASE.
    assert agent._a1961_note_fee_residue(_Trade(book=5, side=0, price=221.78, taker=67, taker_fee=0.1039)) == 5
    # Our sells, and a rebate buy, take nothing in BASE.
    assert agent._a1961_note_fee_residue(_Trade(book=43, side=0, price=329.99, maker=67, maker_fee=0.03)) == 0
    assert agent._a1961_note_fee_residue(_Trade(book=4, side=1, price=237.62, maker=67, maker_fee=-0.0327718127)) == 0
    assert agent._a1961_fee_residue == {43: -0.0001, 5: -0.0005}
    assert abs(agent._a196_ledger_abs() - 0.0006) < 1e-12
    agent.accounts = {43: _Account(-0.0001)}
    local = agent._a195_local_base_by_book({43: None})
    assert abs(local[43] - (-0.0001)) < 1e-12


def test_taker_outcomes_are_matched_to_their_own_decision():
    agent = _harness()
    agent._tick = 20
    agent._a1961_note_taker_decision(book_id=41, tick=20, taker_net_bps=-54.1906, reason="ABSOLUTE_PROTECTION_REDUCE")
    agent._a1961_emit_taker_outcome(book_id=41, net_bps=-52.826)
    agent._a1961_emit_taker_outcome(book_id=17, net_bps=-37.3)
    rows = [kw for typ, kw in agent.events if typ == "A1961_TAKER_OUTCOME"]
    assert rows[0]["matched"] == 1 and rows[0]["late_trigger"] == 1 and rows[0]["slippage_breach"] == 0
    assert rows[1]["matched"] == 0
    # Counters are created on first increment; the harness never runs __init__.
    assert agent._a1961_taker_outcomes == 2 and agent._a1961_taker_unmatched == 1
    assert agent._a1961_late_triggers == 1
    assert getattr(agent, "_a1961_slippage_breaches", 0) == 0


# ---- wiring ---------------------------------------------------------------------------------------

def test_the_version_names_a1961():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_9"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1_9_9"' in SIMPLE


def test_switches_exist_and_default_on():
    for flag in ("research_a1961_seed_quote_guard", "research_a1961_fee_residue_ledger"):
        assert f'"{flag}", True' in SIMPLE, flag


def test_the_seed_mid_comes_from_a_valid_touch():
    body = _method_source("_a195_mid_by_book")
    assert "touch_mid(" in body
    assert body.index("_a1961_seed_quote_guard_enabled()") < body.index("touch_mid(")


def test_the_seed_routes_books_it_could_not_price():
    body = _method_source("_a195_seed_inventory_from_venue")
    assert "route_unpriced_books(" in body and "plan.skipped_no_price" in body
    assert "self._a196_legacy_dust_ledger = dict(split.ledger)" in body
    assert "self._a1961_pending_seed = {" in body
    assert "_net_inventory(" not in body


def test_respond_learns_base_precision_first_and_seeds_pending_lots_before_the_chain():
    body = _method_source("respond")
    chain = body.index("super().respond(state)")
    assert body.index("self._a1961_note_base_decimals(state)") < chain
    assert body.index("_a195_seed_inventory_from_venue") < body.index("self._a1961_service_pending_seed(state)") < chain


def test_the_pending_service_reads_a_valid_touch_and_never_ages_positions():
    body = _method_source("_a1961_service_pending_seed")
    assert "valid_touch(" in body and "pending_seed_step(" in body
    assert "_net_inventory(" not in body


def test_pending_lots_are_charged_on_both_gates_and_covered_by_f10():
    admission = _method_source("build_mm_strategy_instructions")
    charge = admission.index("abs_now += a1961_pending_abs_now")
    assert charge < admission.index("portfolio_slots = direct_liveness_admission_slots(")
    dead, live = _class_defs(SIMPLE, "Strategy1_Research_Simple", "_research_final_validate_instructions")
    assert "filled_abs += a1961_pending_abs_now" in live and "_a1961_" not in dead
    assert live.index("filled_abs += a1961_pending_abs_now") < live.index("shadow_abs = filled_abs + reserved_abs")
    assert "extra_abs=pending_abs" in _method_source("_a196_inherited_parked_report")


def test_the_fee_residue_is_charged_once_per_trade_after_the_replay_guard():
    body = _method_source("onTrade")
    hook = body.index("self._a1961_note_fee_residue(event)")
    assert body.index("if duplicate:") < hook < body.index("super().onTrade(event, validator)")


def test_reconcile_and_exposure_see_the_fee_residue_and_the_waiting_lots():
    local = _method_source("_a195_local_base_by_book")
    assert "_a1961_fee_residue" in local and "_a1961_pending_seed" in local
    assert "self._a1961_fee_residue_abs()" in _method_source("_a196_ledger_abs")


def test_taker_decisions_are_cached_and_outcomes_emitted_at_the_round_trip():
    unified = _method_source("_research_apply_unified_exit")
    assert "== ACTION_TAKER_EXIT" in unified and "self._a1961_note_taker_decision(" in unified
    fill = _method_source("_research_on_own_fill")
    assert fill.index('"DIRECT_MAKER_LIFECYCLE"') < fill.index("self._a1961_emit_taker_outcome(")
    assert '"A1961_TAKER_OUTCOME"' in _method_source("_a1961_emit_taker_outcome")


def test_the_admission_row_carries_the_active_slot_counterfactual():
    body = _method_source("_a196_emit_admission")
    for key in ("parked_inherited_active_books", "active_slots_if_parked_excused",
                "portfolio_slots_if_parked_excused", "pending_seed_abs", "fee_residue_abs"):
        assert key in body, key


def test_summary_stats_are_exported():
    for key in ("direct_a1961_pending_seed_books", "direct_a1961_pending_resolved",
                "direct_a1961_fee_residue_units", "direct_a1961_fee_residue_skipped",
                "direct_a1961_taker_outcomes", "direct_a1961_slippage_breaches",
                "direct_a1961_late_triggers", "direct_a1961_worst_slippage_bps"):
        assert key in SIMPLE, key


def test_module_records_the_measured_evidence():
    for text in ("411.37", "285.28", "256.925", "12 of 12", "62 of the 89", "roundUp"):
        assert text in MODULE, text
    assert A1961_VENUE_INTEGRITY_VERSION.endswith("a1_9_6_1")


# ---- launcher ----------------------------------------------------------------------------------------

def test_launcher_recognises_a1961_and_keeps_every_earlier_arm():
    assert "strategy1_direct_v4_16_2_a1_9_6_1) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1" in LAUNCHER
    assert "strategy1_direct_v4_16_2_a1_9_6) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1" in LAUNCHER
    assert "strategy1_direct_v4_16_2_a1_9_5) A19X_BUILD=1; A195_BUILD=1" in LAUNCHER


def test_launcher_ships_the_a1961_switches_in_the_params_value():
    params = re.search(r'^PARAMS="(.*?)"$', LAUNCHER, flags=re.S | re.M).group(1)
    for flag in ("research_a1961_seed_quote_guard=1", "research_a1961_fee_residue_ledger=1"):
        assert flag in params, flag


def test_launcher_pins_the_a1961_invariants_and_preflights_this_suite():
    for text in ("research_direct_venue_integrity.py",
                 'mid = touch_mid(getattr(book, "bids", None), getattr(book, "asks", None))',
                 "self._a1961_service_pending_seed(state)",
                 "abs_now \\+= a1961_pending_abs_now$",
                 "filled_abs \\+= a1961_pending_abs_now$",
                 "self._a1961_note_fee_residue(event)",
                 "rounding=ROUND_CEILING",
                 "tests/test_research_a1_9_6_1_venue_integrity.py",
                 "[preflight] A1.9.6.1 seed quote guard / fee residue / taker outcome PASS"):
        assert text in LAUNCHER, text


def test_base_precision_is_known_before_the_framework_ingests_trades():
    """handle() calls update() -- which runs onTrade -- before respond()."""
    body = _method_source("update")
    assert body.index("self._a1961_note_base_decimals(state)") < body.index("super().update(state)")
