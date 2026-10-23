"""v6.23: the validator's 0.6.3 release -- research_v623_validator_063.

v6.22 + three switches.  S1 (research_v623_realized_mode, auto: active from a 0.6.3 validator on, read from each state's
version): the deep side trading against a book's fresh fundamental holds no level-0 order -- a resting one is cancelled,
none is placed -- levels 1/2, the other side and the touch quote unchanged; research_v623_against_levels=deep withholds
every level of that side, research_v623_against_gap_bps gates it on |log(fp / mid)|.  S2 (research_v623_class_guard): a
book of another asset class (its minimum order or grid differs from the top-level configuration's) keeps its cancels and
takes no new order, and the final validator drops any placement on it; the vendored protocol keeps the configuration's
classes.  S3 (research_v623_start_once): one PnL reset per simulation start (0.6.3 sends one start notice per
realization), and an exception out of it never fails the request.  Inactive, on a single market and with one start
notice the build is v6.22.  Shipped: S1 off (research_v623_realized_mode=0; the calibrated 0.6.3 replay, scratchpad k48,
finds every rule that thins the against side losing), S2 and S3 on.
"""
import ast
import copy
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import types
from collections import defaultdict, deque
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v616_fair_value as fv  # noqa: E402
import research_v623_validator_063 as vo  # noqa: E402
import research_v62_breadth as br  # noqa: E402
import research_v633_deep_layer as dl  # noqa: E402
import research_v612_reduce_depth as d612  # noqa: E402
import research_v621_young_window as yw  # noqa: E402
import research_v617_touch_quote as tq  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_9 as t69  # noqa: E402
import test_research_v6_16 as t616  # noqa: E402
import test_research_v6_17 as t617  # noqa: E402
import test_research_v6_19 as t619  # noqa: E402
import test_research_v6_22 as t622  # noqa: E402
from _harness import extractor, method_source  # noqa: E402
import _v638_undo  # noqa: E402
from _harness import simple_text, read_source, copy_source, strategy_view  # noqa: E402

SIMPLE = simple_text()
LAUNCHER = read_source(ROOT / "run_strategy1_research_simple_multi.sh")
MODELS = read_source(ROOT / "taos" / "im" / "protocol" / "models.py")
TAOS_AGENTS = read_source(ROOT / "taos" / "im" / "agents" / "__init__.py")
DTA = read_source(STRATEGY / "DetailedTemplateAgent.py")
S1SRC = read_source(STRATEGY / "Strategy1.py")
RESEARCH = read_source(STRATEGY / "Strategy1_Research.py")
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000
NOW = t622.NOW
SIM = t616.SIM
UID = t622.UID
U_FREE = t622.U_FREE
CAP = t622.CAP
BUY, SELL = dl.SIDE_BUY, dl.SIDE_SELL
ABOVE, BELOW = 100.40, 99.60          # book 3 (100.00 / 100.03, mid 100.015): 38 bps above / below
OBSERVE_LINE = "self._v6_23_observe(state)"
FOREIGN_LINE = "cap_skip = self._v6_23_foreign_skip(book_id)"
# v6.25 S5 hands S1 the leaned inventory (v6_25_inv = inv - tau; inv itself without a lean)
AGAINST_LINE = "against = (self._v6_23_against(book_id, raw_bid, raw_ask, cfg, v6_25_inv)"
JUDGE_LINE = "doomed.append((row, V623_CANCEL_AGAINST))"
SKIP_LINE = "continue                    # v6.23 S1: no order on the side trading against the fundamental"
DROP_LINE = "book_id, side, None, None, None, V623_REJECT_FOREIGN, False,"
V623_NAMES = {
    "V623_CANCEL_AGAINST": vo.CANCEL_AGAINST, "V623_GAP_BPS_DEFAULT": vo.GAP_BPS_DEFAULT, "V623_LAYOUT_SINGLE": vo.LAYOUT_SINGLE,
    "V623_LAYOUT_UNREADABLE": vo.LAYOUT_UNREADABLE, "V623_LEVELS_L0": vo.LEVELS_L0,
    "V623_REJECT_FOREIGN": vo.REJECT_FOREIGN, "V623_VALIDATOR_063_VERSION": vo.V623_VALIDATOR_063_VERSION,
    "v623_against_side": vo.against_side, "v623_class_layout": vo.class_layout, "v623_parse_gap_bps": vo.parse_gap_bps,
    "v623_parse_levels": vo.parse_levels, "v623_parse_mode": vo.parse_mode, "v623_realized_active": vo.realized_active,
    "v623_start_key": vo.start_key,
    "v623_validator_spec": vo.validator_spec, "v623_withheld": vo.withheld,
    "V62_STATE_EVERY_TICKS": br.V62_STATE_EVERY_TICKS, "v616_book_fundamental": fv.book_fundamental,
}
EXTRA = dict(t622.EXTRA, **V623_NAMES)
METHODS = ("_v6_23_count", "_v6_23_observe", "_v6_23_fundamental", "_v6_23_against", "_v6_23_foreign_skip",
           "_v6_23_snapshot")


def _classes(min1=2.5364, pd1=2, vd1=4, split=96, n=128):
    """A 0.6.3 multi-asset configuration's classes as the vendored protocol keeps them (raw)."""
    return [{"name": "simulation_0", "books": list(range(split)), "priceDecimals": 2, "volumeDecimals": 4,
             "config": {"min_order_size": 0.25, "priceDecimals": 2, "volumeDecimals": 4, "init_price": 300.0}},
            {"name": "simulation_1", "books": list(range(split, n)), "priceDecimals": pd1, "volumeDecimals": vd1,
             "config": {"min_order_size": min1, "priceDecimals": pd1, "volumeDecimals": vd1, "init_price": 29.57}}]


def _config(classes=None, **kw):
    out = {"min_order_size": 0.25, "priceDecimals": 2, "volumeDecimals": 4, "classes": classes}
    out.update(kw)
    return types.SimpleNamespace(**out)


# ---- 1. the module: the switches' values and S1's rule --------------------------------------------------------------

def test_the_switch_values_parse_to_their_defaults_when_unreadable():
    for value, want in (("auto", "auto"), (" AUTO ", "auto"), (None, "auto"), (1.0, "on"), (1, "on"), ("1", "on"),
                        (True, "on"), ("on", "on"), (0.0, "off"), (0, "off"), ("0", "off"), (False, "off"),
                        ("off", "off"), ("2", "auto"), (2.0, "auto"), ("x", "auto"), (float("nan"), "auto")):
        assert vo.parse_mode(value) == want, value
    for value, want in (("L0", "L0"), ("deep", "deep"), (" DEEP ", "deep"), ("l1", "L0"), (None, "L0"), (2.0, "L0")):
        assert vo.parse_levels(value) == want, value
    for value, want in ((0.0, 0.0), ("2.5", 2.5), (3, 3.0), (-1.0, 0.0), ("x", 0.0), (None, 0.0), (float("inf"), 0.0)):
        assert vo.parse_gap_bps(value) == want, value


def test_s1_is_active_from_a_0_6_3_validator_on_in_auto_always_on_and_never_off():
    assert vo.REALIZED_SPEC == 63                                    # taos 0.6.3: 1000 x 0 + 10 x 6 + 3
    for spec, want in ((63, True), (64, True), (1000, True), ("63", True), (63.0, True), (62, False), (55, False),
                       (None, False), (63.5, False), (-63, False), ("x", False), (True, False)):
        assert vo.realized_active("auto", spec) is want, spec
        if want:
            assert vo.validator_spec(spec) == int(float(spec)), spec
    assert vo.validator_spec(62) == 62 and vo.validator_spec(None) is None and vo.validator_spec(63.5) is None
    for spec in (None, 62, 63):
        assert vo.realized_active("on", spec) and not vo.realized_active("off", spec)
        assert not vo.realized_active("other", spec) and not vo.realized_active(None, spec)


def test_s1_the_against_side_is_the_other_side_of_the_touch_quotes_toward_side():
    assert vo.against_side(100.015, ABOVE) == SELL and vo.against_side(100.015, BELOW) == BUY
    assert vo.against_side(100.015, 100.015) is None
    for mid, fp in ((100.015, ABOVE), (100.015, BELOW), (100.0, 100.0000001), (29.57, 29.56), (300.0, 300.02)):
        toward = tq.toward_side(mid, fp)
        assert vo.against_side(mid, fp) == ({BUY: SELL, SELL: BUY}[toward] if toward else None), (mid, fp)
    for bad in ((None, 100.0), (100.0, None), (0.0, 100.0), (100.0, -1.0), ("x", 100.0), (float("nan"), 100.0)):
        assert vo.against_side(*bad) is None, bad


def test_s1_the_gap_gate_is_the_log_distance_of_the_fundamental_from_the_mid_in_bps():
    gap = abs(math.log(100.04 / 100.015)) * 1e4                                # 2.50 bps
    assert 2.49 < gap < 2.51
    assert vo.against_side(100.015, 100.04, gap) == SELL                         # exactly at the gap: withheld
    assert vo.against_side(100.015, 100.04, 2.0) == SELL and vo.against_side(100.015, 100.04, 2.4) == SELL
    assert vo.against_side(100.015, 100.04, 2.6) is None and vo.against_side(100.015, 99.99, 2.6) is None
    assert vo.against_side(100.015, 100.04, 0.0) == SELL and vo.against_side(100.015, 100.04, -5.0) == SELL


def test_s1_the_gap_default_is_the_touch_quotes_threshold():
    """The replay exercised the rule only where the touch quote rested THRESHOLD_BPS from the fundamental (k48)."""
    assert vo.GAP_BPS_DEFAULT == tq.THRESHOLD_BPS == 2.3
    assert vo.against_side(100.015, 100.02, vo.GAP_BPS_DEFAULT) is None                 # 0.5 bps: inside the gate
    assert vo.against_side(100.015, 100.04, vo.GAP_BPS_DEFAULT) == SELL                 # 2.5 bps: past it


def test_s1_l0_withholds_level_0_of_the_side_and_deep_every_level():
    assert dl.DEEP_LEVELS == 3
    assert vo.withheld(SELL, "L0") == frozenset({(SELL, 0)}) and vo.withheld(BUY, "L0") == frozenset({(BUY, 0)})
    assert vo.withheld(SELL, "deep") == frozenset({(SELL, 0), (SELL, 1), (SELL, 2)})
    assert vo.withheld(None, "L0") == frozenset() and vo.withheld("both", "deep") == frozenset()


def test_s1_adding_withholds_the_side_only_while_its_fill_would_not_reduce_the_position():
    """k48 agdeep=2 / agl0=3: never open or grow a position against the fundamental; unwinding one built toward it stays."""
    for inv, sell_adds in ((0.0, True), (-3.0, True), (3.0, False), (1e-12, True), (None, True), ("x", True)):
        got = vo.withheld(SELL, "deep", adding_only=True, inventory=inv)
        assert got == (vo.withheld(SELL, "deep") if sell_adds else frozenset()), inv
        assert vo.withheld(SELL, "deep", adding_only=False, inventory=inv) == vo.withheld(SELL, "deep"), inv
    for inv, buy_adds in ((0.0, True), (3.0, True), (-3.0, False)):
        assert (vo.withheld(BUY, "L0", adding_only=True, inventory=inv) == frozenset({(BUY, 0)})) is buy_adds, inv


# ---- 2. the module: S2's classes and S3's key -----------------------------------------------------------------------------

def test_s2_a_class_whose_minimum_or_grid_differs_from_the_top_level_is_foreign():
    foreign, status = vo.class_layout(_config(_classes()))
    assert status == vo.LAYOUT_FOREIGN and foreign == frozenset(range(96, 128))     # 0.6.3's 96 + 32 layout
    assert vo.class_layout(_config(_classes(min1=0.25))) == (frozenset(), vo.LAYOUT_SAME)
    assert vo.class_layout(_config(_classes(min1=0.25, pd1=3)))[0] == frozenset(range(96, 128))   # price grid
    assert vo.class_layout(_config(_classes(min1=0.25, vd1=2)))[0] == frozenset(range(96, 128))   # volume grid
    assert vo.class_layout(_config(_classes(min1=0.025)))[0] == frozenset(range(96, 128))         # smaller is foreign too
    assert vo.class_layout(_config(_classes(min1=0.25 + 1e-15)))[1] == vo.LAYOUT_SAME             # the same float
    bare = _classes(min1=0.25)
    for cls in bare:
        del cls["priceDecimals"], cls["volumeDecimals"]                          # the class's own config carries them
    bare[1]["config"]["volumeDecimals"] = 3
    assert vo.class_layout(_config(bare))[0] == frozenset(range(96, 128))
    for cls in bare:
        cls["config"] = None                                                     # nothing to compare: not foreign
    assert vo.class_layout(_config(bare)) == (frozenset(), vo.LAYOUT_SAME)
    top_unread = _config(_classes(), min_order_size=None)
    assert vo.class_layout(top_unread) == (frozenset(), vo.LAYOUT_SAME)          # the top level carries no minimum


def test_s2_a_single_market_or_an_unreadable_layout_has_no_foreign_book():
    for cfg in (None, _config(None), _config([]), types.SimpleNamespace(min_order_size=0.25), {"classes": None}):
        assert vo.class_layout(cfg) == (frozenset(), vo.LAYOUT_SINGLE), cfg
    for junk in ("x", {"a": 1}, [5], [{"books": "x"}], [{"books": None}], [{"books": [1, "y"]}], [[1, 2], None]):
        assert vo.class_layout(_config(junk)) == (frozenset(), vo.LAYOUT_UNREADABLE), junk
    assert vo.class_layout({"min_order_size": 0.25, "priceDecimals": 2, "volumeDecimals": 4,
                            "classes": _classes()})[0] == frozenset(range(96, 128))   # a raw dict reads the same


def test_s3_the_start_key_is_the_state_carrying_the_notices():
    """seq: the agent's count of states (one per update()); the state's timestamp."""
    assert vo.start_key(7, 600 * S) == ("state", 7, 600 * S) == vo.start_key(7.0, str(600 * S))
    assert vo.start_key(8, 600 * S) != vo.start_key(7, 600 * S) != vo.start_key(7, 605 * S)
    for bad in ((None, 600 * S), (7, None), ("x", 1), (7.5, 1), (float("nan"), 1)):
        assert vo.start_key(*bad) is None, bad


# ---- 3. the vendored protocol keeps the classes ------------------------------------------------------------------------

def test_s2_the_vendored_config_keeps_the_classes_raw_and_can_never_fail_on_them():
    tree = ast.parse(MODELS)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MarketSimulationConfig")
    fields = [n for n in cls.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)]
    names = [n.target.id for n in fields]
    field = fields[names.index("classes")]
    assert isinstance(field.annotation, ast.Name) and field.annotation.id == "Any"           # no validation at all
    assert isinstance(field.value, ast.Constant) and field.value.value is None              # absent: None (0.6.2)
    assert names.index("book_count") < names.index("classes") < names.index("book_levels")
    assert "from typing import Literal, Any" in MODELS
    assert MODELS.count("classes : Any = None") == 1


# ---- 4. the agent: a copy of one v6.22 harness agent per test ---------------------------------------------------------

def _with23(fn, names=None):
    g = dict(fn.__globals__)
    g.update(EXTRA if names is None else names)
    f = types.FunctionType(fn.__code__, g, fn.__name__, fn.__defaults__, fn.__closure__)
    f.__kwdefaults__ = fn.__kwdefaults__
    return f


_TEMPLATE: list = []


def _template():
    """One v6.22 harness agent (t622's template) with the v6.23 methods; a deep copy of it is a fresh agent."""
    if not _TEMPLATE:
        agent = copy.deepcopy(t622._template())
        cls = type(agent)
        methods = {n: _with23(getattr(cls, n)) for n in ("_v63_pass", "_v633_book", "_v617_touch", "_v617_book")}
        base = dict(getattr(cls, "_v63_pass").__globals__)
        base.update(EXTRA)
        for n in METHODS:
            methods[n] = t619._method(n, base)
        agent.__class__ = type("P623", (cls,), methods)
        _TEMPLATE.append(agent)
    return _TEMPLATE[0]


def _agent(*, used=U_FREE, inv=None, pace=None, page="default", mode="auto", levels="L0", gap=0.0, adding=False,
           guard=True, once=True):
    """The v6.22 harness agent (fundamental 38 bps above book 3's mid unless ``page`` says otherwise) with v6.23."""
    agent = copy.deepcopy(_template())
    agent.reading, agent.inv22 = {"used": float(used)}, inv
    agent._v66_pace = pace if pace is not None else yw.YoungWindowLine(launch=False)
    agent.research_v622_cap_window_pace = agent.research_v622_unknown_aged = agent.research_v622_cap_skip = True
    agent.research_v66_cap_closed = True
    agent._v6_22_counts, agent._v6_22_errors = {}, 0
    agent.research_v623_realized_mode = vo.parse_mode(mode)
    agent.research_v623_against_levels = vo.parse_levels(levels)
    agent.research_v623_against_gap_bps = vo.parse_gap_bps(gap)
    agent.research_v623_against_adding = adding
    agent.research_v623_class_guard, agent.research_v623_start_once = guard, once
    agent._v6_23_counts, agent._v6_23_errors = {}, 0
    agent._v6_23_spec, agent._v6_23_active, agent._v6_23_foreign = None, False, frozenset()
    agent._v6_23_layout, agent._v6_23_reported, agent._v6_23_report = vo.LAYOUT_SINGLE, False, None
    agent._v6_23_start_key, agent._v6_23_start_error = None, None
    if page != "default":
        agent._v616_feed = t616._Feed(page)
    agent.emitted = []
    agent._emit = lambda event, force=False, **kw: agent.emitted.append((event, kw))
    return agent


def _ref(*, used=U_FREE, inv=None, pace=None, page="default"):
    """The v6.22 harness agent: no v6.23 attribute at all."""
    agent = t622._agent(used=used, inv=inv, pace=pace)
    if page != "default":
        agent._v616_feed = t616._Feed(page)
    return agent


def _state(*, ts=NOW, version=63, classes=None, book=None):
    state = t616._state({3: book or t63._book()})
    state.timestamp = int(ts)
    state.version = version
    state.config.min_order_size = 0.25
    state.config.classes = classes
    return state


def _run(agent, *, ts=NOW, version=63, classes=None, pre=0, book=None):
    """One request as respond() makes it: the v6.23 observation first, then the pass."""
    resp = t63._Resp()
    for i in range(pre):
        resp.cancel_orders(book_id=3, order_ids=[900 + i])
    state = _state(ts=ts, version=version, classes=classes, book=book)
    agent._v6_23_observe(state)
    agent._v63_pass(resp, state, {})
    return resp


def _run_ref(agent, *, ts=NOW, pre=0, book=None):
    resp = t63._Resp()
    for i in range(pre):
        resp.cancel_orders(book_id=3, order_ids=[900 + i])
    state = t616._state({3: book or t63._book()})
    state.timestamp = int(ts)
    agent._v63_pass(resp, state, {})
    return resp


_out = t622._out
_rest_both = t622._rest_both          # level 0 at its price on both sides: the deep layer places nothing
_rest_doomed = t622._rest_doomed      # level 0 far from its depth (repriced) and a touch offer (flipped)


# ---- 5. inactive: v6.22 exactly ------------------------------------------------------------------------------------------

def _scenarios():
    pages = (("default", "fp above"), (t616._page({3: BELOW}), "fp below"), (t616._page({3: 100.015}), "fp at the mid"),
             (None, "no feed"), (t616._page({3: ABOVE}, sim_ts_ns=NOW - 30 * S), "stale"),
             (t616._page({3: ABOVE}, sim_id="20260929_2015"), "other simulation"), (t616._page({5: ABOVE}), "other book"))
    for page, pname in pages:
        for inv in (None, 3.0, -3.0, 60.0):
            for used in (U_FREE, CAP):
                for rest in (None, _rest_both, _rest_doomed):
                    yield page, pname, inv, used, rest


def test_inactive_the_pass_is_v622_exactly_request_after_request():
    """Auto with a 0.6.2 validator (62), a state without a version, and mode 0 with a 0.6.3 one: every placement, every
    cancel and the state left behind are the v6.22 harness's, over pages, inventories, caps and resting orders."""
    seen_cases = 0
    for page, pname, inv, used, rest in _scenarios():
        for mode, version in (("auto", 62), ("auto", None), ("auto", "x"), ("off", 63)):
            ref = _ref(page=page, inv=inv, used=used)
            agent = _agent(page=page, inv=inv, used=used, mode=mode)
            if rest is not None:
                rest(ref)
                rest(agent)
            for i in range(2):
                ts = NOW + i * S
                want = _run_ref(ref, ts=ts)
                got = _run(agent, ts=ts, version=version)
                case = (pname, inv, used, getattr(rest, "__name__", None), mode, version, i)
                assert _out(got) == _out(want), case
                assert t622._fingerprint(agent) == t622._fingerprint(ref), case
            assert agent._v6_23_active is False and agent._v6_23_errors == 0
            assert not any(k.startswith(("fp_", "withheld_", "no_against")) for k in agent._v6_23_counts), case
            assert agent._v617_counts == ref._v617_counts and agent._v633_counts == ref._v633_counts, case
            seen_cases += 1
    assert seen_cases == 7 * 4 * 2 * 3 * 4


def test_active_the_same_scenarios_differ_only_where_a_fresh_fundamental_names_an_against_side():
    differs = set()
    for page, pname, inv, used, rest in _scenarios():
        ref = _ref(page=page, inv=inv, used=used)
        agent = _agent(page=page, inv=inv, used=used)
        if rest is not None:
            rest(ref)
            rest(agent)
        same = _out(_run(agent)) == _out(_run_ref(ref))
        if not same:
            differs.add(pname)
        if pname not in ("fp above", "fp below"):
            assert same, (pname, inv, used)
            assert not any(k.startswith("withheld_") for k in agent._v6_23_counts)
    assert differs == {"fp above", "fp below"}


# ---- 6. S1 in the pass ----------------------------------------------------------------------------------------------------

def test_s1_above_the_mid_the_sell_side_places_no_level_0_order():
    ref, agent = _ref(), _agent()
    assert _out(_run_ref(ref)) == ([(3, "BUY", 40031, 99.8, 2.0), (3, "SELL", 40032, 100.23, 2.0)], [])
    assert _out(_run(agent)) == ([(3, "BUY", 40031, 99.8, 2.0)], [])               # the touch bid yields to the deep bid
    assert agent._v6_23_active is True and agent._v6_23_spec == 63
    assert agent._v6_23_counts == {"active_requests": 1, "activations": 1, "fp_armed": 1, "withheld_sell_l0": 1}
    assert agent._v617_counts.get("yield_to_deep") == 1 and agent._v6_23_errors == 0


def test_s1_below_the_mid_the_buy_side_places_no_level_0_order():
    agent = _agent(page=t616._page({3: BELOW}))
    assert _out(_run(agent)) == ([(3, "SELL", 40032, 100.23, 2.0)], [])
    assert agent._v6_23_counts.get("withheld_buy_l0") == 1 and "withheld_sell_l0" not in agent._v6_23_counts


def test_s1_a_resting_level_0_order_on_the_against_side_is_cancelled_and_the_touch_quote_is_unchanged():
    for page, gone, touch in (("default", (3, 22), (3, "BUY", 43031, 100.0, 2.0)),
                              (t616._page({3: BELOW}), (3, 21), (3, "SELL", 43032, 100.03, 2.0))):
        ref, agent = _ref(inv=3.0, page=page), _agent(inv=3.0, page=page)
        for a in (ref, agent):
            _rest_both(a)                                                          # both level-0 orders at their price
        want, got = _out(_run_ref(ref)), _out(_run(agent))
        assert want == ([touch], [])                                               # v6.22: both rest, the touch quote
        assert got == ([touch], [gone])                                            # the against side's level 0 goes
        assert agent._v633_counts.get("cancel_deep_against_fp") == 1


def test_s1_the_cancelled_side_waits_for_the_removal_and_stays_empty_while_the_fundamental_stays_above():
    agent = _agent()
    _rest_both(agent)
    first = _run(agent)
    assert _out(first)[1] == [(3, 22)]
    second = _run(agent, ts=NOW + S)                     # the cancel is not yet seen: nothing on the ask side
    assert not [p for p in _out(second)[0] if p[1] == "SELL"]


def test_s1_without_a_fresh_fundamental_at_the_mid_or_inside_the_gap_nothing_is_withheld():
    for page, key in ((None, "fp_no_feed"), (t616._page({3: ABOVE}, sim_ts_ns=NOW - 30 * S), "fp_stale"),
                      (t616._page({3: ABOVE}, sim_id="20260929_2015"), "fp_other_sim"),
                      (t616._page({5: ABOVE}), "fp_no_fundamental"), (t616._page({3: 100.015}), "no_against_states")):
        ref, agent = _ref(page=page), _agent(page=page)
        assert _out(_run(agent)) == _out(_run_ref(ref)), key
        assert agent._v6_23_counts.get(key) == 1 and not any(k.startswith("withheld_") for k in agent._v6_23_counts)
    for gap, withheld in ((2.0, True), (2.6, False)):                              # 100.04: 2.50 bps above the mid
        agent = _agent(page=t616._page({3: 100.04}), gap=gap)
        placed, _cancelled = _out(_run(agent))
        assert ((3, "SELL", 40032, 100.23, 2.0) not in placed) is withheld, gap
        assert (agent._v6_23_counts.get("withheld_sell_l0") == 1) is withheld and \
            (agent._v6_23_counts.get("no_against_states") == 1) is (not withheld), gap


def test_s1_with_the_shipped_gap_a_book_near_its_fundamental_is_v622():
    for fp, withheld in ((ABOVE, True), (100.04, True), (100.02, False), (100.01, False)):
        page = t616._page({3: fp})
        ref, agent = _ref(page=page), _agent(page=page, levels="deep", adding=True, gap=vo.GAP_BPS_DEFAULT)
        want, got = _out(_run_ref(ref)), _out(_run(agent))
        assert (got != want) is withheld, fp
        assert (agent._v6_23_counts.get("no_against_states") == 1) is (not withheld), fp


def test_s1_deep_withholds_every_level_of_the_against_side():
    agent = _agent(levels="deep")
    assert _out(_run(agent)) == ([(3, "BUY", 40031, 99.8, 2.0)], [])
    assert {k: v for k, v in agent._v6_23_counts.items() if k.startswith("withheld_")} == \
        {"withheld_sell_l0": 1, "withheld_sell_l1": 1, "withheld_sell_l2": 1}


def test_s1_adding_keeps_an_against_side_that_unwinds_a_position_built_toward_the_fundamental():
    for inv, withheld in ((None, True), (-3.0, True), (3.0, False)):            # fp above: the ask is the against side
        ref, agent = _ref(inv=inv), _agent(inv=inv, levels="deep", adding=True)
        want, got = _out(_run_ref(ref)), _out(_run(agent))
        sells = [p for p in want[0] if p[1] == "SELL"]
        assert sells and want[0][0][1] == "BUY", inv
        if withheld:
            assert got == ([p for p in want[0] if p[1] != "SELL"], want[1]), inv
            assert agent._v6_23_counts.get("withheld_sell_l0") == 1 and "unwinding_kept_states" not in agent._v6_23_counts
        else:
            assert got == want and agent._v6_23_counts.get("unwinding_kept_states") == 1, inv
            assert not any(k.startswith("withheld_") for k in agent._v6_23_counts)


def test_s1_the_fundamental_is_read_exactly_as_the_touch_quote_reads_it():
    agent = _agent()
    pages = ("default", None, t616._page({3: ABOVE}, sim_ts_ns=NOW - 30 * S), t616._page({3: ABOVE}, sim_id="x"),
             t616._page({5: ABOVE}), t616._page({3: 0.0}), t616._page({3: ABOVE}, sim_ts_ns=NOW - 19 * S))
    for page in pages:
        if page != "default":
            agent._v616_feed = t616._Feed(page)
        cfg = _state().config
        for bid, ask in ((100.00, 100.03), (100.03, 100.00), (None, 100.03)):
            agent._v617_counts = {}
            fp617 = agent._v617_fundamental(3, bid, ask, cfg)
            fp623, reason = agent._v6_23_fundamental(3, bid, ask, cfg)
            assert fp623 == fp617 and agent._v617_counts == {"book_" + reason: 1}, (page, bid, ask)
    cfg = types.SimpleNamespace(simulation_id=None, logDir="/logs/" + SIM)      # the log dir names the simulation
    assert agent._v6_23_fundamental(3, 100.00, 100.03, cfg) == (ABOVE, "armed")


def test_s1_a_failing_read_withholds_nothing_and_counts_an_error():
    ref, agent = _ref(), _agent()

    def _boom(self, *a, **k):
        raise RuntimeError("boom")

    agent._v6_23_fundamental = types.MethodType(_boom, agent)
    assert _out(_run(agent)) == _out(_run_ref(ref)) and agent._v6_23_errors == 1
    agent = _agent()
    agent._v6_23_observe = types.MethodType(_boom, agent)
    with pytest.raises(RuntimeError):
        _run(agent)                                       # the harness calls it bare; respond() guards it:
    respond = _src("respond")
    hook = respond[respond.index("self._v6_23_observe(state)"):]
    assert hook.index("except Exception:") < hook.index("self._v6_23_active, self._v6_23_foreign = False, frozenset()")


# ---- 7. S1 in the deep layer, with the ladder (v6.9's harness) ------------------------------------------------------------

def _ladder_agent(v612=False):
    agent = t69._agent()
    names = dict(V623_NAMES, v612_level0_depth=d612.level0_depth)
    agent.__class__ = type("P69x23", (type(agent),), {"_v633_book": _with23(type(agent)._v633_book, names)})
    agent.research_v612_reduce_depth = v612
    return agent


def _deep(agent, against, inv=0.0, bid=100.00, ask=100.03):
    resp = t63._Resp()
    req = {"deep_books": 0, "placed_deep": 0, "cancels": 0}
    rows = [r for r in agent.ledger.live_orders(3) if dl.is_deep_client_id(r.client_id)]
    agent._v633_book(resp, 3, agent._v633_deep, 30.0, bid, ask, inv, [], rows, set(), req, tick_size=t69.TICK,
                     dec=2, clip=2.0, min_order=0.25, budget=5, expiry=t69.EXPIRY,
                     cfg=types.SimpleNamespace(volumeDecimals=4), paced=False, bound_scale=1.0, held=frozenset(),
                     size_scale=1.0, cap_skip=False, against=frozenset(against))
    return resp


def test_s1_l0_cancels_only_the_against_sides_level_0_and_keeps_its_ladder():
    agent = _ladder_agent()
    t69._rest_all(agent)                                  # 21..26: (bid, ask) x levels 0, 1, 2
    assert t63._cancelled(_deep(agent, ())) == set()
    agent = _ladder_agent()
    t69._rest_all(agent)
    resp = _deep(agent, vo.withheld(SELL, "L0"))
    assert t63._cancelled(resp) == {(3, 22)} and t63._placed(resp) == set()
    assert agent._v633_counts.get("cancel_deep_against_fp") == 1


def test_s1_deep_cancels_every_level_of_the_against_side():
    agent = _ladder_agent()
    t69._rest_all(agent)
    resp = _deep(agent, vo.withheld(SELL, "deep"))
    assert t63._cancelled(resp) == {(3, 22), (3, 24), (3, 26)} and t63._placed(resp) == set()
    assert agent._v69_counts.get("cancel_l1_deep_against_fp") == 1 and agent._v69_counts.get("cancel_l2_deep_against_fp") == 1


def test_s1_adding_in_the_deep_layer_follows_the_inventory_the_pass_hands_it():
    long_ = _ladder_agent()
    t69._rest_all(long_)
    resp = _deep(long_, vo.withheld(SELL, "deep", adding_only=True, inventory=3.0), inv=3.0)
    assert t63._cancelled(resp) == set()                                            # the ask unwinds the long: kept
    flat = _ladder_agent()
    t69._rest_all(flat)
    resp = _deep(flat, vo.withheld(SELL, "deep", adding_only=True, inventory=0.0), inv=0.0)
    assert t63._cancelled(resp) == {(3, 22), (3, 24), (3, 26)}


def test_s1_the_wide_spread_vacuum_order_is_the_against_sides_level_0_too():
    wide = dict(bid=100.00, ask=100.30)
    vb = t69.b64.vacuum_price(100.15, 12.0, BUY, bid=100.00, ask=100.30, tick=t69.TICK, decimals=2)
    va = t69.b64.vacuum_price(100.15, 12.0, SELL, bid=100.00, ask=100.30, tick=t69.TICK, decimals=2)
    resp = _deep(_ladder_agent(), (), **wide)
    assert {(p[1], p[3]) for p in t63._placed(resp)} == {("BUY", vb), ("SELL", va)}     # v6.22: both in the gap
    resp = _deep(_ladder_agent(), vo.withheld(SELL, "L0"), **wide)
    assert ("BUY", vb) in {(p[1], p[3]) for p in t63._placed(resp)}
    assert va not in {p[3] for p in t63._placed(resp)}
    agent = _ladder_agent()
    t69._rest(agent, 11, 0, 0, price=vb)
    t69._rest(agent, 12, 1, 0, price=va)
    assert t63._cancelled(_deep(agent, vo.withheld(SELL, "L0"), **wide)) == {(3, 12)}


def test_s1_v612s_closer_reducing_level_0_is_withheld_when_it_is_the_against_side():
    half = d612.level0_depth(SELL, 30.0, 3.0, 0.25)
    assert half == 15.0                                                              # long: the ask reduces, closer
    _b0, s0 = dl.level_client_ids(3, 0)
    resp = _deep(_ladder_agent(v612=True), (), inv=3.0)
    assert (3, "SELL", s0, t69._px(half, SELL), 2.0) in t63._placed(resp)              # v6.22: the closer ask
    resp = _deep(_ladder_agent(v612=True), vo.withheld(SELL, "L0"), inv=3.0)
    sells = {p for p in t63._placed(resp) if p[1] == "SELL"}
    assert sells == {p for p in t69._level(1) if p[1] == "SELL"}                       # its level 1 instead
    agent = _ladder_agent(v612=True)
    t69._rest(agent, 12, 1, 0, price=t69._px(half, SELL))
    assert t63._cancelled(_deep(agent, vo.withheld(SELL, "L0"), inv=3.0)) == {(3, 12)}


def test_s1_an_empty_side_places_its_next_level_and_never_level_0():
    resp = _deep(_ladder_agent(), ())
    assert t63._placed(resp) == t69._level(0)                                       # v6.22: level 0 on both sides
    bid0 = {p for p in t69._level(0) if p[1] == "BUY"}
    resp = _deep(_ladder_agent(), vo.withheld(SELL, "L0"))
    assert t63._placed(resp) == bid0 | {p for p in t69._level(1) if p[1] == "SELL"}      # the ask's level 1 instead
    resp = _deep(_ladder_agent(), vo.withheld(SELL, "deep"))
    assert t63._placed(resp) == bid0


# ---- 8. S2 in the pass and in the final validator -------------------------------------------------------------------------

def test_s2_a_foreign_book_keeps_its_cancels_and_builds_no_new_order():
    foreign = _classes(split=3, n=5)                                                 # book 3 in the 2.5364 class
    ref, agent = _ref(), _agent()
    for a in (ref, agent):
        _rest_doomed(a)
    placed, cancelled = _out(_run_ref(ref))
    assert placed and cancelled == [(3, 31), (3, 32)]
    assert _out(_run(agent, classes=foreign)) == ([], [(3, 31), (3, 32)])
    assert agent._v6_23_foreign == frozenset({3, 4}) and agent._v6_23_layout == vo.LAYOUT_FOREIGN
    assert agent._v6_23_counts.get("foreign_skip_book_states") == 1
    assert not any(k.startswith(("fp_", "withheld_")) for k in agent._v6_23_counts)   # S1 never reads a foreign book
    fresh = _agent()
    assert _out(_run(fresh, classes=foreign)) == ([], [])


def test_s2_a_book_of_the_top_level_class_a_single_market_or_the_guard_off_is_v622():
    for classes, guard in ((_classes(split=4, n=6), True), (None, True), (_classes(min1=0.25, split=3, n=5), True),
                           (_classes(split=3, n=5), False), ("junk", True)):
        ref, agent = _ref(), _agent(guard=guard, mode="off")
        for a in (ref, agent):
            _rest_doomed(a)
        assert _out(_run(agent, classes=classes)) == _out(_run_ref(ref)), (classes, guard)
        assert 3 not in agent._v6_23_foreign and "foreign_skip_book_states" not in agent._v6_23_counts
    agent = _agent()
    _run(agent, classes="junk")
    assert agent._v6_23_layout == vo.LAYOUT_UNREADABLE and agent._v6_23_counts.get("layout_unreadable_states") == 1


class _Host23(t69._Host):
    """v6.9's live-final-validator host with v6.23's names and counter."""
    def _v6_23_count(self, key, n=1):
        self._v6_23_counts[key] = self._v6_23_counts.get(key, 0) + n


_Host23._research_final_validate_instructions = t69._method(
    "_research_final_validate_instructions", **dict(t69.VALIDATOR_NAMES, **EXTRA))


def _validate23(host, *ixs):
    resp = types.SimpleNamespace(instructions=list(ixs))
    state = types.SimpleNamespace(books={3: t63._book(), 5: t63._book()},
                                  config=types.SimpleNamespace(priceDecimals=2, volumeDecimals=4))
    host.rejects = []
    host._research_final_validate_instructions(resp, state)
    return [ix.clientOrderId for ix in resp.instructions], list(host.rejects)


def test_s2_the_final_validator_drops_any_placement_on_a_foreign_book_and_nothing_else():
    host = _Host23()
    host._v6_23_counts = {}
    assert _validate23(host, t69._ix(40031), t69._ix(40052, "SELL", book=5)) == ([40031, 40052], [])
    host._v6_23_foreign = frozenset({3})
    kept, rejects = _validate23(host, t69._ix(40031), t69._ix(80032, "SELL"), t69._ix(40052, "SELL", book=5))
    assert kept == [40052] and rejects == [(3, "FOREIGN_CLASS"), (3, "FOREIGN_CLASS")]
    assert host._v6_23_counts == {"foreign_dropped": 2}
    resp = types.SimpleNamespace(instructions=[types.SimpleNamespace(type="CANCEL_ORDERS", bookId=3, cancellations=[]),
                                               t69._ix(40031)])
    host._research_final_validate_instructions(resp, types.SimpleNamespace(books={3: t63._book()}, config=None))
    assert [ix.type for ix in resp.instructions] == ["CANCEL_ORDERS"]               # its cancels still go out


def test_s2_the_final_validators_check_comes_before_every_other_gate():
    final = _src("_research_final_validate_instructions")
    assert final.count("if v6_23_foreign and book_id in v6_23_foreign:") == 1 and final.count(DROP_LINE) == 1
    assert final.count('v6_23_foreign = getattr(self, "_v6_23_foreign", None) or frozenset()') == 1
    loop = final[final.index("for instruction in placements:"):]
    assert loop.index("side = self._research_instruction_side(instruction)") < loop.index(
        "if v6_23_foreign and book_id in v6_23_foreign:") < loop.index('qty = self._get(instruction, "quantity"')
    assert final.index('v6_23_foreign = getattr(self, "_v6_23_foreign", None) or frozenset()') < final.index(
        "for instruction in placements:")


# ---- 9. the observation and the V623_STATE row ----------------------------------------------------------------------------

def _obs(**kw):
    agent = _agent(**kw)
    agent._tick = 0
    return agent


def _rows(agent):
    return [kw for event, kw in agent.emitted if event == "V623_STATE"]


def test_the_state_row_comes_at_the_start_on_each_change_after_a_simulation_start_and_every_100_ticks():
    agent = _obs()
    agent._v6_23_observe(_state(version=62))
    agent._v6_23_observe(_state(version=62))
    agent._v6_23_observe(_state(version=63))
    agent._v6_23_observe(_state(version=63, classes=_classes(split=3, n=5)))
    agent._v6_23_report = "simulation_start"
    agent._v6_23_observe(_state(version=63, classes=_classes(split=3, n=5)))
    agent._tick = 99
    agent._v6_23_observe(_state(version=63, classes=_classes(split=3, n=5)))
    agent._tick = 100
    agent._v6_23_observe(_state(version=63, classes=_classes(split=3, n=5)))
    agent._v6_23_observe(_state(version=None, classes=_classes(split=3, n=5)))
    rows = _rows(agent)
    assert [(r["reason"], r["tick"], r["spec"], r["active"], r["foreign_books"]) for r in rows] == [
        ("start", 1, 62, 0, 0), ("activation", 1, 63, 1, 0), ("layout", 1, 63, 1, 2), ("simulation_start", 1, 63, 1, 2),
        ("periodic", 100, 63, 1, 2), ("deactivation", 101, None, 0, 2)]
    last = rows[-1]
    assert last["version"] == "validator_063_v6_23" and last["mode"] == "auto" and last["against_levels"] == "L0"
    assert last["against_gap_bps"] == 0.0 and last["against_adding_on"] == 0
    assert last["foreign_range"] == [3, 4] and last["layout"] == "foreign" and last["class_guard_on"] == 1
    assert last["counts"] == {"active_requests": 5, "activations": 1, "deactivations": 1}
    assert last["errors"] == 0 and last["start_once_on"] == 1 and last["start_error"] is None
    assert agent._v6_23_report is None


def test_the_state_row_waits_for_the_reply_when_the_request_defers_its_telemetry():
    agent = _obs()
    queue = []
    agent._v629_request_work = types.SimpleNamespace(add=lambda name, fn: queue.append((name, fn)))
    agent._v6_23_observe(_state(version=63))
    assert _rows(agent) == [] and [n for n, _fn in queue] == ["v623_state_row"]
    queue[0][1]()
    assert [r["reason"] for r in _rows(agent)] == ["start"]


def test_an_error_in_the_observation_leaves_v622():
    agent = _obs()
    fn = type(agent)._v6_23_observe

    def _boom(*a, **k):
        raise RuntimeError("boom")

    agent._v6_23_observe = types.MethodType(types.FunctionType(
        fn.__code__, dict(fn.__globals__, v623_class_layout=_boom, v623_realized_active=_boom), fn.__name__), agent)
    agent._v6_23_observe(_state(version=63, classes=_classes()))
    assert agent._v6_23_active is False and agent._v6_23_foreign == frozenset() and agent._v6_23_errors == 2
    assert agent._v6_23_layout == vo.LAYOUT_UNREADABLE
    respond = _src("respond")
    hook = respond[respond.index("self._v6_23_observe(state)"):respond.index("self._a1961_note_base_decimals(state)")]
    assert "self._v6_23_active, self._v6_23_foreign = False, frozenset()" in hook
    assert "self._v6_23_layout = V623_LAYOUT_UNREADABLE" in hook


# ---- 10. S3: one reset per simulation start ----------------------------------------------------------------------------------

class _Bt:
    class logging:
        @staticmethod
        def info(*a, **k):
            return None

        @staticmethod
        def warning(*a, **k):
            return None


class _Root:
    """The vendored FinanceSimulationAgent's hooks: onStart and onEnd do nothing (counted here, as is onTrade)."""
    def onStart(self, event):
        return None

    def onEnd(self, event):
        self.ends = getattr(self, "ends", 0) + 1

    def onTrade(self, event, validator=None):
        self.trades = getattr(self, "trades", 0) + 1


def _klass(name, base, sources, scope):
    """A class built from real method sources, so their zero-argument super() resolves as in the agent's MRO."""
    body = "\n".join(textwrap.indent(src, "    ") for src in sources)
    ns = dict(scope)
    ns["__base__"] = base
    exec(compile(f"from __future__ import annotations\nclass {name}(__base__):\n{body}\n", f"<{name}>", "exec"), ns)
    return ns[name]


_DTA = _klass("DTA", _Root, [method_source(DTA, n, cls_name="DetailedTemplateAgent")
                             for n in ("onStart", "_reset_pnl_state", "_dispatch_notice_event", "_fast_update")],
              {"bt": _Bt})
_S1 = _klass("S1", _DTA, [method_source(S1SRC, "_reset_pnl_state", cls_name="Strategy1")], {})


class _Counted(_S1):
    def _reset_pnl_state(self):
        self.resets += 1
        super()._reset_pnl_state()


_RESEARCH = _klass("Research", _Counted, [method_source(RESEARCH, "onStart", cls_name="Strategy1_Research")], {})
_SIMPLE = _klass("Simple", _RESEARCH, [_src("onStart"), _src("_v6_23_count")], EXTRA)
CARRIED = {7_000 * S: {3: 1.25, 5: -0.5}}


def _start_agent(*, once=True, preserve=False, sim="20261010_1500"):
    a = _SIMPLE()
    a.research_v623_start_once = once
    a._v6_23_counts, a._v6_23_errors, a._v6_23_start_key, a._v6_23_start_error = {}, 0, None, None
    a._v6_23_report = None
    a.simulation_config = types.SimpleNamespace(simulation_id=sim, logDir=None)
    a._tick, a._v6_23_update_seq, a._scoring_timestamp, a.resets = 7, 7, 600 * S, 0
    a.uid, a.history_len, a.sync_event_csv = UID, 0, False
    a.realized_pnl_history = copy.deepcopy(CARRIED)
    a.total_realized_pnl_by_book = defaultdict(float, {3: 1.25, 5: -0.5})
    a._open_positions = defaultdict(lambda: {"longs": deque(), "shorts": deque()})
    a._open_positions[3]["longs"].append((1, 2.0, 100.0, 0.0))
    a._mid_history = defaultdict(list, {3: [(1, 100.0)]})
    a._last_kappa, a._last_selection, a._last_regime = 0.4, object(), object()
    a._last_predictions, a._last_profiles, a._last_strategy_stats, a._last_pnl_estimates = {3: 1}, [1], {"a": 1}, [1]
    for name in ("_position_ticks", "_inventory_reason", "_micro_prev", "_dir_pending", "_trade_signs",
                 "_trade_signs_tick", "book_memory"):
        setattr(a, name, {3: 1})
    a._last_mm_stats, a._tuning_window, a._last_tuning_ts, a._last_tuning_objective = {"x": 1}, {"y": 1}, 9, 0.5
    a._research_preserve_validator_pnl_history_on_start = preserve
    a._research_local_kappa_cache_value, a._research_local_kappa_source_sig = 0.3, "sig"
    return a


def _ess(n=8, ts=600 * S):
    return [types.SimpleNamespace(type="ESS", timestamp=ts, logDir=f"/run/x-{i}") for i in range(n)]


def test_s3_v622_resets_on_every_notice_and_a_carry_raises_the_frozen_nameerror():
    a = _start_agent(once=False)
    for ev in _ess():
        a.onStart(ev)
    assert a.resets == 8 and a.realized_pnl_history == {}
    carry = _start_agent(once=False, preserve=True)
    with pytest.raises(NameError):                     # Strategy1_Research.onStart: bt.logging.info, bt never imported
        carry.onStart(_ess(1)[0])
    assert carry.realized_pnl_history == CARRIED and carry._research_preserve_validator_pnl_history_on_start is False
    carry.onStart(_ess(1)[0])                          # a second realization's notice, had the request gone on
    assert carry.realized_pnl_history == {} and carry.resets == 2


def test_s3_one_reset_per_state_and_what_the_first_notice_restored_survives():
    a = _start_agent(preserve=True)
    for ev in _ess():
        a.onStart(ev)
    assert a.resets == 1 and a.realized_pnl_history == CARRIED
    assert dict(a.total_realized_pnl_by_book) == {3: 1.25, 5: -0.5}
    assert a._research_preserve_validator_pnl_history_on_start is False and a._research_local_kappa_cache_value is None
    assert a._open_positions == {} and a._position_ticks == {} and a.book_memory == {}         # the reset itself ran
    assert a._v6_23_counts == {"starts_reset": 1, "starts_skipped": 7, "start_errors": 1}
    assert a._v6_23_start_error == "NameError" and a._v6_23_report == "simulation_start"
    assert a._v6_23_start_key == ("state", 7, 600 * S)


def test_s3_a_later_state_with_a_start_resets_again_whatever_its_simulation_id():
    for later_sim in ("20261010_1500", "20261011_1500", None):     # a resumed run, a new run, a state without an id
        a = _start_agent()
        for ev in _ess():
            a.onStart(ev)
        a._v6_23_update_seq, a._scoring_timestamp = 9, 900 * S
        a.simulation_config = types.SimpleNamespace(simulation_id=later_sim, logDir=None)
        for ev in _ess(ts=900 * S):
            a.onStart(ev)
        assert a.resets == 2 and a._v6_23_counts == {"starts_reset": 2, "starts_skipped": 14}, later_sim
    a = _start_agent()
    a._v6_23_update_seq = None                                       # no scope to key on: every notice resets (v6.22)
    for ev in _ess(3):
        a.onStart(ev)
    assert a.resets == 3 and a._v6_23_start_key is None


def test_s3_two_runs_first_states_are_two_starts_while_the_request_tick_stands_still():
    """The real-agent smoke (k49): v5.0.3's quiet gate answers without respond(), so the tick stays 0, and both runs'
    first states sit at the same sim time -- only update()'s own count tells them apart."""
    a = _start_agent()
    for ev in _ess():
        a.onStart(ev)
    a._v6_23_update_seq += 4                                         # four states later, same tick, same start time
    for ev in _ess():
        a.onStart(ev)
    assert a._tick == 7 and a.resets == 2 and a._v6_23_counts == {"starts_reset": 2, "starts_skipped": 14}
    update = _src("update")
    first = next(st for st in ast.parse(update).body[0].body           # v6.38's P3 cache reset (an if on its switch) aside
                 if not (isinstance(st, ast.If) and "research_v638_fast_path" in ast.unparse(st.test)))
    assert ast.unparse(first) == "self._v6_23_update_seq = int(getattr(self, '_v6_23_update_seq', 0) or 0) + 1"
    assert 'key = v623_start_key(getattr(self, "_v6_23_update_seq", None), getattr(self, "_scoring_timestamp", None))' \
        in _src("onStart")


def _first_state(trades=0):
    """0.6.3's first state of a run: no simulation id, one start notice per realization, then the state's own trades."""
    ets = [types.SimpleNamespace(type="ET", timestamp=601 * S, bookId=3, takerAgentId=7, makerAgentId=UID)
           for _ in range(trades)]
    return types.SimpleNamespace(config=types.SimpleNamespace(simulation_id=None, logDir=None), accounts={UID: {}},
                                 notices={UID: _ess() + ets}, dendrite=types.SimpleNamespace(hotkey="validator"),
                                 timestamp=600 * S)


def test_s3_in_the_real_0_6_3_order_the_armed_carry_no_longer_fails_the_request_or_drops_its_trades():
    """The session's carry armed the preserve flag one state after the PREVIOUS run's start (0.6.3's first state has no
    simulation id), so this run's first notice restores and then hits the frozen NameError."""
    for once in (False, True):
        a = _start_agent(once=once, preserve=True)
        state = _first_state(trades=2)
        if not once:
            with pytest.raises(NameError):
                a._fast_update(state)
            assert getattr(a, "trades", 0) == 0 and a.resets == 1        # v6.22: the trades after it never processed
        else:
            a._fast_update(state)
            assert a.trades == 2 and a.resets == 1 and a.realized_pnl_history == CARRIED
            assert a._v6_23_counts == {"starts_reset": 1, "starts_skipped": 7, "start_errors": 1}


def test_s3_through_the_live_notice_path_eight_starts_reset_once_and_eight_ends_do_nothing():
    """fast_update=1 (the shipped PARAMS): DetailedTemplateAgent._fast_update dispatches every notice."""
    assert "fast_update=1" in t617._params()
    for once, resets in ((True, 1), (False, 8)):
        a = _start_agent(once=once)
        ends = [types.SimpleNamespace(type="ESE", timestamp=86_400 * S) for _ in range(8)]
        state = types.SimpleNamespace(config=types.SimpleNamespace(simulation_id="20261010_1500", logDir=None),
                                      accounts={UID: {}}, notices={UID: _ess() + ends},
                                      dendrite=types.SimpleNamespace(hotkey="validator"), timestamp=600 * S)
        a._fast_update(state)
        assert a.resets == resets and a.ends == 8, once


def test_s3_the_start_reset_touches_no_v6_pacing_window_ledger_or_session_state():
    """Every attribute onStart writes or empties, through its whole chain.  None is v6.x pacing, young-window, ledger or
    session state; _open_positions is the FIFO lot table v6.0/v6.1 read (and v6.1 persists), emptied as the venue's new
    accounts are; the carry restores realized_pnl_history and its totals (Strategy1_Research.onStart)."""
    chain = [method_source(DTA, "onStart", cls_name="DetailedTemplateAgent"),
             method_source(DTA, "_reset_pnl_state", cls_name="DetailedTemplateAgent"),
             method_source(S1SRC, "_reset_pnl_state", cls_name="Strategy1"),
             method_source(RESEARCH, "onStart", cls_name="Strategy1_Research")]
    touched = set()
    for src in chain:
        for node in ast.walk(ast.parse(src)):
            target = None
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                        touched.add(t.attr)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                target = node.func.value
                if (node.func.attr in ("clear", "update", "pop", "append", "add", "discard")
                        and isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                        and target.value.id == "self"):
                    touched.add(target.attr)
    assert touched == {
        "realized_pnl_history", "total_realized_pnl_by_book", "_open_positions", "_mid_history", "_last_kappa",
        "_last_predictions", "_last_profiles", "_last_selection", "_last_regime", "_last_strategy_stats",
        "_last_pnl_estimates", "_position_ticks", "_inventory_reason", "_micro_prev", "_dir_pending", "_trade_signs",
        "_trade_signs_tick", "book_memory", "_last_mm_stats", "_tuning_window", "_last_tuning_ts",
        "_last_tuning_objective", "_research_preserve_validator_pnl_history_on_start",
        "_research_local_kappa_cache_value", "_research_local_kappa_source_sig"}
    for name in touched:
        assert not name.startswith(("_v6", "_v62", "_v63", "_v64", "_v66", "_v68", "_v69", "_a19", "_research_session")), name
        assert "ledger" not in name and "pace" not in name and "window" not in name.replace("_tuning_window", ""), name
    end = method_source(TAOS_AGENTS, "onEnd", cls_name="FinanceSimulationAgent")
    assert ast.parse(end).body[0].body[-1].__class__ is ast.Pass and len(ast.parse(end).body[0].body) == 2
    for text, cls in ((DTA, "DetailedTemplateAgent"), (S1SRC, "Strategy1"), (RESEARCH, "Strategy1_Research"),
                      (SIMPLE, "Strategy1_Research_Simple")):
        with pytest.raises(AssertionError):
            method_source(text, "onEnd", cls_name=cls)                      # nobody in the chain overrides onEnd


# ---- 11. v6.22's code, statement by statement (the committed v6.22: d65bff2) ---------------------------------------------

V622_COMMIT = "d65bff2"
V623_IDENTIFIERS = (
    {"against"} | {k for k in V623_NAMES if k.startswith(("V623_", "v623_"))}
    | {"research_v623_realized_mode", "research_v623_against_levels", "research_v623_against_gap_bps",
       "research_v623_against_adding", "research_v623_class_guard", "research_v623_start_once"})
# v6.24 (S4, research_v624_pay_basis) only adds too -- its import, its _v6_24_ methods and statements naming its switch,
# the names it imports or a _v6_24_ / v6_24_ name, inside v6.23's own _v6_23_observe and _v6_23_snapshot as well
# (tests/test_research_v6_24.py strips them against the committed v6.23) -- so they go with v6.23's additions here and
# every v6.22 statement stays pinned.
V624_IDENTIFIERS = {"research_v624_pay_basis", "V624SharedBasis", "v624_reads_basis", "v624_realized_active"}
# v6.25 (S5, research_v625_lean) adds its import, its _v6_25_ methods, its switches and per-request statements, and hands
# the making decisions the leaned inventory: v6_25_inv / v6_25_fills where v6.24 read inv (each is inv itself without a
# lean).  The strip takes its additions out and reads those two names back as inv (tests/test_research_v6_25.py strips
# them against the committed v6.24), so every v6.22 statement stays pinned.
V625_IDENTIFIERS = {"research_v625_lean", "research_v625_lean_slope", "research_v625_lean_cap", "V625_LEAN_CAP",
                    "V625_LEAN_SLOPE", "V625_LEAN_VERSION", "v625_lean_target", "v625_lean_request", "v625_lean_note",
                    "v625_parse_lean_cap", "v625_parse_lean_slope", "v625_lean_summary"}
V625_LEANED = {"v6_25_inv", "v6_25_fills"}
# v6.26 (S6, research_v626_feed_clock) adds its import, its _v6_26_ methods, its switch and per-request statements, and
# hands each reader of the fundamental page the request's freshness limit (the max_stale_sim_s keyword; None -- v6.16's
# limit -- with the switch off).  The strip takes its additions and that keyword out (tests/test_research_v6_26.py strips
# them against the committed v6.25), so every v6.22 statement stays pinned.
V626_IDENTIFIERS = {"research_v626_feed_clock", "V626SimRate", "V626_FEED_CLOCK_VERSION", "v626_page_verdict",
                    "v626_stale_limit", "v626_state_sim_id"}
V626_KEYWORDS = {"max_stale_sim_s"}
# v6.27 (S7, research_v627_touch_centre) only adds: its import, its _v6_27_ methods, its switch and state, and statements
# naming them in the pass and the touch quote (an if on its switch or on a v6_27_ local, or an assignment of one) -- the
# touch quote's v6.10 bound is narrowed inside such an if.  The strip takes them out (tests/test_research_v6_27.py strips
# them against the committed v6.26), so every v6.22 statement stays pinned.
V627_IDENTIFIERS = {"research_v627_touch_centre", "V627_TOUCH_CENTRE_VERSION", "v627_request_lean", "v627_touch_bound"}
# v6.28 (S8, research_v628_flat_launch) only adds: its import, its _v6_28_ methods, its switch and state, and statements
# naming them where the pace line is built, restored and saved and in the pass (an if on its switch or on a v6_28_
# local, or an assignment of one) -- the deep clip of a launching book is re-read inside such an if.  The strip takes
# them out (tests/test_research_v6_28.py strips them against the committed v6.27), so every v6.22 statement stays pinned.
V628_IDENTIFIERS = {"research_v628_flat_launch", "V628FlatLaunchLine", "V628_FLAT_LAUNCH_VERSION", "v628_launch_clip",
                    "v628_restore_rates", "v628_save_rates"}
# v6.29 (S9, research_v629_touch_fp_move, and S8's first save) only adds: its import, its _v6_29_ methods, its switch and
# state, and statements naming them in the touch quote, the pass and the session save (an if on its switch, on a v6_29_
# local or on its state, or an assignment of one) -- the touch quote's withheld side and the save's force flag are set
# by such statements.  The strip takes them out (tests/test_research_v6_29.py checks them against v6.28's footprint),
# so every v6.22 statement stays pinned.
V6_29_IDENTIFIERS = {"research_v629_touch_fp_move", "V6_29PageHistory", "V6_29_TOUCH_MOVE_VERSION",
                     "V6_29_CANCEL_FP_MOVE", "v6_29_launch_records", "v6_29_lookback"}
# v6.30 (the identity profile: D1 research_v630_deep_tier, D2 research_v630_s9_lookback) only adds: its import, its
# _v6_30_ methods, its switches and state, and statements naming them in the deep layer, the pass and S9's decision (an
# if on a switch, on a v6_30_ local or on its state -- the one in S9's decision keeps its else, v6.29's call -- or an
# assignment or call naming one).  The strip takes them out (tests/test_research_v6_30.py checks them against the
# committed v6.29), so every v6.22 statement stays pinned.
V6_30_IDENTIFIERS = {"research_v630_deep_tier", "research_v630_s9_lookback", "V6_30_D1", "V6_30_D2", "V6_30Identity",
                     "V6_30KeepHorizon", "V6_30_PROFILE_VERSION", "V6_30_IDENTITY_PROFILE_VERSION", "V6_30_CLAMPED_BAND",
                     "V6_30_CLAMPED_TOUCH", "V6_30_DEEPER", "v6_30_addressee", "v6_30_deeper_target",
                     "v6_30_page_history", "v6_30_paper_for"}
# v6.31 (S12b research_v631_touch_improve, the single-write save research_v631_single_write_save) only adds: its import,
# its _v6_31_ methods, its switches and state, and statements naming them in the touch quote, the pass, the session path
# and the session save (an if on a switch or on its state, or an assignment or call naming one).  The strip takes them
# out (tests/test_research_v6_31.py checks them against the committed v6.30), so every v6.22 statement stays pinned.
V6_31_IDENTIFIERS = {"research_v631_touch_improve", "research_v631_single_write_save", "V6_31_REASONS",
                     "V6_31_STAGE_SUFFIX", "V6_31_SINGLE_WRITE_SAVE_VERSION", "V6_31_TOUCH_IMPROVE_VERSION",
                     "v6_31_improved_touch_price"}
# v6.32 (S14 research_v632_fee_making + research_v632_fee_weight, P1 research_v632_state_fundamental) only adds: its
# two imports, its _v6_32_ methods, its switches and state, and statements naming them in respond(), the pass, the deep
# layer, the touch quote, the fundamental's readers (v6.16's centre, v6.17's, v6.23's) and S9's history (an if on a
# switch or on its state -- the one in S9's history keeps its else: v6.31's page observation -- or an assignment or call
# naming one).  The strip takes them out (tests/test_research_v6_32.py checks them against the committed v6.31), so
# every v6.22 statement stays pinned.
V6_32_IDENTIFIERS = {"research_v632_fee_making", "research_v632_fee_weight", "research_v632_state_fundamental",
                     "V6_32_CANCEL_FEE_GATE", "V6_32_FEE_WEIGHT_DEFAULT", "V6_32RealizedTracker",
                     "V6_32_FEE_MAKING_VERSION", "V6_32_STATE_FUNDAMENTAL_VERSION", "v6_32_adds", "v6_32_deep_pairs",
                     "v6_32_fee_gated", "v6_32_gate_active", "v6_32_maker_fee_rate", "v6_32_min_sizes",
                     "v6_32_parse_fee_weight", "v6_32_book_value", "v6_32_fundamental_value", "v6_32_s9_snapshot"}
# v6.33 (D4 research_v633_touch_identity) only adds: its import, its _v6_33_ methods, its switch and state, and
# statements naming them in the pass (the touch table's per-request draw, the state row) and the touch quote (the book's
# touch presence, the resting cancel, the new-quote gate) -- an if on the switch or on its state, or an assignment or
# call naming one.  The strip takes them out (tests/test_research_v6_33.py checks them against the committed v6.32), so
# every v6.22 statement stays pinned.
V6_33_IDENTIFIERS = {"research_v633_touch_identity", "V6_33_CANCEL_TOUCH_IDENTITY", "V6_33TouchTable",
                     "V6_33_TOUCH_IDENTITY_VERSION", "v6_33_touch_off"}
# v6.34 (research_v634_class1_entry) only adds: its import, its _v6_34_ methods, its switch and state, and statements
# naming them in respond(), v6.32's tracker hook (the class-1 books' prints stay out of class 0's estimate), the pass
# (the class-1 books, their branch, the state row) and the final validator (this request's class-1 quotes pass S2) -- an if
# on the switch or on its state, or an assignment or call naming one.  The strip takes them out
# (tests/test_research_v6_34.py checks them against v6.33), so every v6.22 statement stays pinned.
V6_34_IDENTIFIERS = {"research_v634_class1_entry", "V6_34_CANCEL_OTHER", "V6_34MinerHolds", "V6_34_STATUS_OK",
                     "V6_34_CLASS1_ENTRY_VERSION", "V6_34_WHY_CAPPED"}
# v6.36 (research_v636_step_withdrawal) only adds: its import, its _v6_36_ methods, its switch and state, and statements
# naming them in respond() (the step record before any decision, the withdrawal cancels after every post-pass) and in
# build_mm_strategy_instructions (the withdrawn placements dropped after the final validator) -- an if on its state, or
# an assignment or call naming one.  The strip takes them out (tests/test_research_v6_36.py checks them against
# v6.35), so every v6.22 statement stays pinned.
V6_36_IDENTIFIERS = {"research_v636_step_withdrawal", "V6_36_GRID_OK", "V6_36StepRecord",
                     "V6_36_STEP_WITHDRAWAL_VERSION"}
# v6.37 (research_v637_fast_path) only adds: its import and, in _init_build_switches, its switch and the statements
# writing it into F2's three module switches and the exit ledger's dict -- assignments naming one.  The strip takes them
# out (tests/test_research_v6_37.py checks them against v6.36), so every v6.22 statement stays pinned.
V6_37_IDENTIFIERS = {"research_v637_fast_path"}
# v6.38 layer 3 (research_v638_kbl, research_v638_lean + _arm, research_v638_c1 + _arm; scratchpad k124) only
# adds: the strip takes its additions out as well (tests/test_research_v6_38_l3.py checks them against v6.37).
V6_38_IDENTIFIERS = {"research_v638_kbl", "research_v638_lean",
                     "research_v638_lean_arm", "research_v638_c1", "research_v638_c1_arm"}


def _v622(rel):
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "show", f"{V622_COMMIT}:{rel}"], cwd=ROOT,
                             capture_output=True, text=True, timeout=120)
    except Exception:
        out = None
    if out is None or out.returncode != 0:
        pytest.skip(f"git or {V622_COMMIT} is not available here")
    return out.stdout


def _mentions(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id in V625_LEANED and not isinstance(n.ctx, ast.Store):
            continue                                                         # read back as inv (visit_Name)
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.arg if isinstance(n, (ast.arg, ast.keyword)) else n.value if isinstance(n, ast.Constant) else None
        if isinstance(name, str) and (name in V623_IDENTIFIERS or name in V624_IDENTIFIERS or name in V625_IDENTIFIERS
                                      or name in V626_IDENTIFIERS or name in V627_IDENTIFIERS
                                      or name in V628_IDENTIFIERS or name in V6_29_IDENTIFIERS
                                      or name in V6_30_IDENTIFIERS or name in V6_31_IDENTIFIERS
                                      or name in V6_32_IDENTIFIERS
                                      or name in V6_33_IDENTIFIERS
                                      or name in V6_34_IDENTIFIERS
                                      or name in V6_36_IDENTIFIERS
                                      or name in V6_37_IDENTIFIERS
                                      or name in V6_38_IDENTIFIERS
                                      or name.startswith(("_v6_23_", "v6_23_", "_v6_24_", "v6_24_", "_v6_25_",
                                                          "v6_25_", "_v6_26_", "v6_26_", "_v6_27_", "v6_27_",
                                                          "_v6_28_", "v6_28_", "_v6_29_", "v6_29_", "_v6_30_",
                                                          "v6_30_", "_v6_31_", "v6_31_", "_v6_32_", "v6_32_",
                                                          "_v6_33_", "v6_33_", "_v6_34_", "v6_34_",
                                                          "_v6_36_", "v6_36_", "_v6_37_", "v6_37_", "_v6_38_",
                                                          "v6_38_"))):
            return True
    return False


class _Strip623(ast.NodeTransformer):
    """Removes what v6.23 (and v6.24, v6.25, v6.26, v6.27, v6.28, v6.29, v6.30, v6.31, v6.32, v6.33, v6.34, v6.36
    and v6.37 after it) adds and nothing else: its import, its methods, statements naming its identifiers (an if on one
    keeps its else), the against keyword and argument, v6.26's limit keyword, and every docstring (v6.23 extended
    one); v6.25's leaned inventory reads back as inv."""

    def visit_Module(self, node):                      # v6.38's changes undone first (tests/_v638_undo.py)
        _v638_undo.undo(node)
        return self.generic_visit(node)

    def _stmts(self, stmts):
        out = []
        for st in stmts:
            if isinstance(st, ast.ImportFrom) and st.module in ("research_v623_validator_063", "research_v624_pay_basis",
                                                                "research_v625_lean", "research_v626_feed_clock",
                                                                "research_v627_touch_centre",
                                                                "research_v628_flat_launch",
                                                                "research_v6_29_touch_move",
                                                                "research_v630_identity_profile",
                                                                "research_v631_touch_improve",
                                                                "research_v6_32_fee_making",
                                                                "research_v6_32_state_fp",
                                                                "research_v6_33_touch_identity",
                                                                "research_v6_34_class1",
                                                                "research_v6_36_step_withdrawal",
                                                                "research_v6_37_fast_path",
                                                                "research_v6_38_canary",
                                                                "research_v6_38_kbl",
                                                                "research_v6_38_lean",
                                                                "research_v6_38_keeper"):
                continue
            if isinstance(st, ast.FunctionDef) and (st.name == "onStart"
                                                    or st.name.startswith(("_v6_23_", "_v6_24_", "_v6_25_", "_v6_26_",
                                                                           "_v6_27_", "_v6_28_", "_v6_29_", "_v6_30_",
                                                                           "_v6_31_", "_v6_32_", "_v6_33_",
                                                                           "_v6_34_", "_v6_36_", "_v6_37_",
                                                                           "_v6_38_"))):
                continue
            if isinstance(st, ast.If) and _mentions(st.test):
                out.extend(self._stmts(st.orelse))
                continue
            if isinstance(st, ast.Try) and all(_mentions(b) for b in st.body):
                continue
            st = self.visit(st)                                              # the against keyword goes first
            if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)) and _mentions(st):
                continue
            out.append(st)
        return out

    def generic_visit(self, node):
        for field in ("body", "orelse", "finalbody"):
            if isinstance(getattr(node, field, None), list):
                body = getattr(node, field)
                if field == "body" and isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and body and \
                        isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and \
                        isinstance(body[0].value.value, str):
                    body = body[1:]                                          # the docstring
                setattr(node, field, self._stmts(body))
        for h in getattr(node, "handlers", None) or []:
            h.body = self._stmts(h.body)
        for name, value in ast.iter_fields(node):
            if name in ("body", "orelse", "finalbody", "handlers"):
                continue
            if isinstance(value, list):
                setattr(node, name, [self.visit(v) if isinstance(v, ast.AST) else v for v in value])
            elif isinstance(value, ast.AST):
                setattr(node, name, self.visit(value))
        return node

    def visit_Call(self, node):
        node.keywords = [k for k in node.keywords if k.arg != "against" and k.arg not in V626_KEYWORDS]
        return self.generic_visit(node)

    def visit_Name(self, node):
        if node.id in V625_LEANED and not isinstance(node.ctx, ast.Store):
            node.id = "inv"                                                  # v6.25: v6.24's inv
        return node

    def visit_arguments(self, node):
        pairs = [(a, d) for a, d in zip(node.kwonlyargs, node.kw_defaults) if a.arg != "against"]
        node.kwonlyargs, node.kw_defaults = [a for a, _d in pairs], [d for _a, d in pairs]
        return node


def _stripped(text):
    return ast.dump(_Strip623().visit(ast.parse(text)))


def test_every_v622_statement_is_in_v623_unchanged_and_v623_only_adds():
    """Simple of the committed v6.22 == Simple of v6.23 with v6.23's additions taken out: no v6.22 statement, argument,
    call or branch was changed or dropped -- v6.23's code is its import, its methods and statements that name its own
    identifiers (each guarded: _v6_23_active false and _v6_23_foreign empty make them no-ops, tested above)."""
    old = _v622("agents/strategy/Strategy1_Research_Simple.py")
    assert not _mentions(ast.parse(old)), "v6.22 already names a v6.23 identifier: the strip would hide a change"
    assert _stripped(old) == _stripped(SIMPLE)
    assert _stripped(SIMPLE) != ast.dump(ast.parse(SIMPLE))                       # it does take something out
    old_models = _v622("taos/im/protocol/models.py")
    tree = ast.parse(MODELS)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MarketSimulationConfig")
    cls.body = [n for n in cls.body if not (isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "classes")]
    _drop_f2_models(tree)                    # k106 F2's fast builds (tests/test_research_v6_37.py pins them)
    _drop632_models(tree)                    # v6.32 P1's protocol additions (tests/test_research_v6_32.py pins them)
    assert ast.dump(tree) == ast.dump(ast.parse(old_models))                       # the vendored change is the field


def _drop632_models(tree):
    """Takes v6.32 P1's additions out of the vendored protocol and nothing else: Book's ``f`` field and ``fundamental``
    property, LazyBook's ``fundamental`` property and the ``f`` keyword of LazyBook.parse's Book.model_construct."""
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef) or cls.name not in ("Book", "LazyBook"):
            continue
        cls.body = [n for n in cls.body if not (isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "f")
                    and not (isinstance(n, ast.FunctionDef) and n.name == "fundamental")]
        for fn in cls.body:
            if isinstance(fn, ast.FunctionDef) and cls.name == "LazyBook" and fn.name == "parse":
                for call in ast.walk(fn):
                    if isinstance(call, ast.Call):
                        call.keywords = [k for k in call.keywords if k.arg != "f"]
    return tree


def _mentions_f2(node):
    """True when the subtree names a k106 F2 identifier (_F2_* / _f2_* / F2_* / f2_*)."""
    for n in ast.walk(node):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else None
        if isinstance(name, str) and name.startswith(("_F2_", "_f2_", "F2_", "f2_")):
            return True
    return False


def _drop_f2_stmts(stmts):
    out = []
    for st in stmts:
        if isinstance(st, ast.FunctionDef) and st.name.startswith("_f2_"):
            continue
        if isinstance(st, ast.ClassDef) and st.name.startswith("_F2"):
            continue
        if isinstance(st, (ast.Assign, ast.AnnAssign, ast.Expr)) and _mentions_f2(st):
            continue
        if isinstance(st, ast.If) and _mentions_f2(st.test):
            out.extend(_drop_f2_stmts(st.orelse))
            continue
        for field in ("body", "orelse", "finalbody"):
            if isinstance(getattr(st, field, None), list):
                setattr(st, field, _drop_f2_stmts(getattr(st, field)))
        if isinstance(st, ast.Try):
            for handler in st.handlers:
                handler.body = _drop_f2_stmts(handler.body)
        out.append(st)
    return out


def _drop_f2_models(tree):
    """Takes k106 F2's additions out of a module and nothing else: its _F2_* / F2_* names, _f2_* functions, _F2*
    classes and the statements naming one (an assignment or a call), and each "if <F2 name>:" replaced by its else
    branch (the original statements).  The vendored protocol: LazyLevel._load / __getattr__, LazyBook.events,
    LazyAccount.data; research_direct_exit_ledger: the orders field and live_orders; research_direct_idle_gc: the
    request-window hold (tests/test_research_v6_37.py pins all three)."""
    _v638_undo.undo(tree)                               # v6.38's changes undone first (tests/_v638_undo.py)
    tree.body = _drop_f2_stmts(tree.body)
    return tree


def _drop_v637_models(tree):
    """Takes v6.37's additions to F2's modules out and nothing else: its _v6_37_ functions and the statements naming one
    -- the exit ledger's _v6_37_orders and F2's orders field naming it (tests/test_research_v6_37.py pins them; with
    _drop_f2_models after it, either order, the ledger is the committed one)."""
    _v638_undo.undo(tree)                               # v6.38's changes undone first (tests/_v638_undo.py)
    def mentions(node):
        return any(isinstance(n, ast.Name) and n.id.startswith("_v6_37_") for n in ast.walk(node))

    def stmts(body):
        out = []
        for st in body:
            if isinstance(st, ast.FunctionDef) and st.name.startswith("_v6_37_"):
                continue
            if isinstance(st, (ast.Assign, ast.AnnAssign, ast.Expr)) and mentions(st):
                continue
            for field in ("body", "orelse", "finalbody"):
                if isinstance(getattr(st, field, None), list):
                    setattr(st, field, stmts(getattr(st, field)))
            out.append(st)
        return out
    tree.body = stmts(tree.body)
    return tree


# ---- 12. wiring and the launcher ---------------------------------------------------------------------------------------------

def test_every_switch_ships_as_k48_recommends_and_agrees_with_its_agent_default():
    """S1 ships auto with every level withheld only while the against side's fill would add to |inventory| (k48's agdeep=2:
    level 0 alone loses in the calibrated 0.6.3 replay, this wins 6 of 6 windows); S2 and S3 ship on."""
    params = t617._params()
    for key, value in (("research_v623_realized_mode", "auto"), ("research_v623_against_levels", "deep"),
                       ("research_v623_against_gap_bps", "2.3"), ("research_v623_against_adding", "1"),
                       ("research_v623_class_guard", "1"),
                       ("research_v623_start_once", "1")):
        assert f"{key}={value} " in params + " " and params.count(f"{key}=") == 1, key
    for line in ('self.research_v623_realized_mode = v623_parse_mode(getattr(self.config, "research_v623_realized_mode", "auto"))',
                 'self.research_v623_against_levels = v623_parse_levels(getattr(self.config, "research_v623_against_levels", "deep"))',
                 'self.research_v623_against_gap_bps = v623_parse_gap_bps(\n'
                 '            getattr(self.config, "research_v623_against_gap_bps", V623_GAP_BPS_DEFAULT))',
                 'self.research_v623_against_adding = self._as_bool(getattr(self.config, "research_v623_against_adding", True))',
                 'self.research_v623_class_guard = self._as_bool(getattr(self.config, "research_v623_class_guard", True))',
                 'self.research_v623_start_once = self._as_bool(getattr(self.config, "research_v623_start_once", True))'):
        assert SIMPLE.count(line) == 1, line
    # the launcher's value parser (taos.common.config.ParseKwargs): a float where it parses, else the text
    for raw, want in (("auto", "auto"), ("1", "on"), ("0", "off")):
        assert vo.parse_mode(float(raw) if raw[0].isdigit() else raw) == want
    assert vo.parse_levels("L0") == "L0" and vo.parse_gap_bps(float("2.3")) == tq.THRESHOLD_BPS


def test_every_v623_name_the_agent_imports_is_the_modules_and_the_harness_has_it():
    tree = ast.parse(SIMPLE)
    imp = next(n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "research_v623_validator_063")
    for alias in imp.names:
        local = alias.asname or alias.name
        assert EXTRA[local] is getattr(vo, alias.name), local
    assert set(vo.__all__) <= {a.name for a in imp.names} | {"LAYOUT_FOREIGN", "LAYOUT_SAME", "LEVELS_DEEP", "MODE_AUTO",
                                                            "MODE_OFF", "MODE_ON", "REALIZED_SPEC"}


def test_each_rule_is_wired_once_and_in_its_place():
    for line in (OBSERVE_LINE, FOREIGN_LINE, AGAINST_LINE, JUDGE_LINE, SKIP_LINE, DROP_LINE):
        assert SIMPLE.count(line) == 1, line
    assert SIMPLE.count("from research_v623_validator_063 import (") == 1
    respond = _src("respond")
    assert respond.index(OBSERVE_LINE) < respond.index("self._a1961_note_base_decimals(state)") < respond.index(
        "response = super().respond(state) if quiet is None else quiet")
    body = _src("_v63_pass")
    assert body.index('v6_23_active = bool(getattr(self, "_v6_23_active", False))') < body.index("for raw_id in sorted(books")
    s3 = body.index(t622.S3_LINE)
    assert s3 < body.index(FOREIGN_LINE) < body.index(AGAINST_LINE) < body.index("placed_total += self._v633_book(")
    assert body.count("against=against") == 2 and body.count("cap_skip=cap_skip") == 4
    deep = _src("_v633_book")
    assert "cap_skip: bool = False,\n                   against: frozenset = frozenset(),\n" in deep
    judge = deep[deep.index("for row in deep_rows:"):deep.index("cancelled: set = set()")]
    assert judge.index("doomed.append((row, V69_CANCEL_LEVEL_OFF))") < judge.index(JUDGE_LINE) < judge.index(
        "doomed.append((row, V619_CANCEL_SURPLUS))")
    loop = deep[deep.index("for level, level_d in candidates[side]:"):]
    assert loop.index("if against and (side, level) in against:") < loop.index("if ladder and ((side, level) in resting")
    assert deep.count("response.cancel_orders(") == 1 and deep.count("response.limit_order(") == 1   # no new path
    start = _src("onStart")
    assert start.count("super().onStart(event)") == 2 and 'self._v6_23_count("starts_skipped")' in start
    assert t622.SIMPLE.count("def onStart(") == 1


def test_the_v623_block_is_gated_preflighted_and_listed():
    assert "; V6_20_BUILD=0; V6_21_BUILD=0; V6_22_BUILD=0; V6_23_BUILD=0; V6_24_BUILD=0; V6_25_BUILD=0; V6_26_BUILD=0; V6_27_BUILD=0; V6_28_BUILD=0; V6_29_BUILD=0; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0; V6_36_BUILD=0; V6_37_BUILD=0; V6_38_BUILD=0\n" in LAUNCHER
    arm = next(line for line in LAUNCHER.splitlines() if line.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_21_BUILD=1; V6_22_BUILD=1; V6_23_BUILD=1; V6_24_BUILD=1; V6_25_BUILD=1; V6_26_BUILD=1; V6_27_BUILD=1; V6_28_BUILD=1; V6_29_BUILD=1; V6_30_BUILD=1; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1; V6_36_BUILD=1; V6_37_BUILD=1; V6_38_BUILD=1"), arm
    assert 'if [[ "${V6_23_BUILD:-0}" == "1" ]]; then' in LAUNCHER
    assert "      tests/test_research_v6_23.py \\\n" in LAUNCHER
    assert LAUNCHER.index('echo "[preflight] v6.22 aged cap PASS') < LAUNCHER.index("# v6.23 S1-S3.")


_PATTERN = r"grep -qF (?:'([^']+)'|\"([^\"]+)\") \"\$(AGENT_PATH|SCRIPT_DIR)/([^\"]+)\""


def _block():
    start = LAUNCHER.index('if [[ "${V6_23_BUILD:-0}" == "1" ]]; then')
    return LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.23 validator 0.6.3 PASS', start)]


def _file(var, name, agent_path=strategy_view(), script_dir=ROOT):
    return (Path(agent_path) if var == "AGENT_PATH" else Path(script_dir)) / name


def test_every_v623_preflight_pattern_is_in_the_file_it_checks():
    pats = re.findall(_PATTERN, _block())
    assert len(pats) == 11, pats
    for single, double, var, name in pats:
        assert (single or double) in _file(var, name).read_text(), (single or double, name)


def _run_block(params, agent_path=strategy_view(), script_dir=ROOT):
    start = LAUNCHER.index("# v6.23 S1-S3.")
    stop = LAUNCHER.index('echo "[preflight] v6.23 validator 0.6.3 PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "SCRIPT_DIR": str(script_dir), "V6_23_BUILD": "1"}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_the_v623_block_passes_the_shipped_params_and_refuses_any_value_it_cannot_ship():
    shipped = t617._shipped()
    out = _run_block(shipped)
    assert out.returncode == 0 and \
        "[preflight] v6.23 validator 0.6.3 PASS (S1 realized_mode=auto levels=deep adding=1 gap=2.3bps:" in out.stdout, \
        out.stderr
    for mode in ("0", "1"):                                                 # the switches, as the PASS line names them
        out = _run_block(shipped.replace("research_v623_realized_mode=auto", f"research_v623_realized_mode={mode}"))
        assert out.returncode == 0 and f"(S1 realized_mode={mode} levels=deep adding=1 gap=2.3bps:" in out.stdout, \
            (mode, out.stderr)
    out = _run_block(shipped.replace("research_v623_against_levels=deep", "research_v623_against_levels=L0")
                     .replace("research_v623_against_adding=1", "research_v623_against_adding=0")
                     .replace("research_v623_against_gap_bps=2.3", "research_v623_against_gap_bps=10"))
    assert out.returncode == 0 and "(S1 realized_mode=auto levels=L0 adding=0 gap=10bps:" in out.stdout, out.stderr
    for key, value, bads, goods in (
            ("research_v623_realized_mode", "auto", ("x", "auto1", "2", "Auto", ""), ("0", "1")),
            ("research_v623_against_levels", "deep", ("L1", "Deep", ""), ("L0",)),
            ("research_v623_against_gap_bps", "2.3", ("-1", "x", "", "1e3", ".5"), ("0", "10")),
            ("research_v623_against_adding", "1", ("2", "x", "", "01"), ("0",)),
            ("research_v623_class_guard", "1", ("0", "10", ""), ()),
            ("research_v623_start_once", "1", ("0", "10", ""), ()),
            ("research_v617_touch_quote", "1", ("0",), ()),
            ("research_v69_deep_ladder", "1", ("0",), ())):
        assert shipped.count(f"{key}={value} ") + shipped.endswith(f"{key}={value}") == 1, key
        for bad in bads:
            out = _run_block(shipped.replace(f"{key}={value}", f"{key}={bad}" if bad else ""))
            assert out.returncode == 1 and "ERROR: v6.23" in out.stderr and key in out.stderr, (key, bad, out.stderr)
        for good in goods:
            out = _run_block(shipped.replace(f"{key}={value}", f"{key}={good}"))
            assert out.returncode == 0, (key, good, out.stderr)


def test_the_v623_block_refuses_a_tree_without_each_line_it_checks():
    cases = re.findall(_PATTERN + r" \|\| \{\n\s+echo \"ERROR: ([^\"]+)\" >&2", _block())
    assert len(cases) == 11, cases
    for single, double, var, name, message in cases:
        needle = single or double
        tmp = Path(tempfile.mkdtemp(prefix="t623_"))
        try:
            (tmp / "agents" / "strategy").mkdir(parents=True)
            (tmp / "taos" / "im" / "protocol").mkdir(parents=True)
            for n in ("Strategy1_Research_Simple.py", "research_v623_validator_063.py"):
                copy_source(STRATEGY / n, tmp / "agents" / "strategy" / n)
            copy_source(ROOT / "taos" / "im" / "protocol" / "models.py", tmp / "taos" / "im" / "protocol" / "models.py")
            path = _file(var, name, tmp / "agents" / "strategy", tmp)
            text = path.read_text()
            assert text.count(needle) == 1, needle
            path.write_text(text.replace(needle, "# removed"))
            out = _run_block(t617._shipped(), agent_path=tmp / "agents" / "strategy", script_dir=tmp)
            assert out.returncode == 1 and message in out.stderr, (name, needle, out.stderr)
        finally:
            shutil.rmtree(tmp)
