"""v6.40: research_v640_* -- the three-class layout of SN79 0.6.5 (scratchpad k139; the arms of k137's replay engine
vm739, every one shipped off; k138's class-guard / S14 fixes, on; the upstream 0.6.5 axon keep-alive).

What is pinned here: the class map / depth unit / grids / foreign-set rules (research_v6_40_classes) on 0.6.4- and
0.6.5-shaped configurations; the arms' rules (C2J's prices and band, C2Q's break-even, the class-split tracker against
its base); the two GENERATED copies in simple_parts/v6_40_part.py against Simple's originals (copy == transform,
reverse == original); the part's methods on a stub agent (default = v6.39, each switch's teeth); the hook edits and
their exact undo (tests/_v640_undo.py: v6.39's joined Simple, launcher and miner byte for byte); the launcher's
"# v6.40." block (the shipped PARAMS and each arm's flip pass, every unshippable combination refused).  No test reads
the scratchpad, the network or a live tree."""
import ast
import hashlib
import json
import math
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
PARTS = STRATEGY / "simple_parts"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v6_40_classes as cl  # noqa: E402
import research_v633_deep_layer as dl  # noqa: E402
import research_v64_board as vb  # noqa: E402
import research_v617_touch_quote as tq  # noqa: E402
import research_v623_validator_063 as v623  # noqa: E402
import research_v6_32_fee_making as fm  # noqa: E402
import research_v6_38_keeper as kp  # noqa: E402
import _v640_gen as gen  # noqa: E402
import _v640_undo  # noqa: E402
from _harness import simple_text  # noqa: E402

SIMPLE = simple_text()
RAW_SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
PART = (PARTS / "v6_40_part.py").read_text()
MODULE = (STRATEGY / "research_v6_40_classes.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
MINER = (ROOT / "taos" / "common" / "neurons" / "miner.py").read_text()
# v6.39 (841a8fc, FROZEN_v6.39 c7a318ae): the files v6.40 edits, byte digests (git show 841a8fc:<path> | sha256sum)
V639_LAUNCHER_SHA256 = "6adc46ba72a024b7"          # prefix (the full digest is _v640_undo.LAUNCHER_BASE_SHA256)
V639_JOINED_SHA256 = "db56e75962e615af02d5169b7b702b9554e4a54cd5609dc04e0b13a8c7d6ac41"
SHIPPED = {"research_v640_traded_classes": "0", "research_v640_c2du": "0", "research_v640_c2d": "0",
           "research_v640_c2j": "0", "research_v640_c2q": "0", "research_v640_c2q_pf": "0", "research_v640_c1w": "0",
           "research_v640_guard_hold": "1", "research_v640_guard_init_price": "1", "research_v640_s14_class_key": "1",
           "research_v640_s14_flush_spec": "1"}


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _cfg(n_classes=3, *, unreadable=False, p2=313.0114, pd2=4, min2=0.2396):
    """A state configuration shaped as the validator sends it (model_dump: dicts), 0.6.5 (three) or 0.6.4 (two)."""
    def cls(name, lo, hi, pd, vd, mn, p0):
        return {"name": name, "books": list(range(lo, hi)), "priceDecimals": pd, "volumeDecimals": vd,
                "config": {"min_order_size": mn, "init_price": p0, "priceDecimals": pd, "volumeDecimals": vd}}
    if n_classes == 3:
        classes = [cls("simulation_0", 0, 64, 2, 4, 0.25, 300.0), cls("simulation_1", 64, 96, 2, 4, 2.5364, 29.57),
                   cls("simulation_2", 96, 128, pd2, 4, min2, p2)]
    else:
        classes = [cls("simulation_0", 0, 96, 2, 4, 0.25, 300.0), cls("simulation_1", 96, 128, 2, 4, 2.5364, 29.57)]
    if unreadable:
        classes = "not a list"
    return {"classes": classes, "priceDecimals": 2, "volumeDecimals": 4, "min_order_size": 0.25, "init_price": 300.0,
            "book_count": 128}


# ---- 1. the class map ----------------------------------------------------------------------------------------------------

def test_the_class_map_reads_each_class_from_the_configuration_in_document_order():
    t = cl.class_table(_cfg())
    assert t["status"] == cl.STATUS_OK and [r["c"] for r in t["classes"]] == [0, 1, 2]
    assert [r["name"] for r in t["classes"]] == ["simulation_0", "simulation_1", "simulation_2"]
    assert [len(r["books"]) for r in t["classes"]] == [64, 32, 32]
    assert [r["tick"] for r in t["classes"]] == [0.01, 0.01, 0.0001]
    assert [r["min_order"] for r in t["classes"]] == [0.25, 2.5364, 0.2396]
    assert t["book_class"][63] == 0 and t["book_class"][64] == 1 and t["book_class"][127] == 2
    assert cl.class_table({"priceDecimals": 2})["status"] == cl.STATUS_SINGLE
    assert cl.class_table(_cfg(unreadable=True))["status"] == cl.STATUS_UNREADABLE
    bad = _cfg()
    bad["classes"][1]["books"] = list(range(60, 96))                         # two classes share a book
    assert cl.class_table(bad)["status"] == cl.STATUS_UNREADABLE


def test_the_depth_unit_is_k137s_rule_and_its_0_6_5_values():
    t = cl.class_table(_cfg())
    assert [r["du"] for r in t["classes"]] == [0.01, 0.01, 0.0104]          # k137 vm738 (0.6.5 release layout)
    assert [r["du"] for r in cl.class_table(_cfg(2))["classes"]] == [0.01, 0.01]
    for _ in range(500):
        t0 = random.choice([0.01, 0.001]); p0 = random.uniform(1, 1000); pc = random.uniform(1, 1000)
        dc = random.choice([2, 3, 4, 5]); tc = round(10.0 ** -dc, dc)
        n = max(1, int(round(t0 * pc / p0 / tc)))
        assert cl.depth_unit(t0, p0, tc, pc, dc) == round(n * tc, dc)
    assert cl.depth_unit(0.01, None, 0.0001, 313.0, 4) == 0.0001             # no initial price: the venue tick


def test_the_traded_classes_switch_reads_numbers_and_strings_and_refuses_the_rest():
    assert cl.parse_traded("0") == cl.parse_traded(0) == cl.parse_traded(0.0) == frozenset({0})
    assert cl.parse_traded("0+2") == frozenset({0, 2}) and cl.parse_traded(" 0 + 1 + 2 ") == frozenset({0, 1, 2})
    for bad in ("2", "", "x", "0+x", True, 0.5, "0.5"):
        assert cl.parse_traded(bad) is None, bad
    assert cl.parse_traded(None) == frozenset({0})


def test_default_no_grid_and_the_foreign_set_is_v623s_exactly_on_today_s_layouts():
    for cfg in (_cfg(), _cfg(2)):
        t = cl.class_table(cfg)
        assert cl.book_grids(t, frozenset({0}), True) == {} and cl.book_grids(t, frozenset({0}), False) == {}
        foreign, layout = v623.class_layout(cfg)
        extra = cl.init_price_foreign(cfg, t)
        assert extra <= foreign                                               # B4(3) adds nothing today
        assert cl.merge_foreign(foreign, t, init_price=extra, traded=frozenset({0})) == foreign
    foreign, _ = v623.class_layout(_cfg())
    assert foreign == frozenset(range(64, 128))


def test_traded_class_two_gets_its_own_grid_and_leaves_the_foreign_set():
    cfg = _cfg()
    t = cl.class_table(cfg)
    g = cl.book_grids(t, frozenset({0, 2}), True)
    assert sorted(g) == list(range(96, 128))
    assert g[100] == cl.Grid(2, "simulation_2", 0.0001, 4, 4, 0.2396, 0.0104, 0.0104, 1.0)
    assert cl.book_grids(t, frozenset({0, 2}), False)[100].du == 0.0001     # c2du off: the venue tick
    foreign, _ = v623.class_layout(cfg)
    assert cl.merge_foreign(foreign, t, traded=frozenset({0, 2})) == frozenset(range(64, 96))
    assert cl.traded_books(t, frozenset({0, 2})) == frozenset(range(96, 128))


def test_b4_3_a_class_with_the_home_grid_but_another_initial_price_is_foreign():
    cfg = _cfg(p2=313.0114, pd2=2, min2=0.25)                               # grid and minimum as the home class
    foreign, _ = v623.class_layout(cfg)
    assert not (foreign & frozenset(range(96, 128)))                         # v6.23 alone: not foreign
    assert cl.init_price_foreign(cfg) == frozenset(range(64, 128))
    cfg2 = _cfg(p2=300.0, pd2=2, min2=0.25)
    assert not (cl.init_price_foreign(cfg2) & frozenset(range(96, 128)))


def test_b4_6_the_flush_follows_the_validator_release():
    assert cl.flush_ns(64) == 60_000_000_000 == fm.CAPTURE_FLUSH_NS and cl.flush_ns(None) == 60_000_000_000
    assert cl.flush_ns(65) == cl.flush_ns(66) == 300_000_000_000


def test_b4_5_the_class_digest_follows_the_class_list():
    a, b = cl.class_digest(_cfg()), cl.class_digest(_cfg(2))
    assert a and b and a != b and a == cl.class_digest(_cfg())
    assert cl.class_digest(_cfg(p2=313.0)) != a and cl.class_digest({"x": 1}) == ""
    assert cl.layout_key(_cfg()) == cl.layout_key(_cfg()) != cl.layout_key(_cfg(2))


# ---- 2. the arms' rules ------------------------------------------------------------------------------------------------

def test_the_depth_unit_prices_are_v633_and_v64s_exactly_when_the_unit_is_the_tick():
    rnd = random.Random(7)
    for _ in range(3000):
        mid = round(rnd.uniform(50, 400), 2); spread = rnd.randint(1, 60) * 0.01
        bid, ask = round(mid - spread / 2, 2), round(mid + spread / 2, 2)
        d = rnd.uniform(0, 80); side = rnd.choice(["buy", "sell"])
        assert cl.deep_price_du(mid, d, side, bid=bid, ask=ask, du=0.01, tick=0.01, decimals=2) == \
            dl.deep_price(mid, d, side, bid=bid, ask=ask, tick=0.01, decimals=2)
        assert cl.vacuum_price_du(mid, d, side, bid=bid, ask=ask, du=0.01, tick=0.01, decimals=2) == \
            vb.vacuum_price(mid, d, side, bid=bid, ask=ask, tick=0.01, decimals=2)
    # class 2: depth in DU, at least one VENUE tick behind the touch (vm738: round(c - D x DU), min with bb - TICK)
    assert cl.deep_price_du(313.0, 10, "buy", bid=312.9, ask=313.1, du=0.0104, tick=0.0001, decimals=4) == 312.896
    assert cl.deep_price_du(313.0, 5, "buy", bid=312.96, ask=313.04, du=0.0104, tick=0.0001, decimals=4) == 312.948
    assert cl.deep_price_du(313.0, 1, "buy", bid=312.995, ask=313.005, du=0.0104, tick=0.0001, decimals=4) == 312.9896
    assert cl.deep_price_du(313.0, 0.3, "buy", bid=312.995, ask=313.005, du=0.0104, tick=0.0001, decimals=4) == 312.9949
    assert cl.deep_price_du(313.0, 0.3, "sell", bid=312.995, ask=313.005, du=0.0104, tick=0.0001, decimals=4) == 313.0051


def test_c2j_prices_and_band():
    assert cl.c2j_rest_ticks(1, tq.REST_TICKS) == 2 and cl.c2j_rest_ticks(2, tq.REST_TICKS) == 3
    assert cl.c2j_behind_price("buy", bid=313.0, ask=313.25, du=0.0104, decimals=4) == 312.9896
    assert cl.c2j_behind_price("sell", bid=313.0, ask=313.25, du=0.0104, decimals=4) == 313.2604
    assert cl.c2j_behind_price("buy", bid=313.0, ask=313.0, du=0.0104, decimals=4) is None       # crossed / locked
    # the band in DU, through v6.17's verdict: c2j=1 re-placed past 1.5 DU, c2j=2 past 2.5 DU
    v = lambda p, rest: tq.verdict("buy", p, toward="buy", bid=313.0, ask=313.3, fundamental=320.0, tick=0.0104,
                                   rest_ticks=rest)
    assert v(313.0 - 1.4 * 0.0104, 2) is None and v(313.0 - 1.6 * 0.0104, 2) == tq.CANCEL_LEFT
    assert v(313.0 - 2.4 * 0.0104, 3) is None and v(313.0 - 2.6 * 0.0104, 3) == tq.CANCEL_LEFT


def _vm739_rule(R, C, R0, C0, R2, C2, b0, b2, pf):
    """vm739 _c2q_update's decision, transcribed (k137/mk_vm739.py edit 3)."""
    q = min(1.0, max(0.0, R / C)) if C > 0 else 0.0
    if C2 <= 0 or C0 <= 0 or not (math.isfinite(b0) and b0 > 0 and math.isfinite(b2)):
        return True
    g = pf * b2 / b0; x = C2 / C0; thr = (1.0 - g) / (1.0 + g * x); q2 = R2 / C2
    return q2 > thr * q


def test_c2q_decision_is_vm739s_rule():
    rnd = random.Random(11)
    for _ in range(20000):
        R0, C0 = rnd.uniform(-50, 100), rnd.choice([0.0, rnd.uniform(0, 200)])
        R2, C2 = rnd.uniform(-50, 50), rnd.choice([0.0, rnd.uniform(0, 100)])
        b0 = rnd.choice([float("nan"), 0.0, rnd.uniform(0, 1)]); b2 = rnd.choice([float("nan"), rnd.uniform(0, 1)])
        pf = rnd.uniform(0.1, 3)
        d = cl.c2q_decision(R0 + R2, C0 + C2, R0, C0, R2, C2, b0, b2, pf)
        assert d["armed"] == _vm739_rule(R0 + R2, C0 + C2, R0, C0, R2, C2, b0, b2, pf)
    assert cl.c2q_decision(1, 2, 1, 2, 0, 0, 0.5, 0.5, 1.0)["probe"] is True     # no class-2 evidence: armed probe
    assert math.isnan(cl.making_beta({}, {}, [1, 2]))
    assert cl.making_beta({1: 10.0, 2: 4.0}, {1: 6.0, 2: 4.0}, [1, 2]) == (2 * 6 + 2 * 4) / 24.0


def _stream(rnd, n_books=6, states=1500):
    """(ts, {book: prints}) for a tracker: our maker fills (uid 7) among background prints."""
    out = []
    px = {b: 300.0 for b in range(n_books)}
    for k in range(states):
        ts = (k + 1) * 1_000_000_000
        books = {}
        for b in range(n_books):
            pr = []
            for i in range(rnd.randint(0, 4)):
                px[b] = round(px[b] + rnd.choice([-0.01, 0, 0.01]), 2)
                mine = rnd.random() < 0.15
                pr.append({"y": "t", "p": px[b], "q": round(rnd.uniform(0.25, 2), 4), "s": rnd.choice([0, 1]),
                           "Ma": 7 if mine else -1, "Ta": -1, "t": ts - 1000 + i})
            books[b] = pr
        out.append((ts, books))
    return out


def test_the_class_tracker_splits_k3qs_sums_and_keeps_every_base_result():
    rnd = random.Random(3)
    s = _stream(rnd)
    base = fm.RealizedTracker()
    k3q = kp.CaptureTracker(weight=0.005)
    split = cl.ClassCaptureTracker(weight=0.005)
    split.class_of = {b: (0 if b < 4 else 2) for b in range(6)}
    rates = {b: 0.0002 * (b + 1) for b in range(6)}
    k3q.rates = split.rates = rates
    for ts, books in s:
        for t in (base, k3q, split):
            t.begin(ts); t.set_active(True, ts)
            for b, pr in books.items():
                if pr:
                    t.observe(b, pr, 7, 0.25, ts)
            t.release(ts)
        assert base.estimate() == k3q.estimate() == split.estimate()
        assert k3q.sums() == split.sums()
        r0, c0 = split.class_sums(0); r2, c2 = split.class_sums(2)
        R, C = split.sums()
        assert math.isclose(r0 + r2, R, abs_tol=1e-9) and math.isclose(c0 + c2, C, abs_tol=1e-9)
    assert base.counts == split.counts and split.counts["counted"] > 50


# ---- 3. the generated copies ---------------------------------------------------------------------------------------------

def _method(text, cls_name, name):
    tree = ast.parse(text)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
    return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)


def test_the_generated_copies_are_the_transforms_of_simples_live_originals_and_reverse_to_them():
    deep0 = _method(SIMPLE, "Strategy1_Research_Simple", "_v633_book")
    touch0 = _method(SIMPLE, "Strategy1_Research_Simple", "_v617_touch")
    deep1 = _method(PART, "V640Part", "_v6_40_deep_core")
    touch1 = _method(PART, "V640Part", "_v6_40_touch_core")
    assert ast.dump(deep1) == ast.dump(gen.deep_core(deep0))
    assert ast.dump(touch1) == ast.dump(gen.touch_core(touch0))
    assert ast.dump(gen.deep_back(deep1, original=deep0)) == ast.dump(deep0)
    assert ast.dump(gen.touch_back(touch1, touch0)) == ast.dump(touch0)
    # the substitutions, counted: every tick_size read but the touch margins -> du; the margins keep the venue tick
    names = [n.id for n in ast.walk(deep1) if isinstance(n, ast.Name)]
    assert "v633_deep_price" not in names and "v64_vacuum_price" not in names
    calls = [n for n in ast.walk(deep1) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id in ("v6_40_deep_price_du", "v6_40_vacuum_price_du")]
    assert len(calls) == 5 and all({k.arg for k in c.keywords} >= {"du", "tick"} for c in calls)
    assert all(ast.unparse(k.value) == "tick_size" for c in calls for k in c.keywords if k.arg == "tick")
    loads = [n for n in ast.walk(deep1) if isinstance(n, ast.Name) and n.id == "tick_size" and isinstance(n.ctx, ast.Load)]
    assert len(loads) == 5 + 1                                                # the 5 margins + the du lookup
    assert "research_v631_touch_improve" not in ast.unparse(touch1) and "_v6_40_touch_price" in ast.unparse(touch1)
    for fn in (deep1, touch1):                                                # UNITS: the class's scale and minimum
        src = ast.unparse(fn)
        assert src.count("v62_lot_quantity(q_ * scale, vdec)") == 1 and src.count("q + 1e-12 < vmin") == 1
        assert src.count("v691_quantity_within_free(q, free, vmin, vdec)") == 1 and "< min_order" not in src


def test_the_part_is_what_the_build_writes_and_imports_only_what_simple_imports():
    tree = ast.parse(PART)
    simple_imp = gen.simple_imports(ast.parse(RAW_SIMPLE))
    gen_start = PART.index("# ---- GENERATED IMPORTS")
    gen_lines = PART[gen_start:PART.index("\n\n\nclass V640Part:")].splitlines()[1:]
    assert gen_lines
    for ln in gen_lines:
        st = ast.parse(ln).body[0]
        a = st.names[0]
        assert simple_imp.get(a.asname or a.name) == (st.module, a.name), ln
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "V640Part")
    assert not cls.bases
    names = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
    assert all(n.startswith("_v6_40_") or n in ("_research_sanitize_maker_instructions", "onOrderRejected")
               for n in names) and len(set(names)) == len(names)              # + the two overrides of the frozen base
    joined_names = {n.name for n in ast.walk(ast.parse(SIMPLE)) if isinstance(n, ast.FunctionDef)}
    assert not (set(names) & joined_names)                                   # new methods only


# ---- 4. the part on a stub agent ---------------------------------------------------------------------------------------

def _part_module():
    """simple_parts.v6_40_part -- its copies import taos.im.protocol.models (bittensor); where this interpreter cannot,
    the four enums they name are stubbed for the import (no test here reaches a real order constructor)."""
    import importlib
    try:
        return importlib.import_module("simple_parts.v6_40_part")
    except ImportError:
        import types
        for name in ("taos", "taos.im", "taos.im.protocol"):
            sys.modules.setdefault(name, types.ModuleType(name))
        models = types.ModuleType("taos.im.protocol.models")
        for enum in ("LoanSettlementOption", "OrderDirection", "STP", "TimeInForce"):
            setattr(models, enum, SimpleNamespace(NONE=0, BUY=0, SELL=1, CANCEL_BOTH=3, GTT=1))
        sys.modules["taos.im.protocol.models"] = models
        return importlib.import_module("simple_parts.v6_40_part")


def _agent(**cfg):
    mod = _part_module()

    class Agent(mod.V640Part):
        def __init__(self, config):
            self.config = SimpleNamespace(**config)
            self._tick = 0
            self.rows = []
            self.research_v623_class_guard = True
            self.research_v617_touch_quote = True

        @staticmethod
        def _as_bool(v):
            return str(v).strip().lower() in ("1", "true", "yes", "on")

        def _emit(self, kind, **row):
            self.rows.append((kind, row))
    a = Agent(cfg)
    a._v6_40_init()
    return a


def _state(cfg, version=65):
    return SimpleNamespace(config=cfg, version=version, timestamp=10 ** 12, books={})


def test_default_the_observe_returns_v623s_foreign_set_and_no_grid():
    a = _agent()
    for cfg in (_cfg(), _cfg(2)):
        foreign, layout = v623.class_layout(cfg)
        assert a._v6_40_observe(_state(cfg), foreign, layout, True) == (foreign, layout)
        assert a._v6_40_grids == {} and a._v6_40_vgrids == {} and a._v6_40_decs == {}
    assert a.research_v640_traded_classes == "0" and not a._v6_40_arms_on()
    for k in ("research_v640_c2du", "research_v640_c2d", "research_v640_c2j", "research_v640_c2q", "research_v640_c1w"):
        assert getattr(a, k) == 0, k
    assert a.research_v640_c2q_pf is None
    for k in ("research_v640_guard_hold", "research_v640_guard_init_price", "research_v640_s14_class_key",
              "research_v640_s14_flush_spec"):
        assert getattr(a, k) is True, k


def test_b4_2_an_unreadable_class_list_keeps_the_last_readable_foreign_set_and_off_it_falls_open():
    a = _agent()
    foreign, layout = v623.class_layout(_cfg())
    a._v6_40_observe(_state(_cfg()), foreign, layout, True)
    bad = _cfg(unreadable=True)
    f2, l2 = v623.class_layout(bad)
    assert l2 == v623.LAYOUT_UNREADABLE and f2 == frozenset()
    assert a._v6_40_observe(_state(bad), f2, l2, True)[0] == frozenset(range(64, 128))
    assert a._v6_40_held_foreign() == frozenset(range(64, 128))
    off = _agent(research_v640_guard_hold="0")
    off._v6_40_observe(_state(_cfg()), foreign, layout, True)
    assert off._v6_40_observe(_state(bad), f2, l2, True)[0] == frozenset()     # v6.39: falls open
    fresh = _agent()
    assert fresh._v6_40_held_foreign() == frozenset()                          # nothing read yet: v6.22


def test_traded_zero_plus_two_with_c2du_carries_class_twos_grid():
    a = _agent(research_v640_traded_classes="0,2", research_v640_c2du="1")
    foreign, layout = v623.class_layout(_cfg())
    f, _l = a._v6_40_observe(_state(_cfg()), foreign, layout, True)
    assert f == frozenset(range(64, 96)) and sorted(a._v6_40_grids) == list(range(96, 128))
    assert a._v6_40_vgrids[100] == (0.0001, 4, 0.2396) and a._v6_40_decs[100] == 4
    assert a._v6_40_du(100, 0.0001) == 0.0104 and a._v6_40_du(5, 0.01) == 0.01


def test_c1w_puts_every_class1_book_of_v634s_map_in_v636s_pre_step_plan_with_an_unbounded_radius():
    a = _agent(research_v640_c1w="1")
    a.research_v634_class1_entry = True
    a.research_v636_step_withdrawal = True
    a._v6_34_c1 = frozenset({64, 65})
    a._v6_36_l1 = {64: (29.5, 29.6)}
    a._v6_36_req = {"pre_step": True, "books": {65: (29.0, 0.001, "sell"), 3: (300.0, 0.001, None)}}
    a._v6_40_request(_state(_cfg()))
    plan = a._v6_36_req["books"]
    r = _part_module().V6_40_C1W_RADIUS
    assert plan[64] == (29.55, r, None) and plan[65] == (29.0, r, "sell") and plan[3] == (300.0, 0.001, None)
    import research_v6_36_step_withdrawal as sw
    for p in (1e-4, 1.0, 29.55, 1e3, 1e6):                                     # every price of the book is withdrawn
        assert sw.withdrawal("buy", p, mid=29.55, radius=r) == sw.PULL and sw.withdrawal("sell", p, mid=1.0, radius=r) == sw.PULL
    a._v6_36_req = {"pre_step": False, "books": {}}
    a._v6_40_request(_state(_cfg()))
    assert a._v6_36_req["books"] == {}
    off = _agent()
    off.research_v634_class1_entry = off.research_v636_step_withdrawal = True
    off._v6_34_c1, off._v6_36_l1 = frozenset({64}), {}
    off._v6_36_req = {"pre_step": True, "books": {}}
    off._v6_40_request(_state(_cfg()))
    assert off._v6_36_req["books"] == {}


def test_c2j_price_joins_or_rests_one_unit_behind_with_the_edge_on_its_own_price():
    a = _agent(research_v640_traded_classes="0,2", research_v640_c2du="1", research_v640_c2j="2")
    foreign, layout = v623.class_layout(_cfg())
    a._v6_40_observe(_state(_cfg()), foreign, layout, True)
    a.research_post_only_safety_ticks = 2
    assert a._v6_40_touch_price("buy", 313.0, 313.25, 314.0, 0.0001, 100) == 312.9896
    assert a._v6_40_touch_price("buy", 313.0, 313.25, 313.0, 0.0001, 100) is None   # its edge short of the hurdle
    assert a._v6_40_touch_units(100, 0.0001) == (0.0104, 3)
    a.research_v640_c2j = 1
    a.research_v631_touch_improve = True
    a._v6_31_price = lambda *x: pytest.fail("C2J never improves")
    assert a._v6_40_touch_price("buy", 313.0, 313.25, 314.0, 0.0001, 100) == \
        tq.wanted_price("buy", bid=313.0, ask=313.25, fundamental=314.0, tick=0.0001, safety_ticks=2)
    assert a._v6_40_touch_units(100, 0.0001) == (0.0104, 2)
    a.research_v640_c2j = 0                                                    # C2P: v6.17's join + v6.31's improve
    a._v6_31_price = lambda book, side, px, fp, tick: round(px + tick, 4)
    assert a._v6_40_touch_price("buy", 313.0, 313.25, 314.0, 0.0001, 100) == 313.0001
    assert a._v6_40_touch_units(100, 0.0001) == (0.0001, tq.REST_TICKS)


def test_the_lift_puts_each_book_on_its_own_decimals():
    a = _agent(research_v640_traded_classes="0+2")
    foreign, layout = v623.class_layout(_cfg())
    a._v6_40_observe(_state(_cfg()), foreign, layout, True)
    import research_v611_wire as w
    p2, p0 = 313.0113, 228.13
    ins = [{"type": "PLACE_ORDER_LIMIT", "bookId": 100, "price": p2, "direction": 0},
           {"type": "PLACE_ORDER_LIMIT", "bookId": 5, "price": p0, "direction": 1}]
    ref = [dict(x) for x in ins]
    a._v6_40_lift(ins, 2)
    w.lift_instruction_prices([ref[0]], 4)
    w.lift_instruction_prices([ref[1]], 2)
    assert ins == ref
    assert w.venue_placed_price(ins[0]["price"], 4) == p2 and w.venue_placed_price(ins[1]["price"], 2) == p0


def test_c2d_cancels_a_resting_class2_touch_quote_and_places_none():
    a = _agent(research_v640_traded_classes="0,2", research_v640_c2du="1", research_v640_c2d="1")
    foreign, layout = v623.class_layout(_cfg())
    a._v6_40_observe(_state(_cfg()), foreign, layout, True)
    seen = []
    a._v617_cancel = lambda response, book_id, doomed, req, budget: seen.append((book_id, [w for _r, w in doomed]))
    a._v617_book = lambda *x, **k: pytest.fail("C2D must not reach v6.17's quote")
    rows = [SimpleNamespace(order_id=11, side=0, price=313.0)]
    assert a._v6_40_touch_book(None, 100, None, 313.0, 313.2, 0.0, rows, set(), {}, budget=5) == 0
    assert seen == [(100, [cl.CANCEL_C2D])]
    calls = []
    a._v6_40_touch_core = lambda *x, **k: calls.append(x[1]) or 0
    a._v6_40_touch_book(None, 70, None, 29.5, 29.6, 0.0, [], set(), {}, budget=5)   # not class 2: the core (no C2D)
    a.research_v640_c2d = 0
    a._v6_40_touch_book(None, 100, None, 313.0, 313.2, 0.0, rows, set(), {}, budget=5)
    assert calls == [70, 100] and seen == [(100, [cl.CANCEL_C2D])]


def test_c2q_disarms_below_the_break_even_and_decides_once_per_block():
    a = _agent(research_v640_traded_classes="0,2", research_v640_c2du="1", research_v640_c2q="1",
               research_v640_c2q_pf="1.5")
    foreign, layout = v623.class_layout(_cfg())
    a._v6_40_observe(_state(_cfg()), foreign, layout, True)
    assert isinstance(a._v6_32_tracker, cl.ClassCaptureTracker)

    class T(cl.ClassCaptureTracker):
        def __init__(self):
            super().__init__()
            self.v = {0: (10.0, 20.0), 2: (-5.0, 10.0)}

        def sums(self):
            return (5.0, 30.0)

        def class_sums(self, c):
            return self.v.get(c, (0.0, 0.0))
    a._v6_32_tracker = T()
    a._v6_40_gauges = SimpleNamespace(snapshot=lambda: None, status=lambda: {})   # no page: the configured fallback
    a.uid = 7
    a._v62_mirror = SimpleNamespace(buy_sums={7: {1: 10.0, 100: 4.0}}, sell_sums={7: {1: 10.0, 100: 4.0}})
    a._direct_current_state_timestamp_ns = 30 * 10 ** 9 * 1000
    a._tick = 1
    assert a._v6_40_c2q_off(100) is True and a._v6_40_c2q_off(5) is False     # q2 -0.5 < thr x q
    a._v6_32_tracker.v[2] = (8.0, 10.0)
    a._tick = 2
    assert a._v6_40_c2q_off(100) is True                                       # same block: the decision stands
    a._direct_current_state_timestamp_ns += 30 * 10 ** 9
    a._tick = 3
    assert a._v6_40_c2q_off(100) is False                                      # the next block re-decides: armed
    off = _agent(research_v640_traded_classes="0+2")
    off._v6_40_observe(_state(_cfg()), foreign, layout, True)
    assert off._v6_40_c2q_off(100) is False


def test_the_state_row_reports_at_start_and_on_a_layout_change():
    a = _agent()
    foreign, layout = v623.class_layout(_cfg())
    a._v6_40_observe(_state(_cfg()), foreign, layout, True)
    a._v6_40_request(_state(_cfg()))
    a._tick = 1
    a._v6_40_request(_state(_cfg()))
    assert [r[1]["reason"] for r in a.rows] == ["start"]
    f2, l2 = v623.class_layout(_cfg(2))
    a._v6_40_observe(_state(_cfg(2)), f2, l2, True)
    a._v6_40_request(_state(_cfg(2)))
    assert [r[1]["reason"] for r in a.rows] == ["start", "layout"] and a.rows[-1][1]["errors"] == 0


def test_g2_the_state_row_carries_the_class2_monitor_the_c2q_verdict_and_the_rejects_by_class_and_reason():
    assert _v640_undo.FLIPS["G2"] == {"research_v640_traded_classes": "0,2", "research_v640_c2du": "1",
                                      "research_v640_c2d": "1", "research_v640_c2q": "1", "research_v640_c2q_pf": None}
    assert FLIPS["G2"] == dict(_v640_undo.FLIPS["G2"], research_v640_c2q_pf="1.86")   # the block test's G2 is that flip
    a = _agent(research_v640_traded_classes="0,2", research_v640_c2du="1", research_v640_c2d="1", research_v640_c2q="1",
               research_v640_c2q_pf="1.5")
    foreign, layout = v623.class_layout(_cfg())
    a._v6_40_observe(_state(_cfg()), foreign, layout, True)
    orders = {100: [SimpleNamespace(quantity=0.5, price=313.0), SimpleNamespace(quantity=0.25, price=314.0)]}
    a._direct_account_orders = lambda b: list(orders.get(b, ()))
    a._direct_signed_inventory = lambda b: -0.75 if b == 100 else 0.0
    a._v6_36_l1 = {100: (312.0, 314.0)}
    a.uid = 7
    a._v62_mirror = SimpleNamespace(buy_sums={7: {1: 10.0, 100: 4.0}}, sell_sums={7: {1: 10.0, 100: 4.0}})
    a._v6_40_request(_state(_cfg()))
    m = a.rows[-1][1]["monitor"]
    assert a.rows[-1][1]["reason"] == "start" and m["c2q"] == "none" and m["c2_books"] == 32
    assert m["c2_open_orders"] == 2 and m["c2_open_notional"] == 235.0 and m["c2_position"] == 0.75
    assert m["c2_position_notional"] == 234.75 and m["c2_making_notional"] == 8.0 and m["c0_making_notional"] == 20.0

    class Tr(cl.ClassCaptureTracker):
        def __init__(self):
            super().__init__()
            self.v = {0: (10.0, 20.0), 2: (-5.0, 10.0)}

        def sums(self):
            return (5.0, 30.0)

        def class_sums(self, c):
            return self.v.get(c, (0.0, 0.0))
    a._v6_32_tracker = Tr()
    a._v6_40_gauges = SimpleNamespace(snapshot=lambda: None, status=lambda: {})   # no page: the configured fallback
    a._direct_current_state_timestamp_ns = 30 * 10 ** 9 * 1000
    a._tick = 1
    assert a._v6_40_c2q_off(100) is True                                       # disarmed
    a._tick = 2
    a._v6_40_request(_state(_cfg()))                                           # the next request: the "c2q" row
    r = a.rows[-1][1]
    m = r["monitor"]
    assert r["reason"] == "c2q" and m["c2q"] == "disarmed" and m["q2"] == -0.5 and abs(m["q"] - 5.0 / 30.0) < 1e-12
    assert m["pf"] == 1.5 and m["pf_source"].startswith("fallback_") and abs(m["break_even"] - m["thr"] * m["q"]) < 1e-12
    assert m["q2"] < m["break_even"] and m["block"] == 1000
    n = len(a.rows)
    a._tick = 3
    a._v6_40_request(_state(_cfg()))                                           # same verdict: no row
    assert len(a.rows) == n
    a._v6_32_tracker.v[2] = (8.0, 10.0)
    a._direct_current_state_timestamp_ns += 30 * 10 ** 9
    a._tick = 4
    assert a._v6_40_c2q_off(100) is False                                      # the next block: armed
    a._tick = 5
    a._v6_40_request(_state(_cfg()))
    assert a.rows[-1][1]["reason"] == "c2q" and a.rows[-1][1]["monitor"]["c2q"] == "armed"
    assert a._v6_40_counts["c2q_verdict_changes"] == 1 and a._v6_40_errors == 0
    off = _agent()
    off._v6_40_request(_state(_cfg()))
    assert off.rows[-1][1]["monitor"] == {"rejects": {}, "c2_books": 0, "c2_open_orders": 0, "c2_open_notional": 0.0,
                                          "c2_position": 0.0, "c2_position_notional": 0.0, "c2_making_notional": 0.0,
                                          "c0_making_notional": 0.0, "c2q": "off"}
    mod = _part_module()

    class Base:
        seen = 0

        def onOrderRejected(self, event):
            self.seen += 1

    class Rej(mod.V640Part, Base):
        pass
    j = Rej()
    j._v6_40_rejects, j._v6_40_errors = {}, 0
    j._v6_40_table = cl.class_table(_cfg())
    for ev in (SimpleNamespace(message="contract_violation", bookId=100), SimpleNamespace(message="", bookId=5),
               SimpleNamespace(message="CONTRACT_VIOLATION", bookId="100"), SimpleNamespace(message="X", bookId="?")):
        j.onOrderRejected(ev)
    assert j.seen == 4 and j._v6_40_errors == 0                                # the base first, every time
    assert j._v6_40_rejects == {"c2:CONTRACT_VIOLATION": 2, "c0:UNKNOWN": 1, "c-1:X": 1}


# ---- 5. the hooks and the undo -------------------------------------------------------------------------------------------

def test_every_hook_edit_is_in_the_joined_text_once_and_the_undo_is_v639s_text_byte_for_byte():
    assert len(_v640_undo.JOINED_EDITS) == 19
    for old, new in _v640_undo.JOINED_EDITS:
        assert SIMPLE.count(new) == 1, new[:80]
    back = _v640_undo.joined_v639(SIMPLE)
    assert _sha(back) == V639_JOINED_SHA256 == _v640_undo.BASE_JOINED_SHA256
    assert _sha(SIMPLE) == _v640_undo.JOINED_SHA256
    assert _v640_undo.joined_v639(back) is back                               # idempotent: v6.39's text as it is
    with pytest.raises(AssertionError):
        _v640_undo.joined_v639(SIMPLE.replace(_v640_undo.JOINED_EDITS[3][1], "", 1))   # an edit missing
    manifest = json.loads((PARTS / "MANIFEST.json").read_text())
    assert manifest["pre_split_sha256"] == _sha(SIMPLE) and manifest["v640_base_sha256"] == V639_JOINED_SHA256
    assert manifest["simple_edits"][:2] == [list(e) for e in _v640_undo.EDITS["split_edits"]]


def test_the_launcher_and_the_miner_with_v640_undone_are_v639s():
    assert _v640_undo.LAUNCHER_BASE_SHA256.startswith(V639_LAUNCHER_SHA256)
    assert _sha(_v640_undo.launcher_v639(LAUNCHER)) == _v640_undo.LAUNCHER_BASE_SHA256
    assert _v640_undo.launcher_v639(_v640_undo.launcher_v639(LAUNCHER)) == _v640_undo.launcher_v639(LAUNCHER)
    m39 = _v640_undo.source_v639("taos/common/neurons/miner.py", MINER)
    assert m39 != MINER and "AXON_KEEP_ALIVE_S" not in m39 and "keep_alive" not in m39
    assert _v640_undo.source_v639("research_v617_touch_quote.py", "x") == "x"


def test_v640s_path_set_is_its_new_files_and_the_files_it_edits():
    assert all((ROOT / p).is_file() for p in _v640_undo.V640_PATHS | _v640_undo.V640_TESTS)
    edited = {"agents/strategy/" + e["file"] for e in _v640_undo.EDITS["edits"]}
    assert edited <= _v640_undo.V640_MODULES
    assert _v640_undo.V640_MODULES - edited == {"agents/strategy/simple_parts/MANIFEST.json", "taos/common/neurons/miner.py"}


def test_the_miner_keeps_an_idle_validator_connection_120_s_right_after_the_axon_is_built():
    tree = ast.parse(MINER)
    assert any(isinstance(st, ast.Assign) and ast.unparse(st) == "AXON_KEEP_ALIVE_S = 120" for st in tree.body)
    init = next(n for c in tree.body if isinstance(c, ast.ClassDef) and c.name == "BaseMinerNeuron"
                for n in c.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    src = [ast.unparse(st) for st in init.body]
    i = next(k for k, s in enumerate(src) if s.startswith("self.axon = bt.Axon("))
    assert src[i + 1] == "self.axon.fast_config.timeout_keep_alive = AXON_KEEP_ALIVE_S"


def test_simple_takes_the_part_as_its_first_base_and_the_joined_view_stays_the_single_file():
    assert RAW_SIMPLE.count("from simple_parts.v6_40_part import V640Part\n") == 1
    assert "class Strategy1_Research_Simple(V640Part, V636Part, V632Part, V630Part, V631Part, V629Part, " \
           "Strategy1_Research):" in RAW_SIMPLE
    assert "class Strategy1_Research_Simple(Strategy1_Research):" in SIMPLE      # the single file (no part bases)
    assert "import V640Part" not in SIMPLE and ", V636Part" not in SIMPLE


# ---- 6. the launcher block -------------------------------------------------------------------------------------------------

def _params(text=None):
    text = LAUNCHER if text is None else text
    start = text.index('PARAMS="')
    return text[start + len('PARAMS="'):text.index('"', start + len('PARAMS="'))].replace("\\\n", " ")


def _shipped_params():
    """The launcher's PARAMS with its documented flip (if any) undone: the base every flip / refusal case is built on."""
    return _params(_v640_undo._unflip(LAUNCHER))


def test_the_shipped_params_carry_every_v640_key_once_at_its_default_or_one_documented_flip():
    toks = _params().split()
    for k in SHIPPED:
        assert len([t for t in toks if t.startswith(k + "=")]) == 1, k
    assert tuple(SHIPPED) == _v640_undo.SWITCHES and SHIPPED == _v640_undo.SHIPPED
    flip = _v640_undo.flip_of(LAUNCHER)
    assert flip is not None, "v6.40's PARAMS are neither the shipped defaults nor one documented flip"
    print("v6.40 PARAMS:", "the shipped defaults" if flip == "" else "the documented flip " + flip)
    base = _v640_undo._unflip(LAUNCHER)                                       # the shipped defaults' text
    assert _v640_undo.flip_of(base) == ""
    for name, kv in _v640_undo.FLIPS.items():                                 # each flip is recognised, only itself
        t = base
        for k, v in kv.items():
            d = dict(SHIPPED, research_v634_class1_entry="0")[k]
            t = t.replace("\n%s=%s " % (k, d), "\n%s=%s " % (k, v or "1.86"), 1).replace(
                "\n%s=%s\"" % (k, d), "\n%s=%s\"" % (k, v or "1.86"), 1)
        assert _v640_undo.flip_of(t) == name and _sha(_v640_undo.launcher_v639(t)) == _v640_undo.LAUNCHER_BASE_SHA256, name
    bad = base.replace("\nresearch_v640_c2j=0 ", "\nresearch_v640_c2j=2 ", 1)               # C2J without class 2
    assert _v640_undo.flip_of(bad) is None
    with pytest.raises(AssertionError):
        _v640_undo.launcher_v639(bad)                                          # the older pins then see the change
    for k in SHIPPED:
        assert ('"%s"' % k) in PART, k                                         # read by name (the launcher's key check)


def _block():
    return LAUNCHER[LAUNCHER.index("# v6.40."):LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')]


def _run_block(params, tmp):
    view = Path(tmp) / "view"
    view.mkdir(parents=True)
    (view / "Strategy1_Research_Simple.py").write_text(SIMPLE)
    script = "set -euo pipefail\n" + _block()
    env = dict(os.environ, V6_38_BUILD="1", AGENT_PATH=str(view), AGENT_PATH_REAL=str(STRATEGY), SCRIPT_DIR=str(ROOT),
               PARAMS=params)
    return subprocess.run(["bash", "-c", 'AGENT_PATH="$AGENT_PATH"; ' + script], capture_output=True, text=True, timeout=60,
                          env=env)


def _with(params, **kv):
    toks = params.split()
    for k, v in kv.items():
        toks = [(k + "=" + v) if t.startswith(k + "=") else t for t in toks]
    return " ".join(toks)


FLIPS = {"C2P": dict(research_v640_traded_classes="0,2", research_v640_c2du="1"),
         "class 1 plain": dict(research_v640_traded_classes="0,1"),
         "class 2 plain": dict(research_v640_traded_classes="0,2"),
         "classes 1 and 2": dict(research_v640_traded_classes="0,1,2", research_v640_c2du="1"),
         "C2D": dict(research_v640_traded_classes="0,2", research_v640_c2du="1", research_v640_c2d="1"),
         "C2J": dict(research_v640_traded_classes="0,2", research_v640_c2du="1", research_v640_c2j="1"),
         "C2J2": dict(research_v640_traded_classes="0,2", research_v640_c2du="1", research_v640_c2j="2"),
         "C2Q": dict(research_v640_traded_classes="0,2", research_v640_c2du="1", research_v640_c2q="1",
                     research_v640_c2q_pf="1.86"),
         "C1W": dict(research_v640_c1w="1", research_v634_class1_entry="1"),
         "G2": dict(research_v640_traded_classes="0,2", research_v640_c2du="1", research_v640_c2d="1",
                    research_v640_c2q="1", research_v640_c2q_pf="1.86")}


def test_the_block_is_placed_gated_and_passes_the_shipped_params_and_each_arms_flip(tmp_path):
    assert LAUNCHER.count("# v6.40.") == 1
    assert LAUNCHER.index('echo "[preflight] v6.39 runlen PASS') < LAUNCHER.index("# v6.40.") < \
        LAUNCHER.index('echo "[preflight] v6.40 PASS') < LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')
    assert "      tests/test_research_v6_39.py \\\n      tests/test_research_v6_40.py \\\n" in LAUNCHER
    out = _run_block(_params(), tmp_path / "ship")
    assert out.returncode == 0 and "[preflight] v6.40 PASS (every v6.40 key explicit; traded " in out.stdout, out
    if _v640_undo.flip_of(LAUNCHER) == "":
        assert "arms C2D 0 C2J 0 C2Q 0 (pf 0) C1W 0; fixes guard_hold 1 init_price 1 s14_class_key 1 s14_flush_spec 1" \
            in out.stdout and "traded 0:" in out.stdout
    for name, kv in FLIPS.items():
        out = _run_block(_with(_shipped_params(), **kv), tmp_path / name)
        assert out.returncode == 0 and "[preflight] v6.40 PASS" in out.stdout, (name, out.stdout, out.stderr)


REFUSED = {
    "c2d without class 2": dict(research_v640_c2d="1"),
    "c2j without class 2": dict(research_v640_c2j="1"),
    "c2q without class 2": dict(research_v640_c2q="1", research_v640_c2q_pf="1.5"),
    "c2j with class 1 only": dict(research_v640_traded_classes="0,1", research_v640_c2j="1"),
    "c2d with c2j": dict(research_v640_traded_classes="0,2", research_v640_c2d="1", research_v640_c2j="1"),
    "c2q without pf": dict(research_v640_traded_classes="0,2", research_v640_c2q="1"),
    "c2q without S14": dict(research_v640_traded_classes="0,2", research_v640_c2q="1", research_v640_c2q_pf="1.5",
                            research_v632_fee_making="0"),
    "c1w without v6.34": dict(research_v640_c1w="1"),
    "c1w without v6.36": dict(research_v640_c1w="1", research_v634_class1_entry="1", research_v636_step_withdrawal="0"),
    "class 1 by both paths": dict(research_v640_traded_classes="0,1", research_v634_class1_entry="1"),
    "non-canonical traded": dict(research_v640_traded_classes="0+2"),
    "bad traded": dict(research_v640_traded_classes="2"),
    "bad c2j": dict(research_v640_c2j="3"),
    "bad flag": dict(research_v640_guard_hold="yes"),
    "bad pf": dict(research_v640_c2q_pf="-1"),
}


def test_the_block_refuses_every_unshippable_combination(tmp_path):
    for name, kv in REFUSED.items():
        out = _run_block(_with(_shipped_params(), **kv), tmp_path / name.replace(" ", "_"))
        assert out.returncode != 0 and "ERROR: v6.40" in out.stderr, (name, out.stdout, out.stderr)
    twice = _shipped_params() + " research_v640_c2d=0"
    out = _run_block(twice, tmp_path / "twice")
    assert out.returncode != 0 and "given 2 times" in out.stderr
    missing = " ".join(t for t in _shipped_params().split() if not t.startswith("research_v640_c1w="))
    out = _run_block(missing, tmp_path / "missing")
    assert out.returncode != 0 and "given 0 times" in out.stderr


def test_the_block_refuses_a_tree_without_each_named_line(tmp_path):
    for k, pat in enumerate(['self._v6_40_init()', 'self._v6_40_request(state)',
                             'if v6_40_g is not None and self._v6_40_c2q_off(book_id):']):
        view = tmp_path / ("v%d" % k)
        view.mkdir()
        (view / "Strategy1_Research_Simple.py").write_text(SIMPLE.replace(pat, "pass", 1))
        script = "set -euo pipefail\n" + _block()
        env = dict(os.environ, V6_38_BUILD="1", AGENT_PATH=str(view), AGENT_PATH_REAL=str(STRATEGY), SCRIPT_DIR=str(ROOT),
                   PARAMS=_params())
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60, env=env)
        assert out.returncode != 0 and "ERROR: v6.40 the agent lacks" in out.stderr, pat


def test_the_ast_undo_is_the_exact_text_undo_and_every_older_chain_takes_it():
    import _v638_undo
    import _v639_undo
    assert ast.dump(_v640_undo.undo_tree(ast.parse(SIMPLE))) == ast.dump(ast.parse(_v640_undo.joined_v639(SIMPLE)))
    assert ast.dump(_v639_undo.undo(ast.parse(SIMPLE))) == ast.dump(_v639_undo.undo(ast.parse(_v640_undo.joined_v639(SIMPLE))))
    assert ast.dump(_v638_undo.parse(SIMPLE)) == ast.dump(_v638_undo.undo(ast.parse(SIMPLE)))
    other = (STRATEGY / "research_v617_touch_quote.py").read_text()
    assert ast.dump(_v640_undo.undo_tree(ast.parse(other))) == ast.dump(ast.parse(other))


def test_the_size_rule_scales_class1_and_keeps_class2_and_the_pass_runs_class1_in_class0_units():
    t = cl.class_table(_cfg())
    g = cl.book_grids(t, frozenset({0, 1, 2}), True)
    assert g[70].scale == 2.5364 / 0.25 and g[100].scale == 1.0                 # k137: class 1 x 10.1456, class 2 x 1
    assert g[70].min_order / g[70].scale == pytest.approx(0.25, abs=1e-12)     # the machinery's minimum: class 0's
    a = _agent(research_v640_traded_classes="0,1,2", research_v640_c2du="1")
    foreign, layout = v623.class_layout(_cfg())
    f, _l = a._v6_40_observe(_state(_cfg()), foreign, layout, True)
    assert f == frozenset() and len(a._v6_40_grids) == 64
    assert a._v6_40_units(70, 0.25, None) == (2.5364 / 0.25, 2.5364, 4) and a._v6_40_units(5, 0.25, SimpleNamespace(volumeDecimals=4)) == (1.0, 0.25, 4)
    assert a._v6_40_caps_books(range(128), 128) == math.ceil(64 + 32 * 2.5364 / 0.25 + 32 - 1e-9)
    for combo, books in (("0", 0), ("0,1", 32), ("0,2", 32), ("0,1,2", 64)):
        b = _agent(research_v640_traded_classes=combo)
        b._v6_40_observe(_state(_cfg()), foreign, layout, True)
        assert len(b._v6_40_grids) == books, combo


def _books(mids):
    """A state's books as the lazy raw wire dicts (best bid / ask around each mid)."""
    return {b: {"b": [{"p": round(m * 0.9995, 4)}], "a": [{"p": round(m * 1.0005, 4)}]} for b, m in mids.items()}


def test_a_traded_book_on_another_classs_market_is_foreign_that_request():
    a = _agent(research_v640_traded_classes="0,1,2", research_v640_c2du="1")
    foreign, layout = v623.class_layout(_cfg())
    ok = SimpleNamespace(config=_cfg(), version=65, timestamp=10 ** 12,
                         books=_books({5: 300.0, 70: 29.6, 100: 313.0}))
    f, _l = a._v6_40_observe(ok, foreign, layout, True)
    assert f == frozenset() and 70 in a._v6_40_grids and 100 in a._v6_40_grids
    # the 23:50-00:05 JST 10-24 window: the three-class map on the old simulator -- books 64-95 at 300, 96-127 at 29.57
    interim = SimpleNamespace(config=_cfg(), version=65, timestamp=10 ** 12 + 10 ** 9,
                              books=_books({5: 300.0, 70: 300.0, 100: 29.6}))
    f, _l = a._v6_40_observe(interim, foreign, layout, True)
    assert {70, 100} <= f and 70 not in a._v6_40_grids and 100 not in a._v6_40_grids and 70 not in a._v6_40_vgrids
    assert a._v6_40_counts["market_mismatch_book_states"] == 2
    f, _l = a._v6_40_observe(ok, foreign, layout, True)                        # back on its market: traded again
    assert f == frozenset() and 70 in a._v6_40_grids


def test_v634s_class1_entry_takes_only_the_books_on_class_1s_market():
    a = _agent(research_v640_c1w="1")
    foreign, layout = v623.class_layout(_cfg())
    st = SimpleNamespace(config=_cfg(), version=65, timestamp=10 ** 12, books=_books({70: 29.6, 71: 300.0}))
    a._v6_40_observe(st, foreign, layout, True)
    assert a._v6_40_c1_market(st, frozenset({70, 71, 72})) == frozenset({70, 72})   # 72: no L1, kept (nothing to place)
    assert a._v6_40_counts["c1_market_mismatch_book_states"] == 1
    a._v6_40_table = {"status": cl.STATUS_OK, "classes": []}
    assert a._v6_40_c1_market(st, frozenset({70})) == frozenset()              # no class 1 in the map: none


def test_the_maker_sanitizer_override_runs_the_base_once_as_v639_and_once_per_class_on_its_own_grid():
    mod = _part_module()
    calls = []

    class Base:
        def _research_sanitize_maker_instructions(self, response, state):
            calls.append((state.config.priceDecimals, state.config.volumeDecimals,
                          self._research_exchange_min_order_size, [ix["bookId"] for ix in response.instructions]))
            response.instructions[:] = [ix for ix in response.instructions if ix.get("keep", True)]

    class Agent(mod.V640Part, Base):
        def __init__(self):
            self._research_exchange_min_order_size = 0.25
            self._v6_40_grids = {}

        @staticmethod
        def _get(ix, *names):
            return next((ix.get(n) for n in names if ix.get(n) is not None), None)

        @staticmethod
        def _research_instruction_is_maker(ix):
            return ix.get("type") == "PLACE_ORDER_LIMIT"

        def _v6_40_count(self, *a, **k):
            pass
    a = Agent()
    state = SimpleNamespace(books={}, config=SimpleNamespace(priceDecimals=2, volumeDecimals=4))
    ins = [{"type": "PLACE_ORDER_LIMIT", "bookId": 100}, {"type": "CANCEL_ORDERS", "bookId": 100},
           {"type": "PLACE_ORDER_LIMIT", "bookId": 5}, {"type": "PLACE_ORDER_LIMIT", "bookId": 70, "keep": False},
           {"type": "PLACE_ORDER_LIMIT", "bookId": 101}]
    resp = SimpleNamespace(instructions=list(ins))
    a._research_sanitize_maker_instructions(resp, state)                      # no grid: the base, once, everything
    assert calls == [(2, 4, 0.25, [100, 100, 5, 70, 101])] and len(resp.instructions) == 4
    calls.clear()
    t = cl.class_table(_cfg())
    a._v6_40_grids = cl.book_grids(t, frozenset({0, 1, 2}), True)
    resp = SimpleNamespace(instructions=list(ins))
    a._research_sanitize_maker_instructions(resp, state)
    assert calls[0] == (2, 4, 0.25, [100, 5])                                  # v6.39's call: home + non-maker
    assert sorted(calls[1:]) == [(2, 4, 2.5364, [70]), (4, 4, 0.2396, [100, 101])]   # each class on its own grid
    assert [ix["bookId"] for ix in resp.instructions] == [100, 100, 5, 101]     # the original order, the dropped one out
    assert a._research_exchange_min_order_size == 0.25                         # restored


def test_mutation_guards_near_the_edges():
    """k139 mutations C7 / C10 / C14 / C15 / P10: the vacuum clamp's inside margin is the venue tick; C2Q is strict at its
    threshold; the digest sees a minimum; the class tracker books class 2 apart; the lift lands a 4-decimal price."""
    # C7: a class-2 vacuum target below the bid + one venue tick is clamped there (not bid + one depth unit)
    assert cl.vacuum_price_du(313.015, 2.0, "buy", bid=313.0, ask=313.03, du=0.0104, tick=0.0001, decimals=4) == 313.0001
    assert cl.vacuum_price_du(313.015, 2.0, "sell", bid=313.0, ask=313.03, du=0.0104, tick=0.0001, decimals=4) == 313.0299
    # C10: q2 exactly at thr x q (b2 = 0 -> g = 0 -> thr = 1): NOT armed; a hair above: armed
    d = cl.c2q_decision(1.0, 2.0, 0.5, 1.0, 0.5, 1.0, 0.5, 0.0, 1.0)
    assert d["thr"] == 1.0 and d["q"] == 0.5 and d["q2"] == 0.5 and d["armed"] is False
    assert cl.c2q_decision(1.0001, 2.0, 0.5, 1.0, 0.5001, 1.0, 0.5, 0.0, 1.0)["armed"] is True
    # C14: the digest changes with a class minimum
    c = _cfg()
    c["classes"][2]["config"]["min_order_size"] = 0.25
    assert cl.class_digest(c) != cl.class_digest(_cfg())
    # C15: fills on class-2 books land in class 2's sums
    rnd = random.Random(5)
    t = cl.ClassCaptureTracker(weight=0.005)
    t.class_of = {b: (0 if b < 4 else 2) for b in range(6)}
    t.rates = {b: 0.0002 for b in range(6)}
    for ts, books in _stream(rnd, states=900):
        t.begin(ts); t.set_active(True, ts)
        for b, pr in books.items():
            if pr:
                t.observe(b, pr, 7, 0.25, ts)
        t.release(ts)
    r2, c2 = t.class_sums(2)
    r0, c0 = t.class_sums(0)
    assert c2 != 0.0 and c0 != 0.0 and len({cls for _ts, cls, _r, _c in t.v640}) == 2
    # P10: a class-2 price whose double sits below its 4-decimal value is lifted on the class's decimals
    import research_v611_wire as w
    from decimal import Decimal
    p2 = next(x for x in (round(313.0 + k * 0.0001, 4) for k in range(1, 400)) if Decimal(x) < Decimal(str(x)))
    a = _agent(research_v640_traded_classes="0,2")
    foreign, layout = v623.class_layout(_cfg())
    a._v6_40_observe(_state(_cfg()), foreign, layout, True)
    ins = [{"type": "PLACE_ORDER_LIMIT", "bookId": 100, "price": p2, "direction": 0}]
    moved = a._v6_40_lift(ins, 2)
    assert ins[0]["price"] != p2 and moved["buy"] == 1 and w.venue_placed_price(ins[0]["price"], 4) == p2


def _gauge_page(w0, mk0, w2, mk2, sim="20261024_1300", ts=600 * 10 ** 9):
    lines = ['simulation_gauges{netuid="79",sim_id="%s",simulation_gauge_name="timestamp",wallet="w"} %e' % (sim, ts)]
    for name, w, mk in (("simulation_0", w0, mk0), ("simulation_1", 0.25, 999.0), ("simulation_2", w2, mk2)):
        lines.append('class_gauges{asset_class="%s",class_gauge_name="weight",netuid="79",sim_id="%s",wallet="w"} %r' % (name, sim, w))
        lines.append('class_gauges{asset_class="%s",class_gauge_name="making_credit",netuid="79",sim_id="%s",wallet="w"} %r'
                     % (name, sim, mk))
    lines.append('agent_class_gauges{agent_id="5",asset_class="simulation_2",agent_class_gauge_name="making"} 3.0')
    return "\n".join(lines) + "\n"


def test_c2qs_pool_factor_is_the_published_class_pools_with_the_configured_number_only_as_fallback():
    g = cl.parse_class_gauges(_gauge_page(0.5, 30000.0, 0.25, 4000.0))
    assert g["sim_id"] == "20261024_1300" and g["sim_ts_ns"] == 600 * 10 ** 9
    assert cl.pool_factor(g, "simulation_0", "simulation_2") == (0.25 / 0.5) * (30000.0 / 4000.0)
    snap = dict(g, wall=1.0)
    assert cl.live_pool_factor(snap, 700 * 10 ** 9, "20261024_1300", "simulation_0", "simulation_2", 1.5) == (3.75, "page")
    assert cl.live_pool_factor(snap, 700 * 10 ** 9, "20261019_1025", "simulation_0", "simulation_2", 1.5) == (1.5, "fallback_other_sim")
    assert cl.live_pool_factor(snap, 500 * 10 ** 9, "20261024_1300", "simulation_0", "simulation_2", 1.5) == (1.5, "fallback_after_state")
    assert cl.live_pool_factor(None, 700 * 10 ** 9, "20261024_1300", "simulation_0", "simulation_2", 1.5) == (1.5, "fallback_no_page")
    none2 = dict(cl.parse_class_gauges(_gauge_page(0.5, 30000.0, 0.25, 0.0)), wall=1.0)       # no class-2 making yet
    assert cl.live_pool_factor(none2, 700 * 10 ** 9, "20261024_1300", "simulation_0", "simulation_2", 1.5) == (1.5, "fallback_no_pool")
    assert cl.parse_class_gauges("nothing here") is None
    # the feed: v6.16's shared cache on its own file, fetched through the injected fetch (no network in the suite)
    with tempfile.TemporaryDirectory() as d:
        calls = []
        feed = cl.ClassGaugeFeed(cache_dir=d, fetch=lambda url, t: calls.append(url) or _gauge_page(0.5, 30000.0, 0.25, 4000.0),
                                 clock=lambda: 1000.0)
        feed.step()
        assert calls == [cl.GAUGE_URL] and feed.snapshot()["classes"]["simulation_2"]["weight"] == 0.25
        feed.step()
        assert len(calls) == 1                                                  # cached for the period
        assert os.path.basename(feed._paths()[0]) == "gauges.json"
    # the agent's decision takes the page's PF and logs its source
    a = _agent(research_v640_traded_classes="0,2", research_v640_c2du="1", research_v640_c2q="1", research_v640_c2q_pf="1.5")
    foreign, layout = v623.class_layout(_cfg())
    st = SimpleNamespace(config=dict(_cfg(), simulation_id="20261024_1300"), version=65, timestamp=10 ** 12, books={})
    a._v6_40_observe(st, foreign, layout, True)
    a._v6_40_gauges = SimpleNamespace(snapshot=lambda: snap, status=lambda: {"sim_id": "20261024_1300"})
    a._direct_current_state_timestamp_ns = 700 * 10 ** 9
    a._v6_40_c2q_update()
    assert a._v6_40_c2q["pf"] == 3.75 and a._v6_40_c2q["pf_source"] == "page"


def test_b4_6_a_spec_65_state_sets_s14s_flush_to_300_s_and_a_spec_64_state_back_to_60():
    a = _agent()
    tr = fm.RealizedTracker()
    a._v6_40_flush(tr, 65)
    assert tr.flush_ns == 300_000_000_000 and a._v6_40_counts["flush_set_300s"] == 1
    a._v6_40_flush(tr, 65)
    assert a._v6_40_counts["flush_set_300s"] == 1                              # set once, not per request
    a._v6_40_flush(tr, 64)
    assert tr.flush_ns == fm.CAPTURE_FLUSH_NS == 60_000_000_000
