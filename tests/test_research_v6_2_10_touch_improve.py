"""v6.2.10: quote one tick inside the OTHERS' best price -- first in the queue, never over ourselves.

A virtual two-sided maker replayed on four recordings (both nets) with an exact queue-position fill model:
joining the touch fills only on level-clearing sweeps (97% of entry fills, -9 bps at 60 states); one tick
inside raised the validator's making per unit volume +6-10% on mainnet and +25-32% on testnet for entries,
+17-33% with exits too.  UID 82 v6.2.8 at tick 3,000 shows the same shape live: entries -7.9 bps at 60
states, own-touch exits +9.2.
"""
import ast
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v6210_touch_improve as ti  # noqa: E402
import research_v628_touch_exit as te  # noqa: E402
import research_realization as rr  # noqa: E402
import research_v62_breadth as br  # noqa: E402
from research_direct_exit_ledger import DirectExitLedger  # noqa: E402
from _harness import extractor  # noqa: E402
import test_research_v6_2_0_breadth as b0  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
TICK = 0.01


class Order:
    def __init__(self, oid, q, cid=None):
        self.id, self.quantity, self.client_id = oid, q, cid


class Level:
    def __init__(self, price, orders=None, qty=None):
        self.price = price
        self.orders = orders
        self.quantity = qty if qty is not None else sum(o.quantity for o in (orders or [])) or 2.5


class Book:
    def __init__(self, bids, asks, events=None):
        self.bids, self.asks, self.events = bids, asks, events or []


def _book(bid=100.00, ask=100.10, **kw):
    return Book([Level(bid, [Order(1, 2.5)])], [Level(ask, [Order(2, 2.5)])], **kw)


# ---- the others' best price ------------------------------------------------------------------------

def test_our_own_order_alone_at_the_touch_is_skipped():
    levels = [Level(100.01, [Order(555, 0.25)]), Level(100.00, [Order(7, 3.0)])]
    assert ti.others_best(levels, own_ids={555}, own_cids=set()) == (100.00, False)


def test_a_level_we_share_is_still_the_others_best():
    levels = [Level(100.01, [Order(555, 0.25), Order(8, 1.0)])]
    assert ti.others_best(levels, own_ids={555}, own_cids=set()) == (100.01, True)


def test_our_entry_client_id_is_recognised_too():
    buy_cid, _ = br.entry_client_ids(48)
    levels = [Level(100.01, [Order(9, 0.25, cid=buy_cid)]), Level(100.00, [Order(7, 3.0)])]
    assert ti.others_best(levels, own_ids=set(), own_cids={buy_cid}) == (100.00, False)


def test_a_level_without_an_order_list_counts_as_the_others():
    assert ti.others_best([Level(100.01, None, qty=1.0)], own_ids={1}, own_cids=set()) == (100.01, False)


def test_raw_dict_levels_are_read_the_same_way():
    levels = [{"p": 100.01, "q": 0.25, "o": [{"i": 555, "q": 0.25}]}, {"p": 100.00, "q": 3.0, "o": [{"i": 7, "q": 3.0}]}]
    assert ti.others_best(levels, own_ids={555}, own_cids=set()) == (100.00, False)


# ---- the improvement rules -------------------------------------------------------------------------

def test_one_tick_inside_on_a_wide_spread():
    assert ti.improved_bid(others_bid=100.00, others_ask=100.10, raw_ask=100.10, tick=TICK) == pytest.approx(100.01)
    assert ti.improved_ask(others_bid=100.00, others_ask=100.10, raw_bid=100.00, tick=TICK) == pytest.approx(100.09)


def test_never_crossing_an_ask_ours_included():
    # our own ask sits one tick above the others' bid: an improved bid would meet it
    assert ti.improved_bid(others_bid=100.00, others_ask=100.10, raw_ask=100.01, tick=TICK) is None


def test_our_side_of_the_mid_so_a_narrow_spread_is_joined():
    assert ti.improved_bid(others_bid=100.00, others_ask=100.02, raw_ask=100.02, tick=TICK) is None   # 2 ticks
    assert ti.improved_ask(others_bid=100.00, others_ask=100.02, raw_bid=100.00, tick=TICK) is None
    assert ti.improved_bid(others_bid=100.00, others_ask=100.03, raw_ask=100.03, tick=TICK) == pytest.approx(100.01)


def test_a_one_tick_war_is_bounded_only_two_ticks_short_of_the_far_side():
    """Disclosed limit: the mid guard stops each step at the OTHERS' mid, and in a war their mid moves with them.

    If another maker answers every improvement with its own, bids climb to two ticks under the ask.  The recorded
    books show no such wars (89% of mainnet touches are 10+ ticks wide); the outbid counter watches for one.
    """
    bid, ask = 100.00, 100.10
    seen = []
    for _ in range(20):
        cand = ti.improved_bid(others_bid=bid, others_ask=ask, raw_ask=ask, tick=TICK)
        if cand is None:
            break
        assert cand < 0.5 * (bid + ask)          # every single step stays on our side of the others' mid
        seen.append(cand)
        bid = cand            # the other maker then matches us
    assert max(seen) == pytest.approx(ask - 2 * TICK)


def test_an_outbid_improved_quote_is_counted():
    agent = _entry_agent(DirectExitLedger())
    agent._v62_place_touch_quotes(b0._Response(), b0._state(), 48,
                                  Book([Level(445.09, [Order(1, 2.5)])], [Level(445.20, [Order(2, 2.5)])]), b0.LOT)
    # someone now shows 445.11 > our 445.10 and 445.18 < our 445.19
    agent._v62_place_touch_quotes(b0._Response(), b0._state(), 48,
                                  Book([Level(445.11, [Order(3, 1.0)])], [Level(445.18, [Order(4, 1.0)])]), b0.LOT)
    assert agent._v6210_counts.get("entry_bid_outbid") == 1 and agent._v6210_counts.get("entry_ask_outbid") == 1


def test_entry_prices_both_sides_and_one_side():
    view = ti.touch_view(_book(), own_ids=set(), own_cids=set())
    assert ti.entry_prices(view, tick=TICK, quote_buy=True, quote_sell=True) == (pytest.approx(100.01), pytest.approx(100.09))
    assert ti.entry_prices(view, tick=TICK, quote_buy=True, quote_sell=False) == (pytest.approx(100.01), None)
    assert ti.entry_prices(None, tick=TICK, quote_buy=True, quote_sell=True) == (None, None)


def test_a_crossed_or_empty_book_has_no_view():
    assert ti.touch_view(_book(100.10, 100.00), own_ids=set(), own_cids=set()) is None
    assert ti.touch_view(Book([], [Level(100.1)]), own_ids=set(), own_cids=set()) is None


# ---- no walking the price --------------------------------------------------------------------------

def test_a_requote_over_our_own_resting_order_keeps_its_price():
    """Our improved bid (id 555) still sits alone one tick above the others: the re-quote goes to the same price."""
    book = Book([Level(100.01, [Order(555, 0.25)]), Level(100.00, [Order(7, 3.0)])], [Level(100.10, [Order(2, 2.5)])])
    view = ti.touch_view(book, own_ids={555}, own_cids=set())
    assert (view.raw_bid, view.others_bid, view.own_at_bid) == (100.01, 100.00, False)
    bid, _ = ti.entry_prices(view, tick=TICK, quote_buy=True, quote_sell=False)
    assert bid == pytest.approx(100.01)
    blind = ti.touch_view(book, own_ids=set(), own_cids=set())            # without the own-order skip
    assert ti.entry_prices(blind, tick=TICK, quote_buy=True, quote_sell=False)[0] == pytest.approx(100.02)


def test_many_requotes_on_a_quiet_book_do_not_move():
    book = Book([Level(100.00, [Order(7, 3.0)])], [Level(100.10, [Order(2, 2.5)])])
    own, price = set(), None
    for oid in range(1000, 1010):
        view = ti.touch_view(book, own_ids=own, own_cids=set())
        price, _ = ti.entry_prices(view, tick=TICK, quote_buy=True, quote_sell=False)
        # the new order is accepted and rests while the old one has not been reported gone yet
        own.add(oid)
        book.bids = [Level(price, [Order(o, 0.25) for o in sorted(own)])] + [Level(100.00, [Order(7, 3.0)])]
    assert price == pytest.approx(100.01)


# ---- exits: through the v6.2.8 price scope, with the real frozen pricer ----------------------------

def test_only_an_own_touch_exit_price_is_moved():
    view = ti.touch_view(_book(), own_ids=set(), own_cids=set())
    long_touch = rr.maker_exit_price(bid=100.0, ask=100.1, long_position=True, action=te.ACTION_COMPETITIVE, tick_size=TICK)
    assert ti.improved_exit_price(long_touch, bid=100.0, ask=100.1, long_position=True, tick=TICK, view=view) == pytest.approx(100.09)
    short_touch = rr.maker_exit_price(bid=100.0, ask=100.1, long_position=False, action=te.ACTION_COMPETITIVE, tick_size=TICK)
    assert ti.improved_exit_price(short_touch, bid=100.0, ask=100.1, long_position=False, tick=TICK, view=view) == pytest.approx(100.01)
    passive = rr.maker_exit_price(bid=100.0, ask=100.1, long_position=True, action=te.ACTION_PASSIVE, tick_size=TICK)
    assert ti.improved_exit_price(passive, bid=100.0, ask=100.1, long_position=True, tick=TICK, view=view) is None


def test_an_exit_is_left_alone_when_the_view_describes_another_touch():
    view = ti.touch_view(_book(), own_ids=set(), own_cids=set())
    assert ti.improved_exit_price(100.2, bid=100.1, ask=100.2, long_position=True, tick=TICK, view=view) is None


class _Agent:
    def __init__(self, *, cap=True, improve=True, ledger=None, tick=5):
        self.research_v628_rung_cap = cap
        self.research_v6210_improve_exits = improve
        self._v628_counts, self._v628_errors = {}, 0
        self._v6210_counts, self._v6210_errors = {}, 0
        self._v6210_exit_book = None
        self._tick = tick
        self._ledger = ledger or DirectExitLedger()

    def _v62_on(self):
        return True

    def _a19_ledger_ref(self):
        return self._ledger


def _scoped_agent(**kwargs):
    frozen = types.SimpleNamespace(maker_exit_price=rr.maker_exit_price)
    ns = {
        "contextmanager": contextmanager,
        "importlib": types.SimpleNamespace(import_module=lambda name: frozen),
        "v628_capped_price_fn": te.capped_price_fn, "V628_CAPPED_MARKER": te.CAPPED_MARKER,
        "v6210_improved_price_fn": ti.improved_price_fn, "v6210_touch_view": ti.touch_view,
        "v62_entry_client_ids": br.entry_client_ids,
    }
    cls = type("A", (_Agent,), {})
    for name in ("_v628_rung_cap_on", "_v628_count", "_v628_rung_cap_scope", "_v6210_count", "_v6210_view",
                 "_v6210_exit_view"):
        fn = ast.parse(_simple(name)).body[0]
        fn.decorator_list = [ast.Name(id="contextmanager", ctx=ast.Load())] if name == "_v628_rung_cap_scope" else []
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), "<v6210>", "exec"), ns)
        setattr(cls, name, ns[name])
    return cls(**kwargs), frozen


def test_inside_the_scope_an_own_touch_exit_goes_one_tick_inside():
    agent, frozen = _scoped_agent()
    agent._v6210_exit_book = (48, _book(), 5)
    with agent._v628_rung_cap_scope():
        price = frozen.maker_exit_price(bid=100.0, ask=100.1, long_position=True, action=te.ACTION_COMPETITIVE, tick_size=TICK)
        aggressive = frozen.maker_exit_price(bid=100.0, ask=100.1, long_position=True, action=te.ACTION_AGGRESSIVE, tick_size=TICK)
        passive = frozen.maker_exit_price(bid=100.0, ask=100.1, long_position=True, action=te.ACTION_PASSIVE, tick_size=TICK)
        assert getattr(frozen.maker_exit_price, te.CAPPED_MARKER)
    assert price == pytest.approx(100.09)
    assert aggressive == pytest.approx(100.09)        # S1 caps it to the own touch, P2 then improves it
    assert passive == pytest.approx(100.12)           # a deliberately deeper rung is left alone
    assert frozen.maker_exit_price is rr.maker_exit_price
    assert agent._v6210_counts.get("exit_improved") == 2 and agent._v628_counts.get("rung_capped") == 1


def test_our_own_exit_at_the_ask_is_not_improved_upon():
    ledger = DirectExitLedger()
    ledger.note_accepted(order_id=555, book_id=48, side=1, price=100.09, quantity=0.25, timestamp_ns=1, tick=4)
    agent, frozen = _scoped_agent(ledger=ledger)
    book = Book([Level(100.00, [Order(1, 2.5)])], [Level(100.09, [Order(555, 0.25)]), Level(100.10, [Order(2, 2.5)])])
    agent._v6210_exit_book = (48, book, 5)
    with agent._v628_rung_cap_scope():
        price = frozen.maker_exit_price(bid=100.0, ask=100.09, long_position=True, action=te.ACTION_COMPETITIVE, tick_size=TICK)
    assert price == pytest.approx(100.09)             # the same price: no walking down


def test_a_book_bound_on_another_tick_is_not_used():
    agent, frozen = _scoped_agent(tick=6)
    agent._v6210_exit_book = (48, _book(), 5)
    with agent._v628_rung_cap_scope():
        price = frozen.maker_exit_price(bid=100.0, ask=100.1, long_position=True, action=te.ACTION_COMPETITIVE, tick_size=TICK)
    assert price == pytest.approx(100.10) and agent._v6210_counts.get("exit_stale_book") == 1


def test_the_exit_switch_off_is_v6_2_8_exactly():
    agent, frozen = _scoped_agent(improve=False)
    agent._v6210_exit_book = (48, _book(), 5)
    with agent._v628_rung_cap_scope():
        assert frozen.maker_exit_price(bid=100.0, ask=100.1, long_position=True, action=te.ACTION_COMPETITIVE, tick_size=TICK) \
            == pytest.approx(100.10)
    assert agent._v6210_counts == {}


def test_improvement_alone_works_without_the_rung_cap():
    agent, frozen = _scoped_agent(cap=False)
    agent._v6210_exit_book = (48, _book(), 5)
    with agent._v628_rung_cap_scope():
        assert frozen.maker_exit_price(bid=100.0, ask=100.1, long_position=True, action=te.ACTION_COMPETITIVE, tick_size=TICK) \
            == pytest.approx(100.09)
    assert frozen.maker_exit_price is rr.maker_exit_price


# ---- entries: through the real _v62_place_touch_quotes -----------------------------------------------

def _entry_agent(ledger=None):
    agent = b0._agent()
    agent.research_v6210_improve_entries = True
    agent._a19_ledger_ref = lambda: ledger
    return agent


def _placed(r):
    return {i.direction: i.price for i in r.instructions}


def test_entry_quotes_go_one_tick_inside_the_others():
    agent = _entry_agent(DirectExitLedger())
    r = b0._Response()
    book = Book([Level(445.09, [Order(1, 2.5)])], [Level(445.20, [Order(2, 2.5)])])
    assert agent._v62_place_touch_quotes(r, b0._state(), 48, book, b0.LOT) == 2
    assert _placed(r) == {b0._OrderDirection.BUY: pytest.approx(445.10), b0._OrderDirection.SELL: pytest.approx(445.19)}
    assert agent._v6210_counts.get("entry_bid_improved") == 1 and agent._v6210_counts.get("entry_ask_improved") == 1


def test_entry_quotes_join_a_narrow_spread():
    agent = _entry_agent(DirectExitLedger())
    r = b0._Response()
    book = Book([Level(445.09, [Order(1, 2.5)])], [Level(445.11, [Order(2, 2.5)])])
    agent._v62_place_touch_quotes(r, b0._state(), 48, book, b0.LOT)
    assert _placed(r) == {b0._OrderDirection.BUY: pytest.approx(445.09), b0._OrderDirection.SELL: pytest.approx(445.11)}
    assert agent._v6210_counts.get("entry_bid_joined") == 1 and agent._v6210_counts.get("entry_ask_joined") == 1


def test_an_entry_requote_does_not_improve_on_our_own_quote():
    buy_cid, sell_cid = br.entry_client_ids(48)
    agent = _entry_agent(DirectExitLedger())
    r = b0._Response()
    book = Book([Level(445.10, [Order(9, 0.25, cid=buy_cid)]), Level(445.09, [Order(1, 2.5)])],
                [Level(445.19, [Order(10, 0.25, cid=sell_cid)]), Level(445.20, [Order(2, 2.5)])])
    agent._v62_place_touch_quotes(r, b0._state(), 48, book, b0.LOT)
    assert _placed(r) == {b0._OrderDirection.BUY: pytest.approx(445.10), b0._OrderDirection.SELL: pytest.approx(445.19)}


def test_the_entry_switch_off_joins_the_touch_as_in_v6_2_9():
    agent = _entry_agent(DirectExitLedger())
    agent.research_v6210_improve_entries = False
    r = b0._Response()
    agent._v62_place_touch_quotes(r, b0._state(), 48, Book([Level(445.09)], [Level(445.20)]), b0.LOT)
    assert _placed(r) == {b0._OrderDirection.BUY: pytest.approx(445.09), b0._OrderDirection.SELL: pytest.approx(445.20)}


# ---- wiring ----------------------------------------------------------------------------------------

def test_all_three_exit_producers_bind_their_book_before_pricing():
    for name, scope_line in (("_research_apply_unified_exit", "with self._v628_rung_cap_scope():"),
                             ("_research_place_maker_exit", "with self._v624_release_life(int(book_id), state):"),
                             ("_research_parked_touch_exit", "with self._v628_rung_cap_scope():")):
        src = _simple(name)
        bind = src.index("self._v6210_exit_book = (")
        assert bind < src.index(scope_line), name


def test_the_scope_caps_first_and_improves_second_under_the_v628_marker():
    scope = _simple("_v628_rung_cap_scope")
    assert scope.index("priced = v628_capped_price_fn(original") < scope.index("priced = v6210_improved_price_fn(")
    assert "marker=V628_CAPPED_MARKER" in scope and 'setattr(module, "maker_exit_price", original)' in scope


def test_the_own_order_skip_reads_the_ledger_and_the_entry_client_ids():
    view = _simple("_v6210_view")
    assert "ledger.live_orders(int(book_id))" in view and "v62_entry_client_ids(int(book_id))" in view


def test_both_switches_default_on_and_are_declared():
    for key in ("research_v6210_improve_entries", "research_v6210_improve_exits"):
        assert f'getattr(self.config, "{key}", True)' in SIMPLE
        assert f"{key}=1" in LAUNCHER


def test_the_state_row_reports_the_build():
    assert "touch_improve=self._v6210_snapshot()," in SIMPLE
    assert "improve_entries_on=" in SIMPLE and "improve_exits_on=" in SIMPLE


def test_the_preflight_guards_the_build():
    for needle in ("v6.2.10 module is not imported",
                   "v6.2.10 entry quotes are not priced inside the others' touch",
                   "v6.2.10 maker exits are not priced inside the others' touch",
                   "v6.2.10 does not bind the priced book in all three frozen exit producers",
                   "v6.2.10 does not skip our own orders when reading the touch",
                   "[preflight] v6.2.10 touch improve PASS"):
        assert needle in LAUNCHER
    assert "tests/test_research_v6_2_10_touch_improve.py" in LAUNCHER
    assert SIMPLE.count("self._v6210_exit_book = (") == 3


def test_the_version_and_arm():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_12"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_12"' in SIMPLE
    assert "strategy1_direct_v6_2_10)" in LAUNCHER and "V629_BUILD=1; V6210_BUILD=1 ;;" in LAUNCHER
    assert ti.V6210_TOUCH_IMPROVE_VERSION == "touch_improve_v6_2_10"
