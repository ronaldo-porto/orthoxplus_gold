"""v6.25: S5 the fundamental-lean inventory target -- research_v625_lean.

v6.24 + one switch (research_v625_lean, default on) and two values (research_v625_lean_slope = 1.0 base per gap unit,
research_v625_lean_cap = 48 base).  Per book and request, with a fresh fundamental of this simulation at least S1's gap
(research_v623_against_gap_bps, 2.3 bps) from the mid, tau = sign(fp - mid) x min(cap, slope x |gap| / gap) and every
MAKING decision of the pass reads (inventory - tau) where v6.24 read the inventory: the touch quote's adding test and
bound, S1's adding test, the v6.19 surplus hold, pacing's adding side, the deep bound, v6.12's half depth and v6.6's add
spacing (its fill-side record with the previous request's tau).  The TRUE inventory stays for the v6.3.3 gate and the
v6.3 fallback, the final validator, the A1.9 exit layer, the dust paths and every ledger.  Off, on a book of another
asset class, or on a book whose tau is 0 now and was 0 at the previous request, the build is v6.24.

No test here touches the host's caches or the network: the fundamental comes from t616's injected feed.
"""
import ast
import difflib
import math
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v625_lean as ln  # noqa: E402
import research_v612_reduce_depth as d612  # noqa: E402
import research_v617_touch_quote as tq  # noqa: E402
import research_v633_deep_layer as dl  # noqa: E402
import research_v63_trend_target as tt  # noqa: E402
import research_v65_deep_clips as dc  # noqa: E402
import research_v69_deep_ladder as d69  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_16 as t616  # noqa: E402
import test_research_v6_17 as t617  # noqa: E402
import test_research_v6_22 as t622  # noqa: E402
import test_research_v6_23 as t623  # noqa: E402
from _harness import extractor  # noqa: E402
import _v638_undo  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
MODULE = (STRATEGY / "research_v625_lean.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000
NOW = t623.NOW
BUY, SELL = dl.SIDE_BUY, dl.SIDE_SELL
MID = 100.015                                   # t63's book 3 (100.00 / 100.03)
ABOVE, BELOW = t623.ABOVE, t623.BELOW           # 38 / 42 bps from the mid
SWITCH_LINE = 'self.research_v625_lean = self._as_bool(getattr(self.config, "research_v625_lean", True))'
LEAN_LINE = "v6_25_inv, v6_25_fills = self._v6_25_lean(book_id, raw_bid, raw_ask, cfg, inv, now_ts, v6_25_req)"
REPORT_LINE = "self._v6_25_report(v6_25_req, v6_25_errors)"
FIELDS = ("version", "lean_on", "slope", "cap", "gap_bps", "books", "tau_books", "mean_abs_tau", "mean_abs_inv_minus_tau",
          "max_abs_tau", "fresh_fp_share", "errors")
V625_NAMES = {"V625_LEAN_CAP": ln.LEAN_CAP, "V625_LEAN_SLOPE": ln.LEAN_SLOPE, "V625_LEAN_VERSION": ln.V625_LEAN_VERSION,
              "v625_lean_target": ln.lean_target, "v625_lean_request": ln.new_request, "v625_lean_note": ln.note_book,
              "v625_parse_lean_cap": ln.parse_cap, "v625_parse_lean_slope": ln.parse_slope,
              "v625_lean_summary": ln.summary}
EXTRA = dict(t623.EXTRA, **V625_NAMES)
METHODS = ("_v6_25_lean", "_v6_25_report", "_v6_25_snapshot")
PASS = ("_v63_pass", "_v633_book", "_v617_touch", "_v617_book")


def _tau(fp, mid=MID, gap=2.3, slope=1.0, cap=48.0):
    return ln.lean_target(mid, fp, gap, slope, cap)


# ---- 1. the module ------------------------------------------------------------------------------------------------------

def test_the_constants_are_the_specified_ones_and_each_is_labelled():
    assert ln.V625_LEAN_VERSION == "lean_v6_25"
    assert (ln.LEAN_SLOPE, ln.LEAN_CAP) == (1.0, 48.0)
    for name, label in (("LEAN_SLOPE", "# OBSERVED"), ("LEAN_CAP", "# STRUCTURAL")):
        line = next(x for x in MODULE.splitlines() if x.startswith(name + " = "))
        assert label in line, name
    assert set(ln.__all__) == {"LEAN_CAP", "LEAN_SLOPE", "V625_LEAN_VERSION", "lean_target", "new_request",
                               "note_book", "parse_cap", "parse_slope", "summary"}
    assert "STRUCTURAL:" in MODULE and "OBSERVED:" in MODULE


def test_the_cap_is_the_bound_a_book_holds_behind_its_line_and_the_slope_is_no_order_size_of_the_agent():
    """STRUCTURAL cap: 48 base = v6.10's behind-the-line bound (24 deep clips) x the deep clip (the v6.3 clip base 1.0 x
    v6.5's multiple 2.0) -- the deep layer's and the touch quote's bound while a book is at or below its volume line.
    OBSERVED slope: 1.0 base is the v6.3 clip base, but no live order rests at it (deep and touch clip 2.0, ladder 2.0 /
    4.0), so the slope is the best makers' measured lean (k57 C1K48), not one of the agent's sizes."""
    behind = float(re.search(r'^BEHIND_MAX_CLIPS="\$\{BEHIND_MAX_CLIPS:-([0-9.]+)\}"', LAUNCHER, re.M).group(1))
    mult = float(re.search(r'^DEEP_CLIP_MULT="\$\{DEEP_CLIP_MULT:-([0-9.]+)\}"', LAUNCHER, re.M).group(1))
    base = float(re.search(r"^research_v63_clip_base=([0-9.]+) ", LAUNCHER, re.M).group(1))
    deep_clip = dc.deep_clip(base, mult, 0.25)
    assert (behind, mult, base, deep_clip) == (24.0, 2.0, 1.0, 2.0)
    assert behind * deep_clip == ln.LEAN_CAP
    agent = t623._agent()
    deep = agent._v633_deep
    assert deep.clip == 2.0 and agent._v610_bound_base(deep, False) == ln.LEAN_CAP          # the harness's layer too
    sizes = {tq.CLIP_DEEP_CLIPS * deep_clip} | {d69.level_quantity(deep_clip, k) for k in (1, 2)} | {deep_clip}
    assert sizes == {2.0, 4.0} and ln.LEAN_SLOPE not in sizes


def test_the_switch_values_parse_to_a_positive_number_or_no_lean():
    for raw, want in ((1.0, 1.0), ("1.0", 1.0), (48, 48.0), ("48", 48.0), (0.5, 0.5), (2, 2.0)):
        assert ln.parse_slope(raw) == want and ln.parse_cap(raw) == want, raw
    for junk in (0, 0.0, -1.0, "-1", float("nan"), float("inf"), float("-inf"), None, True, False, "x", "", [], {}):
        assert ln.parse_slope(junk) == 0.0 and ln.parse_cap(junk) == 0.0, junk


def test_the_lean_is_the_gap_in_units_of_the_gate_toward_the_fundamental_capped():
    above = MID * math.exp(23e-4)                                        # 23 bps above: 10 units of 2.3 bps
    below = MID * math.exp(-23e-4)
    assert _tau(above) == pytest.approx(10.0) and _tau(below) == pytest.approx(-10.0)     # +: fp above, a LONG target
    assert _tau(above, slope=2.0) == pytest.approx(20.0) and _tau(above, slope=0.5) == pytest.approx(5.0)
    assert _tau(above, gap=4.6) == pytest.approx(5.0)                    # the unit is the gate
    assert _tau(above, cap=7.5) == 7.5 and _tau(below, cap=7.5) == -7.5               # capped in base
    assert _tau(MID * math.exp(0.5)) == 48.0 and _tau(MID * math.exp(-0.5)) == -48.0   # 5,000 bps: the cap
    assert _tau(MID * math.exp(0.5), slope=2.0) == 48.0                  # the cap is in base whatever the slope
    assert _tau(ABOVE) == pytest.approx(math.log(ABOVE / MID) * 1e4 / 2.3)
    assert _tau(BELOW) == pytest.approx(math.log(BELOW / MID) * 1e4 / 2.3) and _tau(BELOW) < 0


def test_inside_the_gate_at_the_mid_or_without_a_usable_input_there_is_no_lean():
    g = abs(math.log(100.02 / MID)) * 1e4                                # 0.5 bps
    assert g < 2.3 and _tau(100.02) == 0.0 and _tau(100.00) == 0.0 and _tau(MID) == 0.0      # 0.5 / -1.5 bps: inside
    assert _tau(100.04) == pytest.approx(math.log(100.04 / MID) * 1e4 / 2.3) and _tau(100.04) > 1.0   # 2.5 bps: out
    edge = abs(math.log(ABOVE / MID)) * 1e4
    assert ln.lean_target(MID, ABOVE, edge, 1.0, 48.0) == 1.0           # exactly one unit at the gate
    assert ln.lean_target(MID, ABOVE, edge * (1 + 1e-9), 1.0, 48.0) == 0.0
    assert ln.lean_target(MID, BELOW, abs(math.log(BELOW / MID)) * 1e4, 1.0, 48.0) == -1.0
    for bad in (None, 0.0, -1.0, float("nan"), float("inf"), "x", True, [1.0]):
        assert ln.lean_target(bad, ABOVE, 2.3, 1.0, 48.0) == 0.0, bad
        assert ln.lean_target(MID, bad, 2.3, 1.0, 48.0) == 0.0, bad
        assert ln.lean_target(MID, ABOVE, bad, 1.0, 48.0) == 0.0, bad
        assert ln.lean_target(MID, ABOVE, 2.3, bad, 48.0) == 0.0, bad
        assert ln.lean_target(MID, ABOVE, 2.3, 1.0, bad) == 0.0, bad
    assert ln.lean_target("100.015", "100.40", "2.3", "1.0", "48") == pytest.approx(_tau(ABOVE))   # numbers as text


def test_the_lean_is_vm657s_ltau_2():
    """k57 vm657 ltau=2: tau = copysign(ltc x min(ltk, |gap| / ltg), gap) once |gap| >= ltg -- with ltc = slope and
    ltk = cap / slope; at the shipped slope (1.0) exactly C1K48."""
    for gap_bps in (-900.0, -60.0, -23.0, -2.3, -2.29, 0.0, 1.0, 2.3, 7.7, 38.4, 110.4, 200.0, 5_000.0):
        fp = MID * math.exp(gap_bps / 1e4)
        g = math.log(fp / MID) * 1e4
        for ltc, ltk in ((1.0, 48.0), (2.0, 24.0), (0.5, 96.0), (2.0, 16.0), (1.0, 16.0)):
            want = math.copysign(ltc * min(ltk, abs(g) / 2.3), g) if abs(g) >= 2.3 else 0.0
            got = ln.lean_target(MID, fp, 2.3, ltc, ltc * ltk)
            assert got == pytest.approx(want, abs=1e-12), (gap_bps, ltc, ltk)
            if ltc == 1.0:
                assert got == want, (gap_bps, ltk)


def test_the_tally_counts_books_fresh_pages_and_leans_and_never_raises():
    req = ln.new_request()
    ln.note_book(req, fresh=False, tau=0.0, inventory=3.0)
    ln.note_book(req, fresh=True, tau=0.0, inventory=-2.0)
    ln.note_book(req, fresh=True, tau=10.0, inventory=3.0)
    ln.note_book(req, fresh=True, tau=-4.0, inventory=-10.0)
    assert ln.summary(req) == {"books": 4, "tau_books": 2, "mean_abs_tau": 7.0, "mean_abs_inv_minus_tau": 6.5,
                               "max_abs_tau": 10.0, "fresh_fp_share": 0.75}
    assert ln.summary(ln.new_request()) == {"books": 0, "tau_books": 0, "mean_abs_tau": None,
                                           "mean_abs_inv_minus_tau": None, "max_abs_tau": 0.0, "fresh_fp_share": None}
    for junk in (None, 3, "x", [], {"books": "x"}):
        ln.note_book(junk, fresh=True, tau=1.0, inventory=1.0)
        assert set(ln.summary(junk)) == set(ln.summary(None))
    req = ln.new_request()
    ln.note_book(req, fresh=True, tau=float("nan"), inventory=1.0)
    ln.note_book(req, fresh=True, tau="x", inventory=1.0)
    assert ln.summary(req)["tau_books"] == 0 and ln.summary(req)["books"] == 2


# ---- 2. the agent: the pass with S5, compiled from the real Simple on t623's harness --------------------------------------

def _with25(fn, names=None):
    g = dict(fn.__globals__)
    g.update(EXTRA if names is None else names)
    f = types.FunctionType(fn.__code__, g, fn.__name__, fn.__defaults__, fn.__closure__)
    f.__kwdefaults__ = fn.__kwdefaults__
    return f


_CLASSES: dict = {}


def _klass(base, names=None):
    key = (base, None if names is None else tuple(sorted((k, id(v)) for k, v in names.items())))
    if key not in _CLASSES:
        methods = {n: _with25(getattr(base, n), names) for n in PASS}
        scope = dict(getattr(base, "_v63_pass").__globals__)
        scope.update(EXTRA if names is None else names)
        for n in METHODS:
            ns = dict(scope)
            exec(compile(ast.Module(body=[ast.parse(_src(n)).body[0]], type_ignores=[]), f"<{n}>", "exec"), ns)
            methods[n] = ns[n]
        _CLASSES[key] = type("P625", (base,), methods)
    return _CLASSES[key]


def _agent(*, on=True, slope=1.0, cap=48.0, gap=2.3, names=None, v612=False, **kw):
    """t623's harness agent (the v6.22 pass with v6.23; fundamental 38 bps above book 3's mid unless ``page`` says
    otherwise) with the real Simple's v6.25 pass and methods; S1's gap is the shipped 2.3 bps (S5's unit)."""
    agent = t623._agent(gap=gap, **kw)
    agent.__class__ = _klass(type(agent), names)
    agent.research_v625_lean, agent.research_v625_lean_slope, agent.research_v625_lean_cap = on, slope, cap
    agent._v6_25_taus, agent._v6_25_errors, agent._v6_25_last = {}, 0, {}
    agent._v6_25_reported, agent._v6_25_erring = False, False
    agent.research_v612_reduce_depth = v612
    return agent


def _run(agent, *, ts=NOW, version=62, book=None):
    """One request (t623._run): the v6.23 observation, then the pass.  62: S1 inactive (the live case under the captured
    basis); 63: S1 active."""
    return t623._run(agent, ts=ts, version=version, book=book)


_out = t622._out


def _rest_l0(agent):
    t623._rest_both(agent)                       # level 0 resting at its price on both sides: nothing new from the layer


def _rows(agent):
    return [kw for event, kw in agent.emitted if event == "V625_STATE"]


def _shift(agent, inv=None):
    """The twin's venue inventory: inv - tau (t622's harness reads None as the tracker's 0)."""
    tau = agent._v6_25_taus[3][0]
    base = 0.0 if inv is None else float(inv)
    return base - tau if tau else inv


def test_the_default_page_leans_book_3_long_by_its_gap_in_units():
    agent = _agent()
    _run(agent)
    tau, ts = agent._v6_25_taus[3]
    assert ts == NOW and tau == pytest.approx(_tau(ABOVE)) and 16.0 < tau < 17.0
    below = _agent(page=t616._page({3: BELOW}))
    _run(below)
    assert below._v6_25_taus[3][0] == pytest.approx(_tau(BELOW)) and below._v6_25_taus[3][0] < -17.0
    assert agent._v6_25_errors == 0 and below._v6_25_errors == 0


# ---- 3. each SHIFTED decision reads inv - tau, each TRUE one inv ----------------------------------------------------------

def _spied(agent, calls):
    base = type(agent)

    def wrap(name, pos):
        orig = getattr(base, name)

        def f(self, *a, **k):
            calls.setdefault(name, []).append(a[pos] if len(a) > pos else k.get("inv"))
            return orig(self, *a, **k)
        return f
    agent.__class__ = type("Spied", (base,), {"_v619_side": wrap("_v619_side", 4), "_v6_23_against": wrap("_v6_23_against", 4),
                                              "_v633_book": wrap("_v633_book", 6), "_v617_book": wrap("_v617_book", 5)})
    deep = agent._v633_deep
    orig_open = deep.book_open

    def book_open(book_id, floor, inventory, **k):
        calls.setdefault("book_open", []).append(inventory)
        return orig_open(book_id, floor, inventory, **k)
    deep.book_open = book_open
    agent.research_v66_add_spacing = True
    spacing = agent._v66_spacing_ref()
    orig_note = spacing.note

    def note(book_id, trades, uid, inventory_after, eps=1e-9):
        calls.setdefault("note", []).append(inventory_after)
        return orig_note(book_id, trades, uid, inventory_after, eps=eps)
    spacing.note = note
    return agent


def test_every_shifted_decision_is_handed_inv_minus_tau_and_every_true_one_inv():
    wanted = []

    def spy_wanted(inv, *a, **k):
        wanted.append(inv)
        return tt.wanted(inv, *a, **k)
    names = dict(EXTRA, v63_wanted=spy_wanted)
    for inv in (3.0, -3.0, 20.0):
        calls: dict = {}
        agent = _spied(_agent(inv=inv, names=names, levels="deep", adding=True), calls)
        wanted.clear()
        _run(agent, version=63)                                          # S1 active: its adding test is handed it too
        tau = agent._v6_25_taus[3][0]
        assert tau == pytest.approx(_tau(ABOVE))
        for name in ("_v619_side", "_v6_23_against", "_v633_book", "_v617_book"):
            assert calls[name] == [inv - tau], (name, inv)              # SHIFTED: S2m, S1, the deep layer, the touch quote
        assert calls["book_open"] == [inv] and wanted == [inv], inv      # TRUE: v6.3.3's gate, the v6.3 fallback
        assert calls["note"] == [inv], inv                               # v6.6's record: no lean before this request
        _run(agent, ts=NOW + S, version=63)
        assert calls["note"] == [inv, inv - tau], inv                    # ... then the lean its fills rested under
        assert calls["_v633_book"] == [inv - tau] * 2 and calls["book_open"] == [inv] * 2


def test_the_deep_layer_and_the_touch_quote_read_their_inventory_only_from_the_argument_the_pass_hands_them():
    """Every inventory decision inside them reads their ``inv`` parameter -- no other source of inventory -- so handing
    them inv - tau shifts each one: the bound and v6.6.1's scaled bound, pacing's adding side (resting and new, v6.9 S3's
    deepest level), v6.12's half depth, v6.6's spacing, S1's withheld set (computed from it by the pass), and the touch
    quote's adding test and bound."""
    sources = ("_direct_signed_inventory", "_a195_venue_net_by_book", "_position_tracker_snapshot", "_net_inventory",
               "_research_abs_inventory", "venue")
    for name, reads in (("_v633_book", {"v612_level0_depth", "v66_adds_to_position", "spacing.allows", "deep.room",
                                        "v661_room", "v641_adds"}),
                        ("_v617_touch", {"v641_adds", "v617_room"}), ("_v619_side", {"v619_held_side"}),
                        ("_v6_23_against", {"v623_withheld"})):
        fn = ast.parse(_src(name)).body[0]
        assert "inv" in [a.arg for a in fn.args.args], name
        text = ast.unparse(fn)
        for s in sources:
            assert s not in text, (name, s)
        got = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and any(isinstance(a, ast.Name) and a.id == "inv" for a in
                                                  list(node.args) + [k.value for k in node.keywords]):
                got.add(ast.unparse(node.func).replace("self.", ""))
        assert got == reads, (name, got)


def test_the_pass_reads_the_true_inventory_only_where_the_map_says():
    """The pass's own reads, by name: inv (true) for the lean itself, the v6.3 fallback (v63_wanted, v63_role_for),
    v6.3.3's gate (book_open), v6.32 S14's fee gate (_v6_32_note: the side that adds to the TRUE position) and v6.34's
    class-1 entry (_v6_34_book: no lean on class 1); v6_25_inv for S2m, S1, the deep layer (both calls) and the touch
    quote (both calls); v6_25_fills for v6.6's fill-side record."""
    fn = ast.parse(_src("_v63_pass")).body[0]
    uses: dict = {"inv": [], "v6_25_inv": [], "v6_25_fills": []}
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            for a in list(node.args) + [k.value for k in node.keywords]:
                if isinstance(a, ast.Name) and a.id in uses:
                    uses[a.id].append(ast.unparse(node.func))
    assert sorted(uses["inv"]) == sorted(["self._v6_25_lean", "v63_wanted", "deep.book_open", "deep.book_open",
                                          "v63_role_for", "self._v6_32_note", "self._v6_34_book"])
    assert sorted(uses["v6_25_inv"]) == sorted(["self._v619_side", "self._v6_23_against", "self._v633_book",
                                                "self._v633_book", "self._v617_book", "self._v617_book"])
    assert uses["v6_25_fills"] == ["spacing.note"]
    loads = [n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)]
    assert loads.count("v6_25_inv") == 6 and loads.count("v6_25_fills") == 1


# ---- 4. with a lean, each decision is v6.24's at inv - tau --------------------------------------------------------------

def _scenarios():
    pages = (("default", "fp above"), (t616._page({3: BELOW}), "fp below"),
             (t616._page({3: 101.20}), "fp 118 bps above"), (t616._page({3: 100.02}), "fp inside the gap"),
             (None, "no feed"), (t616._page({3: ABOVE}, sim_ts_ns=NOW - 30 * S), "stale"))
    paces = (("free", dict(used=t622.U_FREE), None),
             ("paced", dict(used=t622.U_BOUND + 500.0), lambda: t622._aged(t622.U_BOUND)),
             ("at the cap", dict(used=t622.CAP), None))
    for page, pname in pages:
        for inv in (None, 3.0, -3.0, 20.0, -20.0, 47.0, -47.0, 60.0):
            for cname, kw, pace in paces:
                for rest in (None, _rest_l0, t622._rest_doomed):
                    yield page, pname, inv, cname, kw, pace, rest


def test_with_a_lean_every_decision_is_v624s_at_inv_minus_tau_with_s1_on_and_off():
    """The S5 agent at inventory inv against the S5-off agent (the v6.24 pass) at inv - tau, every page, inventory,
    pacing state and resting set, v6.12 on and off, S1 inactive (62) and active (63): what reaches the venue is the
    same -- the shifted decisions are exactly v6.24's decisions at the leaned inventory."""
    seen = differs = 0
    for page, pname, inv, cname, kw, pace, rest in _scenarios():
        for v612 in (False, True):
            for version in (62, 63):
                agent = _agent(page=page, inv=inv, v612=v612, levels="deep", adding=True,
                               pace=pace() if pace else None, **kw)
                if rest is not None:
                    rest(agent)
                got = _out(_run(agent, version=version))
                twin = _agent(on=False, page=page, inv=_shift(agent, inv), v612=v612, levels="deep", adding=True,
                              pace=pace() if pace else None, **kw)
                if rest is not None:
                    rest(twin)
                want = _out(_run(twin, version=version))
                case = (pname, inv, cname, getattr(rest, "__name__", None), v612, version)
                assert got == want, case
                assert agent._v633_counts == twin._v633_counts and agent._v617_counts == twin._v617_counts, case
                assert agent._v6_25_errors == 0, case
                seen += 1
                plain = _agent(on=False, page=page, inv=inv, v612=v612, levels="deep", adding=True,
                               pace=pace() if pace else None, **kw)
                if rest is not None:
                    rest(plain)
                differs += int(_out(_run(plain, version=version)) != got)
    assert seen == 6 * 8 * 3 * 3 * 2 * 2
    assert differs > 100                                                 # the lean changes many of them


def test_paced_and_flat_the_side_toward_the_fundamental_now_builds_and_the_other_side_waits():
    """v6.24: a paced flat book adds on neither side (v6.4.1) -- with the lean the bid toward the fundamental reduces the
    leaned short (inv - tau = -16.7) and rests; the offer would grow it and waits."""
    kw = dict(used=t622.U_BOUND + 500.0)
    v624 = _agent(on=False, pace=t622._aged(t622.U_BOUND), **kw)
    assert _out(_run(v624)) == ([], [])
    agent = _agent(pace=t622._aged(t622.U_BOUND), **kw)
    assert _out(_run(agent)) == ([(3, "BUY", 40031, 99.8, 2.0)], [])
    below = _agent(page=t616._page({3: BELOW}), pace=t622._aged(t622.U_BOUND), **kw)
    assert _out(_run(below)) == ([(3, "SELL", 40032, 100.23, 2.0)], [])
    long_ = _agent(inv=40.0, pace=t622._aged(t622.U_BOUND), **kw)    # 40 base long: beyond tau, the offer unwinds
    assert _out(_run(long_)) == ([(3, "SELL", 40032, 100.23, 2.0)], [])


def test_paced_the_touch_quote_buys_toward_the_fundamental_up_to_tau():
    kw = dict(used=t622.U_BOUND + 500.0, inv=3.0)
    v624, agent = _agent(on=False, pace=t622._aged(t622.U_BOUND), **kw), _agent(pace=t622._aged(t622.U_BOUND), **kw)
    for a in (v624, agent):
        _rest_l0(a)
    assert _out(_run(v624)) == ([], [(3, 21)])                          # v6.24: the long's bid adds -- cancelled, no quote
    assert _out(_run(agent)) == ([(3, "BUY", 43031, 100.0, 2.0)], [(3, 22)])   # leaned: the bid reduces, the offer adds
    assert agent._v617_counts.get("placed_buy") == 1 and "paced_states" not in agent._v617_counts


def test_the_touch_quote_and_the_deep_bound_hold_tau_plus_the_bound():
    """Behind its line the bound is 48 base: v6.24 holds a 47-base long's touch bid (47 + 2 > 48) and cancels a 48.5-base
    long's resting bid; with tau = +16.7 both have room (30.3 / 31.8 leaned)."""
    for inv, want_v624, want in ((47.0, ([], []), ([(3, "BUY", 43031, 100.0, 2.0)], [])),
                                 (48.5, ([], [(3, 21)]), ([(3, "BUY", 43031, 100.0, 2.0)], []))):
        v624, agent = _agent(on=False, inv=inv), _agent(inv=inv)
        for a in (v624, agent):
            _rest_l0(a)
        assert _out(_run(v624)) == want_v624, inv
        assert _out(_run(agent)) == want, inv
    short = _agent(page=t616._page({3: BELOW}), inv=-48.5)              # mirrored: tau = -18.1
    _rest_l0(short)
    assert _out(_run(short)) == ([(3, "SELL", 43032, 100.03, 2.0)], [])


def test_s1_withholds_the_side_against_the_fundamental_until_the_inventory_reaches_tau():
    """S1 active (63, every level, adding only): v6.24 keeps a 3-base long's offer (it unwinds); leaned (3 - 16.7) the
    offer would grow the leaned short -- withheld, as for a flat book.  Past tau (40 base) it unwinds again."""
    kw = dict(levels="deep", adding=True)
    for inv, withheld24, withheld in ((3.0, False, True), (None, True, True), (40.0, False, False), (-3.0, True, True)):
        v624, agent = _agent(on=False, inv=inv, **kw), _agent(inv=inv, **kw)
        got24, got = _out(_run(v624, version=63)), _out(_run(agent, version=63))
        sells24 = [p for p in got24[0] if p[1] == "SELL" and p[2] == 40032]
        sells = [p for p in got[0] if p[1] == "SELL" and p[2] == 40032]
        assert (not sells24) is withheld24 and (not sells) is withheld, inv
        assert (agent._v6_23_counts.get("withheld_sell_l0") == 1) is withheld, inv


def test_v612s_half_depth_moves_to_the_side_that_reduces_the_leaned_inventory():
    flat24, flat = _agent(on=False, v612=True), _agent(v612=True)
    placed24, placed = dict((p[2], p[3]) for p in _out(_run(flat24))[0]), dict((p[2], p[3]) for p in _out(_run(flat))[0])
    assert placed24 == {40031: 99.8, 40032: 100.23}                      # flat: both at the full depth
    assert placed[40032] == 100.23 and placed[40031] > 99.8              # leaned short: the bid at half the depth
    depth = flat._v633_deep.depth(3)
    assert placed[40031] == dl.deep_price(MID, d612.level0_depth(BUY, depth, -16.0, 0.25), BUY, bid=100.0, ask=100.03,
                                          tick=0.01, decimals=2)
    long24, long_ = _agent(on=False, v612=True, inv=3.0), _agent(v612=True, inv=3.0)
    p24 = dict((p[2], p[3]) for p in _out(_run(long24))[0])
    p25 = dict((p[2], p[3]) for p in _out(_run(long_))[0])
    assert p24[40032] < 100.23 and p24[40031] == 99.8                    # v6.24: the long's offer closer
    assert p25[40032] == 100.23 and p25[40031] > 99.8                    # leaned (3 - 16.7): the bid closer


def test_the_add_spacing_record_follows_the_leaned_inventory_and_the_lean_the_fills_rested_under():
    """v6.6: a fill that grows the position records its price as the side's last add.  Our bid filling 2 base on a flat
    book grows v6.24's position (+2: the buy side's record is 99.80); leaned by the +16.7 the bid rested under, the
    same fill shrinks a short of -16.7 -- nothing is recorded, and the next bid is not spaced away from it."""
    fill = {"y": "t", "p": 99.80, "q": 2.0, "s": 1, "Ma": int(t623.UID), "Ta": 7}   # a taker sold into our bid
    records = {}
    for on in (False, True):
        agent = _agent(on=on, inv=0.0)
        agent.research_v66_add_spacing = True
        _run(agent)                                                      # the request the bid rested under
        agent.inv22 = 2.0
        seen = []
        spacing = agent._v66_spacing_ref()
        orig_note = spacing.note

        def note(book_id, trades, uid_, inventory_after, eps=1e-9, _seen=seen, _orig=orig_note):
            _seen.append(inventory_after)
            return _orig(book_id, trades, uid_, inventory_after, eps=eps)
        spacing.note = note
        _run(agent, ts=NOW + S, book=t63._book(events=[dict(fill)]))
        records[on] = (seen, spacing.last_add(3, BUY), spacing.adds_recorded)
    assert records[False] == ([2.0], 99.80, 1)
    seen, last, recorded = records[True]
    assert seen == [pytest.approx(2.0 - _tau(ABOVE))] and last is None and recorded == 0


# ---- 5. no lean: v6.24 exactly ---------------------------------------------------------------------------------------------

def _tau_zero_pages():
    return ((None, "no feed"), (t616._page({3: ABOVE}, sim_ts_ns=NOW - 30 * S), "stale"),
            (t616._page({3: ABOVE}, sim_id="20260929_2015"), "other simulation"), (t616._page({5: ABOVE}), "other book"),
            (t616._page({3: MID}), "fp at the mid"), (t616._page({3: 100.02}), "fp inside the gap"),
            (t616._page({3: 100.00}), "fp inside the gap below"))


def test_without_a_lean_the_pass_is_v624_exactly_request_after_request():
    seen = 0
    for page, pname in _tau_zero_pages():
        for inv in (None, 3.0, -3.0, 60.0):
            for used in (t622.U_FREE, t622.CAP):
                for rest in (None, _rest_l0, t622._rest_doomed):
                    for version in (62, 63):
                        ref, agent = t623._agent(page=page, inv=inv, used=used, gap=2.3), \
                            _agent(page=page, inv=inv, used=used)
                        if rest is not None:
                            rest(ref)
                            rest(agent)
                        for i in range(2):
                            ts = NOW + i * S
                            case = (pname, inv, used, getattr(rest, "__name__", None), version, i)
                            assert _out(_run(agent, ts=ts, version=version)) == \
                                _out(t623._run(ref, ts=ts, version=version)), case
                            assert t622._fingerprint(agent) == t622._fingerprint(ref), case
                        assert agent._v6_25_taus[3][0] == 0.0 and agent._v6_25_errors == 0, pname
                        assert agent._v633_counts == ref._v633_counts and agent._v617_counts == ref._v617_counts
                        assert agent._v6_23_counts == ref._v6_23_counts
                        seen += 1
    assert seen == 7 * 4 * 2 * 3 * 2


def test_off_the_pass_is_v624_exactly_and_reads_no_fundamental_for_the_lean():
    for page in ("default", t616._page({3: BELOW})):
        for inv in (None, 3.0, 47.0):
            ref, agent = t623._agent(page=page, inv=inv, gap=2.3), _agent(on=False, page=page, inv=inv)
            for i in range(2):
                assert _out(_run(agent, ts=NOW + i * S)) == _out(t623._run(ref, ts=NOW + i * S, version=62))
                assert t622._fingerprint(agent) == t622._fingerprint(ref)
            assert agent._v6_25_taus == {} and agent._v6_25_last == {"books": 0, "tau_books": 0, "mean_abs_tau": None,
                                                                     "mean_abs_inv_minus_tau": None,
                                                                     "max_abs_tau": 0.0, "fresh_fp_share": None}


def test_a_book_of_another_asset_class_never_leans():
    foreign = t623._classes(split=3, n=5)                                # book 3 in the 2.5364 class
    agent = _agent(inv=3.0)
    t623._run(agent, version=62, classes=foreign)
    assert 3 not in agent._v6_25_taus and agent._v6_25_last["books"] == 0
    assert agent._v6_23_foreign == frozenset({3, 4})


def test_a_lean_that_ended_still_reads_the_fills_of_the_request_it_set():
    calls: dict = {}
    agent = _spied(_agent(inv=3.0), calls)
    _run(agent)
    agent._v616_feed = t616._Feed(None)                                  # the page goes away: no lean now
    _run(agent, ts=NOW + S)
    tau = _tau(ABOVE)
    assert calls["note"] == [3.0, pytest.approx(3.0 - tau)] and calls["_v633_book"] == [pytest.approx(3.0 - tau), 3.0]
    _run(agent, ts=NOW + 2 * S)
    assert calls["note"][-1] == 3.0 and agent._v6_25_taus[3] == (0.0, NOW + 2 * S)
    calls2: dict = {}
    restarted = _spied(_agent(inv=3.0), calls2)
    restarted._v6_25_taus = {3: (9.0, NOW + 10 * S)}                    # set later than now: the clock ran back
    restarted._v616_feed = t616._Feed(None)
    _run(restarted)
    assert calls2["note"] == [3.0] and restarted._v6_25_taus[3] == (0.0, NOW)


# ---- 6. the state row and errors ------------------------------------------------------------------------------------------

def test_the_state_row_comes_at_the_first_pass_every_100_ticks_and_at_the_first_error():
    agent = _agent(inv=3.0)
    for tick in (1, 2, 99, 100, 101):
        agent._tick = tick
        _run(agent)
    rows = _rows(agent)
    assert [(r["reason"], r["tick"]) for r in rows] == [("start", 1), ("periodic", 100)]
    row = rows[0]
    assert tuple(k for k in row if k not in ("reason", "tick")) == FIELDS
    assert row["version"] == ln.V625_LEAN_VERSION and (row["lean_on"], row["slope"], row["cap"], row["gap_bps"]) == \
        (1, 1.0, 48.0, 2.3)
    tau = _tau(ABOVE)
    assert (row["books"], row["tau_books"], row["fresh_fp_share"], row["errors"]) == (1, 1, 1.0, 0)
    assert row["mean_abs_tau"] == pytest.approx(tau, abs=1e-4) and row["max_abs_tau"] == pytest.approx(tau, abs=1e-4)
    assert row["mean_abs_inv_minus_tau"] == pytest.approx(abs(3.0 - tau), abs=1e-4)


def test_an_error_counts_leans_nothing_and_rows_once_until_a_clean_request():
    agent, ref = _agent(inv=3.0), _agent(on=False, inv=3.0)

    def _boom(self, *a, **k):
        raise RuntimeError("boom")
    agent._tick = 1
    _run(agent)
    good = agent._v6_23_fundamental
    agent._v6_23_fundamental = types.MethodType(_boom, agent)
    for tick in (2, 3):
        agent._tick = tick
        assert _out(_run(agent)) == _out(_run(ref))                       # no lean: v6.24
    assert agent._v6_25_errors == 2 and agent._v6_25_taus[3] == (0.0, NOW)
    agent._v6_23_fundamental = good
    agent._tick = 4
    _run(agent)
    agent._v6_23_fundamental = types.MethodType(_boom, agent)
    agent._tick = 5
    _run(agent)
    assert [(r["reason"], r["tick"], r["errors"]) for r in _rows(agent)] == [("start", 1, 0), ("error", 2, 1),
                                                                            ("error", 5, 3)]
    boom_names = dict(EXTRA, v625_lean_target=lambda *a, **k: 1 / 0)
    broken = _agent(inv=3.0, names=boom_names)
    assert _out(_run(broken)) == _out(_run(_agent(on=False, inv=3.0)))
    assert broken._v6_25_errors == 1 and broken._v6_25_last["tau_books"] == 0 and broken._v6_25_last["books"] == 1


def test_the_row_never_fails_the_request_and_waits_for_the_reply_when_the_request_defers_its_telemetry():
    agent = _agent(inv=3.0)
    emitted = agent.emitted
    agent._emit = lambda event, force=False, **kw: (1 / 0) if event == "V625_STATE" else emitted.append((event, kw))
    agent._tick = 1
    assert _out(_run(agent)) == _out(_run(_agent(on=False, inv=3.0)))
    assert agent._v6_25_errors == 1 and _rows(agent) == []
    deferred = _agent(inv=3.0)
    work = []
    deferred._v629_request_work = types.SimpleNamespace(add=lambda name, fn: work.append((name, fn)))
    deferred._tick = 1
    _run(deferred)
    mine = [fn for n, fn in work if n == "v625_state_row"]
    assert _rows(deferred) == [] and len(mine) == 1 and [n for n, _fn in work] == ["v623_state_row", "v625_state_row"]
    mine[0]()
    assert [r["reason"] for r in _rows(deferred)] == ["start"]


def test_off_the_row_says_so_and_an_agent_without_the_build_has_none():
    agent = _agent(on=False, inv=3.0)
    agent._tick = 1
    _run(agent)
    row = _rows(agent)[0]
    assert (row["lean_on"], row["books"], row["tau_books"], row["mean_abs_tau"], row["fresh_fp_share"]) == \
        (0, 0, 0, None, None)
    older = t623._agent(inv=3.0)                                          # v6.24's harness: no research_v625_lean at all
    older._tick = 1
    t623._run(older, version=62)
    assert not [e for e, _kw in older.emitted if e == "V625_STATE"]


# ---- 7. wiring -------------------------------------------------------------------------------------------------------------

def test_the_switches_ship_on_and_agree_with_their_agent_defaults():
    params = t617._params()
    for key, value in (("research_v625_lean", "1"), ("research_v625_lean_slope", "1.0"), ("research_v625_lean_cap", "48")):
        assert f"{key}={value} " in t617._shipped() + " " and params.count(f"{key}=") == 1, key
    assert 'research_v624_pay_basis=1 \\\nresearch_v625_lean=1 \\\nresearch_v625_lean_slope=1.0 \\\n' \
           'research_v625_lean_cap=48 \\\n' in LAUNCHER                    # after v6.24's key (v6.26's next)
    init = _src("_init_build_switches")
    for line in (SWITCH_LINE,
                 'self.research_v625_lean_slope = v625_parse_lean_slope(\n'
                 '            getattr(self.config, "research_v625_lean_slope", V625_LEAN_SLOPE))',
                 'self.research_v625_lean_cap = v625_parse_lean_cap(getattr(self.config, "research_v625_lean_cap", '
                 'V625_LEAN_CAP))'):
        assert SIMPLE.count(line) == 1 and line in init, line
    assert init.index("self._v6_24_basis: str | None = None") < init.index(SWITCH_LINE) < \
        init.index("self._v6_25_taus: dict[int, tuple[float, int]] = {}") < init.index("self._v69_counts: dict[str, int] = {}")
    assert ln.parse_slope(float("1.0")) == ln.LEAN_SLOPE and ln.parse_cap(float("48")) == ln.LEAN_CAP


def test_every_v625_name_the_agent_imports_is_the_modules():
    tree = ast.parse(SIMPLE)
    imp = next(n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "research_v625_lean")
    for alias in imp.names:
        assert V625_NAMES[alias.asname or alias.name] is getattr(ln, alias.name), alias.asname
    assert {a.asname or a.name for a in imp.names} == set(V625_NAMES)
    assert SIMPLE.count("from research_v625_lean import (") == 1
    assert t623.V625_IDENTIFIERS == set(V625_NAMES) | {"research_v625_lean", "research_v625_lean_slope",
                                                         "research_v625_lean_cap"}
    assert t623.V625_LEANED == {"v6_25_inv", "v6_25_fills"}


def test_the_rule_is_wired_once_in_its_place_in_the_pass():
    body = _src("_v63_pass")
    assert body.count(LEAN_LINE) == 1 and SIMPLE.count(LEAN_LINE) == 1
    assert body.count(REPORT_LINE) == 1 and SIMPLE.count(REPORT_LINE) == 1
    assert body.index('v6_25_on = bool(getattr(self, "research_v625_lean", False))') < body.index("for raw_id in sorted(books")
    assert body.index("inv = venue.get(book_id)") < body.index("v6_25_inv = v6_25_fills = inv") < \
        body.index("if v6_25_on and book_id not in v6_23_foreign:") < body.index(LEAN_LINE) < \
        body.index("spacing.note(book_id, book_trades,") < body.index("want = v63_wanted(inv, target")
    assert body.index("return placed_total") > body.index(REPORT_LINE) > body.index('self._v63_count("requests")')
    assert 'if getattr(self, "research_v625_lean", None) is not None:' in body
    assert "self._v6_25_" not in "".join(_src(n) for n in ("_v633_book", "_v617_touch", "_v617_book", "_v619_side",
                                                          "_v6_23_against", "respond"))


# ---- 8. v6.24's code, statement by statement (the committed v6.24: 34a591e) -------------------------------------------------

V624_COMMIT = "34a591e"
V625_IDENTIFIERS = t623.V625_IDENTIFIERS
V625_LEANED = t623.V625_LEANED
# v6.26 (S6, research_v626_feed_clock) only adds too, except the freshness-limit keyword it hands each reader of the
# fundamental page (None -- v6.16's limit -- with the switch off): the strip takes both out (tests/test_research_v6_26.py
# strips them against the committed v6.25), and the method-by-method check reads v6.25 with v6.26 taken out (_Drop626).
V626_IDENTIFIERS, V626_KEYWORDS = t623.V626_IDENTIFIERS, t623.V626_KEYWORDS
# v6.27 (S7, research_v627_touch_centre) only adds: both strips take its additions out as well
# (tests/test_research_v6_27.py strips them against the committed v6.26).
V627_IDENTIFIERS = t623.V627_IDENTIFIERS
# v6.28 (S8, research_v628_flat_launch) only adds: both strips take its additions out as well
# (tests/test_research_v6_28.py strips them against the committed v6.27).
V628_IDENTIFIERS = t623.V628_IDENTIFIERS
# v6.29 (S9, research_v629_touch_fp_move, and S8's first save) only adds: both strips take its additions out as well
# (tests/test_research_v6_29.py checks them against v6.28's footprint).
V6_29_IDENTIFIERS = t623.V6_29_IDENTIFIERS
# v6.30 (the identity profile: D1 research_v630_deep_tier, D2 research_v630_s9_lookback) only adds: both strips take its
# additions out as well (tests/test_research_v6_30.py checks them against the committed v6.29).
V6_30_IDENTIFIERS = t623.V6_30_IDENTIFIERS
# v6.31 (S12b research_v631_touch_improve, the single-write save research_v631_single_write_save) only adds: the
# strip takes its additions out as well (tests/test_research_v6_31.py checks them against the committed v6.30).
V6_31_IDENTIFIERS = t623.V6_31_IDENTIFIERS
# v6.32 (S14 research_v632_fee_making, P1 research_v632_state_fundamental) only adds: the strip takes its additions out
# as well (tests/test_research_v6_32.py checks them against the committed v6.31).
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


def _v624(rel):
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "show", f"{V624_COMMIT}:{rel}"], cwd=ROOT,
                             capture_output=True, text=True, timeout=120)
    except Exception:
        out = None
    if out is None or out.returncode != 0:
        pytest.skip(f"git or {V624_COMMIT} is not available here")
    return out.stdout


def _mentions(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id in V625_LEANED and not isinstance(n.ctx, ast.Store):
            continue                                                         # read back as inv (visit_Name)
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.arg if isinstance(n, (ast.arg, ast.keyword)) else n.value if isinstance(n, ast.Constant) else None
        if isinstance(name, str) and (name in V625_IDENTIFIERS or name in V626_IDENTIFIERS or name in V627_IDENTIFIERS
                                      or name in V628_IDENTIFIERS or name in V6_29_IDENTIFIERS
                                      or name in V6_30_IDENTIFIERS or name in V6_31_IDENTIFIERS
                                      or name in V6_32_IDENTIFIERS
                                      or name in V6_33_IDENTIFIERS
                                      or name in V6_34_IDENTIFIERS
                                      or name in V6_36_IDENTIFIERS
                                      or name in V6_37_IDENTIFIERS
                                      or name.startswith(("_v6_25_", "v6_25_", "_v6_26_", "v6_26_", "_v6_27_",
                                                          "v6_27_", "_v6_28_", "v6_28_", "_v6_29_", "v6_29_",
                                                          "_v6_30_", "v6_30_", "_v6_31_", "v6_31_",
                                                          "_v6_32_", "v6_32_", "_v6_33_", "v6_33_",
                                                          "_v6_34_", "v6_34_", "_v6_36_", "v6_36_",
                                                          "_v6_37_", "v6_37_"))):
            return True
    return False


class _Strip625(ast.NodeTransformer):
    """Removes what v6.25 (and v6.26, v6.27, v6.28, v6.29 and v6.30 after it) adds and nothing else -- its import, its
    _v6_25_ methods, statements naming its identifiers (an if on one keeps its else), v6.26's limit keyword and every
    docstring -- and reads the leaned inventory (v6_25_inv, v6_25_fills) back as v6.24's inv."""

    def visit_Module(self, node):                      # v6.38's changes undone first (tests/_v638_undo.py)
        _v638_undo.undo(node)
        return self.generic_visit(node)

    def _stmts(self, stmts):
        out = []
        for st in stmts:
            if isinstance(st, ast.ImportFrom) and st.module in ("research_v625_lean", "research_v626_feed_clock",
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
                                                                "research_v6_37_fast_path"):
                continue
            if isinstance(st, ast.FunctionDef) and st.name.startswith(("_v6_25_", "_v6_26_", "_v6_27_", "_v6_28_",
                                                                       "_v6_29_", "_v6_30_", "_v6_31_", "_v6_32_",
                                                                       "_v6_33_", "_v6_34_", "_v6_36_", "_v6_37_")):
                continue
            if isinstance(st, ast.If) and _mentions(st.test):
                out.extend(self._stmts(st.orelse))
                continue
            if isinstance(st, ast.Try) and all(_mentions(b) for b in st.body):
                continue
            st = self.visit(st)
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
            if name in ("body", "orelse", "finalbody", "handlers") and isinstance(value, list):
                continue
            if isinstance(value, list):
                setattr(node, name, [self.visit(v) if isinstance(v, ast.AST) else v for v in value])
            elif isinstance(value, ast.AST):
                setattr(node, name, self.visit(value))
        return node

    def visit_Name(self, node):
        if node.id in V625_LEANED and not isinstance(node.ctx, ast.Store):
            node.id = "inv"
        return node

    def visit_Call(self, node):
        node.keywords = [k for k in node.keywords if k.arg not in V626_KEYWORDS]      # v6.26: the reader's limit
        return self.generic_visit(node)


def _stripped(text):
    return ast.dump(_Strip625().visit(ast.parse(text)))


def _mentions626(node):
    for n in ast.walk(node):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.arg if isinstance(n, (ast.arg, ast.keyword)) else n.value if isinstance(n, ast.Constant) else None
        if isinstance(name, str) and (name in V626_IDENTIFIERS or name in V627_IDENTIFIERS or name in V628_IDENTIFIERS
                                      or name in V6_29_IDENTIFIERS or name in V6_30_IDENTIFIERS
                                      or name in V6_31_IDENTIFIERS
                                      or name in V6_32_IDENTIFIERS
                                      or name in V6_33_IDENTIFIERS
                                      or name in V6_34_IDENTIFIERS
                                      or name in V6_36_IDENTIFIERS
                                      or name in V6_37_IDENTIFIERS
                                      or name.startswith(("_v6_26_", "v6_26_", "_v6_27_", "v6_27_", "_v6_28_",
                                                          "v6_28_", "_v6_29_", "v6_29_", "_v6_30_", "v6_30_", "_v6_31_",
                                                          "v6_31_", "_v6_32_", "v6_32_", "_v6_33_", "v6_33_",
                                                          "_v6_34_", "v6_34_", "_v6_36_", "v6_36_",
                                                          "_v6_37_", "v6_37_"))):
            return True
    return False


class _Drop626(ast.NodeTransformer):
    """Takes out what v6.26 (and v6.27, v6.28, v6.29 and v6.30 after it) adds and nothing else -- its import, its
    _v6_26_ methods, statements naming its identifiers (an if on one keeps its else) and the limit keyword it hands each
    reader of the page -- keeping every docstring: v6.25 as committed, for the method-by-method check against v6.24."""

    def visit_Module(self, node):                      # v6.38's changes undone first (tests/_v638_undo.py)
        _v638_undo.undo(node)
        return self.generic_visit(node)

    def _stmts(self, stmts):
        out = []
        for st in stmts:
            if isinstance(st, ast.ImportFrom) and st.module in ("research_v626_feed_clock", "research_v627_touch_centre",
                                                                "research_v628_flat_launch",
                                                                "research_v6_29_touch_move",
                                                                "research_v630_identity_profile",
                                                                "research_v631_touch_improve",
                                                                "research_v6_32_fee_making",
                                                                "research_v6_32_state_fp",
                                                                "research_v6_33_touch_identity",
                                                                "research_v6_34_class1",
                                                                "research_v6_36_step_withdrawal",
                                                                "research_v6_37_fast_path"):
                continue
            if isinstance(st, ast.FunctionDef) and st.name.startswith(("_v6_26_", "_v6_27_", "_v6_28_", "_v6_29_",
                                                                       "_v6_30_", "_v6_31_", "_v6_32_", "_v6_33_",
                                                                       "_v6_34_", "_v6_36_", "_v6_37_")):
                continue
            if isinstance(st, ast.If) and _mentions626(st.test):
                out.extend(self._stmts(st.orelse))
                continue
            st = self.visit(st)
            if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)) and _mentions626(st):
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

    def visit_Call(self, node):
        node.keywords = [k for k in node.keywords if k.arg not in V626_KEYWORDS]
        return self.generic_visit(node)


def test_every_v624_statement_is_in_v625_unchanged_and_v625_only_adds_and_leans():
    """Simple of the committed v6.24 == Simple of v6.25 with v6.25's additions taken out and v6_25_inv / v6_25_fills read
    as inv: no v6.24 statement, argument, call or branch was changed or dropped except that six making calls read the
    leaned inventory and v6.6's note the leaned record (each is inv itself with the switch off or without a lean)."""
    old = _v624("agents/strategy/Strategy1_Research_Simple.py")
    assert not _mentions(ast.parse(old)), "v6.24 already names a v6.25 identifier: the strip would hide a change"
    assert _stripped(old) == _stripped(SIMPLE)
    stripped_new = _Strip625().visit(ast.parse(SIMPLE))
    assert not _mentions(stripped_new)
    assert ast.dump(stripped_new) != ast.dump(ast.parse(SIMPLE))
    bad = SIMPLE.replace("deep_open = bool(deep.book_open(book_id, floor, inv, inventory_bound=not owns_on))",
                         "deep_open = bool(deep.book_open(book_id, floor, v6_25_inv, inventory_bound=not owns_on))")
    assert bad != SIMPLE and _stripped(bad) == _stripped(old)            # the strip alone cannot see that one ...
    assert "v6_25_inv" in ast.unparse(ast.parse(_src("_v63_pass", bad)))  # ... the read map above does


def test_only_the_switch_block_and_the_pass_changed_and_three_methods_were_added():
    """Method by method against the committed v6.24: the final validator, the A1.9 exit layer, the dust and partial-fill
    paths, the touch life, the deep layer, the touch quote, S2m and S1's own methods are untouched."""
    old = _v624("agents/strategy/Strategy1_Research_Simple.py")

    def methods(tree):
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
        out: dict = {}
        for n in cls.body:
            if isinstance(n, ast.FunctionDef):
                out.setdefault(n.name, []).append(ast.dump(n))
        return out
    assert not _mentions626(ast.parse(old))
    a, b = methods(ast.parse(old)), methods(_Drop626().visit(ast.parse(SIMPLE)))    # v6.25: v6.26 - v6.29 out
    assert not _mentions626(_Drop626().visit(ast.parse(SIMPLE)))
    assert set(b) - set(a) == {"_v6_25_lean", "_v6_25_report", "_v6_25_snapshot"} and not set(a) - set(b)
    changed = {k for k in a if a[k] != b[k]}
    assert changed == {"_init_build_switches", "_v63_pass"}, changed
    for k in ("_research_final_validate_instructions", "_a191_service_reprice_cancels", "_a191_decide",
              "_direct_service_partial_fill_recovery", "_direct_place_dust_normalizer", "_direct_compact_selected_dust",
              "_v6214_service_touch_life", "_v633_book", "_v617_touch", "_v617_book", "_v619_side", "_v6_23_against",
              "_v6_23_fundamental", "respond", "build_mm_strategy_instructions"):
        assert a[k] == b[k], k
    for other in ("Strategy1_Research.py", "Strategy1.py"):
        assert _v624(f"agents/strategy/{other}") == (STRATEGY / other).read_text(), other


def test_with_a_lean_of_zero_the_pass_is_the_committed_v624s():
    """The committed v6.24 pass, compiled from 34a591e, against v6.25's with S5 on and a book without a lean."""
    old = _v624("agents/strategy/Strategy1_Research_Simple.py")
    base = type(t623._template())
    methods = {}
    for n in PASS:
        ns = dict(getattr(base, n).__globals__)
        ns.update(EXTRA)
        exec(compile(ast.Module(body=[ast.parse(_src(n, old)).body[0]], type_ignores=[]), f"<v624 {n}>", "exec"), ns)
        methods[n] = ns[n]
    v624 = type("P624", (base,), methods)
    for page, pname in _tau_zero_pages()[:4] + (("default", "fp above"),):
        for inv in (None, 3.0, -3.0):
            ref = t623._agent(page=page, inv=inv, gap=2.3)
            ref.__class__ = v624
            agent = _agent(page=page, inv=inv, on=(pname != "fp above"))
            for i in range(2):
                assert _out(_run(agent, ts=NOW + i * S)) == _out(t623._run(ref, ts=NOW + i * S, version=62)), (pname, inv)
                assert t622._fingerprint(agent) == t622._fingerprint(ref)


def test_the_launcher_only_adds_to_v624s():
    """The committed v6.24 launcher line by line: three lines extended (the flag defaults, the v6_3_0 arm, PARAMS' last
    key), three greps re-pointed at the leaned inventory (v6.6's note, v6.17's call, v6.23's S1 call), the rest added --
    the v6.25 block and its gate entry.  v6.26, v6.27, v6.28, v6.29 and v6.30 each extend the same three lines and add
    their block and gate entry."""
    launcher = _v638_undo.launcher_v637(LAUNCHER)               # v6.37's launcher: v6.38's edits undone
    old, new = _v624("run_strategy1_research_simple_multi.sh").splitlines(), launcher.splitlines()
    removed, added = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += old[i1:i2]
        if tag in ("replace", "insert"):
            added += new[j1:j2]
    defaults = next(x for x in old if x.startswith("A19X_BUILD=0;"))
    arm = next(x for x in old if x.startswith("  strategy1_direct_v6_3_0)"))
    greps = (('spacing.note(book_id, book_trades, getattr(self, "uid", None), {}, eps=eps)', "v6_25_fills"),
             ("placed_total += self._v617_book(response, book_id, deep, raw_bid, raw_ask, {}, touch_rows, already,",
              "v6_25_inv"),
             ("against = (self._v6_23_against(book_id, raw_bid, raw_ask, cfg, {})", "v6_25_inv"))
    olds = [next(x for x in old if f"grep -qF '{g.format('inv')}'" in x) for g, _n in greps]
    assert removed == [defaults, arm, 'research_v624_pay_basis=1"', *olds]
    block = []
    for head in ("# v6.25 S5.", "# v6.26 S6.", "# v6.27 S7.", "# v6.28 S8.", "# v6.29 S9.", "# v6.30.", "# v6.31.",
                 "# v6.32.", "# v6.33.", "# v6.34.", "# v6.36.", "# v6.37."):
        start = launcher.index(head)
        block += launcher[start:launcher.index("\nfi\n", start) + len("\nfi\n")].splitlines() + [""]
    leaned = [x.replace(f"'{g.format('inv')}'", f"'{g.format(n)}'") for x, (g, n) in zip(olds, greps)]
    assert sorted(added) == sorted([defaults + "; V6_25_BUILD=0; V6_26_BUILD=0; V6_27_BUILD=0; V6_28_BUILD=0; V6_29_BUILD=0"
                                    "; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0; V6_36_BUILD=0; V6_37_BUILD=0",
                                    arm + "; V6_25_BUILD=1; V6_26_BUILD=1; V6_27_BUILD=1; V6_28_BUILD=1; V6_29_BUILD=1"
                                    "; V6_30_BUILD=1; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1; V6_36_BUILD=1; V6_37_BUILD=1",
                                    "research_v624_pay_basis=1 \\", "research_v625_lean=1 \\",
                                    "research_v625_lean_slope=1.0 \\", "research_v625_lean_cap=48 \\",
                                    "research_v626_feed_clock=1 \\", "research_v627_touch_centre=1 \\",
                                    "research_v628_flat_launch=1 \\", "research_v629_touch_fp_move=1 \\",
                                    "research_v630_deep_tier=1 \\", "research_v630_s9_lookback=1 \\",
                                    "research_v631_touch_improve=1 \\",
                                    "research_v631_single_write_save=1 \\",
                                    "research_v632_fee_making=1 \\",
                                    "research_v632_fee_weight=0.005 \\",
                                    "research_v632_state_fundamental=1 \\",
                                    "research_v633_touch_identity=1 \\",
                                    "research_v634_class1_entry=0 \\",
                                    "research_v636_step_withdrawal=1 \\",
                                    'research_v637_fast_path=1"', *leaned,
                                    "      tests/test_research_v6_25.py \\", "      tests/test_research_v6_26.py \\",
                                    "      tests/test_research_v6_27.py \\", "      tests/test_research_v6_28.py \\",
                                    "      tests/test_research_v6_29.py \\", "      tests/test_research_v6_30.py \\",
                                    "      tests/test_research_v6_31.py \\",
                                    "      tests/test_research_v6_32.py \\",
                                    "      tests/test_research_v6_33.py \\",
                                    "      tests/test_research_v6_34.py \\",
                                    "      tests/test_research_v6_36.py \\",
                                    "      tests/test_research_v6_37.py \\",
                                    *block])


# ---- 9. the launcher -----------------------------------------------------------------------------------------------------

def test_the_v625_block_is_gated_preflighted_and_listed():
    assert "; V6_22_BUILD=0; V6_23_BUILD=0; V6_24_BUILD=0; V6_25_BUILD=0; V6_26_BUILD=0; V6_27_BUILD=0; V6_28_BUILD=0; V6_29_BUILD=0; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0; V6_36_BUILD=0; V6_37_BUILD=0; V6_38_BUILD=0\n" in LAUNCHER
    arm = next(x for x in LAUNCHER.splitlines() if x.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_23_BUILD=1; V6_24_BUILD=1; V6_25_BUILD=1; V6_26_BUILD=1; V6_27_BUILD=1; V6_28_BUILD=1; V6_29_BUILD=1; V6_30_BUILD=1; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1; V6_36_BUILD=1; V6_37_BUILD=1; V6_38_BUILD=1"), arm
    assert LAUNCHER.count('if [[ "${V6_25_BUILD:-0}" == "1" ]]; then') == 1 and LAUNCHER.count("V6_25_BUILD=") == 2
    assert "      tests/test_research_v6_24.py \\\n      tests/test_research_v6_25.py \\\n" in LAUNCHER
    assert LAUNCHER.index('echo "[preflight] v6.24 pay basis PASS') < LAUNCHER.index("# v6.25 S5.") < \
        LAUNCHER.index('echo "[preflight] v6.25 fundamental lean PASS') < \
        LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')


def test_the_older_blocks_check_the_calls_v625_hands_the_leaned_inventory():
    """v6.6's note, v6.17's touch call and v6.23's S1 call read the leaned inventory now: their preflight greps check
    the new text, which is in the agent, and the old text is in neither."""
    for old, new in (('spacing.note(book_id, book_trades, getattr(self, "uid", None), inv, eps=eps)',
                      'spacing.note(book_id, book_trades, getattr(self, "uid", None), v6_25_fills, eps=eps)'),
                     ("placed_total += self._v617_book(response, book_id, deep, raw_bid, raw_ask, inv, touch_rows, already,",
                      "placed_total += self._v617_book(response, book_id, deep, raw_bid, raw_ask, v6_25_inv, touch_rows, "
                      "already,"),
                     ("against = (self._v6_23_against(book_id, raw_bid, raw_ask, cfg, inv)",
                      "against = (self._v6_23_against(book_id, raw_bid, raw_ask, cfg, v6_25_inv)")):
        assert LAUNCHER.count(f"grep -qF '{new}' \"$AGENT_PATH/Strategy1_Research_Simple.py\"") == 1, new
        assert SIMPLE.count(new) == 1 and old not in SIMPLE and f"'{old}'" not in LAUNCHER, old


def _block():
    start = LAUNCHER.index('if [[ "${V6_25_BUILD:-0}" == "1" ]]; then')
    return LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.25 fundamental lean PASS', start)]


_PATTERN = r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\" \|\| \{\n\s+echo \"ERROR: ([^\"]+)\" >&2"
_COUNTED = r"\[\[ \"\$\(grep -cF '([^']+)' \"\$AGENT_PATH/([^\"]+)\"\)\" == \"(\d+)\" \]\] \|\| \{\n\s+echo \"ERROR: ([^\"]+)\" >&2"
_ABSENT = r"grep -q([EF]) '([^']+)' \"\$AGENT_PATH/([^\"]+)\" && \{\n\s+echo \"ERROR: ([^\"]+)\" >&2"


def test_every_v625_preflight_pattern_is_in_the_file_it_checks():
    pats = re.findall(_PATTERN, _block())
    assert len(pats) == 7, pats
    for needle, name, _msg in pats:
        assert (STRATEGY / name).read_text().count(needle) == 1, (needle, name)
    assert {p[0] for p in pats} == {"from research_v625_lean import (", SWITCH_LINE, LEAN_LINE,
                                    "side_h = self._v619_side(book_id, v619_mirror, v619_mature, v619_cap, v6_25_inv)",
                                    REPORT_LINE,
                                    "def lean_target(mid: Any, fundamental: Any, gap_bps: Any, slope: Any, cap: Any) -> float:",
                                    "return math.copysign(min(c, k * abs(gap) / g), gap)"}
    counted = re.findall(_COUNTED, _block())
    assert len(counted) == 2, counted
    for needle, name, n, _msg in counted:
        assert (STRATEGY / name).read_text().count(needle) == int(n) == 2, needle
    absent = re.findall(_ABSENT, _block())
    assert [(kind, name) for kind, _p, name, _m in absent] == [("E", "Strategy1_Research_Simple.py"),
                                                              ("F", "Strategy1_Research_Simple.py")]
    regex, fixed = absent[0][1], absent[1][1]
    assert regex == "^[[:space:]]+response, book_id, deep, depth, raw_bid, raw_ask, inv,"
    assert not re.search(r"^\s+response, book_id, deep, depth, raw_bid, raw_ask, inv,", SIMPLE, re.M)
    assert len(re.findall(r"^\s+response, book_id, deep, depth, raw_bid, raw_ask, v6_25_inv,", SIMPLE, re.M)) == 2
    assert fixed not in SIMPLE and fixed.replace("inv,", "v6_25_inv,") in SIMPLE


def _run_block(params, agent_path=STRATEGY, build="1"):
    start = LAUNCHER.index("# v6.25 S5.")
    stop = LAUNCHER.index('echo "[preflight] v6.25 fundamental lean PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_25_BUILD": build}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=120)


def test_the_v625_block_passes_the_shipped_params_and_refuses_any_value_it_cannot_ship():
    shipped = t617._shipped()
    out = _run_block(shipped)
    assert out.returncode == 0 and out.stderr == "", out.stderr
    assert out.stdout == (
        "[preflight] v6.25 fundamental lean PASS (S5 slope=1.0 base per 2.3-bps gap unit, cap=48 base: with a fresh "
        "fundamental at least one unit from the mid every making decision of the pass reads inventory - tau, tau = "
        "sign(fp - mid) x min(cap, slope x |gap| / unit); the final validator, the A1.9 exit layer, the dust paths and "
        "every ledger keep the true inventory)\n"), out.stdout
    for key, bad, msg in (("research_v625_lean=1", "research_v625_lean=0", "without research_v625_lean=1"),
                          ("research_v625_lean=1", "", "without research_v625_lean=1"),
                          ("research_v625_lean=1", "research_v625_lean=true", "without research_v625_lean=1"),
                          ("research_v625_lean_slope=1.0", "research_v625_lean_slope=0", "research_v625_lean_slope > 0"),
                          ("research_v625_lean_slope=1.0", "research_v625_lean_slope=0.0", "research_v625_lean_slope > 0"),
                          ("research_v625_lean_slope=1.0", "research_v625_lean_slope=-1", "research_v625_lean_slope > 0"),
                          ("research_v625_lean_slope=1.0", "research_v625_lean_slope=x", "research_v625_lean_slope > 0"),
                          ("research_v625_lean_slope=1.0", "", "research_v625_lean_slope > 0"),
                          ("research_v625_lean_cap=48", "research_v625_lean_cap=0", "research_v625_lean_cap > 0"),
                          ("research_v625_lean_cap=48", "research_v625_lean_cap=", "research_v625_lean_cap > 0"),
                          ("research_v625_lean_cap=48", "", "research_v625_lean_cap > 0"),
                          ("research_v623_against_gap_bps=2.3", "research_v623_against_gap_bps=0",
                           "research_v623_against_gap_bps > 0"),
                          ("research_v623_against_gap_bps=2.3", "", "research_v623_against_gap_bps > 0")):
        assert key in shipped, key
        out = _run_block(shipped.replace(key, bad))
        assert out.returncode == 1 and msg in out.stderr and "PASS" not in out.stdout, (bad, out.stderr)
    out = _run_block(shipped.replace("research_v625_lean_slope=1.0", "research_v625_lean_slope=2.0"))
    assert out.returncode == 0 and "S5 slope=2.0 base per 2.3-bps gap unit, cap=48 base" in out.stdout   # G24 ships too
    out = _run_block("", build="0")                                       # another build: the block is not run
    assert out.returncode == 0 and out.stdout == "" and out.stderr == ""


def test_the_v625_block_refuses_a_tree_without_each_line_it_checks(tmp_path):
    cases = [(needle, name, msg, None) for needle, name, msg in re.findall(_PATTERN, _block())]
    cases += [(needle, name, msg, None) for needle, name, _n, msg in re.findall(_COUNTED, _block())]
    assert len(cases) == 9
    for i, (needle, name, msg, _x) in enumerate(cases):
        tmp = tmp_path / str(i)
        tmp.mkdir()
        for n in ("Strategy1_Research_Simple.py", "research_v625_lean.py"):
            shutil.copy(STRATEGY / n, tmp / n)
        text = (tmp / name).read_text()
        assert text.count(needle) in (1, 2), needle
        (tmp / name).write_text(text.replace(needle, "pass  # removed", 1))
        out = _run_block(t617._shipped(), agent_path=tmp)
        assert out.returncode == 1 and msg in out.stderr and "PASS" not in out.stdout, (needle, out.stderr)
    for k, (lean, true, third, why) in enumerate((
            ("raw_bid, raw_ask, v6_25_inv, rows, deep_rows, already, req,",
             "raw_bid, raw_ask, inv, rows, deep_rows, already, req,",
             "                    response, book_id, deep, depth, raw_bid, raw_ask, inv, rows, deep_rows, already, req,",
             "the deep layer"),
            ("self._v617_book(response, book_id, deep, raw_bid, raw_ask, v6_25_inv, touch_rows,",
             "self._v617_book(response, book_id, deep, raw_bid, raw_ask, inv, touch_rows,",
             "        placed_total += self._v617_book(response, book_id, deep, raw_bid, raw_ask, inv, touch_rows,",
             "the touch quote"))):
        tmp = tmp_path / f"true{k}"
        tmp.mkdir()
        for n in ("Strategy1_Research_Simple.py", "research_v625_lean.py"):
            shutil.copy(STRATEGY / n, tmp / n)
        text = (tmp / "Strategy1_Research_Simple.py").read_text()
        (tmp / "Strategy1_Research_Simple.py").write_text(text.replace(lean, true, 1))       # one call on the true inv
        out = _run_block(t617._shipped(), agent_path=tmp)
        assert out.returncode == 1 and f"{why} does not read the leaned inventory" in out.stderr, why
        (tmp / "Strategy1_Research_Simple.py").write_text(text + "\n" + third + "\n")        # a third call on it
        out = _run_block(t617._shipped(), agent_path=tmp)
        assert out.returncode == 1 and "call still reads the true inventory" in out.stderr, why
