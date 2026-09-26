"""v6.3.3.2: the A1.7.3 partial/dust recovery leaves a deep order to the deep layer.

v6.3.3.1 live on mainnet UID 94 (09-26, ticks 1-~280): with the touch-life and exit-reprice passes exempted, the one
foreign canceller of deep orders left was ``_direct_service_partial_fill_recovery``.  A book holding dust (fees charged
in base leave it on the most active books) keeps a NORMALIZE registry row with no bound order, and every order on the
book then counts as conflicting and is cancelled each request -- 521 of the deep orders' foreign cancels, on 33 of the
49 deep books, which are the books where the sweeps are.  The exemption covers deep orders only (by the order's own
client id or the one the ledger recorded), only while the deep layer runs; the registry row and the dust stay.
"""
import ast
import sys
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v633_deep_layer as dl  # noqa: E402
import test_research_v6_0_3_short_lot_release as t603  # noqa: E402
from research_direct_exit_ledger import DirectExitLedger  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
BOOK = 107
DUST = 0.1142                                   # book 107's dust on v6.3.3.1, tick 150
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")


def _method(name, **ns):
    scope = dict(ns)
    exec(compile(ast.Module(body=[ast.parse(_src(name)).body[0]], type_ignores=[]), f"<{name}>", "exec"),
         scope)
    return scope[name]


def _with_globals(fn, **extra):
    g = dict(fn.__globals__)
    g.update(extra)
    return types.FunctionType(fn.__code__, g, fn.__name__, fn.__defaults__, fn.__closure__)


_V6332_ON = _method("_v6332_on")
_DEEP_IDS = _method("_v6332_deep_order_ids", v633_is_deep_client_id=dl.is_deep_client_id)
_CLIENT_ID = _method("_direct_order_client_id")
_COUNT = _method("_v633_count")


def _agent(*, switch=True, layer=True):
    agent = t603._agent()
    cls = type(agent)
    fn = _with_globals(cls._direct_service_partial_fill_recovery, v633_is_deep_client_id=dl.is_deep_client_id)
    agent.__class__ = type("D", (cls,), {
        "_direct_service_partial_fill_recovery": fn, "_v6332_on": _V6332_ON, "_v6332_deep_order_ids": _DEEP_IDS,
        "_direct_order_client_id": _CLIENT_ID, "_v633_count": _COUNT,
        "_v633_on": lambda self: layer, "_a19_ledger_ref": lambda self: self.ledger,
    })
    agent.research_v6332_deep_partial = switch
    agent._v633_counts = {}
    agent.ledger = DirectExitLedger()
    return agent


def _order(oid, side, cid=None):
    o = SimpleNamespace(id=int(oid), side=side, price=100.0, quantity=1.0)
    if cid is not None:
        o.client_id = int(cid)
    return o


def _dust_book(agent, orders, *, hold=False, bound=None):
    """Book 107 holding dust under a NORMALIZE row; with no active bound hold every order on it conflicts."""
    agent.net[BOOK] = DUST
    agent.holds[BOOK] = hold
    agent.orders[BOOK] = list(orders)
    agent._direct_partial_recovery[BOOK] = {
        "mode": "NORMALIZE", "desired_side": "buy", "bound_order_id": bound,
        "target_inventory": DUST + 0.25, "preserve_existing_remainder": True,
    }
    return agent


DEEP_BUY, DEEP_SELL = dl.client_ids(BOOK)
V63_SELL = 60000 + 10 * BOOK + 2


def test_the_dust_recovery_leaves_a_deep_order_and_still_cancels_the_rest():
    agent = _dust_book(_agent(), [_order(1, "buy", DEEP_BUY), _order(2, "sell", V63_SELL)])
    response, instructions, _holds = t603._run(agent)
    assert response.cancels == [(BOOK, [2])] and instructions == 1
    assert agent._v633_counts == {"life_kept_partial": 1}
    assert BOOK in agent._direct_partial_recovery                # the row and the dust stay


def test_a_deep_order_known_only_to_the_ledger_is_left_too():
    agent = _dust_book(_agent(), [_order(3, "sell")])            # the account view carries no client id
    agent.ledger.note_accepted(order_id=3, book_id=BOOK, side=1, price=100.0, quantity=1.0,
                               timestamp_ns=1, tick=1, client_id=DEEP_SELL)
    response, instructions, _holds = t603._run(agent)
    assert response.cancels == [] and instructions == 0
    assert agent._v633_counts == {"life_kept_partial": 1}


def test_a_ledger_row_on_another_book_does_not_make_an_order_deep():
    agent = _dust_book(_agent(), [_order(4, "sell")])
    agent.ledger.note_accepted(order_id=4, book_id=5, side=1, price=100.0, quantity=1.0,
                               timestamp_ns=1, tick=1, client_id=dl.client_ids(5)[1])
    response, _instructions, _holds = t603._run(agent)
    assert response.cancels == [(BOOK, [4])]


def test_a_book_holding_only_deep_orders_takes_no_recovery_instruction():
    agent = _dust_book(_agent(), [_order(5, "buy", DEEP_BUY), _order(6, "sell", DEEP_SELL)])
    response, instructions, _holds = t603._run(agent)
    assert response.cancels == [] and instructions == 0
    assert t603._rows(agent, "A173_PARTIAL_REMAINDER_CANCEL") == []
    assert agent._v633_counts == {"life_kept_partial": 2}


def test_with_the_switch_off_the_recovery_cancels_the_deep_order_as_v6_3_3_1_did():
    agent = _dust_book(_agent(switch=False), [_order(1, "buy", DEEP_BUY), _order(2, "sell", V63_SELL)])
    response, _instructions, _holds = t603._run(agent)
    assert response.cancels == [(BOOK, [1, 2])] and agent._v633_counts == {}


def test_without_the_deep_layer_the_recovery_owns_a_deep_client_id():
    agent = _dust_book(_agent(layer=False), [_order(1, "buy", DEEP_BUY)])
    response, _instructions, _holds = t603._run(agent)
    assert response.cancels == [(BOOK, [1])]


def test_an_active_bound_hold_keeps_its_remainder_and_spares_the_deep_order():
    agent = _dust_book(_agent(), [_order(946119, "buy"), _order(7, "sell", DEEP_SELL), _order(8, "sell", V63_SELL)],
                       hold=True, bound=946119)
    response, instructions, holds = t603._run(agent)
    assert response.cancels == [(BOOK, [8])] and (instructions, holds) == (1, 1)
    assert agent._v633_counts == {"life_kept_partial": 1}


def test_a_partially_filled_deep_order_bound_by_the_hold_is_still_its_live_remainder():
    """The partition runs first: a deep remainder the hold names stays the hold's live remainder (HOLD, not PENDING)."""
    agent = _dust_book(_agent(), [_order(9, "buy", DEEP_BUY), _order(10, "sell", V63_SELL)], hold=True, bound=9)
    response, _instructions, holds = t603._run(agent)
    assert response.cancels == [(BOOK, [10])] and holds == 1
    held = t603._rows(agent, "A173_PARTIAL_REMAINDER_HOLD")
    assert len(held) == 1 and held[0]["bound_order_id"] == 9 and held[0]["live_remainder_orders"] == 1
    assert t603._rows(agent, "A1731_PARTIAL_REMAINDER_PENDING") == []


def test_v6332_on_needs_the_deep_layer_and_its_own_switch():
    for layer, switch, want in ((True, True, True), (True, False, False), (False, True, False), (False, False, False)):
        obj = types.SimpleNamespace(research_v6332_deep_partial=switch)
        obj._v633_on = lambda layer=layer: layer
        assert _V6332_ON(obj) is want, (layer, switch)


def test_the_exemption_sits_in_the_recovery_behind_its_own_switch():
    body = _src("_direct_service_partial_fill_recovery")
    assert 'deep_partial = bool(getattr(self, "research_v6332_deep_partial", False)) and self._v6332_on()' in body
    assert "deep_ids = self._v6332_deep_order_ids(int(book_id), order_by_id) & set(conflicting_ids)" in body
    # the partition that keeps a bound remainder runs first; only the cancel list loses the deep orders
    assert body.index("direct_partition_bound_remainder_orders(") < body.index("deep_ids = self._v6332_deep_order_ids")
    assert body.index("deep_ids = self._v6332_deep_order_ids") < body.index("response.cancel_orders(")


def test_the_switch_defaults_on_ships_in_params_and_is_preflighted():
    assert ('self.research_v6332_deep_partial = self._as_bool(getattr(self.config, "research_v6332_deep_partial", '
            'True))') in SIMPLE
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"\n', start + len('PARAMS="'))]
    assert "research_v6332_deep_partial=1" in params
    assert 'echo "[preflight] v6.3.3.2 deep partial PASS"' in LAUNCHER
    assert "tests/test_research_v6_3_3_2_deep_partial.py" in LAUNCHER


def test_the_state_row_reports_deep_partial():
    assert 'deep_partial_on=int(bool(getattr(self, "research_v6332_deep_partial", False)) and self._v6332_on()),' \
        in _src("_v62_telemetry")
