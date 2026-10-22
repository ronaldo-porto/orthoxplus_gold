"""v6.31: S12b research_v631_touch_improve and the single-write session save research_v631_single_write_save.

v6.30 + two switches, default on, each its own off switch.

S12b [STRUCTURAL]: a NEW v6.17 touch quote is priced one tick better than the OTHERS' best on its side -- the book's
best with every order of ours left out (v6.2.10's touch view: the ledger's live ids and our client ids, the touch
quote's own included) -- when v6.2.10's geometry accepts it (strictly inside every opposite order, ours included, and
strictly on our side of the others' mid: a spread under 3 ticks is joined), when it is strictly better than v6.17's join
and when its edge vs the fresh fundamental is still v6.17's hurdle; else it is v6.30's join.  Side, size, gates and the rest / cancel
verdict stay v6.17's.

The save [STRUCTURAL bug fix]: v6.30's whole session save -- the frozen base's payload write, then the re-read and the
owner / deep / lots / pace / maker-quality rewrite -- runs on a stage file and replaces the session file in one
os.replace, only when the stage parses and carries the owner and the maker quality; a stop between v6.30's two writes
can no longer leave an owner-less file (UNOWNED_REFUSED, a cold start: UID 115, 10-14 JST).

No test here touches the host's caches, the network or a live tree, and nothing is imported from the scratchpad: the
agent is t630's harness with v6.31's real methods and the v6.31 state its real _init_build_switches statements leave;
the save runs the REAL frozen Strategy1_Research save and session path under Simple's real ones, in tmp dirs.
"""
import ast
import difflib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import textwrap
import types
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_session_state as ss  # noqa: E402
import research_v5_session_identity as si  # noqa: E402
import research_v617_touch_quote as tq  # noqa: E402
import research_v62_breadth as v62b  # noqa: E402
import research_v6210_touch_improve as v6210  # noqa: E402
import research_v6214_touch_life as v6214  # noqa: E402
import research_v631_touch_improve as ti  # noqa: E402
import research_v633_deep_layer as dl  # noqa: E402
import research_v63_trend_target as v63t  # noqa: E402
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
from _harness import extractor  # noqa: E402
import _v638_undo  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
BASE = (STRATEGY / "Strategy1_Research.py").read_text()
MODULE = (STRATEGY / "research_v631_touch_improve.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
_base_src = extractor(BASE, cls_name="Strategy1_Research")
S = 1_000_000_000
NOW = t630.NOW
SIM = t630.SIM
BUY, SELL = dl.SIDE_BUY, dl.SIDE_SELL
TICK = 0.01
UID = t630.T0M1                     # book 3 tier 0, look-back x1: v6.30's profile leaves book 3 as v6.29 had it
TOUCH_LINE = "price = self._v6_31_price(book_id, toward, price, fp, tick_size)"
NOTE_LINE = "self._v6_31_note(book_id, book, own_ids, touch_on)"
SAVE_LINE = 'if bool(getattr(self, "research_v631_single_write_save", False)) and self._v6_31_staged_save(force):'
PATH_LINE = "return self._v6_31_stage"
REPORT_LINE = "self._v6_31_report()"
S12B_LINE = 'self.research_v631_touch_improve = self._as_bool(getattr(self.config, "research_v631_touch_improve", True))'
SAVE_SWITCH = 'getattr(self.config, "research_v631_single_write_save", True))'
V6_31_NAMES = {"V6_31_REASONS": ti.REASONS, "V6_31_STAGE_SUFFIX": ti.STAGE_SUFFIX,
               "V6_31_SINGLE_WRITE_SAVE_VERSION": ti.V631_SINGLE_WRITE_SAVE_VERSION,
               "V6_31_TOUCH_IMPROVE_VERSION": ti.V631_TOUCH_IMPROVE_VERSION,
               "v6_31_improved_touch_price": ti.improved_touch_price}
# the names v6.31's methods reach beyond its own, from the modules Simple imports them from (never invented here)
REACHED = {"v63_own_client_ids": v63t.own_client_ids, "v62_entry_client_ids": v62b.entry_client_ids,
           "v6214_exit_client_ids": v6214.exit_client_ids, "v633_own_client_ids": dl.own_client_ids,
           "v617_client_ids": tq.client_ids, "v6210_touch_view": v6210.touch_view,
           "V62_STATE_EVERY_TICKS": v62b.V62_STATE_EVERY_TICKS, "OWNER_KEY": si.OWNER_KEY, "os": os, "json": json}
EXTRA = dict(t630.EXTRA, **V6_31_NAMES, **REACHED)
METHODS = ("_v6_31_note", "_v6_31_price", "_v6_31_staged_save", "_v6_31_count", "_v6_31_report", "_v6_31_snapshot")
FIELDS = ("version", "save_version", "touch_improve_on", "single_write_save_on", *ti.REASONS, "staged_saves",
          "stage_incomplete", "errors")
ABOVE, BELOW = t630.ABOVE, t630.BELOW            # book 3 (100.00 / 100.03): fp 38 bps above / 42 bps below its mid


# ---- 1. the module ------------------------------------------------------------------------------------------------------

def test_the_module_states_its_version_reasons_and_evidence_and_labels_the_rule():
    assert ti.V631_TOUCH_IMPROVE_VERSION == "touch_improve_v6_31"
    assert ti.V631_SINGLE_WRITE_SAVE_VERSION == "single_write_save_v6_31"
    assert ti.STAGE_SUFFIX == ".v631.stage"
    assert ti.REASONS == ("improved_buy", "improved_sell", "join_not_inside", "join_own_best", "join_edge",
                          "join_no_view")
    assert set(ti.__all__) == {"IMPROVED_BUY", "IMPROVED_SELL", "JOIN_EDGE", "JOIN_NOT_INSIDE", "JOIN_NO_VIEW",
                               "JOIN_OWN_BEST", "REASONS", "STAGE_SUFFIX", "THRESHOLD_BPS",
                               "V631_SINGLE_WRITE_SAVE_VERSION", "V631_TOUCH_IMPROVE_VERSION", "improved_touch_price"}
    assert ti.THRESHOLD_BPS is tq.THRESHOLD_BPS == 2.3                       # v6.17's hurdle, the same object
    assert ti.improved_bid is v6210.improved_bid and ti.improved_ask is v6210.improved_ask
    assert ti.edge_bps is tq.edge_bps
    doc = ast.get_docstring(ast.parse(MODULE))
    for needle in ("14-19%", "0.3-2.9%", "S12 ", "S12b", "S12c", "E +3.6..+19.8%", "-3.2..+17.0%", "-0.017",
                   "STRUCTURAL", "re-improving", "canary", "imp3=1", "v6.2.14", "REALIZED"):
        assert needle in doc, needle


def test_the_module_imports_only_v617s_hurdle_and_v6210s_geometry_and_loads_no_lock_socket_or_url():
    tree = ast.parse(MODULE)
    imported = {}
    for n in tree.body:
        if isinstance(n, ast.Import):
            raise AssertionError(ast.unparse(n))
        if isinstance(n, ast.ImportFrom):
            imported[n.module] = sorted(a.name for a in n.names)
    assert imported == {"__future__": ["annotations"], "typing": ["Any"],
                        "research_v617_touch_quote": ["SIDE_BUY", "SIDE_SELL", "THRESHOLD_BPS", "edge_bps"],
                        "research_v6210_touch_improve": ["improved_ask", "improved_bid"]}, imported
    code = "\n".join(ast.unparse(n) for n in tree.body[1:])
    for word in ("fcntl", "flock", "socket", "/tmp", "multiprocessing", "subprocess", "random", "urllib", "open(",
                 "os.", "time.", "thread", "global "):
        assert word not in code, word
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); before = set(sys.modules); "
             "import research_v631_touch_improve; new = set(sys.modules) - before; "
             "print(sorted(m for m in new if m.split('.')[0] in ('fcntl', 'socket', 'urllib', 'http', 'multiprocessing', "
             "'subprocess', 'random', 'research_v616_fair_value', 'research_v6_29_touch_move', "
             "'research_v630_identity_profile')))")
    out = subprocess.run([sys.executable, "-I", "-B", "-c", probe, str(STRATEGY)], capture_output=True, text=True,
                         timeout=120, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1"})
    assert out.returncode == 0 and out.stdout.strip() == "[]", (out.stdout, out.stderr)


def test_the_rule_has_no_sample_sized_constant():
    """Its numbers are the half tick, the 1e-9 hurdle tolerance and 0.0; the tick, the hurdle and the mid come in."""
    tree = ast.parse(MODULE)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "improved_touch_price")
    nums = {n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
            and not isinstance(n.value, bool)}
    assert nums <= {0.0, 0.5, 1e-9}, nums
    assert [a.arg for a in fn.args.args] == ["side", "view", "fundamental", "tick"]
    assert [a.arg for a in fn.args.kwonlyargs] == ["join_price", "threshold_bps"]


# ---- 2. the pure rule: geometry, the join, the hurdle ------------------------------------------------------------------

def _view(ob, oa, rb=None, ra=None):
    """A TouchView: the others' best bid / ask and the raw touch (ours included; the others' by default)."""
    return v6210.TouchView(ob if rb is None else rb, oa if ra is None else ra, ob, oa, rb is not None, ra is not None)


def _fp(side, px, bps):
    """A fundamental ``bps`` on the safe side of ``px`` for a ``side`` quote (above a bid, below an offer)."""
    return px * math.exp(bps / 1e4) if side == BUY else px * math.exp(-bps / 1e4)


FAR = 40.0                           # an edge far above the hurdle


def test_spreads_of_1_to_10_ticks_improve_from_3_ticks_on_both_sides_and_never_reach_the_mid():
    for side in (BUY, SELL):
        for k in range(1, 11):
            b, a = 100.00, round(100.00 + k * TICK, 2)
            join = b if side == BUY else a
            px, why = ti.improved_touch_price(side, _view(b, a), _fp(side, join, FAR), TICK, join_price=join)
            mid = 0.5 * (b + a)
            if k < 3:
                assert (px, why) == (None, ti.JOIN_NOT_INSIDE), (side, k)
            else:
                want = round(b + TICK, 2) if side == BUY else round(a - TICK, 2)
                assert (px, why) == (want, ti.IMPROVED_BUY if side == BUY else ti.IMPROVED_SELL), (side, k)
                assert (px < mid - 1e-12) if side == BUY else (px > mid + 1e-12), (side, k)
                assert b < px < a, (side, k)


def test_a_two_tick_spread_is_joined_because_the_candidate_sits_at_the_mid():
    b, a = 100.00, 100.02
    assert abs(v6210.on_grid(b + TICK, TICK) - 0.5 * (b + a)) < 1e-9            # the candidate is the mid
    assert ti.improved_touch_price(BUY, _view(b, a), _fp(BUY, b, FAR), TICK, join_price=b) == (None, ti.JOIN_NOT_INSIDE)
    assert ti.improved_touch_price(SELL, _view(b, a), _fp(SELL, a, FAR), TICK, join_price=a) == \
        (None, ti.JOIN_NOT_INSIDE)


def test_the_mid_is_the_others_mid_and_an_opposite_order_of_ours_bounds_the_candidate():
    # our own offer rests inside the others' ask: the others' mid still decides, and the bid stays below our offer
    v = _view(100.00, 100.10, ra=100.02)
    assert ti.improved_touch_price(BUY, v, _fp(BUY, 100.0, FAR), TICK, join_price=100.00) == (100.01, ti.IMPROVED_BUY)
    v = _view(100.00, 100.10, ra=100.01)                                         # one tick above the others' bid
    assert ti.improved_touch_price(BUY, v, _fp(BUY, 100.0, FAR), TICK, join_price=100.00) == \
        (None, ti.JOIN_NOT_INSIDE)
    v = _view(100.00, 100.10, rb=100.09)                                         # our bid one tick below their ask
    assert ti.improved_touch_price(SELL, v, _fp(SELL, 100.1, FAR), TICK, join_price=100.10) == \
        (None, ti.JOIN_NOT_INSIDE)
    v = _view(100.00, 100.04, rb=100.02)                                         # our bid at the others' mid: legal
    assert ti.improved_touch_price(SELL, v, _fp(SELL, 100.04, FAR), TICK, join_price=100.04) == \
        (100.03, ti.IMPROVED_SELL)
    # the others' mid, not the raw one: raw 100.00 / 100.03 (ours at 100.03), others 100.00 / 100.05 -> 100.01 < 100.025
    v = _view(100.00, 100.05, ra=100.03)
    assert ti.improved_touch_price(BUY, v, _fp(BUY, 100.0, FAR), TICK, join_price=100.00) == (100.01, ti.IMPROVED_BUY)


def test_our_own_order_holding_the_best_is_joined_never_improved_over():
    # ours alone at 100.03, the others' best 100.00 (spread to 100.10): the candidate 100.01 is behind our join
    v = _view(100.00, 100.10, rb=100.03)
    assert ti.improved_touch_price(BUY, v, _fp(BUY, 100.03, FAR), TICK, join_price=100.03) == \
        (None, ti.JOIN_OWN_BEST)
    v = _view(100.00, 100.10, ra=100.07)
    assert ti.improved_touch_price(SELL, v, _fp(SELL, 100.07, FAR), TICK, join_price=100.07) == \
        (None, ti.JOIN_OWN_BEST)
    # ours one tick above the others' bid: the candidate EQUALS the join -- not strictly better, joined
    v = _view(100.00, 100.10, rb=100.01)
    assert ti.improved_touch_price(BUY, v, _fp(BUY, 100.01, FAR), TICK, join_price=100.01) == \
        (None, ti.JOIN_OWN_BEST)
    v = _view(100.00, 100.10, ra=100.09)
    assert ti.improved_touch_price(SELL, v, _fp(SELL, 100.09, FAR), TICK, join_price=100.09) == \
        (None, ti.JOIN_OWN_BEST)
    # ours sharing the best with the others: the others' best IS that level, so the candidate improves it
    v = _view(100.00, 100.10, rb=100.00)
    assert ti.improved_touch_price(BUY, v, _fp(BUY, 100.0, FAR), TICK, join_price=100.00) == (100.01, ti.IMPROVED_BUY)


def test_the_candidate_must_beat_the_join_by_a_whole_tick():
    v = _view(100.00, 100.10)
    for join, want in ((99.99, (100.01, ti.IMPROVED_BUY)), (100.00, (100.01, ti.IMPROVED_BUY)),
                       (100.0049, (100.01, ti.IMPROVED_BUY)), (100.0051, (None, ti.JOIN_OWN_BEST)),
                       (100.01, (None, ti.JOIN_OWN_BEST)), (100.02, (None, ti.JOIN_OWN_BEST))):
        assert ti.improved_touch_price(BUY, v, _fp(BUY, 100.0, FAR), TICK, join_price=join) == want, join
    for join, want in ((100.11, (100.09, ti.IMPROVED_SELL)), (100.10, (100.09, ti.IMPROVED_SELL)),
                       (100.0951, (100.09, ti.IMPROVED_SELL)), (100.0949, (None, ti.JOIN_OWN_BEST)),
                       (100.09, (None, ti.JOIN_OWN_BEST)), (100.08, (None, ti.JOIN_OWN_BEST))):
        assert ti.improved_touch_price(SELL, v, _fp(SELL, 100.1, FAR), TICK, join_price=join) == want, join


def test_a_candidate_short_of_the_hurdle_keeps_the_join_and_the_hurdle_is_inclusive():
    v = _view(100.00, 100.10)
    for side, join, cand in ((BUY, 100.00, 100.01), (SELL, 100.10, 100.09)):
        # the join clears the hurdle by less than a tick's worth: the improved price does not
        fp = _fp(side, join, 2.3 + 0.5)                                         # join edge 2.8 bps, a tick ~1 bp
        assert tq.edge_bps(side, join, fp) >= 2.3 > tq.edge_bps(side, cand, fp)
        assert ti.improved_touch_price(side, v, fp, TICK, join_price=join) == (None, ti.JOIN_EDGE), side
        exact = _fp(side, cand, 2.3)                                            # the candidate's edge is the hurdle
        assert abs(tq.edge_bps(side, cand, exact) - 2.3) < 1e-9
        assert ti.improved_touch_price(side, v, exact, TICK, join_price=join)[0] == cand, side
        assert ti.improved_touch_price(side, v, _fp(side, cand, 2.3 - 1e-6), TICK, join_price=join) == \
            (None, ti.JOIN_EDGE), side
        assert ti.improved_touch_price(side, v, None, TICK, join_price=join) == (None, ti.JOIN_EDGE), side
        assert ti.improved_touch_price(side, v, fp, TICK, join_price=join, threshold_bps=0.5)[0] == cand, side
    # the fundamental on the wrong side of the candidate: negative edge
    assert ti.improved_touch_price(BUY, v, 100.005, TICK, join_price=100.00) == (None, ti.JOIN_EDGE)


def test_no_view_a_crossed_or_empty_book_and_unreadable_input_keep_the_join():
    for book in (t14.Book([], [t14.Level(100.03, [t14.Order(2, 1.0)])]),
                 t14.Book([t14.Level(100.00, [t14.Order(1, 1.0)])], []),
                 t14.Book([t14.Level(100.03, [t14.Order(1, 1.0)])], [t14.Level(100.03, [t14.Order(2, 1.0)])]),
                 t14.Book([t14.Level(100.04, [t14.Order(1, 1.0)])], [t14.Level(100.03, [t14.Order(2, 1.0)])]),
                 t14.Book([t14.Level(100.00, [t14.Order(7, 1.0)])], [t14.Level(100.05, [t14.Order(2, 1.0)])])):
        view = v6210.touch_view(book, own_ids={7}, own_cids=set())              # the last: only ours on the bid
        assert view is None
        assert ti.improved_touch_price(BUY, view, _fp(BUY, 100.0, FAR), TICK, join_price=100.00) == \
            (None, ti.JOIN_NO_VIEW)
    v = _view(100.00, 100.10)
    for tick in (None, 0.0, -0.01, float("nan"), "x"):
        assert ti.improved_touch_price(BUY, v, _fp(BUY, 100.0, FAR), tick, join_price=100.00) == \
            (None, ti.JOIN_NO_VIEW), tick
    for join in (None, 0.0, float("inf"), "x"):
        assert ti.improved_touch_price(BUY, v, _fp(BUY, 100.0, FAR), TICK, join_price=join) == \
            (None, ti.JOIN_NO_VIEW), join
    assert ti.improved_touch_price("hold", v, _fp(BUY, 100.0, FAR), TICK, join_price=100.00) == (None, ti.JOIN_NO_VIEW)
    broken = types.SimpleNamespace(others_bid="x", others_ask=None, raw_bid=None, raw_ask=None)
    assert ti.improved_touch_price(BUY, broken, _fp(BUY, 100.0, FAR), TICK, join_price=100.00) == \
        (None, ti.JOIN_NOT_INSIDE)


def test_property_every_improved_price_is_one_tick_inside_strictly_our_side_of_the_mid_beats_the_join_and_clears():
    """A deterministic sweep: 2 decimals and 4, bids across the price scale, spreads 1-12 ticks, our own order (none,
    at the others' best, alone at a better price on the quote's side, inside on the other side), fundamentals from short
    of the hurdle to far.  Every improved price is exactly the others' best one tick better, strictly inside every
    opposite order, strictly our side of the others' mid, strictly better than the join, and clears the hurdle;
    everything else is (None, a reason)."""
    n_improved = n_join = 0
    for dec in (2, 4):
        t = 10.0 ** -dec
        for base in (0.5, 7.3, 100.0, 299.97, 5123.4):
            b0 = round(base, dec)
            for k in range(1, 13):
                ob, oa = b0, round(b0 + k * t, dec)
                for own in ("none", "same", "better", "inside"):
                    for side in (BUY, SELL):
                        rb, ra = ob, oa
                        if own == "better":
                            rb, ra = (round(ob + 2 * t, dec), oa) if side == BUY else (ob, round(oa - 2 * t, dec))
                        elif own == "inside":
                            rb, ra = (ob, min(oa, round(ob + 2 * t, dec))) if side == BUY else \
                                (max(ob, round(oa - 2 * t, dec)), oa)
                        if not rb < ra:
                            continue
                        join = rb if side == BUY else ra
                        for bps in (0.0, 2.0, 2.3, 3.0, 8.0, 50.0):
                            fp = _fp(side, join, bps)
                            px, why = ti.improved_touch_price(side, _view(ob, oa, rb, ra), fp, t, join_price=join)
                            assert why in ti.REASONS
                            if px is None:
                                n_join += 1
                                assert why not in (ti.IMPROVED_BUY, ti.IMPROVED_SELL)
                                continue
                            n_improved += 1
                            mid = 0.5 * (ob + oa)
                            if side == BUY:
                                assert why == ti.IMPROVED_BUY and abs(px - (ob + t)) < 0.5 * t
                                assert px < ra - 0.5 * t and px < mid and px > join + 0.5 * t
                            else:
                                assert why == ti.IMPROVED_SELL and abs(px - (oa - t)) < 0.5 * t
                                assert px > rb + 0.5 * t and px > mid and px < join - 0.5 * t
                            assert Decimal(repr(px)) == Decimal(repr(px)).quantize(Decimal(1).scaleb(-dec))
                            assert tq.edge_bps(side, px, fp) >= tq.THRESHOLD_BPS - 1e-9
    assert n_improved > 1000 and n_join > 1000, (n_improved, n_join)


# ---- 3. the agent: t630's harness with v6.31's real methods and init ---------------------------------------------------

_CLASSES: dict = {}


def _klass(base, names=None):
    key = (base, None if names is None else tuple(sorted((k, id(v)) for k, v in names.items())))
    if key not in _CLASSES:
        scope = dict(getattr(base, "_v63_pass").__globals__)
        scope.update(EXTRA if names is None else names)
        methods = {}
        for n in METHODS:
            ns = dict(scope)
            exec(compile(ast.Module(body=[ast.parse(_src(n)).body[0]], type_ignores=[]), f"<{n}>", "exec"), ns)
            methods[n] = ns[n]
        _CLASSES[key] = type("P631", (base,), methods)
    return _CLASSES[key]


def _mentions631(node):
    """A v6.31 name in ``node`` -- v6.31's alone: t630's _mentions631 also names v6.32's, which its _Drop631 takes out
    with v6.31's."""
    for n in ast.walk(node):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.arg if isinstance(n, (ast.arg, ast.keyword)) else n.value if isinstance(n, ast.Constant) else None
        if isinstance(name, str) and (name in t623.V6_31_IDENTIFIERS or name.startswith(("_v6_31_", "v6_31_"))):
            return True
    return False


def _init_source():
    """The v6.31 statements of _init_build_switches -- the real ones -- as one function of self."""
    fn = ast.parse(_src("_init_build_switches")).body[0]
    body = [st for st in fn.body if _mentions631(st)]
    assert len(body) == 9, [ast.unparse(st) for st in body]
    return "def _init631(self):\n" + textwrap.indent("\n".join(ast.unparse(st) for st in body), "    ") + "\n"


def _init631(agent, *, s12b=True, save=True, names=None):
    ns = dict(EXTRA if names is None else names)
    exec(compile(_init_source(), "<v6.31 init>", "exec"), ns)
    cfg = dict(vars(getattr(agent, "config", None) or types.SimpleNamespace()))
    cfg.update(research_v631_touch_improve=s12b, research_v631_single_write_save=save)
    agent.config = types.SimpleNamespace(**cfg)
    agent._as_bool = t630._as_bool()
    ns["_init631"](agent)


def _agent(*, s12b=True, save=True, safety=1, rest=True, names=None, **kw):
    """t630's harness agent (book 3 tier 0 / look-back x1, so v6.30's profile leaves it v6.29's; S5-S9 on; the
    fundamental 38 bps above book 3's mid unless ``page`` says otherwise) with v6.31's methods and the v6.31 state its
    real _init_build_switches statements leave; the live one-tick post-only gap (v6.2.14's touch gap); level 0 resting
    on both sides -- the deep layer places nothing new, so the touch quote does not yield -- unless ``rest`` is
    False."""
    agent = t630._agent(uid=UID, **kw)
    agent.__class__ = _klass(type(agent), names)
    _init631(agent, s12b=s12b, save=save, names=names)
    agent.research_post_only_safety_ticks = safety
    if rest:
        t627._rest_l0(agent)
    return agent


def _ref(*, safety=1, rest=True, **kw):
    """v6.30 exactly: t630's harness agent, no v6.31 attribute at all."""
    agent = t630._agent(uid=UID, **kw)
    agent.research_post_only_safety_ticks = safety
    if rest:
        t627._rest_l0(agent)
    return agent


def _request(agent, *, ts=NOW, wall=1000.0, version=62, book=None):
    """One request as respond() makes it (t627's: the v6.26 hook, v6.23's observation, the pass) on ``book``."""
    return t627._request(agent, ts=ts, wall=wall, version=version, book=book)


_out = t630._out
_touch, _rest_of = t627._touch, t627._rest_of
BELOW_PAGE = t629._page(BELOW)


def _book(bid=100.00, ask=100.03, bids=None, asks=None):
    """Book 3: the others' orders 1 / 2 at ``bid`` / ``ask``, or the given levels."""
    return t14.Book(bids if bids is not None else [t14.Level(bid, [t14.Order(1, 2.5)])],
                    asks if asks is not None else [t14.Level(ask, [t14.Order(2, 2.5)])])


def _counts(agent):
    return dict(agent._v6_31_counts)


def _rows(agent, event="V6_31_STATE"):
    return [kw for e, kw in agent.emitted if e == event]


def _others_rows(agent):
    """Every other row of the agent, its wall-clock timings left out (v6.21's window scan reports its own ms)."""
    return [(e, {k: v for k, v in kw.items() if k != "ms" and not k.endswith("_ms")}) for e, kw in agent.emitted
            if e != "V6_31_STATE"]


def test_the_init_statements_set_the_state_and_both_switches_ship_default_on():
    agent = _agent()
    assert agent.research_v631_touch_improve is True and agent.research_v631_single_write_save is True
    assert (agent._v6_31_noted, agent._v6_31_stage, agent._v6_31_counts, agent._v6_31_errors) == (None, None, {}, 0)
    assert (agent._v6_31_errors_seen, agent._v6_31_reported, agent._v6_31_erring) == (0, False, False)
    off = _agent(s12b=False, save=False)
    assert off.research_v631_touch_improve is False and off.research_v631_single_write_save is False
    a = _agent()
    a.config = types.SimpleNamespace()                               # no key: both default on
    ns = dict(EXTRA)
    exec(compile(_init_source(), "<v6.31 init>", "exec"), ns)
    ns["_init631"](a)
    assert a.research_v631_touch_improve is True and a.research_v631_single_write_save is True
    for bad in ("0", "false", 0, False):
        a.config = types.SimpleNamespace(research_v631_touch_improve=bad, research_v631_single_write_save=bad)
        ns["_init631"](a)
        assert a.research_v631_touch_improve is False and a.research_v631_single_write_save is False, bad


def test_spreads_of_1_to_10_ticks_through_the_real_touch_quote_improve_from_3_ticks_and_nothing_else_changes():
    """Book 3 at 100.00 / 100.00 + k ticks, the fundamental above (a buy quote) and below (a sell quote): from 3 ticks
    the new quote is the others' best one tick better, under 3 it is v6.30's join; every other placement, cancel and row
    of the request is v6.30's."""
    for page, side in (("default", BUY), (BELOW_PAGE, SELL)):
        for k in range(1, 11):
            b, a = 100.00, round(100.00 + k * TICK, 2)
            on, ref = _agent(page=page), _ref(page=page)
            got, want = _out(_request(on, book=_book(b, a))), _out(_request(ref, book=_book(b, a)))
            case = (side, k)
            assert _rest_of(got) == _rest_of(want), case
            (wt,), (gt,) = _touch(want)[0], _touch(got)[0]
            assert wt[:3] == gt[:3] == (3, side.upper(), tq.client_ids(3)[0 if side == BUY else 1]), case
            assert wt[3] == (b if side == BUY else a) and gt[4] == wt[4], case               # v6.30 joins; same size
            if k < 3:
                assert gt == wt and _counts(on) == {ti.JOIN_NOT_INSIDE: 1}, case
            else:
                assert gt[3] == (round(b + TICK, 2) if side == BUY else round(a - TICK, 2)), case
                assert _counts(on) == {ti.IMPROVED_BUY if side == BUY else ti.IMPROVED_SELL: 1}, case
            assert _others_rows(on) == _others_rows(ref) and on._v6_31_errors == 0, case


def test_the_default_book_of_three_ticks_improves_and_a_two_tick_post_only_gap_keeps_the_one_tick_spread_quiet():
    on, ref = _agent(), _ref()
    got, want = _out(_request(on)), _out(_request(ref))
    assert _touch(want)[0] == [(3, "BUY", 43031, 100.00, 2.0)] and _touch(got)[0] == [(3, "BUY", 43031, 100.01, 2.0)]
    on2, ref2 = _agent(safety=2), _ref(safety=2)                     # the harness's gap: a 1-tick spread, no join
    book = _book(100.00, 100.01)
    got, want = _out(_request(on2, book=book)), _out(_request(ref2, book=book))
    assert got == want and _touch(got)[0] == [] and _counts(on2) == {}


def test_with_the_switch_off_every_request_is_v630s_exactly():
    for page in ("default", BELOW_PAGE):
        for book in (None, _book(100.00, 100.06), _book(100.00, 100.01), _book(100.00, 100.02)):
            for rest in (True, False):                               # False: the deep layer first, the quote yields
                off, ref = _agent(s12b=False, page=page, rest=rest), _ref(page=page, rest=rest)
                assert _out(_request(off, book=book)) == _out(_request(ref, book=book))
                assert _others_rows(off) == _others_rows(ref) and _counts(off) == {} and off._v6_31_noted is None
    on, ref = _agent(rest=False), _ref(rest=False)                   # on, but the deep layer took the side: v6.30's
    assert _out(_request(on, book=_book(100.00, 100.06))) == _out(_request(ref, book=_book(100.00, 100.06)))
    assert _touch(_out(_request(_agent(rest=False))))[0] == []


def _alone(price, oid, cid, others=100.00, ask=100.10):
    """Book 3 with ours alone at ``price`` above the others' bid (``others``), the others' offer at ``ask``."""
    return _book(bids=[t14.Level(price, [t14.Order(oid, 0.5, cid)]), t14.Level(others, [t14.Order(1, 2.5)])],
                 asks=[t14.Level(ask, [t14.Order(2, 2.5)])])


def test_our_own_order_alone_at_the_best_is_joined_never_improved_over():
    """In the ledger (by order id) or only by its client id (not acknowledged yet): ours at 100.02 above the others'
    100.00 -- the join is 100.02 and the candidate 100.01 is behind it: v6.30's price.  Our own touch quote seen only by
    its client id is ours too: without it in the union the quote would walk to 100.03, over itself."""
    for cid, in_ledger in ((70031, True), (43031, False), (40031, False), (80031, False), (60031, False)):
        on, ref = _agent(), _ref()
        if in_ledger:
            for a in (on, ref):
                t14._rest(a, 77, 3, 0, 100.02, cid=cid, qty=0.5)
        book = _alone(100.02, 77, cid)
        got, want = _out(_request(on, book=book)), _out(_request(ref, book=book))
        assert got == want, (cid, got, want)
        assert _touch(got)[0] == [(3, "BUY", 43031, 100.02, 2.0)] and _counts(on) == {ti.JOIN_OWN_BEST: 1}, cid
    stranger = _agent()                                              # someone else's order there: improved over
    got = _out(_request(stranger, book=_alone(100.02, 77, 12345)))
    assert _touch(got)[0] == [(3, "BUY", 43031, 100.03, 2.0)] and _counts(stranger) == {ti.IMPROVED_BUY: 1}


def test_the_others_touch_leaves_out_every_family_of_our_client_ids_and_every_ledger_id():
    """Ours alone at 100.02 above the others' 100.00 (offer 100.10): by any id of any of our client-id families, or by
    the ledger's order id, it is ours -- the others' best is 100.00, the candidate 100.01 is behind the join 100.02: the
    join.  Someone else's order there is the others' best: 100.03.  Shared with the others, the level is theirs too."""
    families = {"v6.3 roles": v63t.own_client_ids(3), "v6.2 entries": set(v62b.entry_client_ids(3)),
                "v6.2.14 exits": set(v6214.exit_client_ids(3)), "v6.3.3 deep": dl.own_client_ids(3),
                "v6.17 touch": set(tq.client_ids(3))}
    agent = _agent()
    fp = _fp(BUY, 100.02, FAR)

    def price(book, own_ids=()):
        agent._v6_31_note(3, book, own_ids, True)
        return agent._v6_31_price(3, BUY, 100.02, fp, TICK)
    n = 0
    for name, cids in families.items():
        assert cids, name
        for cid in sorted(cids):
            assert price(_alone(100.02, 77, cid)) == 100.02, (name, cid)
            n += 1
    assert price(_alone(100.02, 77, 12345)) == 100.03                 # not ours
    assert price(_alone(100.02, 77, 12345), {77}) == 100.02           # ours by the ledger's id
    assert price(_alone(100.02, 77, 12345), [77]) == 100.02           # any iterable of ids
    shared = _book(bids=[t14.Level(100.02, [t14.Order(77, 0.5, 43031), t14.Order(5, 1.0)])], asks=[
        t14.Level(100.10, [t14.Order(2, 2.5)])])
    assert price(shared) == 100.03                                    # ours shares the level with the others'
    assert agent._v6_31_errors == 0 and _counts(agent) == {ti.JOIN_OWN_BEST: n + 2, ti.IMPROVED_BUY: 2}


def test_the_others_touch_is_taken_only_when_a_new_touch_quote_is_priced():
    """The note keeps the book and the ledger's ids; v6.2.10's walk of its best levels runs in _v6_31_price, once per
    new quote -- never for a book whose quote rests, nor one where the deep layer took the side first."""
    calls = []

    def counting(book, **kw):
        calls.append(sorted(kw["own_ids"]))
        return v6210.touch_view(book, **kw)
    names = dict(EXTRA, v6210_touch_view=counting)
    a = _agent(names=names)
    _request(a, book=_book(100.00, 100.06))
    assert calls == [[21, 22]] and _counts(a) == {ti.IMPROVED_BUY: 1}           # level 0's two orders: the ledger's
    assert a._v6_31_noted[:2] == (3, a._tick) and a._v6_31_noted[3] == {21, 22}       # this book, this request
    del calls[:]
    b = _agent(names=names)                                          # ours rests at 100.01: kept, nothing priced
    t14._rest(b, 23, 3, 0, 100.01, cid=43031, qty=2.0)
    _request(b, book=_book(bids=[t14.Level(100.01, [t14.Order(23, 2.0, 43031)]), t14.Level(100.00, [t14.Order(1, 2.5)])],
                           asks=[t14.Level(100.06, [t14.Order(2, 2.5)])]))
    assert calls == [] and _counts(b) == {}
    c = _agent(names=names, rest=False)                              # the deep layer places the side first: it yields
    _request(c, book=_book(100.00, 100.06))
    assert calls == [] and _counts(c) == {}


def test_the_view_is_this_books_and_this_requests_only_and_the_switches_gate_both_methods():
    agent = _agent()
    fp = _fp(BUY, 100.00, FAR)
    agent._tick = 5
    agent._v6_31_note(3, _book(100.00, 100.06), set(), True)
    assert agent._v6_31_noted[:2] == (3, 5)
    assert agent._v6_31_price(3, BUY, 100.00, fp, TICK) == 100.01
    assert agent._v6_31_price(4, BUY, 100.00, fp, TICK) == 100.00                      # another book: no view
    agent._tick = 6
    assert agent._v6_31_price(3, BUY, 100.00, fp, TICK) == 100.00                      # a later request: no view
    assert _counts(agent) == {ti.IMPROVED_BUY: 1, ti.JOIN_NO_VIEW: 2}
    agent._v6_31_note(3, _book(100.00, 100.06), set(), False)        # v6.17's touch quote off: nothing noted
    assert agent._v6_31_noted is None
    agent._v6_31_note(3, _book(100.00, 100.06), set(), True)
    agent.research_v631_touch_improve = False                        # off: the join, a view or not, nothing counted
    assert agent._v6_31_price(3, BUY, 100.00, fp, TICK) == 100.00 and _counts(agent)[ti.IMPROVED_BUY] == 1
    agent._v6_31_note(3, _book(100.00, 100.06), set(), True)
    assert agent._v6_31_noted is None and agent._v6_31_errors == 0


def test_the_note_never_raises_and_an_unreadable_one_leaves_nothing_noted():
    agent = _agent()
    agent._v6_31_note(3, _book(100.00, 100.06), {21}, True)
    assert agent._v6_31_noted is not None and agent._v6_31_noted[3] == {21}
    for book_id, own in (("x", set()), (None, set()), (3, 5), (3, object())):
        agent._v6_31_note(book_id, _book(100.00, 100.06), own, True)
        assert agent._v6_31_noted is None, (book_id, own)
    assert agent._v6_31_errors == 4
    assert agent._v6_31_price(3, BUY, 100.00, _fp(BUY, 100.0, FAR), TICK) == 100.00     # nothing noted: the join
    assert _counts(agent) == {ti.JOIN_NO_VIEW: 1}


def test_every_v631_path_fails_open_to_v630s_join_and_counts():
    def boom(*a, **k):
        raise RuntimeError("boom")
    for name in ("v6_31_improved_touch_price", "v6210_touch_view", "v617_client_ids"):
        names = dict(EXTRA, **{name: boom})
        on, ref = _agent(names=names), _ref()
        got, want = _out(_request(on)), _out(_request(ref))
        assert got == want and on._v6_31_errors == 1, name                              # v6.30's join, one error
        assert _others_rows(on) == _others_rows(ref), name
    agent = _agent()
    agent._v6_31_noted = (3,)                                        # a broken note: the join, an error
    assert agent._v6_31_price(3, BUY, 100.00, 100.5, TICK) == 100.00 and agent._v6_31_errors == 1
    agent._v6_31_noted = None
    agent._v6_31_count = boom
    assert agent._v6_31_price(3, BUY, 100.00, 100.5, TICK) == 100.00 and agent._v6_31_errors == 2
    agent = _agent()
    agent._v6_31_counts = None                                       # the tally rebuilds itself
    agent._v6_31_count("x")
    assert agent._v6_31_counts == {"x": 1}
    agent._v6_31_counts = {"x": "y"}                                 # an unreadable tally: an error, never a raise
    agent._v6_31_count("x")
    assert agent._v6_31_errors == 1


def test_a_resting_improved_quote_keeps_v617s_verdict_and_is_not_chased_by_v631():
    """Ours rests at 100.01 (improved, in the ledger and the book): kept while the others' best is under REST_TICKS (2)
    better -- an overbid by one tick (100.02) keeps it, nothing new -- and cancelled TOUCH_LEFT at two (100.03), as
    v6.30 does with the same resting quote.  (Live, v6.2.14's touch life cancels a non-deep order a tick behind the
    best; that post-pass is v6.30's and unchanged.)"""
    for best, cancelled in ((None, False), (100.02, False), (100.03, True)):
        bids = [t14.Level(100.01, [t14.Order(23, 2.0, 43031)]), t14.Level(100.00, [t14.Order(1, 2.5)])]
        if best is not None:
            bids = [t14.Level(best, [t14.Order(5, 1.0)])] + bids
        book = _book(bids=bids, asks=[t14.Level(100.06, [t14.Order(2, 2.5)])])
        on, ref = _agent(), _ref()
        for a in (on, ref):
            t14._rest(a, 23, 3, 0, 100.01, cid=43031, qty=2.0)
        got, want = _out(_request(on, book=book)), _out(_request(ref, book=book))
        assert got == want, best
        assert _touch(got) == ([], [(3, 23)] if cancelled else []), best
        assert _counts(on) == {}, best                               # no new quote: S12b never ran


def test_the_improved_order_leaves_post_only_with_v617s_flags_size_and_client_id():
    on, ref = _agent(), _ref()
    resp, ref_resp = _request(on, book=_book(100.00, 100.07)), _request(ref, book=_book(100.00, 100.07))
    mine = [ix for ix in resp.instructions if getattr(ix, "clientOrderId", None) == 43031]
    theirs = [ix for ix in ref_resp.instructions if getattr(ix, "clientOrderId", None) == 43031]
    assert len(mine) == len(theirs) == 1
    m, t = mine[0], theirs[0]
    assert (m.price, t.price) == (100.01, 100.00)
    for attr in ("postOnly", "stp", "timeInForce", "quantity", "direction", "bookId", "type"):
        assert getattr(m, attr) == getattr(t, attr), attr
    assert m.postOnly is True and m.stp == "CANCEL_BOTH" and m.timeInForce == "GTT"


# ---- 4. the V6_31_STATE row ---------------------------------------------------------------------------------------------

def test_the_state_row_comes_at_the_first_pass_every_100_ticks_and_at_the_first_error():
    agent = _agent()
    flag = {"boom": False}
    real = ti.improved_touch_price

    def maybe(*a, **k):
        if flag["boom"]:
            raise RuntimeError("boom")
        return real(*a, **k)
    agent2 = _agent(names=dict(EXTRA, v6_31_improved_touch_price=maybe))
    for a in (agent, agent2):
        for i, tick in enumerate((1, 2, 99, 100, 101)):
            a._tick = tick
            _request(a, ts=NOW + i * S, wall=1000.0 + i)
    rows = _rows(agent)
    assert [(r["reason"], r["tick"]) for r in rows] == [("start", 1), ("periodic", 100)]
    assert tuple(k for k in rows[0] if k not in ("reason", "tick")) == FIELDS
    first = rows[0]
    assert (first["version"], first["save_version"], first["touch_improve_on"], first["single_write_save_on"]) == \
        ("touch_improve_v6_31", "single_write_save_v6_31", 1, 1)
    assert (first["improved_buy"], first["join_not_inside"], first["staged_saves"], first["errors"]) == (1, 0, 0, 0)
    for k, (tick, boom) in enumerate(((102, True), (103, True), (104, False), (105, True))):
        flag["boom"] = boom
        agent2._tick = tick
        _request(agent2, ts=NOW + (5 + k) * S, wall=1005.0 + k)
    assert [(r["reason"], r["tick"]) for r in _rows(agent2)] == [("start", 1), ("periodic", 100), ("error", 102),
                                                                  ("error", 105)]
    assert _rows(agent2)[-1]["errors"] == 3


def test_the_row_never_fails_the_request_and_waits_for_the_reply():
    bad, ref = _agent(), _agent()
    emitted = bad.emitted
    bad._emit = lambda event, force=False, **kw: (1 / 0) if event == "V6_31_STATE" else emitted.append((event, kw))
    bad._tick = ref._tick = 1
    assert _out(_request(bad)) == _out(_request(ref)) and bad._v6_31_errors == 1 and _rows(bad) == []
    deferred = _agent()
    work = []
    deferred._v629_request_work = types.SimpleNamespace(add=lambda name, fn: work.append((name, fn)))
    deferred._tick = 1
    _request(deferred)
    names = [n for n, _fn in work]
    assert names[-3:] == ["v6_29_state_row", "v6_30_state_row", "v6_31_state_row"] and _rows(deferred) == []
    dict(work)["v6_31_state_row"]()
    assert _rows(deferred)[0]["reason"] == "start"


def test_off_the_row_says_so_and_an_agent_without_the_build_has_none():
    off = _agent(s12b=False, save=False)
    off._tick = 1
    _request(off)
    row = _rows(off)[0]
    assert (row["touch_improve_on"], row["single_write_save_on"], row["improved_buy"], row["errors"]) == (0, 0, 0, 0)
    older = _ref()
    older._tick = 1
    _request(older)
    assert not _rows(older)
    saved = _agent()                                                 # the save's tally rides on the same row
    saved._v6_31_counts.update(staged_saves=3, stage_incomplete=1)
    saved._v6_31_errors = 2
    saved._tick = 1
    _request(saved)
    row = _rows(saved)[0]
    assert (row["staged_saves"], row["stage_incomplete"], row["errors"], row["improved_buy"]) == (3, 1, 2, 1)


# ---- 5. the single-write save: the REAL frozen base save and path under Simple's real path and save ----------------------

class _Killed(BaseException):
    """A stop the process cannot answer (pm2's SIGKILL 1.6 s after its SIGINT): no except clause catches it."""


class _Stats:
    count = 0

    @staticmethod
    def from_state(row):
        return _Stats()

    def as_state(self):
        return {"n": 1}


class _Recorder:
    """os, with every os.replace recorded as (source, destination)."""

    def __init__(self):
        self.replaced = []
        self.path = os.path

    def __getattr__(self, name):
        return getattr(os, name)

    def replace(self, src, dst):
        self.replaced.append((str(src), str(dst)))
        return os.replace(src, dst)


_HOSTS: dict = {}


def _host_class(osmod=os, extra=None):
    """Strategy1_Research's REAL _research_save_session / _research_session_path as the base; Simple's REAL
    _research_session_path, _research_save_session, _v6_29_saved and v6.31's _v6_31_staged_save / _v6_31_count on
    top."""
    key = (id(osmod), None if extra is None else tuple(sorted((k, id(v)) for k, v in extra.items())))
    if key not in _HOSTS:
        scope = {"json": json, "os": osmod, "build_payload": ss.build_payload, "state_filename": ss.state_filename,
                 "SessionIdentity": ss.SessionIdentity}
        base = "class _Base:\n" + "\n".join(textwrap.indent(_base_src(n), "    ") for n in (
            "_research_save_session", "_research_session_path")) + "\n"
        exec(compile(base, "<Strategy1_Research session>", "exec"), scope)
        scope.update({"DIRECT_QUALITY_VERSION": "q", "MakerLifecycleStats": _Stats, "OWNER_KEY": si.OWNER_KEY,
                      "owner_record": si.owner_record, "uid_session_path": si.uid_session_path,
                      "V503_NEWCOMER_GATE_VERSION": "v503", "V504_MIRROR_SESSION_KEY": "v504m",
                      "V68_DEEP_SESSION_KEY": "deep_layer_v6_8", "V621_PACE_SESSION_KEY": "pace",
                      "mirror_session_state": lambda s: {"start": s}, "v68_deep_state": lambda layer: {}})
        scope.update(V6_31_NAMES)
        scope.update(extra or {})
        body = "class _Host(_Base):\n" + "\n".join(textwrap.indent(_src(n), "    ") for n in (
            "_research_session_path", "_research_save_session", "_v6_29_saved", "_v6_31_staged_save",
            "_v6_31_count")) + "\n"
        exec(compile(body, "<v6.31 session host>", "exec"), scope)
        _HOSTS[key] = scope["_Host"]
    return _HOSTS[key]


def _host(tmp, *, save=True, tick=1, last=-1, uid=115, per_uid=True, osmod=os, extra=None):
    h = _host_class(osmod, extra)()
    h._research_session_dir = str(tmp)
    h.uid, h._tick = uid, tick
    h._research_session_identity = ss.SessionIdentity(simulation_id=SIM, network="finney", netuid=79)
    h._research_session_save_every_n, h._research_session_last_save_tick = 100, last
    h._research_refresh_rolling_kappa_cache = lambda: None
    h._v503_gate_session_state = lambda: {"open": True}
    h._v61_on = lambda: False
    h._v504_mirror = {"start_ts": 7}
    h.research_v504_session_per_uid = per_uid
    h.research_v621_pace_persist = h.research_v68_deep_persist = False
    h._v6_29_save_due, h._v6_29_save_done, h._v6_29_errors = False, None, 0
    h.research_v631_single_write_save = save
    h._v6_31_stage, h._v6_31_counts, h._v6_31_errors = None, {}, 0
    return h


def _real(h):
    return Path(h._research_session_path(h._research_session_identity))


def _stage(h):
    return Path(str(_real(h)) + ti.STAGE_SUFFIX)


def _verdict(path, uid=115):
    return si.owner_verdict(json.loads(Path(path).read_text()), uid)


def _raise(exc):
    def f(*a, **k):
        raise exc
    return f


def test_the_host_runs_the_frozen_base_save_which_writes_through_the_session_path():
    save = _base_src("_research_save_session")
    for line in ("os.makedirs(os.path.dirname(self._research_session_path(identity)) or \".\", exist_ok=True)",
                 "path = self._research_session_path(identity)", 'tmp = path + f".tmp.{os.getpid()}"',
                 "os.replace(tmp, path)", "self._research_session_last_save_tick = tick",
                 "if not force and last >= 0 and tick - last < every:"):
        assert save.count(line) == 1, line
    assert "self._research_session_path(identity)" in _base_src("_research_read_session")
    assert _src("_research_session_path").count("super()._research_session_path(identity)") == 1


def test_a_complete_stage_replaces_the_session_file_in_one_rename_with_v630s_bytes(tmp_path):
    rec_on, rec_off = _Recorder(), _Recorder()
    (tmp_path / "on").mkdir()
    (tmp_path / "off").mkdir()
    on, off = _host(tmp_path / "on", osmod=rec_on), _host(tmp_path / "off", save=False, osmod=rec_off)
    for h in (on, off):
        h._research_save_session(force=False)
    assert _real(on).name == _real(off).name == f"research_session_finney_79_{SIM}.uid115.json"
    assert _real(on).read_bytes() == _real(off).read_bytes()                      # the same payload, v6.30's
    payload = json.loads(_real(on).read_text())
    assert si.OWNER_KEY in payload and "direct_maker_quality_a1_5_1" in payload and _verdict(_real(on)) == "OWN"
    real, stage = str(_real(on)), str(_stage(on))
    assert [d for _s, d in rec_on.replaced] == [stage, stage, real] and rec_on.replaced[-1] == (stage, real)
    assert [s for s, _d in rec_on.replaced[:2]] == [f"{stage}.tmp.{os.getpid()}", f"{stage}.direct.tmp.{os.getpid()}"]
    assert [d for _s, d in rec_off.replaced] == [str(_real(off))] * 2                 # v6.30: the file twice
    assert not _stage(on).exists() and sorted(p.name for p in (tmp_path / "on").iterdir()) == [_real(on).name]
    assert (on._v6_31_counts, on._v6_31_errors, on._v6_31_stage) == ({"staged_saves": 1}, 0, None)
    assert off._v6_31_counts == {} and on._research_session_last_save_tick == 1


def test_a_stop_between_v630s_two_writes_leaves_an_owner_less_file_on_v630_and_the_whole_one_on_v631(tmp_path):
    """Tick 1 saves a whole file; at tick 101 the process is stopped right after the base's write (inside v6.30's
    rewrite).  v6.30: the session file is the base payload alone -- UNOWNED, the next start refuses it (UID 115, 10-14).
    v6.31: the session file is tick 1's, byte for byte (OWN); only the stage holds the half payload, and the next
    process removes it before its own save and never renames it."""
    for save in (False, True):
        d = tmp_path / str(save)
        d.mkdir()
        h = _host(d, save=save)
        h._research_save_session(force=False)
        before = _real(h).read_bytes()
        h._tick, h._v503_gate_session_state = 101, _raise(_Killed())
        try:
            h._research_save_session(force=False)
            raise AssertionError("the stop was swallowed")
        except _Killed:
            pass
        if not save:
            assert _verdict(_real(h)) == "UNOWNED" and _real(h).read_bytes() != before
            continue
        assert _real(h).read_bytes() == before and _verdict(_real(h)) == "OWN"
        assert _stage(h).exists() and _verdict(_stage(h)) == "UNOWNED" and h._v6_31_stage is None
        nxt = _host(d, tick=1)                                       # the next process
        nxt._research_save_session(force=False)
        assert not _stage(nxt).exists() and _verdict(_real(nxt)) == "OWN" and nxt._v6_31_counts == {"staged_saves": 1}


def test_a_stale_stage_is_removed_first_and_never_renamed(tmp_path):
    h = _host(tmp_path)
    h._research_save_session(force=False)
    before = _real(h).read_bytes()
    stale = {"schema": 1, si.OWNER_KEY: si.owner_record(115), "direct_maker_quality_a1_5_1": {}, "stale": True}
    _stage(h).write_text(json.dumps(stale))
    h._tick = 2                                                      # the cadence: nothing to save
    h._research_save_session(force=False)
    assert not _stage(h).exists() and _real(h).read_bytes() == before and h._v6_31_counts == {"staged_saves": 1}
    _stage(h).write_text(json.dumps(stale))
    h._tick = 101
    h._research_save_session(force=False)
    payload = json.loads(_real(h).read_text())
    assert "stale" not in payload and payload["v503"] == {"open": True} and not _stage(h).exists()
    assert h._v6_31_counts == {"staged_saves": 2}


def test_an_exception_inside_v630s_save_leaves_the_session_file_byte_identical_and_never_raises(tmp_path):
    for where in ("rewrite", "base"):
        for save in (False, True):
            d = tmp_path / f"{where}{save}"
            d.mkdir()
            h = _host(d, save=save)
            h._research_save_session(force=False)
            before = _real(h).read_bytes()
            h._tick = 101
            if where == "rewrite":
                h._v503_gate_session_state = _raise(RuntimeError("boom"))      # not one v6.30's rewrite catches
            else:
                h._research_refresh_rolling_kappa_cache = _raise(RuntimeError("boom"))   # before the base writes
            if not save:
                try:
                    h._research_save_session(force=False)
                    raise AssertionError("v6.30 raised nothing")
                except RuntimeError:
                    pass
                assert (_verdict(_real(h)) == "UNOWNED") == (where == "rewrite"), where
                continue
            h._research_save_session(force=False)                    # never raises
            assert _real(h).read_bytes() == before and not _stage(h).exists(), where
            assert (h._v6_31_errors, h._v6_31_stage, h._v6_31_counts) == (1, None, {"staged_saves": 1}), where
            h._v503_gate_session_state = lambda: {"open": True}
            h._research_refresh_rolling_kappa_cache = lambda: None
            h._tick = 202
            h._research_save_session(force=False)                    # the next save is staged again
            assert h._v6_31_counts == {"staged_saves": 2} and json.loads(_real(h).read_text())["v503"] == {"open": True}


def test_an_incomplete_stage_leaves_the_session_file_byte_identical(tmp_path):
    """v6.30's rewrite catches a ValueError and writes nothing: the stage holds the base payload alone -- incomplete,
    removed, the session file untouched (v6.30 left that payload AS the session file: owner-less)."""
    for save in (False, True):
        d = tmp_path / str(save)
        d.mkdir()
        h = _host(d, save=save)
        h._research_save_session(force=False)
        before = _real(h).read_bytes()
        h._tick, h._v503_gate_session_state = 101, _raise(ValueError("bad"))
        h._v6_29_save_due = True
        h._research_save_session(force=False)
        assert h._v6_29_save_due is True and h._v6_29_save_done is None            # v6.29 waits, as on v6.30
        if not save:
            assert _verdict(_real(h)) == "UNOWNED"
            continue
        assert _real(h).read_bytes() == before and not _stage(h).exists() and h._v6_31_stage is None
        assert h._v6_31_counts == {"staged_saves": 1, "stage_incomplete": 1} and h._v6_31_errors == 0


def _writer_host(tmp, text, *, per_uid=True):
    """The staged save around a body that writes ``text`` where v6.30's save would: its completeness rule alone."""
    cls = _host_class()

    class _W(cls):
        def _research_save_session(self, force=False):
            if bool(getattr(self, "research_v631_single_write_save", False)) and self._v6_31_staged_save(force):
                return
            Path(self._research_session_path(self._research_session_identity)).write_text(self._text)
    h = _host(tmp, per_uid=per_uid)
    h.__class__ = _W
    h._text = text
    return h


def test_only_a_stage_that_parses_with_its_owner_and_the_maker_quality_replaces_the_file(tmp_path):
    owner, quality = {si.OWNER_KEY: si.owner_record(115)}, {"direct_maker_quality_a1_5_1": {}}
    cases = (("whole", dict(schema=1, **owner, **quality), True, True),
             ("no owner", dict(schema=1, **quality), True, False),
             ("no maker quality", dict(schema=1, **owner), True, False),
             ("neither", {"schema": 1}, True, False),
             ("not a mapping", [owner, quality], True, False),
             ("not json", "{", True, False),
             ("empty", "", True, False),
             ("per-UID off: no owner needed", dict(schema=1, **quality), False, True),
             ("per-UID off, no maker quality", {"schema": 1}, False, False))
    for i, (name, payload, per_uid, renamed) in enumerate(cases):
        d = tmp_path / str(i)
        d.mkdir()
        text = payload if isinstance(payload, str) else json.dumps(payload)
        h = _writer_host(d, text, per_uid=per_uid)
        _real(h).write_text("PREVIOUS")
        h._research_save_session(force=False)
        assert _real(h).read_text() == (text if renamed else "PREVIOUS"), name
        assert not _stage(h).exists() and h._v6_31_stage is None and h._v6_31_errors == 0, name
        assert h._v6_31_counts == ({"staged_saves": 1} if renamed else {"stage_incomplete": 1}), name


def test_the_cadence_skip_writes_nothing_and_renames_nothing(tmp_path):
    rec = _Recorder()
    h = _host(tmp_path, osmod=rec)
    h._research_save_session(force=False)
    before, stat = _real(h).read_bytes(), _real(h).stat().st_mtime_ns
    n = len(rec.replaced)
    for tick in range(2, 101):
        h._tick = tick
        h._research_save_session(force=False)
    assert len(rec.replaced) == n and _real(h).read_bytes() == before and _real(h).stat().st_mtime_ns == stat
    assert not _stage(h).exists() and h._v6_31_counts == {"staged_saves": 1} and h._research_session_last_save_tick == 1
    h._tick = 50
    h._research_save_session(force=True)                             # forced: staged and renamed
    assert h._v6_31_counts == {"staged_saves": 2} and h._research_session_last_save_tick == 50


def test_the_path_is_the_stage_only_inside_the_staged_run_and_a_reentered_call_is_v630s_body(tmp_path):
    h = _host(tmp_path)
    real = _real(h)
    seen = []
    orig = type(h)._research_session_path

    def spy(self, identity):
        seen.append((orig(self, identity), self._v6_31_stage))
        return seen[-1][0]
    h._research_session_path = types.MethodType(spy, h)
    h._research_save_session(force=False)
    stage = str(real) + ti.STAGE_SUFFIX
    assert seen[0] == (str(real), None)                              # the staged save asks for the real path first
    assert seen[1:] and all(p == stage == s for p, s in seen[1:])    # then every read and write is the stage's
    del h._research_session_path
    assert h._research_session_path(h._research_session_identity) == str(real) and h._v6_31_stage is None
    h._v6_31_stage = "elsewhere"                                     # re-entered: False, the caller runs v6.30's body
    assert h._v6_31_staged_save(False) is False and h._research_session_path(None) == "elsewhere"
    h._v6_31_stage = None
    for attr, value in (("research_v631_single_write_save", False), ("_research_session_identity", None),
                        ("_research_session_identity", ss.SessionIdentity(simulation_id=None)),
                        ("_research_session_identity", ss.SessionIdentity(simulation_id=""))):
        g = _host(tmp_path / "x")
        setattr(g, attr, value)
        assert g._v6_31_staged_save(False) is False and g._v6_31_counts == {} and g._v6_31_stage is None, attr


def test_the_first_flat_launch_save_is_marked_done_by_the_staged_save(tmp_path):
    h = _host(tmp_path)
    h._v6_29_save_due = True
    h._research_save_session(force=False)
    assert (h._v6_29_save_due, h._v6_29_save_done) == (False, 1) and _verdict(_real(h)) == "OWN"
    assert h._v6_31_counts == {"staged_saves": 1}


# ---- 6. wiring ----------------------------------------------------------------------------------------------------------

def test_the_switches_ship_on_after_v630s_keys_and_agree_with_their_agent_defaults():
    params = t617._params()
    for key in ("research_v631_touch_improve", "research_v631_single_write_save"):
        assert f"{key}=1 " in t617._shipped() + " " and params.count(f"{key}=") == 1, key
    assert ('research_v630_s9_lookback=1 \\\nresearch_v631_touch_improve=1 \\\nresearch_v631_single_write_save=1 \\\n'
            in LAUNCHER)                                                                 # v6.32's keys next
    assert " research_v617_touch_quote=1 " in f" {t617._shipped()} "                   # what S12b prices
    init = _src("_init_build_switches")
    lines = (S12B_LINE, "self.research_v631_single_write_save = self._as_bool(", SAVE_SWITCH,
             "self._v6_31_noted = None", "self._v6_31_stage: str | None = None",
             "self._v6_31_counts: dict[str, int] = {}",
             "self._v6_31_errors = 0", "self._v6_31_errors_seen = 0", "self._v6_31_reported = False",
             "self._v6_31_erring = False")
    for line in lines:
        assert init.count(line) == 1, line
    idx = [init.index(x) for x in lines]
    assert init.index("self._v6_29_pages = v6_30_page_history(V6_29PageHistory)") < idx[0] and idx == sorted(idx) \
        and idx[-1] < init.index("self._v69_counts: dict[str, int] = {}")


def test_every_v631_name_the_agent_imports_is_the_modules():
    tree = ast.parse(SIMPLE)
    imp = next(n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "research_v631_touch_improve")
    for alias in imp.names:
        assert V6_31_NAMES[alias.asname or alias.name] is getattr(ti, alias.name), alias.asname
    assert {a.asname or a.name for a in imp.names} == set(V6_31_NAMES)
    assert SIMPLE.count("from research_v631_touch_improve import (") == 1
    assert t623.V6_31_IDENTIFIERS == (set(V6_31_NAMES) | {"research_v631_touch_improve",
                                                          "research_v631_single_write_save"})
    for mod in (t624, t625, t626, t627, t628, t629, t630):
        assert mod.V6_31_IDENTIFIERS is t623.V6_31_IDENTIFIERS, mod.__name__
    bound = [a.asname or a.name for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names]
    for name in V6_31_NAMES:
        assert bound.count(name) == 1, name
    for name in REACHED:                                             # bound at module level, as the harness binds them
        assert bound.count(name) == 1, name
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    names = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
    assert sorted(n for n in names if n.startswith("_v6_31_")) == sorted(METHODS)
    assert '"V6_31_STATE"' in _src("_v6_31_report") and '"v6_31_state_row"' in _src("_v6_31_report")


def test_each_line_is_wired_once_in_its_place():
    for line in (TOUCH_LINE, NOTE_LINE, SAVE_LINE, PATH_LINE, REPORT_LINE, S12B_LINE, SAVE_SWITCH):
        assert SIMPLE.count(line) == 1, line
    touch = _src("_v617_touch")
    assert touch.index("price = v617_wanted_price(toward,") < touch.index('self._v617_count("no_price")') < \
        touch.index('if bool(getattr(self, "research_v631_touch_improve", False)):') < touch.index(TOUCH_LINE) < \
        touch.index("q_ = v620_scaled_quantity(clip, size_scale, min_order)") < touch.index("response.limit_order(")
    body = _src("_v63_pass")
    assert body.index("touch_rows.append(row)") < body.index(NOTE_LINE) < body.index("deep_open = False") < \
        body.index("placed_total += self._v617_book(")
    assert body.index("self._v6_30_report()") < body.index('if getattr(self, "_v6_31_counts", None) is not None:') < \
        body.index(REPORT_LINE) < body.index("return placed_total")
    path = ast.parse(_src("_research_session_path")).body[0]
    first = path.body[0]
    assert isinstance(first, ast.If) and ast.unparse(first.test) == "getattr(self, '_v6_31_stage', None) is not None"
    assert [ast.unparse(s) for s in first.body] == ["return self._v6_31_stage"] and not first.orelse
    save = _src("_research_save_session")
    assert save.index(t629.FORCE_LINE) < save.index(SAVE_LINE) < \
        save.index("super()._research_save_session(force=force)")
    staged = _src("_v6_31_staged_save")
    assert staged.index("stage = real + V6_31_STAGE_SUFFIX") < staged.index("os.unlink(stage)") < \
        staged.index("self._v6_31_stage = stage") < staged.index("self._research_save_session(force=force)") < \
        staged.index("finally:") < staged.index("if not os.path.isfile(stage):") < staged.index("if whole:") < \
        staged.index("os.replace(stage, real)")
    fn = ast.parse(staged).body[0]
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try) and n.finalbody]
    assert len(tries) == 1 and [ast.unparse(s) for s in tries[0].finalbody] == ["self._v6_31_stage = None"]
    assert [ast.unparse(s) for s in tries[0].body] == ["self._research_save_session(force=force)"]
    for name in ("_v617_book", "_v617_fundamental", "_v633_book", "_v6_30_report", "_v6_29_saved", "respond",
                 "_research_final_validate_instructions", "_v504_read_session", "_a191_service_reprice_cancels",
                 "_a191_decide", "_a191_live_exit_row", "_v6214_service_touch_life", "_research_read_session"):
        text = _src(name)
        assert "v6_31" not in text and not any(n in text for n in t623.V6_31_IDENTIFIERS), name


def test_the_module_never_writes_an_order_flag_and_its_statements_name_no_instruction():
    stripped_out = []
    for st in ast.walk(ast.parse(SIMPLE)):
        if isinstance(st, ast.FunctionDef) and st.name.startswith("_v6_31_"):
            stripped_out.append(ast.unparse(st))
    text = "\n".join(stripped_out) + "\n" + "\n".join(ast.unparse(n) for n in ast.parse(MODULE).body[1:])
    for word in ("postOnly", "post_only", "stp", "STP", "timeInForce", "TimeInForce", "limit_order", "cancel_orders",
                 "instructions", "leverage", "OrderDirection"):
        assert not re.search(rf"\b{word}\b", text), word


# ---- 7. v6.30's code, statement by statement (the committed v6.30: f31be6d) ----------------------------------------------

V630_COMMIT = "f31be6d"       # the user's v6.30 commit (10-14 13:26 JST canary, fleet 21:05 JST): k81's FROZEN_3
# v6.32 (S14 research_v632_fee_making, P1 research_v632_state_fundamental) only adds -- its two imports, its _v6_32_
# methods, its switches and state, and statements naming them in respond(), the pass, the deep layer, the touch quote,
# the fundamental's readers and S9's history (an if on a switch or on its state -- S9's keeps its else, v6.31's page
# observation -- or an assignment or call naming one) -- so the method-by-method checks below read v6.31 with v6.32
# taken out (_Drop632; tests/test_research_v6_32.py checks it against the committed v6.31), and t630's _Drop631 takes
# v6.32's additions out with v6.31's.
V6_32_IDENTIFIERS = t623.V6_32_IDENTIFIERS
# v6.33 (D4 research_v633_touch_identity) only adds: the strip takes its additions out as well
# (tests/test_research_v6_33.py checks them against the committed v6.32).
V6_33_IDENTIFIERS = t623.V6_33_IDENTIFIERS
# v6.34 (research_v634_class1_entry) only adds: the strip takes its additions out as well
# (tests/test_research_v6_34.py checks them against v6.33).
V6_34_IDENTIFIERS = t623.V6_34_IDENTIFIERS
# v6.36 (research_v636_step_withdrawal) only adds: the strip takes its additions out as well
# (tests/test_research_v6_36.py checks them against v6.35).
V6_36_IDENTIFIERS = t623.V6_36_IDENTIFIERS
# v6.37 (research_v637_fast_path) only adds: the strip takes its additions out as well
# (tests/test_research_v6_37.py checks them against v6.36).
V6_37_IDENTIFIERS = t623.V6_37_IDENTIFIERS


def _mentions632(node):
    for n in ast.walk(node):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.arg if isinstance(n, (ast.arg, ast.keyword)) else n.value if isinstance(n, ast.Constant) else None
        if isinstance(name, str) and (name in V6_32_IDENTIFIERS or name in V6_33_IDENTIFIERS
                                      or name in V6_34_IDENTIFIERS
                                      or name in V6_36_IDENTIFIERS
                                      or name in V6_37_IDENTIFIERS
                                      or name.startswith(("_v6_32_", "v6_32_", "_v6_33_", "v6_33_",
                                                          "_v6_34_", "v6_34_", "_v6_36_", "v6_36_",
                                                          "_v6_37_", "v6_37_"))):
            return True
    return False


class _Drop632(ast.NodeTransformer):
    """Takes out what v6.32 (and v6.33, v6.34, v6.36 and v6.37 after it) adds and nothing else -- its two imports,
    its _v6_32_ methods, statements naming its identifiers (an if on one keeps its else) -- keeping every docstring:
    v6.31 as committed (fb5bf3c)."""

    def visit_Module(self, node):                      # v6.38's changes undone first (tests/_v638_undo.py)
        _v638_undo.undo(node)
        return self.generic_visit(node)

    def _stmts(self, stmts):
        out = []
        for st in stmts:
            if isinstance(st, ast.ImportFrom) and st.module in ("research_v6_32_fee_making", "research_v6_32_state_fp",
                                                                "research_v6_33_touch_identity",
                                                                "research_v6_34_class1",
                                                                "research_v6_36_step_withdrawal",
                                                                "research_v6_37_fast_path"):
                continue
            if isinstance(st, ast.FunctionDef) and st.name.startswith(("_v6_32_", "_v6_33_", "_v6_34_", "_v6_36_",
                                                                       "_v6_37_")):
                continue
            if isinstance(st, ast.If) and _mentions632(st.test):
                out.extend(self._stmts(st.orelse))
                continue
            st = self.visit(st)
            if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)) and _mentions632(st):
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


def _committed(rel):
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "show", f"{V630_COMMIT}:{rel}"], cwd=ROOT,
                             capture_output=True, text=True, timeout=120, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    except Exception:
        out = None
    if out is None or out.returncode != 0:
        pytest.skip(f"git or {V630_COMMIT} is not available here")
    return out.stdout


CHANGED = {"_init_build_switches", "_v617_touch", "_v63_pass", "_research_save_session", "_research_session_path"}


def test_v631s_strip_takes_out_its_six_methods_and_its_statements_in_five_and_v630_is_under_both_strips():
    """Without git: Simple with v6.31's additions taken out (t630's _Drop631, v6.32's with them) has the same methods as
    v6.31 (Simple with v6.32 taken out, _Drop632) but v6.31's six, and only the switch block, the touch quote, the pass,
    the session path and the session save carry v6.31 statements; v6.30's and v6.29's own methods name nothing of v6.31;
    with v6.30 taken out as well (t629's _Drop630) it is v6.29's (t630 checks that against the committed v6.29)."""
    stripped = t630._Drop631().visit(ast.parse(SIMPLE))
    assert not t630._mentions631(stripped)
    a, b = t629._methods(stripped), t629._methods(_Drop632().visit(ast.parse(SIMPLE)))      # v6.31: v6.32 out
    assert set(b) - set(a) == set(METHODS) and not set(a) - set(b)
    assert {k for k in a if a[k] != b[k]} == CHANGED
    for n in ast.walk(ast.parse(SIMPLE)):
        if isinstance(n, ast.FunctionDef) and n.name.startswith(("_v6_30_", "_v6_29_", "_v6_28_")):
            assert not _mentions631(n), n.name
    assert not t629._mentions630(t629._Drop630().visit(ast.parse(SIMPLE)))


def test_every_v630_statement_is_in_v631_unchanged_and_v631_only_adds():
    old = _committed("agents/strategy/Strategy1_Research_Simple.py")
    assert not t630._mentions631(ast.parse(old)), "v6.30 names a v6.31 identifier: the strip would hide a change"
    stripped = t630._Drop631().visit(ast.parse(SIMPLE))
    assert ast.dump(stripped) == ast.dump(ast.parse(old))
    assert ast.dump(stripped) != ast.dump(ast.parse(SIMPLE))
    for good, bad in (('self._v617_count("no_price")', 'self._v617_count("no_prices")'),
                      ("super()._research_save_session(force=force)", "super()._research_save_session(force=True)"),
                      ('return uid_session_path(path, getattr(self, "uid", None))', "return path")):
        assert SIMPLE.count(good) == 1, good
        mutated = SIMPLE.replace(good, bad)
        assert ast.dump(t630._Drop631().visit(ast.parse(mutated))) != ast.dump(ast.parse(old)), good


def test_only_five_methods_changed_and_six_were_added_and_the_exit_layer_and_frozen_files_are_v630s():
    """The A1.9 exit layer (A1.9.1's reprice post-pass, its classifier's callers) and v6.2.14's touch life are v6.30's
    byte for byte: S12b changes the price of a NEW touch quote only (k88 CHECK: no exit-layer reason fires because a
    quote is priced inside the others' best -- an improved quote IS the raw best, so the classifier's drift is 0 or
    favourable)."""
    old = _committed("agents/strategy/Strategy1_Research_Simple.py")
    a, b = t629._methods(ast.parse(old)), t629._methods(_Drop632().visit(ast.parse(SIMPLE)))  # v6.31: v6.32 out
    assert set(b) - set(a) == set(METHODS) and not set(a) - set(b)
    assert {k for k in a if a[k] != b[k]} == CHANGED
    for k in ("_v617_book", "_v617_fundamental", "_v617_cancel", "_v6_30_report", "_v6_30_snapshot", "_v6_29_saved",
              "_v633_book", "_a191_service_reprice_cancels", "_a191_decide", "_a191_live_exit_row",
              "_a19_is_entry_quote_row", "_v611_seed_comparand", "_v623_resting_floor_bps", "_v6214_service_touch_life",
              "respond", "_research_final_validate_instructions", "_v504_read_session", "_research_read_session",
              "_research_clear_session_observations"):
        assert a[k] == b[k], k
    for other in ("Strategy1_Research.py", "Strategy1.py", "Strategy1_Debug.py", "research_v617_touch_quote.py",
                  "research_v6210_touch_improve.py", "research_direct_exit_refresh.py", "research_v6214_touch_life.py",
                  "research_v5_session_identity.py", "research_session_state.py", "research_v630_identity_profile.py",
                  "research_contract_guard.py", "research_v633_deep_layer.py", "research_v6_29_touch_move.py"):
        assert _committed(f"agents/strategy/{other}") == (STRATEGY / other).read_text(), other


def test_the_launcher_only_adds_to_v630s():
    """The committed v6.30 launcher line by line: three lines extended (the flag defaults, the v6_3_0 arm, PARAMS' last
    key), the rest added -- the v6.31 block, two PARAMS keys and its gate entry.  v6.32 extends the same three lines and
    adds its block, three PARAMS keys and its gate entry."""
    launcher = _v638_undo.launcher_v637(LAUNCHER)               # v6.37's launcher: v6.38's edits undone
    old, new = _committed("run_strategy1_research_simple_multi.sh").splitlines(), launcher.splitlines()
    removed, added = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += old[i1:i2]
        if tag in ("replace", "insert"):
            added += new[j1:j2]
    defaults = next(x for x in old if x.startswith("A19X_BUILD=0;"))
    arm = next(x for x in old if x.startswith("  strategy1_direct_v6_3_0)"))
    assert removed == [defaults, arm, 'research_v630_s9_lookback=1"']
    block = []
    for head in ("# v6.31.", "# v6.32.", "# v6.33.", "# v6.34.", "# v6.36.", "# v6.37."):
        start = launcher.index(head)
        block += launcher[start:launcher.index("\nfi\n", start) + len("\nfi\n")].splitlines() + [""]
    assert sorted(added) == sorted([defaults + "; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0; V6_36_BUILD=0; V6_37_BUILD=0",
                                    arm + "; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1; V6_36_BUILD=1; V6_37_BUILD=1",
                                    "research_v630_s9_lookback=1 \\", "research_v631_touch_improve=1 \\",
                                    "research_v631_single_write_save=1 \\", "research_v632_fee_making=1 \\",
                                    "research_v632_fee_weight=0.005 \\", "research_v632_state_fundamental=1 \\",
                                    "research_v633_touch_identity=1 \\",
                                    "research_v634_class1_entry=0 \\",
                                    "research_v636_step_withdrawal=1 \\",
                                    'research_v637_fast_path=1"',
                                    "      tests/test_research_v6_31.py \\", "      tests/test_research_v6_32.py \\",
                                    "      tests/test_research_v6_33.py \\",
                                    "      tests/test_research_v6_34.py \\",
                                    "      tests/test_research_v6_36.py \\",
                                    "      tests/test_research_v6_37.py \\",
                                    *block])


# ---- 8. the launcher -----------------------------------------------------------------------------------------------------

def test_the_v631_block_is_gated_preflighted_and_listed():
    assert "; V6_28_BUILD=0; V6_29_BUILD=0; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0; V6_36_BUILD=0; V6_37_BUILD=0; V6_38_BUILD=0\n" in LAUNCHER
    arm = next(x for x in LAUNCHER.splitlines() if x.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_29_BUILD=1; V6_30_BUILD=1; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1; V6_36_BUILD=1; V6_37_BUILD=1; V6_38_BUILD=1"), arm
    assert LAUNCHER.count('if [[ "${V6_31_BUILD:-0}" == "1" ]]; then') == 1 and LAUNCHER.count("V6_31_BUILD=") == 2
    assert "      tests/test_research_v6_30.py \\\n      tests/test_research_v6_31.py \\\n" in LAUNCHER
    assert LAUNCHER.index('echo "[preflight] v6.30 identity profile PASS') < LAUNCHER.index("# v6.31.") < \
        LAUNCHER.index('echo "[preflight] v6.31 touch improve + single-write save PASS') < \
        LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')
    head = LAUNCHER[LAUNCHER.index("# v6.31."):LAUNCHER.index('if [[ "${V6_31_BUILD:-0}" == "1" ]]; then')]
    for needle in ("STRUCTURAL", "imp3=1", "UNOWNED_REFUSED", "canary", "research_v617_touch_quote=1"):
        assert needle in head, needle


def _block():
    start = LAUNCHER.index('if [[ "${V6_31_BUILD:-0}" == "1" ]]; then')
    return LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.31 touch improve + single-write save PASS', start)]


_PATTERN = t629._PATTERN
FILES = ("Strategy1_Research_Simple.py", "research_v631_touch_improve.py", "research_v6210_touch_improve.py")


def test_every_v631_preflight_pattern_is_in_the_file_it_checks():
    pats = re.findall(_PATTERN, _block())
    assert len(pats) == 19, pats
    for needle, name, _msg in pats:
        assert name in FILES and (STRATEGY / name).read_text().count(needle) == 1, (needle, name)
    assert {p[0] for p in pats} >= {"from research_v631_touch_improve import (", S12B_LINE, SAVE_SWITCH, NOTE_LINE,
                                    TOUCH_LINE, SAVE_LINE, PATH_LINE, REPORT_LINE, "os.replace(stage, real)",
                                    "| v633_own_client_ids(book_id) | set(v617_client_ids(book_id))"}


def _run_block(params, agent_path=STRATEGY, build="1"):
    start = LAUNCHER.index("# v6.31.")
    stop = LAUNCHER.index('echo "[preflight] v6.31 touch improve + single-write save PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_31_BUILD": build}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=120)


def test_the_v631_block_passes_the_shipped_params_and_refuses_a_build_without_a_switch_or_the_touch_quote():
    shipped = t617._shipped()
    out = _run_block(shipped)
    assert out.returncode == 0 and out.stderr == "", out.stderr
    assert out.stdout.startswith("[preflight] v6.31 touch improve + single-write save PASS (S12b: a new touch quote"), \
        out.stdout
    for key in ("research_v631_touch_improve", "research_v631_single_write_save"):
        for bad in (f"{key}=0", "", f"{key}=true", f"{key}=11", f"x{key}=1"):
            out = _run_block(f" {shipped} ".replace(f" {key}=1 ", f" {bad} "))
            assert out.returncode == 1 and f"without {key}=1" in out.stderr and "PASS" not in out.stdout, (key, bad)
    out = _run_block(f" {shipped} ".replace(" research_v617_touch_quote=1 ", " research_v617_touch_quote=0 "))
    assert out.returncode == 1 and "v6.31 build without research_v617_touch_quote=1 in PARAMS" in out.stderr
    out = _run_block("", build="0")                                   # another build: not run
    assert out.returncode == 0 and out.stdout == "" and out.stderr == ""


def test_the_v631_block_refuses_a_tree_without_each_line_it_checks(tmp_path):
    cases = re.findall(_PATTERN, _block())
    assert len(cases) == 19
    for i, (needle, name, msg) in enumerate(cases):
        tmp = tmp_path / str(i)
        tmp.mkdir()
        for n in FILES:
            shutil.copy(STRATEGY / n, tmp / n)
        text = (tmp / name).read_text()
        assert text.count(needle) == 1, needle
        (tmp / name).write_text(text.replace(needle, "pass  # removed", 1))
        out = _run_block(t617._shipped(), agent_path=tmp)
        assert out.returncode == 1 and msg in out.stderr and "PASS" not in out.stdout, (needle, out.stderr)
