"""v6.3.3.1: a deep order lives as long as the deep layer keeps it.

v6.3.3 live on mainnet UID 94 (09-26, ticks 1-~200): the v6.2.14 touch-life post-pass (S1: an order the touch moved
away from is stale) and the A1.9.1 exit-reprice post-pass (the close-side order is an exit to be re-priced at the
touch) cancelled every deep order one state after it rested -- 1,223 cancelled vs 42 filled, median life one state,
so the layer rested about half the time the replay assumed.  A deep order rests behind the touch by design; the deep
layer already ends it (drift out of [0.5, 1.5] x depth, no room, a shut book, owns-book), and so do a fill and the GTT
backstop.  The exemption covers deep orders only, and only while the deep layer runs.
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
import test_research_v6_2_14_touch_life as tlt  # noqa: E402
import test_research_strategy1_direct_a1_9_1_1 as a1911  # noqa: E402
from research_direct_exit_refresh import EXIT_REPRICE  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
EXEMPTION = 'if deep_life and v633_is_deep_client_id(getattr(row, "client_id", None)):'


def _method(name, **ns):
    scope = dict(ns)
    exec(compile(ast.Module(body=[ast.parse(tlt._simple(name)).body[0]], type_ignores=[]), f"<{name}>", "exec"), scope)
    return scope[name]


def _with_globals(fn, **extra):
    """The same compiled method, with the live module's deep-layer names in its globals."""
    g = dict(fn.__globals__)
    g.update(extra)
    return types.FunctionType(fn.__code__, g, fn.__name__, fn.__defaults__, fn.__closure__)


_V6331_ON = _method("_v6331_on")
_V633_COUNT = _method("_v633_count")


# ---- 1. the touch-life post-pass (v6.2.14 S1/S2) --------------------------------------------------------------

def _life(*, deep_life=True, layer_on=True, **kw):
    agent = tlt._life_agent(**kw)
    cls = type(agent)
    fn = _with_globals(cls._v6214_service_touch_life, v633_is_deep_client_id=dl.is_deep_client_id)
    agent.__class__ = type("D", (cls,), {
        "_v6214_service_touch_life": fn, "_v6331_on": _V6331_ON, "_v633_count": _V633_COUNT,
        "_v633_on": lambda self: layer_on,
    })
    agent.research_v6331_deep_life = deep_life
    agent._v633_counts = {}
    return agent


def _deep_and_entry_behind(agent):
    buy_cid, _sell_cid = dl.client_ids(3)
    tlt._rest(agent, 11, 3, 0, 99.60, cid=buy_cid, qty=1.0)     # deep bid, 40 ticks behind the 100.00 bid
    tlt._rest(agent, 12, 3, 0, 99.99, cid=70031)               # an entry quote one tick behind


def test_touch_life_leaves_a_deep_order_to_the_deep_layer():
    agent = _life()
    _deep_and_entry_behind(agent)
    resp = tlt._Resp()
    agent._v6214_service_touch_life(resp, tlt._state({3: tlt._touch_book()}))
    assert tlt._cancelled(resp) == {(3, 12)}                   # the touch order still goes
    assert agent._v633_counts == {"life_kept_touch": 1}


def test_with_the_switch_off_touch_life_cancels_the_deep_order_as_v6_3_3_did():
    agent = _life(deep_life=False)
    _deep_and_entry_behind(agent)
    resp = tlt._Resp()
    agent._v6214_service_touch_life(resp, tlt._state({3: tlt._touch_book()}))
    assert tlt._cancelled(resp) == {(3, 11), (3, 12)}
    assert agent._v633_counts == {}


def test_without_the_deep_layer_touch_life_owns_a_deep_client_id():
    agent = _life(layer_on=False)
    _deep_and_entry_behind(agent)
    resp = tlt._Resp()
    agent._v6214_service_touch_life(resp, tlt._state({3: tlt._touch_book()}))
    assert tlt._cancelled(resp) == {(3, 11), (3, 12)}


def test_a_book_holding_only_deep_orders_takes_no_touch_life_instruction():
    agent = _life()
    buy_cid, sell_cid = dl.client_ids(3)
    tlt._rest(agent, 21, 3, 0, 99.60, cid=buy_cid, qty=1.0)
    tlt._rest(agent, 22, 3, 1, 100.41, cid=sell_cid, qty=1.0)
    resp = tlt._Resp()
    assert agent._v6214_service_touch_life(resp, tlt._state({3: tlt._touch_book()})) == 0
    assert resp.instructions == [] and agent._v633_counts == {"life_kept_touch": 2}


# ---- 2. the A1.9.1 exit-reprice post-pass --------------------------------------------------------------------

class _A19(a1911._Agent):
    research_v6331_deep_life = True

    def __init__(self, *, layer_on=True, deep_life=True):
        super().__init__()
        self._v633_counts = {}
        self._layer_on = layer_on
        self.research_v6331_deep_life = deep_life

    def _v633_on(self):
        return self._layer_on


_A19._a191_service_reprice_cancels = _with_globals(
    a1911._Agent._a191_service_reprice_cancels, v633_is_deep_client_id=dl.is_deep_client_id)
_A19._v6331_on = _V6331_ON
_A19._v633_count = _V633_COUNT


def _held_long(cid, **kw):
    """Long 0.5 on BOOK; its close-side (sell) order rests 40 ticks above the 100.20 ask."""
    a = _A19(**kw)
    a.positions[a1911.BOOK] = (0.5, 100.00)
    a._a19_ledger.note_accepted(order_id=777, book_id=a1911.BOOK, side=1, price=100.60, quantity=0.5,
                                timestamp_ns=a1911.T0, tick=1, client_id=cid)
    return a


def test_the_exit_reprice_pass_leaves_a_close_side_deep_order_to_the_deep_layer():
    a = _held_long(dl.client_ids(a1911.BOOK)[1])
    r = a1911._Response()
    assert a._a191_service_reprice_cancels(r, a1911._state()) == 0
    assert r.cancels == [] and a._v633_counts == {"life_kept_reprice": 1}
    verdict = a._a191_verdicts[a1911.BOOK]
    # the verdict still names the deep order: the exit placement path returns 0 on any verdict, so no second order
    # goes on the close side while the deep order rests there
    assert verdict["decision"] == EXIT_REPRICE and verdict["order_id"] == 777 and not verdict["cancelled"]


def test_with_the_switch_off_the_exit_reprice_pass_cancels_it_as_v6_3_3_did():
    a = _held_long(dl.client_ids(a1911.BOOK)[1], deep_life=False)
    r = a1911._Response()
    assert a._a191_service_reprice_cancels(r, a1911._state()) == 1
    assert r.cancels == [(a1911.BOOK, [777])] and a._v633_counts == {}


def test_without_the_deep_layer_the_exit_reprice_pass_owns_a_deep_client_id():
    a = _held_long(dl.client_ids(a1911.BOOK)[1], layer_on=False)
    r = a1911._Response()
    assert a._a191_service_reprice_cancels(r, a1911._state()) == 1
    assert r.cancels == [(a1911.BOOK, [777])]


def test_a_stale_exit_that_is_not_deep_is_still_repriced():
    for cid in (None, 80000 + 10 * a1911.BOOK + 2, 60000 + 10 * a1911.BOOK + 2):
        a = _held_long(cid)
        r = a1911._Response()
        assert a._a191_service_reprice_cancels(r, a1911._state()) == 1, cid
        assert r.cancels == [(a1911.BOOK, [777])] and a._v633_counts == {}, cid


def test_the_exit_placement_path_holds_on_any_verdict():
    """The safety of leaving the verdict uncancelled: the placement path places nothing while a verdict exists."""
    src = tlt._simple("_research_place_maker_exit")
    hold = src.index("if verdict is not None:")
    assert "return 0" in src[hold:src.index("authority = self._direct_current_exit_authority", hold)]


# ---- 3. the switch, its scope and its wiring ------------------------------------------------------------------

def test_v6331_on_needs_the_deep_layer_and_its_own_switch():
    for layer, switch, want in ((True, True, True), (True, False, False), (False, True, False), (False, False, False)):
        obj = types.SimpleNamespace(research_v6331_deep_life=switch)
        obj._v633_on = lambda layer=layer: layer
        assert _V6331_ON(obj) is want, (layer, switch)


def test_the_exemption_sits_in_exactly_the_two_touch_order_post_passes():
    assert SIMPLE.count(EXEMPTION) == 2
    assert EXEMPTION in tlt._simple("_v6214_service_touch_life")
    assert EXEMPTION in tlt._simple("_a191_service_reprice_cancels")
    for name in ("_v6214_service_touch_life", "_a191_service_reprice_cancels"):
        assert 'deep_life = bool(getattr(self, "research_v6331_deep_life", False)) and self._v6331_on()' in \
            tlt._simple(name), name


def test_the_deep_layer_still_ends_its_own_orders():
    book = tlt._simple("_v633_book")
    for why in ("V633_CANCEL_OWNS_BOOK", "V633_CANCEL_NO_ROOM", "V633_CANCEL_REPRICE", "expiryPeriod=expiry"):
        assert why in book, why
    assert "doomed.append((row, V633_CANCEL_SHUT))" in tlt._simple("_v63_pass")


def test_the_switch_defaults_on_ships_in_params_and_is_preflighted():
    assert 'self.research_v6331_deep_life = self._as_bool(getattr(self.config, "research_v6331_deep_life", True))' \
        in SIMPLE
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"\n', start + len('PARAMS="'))]
    assert "research_v6331_deep_life=1" in params
    assert 'echo "[preflight] v6.3.3.1 deep life PASS"' in LAUNCHER
    assert "tests/test_research_v6_3_3_1_deep_life.py" in LAUNCHER


def test_the_state_row_reports_deep_life():
    assert 'deep_life_on=int(bool(getattr(self, "research_v6331_deep_life", False)) and self._v6331_on()),' \
        in tlt._simple("_v62_telemetry")
