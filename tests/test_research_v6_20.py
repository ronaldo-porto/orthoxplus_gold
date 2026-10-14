"""v6.20: S1 the room line and smaller orders ahead of it -- research_v620_room_pace.

v6.19 = v6.18 + the cap-bound surplus hold.  With the switch, a book whose window is young (its venue volume has not
fallen since its first report) is paced against the room line -- the room up to the cap-bound threshold
T = cap - cap x 10,800 / 86,400 spread over one assessment period, a launching book's line never above T -- and every
order the deep layer and the touch quote place is scaled by 1 - (volume - line) / (cap x 10,800 / 86,400), never below
the venue's minimum.  An aged book (its volume fell: the window rolls off) keeps the v6.15 / v6.8 line and full sizes.
Without the switch the build is v6.19.
"""
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

import research_v620_room_pace as rp  # noqa: E402
from research_v615_launch import LaunchPaceLine  # noqa: E402
from research_v68_deep_owns import RollingPaceLine  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_17 as t617  # noqa: E402
import test_research_v6_19 as t619  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
S = 1_000_000_000
NOW = t619.NOW
CAP = 500_000.0
BAND = 62_500.0                  # 500,000 x 10,800 / 86,400
T = CAP - BAND                   # the cap-bound threshold
W = 600 * S                      # the pace's sampling window: the line runs one window early
U0 = 300_000.0
SWITCH_LINE = 'self.research_v620_room_pace = self._as_bool(getattr(self.config, "research_v620_room_pace", True))'
LINE_LINE = "pace = V620RoomPaceLine(launch=isinstance(pace, V615LaunchPaceLine))"
SCALE_LINE = "size_scale = self._v620_scale(pace, book_id, now_ts, used, pace_cap, pace_duration)"
EXTRA = {"V620RoomPaceLine": rp.RoomPaceLine, "V620_ROOM_PACE_VERSION": rp.V620_ROOM_PACE_VERSION,
         "v620_scaled_quantity": rp.scaled_quantity}
METHODS = ("_v620_count", "_v620_scale", "_v620_snapshot")


# ---- 1. the module ------------------------------------------------------------------------------------------------------

def test_the_band_is_one_making_window_of_the_cap_and_the_threshold_the_cap_less_it():
    assert rp.band(CAP) == BAND and rp.threshold(CAP) == T
    for bad in (0.0, -1.0, None, "x", float("nan")):
        assert rp.band(bad) == 0.0 and rp.threshold(bad) == 0.0, bad


def test_the_room_line_spreads_the_room_up_to_the_threshold_over_one_assessment_period():
    assert rp.room_line(CAP, U0, 0, 0, window_ns=0) == U0
    assert rp.room_line(CAP, U0, 0, 43_200 * S, window_ns=0) == U0 + (T - U0) / 2
    assert rp.room_line(CAP, U0, 0, 86_400 * S, window_ns=0) == T
    assert rp.room_line(CAP, U0, 0, 200_000 * S, window_ns=0) == T                  # never above the threshold
    assert rp.room_line(CAP, U0, 0, 0, window_ns=W) == U0 + (T - U0) * 600 / 86_400  # one window early
    assert rp.room_line(CAP, 450_000.0, 0, 86_400 * S, window_ns=0) == 450_000.0     # first seen above T: flat
    assert rp.room_line(CAP, 600_000.0, 0, 0, window_ns=0) == CAP                    # never above the cap


def test_the_size_factor_is_one_behind_the_line_and_zero_a_band_ahead():
    assert rp.size_scale(U0, U0, CAP) == 1.0 and rp.size_scale(U0 - 1.0, U0, CAP) == 1.0
    assert rp.size_scale(U0 + BAND / 2, U0, CAP) == 0.5 and rp.size_scale(U0 + BAND / 4, U0, CAP) == 0.75
    assert rp.size_scale(U0 + BAND, U0, CAP) == 0.0 and rp.size_scale(U0 + 3 * BAND, U0, CAP) == 0.0
    for used, line, cap in ((None, U0, CAP), (U0, None, CAP), (U0, U0, 0.0), (float("nan"), U0, CAP)):
        assert rp.size_scale(used, line, cap) == 1.0, (used, line, cap)


def test_a_scaled_quantity_never_drops_below_the_minimum_order():
    assert rp.scaled_quantity(2.0, 0.5, 0.25) == 1.0 and rp.scaled_quantity(4.0, 0.3, 0.25) == 4.0 * 0.3
    assert rp.scaled_quantity(2.0, 0.0, 0.25) == 0.25 and rp.scaled_quantity(2.0, 0.05, 0.25) == 0.25
    assert rp.scaled_quantity(2.0, 1.0, 0.25) == 2.0 and rp.scaled_quantity(2.0, None, 0.25) == 2.0
    assert rp.scaled_quantity(0.2, 0.5, 0.25) == 0.2                                 # already below: left alone


def test_a_young_book_keeps_the_room_line_until_its_volume_falls_then_it_is_v615_exactly():
    pace, ref = rp.RoomPaceLine(launch=True), LaunchPaceLine()
    for p in (pace, ref):
        p.observe(3, 0, U0)
        p.observe(3, 60 * S, U0 + 5_000.0)
    assert pace.young(3) and pace.line(3, 60 * S, CAP) == rp.room_line(CAP, U0, 0, 60 * S, window_ns=W)
    assert pace.line(3, 60 * S, CAP) < ref.line(3, 60 * S, CAP)
    assert pace.scale(3, 60 * S, pace.line(3, 60 * S, CAP) + BAND / 2, CAP) == 0.5
    for p in (pace, ref):
        p.observe(3, 120 * S, U0 + 4_000.0)                                        # the window rolls off
    assert not pace.young(3) and pace.line(3, 120 * S, CAP) == ref.line(3, 120 * S, CAP)
    assert pace.scale(3, 120 * S, CAP, CAP) == 1.0
    assert pace.paced(3, 120 * S, U0 + 4_000.0, CAP, None) == ref.paced(3, 120 * S, U0 + 4_000.0, CAP, None)
    pace.observe(3, 180 * S, U0 + 9_000.0)                                         # aged for good
    assert not pace.young(3)
    snap = pace.snapshot()["room"]
    assert snap == {"version": "room_pace_v6_20", "launch_on": 1, "young": 0, "aged": 1}


def test_a_zero_reading_or_float_noise_does_not_age_a_young_book():
    pace = rp.RoomPaceLine(launch=False)
    pace.observe(3, 0, U0)
    pace.observe(3, S, 0.0)                                                        # a missing account reads 0
    pace.observe(3, 2 * S, U0 - 1e-7)                                              # and a float wobble
    assert pace.young(3)
    pace.observe(3, 3 * S, U0 - 1e-7 - 25.0)                                       # one minimum trade rolled off
    assert not pace.young(3)


def test_a_launching_book_keeps_the_launch_line_below_the_threshold_and_only_with_launching_on():
    pace, ref = rp.RoomPaceLine(launch=True), LaunchPaceLine()
    for p in (pace, ref):
        p.observe(5, 0, 0.0)
    assert pace.launch_open(5, S) and pace.line(5, S, CAP) == ref.line(5, S, CAP)
    assert ref.line(5, 100_000 * S, CAP) == CAP and pace.line(5, 100_000 * S, CAP) == T
    off = rp.RoomPaceLine(launch=False)
    off.observe(5, 0, 0.0)
    assert not off.launch_open(5, S) and off.line(5, 86_400 * S, CAP) == T
    assert off.line(5, 0, CAP) == rp.room_line(CAP, 0.0, 0, 0, window_ns=W)


def test_a_new_simulation_shifts_the_young_line_and_keeps_the_book_young():
    pace = rp.RoomPaceLine(launch=False)
    pace.observe(3, 20_000 * S, U0)
    pace.observe(3, 50_000 * S, U0 + 500.0)
    before = pace.line(3, 50_000 * S, CAP)
    pace.observe(3, 10_000 * S, U0 + 1_000.0)                                      # the clock went back: a rebase
    assert pace.rebases == 1 and pace.young(3)
    assert pace.line(3, 10_000 * S, CAP) == before                                  # the line kept its place
    assert pace.line(3, 10_000 * S, CAP) > rp.room_line(CAP, U0, 10_000 * S, 10_000 * S, window_ns=W)


# ---- 2. the agent -------------------------------------------------------------------------------------------------------

def _with20(fn):
    g = dict(fn.__globals__)
    g.update(EXTRA)
    f = types.FunctionType(fn.__code__, g, fn.__name__, fn.__defaults__, fn.__closure__)
    f.__kwdefaults__ = fn.__kwdefaults__
    return f


def _agent(*, used, inv=None, on=True, aged=False):
    agent = t619._agent(inv=inv, used=used, mature=False)              # v6.19 underneath; its mirror young (quiet)
    cls = type(agent)
    methods = {n: _with20(getattr(cls, n)) for n in ("_v63_pass", "_v633_book", "_v617_touch", "_v617_book")}
    base = dict(getattr(cls, "_v63_pass").__globals__)
    base.update(EXTRA)
    for n in METHODS:
        methods[n] = t619._method(n, base)
    agent.__class__ = type("P620", (cls,), methods)
    agent.research_v620_room_pace = on
    agent._v620_counts, agent._v620_errors, agent._v620_scale_sum = {}, 0, 0.0
    pace = rp.RoomPaceLine(launch=False) if on else RollingPaceLine()
    pace.observe(3, NOW - 120 * S, U0 + (1_000.0 if aged else 0.0))
    pace.observe(3, NOW - 60 * S, U0)                                    # aged: the volume fell once
    agent._v66_pace = pace
    return agent


def _line():
    return rp.room_line(CAP, U0, NOW - 120 * S, NOW, window_ns=W)


def _quantities(resp):
    return sorted((d, q) for (b, d, _cid, _px, q) in t63._placed(resp) if b == 3)


def test_behind_the_line_or_without_the_switch_the_book_is_v619_exactly():
    for used, inv in ((U0, None), (U0, 3.0), (_line() + BAND / 2, 3.0), (_line() + BAND / 2, None)):
        on, off = _agent(used=used, inv=inv), _agent(used=used, inv=inv, on=False)
        r_on, r_off = t617._run(on), t617._run(off)
        if used <= _line():
            assert t63._placed(r_on) == t63._placed(r_off), used
            assert on._v620_counts == {} and on._v620_errors == 0
        assert off._v620_counts == {} and off._v620_errors == 0
    assert _quantities(t617._run(_agent(used=U0))) == [("BUY", 2.0), ("SELL", 2.0)]


def test_ahead_of_the_line_every_order_of_a_young_book_is_smaller():
    agent = _agent(used=_line() + BAND / 2, inv=3.0)                       # long: the offer reduces, the bid is paced
    resp = t617._run(agent)
    assert _quantities(resp) == [("SELL", 1.0)]
    assert agent._v620_counts == {"scaled_book_states": 1} and agent._v620_errors == 0
    assert _quantities(t617._run(_agent(used=_line() + BAND / 2, inv=3.0, on=False))) == [("SELL", 2.0)]
    agent = _agent(used=_line() + 2 * BAND, inv=-3.0)                      # short: the bid reduces, at the minimum
    assert _quantities(t617._run(agent)) == [("BUY", 0.25)]
    assert agent._v620_counts == {"scaled_book_states": 1, "minimum_book_states": 1}


def test_the_touch_quote_is_scaled_too():
    agent = _agent(used=_line() + BAND / 2, inv=-3.0)                      # the fundamental is above: the touch buys
    t63.t14._rest(agent, 21, 3, 0, 99.80, cid=40031)                       # level 0 resting: the touch is not yielded
    resp = t617._run(agent)
    assert t617._touch(resp) and {q for (_d, _px, q) in t617._touch(resp)} == {1.0}, t617._touch(resp)
    off = _agent(used=_line() + BAND / 2, inv=-3.0, on=False)
    t63.t14._rest(off, 21, 3, 0, 99.80, cid=40031)
    assert {q for (_d, _px, q) in t617._touch(t617._run(off))} == {2.0}


def test_an_aged_book_keeps_full_size_orders():
    agent = _agent(used=_line() + BAND / 2, inv=3.0, aged=True)
    assert not agent._v66_pace.young(3)
    assert _quantities(t617._run(agent)) == [("SELL", 2.0)] and agent._v620_counts == {}


def test_a_failing_scale_leaves_the_size_whole_and_counts_an_error():
    agent = _agent(used=U0)

    class _Bad:
        def scale(self, *a, **k):
            raise RuntimeError("boom")

    assert agent._v620_scale(_Bad(), 3, NOW, U0, CAP, None) == 1.0 and agent._v620_errors == 1


def test_the_snapshot_reports_the_version_the_counts_and_the_mean_factor():
    agent = _agent(used=_line() + BAND / 2, inv=3.0)
    t617._run(agent)
    snap = agent._v620_snapshot()
    assert snap == {"version": "room_pace_v6_20", "counts": {"scaled_book_states": 1}, "errors": 0,
                    "mean_scale_when_scaled": 0.5, "room_line": 1}
    assert 'room_pace_on=int(bool(getattr(self, "research_v620_room_pace", False))),' in SIMPLE
    assert 'room_pace=(self._v620_snapshot() if getattr(self, "research_v620_room_pace", None) is not None else {}),' in SIMPLE


# ---- 3. wiring and the launcher -------------------------------------------------------------------------------------------

def test_the_switch_ships_on_and_agrees_with_the_agent_default():
    params = t617._params()
    assert "research_v620_room_pace=1" in params and "research_v620_room_pace=0" not in params
    assert SWITCH_LINE in SIMPLE
    for key in ("research_v641_volume_pace=1", "research_v66_pace_line=1", "research_v68_rolling_budget=1"):
        assert key in params, key


def test_the_room_line_replaces_the_rolling_line_and_the_size_reaches_every_layer():
    assert SIMPLE.count(LINE_LINE) == 1 and SIMPLE.count(SCALE_LINE) == 1
    assert SIMPLE.count("from research_v620_room_pace import (") == 1
    assert SIMPLE.count("size_scale=size_scale") == 5          # the deep layer x2, the touch quote x2 + its guard
    assert SIMPLE.count("v620_scaled_quantity(") == 2         # the deep layer's sizes and the touch quote's
    assert "bound_scale: float = 1.0, held: frozenset = frozenset(), size_scale: float = 1.0) -> int:" in SIMPLE
    assert SIMPLE.index(LINE_LINE) > SIMPLE.index("pace = V615LaunchPaceLine()")


def test_the_v620_block_is_gated_preflighted_and_listed():
    assert "; V6_18_BUILD=0; V6_19_BUILD=0; V6_20_BUILD=0; V6_21_BUILD=0; V6_22_BUILD=0; V6_23_BUILD=0; V6_24_BUILD=0; V6_25_BUILD=0; V6_26_BUILD=0; V6_27_BUILD=0; V6_28_BUILD=0; V6_29_BUILD=0; V6_30_BUILD=0\n" in LAUNCHER
    arm = next(line for line in LAUNCHER.splitlines() if line.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_18_BUILD=1; V6_19_BUILD=1; V6_20_BUILD=1; V6_21_BUILD=1; V6_22_BUILD=1; V6_23_BUILD=1; V6_24_BUILD=1; V6_25_BUILD=1; V6_26_BUILD=1; V6_27_BUILD=1; V6_28_BUILD=1; V6_29_BUILD=1; V6_30_BUILD=1"), arm
    assert 'if [[ "${V6_20_BUILD:-0}" == "1" ]]; then' in LAUNCHER
    assert "tests/test_research_v6_20.py" in LAUNCHER


def test_every_v620_preflight_pattern_is_in_the_file_it_checks():
    import re
    start = LAUNCHER.index('if [[ "${V6_20_BUILD:-0}" == "1" ]]; then')
    block = LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.20 room pace PASS', start)]
    pats = re.findall(r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\"", block)
    assert len(pats) == 4, pats
    for pat, name in pats:
        assert pat in (STRATEGY / name).read_text(), (pat, name)


def _run_block(params, agent_path=STRATEGY):
    start = LAUNCHER.index("# v6.20 S1.")
    stop = LAUNCHER.index('echo "[preflight] v6.20 room pace PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_20_BUILD": "1"}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_the_v620_block_passes_the_shipped_params_and_refuses_any_switch_it_needs_off():
    shipped = t617._shipped()
    out = _run_block(shipped)
    assert out.returncode == 0 and "[preflight] v6.20 room pace PASS" in out.stdout, out.stderr
    for key in ("research_v620_room_pace", "research_v641_volume_pace", "research_v66_pace_line",
                "research_v68_rolling_budget"):
        assert shipped.count(f"{key}=1") == 1, key
        for bad in (f"{key}=0", f"{key}=10", ""):
            out = _run_block(shipped.replace(f"{key}=1", bad))
            assert out.returncode == 1 and f"v6.20 S1 build without {key}=1" in out.stderr, (key, bad)


def test_the_v620_block_refuses_an_agent_without_the_import_the_line_the_size_or_the_module_line():
    cases = (("Strategy1_Research_Simple.py", "from research_v620_room_pace import (", "v6.20 S1 module is not imported"),
             ("Strategy1_Research_Simple.py", LINE_LINE, "v6.20 S1 the room line is not installed"),
             ("Strategy1_Research_Simple.py", SCALE_LINE, "v6.20 S1 the order sizes do not follow the line"),
             ("research_v620_room_pace.py",
              "def room_line(cap: Any, used0: Any, start_ns: int, now_ns: int, *, making_ns: int = MAKING_WINDOW_NS,",
              "v6.20 S1 research_v620_room_pace has no room line"))
    for name, needle, message in cases:
        tmp = Path(tempfile.mkdtemp(prefix="t620_"))
        try:
            for n in ("Strategy1_Research_Simple.py", "research_v620_room_pace.py"):
                shutil.copy(STRATEGY / n, tmp / n)
            text = (tmp / name).read_text()
            assert text.count(needle) == 1, needle
            (tmp / name).write_text(text.replace(needle, "# removed"))
            out = _run_block(t617._shipped(), agent_path=tmp)
            assert out.returncode == 1 and message in out.stderr, (name, out.stderr)
        finally:
            shutil.rmtree(tmp)
