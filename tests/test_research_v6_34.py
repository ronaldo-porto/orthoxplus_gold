"""v6.34: research_v634_class1_entry -- the class-1 entry (C3f).

v6.33 + one switch, default on, its own off switch.

Validator 0.6.4 pays each asset class its own share of both pools (0.75 / 0.25); our UIDs traded no class-1 book.  On
the books of class 1 in the validator's class map (the state configuration's ``classes``, class 1 = its second entry)
each side takes ONE join-only touch quote in the class's own units (v6.32's class-0 clip and bound x min1 / min0), on
the side the fundamental moved toward (S9; no move: both sides), the adding side held 20 sim-s after a miner taker's
print (S17-R), paced per book from its own first class-1 state, gated by S14 on a class-1 estimate of its own, and by
D4.  The reference is the replay k98/vm703.py's c2 path (C3f); class 0 is v6.33's.

No test here touches the host's caches, the network or a live tree, and nothing is imported from the scratchpad: the
agent is t633's harness (v6.33's real methods on t632's) with v6.34's real methods, its real _init_build_switches
statements and respond()'s real v6.34 statement; the state carries book 3 (class 0) and class-1 books of the mainnet
layout (min 2.5364 on the 0.01 grid).
"""
import ast
import os
import re
import shutil
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_contract_guard as cg  # noqa: E402
import research_v62_breadth as v62b  # noqa: E402
import research_v617_touch_quote as tq  # noqa: E402
import research_v633_deep_layer as dl  # noqa: E402
import research_v641_pace as v641  # noqa: E402
import research_v6_32_fee_making as fm  # noqa: E402
import research_v6_33_touch_identity as ti  # noqa: E402
import research_v6_34_class1 as c1  # noqa: E402
import test_research_v6_2_14_touch_life as t14  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_17 as t617  # noqa: E402
import test_research_v6_23 as t623  # noqa: E402
import test_research_v6_24 as t624  # noqa: E402
import test_research_v6_25 as t625  # noqa: E402
import test_research_v6_26 as t626  # noqa: E402
import test_research_v6_27 as t627  # noqa: E402
import test_research_v6_28 as t628  # noqa: E402
import test_research_v6_29 as t629  # noqa: E402
import test_research_v6_30 as t630  # noqa: E402
import test_research_v6_31 as t631  # noqa: E402
import test_research_v6_32 as t632  # noqa: E402
import test_research_v6_33 as t633  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
MODULE = (STRATEGY / "research_v6_34_class1.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000
NOW = t632.NOW
BUY, SELL = dl.SIDE_BUY, dl.SIDE_SELL
SWITCH_LINE = 'self.research_v634_class1_entry = self._as_bool(getattr(self.config, "research_v634_class1_entry", True))'
HOOK_LINE = "self._v6_34_request(state)"
BRANCH_LINE = ("placed_total += self._v6_34_book(response, state, book_id, deep, raw_bid, raw_ask, inv, touch_rows, "
               "rows + deep_rows, already, req, budget=budget, expiry=expiry, cfg=cfg)")
BRANCH_HEAD = BRANCH_LINE[:BRANCH_LINE.index("touch_rows, ") + len("touch_rows,")]     # its first physical line
PASS_TEST = "v6_34_keys and self._v6_34_passes(book_id, instruction, v6_34_keys)"
ROUTE_TEST = "v6_34_route and book_id in v6_34_route"
REPORT_LINE = "self._v6_34_report()"
V6_34_NAMES = {"V6_34_CANCEL_OTHER": c1.CANCEL_OTHER, "V6_34MinerHolds": c1.MinerHolds,
               "V6_34_STATUS_OK": c1.STATUS_OK, "V6_34_CLASS1_ENTRY_VERSION": c1.V6_34_CLASS1_ENTRY_VERSION,
               "V6_34_WHY_CAPPED": c1.WHY_CAPPED, "v6_34_class1_layout": c1.class1_layout,
               "v6_34_join": c1.join, "v6_34_lot": c1.lot, "v6_34_p90": c1.p90, "v6_34_refusal": c1.refusal,
               "v6_34_pace_line": c1.pace_line, "v6_34_paced": c1.paced, "v6_34_resting_verdict": c1.resting_verdict,
               "v6_34_scaled": c1.scaled, "v6_34_side_plan": c1.side_plan}
# the names v6.34's methods reach beyond its own and the harness's scope, from the modules Simple imports them from
REACHED = {"sanitize_post_only_limit_price": cg.sanitize_post_only_limit_price}
EXTRA = dict(t633.EXTRA, **V6_34_NAMES, **REACHED)
METHODS = ("_v6_34_request", "_v6_34_tracker_ref", "_v6_34_book", "_v6_34_entry", "_v6_34_cancel", "_v6_34_passes",
           "_v6_34_count", "_v6_34_report", "_v6_34_snapshot")
FIELDS = ("version", "class1_entry_on", "map_source", "map_status", "classes", "class1_books", "class1_range",
          "route_books", "on_books", "books_seen", "min0", "min1", "scale", "tick_size", "volume_decimals", "clip",
          "bound", "inv_abs_p90", "fee_active", "estimate_bps", "window_fills", "own_fills", "pending", "holds",
          "paced_books", "refusals", "counts", "errors")
MIN1 = 2.5364                      # the mainnet class-1 minimum (0.25 x 300 / 29.57 -- the same 75-quote notional)
CLIP1, BOUND1 = 2.0 * MIN1 / 0.25, 48.0 * MIN1 / 0.25    # v6.32's class-0 touch clip 2.0 / bound 48.0 x min1/min0
CLASSES = [{"name": "simulation_0", "books": list(range(0, 96)), "priceDecimals": 2, "volumeDecimals": 4,
            "config": {"min_order_size": 0.25, "priceDecimals": 2, "volumeDecimals": 4}},
           {"name": "simulation_1", "books": list(range(96, 128)), "priceDecimals": 2, "volumeDecimals": 4,
            "config": {"min_order_size": MIN1, "priceDecimals": 2, "volumeDecimals": 4}}]
UID = t633.ON_UID                  # book 3 ON for the touch (t633's harness UID)


def _d4_books(uid, want):
    """The class-1 books whose D4 draw for ``uid`` is ``want``, ascending: from the frozen formula, not listed."""
    return [b for b in range(96, 128) if t633._formula4(uid, b) == want]


B_ON, B_OFF = _d4_books(UID, 1)[0], _d4_books(UID, 0)[0]


# ---- 1. the module ---------------------------------------------------------------------------------------------------

def test_the_module_states_its_version_and_its_structural_constants_from_their_sources():
    assert c1.V6_34_CLASS1_ENTRY_VERSION == "class1_entry_v6_34"
    assert c1.CLASS1 == 1 and c1.MINER_UID_SLOTS == 256
    assert c1.HOLD_NS is fm.HORIZON_NS and c1.HOLD_NS == 20 * S                       # the validator's making horizon
    assert c1.MAKING_WINDOW_NS is fm.WINDOW_NS and c1.MAKING_WINDOW_NS == 10_800 * S
    assert c1.REST_TICKS is tq.REST_TICKS and c1.TOUCH_CLIENT_BASE == tq.CLIENT_BASE      # v6.17's, never retyped
    tree = ast.parse(MODULE)
    mods = sorted(n.module for n in tree.body if isinstance(n, ast.ImportFrom)) + \
        sorted(a.name for n in tree.body if isinstance(n, ast.Import) for a in n.names)
    assert mods == ["__future__", "research_v617_touch_quote", "research_v633_deep_layer", "research_v641_pace",
                    "research_v6_32_fee_making", "typing", "math"], mods
    doc = ast.get_docstring(tree)
    for needle in ("STRUCTURAL", "OBSERVED", "k98", "C3f", "0.75 / 0.25", "classes", "min1 / min0", "JOIN",
                   "S17-R", "S9", "S14", "D4", "No constant here is sized from an observed metric"):
        assert needle in doc, needle
    for word in ("socket", "fcntl", "urllib", "random", "requests", "threading", "subprocess"):
        assert not re.search(rf"^\s*(import|from)\s+{word}\b", MODULE, re.M), word


def test_the_class_map_reads_class_1_from_the_configuration_never_a_fixed_range():
    got = c1.class1_layout(types.SimpleNamespace(classes=CLASSES, min_order_size=0.25))
    assert got["status"] == c1.STATUS_OK and got["books"] == frozenset(range(96, 128)) and got["classes"] == 2
    assert (got["min0"], got["min1"], got["price_decimals"], got["volume_decimals"]) == (0.25, MIN1, 2, 4)
    assert got["scale"] == pytest.approx(MIN1 / 0.25) and got["tick"] == pytest.approx(0.01)
    other = [dict(CLASSES[0], books=list(range(0, 64))), dict(CLASSES[1], books=list(range(64, 80)))]
    assert c1.class1_layout({"classes": other})["books"] == frozenset(range(64, 80))      # whatever the map says
    three = CLASSES + [dict(CLASSES[1], name="simulation_2", books=[128, 129])]
    assert c1.class1_layout({"classes": three})["books"] == frozenset(range(96, 128))     # class 1 is the 2nd entry
    objs = [types.SimpleNamespace(books=c["books"], priceDecimals=c["priceDecimals"],
                                  volumeDecimals=c["volumeDecimals"], config=types.SimpleNamespace(**c["config"]))
            for c in CLASSES]
    assert c1.class1_layout(types.SimpleNamespace(classes=objs))["status"] == c1.STATUS_OK
    entry = [CLASSES[0], dict(CLASSES[1], priceDecimals=3, config=dict(CLASSES[1]["config"], priceDecimals=2))]
    assert c1.class1_layout({"classes": entry})["tick"] == pytest.approx(0.001)          # the class entry's own grid
    fallback = [dict(CLASSES[0], config={}), CLASSES[1]]
    assert c1.class1_layout({"classes": fallback, "min_order_size": 0.25})["min0"] == 0.25   # the top level's
    for cfg, status in ((None, c1.STATUS_SINGLE), ({}, c1.STATUS_SINGLE), ({"classes": []}, c1.STATUS_SINGLE),
                        ({"classes": CLASSES[:1]}, c1.STATUS_NO_CLASS1), ({"classes": "x"}, c1.STATUS_UNREADABLE),
                        ({"classes": {"a": 1}}, c1.STATUS_UNREADABLE),
                        ({"classes": [CLASSES[0], dict(CLASSES[1], books=None)]}, c1.STATUS_UNREADABLE),
                        ({"classes": [CLASSES[0], dict(CLASSES[1], books=[95, 96])]}, c1.STATUS_UNREADABLE),
                        ({"classes": [CLASSES[0], dict(CLASSES[1], config={})]}, c1.STATUS_NO_UNITS),
                        ({"classes": [CLASSES[0], dict(CLASSES[1], config={"min_order_size": 0.0})]},
                         c1.STATUS_NO_UNITS),
                        ({"classes": [dict(CLASSES[0], config={}), CLASSES[1]]}, c1.STATUS_NO_UNITS),
                        ({"classes": [CLASSES[0], {"books": [96], "config": {"min_order_size": 1.0}}]},
                         c1.STATUS_NO_UNITS)):
        got = c1.class1_layout(cfg)
        assert got["status"] == status and got["books"] == frozenset(), (cfg, got["status"])


def test_sizes_are_v632s_class0_clip_and_bound_in_the_class_minimums_units_floored_on_its_grid():
    assert c1.scaled(2.0, MIN1 / 0.25) == pytest.approx(20.2912) and c1.scaled(48.0, MIN1 / 0.25) == \
        pytest.approx(486.9888)
    for bad in ((None, 1.0), (2.0, None), (2.0, 0.0), ("x", 1.0)):
        assert c1.scaled(*bad) is None, bad
    assert c1.lot(CLIP1, 4) == 20.2912 and c1.lot(37.40899, 4) == 37.4089 and c1.lot(5.0, 0) == 5.0
    for bad in ((None, 4), (-1.0, 4), (1.0, None), (1.0, -1)):
        assert c1.lot(*bad) == 0.0, bad


def test_the_pace_line_is_the_validators_rate_from_the_books_own_entry_capped_one_window_below_the_cap():
    cap = 500_000.0
    top = cap - cap * 10_800 / 86_400                                                 # 437,500
    assert c1.pace_line(cap, 0.0, NOW, NOW) == pytest.approx(cap * 600 / 86_400)        # one sampling interval
    assert c1.pace_line(cap, 1_000.0, NOW, NOW + 3600 * S) == pytest.approx(1_000.0 + cap * 4200 / 86_400)
    assert c1.pace_line(cap, 0.0, NOW, NOW + 86_400 * S) == pytest.approx(top)
    assert c1.pace_line(cap, 450_000.0, NOW, NOW) == pytest.approx(top)                # entered above T: T
    for bad in ((0.0, 0.0, NOW, NOW), (None, 0.0, NOW, NOW), (cap, None, NOW, NOW), (cap, 0.0, None, NOW)):
        assert c1.pace_line(*bad) is None, bad
    line = c1.pace_line(cap, 0.0, NOW, NOW)
    assert not c1.paced(line, line) and c1.paced(line + 0.01, line) and not c1.paced(1e9, None)


def test_the_join_is_the_touch_whenever_the_book_is_not_crossed():
    assert (c1.join(BUY, 30.00, 30.01, 0.01), c1.join(SELL, 30.00, 30.01, 0.01)) == (30.00, 30.01)   # 1 tick
    assert (c1.join(BUY, 30.00, 30.05, 0.01), c1.join(SELL, 30.00, 30.05, 0.01)) == (30.00, 30.05)
    for args in ((BUY, 30.01, 30.01, 0.01), (BUY, 30.02, 30.01, 0.01), (BUY, 30.00, 30.01, 0.0), (BUY, None, 30.0, 0.01),
                 (BUY, 0.0, 30.0, 0.01), ("x", 30.00, 30.01, 0.01), (SELL, 30.00, 30.004, 0.01)):
        assert c1.join(*args) is None, args


def test_a_miner_takers_print_marks_the_side_it_hit_and_nothing_else_does():
    me = 30
    hit = {"y": "t", "s": 0, "Ta": 120, "Ma": 77}
    assert c1.miner_hit(hit, me) == SELL                                     # a buy-initiated print took the asks
    assert c1.miner_hit(dict(hit, s=1), me) == BUY
    for ta in (0, 255):
        assert c1.miner_hit(dict(hit, Ta=ta), me) == SELL, ta
    for e in (dict(hit, Ta=256), dict(hit, Ta=-5), dict(hit, Ta=None), dict(hit, Ta=me), dict(hit, Ta=77, Ma=77),
              dict(hit, s=2), dict(hit, s=True), dict(hit, Ta=True), dict(hit, y="o"), {}, None, "x"):
        assert c1.miner_hit(e, me) is None, e
    assert c1.miner_hit(dict(hit, Ta=me), None) == SELL and c1.miner_hit(dict(hit, Ta=me), "x") == SELL
    assert c1.miner_hit(types.SimpleNamespace(y="t", s=1, Ta=12, Ma=-3), me) == BUY


def test_the_miner_hold_lasts_the_making_horizon_per_book_side():
    holds = c1.MinerHolds()
    t0 = NOW - 5 * S
    prints = [{"y": "t", "s": 0, "Ta": 120, "Ma": 7, "t": t0}, {"y": "t", "s": 1, "Ta": -3, "Ma": 7, "t": t0},
              {"y": "t", "s": 1, "Ta": 30, "Ma": 7, "t": t0}]
    assert holds.observe(100, prints, 30, NOW) == 1
    assert holds.held(100, SELL, t0 + 20 * S - 1) and not holds.held(100, SELL, t0 + 20 * S)
    assert not holds.held(100, BUY, NOW) and not holds.held(101, SELL, NOW)
    holds.observe(100, [{"y": "t", "s": 0, "Ta": 5, "Ma": 7}], 30, NOW)              # no print time: the state's
    assert holds.held(100, SELL, NOW + 20 * S - 1) and not holds.held(100, SELL, NOW + 20 * S)
    holds.observe(100, [{"y": "t", "s": 0, "Ta": 5, "Ma": 7, "t": t0}], 30, NOW)     # an older print never shortens it
    assert holds.held(100, SELL, NOW + 20 * S - 1)
    assert holds.observe("x", prints, 30, NOW) == 0 and holds.counts["malformed"] == 1
    assert holds.snapshot(NOW) == {"sides_tracked": 1, "sides_held": 1, "miner_prints": 3, "malformed": 1,
                                   "resets": 0}
    assert holds.held(100, SELL, None) is False
    assert not holds.held(100, SELL, NOW - S)                     # a print after the clock's now is never read
    holds.observe(101, [], 30, NOW - 3600 * S)                    # the clock ran back (a new run): every hold cleared
    assert holds.until == {} and holds.counts["resets"] == 1 and not holds.held(100, SELL, NOW)


def test_the_side_plan_is_the_replays_c2_order():
    kw = dict(clip=CLIP1, bound=BOUND1, min_size=MIN1, is_paced=False, s9_held=None, fee_gated=False, d4_off=False,
              miner_held=False)
    plan = c1.side_plan
    assert plan(BUY, inventory=0.0, **kw) == (pytest.approx(CLIP1), None)                 # flat: both sides add
    assert plan(SELL, inventory=0.0, **kw) == (pytest.approx(CLIP1), None)
    assert plan(SELL, inventory=50.0, **kw) == (50.0, None)                               # reducing: max(clip, |inv|)
    assert plan(SELL, inventory=5.0, **kw) == (pytest.approx(CLIP1), None)
    assert plan(BUY, inventory=BOUND1, **kw) == (0.0, c1.WHY_BOUND)                       # adding at the bound
    assert plan(BUY, inventory=BOUND1 - 0.001, **kw)[1] is None
    assert plan(SELL, inventory=BOUND1 + 10.0, **kw) == (BOUND1 + 10.0, None)            # reducing past it: quoted
    paced = dict(kw, is_paced=True)
    assert plan(BUY, inventory=10.0, **paced) == (0.0, c1.WHY_PACED)
    assert plan(SELL, inventory=10.0, **paced) == (10.0, None)                            # never across flat
    assert plan(SELL, inventory=40.0, **paced) == (40.0, None)
    assert plan(SELL, inventory=MIN1 - 0.01, **paced) == (0.0, c1.WHY_PACED_FLAT)
    for inv in (0.0, 10.0, -10.0):                                                     # S9: the held side, either way
        assert plan(SELL, inventory=inv, **dict(kw, s9_held=SELL)) == (0.0, c1.WHY_FP_MOVE), inv
        assert plan(BUY, inventory=inv, **dict(kw, s9_held=SELL))[1] is None, inv
    assert plan(BUY, inventory=0.0, **dict(kw, fee_gated=True)) == (0.0, c1.WHY_FEE_GATE)
    assert plan(BUY, inventory=-10.0, **dict(kw, fee_gated=True))[1] is None              # reducing: never gated
    for inv in (0.0, 10.0):
        assert plan(SELL, inventory=inv, **dict(kw, d4_off=True)) == (0.0, c1.WHY_TOUCH_IDENTITY), inv
    assert plan(BUY, inventory=0.0, **dict(kw, miner_held=True)) == (0.0, c1.WHY_MINER_HOLD)
    assert plan(BUY, inventory=-10.0, **dict(kw, miner_held=True)) == (pytest.approx(CLIP1), None)   # reducing: never
    order = dict(kw, is_paced=True, s9_held=BUY, fee_gated=True, d4_off=True, miner_held=True)
    assert plan(BUY, inventory=BOUND1, **order)[1] == c1.WHY_BOUND
    assert plan(BUY, inventory=0.0, **order)[1] == c1.WHY_PACED
    assert plan(BUY, inventory=0.0, **dict(order, is_paced=False))[1] == c1.WHY_FP_MOVE
    assert plan(BUY, inventory=0.0, **dict(order, is_paced=False, s9_held=None))[1] == c1.WHY_FEE_GATE
    assert plan(BUY, inventory=0.0, **dict(order, is_paced=False, s9_held=None, fee_gated=False))[1] == \
        c1.WHY_TOUCH_IDENTITY
    assert plan(BUY, inventory=0.0, **dict(kw, clip=None))[0] == 0.0


def test_a_resting_quote_stays_at_the_touch_and_leaves_two_ticks_behind_or_when_unwanted_or_paced_larger():
    kw = dict(is_paced=False, bid=30.00, ask=30.03, tick=0.01)
    rv = c1.resting_verdict
    assert rv(BUY, 30.00, CLIP1, CLIP1, None, **kw) is None
    assert rv(BUY, 29.99, CLIP1, CLIP1, None, **kw) is None                       # one tick behind: rests
    assert rv(BUY, 29.98, CLIP1, CLIP1, None, **kw) == c1.CANCEL_LEFT             # two ticks: v6.17's rest band
    assert rv(SELL, 30.04, CLIP1, CLIP1, None, **kw) is None and rv(SELL, 30.05, CLIP1, CLIP1, None, **kw) == \
        c1.CANCEL_LEFT
    assert rv(BUY, 30.00, CLIP1, 0.0, c1.WHY_MINER_HOLD, **kw) == c1.WHY_MINER_HOLD
    assert rv(SELL, 30.03, CLIP1, 10.0, None, **dict(kw, is_paced=True)) == c1.CANCEL_RESIZE
    assert rv(SELL, 30.03, 10.0, 10.0, None, **dict(kw, is_paced=True)) is None
    assert rv(SELL, 30.03, 30.0, 10.0, None, **kw) is None                          # unpaced: never resized
    assert rv(BUY, 30.00, CLIP1, CLIP1, None, **dict(kw, tick=None)) == c1.CANCEL_LEFT
    assert c1.p90([]) is None and c1.p90([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == 9 and c1.p90([5.0, None, "x"]) == 5.0


# ---- 2. the agent: t633's harness with v6.34's real methods, init and respond() statement ----------------------------

_CLASSES: dict = {}


def _mentions634(node):
    return t633._mentions634(node)


def _hook_source():
    """respond()'s v6.34 statement, exactly as respond has it."""
    fn = ast.parse(_src("respond")).body[0]
    hooks = [st for st in fn.body if isinstance(st, ast.If) and _mentions634(st.test)]
    assert len(hooks) == 1
    return "def _v634_hook(self, state):\n" + textwrap.indent(ast.unparse(hooks[0]), "    ") + "\n"


def _klass(base, names=None, v634=True):
    key = (base, v634, None if names is None else tuple(sorted((k, id(v)) for k, v in names.items())))
    if key not in _CLASSES:
        scope = dict(getattr(base, "_v63_pass").__globals__)
        scope.update(EXTRA if names is None else names)
        methods = {}
        if v634:
            for n in METHODS:
                ns = dict(scope)
                exec(compile(ast.Module(body=[ast.parse(_src(n)).body[0]], type_ignores=[]), f"<{n}>", "exec"), ns)
                methods[n] = ns[n]
            ns = dict(scope)
            exec(compile(_hook_source(), "<respond v6.34>", "exec"), ns)
            methods["_v634_hook"] = ns["_v634_hook"]
        used, venue = base._research_book_traded_volume, base._a195_venue_net_by_book

        def _used(self, book_id, _used=used):
            per = getattr(self, "used634", None) or {}
            return float(per[int(book_id)]) if int(book_id) in per else _used(self, book_id)

        def _venue(self, books, _venue=venue):
            out = dict(_venue(self, books))
            for b, v in (getattr(self, "inv634", None) or {}).items():
                out[int(b)] = float(v)
            return out
        methods["_research_book_traded_volume"], methods["_a195_venue_net_by_book"] = _used, _venue
        _CLASSES[key] = type("P634" if v634 else "R634", (base,), methods)
    return _CLASSES[key]


def _init_source():
    """The v6.34 statements of _init_build_switches -- the real ones -- as one function of self."""
    fn = ast.parse(_src("_init_build_switches")).body[0]
    body = [st for st in fn.body if _mentions634(st)]
    assert len(body) == 17, [ast.unparse(st) for st in body]
    return "def _init634(self):\n" + textwrap.indent("\n".join(ast.unparse(st) for st in body), "    ") + "\n"


def _init634(agent, *, on=True, names=None):
    ns = dict(EXTRA if names is None else names)
    exec(compile(_init_source(), "<v6.34 init>", "exec"), ns)
    cfg = dict(vars(getattr(agent, "config", None) or types.SimpleNamespace()))
    cfg.update(research_v634_class1_entry=on)
    agent.config = types.SimpleNamespace(**cfg)
    agent._as_bool = t630._as_bool()
    ns["_init634"](agent)


def _agent(*, uid=UID, on=True, names=None, **kw):
    """t633's harness agent (book 3: v6.33 arms its touch buy) constructed with ``uid``, with v6.34's methods, the v6.34
    state its real _init_build_switches statements leave, and per-book volume / inventory (``used634`` / ``inv634``)."""
    agent = t633._agent(uid=uid, **kw)
    agent.__class__ = _klass(type(agent), names)
    _init634(agent, on=on, names=names)
    agent.used634, agent.inv634 = {}, {}
    return agent


def _ref(*, uid=UID, **kw):
    """v6.33 exactly: t633's harness agent with the same per-book volume / inventory hooks, no v6.34 attribute."""
    agent = t633._agent(uid=uid, **kw)
    agent.__class__ = _klass(type(agent), v634=False)
    agent.used634, agent.inv634 = {}, {}
    return agent


def _c1book(bid=30.00, ask=30.03):
    return t631._book(bid=bid, ask=ask)


def _request(agent, *, ts=NOW, wall=1000.0, version=64, books=None, raws=None, classes=CLASSES, book3=None,
             notices=None):
    """One request as respond() makes it: the v6.26 hook, v6.23's observation, respond()'s v6.34 and v6.32 statements
    (each skipped on an agent without its state), then the pass; the state carries book 3 and ``books`` (default: the
    class-1 book B_ON at 30.00 / 30.03), each book's wire view in ``raws`` (prints 'e', fundamental 'f')."""
    resp = t63._Resp()
    state = t623._state(ts=ts, version=version, classes=classes, book=book3)
    for b, book in (books if books is not None else {B_ON: _c1book()}).items():
        state.books[b] = book
    for b, raw in (raws or {}).items():
        state.books[b]._raw = raw
    if notices is not None:
        state.notices = notices
    agent._direct_current_state_timestamp_ns = int(ts)
    agent._direct_request_wall_started = float(wall)
    agent._v626_hook(state)
    agent._v6_23_observe(state)
    if hasattr(type(agent), "_v634_hook"):
        agent._v634_hook(state)
    if hasattr(type(agent), "_v632_hook"):
        agent._v632_hook(state)
    agent._v63_pass(resp, state, {})
    return resp


def _placed(resp, book=None):
    return sorted(x for x in t63._placed(resp) if book is None or x[0] == book)


def _cancelled(resp, book=None):
    return sorted(x for x in t63._cancelled(resp) if book is None or x[0] == book)


def _q(book, side, price, qty):
    cid = tq.client_ids(book)[0 if side == BUY else 1]
    return (book, "BUY" if side == BUY else "SELL", cid, price, pytest.approx(qty))


def _counts(agent):
    return dict(agent._v6_34_counts)


def _rows(agent, event="V6_34_STATE"):
    return [kw for e, kw in agent.emitted if e == event]


def _rest(agent, oid, book, side, price, qty=CLIP1):
    t14._rest(agent, oid, book, 0 if side == BUY else 1, price, cid=tq.client_ids(book)[0 if side == BUY else 1],
              qty=qty)


def _miner(side_hit, *, ts=NOW - S, ta=120, ma=77, q=3.0, p=30.03):
    """A print whose taker is ``ta`` hitting our book's ``side_hit`` (sell side: a buy-initiated print)."""
    return {"y": "t", "i": 1, "s": 0 if side_hit == SELL else 1, "t": int(ts), "q": q, "p": p, "Ta": ta, "Ma": ma,
            "Mi": 5, "Ti": 6}


def test_the_init_statements_set_the_state_and_the_switch_ships_default_on():
    agent = _agent()
    assert agent.research_v634_class1_entry is True and isinstance(agent._v6_34_holds, c1.MinerHolds)
    assert (agent._v6_34_layout, agent._v6_34_c1, agent._v6_34_route, agent._v6_34_tracker) == ({}, frozenset(),
                                                                                               frozenset(), None)
    assert (agent._v6_34_active, agent._v6_34_est, agent._v6_34_pace0, agent._v6_34_keys, agent._v6_34_req) == \
        (False, None, {}, set(), {})
    assert (agent._v6_34_counts, agent._v6_34_errors, agent._v6_34_errors_seen) == ({}, 0, 0)
    assert (agent._v6_34_reported, agent._v6_34_erring) == (False, False)
    a = _agent()
    ns = dict(EXTRA)
    exec(compile(_init_source(), "<v6.34 init>", "exec"), ns)
    a.config = types.SimpleNamespace()
    ns["_init634"](a)
    assert a.research_v634_class1_entry is True
    for bad in ("0", "false", 0, False):
        a.config = types.SimpleNamespace(research_v634_class1_entry=bad)
        ns["_init634"](a)
        assert a.research_v634_class1_entry is False, bad


def test_a_class_1_book_without_an_s9_decision_joins_both_touches_in_the_class_units():
    agent = _agent()
    resp = _request(agent)
    assert _placed(resp, B_ON) == [_q(B_ON, BUY, 30.00, CLIP1), _q(B_ON, SELL, 30.03, CLIP1)]
    assert _placed(resp, 3) == [t633.TOUCH_BUY]                                    # class 0: v6.33's touch buy
    ixs = [ix for ix in resp.instructions if getattr(ix, "type", "") == "PLACE_ORDER_LIMIT" and ix.bookId == B_ON]
    for ix in ixs:
        assert (ix.postOnly, ix.timeInForce, ix.stp, ix.expiryPeriod, ix.leverage) == \
            (True, "GTT", "CANCEL_BOTH", int(t63.ol.BACKSTOP_MS * 1e6), 0.0)
    assert agent._v6_34_keys == {(B_ON, tq.client_ids(B_ON)[0]), (B_ON, tq.client_ids(B_ON)[1])}
    c = _counts(agent)
    assert (c["placed_buy"], c["placed_sell"], c["armed"], c["map_ok"]) == (1, 1, 2, 1) and agent._v6_34_errors == 0
    layout = agent._v6_34_layout
    assert layout["books"] == frozenset(range(96, 128)) and layout["min1"] == MIN1


def test_s9s_decision_on_the_book_picks_the_side_and_holds_the_other_adding_or_reducing():
    # (S9 holds, inventory) -> the one quote: the move side, sized as it adds (the clip) or reduces (|inventory|)
    cases = (((SELL, 0.0), (BUY, 30.00, CLIP1)), ((BUY, 0.0), (SELL, 30.03, CLIP1)),
             ((SELL, 30.0), (BUY, 30.00, CLIP1)), ((BUY, -30.0), (SELL, 30.03, CLIP1)),       # the held side reduces
             ((BUY, 30.0), (SELL, 30.03, 30.0)), ((SELL, -30.0), (BUY, 30.00, 30.0)))         # the move side reduces
    for (held, inv), (side, px, size) in cases:
        agent = _agent()
        agent.inv634[B_ON] = inv
        calls = []
        real = agent._v6_29_held

        def s9(book_id, fp, cfg, _real=real, _held=held):
            calls.append(book_id)
            _real(book_id, fp, cfg)
            return _held if book_id == B_ON else None
        agent._v6_29_held = s9
        resp = _request(agent)
        assert _placed(resp, B_ON) == [_q(B_ON, side, px, size)], (held, inv)
        assert _counts(agent)["held_c1_fp_move"] == 1 and B_ON in calls, (held, inv)


def test_s9_on_the_states_own_fundamental_of_the_class_1_book_picks_the_move_side():
    """The real decision: the book's fundamental in the state (v6.32 P1) rises (falls) between two requests further
    apart than any look-back -> S9 holds the sell (buy) side and only the move side is quoted; no move: both sides."""
    for f0, f1, side, px in ((30.10, 30.20, BUY, 30.00), (30.20, 30.10, SELL, 30.03), (30.10, 30.10, None, None)):
        agent = _agent()
        first = _request(agent, ts=NOW, wall=1000.0, raws={B_ON: {"f": f0}})
        assert len(_placed(first, B_ON)) == 2, (f0, f1)                     # no history yet: both sides
        resp = _request(agent, ts=NOW + 400 * S, wall=1400.0, raws={B_ON: {"f": f1}})
        if side is None:
            assert len(_placed(resp, B_ON)) == 2 and "held_c1_fp_move" not in _counts(agent)
        else:
            assert _placed(resp, B_ON) == [_q(B_ON, side, px, CLIP1)], (f0, f1)
            assert _counts(agent)["held_c1_fp_move"] == 1
        assert agent._v6_32_fp is not None and agent._v6_32_fp[1].get(B_ON) == f1


def test_the_quote_joins_and_never_improves_on_any_spread():
    for bid, ask in ((30.00, 30.01), (30.00, 30.02), (30.00, 30.10)):
        agent = _agent()
        agent.research_v631_touch_improve = True                                    # S12b on class 0 does not reach it
        resp = _request(agent, books={B_ON: _c1book(bid, ask)})
        assert _placed(resp, B_ON) == [_q(B_ON, BUY, bid, CLIP1), _q(B_ON, SELL, ask, CLIP1)], (bid, ask)
    agent = _agent()
    resp = _request(agent, books={B_ON: _c1book(30.01, 30.01)})                     # a locked book: no legal join --
    assert _placed(resp, B_ON) == [] and "book_states" not in _counts(agent)        # the pass never prices it


def test_a_one_tick_spread_joins_on_class_1_whatever_the_safety_ticks_and_class_0_stays_v633s():
    """Coordinator 03:35 JST: on a class-1 book the quote joins the best price whenever the spread is >= 1 tick, with or
    without v6.2.14's one-tick post-only gap (research_post_only_safety_ticks 1 or 2); class 0's touch quote at the same
    1-tick spread is v6.33's under either setting."""
    for safety in (1, 2):
        agent, ref = _agent(), _ref()
        agent.research_post_only_safety_ticks = ref.research_post_only_safety_ticks = safety
        book3 = t631._book(bid=100.00, ask=100.01)
        resp = _request(agent, books={B_ON: _c1book(30.00, 30.01)}, book3=book3)
        want = _request(ref, books={B_ON: _c1book(30.00, 30.01)}, book3=book3)
        assert _placed(resp, B_ON) == [_q(B_ON, BUY, 30.00, CLIP1), _q(B_ON, SELL, 30.01, CLIP1)], safety
        assert (_placed(resp, 3), _cancelled(resp, 3)) == (_placed(want, 3), _cancelled(want, 3)), safety
        assert "no_join" not in _counts(agent), safety


def test_the_venues_refusals_of_class_1_quotes_are_counted_by_kind():
    c0, c1b = tq.client_ids(3), tq.client_ids(B_ON)
    events = [{"type": "ERDPOL", "bookId": B_ON, "clientOrderId": c1b[0], "message": "CONTRACT_VIOLATION"},
              types.SimpleNamespace(type="ERDPOL", bookId=B_ON, clientOrderId=c1b[1], message="contract_violation"),
              {"type": "ERDPOL", "bookId": B_ON, "clientOrderId": c1b[1], "message": "INSUFFICIENT_BASE"},
              {"type": "RDPOL", "bookId": B_ON, "clientOrderId": c1b[0], "message": ""},           # accepted
              {"type": "ERDPOL", "bookId": 3, "clientOrderId": c0[0], "message": "CONTRACT_VIOLATION"},  # class 0
              {"type": "ERDPOL", "bookId": B_ON, "clientOrderId": 40000 + 10 * B_ON + 1,
               "message": "CONTRACT_VIOLATION"}, "garbage", None]
    agent = _agent()
    agent._tick = 1
    _request(agent, notices={UID: events, UID + 1: events})
    c = _counts(agent)
    assert (c["refused_post_only"], c["refused_other"]) == (2, 1)
    assert agent._v6_34_refused == {"post_only": 2, "INSUFFICIENT_BASE": 1}
    assert _rows(agent)[0]["refusals"] == {"post_only": 2, "INSUFFICIENT_BASE": 1}
    assert c1.refusal({"y": "ERDPOL", "b": B_ON, "c": c1b[0], "m": "X"}, {B_ON}) == "X"
    assert c1.refusal({"type": "ERDPOL", "bookId": B_ON, "clientOrderId": c1b[0], "message": ""}, {B_ON}) == "unknown"
    assert c1.refusal({"type": "ERDPOL", "bookId": "x", "clientOrderId": 1}, {B_ON}) is None
    odd = _agent()                                                    # unreadable notices: counted, the entry goes on
    resp = _request(odd, notices=object())
    assert odd._v6_34_errors == 1 and len(_placed(resp, B_ON)) == 2 and "refused_other" not in _counts(odd)


def test_a_reducing_side_is_sized_at_the_position_and_an_adding_side_stops_at_the_bound():
    agent = _agent()
    agent.inv634[B_ON] = 60.0
    assert _placed(_request(agent), B_ON) == [_q(B_ON, BUY, 30.00, CLIP1), _q(B_ON, SELL, 30.03, 60.0)]
    agent = _agent()
    agent.inv634[B_ON] = -BOUND1
    assert _placed(_request(agent), B_ON) == [_q(B_ON, BUY, 30.00, BOUND1)]
    assert _counts(agent)["held_c1_bound"] == 1


def test_s17r_holds_the_adding_side_twenty_sim_seconds_after_a_miner_takers_print_only():
    for ta, ma, held in ((120, 77, True), (-3, 77, False), (UID, 77, False), (77, 77, False), (256, 77, False)):
        agent = _agent()
        resp = _request(agent, raws={B_ON: {"e": [_miner(SELL, ta=ta, ma=ma)]}})
        want = [_q(B_ON, BUY, 30.00, CLIP1)] + ([] if held else [_q(B_ON, SELL, 30.03, CLIP1)])
        assert _placed(resp, B_ON) == want, (ta, ma)
    agent = _agent()
    agent.inv634[B_ON] = 40.0                                                      # long: the sell side reduces
    resp = _request(agent, raws={B_ON: {"e": [_miner(SELL), _miner(BUY)]}})
    assert _placed(resp, B_ON) == [_q(B_ON, SELL, 30.03, 40.0)]                    # buy (adding) held, sell kept
    agent = _agent()                                                               # the hold ends at print + 20 s
    _request(agent, ts=NOW, raws={B_ON: {"e": [_miner(SELL, ts=NOW - S)]}})
    _rest(agent, 601, B_ON, BUY, 30.00)
    assert _placed(_request(agent, ts=NOW + 18 * S), B_ON) == []
    assert _placed(_request(agent, ts=NOW + 19 * S), B_ON) == [_q(B_ON, SELL, 30.03, CLIP1)]
    agent = _agent()                                                               # a resting adding quote: cancelled
    _rest(agent, 602, B_ON, SELL, 30.03)
    _rest(agent, 603, B_ON, BUY, 30.00)
    resp = _request(agent, raws={B_ON: {"e": [_miner(SELL)]}})
    assert _cancelled(resp, B_ON) == [(B_ON, 602)] and _placed(resp, B_ON) == []
    assert _counts(agent)["cancel_c1_miner_hold"] == 1


def test_s14_per_class_gates_the_class_1_adding_side_on_its_own_estimate_and_class_0_keeps_its_own():
    agent = _agent()
    agent.accounts[B_ON] = t632._Acct(0.004)                                       # W x rate = 2e-5 = 0.2 bps
    tracker = agent._v6_34_tracker_ref()
    tracker.set_active(True, NOW - S)
    for _k in range(fm.MIN_FILLS):
        tracker.window.add(NOW, 1.0e-5 * 1000.0, 1000.0)                            # class-1 estimate 0.1 bps
    agent.inv634[B_ON] = 30.0
    resp = _request(agent)
    assert _placed(resp, B_ON) == [_q(B_ON, SELL, 30.03, 30.0)]                    # the buy adds: gated
    assert _counts(agent)["held_c1_fee_gate"] == 1 and _counts(agent)["fee_gated_states"] == 1
    assert _placed(resp, 3) == [t633.TOUCH_BUY] and agent._v6_32_est is None          # class 0: its own (empty) window
    for rate, gated in ((0.001, False), (-0.001, False), (None, False)):
        a = _agent()
        if rate is not None:
            a.accounts[B_ON] = t632._Acct(rate)
        tr = a._v6_34_tracker_ref()
        tr.set_active(True, NOW - S)
        for _k in range(fm.MIN_FILLS):
            tr.window.add(NOW, 1.0e-5 * 1000.0, 1000.0)
        assert len(_placed(_request(a), B_ON)) == 2, rate
    v63 = _agent()
    v63.accounts[B_ON] = t632._Acct(0.004)
    tr = v63._v6_34_tracker_ref()
    tr.set_active(True, NOW - S)
    for _k in range(fm.MIN_FILLS):
        tr.window.add(NOW, 1.0e-5 * 1000.0, 1000.0)
    assert len(_placed(_request(v63, version=63), B_ON)) == 2                     # a 0.6.3 state: inactive


def test_class_1_fills_feed_the_class_1_tracker_only():
    ours = {"y": "t", "i": 9, "s": 1, "t": NOW - S, "q": 5.0, "p": 30.00, "Ta": -2, "Ma": UID, "Mi": 501, "Ti": 7}
    agent = _agent()
    _request(agent, raws={B_ON: {"e": [ours]}, 3: {"e": [dict(ours, p=100.0, Ma=UID)]}})
    assert agent._v6_34_tracker.counts["own_fills"] == 1 and agent._v6_32_tracker.counts["own_fills"] == 1
    assert B_ON in agent._v6_34_tracker.books and B_ON not in agent._v6_32_tracker.books
    assert 3 in agent._v6_32_tracker.books and 3 not in agent._v6_34_tracker.books
    off = _agent(on=False)                                                         # off: v6.33 -- every book pooled
    _request(off, raws={B_ON: {"e": [ours]}})
    assert off._v6_32_tracker.counts["own_fills"] == 1 and off._v6_34_tracker is None


def test_the_pace_line_starts_at_the_books_first_class_1_state_and_binds_adding_then_the_unwind():
    agent = _agent()
    agent.used634[B_ON] = 100_000.0
    _request(agent, ts=NOW)
    assert agent._v6_34_pace0[B_ON] == (NOW, 100_000.0)
    line = c1.pace_line(500_000.0, 100_000.0, NOW, NOW + 60 * S)
    agent.used634[B_ON] = line + 1.0
    agent.inv634[B_ON] = 8.0                                                       # long 8: the sell reduces
    resp = _request(agent, ts=NOW + 60 * S)
    assert _placed(resp, B_ON) == [_q(B_ON, SELL, 30.03, 8.0)]                     # min(clip, |inv|), never past flat
    assert _counts(agent)["paced_states"] == 1 and _counts(agent)["held_c1_paced"] == 1
    agent2 = _agent()
    agent2._v6_34_pace0[B_ON] = (NOW, 0.0)
    agent2.used634[B_ON] = c1.pace_line(500_000.0, 0.0, NOW, NOW) + 10.0
    agent2.inv634[B_ON] = 8.0
    _rest(agent2, 701, B_ON, SELL, 30.03, qty=CLIP1)                               # a full clip resting: resized
    resp = _request(agent2)
    assert _cancelled(resp, B_ON) == [(B_ON, 701)] and _counts(agent2)["cancel_c1_resize"] == 1
    agent2.inv634[B_ON] = MIN1 - 0.5                                               # an unwind below the minimum
    assert _placed(_request(agent2, ts=NOW + S), B_ON) == [] and _counts(agent2)["held_c1_paced_flat"] == 1
    capped = _agent()
    capped.used634[B_ON] = 500_000.0
    _rest(capped, 702, B_ON, BUY, 30.00)
    resp = _request(capped)
    assert _placed(resp, B_ON) == [] and _cancelled(resp, B_ON) == [(B_ON, 702)]
    assert _counts(capped)["held_c1_capped"] == 2
    rewound = _agent()                                                             # a clock that ran back: a new run
    rewound._v6_34_pace0[B_ON] = (NOW + 3600 * S, 400_000.0)
    rewound.used634[B_ON] = 1_000.0
    assert len(_placed(_request(rewound), B_ON)) == 2 and rewound._v6_34_pace0[B_ON] == (NOW, 1_000.0)


def test_d4_off_class_1_books_take_no_quote_and_cancel_a_resting_one():
    agent = _agent()
    _rest(agent, 801, B_OFF, BUY, 30.00)
    resp = _request(agent, books={B_ON: _c1book(), B_OFF: _c1book()})
    assert len(_placed(resp, B_ON)) == 2 and _placed(resp, B_OFF) == []
    assert _cancelled(resp, B_OFF) == [(B_OFF, 801)] and _counts(agent)["cancel_c1_touch_identity"] == 1
    assert agent._v6_33_table.value(B_OFF) == 0 and agent._v6_33_table.value(B_ON) == 1
    d4off = _agent()
    d4off.research_v633_touch_identity = False                                     # D4 off: every class-1 book quotes
    assert len(_placed(_request(d4off, books={B_ON: _c1book(), B_OFF: _c1book()}), B_OFF)) == 2


def test_a_resting_quote_rests_at_the_touch_is_left_behind_two_ticks_and_a_cancelled_side_waits():
    agent = _agent()
    _rest(agent, 901, B_ON, BUY, 30.00)
    _rest(agent, 902, B_ON, SELL, 30.03)
    resp = _request(agent)
    assert _placed(resp, B_ON) == [] and _cancelled(resp, B_ON) == []
    agent = _agent()
    _rest(agent, 903, B_ON, BUY, 29.98)                                            # the touch moved two ticks up
    resp = _request(agent)
    assert _cancelled(resp, B_ON) == [(B_ON, 903)] and _placed(resp, B_ON) == [_q(B_ON, SELL, 30.03, CLIP1)]
    assert _counts(agent)["cancel_c1_left"] == 1 and _counts(agent)["wait_cancel_seen"] == 1
    agent = _agent()
    t14._rest(agent, 904, B_ON, 0, 29.50, cid=40000 + 10 * B_ON + 1, qty=2.0)      # a class-0 layer's order
    resp = _request(agent)
    assert _cancelled(resp, B_ON) == [(B_ON, 904)] and _counts(agent)["cancel_c1_other_order"] == 1


def test_the_final_validator_lets_this_requests_class_1_quotes_through_s2_and_nothing_else():
    fn = ast.parse(_src("_research_final_validate_instructions")).body[0]
    loop = next(n for n in fn.body if isinstance(n, ast.For) and ast.unparse(n.target) == "instruction")
    tests = [ast.unparse(st.test) if isinstance(st, ast.If) else None for st in loop.body]
    i = tests.index(PASS_TEST)
    assert tests[i + 1] == "v6_23_foreign and book_id in v6_23_foreign"
    assert [ast.unparse(s) for s in loop.body[i].body] == ["validated_ids.add(id(instruction))", "continue"]
    pre = [ast.unparse(st) for st in fn.body if isinstance(st, ast.Assign) and "v6_34_keys" in ast.unparse(st)]
    assert len(pre) == 1
    body = "\n".join(ast.unparse(s) for s in loop.body[:i + 2]) + "\nreached.append(id(instruction))"
    src = ("def _probe(self, placements, validated_ids, v6_23_foreign):\n    reached = []\n    " + pre[0] +
           "\n    for instruction in placements:\n" + textwrap.indent(body, "        ") +
           "\n    return reached\n")
    ns = dict(getattr(type(_agent()), "_v63_pass").__globals__)
    ns.update(EXTRA, V623_REJECT_FOREIGN=t623.vo.REJECT_FOREIGN)
    exec(compile(src, "<final validator, its first statements>", "exec"), ns)
    agent = _agent()
    agent._get = lambda ix, *names: next((getattr(ix, n) for n in names if getattr(ix, n, None) is not None), None)
    agent._research_instruction_side = lambda ix: ix.direction
    agent._research_log_final_contract_reject = lambda *a, **k: None
    agent._v6_23_count = lambda *a, **k: None
    resp = _request(agent)
    ours = [ix for ix in resp.instructions if getattr(ix, "type", "") == "PLACE_ORDER_LIMIT" and ix.bookId == B_ON]
    legacy = types.SimpleNamespace(type="PLACE_ORDER_LIMIT", bookId=B_ON, direction="SELL", clientOrderId=12345)
    forged = types.SimpleNamespace(type="PLACE_ORDER_LIMIT", bookId=B_ON + 1, direction="BUY",
                                   clientOrderId=tq.client_ids(B_ON + 1)[0])
    zero = [ix for ix in resp.instructions if getattr(ix, "type", "") == "PLACE_ORDER_LIMIT" and ix.bookId == 3]
    validated = set()
    reached = ns["_probe"](agent, ours + [legacy, forged] + zero, validated, frozenset(range(96, 128)))
    assert validated == {id(ix) for ix in ours} and len(ours) == 2
    assert reached == [id(ix) for ix in zero]                       # class 0 goes on; the legacy / forged ones: S2
    off = _agent(on=False)
    off._v6_34_keys = set(agent._v6_34_keys)
    off._get, off._research_instruction_side = agent._get, agent._research_instruction_side
    off._research_log_final_contract_reject, off._v6_23_count = agent._research_log_final_contract_reject, \
        agent._v6_23_count
    validated = set()
    assert ns["_probe"](off, ours, validated, frozenset(range(96, 128))) == [] and validated == set()
    assert agent._v6_34_passes(B_ON, types.SimpleNamespace(clientOrderId="x"), {(B_ON, 1)}) is False
    assert agent._v6_34_errors == 1


def test_class_0_is_v633s_with_the_switch_on_and_everything_is_v633s_with_it_off():
    for inv in (None, 3.0, -3.0):
        on, ref, off = _agent(inv=inv), _ref(inv=inv), _agent(inv=inv, on=False)
        for k in range(2):
            raws = {B_ON: {"e": [_miner(SELL, ts=NOW + (k - 1) * S)]}, 3: {"e": [_miner(BUY, ts=NOW + (k - 1) * S,
                                                                                         p=100.0)]}}
            r_on = _request(on, ts=NOW + k * S, wall=1000.0 + k, raws=raws)
            r_ref = _request(ref, ts=NOW + k * S, wall=1000.0 + k, raws=raws)
            r_off = _request(off, ts=NOW + k * S, wall=1000.0 + k, raws=raws)
            assert (_placed(r_on, 3), _cancelled(r_on, 3)) == (_placed(r_ref, 3), _cancelled(r_ref, 3)), (inv, k)
            assert (_placed(r_off), _cancelled(r_off)) == (_placed(r_ref), _cancelled(r_ref)), (inv, k)
        assert off._v6_34_counts == {} and off._v6_34_errors == 0 and off._v6_34_c1 == frozenset()
        assert on._v6_32_est == ref._v6_32_est and on._v6_33_counts == ref._v6_33_counts, inv
        assert {k: v for k, v in on._v617_counts.items() if not k.startswith("book_")} == \
            {k: v for k, v in ref._v617_counts.items() if not k.startswith("book_")}, inv


def test_with_the_switch_off_the_pass_routes_no_book_to_the_class_1_entry_whatever_the_state_holds():
    """The pass reads the class-1 books only with the switch on: a class-1 set and layout left on the agent (as an
    earlier request with the switch on would leave them) route nothing to the class-1 entry while it is off."""
    off = _agent(on=False)
    off._v6_34_c1 = frozenset(range(96, 128))
    off._v6_34_layout = c1.class1_layout({"classes": CLASSES, "min_order_size": 0.25})
    resp = t63._Resp()
    state = t623._state(ts=NOW, version=64, classes=CLASSES)
    state.books[B_ON] = _c1book()
    off._direct_current_state_timestamp_ns = int(NOW)
    off._direct_request_wall_started = 1000.0
    off._v626_hook(state)
    off._v6_23_observe(state)
    off._v63_pass(resp, state, {})                                    # no v6.34 request: the set stays as left
    assert _placed(resp, B_ON) == [] and _counts(off) == {} and off._v6_34_keys == set()


def test_without_a_class_map_or_with_an_unreadable_one_no_book_is_class_1():
    for classes, status in ((None, "single"), (CLASSES[:1], "no_class1"), ("x", "unreadable"),
                            ([CLASSES[0], dict(CLASSES[1], config={})], "no_units")):
        agent = _agent()
        resp = _request(agent, classes=classes)
        assert _placed(resp, B_ON) == [] and agent._v6_34_c1 == frozenset(), status
        assert _counts(agent)["map_" + status] == 1 and agent._v6_34_errors == 0, status


def test_every_v634_path_fails_closed_and_counts():
    def boom(*a, **k):
        raise RuntimeError("boom")
    for names in (dict(EXTRA, v6_34_side_plan=boom), dict(EXTRA, v6_34_class1_layout=boom)):
        agent, ref = _agent(names=names), _ref()
        resp, want = _request(agent), _request(ref)
        assert _placed(resp, B_ON) == [] and agent._v6_34_errors == 1
        assert (_placed(resp, 3), _cancelled(resp, 3)) == (_placed(want, 3), _cancelled(want, 3))
    agent = _agent()
    agent._v6_34_counts = None
    agent._v6_34_count("x")
    assert agent._v6_34_counts == {"x": 1}
    agent._v6_34_counts = {"x": "y"}
    agent._v6_34_count("x")
    assert agent._v6_34_errors == 1


# ---- 3. the V6_34_STATE row ------------------------------------------------------------------------------------------

def test_the_state_row_comes_at_the_first_pass_every_100_ticks_and_at_the_first_error():
    agent = _agent()
    flag = {"boom": False}

    def maybe(*a, **k):
        if flag["boom"]:
            raise RuntimeError("boom")
        return c1.side_plan(*a, **k)
    agent2 = _agent(names=dict(EXTRA, v6_34_side_plan=maybe))
    for a in (agent, agent2):
        for i, tick in enumerate((1, 2, 99, 100, 101)):
            a._tick = tick
            _request(a, ts=NOW + i * S, wall=1000.0 + i)
    rows = _rows(agent)
    assert [(r["reason"], r["tick"]) for r in rows] == [("start", 1), ("periodic", 100)]
    assert tuple(k for k in rows[0] if k not in ("reason", "tick")) == FIELDS
    first = rows[0]
    assert (first["version"], first["class1_entry_on"], first["map_source"], first["map_status"]) == \
        ("class1_entry_v6_34", 1, "config.classes[1]", "ok")
    assert (first["classes"], first["class1_books"], first["class1_range"], first["on_books"]) == (2, 32, [96, 127], 1)
    assert (first["min0"], first["min1"], first["tick_size"], first["volume_decimals"]) == (0.25, MIN1, 0.01, 4)
    assert first["clip"] == pytest.approx(CLIP1) and first["bound"] == pytest.approx(BOUND1)
    assert (first["inv_abs_p90"], first["fee_active"], first["estimate_bps"], first["errors"]) == (0.0, 1, None, 0)
    assert first["counts"]["placed_buy"] == 1 and first["holds"]["sides_tracked"] == 0
    for k, (tick, boom) in enumerate(((102, True), (103, True), (104, False), (105, True))):
        flag["boom"] = boom
        agent2._tick = tick
        _request(agent2, ts=NOW + (5 + k) * S, wall=1005.0 + k)
    assert [(r["reason"], r["tick"]) for r in _rows(agent2)] == [("start", 1), ("periodic", 100), ("error", 102),
                                                                  ("error", 105)]


def test_the_row_never_fails_the_request_and_waits_for_the_reply_and_off_it_says_so():
    bad, ref = _agent(), _agent()
    emitted = bad.emitted
    bad._emit = lambda event, force=False, **kw: (1 / 0) if event == "V6_34_STATE" else emitted.append((event, kw))
    bad._tick = ref._tick = 1
    assert _placed(_request(bad)) == _placed(_request(ref)) and bad._v6_34_errors == 1 and _rows(bad) == []
    deferred = _agent()
    work = []
    deferred._v629_request_work = types.SimpleNamespace(add=lambda name, fn: work.append((name, fn)))
    deferred._tick = 1
    _request(deferred)
    names = [n for n, _fn in work]
    assert names[-2:] == ["v6_33_state_row", "v6_34_state_row"] and _rows(deferred) == []
    dict(work)["v6_34_state_row"]()
    assert _rows(deferred)[0]["reason"] == "start"
    off = _agent(on=False)
    off._tick = 1
    _request(off)
    row = _rows(off)[0]
    assert (row["class1_entry_on"], row["map_status"], row["class1_books"], row["counts"]) == (0, None, 0, {})
    older = _ref()
    older._tick = 1
    _request(older)
    assert not _rows(older)


# ---- 4. wiring -------------------------------------------------------------------------------------------------------

def test_the_switch_ships_on_after_v633s_key_and_agrees_with_its_agent_default():
    params = t617._params()
    # v6.35: ships 0 (the launcher's "# v6.35." note); the agent default stays on, the launcher passes =0 explicitly
    assert "research_v634_class1_entry=0 " in t617._shipped() + " " and params.count("research_v634_class1_entry=") == 1
    assert 'research_v633_touch_identity=1 \\\nresearch_v634_class1_entry=0"\n' in LAUNCHER
    init = _src("_init_build_switches")
    assert init.count(SWITCH_LINE) == 1
    assert init.index("self._v6_33_erring = False") < init.index(SWITCH_LINE) < \
        init.index("self._v6_34_erring = False") < init.index("self._v69_counts: dict[str, int] = {}")


def test_every_v634_name_the_agent_imports_is_the_modules():
    tree = ast.parse(SIMPLE)
    imp = next(n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "research_v6_34_class1")
    for alias in imp.names:
        assert V6_34_NAMES[alias.asname or alias.name] is getattr(c1, alias.name), alias.asname
    assert SIMPLE.count("from research_v6_34_class1 import (") == 1
    assert sorted(a.asname or a.name for a in imp.names) == sorted(V6_34_NAMES)
    assert t623.V6_34_IDENTIFIERS == {"research_v634_class1_entry", "V6_34_CANCEL_OTHER", "V6_34MinerHolds",
                                      "V6_34_STATUS_OK", "V6_34_CLASS1_ENTRY_VERSION", "V6_34_WHY_CAPPED"}
    for mod in (t624, t625, t626, t627, t628, t629, t630, t631, t632, t633):
        assert mod.V6_34_IDENTIFIERS is t623.V6_34_IDENTIFIERS, mod.__name__
    bound = [a.asname or a.name for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names]
    for name in list(V6_34_NAMES) + list(REACHED):
        assert bound.count(name) == 1, name
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    names = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
    assert sorted(n for n in names if n.startswith("_v6_34_")) == sorted(METHODS)
    assert not [n for n in names if n.startswith("_v634_")]                       # the old v6.3.4 prefix untouched


def test_each_line_is_wired_once_in_its_place():
    for line in (SWITCH_LINE, HOOK_LINE, BRANCH_HEAD, REPORT_LINE, "layout = v6_34_class1_layout(cfg)",
                 "self._v6_34_holds.observe(book_id, prints, uid, now)"):
        assert SIMPLE.count(line) == 1, line
    respond = _src("respond")
    assert respond.index("self._v6_23_observe(state)") < respond.index(HOOK_LINE) < \
        respond.index("self._v6_32_request(state)")
    req = ast.parse(_src("_v6_32_request")).body[0]
    loop = next(n for n in ast.walk(req) if isinstance(n, ast.For) and ast.unparse(n.target) == "(raw_id, book)")
    tests = [ast.unparse(st.test) if isinstance(st, ast.If) else None for st in loop.body]
    i = tests.index(ROUTE_TEST)
    assert tests[i - 1] == "fp_on" and tests[i + 1] == "tracker is not None"
    assert [ast.unparse(s) for s in loop.body[i].body] == ["continue"]
    body = _src("_v63_pass")
    assert body.index("v6_23_foreign = getattr(self, \"_v6_23_foreign\", None) or frozenset()") < \
        body.index("v6_34_c1 = ") < body.index("for raw_id in sorted(books")
    assert body.index("self._v6_32_note(book_id, inv)") < body.index("if v6_34_c1 and book_id in v6_34_c1:") < \
        body.index(BRANCH_HEAD) < body.index("deep_open = False")
    assert body.index("self._v6_33_report()") < body.index(REPORT_LINE) < body.index("return placed_total")
    for name in ("_v617_touch", "_v617_book", "_v633_book", "_v6_33_off", "_v6_29_held", "_v6_32_note"):
        assert "v6_34" not in _src(name) and "v634" not in _src(name), name


def test_the_class_1_branch_sits_after_v632s_note_and_before_any_class_0_layer():
    fn = ast.parse(_src("_v63_pass")).body[0]
    loop = next(n for n in fn.body if isinstance(n, ast.For) and "sorted(books" in ast.unparse(n.iter))
    top = [ast.unparse(st.test) if isinstance(st, ast.If) else ast.unparse(st)[:40] for st in loop.body]
    i = top.index("v6_34_c1 and book_id in v6_34_c1")
    assert top[i - 1] == "bool(getattr(self, 'research_v632_fee_making', False))" and top[i + 1] == "deep_open = False"
    assert [ast.unparse(s) for s in loop.body[i].body] == [BRANCH_LINE, "continue"]


def test_v634s_methods_place_only_post_only_join_quotes_with_the_touch_slots_ids():
    text = _src("_v6_34_entry")
    assert text.count("response.limit_order(") == 1 and "postOnly=True" in text and "stp=STP.CANCEL_BOTH" in text
    assert "timeInForce=TimeInForce.GTT, expiryPeriod=expiry" in text and "v617_client_ids(book_id)" in text
    assert "_v6_31_price" not in text and "improve" not in text                    # L1: never improve
    assert "v6_34_join(side, raw_bid, raw_ask, tick1)" in text and "safety_ticks=1, price_decimals=pd1)" in text
    for word in ("limit_order", "cancel_orders", "postOnly"):
        assert not re.search(rf"\b{word}\b", MODULE), word


# ---- 5. v6.33 and older, statement by statement ----------------------------------------------------------------------

V632_COMMIT = t633.V632_COMMIT
V633_MODULE_SHA256 = "fb0a1e071f1135c2"           # k96 FROZEN_2: research_v6_33_touch_identity.py (prefix)
CHANGED = {"_init_build_switches", "_v63_pass", "_v6_32_request", "respond", "_research_final_validate_instructions"}


def test_v634s_strip_takes_out_its_methods_and_its_statements_in_five():
    stripped = t633._Drop634().visit(ast.parse(SIMPLE))
    assert not _mentions634(stripped)
    a, b = t629._methods(stripped), t629._methods(ast.parse(SIMPLE))
    assert set(b) - set(a) == set(METHODS) and not set(a) - set(b)
    assert {k for k in a if a[k] != b[k]} == CHANGED
    for n in ast.walk(stripped):
        assert not (isinstance(n, ast.ImportFrom) and n.module == "research_v6_34_class1")


# v6.33 is not committed: its Simple is k96's FROZEN_2 (sha256 225de14b62f6c9f9...); the digest of its AST (ast.dump,
# Python 3.12) was computed once from that file and is embedded here
V633_AST_SHA256 = "702447b48ebe91a6ac3c83a1355c1bfb69b6c94d401628f7a0a3d7c179591ceb"


def _ast_sha(tree):
    import hashlib
    return hashlib.sha256(ast.dump(tree).encode()).hexdigest()


def test_every_v633_statement_is_in_v634_unchanged_and_v634_only_adds():
    assert _ast_sha(t633._Drop634().visit(ast.parse(SIMPLE))) == V633_AST_SHA256      # v6.33 = v6.34 minus v6.34
    assert _ast_sha(ast.parse(SIMPLE)) != V633_AST_SHA256
    old = t633._committed("agents/strategy/Strategy1_Research_Simple.py")            # v6.32, committed
    both = t632._Drop633().visit(t633._Drop634().visit(ast.parse(SIMPLE)))
    assert ast.dump(both) == ast.dump(ast.parse(old))
    for good, bad in (("why = V6_33_CANCEL_TOUCH_IDENTITY", "why = V617_CANCEL_BOUND"),
                      ("self._v6_32_est = tracker.estimate()", "self._v6_32_est = None"),
                      ("if v6_23_foreign and book_id in v6_23_foreign:", "if v6_23_foreign:"),
                      ("self._v6_33_request()", "self._v6_33_report()")):
        assert SIMPLE.count(good) == 1, good
        mutated = SIMPLE.replace(good, bad)
        assert _ast_sha(t633._Drop634().visit(ast.parse(mutated))) != V633_AST_SHA256, good


def test_the_frozen_modules_and_protocol_are_v633s():
    import hashlib
    for other in ("Strategy1_Research.py", "research_v617_touch_quote.py", "research_v6_29_touch_move.py",
                  "research_v630_identity_profile.py", "research_v631_touch_improve.py", "research_v6_32_fee_making.py",
                  "research_v6_32_state_fp.py", "research_v633_deep_layer.py", "research_v641_pace.py",
                  "research_v623_validator_063.py", "research_contract_guard.py", "research_direct_exit_ledger.py"):
        assert t633._committed(f"agents/strategy/{other}") == (STRATEGY / other).read_text(), other
    models = (ROOT / "taos" / "im" / "protocol" / "models.py").read_text()
    assert t633._committed("taos/im/protocol/models.py") == models
    digest = hashlib.sha256((STRATEGY / "research_v6_33_touch_identity.py").read_bytes()).hexdigest()
    assert digest.startswith(V633_MODULE_SHA256), digest


# ---- 6. the launcher -------------------------------------------------------------------------------------------------

def _block():
    start = LAUNCHER.index('if [[ "${V6_34_BUILD:-0}" == "1" ]]; then')
    return LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.34 class-1 entry PASS', start)]


def _patterns():
    """The block's two pattern lists: (Simple's, the module's)."""
    lists = re.findall(r"for _v634_pat in \\\n((?:      '[^']+' \\\n)*      '[^']+'); do", _block())
    assert len(lists) == 2, lists
    return [re.findall(r"'([^']+)'", x) for x in lists]


def test_the_v634_block_is_gated_preflighted_and_listed():
    assert "; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0\n" in LAUNCHER
    arm = next(x for x in LAUNCHER.splitlines() if x.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1"), arm
    assert LAUNCHER.count('if [[ "${V6_34_BUILD:-0}" == "1" ]]; then') == 1 and LAUNCHER.count("V6_34_BUILD=") == 2
    assert "      tests/test_research_v6_33.py \\\n      tests/test_research_v6_34.py \\\n" in LAUNCHER
    assert LAUNCHER.index('echo "[preflight] v6.33 touch identity PASS') < LAUNCHER.index("# v6.34.") < \
        LAUNCHER.index('echo "[preflight] v6.34 class-1 entry PASS') < \
        LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')
    head = LAUNCHER[LAUNCHER.index("# v6.34."):LAUNCHER.index('if [[ "${V6_34_BUILD:-0}" == "1" ]]; then')]
    for needle in ("STRUCTURAL", "OBSERVED", "k98", "0.75 / 0.25", "class map", "never a hard-coded range",
                   "min1 / min0", "S9", "S17-R", "S14", "D4", "ships 1 but may be 0", "=0 / =1", "PARAMS edit",
                   "research_v617_touch_quote=1", "research_v633_touch_identity", "user's decision"):
        assert needle in head, needle
    assert LAUNCHER.count("# v6.34.") == 1


def test_every_v634_preflight_pattern_is_in_the_file_it_checks():
    agent_pats, module_pats = _patterns()
    assert len(agent_pats) == 13 and len(module_pats) == 8
    for p in agent_pats:
        assert SIMPLE.count(p) == 1, p
    for p in module_pats:
        assert MODULE.count(p) == 1, p
    assert {SWITCH_LINE, HOOK_LINE, REPORT_LINE, "from research_v6_34_class1 import ("} <= set(agent_pats)
    assert '[[ -f "$AGENT_PATH/research_v6_34_class1.py" ]] || {' in _block()


def _run_block(params, agent_path=STRATEGY, build="1"):
    start = LAUNCHER.index("# v6.34.")
    stop = LAUNCHER.index('echo "[preflight] v6.34 class-1 entry PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "SCRIPT_DIR": str(ROOT), "V6_34_BUILD": build}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=120)


def test_the_v634_block_passes_the_shipped_params_and_refuses_a_build_it_cannot_ship():
    real = _run_block(t617._shipped())                                                # v6.35: ships off
    assert real.returncode == 0 and real.stdout.splitlines()[0] == "[preflight] v6.34 class-1 entry: off", real.stdout
    shipped = f" {t617._shipped()} ".replace(" research_v634_class1_entry=0 ", " research_v634_class1_entry=1 ").strip()
    out = _run_block(shipped)
    assert out.returncode == 0 and out.stderr == "", out.stderr
    lines = out.stdout.splitlines()
    assert lines[0] == "[preflight] v6.34 class-1 entry: on" and len(lines) == 2, out.stdout
    assert lines[1].startswith("[preflight] v6.34 class-1 entry PASS (on: "), out.stdout
    key = "research_v634_class1_entry"
    off = _run_block(f" {shipped} ".replace(f" {key}=1 ", f" {key}=0 "))
    assert off.returncode == 0 and off.stdout.splitlines()[0] == "[preflight] v6.34 class-1 entry: off"
    for bad in (f"{key}=2", "", f"{key}=true", f"{key}=11", f"{key}=01", f"x{key}=1", f"{key}=-1"):
        out = _run_block(f" {shipped} ".replace(f" {key}=1 ", f" {bad} "))
        assert out.returncode == 1 and f"without {key}=0 or 1" in out.stderr and "PASS" not in out.stdout, bad
    for twice in (f"{key}=1 {key}=1", f"{key}=1 {key}=0"):
        out = _run_block(f" {shipped} ".replace(f" {key}=1 ", f" {twice} "))
        assert out.returncode == 1 and f"{key} given 2 times" in out.stderr, twice
    out = _run_block(f" {shipped} ".replace(" research_v617_touch_quote=1 ", " research_v617_touch_quote=0 "))
    assert out.returncode == 1 and "without research_v617_touch_quote=1" in out.stderr
    out = _run_block(f" {shipped} ".replace(" research_v633_touch_identity=1 ", " "))
    assert out.returncode == 1 and "without research_v633_touch_identity=0 or 1" in out.stderr
    out = _run_block(f" {shipped} ".replace(" research_v633_touch_identity=1 ", " research_v633_touch_identity=0 "))
    assert out.returncode == 0                                                        # D4 off still governs: allowed
    out = _run_block("", build="0")
    assert out.returncode == 0 and out.stdout == "" and out.stderr == ""


def test_the_v634_block_refuses_a_tree_without_each_line_it_checks(tmp_path):
    agent_pats, module_pats = _patterns()
    cases = [(p, "Strategy1_Research_Simple.py") for p in agent_pats] + \
        [(p, "research_v6_34_class1.py") for p in module_pats]
    for i, (needle, name) in enumerate(cases):
        tmp = tmp_path / str(i)
        tmp.mkdir()
        for base in ("Strategy1_Research_Simple.py", "research_v6_34_class1.py"):
            shutil.copy(STRATEGY / base, tmp / base)
        path = tmp / name
        path.write_text(path.read_text().replace(needle, "pass  # removed", 1))
        out = _run_block(t617._shipped(), agent_path=tmp)
        assert out.returncode == 1 and needle in out.stderr and "PASS" not in out.stdout, (needle, out.stderr)
    bare = tmp_path / "no_module"
    bare.mkdir()
    shutil.copy(STRATEGY / "Strategy1_Research_Simple.py", bare / "Strategy1_Research_Simple.py")
    out = _run_block(t617._shipped(), agent_path=bare)
    assert out.returncode == 1 and "v6.34 the class-1 entry module is missing" in out.stderr
