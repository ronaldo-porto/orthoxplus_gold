"""v6.2.0: symmetric touch quotes on every valid flat book -- breadth.

Validator 0.6.1 rung 2 (live on mainnet since 2026-09-18 13:46, upstream 0234998): the de-beta making
term is, per uid, the sum over books of 2·min(buy capture, sell capture) against a centred mid, ranked
among the positive makers; it carries 0.25 of the trading score now and 0.5 at the final rung.  Every
(uid, book) is capped at 500k quote per 24 h, reachable at 0.25 lots on the engine's own cadence, so
the lever is the number of books quoted.  The engine quoted ~8 books (the ranker's top-20 admitted
into 8 slots at 2.0 BASE): making 199.5, rank 0.07 of 44 makers.

Per book the engine already quoted both sides at entry and held one lot in flight; v6.2 removes the
ranker, the score-EV eligibility and the slot count from acquisition and derives the caps from the
universe.  These tests run the real methods in a harness, not copies of them.
"""
import ast
import subprocess
import textwrap
import types
import typing
from pathlib import Path

import pytest

import research_v62_breadth as br
import research_v625_cap_paced as cap_paced
import research_v626_balanced_maker as bm
import research_v627_maker_ceiling as mc
import research_v628_touch_exit as te
import research_v6210_touch_improve as ti
from _harness import extractor
from research_v62_making_mirror import MakingMirror as V62MakingMirror, DEFAULT_LOOKBACK_NS
import research_v6211_score_logic as sl11  # noqa: E402
import research_v6212_pace_defer as pd12  # noqa: E402

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()

_method_source = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")

METHODS = [
    "_v62_on", "_v62_count", "_v62_apply_caps", "_v62_entry_ttl_ns", "_v62_book_facts",
    "_v62_place_touch_quotes", "_v62_acquire", "_v62_feed_mirror", "_v62_telemetry",
    "_v621_on", "_v621_cap_override",          # v6.2.1: the telemetry reports the managed universe
    "_v622_on", "_v622_count",                 # v6.2.2: the telemetry reports the seed at breadth
    "_v61_on", "_v623_on", "_v623_count", "_v623_snapshot",   # v6.2.3: and the premium floor
    "_v624_on",                                # v6.2.4: and the release life
    "_v625_two_sided_on", "_v625_cap_pace_on", "_v625_on", "_v625_count",   # v6.2.5: both switches off
    "_v625_snapshot", "_v625_apply_caps",
    "_v626_capture_balance_on", "_v626_quote_life_on", "_v626_loss_budget_on",   # v6.2.6, all off
    "_v626_count", "_v626_side_clips", "_v626_book_capture", "_v626_snapshot",
    "_v627_balance_gate_on", "_v627_band_caps_on", "_v627_on",              # v6.2.7, both off
    "_v627_count", "_v627_caps", "_v627_snapshot",
    "_v627_clip_bound_on", "_v627_pace_rewind_on", "_v627_clip_bound",
    "_v628_rung_cap_on", "_v628_band_inventory_on", "_v628_fee_viable_on", "_v628_skew_sides_on", "_v628_on", "_v628_count", "_v628_book_viable", "_v628_snapshot",   # v6.2.8, all off
    "_v6210_count", "_v6210_view", "_v6210_entry_prices", "_v6210_note_outbid", "_v6210_snapshot",   # v6.2.10, both off
    "_v6211_count", "_v6211_feed_mirror", "_v6211_snapshot",   # v6.2.11, all off (no switch attribute)
    "_v6212_count", "_v6212_snapshot",                         # v6.2.12, off the same way
]
LOT = 0.25
UNIVERSE = 128


# ------------------------------------------------------------------------------------------------
# 1. The pure module
# ------------------------------------------------------------------------------------------------

def _facts(**kw):
    base = dict(book_id=7, best_bid=204.31, best_ask=204.35, net_base=0.0, live_order=False, cap_ok=True,
                quote_free=1e6, base_free=100.0, instructions_used=0, max_instructions=5)
    base.update(kw)
    return br.BookFacts(**base)


def test_touch_prices_are_the_best_bid_and_ask_on_the_grid():
    assert br.touch_prices(204.31, 204.35, 2) == (204.31, 204.35)
    assert br.touch_prices(204.314, 204.3549, 2) == (204.31, 204.35)
    assert br.touch_prices(204.35, 204.31, 2) is None          # crossed
    assert br.touch_prices(204.31, 204.31, 2) is None          # locked
    assert br.touch_prices(None, 204.35, 2) is None
    assert br.touch_prices(0.0, 204.35, 2) is None
    assert br.touch_prices(float("nan"), 204.35, 2) is None


def test_verdict_ok_for_a_flat_valid_book():
    assert br.universe_verdict(_facts(), lot=LOT, flat_eps=5e-5) == br.REASON_OK


@pytest.mark.parametrize("kw,reason", [
    (dict(best_bid=None), br.REASON_NO_L1),
    (dict(best_ask=None), br.REASON_NO_L1),
    (dict(best_bid=204.36), br.REASON_CROSSED),
    (dict(net_base=0.25), br.REASON_NOT_FLAT),
    (dict(net_base=-0.1382), br.REASON_NOT_FLAT),              # dust belongs to the compactor
    (dict(live_order=True), br.REASON_LIVE_ORDER),
    (dict(cap_ok=False), br.REASON_VOLUME_CAP),
    (dict(instructions_used=4), br.REASON_BUDGET),             # two more would exceed 5
    (dict(quote_free=10.0), br.REASON_NO_BALANCE),
    (dict(base_free=0.1), br.REASON_NO_BALANCE),
])
def test_verdict_names_the_reason(kw, reason):
    assert br.universe_verdict(_facts(**kw), lot=LOT, flat_eps=5e-5) == reason


def test_verdict_reason_order_is_structural_first():
    """A book that fails several ways reports the venue reason before the agent's own state."""
    f = _facts(best_bid=204.36, net_base=0.25, live_order=True)
    assert br.universe_verdict(f, lot=LOT, flat_eps=5e-5) == br.REASON_CROSSED
    f = _facts(net_base=0.25, live_order=True, cap_ok=False)
    assert br.universe_verdict(f, lot=LOT, flat_eps=5e-5) == br.REASON_NOT_FLAT


def test_flat_epsilon_is_respected():
    assert br.universe_verdict(_facts(net_base=4e-5), lot=LOT, flat_eps=5e-5) == br.REASON_OK
    assert br.universe_verdict(_facts(net_base=6e-5), lot=LOT, flat_eps=5e-5) == br.REASON_NOT_FLAT


def test_universe_caps_are_books_times_lot():
    caps = br.universe_caps(UNIVERSE, LOT)
    assert caps == {
        "research_max_total_abs_base": 32.0, "research_max_total_open_books": 128,
        "research_max_active_open_books": 128, "research_max_open_books": 128,
        "max_managed_books_per_tick": 128, "max_mm_books_per_tick": 128,
        "research_a195_max_seed_abs_base": 64.0,           # v6.2.2: two lots per book
    }
    assert br.universe_caps(0, LOT)["research_max_total_open_books"] == 1


def test_lot_quantity_and_client_ids():
    assert br.lot_quantity(0.25, 4) == 0.25
    assert br.lot_quantity("x", 4) == 0.0
    assert br.entry_client_ids(48) == (70481, 70482)
    assert br.entry_client_ids(0) == (70001, 70002)


# ------------------------------------------------------------------------------------------------
# 2. The real methods in a harness
# ------------------------------------------------------------------------------------------------

class _Enum:
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def __repr__(self):
        return self.name


class _OrderDirection:
    BUY = _Enum("BUY", 0)
    SELL = _Enum("SELL", 1)


class _STP:
    CANCEL_BOTH = "CANCEL_BOTH"


class _TIF:
    GTT = 1


class _LSO:
    NONE = "NONE"


class _Level:
    def __init__(self, price, quantity=2.5):
        self.price = price
        self.quantity = quantity


class _Book:
    def __init__(self, bid=204.31, ask=204.35, events=None):
        self.bids = [_Level(bid)] if bid is not None else []
        self.asks = [_Level(ask)] if ask is not None else []
        self.events = events or []


class _Config:
    priceDecimals = 2
    volumeDecimals = 4


class _State:
    def __init__(self, books, timestamp=48_811_000_000_000):
        self.books = books
        self.timestamp = timestamp
        self.config = _Config()


class _Balance:
    def __init__(self, free):
        self.free = free


class _Account:
    def __init__(self, quote=1e6, base=100.0):
        self.quote_balance = _Balance(quote)
        self.base_balance = _Balance(base)


class _Response:
    def __init__(self):
        self.instructions = []

    def limit_order(self, **kw):
        # The real FinanceAgentResponse.limit_order(book_id=...) builds an instruction carrying bookId.
        kw = dict(kw)
        kw["bookId"] = kw.pop("book_id")
        self.instructions.append(types.SimpleNamespace(type="PLACE_ORDER_LIMIT", **kw))


class _Base:
    max_instructions_per_book = 5
    mm_base_size = 0.25
    mm_expiry_period = 500_000_000
    research_enable_adaptive_ttl = True
    research_ttl_min_ms = 250.0
    research_ttl_max_ms = 3000.0
    research_kappa_lookback_ns = 10_800_000_000_000
    uid = 82

    def __init__(self):
        self.rows = []
        self.inventory = {}
        self.live = set()
        self.capped = set()
        self.accounts = {}
        self.mems = {}
        self.ttl_choice = (3000.0, "STABLE_LONG", None)
        self.fill_quotes = []
        self._tick = 0

    def _emit(self, event_type, force=False, **payload):
        self.rows.append((event_type, payload))

    def _direct_signed_inventory(self, book_id):
        return self.inventory.get(int(book_id), 0.0)

    def _direct_book_has_live_order(self, book_id):
        return int(book_id) in self.live

    def _research_can_add_volume(self, state, book_id, notional):
        return int(book_id) not in self.capped

    def _count_book_instructions(self, response, book_id):
        return sum(1 for i in getattr(response, "instructions", []) if int(getattr(i, "bookId", -1)) == int(book_id))

    def _execution_flat_epsilon(self):
        return 5e-5

    def _v61_price_decimals(self, state):
        return 2

    def _prefer_maker(self, book_id):
        return True

    def _mem(self, book_id):
        return self.mems.setdefault(int(book_id), types.SimpleNamespace(quote_count=0))

    def _record_fill_quote(self, mem, side, dist):
        self.fill_quotes.append((side, dist))

    def _research_choose_ttl(self, book_id, profile, state, *, baseline_ns):
        return self.ttl_choice


def _agent(*, v62=True, books=None, tick=10):
    body = "".join(
        textwrap.indent(textwrap.dedent(_method_source(n)), "    ") + "\n" for n in METHODS
    )
    scope = {
        "_Base": _Base, "Any": typing.Any,
        "OrderDirection": _OrderDirection, "STP": _STP, "TimeInForce": _TIF, "LoanSettlementOption": _LSO,
        "V62_REASON_OK": br.REASON_OK, "V62_SKIP_REASONS": br.SKIP_REASONS,
        "V62_STATE_EVERY_TICKS": br.V62_STATE_EVERY_TICKS, "V62_VERSION": br.V62_VERSION,
        "V62BookFacts": br.BookFacts, "v62_entry_client_ids": br.entry_client_ids,
        "v62_lot_quantity": br.lot_quantity, "v62_touch_prices": br.touch_prices,
        "v62_universe_caps": br.universe_caps, "v62_universe_verdict": br.universe_verdict,
        "V62_DEFAULT_LOOKBACK_NS": DEFAULT_LOOKBACK_NS, "V62MakingMirror": V62MakingMirror,
        "V625_REASON_OK": "OK", "V625_BAND_CLIPS": 2.0,
        "V625_SIDE_BUY": "buy", "V625_SIDE_SELL": "sell",
        "v626_side_clips": bm.side_clips, "v626_side_already_instructed": bm.side_already_instructed,
        "v626_skip_reason": bm.skip_reason, "v626_open_book_caps": bm.open_book_caps,
        "v625_lots_of": cap_paced.lots_of, "V626_REASON_SURPLUS": bm.REASON_SURPLUS,
        "v625_band_for": lambda clip: 2.0 * float(clip),
        "v625_pace_snapshot": lambda paces, min_order=0.25: {"books": len(paces)},
        "V625_CAP_PACED_VERSION": "cap_paced_maker_v6_2_5",
        "v627_band_caps": mc.band_caps, "v627_balance_gate_sides": mc.balance_gate_sides,
        "V627_BALANCE_TARGET": mc.BALANCE_TARGET,
        "v627_cap_absorption_clip": mc.cap_absorption_clip,
        "v627_bounded_ceiling": mc.bounded_ceiling, "v627_pace_rewound": mc.pace_rewound,
        "V628_REASON_FEE_UNVIABLE": te.REASON_FEE_UNVIABLE, "V628_TOUCH_EXIT_VERSION": te.V628_TOUCH_EXIT_VERSION,
        "v628_fee_viable": te.fee_viable, "v628_spread_bps": te.spread_bps,
        "v628_skewed_sides": te.skewed_sides, "v628_band_inventory_util": te.band_inventory_util,
        "V625_PACE_PERIOD_NS": cap_paced.PACE_PERIOD_NS,
        "V6210_TOUCH_IMPROVE_VERSION": ti.V6210_TOUCH_IMPROVE_VERSION,
        "v6210_entry_prices": ti.entry_prices, "v6210_touch_view": ti.touch_view,
        "V6211_SCORE_LOGIC_VERSION": sl11.V6211_SCORE_LOGIC_VERSION, "V6211OwnAlphaMirror": sl11.OwnAlphaMirror,
        "V6211_LIFT_ALL_STATUS": sl11.LIFT_ALL_STATUS, "v6211_held_book": sl11.held_book,
        "V6212_PACE_DEFER_VERSION": pd12.V6212_PACE_DEFER_VERSION,
        "v6212_sample_after_hold": pd12.sample_after_hold,
    }
    exec("from __future__ import annotations\nclass Harness(_Base):\n" + body, scope)
    agent = scope["Harness"]()
    agent.research_v62_breadth = v62
    agent._tick = tick
    agent.research_v623_premium_floor = False   # v6.2.3 has its own suite
    agent.research_v625_two_sided = False       # v6.2.5 has its own suite: this one is v6.2.0
    agent.research_v625_cap_pace = False
    agent.research_v626_capture_balance = False
    agent.research_v626_quote_life = False
    agent.research_v626_loss_budget = False
    agent.research_v627_balance_gate = False   # v6.2.7 has its own suite: this one is v6.2.0
    agent.research_v627_band_caps = False
    agent.research_v627_clip_bound = False
    agent.research_v627_pace_rewind = False
    agent.research_v628_rung_cap = False        # v6.2.8 has its own suite: this one is v6.2.0
    agent.research_v628_band_inventory = False
    agent.research_v628_fee_viable = False
    agent.research_v628_skew_sides = False
    agent._v628_counts = {}
    agent._v628_errors = 0
    agent.research_v6210_improve_entries = False   # v6.2.10 has its own suite: this one is v6.2.0
    agent.research_v6210_improve_exits = False
    agent._v6210_counts = {}
    agent._v6210_errors = 0
    agent._v627_counts = {}
    agent._v627_errors = 0
    agent._v626_counts = {}
    agent._v626_errors = 0
    agent._v625_counts = {}
    agent._v625_pace = {}
    agent._v625_caps_lot = None
    agent._v625_errors = 0
    agent._v62_counts = {}
    agent._v62_request = {}
    agent._v62_caps_applied = False
    agent._v62_state_reported = False
    agent._v62_errors = 0
    agent._v62_mirror = None
    agent.research_max_total_abs_base = 2.0
    agent.research_max_total_open_books = 8
    agent.research_max_active_open_books = 8
    agent.research_max_open_books = 8
    agent.max_managed_books_per_tick = 10
    agent.max_mm_books_per_tick = 6
    n = UNIVERSE if books is None else books
    for b in range(n):
        agent.accounts[b] = _Account()
    return agent


def _state(n=UNIVERSE, overrides=None):
    books = {b: _Book() for b in range(n)}
    for b, book in (overrides or {}).items():
        books[b] = book
    return _State(books)


def _rows(agent, name):
    return [p for t, p in agent.rows if t == name]


# ---- the placer

def test_touch_quotes_carry_the_frozen_entry_identity():
    agent = _agent()
    r = _Response()
    assert agent._v62_place_touch_quotes(r, _state(), 48, _Book(445.09, 445.20), LOT) == 2
    buy, sell = r.instructions
    assert (buy.direction, buy.price, buy.quantity, buy.clientOrderId) == (_OrderDirection.BUY, 445.09, 0.25, 70481)
    assert (sell.direction, sell.price, sell.quantity, sell.clientOrderId) == (_OrderDirection.SELL, 445.20, 0.25, 70482)
    for i in (buy, sell):
        assert i.postOnly is True and i.timeInForce == _TIF.GTT and i.stp == "CANCEL_BOTH"
        assert i.expiryPeriod == 3_000_000_000           # the frozen chooser's 3 s, as on the v6.1.1 arm
        assert i.leverage == 0.0 and i.delay == 0
    assert agent.mems[48].quote_count == 2 and agent.fill_quotes == [("buy", 0.0), ("sell", 0.0)]


def test_touch_quotes_respect_the_per_book_budget():
    agent = _agent()
    r = _Response()
    for _ in range(4):
        r.instructions.append(types.SimpleNamespace(type="CANCEL_ORDERS", bookId=48))
    assert agent._v62_place_touch_quotes(r, _state(), 48, _Book(), LOT) == 1   # one slot left of 5


def test_touch_quotes_skip_a_crossed_or_empty_book():
    agent = _agent()
    assert agent._v62_place_touch_quotes(_Response(), _state(), 1, _Book(204.35, 204.31), LOT) == 0
    assert agent._v62_place_touch_quotes(_Response(), _state(), 1, _Book(None, 204.31), LOT) == 0


# ---- the TTL

def test_entry_ttl_follows_the_frozen_chooser_and_its_clamp():
    agent = _agent()
    assert agent._v62_entry_ttl_ns(1, _state()) == 3_000_000_000
    agent.ttl_choice = (9000.0, "X", None)
    assert agent._v62_entry_ttl_ns(1, _state()) == 3_000_000_000      # clamped at research_ttl_max_ms
    agent.ttl_choice = (100.0, "X", None)
    assert agent._v62_entry_ttl_ns(1, _state()) == 250_000_000        # clamped at research_ttl_min_ms
    agent.ttl_choice = (None, "X", None)
    assert agent._v62_entry_ttl_ns(1, _state()) == 500_000_000        # baseline
    agent.research_enable_adaptive_ttl = False
    agent.ttl_choice = (3000.0, "X", None)
    assert agent._v62_entry_ttl_ns(1, _state()) == 500_000_000


# ---- the facts

def test_book_facts_read_the_frozen_views():
    agent = _agent()
    agent.inventory[5] = 0.25
    agent.live.add(6)
    agent.capped.add(7)
    st = _state()
    r = _Response()
    f5 = agent._v62_book_facts(5, st.books[5], st, r, LOT)
    assert (f5.best_bid, f5.best_ask, f5.net_base, f5.live_order, f5.cap_ok) == (204.31, 204.35, 0.25, False, True)
    assert agent._v62_book_facts(6, st.books[6], st, r, LOT).live_order is True
    assert agent._v62_book_facts(7, st.books[7], st, r, LOT).cap_ok is False
    f = agent._v62_book_facts(8, st.books[8], st, r, LOT)
    assert (f.quote_free, f.base_free, f.instructions_used, f.max_instructions) == (1e6, 100.0, 0, 5)
    agent.accounts.pop(9)
    assert agent._v62_book_facts(9, st.books[9], st, r, LOT).quote_free == 0.0


# ---- the pass

def test_acquire_quotes_every_valid_flat_book():
    agent = _agent()
    r = _Response()
    stats = {"instructions": 0}
    n = agent._v62_acquire(r, _state(), stats)
    assert n == 2 * UNIVERSE and len(r.instructions) == 2 * UNIVERSE
    assert stats["candidates"] == UNIVERSE and stats["quoted"] == UNIVERSE
    ids = sorted({int(i.bookId) for i in r.instructions})
    assert ids == list(range(UNIVERSE))
    assert agent._v62_request["eligible"] == UNIVERSE and agent._v62_request["placements"] == 2 * UNIVERSE
    assert agent._v62_counts["requests"] == 1 and agent._v62_counts["quoted_books"] == UNIVERSE


def test_acquire_skips_and_counts_by_reason():
    agent = _agent()
    agent.inventory[1] = 0.25          # a held lot: the exit path owns it
    agent.inventory[2] = 0.1382        # dust: the compactor owns it
    agent.live.add(3)                  # its quotes are resting
    agent.capped.add(4)                # 500k reached
    agent.accounts[5] = _Account(quote=1.0)
    st = _state(overrides={6: _Book(204.35, 204.31), 7: _Book(None, None)})
    r = _Response()
    n = agent._v62_acquire(r, st, {"instructions": 0})
    req = agent._v62_request
    assert req["NOT_FLAT"] == 2 and req["LIVE_ORDER"] == 1 and req["VOLUME_CAP"] == 1
    assert req["NO_BALANCE"] == 1 and req["CROSSED"] == 1 and req["NO_L1"] == 1
    assert req["eligible"] == UNIVERSE - 7 and n == 2 * (UNIVERSE - 7)
    assert not any(int(i.bookId) in {1, 2, 3, 4, 5, 6, 7} for i in r.instructions)


def test_acquire_is_deterministic_in_book_order():
    agent = _agent(books=3)
    r = _Response()
    agent._v62_acquire(r, _state(3), {"instructions": 0})
    assert [int(i.bookId) for i in r.instructions] == [0, 0, 1, 1, 2, 2]


# ---- the caps

def test_caps_come_from_the_universe_once():
    agent = _agent()
    agent._v62_apply_caps(_state())
    assert agent.research_max_total_abs_base == 32.0          # the BASE exposure is still the universe's
    # v6.2.6 widens the book COUNTS (only) for the guard's double count of a book that is dust on its
    # measure and flat on ours; 2 x 128 can never admit more books than exist.
    assert agent.research_max_total_open_books == 256 and agent.research_max_active_open_books == 256
    assert agent.research_max_open_books == 256 and agent.max_managed_books_per_tick == 256
    assert agent.max_mm_books_per_tick == 256
    rows = _rows(agent, "V62_CAPS")
    assert len(rows) == 1 and rows[0]["universe"] == 128 and rows[0]["before"]["research_max_total_abs_base"] == 2.0
    agent.research_max_total_abs_base = 1.0
    agent._v62_apply_caps(_state())                       # applied once per run
    assert agent.research_max_total_abs_base == 1.0 and len(_rows(agent, "V62_CAPS")) == 1


def test_caps_untouched_when_the_switch_is_off_or_no_books():
    agent = _agent(v62=False)
    agent._v62_apply_caps(_state())
    assert agent.research_max_total_abs_base == 2.0 and _rows(agent, "V62_CAPS") == []
    agent = _agent()
    agent._v62_apply_caps(_State({}))
    assert agent._v62_caps_applied is False


# ---- the mirror feed and the row

def test_mirror_is_fed_from_the_state_and_reported():
    agent = _agent(books=2)
    ev = [types.SimpleNamespace(y="t", p=204.33, q=0.25, s=1, Ma=82, Ta=-1570)] + [
        types.SimpleNamespace(y="t", p=204.33 + 0.01 * k, q=0.5, s=0, Ma=-3, Ta=-4) for k in range(16)
    ]
    st = _State({0: _Book(events=ev), 1: _Book()})
    agent._v62_feed_mirror(st)
    assert agent._v62_mirror is not None and agent._v62_mirror.uid == 82
    snap = agent._v62_mirror.snapshot()
    assert snap["fills"] == 1 and snap["prints"] == 17 and snap["books_with_fills"] == 1
    agent._v62_telemetry(st)
    row = _rows(agent, "V62_STATE")[0]
    assert row["making"]["fills"] == 1 and row["enabled"] == 1 and row["v62_version"] == br.V62_VERSION


def test_mirror_not_fed_when_off():
    agent = _agent(v62=False, books=1)
    agent._v62_feed_mirror(_state(1))
    assert agent._v62_mirror is None


def test_state_row_on_first_request_and_every_100_ticks():
    agent = _agent(books=1)
    for tick in (7, 8, 100, 101, 200):
        agent._tick = tick
        agent._v62_telemetry(_state(1))
    assert [r["tick"] for r in _rows(agent, "V62_STATE")] == [7, 100, 200]


# ------------------------------------------------------------------------------------------------
# 3. Wiring
# ------------------------------------------------------------------------------------------------

def _method(name):
    cls = next(n for n in ast.parse(SIMPLE).body
               if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    found = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(found) == 1, (name, len(found))
    return found[0]


def test_acquisition_pass_replaces_the_ranked_loop_under_the_switch():
    body = ast.get_source_segment(SIMPLE, _method("build_mm_strategy_instructions"))
    caps = body.index("self._v62_apply_caps(state)")
    sel = body.index("selected_ids = {int(x) for x in (getattr(state, \"books\", None) or {})}")
    maint = body.index("self._direct_maintain_unselected_entry_quotes(")
    acq = body.index("v62_placed = self._v62_acquire(response, state, stats)")
    frozen = body.index("# One flat-entry path. No maintenance branch and no separate alpha branch.")
    veto = body.index("# Only contract/risk safety may veto the already-decided actions here.")
    assert caps < sel < maint < acq < frozen < veto
    assert body.count("self._v62_acquire(") == 1
    # The frozen loop is the else-branch of the switch, four spaces deeper than before.
    assert "        else:\n            # One flat-entry path." in body


def test_frozen_acquisition_block_is_statement_identical_to_v611():
    try:
        old = subprocess.run(
            ["git", "-C", str(ROOT), "show", "e5ea978:agents/strategy/Strategy1_Research_Simple.py"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception:
        old = ""
    if not old:
        pytest.skip("git history not readable here")
    start = "# One flat-entry path. No maintenance branch and no separate alpha branch.\n"
    end = "# Only contract/risk safety may veto the already-decided actions here.\n"

    def block(src, indent):
        i = src.index(" " * indent + start)
        j = src.index(" " * 8 + end)          # the veto comment sits outside the switch, at 8 spaces
        lines = src[i:j].splitlines(keepends=True)
        return ast.dump(ast.parse("".join(l[indent:] if l.strip() else l for l in lines)))

    assert block(old, 8) == block(SIMPLE, 12)


def test_mirror_and_telemetry_wired():
    upd = ast.get_source_segment(SIMPLE, _method("update"))
    assert upd.index("self._v62_feed_mirror(state)") < upd.index("return super().update(state)")
    resp = ast.get_source_segment(SIMPLE, _method("respond"))
    assert resp.index("self._v611_telemetry(state)") < resp.index("self._v62_telemetry(state)")


def test_switch_defaults_on_and_version():
    init = ast.get_source_segment(SIMPLE, _method("_init_build_switches"))
    assert 'getattr(self.config, "research_v62_breadth", True)' in init
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_12"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_12"' in SIMPLE


def test_launcher_arm_params_guards_and_gate():
    arm = next(line for line in LAUNCHER.splitlines() if line.strip().startswith("strategy1_direct_v6_2_8)"))
    assert "V611_BUILD=1" in arm and "V620_BUILD=1" in arm
    assert "strategy1_direct_v6_1_1)" in LAUNCHER and "V620_BUILD=0" in LAUNCHER
    assert "research_v62_breadth=1" in LAUNCHER
    for literal in ("v62_placed = self._v62_acquire(response, state, stats)", "self._v62_apply_caps(state)",
                    "self._v62_feed_mirror(state)", "def _v62_telemetry"):
        assert f"grep -qF '{literal}'" in LAUNCHER, literal
        # v6.2.2 applies the caps a second time from update(), before the first seed (idempotent).
        expected = 2 if literal == "self._v62_apply_caps(state)" else 1
        assert SIMPLE.count(literal) == expected, literal
    assert "[preflight] v6.2 breadth PASS" in LAUNCHER
    assert "tests/test_research_v6_2_0_breadth.py" in LAUNCHER
    assert "tests/test_research_v6_2_0_making_mirror.py" in LAUNCHER
    # The slot-era PARAMS stay as they were: the caps are applied from the universe at runtime.
    assert "research_max_total_open_books=8 research_max_total_abs_base=2.0" in LAUNCHER
