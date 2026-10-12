"""v6.21: S1 the roll-off guard, S2 the pace line across a restart, S3 the even spend -- research_v621_young_window and
research_v621_window_start.

v6.20 = v6.19 + the room line for a young window.  With the switches, a book's window start t_w is the latest time it (or
every book of the UID) was seen empty -- its own first report, the session file, or the UID's own earliest log of the
simulation.  S1: a fall of the venue's volume ages a book only if it is at least one minimum fill at the book's price, on
a state whose clock moved on, and -- t_w known -- not before t_w + 86,400 s - 600 s.  S2: every book's pace state rides in
the UID's session file and a restart continues the same line and size factor.  S3: a young book with a known t_w spends
its room evenly to the aim at its own roll-off (band = one making window at its own rate), starting one steady lead below
its usage, and a launching book pays back to the aim at its roll-off.  Unknown t_w or aged: v6.20 exactly; without the
switches the build is v6.20.
"""
import gzip
import json
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
import research_v621_window_start as ws  # noqa: E402
import research_v621_young_window as yw  # noqa: E402
from research_session_state import extract_simulation_id  # noqa: E402
from research_v615_launch import LaunchPaceLine, launch_line  # noqa: E402
from research_v62_breadth import lot_quantity  # noqa: E402
from research_v66_pace_line import PaceLine  # noqa: E402
from research_v68_deep_owns import RollingPaceLine  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_16 as t616  # noqa: E402
import test_research_v6_17 as t617  # noqa: E402
import test_research_v6_19 as t619  # noqa: E402
import test_research_v6_20 as t620  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
S = 1_000_000_000
NOW = t620.NOW
CAP = 500_000.0
BAND = 62_500.0                  # the cap's making-window share: 500,000 x 10,800 / 86,400
T = CAP - BAND                   # v6.20's cap-bound threshold
P = 86_400 * S                   # the assessment period
W = 10_800 * S                   # the making window
PW = 600 * S                     # the validator's sampling window (its volume key)
FLOOR = 0.25 * 300.0             # one minimum fill at a mid of 300
SIM = t616.SIM
UID = 22
SWITCHES = ("research_v621_rolloff_guard", "research_v621_pace_persist", "research_v621_even_spend")
INSTALL_LINE = "pace = V621YoungWindowLine(launch=pace.launch_on,"
PASS_LINE = "self._v621_pass(pace, state, now_ts, pace_duration)"
OBSERVE_LINE = "pace.observe(book_id, now_ts, reading, min_order * 0.5 * (raw_bid + raw_ask))"
READ_LINE = "self._v621_restore_payload = raw.get(V621_PACE_SESSION_KEY)"
SAVE_LINE = "payload[V621_PACE_SESSION_KEY] = v621_state"
EXTRA = dict(t620.EXTRA)
EXTRA.update({
    "V621YoungWindowLine": yw.YoungWindowLine, "V621_YOUNG_WINDOW_VERSION": yw.V621_YOUNG_WINDOW_VERSION,
    "V621_PACE_SESSION_KEY": yw.PACE_SESSION_KEY, "V621_WINDOW_OWN_EMPTY_START": yw.WINDOW_OWN_EMPTY_START,
    "v621_empty_window": yw.empty_window, "v621_pace_state": yw.pace_state, "v621_restore_pace": yw.restore_pace,
    "V621_WINDOW_START_VERSION": ws.V621_WINDOW_START_VERSION, "v621_derive_window_start": ws.derive_window_start,
    "v621_window_of": ws.window_of, "extract_simulation_id": extract_simulation_id,
})
METHODS = ("_v621_count", "_v621_pass", "_v621_reading", "_v621_window_start", "_v621_session_state", "_v621_snapshot")


# ---- 1. S1: the roll-off guard -------------------------------------------------------------------------------------------

def test_nothing_of_a_window_rolls_off_before_one_assessment_period_after_its_start_less_one_sample():
    assert yw.earliest_rolloff(37_320 * S) == (37_320 + 86_400 - 600) * S
    assert yw.earliest_rolloff(None) is None and yw.earliest_rolloff("x") is None
    for t_w in (37_320, 37_200, 37_799, 0, -17_342):
        key0 = (t_w * S // PW) * PW                     # the validator keys the first fill here and prunes it a period on
        assert yw.earliest_rolloff(t_w * S) <= key0 + P, t_w


def test_what_a_new_reading_says_about_the_window():
    roll = 100_000 * S

    def k(prev, used, ts=120_000 * S, last=110_000 * S, floor=FLOOR, r=roll):
        return yw.fall_kind(prev, used, ts, last, floor, r)

    assert k(20_000.0, 20_001.0) is None and k(20_000.0, 20_000.0) is None          # no fall
    assert k(20_000.0, 0.0) is None                                                  # a zero is no reading
    assert k(20_000.0, 20_000.0 - 1e-7) is None                                      # float noise (v6.20's tolerance)
    assert k(20_000.0, 20_000.0 - 5e-5) == yw.FALL_NOISE                             # the validator's rounding
    assert k(20_000.0, 20_000.0 - 74.9) == yw.FALL_NOISE                             # under one minimum fill
    assert k(20_000.0, 20_000.0 - 75.0) == yw.FALL_ROLLOFF                           # a whole fill past the roll-off
    assert k(20_000.0, 19_000.0, ts=110_000 * S) == yw.FALL_REWIND                   # the clock did not move on
    assert k(20_000.0, 19_000.0, ts=99_999 * S, last=99_000 * S) == yw.FALL_GUARDED  # before the window can roll off
    assert k(20_000.0, 19_000.0, ts=roll, last=99_000 * S) == yw.FALL_ROLLOFF        # from t_roll on
    assert k(20_000.0, 19_000.0, r=None) == yw.FALL_ROLLOFF                          # unknown start: the floor only
    assert k(20_000.0, 19_999.0, floor=None) == yw.FALL_ROLLOFF                      # no floor: any real fall
    assert k(None, 1.0) is None and k(1.0, None) is None and k(float("nan"), 1.0) is None


def _fresh_book(line, *, t_w=37_320 * S, book=3):
    line.observe(book, t_w, 0.0, FLOOR)                                              # its first report: an empty window
    return t_w


def test_a_fresh_books_rounding_noise_never_ages_it_and_ages_it_under_v620():
    """UID 22 (10-08): a fresh window cannot roll off for one period; v6.20 aged books on falls of at most 5e-5 quote."""
    line, v620 = yw.YoungWindowLine(launch=True), rp.RoomPaceLine(launch=True)
    t_w = _fresh_book(line)
    v620.observe(3, t_w, 0.0)
    u = 0.0
    for i in range(1, 40):
        u += 900.0
        ts = t_w + i * 50 * S
        line.observe(3, ts, u, FLOOR)
        v620.observe(3, ts, u)
        if i % 5 == 0:                                   # a reading taken between the validator's two passes
            line.observe(3, ts + S, u - 5e-5, FLOOR)
            v620.observe(3, ts + S, u - 5e-5)
    assert line.young(3) and not v620.young(3)
    young = line.snapshot()["young"]
    assert young["falls"] == {"noise": 7, "rewind": 0, "guarded": 0, "rolloff": 0}
    assert abs(young["noise_max"] - 5e-5) < 1e-9 and line.snapshot()["room"]["aged"] == 0


def test_a_real_roll_off_ages_the_book_once_its_window_can_roll_and_not_before():
    line = yw.YoungWindowLine(launch=True)
    t_w = _fresh_book(line)
    roll = t_w + P - PW
    line.observe(3, t_w + 1_000 * S, 40_000.0, FLOOR)
    line.observe(3, roll - 10 * S, 39_000.0, FLOOR)      # a key's worth, but nothing of the window can have rolled off
    assert line.young(3) and line.falls[yw.FALL_GUARDED] == 1 and line.guarded_max == 1_000.0
    line.observe(3, roll + 60 * S, 38_000.0, FLOOR)      # the first key rolls off
    assert not line.young(3) and line.falls[yw.FALL_ROLLOFF] == 1 and line.snapshot()["room"]["aged"] == 1


def test_with_an_unknown_window_start_only_a_fall_of_a_whole_fill_ages_a_book():
    line = yw.YoungWindowLine(launch=True)
    line.observe(3, 40_000 * S, 20_000.0, FLOOR)         # first seen with volume, no UID window start: unknown
    assert line.window_start_ns(3) is None and line.rolloff(3) is None
    line.observe(3, 40_060 * S, 20_000.0 - 5e-5, FLOOR)
    line.observe(3, 40_120 * S, 20_000.0 - 50.0, FLOOR)
    assert line.young(3) and line.falls[yw.FALL_NOISE] == 2
    line.observe(3, 40_180 * S, 20_000.0 - 150.0, FLOOR)  # 100 quote: more than one minimum fill
    assert not line.young(3)
    big = yw.YoungWindowLine(launch=True)                # 172/10's sizes: v6.20's own tolerance (1e-9 x prev) hides it
    big.observe(3, 40_000 * S, 300_000.0, FLOOR)
    big.observe(3, 40_060 * S, 300_000.0 - 5e-5, FLOOR)
    assert big.young(3) and big.falls[yw.FALL_NOISE] == 0


def test_a_fall_on_a_state_whose_clock_did_not_move_on_is_no_roll_off():
    line = yw.YoungWindowLine(launch=True)
    line.observe(3, 40_000 * S, 300_000.0, FLOOR)
    line.observe(3, 40_000 * S, 299_000.0, FLOOR)        # the same state again, lower
    line.observe(3, 39_990 * S, 298_000.0, FLOOR)        # a clock that went back
    assert line.young(3) and line.falls[yw.FALL_REWIND] == 2


def test_without_the_guard_the_fall_test_is_v620s():
    line, v620 = yw.YoungWindowLine(launch=True, guard=False, even=False), rp.RoomPaceLine(launch=True)
    for p in (line, v620):
        p.observe(3, 40_000 * S, 20_000.0)
        p.observe(3, 40_060 * S, 20_000.0 - 5e-5)
    assert not line.young(3) and not v620.young(3)


# ---- 2. S3: the even spend -----------------------------------------------------------------------------------------------

def test_an_anchored_books_line_aim_band_and_start_reproduce_the_replay():
    """vm636 D2L2 (k36): a 172-like book at 70% of the cap with 67,800 sim-s to its roll-off."""
    t_w, now = -17_342 * S, 658 * S
    roll = yw.earliest_rolloff(t_w)
    assert roll == 68_458 * S
    ua = yw.no_burst_anchor(CAP, 0.7 * CAP, now, roll)
    a, s, b = yw.even_aim(CAP, ua, roll - now)
    assert round(ua / CAP, 5) == 0.67115 and round(a / CAP, 5) == 0.95481
    assert round(s * P / CAP, 4) == 0.3615 and round(b / CAP, 5) == 0.04519
    assert abs(b - s * W) < 1e-6 and abs(a + b - CAP) < 1e-6           # one making window at its own rate; aim + band = cap
    assert abs((0.7 * CAP - ua) - b * (1.0 - s * P / CAP)) < 1e-3      # lead0: the steady lead at the sustainable rate
    line, band = yw.even_line(CAP, now, ua, roll, now)
    assert line == ua and band == b
    assert abs(yw.band_scale(0.7 * CAP, line, band) - s * P / CAP) < 1e-6   # the start: f = s in sustainable units
    mid, _ = yw.even_line(CAP, now, ua, roll, now + (roll - now) // 2)
    assert abs(mid - (ua + a) / 2) < 1e-3
    assert yw.even_line(CAP, now, ua, roll, roll) == (a, b) and yw.even_line(CAP, now, ua, roll, roll + P) == (a, b)


def test_above_the_sustainable_rate_the_aim_is_the_threshold_with_v620s_band_and_the_rule_is_continuous():
    a, s, b = yw.even_aim(CAP, 0.0, 10_000 * S)          # all the room in a few hours: faster than sustainable
    assert (a, b) == (T, BAND) and abs(s - T / (10_000 * S)) < 1e-18
    h = 50_000 * S
    u = CAP - CAP * (h + W) / P                          # exactly the sustainable rate: (cap - u) / (H + W) = cap / P
    lo, hi = yw.even_aim(CAP, u - 1e-6, h), yw.even_aim(CAP, u + 1e-6, h)
    assert abs(lo[0] - hi[0]) < 1e-3 and abs(lo[2] - hi[2]) < 1e-3 and abs(lo[1] - hi[1]) < 1e-15
    assert yw.even_aim(CAP, CAP, h)[:2] == (CAP, 0.0)   # nothing left: flat
    assert yw.even_aim(0.0, 10.0, h) == (10.0, 0.0, 0.0)


def test_a_launch_line_spends_two_windows_in_the_first_then_pays_back_to_the_aim_at_its_roll_off():
    t_w = 37_320 * S
    roll = yw.earliest_rolloff(t_w)
    for dt in (0, 3_000 * S, W - PW):                    # inside the launch window: v6.15's launch line exactly
        lv, band = yw.launch_even_line(CAP, t_w, t_w + dt)
        assert abs(lv - launch_line(CAP, t_w, t_w + dt)) < 1e-6 and band == BAND, dt
    lv, band = yw.launch_even_line(CAP, t_w, roll)
    assert abs(lv - 0.90625 * CAP) < 1e-6 and abs(band - 0.09375 * CAP) < 1e-6   # payback 0.75x: A = cap - its band
    assert yw.launch_even_line(CAP, t_w, roll + P) == (lv, band)                  # flat at the aim after
    ref = rp.RoomPaceLine(launch=True)                   # v6.20 caps it at T: a flat stretch before the roll-off
    ref.observe(3, t_w, 0.0)
    assert ref.line(3, roll, CAP) == T and lv > T
    half = t_w + W - PW + (roll - (t_w + W - PW)) // 2
    assert abs(yw.launch_even_line(CAP, t_w, half)[0] - (0.25 + 0.90625) * CAP / 2) < 1.0
    flat, band1 = yw.launch_even_line(CAP, t_w, t_w + 3_000 * S, rate=1.0)        # without launching: rate 1
    assert abs(flat - CAP * 3_600 / 86_400) < 1e-6 and band1 == BAND


def test_the_size_factor_is_one_behind_the_line_and_zero_a_band_ahead():
    assert yw.band_scale(100.0, 100.0, 10.0) == 1.0 and yw.band_scale(90.0, 100.0, 10.0) == 1.0
    assert yw.band_scale(105.0, 100.0, 10.0) == 0.5 and yw.band_scale(130.0, 100.0, 10.0) == 0.0
    for bad in ((None, 100.0, 10.0), (100.0, None, 10.0), (100.0, 100.0, 0.0), (float("nan"), 1.0, 1.0)):
        assert yw.band_scale(*bad) == 1.0, bad


# ---- 3. the line ---------------------------------------------------------------------------------------------------------

def _sequence():
    """Three books: one launching, one first seen with volume that falls, one whose reading wobbles by the rounding."""
    return [(3, 40_000 * S, 0.0), (5, 40_000 * S, 300_000.0), (7, 40_000 * S, 4_500.0),
            (3, 40_600 * S, 9_000.0), (5, 40_600 * S, 312_000.0), (7, 40_600 * S, 4_510.0),
            (3, 41_200 * S, 17_000.0), (5, 41_200 * S, 311_000.0), (7, 41_200 * S, 5_000.0),
            (3, 41_800 * S, 30_000.0), (5, 41_800 * S, 330_000.0), (7, 41_800 * S, 5_000.0 - 5e-5)]


def _same(a, b, now, books=(3, 5, 7)):
    for book in books:
        for used in (0.0, 300_000.0, 460_000.0):
            assert a.line(book, now, CAP) == b.line(book, now, CAP), (book, now)
            assert a.scale(book, now, used, CAP) == b.scale(book, now, used, CAP), (book, now, used)
            assert a.paced(book, now, used, CAP, None) == b.paced(book, now, used, CAP, None), (book, now, used)
        assert a.young(book) == b.young(book) and a.launch_open(book, now) == b.launch_open(book, now), (book, now)


def test_with_its_switches_off_the_line_is_v620_exactly():
    for launch in (True, False):
        line, ref = yw.YoungWindowLine(launch=launch, guard=False, even=False), rp.RoomPaceLine(launch=launch)
        for book, ts, used in _sequence():
            line.observe(book, ts, used, FLOOR)
            ref.observe(book, ts, used)
            _same(line, ref, ts)
        assert not line.young(5) and not line.young(7) and not ref.young(7)


def test_a_book_without_a_known_window_start_keeps_v620s_line_with_every_switch_on():
    line, ref = yw.YoungWindowLine(launch=True), rp.RoomPaceLine(launch=True)
    for book, ts, used in [(5, 40_000 * S, 300_000.0), (5, 40_600 * S, 312_000.0), (5, 41_200 * S, 320_000.0)]:
        line.observe(book, ts, used, FLOOR)
        ref.observe(book, ts, used)
        _same(line, ref, ts, books=(5,))
    assert line.window_start_ns(5) is None and line.even == {}


def test_a_known_window_start_gives_a_book_far_above_its_launch_line_the_no_burst_even_line():
    t_w, now = 17_744 * S, 44_000 * S                    # 172: its window started at its first all-empty report
    line = yw.YoungWindowLine(launch=True)
    line.set_window(t_w, ws.KNOWN)
    line.observe(5, now, 0.72 * CAP, FLOOR)
    roll = yw.earliest_rolloff(t_w)
    ua = yw.no_burst_anchor(CAP, 0.72 * CAP, now, roll)
    assert line.line(5, now, CAP) == ua and line.even[5] == (yw.EVEN_ANCHORED, now, ua)
    a, s, b = yw.even_aim(CAP, ua, roll - now)
    f0 = line.scale(5, now, 0.72 * CAP, CAP)
    assert abs(f0 - s * P / CAP) < 1e-6 and 0.3 < f0 < 0.45                       # the deploy slope (k36: 0.380x)
    assert line.line(5, roll, CAP) == a and abs(a + b - CAP) < 1e-6 and not line.launch_open(5, now)
    ref = rp.RoomPaceLine(launch=True)                   # v6.20 anchors at the usage: full size at the start (the burst)
    ref.observe(5, now, 0.72 * CAP)
    assert ref.scale(5, now, 0.72 * CAP, CAP) == 1.0


def test_a_book_near_its_launch_line_takes_it_after_a_restart_with_the_launch_clip_until_one_window_after_t_w():
    t_w = 37_320 * S                                     # 22: launched 23:25 JST, restarted inside its launch window
    now = t_w + 6_000 * S
    lv, band = yw.launch_even_line(CAP, t_w, now)
    line = yw.YoungWindowLine(launch=True)
    line.set_window(t_w, ws.KNOWN)
    line.observe(3, now, lv + band - 1.0, FLOOR)         # within one band of the launch line
    line.observe(5, now, lv + band + 1_000.0, FLOOR)     # beyond it: anchored
    assert line.line(3, now, CAP) == lv and line.even[3] == (yw.EVEN_LAUNCH,)
    assert line.line(5, now, CAP) < lv + band + 1_000.0 and line.even[5][0] == yw.EVEN_ANCHORED
    assert line.launch_open(3, now) and line.launch_open(3, t_w + W - 1) and not line.launch_open(3, t_w + W)
    assert not line.launch_open(5, now)
    ref = LaunchPaceLine()                               # v6.15 / v6.20: a book first seen with volume never launches
    ref.observe(3, now, lv)
    assert not ref.launch_open(3, now)
    off = yw.YoungWindowLine(launch=False)
    off.set_window(t_w, ws.KNOWN)
    off.observe(3, now, 1.0, FLOOR)
    off.line(3, now, CAP)
    assert off.even[3] == (yw.EVEN_LAUNCH,) and not off.launch_open(3, now)        # no launch clip without v6.15


def test_after_its_launch_window_a_book_far_below_its_launch_line_is_anchored_not_caught_up():
    t_w = 37_320 * S
    now = t_w + W + 3_600 * S                            # the launch window closed an hour (sim) ago
    lv, band = yw.launch_even_line(CAP, t_w, now)
    assert lv - band > 0.10 * CAP
    line = yw.YoungWindowLine(launch=True)
    line.set_window(t_w, ws.KNOWN)
    line.observe(3, now, lv - band + 1.0, FLOOR)         # within one band below: its launch line
    line.observe(5, now, 0.10 * CAP, FLOOR)              # far below: anchored (no full-size, unpaced catch-up)
    assert line.line(3, now, CAP) == lv and line.even[3] == (yw.EVEN_LAUNCH,)
    line.line(5, now, CAP)
    assert line.even[5][0] == yw.EVEN_ANCHORED and line.scale(5, now, 0.10 * CAP, CAP) < 1.0
    inside = yw.YoungWindowLine(launch=True)             # inside its launch window the same book takes the launch line
    inside.set_window(t_w, ws.KNOWN)
    inside.observe(5, t_w + 6_000 * S, 0.01 * CAP, FLOOR)
    inside.line(5, t_w + 6_000 * S, CAP)
    assert inside.even[5] == (yw.EVEN_LAUNCH,)


def test_a_launching_book_pays_back_to_the_aim_at_its_own_roll_off():
    line = yw.YoungWindowLine(launch=True)
    t_w = _fresh_book(line)
    assert line.window_start_ns(3) == t_w and 3 in line.launching
    assert line.line(3, t_w, CAP) == yw.launch_even_line(CAP, t_w, t_w)[0] and line.even[3] == (yw.EVEN_LAUNCH,)
    roll = yw.earliest_rolloff(t_w)
    assert abs(line.line(3, roll, CAP) - 0.90625 * CAP) < 1e-6
    assert abs(line.scale(3, roll, 0.90625 * CAP + 0.09375 * CAP / 2, CAP) - 0.5) < 1e-9
    assert line.launch_open(3, t_w + W - 1) and not line.launch_open(3, t_w + W)
    off = yw.YoungWindowLine(launch=True, even=False)    # without S3: v6.20's launch line, capped at T
    _fresh_book(off)
    assert off.line(3, roll, CAP) == T


def test_a_book_first_seen_empty_after_the_uids_window_start_starts_its_own_window_there():
    line = yw.YoungWindowLine(launch=True)
    line.set_window(17_744 * S, ws.KNOWN)                # the UID's start; a quiet book still empty at a restart
    line.observe(3, 44_000 * S, 0.0, FLOOR)
    line.observe(5, 44_000 * S, 0.5 * CAP, FLOOR)
    assert line.window_start_ns(3) == 44_000 * S and line.window_start_ns(5) == 17_744 * S   # the latest known empty
    assert line.rolloff(3) == (44_000 + 85_800) * S
    assert line.line(3, 44_000 * S, CAP) == yw.launch_even_line(CAP, 44_000 * S, 44_000 * S)[0]
    assert line.launch_open(3, 44_000 * S) and not line.launch_open(5, 44_000 * S)


def test_an_aged_book_is_v615_exactly_whatever_its_window_start():
    line, ref = yw.YoungWindowLine(launch=True), LaunchPaceLine()
    t_w = _fresh_book(line)
    ref.observe(3, t_w, 0.0)
    roll = t_w + P - PW
    line.observe(3, t_w + 1_000 * S, 40_000.0, FLOOR)
    ref.observe(3, t_w + 1_000 * S, 40_000.0)
    line.observe(3, roll + 60 * S, 39_000.0, FLOOR)
    ref.observe(3, roll + 60 * S, 39_000.0)
    assert not line.young(3)
    for now in (roll + 60 * S, roll + 600 * S):
        assert line.line(3, now, CAP) == ref.line(3, now, CAP) and line.scale(3, now, CAP, CAP) == 1.0
        assert line.launch_open(3, now) == ref.launch_open(3, now)


def test_a_new_simulation_rekeys_the_window_start_and_the_anchor_the_validators_way():
    line = yw.YoungWindowLine(launch=True)
    line.sim_duration_ns = P
    line.set_window(17_744 * S, ws.KNOWN)
    line.observe(5, 86_000 * S, 0.9 * CAP, FLOOR)        # above its launch line + band (0.848 of the cap then)
    line.line(5, 86_000 * S, CAP)
    t_a = line.even[5][1]
    line.observe(5, 86_398 * S, 0.9 * CAP + 10.0, FLOOR)
    line.observe(5, 607 * S, 0.9 * CAP + 20.0, FLOOR)    # the next simulation's first state
    assert line.rebases == 1 and line.rekeys == 1 and line.young(5)
    assert line.uid_window_start == 17_744 * S - P       # new = 0 - (old_end - t), old_end = the simulation's length
    assert line.even[5][1] == t_a - P and line.rolloff(5) == (17_744 - 86_400 + 85_800) * S
    assert line.start[5][0] == 86_000 * S - (86_398 - 607) * S                    # v6.8's own line: its rebase, unchanged


# ---- 4. S2: the state a restart restores ---------------------------------------------------------------------------------

def _drive(line, steps):
    for book, ts, used in steps:
        line.observe(book, ts, used, FLOOR)
        line.paced(book, ts, used, CAP, P)             # the pass's order: observe, then pace (which starts the even line)


FIRST = [(3, 44_000 * S, 0.0), (5, 44_000 * S, 0.72 * CAP), (7, 44_000 * S, 0.30 * CAP), (9, 44_000 * S, 0.95 * CAP),
         (3, 44_600 * S, 12_000.0), (5, 44_600 * S, 0.72 * CAP + 2_000.0), (7, 44_600 * S, 0.30 * CAP + 900.0),
         (9, 44_600 * S, 0.95 * CAP + 10.0)]
LATER = [(3, 45_200 * S, 30_000.0), (5, 45_200 * S, 0.72 * CAP + 4_500.0), (7, 45_200 * S, 0.30 * CAP + 1_000.0),
         (9, 45_200 * S, 0.95 * CAP + 10.0 - 5e-5), (3, 45_800 * S, 31_000.0), (5, 45_800 * S, 0.72 * CAP + 4_400.0),
         (7, 45_800 * S, 0.30 * CAP + 5_000.0), (9, 45_800 * S, 0.95 * CAP + 900.0)]


def _busy(window):
    line = yw.YoungWindowLine(launch=True)
    line.sim_id = SIM
    if window is not None:
        line.set_window(window, ws.KNOWN)
    _drive(line, FIRST)
    return line


def test_a_saved_line_restored_in_a_new_process_continues_identically():
    for window in (17_744 * S, None):
        a = _busy(window)
        saved = json.loads(json.dumps(yw.pace_state(a, uid=UID)))
        b = yw.YoungWindowLine(launch=True)
        how = yw.restore_pace(b, saved, uid=UID, sim_id=SIM, now_ns=45_200 * S, duration_ns=P,
                              readings={3: 30_000.0, 5: 0.72 * CAP, 7: 0.3 * CAP, 9: 0.95 * CAP})
        assert how == yw.RESTORE_RESTORED
        b.sim_id = SIM                                   # the agent sets the clock's simulation every request
        assert b.even == a.even and b.start == a.start and b.launching == a.launching and b.window_start == a.window_start
        for step in LATER:
            _drive(a, [step])
            _drive(b, [step])
            _same(a, b, step[1], books=(3, 5, 7, 9))
        assert yw.pace_state(a, uid=UID) == yw.pace_state(b, uid=UID)
        assert b.first_seen == 0 and b.first_empty == 0           # a restored book is not one this process first saw


def test_the_size_factor_continues_across_a_restart_where_v620_and_an_unsaved_start_begin_again():
    a = _busy(17_744 * S)
    used = 0.72 * CAP + 4_500.0
    saved = json.loads(json.dumps(yw.pace_state(a, uid=UID)))
    b = yw.YoungWindowLine(launch=True)
    yw.restore_pace(b, saved, uid=UID, sim_id=SIM, now_ns=45_200 * S, duration_ns=P)
    for p in (a, b):
        _drive(p, [(5, 45_200 * S, used)])
    fa = a.scale(5, 45_200 * S, used, CAP)
    assert b.scale(5, 45_200 * S, used, CAP) == fa < 0.38          # the lead the book built is still there
    fresh = yw.YoungWindowLine(launch=True)                        # no saved state: the no-burst start again
    fresh.set_window(17_744 * S, ws.KNOWN)
    _drive(fresh, [(5, 45_200 * S, used)])
    assert fresh.scale(5, 45_200 * S, used, CAP) > fa
    v620 = rp.RoomPaceLine(launch=True)                             # v6.20: anchored at the usage, full size
    v620.observe(5, 45_200 * S, used)
    assert v620.scale(5, 45_200 * S, used, CAP) == 1.0


def test_a_restore_takes_only_this_uids_state_of_this_simulation_into_a_line_that_has_seen_nothing():
    saved = yw.pace_state(_busy(17_744 * S), uid=UID)

    def r(payload, **kw):
        args = {"uid": UID, "sim_id": SIM, "now_ns": 45_200 * S, "duration_ns": P}
        args.update(kw)
        return yw.restore_pace(yw.YoungWindowLine(launch=True), payload, **args)

    assert r(None) == yw.RESTORE_NONE and r([1, 2]) == yw.RESTORE_NONE
    assert r(dict(saved, version="room_pace_v6_20")) == yw.RESTORE_VERSION
    assert r(saved, uid=23) == yw.RESTORE_FOREIGN and r(saved, uid=None) == yw.RESTORE_FOREIGN
    assert r(dict(saved, uid=None)) == yw.RESTORE_FOREIGN
    assert r(dict(saved, books=[])) == yw.RESTORE_INVALID
    assert r(dict(saved, books={"x": {"t0": 1, "u0": 1.0}, "3": {"t0": "y", "u0": 1.0}})) == yw.RESTORE_INVALID
    assert r(saved, sim_id="20261010_1500") == yw.RESTORE_OTHER_SIM               # another simulation, clock not ahead
    assert r(dict(saved, sim_id=None)) == yw.RESTORE_OTHER_SIM
    live = yw.YoungWindowLine()
    live.observe(3, NOW, 1.0)
    assert yw.restore_pace(live, saved, uid=UID, sim_id=SIM, now_ns=NOW) == yw.RESTORE_LIVE
    assert r(saved) == yw.RESTORE_RESTORED


def test_a_state_saved_in_the_previous_simulations_clock_is_rekeyed_the_validators_way():
    a = yw.YoungWindowLine(launch=True)
    a.sim_id = SIM
    a.set_window(17_744 * S, ws.KNOWN)
    _drive(a, [(5, 86_000 * S, 0.9 * CAP), (5, 86_398 * S, 0.9 * CAP + 10.0)])
    saved = yw.pace_state(a, uid=UID)                    # the save that crossed the seam: the old clock in the new file
    b = yw.YoungWindowLine(launch=True)
    assert yw.restore_pace(b, saved, uid=UID, sim_id="20261010_1500", now_ns=900 * S, duration_ns=P) == yw.RESTORE_REKEYED
    assert b.uid_window_start == 17_744 * S - P and b.start[5][0] == 86_000 * S - P
    assert b.even[5] == (yw.EVEN_ANCHORED, a.even[5][1] - P, a.even[5][2]) and b.last_ts == 86_398 * S - P
    assert b.book_ts == {}
    b.observe(5, 900 * S, 0.9 * CAP + 20.0, FLOOR)
    assert b.rebases == 0 and b.young(5)


def test_a_first_state_reading_every_book_empty_drops_the_saved_state():
    saved = yw.pace_state(_busy(17_744 * S), uid=UID)
    b = yw.YoungWindowLine(launch=True)
    assert yw.restore_pace(b, saved, uid=UID, sim_id=SIM, now_ns=45_200 * S,
                           readings={3: 0.0, 5: 0.0, 7: 0.0, 9: 0.0}) == yw.RESTORE_EMPTY_WINDOW
    assert b.start == {} and b.uid_window_start is None and b.even == {}
    kept = yw.YoungWindowLine(launch=True)
    assert yw.restore_pace(kept, saved, uid=UID, sim_id=SIM, now_ns=45_200 * S,
                           readings={3: 0.0, 5: 10.0}) == yw.RESTORE_RESTORED     # a zero alone is no reading
    assert yw.empty_window({3: 0.0, 5: 0.0}, 2) and not yw.empty_window({3: 0.0}, 2)
    assert not yw.empty_window({3: 0.0, 5: 1.0}, 2) and not yw.empty_window(None, 2) and not yw.empty_window({}, 0)


def test_a_saved_state_is_kept_when_a_saved_book_has_no_reading():
    """A book the first state carries no account for reads nothing: the window emptied only if EVERY saved book reads
    empty (review 10-08: a missing account read as 0 dropped a valid saved state)."""
    saved = yw.pace_state(_busy(17_744 * S), uid=UID)
    b = yw.YoungWindowLine(launch=True)
    assert yw.restore_pace(b, saved, uid=UID, sim_id=SIM, now_ns=45_200 * S,
                           readings={3: 0.0, 5: 0.0}) == yw.RESTORE_RESTORED          # 7 and 9 without an account
    assert set(b.start) == {3, 5, 7, 9}


def test_a_saved_aged_mark_comes_back_unless_the_window_cannot_have_rolled_off_yet():
    a = yw.YoungWindowLine()
    a.sim_id = SIM
    _drive(a, [(5, 40_000 * S, 300_000.0), (5, 40_060 * S, 299_000.0)])   # unknown start: a whole-fill fall ages it
    assert not a.young(5)
    saved = yw.pace_state(a, uid=UID)
    b = yw.YoungWindowLine()
    yw.restore_pace(b, saved, uid=UID, sim_id=SIM, now_ns=40_100 * S)
    assert not b.young(5) and b.unaged == 0
    early = dict(saved, window={"start": 30_000 * S, "status": ws.KNOWN})   # a window start that rules the fall out
    c = yw.YoungWindowLine()
    yw.restore_pace(c, early, uid=UID, sim_id=SIM, now_ns=40_100 * S)
    assert c.young(5) and c.unaged == 1 and c.window_status == ws.KNOWN
    d = yw.YoungWindowLine(guard=False)
    yw.restore_pace(d, early, uid=UID, sim_id=SIM, now_ns=40_100 * S)
    assert not d.young(5)


def test_rows_it_cannot_read_are_skipped_and_the_rest_restored():
    saved = json.loads(json.dumps(yw.pace_state(_busy(17_744 * S), uid=UID)))
    saved["books"]["11"] = {"t0": "x", "u0": 1.0}
    saved["books"]["y"] = {"t0": 1, "u0": 1.0}
    saved["books"]["13"] = {"t0": 44_000 * S, "u0": 2.0, "even": ["A", "z", 1.0], "launch": "1", "aged": 2}
    b = yw.YoungWindowLine(launch=True)
    assert yw.restore_pace(b, saved, uid=UID, sim_id=SIM, now_ns=45_200 * S) == yw.RESTORE_RESTORED
    assert 11 not in b.start and 13 in b.start and 13 not in b.even and 13 not in b.launching and b.young(13)
    assert set(b.start) == {3, 5, 7, 9, 13}


# ---- 5. S2: the window start from the UID's own logs ---------------------------------------------------------------------

W0 = 1_791_300_000 * S


def _log(d, uid, run_id, *, wall, sim=SIM, first_ts=None, books=128, launch=128, tick=1, header=True, report=True, pad=0,
         gz=False, owner=True, room=None, mid=0):
    name = f"strategy1_research_agent_{uid}_{run_id}.jsonl" + (".gz" if gz else "")
    lines = []
    if header:
        lines.append({"type": "DEBUG_CONFIG", "agent_id": uid, "wall_time_ns": wall})
    for i in range(pad):
        lines.append({"type": "PAD", "i": i, "blob": "x" * 200, "wall_time_ns": wall + 1})
    if owner:
        lines.append({"type": "V504_SESSION_OWNER", "tick": 0, "uid": uid, "wall_time_ns": wall + 2,
                      "own_file": f"research_session_unknown_79_{sim}.uid{uid}.json"})
    for i in range(mid):
        lines.append({"type": "PAD", "i": i, "blob": "y" * 200, "wall_time_ns": wall + 2})
    if report:
        lines.append({"type": "TIMING", "tick": tick, "timestamp": first_ts, "wall_time_ns": wall + 3})
        pl = {"books": books, "rebases": 0}
        if launch is not None:
            pl["launch"] = {"books": launch, "open_now": launch, "rate": 2.0}
        if room is not None:
            pl["room"] = dict(room)
        lines.append({"type": "V62_STATE", "tick": tick, "v66": {"pace_line": pl}, "wall_time_ns": wall + 4})
        lines.append({"type": "FILL", "tick": tick + 5, "wall_time_ns": wall + 5})
    data = "".join(json.dumps(x, separators=(",", ":")) + "\n" for x in lines).encode()
    path = os.path.join(d, name)
    with (gzip.open(path, "wb") if gz else open(path, "wb")) as fh:
        fh.write(data)
    return path


def _derive(d, uid, **kw):
    return ws.derive_window_start(str(d), uid, SIM, 128, **kw)


def test_a_fresh_launch_log_gives_its_first_state_as_the_window_start(tmp_path):
    _log(tmp_path, 22, "20261007_162337", wall=W0, first_ts=37_320 * S)
    _log(tmp_path, 23, "20261007_170000", wall=W0 + S, first_ts=37_500 * S)          # another UID's log: never read
    r = _derive(tmp_path, 22)
    assert (r["status"], r["t_w_ns"], r["anchor"]) == (ws.KNOWN, 37_320 * S, "20261007_162337")
    assert r["no_rolloff_before_ns"] == (37_200 + 86_400) * S and len(r["files"]) == 1
    assert ws.window_of(r) == (37_320 * S, ws.KNOWN)


def test_the_latest_all_empty_start_wins_and_a_restart_with_volume_does_not_move_it(tmp_path):
    """209 (10-07): a brief run at 22:50 JST without fills, the 22:52 start, then a restart with volume at 00:25."""
    _log(tmp_path, 209, "20261007_155031", wall=W0, first_ts=36_879 * S)
    _log(tmp_path, 209, "20261007_155222", wall=W0 + 111 * S, first_ts=36_889 * S)
    _log(tmp_path, 209, "20261007_172553", wall=W0 + 5_000 * S, first_ts=38_102 * S, launch=0)
    r = _derive(tmp_path, 209)
    assert (r["status"], r["t_w_ns"], r["anchor"]) == (ws.KNOWN, 36_889 * S, "20261007_155222")
    assert [f["kind"] for f in r["files"]] == ["ALL_EMPTY", "ALL_EMPTY", "VOLUME"]


def test_a_partial_first_run_that_never_reported_is_no_information(tmp_path):
    _log(tmp_path, 209, "20261007_155031", wall=W0, report=False)                 # died before its first state
    _log(tmp_path, 209, "20261007_155222", wall=W0 + 111 * S, first_ts=36_889 * S)
    r = _derive(tmp_path, 209)
    assert (r["status"], r["t_w_ns"]) == (ws.KNOWN, 36_889 * S) and r["files"][0]["kind"] == "NO_REPORT"


def test_an_aged_uid_and_a_uid_seen_only_in_another_simulation_are_unknown(tmp_path):
    aged, other = tmp_path / "aged", tmp_path / "other"
    aged.mkdir()
    other.mkdir()
    _log(aged, 187, "20261005_053902", wall=W0, sim="20260929_2015", first_ts=81_151 * S, launch=None)
    _log(aged, 187, "20261006_173137", wall=W0 + 9 * 3600 * S, first_ts=19_728 * S, launch=0)
    _log(aged, 187, "20261007_132114", wall=W0 + 29 * 3600 * S, first_ts=34_901 * S, launch=0)
    r = _derive(aged, 187)
    assert (r["status"], r["t_w_ns"]) == (ws.UNKNOWN_NO_EMPTY_START, None)
    assert ws.window_of(r) == (None, ws.UNKNOWN_NO_EMPTY_START)
    _log(other, 187, "20261005_053902", wall=W0, sim="20260929_2015", first_ts=81_151 * S, mid=40)
    r = _derive(other, 187)
    assert (r["status"], r["t_w_ns"]) == (ws.UNKNOWN_NO_READABLE_START, None)
    assert r["files"][0]["kind"] == "OTHER_SIM" and r["files"][0]["bytes"] < 1_000   # dropped at its session owner


def test_a_re_registration_in_the_same_simulation_starts_the_window_again(tmp_path):
    _log(tmp_path, 94, "20261006_010000", wall=W0, first_ts=5_000 * S)
    _log(tmp_path, 94, "20261006_090000", wall=W0 + 8 * 3600 * S, first_ts=11_000 * S, launch=0)
    _log(tmp_path, 94, "20261007_090000", wall=W0 + 32 * 3600 * S, first_ts=30_000 * S)
    _log(tmp_path, 94, "20261007_120000", wall=W0 + 35 * 3600 * S, first_ts=32_500 * S, launch=3)
    assert _derive(tmp_path, 94)["t_w_ns"] == 30_000 * S


def test_a_restart_with_some_books_still_empty_is_not_a_window_start(tmp_path):
    _log(tmp_path, 10, "20261006_162352", wall=W0, first_ts=18_964 * S)
    _log(tmp_path, 10, "20261007_021913", wall=W0 + 10 * 3600 * S, first_ts=26_257 * S, launch=2)
    _log(tmp_path, 10, "20261007_131901", wall=W0 + 21 * 3600 * S, first_ts=34_871 * S, launch=0)
    assert _derive(tmp_path, 10)["t_w_ns"] == 18_964 * S


def test_a_later_start_it_cannot_read_leaves_the_window_start_unknown(tmp_path):
    _log(tmp_path, 172, "20261006_144054", wall=W0, first_ts=17_744 * S)
    _log(tmp_path, 172, "20261007_021805", wall=W0 + 10 * 3600 * S, first_ts=26_243 * S, launch=0, pad=30_000)
    assert _derive(tmp_path, 172, budget=1 << 20)["status"] == ws.UNKNOWN_GAP_AFTER_ANCHOR
    r = _derive(tmp_path, 172, budget=16 << 20)
    assert (r["status"], r["t_w_ns"]) == (ws.KNOWN, 17_744 * S)


def test_gzip_heads_the_running_processs_own_file_and_logs_before_v615(tmp_path):
    gz, own, old = tmp_path / "gz", tmp_path / "own", tmp_path / "old"
    for d in (gz, own, old):
        d.mkdir()
    _log(gz, 172, "20261006_144054", wall=W0, first_ts=17_744 * S, gz=True)
    _log(gz, 172, "20261007_131729", wall=W0 + 22 * 3600 * S, first_ts=34_850 * S, launch=0)
    assert _derive(gz, 172)["t_w_ns"] == 17_744 * S
    mine = _log(own, 22, "20261008_100000", wall=W0, first_ts=50_000 * S)
    assert _derive(own, 22, exclude=mine)["status"] == ws.UNKNOWN_NO_READABLE_START
    _log(old, 104, "20261006_033301", wall=W0, first_ts=10_102 * S, launch=None)       # no launch counter
    assert _derive(old, 104)["status"] == ws.UNKNOWN_NO_READABLE_START


def test_starts_are_ordered_by_their_first_record_not_their_run_id(tmp_path):
    _log(tmp_path, 22, "20261025_023000", wall=W0 + 3600 * S, first_ts=40_000 * S, launch=0)   # later, earlier id
    _log(tmp_path, 22, "20261025_025000", wall=W0, first_ts=39_000 * S)
    assert _derive(tmp_path, 22)["t_w_ns"] == 39_000 * S


def test_at_a_clock_fall_back_the_latest_empty_start_is_the_latest_by_its_first_record(tmp_path):
    """Run ids are local times and repeat after a DST fall-back (Europe 10-25): two all-empty starts whose ids run
    backwards -- the window starts at the one that came LAST in time, not the one with the larger id."""
    _log(tmp_path, 22, "20261025_021500", wall=W0 + 3600 * S, first_ts=40_000 * S)    # second start, smaller id
    _log(tmp_path, 22, "20261025_024500", wall=W0, first_ts=39_000 * S)               # first start, larger id
    r = _derive(tmp_path, 22)
    assert (r["status"], r["t_w_ns"], r["anchor"]) == (ws.KNOWN, 40_000 * S, "20261025_021500")


def test_a_v621_restart_whose_restored_launching_set_fills_the_launch_counter_is_no_window_start(tmp_path):
    _log(tmp_path, 22, "20261007_162337", wall=W0, first_ts=37_320 * S)
    _log(tmp_path, 22, "20261008_080000", wall=W0 + 9 * 3600 * S, first_ts=44_000 * S, launch=128,
         room={"first_seen": 0, "first_empty": 0})                                 # every book came from the session
    assert _derive(tmp_path, 22)["t_w_ns"] == 37_320 * S
    _log(tmp_path, 22, "20261008_090000", wall=W0 + 10 * 3600 * S, first_ts=45_000 * S, launch=0,
         room={"first_seen": 128, "first_empty": 128})                             # a new registration read empty
    assert _derive(tmp_path, 22)["t_w_ns"] == 45_000 * S


def test_every_start_showing_volume_carries_the_previous_simulations_anchor_rekeyed(tmp_path):
    prev = "20260929_2015"
    _log(tmp_path, 172, "20261006_144054", wall=W0, sim=prev, first_ts=17_744 * S)
    _log(tmp_path, 172, "20261007_131729", wall=W0 + 22 * 3600 * S, sim=prev, first_ts=34_850 * S, launch=0)
    assert _derive(tmp_path, 172)["status"] == ws.UNKNOWN_NO_READABLE_START          # no seam length: no carry
    for s_ in (prev, SIM):                                # the UID's sessions: prev is the simulation just before
        (tmp_path / f"research_session_unknown_79_{s_}.uid172.json").write_text("{}")
    (tmp_path / "research_session_unknown_79_20261001_0000.uid10.json").write_text("{}")  # another UID's: ignored
    r = _derive(tmp_path, 172, old_end_ns=P, now_ns=900 * S)          # this process is the simulation's first
    assert (r["status"], r["t_w_ns"], r["carry"]) == (ws.KNOWN_CARRIED, (17_744 - 86_400) * S,
                                                      {"sim": prev, "status": ws.KNOWN})
    assert r["no_rolloff_before_ns"] == (17_400 - 86_400) * S + P
    _log(tmp_path, 172, "20261010_160000", wall=W0 + 90 * 3600 * S, first_ts=2_000 * S, launch=0)
    r = _derive(tmp_path, 172, old_end_ns=P, now_ns=2_000 * S)
    assert (r["status"], r["t_w_ns"]) == (ws.KNOWN_CARRIED, (17_744 - 86_400) * S)
    assert ws.window_of(r) == ((17_744 - 86_400) * S, ws.KNOWN_CARRIED)
    rolling = _derive(tmp_path, 172, old_end_ns=P, now_ns=20_000 * S)
    assert rolling["status"] == ws.KNOWN_CARRIED_ROLLING                         # past its first possible roll-off
    assert ws.window_of(rolling) == (None, ws.KNOWN_CARRIED_ROLLING)              # a rolling window: v6.20
    _log(tmp_path, 172, "20261010_170000", wall=W0 + 91 * 3600 * S, first_ts=3_000 * S, launch=0, pad=30_000)
    assert _derive(tmp_path, 172, old_end_ns=P, now_ns=4_000 * S, budget=1 << 20)["status"] == ws.UNKNOWN_NO_EMPTY_START


def test_a_carry_comes_only_from_the_simulation_just_before_this_one(tmp_path):
    """Review 10-08: one process that ran on through a whole simulation leaves no head in it -- the carry must not reach
    two simulations back (an aged UID would be guarded and paced as young)."""
    older, prev = "20260924_1653", "20260929_2015"
    _log(tmp_path, 172, "20260925_100000", wall=W0, sim=older, first_ts=17_744 * S)
    for s_ in (older, prev, SIM):
        (tmp_path / f"research_session_unknown_79_{s_}.uid172.json").write_text("{}")
    r = _derive(tmp_path, 172, old_end_ns=P, now_ns=900 * S)
    assert (r["status"], r["t_w_ns"]) == (ws.UNKNOWN_CARRY_UNVERIFIED, None)
    assert r["carry"] == {"sim": older, "status": ws.UNKNOWN_CARRY_UNVERIFIED, "just_before": prev}
    assert ws.window_of(r)[0] is None
    bare = tmp_path / "bare"
    bare.mkdir()
    _log(bare, 172, "20261006_144054", wall=W0, sim=prev, first_ts=17_744 * S)
    assert _derive(bare, 172, old_end_ns=P, now_ns=900 * S)["status"] == ws.UNKNOWN_CARRY_UNVERIFIED  # no sessions


def test_the_derivation_reads_only_heads_within_its_budget_and_never_raises(tmp_path):
    path = _log(tmp_path, 22, "20261007_162337", wall=W0, first_ts=37_320 * S, pad=150)
    r = _derive(tmp_path, 22)
    tail = len(open(path, "rb").read().split(b"\n")[-2]) + 1          # the FILL line after the first V62_STATE
    assert r["files"][0]["bytes"] == os.path.getsize(path) - tail
    assert _derive(tmp_path, 22, total_budget=1_000)["status"] == ws.UNKNOWN_BUDGET
    assert _derive(tmp_path / "missing", 22)["status"] == ws.UNKNOWN_NO_DIR
    assert ws.derive_window_start(str(tmp_path), 22, None, 128)["status"] == ws.UNKNOWN_NO_SIM
    assert ws.derive_window_start(str(tmp_path), None, SIM, 128)["status"] == ws.UNKNOWN_NO_READABLE_START
    (tmp_path / "strategy1_research_agent_22_20261007_170000.jsonl.gz").write_bytes(b"not gzip")
    r = _derive(tmp_path, 22)
    assert r["status"] == ws.UNKNOWN_GAP_AFTER_ANCHOR or r["t_w_ns"] == 37_320 * S
    for failed in (ws.UNKNOWN_BUDGET, ws.UNKNOWN_NO_DIR, ws.UNKNOWN_NO_SIM, ws.UNKNOWN_ERROR):
        assert ws.window_of({"status": failed, "t_w_ns": 5}) == (None, None)        # a later start reads again
    assert ws.window_of(None) == (None, None) and ws.sim_key(None) is None and ws.sim_key("a b") == "a_b"


# ---- 6. the agent --------------------------------------------------------------------------------------------------------

def _with21(fn):
    g = dict(fn.__globals__)
    g.update(EXTRA)
    f = types.FunctionType(fn.__code__, g, fn.__name__, fn.__defaults__, fn.__closure__)
    f.__kwdefaults__ = fn.__kwdefaults__
    return f


def _agent(*, used, inv=None, pace=None, on=True, out_dir=None, launch=False):
    agent = t619._agent(inv=inv, used=used, mature=False)              # v6.19 underneath; its mirror young (quiet)
    cls = type(agent)
    methods = {n: _with21(getattr(cls, n)) for n in ("_v63_pass", "_v633_book", "_v617_touch", "_v617_book")}
    base = dict(getattr(cls, "_v63_pass").__globals__)
    base.update(EXTRA)
    for n in t620.METHODS + METHODS:
        methods[n] = t619._method(n, base)
    reading = {"used": float(used)}
    methods["_research_book_traded_volume"] = lambda self, book_id: float(reading["used"])
    agent.__class__ = type("P621", (cls,), methods)
    agent.reading = reading
    agent.research_v620_room_pace = True
    agent._v620_counts, agent._v620_errors, agent._v620_scale_sum = {}, 0, 0.0
    for name in SWITCHES:
        setattr(agent, name, on)
    agent._v621_counts, agent._v621_errors, agent._v621_prepared = {}, 0, False
    agent._v621_restore_payload, agent._v621_window = None, {}
    agent.uid = UID
    agent.research_output_dir = str(out_dir) if out_dir is not None else str(ROOT / "tests" / "no-such-v621-log-dir")
    agent._research_output_file = None
    agent.research_v615_launch = launch
    if pace is None:
        pace = yw.YoungWindowLine(launch=launch) if on else rp.RoomPaceLine(launch=launch)
    agent._v66_pace = pace
    return agent


def _run(agent, ts=NOW):
    resp = t63._Resp()
    state = t616._state({3: t63._book()})
    state.timestamp = int(ts)
    agent._v63_pass(resp, state, {})
    return resp


def _q(resp):
    return t620._quantities(resp)


def _history(pace, *events):
    for ts, used in events:
        pace.observe(3, ts, used)
    return pace


def test_without_the_switches_and_without_a_known_window_start_the_pass_is_v620_exactly():
    for used, inv in ((t620.U0, None), (t620.U0, 3.0), (t620._line() + BAND / 2, 3.0), (t620._line() + 2 * BAND, -3.0)):
        hist = ((NOW - 120 * S, t620.U0), (NOW - 60 * S, t620.U0))
        ref = t620._agent(used=used, inv=inv)
        off = _agent(used=used, inv=inv, on=False, pace=_history(rp.RoomPaceLine(launch=False), *hist))
        on = _agent(used=used, inv=inv, pace=_history(yw.YoungWindowLine(launch=False), *hist))
        want = t63._placed(t617._run(ref))
        assert t63._placed(_run(off)) == want and t63._placed(_run(on)) == want, (used, inv)
        assert off._v621_counts == {} and on._v621_errors == 0 and off._v621_errors == 0
        assert on._v621_counts == {"restore_none": 1, "window_unknown_no_dir": 1}       # nothing saved, no logs: unknown
        assert on._v66_pace.window_status is None                                     # a later start reads again


def test_in_the_pass_the_validators_rounding_does_not_age_a_fresh_book_and_v620_does():
    t_w = NOW - 3_000 * S
    young = _history(yw.YoungWindowLine(launch=False), (t_w, 0.0), (NOW - 60 * S, 20_000.0))
    old = _history(rp.RoomPaceLine(launch=False), (t_w, 0.0), (NOW - 60 * S, 20_000.0))
    mine, ref = _agent(used=20_000.0 - 5e-5, pace=young), _agent(used=20_000.0 - 5e-5, pace=old, on=False)
    _run(mine)
    _run(ref)
    assert young.young(3) and not old.young(3)
    assert young.falls[yw.FALL_NOISE] == 1 and young.noise_max < 1e-4 and mine._v621_errors == 0


def test_a_book_the_state_carries_no_account_for_is_no_reading():
    """Review 10-08: the venue's reader returns 0 for a missing account; v6.21 must not take it as an empty window (a
    window start, a launch line) -- v6.20 itself took a zero as no reading."""
    pace = yw.YoungWindowLine(launch=False)
    agent = _agent(used=0.0, pace=pace)
    agent.accounts = {}                                  # the state carries no account for book 3
    _run(agent)
    assert 3 not in pace.start and pace.window_start_ns(3) is None
    assert agent._v621_counts.get("no_account_states") == 1 and agent._v621_errors == 0
    assert pace.window_status != yw.WINDOW_OWN_EMPTY_START
    pace2 = yw.YoungWindowLine(launch=False)
    agent2 = _agent(used=0.0, pace=pace2)
    agent2.accounts = {3: object()}                      # an account reading 0: a genuinely empty window
    _run(agent2)
    assert pace2.window_start_ns(3) == NOW and "no_account_states" not in agent2._v621_counts
    assert pace2.window_status == yw.WINDOW_OWN_EMPTY_START


def test_a_young_book_with_a_known_window_start_starts_its_even_line_without_a_burst():
    t_w = NOW + 67_800 * S - (P - PW)                    # 67,800 sim-s to its roll-off
    pace = yw.YoungWindowLine(launch=False)
    pace.set_window(t_w, ws.KNOWN)
    agent = _agent(used=0.7 * CAP, inv=3.0, pace=pace)
    resp = _run(agent)
    roll = yw.earliest_rolloff(t_w)
    ua = yw.no_burst_anchor(CAP, 0.7 * CAP, NOW, roll)
    f = yw.band_scale(0.7 * CAP, *yw.even_line(CAP, NOW, ua, roll, NOW))
    assert 0.35 < f < 0.37
    assert _q(resp) == [("SELL", lot_quantity(2.0 * f, 4))]   # long: the offer reduces at the line's rate, the bid waits
    assert agent._v620_counts == {"scaled_book_states": 1} and agent._v621_errors == 0
    assert agent._v621_counts == {"restore_none": 1}           # a known window start: nothing to derive
    ref = _agent(used=0.7 * CAP, inv=3.0, on=False)            # v6.20 anchors a restarted book at its usage: the burst
    full = _q(_run(ref))
    assert ("SELL", 2.0) in full and {q for _d, q in full} == {2.0}


def test_the_first_request_restores_the_saved_state_and_a_restart_trades_like_no_restart():
    t_w = NOW + 67_800 * S - (P - PW)
    a_pace = yw.YoungWindowLine(launch=False)
    a_pace.set_window(t_w, ws.KNOWN)
    a = _agent(used=0.7 * CAP, inv=3.0, pace=a_pace)
    _run(a)
    saved = json.loads(json.dumps(a._v621_session_state()))
    assert saved["uid"] == UID and saved["sim_id"] == SIM and saved["books"]["3"]["even"][0] == yw.EVEN_ANCHORED
    for ag in (a,):
        ag.reading["used"] = 0.7 * CAP + 3_000.0
    q_continuous = _q(_run(a, NOW + 60 * S))
    b = _agent(used=0.7 * CAP + 3_000.0, inv=3.0)              # a new process: an empty line, the saved state stashed
    b._v621_restore_payload = saved
    q_restored = _q(_run(b, NOW + 60 * S))
    assert q_restored == q_continuous and b._v621_counts == {"restore_restored": 1}
    assert b._v66_pace.even[3] == a_pace.even[3] and b._v66_pace.window_status == ws.KNOWN
    c = _agent(used=0.7 * CAP + 3_000.0, inv=3.0)              # no saved state, its start known: the no-burst start again
    c._v66_pace.set_window(t_w, ws.KNOWN)
    assert _q(_run(c, NOW + 60 * S)) != q_continuous


def test_without_a_saved_state_the_window_start_comes_from_the_uids_own_logs(tmp_path):
    _log(tmp_path, UID, "20261007_162337", wall=W0, first_ts=NOW - 1_000 * S, books=1, launch=1)   # the harness: one book
    own = _log(tmp_path, UID, "20261008_080000", wall=W0 + 9 * 3600 * S, first_ts=NOW - 500 * S, books=1, launch=1)
    agent = _agent(used=0.1 * CAP, inv=3.0, out_dir=tmp_path)            # its own log (here a later empty start) is skipped
    agent._research_output_file = str(own)
    _run(agent)
    pace = agent._v66_pace
    assert pace.uid_window_start == NOW - 1_000 * S and pace.window_status == ws.KNOWN
    assert agent._v621_window["status"] == ws.KNOWN and agent._v621_window["files"] == 1
    assert agent._v621_counts == {"restore_none": 1, "window_known": 1}
    assert pace.even[3] == (yw.EVEN_LAUNCH,)                   # within one band of its launch line from t_w
    events = [p for (kind, p) in agent.emitted if kind == "V621_WINDOW_START"]
    assert len(events) == 1 and events[0]["t_w_ns"] == NOW - 1_000 * S and events[0]["sim_id"] == SIM
    _run(agent, NOW + S)                                        # once per process
    assert agent._v621_counts == {"restore_none": 1, "window_known": 1}


def test_a_first_state_reading_every_book_empty_is_its_own_window_start_and_derives_nothing():
    agent = _agent(used=0.0)
    _run(agent)
    pace = agent._v66_pace
    assert pace.window_status == yw.WINDOW_OWN_EMPTY_START and pace.window_start_ns(3) == NOW
    assert agent._v621_counts == {"restore_none": 1, "window_own_empty_start": 1}
    assert pace.snapshot()["room"]["first_seen"] == 1 and pace.snapshot()["room"]["first_empty"] == 1


def test_the_session_file_carries_the_line_and_a_save_before_the_first_request_keeps_the_saved_state():
    agent = _agent(used=0.7 * CAP, inv=3.0)
    stash = {"version": yw.V621_YOUNG_WINDOW_VERSION, "uid": UID, "sim_id": SIM, "books": {}}
    agent._v621_restore_payload = stash
    assert agent._v621_session_state() is stash                # before the first request: what it has not applied yet
    agent._v621_restore_payload = None
    assert agent._v621_session_state() is None
    _run(agent)
    state = agent._v621_session_state()
    assert state["version"] == yw.V621_YOUNG_WINDOW_VERSION and set(state["books"]) == {"3"}
    agent._v66_pace = yw.YoungWindowLine()
    agent._v66_pace.start = None                               # a broken line: counted, nothing saved
    assert agent._v621_session_state() is None and agent._v621_errors == 1


def _session_host():
    """The real _research_save_session / _research_read_session / _v621_session_state, compiled in a class whose base
    stands in for Strategy1_Research (a zero-argument super() needs the class cell)."""
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
    body = "class _Host(_Base):\n" + "\n".join(textwrap.indent(t619._src(n), "    ") for n in (
        "_research_save_session", "_research_read_session", "_v621_session_state")) + "\n"
    exec(compile(body, "<v621-session-host>", "exec"), scope)
    return scope["_Host"]


def _host(path, *, on=True, pace=None, prepared=True):
    h = _session_host()()
    h.path, h.uid, h._tick = str(path), UID, 100
    h._research_session_identity = types.SimpleNamespace(simulation_id=SIM)
    h._v503_gate_session_state = lambda: {}
    h._v61_on = lambda: False
    h._v61_lots_from_session = lambda raw: None
    h._v504_mirror, h._v633_deep = None, None
    h.research_v621_pace_persist = on
    h._v621_errors, h._v621_prepared, h._v621_restore_payload, h._v66_pace = 0, prepared, None, pace
    return h


def test_the_session_save_writes_every_books_line_and_a_new_process_reads_it_back_until_its_first_request(tmp_path):
    first = _host(tmp_path / "a.json", pace=_busy(17_744 * S))
    first._research_save_session(force=True)
    disk = json.loads((tmp_path / "a.json").read_text())
    assert disk[yw.PACE_SESSION_KEY] == json.loads(json.dumps(yw.pace_state(first._v66_pace, uid=UID)))
    assert disk["v504_session_owner"] == {"uid": UID} and disk["schema"] == 1
    second = _host(tmp_path / "b.json", prepared=False)
    second._v504_read_session = lambda identity: disk
    assert second._research_read_session(None) is disk
    assert second._v621_restore_payload == disk[yw.PACE_SESSION_KEY]
    second._research_save_session(force=True)                  # a save before its first request keeps the saved line
    assert json.loads((tmp_path / "b.json").read_text())[yw.PACE_SESSION_KEY] == disk[yw.PACE_SESSION_KEY]
    second._v621_prepared = True                               # read again after the first request: nothing stashed
    second._v621_restore_payload = None
    second._research_read_session(None)
    assert second._v621_restore_payload is None
    off = _host(tmp_path / "c.json", on=False, pace=_busy(17_744 * S), prepared=False)
    off._v504_read_session = lambda identity: disk
    off._research_read_session(None)
    off._research_save_session(force=True)
    assert off._v621_restore_payload is None and yw.PACE_SESSION_KEY not in json.loads((tmp_path / "c.json").read_text())


def test_a_failing_first_request_counts_an_error_and_never_raises():
    agent = _agent(used=0.7 * CAP)

    class _Line(yw.YoungWindowLine):
        def set_window(self, *a, **k):
            raise RuntimeError("boom")

    agent._v66_pace = _Line()
    _run(agent)
    assert agent._v621_errors == 1 and agent._v621_prepared


def test_the_snapshot_reports_the_line_the_window_and_the_counts():
    agent = _agent(used=0.0)
    _run(agent)
    snap = agent._v621_snapshot()
    assert snap["version"] == "young_window_v6_21" and snap["errors"] == 0
    assert snap["window"] == {"status": yw.WINDOW_OWN_EMPTY_START}
    line = snap["line"]
    assert line["falls"] == {"noise": 0, "rewind": 0, "guarded": 0, "rolloff": 0} and line["window_known"] == 1
    assert line["even_launch"] == 1 and line["even_anchored"] == 0 and line["guard_on"] == 1 and line["even_on"] == 1
    for key in SWITCHES:
        assert f'{key.replace("research_v621_", "")}_on=int(bool(getattr(self, "{key}", False))),' in SIMPLE
    assert ('young_window=(self._v621_snapshot() if getattr(self, "research_v621_rolloff_guard", None) is not None '
            'else {}),') in SIMPLE


def test_the_pace_line_becomes_the_young_window_line_only_with_a_switch_on():
    src_globals = {"V68RollingPaceLine": RollingPaceLine, "V66PaceLine": PaceLine, "V615LaunchPaceLine": LaunchPaceLine,
                   "V620RoomPaceLine": rp.RoomPaceLine}
    fn = t619._method("_v66_pace_ref", dict(src_globals, **EXTRA))
    for switches, want in (((False, False, False), rp.RoomPaceLine), ((True, False, False), yw.YoungWindowLine),
                           ((False, True, False), yw.YoungWindowLine), ((False, False, True), yw.YoungWindowLine)):
        o = types.SimpleNamespace(_v66_pace=None, research_v68_rolling_budget=True, research_v615_launch=True,
                                  research_v620_room_pace=True, **dict(zip(SWITCHES, switches)))
        pace = types.MethodType(fn, o)()
        assert type(pace) is want, switches
        if want is yw.YoungWindowLine:
            assert (pace.guard_on, pace.even_on) == (switches[0], switches[2]) and pace.launch_on
    o = types.SimpleNamespace(_v66_pace=None, research_v68_rolling_budget=True, research_v620_room_pace=False,
                              **dict(zip(SWITCHES, (True, True, True))))
    assert type(types.MethodType(fn, o)()) is RollingPaceLine                      # without v6.20's line: nothing to wrap


# ---- 7. wiring and the launcher ------------------------------------------------------------------------------------------

def test_the_switches_ship_on_and_agree_with_the_agent_default():
    params = t617._params()
    for key in SWITCHES:
        assert f"{key}=1" in params and f"{key}=0" not in params, key
        assert f'self.{key} = self._as_bool(getattr(self.config, "{key}", True))' in SIMPLE, key
    for key in ("research_v620_room_pace=1", "research_v641_volume_pace=1", "research_v66_pace_line=1",
                "research_v68_rolling_budget=1"):
        assert key in params, key


def test_the_young_window_line_is_installed_prepared_fed_saved_and_restored_once_each():
    for line in (INSTALL_LINE, PASS_LINE, OBSERVE_LINE, READ_LINE, SAVE_LINE):
        assert SIMPLE.count(line) == 1, line
    assert SIMPLE.count("from research_v621_young_window import (") == 1
    assert SIMPLE.count("from research_v621_window_start import (") == 1
    assert SIMPLE.index(INSTALL_LINE) > SIMPLE.index("pace = V620RoomPaceLine(launch=isinstance(pace, V615LaunchPaceLine))")
    pass_body = t619._src("_v63_pass")
    assert pass_body.index(PASS_LINE) < pass_body.index(OBSERVE_LINE) < pass_body.index(
        "paced = bool(pace.paced(book_id, now_ts, used, pace_cap, pace_duration))")
    assert pass_body.count("pace.observe(book_id, now_ts, used)") == 1               # the v6.20 call without the switches
    save = t619._src("_research_save_session")
    assert save.index("payload[V68_DEEP_SESSION_KEY]") < save.index(SAVE_LINE)
    read = t619._src("_research_read_session")
    assert read.index("raw.get(V68_DEEP_SESSION_KEY)") < read.index(READ_LINE)


def test_the_v621_block_is_gated_preflighted_and_listed():
    assert "; V6_18_BUILD=0; V6_19_BUILD=0; V6_20_BUILD=0; V6_21_BUILD=0; V6_22_BUILD=0; V6_23_BUILD=0; V6_24_BUILD=0; V6_25_BUILD=0\n" in LAUNCHER
    arm = next(line for line in LAUNCHER.splitlines() if line.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_19_BUILD=1; V6_20_BUILD=1; V6_21_BUILD=1; V6_22_BUILD=1; V6_23_BUILD=1; V6_24_BUILD=1; V6_25_BUILD=1"), arm
    assert 'if [[ "${V6_21_BUILD:-0}" == "1" ]]; then' in LAUNCHER
    assert "tests/test_research_v6_21.py" in LAUNCHER
    assert LAUNCHER.index('echo "[preflight] v6.20 room pace PASS') < LAUNCHER.index("# v6.21 S1-S3.")


def test_every_v621_preflight_pattern_is_in_the_file_it_checks():
    import re
    start = LAUNCHER.index('if [[ "${V6_21_BUILD:-0}" == "1" ]]; then')
    block = LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.21 young window PASS', start)]
    pats = re.findall(r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\"", block)
    assert len(pats) == 11, pats
    for pat, name in pats:
        assert pat in (STRATEGY / name).read_text(), (pat, name)


def _run_block(params, agent_path=STRATEGY):
    start = LAUNCHER.index("# v6.21 S1-S3.")
    stop = LAUNCHER.index('echo "[preflight] v6.21 young window PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_21_BUILD": "1"}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_the_v621_block_passes_the_shipped_params_and_refuses_any_switch_it_needs_off():
    shipped = t617._shipped()
    out = _run_block(shipped)
    assert out.returncode == 0 and "[preflight] v6.21 young window PASS" in out.stdout, out.stderr
    for key in SWITCHES + ("research_v620_room_pace", "research_v641_volume_pace", "research_v66_pace_line",
                           "research_v68_rolling_budget"):
        assert shipped.count(f"{key}=1") == 1, key
        for bad in (f"{key}=0", f"{key}=10", ""):
            out = _run_block(shipped.replace(f"{key}=1", bad))
            assert out.returncode == 1 and f"v6.21 S1-S3 build without {key}=1" in out.stderr, (key, bad)


def test_the_v621_block_refuses_an_agent_without_each_line_it_checks():
    import re
    start = LAUNCHER.index('if [[ "${V6_21_BUILD:-0}" == "1" ]]; then')
    block = LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.21 young window PASS', start)]
    cases = re.findall(r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\" \|\| \{\n\s+echo \"ERROR: ([^\"]+)\" >&2", block)
    assert len(cases) == 11, cases
    names = ("Strategy1_Research_Simple.py", "research_v621_young_window.py", "research_v621_window_start.py")
    for needle, name, message in cases:
        tmp = Path(tempfile.mkdtemp(prefix="t621_"))
        try:
            for n in names:
                shutil.copy(STRATEGY / n, tmp / n)
            text = (tmp / name).read_text()
            assert text.count(needle) == 1, needle
            (tmp / name).write_text(text.replace(needle, "# removed"))
            out = _run_block(t617._shipped(), agent_path=tmp)
            assert out.returncode == 1 and message in out.stderr, (name, needle, out.stderr)
        finally:
            shutil.rmtree(tmp)
