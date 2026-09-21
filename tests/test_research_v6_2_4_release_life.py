"""v6.2.4: a v6.2.3 release takes the exit order life.

Testnet UID 82, v6.2.3 to tick 3,996 (2026-09-19 19:19 - 09-20 00:41 JST): books holding a lot ~112-122
of 128, a release resting only 35% of the holding time, release order life p50 1.0 s against 4.0 s for
v6.2.2's exits, and the fill itself favourable (markout p50 +6.1 bps at 1 s): the loss builds over the
hold while no exit rests.

Cause (STRUCTURAL, not a tuned number): the frozen placement gives the persistent exit TTL
(``research_profitable_exit_ttl_ms``) and the V4.13.8 queue hold only to an exit whose net clears
``research_profitable_exit_min_net_bps`` -- the no-loss floor restated.  v6.2.3 lifted that floor at
five sites; this was the sixth.  On a lifted book the threshold is -inf for the one frozen call; the
TTL value, its 5 s cap and the TOXIC / STRESSED exclusions are the frozen ones.  Off restores v6.2.3.
"""
import ast
import sys
import textwrap
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v623_premium_floor as pf  # noqa: E402
from research_quote_hysteresis import (  # noqa: E402
    hold_existing_profitable_maker_exit, profitable_maker_exit_ttl_ms,
)
from research_quote_lifecycle import ms_to_ns, sim_delta_ms  # noqa: E402
from research_realization import maker_exit_price  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
RESEARCH = (STRATEGY / "Strategy1_Research.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
_frozen = extractor(RESEARCH, cls_name="Strategy1_Research")
S = 1_000_000_000


# ------------------------------------------------------------------------------------------------
# 1. The frozen gates with the threshold lifted
# ------------------------------------------------------------------------------------------------

def test_the_persistent_ttl_needs_the_threshold_and_keeps_its_exclusions():
    kw = dict(baseline_ttl_ms=500.0, persistent_ttl_ms=4000.0, enabled=True)
    assert profitable_maker_exit_ttl_ms(maker_net_bps=-40.0, market_regime="NORMAL", min_net_bps=0.0, **kw) == (500.0, False)
    assert profitable_maker_exit_ttl_ms(maker_net_bps=-40.0, market_regime="NORMAL", min_net_bps=float("-inf"), **kw) == (4000.0, True)
    for regime in ("TOXIC", "STRESSED"):
        assert profitable_maker_exit_ttl_ms(maker_net_bps=-40.0, market_regime=regime, min_net_bps=float("-inf"), **kw) == (500.0, False)
    assert profitable_maker_exit_ttl_ms(maker_net_bps=float("nan"), market_regime="NORMAL", min_net_bps=float("-inf"), **kw) == (500.0, False)
    assert profitable_maker_exit_ttl_ms(maker_net_bps=-40.0, market_regime="NORMAL", min_net_bps=float("-inf"),
                                        baseline_ttl_ms=500.0, persistent_ttl_ms=9000.0)[0] == 5000.0


def test_the_queue_hold_needs_the_threshold():
    kw = dict(existing_price=300.00, desired_price=300.01, tick_size=0.01, existing_qty=0.25, desired_qty=0.25,
              maker_net_bps=-40.0)
    assert hold_existing_profitable_maker_exit(min_net_bps=0.0, **kw) is False
    assert hold_existing_profitable_maker_exit(min_net_bps=float("-inf"), **kw) is True


def test_the_frozen_placement_reads_the_threshold_only_in_those_two_gates():
    body = _frozen("_research_place_maker_exit")
    assert body.count("research_profitable_exit_min_net_bps") == 2
    assert "hold_existing_profitable_maker_exit(" in body and "profitable_maker_exit_ttl_ms(" in body


# ------------------------------------------------------------------------------------------------
# 2. The frozen placement, executed, under the v6.2.4 scope
# ------------------------------------------------------------------------------------------------

class _Dir:
    SELL, BUY = "SELL", "BUY"


class _Bal:
    def __init__(self, free):
        self.free = free


class _Account:
    def __init__(self):
        self.orders = []
        self.base_balance = _Bal(10.0)
        self.quote_balance = _Bal(1e6)


class _Level:
    def __init__(self, price):
        self.price = price


class _Response:
    def __init__(self, fail=False):
        self.orders, self.fail = [], fail

    def limit_order(self, **kw):
        if self.fail:
            raise RuntimeError("venue down")
        self.orders.append(kw)


NOW = 20_000 * S
EVENTS = {
    1: [(NOW - 30 * S, 0.1), (NOW - 20 * S, 0.1), (NOW - 10 * S, 0.1)],    # premium
    2: [(NOW - 30 * S, 0.1), (NOW - 20 * S, -0.4), (NOW - 10 * S, 0.1)],   # loss in window: lifted
}


class _Base:
    def __init__(self, v623=True, v624=True, regime="NORMAL"):
        self.research_v62_breadth = True
        self.research_v61_no_loss = True
        self.research_v626_loss_budget = False   # v6.2.6 has its own suite
        self.research_v623_premium_floor = v623
        self.research_v624_release_life = v624
        self._research_realized_pnl_events_by_book = dict(EVENTS)
        self._research_realized_generation = 0
        self.research_kappa_lookback_ns = 10_800 * S
        self.research_profitable_exit_persistence_enabled = True
        self.research_profitable_exit_ttl_ms = 4000.0
        self.research_profitable_exit_min_net_bps = 0.0
        self.research_profitable_exit_reprice_ticks = 3.0
        self._research_market_regime = regime
        self._research_contract_reject_state = {}
        self.accounts = {b: _Account() for b in (1, 2)}
        self.max_instructions_per_book = 5
        self.mm_expiry_period = 500_000_000
        self._tick = 9
        self.rows = []

    def _emit(self, kind, **kw):
        self.rows.append((kind, kw))

    def _research_in_transition_quarantine(self):
        return False

    def _count_book_instructions(self, response, book_id):
        return 0

    def _research_can_add_volume(self, state, book_id, requested):
        return True


_ns = {
    "_Base": _Base, "Any": object, "contextmanager": contextmanager,
    "OrderDirection": _Dir, "STP": types.SimpleNamespace(CANCEL_BOTH="CB"),
    "TimeInForce": types.SimpleNamespace(GTT="GTT"), "LoanSettlementOption": types.SimpleNamespace(NONE="N"),
    "maker_exit_price": maker_exit_price, "guard_is_active": lambda *a, **k: False,
    "guarded_post_only_price": lambda **k: None,
    "hold_existing_profitable_maker_exit": hold_existing_profitable_maker_exit,
    "profitable_maker_exit_ttl_ms": profitable_maker_exit_ttl_ms,
    "sim_delta_ms": sim_delta_ms, "ms_to_ns": ms_to_ns,
    "V623_BOOK_PREMIUM": pf.BOOK_PREMIUM, "V623_MIN_OBSERVATIONS": pf.KAPPA_MIN_REALIZED_OBSERVATIONS,
    "V623_PUBLISH_STEP_NS": pf.PUBLISH_STEP_NS, "V623_VOLUME_DECIMALS": pf.VOLUME_DECIMALS,
    "v623_window_census": pf.window_census, "v623_book_status": pf.book_status,
}
exec("class Harness(_Base):\n    pass\n", _ns)
exec("class Harness(Harness):\n" + textwrap.indent(textwrap.dedent(_frozen("_research_place_maker_exit")), "    "), _ns)
for _name in ("_v61_on", "_v62_on", "_v623_on", "_v623_count", "_v623_census", "_v623_lifted",
              "_v624_on", "_v624_count", "_v624_release_life", "_v626_loss_budget_on"):
    src = textwrap.dedent(_simple(_name))
    if _name == "_v624_release_life":          # the extractor returns the def without its decorator
        src = "@contextmanager\n" + src
    exec("class Harness(Harness):\n" + textwrap.indent(src, "    "), _ns)
Harness = _ns["Harness"]


def _state():
    return types.SimpleNamespace(timestamp=NOW, config=types.SimpleNamespace(
        priceDecimals=2, publish_interval=S, volumeDecimals=4))


def _book():
    return types.SimpleNamespace(bids=[_Level(297.99)], asks=[_Level(298.00)])


def _place(agent, book_id, net_bps, response=None):
    """What the Simple override's tail does: the one frozen call, inside the v6.2.4 scope."""
    response = response or _Response()
    inv = types.SimpleNamespace(net_base=0.25)
    with agent._v624_release_life(book_id, _state()):
        agent._research_place_maker_exit(response, _state(), book_id, _book(), inv, 0.25,
                                         "COMPETITIVE_MAKER_EXIT", close_price=298.00, maker_net_bps=net_bps)
    return response


def test_a_release_on_a_lifted_book_rests_with_the_exit_life():
    a = Harness()
    order = _place(a, 2, -40.0).orders[0]
    assert order["expiryPeriod"] == 4 * S and order["postOnly"] is True
    assert a.research_profitable_exit_min_net_bps == 0.0            # restored after the call
    assert a._v624_counts == {"release_life": 1}


def test_off_or_v623_off_keeps_the_v6_2_3_base_life():
    for a in (Harness(v624=False), Harness(v623=False)):
        assert _place(a, 2, -40.0).orders[0]["expiryPeriod"] == 500_000_000
        assert not getattr(a, "_v624_counts", {})


def test_a_premium_book_is_untouched():
    a = Harness()
    assert _place(a, 1, -40.0).orders[0]["expiryPeriod"] == 500_000_000   # not lifted: the gate stands
    assert _place(a, 1, 12.0).orders[0]["expiryPeriod"] == 4 * S          # a profitable exit, as before
    assert not getattr(a, "_v624_counts", {})


def test_the_frozen_regime_exclusion_still_applies():
    assert _place(Harness(regime="TOXIC"), 2, -40.0).orders[0]["expiryPeriod"] == 500_000_000


def test_the_threshold_is_restored_on_an_exception():
    a = Harness()
    with pytest.raises(RuntimeError):
        _place(a, 2, -40.0, response=_Response(fail=True))
    assert a.research_profitable_exit_min_net_bps == 0.0


# ------------------------------------------------------------------------------------------------
# 3. Wiring
# ------------------------------------------------------------------------------------------------

def test_the_scope_wraps_exactly_the_one_frozen_return():
    place = _simple("_research_place_maker_exit")
    scope = place.index("with self._v624_release_life(int(book_id), state):")
    ret = place.index("return super()._research_place_maker_exit(")
    # the return is the with-body; v6.2.8 nests exactly its own rung-cap scope (and its comment) inside
    between = [ln.strip() for ln in place[scope:ret].splitlines()[1:]]
    assert scope < ret and between in ([""], ["# v6.2.8 S1: the maker exit is priced through the capped rung.",
                                               "with self._v628_rung_cap_scope():", ""])
    assert SIMPLE.count("return super()._research_place_maker_exit") == 1
    assert "= super()._research_place_maker_exit" not in SIMPLE
    # every v6.2.3 guard still runs before the scope
    for guard in ("self._v61_apply_floor(", "and not self._v623_lifted(int(book_id), state)[0]"):
        assert place.index(guard) < scope


def test_the_decorator_and_the_restore_are_in_the_source():
    tree = ast.parse(SIMPLE)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_v624_release_life")
    assert [ast.unparse(d) for d in fn.decorator_list] == ["contextmanager"]
    body = _simple("_v624_release_life")
    assert 'self.research_profitable_exit_min_net_bps = float("-inf")' in body
    assert "finally:" in body and "self.research_profitable_exit_min_net_bps = saved" in body
    assert "from contextlib import contextmanager" in SIMPLE


def test_switch_defaults_on_needs_v623_and_telemetry_and_stats():
    assert 'getattr(self.config, "research_v624_release_life", True)' in _simple("_init_build_switches")
    assert "self._v623_on() and" in _simple("_v624_on")
    tele = _simple("_v62_telemetry")
    assert "release_life_on=int(self._v624_on())" in tele
    assert '"direct_v624_release_life"' in SIMPLE
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_9"' in SIMPLE


def test_launcher_arm_params_guard_and_gate():
    assert "strategy1_direct_v6_2_8)" in LAUNCHER and "V624_BUILD=1 ;;" in LAUNCHER
    assert "strategy1_direct_v6_2_3)" in LAUNCHER
    assert "research_v624_release_life=1" in LAUNCHER
    assert "[preflight] v6.2.4 release life PASS" in LAUNCHER
    assert "tests/test_research_v6_2_4_release_life.py" in LAUNCHER
