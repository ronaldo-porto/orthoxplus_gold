"""v6.13: S1 the seam reserve (research_v613_seam_reserve).  In the simulation's last 30,000 sim-s each book's own new
volume may not exceed the volume cap's sustainable rate (cap / assessment period) over the elapsed time plus one
sampling interval; a book over it is held on both sides -- its orders cancelled, nothing placed.

The validator's volume window rolls across a seam, so this simulation's last hours stay on the next one's cap.  Measured
10-03 on sim 20260929_2015: 193 of the five UIDs' books at >= 450k of the 500k cap; without a ceiling the next
simulation's first 30,000 sim-s are 22% of book-time dark; the sustainable-rate ceiling from sim ~62,000 swaps -15.9% of
this simulation's volume for +20.6% in the next one's opening (a ~1:1 swap).
"""
import ast
import math
import os
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v613_seam_reserve as sr  # noqa: E402
import research_v641_pace as vp  # noqa: E402
import research_v66_add_spacing as sp  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_4_1_volume_pace as t641  # noqa: E402
import test_research_v6_9 as t69  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
S = 1_000_000_000
NOW = t63.NOW
CAP = 500_000.0
RATE = CAP / 86_400.0                                    # the sustainable rate, quote per sim-s
HELD_LINE = "held = bool(reserve.held(book_id, now_ts, v613_cap, v613_duration))"
UID = 7


# ---- 1. the rule ------------------------------------------------------------------------------------------------------

def test_the_horizon_is_the_simulations_last_30000_sim_seconds():
    assert sr.RESERVE_HORIZON_S == 30_000 and sr.V613_SEAM_RESERVE_VERSION == "seam_reserve_v6_13"
    dur = 86_400 * S
    assert sr.in_horizon(56_400 * S, dur) and sr.in_horizon(86_000 * S, dur)
    assert not sr.in_horizon(56_399 * S, dur) and not sr.in_horizon(1_000 * S, dur)
    for now, d in ((None, dur), (56_400 * S, None), (56_400 * S, 0), ("x", dur), (-1, dur)):
        assert not sr.in_horizon(now, d), (now, d)


def test_the_allowance_is_the_caps_sustainable_rate_plus_one_sampling_interval():
    assert math.isclose(sr.allowance(CAP, 0), RATE * 600)                       # 3,472 quote of burst at the start
    assert math.isclose(sr.allowance(CAP, 3_600 * S), RATE * 4_200)
    assert math.isclose(sr.allowance(CAP, -5 * S), RATE * 600)                  # time never runs back into it
    for cap in (None, 0.0, -1.0, "x"):
        assert sr.allowance(cap, 3_600 * S) is None, cap


def test_fill_notional_counts_price_times_quantity():
    assert sr.fill_notional([("buy", 100.0, 2.0), ("sell", 101.0, 1.0)]) == 301.0
    assert sr.fill_notional([("buy", "x", 2.0), ("sell", 100.0), None, ("buy", 100.0, -1.0)]) == 0.0
    assert sr.fill_notional(None) == 0.0


def test_a_book_is_held_while_its_own_volume_since_the_start_exceeds_the_allowance():
    r = sr.SeamReserve()
    dur = 86_400 * S
    r.observe(3, 50_000 * S, [("buy", 100.0, 100.0)], dur)                      # before the horizon: not counted
    assert r.start_ns is None and r.spent == {} and not r.held(3, 50_000 * S, CAP, dur)
    r.observe(3, 60_000 * S, [("buy", 100.0, 30.0)], dur)                       # 3,000 < 3,472: free
    assert r.start_ns == 60_000 * S and r.spent[3] == 3_000.0 and not r.held(3, 60_000 * S, CAP, dur)
    r.observe(3, 60_000 * S, [("sell", 100.0, 5.0)], dur)                       # 3,500 > 3,472: held
    assert r.held(3, 60_000 * S, CAP, dur) and r.held_now == {3}
    assert not r.held(3, 60_100 * S, CAP, dur)                                  # 100 s later the line passed it
    assert r.held_now == set()
    r.observe(4, 60_100 * S, [], dur)
    assert r.spent[4] == 0.0 and not r.held(4, 60_100 * S, CAP, dur)
    assert not r.held(3, 60_000 * S, None, dur)                                 # no cap: never held
    snap = r.snapshot()
    assert snap["version"] == "seam_reserve_v6_13" and snap["books"] == 2 and snap["start_ns"] == 60_000 * S


def test_a_new_simulation_clears_the_reserve():
    r = sr.SeamReserve()
    dur = 86_400 * S
    r.observe(3, 60_000 * S, [("buy", 100.0, 40.0)], dur)
    assert r.held(3, 60_000 * S, CAP, dur)
    r.observe(3, 100 * S, [], dur)                                              # the clock runs back: a new simulation
    assert r.rebases == 1 and r.start_ns is None and r.spent == {} and r.held_now == set()
    assert not r.held(3, 100 * S, CAP, dur)


# ---- 2. the pass ------------------------------------------------------------------------------------------------------

_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
EXTRA = {"V613SeamReserve": sr.SeamReserve, "V613_SEAM_RESERVE_VERSION": sr.V613_SEAM_RESERVE_VERSION,
         "v66_own_fills": sp.own_fills, "v641_duration_ns": vp.duration_ns}


def _method(name):
    scope = dict(EXTRA)
    exec(compile(ast.Module(body=[ast.parse(_src(name)).body[0]], type_ignores=[]), f"<{name}>", "exec"), scope)
    return scope[name]


def _with_globals(fn):
    g = dict(fn.__globals__)
    g.update(EXTRA)
    return types.FunctionType(fn.__code__, g, fn.__name__, fn.__defaults__, fn.__closure__)


def _agent(*, on=True, **kw):
    agent = t69._agent(**kw)
    cls = type(agent)
    methods = {n: _with_globals(getattr(cls, n)) for n in ("_v63_pass", "_v633_book")}
    for n in ("_v613_count", "_v613_ref", "_v613_snapshot"):
        methods[n] = _method(n)
    methods["_research_volume_cap_quote"] = lambda self, state: CAP
    agent.__class__ = type("P613", (cls,), methods)
    agent.uid = UID
    if on:
        agent.research_v613_seam_reserve = True
    return agent


def _fill(q, *, ours=True, maker=True, s=1, p=100.0):
    ma = UID if (ours and maker) else 900
    ta = UID if (ours and not maker) else 901
    return {"y": "t", "p": p, "q": q, "s": s, "Ma": ma, "Ta": ta}


IN = NOW + 20_000 * S          # a simulation ending 20,000 sim-s from now: inside the horizon
OUT = NOW + 40_000 * S         # ... 40,000 sim-s from now: outside it


def _run(agent, events, duration):
    return t641._run(agent, {3: t63._book(events=events)}, duration=duration)


def test_a_book_over_its_allowance_is_held_on_both_sides():
    agent = _agent()
    t69._rest(agent, 11, 0, 0)
    t69._rest(agent, 12, 1, 0)
    resp = _run(agent, [_fill(40.0)], IN)                                       # 4,000 > 3,472
    assert t63._placed(resp) == set() and t63._cancelled(resp) == {(3, 11), (3, 12)}
    assert agent._v613_counts == {"held_book_states": 1} and agent._v613_reserve.held_now == {3}


def test_a_book_within_its_allowance_and_any_book_outside_the_horizon_are_v612():
    for events, duration in (([_fill(10.0)], IN), ([_fill(40.0)], OUT), ([], IN)):
        agent = _agent()
        resp = _run(agent, events, duration)
        assert t63._placed(resp) == t69._level(0), (events, duration)
        assert "held_book_states" not in getattr(agent, "_v613_counts", {})


def test_only_our_fills_count_maker_or_taker():
    agent = _agent()
    _run(agent, [_fill(40.0, ours=False), _fill(10.0, maker=False, s=0)], IN)
    assert agent._v613_reserve.spent == {3: 1_000.0}
    assert "held_book_states" not in getattr(agent, "_v613_counts", {})


def test_the_switch_off_never_builds_a_reserve():
    agent = _agent(on=False)
    resp = _run(agent, [_fill(40.0)], IN)
    assert t63._placed(resp) == t69._level(0) and getattr(agent, "_v613_reserve", None) is None


def test_the_reserve_is_in_the_state_record():
    agent = _agent()
    _run(agent, [_fill(40.0)], IN)
    snap = agent._v613_snapshot()
    assert snap["version"] == "seam_reserve_v6_13" and snap["held_now"] == 1 and snap["counts"] == {"held_book_states": 1}


# ---- 3. wiring --------------------------------------------------------------------------------------------------------

def _params():
    start = LAUNCHER.index('PARAMS="')
    return LAUNCHER[start:LAUNCHER.index('"\n', start + len('PARAMS="'))]


def _shipped():
    return _params()[len('PARAMS="'):].replace("\\\n", " ")


def test_the_switch_ships_on_and_agrees_with_the_agent_default():
    assert "research_v613_seam_reserve=1" in _params() and "research_v613_seam_reserve=0" not in _params()
    assert ('self.research_v613_seam_reserve = self._as_bool(getattr(self.config, "research_v613_seam_reserve", True))'
            in SIMPLE)
    assert SIMPLE.count(HELD_LINE) == 1
    assert 'if bool(getattr(self, "research_v613_seam_reserve", False)):' in SIMPLE          # a harness agent stays v6.12
    assert 'seam_reserve_on=int(bool(getattr(self, "research_v613_seam_reserve", False))),' in SIMPLE


def test_the_v613_block_is_gated_preflighted_and_listed():
    assert "; V6_10_BUILD=0; V6_11_BUILD=0; V6_12_BUILD=0; V6_13_BUILD=0; V7_0_BUILD=0\n" in LAUNCHER
    arm = next(line for line in LAUNCHER.splitlines() if line.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_12_BUILD=1; V6_13_BUILD=1; V7_0_BUILD=1"), arm
    assert 'if [[ "${V6_13_BUILD:-0}" == "1" ]]; then' in LAUNCHER
    assert 'echo "[preflight] v6.13 seam reserve PASS (sustainable rate, horizon 30000 sim-s)"' in LAUNCHER
    assert "tests/test_research_v6_13.py" in LAUNCHER


def test_every_v613_preflight_pattern_is_in_the_file_it_checks():
    import re
    start = LAUNCHER.index('if [[ "${V6_13_BUILD:-0}" == "1" ]]; then')
    block = LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.13 seam reserve PASS', start)]
    pats = re.findall(r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\"", block)
    assert len(pats) == 5, pats
    for pat, name in pats:
        assert "\n" not in pat, pat                                              # grep -F would OR the lines
        assert pat in (STRATEGY / name).read_text(), (pat, name)


def _run_block(params, agent_path=STRATEGY):
    start = LAUNCHER.index("# v6.13 S1.")
    stop = LAUNCHER.index('echo "[preflight] v6.13 seam reserve PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_13_BUILD": "1"}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_the_v613_block_passes_the_shipped_params_and_refuses_the_switch_off():
    out = _run_block(_shipped())
    assert out.returncode == 0 and "[preflight] v6.13 seam reserve PASS" in out.stdout, out.stderr
    for bad in ("research_v613_seam_reserve=0", "research_v613_seam_reserve=10", ""):
        out = _run_block(_shipped().replace("research_v613_seam_reserve=1", bad))
        assert out.returncode == 1 and "v6.13 S1 build without research_v613_seam_reserve=1" in out.stderr, repr(bad)


def test_the_v613_block_refuses_an_agent_without_the_hold_or_a_module_with_another_horizon(tmp_path):
    module = (STRATEGY / "research_v613_seam_reserve.py").read_text()
    (tmp_path / "research_v613_seam_reserve.py").write_text(module)
    (tmp_path / "Strategy1_Research_Simple.py").write_text(SIMPLE.replace(HELD_LINE, "held = False"))
    out = _run_block(_shipped(), agent_path=tmp_path)
    assert out.returncode == 1 and "does not hold a book over its reserve allowance" in out.stderr
    (tmp_path / "Strategy1_Research_Simple.py").write_text(SIMPLE.replace('self._v613_count("held_book_states")', "pass"))
    out = _run_block(_shipped(), agent_path=tmp_path)
    assert out.returncode == 1 and "a held book is not idled" in out.stderr
    (tmp_path / "Strategy1_Research_Simple.py").write_text(SIMPLE)
    for old, new, msg in (("RESERVE_HORIZON_S = 30_000 ", "RESERVE_HORIZON_S = 60_000 ", "30,000 sim-s horizon"),
                          ("rate = c / (float(assessment_ns) / 1e9)", "rate = c / 3600.0", "sustainable rate")):
        (tmp_path / "research_v613_seam_reserve.py").write_text(module.replace(old, new))
        out = _run_block(_shipped(), agent_path=tmp_path)
        assert out.returncode == 1 and msg in out.stderr, (old, out.stderr)
