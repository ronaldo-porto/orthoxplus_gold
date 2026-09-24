"""v6.2.14: rest only at the best price, add only where the book is deep, and let a filled exit free its book.

Mainnet UID 94 (v6.2.11.1, ticks 1-5,950 of its own log): the two-tick post-only cushion put 87-91% of maker exits
one tick BEHIND the own touch (fill 4.4%), the reject guard and the PASSIVE rung put more there, 68-75% of entries sat
on the thin side of the book, and every exit fill held its book to the local TTL.  Recorder replay of the rules as
the agent runs them: making 7.6 / 0.0 -> 58.3 / 16.5 (ticks 1-3,000 / 3,350-4,161), alpha -289 / -3.
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

import research_v6214_touch_life as tl  # noqa: E402
import research_v628_touch_exit as te  # noqa: E402
import research_v6210_touch_improve as ti  # noqa: E402
import research_realization as rr  # noqa: E402
import research_v62_breadth as br  # noqa: E402
import research_contract_guard as cg  # noqa: E402
import research_v625_cap_paced as cp  # noqa: E402
from research_direct_exit_ledger import DirectExitLedger  # noqa: E402
from research_direct_exit_refresh import (  # noqa: E402
    ABSENT_ENTRY_QUOTE_CANCEL, ABSENT_REPRICE_CANCEL, AGENT_CANCEL_DISPOSITIONS,
)
from _harness import extractor  # noqa: E402
import test_research_v6_2_0_breadth as b0  # noqa: E402
import test_research_v6_2_5_cap_paced as c5  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
TICK = 0.01
OK = cp.REASON_OK
NOW = 48_811_000_000_000


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


def trade(s, p=100.0, q=0.25):
    """A state trade event as the miner receives it: s=0 the taker bought, s=1 the taker sold."""
    return {"y": "t", "p": p, "q": q, "s": s, "Ma": 7, "Ta": 9}


# ---- 1. the rules ------------------------------------------------------------------------------------------

def test_the_one_tick_cushion_keeps_a_touch_price_the_two_tick_cushion_put_behind():
    san = cg.sanitize_post_only_limit_price
    kw = dict(best_bid=100.00, best_ask=100.01, tick_size=TICK, price_decimals=2)
    # one-tick spread: the frozen two-tick cushion is one tick BEHIND our own touch on both sides
    assert san(side="buy", original_price=100.00, safety_ticks=2, **kw) == pytest.approx(99.99)
    assert san(side="sell", original_price=100.01, safety_ticks=2, **kw) == pytest.approx(100.02)
    assert san(side="buy", original_price=100.00, safety_ticks=tl.TOUCH_GAP_TICKS, **kw) == pytest.approx(100.00)
    assert san(side="sell", original_price=100.01, safety_ticks=tl.TOUCH_GAP_TICKS, **kw) == pytest.approx(100.01)
    # a crossing price is still made legal, one increment from the opposite touch
    assert san(side="buy", original_price=100.01, safety_ticks=1, **kw) == pytest.approx(100.00)
    assert san(side="sell", original_price=100.00, safety_ticks=1, **kw) == pytest.approx(100.01)
    # two-tick spread: an improved sell one tick inside stays there
    kw2 = dict(best_bid=100.00, best_ask=100.02, tick_size=TICK, price_decimals=2)
    assert san(side="sell", original_price=100.01, safety_ticks=1, **kw2) == pytest.approx(100.01)
    assert san(side="sell", original_price=100.01, safety_ticks=2, **kw2) == pytest.approx(100.02)


def test_the_fresh_touch_guard_keeps_a_legal_price_where_the_frozen_guard_went_behind():
    kw = dict(best_bid=100.00, best_ask=100.01, tick_size=TICK, reject_streak=1)
    assert cg.guarded_post_only_price(side="sell", original_price=100.01, **kw) == pytest.approx(100.02)
    assert cg.guarded_post_only_price(side="buy", original_price=100.00, **kw) == pytest.approx(99.99)
    assert tl.fresh_touch_price(side="sell", original_price=100.01, **kw) == pytest.approx(100.01)
    assert tl.fresh_touch_price(side="buy", original_price=100.00, **kw) == pytest.approx(100.00)
    # a crossing original is moved to the nearest legal price, never past it
    assert tl.fresh_touch_price(side="sell", original_price=99.95, **kw) == pytest.approx(100.01)
    assert tl.fresh_touch_price(side="buy", original_price=100.30, **kw) == pytest.approx(100.00)
    # a price already further away is not pulled in: the guard only ever makes an order legal
    assert tl.fresh_touch_price(side="sell", original_price=100.05, **kw) == pytest.approx(100.05)
    for bad in (dict(best_bid=0.0), dict(best_ask=-1.0), dict(tick_size=0.0), dict(best_bid=100.02, best_ask=100.01)):
        assert tl.fresh_touch_price(side="sell", original_price=100.01, **{**kw, **bad}) is None
    assert tl.fresh_touch_price(side="?", original_price=100.01, **kw) is None


def test_the_passive_rung_is_priced_at_the_own_touch():
    assert tl.never_behind_action(te.ACTION_PASSIVE) == te.ACTION_COMPETITIVE
    for other in (te.ACTION_COMPETITIVE, te.ACTION_AGGRESSIVE, "TAKER_EXIT", None, "?"):
        assert tl.never_behind_action(other) == other
    calls = []
    priced = tl.never_behind_price_fn(rr.maker_exit_price, on_capped=lambda: calls.append(1), marker="_m")
    frozen_passive = rr.maker_exit_price(bid=100.0, ask=100.1, long_position=True, action=te.ACTION_PASSIVE, tick_size=TICK)
    assert frozen_passive == pytest.approx(100.12)                 # two ticks behind the ask
    assert priced(bid=100.0, ask=100.1, long_position=True, action=te.ACTION_PASSIVE, tick_size=TICK) == pytest.approx(100.10)
    assert priced(bid=100.0, ask=100.1, long_position=False, action=te.ACTION_PASSIVE, tick_size=TICK) == pytest.approx(100.00)
    assert priced(bid=100.0, ask=100.1, long_position=True, action=te.ACTION_COMPETITIVE, tick_size=TICK) == pytest.approx(100.10)
    assert calls == [1, 1]
    assert getattr(priced, tl.NEVER_BEHIND_MARKER) and getattr(priced, "_m") and priced.__wrapped__ is rr.maker_exit_price


def test_behind_the_touch_is_one_increment_worse_than_the_best_price_on_the_order_side():
    at = dict(best_bid=100.00, best_ask=100.01, tick=TICK)
    assert not tl.behind_touch(side=tl.SIDE_BUY, price=100.00, **at)
    assert tl.behind_touch(side=tl.SIDE_BUY, price=99.99, **at)
    assert not tl.behind_touch(side=tl.SIDE_SELL, price=100.01, **at)
    assert tl.behind_touch(side=tl.SIDE_SELL, price=100.02, **at)
    # a floating-point hair is not an increment
    assert not tl.behind_touch(side=tl.SIDE_BUY, price=100.00 - 1e-9, **at)
    assert not tl.behind_touch(side=tl.SIDE_BUY, price=float("nan"), **at)
    assert not tl.behind_touch(side=tl.SIDE_SELL, price=100.02, best_bid=100.0, best_ask=None, tick=TICK)
    assert not tl.behind_touch(side=tl.SIDE_BUY, price=99.99, best_bid=100.0, best_ask=100.01, tick=0.0)


def test_the_last_print_is_read_from_trade_events_only():
    order = {"y": "o", "p": 100.0, "q": 1.0, "s": 0}
    cancel = {"y": "c", "p": 100.0, "q": 1.0, "s": 1}
    assert tl.last_taker_side([order, trade(0), cancel, trade(1), order]) == tl.TAKER_SELL
    assert tl.last_taker_side([trade(1), trade(0)]) == tl.TAKER_BUY
    assert tl.last_taker_side([order, cancel]) is None
    assert tl.last_taker_side(None) is None and tl.last_taker_side([]) is None
    model = types.SimpleNamespace(y="t", p=100.0, q=0.5, s=1, Ma=3, Ta=4)
    assert tl.last_taker_side([trade(0), model]) == tl.TAKER_SELL
    assert tl.hit_side(tl.TAKER_SELL) == "buy" and tl.hit_side(tl.TAKER_BUY) == "sell"
    assert tl.hit_side(None) is None and tl.hit_side(7) is None


def test_the_others_touch_takes_our_own_orders_out_of_the_book():
    ours = {555}
    alone = [Level(100.01, [Order(555, 0.25)]), Level(100.00, [Order(7, 3.0)])]
    assert tl.others_touch(alone, own_ids=ours, own_cids=set()) == (100.00, pytest.approx(3.0))
    mixed = [Level(100.00, [Order(555, 0.25), Order(7, 1.0)])]
    assert tl.others_touch(mixed, own_ids=ours, own_cids=set()) == (100.00, pytest.approx(1.0))
    by_cid = [Level(100.00, [Order(9, 0.25, cid=70001), Order(7, 1.0)])]
    assert tl.others_touch(by_cid, own_ids=set(), own_cids={70001}) == (100.00, pytest.approx(1.0))
    unsplittable = [Level(100.00, None, qty=4.0)]
    assert tl.others_touch(unsplittable, own_ids=ours, own_cids=set()) == (100.00, pytest.approx(4.0))
    only_ours = [Level(100.01, [Order(555, 0.25)])]
    price, qty = tl.others_touch(only_ours, own_ids=ours, own_cids=set())
    assert price is None and qty != qty
    assert tl.others_touch([], own_ids=ours, own_cids=set())[0] is None


def test_an_adding_side_rests_only_on_the_deeper_side_and_not_where_the_last_print_hit():
    v = tl.side_verdict
    assert v(side="buy", own_depth=1.0, other_depth=3.0, last_taker=None, ok_token=OK) == tl.REASON_THIN_SIDE
    assert v(side="buy", own_depth=3.0, other_depth=3.0, last_taker=None, ok_token=OK) == OK      # equal is deep enough
    assert v(side="buy", own_depth=3.0, other_depth=1.0, last_taker=tl.TAKER_SELL, ok_token=OK) == tl.REASON_JUST_HIT
    assert v(side="buy", own_depth=3.0, other_depth=1.0, last_taker=tl.TAKER_BUY, ok_token=OK) == OK
    assert v(side="sell", own_depth=3.0, other_depth=1.0, last_taker=tl.TAKER_BUY, ok_token=OK) == tl.REASON_JUST_HIT
    # no information is not a thin book
    assert v(side="sell", own_depth=float("nan"), other_depth=1.0, last_taker=None, ok_token=OK) == OK
    sides, thin, hit = tl.select_sides({"buy": OK, "sell": OK}, bid_depth=1.0, ask_depth=3.0,
                                       last_taker=tl.TAKER_BUY, ok_token=OK)
    assert sides == {"buy": tl.REASON_THIN_SIDE, "sell": tl.REASON_JUST_HIT} and (thin, hit) == (1, 1)
    # a side another rule already refused passes through untouched
    sides, thin, hit = tl.select_sides({"buy": OK, "sell": cp.REASON_EXIT_SIDE}, bid_depth=1.0, ask_depth=3.0,
                                       last_taker=None, ok_token=OK)
    assert sides == {"buy": tl.REASON_THIN_SIDE, "sell": cp.REASON_EXIT_SIDE} and (thin, hit) == (1, 0)


def test_adding_is_read_from_the_position():
    assert tl.is_adding(tl.SIDE_BUY, 0.0) and tl.is_adding(tl.SIDE_SELL, 0.0)
    assert tl.is_adding(tl.SIDE_BUY, 0.25) and not tl.is_adding(tl.SIDE_SELL, 0.25)
    assert tl.is_adding(tl.SIDE_SELL, -0.25) and not tl.is_adding(tl.SIDE_BUY, -0.25)


def test_the_life_verdict_applies_the_touch_to_every_order_and_the_depth_only_to_an_adding_one():
    base = dict(best_bid=100.00, best_ask=100.01, tick=TICK, touch_on=True, select_on=True)
    assert tl.life_verdict(side=0, price=99.99, adding=False, **base) == tl.CANCEL_BEHIND
    assert tl.life_verdict(side=0, price=100.00, adding=False, own_depth=1.0, other_depth=3.0, **base) is None
    assert tl.life_verdict(side=0, price=100.00, adding=True, own_depth=1.0, other_depth=3.0, **base) == tl.CANCEL_THIN
    assert tl.life_verdict(side=0, price=100.00, adding=True, own_depth=3.0, other_depth=1.0,
                           last_taker=tl.TAKER_SELL, **base) == tl.CANCEL_HIT
    assert tl.life_verdict(side=1, price=100.01, adding=True, own_depth=3.0, other_depth=1.0,
                           last_taker=tl.TAKER_SELL, **base) is None
    # each rule only under its own switch
    assert tl.life_verdict(side=0, price=99.99, adding=False, **{**base, "touch_on": False}) is None
    assert tl.life_verdict(side=0, price=100.00, adding=True, own_depth=1.0, other_depth=3.0,
                           **{**base, "select_on": False}) is None


def test_exit_client_ids_are_one_per_book_side_and_collide_with_no_other_scheme():
    exits = {cid for b in range(128) for cid in tl.exit_client_ids(b)}
    entries = {cid for b in range(128) for cid in br.entry_client_ids(b)}
    compaction = {91000 + b * 10 + s for b in range(128) for s in (0, 1, 2)}
    assert len(exits) == 256 and not (exits & entries) and not (exits & compaction)
    assert tl.exit_client_ids(48) == (80481, 80482)
    assert tl.exit_client_id(48, "buy") == 80481 and tl.exit_client_id(48, "sell") == 80482
    assert tl.exit_client_id(0, "BID") == 80001 and tl.exit_client_id(0, "ask") == 80002


def test_the_version_is_declared():
    assert tl.V6214_TOUCH_LIFE_VERSION == "touch_life_v6_2_14"
    assert tl.TOUCH_GAP_TICKS == 1 and tl.EXIT_CLIENT_ID_BASE == 80000


# ---- 2. S0 through the one price intercept the three exit producers share ---------------------------------

class _ScopeAgent:
    def __init__(self, *, cap=True, improve=False, gap=True, tick=5):
        self.research_v628_rung_cap = cap
        self.research_v6210_improve_exits = improve
        self.research_v6214_touch_gap = gap
        self._v628_counts, self._v628_errors = {}, 0
        self._v6210_counts, self._v6210_errors = {}, 0
        self._v6214_counts, self._v6214_errors = {}, 0
        self._v6210_exit_book = None
        self._tick = tick
        self._ledger = DirectExitLedger()

    def _v62_on(self):
        return True

    def _a19_ledger_ref(self):
        return self._ledger


def _scoped(**kwargs):
    frozen = types.SimpleNamespace(maker_exit_price=rr.maker_exit_price,
                                   guarded_post_only_price=cg.guarded_post_only_price)
    ns = {
        "contextmanager": contextmanager,
        "importlib": types.SimpleNamespace(import_module=lambda name: frozen),
        "v628_capped_price_fn": te.capped_price_fn, "V628_CAPPED_MARKER": te.CAPPED_MARKER,
        "v6210_improved_price_fn": ti.improved_price_fn, "v6210_touch_view": ti.touch_view,
        "v62_entry_client_ids": br.entry_client_ids,
        "v6214_never_behind_price_fn": tl.never_behind_price_fn,
        "V6214_NEVER_BEHIND_MARKER": tl.NEVER_BEHIND_MARKER,
        "v6214_fresh_touch_price": tl.fresh_touch_price,
    }
    cls = type("A", (_ScopeAgent,), {})
    for name in ("_v628_rung_cap_on", "_v628_count", "_v628_rung_cap_scope", "_v6210_count", "_v6210_view",
                 "_v6210_exit_view", "_v6214_count", "_v6214_guard_fn"):
        fn = ast.parse(_simple(name)).body[0]
        fn.decorator_list = [ast.Name(id="contextmanager", ctx=ast.Load())] if name == "_v628_rung_cap_scope" else []
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), "<v6214>", "exec"), ns)
        setattr(cls, name, ns[name])
    return cls(**kwargs), frozen


def test_inside_the_scope_no_exit_rung_and_no_reject_guard_prices_behind_the_own_touch():
    agent, frozen = _scoped()
    with agent._v628_rung_cap_scope():
        passive = frozen.maker_exit_price(bid=100.0, ask=100.01, long_position=True, action=te.ACTION_PASSIVE,
                                          tick_size=TICK)
        aggressive = frozen.maker_exit_price(bid=100.0, ask=100.01, long_position=True, action=te.ACTION_AGGRESSIVE,
                                             tick_size=TICK)
        guarded = frozen.guarded_post_only_price(side="sell", original_price=100.01, best_bid=100.00,
                                                 best_ask=100.01, tick_size=TICK, reject_streak=2)
        assert getattr(frozen.maker_exit_price, te.CAPPED_MARKER)
        assert getattr(frozen.guarded_post_only_price, tl.NEVER_BEHIND_MARKER)
    assert passive == pytest.approx(100.01) and aggressive == pytest.approx(100.01) and guarded == pytest.approx(100.01)
    # both frozen functions are restored on the way out
    assert frozen.maker_exit_price is rr.maker_exit_price
    assert frozen.guarded_post_only_price is cg.guarded_post_only_price
    assert agent._v6214_counts == {"passive_capped": 1, "guard_fresh_touch": 1}
    assert agent._v628_counts == {"rung_capped": 1}


def test_s0_alone_still_installs_the_intercept():
    agent, frozen = _scoped(cap=False, improve=False, gap=True)
    with agent._v628_rung_cap_scope():
        assert frozen.maker_exit_price(bid=100.0, ask=100.01, long_position=False, action=te.ACTION_PASSIVE,
                                       tick_size=TICK) == pytest.approx(100.00)
    assert frozen.maker_exit_price is rr.maker_exit_price


def test_with_s0_off_the_scope_is_v6_2_13_exactly():
    agent, frozen = _scoped(gap=False)
    with agent._v628_rung_cap_scope():
        assert frozen.maker_exit_price(bid=100.0, ask=100.01, long_position=True, action=te.ACTION_PASSIVE,
                                       tick_size=TICK) == pytest.approx(100.03)
        assert frozen.guarded_post_only_price is cg.guarded_post_only_price
    assert agent._v6214_counts == {}


def test_s0_composes_with_the_improvement_inside_a_wide_spread():
    agent, frozen = _scoped(improve=True)
    book = Book([Level(100.00, [Order(1, 2.5)])], [Level(100.10, [Order(2, 2.5)])])
    agent._v6210_exit_book = (48, book, 5)
    with agent._v628_rung_cap_scope():
        passive = frozen.maker_exit_price(bid=100.0, ask=100.1, long_position=True, action=te.ACTION_PASSIVE,
                                          tick_size=TICK)
    assert passive == pytest.approx(100.09)          # to the own touch, then one tick inside the others' best


def test_a_nested_scope_neither_rewraps_nor_restores():
    agent, frozen = _scoped()
    with agent._v628_rung_cap_scope():
        outer_price, outer_guard = frozen.maker_exit_price, frozen.guarded_post_only_price
        with agent._v628_rung_cap_scope():
            assert frozen.maker_exit_price is outer_price and frozen.guarded_post_only_price is outer_guard
        assert frozen.maker_exit_price is outer_price and frozen.guarded_post_only_price is outer_guard
    assert frozen.maker_exit_price is rr.maker_exit_price
    assert frozen.guarded_post_only_price is cg.guarded_post_only_price


# ---- 3. S1 + S2: the post-pass over every resting order -------------------------------------------------

class _Resp:
    def __init__(self):
        self.instructions = []

    def cancel_orders(self, book_id, order_ids, delay=0):
        self.instructions.append(types.SimpleNamespace(
            type="CANCEL_ORDERS", bookId=book_id, delay=delay,
            cancellations=[types.SimpleNamespace(orderId=o, volume=None) for o in order_ids]))


class _LifeAgent:
    A19_CANCEL_MEMO_MAX = 2048
    max_instructions_per_book = 5
    research_profitable_exit_ttl_ms = 4000.0

    def __init__(self, *, touch=True, select=True, net=None, last=None):
        self.research_v6214_touch_life = touch
        self.research_v6214_side_select = select
        self._v6214_counts, self._v6214_errors = {}, 0
        self._v6214_last_taker = dict(last or {})
        self._a191_reprice_release = {}
        self._tick = 9
        self.net = dict(net or {})
        self.notes = []
        self.ledger = DirectExitLedger()

    def _a19_ledger_ref(self):
        return self.ledger

    @staticmethod
    def _a19_tick_size(state):
        return TICK

    def _direct_signed_inventory(self, book_id):
        return float(self.net.get(int(book_id), 0.0))

    @staticmethod
    def _execution_flat_epsilon():
        return 1e-9

    @staticmethod
    def _count_book_instructions(response, book_id):
        return sum(1 for ix in response.instructions if getattr(ix, "bookId", None) == book_id)

    @staticmethod
    def _direct_entry_quote_client_ids(book_id):
        return set(br.entry_client_ids(int(book_id)))

    def _a19_note_exit_cancel(self, book_id, order_ids, disposition):
        self.notes.append((int(book_id), list(order_ids), disposition))

    def _direct_protected_partial_order_id(self, book_id, state=None):
        return getattr(self, "protected", {}).get(int(book_id))


def _life_agent(**kwargs):
    ns = {
        "V6214_SIDE_BUY": tl.SIDE_BUY, "v6214_is_adding": tl.is_adding, "v6214_life_verdict": tl.life_verdict,
        "v6214_others_touch": tl.others_touch, "v6214_exit_client_ids": tl.exit_client_ids,
        "v62_entry_client_ids": br.entry_client_ids,
        "V6214_CANCEL_BEHIND": tl.CANCEL_BEHIND, "V6214_CANCEL_THIN": tl.CANCEL_THIN,
        "V6214_CANCEL_HIT": tl.CANCEL_HIT,
        "ABSENT_ENTRY_QUOTE_CANCEL": ABSENT_ENTRY_QUOTE_CANCEL, "ABSENT_REPRICE_CANCEL": ABSENT_REPRICE_CANCEL,
        "resolve_book_from_state_mapping": cg.resolve_book_from_state_mapping,
    }
    cls = type("L", (_LifeAgent,), {})
    for name in ("_v6214_service_touch_life", "_v6214_count", "_v6214_cancelled_ids", "_v6214_others_depth",
                 "_a19_is_entry_quote_row"):
        exec(compile(ast.Module(body=[ast.parse(_simple(name)).body[0]], type_ignores=[]), "<v6214>", "exec"), ns)
        setattr(cls, name, ns[name])
    return cls(**kwargs)


def _rest(agent, oid, book, side, price, cid=None, qty=0.25, age_ms=1000.0):
    agent.ledger.note_accepted(order_id=oid, book_id=book, side=side, price=price, quantity=qty,
                               timestamp_ns=NOW - int(age_ms * 1e6), tick=8, client_id=cid)


def _state(books):
    return types.SimpleNamespace(books=books, timestamp=NOW, config=b0._Config())


def _cancelled(resp):
    return {(ix.bookId, c.orderId) for ix in resp.instructions for c in ix.cancellations}


def _touch_book(bid_q=2.5, ask_q=2.5, ours=(), events=None, bid=100.00, ask=100.01):
    """Best levels at 100.00 / 100.01; ``ours`` = [(side, oid, qty)] of our orders resting at the touch."""
    bid_orders = [Order(1, bid_q)] + [Order(o, q) for s, o, q in ours if s == 0]
    ask_orders = [Order(2, ask_q)] + [Order(o, q) for s, o, q in ours if s == 1]
    return Book([Level(bid, bid_orders)], [Level(ask, ask_orders)], events=events)


def test_an_order_at_the_touch_is_left_to_rest():
    agent = _life_agent()
    _rest(agent, 11, 3, 0, 100.00, cid=70031)
    _rest(agent, 12, 3, 1, 100.01, cid=70032)
    resp = _Resp()
    assert agent._v6214_service_touch_life(resp, _state({3: _touch_book(ours=[(0, 11, 0.25), (1, 12, 0.25)])})) == 0
    assert resp.instructions == [] and agent._v6214_counts == {}


def test_an_order_the_touch_moved_away_from_is_cancelled_and_its_book_released_on_the_ack():
    agent = _life_agent(net={3: 0.25})
    _rest(agent, 21, 3, 1, 100.02)                     # a client-less exit one tick behind the best ask
    resp = _Resp()
    assert agent._v6214_service_touch_life(resp, _state({3: _touch_book()})) == 1
    assert _cancelled(resp) == {(3, 21)}
    assert agent._v6214_counts == {"cancel_behind": 1, "cancel_instructions": 1}
    # no registered identity: the A1.9.1.2 path releases the book on the exchange's acknowledgement
    assert agent._a191_reprice_release[21] == {"book_id": 3, "side": "sell", "client_id": None, "tick": 9}
    assert agent.notes == [(3, [21], ABSENT_REPRICE_CANCEL)]


def test_only_the_offending_order_is_cancelled():
    agent = _life_agent(net={3: 0.25})
    _rest(agent, 31, 3, 0, 100.00, cid=70031)          # adding bid at the touch, deep side
    _rest(agent, 32, 3, 1, 100.02, cid=80032)          # exit one tick behind
    resp = _Resp()
    agent._v6214_service_touch_life(resp, _state({3: _touch_book(bid_q=5.0, ours=[(0, 31, 0.25)])}))
    assert _cancelled(resp) == {(3, 32)} and 32 not in agent._a191_reprice_release   # identified: normal release


def test_an_adding_order_on_the_thinner_side_is_cancelled_with_our_own_quantity_taken_out():
    agent = _life_agent()
    _rest(agent, 41, 3, 0, 100.00, cid=70031)
    # the bid level shows 1.25 with ours, 1.0 without; the ask shows 1.1 -> the bid side is the thinner one
    resp = _Resp()
    agent._v6214_service_touch_life(resp, _state({3: _touch_book(bid_q=1.0, ask_q=1.1, ours=[(0, 41, 0.25)])}))
    assert _cancelled(resp) == {(3, 41)} and agent._v6214_counts["cancel_thin"] == 1


def test_each_cancel_is_registered_as_ours_in_the_observer_vocabulary():
    agent = _life_agent(net={3: 0.25})
    _rest(agent, 141, 3, 0, 99.99, cid=70031)          # entry quote, behind
    _rest(agent, 142, 3, 1, 100.02, cid=80032)         # exit, behind
    resp = _Resp()
    agent._v6214_service_touch_life(resp, _state({3: _touch_book()}))
    assert sorted(agent.notes) == [(3, [141], ABSENT_ENTRY_QUOTE_CANCEL), (3, [142], ABSENT_REPRICE_CANCEL)]
    assert {ABSENT_ENTRY_QUOTE_CANCEL, ABSENT_REPRICE_CANCEL} <= AGENT_CANCEL_DISPOSITIONS
    assert len(resp.instructions) == 1                 # one cancel instruction for the book


def test_an_adding_order_on_the_side_the_last_print_hit_is_cancelled():
    agent = _life_agent(last={3: tl.TAKER_SELL})
    _rest(agent, 51, 3, 0, 100.00, cid=70031)
    resp = _Resp()
    agent._v6214_service_touch_life(resp, _state({3: _touch_book(bid_q=5.0, ours=[(0, 51, 0.25)])}))
    assert _cancelled(resp) == {(3, 51)} and agent._v6214_counts["cancel_hit"] == 1


def test_an_exit_is_held_by_the_touch_rule_alone():
    agent = _life_agent(net={3: 0.25}, last={3: tl.TAKER_BUY})
    _rest(agent, 61, 3, 1, 100.01, cid=80032)          # reducing ask at the touch, thin and just lifted
    _rest(agent, 62, 5, 1, 100.01, cid=70052)          # an entry quote that became the reducing side
    resp = _Resp()
    books = {3: _touch_book(bid_q=5.0, ask_q=0.5, ours=[(1, 61, 0.25)]),
             5: _touch_book(bid_q=5.0, ask_q=0.5, ours=[(1, 62, 0.25)])}
    agent.net[5] = 0.25
    assert agent._v6214_service_touch_life(resp, _state(books)) == 0 and resp.instructions == []


def test_a_row_past_its_life_has_expired_at_the_venue_and_is_left_alone():
    agent = _life_agent(net={3: 0.25})
    _rest(agent, 71, 3, 1, 100.02, age_ms=4500.0)
    resp = _Resp()
    assert agent._v6214_service_touch_life(resp, _state({3: _touch_book()})) == 0


def test_an_order_another_pass_already_cancels_is_not_cancelled_twice():
    agent = _life_agent(net={3: 0.25})
    _rest(agent, 81, 3, 1, 100.02)
    resp = _Resp()
    resp.cancel_orders(book_id=3, order_ids=[81])
    agent._v6214_service_touch_life(resp, _state({3: _touch_book()}))
    assert len(resp.instructions) == 1


def test_a_spent_instruction_budget_defers_the_cancel():
    agent = _life_agent(net={3: 0.25})
    _rest(agent, 91, 3, 1, 100.02)
    resp = _Resp()
    for _ in range(5):
        resp.instructions.append(types.SimpleNamespace(type="PLACE_ORDER_LIMIT", bookId=3, cancellations=[]))
    assert agent._v6214_service_touch_life(resp, _state({3: _touch_book()})) == 0
    assert agent._v6214_counts == {"budget_deferred": 1}


def test_each_rule_runs_only_under_its_own_switch():
    def run(**kw):
        agent = _life_agent(**kw)
        _rest(agent, 101, 3, 0, 99.99, cid=70031)       # behind the touch
        _rest(agent, 102, 4, 0, 100.00, cid=70041)      # at the touch on the thin side
        resp = _Resp()
        books = {3: _touch_book(), 4: _touch_book(bid_q=1.0, ask_q=3.0, ours=[(0, 102, 0.25)])}
        agent._v6214_service_touch_life(resp, _state(books))
        return _cancelled(resp)
    assert run() == {(3, 101), (4, 102)}
    assert run(select=False) == {(3, 101)}
    assert run(touch=False) == {(4, 102)}
    assert run(touch=False, select=False) == set()


def test_a_row_for_a_book_the_state_does_not_carry_is_left_alone():
    agent = _life_agent(net={3: 0.25})
    _rest(agent, 121, 9, 1, 100.02)
    resp = _Resp()
    assert agent._v6214_service_touch_life(resp, _state({3: _touch_book()})) == 0 and resp.instructions == []


def test_the_last_print_scan_stops_at_the_newest_trade():
    class Boom:
        def __getattribute__(self, name):
            raise AssertionError("an event older than the newest trade was read")
    events = [Boom(), trade(0), {"y": "o"}, {"y": "c"}]
    assert tl.last_taker_side(events) == tl.TAKER_BUY
    assert tl.last_taker_side(iter([trade(1), {"y": "o"}])) == tl.TAKER_SELL


def test_the_protected_remainder_of_a_partial_fill_is_never_a_touch_life_cancel():
    agent = _life_agent(net={3: 0.1})
    agent.protected = {3: 131}
    _rest(agent, 131, 3, 0, 99.99, cid=70031, qty=0.15)     # the bound remainder, one tick behind
    resp = _Resp()
    assert agent._v6214_service_touch_life(resp, _state({3: _touch_book()})) == 0
    assert agent._v6214_counts == {"partial_protected": 1}


def test_a_book_without_a_readable_touch_is_skipped():
    agent = _life_agent(net={3: 0.25})
    _rest(agent, 111, 3, 1, 100.02)
    resp = _Resp()
    assert agent._v6214_service_touch_life(resp, _state({3: Book([], [])})) == 0


# ---- 4. S2 at placement, through the real acquisition pass ------------------------------------------------

def _select_agent(on=True):
    agent = c5._acquire_agent()
    agent.research_v6214_side_select = on
    agent._v6214_counts, agent._v6214_errors, agent._v6214_last_taker = {}, 0, {}
    ns = {"v6214_last_taker_side": tl.last_taker_side, "v6214_select_sides": tl.select_sides,
          "v6214_level_quantity": tl.level_quantity, "V625_REASON_OK": cp.REASON_OK}
    cls = type(agent)
    for name in ("_v6214_note_prints", "_v6214_select_sides", "_v6214_count"):
        exec(compile(ast.Module(body=[ast.parse(_simple(name)).body[0]], type_ignores=[]), "<v6214>", "exec"), ns)
        setattr(cls, name, ns[name])
    return agent


def _acquire(agent, state):
    r = b0._Response()
    agent._v62_acquire(r, state, {})
    return c5._placed(r)


def _sides(placed, book):
    return sorted("buy" if ix.direction is b0._OrderDirection.BUY else "sell" for ix in placed.get(book, []))


def test_a_flat_book_quotes_only_its_deeper_side():
    agent = _select_agent()
    thin_bid = b0._Book()
    thin_bid.bids[0].quantity, thin_bid.asks[0].quantity = 1.0, 3.0
    placed = _acquire(agent, b0._state(8, overrides={3: thin_bid}))
    assert _sides(placed, 3) == ["sell"]
    assert all(_sides(placed, b) == ["buy", "sell"] for b in range(8) if b != 3)    # equal depth quotes both
    assert agent._v6214_counts == {"select_thin": 1}


def test_a_side_the_last_print_hit_is_not_quoted_and_the_memory_outlives_a_quiet_state():
    agent = _select_agent()
    lifted = b0._Book(events=[trade(1), trade(0)])        # the last print was a taker buy: the asks were hit
    assert _sides(_acquire(agent, b0._state(8, overrides={5: lifted})), 5) == ["buy"]
    quiet = b0._Book()                                       # no print on the next state: the memory holds
    assert _sides(_acquire(agent, b0._state(8, overrides={5: quiet})), 5) == ["buy"]
    hit = b0._Book(events=[trade(1)])                       # now the bids were hit
    assert _sides(_acquire(agent, b0._state(8, overrides={5: hit})), 5) == ["sell"]
    assert agent._v6214_counts == {"select_hit": 3}


def test_with_the_switch_off_every_flat_book_quotes_both_sides():
    agent = _select_agent(on=False)
    thin_bid = b0._Book(events=[trade(1)])
    thin_bid.bids[0].quantity = 0.5
    placed = _acquire(agent, b0._state(8, overrides={3: thin_bid}))
    assert all(_sides(placed, b) == ["buy", "sell"] for b in range(8))
    assert agent._v6214_counts == {} and agent._v6214_last_taker == {}


def test_a_book_refused_on_every_side_reports_the_binding_reason():
    agent = _select_agent()
    book = b0._Book(events=[trade(0)])                     # asks lifted
    book.bids[0].quantity, book.asks[0].quantity = 1.0, 3.0  # and the bid side is the thinner one
    _acquire(agent, b0._state(8, overrides={3: book}))
    assert agent._v62_request.get(tl.REASON_THIN_SIDE) == 1


# ---- 5. S3: an exit leaves with a client id ------------------------------------------------------------------

class _IdAgent:
    def __init__(self):
        self._v6214_counts, self._v6214_errors = {}, 0

    @staticmethod
    def _get(obj, *names):
        for name in names:
            if hasattr(obj, name):
                return getattr(obj, name)
        return None

    @staticmethod
    def _research_instruction_side(instruction):
        return "buy" if getattr(instruction, "direction", None) in (0, "BUY") else "sell"

    @staticmethod
    def _research_set_instruction_attr(instruction, name, value):
        setattr(instruction, name, value)
        return True


def test_a_limit_placement_without_a_client_id_gets_its_book_side_exit_id():
    ns = {"v6214_exit_client_id": tl.exit_client_id}
    cls = type("I", (_IdAgent,), {})
    for name in ("_v6214_assign_exit_identity", "_v6214_count"):
        exec(compile(ast.Module(body=[ast.parse(_simple(name)).body[0]], type_ignores=[]), "<v6214>", "exec"), ns)
        setattr(cls, name, ns[name])
    agent = cls()
    exit_sell = types.SimpleNamespace(type="PLACE_ORDER_LIMIT", bookId=5, direction=1, clientOrderId=None)
    exit_buy = types.SimpleNamespace(type="PLACE_ORDER_LIMIT", bookId=7, direction=0, clientOrderId=None)
    entry = types.SimpleNamespace(type="PLACE_ORDER_LIMIT", bookId=5, direction=0, clientOrderId=70051)
    market = types.SimpleNamespace(type="PLACE_ORDER_MARKET", bookId=5, direction=1, clientOrderId=None)
    cancel = types.SimpleNamespace(type="CANCEL_ORDERS", bookId=5, cancellations=[])
    resp = types.SimpleNamespace(instructions=[exit_sell, exit_buy, entry, market, cancel])
    assert agent._v6214_assign_exit_identity(resp) == 2
    assert (exit_sell.clientOrderId, exit_buy.clientOrderId) == (80052, 80071)
    assert entry.clientOrderId == 70051 and market.clientOrderId is None
    assert agent._v6214_counts == {"exit_ids": 2}


# ---- 6. wiring -------------------------------------------------------------------------------------------------

def test_all_four_switches_default_on_and_ship_in_params():
    for key in ("research_v6214_touch_gap", "research_v6214_touch_life", "research_v6214_side_select",
                "research_v6214_exit_identity"):
        assert f'getattr(self.config, "{key}", True)' in SIMPLE
        assert f"{key}=1" in LAUNCHER


def test_s0_sets_the_cushion_after_the_frozen_config_is_parsed():
    init = _simple("initialize")
    assert init.index("super().initialize()") < init.index("self._init_build_switches()")
    switches = _simple("_init_build_switches")
    read = switches.index('getattr(self.config, "research_v6214_touch_gap", True)')
    keep = switches.index('self._v6214_configured_gap = int(getattr(self, "research_post_only_safety_ticks", 2) or 2)')
    sets = switches.index("self.research_post_only_safety_ticks = int(V6214_TOUCH_GAP_TICKS)")
    assert read < keep < sets and "if self.research_v6214_touch_gap:" in switches[keep:sets]


def test_the_post_pass_runs_after_a1_9_1_and_before_the_stall_telemetry():
    resp = _simple("respond")
    a191 = resp.index("self._a191_service_reprice_cancels(response, state)")
    ours = resp.index("self._v6214_service_touch_life(response, state)")
    stall = resp.index("self._a199_note_exit_stalls(response)")
    assert a191 < ours < stall and resp.index("response = super().respond(state)") < a191


def test_s2_sits_after_the_venue_band_and_the_print_memory_before_the_book_facts():
    acq = _simple("_v62_acquire")
    note = acq.index("self._v6214_note_prints(book_id, book)")
    facts = acq.index("facts = self._v62_book_facts(book_id, book, state, response, lot)")
    band = acq.index("sides, side_qty = self._v6213_venue_band(book_id, sides, side_qty, flat_eps=eps)")
    select = acq.index("sides = self._v6214_select_sides(book_id, book, sides)")
    surplus = acq.index("sides[side_token] = V626_REASON_SURPLUS")
    assert note < facts < band < select < surplus


def test_s3_labels_before_the_sanitizer_the_validator_and_the_pending_ledger():
    body = SIMPLE[SIMPLE.index("# Only contract/risk safety may veto the already-decided actions here."):]
    ids = body.index("self._v6214_assign_exit_identity(response)")
    assert ids < body.index("self._research_sanitize_maker_instructions(response, state)") \
        < body.index("self._research_final_validate_instructions(response, state)") \
        < body.index("self._direct_record_pending_placements(response, state)")


def test_the_intercept_caps_then_keeps_never_behind_then_improves_and_restores_both():
    scope = _simple("_v628_rung_cap_scope")
    capped = scope.index("priced = v628_capped_price_fn(original")
    never = scope.index("priced = v6214_never_behind_price_fn(")
    improved = scope.index("priced = v6210_improved_price_fn(")
    assert capped < never < improved
    assert 'setattr(module, "guarded_post_only_price", original_guard)' in scope
    assert 'setattr(module, "maker_exit_price", original)' in scope


def test_the_state_row_reports_every_switch_and_the_counters():
    tele = _simple("_v62_telemetry")
    for key in ("touch_gap_on=", "touch_life_on=", "side_select_on=", "exit_identity_on=",
                "touch_life=self._v6214_snapshot()"):
        assert key in tele
    snap = _simple("_v6214_snapshot")
    assert 'out["post_only_gap_ticks"]' in snap and 'out["configured_gap_ticks"]' in snap


def test_pins_and_the_launcher_arm():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_15"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_15"' in SIMPLE
    assert "strategy1_direct_v6_2_14)" in LAUNCHER and "V6213_BUILD=1; V6214_BUILD=1 ;;" in LAUNCHER
    assert "strategy1_direct_v6_2_13)" in LAUNCHER                      # the previous arm stays
    assert 'if [[ "$V6214_BUILD" == "1" ]]; then' in LAUNCHER
    assert "[preflight] v6.2.14 touch life PASS" in LAUNCHER
    assert "tests/test_research_v6_2_14_touch_life.py" in LAUNCHER
