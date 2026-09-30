"""v6.9.1 C2: a deep order is no larger than what the book's account can reserve for it -- its free base for a sell,
its free quote at the order's price for a buy.  The level keeps its identity (client id, slot, depth); only its size
shrinks, on the volume grid; a level the account cannot fund at the minimum order takes nothing and spends no
instruction, and the next level is tried.

Why (STRUCTURAL, OrderPlacementValidator.cpp / Balances.cpp of the simulator): a limit sell reserves its base at
placement and is refused outright (INSUFFICIENT_BASE) when the free base is short; a miner's base on a book is its
Pareto endowment wealth / ((1 + r) * price), r = (1 - u) ** -0.5 -- at most 83.3 base, median 69, under 12 base on
0.6% of books.  OBSERVED 09-30 (sim 20260929_2015, UIDs 94/251/165/104/88): seven books with 2-10 base of initial
base met the 2/2/4 sell ladder plus a short bound of 8 and were refused 25-1,671 times each, one-sided for 7-62 min.
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v633_deep_layer as dl  # noqa: E402
import research_v69_deep_ladder as d69  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_4_board as t64  # noqa: E402
import test_research_v6_9 as t69  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
LADDER = (STRATEGY / "research_v69_deep_ladder.py").read_text()
SWITCH = "research_v691_free_base"


def _agent(*, free_base=None, free_quote=1e9, on=True, **kw):
    agent = t69._agent(**kw)
    agent.research_v691_free_base = on
    if free_base is not None:
        agent.accounts = {3: types.SimpleNamespace(base_balance=types.SimpleNamespace(free=free_base),
                                                   quote_balance=types.SimpleNamespace(free=free_quote))}
    return agent


def _sells(resp):
    return {p for p in t63._placed(resp) if p[1] == "SELL"}


def _buys(resp):
    return {p for p in t63._placed(resp) if p[1] == "BUY"}


# ---- 1. the rule ----------------------------------------------------------------------------------------------------

def test_the_size_is_the_free_balance_floored_to_the_grid_or_nothing_under_the_minimum_order():
    within = d69.quantity_within_free
    assert within(4.0, 10.0, 0.25, 4) == 4.0                          # ample: the level's full size
    assert within(4.0, 4.0, 0.25, 4) == 4.0                           # exactly enough
    assert within(4.0, 3.99996, 0.25, 4) == 3.9999                    # floored to the grid, never rounded up
    assert within(4.0, 1.5, 0.25, 4) == 1.5
    assert within(2.0, 0.2, 0.25, 4) == 0.0                           # under the minimum order: nothing
    assert within(2.0, 0.0, 0.25, 4) == 0.0 and within(2.0, -1.0, 0.25, 4) == 0.0
    assert within(4.0, None, 0.25, 4) == 4.0 and within(4.0, "x", 0.25, 4) == 4.0   # an unreadable balance: unchanged
    assert within(0.0, 10.0, 0.25, 4) == 0.0 and within(None, 10.0, 0.25, 4) == 0.0
    assert within(4.0, 1.23456, 0.25, "x") == 1.2345                  # a bad grid is the venue's four decimals
    assert d69.V691_FREE_BASE_VERSION == "deep_ladder_free_base_v6_9_1"


# ---- 2. in the pass -------------------------------------------------------------------------------------------------

def test_with_ample_base_every_level_keeps_its_v69_size():
    agent = _agent(free_base=100.0)
    resp = t64._run(agent, {3: t63._book()})
    assert t63._placed(resp) == t69._level(0)
    assert agent._v69_counts == {}


def test_a_short_book_sells_what_its_account_can_reserve_and_keeps_the_levels_identity():
    agent = _agent(free_base=1.5)
    resp = t64._run(agent, {3: t63._book()})
    assert _buys(resp) == {p for p in t69._level(0) if p[1] == "BUY"}                  # the bid is v6.9's
    assert _sells(resp) == {(3, "SELL", 40032, t69._px(30.0, dl.SIDE_SELL), 1.5)}    # level 0's id, 1.5 base
    assert agent._v69_counts == {"sized_by_free_base_l0": 1}
    # the ladder's levels shrink the same way (level 2 is two clips, so the account's 3 base is what it gets)
    agent = _agent(free_base=3.0)
    for i, (side, level) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1), (0, 2))):
        t69._rest(agent, 11 + i, side, level)
    resp = t64._run(agent, {3: t63._book()})
    assert _sells(resp) == {(3, "SELL", 40036, t69._px(60.0, dl.SIDE_SELL), 3.0)} and _buys(resp) == set()
    assert agent._v69_counts == {"sized_by_free_base_l2": 1, "placed_l2": 1}


def test_a_book_with_no_free_base_rests_no_sell_spends_no_instruction_and_still_bids():
    agent = _agent(free_base=0.2)                                                       # under the 0.25 minimum
    resp = t64._run(agent, {3: t63._book()})
    assert _sells(resp) == set() and _buys(resp) == {p for p in t69._level(0) if p[1] == "BUY"}
    assert agent._v69_counts == {"no_free_base_l0": 1, "no_free_base_l1": 1, "no_free_base_l2": 1}
    assert agent._v633_counts.get("budget_deferred") is None
    # once the account is funded again (a bid filled, a sell cancelled) the sell is back at full size
    agent.accounts[3].base_balance.free = 8.0
    resp = t64._run(agent, {3: t63._book()})
    assert _sells(resp) == {p for p in t69._level(0) if p[1] == "SELL"}


def test_the_buy_leg_is_sized_by_the_free_quote_at_the_orders_price():
    px = t69._px(30.0, dl.SIDE_BUY)
    agent = _agent(free_base=100.0, free_quote=px * 1.25)
    resp = t64._run(agent, {3: t63._book()})
    assert _buys(resp) == {(3, "BUY", 40031, px, 1.25)} and agent._v69_counts == {"sized_by_free_quote_l0": 1}
    assert _sells(resp) == {p for p in t69._level(0) if p[1] == "SELL"}


def test_without_an_account_or_with_the_switch_off_the_size_is_v69s():
    agent = _agent()                                                                     # no accounts on the harness
    resp = t64._run(agent, {3: t63._book()})
    assert t63._placed(resp) == t69._level(0) and agent._v69_counts == {}
    agent = _agent(free_base=1.5, on=False)
    resp = t64._run(agent, {3: t63._book()})
    assert t63._placed(resp) == t69._level(0) and agent._v69_counts == {}
    agent = _agent(free_base=1.5)
    agent.research_v69_deep_ladder = False                                               # v6.8: level 0 only
    resp = t64._run(agent, {3: t63._book()})
    assert _sells(resp) == {(3, "SELL", 40032, t69._px(30.0, dl.SIDE_SELL), 1.5)}


def test_the_bound_test_stays_pre_fill_on_inventory_not_on_the_sized_order():
    agent = _agent(free_base=1.5, venue={3: -7.9})                                       # short, inside the bound
    resp = t64._run(agent, {3: t63._book()})
    assert _buys(resp) == {p for p in t69._level(0) if p[1] == "BUY"}                    # the reducing side, full size
    assert _sells(resp) == {(3, "SELL", 40032, t69._px(30.0, dl.SIDE_SELL), 1.5)}       # the adding side, sized
    agent = _agent(free_base=1.5, venue={3: -8.0})                                       # at the bound: no sell at all
    resp = t64._run(agent, {3: t63._book()})
    assert _sells(resp) == set()


# ---- 3. the wiring --------------------------------------------------------------------------------------------------

def test_the_switch_is_read_by_name_defaults_on_and_is_a_launcher_parameter():
    # v6.10 ships this ON: the plain launcher run is v6.10.  FREE_BASE=0 turns it back to v6.9.
    assert 'getattr(self.config, "research_v691_free_base", True)' in SIMPLE
    assert "%s=${FREE_BASE}" % SWITCH in LAUNCHER
    assert 'FREE_BASE="${FREE_BASE:-1}"' in LAUNCHER
    snap = t69._method("_v69_snapshot", V691_FREE_BASE_VERSION=d69.V691_FREE_BASE_VERSION)(
        types.SimpleNamespace(_v69_counts={"sized_by_free_base_l0": 2}, _v69_errors=0, research_v69_deep_ladder=True,
                              research_v69_deep_first_pace=True, research_v691_free_base=True, _v633_deep=None))
    assert snap["free_base_on"] == 1 and snap["free_base_version"] == d69.V691_FREE_BASE_VERSION
    assert snap["sized_by_free_base_l0"] == 2
    assert "from research_v69_deep_ladder import (" in SIMPLE and "quantity_within_free as v691_quantity_within_free" in SIMPLE
    assert "def quantity_within_free(" in LADDER
