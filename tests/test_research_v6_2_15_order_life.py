"""v6.2.15: an order lives as long as its reason to rest, a book side is owned by its own order, and a position whose
mid mark is inside ABSOLUTE_PROTECTION keeps the chain's own taker.

Mainnet UID 94 (v6.2.14, ticks 1-1,000, recorder with the full L1 order queue): the winners' maker orders fill at a
median age of 7.0-8.0 s, ours at 0.7 s because every order died at the 4-s GTT; of our early-cancelled entries alone at
their price, 40 / 66 / 79% would have filled within 10 / 30 / 60 s; ~40 books per state were refused because one live
order owned the whole book; round trips older than 60 s (13.9%) carried -15.8 of the -13.4 bps round-trip mean.
"""
import ast
import sys
import textwrap
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v6215_order_life as ol  # noqa: E402
import research_v62_breadth as br  # noqa: E402
import research_v625_cap_paced as cp  # noqa: E402
from research_direct_exit_ledger import (  # noqa: E402
    LEDGER_REMOVED_TTL_SWEEP, LEDGER_SWEEP_GRACE_MS, DirectExitLedger,
)
from research_direct_exit_refresh import ABSENT_ENTRY_QUOTE_CANCEL, ABSENT_REPRICE_CANCEL  # noqa: E402
from research_direct_postfill_protection import postfill_protect_eligible  # noqa: E402
from research_position_exit import BAND_ABSOLUTE, classify_risk_band  # noqa: E402
from _harness import extractor  # noqa: E402
import test_research_v6_2_14_touch_life as t14  # noqa: E402
import test_research_v6_2_5_cap_paced as c5  # noqa: E402
import test_research_v6_2_0_breadth as b0  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
NOW = t14.NOW


def _bind(cls, names, ns):
    for name in names:
        exec(compile(ast.Module(body=[ast.parse(_simple(name)).body[0]], type_ignores=[]), "<v6215>", "exec"), ns)
        setattr(cls, name, ns[name])
    return cls


# ---- 1. the rules ------------------------------------------------------------------------------------------------

def test_the_backstop_is_the_validators_presence_window_not_a_strategy_clock():
    assert ol.PRESENCE_WINDOW_QUERIES == 50 and ol.STATE_INTERVAL_NS == 1_000_000_000
    assert ol.BACKSTOP_NS == 50_000_000_000 and ol.BACKSTOP_MS == 50_000.0
    assert ol.life_ms(True, 4000.0, 3000.0) == 50_000.0
    assert ol.life_ms(False, 4000.0, 3000.0) == 4000.0
    assert ol.life_ms(False, None, 3000.0) == 3000.0 and ol.life_ms(False, "x", 3000.0) == 3000.0


def test_the_ledger_sweep_outlasts_the_backstop_only_under_s1():
    assert ol.sweep_grace_ms(False) == LEDGER_SWEEP_GRACE_MS
    assert ol.sweep_grace_ms(True) == ol.BACKSTOP_MS + LEDGER_SWEEP_GRACE_MS > ol.BACKSTOP_MS


def test_s1_owns_the_touch_entry_and_exit_families_only():
    assert ol.owned_family(70031) and ol.owned_family(71272)          # entries 70000 + 10 * book + 1|2
    assert ol.owned_family(80052) and ol.owned_family(81272)          # exits 80000 + 10 * book + 1|2
    assert not ol.owned_family(91031)                                  # compaction keeps its own life
    assert not ol.owned_family(None) and not ol.owned_family("x") and not ol.owned_family(0)


def test_only_a_resting_limit_with_an_expiry_gets_the_backstop():
    tif = types.SimpleNamespace(name="GTT")
    assert ol.is_gtt_limit("PLACE_ORDER_LIMIT", 1) and ol.is_gtt_limit("PLACE_ORDER_LIMIT", tif)
    assert ol.is_gtt_limit("PLACE_ORDER_LIMIT", "GTT")
    assert not ol.is_gtt_limit("PLACE_ORDER_MARKET", 1)
    assert not ol.is_gtt_limit("PLACE_ORDER_LIMIT", "IOC") and not ol.is_gtt_limit("PLACE_ORDER_LIMIT", 0)
    assert ol.backstop_expiry(4_000_000_000) == ol.BACKSTOP_NS and ol.backstop_expiry(None) == ol.BACKSTOP_NS
    assert ol.backstop_expiry(ol.BACKSTOP_NS) is None


def _ledger_with(*ages_ms):
    ledger = DirectExitLedger()
    for oid, age in enumerate(ages_ms, 1):
        ledger.note_accepted(order_id=oid, book_id=3, side=0, price=100.0, quantity=0.25,
                             timestamp_ns=NOW - int(age * 1e6), tick=1)
    return ledger


def test_the_grace_sweep_keeps_what_the_frozen_sweep_drops_and_speaks_its_vocabulary():
    frozen, graced = _ledger_with(20_000, 70_000), _ledger_with(20_000, 70_000)
    assert frozen.sweep(NOW) == 2                                       # the frozen 15 s drops a live 20-s order
    assert ol.sweep_with_grace(graced, NOW, ol.sweep_grace_ms(True)) == 1
    assert sorted(graced.orders) == [1] and graced.removal_cause(2) == LEDGER_REMOVED_TTL_SWEEP
    assert graced.swept == 1


def test_the_grace_sweep_resets_on_a_simulation_restart_like_the_ledger():
    ledger = _ledger_with(1_000)
    assert ol.sweep_with_grace(ledger, NOW - 10_000_000_000, 65_000.0) == 1 and not ledger.orders
    assert ol.sweep_with_grace(ledger, 0, 65_000.0) == 0 and ol.sweep_with_grace(ledger, "x", 65_000.0) == 0


def test_sides_are_read_the_way_the_venue_sends_them():
    assert ol.order_side_token(0) == ol.order_side_token("buy") == ol.order_side_token("BID") == ol.SIDE_BUY
    assert ol.order_side_token(1) == ol.order_side_token("sell") == ol.order_side_token("ask") == ol.SIDE_SELL
    assert ol.order_side_token(types.SimpleNamespace(name="SELL")) == ol.SIDE_SELL
    assert ol.order_side_token(None) == "" and ol.order_side_token(7) == ""
    assert ol.reducing_side(0.25) == ol.SIDE_SELL and ol.reducing_side(-0.25) == ol.SIDE_BUY


def test_a_live_side_is_owned_and_an_unreadable_side_owns_the_whole_book():
    assert ol.live_sides([0], []) == {"buy"} and ol.live_sides([], ["sell"]) == {"sell"}
    assert ol.live_sides([0], ["sell"]) == {"buy", "sell"} and ol.live_sides([], []) == frozenset()
    assert ol.live_sides([None], []) == {"buy", "sell"}


def test_marking_refuses_only_an_owned_side_that_would_otherwise_quote():
    sides = {"buy": "OK", "sell": "EXIT_SIDE"}
    out, n = ol.mark_live_sides(sides, {"buy"}, ok_token="OK", live_token="LIVE_ORDER")
    assert out == {"buy": "LIVE_ORDER", "sell": "EXIT_SIDE"} and n == 1 and sides["buy"] == "OK"
    out, n = ol.mark_live_sides(sides, {"sell"}, ok_token="OK", live_token="LIVE_ORDER")
    assert out == sides and n == 0


def test_the_stop_is_the_frozen_corridors_absolute_band_on_the_mid_mark():
    for mark in (-60.0, -25.01, -25.0, -24.99, -18.0, -8.0, 0.0, 12.0):
        assert ol.stop_eligible(mark) == postfill_protect_eligible(mark) == (classify_risk_band(mark) == BAND_ABSOLUTE)
    assert ol.stop_eligible(-25.01) and not ol.stop_eligible(-25.0) and not ol.stop_eligible(None)
    assert ol.stop_mark_bps(net_base=0.25, vwap_entry=300.0, mid=299.2) == pytest.approx(-26.6667, abs=1e-3)
    assert ol.stop_mark_bps(net_base=-0.25, vwap_entry=300.0, mid=300.8) == pytest.approx(-26.6667, abs=1e-3)
    assert ol.stop_mark_bps(net_base=0.0, vwap_entry=300.0, mid=299.2, fallback=-3.0) == -3.0


def test_the_version_is_declared():
    assert ol.V6215_ORDER_LIFE_VERSION == "order_life_v6_2_15" and ol.STOP_REASON == "V6215_LOSS_STOP"


# ---- 2. S1 + S3 in the touch-life post-pass -----------------------------------------------------------------------

class _StopLifeAgent(t14._LifeAgent):
    def __init__(self, *, life=True, stop=False, stop_books=(), net=None, **kwargs):
        super().__init__(net=net, **kwargs)
        self.research_v6215_order_life = life
        self.research_v6215_loss_stop = stop
        self._v6215_order_life_ms = ol.BACKSTOP_MS if life else None
        self._v6215_counts, self._v6215_errors = {}, 0
        self._v6215_stop_books = {int(b): self._tick for b in stop_books}


def _stop_life_agent(**kwargs):
    ns = {
        "V6214_SIDE_BUY": t14.tl.SIDE_BUY, "v6214_is_adding": t14.tl.is_adding,
        "v6214_life_verdict": t14.tl.life_verdict, "v6214_others_touch": t14.tl.others_touch,
        "v6214_exit_client_ids": t14.tl.exit_client_ids, "v62_entry_client_ids": br.entry_client_ids,
        "V6214_CANCEL_BEHIND": t14.tl.CANCEL_BEHIND, "V6214_CANCEL_THIN": t14.tl.CANCEL_THIN,
        "V6214_CANCEL_HIT": t14.tl.CANCEL_HIT,
        "ABSENT_ENTRY_QUOTE_CANCEL": ABSENT_ENTRY_QUOTE_CANCEL, "ABSENT_REPRICE_CANCEL": ABSENT_REPRICE_CANCEL,
        "resolve_book_from_state_mapping": t14.cg.resolve_book_from_state_mapping,
        "V6215_STOP_REASON": ol.STOP_REASON, "v6215_order_side_token": ol.order_side_token,
        "v6215_reducing_side": ol.reducing_side,
    }
    cls = type("S", (_StopLifeAgent,), {})
    _bind(cls, ("_v6214_service_touch_life", "_v6214_count", "_v6214_cancelled_ids", "_v6214_others_depth",
                "_a19_is_entry_quote_row", "_v6215_count", "_v6215_stop_active"), ns)
    return cls(**kwargs)


def test_s1_manages_an_order_older_than_the_frozen_ttl():
    # a 10-s-old exit the touch moved away from: under S1 it is still ours and is cancelled; without S1 the pass
    # presumes it expired at 4 s and leaves it resting behind the touch
    for life, expect in ((True, {(3, 11)}), (False, set())):
        agent = _stop_life_agent(life=life, net={3: 0.25})
        t14._rest(agent, 11, 3, 1, 100.02, cid=80032, age_ms=10_000.0)
        resp = t14._Resp()
        agent._v6214_service_touch_life(resp, t14._state({3: t14._touch_book()}))
        assert t14._cancelled(resp) == expect


def test_a_row_past_the_backstop_is_left_to_the_venue():
    agent = _stop_life_agent(net={3: 0.25})
    t14._rest(agent, 11, 3, 1, 100.02, cid=80032, age_ms=ol.BACKSTOP_MS + 1_000.0)
    resp = t14._Resp()
    agent._v6214_service_touch_life(resp, t14._state({3: t14._touch_book()}))
    assert t14._cancelled(resp) == set()


def test_s3_cancels_the_exit_resting_at_the_touch_beside_a_stopped_position():
    agent = _stop_life_agent(stop=True, stop_books=(3,), net={3: 0.25})
    t14._rest(agent, 11, 3, 1, 100.01, cid=80032)           # the exit, AT the touch: the touch rule keeps it
    t14._rest(agent, 12, 3, 0, 100.00, cid=70031)           # the adding entry on the other side
    resp = t14._Resp()
    agent._v6214_service_touch_life(resp, t14._state({3: t14._touch_book(bid_q=9.0, ask_q=1.0)}))
    assert t14._cancelled(resp) == {(3, 11)}
    assert agent.notes == [(3, [11], ABSENT_REPRICE_CANCEL)]
    assert agent._v6215_counts == {"stop_exit_cancels": 1} and "cancel_other" not in agent._v6214_counts


def test_without_a_stop_the_exit_at_the_touch_rests_and_off_the_stop_is_never_consulted():
    agent = _stop_life_agent(stop=True, stop_books=(), net={3: 0.25})
    t14._rest(agent, 11, 3, 1, 100.01, cid=80032)
    resp = t14._Resp()
    agent._v6214_service_touch_life(resp, t14._state({3: t14._touch_book()}))
    assert t14._cancelled(resp) == set()

    agent = _stop_life_agent(stop=False, net={3: 0.25})
    type(agent)._v6215_stop_active = lambda self, book_id: pytest.fail("the stop is read only under its switch")
    t14._rest(agent, 11, 3, 1, 100.01, cid=80032)
    resp = t14._Resp()
    agent._v6214_service_touch_life(resp, t14._state({3: t14._touch_book()}))
    assert t14._cancelled(resp) == set()


def test_a_stop_from_an_earlier_request_does_not_cancel():
    agent = _stop_life_agent(stop=True, stop_books=(3,), net={3: 0.25})
    agent._v6215_stop_books[3] = agent._tick - 1
    t14._rest(agent, 11, 3, 1, 100.01, cid=80032)
    resp = t14._Resp()
    agent._v6214_service_touch_life(resp, t14._state({3: t14._touch_book()}))
    assert t14._cancelled(resp) == set()


# ---- 3. S3 marks, the rewrite and the executor ---------------------------------------------------------------------

class _NoteAgent:
    def __init__(self, stop=True, tick=9):
        self.research_v6215_loss_stop = stop
        self._tick = tick
        self._v6215_counts, self._v6215_errors, self._v6215_stop_books = {}, 0, {}


def _note_agent(**kwargs):
    ns = {"v6215_stop_mark_bps": ol.stop_mark_bps, "v6215_stop_eligible": ol.stop_eligible}
    return _bind(type("N", (_NoteAgent,), {}), ("_v6215_note_stop", "_v6215_stop_active", "_v6215_count"), ns)(
        **kwargs)


def _inv(net, entry):
    return types.SimpleNamespace(net_base=net, vwap_entry=entry, unrealized_bps=None)


def test_a_position_past_the_boundary_is_stopped_for_this_request_only():
    agent = _note_agent()
    assert agent._v6215_note_stop(3, _inv(0.25, 300.0), 299.2) and agent._v6215_stop_active(3)
    assert agent._v6215_counts == {"stop_positions": 1}
    agent._tick += 1
    assert not agent._v6215_stop_active(3)                          # a new request must find it again
    assert agent._v6215_note_stop(3, _inv(0.25, 300.0), 299.2) and agent._v6215_counts == {"stop_positions": 1}
    assert not agent._v6215_note_stop(3, _inv(0.25, 300.0), 299.9) and not agent._v6215_stop_active(3)
    assert 3 not in agent._v6215_stop_books


def test_the_stop_is_off_without_its_switch():
    agent = _note_agent(stop=False)
    assert not agent._v6215_note_stop(3, _inv(0.25, 300.0), 290.0) and not agent._v6215_stop_active(3)


class _RewriteAgent:
    def __init__(self, stop_active, stop_on=True):
        self.research_v6215_loss_stop = stop_on
        self._active = stop_active
        self._v6215_counts = {}

    @staticmethod
    def _v61_on():
        return True

    def _v6215_stop_active(self, book_id):
        return self._active

    def _v61_floor_for(self, *args, **kwargs):
        raise LookupError("past the stop: the v6.1 rewrite ran")


def _rewrite_agent(*args, **kwargs):
    ns = {"V61_REWRITE_NONE": "NONE", "ACTION_TAKER_EXIT": "TAKER_EXIT"}
    return _bind(type("R", (_RewriteAgent,), {}), ("_v61_rewrite_exit", "_v6215_count"), ns)(*args, **kwargs)


def test_the_rewrite_keeps_the_chains_taker_for_a_stopped_position_only():
    taker = types.SimpleNamespace(action="TAKER_EXIT", selected_qty=0.25)
    maker = types.SimpleNamespace(action="MAKER_EXIT", selected_qty=0.25)
    kw = dict(book=None, inventory=types.SimpleNamespace(net_base=0.25), exit_kwargs={"inventory_qty": 0.25})
    agent = _rewrite_agent(True)
    assert agent._v61_rewrite_exit(3, taker, **kw) == (taker, "NONE") and agent._v6215_counts == {"stop_takers_kept": 1}
    for agent, decision in ((_rewrite_agent(True), maker), (_rewrite_agent(False), taker),
                            (_rewrite_agent(True, stop_on=False), taker)):
        with pytest.raises(LookupError):
            agent._v61_rewrite_exit(3, decision, **kw)


class _ExecBase:
    def _execute_aggressive_close(self, response, book_id, book, qty, long_pos):
        self.sent.append((book_id, qty, long_pos))
        return True


def _exec_agent(stop_active, stop_on=True):
    body = textwrap.indent(textwrap.dedent(_simple("_execute_aggressive_close")), "    ")
    body += "\n" + textwrap.indent(textwrap.dedent(_simple("_v6215_count")), "    ")
    scope = {"_ExecBase": _ExecBase, "Any": object}
    exec("from __future__ import annotations\nclass Harness(_ExecBase):\n" + body, scope)
    agent = scope["Harness"]()
    agent.sent, agent.refused, agent._v6215_counts, agent._v61_errors = [], [], {}, 0
    agent.research_v6215_loss_stop = stop_on
    agent._v61_on = lambda: True
    agent._v61_taker_verdict = lambda *a: (False, {"fifo_pnl": -0.2})
    agent._v61_note_refusal = lambda *a: agent.refused.append(a[0])
    agent._v6215_stop_active = lambda book_id: stop_active
    agent._a195_taker_floor_enabled = lambda: False
    return agent


def test_the_executor_sends_a_stop_taker_the_no_loss_floor_would_refuse():
    agent = _exec_agent(True)
    assert agent._execute_aggressive_close(None, 3, None, 0.25, True) is True
    assert agent.sent == [(3, 0.25, True)] and agent.refused == [] and agent._v6215_counts == {"stop_takers_sent": 1}
    for agent in (_exec_agent(False), _exec_agent(True, stop_on=False)):
        assert agent._execute_aggressive_close(None, 3, None, 0.25, True) is False
        assert agent.sent == [] and agent.refused == [3]


# ---- 4. S1 on the wire -------------------------------------------------------------------------------------------

class _WireAgent:
    def __init__(self):
        self.research_v6215_order_life = True
        self._v6215_counts, self._v6215_errors = {}, 0
        self._a19_ledger = DirectExitLedger()

    @staticmethod
    def _get(obj, *names):
        for name in names:
            if hasattr(obj, name):
                return getattr(obj, name)
        return None

    @staticmethod
    def _research_set_instruction_attr(instruction, name, value):
        setattr(instruction, name, value)
        return True

    def _a19_ledger_ref(self):
        return self._a19_ledger


def _wire_agent():
    ns = {"v6215_is_gtt_limit": ol.is_gtt_limit, "v6215_owned_family": ol.owned_family,
          "v6215_backstop_expiry": ol.backstop_expiry, "v6215_sweep_grace_ms": ol.sweep_grace_ms,
          "v6215_sweep_with_grace": ol.sweep_with_grace}
    return _bind(type("W", (_WireAgent,), {}),
                 ("_v6215_normalize_expiry", "_v6215_arm_ledger", "_v6215_count"), ns)()


def _limit(cid, expiry=4_000_000_000, tif=1, kind="PLACE_ORDER_LIMIT"):
    return types.SimpleNamespace(type=kind, clientOrderId=cid, timeInForce=tif, expiryPeriod=expiry)


def test_every_entry_and_exit_rests_to_the_backstop_and_nothing_else_changes():
    agent = _wire_agent()
    entry, exit_, short_exit = _limit(70031), _limit(80052), _limit(80071, expiry=500_000_000)
    compaction, market, ioc = _limit(91031, expiry=3_000_000_000), _limit(None, kind="PLACE_ORDER_MARKET"), _limit(70041, tif="IOC")
    cancel = types.SimpleNamespace(type="CANCEL_ORDERS", clientOrderId=None)
    resp = types.SimpleNamespace(instructions=[entry, exit_, short_exit, compaction, market, ioc, cancel])
    assert agent._v6215_normalize_expiry(resp) == 3
    assert entry.expiryPeriod == exit_.expiryPeriod == short_exit.expiryPeriod == ol.BACKSTOP_NS
    assert (compaction.expiryPeriod, market.expiryPeriod, ioc.expiryPeriod) == (3_000_000_000, 4_000_000_000, 4_000_000_000)
    assert agent._v6215_counts == {"ledger_armed": 1, "expiry_backstop": 3}
    assert agent._v6215_normalize_expiry(resp) == 0                   # idempotent


def test_the_ledger_is_armed_once_and_a_rebuilt_ledger_again():
    agent = _wire_agent()
    assert agent._v6215_arm_ledger(agent._a19_ledger) is True
    assert agent._a19_ledger.sweep._v6215_grace_ms == ol.sweep_grace_ms(True)
    assert agent._v6215_arm_ledger(agent._a19_ledger) is False
    agent._a19_ledger = DirectExitLedger()
    agent._v6215_normalize_expiry(types.SimpleNamespace(instructions=[]))
    assert agent._a19_ledger.sweep._v6215_grace_ms == ol.sweep_grace_ms(True)
    assert agent._v6215_arm_ledger(None) is False


def test_an_armed_ledger_keeps_a_live_20_s_order_the_frozen_sweep_drops():
    agent = _wire_agent()
    ledger = agent._a19_ledger
    ledger.note_accepted(order_id=1, book_id=3, side=1, price=100.01, quantity=0.25,
                         timestamp_ns=NOW - 20_000_000_000, tick=1)
    agent._v6215_arm_ledger(ledger)
    assert ledger.sweep(NOW) == 0 and 1 in ledger.orders


# ---- 5. S2 in acquisition ----------------------------------------------------------------------------------------

class _Order:
    def __init__(self, side):
        self.side = side


def test_the_live_sides_are_read_from_the_account_view_and_the_pending_ledger():
    agent = types.SimpleNamespace(
        _direct_account_orders=lambda book_id: [_Order(1)] if book_id == 3 else [],
        _direct_pending_ledger=lambda: {(4, "70041", "buy"): object(), (3, "", "sell"): object()},
    )
    ns = {"v6215_live_sides": ol.live_sides}
    exec(compile(ast.Module(body=[ast.parse(_simple("_v6215_live_sides")).body[0]], type_ignores=[]),
                 "<v6215>", "exec"), ns)
    live = ns["_v6215_live_sides"]
    assert live(agent, 3) == {"sell"} and live(agent, 4) == {"buy"} and live(agent, 5) == frozenset()


def _side_agent(on=True, sides=None):
    agent = c5._acquire_agent()
    agent.research_v6215_side_ownership = on
    agent._v6215_counts, agent._v6215_errors = {}, 0
    owned = {int(k): frozenset(v) for k, v in (sides or {}).items()}
    agent.live = {b for b in owned}                                  # the book-level view the S2-off path reads
    cls = type(agent)
    cls._v6215_live_sides = lambda self, book_id: owned.get(int(book_id), frozenset())
    g = cls._v62_acquire.__globals__
    g.update({"v6215_mark_live_sides": ol.mark_live_sides, "V62_REASON_LIVE_ORDER": br.REASON_LIVE_ORDER})
    ns = {}
    _bind(cls, ("_v6215_count",), ns)
    return agent


def test_a_held_book_rests_its_adding_side_beside_its_exit():
    agent = _side_agent(sides={3: {"sell"}})
    agent.inventory[3] = 0.25                                         # long: the exit sells, adding buys
    placed = t14._acquire(agent, b0._state(8))
    assert t14._sides(placed, 3) == ["buy"]
    agent = _side_agent(on=False, sides={3: {"sell"}})
    agent.inventory[3] = 0.25
    assert t14._sides(t14._acquire(agent, b0._state(8)), 3) == []    # v6.2.14: the exit owns the whole book


def test_an_owned_side_takes_no_second_order_and_a_fully_owned_book_none():
    agent = _side_agent(sides={2: {"buy"}, 5: {"buy", "sell"}})
    placed = t14._acquire(agent, b0._state(8))
    assert t14._sides(placed, 2) == ["sell"] and t14._sides(placed, 5) == []
    assert agent._v6215_counts == {"side_owned_refusals": 1}
    assert agent._v62_counts.get(br.REASON_LIVE_ORDER, 0) >= 1


# ---- 6. wiring ---------------------------------------------------------------------------------------------------

def test_all_three_switches_default_on_and_ship_in_params():
    for key in ("research_v6215_order_life", "research_v6215_side_ownership", "research_v6215_loss_stop"):
        assert f'getattr(self.config, "{key}", True)' in SIMPLE
        assert f"{key}=1" in LAUNCHER


def test_the_frozen_ttl_attribute_is_never_written():
    # A1.8: the TTL is set through PARAMS only; S1 reads its own attribute instead
    assert "self.research_profitable_exit_ttl_ms =" not in SIMPLE
    switches = _simple("_init_build_switches")
    assert "self._v6215_order_life_ms = float(V6215_BACKSTOP_MS) if self.research_v6215_order_life else None" in switches
    assert 'self._v6215_arm_ledger(getattr(self, "_a19_ledger", None))' in switches


def test_every_overlay_liveness_check_reads_the_s1_life():
    life = 'float(getattr(self, "_v6215_order_life_ms", None) or getattr(self, "research_profitable_exit_ttl_ms", {d}) or {d})'
    assert life.format(d="4000.0") in _simple("_v6214_service_touch_life")
    assert life.format(d="3000.0") in _simple("_a191_live_exit_row")
    assert life.format(d="3000.0") in _simple("_a191_decide")
    assert SIMPLE.count("exit_ttl_ms = " + life.format(d="3000.0")) == 2
    assert 'float(getattr(self, "research_profitable_exit_ttl_ms", 3000.0) or 3000.0)' not in SIMPLE.split(
        "    def _direct_build_mm_stats")[0].split("    def _a191_live_exit_row")[1][:4000]


def test_s1_sets_the_life_after_the_exit_ids_and_before_the_sanitizer_the_validator_and_the_pending_ledger():
    body = SIMPLE[SIMPLE.index("# Only contract/risk safety may veto the already-decided actions here."):]
    assert body.index("self._v6214_assign_exit_identity(response)") < body.index("self._v6215_normalize_expiry(response)") \
        < body.index("self._research_sanitize_maker_instructions(response, state)") \
        < body.index("self._research_final_validate_instructions(response, state)") \
        < body.index("self._direct_record_pending_placements(response, state)")


def test_s2_marks_owned_sides_after_the_side_selection_and_before_the_surplus_check():
    acq = _simple("_v62_acquire")
    select = acq.index("sides = self._v6214_select_sides(book_id, book, sides)")
    mark = acq.index("sides, owned = v6215_mark_live_sides(")
    surplus = acq.index("sides[side_token] = V626_REASON_SURPLUS")
    assert select < mark < surplus
    facts = _simple("_v62_book_facts")
    assert "live = len(self._v6215_live_sides(int(book_id))) >= 2" in facts
    assert "live = bool(self._direct_book_has_live_order(int(book_id)))" in facts


def test_s2_is_per_side_in_the_final_validator():
    validator = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")("_research_final_validate_instructions")
    assert 'v6215_side_owned = bool(getattr(self, "research_v6215_side_ownership", False))' in validator
    assert "(book_id, canonical_order_side(side)) in preexisting_order_sides if v6215_side_owned" in validator
    assert "else book_id in preexisting_order_books" in validator


def test_the_inventory_loop_lets_an_adding_entry_rest_and_notes_the_stop():
    loop = _simple("build_mm_strategy_instructions")
    note = loop.index("self._v6215_note_stop(book_id, inventory, mid)")
    closing = loop.index('if bool(getattr(self, "research_v6215_side_ownership", False)) else None')
    live = loop.index("closing in self._v6215_live_sides(book_id) if closing is not None")
    assert note < closing < live
    assert 'response, book_id, reason="INVENTORY_OPENED", side=closing,' in loop


def test_the_rewrite_and_the_executor_read_the_stop_only_under_its_switch():
    rewrite = _simple("_v61_rewrite_exit")
    assert rewrite.index('bool(getattr(self, "research_v6215_loss_stop", False)) and self._v6215_stop_active(int(book_id))') \
        < rewrite.index("self._v61_floor_for(int(book_id), long_pos)")
    close = _simple("_execute_aggressive_close")
    assert close.index('if not ok and bool(getattr(self, "research_v6215_loss_stop", False)) and self._v6215_stop_active(int(book_id)):') \
        < close.index("self._v61_note_refusal(int(book_id), float(qty), bool(long_pos), detail)") \
        < close.index("placed = super()._execute_aggressive_close(")


def test_the_state_row_reports_every_switch_and_the_counters():
    tele = _simple("_v62_telemetry")
    for key in ("order_life_on=", "side_ownership_on=", "loss_stop_on=", "order_life=self._v6215_snapshot()"):
        assert key in tele
    snap = _simple("_v6215_snapshot")
    for key in ('out["life_ms"]', 'out["sweep_grace_ms"]', 'out["stop_books"]', 'out["version"]'):
        assert key in snap


def test_pins_and_the_launcher_arm():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_15"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_15"' in SIMPLE
    assert "strategy1_direct_v6_2_15)" in LAUNCHER and "V6214_BUILD=1; V6215_BUILD=1" in LAUNCHER
    assert "strategy1_direct_v6_2_14)" in LAUNCHER                      # the previous arm stays
    assert 'if [[ "$V6215_BUILD" == "1" ]]; then' in LAUNCHER
    assert "[preflight] v6.2.15 order life PASS" in LAUNCHER
    assert "tests/test_research_v6_2_15_order_life.py" in LAUNCHER


def test_the_arm_restarts_inherited_positions_as_lots_and_refuses_park():
    assert 'INHERITED_SHORT_LOTS_SOURCE="default"' in LAUNCHER
    arm = LAUNCHER[LAUNCHER.index("strategy1_direct_v6_2_15) "):]
    arm = arm[:arm.index(";;") + 2]
    assert '[[ "$INHERITED_SHORT_LOTS_SOURCE" == "default" ]] && INHERITED_SHORT_LOTS="exit"' in arm
    block = LAUNCHER[LAUNCHER.index('if [[ "$V6215_BUILD" == "1" ]]; then'):]
    block = block[:block.index("[preflight] v6.2.15 order life PASS")]
    assert '[[ "$INHERITED_SHORT_LOTS" == "exit" ]] ||' in block
