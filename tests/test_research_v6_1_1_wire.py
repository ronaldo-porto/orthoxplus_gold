"""v6.1.1: the reprice seed sees the v6.1 floor, and every limit price lands on its own tick.

Measured on the v6.1.0 testnet read (UID 82, log 20260918_151435, ticks 1-1,014, 2026-09-18),
against UID 68 (v6.0.3) trading the same simulation over the same sim seconds:

* 2,944 A1.9.1 reprice cancels in 1,014 ticks (the control: 391 per 1k).  Book 81, a SHORT lot at
  204.63 held at its floor 204.32 while the bid stood at ~215.45: the floored buy was placed, seen
  by the A1.9.1.1 seed against the bare touch (~1,114 ticks "behind"), cancelled as
  STALE_BEHIND_TOUCH on the next request, re-placed -- a two-request loop on every held lot.
* 783 NET_BELOW_FLOOR cancels: the venue truncates a price's binary expansion, so a floored sell
  whose double sits below its decimal rested one tick under the floor.  Every SUBMITTED limit
  order matched to its LimitOrderPlacementEvent: testnet 1,838 of 3,915 one tick low, mainnet
  UID 34 1,804 of 3,573 -- exactly the ones whose double sits below the decimal.

These tests run the real methods in a harness, not copies of them.
"""
import ast
import math
import sys
import types
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
SIMPLE = STRATEGY_DIR / "Strategy1_Research_Simple.py"
LAUNCHER = ROOT / "run_strategy1_research_simple_multi.sh"

sys.path.insert(0, str(STRATEGY_DIR))

SRC = SIMPLE.read_text()
LAUNCHER_SRC = LAUNCHER.read_text()

import research_v611_wire as wire  # noqa: E402
from research_direct_exit import DIRECT_MAKER_EXIT_TARGET_BPS  # noqa: E402
from research_direct_exit_ledger import DirectExitLedger, RestingInventoryView, close_side_for  # noqa: E402
from research_direct_exit_refresh import (  # noqa: E402
    ABSENT_REPRICE_CANCEL, EXIT_HOLD, EXIT_REPRICE, REASON_NET_BELOW_FLOOR,
    REASON_QUEUE_PRESERVED, REASON_STALE_BEHIND_TOUCH,
    behind_ticks, classify_resting_maker_exit, forgone_edge_bps,
)
from research_unified_exit import completion_net_bps as unified_completion_net_bps  # noqa: E402
from research_v61_lot_floor import (  # noqa: E402
    fifo_close_net_bps, floor_price, floored_close_price, head_lot,
)

MS = 1_000_000
PUBLISH_NS = 1000 * MS
T0 = 46_000_000_000_000
BOOK = 81

# Book 81 at tick 901 of the UID 82 log: a SHORT lot of 0.25 opened at 204.63 with a 0.0277 quote
# opening fee, live maker fee 8.66 bps, floor 204.32, the bid ~215.45.
SHORT_LOT = (T0 - 900 * 10**9, 0.25, 204.63, 0.0277)
SHORT_FEE_BPS = 8.660147387112895
SHORT_FLOOR = 204.32
BID_81, ASK_81 = 215.45, 215.50
# A long held under water: 0.25 bought at 264.67 (book 94), live maker fee 1.7 bps, ask ~250.93.
LONG_LOT = (T0 - 900 * 10**9, 0.25, 264.67, 0.0333)
LONG_FEE_BPS = 1.7
BID_94, ASK_94 = 250.90, 250.93


# ------------------------------------------------------------------------------------------------
# 1. The venue's price truncation and the wire price
# ------------------------------------------------------------------------------------------------

def _decimal(p, d=2):
    return Decimal(p).quantize(Decimal(1).scaleb(-d))


def test_every_grid_price_from_100_to_500_lands_on_its_own_tick():
    misplaced_as_sent = lifted = 0
    for cents in range(10_000, 50_001):
        p = round(cents / 100, 2)
        want = Decimal(cents).scaleb(-2)
        if Decimal(repr(wire.venue_placed_price(p, 2))) != want:
            misplaced_as_sent += 1
        w = wire.wire_price(p, 2)
        lifted += w != p
        assert Decimal(repr(wire.venue_placed_price(w, 2))) == want, p
    share = misplaced_as_sent / 40_001
    # 47-50% of the live placements on both nets; the model gives 48.0% over this range.
    assert 0.40 < share < 0.56
    assert lifted == misplaced_as_sent


@pytest.mark.parametrize("sent,placed", [
    (228.13, 228.12), (445.09, 445.08), (199.89, 199.88),       # testnet buys, tick 1
    (445.2, 445.19), (234.88, 234.87), (200.44, 200.43),        # testnet sells, tick 1
    (204.32, 204.31),                                           # book 81's floored buy
])
def test_the_logged_placements_are_reproduced_and_repaired(sent, placed):
    assert wire.venue_placed_price(sent, 2) == placed
    assert wire.venue_placed_price(wire.wire_price(sent, 2), 2) == sent


def test_a_lift_is_exactly_one_ulp_and_idempotent():
    for p in (228.13, 204.32, 445.2):
        w = wire.wire_price(p, 2)
        assert w == math.nextafter(p, math.inf)
        assert wire.wire_price(w, 2) == w


@pytest.mark.parametrize("p", [204.31, 250.25, 100.5, 300.0])
def test_a_price_at_or_above_its_decimal_is_sent_unchanged(p):
    assert Decimal(p) >= Decimal(repr(p))
    assert wire.wire_price(p, 2) == p


@pytest.mark.parametrize("p", [204.325, 0.0, -1.0, float("inf"), None, "x"])
def test_off_grid_and_invalid_prices_pass_through(p):
    out = wire.wire_price(p, 2)
    if isinstance(p, float) and math.isnan(p):
        assert math.isnan(out)
    else:
        assert out == p or (out is p)


def test_nan_passes_through():
    assert math.isnan(wire.wire_price(float("nan"), 2))


def test_other_price_grids():
    assert wire.venue_placed_price(wire.wire_price(1.001, 3), 3) == 1.001
    assert wire.wire_price(205.0, 0) == 205.0


def test_lift_instruction_prices_touches_limit_orders_only():
    ins = [
        {"type": "PLACE_ORDER_LIMIT", "price": 228.13, "direction": 0},
        {"type": "PLACE_ORDER_LIMIT", "price": 445.2, "direction": 1},
        {"type": "PLACE_ORDER_LIMIT", "price": 250.25, "direction": 1},
        types.SimpleNamespace(type="PLACE_ORDER_LIMIT", price=204.32, direction=0),
        {"type": "PLACE_ORDER_MARKET", "direction": 1, "quantity": 0.25},
        {"type": "CANCEL_ORDERS", "cancellations": []},
    ]
    moved = wire.lift_instruction_prices(ins, 2)
    assert moved == {"buy": 2, "sell": 1, "other": 0}
    assert ins[0]["price"] == math.nextafter(228.13, math.inf)
    assert ins[2]["price"] == 250.25
    assert ins[3].price == math.nextafter(204.32, math.inf)
    assert "price" not in ins[4] and "price" not in ins[5]
    assert wire.lift_instruction_prices(ins, 2) == {"buy": 0, "sell": 0, "other": 0}


def test_a_lifted_price_survives_the_json_hop_to_the_simulator():
    """The validator answers the simulator through FastAPI's JSONResponse (json.dumps, shortest
    round-trip repr); the miner hop is msgspec.  Both keep the exact double."""
    import json
    w = wire.wire_price(204.32, 2)
    assert json.loads(json.dumps({"price": w}))["price"] == w
    assert wire.venue_placed_price(json.loads(json.dumps(w)), 2) == 204.32


# ------------------------------------------------------------------------------------------------
# 2. The reprice seed, in the real methods
# ------------------------------------------------------------------------------------------------

WANTED = {
    "_a191_enabled", "_a191_verdict_store", "_a191_live_exit_row",
    "_a191_decide", "_a191_service_reprice_cancels",
    "_a19_is_entry_quote_row", "_direct_entry_quote_client_ids",
    "_a19_resting_net_bps", "_a19_tick_size", "_a19_ledger_ref",
    "_a19_note_exit_cancel", "_direct_account_orders",
    "_a191_check_activation", "_a19_runtime_phase", "_a19_behaviour_change",
    "_v61_on", "_v61_positions", "_v61_floor_for", "_v61_price_decimals",
    "_v611_floor_reprice_on", "_v611_price_lift_on", "_v611_count", "_v611_seed_comparand",
    "_v611_lift_outgoing_prices", "_v611_telemetry",
    "_v62_on", "_v623_on", "_v623_count", "_v623_lifted", "_v623_resting_floor_bps",
}


def _load():
    cls = next(n for n in ast.parse(SRC).body
               if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in WANTED]
    assert {m.name for m in methods} == WANTED, WANTED ^ {m.name for m in methods}
    ns = {
        "math": math, "Any": object,
        "DirectExitLedger": DirectExitLedger,
        "RestingInventoryView": RestingInventoryView,
        "close_side_for": close_side_for,
        "EXIT_HOLD": EXIT_HOLD, "EXIT_REPRICE": EXIT_REPRICE,
        "REASON_QUEUE_PRESERVED": REASON_QUEUE_PRESERVED,
        "ABSENT_REPRICE_CANCEL": ABSENT_REPRICE_CANCEL,
        "behind_ticks": behind_ticks, "forgone_edge_bps": forgone_edge_bps,
        "classify_resting_maker_exit": classify_resting_maker_exit,
        "unified_completion_net_bps": unified_completion_net_bps,
        "v61_head_lot": head_lot,
        "v61_fifo_close_net_bps": fifo_close_net_bps,
        "v61_floor_price": floor_price,
        "v61_floored_close_price": floored_close_price,
        "v611_lift_instruction_prices": wire.lift_instruction_prices,
        "V611_STATE_EVERY_TICKS": wire.V611_STATE_EVERY_TICKS,
        "V611_VERSION": wire.V611_VERSION,
        "DIRECT_MAKER_EXIT_TARGET_BPS": DIRECT_MAKER_EXIT_TARGET_BPS,
        "DIRECT_A19_PHASE_BEHAVIOURAL": "B_QUEUE_PRESERVING_EXIT",
        "DIRECT_A19_PHASE_SHADOW": "A_SHADOW_MEASUREMENT",
        "SIMPLE_ENGINE_VERSION": "strategy1_direct_v6_1_1",
        "DIRECT_EXIT_REFRESH_VERSION": "direct_exit_refresh_v4_16_2_a1_9_2",
        "DIRECT_EXIT_LEDGER_VERSION": "direct_exit_ledger_v4_16_2_a1_9_0_3",
        "DIRECT_A19_PHASE_B_EVENTS": ("A19_QUEUE_HOLD", "A19_EXIT_REPRICE_CANCEL", "A19_REPRICE_BUDGET_BLOCK"),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
                 "<v611>", "exec"), ns)
    return {n: ns[n] for n in WANTED}


class _Level:
    def __init__(self, price):
        self.price = price


class _Book:
    def __init__(self, bid, ask):
        self.bids = [_Level(bid)]
        self.asks = [_Level(ask)]


class _State:
    def __init__(self, books, timestamp=T0):
        self.books = books
        self.timestamp = timestamp
        self.config = types.SimpleNamespace(priceDecimals=2, publish_interval=PUBLISH_NS)


class _Response:
    def __init__(self, instructions=None):
        self.cancels = []
        self.instructions = list(instructions or [])

    def cancel_orders(self, *, book_id, order_ids, delay=0):
        self.cancels.append((int(book_id), list(order_ids)))


class _Agent:
    research_profitable_exit_ttl_ms = 4000.0
    research_v623_premium_floor = False   # v6.2.3 (the premium-scoped floor) has its own suite
    research_profitable_exit_min_net_bps = 0.0
    research_profitable_exit_reprice_ticks = 3.0
    research_a191_queue_preservation_enabled = True
    _research_market_regime = "NORMAL"
    max_instructions_per_book = 5
    A19_CANCEL_WATCH_MAX_TICKS = 10
    A19_CANCEL_ACK_BUDGET_TICKS = 2
    A19_CANCEL_MEMO_MAX = 2048
    A191_MIN_REMAINING_TTL_MS = 1000.0
    A191_ACTIVATION_ALARM_CANDIDATES = 20

    def __init__(self, maker_fee_bps):
        self._tick = 1
        self._a19_ledger = DirectExitLedger()
        self._a19_cancel_watch = {}
        self._a19_cancel_reason = {}
        self._a191_verdicts = {}
        self._a191_verdict_tick = -1
        self.accounts = {}
        self.positions = {}
        self.events = []
        self._book_instructions = 0
        self._a191_activation_banner_emitted = False
        self._a191_activation_alarm_emitted = False
        self._a191_reprice_release = {}
        for n in ("_a191_holds", "_a191_reprice_cancels", "_a191_reprice_deferred_ttl",
                  "_a191_reprice_deferred_budget", "_a191_placements_suppressed",
                  "_a191_cancel_emit_failures", "_a191_postpass_cancels",
                  "_a191_reprice_candidates"):
            setattr(self, n, 0)
        self.research_v61_no_loss = True
        self.research_v611_floor_reprice = True
        self.research_v611_price_lift = True
        self._v611_counts = {}
        self._v611_state_reported = False
        self._v611_errors = 0
        self._open_positions = {}
        self._maker_fee_bps = maker_fee_bps

    def _position_tracker_snapshot(self, book_id):
        net, vwap = self.positions.get(int(book_id), (0.0, None))
        return types.SimpleNamespace(net_qty=net, vwap_entry=vwap)

    def _research_live_fee_bps(self, book_id, *, is_maker=True, fallback_bps=None):
        return self._maker_fee_bps if is_maker else 5.0

    def _count_book_instructions(self, response, book_id):
        return self._book_instructions

    def _emit(self, event_type, force=False, **payload):
        self.events.append((event_type, payload))

    def rows(self, name):
        return [p for e, p in self.events if e == name]


_STATIC = {"_a19_tick_size", "_direct_entry_quote_client_ids"}
for _n, _f in _load().items():
    setattr(_Agent, _n, staticmethod(_f) if _n in _STATIC else _f)


def _held_short(resting_price, *, v611=True, v61=True, lot=True):
    """Book 81: a short held at its floor, one resting floored buy, the bid far above."""
    a = _Agent(SHORT_FEE_BPS)
    a.research_v611_floor_reprice = v611
    a.research_v61_no_loss = v61
    a.positions[BOOK] = (-0.25, SHORT_LOT[2])
    if lot:
        a._open_positions[BOOK] = {"longs": [], "shorts": [SHORT_LOT]}
    a._a19_ledger.note_accepted(
        order_id=1417196, book_id=BOOK, side=0, price=resting_price, quantity=0.25,
        timestamp_ns=T0, tick=1, action="AGGRESSIVE_MAKER_EXIT", client_id=None,
    )
    return a, _State({BOOK: _Book(BID_81, ASK_81)})


def _held_long(resting_price, floor):
    a = _Agent(LONG_FEE_BPS)
    a.positions[BOOK] = (0.25, LONG_LOT[2])
    a._open_positions[BOOK] = {"longs": [LONG_LOT], "shorts": []}
    a._a19_ledger.note_accepted(
        order_id=77, book_id=BOOK, side=1, price=resting_price, quantity=0.25,
        timestamp_ns=T0, tick=1, action="AGGRESSIVE_MAKER_EXIT", client_id=None,
    )
    return a, _State({BOOK: _Book(BID_94, ASK_94)})


def _inv(a):
    return RestingInventoryView.from_tracker(a._position_tracker_snapshot(BOOK))


def test_the_live_floor_is_reproduced():
    f = floor_price(SHORT_LOT, close_fee_bps=SHORT_FEE_BPS, long_position=False,
                    target_bps=DIRECT_MAKER_EXIT_TARGET_BPS, price_decimals=2)
    assert f == SHORT_FLOOR
    a, state = _held_short(SHORT_FLOOR)
    assert a._v61_floor_for(BOOK, False, state=state) == SHORT_FLOOR


def test_v610_tears_the_floored_exit_down():
    """The defect, reproduced: against the bare bid the floored buy is ~1,113 ticks stale."""
    a, state = _held_short(SHORT_FLOOR, v611=False)
    v = a._a191_decide(state, BOOK, _inv(a), 0.25, None, None)
    assert v["decision"] == EXIT_REPRICE and v["reason"] == REASON_STALE_BEHIND_TOUCH
    assert v["desired_price"] == BID_81
    assert v["drift_ticks"] == pytest.approx(1113.0)
    r = _Response()
    assert a._a191_service_reprice_cancels(r, state) == 1
    assert r.cancels == [(BOOK, [1417196])]


def test_v611_holds_the_floored_exit():
    a, state = _held_short(SHORT_FLOOR)
    v = a._a191_decide(state, BOOK, _inv(a), 0.25, None, None)
    assert v["decision"] == EXIT_HOLD and v["reason"] == REASON_QUEUE_PRESERVED
    assert v["desired_price"] == SHORT_FLOOR and v["drift_ticks"] == pytest.approx(0.0)
    assert a._v611_counts == {"seed_floored": 1}


def test_v611_post_pass_sends_no_cancel_for_the_held_lot():
    a, state = _held_short(SHORT_FLOOR)
    r = _Response()
    assert a._a191_service_reprice_cancels(r, state) == 0
    assert r.cancels == [] and a.rows("A19_EXIT_REPRICE_CANCEL") == []


def test_the_truncated_buy_one_tick_under_the_floor_is_held_too():
    """204.31 is what the venue placed for 204.32; a buy one tick lower nets more, drifts 1 < 3."""
    a, state = _held_short(204.31)
    v = a._a191_decide(state, BOOK, _inv(a), 0.25, None, None)
    assert v["decision"] == EXIT_HOLD and v["drift_ticks"] == pytest.approx(1.0)


def test_a_buy_resting_well_under_the_floor_still_reprices_up_to_it():
    a, state = _held_short(203.00)
    v = a._a191_decide(state, BOOK, _inv(a), 0.25, None, None)
    assert v["decision"] == EXIT_REPRICE and v["reason"] == REASON_STALE_BEHIND_TOUCH
    assert v["desired_price"] == SHORT_FLOOR and v["drift_ticks"] == pytest.approx(132.0)


def test_once_the_market_comes_back_the_comparand_is_the_touch_again():
    a, _ = _held_short(SHORT_FLOOR)
    state = _State({BOOK: _Book(204.00, 204.05)})
    v = a._a191_decide(state, BOOK, _inv(a), 0.25, None, None)
    assert v["desired_price"] == 204.00 and v["decision"] == EXIT_HOLD
    assert a._v611_counts == {}


def test_without_a_lot_or_with_v61_off_the_seed_is_the_bare_touch():
    """v6.1.0 behaviour exactly.  Without the lot the resting net falls back to vwap_entry with
    today's 8.66 bps fee on both legs (-2.2 bps at 204.32), so it is NET_BELOW_FLOOR first."""
    for kwargs in ({"lot": False}, {"v61": False}):
        a, state = _held_short(SHORT_FLOOR, **kwargs)
        v = a._a191_decide(state, BOOK, _inv(a), 0.25, None, None)
        assert v["desired_price"] == BID_81 and v["decision"] == EXIT_REPRICE, kwargs
        assert v["reason"] == REASON_NET_BELOW_FLOOR, kwargs
        assert a._v611_counts == {}, kwargs


def test_an_explicit_desired_price_is_not_refloored():
    """The placement path floors before it calls the classifier; the seed fix touches only the
    no-desired-price branch."""
    a, state = _held_short(SHORT_FLOOR)
    v = a._a191_decide(state, BOOK, _inv(a), 0.25, BID_81, "AGGRESSIVE_MAKER_EXIT")
    assert v["desired_price"] == BID_81
    assert a._v611_counts == {}


def test_a_held_long_at_its_floor_is_held_and_one_tick_under_is_net_below_floor():
    """Why the price lift is needed: the classifier rightly refuses a sell under the floor, so a
    truncated floored sell is cancelled every other request until the wire puts it on the floor."""
    floor = floor_price(LONG_LOT, close_fee_bps=LONG_FEE_BPS, long_position=True,
                        target_bps=DIRECT_MAKER_EXIT_TARGET_BPS, price_decimals=2)
    a, state = _held_long(floor, floor)
    v = a._a191_decide(state, BOOK, _inv(a), 0.25, None, None)
    assert v["decision"] == EXIT_HOLD and v["desired_price"] == floor
    a, state = _held_long(round(floor - 0.01, 2), floor)
    v = a._a191_decide(state, BOOK, _inv(a), 0.25, None, None)
    assert v["decision"] == EXIT_REPRICE and v["reason"] == REASON_NET_BELOW_FLOOR


# ------------------------------------------------------------------------------------------------
# 3. The price lift and the state row, in the real methods
# ------------------------------------------------------------------------------------------------

def test_the_respond_post_pass_lifts_limit_prices_and_counts_them():
    a = _Agent(0.0)
    ins = [types.SimpleNamespace(type="PLACE_ORDER_LIMIT", price=204.32, direction=0),
           types.SimpleNamespace(type="PLACE_ORDER_LIMIT", price=445.2, direction=1),
           types.SimpleNamespace(type="PLACE_ORDER_LIMIT", price=204.31, direction=0)]
    state = _State({})
    assert a._v611_lift_outgoing_prices(_Response(ins), state) == 2
    assert [wire.venue_placed_price(i.price, 2) for i in ins] == [204.32, 445.2, 204.31]
    assert a._v611_counts == {"lifted_buy": 1, "lifted_sell": 1}


def test_the_price_lift_switch_restores_v610():
    a = _Agent(0.0)
    a.research_v611_price_lift = False
    ins = [types.SimpleNamespace(type="PLACE_ORDER_LIMIT", price=204.32, direction=0)]
    assert a._v611_lift_outgoing_prices(_Response(ins), _State({})) == 0
    assert ins[0].price == 204.32


def test_v611_state_row_on_first_request_and_every_100_ticks():
    a = _Agent(0.0)
    a._v611_counts = {"seed_floored": 3, "lifted_buy": 5}
    a._tick = 7
    a._v611_telemetry(_State({}))
    a._tick = 8
    a._v611_telemetry(_State({}))
    a._tick = 100
    a._v611_telemetry(_State({}))
    rows = a.rows("V611_STATE")
    assert [r["tick"] for r in rows] == [7, 100]
    assert rows[0]["counts"] == {"seed_floored": 3, "lifted_buy": 5}
    assert rows[0]["floor_reprice"] == 1 and rows[0]["price_lift"] == 1
    assert rows[0]["v611_version"] == wire.V611_VERSION


# ------------------------------------------------------------------------------------------------
# 4. Wiring: where the two fixes sit, and the launcher
# ------------------------------------------------------------------------------------------------

def _method(name):
    cls = next(n for n in ast.parse(SRC).body
               if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    found = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(found) == 1, (name, len(found))
    return found[0]


def test_the_seed_comparand_is_floored_only_in_the_no_desired_price_branch():
    decide = _method("_a191_decide")
    branches = [
        n for n in ast.walk(decide)
        if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
        and isinstance(n.test.left, ast.Name) and n.test.left.id == "desired_price"
        and isinstance(n.test.ops[0], ast.Is)
    ]
    calls_in = {
        c.func.attr for b in branches for c in ast.walk(b)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
    }
    assert "_v611_seed_comparand" in calls_in
    all_calls = [c for c in ast.walk(decide) if isinstance(c, ast.Call)
                 and isinstance(c.func, ast.Attribute) and c.func.attr == "_v611_seed_comparand"]
    assert len(all_calls) == 1


def test_the_price_lift_runs_on_the_final_placement_set():
    body = ast.get_source_segment(SRC, _method("respond"))
    built = body.index("response = super().respond(state) if quiet is None else quiet")
    snap = body.index("self._a196_snap_outgoing_quantities(response, state)")
    lift = body.index("self._v611_lift_outgoing_prices(response, state)")
    orphan = body.index("self._a195_cancel_orphan_orders(response, state)")
    assert built < snap < lift < orphan
    assert body.count("self._v611_lift_outgoing_prices(response, state)") == 1
    assert "self._v611_telemetry(state)" in body


def test_the_switches_default_on():
    init = ast.get_source_segment(SRC, _method("_init_build_switches"))
    assert 'getattr(self.config, "research_v611_floor_reprice", True)' in init
    assert 'getattr(self.config, "research_v611_price_lift", True)' in init


def test_version_and_launcher_arm():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_7"' in SRC
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_7"' in SRC
    arm = next(line for line in LAUNCHER_SRC.splitlines() if line.strip().startswith("strategy1_direct_v6_1_1)"))
    assert "V610_BUILD=1" in arm and "V611_BUILD=1" in arm
    assert "strategy1_direct_v6_1_0)" in LAUNCHER_SRC
    assert "V611_BUILD=0" in LAUNCHER_SRC


def test_launcher_params_guards_and_gate():
    assert "research_v611_floor_reprice=1 research_v611_price_lift=1" in LAUNCHER_SRC
    for literal in ("desired_price = self._v611_seed_comparand(",
                    "self._v611_lift_outgoing_prices(response, state)",
                    "def _v611_telemetry"):
        assert f"grep -qF '{literal}'" in LAUNCHER_SRC, literal
        assert SRC.count(literal) == 1, literal
    assert "[preflight] v6.1.1 floor-aware reprice seed PASS" in LAUNCHER_SRC
    assert "[preflight] v6.1.1 price lift PASS" in LAUNCHER_SRC
    assert "tests/test_research_v6_1_1_wire.py" in LAUNCHER_SRC
