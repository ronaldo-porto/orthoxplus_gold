"""v6.15: S1 a book first seen on an empty volume window (a new UID) launches -- research_v615_launch.

v6.8 S5's rolling line holds every book of a new UID to the sustainable rate from its first state, while v6.14's books
trade 0.05-2.9x of it on their own, so a new UID averages ~0.70x and reaches its 24-h immunity end far below the prune
line.  The launch line spends one making window at twice the sustainable rate with twice the deep clip and pays it back
before the assessment window rolls.  Replayed on a fresh UID (10-05): first windows' making share 0.29/0.53/0.70% ->
0.49/0.89/1.22%, making per 1M unchanged.
"""
import ast
import os
import re
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v615_launch as lz  # noqa: E402
import research_v641_pace as v641  # noqa: E402
import research_v66_pace_line as pl  # noqa: E402
import research_v68_deep_owns as d68  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_4_1_volume_pace as t641  # noqa: E402
import test_research_v6_6 as t66  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000
CAP = 500_000.0
A = v641.ASSESSMENT_NS
M = lz.MAKING_WINDOW_NS
SUSTAINABLE = CAP / (A / S)                                   # 5.787 quote per sim-s per book
CALL_LINE = "clip_b = v615_launch_clip(deep.clip, pace, book_id, now_ts)"
EXTRA = {"V68RollingPaceLine": d68.RollingPaceLine, "V66PaceLine": pl.PaceLine, "V615LaunchPaceLine": lz.LaunchPaceLine,
         "V615_LAUNCH_VERSION": lz.V615_LAUNCH_VERSION, "v615_launch_clip": lz.launch_clip}


def _params():
    start = LAUNCHER.index('PARAMS="')
    return LAUNCHER[start:LAUNCHER.index('"\n', start + len('PARAMS="'))]


def _shipped():
    return _params()[len('PARAMS="'):].replace("\\\n", " ")


# ---- 1. the line --------------------------------------------------------------------------------------------------------

def test_the_launch_spends_one_making_window_at_twice_the_rate_and_reaches_the_cap_as_the_window_rolls():
    assert lz.V615_LAUNCH_VERSION == "launch_v6_15" and lz.LAUNCH_RATE == 2.0 and M == 10_800 * S
    r2 = lz.payback_rate()
    assert abs(r2 - (86_400 - 2 * 10_800) / (86_400 - 10_800)) < 1e-12                    # 0.857 x after the launch
    w = v641.PACE_WINDOW_NS
    assert abs(lz.launch_line(CAP, 0, -w) - 0.0) < 1e-9
    assert abs(lz.launch_line(CAP, 0, M - w) - CAP * 2 * 10_800 / 86_400) < 1e-6             # a quarter of the cap
    assert abs(lz.launch_line(CAP, 0, 2 * M - w) - CAP * (2 * 10_800 + r2 * 10_800) / 86_400) < 1e-6   # then the payback rate
    assert lz.launch_line(CAP, 0, A // 2) < CAP                                               # not at the cap before it rolls
    assert abs(lz.launch_line(CAP, 0, A - w) - CAP) < 1e-6                                    # the cap, as the window rolls
    assert lz.launch_line(CAP, 0, 3 * A) == CAP                                               # never above the cap
    assert lz.launch_line(CAP, 10 * S, 0) >= 0.0                                              # a clock behind: never below 0
    assert lz.launch_line(None, 0, M) == 0.0 and lz.launch_line(float("nan"), 0, M) == 0.0


def test_the_launch_line_is_never_below_the_rolling_line_from_an_empty_window_and_meets_it_at_the_cap():
    for t in (0, 600 * S, M // 2, M, 2 * M, A // 2, A - 3_600 * S, A - v641.PACE_WINDOW_NS, A, 2 * A):
        launch, rolling = lz.launch_line(CAP, 0, t), d68.rolling_line(CAP, 0.0, 0, t)
        assert launch >= rolling - 1e-6, (t, launch, rolling)
    assert abs(lz.launch_line(CAP, 0, M - v641.PACE_WINDOW_NS) - 2 * d68.rolling_line(CAP, 0.0, 0, M - v641.PACE_WINDOW_NS)) < 1e-6
    assert lz.launch_line(CAP, 0, A) == d68.rolling_line(CAP, 0.0, 0, A) == CAP


# ---- 2. the pace line ---------------------------------------------------------------------------------------------------

def test_only_a_book_first_seen_on_an_empty_window_launches_and_the_rest_is_v68():
    launch, rolling = lz.LaunchPaceLine(), d68.RollingPaceLine()
    for line in (launch, rolling):
        line.observe(3, 0, 0.0)                     # a new UID's book
        line.observe(5, 0, 300_000.0)               # a running UID's book
        line.observe(3, 3_600 * S, 9_000.0)         # later reports never start a launch
        line.observe(7, 3_600 * S, 0.0)             # a book first seen later on an empty window launches then
    assert launch.launching == {3, 7}
    t = 2 * 3_600 * S
    assert launch.line(5, t, CAP) == rolling.line(5, t, CAP)                                      # v6.8, unchanged
    assert abs(launch.line(3, t, CAP) - lz.launch_line(CAP, 0, t)) < 1e-9
    assert launch.line(3, t, CAP) > rolling.line(3, t, CAP)
    # one hour in at 1.5x the sustainable rate: the launch lets it trade, v6.8 holds it
    used = 1.5 * SUSTAINABLE * 3_600
    assert not launch.paced(3, 3_600 * S, used, CAP, None) and rolling.paced(3, 3_600 * S, used, CAP, None)
    assert launch.snapshot()["rolling"] == 1 and launch.snapshot()["launch"]["books"] == 2
    assert isinstance(launch, d68.RollingPaceLine)                                                # the agent's rolling_line flag


def test_the_launch_window_is_one_making_window_and_the_clip_follows_it():
    line = lz.LaunchPaceLine()
    line.observe(3, 0, 0.0)
    line.observe(5, 0, 300_000.0)
    assert line.launch_open(3, 0) and line.launch_open(3, M - 1)
    assert not line.launch_open(3, M) and not line.launch_open(5, 0) and not line.launch_open(9, 0)
    assert lz.launch_clip(2.0, line, 3, M - 1) == 4.0
    assert lz.launch_clip(2.0, line, 3, M) == 2.0 and lz.launch_clip(2.0, line, 5, 0) == 2.0
    assert lz.launch_clip(2.0, d68.RollingPaceLine(), 3, 0) == 2.0                                 # a line without launches
    assert lz.launch_clip(2.0, None, 3, 0) == 2.0
    snap = line.snapshot()["launch"]
    assert snap["open_now"] == 1 and snap["rate"] == 2.0 and snap["version"] == "launch_v6_15"


def test_a_new_simulation_shifts_the_launch_with_the_line():
    line = lz.LaunchPaceLine()
    line.observe(3, 80_000 * S, 0.0)
    before = line.line(3, 85_000 * S, CAP)
    line.observe(3, 85_000 * S, 20_000.0)
    line.observe(3, 100 * S, 20_000.0)                                                              # the next simulation
    assert line.rebases == 1 and 3 in line.launching
    assert line.start[3][0] == (80_000 - 85_000 + 100) * S
    assert abs(line.line(3, 100 * S, CAP) - before) < 1e-6                                          # the same place on the line
    assert line.launch_open(3, 100 * S)                                                             # 5,000 sim-s into its window
    assert not line.launch_open(3, 100 * S + (M - 5_000 * S))
    line.reset()
    assert line.launching == set() and line.start == {}


# ---- 3. the agent -------------------------------------------------------------------------------------------------------

def _method(name):
    scope = dict(t641.EXTRA)
    scope.update(t66.EXTRA)
    scope.update(EXTRA)
    exec(compile(ast.Module(body=[ast.parse(_src(name)).body[0]], type_ignores=[]), f"<{name}>", "exec"), scope)
    return scope[name]


def _with15(fn):
    g = dict(fn.__globals__)
    g.update(EXTRA)
    return types.FunctionType(fn.__code__, g, fn.__name__, fn.__defaults__, fn.__closure__)


def _launch_agent(used, *, launch=True, rolling=True):
    agent = t66._v66(t641._agent(t66._layer(books=(3, 5)), used=used), spacing=False, line=True)
    cls = type(agent)
    methods = {n: _with15(getattr(cls, n)) for n in ("_v63_pass", "_v633_book")}
    for n in ("_v66_pace_ref", "_v615_count", "_v615_snapshot"):
        methods[n] = _method(n)
    agent.__class__ = type("P615", (cls,), methods)
    agent.research_v68_rolling_budget = rolling
    agent.research_v615_launch = launch
    agent._v615_counts, agent._v615_errors = {}, 0
    agent._v66_pace = None
    return agent


def _level0_quantities(resp, book):
    cids = {40001 + 10 * book, 40002 + 10 * book}
    return sorted(q for (b, _side, cid, _px, q) in t63._placed(resp) if b == book and cid in cids)


def test_the_pace_line_is_the_launch_line_only_with_both_switches_on():
    assert isinstance(_launch_agent({})._v66_pace_ref(), lz.LaunchPaceLine)
    off = _launch_agent({}, launch=False)._v66_pace_ref()
    assert isinstance(off, d68.RollingPaceLine) and not isinstance(off, lz.LaunchPaceLine)
    v66 = _launch_agent({}, rolling=False)._v66_pace_ref()
    assert isinstance(v66, pl.PaceLine) and not isinstance(v66, d68.RollingPaceLine)


def test_in_the_pass_a_new_uids_book_quotes_the_launch_clip_and_a_running_book_its_clip():
    agent = _launch_agent({3: 0.0, 5: 300_000.0})
    resp = t641._run(agent, {3: t63._book(), 5: t63._book()})
    assert _level0_quantities(resp, 3) == [4.0, 4.0]                    # both sides, twice the deep clip
    assert _level0_quantities(resp, 5) == [2.0, 2.0]                    # the running book: v6.14 exactly
    assert agent._v615_counts == {"launch_clip_book_states": 1} and agent._v615_errors == 0
    snap = agent._v615_snapshot()
    assert snap["version"] == "launch_v6_15" and snap["line"]["books"] == 1 and snap["line"]["open_now"] == 1
    off = _launch_agent({3: 0.0, 5: 300_000.0}, launch=False)
    resp = t641._run(off, {3: t63._book(), 5: t63._book()})
    assert _level0_quantities(resp, 3) == [2.0, 2.0] and _level0_quantities(resp, 5) == [2.0, 2.0]
    assert off._v615_counts == {}


def test_the_switch_is_in_the_state_record():
    assert 'launch_on=int(bool(getattr(self, "research_v615_launch", False))),' in SIMPLE
    assert "launch=(self._v615_snapshot()" in SIMPLE


# ---- 4. wiring ----------------------------------------------------------------------------------------------------------

def test_the_switch_ships_on_and_agrees_with_the_agent_default():
    params = _params()
    assert "research_v615_launch=1" in params and "research_v615_launch=0" not in params
    assert 'self.research_v615_launch = self._as_bool(getattr(self.config, "research_v615_launch", True))' in SIMPLE
    assert SIMPLE.count(CALL_LINE) == 1
    assert SIMPLE.count("pace = V615LaunchPaceLine()") == 1
    assert "research_v68_rolling_budget=1 " in params + " "             # the launch line is a v6.8 rolling line


def test_the_v615_block_is_gated_preflighted_and_listed():
    assert "; V6_10_BUILD=0; V6_11_BUILD=0; V6_12_BUILD=0; V6_14_BUILD=0; V6_15_BUILD=0; V6_16_BUILD=0; V6_17_BUILD=0; V6_18_BUILD=0; V6_19_BUILD=0; V6_20_BUILD=0; V6_21_BUILD=0; V6_22_BUILD=0; V6_23_BUILD=0; V6_24_BUILD=0\n" in LAUNCHER  # v6.16 appends its own
    arm = next(line for line in LAUNCHER.splitlines() if line.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_12_BUILD=1; V6_14_BUILD=1; V6_15_BUILD=1; V6_16_BUILD=1; V6_17_BUILD=1; V6_18_BUILD=1; V6_19_BUILD=1; V6_20_BUILD=1; V6_21_BUILD=1; V6_22_BUILD=1; V6_23_BUILD=1; V6_24_BUILD=1"), arm
    assert 'if [[ "${V6_15_BUILD:-0}" == "1" ]]; then' in LAUNCHER
    assert 'echo "[preflight] v6.15 launch PASS (one making window at 2x line and clip, then payback)"' in LAUNCHER
    assert "tests/test_research_v6_15.py" in LAUNCHER


def test_every_v615_preflight_pattern_is_in_the_file_it_checks():
    start = LAUNCHER.index('if [[ "${V6_15_BUILD:-0}" == "1" ]]; then')
    block = LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.15 launch PASS', start)]
    pats = re.findall(r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\"", block)
    assert len(pats) == 4, pats
    for pat, name in pats:
        assert pat in (STRATEGY / name).read_text(), (pat, name)


def _run_block(params, agent_path=STRATEGY):
    start = LAUNCHER.index("# v6.15 S1.")
    stop = LAUNCHER.index('echo "[preflight] v6.15 launch PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_15_BUILD": "1"}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_the_v615_block_passes_the_shipped_params_and_refuses_the_switch_off():
    out = _run_block(_shipped())
    assert out.returncode == 0 and "[preflight] v6.15 launch PASS" in out.stdout, out.stderr
    for bad in ("research_v615_launch=0", "research_v615_launch=10", ""):
        out = _run_block(_shipped().replace("research_v615_launch=1", bad))
        assert out.returncode == 1 and "v6.15 S1 build without research_v615_launch=1" in out.stderr, repr(bad)


def test_the_v615_block_refuses_an_agent_without_the_launch_line_or_clip(tmp_path=None):
    import tempfile
    tmp = Path(tmp_path) if tmp_path else Path(tempfile.mkdtemp())
    for f in STRATEGY.glob("*.py"):
        (tmp / f.name).write_text(f.read_text())
    (tmp / "Strategy1_Research_Simple.py").write_text(SIMPLE.replace(CALL_LINE, "clip_b = deep.clip"))
    out = _run_block(_shipped(), agent_path=tmp)
    assert out.returncode == 1 and "does not read the launch window" in out.stderr
    (tmp / "Strategy1_Research_Simple.py").write_text(SIMPLE.replace("pace = V615LaunchPaceLine()", "pass"))
    out = _run_block(_shipped(), agent_path=tmp)
    assert out.returncode == 1 and "is not the launch line" in out.stderr
    (tmp / "Strategy1_Research_Simple.py").write_text(SIMPLE)
    src = (STRATEGY / "research_v615_launch.py").read_text()
    (tmp / "research_v615_launch.py").write_text(src.replace("class LaunchPaceLine(RollingPaceLine):",
                                                             "class LaunchPaceLine(object):"))
    out = _run_block(_shipped(), agent_path=tmp)
    assert out.returncode == 1 and "has no launch line" in out.stderr
