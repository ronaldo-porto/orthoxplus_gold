"""v6.4: the deep layer rests inside a blown-out spread (S1), and while its board pays it owns every book (S2).

Validator window sim 16,790-27,590 (09-26): the high-volume skill leaders fill 70% of the time while a book's spread is
>= 10 ticks, inside the gap; the v6.3.3 deep price stayed one tick behind the far touch.  UID 94's worst book was a
touch book (782 v6.3 target/making fills, -40.7 = ten typical books).  Replay with the validator's arithmetic, partial
fills and the side-ownership delay: making 2,805-4,482 vs 1,048-1,577 and skill 1.50-3.21 vs 0.83-1.12 on the latest
windows; the trending simulation keeps v6.3.2 (the board stays shut).
"""
import ast
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v633_deep_layer as dl  # noqa: E402
import research_v64_board as b64  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_3_3_deep_layer as d633  # noqa: E402
from research_direct_exit_refresh import ABSENT_REPRICE_CANCEL  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
TICK = 0.01
EXTRA = {"v64_vacuum_depth": b64.vacuum_depth, "v64_vacuum_price": b64.vacuum_price,
         "V64_CANCEL_BOARD_OWNS": b64.CANCEL_BOARD_OWNS, "V64_BOARD_VERSION": b64.V64_BOARD_VERSION,
         "V633_CANCEL_SHUT": dl.CANCEL_SHUT, "ABSENT_REPRICE_CANCEL": ABSENT_REPRICE_CANCEL}


def _method(name):
    scope = dict(EXTRA)
    exec(compile(ast.Module(body=[ast.parse(_src(name)).body[0]], type_ignores=[]), f"<{name}>", "exec"), scope)
    return scope[name]


def _with_globals(fn):
    g = dict(fn.__globals__)
    g.update(EXTRA)
    return types.FunctionType(fn.__code__, g, fn.__name__, fn.__defaults__, fn.__closure__)


def _agent(layer, *, vacuum=True, owns=True, **kw):
    agent = d633._agent(layer, **kw)
    cls = type(agent)
    methods = {n: _with_globals(getattr(cls, n)) for n in ("_v63_pass", "_v633_book")}
    for n in ("_v64_vacuum_on", "_v64_board_owns_on", "_v64_count", "_v64_snapshot", "_v64_board_idle"):
        methods[n] = _method(n)
    agent.__class__ = type("P64", (cls,), methods)
    agent.research_v64_vacuum, agent.research_v64_board_owns = vacuum, owns
    agent._v64_counts, agent._v64_errors = {}, 0
    return agent


def _run(agent, books):
    resp = t63._Resp()
    agent._v63_pass(resp, t63._state(books), {})
    return resp


# ---- 1. the module --------------------------------------------------------------------------------------------------

def test_the_vacuum_opens_at_a_20_tick_spread_and_sits_at_0_8_of_the_half_spread():
    assert b64.vacuum_depth(100.00, 100.19, TICK) is None
    assert abs(b64.vacuum_depth(100.00, 100.20, TICK) - 8.0) < 1e-6
    assert abs(b64.vacuum_depth(100.00, 101.06, TICK) - 42.4) < 1e-6
    assert b64.vacuum_depth(100.00, 101.06, 0.0) is None
    assert b64.VACUUM_MIN_SPREAD_TICKS == 20.0 and b64.VACUUM_FRACTION == 0.8


def test_a_vacuum_order_rests_strictly_inside_the_touch_near_its_own_side():
    bid, ask = 100.00, 101.06
    mid, d = 0.5 * (bid + ask), b64.vacuum_depth(bid, ask, TICK)
    pb = b64.vacuum_price(mid, d, dl.SIDE_BUY, bid=bid, ask=ask, tick=TICK, decimals=2)
    pa = b64.vacuum_price(mid, d, dl.SIDE_SELL, bid=bid, ask=ask, tick=TICK, decimals=2)
    assert (pb, pa) == (100.11, 100.95)
    assert bid < pb < mid < pa < ask
    # a fraction at the far edge is still held one tick inside the touch
    assert b64.vacuum_price(mid, 53.0, dl.SIDE_BUY, bid=bid, ask=ask, tick=TICK, decimals=2) == 100.01
    assert b64.vacuum_price(mid, 53.0, dl.SIDE_SELL, bid=bid, ask=ask, tick=TICK, decimals=2) == 101.05


def test_book_open_without_the_inventory_bound_keeps_an_over_limit_book():
    layer = d633._deep_layer(books=(1,), alpha=50.0)
    assert layer.update_board() is True
    assert not layer.book_open(1, 30.0, 3.0)
    assert layer.book_open(1, 30.0, 3.0, inventory_bound=False)
    assert not layer.room(dl.SIDE_BUY, 3.0) and layer.room(dl.SIDE_SELL, 3.0)       # only the reducing side


# ---- 2. S1 in the pass ----------------------------------------------------------------------------------------------

WIDE = dict(bid=100.00, ask=100.30)             # 30 ticks: vacuum at 0.8 x 15 = 12 ticks from the mid


def test_an_open_book_in_a_blown_out_spread_rests_inside_the_gap():
    agent = _agent(d633._deep_layer())
    resp = _run(agent, {3: t63._book(**WIDE)})
    mid = 100.15
    want_b = b64.vacuum_price(mid, 12.0, dl.SIDE_BUY, bid=100.00, ask=100.30, tick=TICK, decimals=2)
    want_a = b64.vacuum_price(mid, 12.0, dl.SIDE_SELL, bid=100.00, ask=100.30, tick=TICK, decimals=2)
    assert (want_b, want_a) == (100.03, 100.27)
    assert t63._placed(resp) == {(3, "BUY", 40031, want_b, 1.0), (3, "SELL", 40032, want_a, 1.0)}
    assert agent._v64_counts == {"vacuum_book_states": 1, "placed_vacuum": 2}


def test_with_s1_off_the_wide_book_keeps_the_v633_price_behind_the_touch():
    agent = _agent(d633._deep_layer(), vacuum=False)
    resp = _run(agent, {3: t63._book(**WIDE)})
    want_b = dl.deep_price(100.15, 21.5, dl.SIDE_BUY, bid=100.00, ask=100.30, tick=TICK, decimals=2)
    want_a = dl.deep_price(100.15, 21.5, dl.SIDE_SELL, bid=100.00, ask=100.30, tick=TICK, decimals=2)
    assert t63._placed(resp) == {(3, "BUY", 40031, want_b, 1.0), (3, "SELL", 40032, want_a, 1.0)}
    assert want_b < 100.00 and want_a > 100.30 and agent._v64_counts == {}


def test_a_normal_spread_is_the_v633_deep_layer_unchanged():
    agent = _agent(d633._deep_layer())
    resp = _run(agent, {3: t63._book()})
    want_b = dl.deep_price(100.015, 21.5, dl.SIDE_BUY, bid=100.00, ask=100.03, tick=TICK, decimals=2)
    want_a = dl.deep_price(100.015, 21.5, dl.SIDE_SELL, bid=100.00, ask=100.03, tick=TICK, decimals=2)
    assert t63._placed(resp) == {(3, "BUY", 40031, want_b, 1.0), (3, "SELL", 40032, want_a, 1.0)}
    assert agent._v64_counts == {}


def test_a_resting_deep_order_is_judged_against_the_vacuum_distance():
    # 21 ticks from the 100.15 mid: inside [0.5, 1.5] x 21.5, outside [0.5, 1.5] x 12
    agent = _agent(d633._deep_layer())
    t63.t14._rest(agent, 21, 3, 0, 99.94, cid=40031)
    resp = _run(agent, {3: t63._book(**WIDE)})
    assert (3, 21) in t63._cancelled(resp) and agent._v633_counts.get("cancel_deep_reprice") == 1
    agent = _agent(d633._deep_layer(), vacuum=False)
    t63.t14._rest(agent, 21, 3, 0, 99.94, cid=40031)
    resp = _run(agent, {3: t63._book(**WIDE)})
    assert (3, 21) not in t63._cancelled(resp)


# ---- 3. S2 in the pass ----------------------------------------------------------------------------------------------

def test_the_open_board_trades_no_book_at_the_touch():
    agent = _agent(d633._deep_layer(books=(3,)))
    t63.t14._rest(agent, 11, 5, 0, 100.01, cid=65051)              # a v6.3 making bid on a book with no depth yet
    resp = _run(agent, {3: t63._book(), 5: t63._book()})
    assert (5, 11) in t63._cancelled(resp) and agent._v64_counts.get("cancel_v64_board_owns") == 1
    assert {p for p in t63._placed(resp) if p[0] == 5} == set()
    assert agent._v63_last["board_idle"] == 1 and agent._v63_last["board_owns"] == 1
    assert {p[0] for p in t63._placed(resp)} == {3}


def test_with_s2_off_a_book_the_layer_does_not_open_runs_v632():
    agent = _agent(d633._deep_layer(books=(3,)), owns=False)
    resp = _run(agent, {3: t63._book(), 5: t63._book()})
    assert {(p[1], p[2]) for p in t63._placed(resp) if p[0] == 5} == {("BUY", 65051), ("SELL", 65052)}
    assert agent._v63_last["board_idle"] == 0 and agent._v63_last["board_owns"] == 0


def test_an_over_limit_book_stays_deep_and_quotes_only_its_reducing_side():
    agent = _agent(d633._deep_layer(), venue={3: 3.0})             # long three clips
    resp = _run(agent, {3: t63._book()})
    assert {(p[1], p[2]) for p in t63._placed(resp)} == {("SELL", 40032)}
    assert agent._v63_last["deep_books"] == 1
    agent = _agent(d633._deep_layer(), venue={3: 3.0}, owns=False)   # v6.3.3: the book shuts to v6.3.2
    resp = _run(agent, {3: t63._book()})
    assert agent._v63_last["deep_books"] == 0 and not d633._deep_placed(resp)


def test_a_book_whose_own_record_shut_it_goes_quiet_while_the_board_is_open():
    layer = d633._deep_layer(books=(3, 4), alpha=100.0)
    layer.books[4].paper.buckets[dl.sampled_key(t63.NOW)] = [-50.0, 0.0, 1.0, 0.0]     # under -floor/2
    agent = _agent(layer)
    t63.t14._rest(agent, 41, 4, 0, 99.80, cid=40041)
    resp = _run(agent, {3: t63._book(), 4: t63._book()})
    assert (4, 41) in t63._cancelled(resp) and agent._v633_counts.get("cancel_deep_shut") == 1
    assert {p for p in t63._placed(resp) if p[0] == 4} == set()


def test_a_closed_board_hands_every_book_back_to_v632():
    agent = _agent(d633._deep_layer(alpha=-10.0))
    resp = _run(agent, {3: t63._book()})
    assert t63._placed(resp) == {(3, "BUY", 65031, 100.01, 1.0), (3, "SELL", 65032, 100.02, 1.0)}
    assert agent._v63_last["board_owns"] == 0 and agent._v63_last["board_idle"] == 0


def test_both_switches_need_the_deep_layer():
    for layer_on, sw, want in ((True, True, True), (True, False, False), (False, True, False)):
        obj = types.SimpleNamespace(research_v64_vacuum=sw, research_v64_board_owns=sw)
        obj._v633_on = lambda layer_on=layer_on: layer_on
        assert _method("_v64_vacuum_on")(obj) is want and _method("_v64_board_owns_on")(obj) is want


# ---- 4. wiring ------------------------------------------------------------------------------------------------------

def test_the_switches_default_on_ship_in_params_and_are_preflighted():
    for key in ("research_v64_vacuum", "research_v64_board_owns"):
        assert f'self.{key} = self._as_bool(getattr(self.config, "{key}", True))' in SIMPLE
        start = LAUNCHER.index('PARAMS="')
        assert f"{key}=1" in LAUNCHER[start:LAUNCHER.index('"\n', start + len('PARAMS="'))]
    assert 'echo "[preflight] v6.4 board PASS"' in LAUNCHER
    assert "tests/test_research_v6_4_board.py" in LAUNCHER


def test_the_state_row_reports_both_switches_and_the_counters():
    tele = _src("_v62_telemetry")
    for frag in ('vacuum_on=int(bool(getattr(self, "research_v64_vacuum", False)) and self._v64_vacuum_on()),',
                 'board_owns_on=int(bool(getattr(self, "research_v64_board_owns", False)) and self._v64_board_owns_on()),',
                 "board=(self._v64_snapshot()"):
        assert frag in tele, frag
    snap = _method("_v64_snapshot")(types.SimpleNamespace(_v64_counts={"placed_vacuum": 3}, _v64_errors=0))
    assert snap == {"placed_vacuum": 3, "errors": 0, "version": "board_v6_4"}
