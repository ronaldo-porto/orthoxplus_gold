"""v6.38 layer 3: tonight's pre-registered candidates as agent code -- research_v638_kbl, research_v638_lean (+ _arm),
research_v638_c1 (+ _arm) -- every switch shipped 0.

Piece 0 the per-(UID, book) canary draws (k116 PREREG_2 A3, k118 PREREG section 8); piece 1 KBL / K5 (k105 / L2B: the
post-step window touch quote keyed to the book's capture balance, the balance-keyed ladder, the touch cleanup); piece 2
the k116 lean candidates CA / CB / CC / CD (slope 2, the front-built lean); piece 3 the k118 class-1 keepers K1 / K3 /
K3B / K3Q on v6.34's class-1 entry.  The references are the replay engines k105/vm715.py, k116/vm717.py, k118/vm718.py;
the engine parity itself runs outside the suite (scratchpad k124/check), these tests pin the rules element by element,
the switches' shipped values (all 0: v6.37) and the wiring (layer 3 only adds).

Nothing here touches the host's caches, the network or a live tree, and nothing is imported from the scratchpad: the
agent is the older suites' harness class (t634: the real v6.3 pass with v6.17 / v6.33 / v6.34 on a synthetic state)
carrying v6.38 layer 3's real methods, its real _init_build_switches statements and the real methods its hooks sit in.
"""
import ast
import hashlib
import math
import os
import re
import subprocess
import sys
import textwrap
import types
from collections import deque
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v617_touch_quote as tq  # noqa: E402
import research_v6_32_fee_making as fm  # noqa: E402
import research_v6_36_step_withdrawal as sw  # noqa: E402
import research_v6_38_canary as cn  # noqa: E402
import research_v6_38_kbl as kb  # noqa: E402
import research_v6_38_keeper as kp  # noqa: E402
import research_v6_38_lean as ln  # noqa: E402
from _harness import extractor  # noqa: E402
from _harness import simple_text, read_source, copy_source, strategy_view  # noqa: E402

SIMPLE = simple_text()
import _v640_undo  # noqa: E402   (k139)
LAUNCHER = _v640_undo.launcher_v639(read_source(ROOT / "run_strategy1_research_simple_multi.sh"))   # k139: v6.39's launcher
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000
BUY, SELL = tq.SIDE_BUY, tq.SIDE_SELL
KEYS = ("research_v638_kbl", "research_v638_lean", "research_v638_lean_arm", "research_v638_c1", "research_v638_c1_arm")
MODULES = ("research_v6_38_canary", "research_v6_38_kbl", "research_v6_38_lean", "research_v6_38_keeper")
PREFIXES = ("_v6_38_", "v6_38_")
# k126 (the merged v6.38 = layer 2, k123's v6.38-A, + this layer 3): layer 2's own names carry the same prefixes (its
# module's aliases v6_38_apply / _feed / _trades_of / _trades_reset / _last_taker_side and its init target
# _v6_38_switches) and no identifier is shared by the two layers (k124 CHANGES_L3 5a): layer 3's statements are those
# naming a prefixed name that is not layer 2's -- read from Simple's own import of layer 2's module and k123's
# tests/_v638_undo.py (none on a tree without layer 2).
try:
    import _v638_undo  # noqa: E402
except ImportError:                                  # a tree without v6.38 layer 2
    _v638_undo = None


def _layer2_names():
    if _v638_undo is None:
        return frozenset()
    names = set(_v638_undo.V638_INIT_TARGETS)
    for node in ast.parse(SIMPLE).body:
        if isinstance(node, ast.ImportFrom) and node.module == _v638_undo.V638_MODULE:
            names.update(a.asname or a.name for a in node.names)
    return frozenset(names)


L2_NAMES = _layer2_names()


def _mentions638(node):
    for n in ast.walk(node):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.arg if isinstance(n, (ast.arg, ast.keyword)) else n.value if isinstance(n, ast.Constant) else None
        if isinstance(name, str) and (name in KEYS or (name.startswith(PREFIXES) and name not in L2_NAMES)):
            return True
    return False


class _Drop638(ast.NodeTransformer):
    """Takes out what v6.38 layer 3 adds and nothing else -- its imports, its _v6_38_ methods, statements naming its
    identifiers (an if on one keeps its else) -- keeping every docstring: v6.37 (FROZEN_1)."""

    def _stmts(self, stmts):
        out = []
        for st in stmts:
            if isinstance(st, ast.ImportFrom) and st.module in MODULES:
                continue
            if isinstance(st, ast.FunctionDef) and st.name.startswith("_v6_38_"):
                continue
            if isinstance(st, ast.If) and _mentions638(st.test):
                out.extend(self._stmts(st.orelse))
                continue
            st = self.visit(st)
            if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)) and _mentions638(st):
                continue
            out.append(st)
        return out

    def generic_visit(self, node):
        for field in ("body", "orelse", "finalbody"):
            if isinstance(getattr(node, field, None), list):
                setattr(node, field, self._stmts(getattr(node, field)))
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


# ---- 0. the canary draw ----------------------------------------------------------------------------------------------

# fixed vectors (the pre-registered formula evaluated once; draws by book 0..7 / 96..127)
VECTORS_K116 = {220: {"CA": "00100111", "CB": "00010101", "CC": "01101100", "CD": "10011100"},
                162: {"CA": "11101001", "CB": "11111011", "CC": "01001101", "CD": "00110101"}}
VECTORS_K118 = {220: "11101100001010010010100100110100", 162: "10010110110111011001000001000100"}


def test_the_canary_tags_dims_and_modes_are_the_pre_registered_ones():
    assert cn.TAG_K116 == "orthox-profile-k116-v1" and cn.TAG_K118 == "orthox-profile-k118-v1"
    assert cn.DIMS_K116 == ("CA", "CB", "CC", "CD") and cn.DIM_C1 == "c1" and cn.K == 2
    assert (cn.MODE_OFF, cn.MODE_HALF, cn.MODE_ALL) == (0, 1, 2)
    assert cn.LEAN_ARMS == {1: "CA", 2: "CB", 3: "CC", 4: "CD"} and cn.C1_ARMS == {1: "K1", 2: "K3", 3: "K3B", 4: "K3Q"}


def test_the_draw_is_the_pre_registered_formula_on_fixed_vectors():
    for uid, dims in VECTORS_K116.items():
        for dim, want in dims.items():
            assert "".join(str(cn.draw(cn.TAG_K116, dim, uid, b)) for b in range(8)) == want, (uid, dim)
    for uid, want in VECTORS_K118.items():
        assert "".join(str(cn.draw(cn.TAG_K118, cn.DIM_C1, uid, b)) for b in range(96, 128)) == want, uid
    assert hashlib.sha256(b"orthox-profile-k116-v1|CA|220|0").hexdigest()[:8] == "3520a86a"     # even: 0
    assert hashlib.sha256(b"orthox-profile-k118-v1|c1|220|96").hexdigest()[:8] == "d18159f5"    # odd: 1
    for uid in (0, 7, 162, 220, 255):
        for b in range(128):
            for tag, dim in [(cn.TAG_K116, d) for d in cn.DIMS_K116] + [(cn.TAG_K118, cn.DIM_C1)]:
                h = hashlib.sha256(("%s|%s|%d|%d" % (tag, dim, uid, b)).encode()).hexdigest()
                assert cn.draw(tag, dim, uid, b) == int(h[:8], 16) % 2


def test_modes_and_arms_parse_strictly():
    assert [cn.mode_of(v) for v in (0, 1, 2, "1", 2.0, 1.0)] == [0, 1, 2, 1, 2, 1]
    assert [cn.mode_of(v) for v in (3, -1, 1.5, "x", None, True, float("inf"), float("nan"))] == [0] * 8
    assert [cn.arm_of(v, cn.LEAN_ARMS) for v in (1, 4, "3", 2.0)] == [1, 4, 3, 2]
    assert [cn.arm_of(v, cn.C1_ARMS) for v in (0, 5, -1, 1.5, "K1", None, False)] == [0] * 7
    assert cn.book_on(2, None) and cn.book_on(1, 1) and not cn.book_on(1, 0) and not cn.book_on(1, None)
    assert not cn.book_on(0, 1) and not cn.book_on(3, 1)


def test_the_table_draws_each_book_once_for_the_first_uid_and_never_for_another():
    t = cn.CanaryTable(cn.TAG_K116, "CB")
    assert t.value(220, 3) == cn.draw(cn.TAG_K116, "CB", 220, 3) and t.uid == 220
    t.table[3] = 1 - t.table[3]                     # a value once drawn is never drawn again
    before = t.table[3]
    assert t.value(220, 3) == before and t.table[3] == before
    assert t.value(162, 4) is None and t.value(None, 4) is None and t.value(-1, 4) is None and t.refused == 3
    assert t.extend(220, range(8)) == 7 and sorted(t.table) == list(range(8))
    snap = t.snapshot()
    assert snap["table_uid"] == 220 and snap["books"] == 8 and snap["tag"] == cn.TAG_K116 and snap["dim"] == "CB"
    text = cn.table_text(cn.TAG_K116, "CB", 220, t.table)
    assert text.splitlines()[0] == "orthox-profile-k116-v1|uid=220|CB" and snap["table_sha256"] == \
        hashlib.sha256(text.encode()).hexdigest()


def test_the_canary_module_reads_nothing_but_its_arguments():
    tree = ast.parse(read_source(STRATEGY / "research_v6_38_canary.py"))
    mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
        {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert mods <= {"hashlib", "math", "operator", "typing", "__future__"}, mods
    assert not any(isinstance(n, ast.Constant) and isinstance(n.value, int) and n.value in (220, 162)
                   for n in ast.walk(tree))


# ---- 1. KBL / K5: the module -----------------------------------------------------------------------------------------

def test_the_kbl_modes_are_the_pre_registered_arm_strings():
    assert kb.components(1) == {"x1": True, "x2b": True, "x3a": True, "x5": True, "xb": True, "xl": True, "x4": False}
    assert kb.components(2) == {"x1": True, "x2b": True, "x3a": True, "x5": True, "xb": False, "xl": False, "x4": True}
    assert not any(kb.components(0).values()) and not any(kb.components("x").values())
    assert [kb.mode_of(v) for v in (0, 1, 2, "2", 1.0, 3, None, True, 0.5)] == [0, 1, 2, 2, 1, 0, 0, 0, 0]


def test_the_window_is_the_step_period_minus_the_validators_horizon():
    assert fm.HORIZON_NS == 20 * S and kb.window_ns(30 * S) == 10 * S
    assert kb.window_ns(20 * S) is None and kb.window_ns(None) is None
    for phase, want in ((0, True), (9, True), (10, False), (29, False)):
        assert kb.in_window(77, phase, 77, S, 10 * S) is want
    assert not kb.in_window(77, 0, 76, S, 10 * S) and not kb.in_window(77, 0, None, S, 10 * S)
    assert not kb.in_window(None, 0, 77, S, 10 * S) and not kb.in_window(77, 0, 77, S, None)


def test_the_window_side_is_the_steps_and_xb_withholds_the_capture_surplus_side():
    assert kb.window_side(1) == BUY and kb.window_side(-1) == SELL and kb.window_side(0) is None
    assert kb.window_side(1, buy_capture=5.0, sell_capture=1.0, keyed=True) is None      # buy is the surplus side
    assert kb.window_side(1, buy_capture=1.0, sell_capture=5.0, keyed=True) == BUY      # the deficit side
    assert kb.window_side(-1, buy_capture=1.0, sell_capture=1.0, keyed=True) == SELL     # balanced
    assert kb.window_side(1, buy_capture=5.0, sell_capture=1.0, keyed=False) == BUY
    assert kb.surplus_side(1.0, 1.0 + 0.5e-9) is None and kb.surplus_side(1.0 + 2e-9, 1.0) == BUY
    assert kb.surplus_side(0.0, 3.0) == SELL


def test_the_window_verdict_is_flipped_off_side_and_left_at_one_tick_with_no_edge_test():
    kw = dict(window=BUY, bid=100.00, ask=100.03, tick=0.01)
    assert kb.verdict(SELL, 100.03, **kw) == tq.CANCEL_FLIPPED
    assert kb.verdict(BUY, 100.00, **kw) is None and kb.verdict(BUY, 100.01, **kw) is None
    assert kb.verdict(BUY, 99.99, **kw) == tq.CANCEL_LEFT                                 # the touch one tick better
    assert kb.verdict(SELL, 100.04, window=SELL, bid=100.00, ask=100.03, tick=0.01) == tq.CANCEL_LEFT
    assert kb.verdict(BUY, None, **kw) == tq.CANCEL_LEFT


def test_the_window_join_carries_no_hurdle_and_the_ladder_pairs_are_per_side_and_level():
    assert kb.join(BUY, 100.00, 100.03, 0.01) == 100.00 and kb.join(SELL, 100.00, 100.03, 0.01) == 100.03
    assert kb.join(BUY, 100.00, 100.00, 0.01) is None
    assert kb.ladder_pairs((SELL,), (1, 2)) == frozenset({(SELL, 1), (SELL, 2)})
    assert kb.LADDER_LEVELS == (1, 2) and kb.REST_TICKS_WINDOW == 1


def test_touch_first_moves_each_books_touch_placement_before_its_first_instruction_only():
    ix = [("c", 3), ("d", 3), ("t", 3), ("c", 4), ("t", 5)]
    out, ahead = kb.touch_first(ix, lambda x: x[1], lambda x: x[0] == "t")
    assert out == [("t", 3), ("c", 3), ("d", 3), ("c", 4), ("t", 5)] and ahead == 1
    assert kb.touch_first([("c", 3)], lambda x: x[1], lambda x: False) == ([("c", 3)], 0)


# ---- 2. the lean candidates: the module ------------------------------------------------------------------------------

def test_the_lean_arms_and_constants_are_the_pre_registered_ones():
    assert ln.CA_SLOPE == 2.0 and ln.FBL_THRESHOLD_BPS == tq.THRESHOLD_BPS == 2.3 and ln.FBL_CAP == 48.0
    assert ln.FBL_TARGET == 0.0
    parts = {a: ln.arm_parts(a) for a in (1, 2, 3, 4)}
    assert parts[1] == {"slope2": True, "fbl": False, "kbl": False}            # CA
    assert parts[2] == {"slope2": True, "fbl": True, "kbl": False}             # CB = CA + FBL
    assert parts[3] == {"slope2": False, "fbl": True, "kbl": False}            # CC = FBL
    assert parts[4] == {"slope2": True, "fbl": True, "kbl": True}              # CD = KBL + CB


def test_the_lean_sign_is_set_at_the_hurdle_held_inside_it_and_cleared_without_a_fundamental():
    mid = 100.0
    up = mid * math.exp(2.4e-4)
    assert ln.fbl_sign(0, up, mid) == 1 and ln.fbl_sign(-1, mid * math.exp(-3e-4), mid) == -1
    assert ln.fbl_sign(1, mid * math.exp(1e-4), mid) == 1                     # inside the hurdle: held
    assert ln.fbl_sign(-1, mid * math.exp(1e-4), mid) == -1
    assert ln.fbl_sign(1, None, mid) == 0 and ln.fbl_sign(1, float("nan"), mid) == 0 and ln.fbl_sign(1, -1.0, mid) == 0


def test_the_front_built_clip_is_the_lean_shortfall_capped_when_it_beats_the_clip():
    assert ln.fbl_clip(BUY, -10.0, 2.0, 1) == 10.0 and ln.fbl_clip(SELL, 7.5, 2.0, -1) == 7.5
    assert ln.fbl_clip(BUY, -60.0, 2.0, 1) == 48.0                            # at most LEAN_CAP
    assert ln.fbl_clip(BUY, -1.5, 2.0, 1) == 2.0 and ln.fbl_clip(BUY, 5.0, 2.0, 1) == 2.0
    assert ln.fbl_clip(BUY, -10.0, 2.0, 0) == 2.0                             # no sign: the clip


# ---- 3. the keepers: the module --------------------------------------------------------------------------------------

def _k1_target_vm717(side, bb, ba, r, tick=0.01):
    """vm717's _k1_target(B, side, r, 0), verbatim arithmetic (k118/vm718.py)."""
    if r is None or not bb < ba:
        return None
    mid_ = 0.5 * (bb + ba)
    if side == "bid":
        p_ = round(math.floor(mid_ * (1.0 - r) / tick + 1e-9) * tick, 2)
        while mid_ - p_ <= r * mid_ + 1e-12:
            p_ = round(p_ - tick, 2)
        return round(min(p_, bb), 2)
    p_ = round(math.ceil(mid_ * (1.0 + r) / tick - 1e-9) * tick, 2)
    while p_ - mid_ <= r * mid_ + 1e-12:
        p_ = round(p_ + tick, 2)
    return round(max(p_, ba), 2)


def test_the_keeper_target_is_the_first_price_the_a_radius_does_not_pull():
    assert kp.target(BUY, 29.55, 29.57, 3.8e-4, 0.01) == 29.54 and kp.target(SELL, 29.55, 29.57, 3.8e-4, 0.01) == 29.58
    assert kp.target(BUY, 29.55, 29.57, None, 0.01) is None and kp.target(BUY, 29.57, 29.57, 3e-4, 0.01) is None
    import random
    rng = random.Random(118)
    for _ in range(5000):
        mid = rng.uniform(20.0, 40.0)
        spread = rng.choice((1, 2, 3, 4)) * 0.01
        bb = round(math.floor(mid / 0.01) * 0.01, 2)
        ba = round(bb + spread, 2)
        r = rng.uniform(0.0, 1.5e-3)
        for side, eng in ((BUY, "bid"), (SELL, "ask")):
            t = kp.target(side, bb, ba, r, 0.01, 2)
            assert t == _k1_target_vm717(eng, bb, ba, r), (side, bb, ba, r)
            m = 0.5 * (bb + ba)
            assert abs(t - m) > r * m + kp.RADIUS_EPS and ((t <= bb) if side == BUY else (t >= ba))


def test_the_keep_rule_is_one_tick_farther_than_the_target_and_closer_is_kept():
    assert kp.keeps(BUY, 29.53, 29.54, 0.01) and kp.keeps(BUY, 29.56, 29.54, 0.01)
    assert not kp.keeps(BUY, 29.52, 29.54, 0.01) and not kp.keeps(BUY, 29.53, None, 0.01)
    assert kp.keeps(SELL, 29.59, 29.58, 0.01) and not kp.keeps(SELL, 29.60, 29.58, 0.01)
    from research_v6_34_class1 import CANCEL_RESIZE
    kw = dict(is_paced=False, target_price=29.54, tick=0.01)
    assert kp.verdict(BUY, 29.54, 5.0, 0.0, "C1_BOUND", **kw) == "C1_BOUND"
    assert kp.verdict(BUY, 29.54, 30.0, 20.0, None, **dict(kw, is_paced=True)) == CANCEL_RESIZE
    assert kp.verdict(BUY, 29.52, 20.0, 20.0, None, **kw) == kp.CANCEL_AWAY
    assert kp.verdict(BUY, 29.53, 20.0, 20.0, None, **kw) is None
    assert kp.LIFE_NS == 3_600 * S and kp.KEEP_TICKS == 1


def test_the_arm_side_gates_are_k3s_move_k3bs_surplus_and_k3qs_parity():
    assert kp.move_side(BUY) == SELL and kp.move_side(SELL) == BUY and kp.move_side(None) is None
    g = kp.side_gate
    assert g(BUY, arm=1, move=None, surplus=BUY, adding=True, hold=True) is None                 # K1: none
    assert g(BUY, arm=2, move=None, surplus=None, adding=True, hold=False) == kp.WHY_NO_MOVE
    assert g(BUY, arm=2, move=SELL, surplus=None, adding=True, hold=False) == kp.WHY_AGAINST_MOVE
    assert g(BUY, arm=2, move=BUY, surplus=BUY, adding=True, hold=True) is None
    assert g(BUY, arm=3, move=BUY, surplus=BUY, adding=True, hold=False) == kp.WHY_SURPLUS
    assert g(BUY, arm=3, move=BUY, surplus=None, adding=True, hold=False) is None
    assert g(BUY, arm=4, move=BUY, surplus=None, adding=True, hold=True) == kp.WHY_PARITY
    assert g(BUY, arm=4, move=BUY, surplus=None, adding=False, hold=True) is None            # never the reducing side
    assert kp.q0_of(1.0, 4.0) == 0.25 and kp.q0_of(5.0, 4.0) == 1.0 and kp.q0_of(-1.0, 4.0) == 0.0
    assert kp.q0_of(1.0, 0.0) == 0.0 and not kp.parity_hold(0.0, 0.0, 0.0)                  # cold start: admitted
    assert kp.parity_hold(0.1, 1.0, 0.25) and not kp.parity_hold(0.3, 1.0, 0.25)


def test_the_capture_tracker_keeps_the_base_trackers_results_and_adds_captured_and_fees():
    import random
    rng = random.Random(7)

    def feed(t):
        t.begin(10 * S)
        t.set_active(True, 10 * S)
        ts = 10 * S
        for k in range(400):
            ts += S
            t.begin(ts)
            prints = []
            for j in range(3):
                p = round(100.0 + rng.uniform(-0.05, 0.05), 2)
                own = rng.random() < 0.2
                prints.append({"p": p, "q": 2.0, "s": rng.choice((0, 1)), "t": ts + j, "Ma": 9 if own else 5,
                               "Ta": 120})
            t.observe(3, prints, 9, 0.25, ts)
            t.release(ts)
        return t

    rng.seed(7)
    base = feed(fm.RealizedTracker())
    rng.seed(7)
    cap = kp.CaptureTracker(weight=0.005)
    cap.rates = {3: 0.004}
    feed(cap)
    assert list(base.window.entries) == list(cap.window.entries) and base.counts == cap.counts
    assert base.estimate() == cap.estimate()
    assert len(cap.k3q) == len(cap.window.entries) > 0
    r = sum(e[1] for e in cap.k3q)
    assert cap.sums()[0] == pytest.approx(r)
    fees = sum(0.005 * 0.004 * n for _ts, _r, n in cap.window.entries)
    assert cap.sums()[0] == pytest.approx(sum(e[1] for e in cap.window.entries) - fees)


# ---- the agent: t634's harness (the real v6.3 pass with v6.17 / v6.33 / v6.34) with layer 3's real code --------------

import research_v6_37_fast_path  # noqa: E402,F401  (a module Simple imports; its names resolve below)


def _resolve(names):
    """Each name Simple imports from a module, resolved on the real module (only the names layer 3 needs)."""
    out = {}
    tree = ast.parse(SIMPLE)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                alias = a.asname or a.name
                if alias in names and alias not in out:
                    try:
                        mod = __import__(node.module, fromlist=[a.name])
                        out[alias] = getattr(mod, a.name)
                    except Exception:
                        pass
    return out


def _layer3_names():
    """Every global name layer 3's methods and hooks read (its modules' aliases and Simple's own imports)."""
    want = set()
    cls = next(n for n in ast.parse(SIMPLE).body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    for fn in cls.body:
        if isinstance(fn, ast.FunctionDef):
            for n in ast.walk(fn):
                if isinstance(n, ast.Name):
                    want.add(n.id)
    return _resolve(want)


NAMES = _layer3_names()
HOOKED = ("_v617_touch", "_v633_book", "_v6_25_lean", "_v63_pass", "_v6_34_entry")
METHODS = tuple(n.name for n in next(c for c in ast.parse(SIMPLE).body if isinstance(c, ast.ClassDef) and
                                      c.name == "Strategy1_Research_Simple").body
                if isinstance(n, ast.FunctionDef) and n.name.startswith("_v6_38_"))
_CLASSES: dict = {}


def _merged(harness_globals):
    """An older harness's own globals plus every layer-3 name it lacks: NAMES fills in and never replaces, so the harness's
    own objects (e.g. t634's string stand-in for OrderDirection) stay on every interpreter -- whichever of Simple's
    imports the interpreter's packages can resolve."""
    scope = dict(harness_globals)
    for k, v in NAMES.items():
        scope.setdefault(k, v)
    return scope


def _rebind(fn, scope):
    f = types.FunctionType(fn.__code__, scope, fn.__name__, fn.__defaults__, fn.__closure__)
    f.__kwdefaults__ = fn.__kwdefaults__
    return f


def _klass(base):
    if base not in _CLASSES:
        scope = _merged(getattr(base, "_v63_pass").__globals__)
        methods = {}
        for n in METHODS:
            ns = dict(scope)
            exec(compile(ast.Module(body=[ast.parse(_src(n)).body[0]], type_ignores=[]), f"<{n}>", "exec"), ns)
            methods[n] = ns[n]
        for n in HOOKED:
            fn = getattr(base, n)
            methods[n] = _rebind(fn, _merged(fn.__globals__))
        if not hasattr(base, "_get"):                    # Strategy1_Debug's staticmethod, as the live class chain has it
            dbg = extractor(read_source(STRATEGY / "Strategy1_Debug.py"), cls_name="Strategy1_Debug")
            ns = dict(scope)
            exec(compile(ast.Module(body=[ast.parse(dbg("_get")).body[0]], type_ignores=[]), "<_get>", "exec"), ns)
            methods["_get"] = staticmethod(ns["_get"])
        _CLASSES[base] = type("L638", (base,), methods)
    return _CLASSES[base]


@__import__("functools").lru_cache(maxsize=None)
def _init_source():
    """The layer-3 statements of _init_build_switches -- the real ones -- as one function of self."""
    fn = ast.parse(_src("_init_build_switches")).body[0]
    body = [st for st in fn.body if _mentions638(st)]
    assert len(body) == 21, [ast.unparse(st) for st in body]
    return "def _init638(self):\n" + textwrap.indent("\n".join(ast.unparse(st) for st in body), "    ") + "\n"


def _hook(method, nth):
    """respond()'s nth layer-3 statement as a function of (self, *args)."""
    fn = ast.parse(_src(method)).body[0]
    hooks = [st for st in fn.body if isinstance(st, ast.If) and _mentions638(st.test)]
    return hooks[nth]


def _l3(agent, **switches):
    """``agent`` (an older suite's harness agent) with layer 3's real methods, the real methods its hooks sit in and
    the state its real init statements leave for ``switches`` (absent: 0)."""
    agent.__class__ = _klass(type(agent))
    cfg = dict(vars(getattr(agent, "config", None) or types.SimpleNamespace()))
    cfg.update(switches)
    agent.config = types.SimpleNamespace(**cfg)
    ns = dict(NAMES)
    exec(compile(_init_source(), "<v6.38 init>", "exec"), ns)
    ns["_init638"](agent)
    return agent


_CACHED: list = []


def _t634():
    """t634's harness module -- its chain's statement extractors read Simple's static source: memoized once here (this
    file's process only), so an agent costs milliseconds."""
    import test_research_v6_34 as t634
    if not _CACHED:
        import functools
        for mod in list(sys.modules.values()):
            if getattr(mod, "__name__", "").startswith("test_research_"):
                for f in ("_init_source", "_identity_source", "_hook_source"):
                    fn = getattr(mod, f, None)
                    if callable(fn) and not hasattr(fn, "cache_info"):
                        setattr(mod, f, functools.lru_cache(maxsize=None)(fn))
        _CACHED.append(True)
    return t634


def _agent(harness=None, **switches):
    t634 = _t634()
    return _l3(t634._agent(**(harness or {})), **switches)


def _below():
    """t631's page with the fundamental 42 bps BELOW book 3's mid (v6.36's touch quote: the toward-fp sell)."""
    import test_research_v6_31 as t631
    return {"page": t631.BELOW_PAGE}


def _near():
    """A page with book 3's fundamental 0.1 bps above its mid 100.015: no lean (inside the 2.3-bps gap) and no v6.36
    touch quote (the join's edge is below the hurdle on both sides)."""
    import test_research_v6_29 as t629
    return {"page": t629._page(100.015 * math.exp(1e-5))}


def _request(agent, **kw):
    """t634's request (v6.26 hook, v6.23 observation, v6.34 / v6.32 statements, the pass) with layer 3's request
    statement right after them, as respond() orders it (v6.36's request in between is not on this harness: the KBL
    window is set by the test where it needs one)."""
    t634 = _t634()
    win = kw.pop("window", None)
    hook = kw.pop("hook", None)
    orig = type(agent)._v63_pass

    def _pass(self, resp, state, stats, _orig=orig):
        self._v6_38_l3_request(state)
        if win is not None:
            self._v6_38_kbl_win = dict(win)
        if hook is not None:
            hook(self)
        return _orig(self, resp, state, stats)
    type(agent)._v63_pass = _pass
    try:
        return t634._request(agent, **kw)
    finally:
        type(agent)._v63_pass = orig


def test_the_init_statements_set_every_switch_and_each_ships_0():
    a = _agent()
    assert (a.research_v638_kbl, a.research_v638_lean, a.research_v638_lean_arm, a.research_v638_c1,
            a.research_v638_c1_arm) == (0, 0, 0, 0, 0)
    assert a._v6_38_l3_on is False and a._v6_38_l3_counts == {} and a._v6_38_canary_tables == {}
    b = _agent(research_v638_kbl=1.0, research_v638_lean="1", research_v638_lean_arm=2.0, research_v638_c1=2,
               research_v638_c1_arm=3)
    assert (b.research_v638_kbl, b.research_v638_lean, b.research_v638_lean_arm, b.research_v638_c1,
            b.research_v638_c1_arm) == (1, 1, 2, 2, 3)
    bad = _agent(research_v638_kbl=7, research_v638_lean=9, research_v638_lean_arm=6, research_v638_c1=-1,
                 research_v638_c1_arm="K3")
    assert (bad.research_v638_kbl, bad.research_v638_lean, bad.research_v638_lean_arm, bad.research_v638_c1,
            bad.research_v638_c1_arm) == (0, 0, 0, 0, 0)


def _strip_volatile(rows):
    return [(e, {k: v for k, v in kw.items() if k != "ms" and not k.endswith("_ms") and "_ms_" not in k})
            for e, kw in rows]


def test_every_switch_off_is_v637_request_for_request():
    t634 = _t634()
    ref, a = t634._agent(), _agent()
    for k in range(3):
        r0, r1 = t634._request(ref, ts=t634.NOW + k * S), _request(a, ts=t634.NOW + k * S)
        assert repr(r0.instructions) == repr(r1.instructions)
    assert _strip_volatile(ref.emitted) == _strip_volatile(a.emitted)
    assert a._v6_38_l3_on is False and a._v6_38_l3_counts == {} and not [e for e, _ in a.emitted if "V6_38" in e]


BALANCED = types.SimpleNamespace(book_capture=lambda b: (0.0, 0.0))        # xb keeps the window on a balanced book


def test_kbl_window_quote_takes_the_steps_side_without_the_hurdle_improving_under_s12b():
    t634 = _t634()
    for direction, side, price in ((1, "BUY", 100.01), (-1, "SELL", 100.02)):
        ref = _agent(_near(), research_v638_kbl=1)
        ref._v62_mirror = BALANCED
        assert t634._placed(_request(ref), 3) == []         # no window: the hurdle keeps v6.36's quote off both sides
        a = _agent(_near(), research_v638_kbl=1)
        a._v62_mirror = BALANCED
        r = _request(a, window={3: direction})
        got = t634._placed(r, 3)
        assert [(x[1], x[3]) for x in got] == [(side, price)], got        # S12b's improve, no fp-edge hurdle
        assert got[0][2] == tq.client_ids(3)[0 if side == "BUY" else 1] and got[0][4] == pytest.approx(2.0)
        assert a._v6_38_l3_counts.get("kbl_window_book_states") == 1
    off = _agent(_near())                                    # the switch off: a window is never read
    assert t634._placed(_request(off, window={3: 1}), 3) == []


def test_the_window_quote_keeps_every_other_gate_the_lean_bound_included():
    t634 = _t634()
    a = _agent(_below(), research_v638_kbl=1)                # fp 42 bps below: tau ~ -18 base, S7's one-clip band
    a._v62_mirror = BALANCED
    r = _request(a, window={3: 1})                           # the step's buy would add past the band: refused
    assert t634._placed(r, 3) == [] and a._v617_counts.get("bound_states") == 1
    assert a._v6_38_l3_counts.get("kbl_window_book_states") == 1


def test_kbl_xb_withholds_the_window_on_the_capture_surplus_side_and_k5_does_not():
    t634 = _t634()
    mirror = types.SimpleNamespace(book_capture=lambda b: (9.0, 1.0) if int(b) == 3 else (0.0, 0.0))
    a = _agent(_near(), research_v638_kbl=1)
    a._v62_mirror = mirror
    r = _request(a, window={3: 1})                      # buy is book 3's capture-surplus side: no window (v6.36: none)
    assert t634._placed(r, 3) == [] and a._v6_38_l3_counts.get("kbl_xb_withheld") == 1
    k5 = _agent(_near(), research_v638_kbl=2)            # K5 carries no xb
    k5._v62_mirror = mirror
    r5 = _request(k5, window={3: 1})
    assert [x[1] for x in t634._placed(r5, 3)] == ["BUY"]


def test_cd_runs_kbl_through_the_lean_arm_on_its_canary_books_only():
    """CD = KBL + CB (k116 PREREG section 3): with research_v638_kbl=0 the lean arm CD alone runs KBL -- the window quote
    and X1's touch-first post -- on the books its canary draws (UID 9: book 3 on, book 4 off); CB carries no KBL."""
    t634 = _t634()
    for mode in (2, 1):
        for direction, side in ((1, "BUY"), (-1, "SELL")):
            a = _agent(_near(), research_v638_lean=mode, research_v638_lean_arm=4)
            a._v62_mirror = BALANCED
            got = t634._placed(_request(a, window={3: direction}), 3)
            assert [x[1] for x in got] == [side], (mode, got)
            assert a._v6_38_l3_counts.get("kbl_window_book_states") == 1
    half = _agent(_near(), research_v638_lean=1, research_v638_lean_arm=4)
    _request(half)
    assert [half._v6_38_kbl_mode(b) for b in range(12)] == [cn.draw(cn.TAG_K116, "CD", 9, b) for b in range(12)]
    assert half._v6_38_kbl_mode(3) == 1 and half._v6_38_kbl_mode(4) == 0
    cb = _agent(_near(), research_v638_lean=2, research_v638_lean_arm=2)
    cb._v62_mirror = BALANCED
    assert t634._placed(_request(cb, window={3: 1}), 3) == [] and cb._v6_38_kbl_mode(3) == 0
    ix = [types.SimpleNamespace(type="CANCEL_ORDERS", bookId=3, clientOrderId=None),
          types.SimpleNamespace(type="PLACE_ORDER_LIMIT", bookId=3, clientOrderId=tq.client_ids(3)[1])]
    for agent, want in ((_agent(research_v638_lean=2, research_v638_lean_arm=4), [ix[1], ix[0]]),
                        (_agent(research_v638_lean=2, research_v638_lean_arm=2), ix)):
        agent._v6_38_l3_on = True
        resp = types.SimpleNamespace(instructions=list(ix))
        agent._v6_38_l3_post(resp, None)
        assert resp.instructions == want


def test_the_request_opens_kbls_window_from_v636s_step_record_for_kbl_and_cd_alone():
    """_v6_38_l3_request on t636's harness, right after v6.36's real request: a step v6.36's record saw at the first state
    of block n opens [30n, 30n + 10 s) with each book's step direction -- for KBL and for the lean arm CD alone (KBL on its
    books); CB opens none; without v6.36's record (research_v636_step_withdrawal off) no window, counted."""
    import test_research_v6_36 as t636
    scope = _merged(t636.EXTRA)
    methods = {}
    for n in METHODS:
        ns = dict(scope)
        exec(compile(ast.Module(body=[ast.parse(_src(n)).body[0]], type_ignores=[]), f"<{n}>", "exec"), ns)
        methods[n] = ns[n]
    klass = type("T638Request", (t636._klass(),), methods)

    def run(on, **switches):
        a = t636._agent(on=on)
        a.__class__ = klass
        a.config = types.SimpleNamespace(research_v636_step_withdrawal=on, **switches)
        ns = dict(NAMES)
        exec(compile(_init_source(), "<v6.38 init>", "exec"), ns)
        ns["_init638"](a)
        seen = []
        for blk, ph, f in ((100, 29, 100.0), (101, 0, 100.05), (101, 9, 100.05), (101, 10, 100.05), (102, 0, 99.9)):
            st = t636._request(a, (30 * blk + ph) * S, {3: t636._raw(99.99, 100.01, f)})
            a._v6_38_l3_request(st)
            seen.append(None if a._v6_38_kbl_win is None else a._v6_38_kbl_win.get(3, 0))
        return a, seen
    for switches in (dict(research_v638_kbl=1), dict(research_v638_lean=2, research_v638_lean_arm=4)):
        a, seen = run(True, **switches)
        assert seen == [None, 1, 1, None, -1], (switches, seen)
        assert a._v6_38_l3_counts.get("kbl_window_states") == 3
    a, seen = run(True, research_v638_lean=2, research_v638_lean_arm=2)
    assert seen == [None] * 5 and not a._v6_38_l3_counts.get("kbl_window_states")
    a, seen = run(False, research_v638_kbl=1)
    assert seen == [None] * 5 and a._v6_38_l3_counts.get("kbl_no_step_record") == 5


def test_with_the_switch_off_a_window_changes_nothing():
    t634 = _t634()
    ref, a = t634._agent(), _agent()
    r0, r1 = t634._request(ref), _request(a, window={3: -1})
    assert repr(r0.instructions) == repr(r1.instructions)


# ---- wiring ----------------------------------------------------------------------------------------------------------

def test_every_v637_statement_is_in_layer_3_unchanged_and_layer_3_only_adds():
    stripped = _Drop638().visit(ast.parse(SIMPLE))
    assert not _mentions638(stripped) and _mentions638(ast.parse(SIMPLE))
    try:
        import test_research_v6_37 as t637
    except Exception:
        t637 = None
    if t637 is not None and hasattr(t637, "_Drop637"):
        try:
            out = subprocess.run(["git", "-c", "safe.directory=*", "show",
                                  "6171a57:agents/strategy/Strategy1_Research_Simple.py"], cwd=ROOT,
                                 capture_output=True, text=True, timeout=120,
                                 env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
        except Exception:
            out = None
        if out is not None and out.returncode == 0:
            assert ast.dump(t637._Drop637().visit(stripped)) == ast.dump(ast.parse(out.stdout))


def test_the_hooks_are_inert_without_layer_3s_state():
    for method in HOOKED + ("respond", "_a191_service_reprice_cancels", "_v6214_service_touch_life",
                            "_direct_service_partial_fill_recovery"):
        fn = ast.parse(_src(method)).body[0]
        guarded = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.If) and _mentions638(n.test):
                guarded.update(id(x) for x in ast.walk(n) if x is not n)
        for node in ast.walk(fn):
            for field in ("body", "orelse", "finalbody"):
                stmts = getattr(node, field, None)
                for st in stmts if isinstance(stmts, list) else []:
                    if id(st) in guarded:
                        continue                         # inside a layer-3 guard: runs only when the guard holds
                    simple = isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr))
                    if not (simple or (isinstance(st, ast.If) and _mentions638(st.test))) or not _mentions638(st):
                        continue
                    text = ast.unparse(st)
                    if isinstance(st, ast.If):
                        test = ast.unparse(st.test)
                        assert ("getattr(self, '_v6_38_l3_on', False)" in test or
                                "getattr(self, '_v6_38_l3_counts', None) is not None" in test or
                                test.startswith(("v6_38_win is not None", "v6_38_keep is not None",
                                                 "v6_38_ladder and", "v6_38_q is not None", "v6_38_ids"))), \
                            (method, text[:200])
                    else:
                        assert text.startswith(("v6_38_win = None", "v6_38_ladder: frozenset = frozenset()",
                                                "v6_38_keep = None")) or "self._v6_38_" in text, (method, text[:200])


# ---- the launcher ----------------------------------------------------------------------------------------------------

# The lines a layer-3 build must carry -- this file's own list, independent of the launcher's: the launcher's block must
# refuse a tree without any one of them (test_the_layer3_block_refuses_a_tree_without_each_line_it_checks).
L3_SIMPLE_LINES = (
    'from research_v6_38_canary import (', 'from research_v6_38_kbl import (', 'from research_v6_38_lean import (',
    'from research_v6_38_keeper import (',
    'self.research_v638_kbl = v6_38_kbl_mode_of(getattr(self.config, "research_v638_kbl", 0))',
    'self.research_v638_lean = v6_38_mode_of(getattr(self.config, "research_v638_lean", 0))',
    'self.research_v638_lean_arm = v6_38_arm_of(getattr(self.config, "research_v638_lean_arm", 0), V6_38_LEAN_ARMS)',
    'self.research_v638_c1 = v6_38_mode_of(getattr(self.config, "research_v638_c1", 0))',
    'self.research_v638_c1_arm = v6_38_arm_of(getattr(self.config, "research_v638_c1_arm", 0), V6_38_C1_ARMS)',
    'self._v6_38_l3_request(state)', 'self._v6_38_l3_post(response, state)',
    'v6_38_win = self._v6_38_kbl_window(book_id)', 'v6_38_ladder = self._v6_38_kbl_ladder(book_id, levels_side)',
    'self._v6_38_lean_note(book_id, depth, raw_bid, raw_ask, cfg)', 'v6_34_c1 = self._v6_38_k1_books(v6_34_c1)')
L3_CANARY_LINES = ('TAG_K116 = "orthox-profile-k116-v1"', 'TAG_K118 = "orthox-profile-k118-v1"',
                   'h = hashlib.sha256(f"{tag}|{dim}|{int(uid)}|{int(book)}".encode()).hexdigest()',
                   'return int(h[:8], 16) % K')


def _params():
    start = LAUNCHER.index('PARAMS="')
    return LAUNCHER[start + len('PARAMS="'):LAUNCHER.index('"', start + len('PARAMS="'))].replace("\\\n", " ")


def _block():
    start = LAUNCHER.index("# v6.38 L3.")
    stop = LAUNCHER.index('echo "[preflight] v6.38 L3 PASS', start)
    return LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]


def _patterns():
    lists = re.findall(r"for _v638l3_pat in \\\n((?:      '[^']+' \\\n)*      '[^']+'); do", _block())
    assert len(lists) == 2, lists
    return [re.findall(r"'([^']+)'", x) for x in lists]


def _run_block(params, agent_path=strategy_view(), build="1"):
    script = "set -euo pipefail\n" + _block()
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "SCRIPT_DIR": str(ROOT), "V6_38_BUILD": build}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=120)


def _with(params, **kv):
    out = " %s " % params
    for k, v in kv.items():
        out = re.sub(r" %s=\S+ " % k, " %s=%s " % (k, v), out)
    return out.strip()


def test_the_layer3_block_is_gated_listed_and_ships_every_switch_0():
    assert LAUNCHER.count("; V6_36_BUILD=0; V6_37_BUILD=0; V6_38_BUILD=0\n") == 1
    arm = next(x for x in LAUNCHER.splitlines() if x.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_36_BUILD=1; V6_37_BUILD=1; V6_38_BUILD=1"), arm
    params = _params().split()
    # k126: layer 3's five keys follow layer 2's research_v638_fast_path=1, its gate entry layer 2's test file
    # k129: v6.39 ships CC OFF (the user, 10-23) -- every layer-3 switch 0, as in M-FROZEN_1
    assert params[-7:-1] == ["research_v638_fast_path=1", "research_v638_kbl=0", "research_v638_lean=0",
                             "research_v638_lean_arm=0", "research_v638_c1=0", "research_v638_c1_arm=0"], params[-7:-1]
    assert "      tests/test_research_v6_38.py \\\n      tests/test_research_v6_38_l3.py \\\n" in LAUNCHER
    assert LAUNCHER.count("# v6.38 L3.") == 1 and LAUNCHER.count('[[ "${V6_38_BUILD:-0}" == "1" ]]') >= 1
    assert LAUNCHER.index('echo "[preflight] v6.37 fast path PASS') < LAUNCHER.index("# v6.38 L3.") < \
        LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')
    head = _block()[:_block().index('if [[ "${V6_38_BUILD:-0}" == "1" ]]; then')]
    for needle in ("EVERY switch shipped 0", "v6.37", "KBL", "K5", "CA", "CB", "CC", "CD", "K1", "K3B", "K3Q",
                   "hash half", "research_v6_38_canary", "research_v634_class1_entry=1",
                   "research_v636_step_withdrawal=1", "the user's call"):
        assert needle in head, needle


def test_every_layer3_preflight_pattern_is_in_the_file_it_checks():
    agent_pats, canary_pats = _patterns()
    for p in agent_pats:
        assert SIMPLE.count(p) == 1, p
    canary = read_source(STRATEGY / "research_v6_38_canary.py")
    for p in canary_pats:
        assert canary.count(p) == 1, p
    for m in MODULES:
        assert '"$AGENT_PATH/$_v638l3_mod"' in _block() and m + ".py" in _block(), m


def test_the_layer3_block_passes_the_shipped_params_and_refuses_what_it_cannot_ship():
    shipped = _params()
    out = _run_block(shipped)
    assert out.returncode == 0 and out.stderr == "", out.stderr
    # k129: v6.39 ships CC OFF (the user, 10-23): every layer-3 switch ships 0, as in M-FROZEN_1
    off = shipped
    assert out.stdout.splitlines() == ["[preflight] v6.38 L3 KBL: off; lean: off; class-1 keeper: off",
                                       "[preflight] v6.38 L3 PASS (every layer-3 switch explicit; off: v6.37 exactly)"]
    ok = {(("research_v638_kbl", 1),): "KBL: KBL; lean: off; class-1 keeper: off",
          (("research_v638_kbl", 2),): "KBL: K5; lean: off; class-1 keeper: off",
          (("research_v638_lean", 1), ("research_v638_lean_arm", 2)): "KBL: off; lean: CB on the hash half;",
          (("research_v638_lean", 2), ("research_v638_lean_arm", 4)): "lean: CD on every book;",
          (("research_v638_c1", 1), ("research_v638_c1_arm", 3), ("research_v634_class1_entry", 1)):
              "class-1 keeper: K3B on the hash half"}
    for kv, want in ok.items():
        out = _run_block(_with(off, **dict(kv)))
        assert out.returncode == 0 and want in out.stdout, (kv, out.stdout, out.stderr)
    bad = [dict(research_v638_kbl=3), dict(research_v638_lean=1), dict(research_v638_lean_arm=1),
           dict(research_v638_c1=1, research_v638_c1_arm=1),                           # no class-1 entry
           dict(research_v638_kbl=1, research_v636_step_withdrawal=0),
           dict(research_v638_lean=1, research_v638_lean_arm=4, research_v636_step_withdrawal=0),
           dict(research_v638_kbl=2, research_v638_lean=1, research_v638_lean_arm=4),
           dict(research_v638_c1_arm=5), dict(research_v638_lean="x")]
    for kv in bad:
        out = _run_block(_with(off, **kv))
        assert out.returncode == 1 and "ERROR: v6.38 L3" in out.stderr and "PASS" not in out.stdout, (kv, out.stdout)
    for key in KEYS:
        out = _run_block(" ".join(t for t in shipped.split() if not t.startswith(key + "=")))
        assert out.returncode == 1 and "given 0 times" in out.stderr, key
        out = _run_block(shipped + " %s=0" % key)
        assert out.returncode == 1 and "given 2 times" in out.stderr, key
    out = _run_block("", build="0")
    assert out.returncode == 0 and out.stdout == "" and out.stderr == ""


def test_the_v63_arm_arms_layer_3s_build_flag_and_no_older_arm_does():
    """The launcher's own build-flag defaults and candidate case block, run: this build's POLICY_VER
    (strategy1_direct_v6_3_0) sets V6_38_BUILD=1 -- the "# v6.38 L3." block runs at every launch of this build -- and an
    older candidate's arm leaves it 0."""
    start = LAUNCHER.index("A19X_BUILD=0; A195_BUILD=0;")
    stop = LAUNCHER.index("\nesac\n", start) + len("\nesac\n")
    script = "set -euo pipefail\nINHERITED_SHORT_LOTS_SOURCE=default\n" + LAUNCHER[start:stop] + 'echo "$V6_38_BUILD"\n'
    for ver, want in (("strategy1_direct_v6_3_0", "1"), ("strategy1_direct_v6_2_15", "0")):
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
                             env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "POLICY_VER": ver})
        assert out.returncode == 0 and out.stdout.strip() == want, (ver, out.stdout, out.stderr)


def test_the_preflight_gate_runs_layer_3s_tests_after_v637s(tmp_path):
    """The launcher's preflight gate itself, run with a stub python that records each call: it compiles the agent and
    runs the repo runner on its list, tests/test_research_v6_38_l3.py once, after tests/test_research_v6_37.py (and
    after layer 2's tests/test_research_v6_38.py when the gate lists it)."""
    start = LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')
    gate = LAUNCHER[start:LAUNCHER.index("\nfi\n", start) + len("\nfi\n")]
    log = tmp_path / "calls"
    stub = tmp_path / "python"
    stub.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$STUB_LOG"\n')
    stub.chmod(0o755)
    env = {"PATH": "%s:%s" % (tmp_path, os.environ.get("PATH", "/usr/bin:/bin")), "STUB_LOG": str(log),
           "RESEARCH_PREFLIGHT_ONLY": "1", "AGENT_PATH": str(STRATEGY), "SCRIPT_DIR": str(ROOT), "POLICY_VER": "v"}
    out = subprocess.run(["bash", "-c", "set -euo pipefail\n" + gate], capture_output=True, text=True, env=env,
                         timeout=60)
    assert out.returncode == 0 and "preflight PASS" in out.stdout, (out.stdout, out.stderr)
    calls = [c.split() for c in log.read_text().splitlines()]
    runner = str(ROOT / "tests" / "run_tests.py")
    runs = [c[c.index(runner) + 1:] for c in calls if runner in c]
    assert len(calls) == 2 and calls[0][:2] == ["-m", "py_compile"] and len(runs) == 1, calls
    files = runs[0]
    assert files.count("tests/test_research_v6_38_l3.py") == 1, files
    after = [f for f in ("tests/test_research_v6_37.py", "tests/test_research_v6_38.py") if f in files]
    assert after and after[0] == "tests/test_research_v6_37.py", files
    assert all(files.index("tests/test_research_v6_38_l3.py") > files.index(f) for f in after), files


def test_the_layer3_block_refuses_a_tree_without_each_line_it_checks(tmp_path):
    import shutil
    agent_pats, canary_pats = _patterns()
    agent_pats = list(dict.fromkeys(list(agent_pats) + list(L3_SIMPLE_LINES)))
    canary_pats = list(dict.fromkeys(list(canary_pats) + list(L3_CANARY_LINES)))
    cases = [(p, "Strategy1_Research_Simple.py") for p in agent_pats] + \
        [(p, "research_v6_38_canary.py") for p in canary_pats]
    names = ["Strategy1_Research_Simple.py"] + [m + ".py" for m in MODULES]
    for i, (needle, name) in enumerate(cases):
        tmp = tmp_path / str(i)
        tmp.mkdir()
        for base in names:
            copy_source(STRATEGY / base, tmp / base)
        path = tmp / name
        path.write_text(path.read_text().replace(needle, "pass  # removed", 1))
        assert path.read_text().count("pass  # removed") >= 1, needle               # the line was there to remove
        out = _run_block(_params(), agent_path=tmp)
        assert out.returncode == 1 and needle in out.stderr and "PASS" not in out.stdout, (needle, out.stderr)
    for m in MODULES:
        bare = tmp_path / ("no_" + m)
        bare.mkdir()
        for base in names:
            if base != m + ".py":
                copy_source(STRATEGY / base, bare / base)
        out = _run_block(_params(), agent_path=bare)
        assert out.returncode == 1 and "is missing" in out.stderr, m


# ---- the post-passes: KBL X1 (a) and the keeper's exemptions ----------------------------------------------------------

L3_STATE = ("_v6_38_l3_count", "_v6_38_kbl_mode", "_v6_38_l3_canary", "_v6_38_kbl_x1", "_v6_38_kbl_touch_ids",
            "_v6_38_k3q_trackers", "_v6_38_k1_life")


def _attach(agent, passes, **switches):
    """``agent`` with layer 3's real methods (L3_STATE), the post-passes ``passes`` rebound with layer 3's names in their
    globals, and layer 3's init state for ``switches`` -- the request hook's flag set (_v6_38_l3_on)."""
    cls = type(agent)
    methods = {}
    for n in L3_STATE:
        ns = dict(NAMES)
        exec(compile(ast.Module(body=[ast.parse(_src(n)).body[0]], type_ignores=[]), f"<{n}>", "exec"), ns)
        methods[n] = ns[n]
    for n in passes:
        fn = getattr(cls, n)
        methods[n] = _rebind(fn, _merged(fn.__globals__))
    agent.__class__ = type("A638", (cls,), methods)
    agent.config = types.SimpleNamespace(**switches)
    ns = dict(NAMES)
    exec(compile(_init_source(), "<v6.38 init>", "exec"), ns)
    ns["_init638"](agent)
    agent._v6_38_l3_on = bool(switches)
    return agent


def test_kbl_x1_spares_the_touch_quote_from_the_exit_reprice_and_off_it_is_repriced():
    import test_research_v6_3_3_1_deep_life as t6331
    import test_research_strategy1_direct_a1_9_1_1 as a1911
    touch_sell = tq.client_ids(a1911.BOOK)[1]
    for on, cancels in ((True, []), (False, [(a1911.BOOK, [777])])):
        a = t6331._held_long(touch_sell)
        a = _attach(a, ("_a191_service_reprice_cancels",), **({"research_v638_kbl": 1} if on else {}))
        r = a1911._Response()
        a._a191_service_reprice_cancels(r, a1911._state())
        assert r.cancels == cancels, (on, r.cancels)
        assert a._v6_38_l3_counts == ({"kbl_x1_kept_reprice": 1} if on else {})
    a = _attach(t6331._held_long(None), ("_a191_service_reprice_cancels",), research_v638_kbl=1)
    r = a1911._Response()
    a._a191_service_reprice_cancels(r, a1911._state())
    assert r.cancels == [(a1911.BOOK, [777])]                       # not the touch quote: repriced as before


def test_the_keeper_keeps_only_the_size_shortfall_reprice_and_the_touch_life_spares_it():
    import test_research_v6_3_3_1_deep_life as t6331
    import test_research_strategy1_direct_a1_9_1_1 as a1911
    touch_sell = tq.client_ids(a1911.BOOK)[1]
    a = _attach(t6331._held_long(touch_sell), ("_a191_service_reprice_cancels",), research_v638_c1=2,
                research_v638_c1_arm=1)
    a._v6_38_k1_books_now = frozenset({a1911.BOOK})
    r = a1911._Response()
    a._a191_service_reprice_cancels(r, a1911._state())
    assert r.cancels == [] and a._v6_38_l3_counts == {"k1_kept_reprice": 1}     # stale behind the touch: kept
    a2 = _attach(t6331._held_long(touch_sell), ("_a191_service_reprice_cancels",), research_v638_c1=2,
                 research_v638_c1_arm=1)
    a2._v6_38_k1_books_now = frozenset({a1911.BOOK})
    a2.positions[a1911.BOOK] = (5.0, 100.00)                             # 0.5 resting vs 5.0 held: size shortfall
    r2 = a1911._Response()
    a2._a191_service_reprice_cancels(r2, a1911._state())
    assert r2.cancels == [(a1911.BOOK, [777])] and "k1_kept_reprice" not in a2._v6_38_l3_counts
    # v6.2.14's touch life: a keeper resting behind the touch stays; another touch-slot order there goes
    import test_research_v6_2_14_touch_life as tlt
    life = t6331._life()
    life = _attach(life, ("_v6214_service_touch_life",), research_v638_c1=2, research_v638_c1_arm=1)
    life._v6_38_k1_books_now = frozenset({3})
    tlt._rest(life, 31, 3, 0, 99.95, cid=tq.client_ids(3)[0], qty=1.0)
    resp = tlt._Resp()
    life._v6214_service_touch_life(resp, tlt._state({3: tlt._touch_book()}))
    assert tlt._cancelled(resp) == set() and life._v6_38_l3_counts == {"k1_life_kept": 1}
    off = t6331._life()
    off = _attach(off, ("_v6214_service_touch_life",))
    tlt._rest(off, 31, 3, 0, 99.95, cid=tq.client_ids(3)[0], qty=1.0)
    resp = tlt._Resp()
    off._v6214_service_touch_life(resp, tlt._state({3: tlt._touch_book()}))
    assert tlt._cancelled(resp) == {(3, 31)}                           # off: v6.2.14 as before


def test_kbl_x1_spares_the_touch_quote_from_the_a173_remainder_cancel():
    import test_research_v6_3_3_2_deep_partial as t6332
    import test_research_v6_0_3_short_lot_release as t603
    touch = tq.client_ids(t6332.BOOK)[0]
    for on, cancels in ((True, [(t6332.BOOK, [2])]), (False, [(t6332.BOOK, [1, 2])])):
        agent = t6332._agent()
        agent = _attach(agent, ("_direct_service_partial_fill_recovery",), **({"research_v638_kbl": 1} if on else {}))
        t6332._dust_book(agent, [t6332._order(1, "buy", touch), t6332._order(2, "sell", t6332.V63_SELL)])
        response, _instructions, _holds = t603._run(agent)
        assert response.cancels == cancels, (on, response.cancels)
        assert agent._v6_38_l3_counts == ({"kbl_x1_kept_partial": 1} if on else {})


# ---- the agent: the lean candidates (piece 2) and the canary on the real pass ---------------------------------------

def _tau(agent):
    return agent._v6_25_taus[3][0]


def test_ca_leans_two_clips_per_hurdle_unit_on_its_canary_books_only():
    t634 = _t634()
    ref = t634._agent()
    t634._request(ref)
    base = _tau(ref)                                    # v6.36: fp 38 bps above the mid -> tau = min(48, 1.0 x gap / 2.3)
    assert 0 < base < 48
    every = _agent(research_v638_lean=2, research_v638_lean_arm=1)          # CA on every class-0 book
    _request(every)
    assert _tau(every) == pytest.approx(min(48.0, 2.0 * base))
    uid = every._v6_30_identity.uid
    assert uid == 9 and cn.draw(cn.TAG_K116, "CA", 9, 3) == 1 and cn.draw(cn.TAG_K116, "CB", 9, 3) == 0
    half = _agent(research_v638_lean=1, research_v638_lean_arm=1)           # CA on the hash half: book 3 drawn on
    _request(half)
    assert _tau(half) == pytest.approx(min(48.0, 2.0 * base))
    cb = _agent(research_v638_lean=1, research_v638_lean_arm=2)             # CB's draw for book 3 is 0: v6.36
    _request(cb)
    assert _tau(cb) == pytest.approx(base)
    tables = {k: t.snapshot() for k, t in half._v6_38_canary_tables.items()}
    assert tables[(cn.TAG_K116, "CA")]["table_uid"] == 9 and tables[(cn.TAG_K116, "CA")]["books"] >= 1
    off = _agent(research_v638_lean=1, research_v638_lean_arm=1)
    off._v6_30_identity.off = "uid_changed"                                # the profile off: mode 1 runs nowhere
    orig = type(off)._v6_30_request
    type(off)._v6_30_request = lambda self, state: setattr(self, "_v6_30_on", False)
    try:
        _request(off)
    finally:
        type(off)._v6_30_request = orig
    assert _tau(off) == pytest.approx(base)


def test_each_lean_arm_runs_its_pre_registered_parts_on_the_agent():
    """Every k116 arm on the agent (mode 2: every class-0 book), the same state, the previous state's FBL sign set: CA,
    CB and CD lean two clips per hurdle unit (slope 2) and CC (FBL alone) does not; CB, CC and CD grow the touch quote
    to the lean shortfall (tau - inv, inv 0 here) and CA does not (v6.36's clip)."""
    t634 = _t634()
    ref = t634._agent()
    t634._request(ref)
    base = _tau(ref)
    assert 0 < base < 24
    for arm, slope2, fbl in ((1, True, False), (2, True, True), (3, False, True), (4, True, True)):
        a = _agent(research_v638_lean=2, research_v638_lean_arm=arm)
        a._v62_mirror = BALANCED
        a._v6_38_fbl_sign = {3: 1}                                           # the previous state left the sign set
        r = _request(a)
        tau = _tau(a)
        assert tau == pytest.approx(2.0 * base if slope2 else base), (arm, tau, base)
        q = [x[4] for x in t634._placed(r, 3) if tq.is_touch_client_id(x[2])]
        assert q == [pytest.approx(min(48.0, tau), abs=1e-4) if fbl else pytest.approx(2.0)], (arm, q, tau)


def test_fbl_grows_the_touch_quote_to_the_lean_shortfall_with_the_previous_states_sign():
    t634 = _t634()
    a = _agent(research_v638_lean=2, research_v638_lean_arm=3)              # CC = FBL alone, every class-0 book
    a._v62_mirror = BALANCED
    r1 = _request(a)                                                         # first state: the sign before it is 0
    q1 = [x for x in t634._placed(r1, 3) if tq.is_touch_client_id(x[2])]
    assert q1 and q1[0][4] == pytest.approx(2.0), q1                         # the clip: no sign yet
    assert a._v6_38_fbl_sign.get(3) == 1                                     # fp 38 bps above: the sign set after it
    b = _agent(research_v638_lean=2, research_v638_lean_arm=3)
    b._v62_mirror = BALANCED
    b._v6_38_fbl_sign = {3: 1}                                               # the previous state left the sign set
    r2 = _request(b)
    q2 = [x for x in t634._placed(r2, 3) if tq.is_touch_client_id(x[2])]
    tau = _tau(b)
    assert q2 and q2[0][4] == pytest.approx(min(48.0, tau - 0.0), abs=1e-4), (q2, tau)   # the shortfall tau - inv
    assert b._v6_38_l3_counts.get("fbl_clip_up") == 1
    ref = t634._agent()
    r0 = t634._request(ref)
    assert [x[4] for x in t634._placed(r0, 3) if tq.is_touch_client_id(x[2])] == [pytest.approx(2.0)]


def test_respond_runs_layer_3s_request_and_post_statements_after_v636s():
    """respond()'s OWN two layer-3 statements, exec'd from Simple's source in place of the direct calls on t634's
    harness: the request statement (after v6.36's request) turns layer 3 on for the request with a switch on and leaves
    it off with every switch 0; the post statement (after v6.36's withdrawal cancels) then emits the V6_38_L3_STATE row
    with a switch on and nothing with every switch 0."""
    body = list(ast.parse(_src("respond")).body[0].body)
    hooks = [i for i, st in enumerate(body) if isinstance(st, ast.If) and _mentions638(st.test)]
    assert len(hooks) == 2, [ast.unparse(body[i].test) for i in hooks]
    before = lambda i, text: any(text in ast.unparse(st) for st in body[:i])
    assert before(hooks[0], "self._v6_36_request(state)") and before(hooks[1], "self._v6_36_cancel(response, state)")
    fns = []
    for i, args in zip(hooks, ("self, state", "self, response, state")):
        ns = dict(NAMES)
        exec(compile("def _f(" + args + "):\n" + textwrap.indent(ast.unparse(body[i]), "    ") + "\n", "<respond>", "exec"), ns)
        fns.append(ns["_f"])
    req, post = fns
    t634 = _t634()

    def run(agent):
        orig = type(agent)._v63_pass

        def _pass(self, resp, state, stats, _orig=orig):
            req(self, state)
            return _orig(self, resp, state, stats)
        type(agent)._v63_pass = _pass
        try:
            resp = t634._request(agent)
        finally:
            type(agent)._v63_pass = orig
        on = agent._v6_38_l3_on
        post(agent, resp, None)
        return on, [kw for e, kw in agent.emitted if e == "V6_38_L3_STATE"]
    on, rows = run(_agent(research_v638_kbl=1))
    assert on is True and len(rows) == 1 and rows[0]["kbl"] == 1 and rows[0]["errors"] == 0, (on, rows)
    on, rows = run(_agent(research_v638_lean=2, research_v638_lean_arm=1))
    assert on is True and len(rows) == 1 and rows[0]["lean_arm"] == "CA", (on, rows)
    on, rows = run(_agent())
    assert on is False and rows == [], (on, rows)


def test_the_layer3_row_comes_with_a_switch_on_and_never_with_every_switch_off():
    a = _agent(research_v638_kbl=1)
    _request(a)
    a._v6_38_l3_post(types.SimpleNamespace(instructions=[]), None)
    rows = [kw for e, kw in a.emitted if e == "V6_38_L3_STATE"]
    assert len(rows) == 1 and rows[0]["reason"] == "start" and rows[0]["kbl"] == 1 and rows[0]["errors"] == 0
    for field in ("versions", "lean", "lean_arm", "c1", "c1_arm", "profile_on", "canary_tables", "kbl_window",
                  "fbl_signed_books", "k1_books", "k1_pooled_radius_bps", "k3q", "counts"):
        assert field in rows[0], field
    a._v6_38_l3_post(types.SimpleNamespace(instructions=[]), None)
    assert len([1 for e, _kw in a.emitted if e == "V6_38_L3_STATE"]) == 1     # its cadence: start, errors, periodic
    off = _agent()
    _request(off)
    off._v6_38_l3_report()
    assert not [e for e, _kw in off.emitted if e == "V6_38_L3_STATE"]


# ---- the agent: the keepers (piece 3) on v6.34's class-1 entry -----------------------------------------------------

def _keeper(arm, *, rbps=3.8, move=None, cap=(0.0, 0.0), hold=False, inv=0.0, mode=2):
    """t634's agent with v6.34's class-1 entry on (book B_ON 30.00 / 30.03), the keeper's arm on ``mode``, v6.36's
    step record holding 30 steps of ``rbps`` on the book, S9's move side ``move`` (None / buy / sell), the book's
    capture ``cap``, K3Q's hold and the true inventory."""
    t634 = _t634()
    a = _agent(research_v638_c1=mode, research_v638_c1_arm=arm)
    a.research_v632_fee_making = False
    a.research_v636_step_withdrawal = True
    rec = sw.StepRecord()
    rec.steps[t634.B_ON] = deque([rbps] * 30, maxlen=360)
    a._v6_36_record = rec
    a._v6_36_grid = {"excluded": frozenset()}
    a._v62_mirror = types.SimpleNamespace(book_capture=lambda b, _c=cap: _c if int(b) == t634.B_ON else (0.0, 0.0))
    a.inv634 = {t634.B_ON: inv}
    held = {None: None, BUY: SELL, SELL: BUY}[move]
    a._v6_29_held = lambda book_id, fp, cfg, _h=held: (_h if int(book_id) == t634.B_ON else None)
    return a


def _hold(value):
    return lambda agent: setattr(agent, "_v6_38_k3q", {"hold": value})


def test_k1_keeps_both_sides_at_the_first_price_outside_the_a_radius_for_3600_s():
    t634 = _t634()
    for move in (None, BUY, SELL):                                       # S9 is not applied to K1
        a = _keeper(1, rbps=12.0, move=move)
        r = _request(a)
        got = t634._placed(r, t634.B_ON)
        t_buy, t_sell = kp.target(BUY, 30.00, 30.03, 12e-4, 0.01), kp.target(SELL, 30.00, 30.03, 12e-4, 0.01)
        assert (t_buy, t_sell) == (29.97, 30.06)                           # 12 bps of 30.015: the touch is inside
        assert [(x[1], x[3]) for x in got] == [("BUY", t_buy), ("SELL", t_sell)], (move, got)
        assert [x[4] for x in got] == [pytest.approx(t634.CLIP1)] * 2
        lives = [ix.expiryPeriod for ix in r.instructions if getattr(ix, "bookId", None) == t634.B_ON]
        assert lives == [3_600 * S] * 2                                   # the pre-registered life
        assert t634.B_ON in a._v6_38_k1_books_now and a._v6_38_l3_counts.get("k1_armed") == 2
    wide = _keeper(1, rbps=3.8)                                           # a radius inside the spread: T is the touch
    assert [(x[1], x[3]) for x in t634._placed(_request(wide), t634.B_ON)] == [("BUY", 30.00), ("SELL", 30.03)]
    c3f = _t634()._agent()
    r0 = _t634()._request(c3f)                                              # v6.34's C3f: the join at the touch
    assert [(x[1], x[3]) for x in t634._placed(r0, t634.B_ON)] == [("BUY", 30.00), ("SELL", 30.03)]


def test_k3_keeps_the_with_move_side_only_and_nothing_without_a_move():
    t634 = _t634()
    assert t634._placed(_request(_keeper(2)), t634.B_ON) == []                # no fresh move: neither side
    got = t634._placed(_request(_keeper(2, move=BUY)), t634.B_ON)
    assert [x[1] for x in got] == ["BUY"], got
    got = t634._placed(_request(_keeper(2, move=SELL)), t634.B_ON)
    assert [x[1] for x in got] == ["SELL"], got


def test_k3b_withholds_the_capture_surplus_side_and_k3q_the_adding_side_under_its_hold():
    t634 = _t634()
    assert t634._placed(_request(_keeper(3, move=BUY, cap=(9.0, 1.0))), t634.B_ON) == []     # buy is the surplus
    assert [x[1] for x in t634._placed(_request(_keeper(3, move=BUY, cap=(1.0, 9.0))), t634.B_ON)] == ["BUY"]
    held = _keeper(4, move=BUY)
    assert t634._placed(_request(held, hook=_hold(True)), t634.B_ON) == []                 # adding (flat): held
    long_ = _keeper(4, move=SELL, inv=30.0)                                                 # long: the sell reduces
    got = t634._placed(_request(long_, hook=_hold(True)), t634.B_ON)
    assert [x[1] for x in got] == ["SELL"] and got[0][4] == pytest.approx(30.0)          # max(clip, |inv|)
    free = _keeper(4, move=BUY)
    assert [x[1] for x in t634._placed(_request(free, hook=_hold(False)), t634.B_ON)] == ["BUY"]


def test_no_radius_no_keeper_and_the_canary_half_leaves_the_off_books_without_a_class1_quote():
    t634 = _t634()
    a = _keeper(1)
    a._v6_36_record = sw.StepRecord()                                       # no step yet: no radius
    assert t634._placed(_request(a), t634.B_ON) == []
    assert cn.draw(cn.TAG_K118, cn.DIM_C1, 9, t634.B_ON) == 1
    import test_research_v6_33 as t633
    b_off = next(b for b in range(96, 128) if cn.draw(cn.TAG_K118, cn.DIM_C1, 9, b) == 0 and t633._formula4(9, b) == 1)
    half = _keeper(1, mode=1)
    books = {t634.B_ON: t634._c1book(), b_off: t634._c1book()}
    r = _request(half, books=books)
    assert len(t634._placed(r, t634.B_ON)) == 2                            # B_ON drawn on for UID 9: the keeper
    assert t634._placed(r, b_off) == []                                    # drawn off: no class-1 quote (v6.36)
    on = frozenset(b for b in range(96, 128) if cn.draw(cn.TAG_K118, cn.DIM_C1, 9, b) == 1)
    assert half._v6_38_k1_books_now == on and len(on) == 15
    full = _keeper(1, mode=2)
    r2 = _request(full, books=books)
    assert len(t634._placed(r2, b_off)) == 2                               # mode 2: every class-1 book


def test_the_resting_keeper_follows_the_keep_rule_not_the_chase():
    t634 = _t634()
    t_buy = kp.target(BUY, 30.00, 30.03, 12e-4, 0.01)
    assert t_buy == 29.97                                                  # three ticks behind the 30.00 touch
    for price, kept in ((t_buy, True), (round(t_buy - 0.01, 2), True), (round(t_buy - 0.02, 2), False),
                        (30.00, True)):
        a = _keeper(1, rbps=12.0)
        t634._rest(a, 501, t634.B_ON, BUY, price)
        r = _request(a)
        assert ((t634.B_ON, 501) not in set(t634._cancelled(r, t634.B_ON))) is kept, (price, kept)


def test_k3q_reads_s14s_trackers_as_capture_trackers_only_when_selected():
    a = _agent(research_v638_c1=2, research_v638_c1_arm=4)
    a._v6_36_record = sw.StepRecord()
    assert isinstance(a._v6_32_tracker, kp.CaptureTracker) and isinstance(a._v6_34_tracker, kp.CaptureTracker)
    assert a._v6_32_tracker.weight == pytest.approx(a.research_v632_fee_weight)
    b = _agent(research_v638_c1=2, research_v638_c1_arm=2)
    assert not isinstance(b._v6_32_tracker, kp.CaptureTracker)
    a._v6_38_l3_on = True
    a._v6_32_tracker.k3q_r, a._v6_32_tracker.k3q_c = 1.0, 4.0            # q0 = 0.25
    a._v6_34_tracker.k3q_r, a._v6_34_tracker.k3q_c = 0.1, 1.0            # R1 - q0 C1 = -0.15 < 0: hold
    a.research_v636_step_withdrawal = True
    a._v6_38_k1_request()
    assert a._v6_38_k3q["hold"] is True and a._v6_38_k3q["q0"] == pytest.approx(0.25)
    a._v6_34_tracker.k3q_r = 0.3
    a._v6_38_k1_request()
    assert a._v6_38_k3q["hold"] is False


# ---- more KBL / FBL rule elements on the agent ------------------------------------------------------------------------

def test_s9_does_not_hold_the_window_quote_but_holds_v636s_quote():
    t634 = _t634()
    for window, want in ((None, []), ({3: 1}, ["BUY"])):
        a = _agent(_near(), research_v638_kbl=1)
        a._v62_mirror = BALANCED
        a._v6_29_held = lambda book_id, fp, cfg: BUY                     # S9 holds the buy (the move is against it)
        r = _request(a, window=window)
        assert [x[1] for x in t634._placed(r, 3)] == want, (window, t634._placed(r, 3))
    near_above = {"page": __import__("test_research_v6_29")._page(100.015 * math.exp(3e-4))}   # 3 bps: v6.36 buys
    b = _agent(near_above, research_v638_kbl=1)
    b._v62_mirror = BALANCED
    b._v6_29_held = lambda book_id, fp, cfg: BUY
    assert t634._placed(_request(b), 3) == []                             # outside the window S9 still holds it


def test_the_touch_size_is_x3as_unscaled_clip_in_the_window_and_fbls_shortfall_scaled_outside():
    a = _agent(research_v638_kbl=1, research_v638_lean=2, research_v638_lean_arm=3)
    a._v6_38_l3_on = True
    a._v6_38_fbl_prev = {3: 0}
    assert a._v6_38_touch_size(3, BUY, 0.0, 2.0, 0.5, 0.25, BUY) == 2.0        # the window quote: unscaled
    assert a._v6_38_touch_size(3, BUY, 0.0, 2.0, 0.5, 0.25, None) is None     # no window, no FBL growth: v6.36
    a._v6_38_fbl_prev = {3: 1}
    assert a._v6_38_touch_size(3, BUY, -10.0, 2.0, 1.0, 0.25, None) == pytest.approx(10.0)     # the shortfall
    assert a._v6_38_touch_size(3, BUY, -10.0, 2.0, 0.5, 0.25, None) == pytest.approx(5.0)      # v6.20 scaled
    assert a._v6_38_touch_size(3, BUY, -10.0, 2.0, 0.5, 0.25, BUY) == pytest.approx(10.0)      # X3a: unscaled
    assert a._v6_38_touch_size(3, SELL, -10.0, 2.0, 1.0, 0.25, None) is None   # the sell adds: no growth
    assert ln.fbl_sign(0, 100.0 * math.exp(2.2e-4), 100.0) == 0 and ln.fbl_sign(1, 100.0 * math.exp(2.2e-4), 100.0) == 1


def test_x1_puts_the_touch_placement_first_in_its_book_only_on_kbl_books():
    a = _agent(research_v638_kbl=1)
    a._v6_38_l3_on = True
    ix = [types.SimpleNamespace(type="CANCEL_ORDERS", bookId=3, clientOrderId=None),
          types.SimpleNamespace(type="PLACE_ORDER_LIMIT", bookId=3, clientOrderId=40031),
          types.SimpleNamespace(type="PLACE_ORDER_LIMIT", bookId=5, clientOrderId=40051),
          types.SimpleNamespace(type="PLACE_ORDER_LIMIT", bookId=3, clientOrderId=tq.client_ids(3)[0])]
    resp = types.SimpleNamespace(instructions=list(ix))
    a._v6_38_l3_post(resp, None)
    assert resp.instructions == [ix[3], ix[0], ix[1], ix[2]] and a._v6_38_l3_counts.get("kbl_x1_touch_first") == 1
    a._v6_23_foreign = frozenset({3})                                     # class 1: not a KBL book
    resp2 = types.SimpleNamespace(instructions=list(ix))
    a._v6_38_l3_post(resp2, None)
    assert resp2.instructions == ix
    off = _agent()
    resp3 = types.SimpleNamespace(instructions=list(ix))
    off._v6_38_l3_post(resp3, None)
    assert resp3.instructions == ix


def _ladder_agent(**switches):
    a = _agent({"rest": True}, **switches)
    a.research_v69_deep_ladder = True
    a.research_v632_fee_making = False
    db = a._v633_deep.books[3]
    db.sweeps = deque(sorted([21.5] * 196 + [25.0, 30.0, 34.0, 38.0]), maxlen=getattr(db.sweeps, "maxlen", None))
    return a


def test_kbl_xl_takes_the_ladder_off_the_capture_surplus_side_and_k5_off_both():
    t634 = _t634()
    import research_v633_deep_layer as dl
    lvl = {dl.level_client_ids(3, k)[i]: (s, k) for k in (1, 2) for i, s in enumerate((BUY, SELL))}
    ref = _ladder_agent()
    p0 = {lvl.get(x[2]) for x in t634._placed(_request(ref), 3)} - {None}
    assert p0 == {(BUY, 1), (SELL, 1)}, p0                                   # v6.36: L1 on both sides (L0 rests)
    a = _ladder_agent(research_v638_kbl=1)
    a._v62_mirror = types.SimpleNamespace(book_capture=lambda b: (9.0, 1.0) if int(b) == 3 else (0.0, 0.0))
    p1 = {lvl.get(x[2]) for x in t634._placed(_request(a), 3)} - {None}
    assert p1 == {(SELL, 1)} and a._v6_38_l3_counts.get("kbl_ladder_withheld", 0) >= 1, p1
    b = _ladder_agent(research_v638_kbl=1)
    b._v62_mirror = BALANCED
    assert {lvl.get(x[2]) for x in t634._placed(_request(b), 3)} - {None} == p0        # balanced: v6.36's ladder
    k5 = _ladder_agent(research_v638_kbl=2)
    assert {lvl.get(x[2]) for x in t634._placed(_request(k5), 3)} - {None} == set()   # X4: no ladder level at all
    import test_research_v6_2_14_touch_life as tlt
    rest = _ladder_agent(research_v638_kbl=1)
    rest._v62_mirror = a._v62_mirror
    tlt._rest(rest, 601, 3, 0, round(100.015 - 34.0 * 0.01, 2), cid=dl.level_client_ids(3, 1)[0], qty=2.0)
    tlt._rest(rest, 602, 3, 1, round(100.015 + 34.0 * 0.01, 2), cid=dl.level_client_ids(3, 1)[1], qty=2.0)
    cancelled = set(t634._cancelled(_request(rest), 3))
    assert (3, 601) in cancelled and (3, 602) not in cancelled, cancelled   # the surplus side's L1 goes, the other's stays
