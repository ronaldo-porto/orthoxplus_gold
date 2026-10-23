"""v6.27: S7 the touch quote holds to the lean centre -- research_v627_touch_centre.

v6.26 + one switch (research_v627_touch_centre, default on).  v6.25 S5 makes every making decision read (inventory -
tau), tau the book's fundamental lean; the v6.17 touch quote adds while a fill of one touch clip keeps |inventory - tau|
inside v6.10's bound -- 48 base for a book behind its volume line (a paced book already places no adding touch quote).
On UID 115 the touch alone bought +57.5 base on book 95 while tau was +2..+8.  S7: while S5 leans the book THIS request
(tau != 0) the touch quote's bound is one touch clip -- the clip it is sized with, never above v6.10's bound -- so the
toward side adds only while (inv - tau) + clip <= clip (never past the lean centre) and a resting touch quote whose fill
would pass the centre is cancelled by the quote's own room check.  The lean is the one S5 set in the same request: the
pass notes it right after S5 sets it (S5's own (tau, state time) record, only at this request's state time) and starts
every request's notes empty.  Without a lean, on a paced book, or off, the touch quote is v6.26's; the deep layer, S1,
S5 and S6 are unchanged.  Replay spec: k65 vm665 $V626 + ltbt=2.

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

import research_v617_touch_quote as tq  # noqa: E402
import research_v627_touch_centre as tc  # noqa: E402
import research_v641_pace as pc  # noqa: E402
import research_v65_deep_clips as dc  # noqa: E402
import test_research_v6_16 as t616  # noqa: E402
import test_research_v6_17 as t617  # noqa: E402
import test_research_v6_22 as t622  # noqa: E402
import test_research_v6_23 as t623  # noqa: E402
import test_research_v6_25 as t625  # noqa: E402
import test_research_v6_26 as t626  # noqa: E402
from _harness import extractor  # noqa: E402
import _v638_undo  # noqa: E402
from _harness import simple_text, read_source, copy_source, strategy_view  # noqa: E402

SIMPLE = simple_text()
MODULE = read_source(STRATEGY / "research_v627_touch_centre.py")
LAUNCHER = read_source(ROOT / "run_strategy1_research_simple_multi.sh")
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000
NOW = t623.NOW
ABOVE, BELOW = t623.ABOVE, t623.BELOW           # book 3 (100.00 / 100.03): fp 38 bps above / 42 bps below the mid
INSIDE = 100.03                                 # 1.5 bps above the mid: inside S5's gap (no lean), 3.0 bps past the bid
BUY, SELL = tq.SIDE_BUY, tq.SIDE_SELL
EPS = 1e-9
TOUCH_CIDS = set(tq.client_ids(3))              # 43031 / 43032
SWITCH_LINE = 'self.research_v627_touch_centre = self._as_bool(getattr(self.config, "research_v627_touch_centre", True))'
RESET_LINE = "self._v6_27_taus = {}"
NOTE_LINE = "self._v6_27_note(book_id, now_ts)"
BOUND_LINE = "v6_27_wide, bound = bound, self._v6_27_bound(book_id, clip, bound)"
REPORT_LINE = "self._v6_27_report()"
FIELDS = ("version", "touch_centre_on", "leaned_books", "touch_decisions", "armed", "refused_centre", "cancel_centre",
          "errors")
V627_NAMES = {"V627_TOUCH_CENTRE_VERSION": tc.V627_TOUCH_CENTRE_VERSION, "v627_request_lean": tc.request_lean,
              "v627_touch_bound": tc.touch_bound}
EXTRA = dict(t626.EXTRA, **V627_NAMES)
METHODS = ("_v6_27_note", "_v6_27_bound", "_v6_27_count", "_v6_27_report", "_v6_27_snapshot")


def _adds(side, e):
    return pc.adds(side, e)


def _v626_room(side, e, clip=2.0, bound=48.0):
    """v6.26's touch room on the leaned inventory e = inv - tau (v6.10's behind-the-line bound)."""
    return (not _adds(side, e)) or abs(e) + clip <= bound + EPS


def _s7_room(side, e, clip=2.0):
    """S7's: one touch clip -- the toward side adds only while e + clip <= clip, i.e. up to the centre."""
    return (not _adds(side, e)) or abs(e) + clip <= clip + EPS


# ---- 1. the module ------------------------------------------------------------------------------------------------------

def test_the_module_exports_its_version_and_two_rules_and_sets_no_number():
    """STRUCTURAL: the bound is the touch quote's own clip and the centre is S5's tau -- the module defines no number."""
    assert tc.V627_TOUCH_CENTRE_VERSION == "touch_centre_v6_27"
    assert set(tc.__all__) == {"V627_TOUCH_CENTRE_VERSION", "request_lean", "touch_bound"}
    assert "STRUCTURAL:" in MODULE and "OBSERVED:" in MODULE
    tree = ast.parse(MODULE)
    assigned = {t.id for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)}
    assert assigned == {"V627_TOUCH_CENTRE_VERSION", "__all__"}
    numbers = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
               and not isinstance(n.value, bool)]
    assert set(numbers) <= {0.0}, numbers                                          # only the zero tests


def test_the_lean_is_this_requests_only():
    for tau in (16.7, -18.1, 48.0, 1.0, 1e-6, -1e-6):
        assert tc.request_lean((tau, NOW), NOW) == tau, tau
        assert tc.request_lean([tau, NOW], NOW) == tau, tau
    for ts in (NOW - 1, NOW - S, NOW + 1, 0, -NOW):                                 # another request's record
        assert tc.request_lean((16.7, ts), NOW) == 0.0, ts
    assert tc.request_lean((16.7, 0), 0) == 16.7                                     # (a state without a time: S5 = 0)
    for tau in (0.0, -0.0, float("nan"), float("inf"), float("-inf"), None, "x", True, False, [1.0]):
        assert tc.request_lean((tau, NOW), NOW) == 0.0, tau
    assert tc.request_lean(("16.7", NOW), NOW) == 16.7                               # a number as text
    for entry in (None, (), (16.7,), (16.7, NOW, 1), "ab", 5, {}, (16.7, None), (16.7, "x"), (16.7, True)):
        assert tc.request_lean(entry, NOW) == 0.0, entry
    for now in (None, "x", True, float("nan")):
        assert tc.request_lean((16.7, NOW), now) == 0.0, now
    for entry, now in (((16.7, False), 0), ((16.7, True), 1), ((16.7, 1), True), ((16.7, 0), False)):
        assert tc.request_lean(entry, now) == 0.0, (entry, now)               # a bool is never a state time
    assert tc.request_lean((16.7, str(NOW)), NOW) == 16.7                            # int() reads it: same time


def test_the_bound_is_one_touch_clip_never_above_v610s():
    assert tc.touch_bound(48.0, 2.0) == 2.0 and tc.touch_bound(8.0, 2.0) == 2.0      # behind / paced: one clip
    assert tc.touch_bound(2.0, 2.0) == 2.0 and tc.touch_bound(48.0, 4.0) == 4.0
    assert tc.touch_bound(1.0, 2.0) == 1.0 and tc.touch_bound(0.0, 2.0) == 0.0       # never above v6.10's
    assert tc.touch_bound(-1.0, 2.0) == -1.0
    assert tc.touch_bound("48", "2") == 2.0
    for clip in (0.0, -2.0, float("nan"), float("inf"), None, "x", True, [2.0]):    # no usable clip: v6.10's bound
        assert tc.touch_bound(48.0, clip) == 48.0, clip
    for bound in (float("nan"), float("inf"), None, "x", True):                     # no usable bound: itself
        got = tc.touch_bound(bound, 2.0)
        assert got is bound or (isinstance(got, float) and math.isnan(got) and math.isnan(bound)), bound


def test_the_rule_is_the_replays_ltbt_2():
    """k65 vm665 (l. 1057-1058): with the lean armed the touch's room is e + c <= ltbt for a bid and e - c >= -ltbt
    for an offer, ltbt = c = one touch clip.  v6.17's room with S7's bound decides exactly that, at any clip."""
    for c in (2.0, 1.0, 4.0, 0.25):
        for k in range(-4000, 4001):
            e = k / 40.0 if k % 7 else k / 40.0 + 1e-6
            bound = tc.touch_bound(24.0 * c, c)
            assert tq.room(BUY, e, c, bound) == (e + c <= c + EPS), (c, e)
            assert tq.room(SELL, e, c, bound) == (e - c >= -c - EPS), (c, e)
            assert tq.room(BUY, e, c, bound) == _s7_room(BUY, e, c) and tq.room(SELL, e, c, bound) == _s7_room(SELL, e, c)
    assert tq.room(BUY, 0.0, 2.0, 2.0) and not tq.room(BUY, 1e-6, 2.0, 2.0)         # at the centre: one more clip
    assert tq.room(SELL, 0.0, 2.0, 2.0) and not tq.room(SELL, -1e-6, 2.0, 2.0)


def test_the_touch_clip_under_the_shipped_params_is_2_base_the_replays_one_clip():
    """The touch quote is sized CLIP_DEEP_CLIPS x the deep clip: the v6.3 clip base 1.0 x v6.5's multiple 2.0 x 1.0 =
    2.0 base (the live V62_STATE deep_layer.clip of UIDs 115 and 78 on v6.26 reads 2.0) -- the replay's ltbt=2 -- and
    S7's bound is that clip: under both v6.10 bounds (48 behind the line, 8 paced live)."""
    behind = float(re.search(r'^BEHIND_MAX_CLIPS="\$\{BEHIND_MAX_CLIPS:-([0-9.]+)\}"', LAUNCHER, re.M).group(1))
    mult = float(re.search(r'^DEEP_CLIP_MULT="\$\{DEEP_CLIP_MULT:-([0-9.]+)\}"', LAUNCHER, re.M).group(1))
    base = float(re.search(r"^research_v63_clip_base=([0-9.]+) ", LAUNCHER, re.M).group(1))
    max_clips = float(re.search(r"^research_v65_deep_max_clips=([0-9.]+) ", LAUNCHER, re.M).group(1))
    deep_clip = dc.deep_clip(base, mult, 0.25)
    touch_clip = tq.CLIP_DEEP_CLIPS * deep_clip
    assert (behind, mult, base, max_clips, deep_clip, touch_clip) == (24.0, 2.0, 1.0, 4.0, 2.0, 2.0)
    assert 'clip = float(V617_CLIP_DEEP_CLIPS) * float(getattr(deep, "clip", 0.0) or 0.0)' in _src("_v617_touch")
    paced = dc.book_bound(deep_clip, max_clips)                       # live: 4 deep clips x 2.0 base
    assert behind * deep_clip == 48.0 and paced == 8.0
    agent = _agent()
    deep = agent._v633_deep                                           # the harness keeps v6.3.3's 2 clips when paced
    assert (deep.clip, agent._v610_bound_base(deep, False), agent._v610_bound_base(deep, True)) == (2.0, 48.0, 4.0)
    for bound in (48.0, paced, agent._v610_bound_base(deep, True)):
        assert tc.touch_bound(bound, touch_clip) == 2.0, bound


# ---- 2. the agent: S7 compiled from the real Simple on t626's harness ----------------------------------------------------

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
        _CLASSES[key] = type("P627", (base,), methods)
    return _CLASSES[key]


def _agent(*, on=True, names=None, **kw):
    """t626's harness agent (v6.26: S5 on, S6 on, S1's gap 2.3 bps; fundamental 38 bps above book 3's mid unless
    ``page`` says otherwise) with v6.27's methods; the v6.27 state as _init_build_switches leaves it."""
    agent = t626._agent(**kw)
    agent.__class__ = _klass(type(agent), names)
    agent.research_v627_touch_centre = on
    agent._v6_27_taus, agent._v6_27_counts = {}, {}
    agent._v6_27_errors, agent._v6_27_errors_seen = 0, 0
    agent._v6_27_reported, agent._v6_27_erring = False, False
    return agent


def _request(agent, *, ts=NOW, wall=1000.0, version=62, book=None):
    """One request as respond() makes it (t626: the v6.26 hook, v6.23's observation, the pass).  62: S1 inactive."""
    return t626._request(agent, ts=ts, wall=wall, version=version, book=book)


_out = t622._out
_rest_l0 = t625._rest_l0                         # level 0 resting on both sides: the deep layer places nothing new


def _rest_touch(agent, side=BUY, oid=23):
    """A touch quote of ours resting at the touch on ``side`` (book 3: bid 100.00, ask 100.03)."""
    buy, sell = tq.client_ids(3)
    t622.t63.t14._rest(agent, oid, 3, 0 if side == BUY else 1, 100.00 if side == BUY else 100.03,
                       cid=buy if side == BUY else sell)


def _l0_and_touch(side):
    def rest(agent):
        _rest_l0(agent)
        _rest_touch(agent, side)
    rest.__name__ = f"l0+touch_{side}"
    return rest


TOUCH_ORDER_IDS = {23, 32}                       # _rest_touch's and t622._rest_doomed's touch offer


def _touch(out):
    """The touch quote's placements and cancels in one request's output."""
    placed, cancelled = out
    return sorted(p for p in placed if p[2] in TOUCH_CIDS), sorted(c for c in cancelled if c[1] in TOUCH_ORDER_IDS)


def _rest_of(out):
    """Everything else: the deep layer's placements and cancels."""
    placed, cancelled = out
    return sorted(p for p in placed if p[2] not in TOUCH_CIDS), sorted(c for c in cancelled if c[1] not in TOUCH_ORDER_IDS)


def _pair(*, rest=None, version=62, **kw):
    """S7 on and off (v6.26), each a fresh agent after one request: (on, off, out_on, out_off)."""
    on, off = _agent(**kw), _agent(on=False, **kw)
    for a in (on, off):
        if rest is not None:
            rest(a)
    return on, off, _out(_request(on, version=version)), _out(_request(off, version=version))


def _rows(agent):
    return [kw for event, kw in agent.emitted if event == "V627_STATE"]


def _counts(agent):
    return dict(agent._v6_27_counts)


# ---- 3. the rule: behind the line the touch quote adds only up to the lean centre ---------------------------------------

GRID_ABOVE = (None, 3.0, 14.0, 16.0, 17.0, 20.0, 30.0, 47.0, 60.0, 65.0, -40.0)     # tau +16.7: toward BUY
GRID_BELOW = (None, -3.0, -17.0, -18.0, -19.0, -20.0, -47.0, -64.0, -70.0, 40.0)   # tau -18.1: toward SELL


def test_behind_the_line_the_touch_adds_only_up_to_the_lean_centre():
    """With the lean armed the toward-side touch quote is placed only while e = inv - tau <= 0 (+ one clip <= one clip);
    v6.26 placed it up to e + 2 <= 48.  The deep layer's own orders are the same either way."""
    seen = refused = 0
    for page, toward, grid in (("default", BUY, GRID_ABOVE), (t616._page({3: BELOW}), SELL, GRID_BELOW)):
        for inv in grid:
            on, off, got, want = _pair(page=page, inv=inv, rest=_rest_l0)
            tau = on._v6_25_taus[3][0]
            assert tau == off._v6_25_taus[3][0] and (tau > 16.0 if toward == BUY else tau < -18.0)
            e = (inv or 0.0) - tau
            cid = tq.client_ids(3)[0 if toward == BUY else 1]
            price = 100.00 if toward == BUY else 100.03
            quote = [(3, "BUY" if toward == BUY else "SELL", cid, price, 2.0)]
            assert _touch(want) == ((quote if _v626_room(toward, e) else []), []), (inv, e)
            assert _touch(got) == ((quote if _s7_room(toward, e) else []), []), (inv, e)
            assert _rest_of(got) == _rest_of(want), inv
            centre = int(_v626_room(toward, e) and not _s7_room(toward, e))
            assert _counts(on) == dict({"touch_decisions": 1, "armed": 1}, **({"refused_centre": 1} if centre else {})), inv
            assert on._v6_27_taus == {3: tau} and on._v6_27_errors == 0 and on._v617_errors == 0
            assert _counts(off) == {} and off._v6_27_taus == {}
            if centre:
                assert on._v617_counts.get("bound_states") == 1 and "bound_states" not in off._v617_counts
            seen += 1
            refused += centre
    assert seen == len(GRID_ABOVE) + len(GRID_BELOW) and refused == 9           # 5 + 4 placements v6.26 made past it


def test_the_quotes_v626_placed_past_the_centre_are_the_leak():
    """The canonical case (t625: a 47-base long, tau +16.7): v6.26 bought a touch clip at e = +30.3; S7 refuses it."""
    on, off, got, want = _pair(inv=47.0, rest=_rest_l0)
    assert want == ([(3, "BUY", 43031, 100.0, 2.0)], []) and got == ([], [])
    assert _counts(on) == {"touch_decisions": 1, "armed": 1, "refused_centre": 1}


def test_at_the_centre_the_touch_adds_one_more_clip_and_not_past_it():
    probe = _agent()
    _request(probe)
    for page, toward in (("default", BUY), (t616._page({3: BELOW}), SELL)):
        a = _agent(page=page)
        _request(a)
        tau = a._v6_25_taus[3][0]
        sign = 1.0 if toward == BUY else -1.0
        for inv, placed in ((tau, True), (tau - sign * 1e-6, True), (tau + sign * 1e-6, False),
                            (tau + sign * 1.0, False), (tau - sign * 1.0, True)):
            on, off, got, want = _pair(page=page, inv=inv, rest=_rest_l0)
            assert on._v6_25_taus[3][0] == tau
            assert bool(_touch(got)[0]) is placed, (toward, inv - tau)
            assert bool(_touch(want)[0]) is True, (toward, inv - tau)               # v6.26: 47 base of room left
    assert probe._v6_25_taus[3][0] == pytest.approx(t625._tau(ABOVE))


def test_a_resting_touch_quote_whose_fill_would_pass_the_centre_is_cancelled():
    """The quote's own room check, with S7's bound: a resting bid at e = +3.3 (v6.26 keeps it: 5.3 <= 48) is cancelled
    (TOUCH_BOUND) and nothing is re-placed this request; at e = -13.7 it rests either way; past v6.10's bound both
    cancel it and S7 claims none of it."""
    for inv, cancel_off, cancel_on in ((20.0, False, True), (17.0, False, True), (60.0, False, True),
                                       (3.0, False, False), (None, False, False), (65.0, True, True)):
        on, off, got, want = _pair(inv=inv, rest=_l0_and_touch(BUY))
        assert _touch(want) == ([], [(3, 23)] if cancel_off else []), inv
        assert _touch(got) == ([], [(3, 23)] if cancel_on else []), inv
        assert _rest_of(got) == _rest_of(want), inv
        centre = int(cancel_on and not cancel_off)
        assert _counts(on).get("cancel_centre", 0) == centre and "refused_centre" not in _counts(on), inv
        if cancel_on:
            assert on._v617_counts.get("cancel_touch_bound") == 1 and on._v617_counts.get("wait_cancel_seen") == 1
        if centre:
            assert "cancel_touch_bound" not in off._v617_counts
    on, off, got, want = _pair(page=t616._page({3: BELOW}), inv=-20.0, rest=_l0_and_touch(SELL))   # mirrored: e -1.9
    assert _touch(want) == ([], []) and _touch(got) == ([], [(3, 23)])
    assert _counts(on).get("cancel_centre") == 1


def test_s7_holds_request_after_request_while_the_lean_holds():
    agent = _agent(inv=20.0)
    _rest_l0(agent)
    for i in range(3):
        assert _touch(_out(_request(agent, ts=NOW + i * S, wall=1000.0 + i))) == ([], []), i
    assert _counts(agent) == {"touch_decisions": 3, "armed": 3, "refused_centre": 3}


def test_the_deep_layer_s1_s5_and_s6_are_unchanged():
    """S7 changes the touch quote only: every other order, every other counter and the state a request leaves behind are
    v6.26's, on every page, inventory, resting set, pacing state and with S1 inactive (62) and active (63)."""
    seen = differs = 0
    for page in ("default", t616._page({3: BELOW}), t616._page({3: INSIDE})):
        for inv in (None, 3.0, 20.0, 47.0, 65.0, -20.0, -47.0):
            for rest in (None, _rest_l0, t622._rest_doomed, _l0_and_touch(BUY), _l0_and_touch(SELL)):
                for pace in (None, "paced"):
                    for version in (62, 63):
                        kw = dict(pace=t622._aged(t622.U_BOUND), used=t622.U_BOUND + 500.0) if pace else {}
                        on, off, got, want = _pair(page=page, inv=inv, rest=rest, version=version, **kw)
                        case = (str(page)[:20], inv, getattr(rest, "__name__", None), pace, version)
                        assert _rest_of(got) == _rest_of(want), case
                        assert t622._fingerprint(on) == t622._fingerprint(off), case
                        assert on._v633_counts == off._v633_counts and on._v6_23_counts == off._v6_23_counts, case
                        assert on._v6_25_taus == off._v6_25_taus and on._v6_25_last == off._v6_25_last, case
                        assert on._v6_26_counts == off._v6_26_counts and on._v6_26_limit == off._v6_26_limit, case
                        assert on._v6_27_errors == 0 and on._v617_errors == 0, case
                        seen += 1
                        differs += int(_touch(got) != _touch(want))
    assert seen == 3 * 7 * 5 * 2 * 2 and differs > 20                          # and the touch quote does differ


# ---- 4. without a lean, on a paced book, or off: v6.26 ---------------------------------------------------------------

def _no_lean_pages():
    return ((None, "no feed"), (t616._page({3: ABOVE}, sim_ts_ns=NOW - 30 * S), "stale"),
            (t616._page({3: ABOVE}, sim_id="20260929_2015"), "other simulation"), (t616._page({5: ABOVE}), "other book"),
            (t616._page({3: t625.MID}), "fp at the mid"), (t616._page({3: 100.00}), "fp inside the gap below"),
            (t616._page({3: INSIDE}), "fp inside the gap, past the touch's hurdle"))


def test_without_a_lean_the_touch_quote_is_v626s():
    """tau == 0 -- no page, a stale one (30 s at the start: v6.16's 20-s limit), another simulation or book, or a
    fundamental inside S1's 2.3-bps gap: S7 notes nothing and the touch quote is v6.26's.  Inside the gap but 3.0 bps
    past the bid the touch quote is live: a 20-base long still buys its clip (v6.26's 48-base bound) -- S7 does not
    touch a book S5 does not lean."""
    live = 0
    for page, pname in _no_lean_pages():
        for inv in (None, 3.0, 20.0, 47.0, -20.0):
            for rest in (None, _rest_l0, _l0_and_touch(BUY), t622._rest_doomed):
                for version in (62, 63):
                    on, off, got, want = _pair(page=page, inv=inv, rest=rest, version=version)
                    case = (pname, inv, getattr(rest, "__name__", None), version)
                    assert got == want, case
                    assert on._v617_counts == off._v617_counts and on._v6_25_taus[3][0] == 0.0, case
                    assert on._v6_27_taus == {} and on._v6_27_errors == 0, case
                    assert _counts(on).get("armed", 0) == 0 and "refused_centre" not in _counts(on), case
                    assert "cancel_centre" not in _counts(on), case
                    live += int(bool(_touch(got)[0]))
    assert live > 0
    on, off, got, want = _pair(page=t616._page({3: INSIDE}), inv=20.0, rest=_rest_l0)
    assert _touch(got) == _touch(want) == ([(3, "BUY", 43031, 100.0, 2.0)], [])     # e = 20: v6.26's room, not S7's


def test_a_book_of_another_asset_class_is_v626s():
    foreign = t623._classes(split=3, n=5)                                # book 3 in the 2.5364 class: S5 never leans it
    for rest in (None, _rest_l0, _l0_and_touch(BUY)):
        outs = []
        for on in (True, False):
            agent = _agent(on=on, inv=20.0)
            if rest is not None:
                rest(agent)
            t626._hook(agent, ts=NOW, wall=1000.0)
            outs.append(_out(t623._run(agent, version=62, classes=foreign)))
            assert agent._v6_27_taus == {} and 3 not in agent._v6_25_taus
            assert _counts(agent).get("armed", 0) == 0
        assert outs[0] == outs[1], getattr(rest, "__name__", None)


def test_a_paced_book_is_unchanged():
    """Ahead of its volume line v6.4.1 already places no adding touch quote, so S7 never decides there: every request
    is v6.26's and S7 refuses or cancels nothing (its tally only counts the decisions)."""
    for page, grid in (("default", GRID_ABOVE), (t616._page({3: BELOW}), GRID_BELOW)):
        for inv in grid:
            for rest in (_rest_l0, _l0_and_touch(BUY), _l0_and_touch(SELL)):
                kw = dict(pace=t622._aged(t622.U_BOUND), used=t622.U_BOUND + 500.0)
                on, off, got, want = _pair(page=page, inv=inv, rest=rest, **kw)
                case = (str(page)[:12], inv, rest.__name__)
                assert got == want, case
                assert "refused_centre" not in _counts(on) and "cancel_centre" not in _counts(on), case
                assert on._v617_counts == off._v617_counts, case
                assert _counts(on)["armed"] == 1, case                             # leaned and armed, yet v6.26's


def test_off_the_pass_is_v626s_exactly_and_s7_never_runs():
    """Switch off: what reaches the venue, the counters and the state left behind are those of v6.26's harness agent
    (no v6.27 attribute at all), and S7 notes, decides and counts nothing."""
    for page in ("default", t616._page({3: BELOW}), t616._page({3: INSIDE}), None):
        for inv in (None, 20.0, 47.0, -20.0):
            for rest in (None, _rest_l0, _l0_and_touch(BUY), t622._rest_doomed):
                for version in (62, 63):
                    off = _agent(on=False, page=page, inv=inv)
                    ref = t626._agent(page=page, inv=inv)
                    for a in (off, ref):
                        if rest is not None:
                            rest(a)
                    case = (str(page)[:12], inv, getattr(rest, "__name__", None), version)
                    assert _out(_request(off, version=version)) == _out(t626._request(ref, version=version)), case
                    assert t622._fingerprint(off) == t622._fingerprint(ref), case
                    assert off._v617_counts == ref._v617_counts and off._v633_counts == ref._v633_counts, case
                    assert off._v6_27_taus == {} and off._v6_27_counts == {} and off._v6_27_errors == 0, case


# ---- 5. this request's lean only ------------------------------------------------------------------------------------------

def test_the_pass_notes_the_lean_s5_set_this_request_for_the_book():
    for page, sign in (("default", 1.0), (t616._page({3: BELOW}), -1.0)):
        agent = _agent(page=page)
        _request(agent)
        tau = agent._v6_25_taus[3][0]
        assert agent._v6_25_taus[3][1] == NOW and tau * sign > 16.0
        assert agent._v6_27_taus == {3: tau}


def test_a_lean_of_an_earlier_request_never_arms_the_touch_quote():
    """Request 1 leans book 3 (+16.7) and S7 holds its touch quote at the centre; request 2's fundamental is inside the
    gap (no lean): the notes start empty, so the 20-base long's touch quote is v6.26's again -- it buys."""
    on, off = _agent(inv=20.0), _agent(on=False, inv=20.0)
    for a in (on, off):
        _rest_l0(a)
    assert _touch(_out(_request(on))) == ([], []) and on._v6_27_taus == {3: on._v6_25_taus[3][0]}
    _request(off)
    for a in (on, off):
        a._v616_feed = t616._Feed(t616._page({3: INSIDE}, sim_ts_ns=NOW + S - 8 * S))
    got, want = _out(_request(on, ts=NOW + S, wall=1001.0)), _out(_request(off, ts=NOW + S, wall=1001.0))
    assert got == want and _touch(got) == ([(3, "BUY", 43031, 100.0, 2.0)], [])
    assert on._v6_25_taus[3] == (0.0, NOW + S) and on._v6_27_taus == {}
    assert _counts(on) == {"touch_decisions": 2, "armed": 1, "refused_centre": 1}


def _recordless_lean(tau):
    """S5 replaced by a lean of ``tau`` that leaves S5's record alone (the record S7 reads is whatever was there)."""
    def lean(self, book_id, raw_bid, raw_ask, cfg, inv, now_ts, req):
        return inv - tau, inv
    return lean


def test_s5s_record_must_carry_this_requests_state_time():
    """The touch reads the leaned inventory S5 hands the pass; S7 arms only from S5's record of THIS request: a record
    of an earlier state time (here left behind by a lean that did not record) never arms it, one of this state time
    does."""
    for record_ts, armed in ((NOW - S, False), (NOW - 1, False), (NOW + S, False), (NOW, True)):
        on, off = _agent(inv=20.0), _agent(on=False, inv=20.0)
        for a in (on, off):
            _rest_l0(a)
            a._v6_25_lean = types.MethodType(_recordless_lean(16.0), a)
            a._v6_25_taus = {3: (16.0, record_ts)}
        got, want = _out(_request(on)), _out(_request(off))
        assert _touch(want) == ([(3, "BUY", 43031, 100.0, 2.0)], [])                  # e = +4: v6.26 buys
        assert _touch(got) == (([], []) if armed else _touch(want)), record_ts
        assert on._v6_27_taus == ({3: 16.0} if armed else {}), record_ts
        assert _rest_of(got) == _rest_of(want)


def test_s5_off_or_a_failed_lean_never_arms_it():
    on, off = _agent(inv=20.0), _agent(on=False, inv=20.0)
    for a in (on, off):
        _rest_l0(a)
        a.research_v625_lean = False                                     # S5 off: no lean, no note, whatever the record
        a._v6_25_taus = {3: (16.0, NOW)}
    got, want = _out(_request(on)), _out(_request(off))
    assert got == want and on._v6_27_taus == {} and _counts(on) == {"touch_decisions": 1}
    boom = _agent(inv=20.0)
    _rest_l0(boom)
    boom._v6_23_fundamental = types.MethodType(lambda self, *a, **k: 1 / 0, boom)  # S5 errs: its lean is 0 this request
    assert _touch(_out(_request(boom))) == ([(3, "BUY", 43031, 100.0, 2.0)], [])
    assert boom._v6_25_taus[3] == (0.0, NOW) and boom._v6_25_errors == 1 and boom._v6_27_taus == {}


def test_each_request_starts_its_notes_empty():
    agent = _agent()
    _request(agent)
    assert agent._v6_27_taus == {3: agent._v6_25_taus[3][0]}
    agent._v616_feed = t616._Feed(None)
    _request(agent, ts=NOW + S, wall=1001.0)
    assert agent._v6_27_taus == {}
    agent._v6_27_taus = {3: 16.0, 7: -5.0}                               # whatever is left over from before
    _request(agent, ts=NOW + 2 * S, wall=1002.0)
    assert agent._v6_27_taus == {}


def test_the_note_reads_s5s_record_directly():
    agent = _agent()
    for record, want in (((16.7, NOW), {3: 16.7}), ((16.7, NOW - S), {}), ((0.0, NOW), {}), (None, {}),
                         ((float("nan"), NOW), {}), ((-18.1, NOW), {3: -18.1})):
        agent._v6_27_taus, agent._v6_25_taus = {}, ({} if record is None else {3: record})
        agent._v6_27_note(3, NOW)
        assert agent._v6_27_taus == want, record
    agent._v6_27_taus = None                                             # rebuilt on first use
    agent._v6_25_taus = {3: (16.7, NOW)}
    agent._v6_27_note(3, NOW)
    assert agent._v6_27_taus == {3: 16.7} and agent._v6_27_errors == 0


def test_the_bound_follows_the_note_and_never_loosens_v610s():
    agent = _agent()
    agent._v6_27_taus = {3: 16.7}
    assert agent._v6_27_bound(3, 2.0, 48.0) == 2.0 and agent._v6_27_bound(3, 2.0, 8.0) == 2.0
    assert agent._v6_27_bound(3, 2.0, 1.0) == 1.0                         # a tighter v6.10 bound stays
    assert agent._v6_27_bound(3, 0.0, 48.0) == 48.0                       # no usable clip: v6.10's
    assert agent._v6_27_bound(5, 2.0, 48.0) == 48.0                       # another book: not leaned
    agent._v6_27_taus = {}
    assert agent._v6_27_bound(3, 2.0, 48.0) == 48.0
    assert _counts(agent) == {"touch_decisions": 6, "armed": 4} and agent._v6_27_errors == 0


# ---- 6. errors and the state row -----------------------------------------------------------------------------------------

def test_an_error_in_the_bound_falls_back_to_v610s_bound_for_that_book():
    boom = dict(EXTRA, v627_touch_bound=lambda *a, **k: 1 / 0)
    for inv in (20.0, 47.0, 3.0):
        on, off = _agent(inv=inv, names=boom), _agent(on=False, inv=inv)
        for a in (on, off):
            _rest_l0(a)
        assert _out(_request(on)) == _out(_request(off)), inv               # v6.26's decision
        assert on._v6_27_errors == 1 and on._v617_errors == 0, inv
        assert _counts(on) == {"touch_decisions": 1, "armed": 1}, inv


def test_an_error_in_the_note_leaves_the_book_unarmed():
    boom = dict(EXTRA, v627_request_lean=lambda *a, **k: 1 / 0)
    on, off = _agent(inv=20.0, names=boom), _agent(on=False, inv=20.0)
    for a in (on, off):
        _rest_l0(a)
    assert _out(_request(on)) == _out(_request(off))
    assert on._v6_27_taus == {} and on._v6_27_errors == 1 and _counts(on) == {"touch_decisions": 1}


def test_a_tally_error_never_changes_a_decision():
    ref, agent = _agent(inv=20.0), _agent(inv=20.0)
    for a in (ref, agent):
        _rest_l0(a)
    agent._v6_27_counts = "x"                                            # every count raises inside the touch quote
    assert _out(_request(agent)) == _out(_request(ref)) == ([], [])       # S7 still refuses the clip past the centre
    assert agent._v6_27_errors == 3 + 1 and agent._v617_errors == 0       # three counts and the row's snapshot


def test_the_state_row_comes_at_the_first_pass_every_100_ticks_and_at_the_first_error():
    flag = {"boom": False}

    def lean(entry, now_ts):
        if flag["boom"]:
            raise RuntimeError("boom")
        return tc.request_lean(entry, now_ts)
    agent = _agent(names=dict(EXTRA, v627_request_lean=lean))
    for i, tick in enumerate((1, 2, 99, 100, 101)):
        agent._tick = tick
        _request(agent, ts=NOW + i * S, wall=1000.0 + i)
    rows = _rows(agent)
    assert [(r["reason"], r["tick"]) for r in rows] == [("start", 1), ("periodic", 100)]
    assert tuple(k for k in rows[0] if k not in ("reason", "tick")) == FIELDS
    first, periodic = rows
    assert first["version"] == tc.V627_TOUCH_CENTRE_VERSION and first["touch_centre_on"] == 1
    assert (first["leaned_books"], first["touch_decisions"], first["armed"], first["refused_centre"],
            first["cancel_centre"], first["errors"]) == (1, 1, 1, 0, 0, 0)
    assert (periodic["touch_decisions"], periodic["armed"]) == (4, 4)
    for k, (tick, boom) in enumerate(((102, True), (103, True), (104, False), (105, True))):
        flag["boom"] = boom
        agent._tick = tick
        _request(agent, ts=NOW + (5 + k) * S, wall=1005.0 + k)
    got = [(r["reason"], r["tick"], r["errors"], r["leaned_books"]) for r in _rows(agent)]
    assert got == [("start", 1, 0, 1), ("periodic", 100, 0, 1), ("error", 102, 1, 0), ("error", 105, 3, 0)]


def test_the_row_never_fails_the_request_and_waits_for_the_reply_when_the_request_defers_its_telemetry():
    agent, ref, deferred = _agent(inv=20.0), _agent(inv=20.0), _agent(inv=20.0)
    for a in (agent, ref, deferred):
        _rest_l0(a)
    emitted = agent.emitted
    agent._emit = lambda event, force=False, **kw: (1 / 0) if event == "V627_STATE" else emitted.append((event, kw))
    agent._tick = 1
    assert _out(_request(agent)) == _out(_request(ref)) == ([], [])
    assert agent._v6_27_errors == 1 and _rows(agent) == []
    work = []
    deferred._v629_request_work = types.SimpleNamespace(add=lambda name, fn: work.append((name, fn)))
    deferred._tick = 1
    _request(deferred)
    mine = [fn for n, fn in work if n == "v627_state_row"]
    assert _rows(deferred) == [] and len(mine) == 1
    assert [n for n, _fn in work] == ["v623_state_row", "v625_state_row", "v626_state_row", "v627_state_row"]
    mine[0]()
    row = _rows(deferred)[0]
    assert row["reason"] == "start" and (row["refused_centre"], row["armed"]) == (1, 1)


def test_off_the_row_says_so_and_an_agent_without_the_build_has_none():
    agent = _agent(on=False)
    agent._tick = 1
    _request(agent)
    row = _rows(agent)[0]
    assert (row["touch_centre_on"], row["leaned_books"], row["touch_decisions"], row["armed"], row["errors"]) == \
        (0, 0, 0, 0, 0)
    older = t626._agent()                                                # v6.26's harness: no switch at all
    older._tick = 1
    t626._request(older)
    assert not [e for e, _kw in older.emitted if e == "V627_STATE"]


# ---- 7. wiring -----------------------------------------------------------------------------------------------------------

def test_the_switch_ships_on_and_agrees_with_its_agent_default():
    params = t617._params()
    assert "research_v627_touch_centre=1 " in t617._shipped() + " " and params.count("research_v627_touch_centre=") == 1
    assert 'research_v626_feed_clock=1 \\\nresearch_v627_touch_centre=1 \\\n' in LAUNCHER   # after v6.26's key (v6.28's next)
    init = _src("_init_build_switches")
    lines = (SWITCH_LINE, "self._v6_27_taus: dict[int, float] = {}", "self._v6_27_counts: dict[str, int] = {}",
             "self._v6_27_errors = 0", "self._v6_27_errors_seen = 0", "self._v6_27_reported = False",
             "self._v6_27_erring = False")
    for line in lines:
        assert SIMPLE.count(line) == 1 and line in init, line
    idx = [init.index(x) for x in lines]
    assert init.index("self._v6_26_erring = False") < idx[0] and idx == sorted(idx) and \
        idx[-1] < init.index("self._v69_counts: dict[str, int] = {}")
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_3_0"' in SIMPLE                  # the launcher's arm keys on it


def test_every_v627_name_the_agent_imports_is_the_modules():
    tree = ast.parse(SIMPLE)
    imp = next(n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "research_v627_touch_centre")
    for alias in imp.names:
        assert V627_NAMES[alias.asname or alias.name] is getattr(tc, alias.name), alias.asname
    assert {a.asname or a.name for a in imp.names} == set(V627_NAMES)
    assert SIMPLE.count("from research_v627_touch_centre import (") == 1
    assert t623.V627_IDENTIFIERS == set(V627_NAMES) | {"research_v627_touch_centre"}
    assert t626.V627_IDENTIFIERS is t623.V627_IDENTIFIERS and t625.V627_IDENTIFIERS is t623.V627_IDENTIFIERS


def test_each_line_is_wired_once_in_its_place():
    body = _src("_v63_pass")
    fn = ast.parse(body).body[0]
    s5 = [n for n in ast.walk(fn) if isinstance(n, ast.If) and ast.unparse(n.test) == "v6_25_on and book_id not in v6_23_foreign"]
    assert len(s5) == 1 and not s5[0].orelse
    assert [ast.unparse(s) for s in s5[0].body] == [ast.unparse(ast.parse(x)) for x in (   # v6.38: as this interpreter
        "v6_25_inv, v6_25_fills = self._v6_25_lean(book_id, raw_bid, raw_ask, cfg, inv, now_ts, v6_25_req)",  # writes it
        "if v6_27_on:\n    self._v6_27_note(book_id, now_ts)")]                     # right after S5, inside its if
    for line in (NOTE_LINE, RESET_LINE, REPORT_LINE):
        assert body.count(line) == 1 and SIMPLE.count(line) == 1, line
    assert body.index('v6_25_errors = int(getattr(self, "_v6_25_errors", 0) or 0)') < \
        body.index('v6_27_on = bool(getattr(self, "research_v627_touch_centre", False))') < body.index(RESET_LINE) < \
        body.index("for raw_id in sorted(books") < body.index(NOTE_LINE)
    assert body.index("self._v6_26_report()") < body.index(REPORT_LINE) < body.index("return placed_total")
    assert 'if getattr(self, "research_v627_touch_centre", None) is not None:' in body
    assert body.count("self._v617_book(") == 2 and "v6_27" not in "".join(
        ast.unparse(n) for n in ast.walk(fn) if isinstance(n, ast.Call) and ast.unparse(n.func) == "self._v617_book")
    touch = _src("_v617_touch")
    assert touch.count(BOUND_LINE) == 1 and SIMPLE.count(BOUND_LINE) == 1
    assert touch.index("bound = self._v610_bound_base(deep, paced)") < touch.index("v6_27_wide = None") < \
        touch.index('if bool(getattr(self, "research_v627_touch_centre", False)):') < touch.index(BOUND_LINE) < \
        touch.index("for row in touch_rows:")
    assert touch.count("v617_room(side, inv, clip, bound)") == 1 and touch.count("v617_room(toward, inv, clip, bound)") == 1
    assert touch.index("v617_room(side, inv, clip, bound)") < touch.index('self._v6_27_count("cancel_centre")') < \
        touch.index("if why is None:\n                kept += 1")
    assert touch.index("v617_room(toward, inv, clip, bound)") < touch.index('self._v6_27_count("refused_centre")') < \
        touch.index("V617_TOUCH_SLOT in")
    for name in ("_v633_book", "_v617_book", "_v6_25_lean", "_v6_23_against", "_v6_23_fundamental", "_v617_fundamental",
                 "_v619_side", "_v6_26_observe", "_v610_bound_base", "respond", "_research_final_validate_instructions"):
        text = _src(name)
        assert "v6_27" not in text and not any(n in text for n in t623.V627_IDENTIFIERS), name


# ---- 8. v6.26's code, statement by statement (the committed v6.26: eb25bdf) -------------------------------------------------

V626_COMMIT = "eb25bdf"
# v6.28 (S8, research_v628_flat_launch) only adds -- its import, its _v6_28_ methods, its switch and state, and
# statements naming them where the pace line is built, restored and saved and in the pass -- so the method-by-method
# check reads v6.27 with v6.28 taken out (_Drop628; tests/test_research_v6_28.py strips it against the committed v6.27),
# and t626's _Drop627 takes v6.28's additions out with v6.27's.  v6.29 (S9, research_v629_touch_fp_move, and S8's first
# save) only adds as well: _Drop628 takes its additions out too (tests/test_research_v6_29.py checks them against
# v6.28's footprint).  So does v6.30 (the identity profile, research_v630_deep_tier / research_v630_s9_lookback):
# _Drop628 takes its additions out too (tests/test_research_v6_30.py checks them against the committed v6.29).
V628_IDENTIFIERS = t623.V628_IDENTIFIERS
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
# v6.37 (research_v637_fast_path) only adds: the strip takes its additions out as well
# (tests/test_research_v6_37.py checks them against v6.36).
V6_37_IDENTIFIERS = t623.V6_37_IDENTIFIERS
# v6.38 layer 3 only adds: the strip takes its additions out as well (tests/test_research_v6_38_l3.py).
V6_38_IDENTIFIERS = t623.V6_38_IDENTIFIERS


def _mentions628(node):
    for n in ast.walk(node):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.arg if isinstance(n, (ast.arg, ast.keyword)) else n.value if isinstance(n, ast.Constant) else None
        if isinstance(name, str) and (name in V628_IDENTIFIERS or name in V6_29_IDENTIFIERS
                                      or name in V6_30_IDENTIFIERS or name in V6_31_IDENTIFIERS
                                      or name in V6_32_IDENTIFIERS
                                      or name in V6_33_IDENTIFIERS
                                      or name in V6_34_IDENTIFIERS
                                      or name in V6_36_IDENTIFIERS
                                      or name in V6_37_IDENTIFIERS
                                      or name in V6_38_IDENTIFIERS
                                      or name.startswith(("_v6_28_", "v6_28_", "_v6_29_", "v6_29_", "_v6_30_",
                                                          "v6_30_", "_v6_31_", "v6_31_", "_v6_32_", "v6_32_",
                                                          "_v6_33_", "v6_33_", "_v6_34_", "v6_34_",
                                                          "_v6_36_", "v6_36_", "_v6_37_", "v6_37_", "_v6_38_",
                                                          "v6_38_"))):
            return True
    return False


class _Drop628(ast.NodeTransformer):
    """Takes out what v6.28 (and v6.29 and v6.30 after it) adds and nothing else -- its import, its _v6_28_ methods,
    statements naming its identifiers (an if on one keeps its else) -- keeping every docstring: v6.27 as committed, for
    the method-by-method check against v6.26."""

    def visit_Module(self, node):                      # v6.38's changes undone first (tests/_v638_undo.py)
        _v638_undo.undo(node)
        return self.generic_visit(node)

    def _stmts(self, stmts):
        out = []
        for st in stmts:
            if isinstance(st, ast.ImportFrom) and st.module in ("research_v628_flat_launch", "research_v6_29_touch_move",
                                                                "research_v630_identity_profile",
                                                                "research_v631_touch_improve",
                                                                "research_v6_32_fee_making",
                                                                "research_v6_32_state_fp",
                                                                "research_v6_33_touch_identity",
                                                                "research_v6_34_class1",
                                                                "research_v6_36_step_withdrawal",
                                                                "research_v6_37_fast_path",
                                                                "research_v6_38_canary",
                                                                "research_v6_38_kbl",
                                                                "research_v6_38_lean",
                                                                "research_v6_38_keeper"):
                continue
            if isinstance(st, ast.FunctionDef) and st.name.startswith(("_v6_28_", "_v6_29_", "_v6_30_", "_v6_31_",
                                                                       "_v6_32_", "_v6_33_", "_v6_34_", "_v6_36_",
                                                                       "_v6_37_", "_v6_38_")):
                continue
            if isinstance(st, ast.If) and _mentions628(st.test):
                out.extend(self._stmts(st.orelse))
                continue
            st = self.visit(st)
            if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)) and _mentions628(st):
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


def _v626(rel):
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "show", f"{V626_COMMIT}:{rel}"], cwd=ROOT,
                             capture_output=True, text=True, timeout=120)
    except Exception:
        out = None
    if out is None or out.returncode != 0:
        pytest.skip(f"git or {V626_COMMIT} is not available here")
    return out.stdout


def test_every_v626_statement_is_in_v627_unchanged_and_v627_only_adds():
    """Simple of the committed v6.26 == Simple of v6.27 with v6.27's additions taken out, docstrings and all: no v6.26
    statement, argument, call or branch was changed or dropped."""
    old = _v626("agents/strategy/Strategy1_Research_Simple.py")
    assert not t626._mentions627(ast.parse(old)), "v6.26 already names a v6.27 identifier: the strip would hide a change"
    stripped = t626._Drop627().visit(ast.parse(SIMPLE))
    assert ast.dump(stripped) == ast.dump(ast.parse(old))
    assert not t626._mentions627(stripped)
    assert ast.dump(t626._Drop627().visit(ast.parse(SIMPLE))) != ast.dump(ast.parse(SIMPLE))
    bad = SIMPLE.replace("        if not v617_room(toward, inv, clip, bound):\n",
                         "        if not v617_room(toward, inv, clip, clip):\n")
    assert bad != SIMPLE and ast.dump(t626._Drop627().visit(ast.parse(bad))) != ast.dump(ast.parse(old))


def test_only_the_switch_block_the_pass_and_the_touch_quote_changed_and_five_methods_were_added():
    old = _v626("agents/strategy/Strategy1_Research_Simple.py")

    def methods(tree):
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
        out: dict = {}
        for n in cls.body:
            if isinstance(n, ast.FunctionDef):
                out.setdefault(n.name, []).append(ast.dump(n))
        return out
    assert not _mentions628(ast.parse(old))
    a, b = methods(ast.parse(old)), methods(_Drop628().visit(ast.parse(SIMPLE)))     # v6.27: v6.28, v6.29 out
    assert not _mentions628(_Drop628().visit(ast.parse(SIMPLE)))
    assert set(b) - set(a) == set(METHODS) and not set(a) - set(b)
    changed = {k for k in a if a[k] != b[k]}
    assert changed == {"_init_build_switches", "_v63_pass", "_v617_touch"}, changed
    for k in ("_v617_book", "_v633_book", "_v6_25_lean", "_v6_23_against", "_v6_23_fundamental", "_v617_fundamental",
              "_v617_cancel", "_v6_26_observe", "_v610_bound_base", "_research_final_validate_instructions", "respond"):
        assert a[k] == b[k], k
    for other in ("Strategy1_Research.py", "Strategy1.py", "research_v617_touch_quote.py", "research_v625_lean.py",
                  "research_v626_feed_clock.py", "research_v616_fair_value.py", "research_v641_pace.py",
                  "research_v610_paced_bound.py", "research_v623_validator_063.py"):
        assert _v626(f"agents/strategy/{other}") == read_source(STRATEGY / other), other


def test_the_launcher_only_adds_to_v626s():
    """The committed v6.26 launcher line by line: three lines extended (the flag defaults, the v6_3_0 arm, PARAMS' last
    key), the rest added -- the v6.27 block and its gate entry.  v6.28, v6.29 and v6.30 each extend the same three
    lines and add their block and gate entry."""
    launcher = _v638_undo.launcher_v637(LAUNCHER)               # v6.37's launcher: v6.38's edits undone
    old, new = _v626("run_strategy1_research_simple_multi.sh").splitlines(), launcher.splitlines()
    removed, added = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += old[i1:i2]
        if tag in ("replace", "insert"):
            added += new[j1:j2]
    defaults = next(x for x in old if x.startswith("A19X_BUILD=0;"))
    arm = next(x for x in old if x.startswith("  strategy1_direct_v6_3_0)"))
    assert removed == [defaults, arm, 'research_v626_feed_clock=1"']
    block = []
    for head in ("# v6.27 S7.", "# v6.28 S8.", "# v6.29 S9.", "# v6.30.", "# v6.31.", "# v6.32.", "# v6.33.",
                 "# v6.34.", "# v6.36.", "# v6.37.", "# v6.38 L3."):
        start = launcher.index(head)
        block += launcher[start:launcher.index("\nfi\n", start) + len("\nfi\n")].splitlines() + [""]
    assert sorted(added) == sorted([defaults + "; V6_27_BUILD=0; V6_28_BUILD=0; V6_29_BUILD=0; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0; V6_36_BUILD=0; V6_37_BUILD=0; V6_38_BUILD=0",
                                    arm + "; V6_27_BUILD=1; V6_28_BUILD=1; V6_29_BUILD=1; V6_30_BUILD=1; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1; V6_36_BUILD=1; V6_37_BUILD=1; V6_38_BUILD=1",
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
                                    "research_v637_fast_path=1 \\",
                                    "research_v638_kbl=0 \\",
                                    "research_v638_lean=0 \\",
                                    "research_v638_lean_arm=0 \\",
                                    "research_v638_c1=0 \\",
                                    'research_v638_c1_arm=0"',
                                    "      tests/test_research_v6_27.py \\", "      tests/test_research_v6_28.py \\",
                                    "      tests/test_research_v6_29.py \\", "      tests/test_research_v6_30.py \\",
                                    "      tests/test_research_v6_31.py \\",
                                    "      tests/test_research_v6_32.py \\",
                                    "      tests/test_research_v6_33.py \\",
                                    "      tests/test_research_v6_34.py \\",
                                    "      tests/test_research_v6_36.py \\",
                                    "      tests/test_research_v6_37.py \\",
                                    "      tests/test_research_v6_38_l3.py \\",
                                    *block])


# ---- 9. the launcher -----------------------------------------------------------------------------------------------------

def test_the_v627_block_is_gated_preflighted_and_listed():
    assert "; V6_24_BUILD=0; V6_25_BUILD=0; V6_26_BUILD=0; V6_27_BUILD=0; V6_28_BUILD=0; V6_29_BUILD=0; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0; V6_36_BUILD=0; V6_37_BUILD=0; V6_38_BUILD=0\n" in LAUNCHER
    arm = next(x for x in LAUNCHER.splitlines() if x.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_25_BUILD=1; V6_26_BUILD=1; V6_27_BUILD=1; V6_28_BUILD=1; V6_29_BUILD=1; V6_30_BUILD=1; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1; V6_36_BUILD=1; V6_37_BUILD=1; V6_38_BUILD=1"), arm
    assert LAUNCHER.count('if [[ "${V6_27_BUILD:-0}" == "1" ]]; then') == 1 and LAUNCHER.count("V6_27_BUILD=") == 2
    assert "      tests/test_research_v6_26.py \\\n      tests/test_research_v6_27.py \\\n" in LAUNCHER
    assert LAUNCHER.index('echo "[preflight] v6.26 feed clock PASS') < LAUNCHER.index("# v6.27 S7.") < \
        LAUNCHER.index('echo "[preflight] v6.27 touch centre PASS') < \
        LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')


def _block():
    start = LAUNCHER.index('if [[ "${V6_27_BUILD:-0}" == "1" ]]; then')
    return LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.27 touch centre PASS', start)]


_PATTERN = r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\" \|\| \{\n\s+echo \"ERROR: ([^\"]+)\" >&2"
FILES = ("Strategy1_Research_Simple.py", "research_v627_touch_centre.py")


def test_every_v627_preflight_pattern_is_in_the_file_it_checks():
    pats = re.findall(_PATTERN, _block())
    assert len(pats) == 8, pats
    for needle, name, _msg in pats:
        assert name in FILES and read_source(STRATEGY / name).count(needle) == 1, (needle, name)
    assert {p[0] for p in pats} == {
        "from research_v627_touch_centre import (", SWITCH_LINE, RESET_LINE, NOTE_LINE, BOUND_LINE, REPORT_LINE,
        "if isinstance(ts, bool) or isinstance(now_ts, bool) or int(ts) != int(now_ts):", "return min(b, c)"}


def _run_block(params, agent_path=strategy_view(), build="1"):
    start = LAUNCHER.index("# v6.27 S7.")
    stop = LAUNCHER.index('echo "[preflight] v6.27 touch centre PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_27_BUILD": build}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=120)


def test_the_v627_block_passes_the_shipped_params_and_refuses_a_build_without_the_switch_or_what_it_ties():
    shipped = t617._shipped()
    out = _run_block(shipped)
    assert out.returncode == 0 and out.stderr == "", out.stderr
    assert out.stdout.startswith("[preflight] v6.27 touch centre PASS (S7: while S5 leans a book this request the touch "
                                 "quote's bound is one touch clip"), out.stdout
    for bad in ("research_v627_touch_centre=0", "", "research_v627_touch_centre=true", "research_v627_touch_centre=11"):
        out = _run_block(shipped.replace("research_v627_touch_centre=1", bad))
        assert out.returncode == 1 and "without research_v627_touch_centre=1" in out.stderr and "PASS" not in out.stdout, bad
    for key in ("research_v625_lean=1", "research_v617_touch_quote=1"):
        assert f" {key} " in f" {shipped} ", key
        out = _run_block(f" {shipped} ".replace(f" {key} ", f" {key[:-1]}0 "))
        assert out.returncode == 1 and "it needs research_v625_lean=1 and research_v617_touch_quote=1" in out.stderr, key
    out = _run_block("", build="0")                                                   # another build: not run
    assert out.returncode == 0 and out.stdout == "" and out.stderr == ""


def test_the_v627_block_refuses_a_tree_without_each_line_it_checks(tmp_path):
    cases = re.findall(_PATTERN, _block())
    assert len(cases) == 8
    for i, (needle, name, msg) in enumerate(cases):
        tmp = tmp_path / str(i)
        tmp.mkdir()
        for n in FILES:
            copy_source(STRATEGY / n, tmp / n)
        text = (tmp / name).read_text()
        assert text.count(needle) == 1, needle
        (tmp / name).write_text(text.replace(needle, "pass  # removed", 1))
        out = _run_block(t617._shipped(), agent_path=tmp)
        assert out.returncode == 1 and msg in out.stderr and "PASS" not in out.stdout, (needle, out.stderr)
