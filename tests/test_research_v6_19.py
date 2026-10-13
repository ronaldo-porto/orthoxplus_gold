"""v6.19: S1 a cap-bound book's leading side stops adding -- research_v619_surplus_hold.

v6.18 = v6.15 ladder (mid) + v6.17 touch quote.  The validator pays 2 x min(buy capture, sell capture) per book and making
window and caps each book's volume over a rolling assessment period.  With the switch, a book whose room under the cap is
below one making window of sustainable volume withholds every order that would add on the side whose capture in the
making mirror's window already leads (level 0, every ladder level, the touch quote) -- once the mirror holds one whole
making window.  Without the switch, before the mirror is mature, off the cap or on a side that reduces, the build is v6.18.
"""
import ast
import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v619_surplus_hold as sh  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_16 as t616  # noqa: E402
import test_research_v6_17 as t617  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000
NOW = t616.NOW
CAP = 500_000.0
BOUND_USED = 480_000.0          # room 20,000 < 500,000 x 10,800 / 86,400 = 62,500: cap-bound
FREE_USED = 300_000.0           # room 200,000: not cap-bound
SWITCH_LINE = 'self.research_v619_surplus_hold = self._as_bool(getattr(self.config, "research_v619_surplus_hold", True))'
RULE_LINE = "side = v619_held_side(cap, used, buy, sell, inv, mature=mature)"
EXTRA = {"V619_CANCEL_SURPLUS": sh.CANCEL_SURPLUS, "V619MirrorAge": sh.MirrorAge,
         "V619_SURPLUS_HOLD_VERSION": sh.V619_SURPLUS_HOLD_VERSION, "v619_cap_bound": sh.cap_bound,
         "v619_held_side": sh.held_side}
METHODS = ("_v619_count", "_v619_context", "_v619_side", "_v619_snapshot")
LEVEL0_IDS = (40031, 40032)


# ---- 1. the module ------------------------------------------------------------------------------------------------------

def test_a_book_is_cap_bound_below_one_making_window_of_sustainable_room():
    assert sh.MAKING_WINDOW_NS == 10_800 * S
    assert not sh.cap_bound(CAP, 437_500.0)                 # room 62,500 = exactly one making window: not yet
    assert sh.cap_bound(CAP, 437_500.01) and sh.cap_bound(CAP, CAP) and sh.cap_bound(CAP, 600_000.0)
    for cap, used in ((0.0, 490_000.0), (None, 490_000.0), (CAP, None), (CAP, float("nan")), ("x", 1.0)):
        assert not sh.cap_bound(cap, used), (cap, used)


def test_the_leading_side_is_the_one_whose_window_capture_is_larger():
    assert sh.leading_side(5.0, 2.0) == sh.SIDE_BUY and sh.leading_side(-1.0, 0.5) == sh.SIDE_SELL
    for b, s in ((2.0, 2.0), (None, 1.0), (1.0, float("nan")), (0.0, 0.0)):
        assert sh.leading_side(b, s) is None, (b, s)


def test_the_held_side_is_the_cap_bound_leading_side_while_it_would_add():
    held = lambda b, s, inv, used=BOUND_USED, mature=True: sh.held_side(CAP, used, b, s, inv, mature=mature)
    assert held(5.0, 2.0, 0.0) == sh.SIDE_BUY and held(5.0, 2.0, 3.0) == sh.SIDE_BUY      # flat or long: a buy adds
    assert held(5.0, 2.0, -3.0) is None                                                      # short: a buy reduces
    assert held(2.0, 5.0, 0.0) == sh.SIDE_SELL and held(2.0, 5.0, 3.0) is None
    assert held(5.0, 2.0, 0.0, used=FREE_USED) is None and held(5.0, 2.0, 0.0, mature=False) is None
    assert held(2.0, 2.0, 0.0) is None and held(5.0, 2.0, None) is None


def test_the_mirror_is_mature_after_one_whole_making_window_and_starts_over_with_it():
    age = sh.MirrorAge()
    assert not age.mature(NOW)
    age.observe(NOW - 10_800 * S, 0)
    assert age.mature(NOW) and not age.mature(NOW - 1)
    age.observe(NOW, 0)                                     # the same mirror: its start is kept
    assert age.mature(NOW)
    age.observe(NOW, 1)                                     # a new simulation: the mirror started over
    assert not age.mature(NOW) and age.snapshot() == {"t0": NOW, "rebases": 1}
    age.observe(NOW - 20_000 * S, 1)                        # a clock that went back: a fresh start
    assert age.t0 == NOW - 20_000 * S


# ---- 2. the agent -------------------------------------------------------------------------------------------------------

def _method(name, base):
    scope = dict(base)
    scope.update(EXTRA)
    exec(compile(ast.Module(body=[ast.parse(_src(name)).body[0]], type_ignores=[]), f"<{name}>", "exec"), scope)
    return scope[name]


def _with19(fn):
    g = dict(fn.__globals__)
    g.update(EXTRA)
    return types.FunctionType(fn.__code__, g, fn.__name__, fn.__defaults__, fn.__closure__)


class _Mirror:
    def __init__(self, buy, sell, *, rebases=0):
        self.buy, self.sell, self.rebases, self.last_ts = float(buy), float(sell), int(rebases), NOW

    def book_capture(self, book_id):
        return (self.buy, self.sell) if int(book_id) == 3 else (0.0, 0.0)


def _agent(*, buy=5.0, sell=2.0, inv=None, used=BOUND_USED, mature=True, on=True):
    agent = t617._tq_agent(t616._page({3: 100.40}), inv=inv, used=used)        # the fundamental 38 bps above the mid
    cls = type(agent)
    base = getattr(cls, "_v633_book").__globals__
    methods = {n: _with19(getattr(cls, n)) for n in ("_v63_pass", "_v633_book")}
    for n in ("_v617_touch", "_v617_book") + METHODS:
        methods[n] = _method(n, dict(getattr(cls, "_v617_touch").__globals__, **{k: v for k, v in base.items()
                                                                                    if k not in getattr(cls, "_v617_touch").__globals__}))
    methods["_research_volume_cap_quote"] = lambda self, state: CAP
    agent.__class__ = type("P619", (cls,), methods)
    agent.research_v618_centre_off, agent._v618_counts = True, {}                # the v6.18 build underneath
    agent.research_v619_surplus_hold = on
    agent._v619_counts, agent._v619_errors = {}, 0
    agent._v62_mirror = _Mirror(buy, sell)
    if mature:
        agent._v619_age = sh.MirrorAge()
        agent._v619_age.observe(NOW - 10_800 * S, 0)
    return agent


def _deep(resp):
    return {(d, px) for (b, d, cid, px, _q) in t63._placed(resp) if b == 3 and cid in LEVEL0_IDS}


def _sides(resp):
    return {d for (b, d, _cid, _px, _q) in t63._placed(resp) if b == 3}


V618 = {("BUY", 99.80), ("SELL", 100.23)}                  # v6.18: level 0 at 21.5 ticks either side of the mid 100.015


def test_the_v618_build_is_unchanged_without_the_switch_off_the_cap_before_the_mirror_is_mature_or_on_a_reducing_side():
    for kw in ({"on": False}, {"used": FREE_USED}, {"mature": False}, {"inv": -3.0}):
        agent = _agent(**kw)
        resp = t617._run(agent)
        assert _deep(resp) == V618, kw
        assert not any(k.startswith("held_") for k in agent._v619_counts), (kw, agent._v619_counts)
        assert agent._v619_errors == 0 and t63._cancelled(resp) == set(), kw
    assert _agent(on=False)._v619_counts == {}


def test_a_cap_bound_books_leading_side_places_nothing_and_the_other_side_quotes_as_v618():
    agent = _agent(buy=5.0, sell=2.0)                         # buys lead the window; flat: a buy adds
    resp = t617._run(agent)
    assert _deep(resp) == {("SELL", 100.23)} and _sides(resp) == {"SELL"}       # no deep bid, no touch bid
    assert agent._v619_counts == {"cap_bound_book_states": 1, "held_buy": 1, "touch_held_states": 1}
    assert agent._v619_errors == 0
    agent = _agent(buy=2.0, sell=5.0)                         # sells lead: the mirror image; the touch bid still comes
    resp = t617._run(agent)
    assert _deep(resp) == {("BUY", 99.80)} and agent._v619_counts.get("held_sell") == 1
    assert t617._touch(resp) == set() and agent._v617_counts.get("yield_to_deep") == 1     # the deep bid placed first


def test_a_long_book_holds_its_leading_bid_and_a_short_one_does_not():
    agent = _agent(buy=5.0, sell=2.0, inv=3.0)
    assert "BUY" not in _sides(t617._run(agent)) and agent._v619_counts.get("held_buy") == 1
    agent = _agent(buy=5.0, sell=2.0, inv=-3.0)               # short: the bid reduces -- untouched
    assert ("BUY", 99.80) in _deep(t617._run(agent)) and "held_buy" not in agent._v619_counts


def test_resting_orders_on_the_held_side_are_cancelled_and_the_other_side_kept():
    agent = _agent(buy=5.0, sell=2.0)
    t63.t14._rest(agent, 21, 3, 0, 99.80, cid=40031)        # the v6.18 bid and offer at level 0
    t63.t14._rest(agent, 22, 3, 1, 100.23, cid=40032)
    t63.t14._rest(agent, 31, 3, 0, 100.00, cid=43031, qty=2.0)   # and a touch bid
    resp = t617._run(agent)
    assert t63._cancelled(resp) == {(3, 21), (3, 31)} and t63._placed(resp) == set()
    assert agent._v633_counts.get("cancel_v619_surplus_hold") == 1
    assert agent._v617_counts.get("cancel_v619_surplus_hold") == 1
    agent = _agent(buy=5.0, sell=2.0, on=False)               # v6.18: both kept
    t63.t14._rest(agent, 21, 3, 0, 99.80, cid=40031)
    t63.t14._rest(agent, 22, 3, 1, 100.23, cid=40032)
    assert t63._cancelled(t617._run(agent)) == set()


def test_the_snapshot_reports_the_version_the_counts_and_the_mirrors_age():
    agent = _agent()
    t617._run(agent)
    snap = agent._v619_snapshot()
    assert snap["version"] == "surplus_hold_v6_19" and snap["errors"] == 0 and snap["mirror_mature"] == 1
    assert snap["counts"]["held_buy"] == 1 and snap["mirror_age"] == {"t0": NOW - 10_800 * S, "rebases": 0}
    young = _agent(mature=False)
    t617._run(young)
    assert young._v619_snapshot()["mirror_mature"] == 0 and young._v619_age.t0 == NOW
    assert 'surplus_hold_on=int(bool(getattr(self, "research_v619_surplus_hold", False))),' in SIMPLE
    assert ('surplus_hold=(self._v619_snapshot() if getattr(self, "research_v619_surplus_hold", None) is not None else {}),'
            in SIMPLE)


# ---- 3. wiring and the launcher -------------------------------------------------------------------------------------------

def test_the_switch_ships_on_and_agrees_with_the_agent_default():
    params = t617._params()
    assert "research_v619_surplus_hold=1" in params and "research_v619_surplus_hold=0" not in params
    assert SWITCH_LINE in SIMPLE and "research_v62_breadth=1" in params        # the mirror it reads


def test_every_layer_on_the_book_takes_the_held_side():
    assert SIMPLE.count(RULE_LINE) == 1 and SIMPLE.count("from research_v619_surplus_hold import (") == 1
    assert SIMPLE.count("paced=paced, bound_scale=bound_scale, held=held,") == 2       # the deep layer, both call sites
    assert SIMPLE.count("paced=paced, held=held") == 3                                # the touch quote, both + its guard (v6.20 appends size_scale)
    assert SIMPLE.count("doomed.append((row, V619_CANCEL_SURPLUS))") == 1
    assert SIMPLE.count("why = V619_CANCEL_SURPLUS") == 1
    assert "want[side_h] = 0.0" in SIMPLE                                              # the v6.3 layer off the deep board


def test_the_v619_block_is_gated_preflighted_and_listed():
    assert "; V6_17_BUILD=0; V6_18_BUILD=0; V6_19_BUILD=0; V6_20_BUILD=0; V6_21_BUILD=0; V6_22_BUILD=0; V6_23_BUILD=0; V6_24_BUILD=0; V6_25_BUILD=0; V6_26_BUILD=0; V6_27_BUILD=0\n" in LAUNCHER
    arm = next(line for line in LAUNCHER.splitlines() if line.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_17_BUILD=1; V6_18_BUILD=1; V6_19_BUILD=1; V6_20_BUILD=1; V6_21_BUILD=1; V6_22_BUILD=1; V6_23_BUILD=1; V6_24_BUILD=1; V6_25_BUILD=1; V6_26_BUILD=1; V6_27_BUILD=1"), arm
    assert 'if [[ "${V6_19_BUILD:-0}" == "1" ]]; then' in LAUNCHER
    assert "tests/test_research_v6_19.py" in LAUNCHER


def test_every_v619_preflight_pattern_is_in_the_file_it_checks():
    import re
    start = LAUNCHER.index('if [[ "${V6_19_BUILD:-0}" == "1" ]]; then')
    block = LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.19 surplus hold PASS', start)]
    pats = re.findall(r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\"", block)
    assert len(pats) == 3, pats
    for pat, name in pats:
        assert pat in (STRATEGY / name).read_text(), (pat, name)


def _run_block(params, agent_path=STRATEGY):
    start = LAUNCHER.index("# v6.19 S1.")
    stop = LAUNCHER.index('echo "[preflight] v6.19 surplus hold PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_19_BUILD": "1"}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_the_v619_block_passes_the_shipped_params_and_refuses_the_switch_off_or_no_mirror():
    shipped = t617._shipped()
    out = _run_block(shipped)
    assert out.returncode == 0 and "[preflight] v6.19 surplus hold PASS" in out.stdout, out.stderr
    for bad in ("research_v619_surplus_hold=0", "research_v619_surplus_hold=10", ""):
        out = _run_block(shipped.replace("research_v619_surplus_hold=1", bad))
        assert out.returncode == 1 and "v6.19 S1 build without research_v619_surplus_hold=1" in out.stderr, repr(bad)
    assert shipped.count("research_v62_breadth=1") == 1
    out = _run_block(shipped.replace("research_v62_breadth=1", "research_v62_breadth=0"))
    assert out.returncode == 1 and "the making mirror it reads needs research_v62_breadth=1" in out.stderr


def test_the_v619_block_refuses_an_agent_without_the_import_the_rule_or_the_module_function():
    cases = (("Strategy1_Research_Simple.py", "from research_v619_surplus_hold import (", "v6.19 S1 module is not imported"),
             ("Strategy1_Research_Simple.py", RULE_LINE, "v6.19 S1 the held side is not taken from the rule"),
             ("research_v619_surplus_hold.py",
              "def held_side(cap: Any, used: Any, buy_capture: Any, sell_capture: Any, inventory: Any, *, mature: bool) -> str | None:",
              "v6.19 S1 research_v619_surplus_hold has no held_side"))
    for name, needle, message in cases:
        tmp = Path(tempfile.mkdtemp(prefix="t619_"))
        try:
            for n in ("Strategy1_Research_Simple.py", "research_v619_surplus_hold.py"):
                shutil.copy(STRATEGY / n, tmp / n)
            text = (tmp / name).read_text()
            assert text.count(needle) == 1, needle
            (tmp / name).write_text(text.replace(needle, "# removed"))
            out = _run_block(t617._shipped(), agent_path=tmp)
            assert out.returncode == 1 and message in out.stderr, (name, out.stderr)
        finally:
            shutil.rmtree(tmp)
