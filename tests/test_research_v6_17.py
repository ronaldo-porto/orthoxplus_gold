"""v6.17: S1 the fair-value touch quote -- research_v617_touch_quote.

The makers that beat us on both halves of the pay in both simulations quote one side, the one trading toward the
validator-published fundamental, at the touch.  Ours reach the touch only when it is consumed down to our deep levels,
two thirds of them on the side trading against it.  On a deep-layer book with a fresh fundamental of this simulation
ONE post-only order joins the touch on the side trading toward it while its own price is the validator's alpha hurdle
on the safe side of it; the deep layer places first, the quote owns its own slot, follows the volume line and the deep
layer's bound, and rests within two ticks of the touch.  Replay (k21/vm617, 10-06): see the build memo.
"""
import ast
import os
import re
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v617_touch_quote as tq  # noqa: E402
import research_v69_deep_ladder as d69  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_5_deep_clips as t65  # noqa: E402
import test_research_v6_9 as t69  # noqa: E402
import test_research_v6_16 as t616  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000
NOW = t616.NOW
TICK = 0.01
BUY, SELL = tq.SIDE_BUY, tq.SIDE_SELL
# v6.25 S5 hands the touch quote the leaned inventory (v6_25_inv = inv - tau; inv itself without a lean)
CALL_LINE = "placed_total += self._v617_book(response, book_id, deep, raw_bid, raw_ask, v6_25_inv, touch_rows, already,"
SIDES_LINE = 'if toward in v617_placed_sides(getattr(response, "instructions", None) or (), book_id):'
SLOT_LINE = "deep_slot = V617_TOUCH_SLOT"
LIVE_LINE = "return v617_live_slots(rows)"
EXTRA = {
    "V617_CANCEL_BOUND": tq.CANCEL_BOUND, "V617_CANCEL_NO_FP": tq.CANCEL_NO_FP, "V617_CANCEL_PACED": tq.CANCEL_PACED,
    "V617_CANCEL_SHUT": tq.CANCEL_SHUT, "V617_CLIP_DEEP_CLIPS": tq.CLIP_DEEP_CLIPS, "V617_TOUCH_SLOT": tq.TOUCH_SLOT,
    "V617_TOUCH_QUOTE_VERSION": tq.V617_TOUCH_QUOTE_VERSION, "v617_caps_extra": tq.caps_extra,
    "v617_client_ids": tq.client_ids, "v617_is_touch_client_id": tq.is_touch_client_id,
    "v617_live_slots": tq.live_slots, "v617_placed_sides": tq.placed_sides, "v617_room": tq.room,
    "v617_toward_side": tq.toward_side,
    "v617_verdict": tq.verdict, "v617_wanted_price": tq.wanted_price,
}
METHODS = ("_v617_count", "_v617_fundamental", "_v617_cancel", "_v617_shut", "_v617_touch", "_v617_book",
           "_v617_snapshot")
TOUCH_IDS = (43031, 43032)


# ---- 1. the module ------------------------------------------------------------------------------------------------------

def test_the_quote_has_its_own_client_ids_apart_from_every_other_family():
    assert tq.client_ids(3) == TOUCH_IDS and tq.client_ids(127) == (44271, 44272)
    assert all(tq.is_touch_client_id(c) for c in TOUCH_IDS)
    for other in (40031, 40032, 40033, 41276, 60031, 65032, 70031, 80032, 91000, 43033, 43030, None, "x"):
        assert not tq.is_touch_client_id(other), other
    assert tq.side_of_client_id(43031) == BUY and tq.side_of_client_id(43032) == SELL and tq.side_of_client_id(40031) is None
    assert not any(t63.tt.role_of(c) for c in TOUCH_IDS)                       # not a v6.3 order
    assert not any(t616.dl.is_deep_client_id(c) for c in TOUCH_IDS)            # not a deep order
    assert tq.CLIENT_BASE + 10 * 127 + 2 < tq.CLIENT_BASE + tq.CLIENT_SPAN


def test_the_side_is_the_one_trading_toward_the_fundamental():
    assert tq.toward_side(100.015, 100.40) == BUY and tq.toward_side(100.015, 99.60) == SELL
    assert tq.toward_side(100.015, 100.015) is None
    assert tq.toward_side(None, 100.4) is None and tq.toward_side(100.0, 0.0) is None


def test_the_edge_is_how_far_the_price_sits_on_the_safe_side_of_the_fundamental():
    assert abs(tq.edge_bps(BUY, 100.00, 100.40) - 39.920) < 1e-3                 # a bid below the fundamental: +
    assert abs(tq.edge_bps(SELL, 100.03, 99.60) - 43.080) < 1e-3                 # an offer above it: +
    assert tq.edge_bps(BUY, 100.50, 100.40) < 0 and tq.edge_bps(SELL, 99.50, 99.60) < 0
    assert tq.edge_bps(BUY, None, 100.4) is None and tq.edge_bps(BUY, 100.0, 0.0) is None


def test_a_new_quote_joins_the_touch_only_where_the_sanitizer_keeps_it_and_its_edge_clears_the_hurdle():
    assert tq.join_price(BUY, 100.00, 100.03, TICK, 2) == 100.00 and tq.join_price(SELL, 100.00, 100.03, TICK, 2) == 100.03
    assert tq.join_price(BUY, 100.00, 100.02, TICK, 2) == 100.00                # two ticks: the sanitizer keeps it
    assert tq.join_price(BUY, 100.00, 100.01, TICK, 2) is None                  # one tick: it would be moved back
    assert tq.join_price(BUY, 100.00, 100.01, TICK, 1) == 100.00
    assert tq.join_price(BUY, 100.03, 100.00, TICK, 2) is None                  # crossed
    assert tq.THRESHOLD_BPS == 2.3                                              # the validator's alpha hurdle
    kw = dict(bid=100.00, ask=100.03, tick=TICK, safety_ticks=2)
    assert tq.wanted_price(BUY, fundamental=100.40, **kw) == 100.00
    assert tq.wanted_price(BUY, fundamental=100.022, **kw) is None              # 2.2 bps: short of the hurdle
    assert tq.wanted_price(BUY, fundamental=100.024, **kw) == 100.00            # 2.4 bps
    assert tq.wanted_price(SELL, fundamental=99.60, **kw) == 100.03
    assert tq.wanted_price(SELL, fundamental=100.008, **kw) is None


def test_a_resting_quote_stays_while_it_is_on_the_right_side_past_the_hurdle_and_within_two_ticks():
    kw = dict(bid=100.00, ask=100.03, tick=TICK)
    assert tq.REST_TICKS == 2
    assert tq.verdict(BUY, 100.00, toward=BUY, fundamental=100.40, **kw) is None
    assert tq.verdict(BUY, 99.99, toward=BUY, fundamental=100.40, **kw) is None           # one tick behind: rests
    assert tq.verdict(BUY, 99.98, toward=BUY, fundamental=100.40, **kw) == tq.CANCEL_LEFT  # two ticks: re-placed
    assert tq.verdict(SELL, 100.04, toward=SELL, fundamental=99.60, **kw) is None
    assert tq.verdict(SELL, 100.05, toward=SELL, fundamental=99.60, **kw) == tq.CANCEL_LEFT
    assert tq.verdict(BUY, 100.00, toward=SELL, fundamental=99.60, **kw) == tq.CANCEL_FLIPPED
    assert tq.verdict(BUY, 100.00, toward=None, fundamental=100.015, **kw) == tq.CANCEL_FLIPPED
    assert tq.verdict(BUY, 100.00, toward=BUY, fundamental=100.02, **kw) == tq.CANCEL_EDGE   # 2.0 bps
    assert tq.verdict(BUY, 100.00, toward=BUY, fundamental=100.40, bid=None, ask=100.03, tick=TICK) == tq.CANCEL_LEFT


def test_an_adding_quote_must_fit_the_bound_and_a_reducing_one_always_fits():
    assert tq.room(BUY, 0.0, 8.0, 48.0) and tq.room(BUY, 40.0, 8.0, 48.0)
    assert not tq.room(BUY, 40.5, 8.0, 48.0) and not tq.room(SELL, -41.0, 8.0, 48.0)
    assert tq.room(SELL, 47.0, 8.0, 48.0) and tq.room(BUY, -47.0, 8.0, 48.0)              # it reduces the position
    assert tq.room(SELL, 0.0, 8.0, 48.0)                                                  # flat: both sides add
    assert not tq.room(BUY, 0.0, 0.0, 48.0) and not tq.room(BUY, 0.0, None, 48.0)


def test_the_quote_holds_its_own_slot_and_never_a_deep_level():
    rows = [(0, 40031), (0, 43031), (1, 40034)]
    assert tq.live_slots(rows) == {"buy": frozenset({0, tq.TOUCH_SLOT}), "sell": frozenset({1})}
    assert d69.live_slots(rows)["buy"] == frozenset({0, d69.WHOLE_SIDE})                  # v6.9 alone: the whole side
    assert tq.live_slots([(1, 43032), (0, 80031)]) == {"buy": frozenset({d69.WHOLE_SIDE}), "sell": frozenset({tq.TOUCH_SLOT})}
    assert tq.TOUCH_SLOT not in (0, 1, 2, d69.WHOLE_SIDE)
    assert not d69.slot_taken(tq.live_slots([(0, 40031)]), "buy", tq.TOUCH_SLOT)
    assert d69.slot_taken(tq.live_slots([(0, 43031)]), "buy", tq.TOUCH_SLOT)
    assert not d69.slot_taken(tq.live_slots([(0, 43031)]), "buy", 0)                      # a deep level stays free


def test_a_side_this_response_already_placed_on_is_taken():
    ns = types.SimpleNamespace
    ixs = [ns(type="PLACE_ORDER_LIMIT", bookId=3, direction="BUY"), ns(type="CANCEL_ORDERS", bookId=3),
           ns(type="PLACE_ORDER_LIMIT", bookId=5, direction="SELL"),
           {"type": "PLACE_ORDER_MARKET", "book_id": 3, "side": 1}]
    assert tq.placed_sides(ixs, 3) == {BUY, SELL} and tq.placed_sides(ixs, 5) == {SELL} and tq.placed_sides(ixs, 4) == set()
    enum = types.SimpleNamespace(name="BUY")                                              # an OrderDirection member
    kind = types.SimpleNamespace(value="PLACE_ORDER_LIMIT")
    assert tq.placed_sides([ns(type=kind, bookId=3, direction=enum)], 3) == {BUY}
    assert tq.placed_sides([ns(type="PLACE_ORDER_LIMIT", bookId=3, direction=0)], 3) == {BUY}   # the venue's 0
    assert tq.placed_sides(None, 3) == set() and tq.placed_sides([ns(type="PLACE_ORDER_LIMIT", bookId=None)], 3) == set()


def test_the_cap_carries_one_quote_in_flight_per_book():
    assert tq.caps_extra(128, 8.0) == 1024.0 and tq.caps_extra(0, 8.0) == 0.0 and tq.caps_extra(128, None) == 0.0


# ---- 2. the agent -------------------------------------------------------------------------------------------------------

def _method(name, base=None):
    scope = dict(base or {})
    scope.update(EXTRA)
    exec(compile(ast.Module(body=[ast.parse(_src(name)).body[0]], type_ignores=[]), f"<{name}>", "exec"), scope)
    return scope[name]


def _with17(fn):
    g = dict(fn.__globals__)
    g.update(EXTRA)
    return types.FunctionType(fn.__code__, g, fn.__name__, fn.__defaults__, fn.__closure__)


def _tq_agent(page, *, on=True, inv=None, used=300_000.0, slots=None):
    agent = t616._fv_agent(page)
    cls = type(agent)
    base = getattr(cls, "_v633_book").__globals__
    methods = {n: _with17(getattr(cls, n)) for n in ("_v63_pass", "_v633_book")}
    for n in METHODS:
        methods[n] = _method(n, base)
    if inv is not None:
        methods["_a195_venue_net_by_book"] = lambda self, books: {int(b): float(inv) for b in books}
    methods["_research_book_traded_volume"] = lambda self, book_id: float(used)
    live = dict(slots or {})
    methods["_v69_live_slots"] = lambda self, b: tq.live_slots(live.get(int(b), ()))
    agent.__class__ = type("P617", (cls,), methods)
    agent.research_v617_touch_quote = on
    agent._v617_counts, agent._v617_errors = {}, 0
    agent.research_post_only_safety_ticks = 2
    agent.research_v610_behind_max_clips = 24.0                        # the shipped bound: 48 base behind the line
    return agent


def _run(agent, book=None):
    resp = t63._Resp()
    agent._v63_pass(resp, t616._state({3: book or t63._book()}), {})
    return resp


def _rest_deep(agent):
    t63.t14._rest(agent, 21, 3, 0, 99.99, cid=40031)               # v6.16's level 0, so the deep layer places nothing
    t63.t14._rest(agent, 22, 3, 1, 100.23, cid=40032)


def _touch(resp):
    return {(d, px, q) for (b, d, cid, px, q) in t63._placed(resp) if b == 3 and cid in TOUCH_IDS}


def test_with_the_switch_off_or_without_a_usable_fundamental_the_book_is_v616_exactly():
    base = t63._placed(_run(t616._fv_agent(t616._page({3: 100.40}))))
    assert t63._placed(_run(_tq_agent(t616._page({3: 100.40}), on=False))) == base
    for page in (None, t616._page({3: 100.4}, sim_id="20260929_2015"), t616._page({3: 100.4}, sim_ts_ns=NOW - 30 * S),
                 t616._page({5: 100.4})):
        agent = _tq_agent(page)
        _rest_deep(agent)
        resp = _run(agent)
        assert _touch(resp) == set(), page
        assert agent._v617_errors == 0 and not any(k.startswith("placed_") for k in agent._v617_counts)


def test_the_quote_joins_the_touch_on_the_side_trading_toward_the_fundamental():
    agent = _tq_agent(t616._page({3: 100.40}))                                   # fundamental 38 bps above the mid
    _rest_deep(agent)
    resp = _run(agent)
    assert _touch(resp) == {("BUY", 100.00, 2.0)}                                # one deep clip at the best bid
    assert t63._cancelled(resp) == set() and agent._v617_counts.get("placed_buy") == 1
    ix = [i for i in resp.instructions if getattr(i, "clientOrderId", None) == 43031][0]
    assert ix.postOnly is True and ix.timeInForce == "GTT" and ix.stp == "CANCEL_BOTH"
    agent = _tq_agent(t616._page({3: 99.60}))
    t63.t14._rest(agent, 21, 3, 0, 99.80, cid=40031)
    t63.t14._rest(agent, 22, 3, 1, 100.04, cid=40032)
    assert _touch(_run(agent)) == {("SELL", 100.03, 2.0)}
    assert agent._v617_errors == 0


def test_a_quote_whose_edge_is_short_of_the_hurdle_is_not_placed():
    agent = _tq_agent(t616._page({3: 100.022}))                                  # bid edge 2.2 bps
    _rest_deep(agent)
    assert _touch(_run(agent)) == set() and agent._v617_counts.get("no_price") == 1
    agent = _tq_agent(t616._page({3: 100.024}))
    _rest_deep(agent)
    assert _touch(_run(agent)) == {("BUY", 100.00, 2.0)}


def test_the_deep_layer_places_first_and_the_quote_waits_for_the_next_request():
    agent = _tq_agent(t616._page({3: 100.40}))                                   # nothing resting: deep places both sides
    resp = _run(agent)
    deep = {(d, px) for (b, d, cid, px, _q) in t63._placed(resp) if cid in (40031, 40032)}
    assert deep == {("BUY", 99.99), ("SELL", 100.23)} and _touch(resp) == set()
    assert agent._v617_counts.get("yield_to_deep") == 1


def test_a_resting_quote_is_kept_or_cancelled_and_a_cancelled_side_waits_until_the_cancel_is_seen():
    def rested(page, price, side=0, inv=None, used=300_000.0):
        agent = _tq_agent(page, inv=inv, used=used)
        _rest_deep(agent)
        t63.t14._rest(agent, 31, 3, side, price, cid=43031 if side == 0 else 43032, qty=2.0)
        return agent, _run(agent)

    agent, resp = rested(t616._page({3: 100.40}), 100.00)
    assert t63._cancelled(resp) == set() and _touch(resp) == set() and agent._v617_counts == {"book_armed": 1}
    agent, resp = rested(t616._page({3: 100.40}), 99.99)                        # one tick behind: rests
    assert t63._cancelled(resp) == set() and _touch(resp) == set()
    agent, resp = rested(t616._page({3: 100.40}), 99.98)                        # two ticks behind: re-placed next request
    assert t63._cancelled(resp) == {(3, 31)} and _touch(resp) == set()
    assert agent._v617_counts.get("cancel_touch_left") == 1 and agent._v617_counts.get("wait_cancel_seen") == 1
    agent, resp = rested(t616._page({3: 100.02}), 100.00)                        # its edge fell to 2.0 bps
    assert (3, 31) in t63._cancelled(resp) and agent._v617_counts.get("cancel_touch_edge") == 1   # (v6.16 re-centres 21)
    agent, resp = rested(None, 100.00)                                           # no fundamental (v6.16: 21 back to the mid)
    assert (3, 31) in t63._cancelled(resp) and agent._v617_counts.get("cancel_touch_no_fp") == 1
    agent, resp = rested(t616._page({3: 99.60}), 100.00)                         # flipped: the bid goes, the offer comes
    assert (3, 31) in t63._cancelled(resp) and agent._v617_counts.get("cancel_touch_flipped") == 1
    assert _touch(resp) == {("SELL", 100.03, 2.0)}


def test_ahead_of_its_volume_line_an_adding_quote_goes_and_a_reducing_one_stays():
    agent = _tq_agent(t616._page({3: 100.40}), used=10_000_000.0)                # far ahead of the line
    _rest_deep(agent)
    t63.t14._rest(agent, 31, 3, 0, 100.00, cid=43031, qty=2.0)
    resp = _run(agent)
    assert (3, 31) in t63._cancelled(resp) and agent._v617_counts.get("cancel_touch_paced") == 1
    agent = _tq_agent(t616._page({3: 100.40}), used=10_000_000.0, inv=-20.0)     # short: the bid reduces it
    _rest_deep(agent)
    t63.t14._rest(agent, 31, 3, 0, 100.00, cid=43031, qty=2.0)
    resp = _run(agent)
    assert (3, 31) not in t63._cancelled(resp) and not agent._v617_counts.get("cancel_touch_paced")


def test_ahead_of_its_volume_line_no_adding_quote_is_placed_and_a_reducing_one_is():
    agent = _tq_agent(t616._page({3: 100.40}), used=10_000_000.0)                # flat: the bid adds
    _rest_deep(agent)
    assert _touch(_run(agent)) == set() and agent._v617_counts.get("paced_states") == 1
    agent = _tq_agent(t616._page({3: 100.40}), used=10_000_000.0, inv=-20.0)     # short: the bid reduces it
    _rest_deep(agent)
    assert _touch(_run(agent)) == {("BUY", 100.00, 2.0)} and not agent._v617_counts.get("paced_states")


def test_an_adding_quote_never_passes_the_deep_layers_bound():
    probe = _tq_agent(t616._page({3: 100.40}))
    bound = probe._v610_bound_base(probe._v633_deep, False)
    agent = _tq_agent(t616._page({3: 100.40}), inv=bound - 1.0)
    _rest_deep(agent)
    assert _touch(_run(agent)) == set() and agent._v617_counts.get("bound_states") == 1
    agent = _tq_agent(t616._page({3: 100.40}), inv=bound - 1.0)
    _rest_deep(agent)
    t63.t14._rest(agent, 31, 3, 0, 100.00, cid=43031, qty=2.0)
    assert (3, 31) in t63._cancelled(_run(agent)) and agent._v617_counts.get("cancel_touch_bound") == 1


def test_a_quote_in_flight_on_its_side_takes_no_second_one():
    agent = _tq_agent(t616._page({3: 100.40}), slots={3: [(0, 43031)]})           # sent last request, not yet seen
    _rest_deep(agent)
    assert _touch(_run(agent)) == set() and agent._v617_counts.get("slot_in_flight") == 1


def test_a_book_the_deep_layer_does_not_trade_keeps_no_quote():
    agent = _tq_agent(t616._page({3: 100.40}))
    agent._v633_deep.book_open = lambda *a, **k: False
    t63.t14._rest(agent, 31, 3, 0, 100.00, cid=43031, qty=2.0)
    resp = _run(agent)
    assert (3, 31) in t63._cancelled(resp) and agent._v617_counts.get("cancel_touch_shut") == 1
    assert _touch(resp) == set()


def test_the_switch_is_in_the_state_record():
    assert 'touch_quote_on=int(bool(getattr(self, "research_v617_touch_quote", False))),' in SIMPLE
    assert "touch_quote=(self._v617_snapshot()" in SIMPLE
    snap = _tq_agent(t616._page({3: 100.40}))._v617_snapshot()
    assert snap == {"version": "touch_quote_v6_17", "counts": {}, "errors": 0}


# ---- 3. the order rules -------------------------------------------------------------------------------------------------

class _Host(t69._Host):
    research_v617_touch_quote = True


for _name in ("_research_final_validate_instructions", "_v69_live_slots"):
    setattr(_Host, _name, _method(_name, dict(t69.VALIDATOR_NAMES, v69_live_slots=d69.live_slots)))


def test_the_final_validator_judges_the_quote_on_its_own_slot():
    host = _Host(account={3: [t69._acct(11, 0, 40031)]})                         # a deep level 0 live on the bid
    assert t69._validate(host, t69._ix(43031)) == ([43031], [])                  # the quote goes out beside it
    host = _Host(account={3: [t69._acct(11, 0, 43031)]})                         # a quote live on the bid
    assert t69._validate(host, t69._ix(40033)) == ([40033], [])                  # it holds no deep level
    assert t69._validate(host, t69._ix(43031)) == ([], [(3, "INFLIGHT_BOOK_ORDER")])
    pending = _Host(pending=[(3, "43031", "buy")])                                # sent, not yet acknowledged
    assert t69._validate(pending, t69._ix(43031)) == ([], [(3, "INFLIGHT_BOOK_ORDER")])
    host = _Host()
    assert t69._validate(host, t69._ix(40031), t69._ix(43031)) == ([40031], [(3, "SAME_REQUEST_BOOK_SIDE_OWNED")])
    off = t69._Host(account={3: [t69._acct(11, 0, 40031)]})                       # switch off: v6.9's whole-side rule
    assert t69._validate(off, t69._ix(43031)) == ([], [(3, "INFLIGHT_BOOK_ORDER")])


def test_the_cap_grows_by_one_quote_per_book_only_with_the_switch():
    def caps(on, ladder=False):
        o = t65._obj(2.0, 3.0)                                                  # 2-base clip, 3-clip bound: v6.5's 1,024
        o._v69_ladder_on = lambda: ladder
        scope = dict(t65._method("_v63_apply_caps").__globals__, v69_caps_for=d69.caps_for)
        o._v63_apply_caps = types.MethodType(_method("_v63_apply_caps", scope), o)
        if on:
            o.research_v617_touch_quote = True
        return t65._caps(o)
    assert caps(False) == (1024.0, 1024.0)
    assert caps(True) == (1024.0 + 128 * 2.0, 1024.0 + 128 * 2.0)              # one deep clip in flight per book
    with_ladder = caps(False, ladder=True)
    assert caps(True, ladder=True) == (with_ladder[0] + 256.0, with_ladder[1] + 256.0)


# ---- 4. wiring ----------------------------------------------------------------------------------------------------------

def _params():
    start = LAUNCHER.index('PARAMS="')
    return LAUNCHER[start:LAUNCHER.index('"\n', start + len('PARAMS="'))]


def _shipped():
    return _params()[len('PARAMS="'):].replace("\\\n", " ")


def test_the_switch_ships_on_and_agrees_with_the_agent_default():
    params = _params()
    assert "research_v617_touch_quote=1" in params and "research_v617_touch_quote=0" not in params
    assert 'self.research_v617_touch_quote = self._as_bool(getattr(self.config, "research_v617_touch_quote", True))' in SIMPLE
    for line in (CALL_LINE, SLOT_LINE, LIVE_LINE):
        assert SIMPLE.count(line) == 1, line
    assert SIMPLE.count(SIDES_LINE) == 1
    # the deep layer places first: each touch call follows its deep call
    pass_body = _src("_v63_pass")
    for a, b in re.findall(r"(placed_total \+= self\._v633_book\()[\s\S]*?(placed_total \+= self\._v617_book\()", pass_body):
        assert a and b
    assert pass_body.count("placed_total += self._v617_book(") == 2


def test_the_v617_block_is_gated_preflighted_and_listed():
    assert "; V6_15_BUILD=0; V6_16_BUILD=0; V6_17_BUILD=0; V6_18_BUILD=0; V6_19_BUILD=0; V6_20_BUILD=0; V6_21_BUILD=0; V6_22_BUILD=0; V6_23_BUILD=0; V6_24_BUILD=0; V6_25_BUILD=0; V6_26_BUILD=0; V6_27_BUILD=0; V6_28_BUILD=0; V6_29_BUILD=0; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0\n" in LAUNCHER
    arm = next(line for line in LAUNCHER.splitlines() if line.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_15_BUILD=1; V6_16_BUILD=1; V6_17_BUILD=1; V6_18_BUILD=1; V6_19_BUILD=1; V6_20_BUILD=1; V6_21_BUILD=1; V6_22_BUILD=1; V6_23_BUILD=1; V6_24_BUILD=1; V6_25_BUILD=1; V6_26_BUILD=1; V6_27_BUILD=1; V6_28_BUILD=1; V6_29_BUILD=1; V6_30_BUILD=1; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1"), arm
    assert 'if [[ "${V6_17_BUILD:-0}" == "1" ]]; then' in LAUNCHER
    assert "tests/test_research_v6_17.py" in LAUNCHER


def test_every_v617_preflight_pattern_is_in_the_file_it_checks():
    start = LAUNCHER.index('if [[ "${V6_17_BUILD:-0}" == "1" ]]; then')
    block = LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.17 touch quote PASS', start)]
    pats = re.findall(r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\"", block)
    assert len(pats) == 6, pats
    for pat, name in pats:
        assert pat in (STRATEGY / name).read_text(), (pat, name)


def _run_block(params, agent_path=STRATEGY):
    start = LAUNCHER.index("# v6.17 S1.")
    stop = LAUNCHER.index('echo "[preflight] v6.17 touch quote PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_17_BUILD": "1"}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_the_v617_block_passes_the_shipped_params_and_refuses_the_switch_off():
    out = _run_block(_shipped())
    assert out.returncode == 0 and "[preflight] v6.17 touch quote PASS" in out.stdout, out.stderr
    for bad in ("research_v617_touch_quote=0", "research_v617_touch_quote=10", ""):
        out = _run_block(_shipped().replace("research_v617_touch_quote=1", bad))
        assert out.returncode == 1 and "v6.17 S1 build without research_v617_touch_quote=1" in out.stderr, repr(bad)


def test_the_v617_block_refuses_an_agent_without_the_quote_the_sides_the_slot_or_the_verdict(tmp_path=None):
    import tempfile
    tmp = Path(tmp_path) if tmp_path else Path(tempfile.mkdtemp())
    for f in STRATEGY.glob("*.py"):
        (tmp / f.name).write_text(f.read_text())
    for line, why in ((CALL_LINE, "take no touch quote"), (SIDES_LINE, "does not check the sides this response"),
                      (SLOT_LINE, "does not give the touch quote its own slot"), (LIVE_LINE, "hold its whole side")):
        (tmp / "Strategy1_Research_Simple.py").write_text(SIMPLE.replace(line, "pass  # removed"))
        out = _run_block(_shipped(), agent_path=tmp)
        assert out.returncode == 1 and why in out.stderr, why
    (tmp / "Strategy1_Research_Simple.py").write_text(SIMPLE)
    src = (STRATEGY / "research_v617_touch_quote.py").read_text()
    (tmp / "research_v617_touch_quote.py").write_text(src.replace("def verdict(", "def no_verdict("))
    out = _run_block(_shipped(), agent_path=tmp)
    assert out.returncode == 1 and "has no verdict" in out.stderr
