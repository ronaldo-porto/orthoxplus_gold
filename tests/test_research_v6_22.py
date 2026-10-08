"""v6.22: S1 pacing within one making window of the cap, S2 an unknown window's books aged, S3 no new order at the cap --
research_v622_aged_cap.

v6.21 = v6.20 + the young window.  An aged book follows the v6.8 rolling line, which reaches the cap and then never paces
it while roll-off frees room.  S1: a book on that line (aged) whose room under the cap is below one making window of it
is paced -- v6.4.1 holds its adding orders, v6.10 gives it v6.9's bound, v6.9 S3 keeps only its deepest level and the
v6.17 touch quote places no adding quote.  S2: on a UID whose window started before its own logs (v6.21
UNKNOWN_NO_EMPTY_START) every book without a window start of its own is aged from its first report; the aged marks ride
in the session file.  S3: with v6.6 S4 on, a book at its cap keeps every cancel and builds no new order -- the final
validator would drop each one -- unless the touch quote's cancel could then see a different budget.  Without the
switches the build is v6.21.
"""
import copy
import json
import os
import re
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
import research_v622_aged_cap as ac  # noqa: E402
import research_v641_pace as vp  # noqa: E402
from research_v615_launch import LaunchPaceLine  # noqa: E402
from research_v66_pace_line import PaceLine  # noqa: E402
from research_v68_deep_owns import RollingPaceLine  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_16 as t616  # noqa: E402
import test_research_v6_17 as t617  # noqa: E402
import test_research_v6_19 as t619  # noqa: E402
import test_research_v6_21 as t621  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000
NOW = t621.NOW
CAP = 500_000.0
BAND = 62_500.0                  # the cap's making-window share: 500,000 x 10,800 / 86,400
FLOOR = t621.FLOOR
SIM = t621.SIM
UID = t621.UID
SWITCHES = ("research_v622_cap_window_pace", "research_v622_unknown_aged", "research_v622_cap_skip")
S1_LINE = "paced = self._v6_22_cap_window(pace, book_id, pace_cap, used, v6_22_room)"
S2_LINE = "self._v6_22_age_unknown(pace, book_id)"
S3_LINE = "cap_skip = bool(v6_22_skip and self._v6_22_cap_skip(state, response, book_id, budget))"
DEEP_SKIP = "return 0                   # v6.22 S3: at its cap the book's cancels went out"
TOUCH_SKIP = "return 0                   # v6.22 S3: at its cap the quote's cancel went out"
EXTRA = dict(t621.EXTRA)
EXTRA.update({
    "V622_AGED_CAP_VERSION": ac.V622_AGED_CAP_VERSION, "v622_ages_unknown": ac.ages_unknown, "v622_at_cap": ac.at_cap,
    "v622_cap_window_paced": ac.cap_window_paced, "v622_skip_keeps_budget": ac.skip_keeps_budget,
})
METHODS = ("_v6_22_count", "_v6_22_cap_window", "_v6_22_age_unknown", "_v6_22_cap_skip", "_v6_22_snapshot")
U_BOUND = 476_000.0              # room 24,000 < BAND: within one making window of the cap
U_FREE = 300_000.0               # room 200,000: not


# ---- 1. S1: pacing within one making window of the cap ------------------------------------------------------------

def _aged(u0, cls=yw.YoungWindowLine, since=120, **kw):
    """A line whose book 3, first seen ``since`` sim-s ago, fell by a whole fill (no window start known): aged, on the
    v6.15 / v6.8 line -- which has reached the cap when ``since`` is long enough."""
    line = cls(launch=False, **kw)
    floor = (FLOOR,) if isinstance(line, yw.YoungWindowLine) else ()     # v6.20's line takes no fall floor
    line.observe(3, NOW - since * S, u0 + 1_000.0, *floor)
    line.observe(3, NOW - 60 * S, u0, *floor)
    assert not line.young(3)
    return line


def _seen(cls, u0, **kw):
    line = cls(**kw)
    line.observe(3, NOW - 60 * S, u0)
    return line


def test_the_cap_window_is_v619s_cap_bound_threshold():
    line = _aged(U_FREE)
    paced = lambda used, cap=CAP: ac.cap_window_paced(line, 3, cap, used, room_on=True)
    assert not paced(CAP - BAND)                         # room exactly one making window: not yet
    assert paced(CAP - BAND + 0.01) and paced(U_BOUND) and paced(CAP) and paced(CAP + 10.0)   # at or over the cap too
    assert not paced(U_FREE)
    for cap, used in ((0.0, U_BOUND), (None, U_BOUND), (CAP, None), (CAP, float("nan")), ("x", U_BOUND)):
        assert not paced(used, cap), (cap, used)


def test_s1_the_books_on_the_rolling_line_are_the_aged_ones():
    young = _seen(yw.YoungWindowLine, U_BOUND, launch=False)
    assert young.young(3) and not ac.on_rolling_line(young, 3, room_on=True)
    assert ac.on_rolling_line(_aged(U_BOUND), 3, room_on=True)
    assert ac.on_rolling_line(_aged(U_BOUND, rp.RoomPaceLine), 3, room_on=True)        # v6.20's line: the same test
    assert not ac.on_rolling_line(_aged(U_BOUND), 5, room_on=True)                      # a book without a line yet
    for cls in (RollingPaceLine, LaunchPaceLine):                                       # no young(): v6.20 off only
        line = _seen(cls, U_BOUND)
        assert ac.on_rolling_line(line, 3, room_on=False) and not ac.on_rolling_line(line, 3, room_on=True), cls
        assert ac.cap_window_paced(line, 3, CAP, U_BOUND, room_on=False)
    v66 = _seen(PaceLine, U_BOUND)                                                      # v6.6's line: not the v6.8 line
    v641 = vp.VolumePace()
    v641.observe(3, NOW, U_BOUND)
    for other in (v66, v641, None, object()):
        assert not ac.cap_window_paced(other, 3, CAP, U_BOUND, room_on=False), other


# ---- 2. S2: the books of a window that started before the logs ----------------------------------------------------

def _rolled(line, book=5, ts=NOW - 60 * S):
    """Book ``book`` of the line's UID rolls off: a whole fill falls on a clock that moved on (the guard's test)."""
    line.observe(book, ts - 60 * S, U_FREE + 1_000.0, FLOOR)
    line.observe(book, ts, U_FREE, FLOOR)
    return line


def test_s2_only_an_unknown_window_seen_to_roll_ages_and_only_a_book_without_a_start_of_its_own():
    def line(status, *, first=U_FREE, start=None, aged=False, rolls=True, guard=True):
        out = yw.YoungWindowLine(launch=False, guard=guard)
        out.set_window(start, status)
        if rolls:
            _rolled(out)                                                                 # book 5 rolled off
        out.observe(3, NOW, first, FLOOR)
        if aged:
            out.aged.add(3)
        return out

    assert ac.window_rolls(line(ws.UNKNOWN_NO_EMPTY_START)) and ac.ages_unknown(line(ws.UNKNOWN_NO_EMPTY_START), 3)
    assert not ac.ages_unknown(line(ws.UNKNOWN_NO_EMPTY_START, rolls=False), 3)         # not seen to roll: no proof
    no_guard = line(ws.UNKNOWN_NO_EMPTY_START, guard=False)                              # v6.20's fall test (noise ages)
    assert not no_guard.young(5) and not ac.window_rolls(no_guard) and not ac.ages_unknown(no_guard, 3)
    assert not ac.ages_unknown(line(ws.UNKNOWN_NO_EMPTY_START), 7)                      # no line yet
    assert not ac.ages_unknown(line(ws.UNKNOWN_NO_EMPTY_START, aged=True), 3)           # aged already
    assert not ac.ages_unknown(line(ws.UNKNOWN_NO_EMPTY_START, first=0.0), 3)           # first seen empty: its own start
    for status in (None, ws.KNOWN, ws.KNOWN_CARRIED, ws.KNOWN_CARRIED_ROLLING, ws.UNKNOWN_NO_READABLE_START,
                   ws.UNKNOWN_GAP_AFTER_ANCHOR, ws.UNKNOWN_CARRY_UNVERIFIED, yw.WINDOW_OWN_EMPTY_START):
        start = 37_320 * S if status in (ws.KNOWN, ws.KNOWN_CARRIED) else None
        assert not ac.ages_unknown(line(status, start=start), 3), status
    room = rp.RoomPaceLine(launch=False)                                                 # no window status at all
    room.observe(3, NOW, U_FREE)
    assert not ac.ages_unknown(room, 3) and not ac.ages_unknown(None, 3) and not ac.window_rolls(None)


def test_s2_a_mislabelled_young_window_never_shows_a_roll_off_so_it_is_never_aged():
    """Review 10-09: v6.21 also reports UNKNOWN_NO_EMPTY_START when it cannot read a launch -- a first report that saw
    fewer books than the state counts as VOLUME, and an unreadable head before a VOLUME head leaves no empty start."""
    partial = {"kind": None, "header": True, "v62_tick": 1, "first_ts": 1, "sim": SIM, "books": 120, "empty": 120}
    assert ws.classify(partial, SIM, 128) == "VOLUME"
    status, _anchor = ws._anchor([{"kind": "UNREADABLE"}, {"kind": "VOLUME"}], SIM)
    assert status == ws.UNKNOWN_NO_EMPTY_START
    line = _status_line(ws.UNKNOWN_NO_EMPTY_START)                 # such a young window: launched at NOW - 3,000 s
    line.observe(3, NOW - 3_000 * S, 0.0, FLOOR)
    line.observe(5, NOW - 3_000 * S, 0.0, FLOOR)
    for i in range(1, 30):                                         # it trades; the validator's rounding wobbles
        ts = NOW - 3_000 * S + i * 60 * S
        line.observe(3, ts, 900.0 * i, FLOOR)
        line.observe(5, ts, 1_000.0 * i, FLOOR)
        line.observe(5, ts + S, 1_000.0 * i - 5e-5, FLOOR)        # a reading between the validator's two passes
    assert line.falls[yw.FALL_NOISE] == 29 and line.falls[yw.FALL_ROLLOFF] == 0
    line.observe(7, NOW, 40_000.0, FLOOR)                          # a book first seen with volume after a restart
    assert not ac.window_rolls(line) and not ac.ages_unknown(line, 7) and line.young(7)


# ---- 3. S3: no new order at the cap ---------------------------------------------------------------------------------

def test_s3_at_the_cap_is_the_final_validators_drop_condition():
    assert ac.at_cap(0.0, True) and ac.at_cap(-0.1, True)
    assert not ac.at_cap(1e-12, True) and not ac.at_cap(0.5, True)
    assert not ac.at_cap(0.0, False) and not ac.at_cap(0.0, None)                       # v6.6 S4 off: reducing ones pass
    for bad in (None, float("nan"), "x"):
        assert not ac.at_cap(bad, True), bad                                             # no reading is room


def test_s3_the_skip_keeps_the_budget_the_touch_quotes_cancel_sees():
    assert ac.DEEP_MAX_INSTRUCTIONS == 3
    assert ac.skip_keeps_budget(0, 5) and ac.skip_keeps_budget(1, 5) and ac.skip_keeps_budget(0, 4)
    assert not ac.skip_keeps_budget(2, 5) and not ac.skip_keeps_budget(3, 5) and not ac.skip_keeps_budget(0, 3)
    for bad in ((None, 5), (1, None), ("x", 5), (float("inf"), 5)):
        assert not ac.skip_keeps_budget(*bad), bad


def test_s3_the_deep_layer_issues_at_most_one_cancel_instruction_and_one_new_order_per_side():
    """DEEP_MAX_INSTRUCTIONS is the deep layer's structure: one cancel_orders call, and in the placement loop one
    limit_order call followed by a break out of the side's levels."""
    deep = _src("_v633_book")
    assert deep.count("response.cancel_orders(") == 1 and deep.count("response.limit_order(") == 1
    assert "break                   # one new order per book side per request" in deep
    assert deep.index("response.cancel_orders(") < deep.index(DEEP_SKIP) < deep.index("response.limit_order(")
    touch = _src("_v617_touch")
    assert touch.count("self._v617_cancel(") == 1 and touch.count("response.limit_order(") == 1
    assert touch.index("self._v617_cancel(") < touch.index(TOUCH_SKIP) < touch.index("response.limit_order(")


def test_s3_the_final_validator_stays_the_backstop_and_drops_what_s3_leaves_out():
    final = _src("_research_final_validate_instructions")           # the definition Python binds (the last one)
    assert "headroom = float(self._research_volume_cap_headroom(state, book_id))" in final
    assert 'if risk_reducing_batch and headroom <= 0.0 and bool(getattr(self, "research_v66_cap_closed", False)):' in final
    assert 'if headroom <= 0.0:\n                    reason = "VOLUME_CAP"' in final
    skip = _src("_v6_22_cap_skip")
    assert "headroom = float(self._research_volume_cap_headroom(state, book_id))" in skip
    assert 'v622_at_cap(headroom, getattr(self, "research_v66_cap_closed", False))' in skip


# ---- 4. the agent: a copy of one v6.21 harness agent per test --------------------------------------------------------

def _with22(fn):
    g = dict(fn.__globals__)
    g.update(EXTRA)
    f = types.FunctionType(fn.__code__, g, fn.__name__, fn.__defaults__, fn.__closure__)
    f.__kwdefaults__ = fn.__kwdefaults__
    return f


_TEMPLATE: list = []


def _template():
    """One v6.21 harness agent (t621._agent, ~14 s to build) with the v6.22 methods; it never runs a pass, so a deep
    copy of it is a fresh agent.  Its reading, inventory and cap headroom come from the copy's own attributes."""
    if not _TEMPLATE:
        agent = t621._agent(used=0.0)
        cls = type(agent)
        methods = {n: _with22(getattr(cls, n)) for n in ("_v63_pass", "_v633_book", "_v617_touch", "_v617_book")}
        base = dict(getattr(cls, "_v63_pass").__globals__)
        base.update(EXTRA)
        for n in METHODS:
            methods[n] = t619._method(n, base)
        methods["_research_book_traded_volume"] = lambda self, book_id: float(self.reading["used"])
        methods["_research_volume_cap_headroom"] = (
            lambda self, state, book_id: max(0.0, CAP - float(self.reading["used"])) / CAP)
        methods["_a195_venue_net_by_book"] = (
            lambda self, books: {int(b): float(self.inv22) for b in books} if self.inv22 is not None else dict(self.venue))
        agent.__class__ = type("P622", (cls,), methods)
        _TEMPLATE.append(agent)
    return _TEMPLATE[0]


def _agent(*, used, inv=None, pace=None, s1=True, s2=True, s3=True, closed=True, out_dir=None):
    agent = copy.deepcopy(_template())
    agent.reading, agent.inv22 = {"used": float(used)}, inv
    agent._v66_pace = pace if pace is not None else yw.YoungWindowLine(launch=False)
    agent.research_v622_cap_window_pace, agent.research_v622_unknown_aged, agent.research_v622_cap_skip = s1, s2, s3
    agent.research_v66_cap_closed = closed
    agent._v6_22_counts, agent._v6_22_errors = {}, 0
    if out_dir is not None:
        agent.research_output_dir = str(out_dir)
    return agent


def _run(agent, ts=NOW, pre=0):
    """One request; ``pre`` instructions on book 3 already in the response (an earlier pass's cancels)."""
    resp = t63._Resp()
    for i in range(pre):
        resp.cancel_orders(book_id=3, order_ids=[900 + i])
    state = t616._state({3: t63._book()})
    state.timestamp = int(ts)
    agent._v63_pass(resp, state, {})
    return resp


def _out(resp):
    return sorted(t63._placed(resp)), sorted(t63._cancelled(resp))


def _rest_both(agent):
    t63.t14._rest(agent, 21, 3, 0, 99.80, cid=40031)            # level 0 at its price: the deep layer places nothing
    t63.t14._rest(agent, 22, 3, 1, 100.23, cid=40032)


def _rest_doomed(agent):
    t63.t14._rest(agent, 31, 3, 0, 99.50, cid=40031)            # level 0 far from its depth: repriced (cancelled)
    t63.t14._rest(agent, 32, 3, 1, 100.03, cid=43032)           # a touch offer while the fundamental is above: flipped


def test_with_the_switches_off_the_pass_is_v621_exactly():
    cases = (("aged, cap-bound, long", dict(used=U_BOUND + 500.0, inv=3.0), lambda: _aged(U_BOUND), None),
             ("unknown window seen to roll, cap-bound", dict(used=U_BOUND, inv=3.0), lambda: _unknown(), None),
             ("at the cap", dict(used=CAP), lambda: None, _rest_doomed))
    for name, kw, pace, rest in cases:
        ref = t621._agent(pace=pace(), **kw)                      # the v6.21 harness: no v6.22 attribute at all
        off = _agent(pace=pace(), s1=False, s2=False, s3=False, **kw)
        on = _agent(pace=pace(), **kw)
        for a in (ref, off, on):
            if rest is not None:
                rest(a)
        want = _out(t621._run(ref))
        assert _out(_run(off)) == want, name
        assert off._v6_22_counts == {} and off._v6_22_errors == 0, name
        assert _out(_run(on)) != want, name                       # each case is one a switch changes


def _status_line(status, start=None):
    line = yw.YoungWindowLine(launch=False)
    line.set_window(start, status)
    return line


def _unknown(rolls=True):
    """A v6.21 line on a UID whose window started before its own logs; with ``rolls`` its book 5 has rolled off."""
    line = _status_line(ws.UNKNOWN_NO_EMPTY_START)
    return _rolled(line) if rolls else line


# ---- 5. S1 in the pass --------------------------------------------------------------------------------------------------

def test_s1_an_aged_book_within_one_making_window_of_its_cap_holds_its_adds():
    used = U_BOUND + 500.0                                         # under its rolling line: the line does not pace it
    flat_off, flat = _agent(used=used, pace=_aged(U_BOUND), s1=False), _agent(used=used, pace=_aged(U_BOUND))
    assert _out(_run(flat_off)) == ([(3, "BUY", 40031, 99.8, 2.0), (3, "SELL", 40032, 100.23, 2.0)], [])
    assert _out(_run(flat)) == ([], [])                            # flat: both sides add, both held
    assert flat._v6_22_counts == {"cap_window_paced_states": 1} and flat._v641_counts == {"paced_book_states": 1}
    assert 3 in flat._v66_pace.paced_now and flat._v617_counts.get("paced_states") == 1
    long_ = _agent(used=used, inv=3.0, pace=_aged(U_BOUND))
    assert _out(_run(long_)) == ([(3, "SELL", 40032, 100.23, 2.0)], [])   # the reducing side, at its full size
    short = _agent(used=used, inv=-3.0, pace=_aged(U_BOUND))
    assert _out(_run(short)) == ([(3, "BUY", 40031, 99.8, 2.0)], [])
    assert long_._v620_counts == {} and short._v620_counts == {}   # an aged book's size factor stays 1
    saturated = _aged(U_BOUND, since=50_000)                       # first seen long ago: its line is the cap now
    assert saturated.line(3, NOW, CAP) == CAP and not saturated.paced(3, NOW, CAP, CAP, None)
    over = _agent(used=CAP, pace=saturated)                        # at the cap: the line itself never paces it
    _run(over)
    assert over._v6_22_counts.get("cap_window_paced_states") == 1 and over._v641_counts == {"paced_book_states": 1}
    ahead = _agent(used=U_BOUND + 6_000.0, pace=_aged(U_BOUND))    # above its own line: the line paces it already
    _run(ahead)
    assert ahead._v641_counts.get("paced_book_states") == 1 and ahead._v6_22_counts == {}


def test_s1_a_resting_adding_order_goes_and_the_touch_quote_places_no_adding_quote():
    off, on = _agent(used=U_BOUND + 500.0, inv=3.0, pace=_aged(U_BOUND), s1=False), \
        _agent(used=U_BOUND + 500.0, inv=3.0, pace=_aged(U_BOUND))
    for a in (off, on):
        _rest_both(a)
    assert _out(_run(off)) == ([(3, "BUY", 43031, 100.0, 2.0)], [])       # v6.21: the touch bid adds to the long
    assert _out(_run(on)) == ([], [(3, 21)])                              # S1: the adding level 0 goes, no touch bid
    assert on._v641_counts.get("cancel_v641_paced") == 1 and on._v617_counts.get("paced_states") == 1


def test_s1_the_paced_book_takes_v69s_bound_instead_of_the_behind_the_line_bound():
    agent = _agent(used=U_BOUND + 500.0, inv=6.0, pace=_aged(U_BOUND))
    deep = agent._v633_deep
    assert agent._v610_bound_base(deep, True) == deep.max_clips * deep.clip == 4.0   # the harness layer: 2 clips of 2
    assert agent._v610_bound_base(deep, False) == 24.0 * deep.clip == 48.0          # v6.10's 24 clips behind the line
    off = _agent(used=U_BOUND + 500.0, inv=6.0, pace=_aged(U_BOUND), s1=False)
    for a in (off, agent):
        _rest_both(a)
    assert _out(_run(off)) == ([(3, "BUY", 43031, 100.0, 2.0)], [])       # 6 base long: room under 48
    assert _out(_run(agent)) == ([], [(3, 21)])                           # beyond 4: no room, cancelled for it
    assert agent._v633_counts.get("cancel_deep_no_room") == 1


def test_s1_a_line_without_young_is_paced_only_while_v620s_room_line_is_off():
    for room, paced in ((False, True), (True, False)):
        pace = LaunchPaceLine()                                    # v6.15 / v6.8: no young() to tell
        pace.observe(3, NOW - 60 * S, U_BOUND)
        agent = _agent(used=U_BOUND, inv=3.0, pace=pace)
        agent.research_v620_room_pace = room
        placed, _cancelled = _out(_run(agent))
        assert (agent._v6_22_counts.get("cap_window_paced_states") == 1) is paced, room
        assert (placed == [(3, "SELL", 40032, 100.23, 2.0)]) is paced, (room, placed)


def test_s1_an_aged_book_with_room_and_a_young_book_keep_v621():
    young = _seen(yw.YoungWindowLine, U_BOUND, launch=False)
    known = _status_line(ws.KNOWN, NOW + 67_800 * S - (86_400 - 600) * S)  # a young book on its even line
    for name, kw, pace in (("aged with room", dict(used=U_FREE + 500.0, inv=3.0), lambda: _aged(U_FREE)),
                           ("young at its flat line", dict(used=U_BOUND, inv=3.0), lambda: copy.deepcopy(young)),
                           ("young, known window", dict(used=0.9 * CAP, inv=3.0), lambda: copy.deepcopy(known))):
        off, on = _agent(pace=pace(), s1=False, **kw), _agent(pace=pace(), **kw)
        assert _out(_run(on)) == _out(_run(off)), name
        assert "cap_window_paced_states" not in on._v6_22_counts, name


# ---- 6. S2 in the pass --------------------------------------------------------------------------------------------------

def test_s2_an_unknown_windows_books_are_aged_at_their_first_report_once():
    pace = _unknown()
    agent = _agent(used=U_FREE, inv=3.0, pace=pace)
    _run(agent)
    assert 3 in pace.aged and not pace.young(3) and agent._v6_22_counts == {"unknown_aged_books": 1}
    ref = LaunchPaceLine()                                         # aged: v6.15's line from its first report
    ref.observe(3, NOW, U_FREE)
    assert pace.line(3, NOW + 600 * S, CAP) == ref.line(3, NOW + 600 * S, CAP)
    assert pace.scale(3, NOW, U_FREE + 10_000.0, CAP) == 1.0      # full size
    _run(agent, NOW + S)
    assert agent._v6_22_counts == {"unknown_aged_books": 1} and agent._v6_22_errors == 0
    off_pace = _unknown()
    off = _agent(used=U_FREE, inv=3.0, pace=off_pace, s2=False)
    _run(off)
    assert off_pace.young(3) and off._v6_22_counts == {}           # v6.21: young on v6.20's room line


def test_s2_waits_until_the_uids_window_is_seen_to_roll():
    pace = _unknown(rolls=False)
    agent = _agent(used=U_FREE, inv=3.0, pace=pace)
    _run(agent)
    assert pace.young(3) and agent._v6_22_counts == {}             # the status alone: v6.21
    _rolled(pace, ts=NOW + 30 * S)                                 # a book of the UID rolls off (its first fall)
    _run(agent, NOW + 60 * S)
    assert not pace.young(3) and agent._v6_22_counts == {"unknown_aged_books": 1}


def test_s2_with_s1_the_aged_book_of_an_unknown_window_is_paced_near_its_cap():
    off_pace, pace = _unknown(), _unknown()
    off, on = _agent(used=U_BOUND, inv=3.0, pace=off_pace, s2=False), _agent(used=U_BOUND, inv=3.0, pace=pace)
    assert _out(_run(off)) == ([(3, "BUY", 40031, 99.8, 2.0), (3, "SELL", 40032, 100.23, 2.0)], [])  # flat young line
    assert _out(_run(on)) == ([(3, "SELL", 40032, 100.23, 2.0)], [])
    assert on._v6_22_counts == {"unknown_aged_books": 1, "cap_window_paced_states": 1}


def test_s2_a_known_window_or_a_book_first_seen_empty_is_unchanged():
    known = _status_line(ws.KNOWN, NOW - 3_000 * S)
    agent = _agent(used=U_FREE, inv=3.0, pace=known)
    _run(agent)
    assert known.young(3) and agent._v6_22_counts == {}
    empty = _unknown()
    agent = _agent(used=0.0, pace=empty)                           # nothing of ours in the window: its own start
    _run(agent)
    assert empty.window_start_ns(3) == NOW and empty.young(3) and agent._v6_22_counts == {}


def test_s2_the_aged_marks_survive_a_restart_and_a_v621_state_is_aged_at_its_first_state():
    pace = _unknown()
    first = _agent(used=U_FREE, inv=3.0, pace=pace)
    _run(first)
    saved = json.loads(json.dumps(first._v621_session_state()))
    assert saved["books"]["3"].get("aged") == 1 and saved["window"]["status"] == ws.UNKNOWN_NO_EMPTY_START
    second = _agent(used=U_FREE + 500.0, inv=3.0)                  # a new process: the saved state stashed
    second._v621_restore_payload = saved
    _run(second, NOW + 60 * S)
    assert second._v621_counts.get("restore_restored") == 1 and not second._v66_pace.young(3)
    assert second._v6_22_counts == {}                              # restored aged: nothing to age again
    for rolls, aged in ((True, True), (False, False)):            # what a v6.21 process saved: the book still young
        v621 = _unknown(rolls=rolls)
        v621.sim_id = SIM
        v621.observe(3, NOW - 60 * S, U_FREE, FLOOR)
        third = _agent(used=U_FREE + 500.0, inv=3.0)
        third._v621_restore_payload = json.loads(json.dumps(yw.pace_state(v621, uid=UID)))
        _run(third)
        assert third._v621_counts.get("restore_restored") == 1 and (3 in third._v66_pace.aged) is aged, rolls
        assert third._v6_22_counts == ({"unknown_aged_books": 1} if aged else {}), rolls


def test_s2_with_the_window_start_read_from_the_uids_own_logs(tmp_path):
    t621._log(tmp_path, UID, "20261008_005809", wall=t621.W0, first_ts=NOW - 40_000 * S, books=1, launch=0)
    for rolls in (False, True):                                    # every start of this simulation shows volume
        agent = _agent(used=U_FREE, inv=3.0, out_dir=tmp_path,
                       pace=_rolled(yw.YoungWindowLine(launch=False)) if rolls else None)
        _run(agent)
        assert agent._v621_window["status"] == ws.UNKNOWN_NO_EMPTY_START, rolls
        assert (3 in agent._v66_pace.aged) is rolls, rolls
        assert agent._v6_22_counts == ({"unknown_aged_books": 1} if rolls else {}), rolls
    t621._log(tmp_path, UID, "20261007_162337", wall=t621.W0 - 3600 * S, first_ts=NOW - 50_000 * S, books=1, launch=1)
    known = _agent(used=U_FREE, inv=3.0, out_dir=tmp_path)        # an earlier all-empty start: a known window
    _run(known)
    assert known._v621_window["status"] == ws.KNOWN and known._v66_pace.young(3) and known._v6_22_counts == {}


# ---- 7. S3 in the pass --------------------------------------------------------------------------------------------------

def test_s3_a_book_at_its_cap_keeps_its_cancels_and_builds_no_new_order():
    off, on = _agent(used=CAP, s1=False, s3=False), _agent(used=CAP, s1=False)
    for a in (off, on):
        _rest_doomed(a)
    placed, cancelled = _out(_run(off))
    assert placed == [(3, "BUY", 43031, 100.0, 2.0), (3, "SELL", 40032, 100.23, 2.0)]   # v6.21: dropped downstream
    assert cancelled == [(3, 31), (3, 32)]
    assert _out(_run(on)) == ([], [(3, 31), (3, 32)])                 # the same cancels, nothing to drop
    assert on._v6_22_counts == {"cap_skip_book_states": 1} and on._v6_22_errors == 0
    assert on._v617_counts.get("cancel_touch_flipped") == 1 and on._v633_counts.get("cancel_deep_reprice") == 1
    both = _agent(used=CAP)                                          # S1 too (the book is cap-bound)
    _rest_doomed(both)
    assert _out(_run(both)) == ([], [(3, 31), (3, 32)])


def test_s3_is_v621_with_room_without_v66_s4_or_with_its_switch_off():
    for name, kw in (("room", dict(used=CAP - 1.0)), ("v6.6 S4 off", dict(used=CAP, closed=False)),
                     ("switch off", dict(used=CAP, s3=False))):
        ref, agent = _agent(s1=False, s3=False, **{k: v for k, v in kw.items() if k != "s3"}), _agent(s1=False, **kw)
        for a in (ref, agent):
            _rest_doomed(a)
        assert _out(_run(agent)) == _out(_run(ref)), name
        assert "cap_skip_book_states" not in agent._v6_22_counts, name


def test_s3_short_of_budget_it_builds_as_v621_and_without_that_guard_a_cancel_would_go_out_early():
    for pre in (0, 1, 2, 3):
        ref, agent = _agent(used=CAP, s1=False, s3=False), _agent(used=CAP, s1=False)
        for a in (ref, agent):
            _rest_doomed(a)
        want, got = _out(_run(ref, pre=pre)), _out(_run(agent, pre=pre))
        assert got[1] == want[1], pre                                  # every cancel exactly as v6.21 sends it
        if pre <= 1:
            assert got[0] == [] and agent._v6_22_counts == {"cap_skip_book_states": 1}, pre
        else:
            assert got == want and agent._v6_22_counts == {"cap_skip_budget_kept": 1}, pre
    ref = _agent(used=CAP, s1=False, s3=False)
    _rest_doomed(ref)
    assert (3, 32) not in _out(_run(ref, pre=3))[1] and ref._v617_counts.get("cancel_budget_deferred") == 1
    naive = _agent(used=CAP, s1=False)                               # the same rule without the budget guard
    fn = type(naive)._v6_22_cap_skip
    naive._v6_22_cap_skip = types.MethodType(types.FunctionType(
        fn.__code__, dict(fn.__globals__, v622_skip_keeps_budget=lambda count, budget: True), fn.__name__), naive)
    _rest_doomed(naive)
    assert (3, 32) in _out(_run(naive, pre=3))[1]                    # the touch quote's cancel one request early


def _validated(resp, book=3):
    """What reaches the venue at the cap with v6.6 S4 on: the final validator drops every placement on the book."""
    out = []
    for ix in resp.instructions:
        if ix.type == "PLACE_ORDER_LIMIT" and ix.bookId == book:
            continue
        ids = sorted(int(c.orderId) for c in getattr(ix, "cancellations", None) or [])
        out.append((ix.type, ix.bookId, tuple(ids)))
    return out


def _fingerprint(agent):
    """The decision state a request leaves behind (telemetry counters aside)."""
    return (json.dumps(yw.pace_state(agent._v66_pace, uid=UID), sort_keys=True),
            json.dumps(agent._v633_deep.snapshot(), sort_keys=True, default=str),
            sorted((int(r.order_id), int(r.side), float(r.price)) for r in agent.ledger.live_orders(3)),
            list(agent._v63_mids.get(3) or []), sorted(agent._v63_paused))


def test_s3_what_reaches_the_venue_and_the_state_left_behind_match_v621_request_after_request():
    ref, agent = _agent(used=CAP, s3=False), _agent(used=CAP)
    for a in (ref, agent):
        _rest_doomed(a)
    for i in range(3):
        ts = NOW + i * S
        want, got = _run(ref, ts), _run(agent, ts)
        assert _validated(got) == _validated(want), i
        assert _fingerprint(agent) == _fingerprint(ref), i
    assert agent._v6_22_counts.get("cap_skip_book_states") == 3


def test_s3_a_failing_headroom_read_is_room_and_never_raises():
    agent = _agent(used=CAP, s1=False)

    def _boom(self, state, book_id):
        raise RuntimeError("boom")

    agent._research_volume_cap_headroom = types.MethodType(_boom, agent)
    ref = _agent(used=CAP, s1=False, s3=False)
    for a in (ref, agent):
        _rest_doomed(a)
    assert _out(_run(agent)) == _out(_run(ref)) and agent._v6_22_counts == {} and agent._v6_22_errors == 0
    del agent._research_volume_cap_headroom                         # at the cap again ...
    agent._count_book_instructions = lambda *a, **k: 1 / 0         # ... and anything past the read fails: no skip
    assert agent._v6_22_cap_skip(None, t63._Resp(), 3, 5) is False and agent._v6_22_errors == 1


# ---- 8. telemetry and errors ----------------------------------------------------------------------------------------------

def test_the_snapshot_and_the_state_row_carry_the_counters():
    agent = _agent(used=CAP, pace=_aged(U_BOUND, since=50_000))
    _run(agent)
    assert agent._v6_22_snapshot() == {"version": "aged_cap_v6_22", "errors": 0,
                                        "counts": {"cap_window_paced_states": 1, "cap_skip_book_states": 1}}
    for key in SWITCHES:
        assert f'{key.replace("research_v622_", "")}_on=int(bool(getattr(self, "{key}", False))),' in SIMPLE, key
    assert 'aged_cap=(self._v6_22_snapshot() if getattr(self, "research_v622_cap_skip", None) is not None else {}),' in SIMPLE
    row = _src("_v62_telemetry")
    assert row.index("young_window=") < row.index("aged_cap=") < row.index("trend_target=")


def _failing(agent, method, name):
    """The agent's ``method`` with the module function ``name`` raising."""
    def _boom(*a, **k):
        raise RuntimeError("boom")

    fn = getattr(type(agent), method)
    setattr(agent, method, types.MethodType(types.FunctionType(
        fn.__code__, dict(fn.__globals__, **{name: _boom}), fn.__name__, fn.__defaults__), agent))


def test_a_failing_rule_counts_an_error_and_never_raises():
    ref = _agent(used=U_BOUND, inv=3.0, pace=_unknown(), s1=False, s2=False)
    agent = _agent(used=U_BOUND, inv=3.0, pace=_unknown())
    _failing(agent, "_v6_22_age_unknown", "v622_ages_unknown")
    _failing(agent, "_v6_22_cap_window", "v622_cap_window_paced")
    assert _out(_run(agent)) == _out(_run(ref))                     # S2 and S1 failed: v6.21 for the book
    assert agent._v6_22_errors == 2 and agent._v6_22_counts == {} and agent._v641_errors == 0


# ---- 9. wiring and the launcher -------------------------------------------------------------------------------------------

def test_the_switches_ship_on_and_agree_with_the_agent_default():
    params = t617._params()
    for key in SWITCHES:
        assert f"{key}=1" in params and f"{key}=0" not in params, key
        assert f'self.{key} = self._as_bool(getattr(self.config, "{key}", True))' in SIMPLE, key
    for key in ("research_v621_rolloff_guard=1", "research_v621_pace_persist=1", "research_v620_room_pace=1",
                "research_v66_cap_closed=1", "research_v641_volume_pace=1", "research_v66_pace_line=1",
                "research_v68_rolling_budget=1", "research_v69_deep_ladder=1", "research_v69_deep_first_pace=1"):
        assert key in params, key


def test_each_rule_is_wired_once_and_in_its_place_in_the_pass():
    for line in (S1_LINE, S2_LINE, S3_LINE, DEEP_SKIP, TOUCH_SKIP):
        assert SIMPLE.count(line) == 1, line
    assert SIMPLE.count("from research_v622_aged_cap import (") == 1
    body = _src("_v63_pass")
    observe = body.index("pace.observe(book_id, now_ts, reading, min_order * 0.5 * (raw_bid + raw_ask))")
    paced = body.index("paced = bool(pace.paced(book_id, now_ts, used, pace_cap, pace_duration))")
    assert observe < body.index(S2_LINE) < paced < body.index(S1_LINE) < body.index("if paced and v620_on:")
    assert body.index("if v621_on:\n            self._v621_pass(") < body.index("v6_22_aged = ")   # its status first
    assert body.index(S3_LINE) < body.index("placed_total += self._v633_book(")
    assert body.count("cap_skip=cap_skip") == 4                      # the deep layer and the touch quote, both calls
    assert _src("_v617_book").count("cap_skip=cap_skip") == 1
    assert "paced: bool = False, cap_skip: bool = False," in _src("_v633_book")
    assert "size_scale: float = 1.0, cap_skip: bool = False) -> int:" in _src("_v617_touch")


def test_the_v622_block_is_gated_preflighted_and_listed():
    assert "; V6_19_BUILD=0; V6_20_BUILD=0; V6_21_BUILD=0; V6_22_BUILD=0\n" in LAUNCHER
    arm = next(line for line in LAUNCHER.splitlines() if line.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_20_BUILD=1; V6_21_BUILD=1; V6_22_BUILD=1"), arm
    assert 'if [[ "${V6_22_BUILD:-0}" == "1" ]]; then' in LAUNCHER
    assert "      tests/test_research_v6_22.py \\\n" in LAUNCHER
    assert LAUNCHER.index('echo "[preflight] v6.21 young window PASS') < LAUNCHER.index("# v6.22 S1-S3.")


_PATTERN = r"grep -qF (?:'([^']+)'|\"([^\"]+)\") \"\$AGENT_PATH/([^\"]+)\""


def _block():
    start = LAUNCHER.index('if [[ "${V6_22_BUILD:-0}" == "1" ]]; then')
    return LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.22 aged cap PASS', start)]


def test_every_v622_preflight_pattern_is_in_the_file_it_checks():
    pats = re.findall(_PATTERN, _block())
    assert len(pats) == 9, pats
    for single, double, name in pats:
        assert (single or double) in (STRATEGY / name).read_text(), (single or double, name)


def _run_block(params, agent_path=STRATEGY):
    start = LAUNCHER.index("# v6.22 S1-S3.")
    stop = LAUNCHER.index('echo "[preflight] v6.22 aged cap PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_22_BUILD": "1"}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_the_v622_block_passes_the_shipped_params_and_refuses_any_switch_it_needs_off():
    shipped = t617._shipped()
    out = _run_block(shipped)
    assert out.returncode == 0 and "[preflight] v6.22 aged cap PASS" in out.stdout, out.stderr
    for key in SWITCHES + ("research_v621_rolloff_guard", "research_v621_pace_persist", "research_v620_room_pace",
                           "research_v66_cap_closed", "research_v641_volume_pace", "research_v66_pace_line",
                           "research_v68_rolling_budget", "research_v69_deep_ladder", "research_v69_deep_first_pace"):
        assert shipped.count(f"{key}=1") == 1, key
        for bad in (f"{key}=0", f"{key}=10", ""):
            out = _run_block(shipped.replace(f"{key}=1", bad))
            assert out.returncode == 1 and f"v6.22 S1-S3 build without {key}=1" in out.stderr, (key, bad)


def test_the_v622_block_refuses_an_agent_without_each_line_it_checks():
    block = _block()
    cases = re.findall(_PATTERN + r" \|\| \{\n\s+echo \"ERROR: ([^\"]+)\" >&2", block)
    assert len(cases) == 9, cases
    names = ("Strategy1_Research_Simple.py", "research_v622_aged_cap.py")
    for single, double, name, message in cases:
        needle = single or double
        tmp = Path(tempfile.mkdtemp(prefix="t622_"))
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
