"""v6.28: S8 the flat launch -- research_v628_flat_launch.

v6.27 + one switch (research_v628_flat_launch, default on).  v6.15 S1 launches a book first seen on an empty volume window
(a fresh UID) at LAUNCH_RATE = 2 x the sustainable rate for one making window with 2 x the deep clip, then pays it back
(v6.21 S3: evenly to the aim at its own roll-off).  S8: a book whose launch starts in this process (first seen on an
empty window) launches at rate 1 -- v6.15's launch line at rate 1 is v6.8's rolling line from zero, v6.21's even line
runs from one making window of the cap's sustainable share to the aim -- with the deep layer's clip.  Its rate rides in
its v6.21 pace-state row ("rate"), so a restart keeps it.  A launch restored without a rate -- every record saved before
v6.28 (UIDs 115 / 78 / 161 on 10-13: 128 books each, launch + even L, no rate) -- or begun before this process keeps
v6.15's 2.0: line, size factor, launch window and clip exactly as v6.27.  Off: v6.27.  Replay spec: k70 vm670 launch=1
lclip=1 from t_w (aim=2, bes=2, lpb=1).

No test here touches the host's caches, the network or a live tree: the agent is t627's harness (no fundamental page).
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
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v615_launch as lz  # noqa: E402
import research_v620_room_pace as rp  # noqa: E402
import research_v621_window_start as ws  # noqa: E402
import research_v621_young_window as yw  # noqa: E402
import research_v628_flat_launch as fl  # noqa: E402
from research_v66_pace_line import PaceLine  # noqa: E402
from research_v68_deep_owns import RollingPaceLine, rolling_line  # noqa: E402
import test_research_v6_17 as t617  # noqa: E402
import test_research_v6_19 as t619  # noqa: E402
import test_research_v6_21 as t621  # noqa: E402
import test_research_v6_22 as t622  # noqa: E402
import test_research_v6_23 as t623  # noqa: E402
import test_research_v6_24 as t624  # noqa: E402
import test_research_v6_25 as t625  # noqa: E402
import test_research_v6_26 as t626  # noqa: E402
import test_research_v6_27 as t627  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
MODULE = (STRATEGY / "research_v628_flat_launch.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000
NOW = t627.NOW
CAP = 500_000.0                  # the harness's cap (t619)
BAND = 62_500.0                  # the cap's making-window share: 500,000 x 10,800 / 86,400
P = 86_400 * S                   # the assessment period
W = 10_800 * S                   # the making window (v6.15's launch window)
PW = 600 * S                     # the validator's sampling window
FLOOR = t621.FLOOR
UID = t621.UID
SIM = t621.SIM
SWITCH_LINE = 'self.research_v628_flat_launch = self._as_bool(getattr(self.config, "research_v628_flat_launch", True))'
INSTALL_LINE = "pace = self._v6_28_line(pace)"
RESTORE_LINE = "self._v6_28_restore(pace, payload, how)"
SAVE_LINE = "self._v6_28_save(v621_state)"
CLIP_LINE = "clip_b = self._v6_28_clip(deep.clip, clip_b, pace, book_id, now_ts)"
REPORT_LINE = "self._v6_28_report()"
FIELDS = ("version", "flat_launch_on", "flat_line", "launch_flat", "launch_legacy", "open_flat", "open_legacy",
          "restored_flat", "restored_legacy", "restored_unrecorded", "restored_invalid", "flat_clip_book_states", "errors")
V628_NAMES = {"V628FlatLaunchLine": fl.FlatLaunchLine, "V628_FLAT_LAUNCH_VERSION": fl.V628_FLAT_LAUNCH_VERSION,
              "v628_launch_clip": fl.launch_clip, "v628_restore_rates": fl.restore_rates,
              "v628_save_rates": fl.save_rates}
EXTRA = dict(t627.EXTRA, **V628_NAMES)
METHODS = ("_v6_28_line", "_v6_28_restore", "_v6_28_save", "_v6_28_clip", "_v6_28_count", "_v6_28_error_total",
           "_v6_28_report", "_v6_28_snapshot")
L0_CIDS = {40031, 40032}         # book 3's deep level 0 (bid 99.80, offer 100.23)
GRID = (0, 1, PW, 3_000 * S, W - PW - 1, W - PW, W - 1, W, W + 1, 20_000 * S, 50_000 * S, P - PW - 1, P - PW, P,
        P + W)
USED = (0.0, 0.05 * CAP, 0.125 * CAP, 0.3 * CAP, 0.9 * CAP, CAP)


def _clip(line, book, now, clip=2.0):
    """The deep clip each build hands the layer: S8's on its line, v6.15's call (v6.27) on any other."""
    if isinstance(line, fl.FlatLaunchLine):
        return fl.launch_clip(clip, line, book, now)
    return lz.launch_clip(clip, line, book, now)


def _same(a, b, books, times, used=USED):
    """Two lines decide alike: line, size factor, pacing, launch window and the deep clip of every book at every time,
    and the state they leave (even spend, launching, aged)."""
    for t in times:
        for book in books:
            assert a.line(book, t, CAP) == b.line(book, t, CAP), (book, t)
            for u in used:
                assert a.scale(book, t, u, CAP) == b.scale(book, t, u, CAP), (book, t, u)
                assert a.paced(book, t, u, CAP, None) == b.paced(book, t, u, CAP, None), (book, t, u)
            assert a.launch_open(book, t) == b.launch_open(book, t), (book, t)
            assert a.young(book) == b.young(book), (book, t)
            assert _clip(a, book, t) == _clip(b, book, t), (book, t)
    assert a.even == b.even and a.launching == b.launching and a.aged == b.aged


def _lines(*, launch=True, **kw):
    """v6.27's line and S8's, with the same switches."""
    return yw.YoungWindowLine(launch=launch, **kw), fl.FlatLaunchLine(launch=launch, **kw)


def _launch(cls, t_w, *, books=(3,), used=3_000.0, now=NOW):
    """A line of ``cls`` in a process whose first state (``t_w``) read every book empty -- each book's even state
    decided in that state, as the pass decides it -- and whose books have traded ``used`` each by ``now``."""
    src = cls(launch=True)
    src.sim_id = SIM
    src.set_window(None, yw.WINDOW_OWN_EMPTY_START)
    for b in books:
        src.observe(b, t_w, 0.0, FLOOR)
        src.line(b, t_w, CAP)
    for b in books:
        src.observe(b, now - 60 * S, used, FLOOR)
    return src


def _saved(cls, t_w, *, books=(3,), used=3_000.0, now=NOW):
    """The pace state such a process (``_launch``) leaves in the session file, as JSON."""
    src = _launch(cls, t_w, books=books, used=used, now=now)
    state = yw.pace_state(src, uid=UID)
    if isinstance(src, fl.FlatLaunchLine):
        fl.save_rates(src, state)
    return json.loads(json.dumps(state))


def _restored(cls, payload, *, now=NOW):
    line = cls(launch=True)
    line.sim_id = SIM
    how = yw.restore_pace(line, payload, uid=UID, sim_id=SIM, now_ns=now)
    assert how == yw.RESTORE_RESTORED
    got = fl.restore_rates(line, payload, how) if isinstance(line, fl.FlatLaunchLine) else None
    return line, got


# ---- 1. the module ------------------------------------------------------------------------------------------------------

def test_the_module_exports_its_version_rates_and_rules_and_fits_no_number():
    """STRUCTURAL: the flat rate is the sustainable rate (1) and the legacy rate is v6.15's own constant, imported --
    the module states no other number."""
    assert fl.V628_FLAT_LAUNCH_VERSION == "flat_launch_v6_28"
    assert set(fl.__all__) == {"FLAT_LAUNCH_RATE", "FlatLaunchLine", "LEGACY_LAUNCH_RATE", "RATE_KEY",
                               "V628_FLAT_LAUNCH_VERSION", "launch_clip", "recorded_rate", "restore_rates", "save_rates"}
    assert fl.FLAT_LAUNCH_RATE == 1.0 and fl.LEGACY_LAUNCH_RATE is lz.LAUNCH_RATE and lz.LAUNCH_RATE == 2.0
    assert fl.RATE_KEY == "rate" and fl.EMPTY_WINDOW_QUOTE is lz.EMPTY_WINDOW_QUOTE
    assert "STRUCTURAL:" in MODULE and "OBSERVED:" in MODULE
    for name in ("FLAT_LAUNCH_RATE", "LEGACY_LAUNCH_RATE"):
        line = next(x for x in MODULE.splitlines() if x.startswith(f"{name} ="))
        assert "# STRUCTURAL:" in line, line
    tree = ast.parse(MODULE)
    assigned = {t.id for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)}
    assert assigned == {"V628_FLAT_LAUNCH_VERSION", "FLAT_LAUNCH_RATE", "LEGACY_LAUNCH_RATE", "RATE_KEY", "__all__"}
    numbers = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
               and not isinstance(n.value, bool)]
    assert sorted(x for x in numbers if isinstance(x, float)) == [0.0, 1.0], numbers   # FLAT_LAUNCH_RATE, v6.21's
    assert set(numbers) <= {0, 1}, numbers                          # fall-floor default; the tallies' 0 / +1


def test_a_recorded_rate_is_the_flat_rate_or_v615s_and_nothing_else():
    assert fl.recorded_rate(1.0) == 1.0 and fl.recorded_rate(2.0) == 2.0
    assert fl.recorded_rate(1) == 1.0 and fl.recorded_rate("1.0") == 1.0 and fl.recorded_rate("2") == 2.0
    for bad in (None, True, False, 1.5, 0.0, -1.0, 3.0, 2.0000001, 0.9999999, float("nan"), float("inf"), "x", [1.0],
                {}, ()):
        assert fl.recorded_rate(bad) is None, bad


# ---- 2. v6.15's arithmetic at rate 1 --------------------------------------------------------------------------------------

def test_at_rate_1_the_payback_rate_is_1_and_the_launch_line_is_v68s_rolling_line_from_zero():
    """payback_rate = (A - rate x M) / (A - M): at rate 1 it is 1 (A > M -- no division by zero; A <= M is guarded),
    and v6.15's launch line from zero is v6.8's rolling line from zero, bit for bit, for any cap, before and after its
    launch window and past the assessment period."""
    assert lz.payback_rate(rate=1.0) == 1.0
    assert lz.payback_rate(rate=2.0) == pytest.approx(64_800 / 75_600, rel=1e-12)          # v6.15's 0.857
    assert lz.payback_rate(rate=1.0, making_ns=P, assessment_ns=P) == 1.0                   # degenerate windows
    assert lz.payback_rate(rate=1.0, making_ns=2 * P, assessment_ns=P) == 1.0
    t0 = 40_000 * S
    for cap in (CAP, 1.0, 0.0, -5.0, float("nan"), float("inf"), None, "x", 1e12):
        top = max(0.0, cap) if isinstance(cap, float) and math.isfinite(cap) else 0.0
        for dt in (-2 * PW, -PW, -PW + 1, 0, 1) + GRID[2:] + (2 * P,):
            got = lz.launch_line(cap, t0, t0 + dt, rate=1.0)
            assert got == rolling_line(cap, 0.0, t0, t0 + dt), (cap, dt)
            assert math.isfinite(got) and 0.0 <= got <= top, (cap, dt)


def test_at_rate_1_v615s_line_window_and_clip_are_the_plain_line_the_same_window_and_the_layers_clip():
    t0 = 40_000 * S
    flat, plain = lz.LaunchPaceLine(rate=1.0), RollingPaceLine()
    for line in (flat, plain):
        line.observe(3, t0, 0.0)
    assert 3 in flat.launching
    for dt in GRID:
        assert flat.line(3, t0 + dt, CAP) == plain.line(3, t0 + dt, CAP), dt
    assert flat.launch_open(3, t0) and flat.launch_open(3, t0 + W - 1) and not flat.launch_open(3, t0 + W)
    for clip, want in ((2.0, 2.0), (0.25, 0.25), (0.0, 0.0), (-1.0, 0.0), (float("nan"), 0.0), (None, 0.0)):
        assert lz.launch_clip(clip, flat, 3, t0, rate=1.0) == want, clip
        assert lz.launch_clip(clip, flat, 3, t0 + W, rate=1.0) == want, clip
    assert lz.launch_clip(2.0, flat, 3, t0) == 4.0                  # v6.15's call: its module rate, not the line's
    room = rp.RoomPaceLine(launch=True, rate=1.0)                   # v6.20: the rolling line, never above T
    room.observe(3, t0, 0.0)
    for dt in GRID:
        assert room.line(3, t0 + dt, CAP) == min(rolling_line(CAP, 0.0, t0, t0 + dt), rp.threshold(CAP)), dt


def test_at_rate_1_v621s_even_launch_line_rises_at_1x_then_pays_back_evenly_to_its_aim():
    """One making window at the sustainable rate (0.125 x cap at its end), then 0.875x to the aim 0.890625 x cap at
    the roll-off (its band 0.109375 x cap: aim + band = cap), flat after: finite, never negative, never falling, never
    above the cap, continuous where the payback starts, and below v6.27's 2x line at every time."""
    t_w = 37_320 * S
    roll = yw.earliest_rolloff(t_w)
    prev = -1.0
    for dt in range(-PW, P + 2 * W, 300 * S):
        lv, band = yw.launch_even_line(CAP, t_w, t_w + dt, rate=1.0)
        assert math.isfinite(lv) and math.isfinite(band) and 0.0 <= lv <= CAP and band > 0.0, dt
        assert lv >= prev - 1e-9, dt
        assert lv <= yw.launch_even_line(CAP, t_w, t_w + dt)[0] + 1e-9, dt                # v6.27: rate 2
        prev = lv
    for dt in (0, 3_000 * S, W - PW):
        lv, band = yw.launch_even_line(CAP, t_w, t_w + dt, rate=1.0)
        assert lv == pytest.approx(lz.launch_line(CAP, t_w, t_w + dt, rate=1.0), abs=1e-6) and band == BAND, dt
    start = t_w + W - PW                                            # x = one making window
    assert yw.launch_even_line(CAP, t_w, start, rate=1.0)[0] == pytest.approx(0.125 * CAP)
    assert yw.launch_even_line(CAP, t_w, start + 1, rate=1.0)[0] == pytest.approx(0.125 * CAP, abs=1e-3)
    lv, band = yw.launch_even_line(CAP, t_w, roll, rate=1.0)
    assert lv == pytest.approx(0.890625 * CAP) and band == pytest.approx(0.109375 * CAP) and lv + band == pytest.approx(CAP)
    assert yw.launch_even_line(CAP, t_w, roll + P, rate=1.0) == (lv, band)
    assert (lv - 0.125 * CAP) / ((roll - start) / S) / (CAP / 86_400) == pytest.approx(0.875)
    old = yw.launch_even_line(CAP, t_w, roll)                       # v6.27: 0.25 -> 0.90625 at 0.75x
    assert old[0] == pytest.approx(0.90625 * CAP) and lv < old[0]
    for cap in (0.0, -1.0, None, "x"):
        got = yw.launch_even_line(cap, t_w, roll, rate=1.0)
        assert got[0] == 0.0 and all(math.isfinite(v) and v >= 0.0 for v in got), cap


def _replay_es_launch(ts, t_w, cap, k):
    """k70 vm670's _es_launch / _es_aim (lpb=1, aim=2, bes=2): the launch line from an empty window at t_w."""
    es_w, es_p, es_pw = 10800e9, 86400e9, 600e9
    bm = cap * es_w / es_p
    x = float(ts - t_w) + es_pw
    lw = cap * k * es_w / es_p
    if x <= es_w:
        return cap * k * max(0.0, x) / es_p, bm
    h = max(1.0, es_p - es_w)
    s = (cap - lw) / (h + es_w)
    if s > cap / es_p:
        a = cap - bm
        s = max(0.0, (a - lw) / h)
        b = bm
    else:
        s = max(0.0, s)
        b = s * es_w
        a = cap - b
    if a <= lw:
        a = lw
    return lw + (a - lw) * min(1.0, (x - es_w) / (es_p - es_w)), max(b, 1e-6 * cap)


def test_the_rule_is_the_replays_launch_1_and_clip_1():
    """k70's LAUNCH 1x arm (launch=1, lclip=1 from t_w) against S8's line for a flat book; its 2x arm against v6.27's."""
    t_w = 40_000 * S
    ref, line = _lines()
    for one in (ref, line):
        one.observe(3, t_w, 0.0, FLOOR)
    for dt in GRID + (2_000 * S, 70_000 * S):
        now = t_w + dt
        for k, one in ((1.0, line), (2.0, ref)):
            want, band = _replay_es_launch(now, t_w, CAP, k)
            assert one.line(3, now, CAP) == pytest.approx(want, rel=1e-12, abs=1e-6), (k, dt)
            assert yw.launch_even_line(CAP, t_w, now, rate=k)[1] == pytest.approx(band, rel=1e-12), (k, dt)
        lopen = dt < W                                              # vm636: (ts - TWB) < lwin, then clip x lclip
        assert _clip(line, 3, now) == 2.0 * 1.0 and _clip(ref, 3, now) == 2.0 * (2.0 if lopen else 1.0), dt


# ---- 3. the line ---------------------------------------------------------------------------------------------------------

def test_a_book_first_seen_empty_launches_flat():
    t_w = 40_000 * S
    ref, line = _lines()
    for one in (ref, line):
        one.observe(3, t_w, 0.0, FLOOR)
    assert line.launch_rates == {3: 1.0} and line.rate_of(3) == 1.0 and line.rate_of(5) == 2.0
    assert 3 in line.launching and line.window_start_ns(3) == t_w and (line.rate, line.legacy_rate) == (2.0, 2.0)
    for dt in GRID:
        now = t_w + dt
        lv, band = yw.launch_even_line(CAP, t_w, now, rate=1.0)
        assert line.line(3, now, CAP) == lv and line.rate == 2.0, dt                # the line's own rate is back
        assert lv <= ref.line(3, now, CAP), dt
        for used in USED:
            assert line.scale(3, now, used, CAP) == yw.band_scale(used, lv, band), (dt, used)
            assert line.paced(3, now, used, CAP, None) == (used > lv + 1e-9), (dt, used)
        assert line.launch_open(3, now) == ref.launch_open(3, now) == (dt < W), dt  # one making window, as v6.15
        assert _clip(line, 3, now) == 2.0 and _clip(ref, 3, now) == (4.0 if dt < W else 2.0), dt
        assert fl.launch_clip(2.0, ref, 3, now) == _clip(ref, 3, now), dt         # on a line without rates: v6.15's
        assert line.rate == 2.0
    assert line.even == ref.even == {3: (yw.EVEN_LAUNCH,)}


def test_a_launch_restored_without_a_rate_keeps_v615s_rate_exactly_as_v627():
    """A record saved by v6.27 (no rate): line, size factor, pacing, launch window and clip are v6.27's at every time
    -- inside the open launch window, at its end, through the payback and after the roll-off."""
    t_w = NOW - 3_000 * S
    saved = _saved(yw.YoungWindowLine, t_w, books=(3, 5, 7))
    assert all("rate" not in row and row["launch"] == 1 and row["even"] == ["L"] for row in saved["books"].values())
    ref, _none = _restored(yw.YoungWindowLine, saved)
    line, got = _restored(fl.FlatLaunchLine, saved)
    assert got == {"flat": 0, "legacy": 0, "unrecorded": 3, "invalid": 0} and line.launch_rates == {}
    roll = yw.earliest_rolloff(t_w)
    times = (NOW, NOW + 3_000 * S, t_w + W - 1, t_w + W, t_w + W + PW, roll - 1, roll, roll + P)
    _same(ref, line, (3, 5, 7), times)
    assert _clip(line, 3, NOW) == 4.0 and _clip(line, 3, t_w + W) == 2.0
    assert line.rates_summary() == {"flat": 0, "legacy": 3, "open_flat": 0, "open_legacy": 3}


def test_the_three_running_uids_records_keep_v615s_launch():
    """The live shape (session files of UIDs 115 / 78 / 161, 10-13 12:48 JST): 128 books, every row launch + even L + its
    own window start, no rate -- 78 / 161 inside their launch window, 115 in its payback.  Restored, S8's line is v6.27's
    for every book."""
    for since in (3_000, 30_000):                                   # 78 / 161: open; 115: paying back
        t_w = NOW - since * S
        saved = _saved(yw.YoungWindowLine, t_w, books=tuple(range(128)), used=0.02 * CAP)
        assert all(set(row) == {"t0", "u0", "last", "launch", "tw", "ts", "even"} and row["even"] == ["L"]
                   for row in saved["books"].values()) and len(saved["books"]) == 128
        ref, _none = _restored(yw.YoungWindowLine, saved)
        line, got = _restored(fl.FlatLaunchLine, saved)
        assert got["unrecorded"] == 128 and line.launch_rates == {}
        roll = yw.earliest_rolloff(t_w)
        _same(ref, line, (0, 1, 64, 127), (NOW, t_w + W - 1, t_w + W, roll), used=(0.0, 0.02 * CAP, 0.5 * CAP))
        open_now = since * S < W
        assert line.rates_summary() == {"flat": 0, "legacy": 128, "open_flat": 0, "open_legacy": 128 if open_now else 0}


def test_a_v628_record_brings_its_flat_rate_back_and_a_restart_continues_identically():
    t_w = NOW - 3_000 * S
    saved = _saved(fl.FlatLaunchLine, t_w, books=(3, 5))
    assert all(row["rate"] == 1.0 for row in saved["books"].values())
    cont = _launch(fl.FlatLaunchLine, t_w, books=(3, 5))            # the process that never restarted
    line, got = _restored(fl.FlatLaunchLine, saved)
    assert got == {"flat": 2, "legacy": 0, "unrecorded": 0, "invalid": 0} and line.launch_rates == {3: 1.0, 5: 1.0}
    roll = yw.earliest_rolloff(t_w)
    _same(cont, line, (3, 5), (NOW, t_w + W - 1, t_w + W, roll, roll + P))
    assert _clip(line, 3, NOW) == 2.0
    old, _none = _restored(yw.YoungWindowLine, saved)               # a v6.27 process reads the row without its rate
    assert old.line(3, NOW, CAP) > line.line(3, NOW, CAP) and _clip(old, 3, NOW) == 4.0


def test_a_book_first_seen_with_volume_is_v627s():
    """No launch of this process: a book first seen with volume, with the UID's window start known (its launch line
    rebuilt from t_w: v6.15's rate), far above it (anchored), without a window start (v6.20's room line) or aged."""
    t_w = NOW - 6_000 * S
    lv, band = yw.launch_even_line(CAP, t_w, NOW)
    for start in (t_w, None):
        ref, line = _lines()
        for one in (ref, line):
            if start is not None:
                one.set_window(start, ws.KNOWN)
            one.observe(3, NOW, lv + band - 1.0, FLOOR)            # near its 2x launch line
            one.observe(5, NOW, lv + band + 1_000.0, FLOOR)        # beyond it
            one.observe(7, NOW, 0.3 * CAP, FLOOR)
        assert line.launch_rates == {} and line.rate_of(3) == 2.0
        _same(ref, line, (3, 5, 7), (NOW, NOW + 600 * S, t_w + W - 1, t_w + W, t_w + P))
        if start is not None:
            assert line.even[3] == (yw.EVEN_LAUNCH,) and _clip(line, 3, NOW) == 4.0
            assert line.launch_books() == [3] and line.rates_summary() == {"flat": 0, "legacy": 1, "open_flat": 0,
                                                                            "open_legacy": 1}
        else:
            assert line.launch_books() == []
    ref, line = _lines()
    for one in (ref, line):                                          # aged: a whole fill fell past its roll-off
        one.observe(3, NOW - 120 * S, 300_000.0, FLOOR)
        one.observe(3, NOW - 60 * S, 299_000.0, FLOOR)
    assert not line.young(3) and line.launch_rates == {}
    _same(ref, line, (3,), (NOW, NOW + W))


def test_v620s_line_without_s3_v615_off_and_the_aged_line_run_at_the_books_rate():
    t0 = 40_000 * S
    line = fl.FlatLaunchLine(launch=True, even=False)               # without S3: v6.20's launch line at rate 1, under T
    line.observe(3, t0, 0.0, FLOOR)
    for dt in GRID:
        assert line.line(3, t0 + dt, CAP) == min(rolling_line(CAP, 0.0, t0, t0 + dt), rp.threshold(CAP)), dt
    ref, off = _lines(launch=False)                                  # v6.15 off: v6.21 already runs its even line at 1
    for one in (ref, off):
        one.observe(3, t0, 0.0, FLOOR)
    _same(ref, off, (3,), tuple(t0 + dt for dt in GRID))
    assert off.launching == set() and _clip(off, 3, t0) == 2.0
    aged = fl.FlatLaunchLine(launch=True, guard=False)              # v6.20's fall test: aged at the first fall
    aged.observe(3, t0, 0.0, FLOOR)
    aged.observe(3, t0 + PW, 1_000.0, FLOOR)
    aged.observe(3, t0 + 2 * PW, 500.0, FLOOR)
    assert 3 in aged.aged and 3 in aged.launching
    for dt in GRID:                                                  # v6.15's launch line at the book's rate 1
        assert aged.line(3, t0 + dt, CAP) == lz.launch_line(CAP, t0, t0 + dt, rate=1.0), dt


def test_a_new_simulation_keeps_each_books_rate():
    ref, line = _lines()
    for one in (ref, line):
        one.observe(3, 80_000 * S, 0.0, FLOOR)
        one.observe(5, 80_000 * S, 300_000.0, FLOOR)
        one.line(3, 80_000 * S, CAP)
        one.observe(3, 85_000 * S, 20_000.0, FLOOR)
        one.observe(3, 100 * S, 20_000.0, FLOOR)                     # the next simulation: re-keyed
    assert line.rekeys == ref.rekeys == 1 and line.launch_rates == {3: 1.0}
    t_w = line.window_start_ns(3)
    assert t_w == ref.window_start_ns(3) == (80_000 - 85_000) * S
    for now in (100 * S, 3_000 * S, W, P):
        assert line.line(3, now, CAP) == yw.launch_even_line(CAP, t_w, now, rate=1.0)[0], now
        assert ref.line(3, now, CAP) == yw.launch_even_line(CAP, t_w, now)[0], now
    assert line.launch_open(3, 100 * S) and _clip(line, 3, 100 * S) == 2.0 and _clip(ref, 3, 100 * S) == 4.0


def test_the_lines_rate_is_back_after_every_call_even_one_that_raises():
    ref, line = _lines()
    for one in (ref, line):
        one.observe(3, NOW, 0.0, FLOOR)
        one.window_start[3] = "broken"
        with pytest.raises(Exception):
            one.line(3, NOW, CAP)                                     # v6.27 raises the same: the caller counts it
        with pytest.raises(Exception):
            one.scale(3, NOW, 0.0, CAP)
    assert line.rate == line.legacy_rate == 2.0 and line.errors == 0


def test_a_broken_rate_record_falls_back_to_v615s_rate_and_counts():
    ref, line = _lines()
    for one in (ref, line):
        one.observe(3, NOW, 0.0, FLOOR)
    line.launch_rates = "broken"                                     # no record readable: v6.15's rate, as v6.27
    assert line.rate_of(3) == 2.0 and line.errors == 1
    _same(ref, line, (3,), (NOW, NOW + W))
    line2 = fl.FlatLaunchLine(launch=True)
    line2.launch_rates = None
    line2.observe(7, NOW, 0.0, FLOOR)                                # its launch cannot be recorded: v6.15's rate
    assert line2.errors == 1 and 7 in line2.start and 7 in line2.launching and line2.window_start_ns(7) == NOW
    assert line2.rate_of(7) == 2.0 and line2.errors == 2


def test_the_rates_summary_counts_launches_by_rate_and_window():
    line = fl.FlatLaunchLine(launch=True)
    line.sim_id = SIM
    saved = _saved(yw.YoungWindowLine, NOW - 3_000 * S, books=(1, 2))
    yw.restore_pace(line, saved, uid=UID, sim_id=SIM, now_ns=NOW)
    line.observe(3, NOW, 0.0, FLOOR)                                 # this process's launch
    line.observe(4, NOW, 300_000.0, FLOOR)                           # no launch
    line.line(3, NOW, CAP)
    line.line(4, NOW, CAP)
    assert line.launch_books() == [1, 2, 3]
    assert line.rates_summary() == {"flat": 1, "legacy": 2, "open_flat": 1, "open_legacy": 2}
    line.observe(3, NOW + W, 10.0, FLOOR)                            # the clock past every launch window
    assert line.rates_summary() == {"flat": 1, "legacy": 2, "open_flat": 0, "open_legacy": 0}
    snap = line.snapshot()                                           # V62_STATE's launch row carries it (v6.15's line)
    assert snap["launch"]["flat_launch"] == {"flat": 1, "legacy": 2, "open_flat": 0, "open_legacy": 0,
                                             "version": fl.V628_FLAT_LAUNCH_VERSION, "flat_rate": 1.0,
                                             "legacy_rate": 2.0, "errors": 0}
    assert (snap["launch"]["rate"], snap["launch"]["books"]) == (2.0, 3)                # v6.15's fields as before
    assert snap["young"]["even_launch"] == 3 and "flat_launch" not in snap


# ---- 4. the record -------------------------------------------------------------------------------------------------------

def test_the_rate_rides_in_its_books_row_and_nothing_else_changes():
    line = fl.FlatLaunchLine(launch=True)
    line.observe(3, NOW, 0.0, FLOOR)
    line.observe(5, NOW, 300_000.0, FLOOR)
    plain = yw.pace_state(line, uid=UID)
    state = yw.pace_state(line, uid=UID)
    assert fl.save_rates(line, state) == 1
    assert state["books"]["3"]["rate"] == 1.0 and "rate" not in state["books"]["5"]
    assert {k: v for k, v in state["books"]["3"].items() if k != "rate"} == plain["books"]["3"]
    assert state["books"]["5"] == plain["books"]["5"] and {k: v for k, v in state.items() if k != "books"} == \
        {k: v for k, v in plain.items() if k != "books"}
    for other in (yw.YoungWindowLine(), None, object()):
        assert fl.save_rates(other, yw.pace_state(line, uid=UID)) == 0
    for bad in (None, {}, {"books": []}, "x"):
        assert fl.save_rates(line, bad) == 0
    line.launch_rates[9] = 1.0                                       # a record of a book without a row: no row invented
    line.launch_rates[5] = 1.5                                       # a rate this build cannot run: not written
    state = yw.pace_state(line, uid=UID)
    assert fl.save_rates(line, state) == 1 and set(state["books"]) == {"3", "5"} and "rate" not in state["books"]["5"]


def test_restore_reads_each_restored_rows_rate_and_nothing_v621_did_not_restore():
    saved = _saved(yw.YoungWindowLine, NOW - 3_000 * S, books=(1, 2, 3, 4, 5, 6, 7, 8))
    rows = saved["books"]
    rows["1"]["rate"], rows["2"]["rate"], rows["3"]["rate"] = 1.0, 2.0, 1.5
    rows["4"]["rate"], rows["5"]["rate"] = "x", True
    rows["8"]["rate"], rows["8"]["t0"] = 1.0, "bad"                 # a row v6.21 skips: its rate is not taken either
    line = fl.FlatLaunchLine(launch=True)
    line.sim_id = SIM
    how = yw.restore_pace(line, saved, uid=UID, sim_id=SIM, now_ns=NOW)
    assert how == yw.RESTORE_RESTORED and 8 not in line.start
    got = fl.restore_rates(line, saved, how)
    assert got == {"flat": 1, "legacy": 1, "unrecorded": 2, "invalid": 3}
    assert line.launch_rates == {1: 1.0, 2: 2.0}
    assert [line.rate_of(b) for b in range(1, 9)] == [1.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0]
    for how in (yw.RESTORE_NONE, yw.RESTORE_LIVE, yw.RESTORE_OTHER_SIM, yw.RESTORE_EMPTY_WINDOW, yw.RESTORE_INVALID,
                yw.RESTORE_FOREIGN, yw.RESTORE_VERSION, None):
        fresh = fl.FlatLaunchLine(launch=True)
        fresh.start[1] = (NOW, 0.0)
        assert fl.restore_rates(fresh, saved, how) == {"flat": 0, "legacy": 0, "unrecorded": 0, "invalid": 0}, how
        assert fresh.launch_rates == {}
    assert fl.restore_rates(line, None, yw.RESTORE_RESTORED)["flat"] == 0
    assert fl.restore_rates(yw.YoungWindowLine(), saved, yw.RESTORE_RESTORED)["flat"] == 0   # no rates on v6.21's line


def test_a_record_saved_across_a_seam_is_rekeyed_with_its_rate():
    t_w = 80_000 * S
    src = fl.FlatLaunchLine(launch=True)
    src.sim_id = "20261012_0000"
    src.observe(3, t_w, 0.0, FLOOR)
    src.observe(3, t_w + 600 * S, 5_000.0, FLOOR)
    saved = yw.pace_state(src, uid=UID)
    fl.save_rates(src, saved)
    saved = json.loads(json.dumps(saved))
    line = fl.FlatLaunchLine(launch=True)
    line.sim_id = SIM
    how = yw.restore_pace(line, saved, uid=UID, sim_id=SIM, now_ns=100 * S, duration_ns=86_400 * S)
    assert how == yw.RESTORE_REKEYED and fl.restore_rates(line, saved, how)["flat"] == 1 and line.rate_of(3) == 1.0


# ---- 5. the agent: t627's harness, v6.15's launch on, no fundamental page --------------------------------------------------

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
        _CLASSES[key] = type("P628", (base,), methods)
    return _CLASSES[key]


def _agent(*, on=True, names=None, pace=None, used=0.0, **kw):
    """t627's harness agent (v6.27: S5 on, no fundamental page -- no lean) with v6.15's launch on, S8's line (v6.27's
    with the switch off) unless ``pace`` says otherwise, and v6.28's methods; the state as _init_build_switches leaves
    it."""
    if pace is None:
        pace = fl.FlatLaunchLine(launch=True) if on else yw.YoungWindowLine(launch=True)
    agent = t627._agent(page=None, used=used, pace=pace, **kw)
    agent.__class__ = _klass(type(agent), names)
    agent.research_v615_launch = True
    agent.research_v628_flat_launch = on
    agent._v6_28_counts, agent._v6_28_errors, agent._v6_28_errors_seen = {}, 0, 0
    agent._v6_28_reported, agent._v6_28_erring = False, False
    return agent


def _ref(*, pace=None, used=0.0, **kw):
    """v6.27's harness agent -- no v6.28 attribute at all -- with v6.15's launch on and v6.27's line."""
    agent = t627._agent(page=None, used=used, pace=pace if pace is not None else yw.YoungWindowLine(launch=True), **kw)
    agent.research_v615_launch = True
    return agent


def _request(agent, *, ts=NOW, wall=1000.0, version=62):
    return t627._request(agent, ts=ts, wall=wall, version=version)


_out = t622._out


def _l0(out):
    """Book 3's deep level-0 quantities in one request's output."""
    return sorted(q for (b, _side, cid, _px, q) in out[0] if b == 3 and cid in L0_CIDS)


def _rows(agent):
    return [kw for event, kw in agent.emitted if event == "V628_STATE"]


def _counts(agent):
    return dict(agent._v6_28_counts)


def test_a_new_uids_book_quotes_the_layers_clip_on_its_flat_line():
    """A fresh UID (its first state reads every book empty): S8 quotes level 0 at the deep layer's clip, v6.27 at twice
    it, for one making window; from its end both requests are the same (the harness's layer, without prints for a
    making window, hands the book back to v6.3.2 there).  S8's line is the flat one."""
    on, ref = _agent(), _ref()
    for i, dt in enumerate((0, 600 * S, W - PW, W, W + 600 * S)):
        for a in (on, ref):
            a.reading["used"] = 1_000.0 * i
        got, want = _out(_request(on, ts=NOW + dt, wall=1000.0 + i)), _out(_request(ref, ts=NOW + dt, wall=1000.0 + i))
        if dt < W:
            assert _l0(got) == [2.0, 2.0] and _l0(want) == [4.0, 4.0], dt
            assert [p for p in got[0] if p[2] not in L0_CIDS] == [p for p in want[0] if p[2] not in L0_CIDS], dt
        else:
            assert got == want, dt
    pace = on._v66_pace
    assert pace.launch_rates == {3: 1.0} and pace.window_status == yw.WINDOW_OWN_EMPTY_START
    assert pace.line(3, NOW, CAP) == yw.launch_even_line(CAP, NOW, NOW, rate=1.0)[0]
    assert _counts(on) == {"flat_clip_book_states": 3} and on._v6_28_errors == 0
    assert "launch_clip_book_states" not in on._v615_counts and ref._v615_counts == {"launch_clip_book_states": 3}
    assert on._v621_counts == ref._v621_counts == {"restore_none": 1, "window_own_empty_start": 1}


def test_a_launch_restored_without_a_rate_trades_exactly_as_v627():
    """UIDs 78 / 161 restarted onto v6.28 inside their 2x launch window: what reaches the venue and the state left
    behind are v6.27's request after request -- twice the clip until t_w + one making window, then the clip."""
    t_w = NOW - 3_000 * S
    saved = _saved(yw.YoungWindowLine, t_w)
    on, ref = _agent(used=3_000.0), _ref(used=3_000.0)
    for a in (on, ref):
        a._v621_restore_payload = json.loads(json.dumps(saved))
    for i, ts in enumerate((NOW, NOW + 600 * S, t_w + W - 1, t_w + W, t_w + W + 600 * S)):
        for a in (on, ref):
            a.reading["used"] = 3_000.0 + 500.0 * i
        got, want = _out(_request(on, ts=ts, wall=1000.0 + i)), _out(_request(ref, ts=ts, wall=1000.0 + i))
        assert got == want and _l0(got) == ([4.0, 4.0] if ts - t_w < W else [2.0, 2.0]), i
        assert t622._fingerprint(on) == t622._fingerprint(ref), i
    assert on._v621_counts == ref._v621_counts == {"restore_restored": 1}
    assert _counts(on) == {"restored_unrecorded": 1} and on._v6_28_errors == 0 and on._v66_pace.launch_rates == {}
    assert on._v615_counts == ref._v615_counts


def test_a_restored_v628_record_keeps_its_flat_launch():
    t_w = NOW - 3_000 * S
    saved = _saved(fl.FlatLaunchLine, t_w)
    assert saved["books"]["3"]["rate"] == 1.0
    on, ref = _agent(used=3_000.0), _ref(used=3_000.0)
    for a in (on, ref):
        a._v621_restore_payload = json.loads(json.dumps(saved))
    got, want = _out(_request(on)), _out(_request(ref))
    assert _l0(got) == [2.0, 2.0] and _counts(on) == {"restored_flat": 1, "flat_clip_book_states": 1}
    assert _l0(want) == [4.0, 4.0]                                   # a v6.27 process reads the row without its rate
    assert on._v66_pace.launch_rates == {3: 1.0} and on._v621_counts == {"restore_restored": 1}


def test_a_book_first_seen_with_volume_trades_as_v627():
    """Restarted without a saved state: the UID's window start known (its launch line rebuilt from t_w, v6.15's rate)
    or unknown (no logs: v6.20's room line) -- S8 starts no launch and changes nothing."""
    t_w = NOW - 6_000 * S
    for known in (True, False):
        on, ref = _agent(used=3_000.0), _ref(used=3_000.0)
        if known:
            for a in (on, ref):
                a._v66_pace.set_window(t_w, ws.KNOWN)
        for i, ts in enumerate((NOW, NOW + 600 * S, t_w + W)):
            for a in (on, ref):
                a.reading["used"] = 3_000.0 + 100.0 * i
            got, want = _out(_request(on, ts=ts, wall=1000.0 + i)), _out(_request(ref, ts=ts, wall=1000.0 + i))
            assert got == want, (known, i)
            assert t622._fingerprint(on) == t622._fingerprint(ref), (known, i)
            if known and i == 0:
                assert _l0(got) == [4.0, 4.0] and on._v66_pace.even[3] == (yw.EVEN_LAUNCH,)
        assert on._v66_pace.launch_rates == {} and _counts(on) == {} and on._v6_28_errors == 0, known


def test_off_the_pass_is_v627s_exactly_and_s8_never_runs():
    """Switch off (v6.27's line, as _v66_pace_ref builds it then): what reaches the venue, the counters and the state left
    behind are v6.27's harness agent's, for a fresh UID, a restored v6.27 or v6.28 record and a book seen with volume;
    S8 restores, records and counts nothing.  So is a switch-on agent whose line is v6.27's (the install failed)."""
    t_w = NOW - 3_000 * S
    cases = (("fresh", 0.0, None), ("v6.27 record", 3_000.0, _saved(yw.YoungWindowLine, t_w)),
             ("v6.28 record", 3_000.0, _saved(fl.FlatLaunchLine, t_w)), ("seen with volume", 0.3 * CAP, None))
    for name, used, payload in cases:
        for on in (False, True):
            agent = _agent(on=on, used=used, pace=yw.YoungWindowLine(launch=True))
            ref = _ref(used=used)
            for a in (agent, ref):
                a._v621_restore_payload = None if payload is None else json.loads(json.dumps(payload))
            for i in range(3):
                ts = NOW + i * 600 * S
                assert _out(_request(agent, ts=ts, wall=1000.0 + i)) == _out(_request(ref, ts=ts, wall=1000.0 + i)), \
                    (name, on, i)
                assert t622._fingerprint(agent) == t622._fingerprint(ref), (name, on, i)
            assert agent._v615_counts == ref._v615_counts and agent._v621_counts == ref._v621_counts, (name, on)
            assert agent._v633_counts == ref._v633_counts, (name, on)
            assert _counts(agent) == {} and agent._v6_28_errors == 0, (name, on)


def _install(**kw):
    src = {"V68RollingPaceLine": RollingPaceLine, "V66PaceLine": PaceLine, "V615LaunchPaceLine": lz.LaunchPaceLine,
           "V620RoomPaceLine": rp.RoomPaceLine}
    names = dict(EXTRA, **kw)
    ref_fn = t619._method("_v66_pace_ref", dict(src, **names))
    line_fn = t619._method("_v6_28_line", dict(src, **names))
    return ref_fn, line_fn


def test_the_pace_line_becomes_s8s_only_with_the_switch_and_v621s_line():
    ref_fn, line_fn = _install()
    for v621, flat in (((True, True, True), True), ((True, True, True), False), ((True, False, False), True),
                       ((False, True, False), True), ((False, False, True), True), ((False, False, False), True)):
        o = types.SimpleNamespace(_v66_pace=None, research_v68_rolling_budget=True, research_v615_launch=True,
                                  research_v620_room_pace=True, research_v628_flat_launch=flat, _v6_28_errors=0,
                                  **dict(zip(t621.SWITCHES, v621)))
        o._v6_28_line = types.MethodType(line_fn, o)
        pace = types.MethodType(ref_fn, o)()
        want = (fl.FlatLaunchLine if flat else yw.YoungWindowLine) if any(v621) else rp.RoomPaceLine
        assert type(pace) is want and o._v6_28_errors == 0, (v621, flat)
        if want is fl.FlatLaunchLine:
            assert (pace.launch_on, pace.guard_on, pace.even_on) == (True, v621[0], v621[2])
            assert (pace.rate, pace.legacy_rate, pace.flat_rate) == (2.0, 2.0, 1.0)
        assert types.MethodType(ref_fn, o)() is pace                                   # built once
    o = types.SimpleNamespace(_v66_pace=None, research_v68_rolling_budget=True, research_v615_launch=False,
                              research_v620_room_pace=True, research_v628_flat_launch=True, _v6_28_errors=0,
                              **dict(zip(t621.SWITCHES, (True, True, True))))
    o._v6_28_line = types.MethodType(line_fn, o)
    pace = types.MethodType(ref_fn, o)()
    assert type(pace) is fl.FlatLaunchLine and not pace.launch_on                     # v6.15 off: no launch at all
    o = types.SimpleNamespace(_v66_pace=None, research_v68_rolling_budget=True, research_v620_room_pace=False,
                              research_v628_flat_launch=True, **dict(zip(t621.SWITCHES, (True, True, True))))
    assert type(types.MethodType(ref_fn, o)()) is RollingPaceLine                      # nothing to wrap


def test_an_error_building_s8s_line_keeps_v627s_line():
    def boom(**kw):
        raise RuntimeError("boom")
    ref_fn, line_fn = _install(V628FlatLaunchLine=boom)
    o = types.SimpleNamespace(_v66_pace=None, research_v68_rolling_budget=True, research_v615_launch=True,
                              research_v620_room_pace=True, research_v628_flat_launch=True, _v6_28_errors=0,
                              **dict(zip(t621.SWITCHES, (True, True, True))))
    o._v6_28_line = types.MethodType(line_fn, o)
    pace = types.MethodType(ref_fn, o)()
    assert type(pace) is yw.YoungWindowLine and pace.launch_on and o._v6_28_errors == 1


# ---- 6. errors and the state row -----------------------------------------------------------------------------------------

def test_an_error_in_the_clip_falls_back_to_v627s_clip_for_that_book():
    boom = dict(EXTRA, v628_launch_clip=lambda *a, **k: 1 / 0)
    on, ref = _agent(names=boom), _ref()
    for i in range(2):
        ts = NOW + i * 600 * S
        assert _out(_request(on, ts=ts, wall=1000.0 + i)) == _out(_request(ref, ts=ts, wall=1000.0 + i)), i
    assert on._v6_28_errors == 2 and _counts(on) == {} and on._v615_counts == ref._v615_counts


def test_an_error_restoring_the_rates_leaves_v615s_rate_and_the_rest_of_the_restart():
    t_w = NOW - 3_000 * S
    saved = _saved(fl.FlatLaunchLine, t_w)
    boom = dict(EXTRA, v628_restore_rates=lambda *a, **k: 1 / 0)
    on, ref = _agent(used=3_000.0, names=boom), _ref(used=3_000.0)
    for a in (on, ref):
        a._v621_restore_payload = json.loads(json.dumps(saved))
    got, want = _out(_request(on)), _out(_request(ref))
    assert got == want and _l0(got) == [4.0, 4.0]                    # v6.15's rate for that book: v6.27
    assert on._v621_counts == {"restore_restored": 1} and on._v621_errors == 0 and on._v6_28_errors == 1
    assert on._v66_pace.launch_rates == {} and t622._fingerprint(on) == t622._fingerprint(ref)


def test_an_error_saving_the_rates_leaves_v621s_rows():
    boom = dict(EXTRA, v628_save_rates=lambda *a, **k: 1 / 0)
    agent = _agent(names=boom)
    _request(agent)
    state = agent._v621_session_state()
    plain = json.loads(json.dumps(state))
    agent._v6_28_save(state)
    assert state == plain and "rate" not in state["books"]["3"] and agent._v6_28_errors == 1
    good = _agent()
    _request(good)
    state = good._v621_session_state()
    good._v6_28_save(state)
    assert state["books"]["3"]["rate"] == 1.0 and good._v6_28_errors == 0
    good._v621_prepared = False                                      # a state not applied yet is left as it came
    stash = {"books": {"3": {"t0": NOW, "u0": 0.0}}}
    good._v6_28_save(stash)
    assert stash == {"books": {"3": {"t0": NOW, "u0": 0.0}}}


def test_a_tally_error_never_changes_a_decision():
    ref, agent = _agent(), _agent()
    agent._v6_28_counts = "x"                                        # every count raises inside the clip's tally
    assert _out(_request(agent)) == _out(_request(ref))
    assert _l0(_out(_request(agent, ts=NOW + 600 * S, wall=1001.0))) == [2.0, 2.0]
    assert agent._v6_28_errors >= 2 and ref._v6_28_errors == 0


def test_the_state_row_comes_at_the_first_pass_every_100_ticks_and_at_the_first_error():
    flag = {"boom": False}

    def clip(*a, **k):
        if flag["boom"]:
            raise RuntimeError("boom")
        return fl.launch_clip(*a, **k)
    agent = _agent(names=dict(EXTRA, v628_launch_clip=clip))
    for i, tick in enumerate((1, 2, 99, 100, 101)):
        agent._tick = tick
        _request(agent, ts=NOW + i * S, wall=1000.0 + i)
    rows = _rows(agent)
    assert [(r["reason"], r["tick"]) for r in rows] == [("start", 1), ("periodic", 100)]
    assert tuple(k for k in rows[0] if k not in ("reason", "tick")) == FIELDS
    first, periodic = rows
    assert first["version"] == fl.V628_FLAT_LAUNCH_VERSION and first["flat_launch_on"] == 1 and first["flat_line"] == 1
    assert (first["launch_flat"], first["launch_legacy"], first["open_flat"], first["open_legacy"]) == (1, 0, 1, 0)
    assert (first["flat_clip_book_states"], first["errors"]) == (1, 0)
    assert periodic["flat_clip_book_states"] == 4
    for k, (tick, boom) in enumerate(((102, True), (103, True), (104, False), (105, True))):
        flag["boom"] = boom
        agent._tick = tick
        _request(agent, ts=NOW + (5 + k) * S, wall=1005.0 + k)
    got = [(r["reason"], r["tick"], r["errors"]) for r in _rows(agent)]
    assert got == [("start", 1, 0), ("periodic", 100, 0), ("error", 102, 1), ("error", 105, 3)]


def test_the_lines_own_errors_reach_the_row():
    agent = _agent()
    agent._tick = 1
    _request(agent)
    agent._v66_pace.launch_rates = "broken"                          # every rate read fails: v6.15's rate, counted
    agent._tick = 2
    out = _out(_request(agent, ts=NOW + S, wall=1001.0))
    assert _l0(out) == [4.0, 4.0]                                    # that book's v6.27 clip
    rows = _rows(agent)
    assert [(r["reason"], r["errors"] > 0) for r in rows] == [("start", False), ("error", True)]
    assert agent._v6_28_errors == 0 and agent._v66_pace.errors > 0 and rows[1]["errors"] == agent._v66_pace.errors


def test_the_row_reports_what_the_restart_restored():
    saved = _saved(yw.YoungWindowLine, NOW - 3_000 * S)
    agent = _agent(used=3_000.0)
    agent._v621_restore_payload = saved
    agent._tick = 1
    _request(agent)
    row = _rows(agent)[0]
    assert (row["restored_unrecorded"], row["restored_flat"], row["launch_legacy"], row["open_legacy"]) == (1, 0, 1, 1)
    assert (row["launch_flat"], row["flat_clip_book_states"], row["errors"]) == (0, 0, 0)


def test_the_row_never_fails_the_request_and_waits_for_the_reply_when_the_request_defers_its_telemetry():
    agent, ref, deferred = _agent(), _agent(), _agent()
    emitted = agent.emitted
    agent._emit = lambda event, force=False, **kw: (1 / 0) if event == "V628_STATE" else emitted.append((event, kw))
    agent._tick = 1
    assert _out(_request(agent)) == _out(_request(ref))
    assert agent._v6_28_errors == 1 and _rows(agent) == []
    work = []
    deferred._v629_request_work = types.SimpleNamespace(add=lambda name, fn: work.append((name, fn)))
    deferred._tick = 1
    _request(deferred)
    mine = [fn for n, fn in work if n == "v628_state_row"]
    assert _rows(deferred) == [] and len(mine) == 1
    assert [n for n, _fn in work] == ["v623_state_row", "v625_state_row", "v626_state_row", "v627_state_row",
                                      "v628_state_row"]
    mine[0]()
    row = _rows(deferred)[0]
    assert row["reason"] == "start" and (row["launch_flat"], row["flat_clip_book_states"]) == (1, 1)


def test_off_the_row_says_so_and_an_agent_without_the_build_has_none():
    agent = _agent(on=False)
    agent._tick = 1
    _request(agent)
    row = _rows(agent)[0]
    assert (row["flat_launch_on"], row["flat_line"], row["launch_flat"], row["launch_legacy"], row["errors"]) == \
        (0, 0, 0, 0, 0)
    older = _ref()                                                   # v6.27's harness: no switch at all
    older._tick = 1
    _request(older)
    assert not [e for e, _kw in older.emitted if e == "V628_STATE"]


# ---- 7. the session file ---------------------------------------------------------------------------------------------------

def _session_host():
    """The real _research_save_session / _research_read_session / _v621_session_state and v6.28's _v6_28_save, compiled
    in a class whose base stands in for Strategy1_Research (t621's host)."""
    import textwrap

    class _Base:
        def _research_save_session(self, force=False):
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump({"schema": 1}, fh)
            self._research_session_last_save_tick = self._tick

        def _research_session_path(self, identity):
            return self.path

    class _Stats:
        count = 0

        @staticmethod
        def from_state(row):
            return _Stats()

        def as_state(self):
            return {}

    scope = {"_Base": _Base, "json": json, "os": os, "DIRECT_QUALITY_VERSION": "q", "MakerLifecycleStats": _Stats,
             "OWNER_KEY": "v504_session_owner", "V503_NEWCOMER_GATE_VERSION": "v503", "V504_MIRROR_SESSION_KEY": "v504m",
             "V68_DEEP_SESSION_KEY": "deep_layer_v6_8", "mirror_session_state": lambda s: {},
             "owner_record": lambda uid: {"uid": uid}, "v68_deep_state": lambda layer: {}}
    scope.update(EXTRA)
    body = "class _Host(_Base):\n" + "\n".join(textwrap.indent(_src(n), "    ") for n in (
        "_research_save_session", "_research_read_session", "_v621_session_state", "_v6_28_save")) + "\n"
    exec(compile(body, "<v628-session-host>", "exec"), scope)
    return scope["_Host"]


def _host(path, *, on=True, pace=None, prepared=True):
    h = _session_host()()
    h.path, h.uid, h._tick = str(path), UID, 100
    h._research_session_identity = types.SimpleNamespace(simulation_id=SIM)
    h._v503_gate_session_state = lambda: {}
    h._v61_on = lambda: False
    h._v61_lots_from_session = lambda raw: None
    h._v504_mirror, h._v633_deep = None, None
    h.research_v621_pace_persist = True
    h.research_v628_flat_launch = on
    h._v621_errors, h._v621_prepared, h._v621_restore_payload, h._v66_pace = 0, prepared, None, pace
    h._v6_28_errors = 0
    return h


def _launched():
    pace = fl.FlatLaunchLine(launch=True)
    pace.sim_id = SIM
    pace.observe(3, NOW, 0.0, FLOOR)
    pace.observe(5, NOW, 300_000.0, FLOOR)
    pace.line(3, NOW, CAP)
    return pace


def test_the_session_file_carries_each_launchs_rate_and_a_new_process_brings_it_back(tmp_path):
    pace = _launched()
    first = _host(tmp_path / "a.json", pace=pace)
    first._research_save_session(force=True)
    disk = json.loads((tmp_path / "a.json").read_text())
    rows = disk[yw.PACE_SESSION_KEY]["books"]
    assert rows["3"]["rate"] == 1.0 and "rate" not in rows["5"] and first._v6_28_errors == 0
    want = yw.pace_state(pace, uid=UID)
    fl.save_rates(pace, want)
    assert disk[yw.PACE_SESSION_KEY] == json.loads(json.dumps(want))
    second = _host(tmp_path / "b.json", prepared=False)              # a new process: stashed until its first request
    second._v504_read_session = lambda identity: disk
    second._research_read_session(None)
    second._research_save_session(force=True)                         # a save before it keeps the record as it came
    assert json.loads((tmp_path / "b.json").read_text())[yw.PACE_SESSION_KEY] == disk[yw.PACE_SESSION_KEY]
    line, got = _restored(fl.FlatLaunchLine, second._v621_restore_payload)
    assert got["flat"] == 1 and line.launch_rates == {3: 1.0}
    _same(pace, line, (3, 5), (NOW + 600 * S, NOW + W, NOW + P))


def test_off_or_on_v627s_line_the_session_file_is_v627s(tmp_path):
    for name, on, pace in (("off", False, _launched()), ("v6.27 line", True, None)):
        if pace is None:
            pace = yw.YoungWindowLine(launch=True)
            pace.sim_id = SIM
            pace.observe(3, NOW, 0.0, FLOOR)
        h = _host(tmp_path / f"{name}.json", on=on, pace=pace)
        h._research_save_session(force=True)
        disk = json.loads((tmp_path / f"{name}.json").read_text())
        assert disk[yw.PACE_SESSION_KEY] == json.loads(json.dumps(yw.pace_state(pace, uid=UID))), name
        assert all("rate" not in row for row in disk[yw.PACE_SESSION_KEY]["books"].values()), name
        assert h._v6_28_errors == 0


# ---- 8. wiring -----------------------------------------------------------------------------------------------------------

def test_the_switch_ships_on_and_agrees_with_its_agent_default():
    params = t617._params()
    assert "research_v628_flat_launch=1 " in t617._shipped() + " " and params.count("research_v628_flat_launch=") == 1
    assert 'research_v627_touch_centre=1 \\\nresearch_v628_flat_launch=1 \\\n' in LAUNCHER    # after v6.27's key (v6.29's next)
    for key in ("research_v615_launch=1", "research_v620_room_pace=1", "research_v621_pace_persist=1",
                "research_v621_even_spend=1", "research_v621_rolloff_guard=1"):
        assert f" {key} " in f" {t617._shipped()} ", key                                      # what S8 sits on
    init = _src("_init_build_switches")
    lines = (SWITCH_LINE, "self._v6_28_counts: dict[str, int] = {}", "self._v6_28_errors = 0",
             "self._v6_28_errors_seen = 0", "self._v6_28_reported = False", "self._v6_28_erring = False")
    for line in lines:
        assert SIMPLE.count(line) == 1 and line in init, line
    idx = [init.index(x) for x in lines]
    assert init.index("self._v6_27_erring = False") < idx[0] and idx == sorted(idx) and \
        idx[-1] < init.index("self._v69_counts: dict[str, int] = {}")
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_3_0"' in SIMPLE                  # the launcher's arm keys on it


def test_every_v628_name_the_agent_imports_is_the_modules():
    tree = ast.parse(SIMPLE)
    imp = next(n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "research_v628_flat_launch")
    for alias in imp.names:
        assert V628_NAMES[alias.asname or alias.name] is getattr(fl, alias.name), alias.asname
    assert {a.asname or a.name for a in imp.names} == set(V628_NAMES)
    assert SIMPLE.count("from research_v628_flat_launch import (") == 1
    assert t623.V628_IDENTIFIERS == set(V628_NAMES) | {"research_v628_flat_launch"}
    for mod in (t624, t625, t626, t627):
        assert mod.V628_IDENTIFIERS is t623.V628_IDENTIFIERS, mod.__name__
    bound = [a.asname or a.name for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names]
    for name in V628_NAMES:
        assert bound.count(name) == 1, name                       # no clash with v6.2.8's V628_ / v628_ names


def test_each_line_is_wired_once_in_its_place():
    for line in (INSTALL_LINE, RESTORE_LINE, SAVE_LINE, CLIP_LINE, REPORT_LINE):
        assert SIMPLE.count(line) == 1, line
    ref = _src("_v66_pace_ref")
    assert ref.index("pace = V621YoungWindowLine(launch=pace.launch_on,") < \
        ref.index('if bool(getattr(self, "research_v628_flat_launch", False)) and isinstance(pace, V621YoungWindowLine):') \
        < ref.index(INSTALL_LINE) < ref.index("self._v66_pace = pace")
    pass21 = _src("_v621_pass")
    assert pass21.index("how = v621_restore_pace(") < pass21.index('self._v621_count("restore_" + str(how))') < \
        pass21.index('if bool(getattr(self, "research_v628_flat_launch", False)):') < pass21.index(RESTORE_LINE) < \
        pass21.index("if pace.window_status is None:")
    save = _src("_research_save_session")
    assert save.index("payload[V621_PACE_SESSION_KEY] = v621_state") < save.index(SAVE_LINE) < \
        save.index('payload["direct_maker_quality_a1_5_1"]')
    body = _src("_v63_pass")
    fn = ast.parse(body).body[0]
    launch_if = [n for n in ast.walk(fn) if isinstance(n, ast.If) and ast.unparse(n.test) ==
                 "bool(getattr(self, 'research_v615_launch', False)) and pace is not None"]
    assert len(launch_if) == 1 and not launch_if[0].orelse
    assert [ast.unparse(s).splitlines()[0] for s in launch_if[0].body] == ["try:", "if v6_28_on:"]
    assert ast.unparse(launch_if[0].body[1].body[0]) == CLIP_LINE and not launch_if[0].body[1].orelse
    assert body.index("self._v6_27_taus = {}") < body.index('v6_28_on = bool(getattr(self, "research_v628_flat_launch", '
                                                            'False))') < body.index("for raw_id in sorted(books")
    assert body.index("clip_b = v615_launch_clip(deep.clip, pace, book_id, now_ts)") < body.index(CLIP_LINE) < \
        body.index("if clip_b > deep.clip + 1e-12:")
    assert body.index("self._v6_27_report()") < body.index(REPORT_LINE) < body.index("return placed_total")
    assert 'if getattr(self, "research_v628_flat_launch", None) is not None:' in body
    for name in ("_v633_book", "_v617_book", "_v617_touch", "_v621_session_state", "_research_read_session",
                 "_v615_snapshot", "_v621_snapshot", "_v66_snapshot", "_v621_window_start", "respond",
                 "_research_final_validate_instructions"):
        text = _src(name)
        assert "v6_28" not in text and not any(n in text for n in t623.V628_IDENTIFIERS), name


# ---- 9. v6.27's code, statement by statement (the committed v6.27: 68948b3) -------------------------------------------------

V627_COMMIT = "68948b3"
# v6.29 (S9, research_v629_touch_fp_move, and S8's first save) only adds -- its import, its _v6_29_ methods, its switch
# and state, and statements naming them in the touch quote, the pass and the session save -- so the method-by-method
# check reads v6.28 with v6.29 taken out (_Drop629; tests/test_research_v6_29.py checks v6.29's footprint), and t627's
# _Drop628 takes v6.29's additions out with v6.28's.  v6.30 (the identity profile, research_v630_deep_tier /
# research_v630_s9_lookback) only adds as well: _Drop629 takes its additions out too (tests/test_research_v6_30.py checks
# them against the committed v6.29).
V6_29_IDENTIFIERS = t623.V6_29_IDENTIFIERS
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


def _mentions629(node):
    for n in ast.walk(node):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.arg if isinstance(n, (ast.arg, ast.keyword)) else n.value if isinstance(n, ast.Constant) else None
        if isinstance(name, str) and (name in V6_29_IDENTIFIERS or name in V6_30_IDENTIFIERS
                                      or name in V6_31_IDENTIFIERS
                                      or name in V6_32_IDENTIFIERS
                                      or name in V6_33_IDENTIFIERS
                                      or name in V6_34_IDENTIFIERS
                                      or name in V6_36_IDENTIFIERS
                                      or name.startswith(("_v6_29_", "v6_29_", "_v6_30_", "v6_30_", "_v6_31_",
                                                          "v6_31_", "_v6_32_", "v6_32_", "_v6_33_", "v6_33_",
                                                          "_v6_34_", "v6_34_", "_v6_36_", "v6_36_"))):
            return True
    return False


class _Drop629(ast.NodeTransformer):
    """Takes out what v6.29 (and v6.30 after it) adds and nothing else -- its import, its _v6_29_ methods, statements
    naming its identifiers (an if on one keeps its else) -- keeping every docstring: v6.28 as verified, for the
    method-by-method check against v6.27."""

    def _stmts(self, stmts):
        out = []
        for st in stmts:
            if isinstance(st, ast.ImportFrom) and st.module in ("research_v6_29_touch_move",
                                                                "research_v630_identity_profile",
                                                                "research_v631_touch_improve",
                                                                "research_v6_32_fee_making",
                                                                "research_v6_32_state_fp",
                                                                "research_v6_33_touch_identity",
                                                                "research_v6_34_class1",
                                                                "research_v6_36_step_withdrawal"):
                continue
            if isinstance(st, ast.FunctionDef) and st.name.startswith(("_v6_29_", "_v6_30_", "_v6_31_", "_v6_32_",
                                                                       "_v6_33_", "_v6_34_", "_v6_36_")):
                continue
            if isinstance(st, ast.If) and _mentions629(st.test):
                out.extend(self._stmts(st.orelse))
                continue
            st = self.visit(st)
            if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)) and _mentions629(st):
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


def _v627(rel):
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "show", f"{V627_COMMIT}:{rel}"], cwd=ROOT,
                             capture_output=True, text=True, timeout=120)
    except Exception:
        out = None
    if out is None or out.returncode != 0:
        pytest.skip(f"git or {V627_COMMIT} is not available here")
    return out.stdout


def test_every_v627_statement_is_in_v628_unchanged_and_v628_only_adds():
    """Simple of the committed v6.27 == Simple of v6.28 with v6.28's additions taken out, docstrings and all: no v6.27
    statement, argument, call or branch was changed or dropped."""
    old = _v627("agents/strategy/Strategy1_Research_Simple.py")
    assert not t627._mentions628(ast.parse(old)), "v6.27 already names a v6.28 identifier: the strip would hide a change"
    stripped = t627._Drop628().visit(ast.parse(SIMPLE))
    assert ast.dump(stripped) == ast.dump(ast.parse(old))
    assert not t627._mentions628(stripped)
    assert ast.dump(t627._Drop628().visit(ast.parse(SIMPLE))) != ast.dump(ast.parse(SIMPLE))
    bad = SIMPLE.replace("clip_b = v615_launch_clip(deep.clip, pace, book_id, now_ts)\n",
                         "clip_b = v615_launch_clip(deep.clip, pace, book_id, now_ts, rate=1.0)\n")
    assert bad != SIMPLE and ast.dump(t627._Drop628().visit(ast.parse(bad))) != ast.dump(ast.parse(old))


def test_only_the_switch_block_the_pace_line_its_restore_and_save_and_the_pass_changed_and_eight_methods_were_added():
    old = _v627("agents/strategy/Strategy1_Research_Simple.py")

    def methods(tree):
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
        out: dict = {}
        for n in cls.body:
            if isinstance(n, ast.FunctionDef):
                out.setdefault(n.name, []).append(ast.dump(n))
        return out
    assert not _mentions629(ast.parse(old))
    a, b = methods(ast.parse(old)), methods(_Drop629().visit(ast.parse(SIMPLE)))     # v6.28: v6.29, v6.30 out
    assert not _mentions629(_Drop629().visit(ast.parse(SIMPLE)))
    assert set(b) - set(a) == set(METHODS) and not set(a) - set(b)
    changed = {k for k in a if a[k] != b[k]}
    assert changed == {"_init_build_switches", "_v66_pace_ref", "_v621_pass", "_research_save_session", "_v63_pass"}, \
        changed
    for k in ("_v633_book", "_v617_book", "_v617_touch", "_v621_session_state", "_research_read_session",
              "_v615_snapshot", "_v621_snapshot", "_v621_window_start", "_v6_27_report", "respond",
              "_research_final_validate_instructions"):
        assert a[k] == b[k], k
    for other in ("Strategy1_Research.py", "Strategy1.py", "research_v615_launch.py", "research_v620_room_pace.py",
                  "research_v621_young_window.py", "research_v621_window_start.py", "research_v622_aged_cap.py",
                  "research_v68_deep_owns.py", "research_v66_pace_line.py", "research_v641_pace.py",
                  "research_v627_touch_centre.py", "research_v65_deep_clips.py", "research_v633_deep_layer.py"):
        assert _v627(f"agents/strategy/{other}") == (STRATEGY / other).read_text(), other


def test_the_launcher_only_adds_to_v627s():
    """The committed v6.27 launcher line by line: three lines extended (the flag defaults, the v6_3_0 arm, PARAMS' last
    key), the rest added -- the v6.28 block and its gate entry.  v6.29 and v6.30 each extend the same three lines and
    add their block and gate entry."""
    old, new = _v627("run_strategy1_research_simple_multi.sh").splitlines(), LAUNCHER.splitlines()
    removed, added = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += old[i1:i2]
        if tag in ("replace", "insert"):
            added += new[j1:j2]
    defaults = next(x for x in old if x.startswith("A19X_BUILD=0;"))
    arm = next(x for x in old if x.startswith("  strategy1_direct_v6_3_0)"))
    assert removed == [defaults, arm, 'research_v627_touch_centre=1"']
    block = []
    for head in ("# v6.28 S8.", "# v6.29 S9.", "# v6.30.", "# v6.31.", "# v6.32.", "# v6.33.", "# v6.34.", "# v6.36."):
        start = LAUNCHER.index(head)
        block += LAUNCHER[start:LAUNCHER.index("\nfi\n", start) + len("\nfi\n")].splitlines() + [""]
    assert sorted(added) == sorted([defaults + "; V6_28_BUILD=0; V6_29_BUILD=0; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0; V6_36_BUILD=0",
                                    arm + "; V6_28_BUILD=1; V6_29_BUILD=1; V6_30_BUILD=1; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1; V6_36_BUILD=1",
                                    "research_v627_touch_centre=1 \\", "research_v628_flat_launch=1 \\",
                                    "research_v629_touch_fp_move=1 \\", "research_v630_deep_tier=1 \\",
                                    "research_v630_s9_lookback=1 \\", "research_v631_touch_improve=1 \\",
                                    "research_v631_single_write_save=1 \\",
                                    "research_v632_fee_making=1 \\",
                                    "research_v632_fee_weight=0.005 \\",
                                    "research_v632_state_fundamental=1 \\",
                                    "research_v633_touch_identity=1 \\",
                                    "research_v634_class1_entry=0 \\",
                                    'research_v636_step_withdrawal=1"', "      tests/test_research_v6_28.py \\",
                                    "      tests/test_research_v6_29.py \\", "      tests/test_research_v6_30.py \\",
                                    "      tests/test_research_v6_31.py \\",
                                    "      tests/test_research_v6_32.py \\",
                                    "      tests/test_research_v6_33.py \\",
                                    "      tests/test_research_v6_34.py \\",
                                    "      tests/test_research_v6_36.py \\",
                                    *block])


# ---- 10. the launcher ----------------------------------------------------------------------------------------------------

def test_the_v628_block_is_gated_preflighted_and_listed():
    assert "; V6_25_BUILD=0; V6_26_BUILD=0; V6_27_BUILD=0; V6_28_BUILD=0; V6_29_BUILD=0; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0; V6_36_BUILD=0\n" in LAUNCHER
    arm = next(x for x in LAUNCHER.splitlines() if x.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_26_BUILD=1; V6_27_BUILD=1; V6_28_BUILD=1; V6_29_BUILD=1; V6_30_BUILD=1; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1; V6_36_BUILD=1"), arm
    assert LAUNCHER.count('if [[ "${V6_28_BUILD:-0}" == "1" ]]; then') == 1 and LAUNCHER.count("V6_28_BUILD=") == 2
    assert "      tests/test_research_v6_27.py \\\n      tests/test_research_v6_28.py \\\n" in LAUNCHER
    assert LAUNCHER.index('echo "[preflight] v6.27 touch centre PASS') < LAUNCHER.index("# v6.28 S8.") < \
        LAUNCHER.index('echo "[preflight] v6.28 flat launch PASS') < \
        LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')
    assert LAUNCHER.index('echo "[preflight] v6.15 launch PASS') < LAUNCHER.index('echo "[preflight] v6.21 young window PASS')\
        < LAUNCHER.index("# v6.28 S8.")


def _block():
    start = LAUNCHER.index('if [[ "${V6_28_BUILD:-0}" == "1" ]]; then')
    return LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.28 flat launch PASS', start)]


_PATTERN = r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\" \|\| \{\n\s+echo \"ERROR: ([^\"]+)\" >&2"
FILES = ("Strategy1_Research_Simple.py", "research_v628_flat_launch.py")


def test_every_v628_preflight_pattern_is_in_the_file_it_checks():
    pats = re.findall(_PATTERN, _block())
    assert len(pats) == 9, pats
    for needle, name, _msg in pats:
        assert name in FILES and (STRATEGY / name).read_text().count(needle) == 1, (needle, name)
    assert {p[0] for p in pats} == {
        "from research_v628_flat_launch import (", SWITCH_LINE, INSTALL_LINE, RESTORE_LINE, SAVE_LINE, CLIP_LINE,
        REPORT_LINE, "self.launch_rates[b] = self.flat_rate", "return self.legacy_rate if r is None else float(r)"}


def _run_block(params, agent_path=STRATEGY, build="1"):
    start = LAUNCHER.index("# v6.28 S8.")
    stop = LAUNCHER.index('echo "[preflight] v6.28 flat launch PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_28_BUILD": build}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=120)


def test_the_v628_block_passes_the_shipped_params_and_refuses_a_build_without_the_switch_or_what_it_sits_on():
    shipped = t617._shipped()
    out = _run_block(shipped)
    assert out.returncode == 0 and out.stderr == "", out.stderr
    assert out.stdout.startswith("[preflight] v6.28 flat launch PASS (S8: a launch this process starts runs at the "
                                 "sustainable rate with the deep layer's clip"), out.stdout
    for bad in ("research_v628_flat_launch=0", "", "research_v628_flat_launch=true", "research_v628_flat_launch=11"):
        out = _run_block(shipped.replace("research_v628_flat_launch=1", bad))
        assert out.returncode == 1 and "without research_v628_flat_launch=1" in out.stderr and "PASS" not in out.stdout, bad
    for key in ("research_v615_launch=1", "research_v620_room_pace=1", "research_v621_pace_persist=1"):
        assert f" {key} " in f" {shipped} ", key
        out = _run_block(f" {shipped} ".replace(f" {key} ", f" {key[:-1]}0 "))
        assert out.returncode == 1 and f"v6.28 S8 build without {key} in PARAMS" in out.stderr, key
        assert "PASS" not in out.stdout, key
    out = _run_block("", build="0")                                                   # another build: not run
    assert out.returncode == 0 and out.stdout == "" and out.stderr == ""


def test_the_v628_block_refuses_a_tree_without_each_line_it_checks(tmp_path):
    cases = re.findall(_PATTERN, _block())
    assert len(cases) == 9
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
