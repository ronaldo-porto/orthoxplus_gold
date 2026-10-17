"""v6.29: S9 the touch on the fundamental's move -- research_v629_touch_fp_move -- and S8's first save.

v6.28 + one switch (research_v629_touch_fp_move, default on).  The v6.17 touch quote (S7's centre rule unchanged) rests
only while the fundamental's move over the look-back is not against its side: a BUY touch (the fundamental above the
mid) is withheld -- not placed, and a resting one cancelled by the quote's own checks (TOUCH_FP_MOVE) -- while the
fundamental fell over the look-back, a SELL touch while it rose.  No move, no page that old, a stale or missing page or
an error: no gate (v6.28).  The move is this request's fundamental minus the book's on the page that was current W
sim-s ago: the newest page the agent first saw at or before the request's state time minus W (k71 vm671's reference
FP_H: the page usable H ago), from a short history of the distinct pages (by their own sim time) its feed snapshot
showed.  W is this request's S6 limit (v6.16's MAX_STALE_SIM_S without one).  Only the touch quote: the deep layer, S1,
S5, S6, S7 and S8 are unchanged.  Replay spec: k71 vm671 fmg=4 fmh=30 (fme 0.5 there; any change here).

S8's first save (under research_v628_flat_launch, no switch of its own): the first flat launch S8 records asks for one
session save at the request's own save path, then the normal cadence -- so a fresh UID's flat rates reach its session
file at its first request, not a whole cadence later (k72's gap: a restart in between rebuilt the launch at 2x).

No test here touches the host's caches, the network or a live tree: the agent is t627's harness (t616's injected feed)
with v6.28's and v6.29's methods.
"""
import ast
import bisect
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

import research_v616_fair_value as fv  # noqa: E402
import research_v617_touch_quote as tq  # noqa: E402
import research_v621_young_window as yw  # noqa: E402
import research_v626_feed_clock as fc  # noqa: E402
import research_v628_flat_launch as fl  # noqa: E402
import research_v6_29_touch_move as tm  # noqa: E402
import test_research_v6_16 as t616  # noqa: E402
import test_research_v6_17 as t617  # noqa: E402
import test_research_v6_21 as t621  # noqa: E402
import test_research_v6_22 as t622  # noqa: E402
import test_research_v6_23 as t623  # noqa: E402
import test_research_v6_24 as t624  # noqa: E402
import test_research_v6_25 as t625  # noqa: E402
import test_research_v6_26 as t626  # noqa: E402
import test_research_v6_27 as t627  # noqa: E402
import test_research_v6_28 as t628  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
MODULE = (STRATEGY / "research_v6_29_touch_move.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000
NOW = t623.NOW
SIM = t616.SIM
ABOVE, BELOW = t623.ABOVE, t623.BELOW           # book 3 (100.00 / 100.03): fp 38 bps above / 42 bps below the mid
BUY, SELL = tq.SIDE_BUY, tq.SIDE_SELL
W20 = fv.MAX_STALE_SIM_S                        # v6.16's limit: S6's own until its rate is known
QUOTE_BUY = (3, "BUY", 43031, 100.00, 2.0)      # the touch bid t627's harness places on book 3
QUOTE_SELL = (3, "SELL", 43032, 100.03, 2.0)
SWITCH_LINE = 'self.research_v629_touch_fp_move = self._as_bool(getattr(self.config, "research_v629_touch_fp_move", True))'
HOLD_LINE = "v6_29_held = self._v6_29_held(book_id, fp, cfg)"
CANCEL_LINE = "why = V6_29_CANCEL_FP_MOVE"
WITHHOLD_LINE = "if toward == v6_29_held:"
REPORT_LINE = "self._v6_29_report()"
LAUNCHES_LINE = "v6_29_launches = self._v6_29_launches(pace)"
FIRST_LAUNCH_LINE = "self._v6_29_first_launch(pace)"
FORCE_LINE = 'force = force or bool(getattr(self, "_v6_29_save_due", False))'
SAVED_LINE = "self._v6_29_saved()"
FIELDS = ("version", "touch_fp_move_on", "lookback_sim_s", "pages", "page_resets", "touch_decisions", "fp_fell", "fp_rose",
          "withheld", "cancelled", "no_history", "stale", "zero_move", "s8_save_due", "s8_save_asked", "s8_save_done",
          "errors")
V6_29_NAMES = {"V6_29PageHistory": tm.PageHistory, "V6_29_TOUCH_MOVE_VERSION": tm.V6_29_TOUCH_MOVE_VERSION,
               "V6_29_CANCEL_FP_MOVE": tm.CANCEL_FP_MOVE, "v6_29_launch_records": tm.launch_records,
               "v6_29_lookback": tm.lookback_sim_s}
EXTRA = dict(t628.EXTRA, **V6_29_NAMES)
METHODS = ("_v6_29_held", "_v6_29_count", "_v6_29_launches", "_v6_29_first_launch", "_v6_29_saved", "_v6_29_report",
           "_v6_29_snapshot")


def _page(fp, *, at=NOW, age=8.0, book=3, **kw):
    """t616's page of book ``book``'s fundamental ``fp``, ``age`` sim-s behind the state time ``at``."""
    return t616._page({book: fp}, sim_ts_ns=int(at - age * S), **kw)


# ---- 1. the module ---------------------------------------------------------------------------------------------------

def test_the_module_exports_its_version_rule_and_reasons_and_states_no_number():
    """STRUCTURAL: the look-back is S6's limit (or v6.16's, imported), the side the move's sign -- the module's only numbers
    are 0 / 1 and the ns per second."""
    assert tm.V6_29_TOUCH_MOVE_VERSION == "touch_fp_move_v6_29"
    assert set(tm.__all__) == {"CANCEL_FP_MOVE", "FELL", "NO_HISTORY", "PageHistory", "ROSE", "STALE",
                               "V6_29_TOUCH_MOVE_VERSION", "ZERO_MOVE", "held_side", "launch_records", "lookback_sim_s"}
    assert tm.CANCEL_FP_MOVE == "TOUCH_FP_MOVE"
    assert tm.CANCEL_FP_MOVE not in {tq.CANCEL_FLIPPED, tq.CANCEL_EDGE, tq.CANCEL_LEFT, tq.CANCEL_PACED, tq.CANCEL_BOUND,
                                     tq.CANCEL_NO_FP, tq.CANCEL_SHUT}
    assert (tm.NO_HISTORY, tm.STALE, tm.ZERO_MOVE, tm.FELL, tm.ROSE) == ("no_history", "stale", "zero_move", "fp_fell",
                                                                         "fp_rose")
    assert tm.MAX_STALE_SIM_S is fv.MAX_STALE_SIM_S and (tm.SIDE_BUY, tm.SIDE_SELL) == (BUY, SELL)
    assert "STRUCTURAL:" in MODULE and "OBSERVED:" in MODULE
    tree = ast.parse(MODULE)
    assigned = set()
    for n in tree.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                assigned |= {e.id for e in ([t] if isinstance(t, ast.Name) else t.elts)}
    assert assigned == {"V6_29_TOUCH_MOVE_VERSION", "CANCEL_FP_MOVE", "NO_HISTORY", "STALE", "ZERO_MOVE", "FELL", "ROSE",
                        "__all__"}
    numbers = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
               and not isinstance(n.value, bool)]
    assert set(numbers) <= {0, 1, 1e9}, numbers                    # 1e9: the page and state times are in ns


def test_the_look_back_is_s6s_limit_or_v616s():
    assert tm.lookback_sim_s(32.65) == 32.65 and tm.lookback_sim_s(30) == 30.0 and tm.lookback_sim_s("31.5") == 31.5
    for bad in (None, True, False, 0, 0.0, -1.0, float("nan"), float("inf"), -float("inf"), "x", [], {}):
        assert tm.lookback_sim_s(bad) == W20 == 20.0, bad
    assert tm.lookback_sim_s(fc.stale_limit_sim_s(None)) == W20                       # S6 before its rate is known
    assert tm.lookback_sim_s(fc.stale_limit_sim_s(0.182)) == pytest.approx(20.0 + 0.182 * 55.0)   # 10-13: 30.0 sim-s


def test_the_side_withheld_is_the_one_the_move_is_against():
    for d in (-1e-12, -0.05, -5, "-0.01"):
        assert tm.held_side(d) == BUY, d                            # it fell: a buy touch would lean against it
    for d in (1e-12, 0.05, 5, "0.01"):
        assert tm.held_side(d) == SELL, d
    for d in (0, 0.0, -0.0, None, float("nan"), float("inf"), -float("inf"), True, False, "x", [], {}):
        assert tm.held_side(d) is None, d


def test_the_history_keeps_each_page_once_by_its_own_sim_time_with_its_first_sighting():
    h = tm.PageHistory()
    p1 = _page(100.40, at=NOW - 25 * S, age=5.0)
    assert h.observe(p1, NOW - 25 * S, SIM) is True
    assert h.observe(dict(p1), NOW - 20 * S, SIM) is False         # the same page again: its first sighting stays
    assert h.observe(_page(1.0, at=NOW - 25 * S, age=6.0), NOW - 20 * S, SIM) is False   # an older page
    assert h.observe(_page(100.41, at=NOW - 15 * S, age=3.0), NOW - 15 * S, SIM) is True
    assert [(a, b) for a, b, _fp in h.pages] == [(NOW - 25 * S, NOW - 30 * S), (NOW - 15 * S, NOW - 18 * S)]
    assert h.pages[1][2] == {3: 100.41} and h.sim_id == SIM and h.resets == 0
    for snap in (None, {}, {"fp": None}, "x", {"fp": {3: 1.0}}, dict(p1, sim_ts_ns=None), dict(p1, sim_ts_ns=0),
                 dict(p1, sim_ts_ns=-5), dict(p1, sim_ts_ns="x"), dict(p1, sim_ts_ns=True)):
        assert h.observe(snap, NOW, SIM) is False, snap
    for now in (None, 0, -1, True, "x"):
        assert h.observe(_page(9.0, at=NOW), now, SIM) is False, now
    assert h.observe(_page(9.0, at=NOW, sim_id="20260929_2015"), NOW, SIM) is False   # another simulation's page
    assert len(h.pages) == 2 and h.resets == 0


def test_the_reference_is_the_page_current_a_look_back_ago_and_nothing_older_is_kept():
    t0 = NOW - 100 * S
    h = tm.PageHistory()
    for i, dt in enumerate((0, 10, 20, 35)):
        assert h.observe(_page(100.0 + i, at=t0 + dt * S), t0 + dt * S, SIM)
    assert h.reference(t0 + 19 * S, 20.0) is None and len(h.pages) == 4          # nothing first seen 20 s ago yet
    assert h.reference(t0 + 40 * S, 30.0)[0] == t0 + 10 * S                       # cut at t0 + 10: that one, at it
    assert [p[0] for p in h.pages] == [t0 + 10 * S, t0 + 20 * S, t0 + 35 * S]     # the older page is gone
    assert h.reference(t0 + 40 * S, 30.0)[0] == t0 + 10 * S                       # the same request again: the same
    assert h.reference(t0 + 50 * S, 30.0)[0] == t0 + 20 * S                       # the cut reaches t0 + 20 exactly
    assert h.reference(t0 + 60 * S, 0.0)[0] == t0 + 35 * S and len(h.pages) == 1  # no look-back: the newest page
    for bad in (None, "x", -1.0, float("nan")):
        assert h.reference(t0 + 60 * S, bad) is None, bad
    assert h.reference(None, 20.0) is None and len(h.pages) == 1


def test_a_run_of_stale_pages_keeps_only_what_the_look_back_needs():
    """Pages keep arriving while none is fresh for the request (no fundamental now): each decision still drops what the
    look-back no longer needs, so the history stays a reference and the pages newer than it."""
    h = tm.PageHistory()
    for r in range(200):
        now = NOW + r * S
        if r % 10 == 0:
            assert h.observe(_page(100.0 + r, at=now, age=60.0), now, SIM)
        assert h.decide(3, None, now, W20) == (None, tm.STALE)
        assert len(h.pages) <= 3, r
    assert [p[0] for p in h.pages] == [NOW + 170 * S, NOW + 180 * S, NOW + 190 * S]


def test_a_new_simulation_starts_the_history_over():
    h = tm.PageHistory()
    h.observe(_page(100.40, at=NOW), NOW, SIM)
    assert h.observe(_page(55.0, at=NOW + 10 * S, sim_id="20260929_2015"), NOW + 10 * S, None)   # the state's id unread
    assert [p[2] for p in h.pages] == [{3: 55.0}] and h.sim_id == "20260929_2015" and h.resets == 1
    assert h.observe(_page(56.0, at=100 * S), 100 * S, None)      # the state clock ran back: started over
    assert [p[0] for p in h.pages] == [100 * S] and h.resets == 2
    assert h.reference(100 * S + 30 * S, 20.0)[2] == {3: 56.0}


def test_the_state_clock_running_back_starts_the_history_over_within_one_simulation():
    """No page or state names another simulation (a validator rewind, or ids unread): a state time behind the newest
    sighting alone starts the history over -- the page then shown is kept though its own time is older than every page
    kept before, and it is the reference a look-back later."""
    for sim in (SIM, None):
        h = tm.PageHistory()
        for dt in (0, 10, 20):
            assert h.observe(_page(100.0 + dt, at=NOW + dt * S, sim_id=sim), NOW + dt * S, sim), sim
        back = NOW - 60 * S
        assert h.observe(_page(99.0, at=back, sim_id=sim), back, sim), sim
        assert [p[0] for p in h.pages] == [back] and h.resets == 1, sim
        assert h.decide(3, 99.5, back + 25 * S, W20) == (SELL, tm.ROSE), sim


def test_a_checkpoint_rewind_never_keeps_the_abandoned_runs_page():
    """The validator resumes from its latest checkpoint with the same sim id: the state clock runs back 1,100 -> 1,064
    while the shared cache still shows the abandoned run's last page (1,090, its fundamental above everything the resumed
    run publishes) for six requests.  The history starts over and never records that page (ahead of the clock); the
    resumed run's pages are kept as they come; no gate until one of them was first seen W sim-s earlier, and it is then
    the reference -- the bid is never withheld on the abandoned page's 'fall' (k75's replay: 44 wrong BUY holds)."""
    W = 31.0
    page = lambda t, fp: t616._page({3: fp}, sim_ts_ns=t * S, sim_id=SIM)
    old = {t: 101.0 + 0.01 * (t - 900) for t in range(900, 1100, 10)}     # the abandoned run
    new = {t: 100.0 + 0.01 * (t - 1060) for t in range(1060, 1200, 10)}   # the resumed run: below it, rising
    h = tm.PageHistory()
    for t in range(950, 1100):
        p = max(x for x in old if x + 8 <= t)
        h.observe(page(p, old[p]), t * S, SIM)
    abandoned = page(1090, old[1090])
    first_seen, seen, cached = {}, [], 6
    for t in range(1064, 1150):
        if cached:
            snap, cached = abandoned, cached - 1
        else:
            p = max(x for x in new if x + 8 <= t)
            snap = page(p, new[p])
        if h.observe(snap, t * S, SIM):
            first_seen[snap["sim_ts_ns"] // S] = t
        assert snap is not abandoned or h.pages == [] or h.pages[-1][2] is not abandoned["fp"], t
        fp = fv.book_fundamental(snap, 3, 99.9, 100.1, state_ts_ns=t * S, sim_id=SIM, max_stale_sim_s=W)[0]
        side, why = h.decide(3, fp, t * S, W)
        seen.append((t, side, why))
        assert all(p[2] is not abandoned["fp"] for p in h.pages), t                 # never kept, never the reference
        assert side != BUY, (t, why)
        ready = [ft for ft in first_seen.values() if ft <= t - W]
        assert (side is not None) == bool(ready), (t, side, why)
        if ready:
            assert h.pages[0][0] == max(ready) * S and h.pages[0][1] // S in new, t     # the resumed run's page
    assert h.resets == 1 and first_seen[1060] == 1070 and seen[0] == (1064, None, tm.NO_HISTORY)
    assert next(t for t, side, _why in seen if side) == 1101 and {w for _t, s_, w in seen if s_} == {tm.ROSE}


def test_a_page_ahead_of_the_state_clock_is_recorded_once_the_clock_reaches_it():
    """In normal operation a page never leads the state (the validator publishes it after sending that state); one that
    does is skipped -- no reset, nothing kept -- and recorded at the first request whose state time reaches it, first
    seen then.  A page at the state time itself is recorded."""
    h = tm.PageHistory()
    assert h.observe(_page(100.0, at=NOW, age=8.0), NOW, SIM)
    ahead = t616._page({3: 100.5}, sim_ts_ns=NOW + 5 * S, sim_id=SIM)
    for dt in (1, 2, 4):
        assert h.observe(ahead, NOW + dt * S, SIM) is False, dt
        assert [p[1] for p in h.pages] == [NOW - 8 * S] and h.resets == 0, dt
    assert h.observe(ahead, NOW + 6 * S, SIM) is True
    assert [(p[0], p[1]) for p in h.pages] == [(NOW, NOW - 8 * S), (NOW + 6 * S, NOW + 5 * S)]
    at = tm.PageHistory()
    assert at.observe(t616._page({3: 99.0}, sim_ts_ns=NOW, sim_id=SIM), NOW, SIM) is True     # page time == state time
    assert at.pages[0][:2] == (NOW, NOW)
    assert tm.PageHistory().observe(t616._page({3: 99.0}, sim_ts_ns=NOW + 1, sim_id=SIM), NOW, SIM) is False   # 1 ns


def test_one_books_decision_per_side_and_sign_and_why_there_is_none():
    h = tm.PageHistory()
    h.observe(t616._page({3: 100.40, 5: 99.60, "7": 50.0}, sim_ts_ns=NOW - 40 * S), NOW - 30 * S, SIM)
    now = NOW
    assert h.decide(3, 100.39, now, W20) == (BUY, tm.FELL)
    assert h.decide(3, 100.41, now, W20) == (SELL, tm.ROSE)
    assert h.decide(3, 100.40, now, W20) == (None, tm.ZERO_MOVE)
    assert h.decide(5, 99.59, now, W20) == (BUY, tm.FELL) and h.decide("5", 99.61, now, W20) == (SELL, tm.ROSE)
    assert h.decide(7, 49.0, now, W20) == (BUY, tm.FELL)          # a page keyed by text, as v6.16 reads it
    for fp in (None, 0.0, -1.0, float("nan"), float("inf"), "x", True):
        assert h.decide(3, fp, now, W20) == (None, tm.STALE), fp
    assert h.decide(9, 100.0, now, W20) == (None, tm.NO_HISTORY)  # the reference page has no fundamental for it
    assert h.decide(3, 100.0, NOW - 15 * S, W20) == (None, tm.NO_HISTORY)   # nothing first seen 20 s before then
    assert tm.PageHistory().decide(3, 100.0, now, W20) == (None, tm.NO_HISTORY)


def _vm671(pages, t, H, limit):
    """k71 vm671 (fmg=4, fplag=0, fpown=1, fme -> any change): pages (T usable, S own sim time, fp), oldest first.  The
    current page is the newest with T <= t, fresh while t - S <= the stale limit; FP_H the newest with T <= t - H; the
    side held is the one the sign of ln(fp / fp_H) is against."""
    T = [p[0] for p in pages]
    iv = bisect.bisect_right(T, t) - 1
    if iv < 0 or (t - pages[iv][1]) / 1e9 > limit:
        return None, None
    ih = bisect.bisect_right(T, t - H * 1e9) - 1
    if ih < 0:
        return None, pages[iv][2]
    d = math.log(pages[iv][2] / pages[ih][2])
    return (BUY if d < 0 else SELL if d > 0 else None), pages[iv][2]


def test_the_rule_is_k71s_vm671_reference():
    """A request a sim-s; pages arriving every 5-14 sim-s, 3-12 sim-s old when first seen, the fundamental changing every
    third page (the live pattern: all books at once every ~30 sim-s).  The agent's history, fed by the snapshot each
    request reads (the newest page that has arrived), and v6.16's freshness at the same limit take vm671's decision at
    every request: its usable time T is the request that first saw the page."""
    gaps, lags, steps = (5, 9, 14, 7, 11, 8, 6, 12), (6, 8, 7, 4, 5), (0.03, -0.05, 0.0, 0.01, -0.02, 0.04)
    t0 = NOW - 2_000 * S
    pages, t, fp, k = [], t0, 100.0, 0
    while t < t0 + 900 * S:
        if k % 3 == 0:
            fp = round(fp + steps[(k // 3) % len(steps)], 6)
        pages.append((t, t - lags[k % len(lags)] * S, fp))
        t += gaps[k % len(gaps)] * S
        k += 1
    assert all(b[1] > a[1] for a, b in zip(pages, pages[1:]))     # a page's own sim time names it (v6.16's feed)
    for limit in (W20, 30.0, 32.65):
        h = tm.PageHistory()
        seen = {"held": 0, "zero": 0, "none": 0}
        for r in range(901):
            now = t0 + r * S
            arrived = [p for p in pages if p[0] <= now]
            snap = t616._page({3: arrived[-1][2]}, sim_ts_ns=arrived[-1][1]) if arrived else None
            h.observe(snap, now, SIM)
            got, _why = h.decide(3, fv.book_fundamental(snap, 3, 99.9, 100.1, state_ts_ns=now, sim_id=SIM,
                                                         max_stale_sim_s=limit)[0], now, tm.lookback_sim_s(limit))
            want, cur = _vm671(pages, now, limit, limit)
            assert got == want, (limit, r)
            seen["held" if got else "zero" if cur is not None else "none"] += 1
        assert seen["held"] > 300 and seen["zero"] > 30, (limit, seen)
        assert (seen["none"] > 0) is (limit == W20), (limit, seen)   # pages reach 22 sim-s old: stale only under 20
        assert len(h.pages) <= 1 + math.ceil(limit / min(gaps)), len(h.pages)   # only what the look-back needs


def test_the_launch_records_are_s8s_rates_and_only_those():
    line = fl.FlatLaunchLine(launch=True)
    assert tm.launch_records(line) == 0
    line.observe(3, NOW, 0.0, t621.FLOOR)
    assert tm.launch_records(line) == 1 and line.launch_rates == {3: 1.0}
    for other in (yw.YoungWindowLine(launch=True), None, object(), types.SimpleNamespace(launch_rates="x")):
        assert tm.launch_records(other) is None, other


# ---- 2. the agent: t627's harness with v6.28's and v6.29's methods ---------------------------------------------------

_CLASSES: dict = {}


def _with629(fn, scope):
    f = types.FunctionType(fn.__code__, scope, fn.__name__, fn.__defaults__, fn.__closure__)
    f.__kwdefaults__ = fn.__kwdefaults__
    return f


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
        g = dict(getattr(base, "_v617_touch").__globals__)
        g.update(EXTRA if names is None else names)
        methods["_v617_touch"] = _with629(getattr(base, "_v617_touch"), g)   # it names V6_29_CANCEL_FP_MOVE
        _CLASSES[key] = type("P629", (base,), methods)
    return _CLASSES[key]


def _v628(*, s8=True, launch=False, page="default", **kw):
    """v6.28's harness agent with a fundamental page: t627's (v6.27: S5 on, S6 on, S1's gap 2.3 bps; the page 8 sim-s old
    with book 3's fundamental 38 bps above its mid unless ``page`` says otherwise, None: no feed) with v6.28's methods
    and state (S8 on unless ``s8`` is False; v6.15's launch only with ``launch``) -- no v6.29 attribute at all."""
    agent = t627._agent(page=_page(ABOVE) if page == "default" else page, **kw)
    agent.__class__ = t628._klass(type(agent))
    agent.research_v615_launch = launch
    agent.research_v628_flat_launch = s8
    agent._v6_28_counts, agent._v6_28_errors, agent._v6_28_errors_seen = {}, 0, 0
    agent._v6_28_reported, agent._v6_28_erring = False, False
    return agent


def _agent(*, on=True, names=None, **kw):
    """_v628's agent with v6.29's methods and the v6.29 state as _init_build_switches leaves it."""
    agent = _v628(**kw)
    agent.__class__ = _klass(type(agent), names)
    agent.research_v629_touch_fp_move = on
    agent._v6_29_pages = tm.PageHistory()
    agent._v6_29_lookback = None
    agent._v6_29_counts, agent._v6_29_errors, agent._v6_29_errors_seen = {}, 0, 0
    agent._v6_29_reported, agent._v6_29_erring = False, False
    agent._v6_29_launches_seen, agent._v6_29_save_due = None, False
    agent._v6_29_save_asked = agent._v6_29_save_done = None
    return agent


def _request(agent, *, ts=NOW, wall=1000.0, version=62):
    return t627._request(agent, ts=ts, wall=wall, version=version)


_out = t622._out
_touch, _rest_of, _rest_l0, _rest_touch = t627._touch, t627._rest_of, t627._rest_l0, t627._rest_touch


def _l0_and_touch(side):
    return t627._l0_and_touch(side)


def _seen(agent, fp, *, at=NOW - 25 * S, age=8.0, book=3):
    """The agent first saw a page with ``book``'s fundamental ``fp`` at the state time ``at`` (its page history)."""
    assert agent._v6_29_pages.observe(_page(fp, at=at, age=age, book=book), at, None)


def _pair(*, before=None, seen_at=NOW - 25 * S, now_fp=ABOVE, rest=None, version=62, **kw):
    """S9 on (its history holding a page with book 3's fundamental ``before`` first seen at ``seen_at``, unless None) and
    v6.28 (no v6.29 attribute), both reading ``now_fp`` now: (on, off, out_on, out_off)."""
    page = _page(now_fp) if now_fp is not None else None
    on, off = _agent(page=page, **kw), _v628(page=page, **kw)
    if before is not None:
        _seen(on, before, at=seen_at)
    for a in (on, off):
        if rest is not None:
            rest(a)
    return on, off, _out(_request(on, version=version)), _out(_request(off, version=version))


def _counts(agent):
    return dict(agent._v6_29_counts)


def _rows(agent):
    return [kw for event, kw in agent.emitted if event == "V6_29_STATE"]


def test_a_buy_touch_is_withheld_while_the_fundamental_fell_and_quoted_while_it_rose_or_held():
    """The fundamental above the mid (the touch bids): it fell over the look-back -> no bid (v6.28 bids); it rose or
    did not move -> v6.28's bid.  The deep layer's orders are the same either way."""
    for before, held in ((ABOVE + 0.05, True), (ABOVE + 1e-6, True), (ABOVE - 0.05, False), (ABOVE, False)):
        on, off, got, want = _pair(before=before, rest=_rest_l0)
        assert _touch(want) == ([QUOTE_BUY], []), before
        assert _touch(got) == (([], []) if held else ([QUOTE_BUY], [])), before
        assert _rest_of(got) == _rest_of(want), before
        why = tm.FELL if held else tm.ROSE if before < ABOVE else tm.ZERO_MOVE
        assert _counts(on) == dict({"touch_decisions": 1, why: 1}, **({"withheld": 1} if held else {})), before
        assert on._v6_29_errors == 0 and on._v617_errors == 0 and on._v6_29_lookback == W20, before


def test_a_sell_touch_is_withheld_while_the_fundamental_rose():
    for before, held in ((BELOW - 0.05, True), (BELOW + 0.05, False), (BELOW, False)):
        on, off, got, want = _pair(before=before, now_fp=BELOW, rest=_rest_l0)
        assert _touch(want) == ([QUOTE_SELL], []), before
        assert _touch(got) == (([], []) if held else ([QUOTE_SELL], [])), before
        assert _rest_of(got) == _rest_of(want), before
        why = tm.ROSE if held else tm.FELL if before > BELOW else tm.ZERO_MOVE
        assert _counts(on) == dict({"touch_decisions": 1, why: 1}, **({"withheld": 1} if held else {})), before


def test_a_resting_touch_quote_the_move_is_against_is_cancelled_by_the_quotes_own_checks():
    """A resting bid while the fundamental fell is cancelled (TOUCH_FP_MOVE) and nothing is placed this request; v6.28
    keeps it.  A resting quote the move is with rests either way; mirrored for an offer."""
    for now_fp, side, before, cancel in ((ABOVE, BUY, ABOVE + 0.05, True), (ABOVE, BUY, ABOVE - 0.05, False),
                                         (ABOVE, BUY, ABOVE, False), (BELOW, SELL, BELOW - 0.05, True),
                                         (BELOW, SELL, BELOW + 0.05, False)):
        on, off, got, want = _pair(before=before, now_fp=now_fp, rest=_l0_and_touch(side))
        assert _touch(want) == ([], []), (side, before)
        assert _touch(got) == ([], [(3, 23)] if cancel else []), (side, before)
        assert _rest_of(got) == _rest_of(want), (side, before)
        assert _counts(on).get("cancelled", 0) == int(cancel) and "withheld" not in _counts(on), (side, before)
        if cancel:
            assert on._v617_counts.get("cancel_touch_fp_move") == 1 and on._v617_counts.get("wait_cancel_seen") == 1
            assert "cancel_touch_fp_move" not in off._v617_counts


def _no_gate_cases():
    """(name, page now, the page the agent first saw before or None, when, why): no gate."""
    return (("zero move", _page(ABOVE), ABOVE, NOW - 25 * S, tm.ZERO_MOVE),
            ("no page before", _page(ABOVE), None, None, tm.NO_HISTORY),
            ("first seen inside the look-back", _page(ABOVE), ABOVE + 0.05, NOW - 19 * S, tm.NO_HISTORY),
            ("reference without the book", _page(ABOVE), ("other book", ABOVE + 0.05), NOW - 25 * S, tm.NO_HISTORY),
            ("stale page now", _page(ABOVE, age=30.0), ABOVE + 0.05, NOW - 40 * S, tm.STALE),
            ("no feed", None, ABOVE + 0.05, NOW - 25 * S, tm.STALE),
            ("another simulation's page", _page(ABOVE, sim_id="20260929_2015"), ABOVE + 0.05, NOW - 25 * S, tm.STALE),
            ("no fundamental for the book", _page(ABOVE, book=5), ABOVE + 0.05, NOW - 25 * S, tm.STALE))


def test_no_move_no_page_that_old_or_no_fresh_page_is_v628s_quote():
    """No gate: what reaches the venue and v6.17's tally are v6.28's, request for request, on every resting set and
    inventory; S9 counts why."""
    for name, page, before, at, why in _no_gate_cases():
        for rest in (None, _rest_l0, _l0_and_touch(BUY), _l0_and_touch(SELL)):
            for inv in (None, 20.0, -20.0):
                on, off = _agent(page=page, inv=inv), _v628(page=page, inv=inv)
                if before is not None:
                    if isinstance(before, tuple):
                        _seen(on, before[1], at=at, book=5)
                    else:
                        _seen(on, before, at=at)
                for a in (on, off):
                    if rest is not None:
                        rest(a)
                case = (name, getattr(rest, "__name__", None), inv)
                assert _out(_request(on)) == _out(_request(off)), case
                assert on._v617_counts == off._v617_counts and on._v6_29_errors == 0, case
                assert _counts(on) == {"touch_decisions": 1, why: 1}, case


def test_the_look_back_is_this_requests_s6_limit():
    """S6's rate known (t626's prime: 0.23 sim-s per wall-s): W = its limit, 20 + 0.23 x 55 = 32.65 sim-s -- a page first
    seen 31.65 sim-s ago is too recent, one first seen 33.65 sim-s ago is the reference."""
    limit = fc.stale_limit_sim_s(t626.RATE)
    for ago, held in ((limit - 1.0, False), (limit + 1.0, True)):
        on = _agent()
        _rest_l0(on)
        _seen(on, ABOVE + 0.05, at=NOW - int(round(ago * S)))
        t626._prime(on, ts=NOW, wall=1000.0)
        got = _out(_request(on, ts=NOW, wall=1000.0))
        assert on._v6_26_limit == pytest.approx(limit) and on._v6_29_lookback == on._v6_26_limit, ago
        assert _touch(got) == (([], []) if held else ([QUOTE_BUY], [])), ago
        assert _counts(on) == dict({"touch_decisions": 1, tm.FELL if held else tm.NO_HISTORY: 1},
                                   **({"withheld": 1} if held else {})), ago


def test_without_an_s6_limit_the_look_back_is_v616s():
    """S6 off (no limit is ever set) or before its rate is known (its limit is v6.16's): W = 20 sim-s."""
    for s6 in (False, True):
        for ago, held in ((19.0, False), (21.0, True)):
            on = _agent()
            on.research_v626_feed_clock = s6
            _rest_l0(on)
            _seen(on, ABOVE + 0.05, at=NOW - int(ago * S))
            got = _out(_request(on))
            assert on._v6_26_limit == (W20 if s6 else None) and on._v6_29_lookback == W20, (s6, ago)
            assert _touch(got) == (([], []) if held else ([QUOTE_BUY], [])), (s6, ago)


def test_the_reference_is_the_page_first_seen_a_look_back_ago_not_the_pages_own_time():
    """vm671's usable time, not the pages' own sim times: an old page first seen 10 s ago is no reference though its own
    time is 90 s back; a page first seen 24 s ago is one though its own time is only 10 s before this page's."""
    on = _agent()
    _rest_l0(on)
    _seen(on, ABOVE + 0.05, at=NOW - 10 * S, age=90.0)                   # page time NOW - 100 s
    assert _touch(_out(_request(on))) == ([QUOTE_BUY], []) and _counts(on)[tm.NO_HISTORY] == 1
    on = _agent(page=_page(ABOVE, age=15.0))                           # this page's time: NOW - 15 s
    _rest_l0(on)
    _seen(on, ABOVE + 0.05, at=NOW - 24 * S, age=1.0)                   # page time NOW - 25 s: 10 s before it
    assert _touch(_out(_request(on))) == ([], []) and _counts(on)[tm.FELL] == 1


def test_the_requests_feed_the_history_and_it_keeps_only_what_the_look_back_needs():
    """Request after request (one a sim-s), a page every 10 sim-s (the fundamental falling 0.01 on each of the first
    four, then flat): the history records each page at the request that first sees it, keeps the reference and what is
    newer, and the touch bid is withheld once a page first seen 20 sim-s ago exists -- and only while the fundamental's
    move over the last 20 sim-s is a fall."""
    on = _agent()
    _rest_l0(on)
    placed = []
    for r in range(60):
        ts = NOW + r * S
        fp = ABOVE - 0.01 * (r // 10) if r < 40 else ABOVE - 0.03          # it stops falling at 30 s (page 3)
        on._v616_feed = t616._Feed(_page(fp, at=NOW + (r - r % 10) * S, age=0.0))
        placed.append(bool(_touch(_out(_request(on, ts=ts, wall=1000.0)))[0]))   # one wall stamp: S6 has no rate
        assert [p[0] for p in on._v6_29_pages.pages] == sorted(p[0] for p in on._v6_29_pages.pages)
        assert len(on._v6_29_pages.pages) <= 4, r
        assert all((p[0] - NOW) % (10 * S) == 0 for p in on._v6_29_pages.pages), r   # first seen at its first request
    assert placed[:20] == [True] * 20                               # no page 20 s old yet
    assert placed[20:50] == [False] * 30                            # it fell over the last 20-29 s
    assert placed[50:] == [True] * 10                               # flat for 20 s: zero move
    assert _counts(on)["withheld"] == 30 and _counts(on)[tm.ZERO_MOVE] == 10 and _counts(on)[tm.NO_HISTORY] == 20


def test_the_deep_layer_s1_s5_s6_s7_and_s8_are_unchanged():
    """S9 changes the touch quote only: every other order, every other counter and the state a request leaves behind are
    v6.28's, on every move, inventory, resting set and pacing state, with S1 inactive (62) and active (63)."""
    seen = differs = 0
    for now_fp, before in ((ABOVE, ABOVE + 0.05), (ABOVE, ABOVE - 0.05), (BELOW, BELOW - 0.05), (BELOW, BELOW),
                           (t627.INSIDE, t627.INSIDE + 0.05)):
        for inv in (None, 3.0, 20.0, 47.0, -20.0, -47.0):
            for rest in (None, _rest_l0, t622._rest_doomed, _l0_and_touch(BUY), _l0_and_touch(SELL)):
                for pace in (None, "paced"):
                    for version in (62, 63):
                        kw = dict(pace=t622._aged(t622.U_BOUND), used=t622.U_BOUND + 500.0) if pace else {}
                        on, off, got, want = _pair(before=before, now_fp=now_fp, inv=inv, rest=rest, version=version,
                                                   **kw)
                        case = (now_fp, before, inv, getattr(rest, "__name__", None), pace, version)
                        assert _rest_of(got) == _rest_of(want), case
                        assert t622._fingerprint(on) == t622._fingerprint(off), case
                        assert on._v633_counts == off._v633_counts and on._v6_23_counts == off._v6_23_counts, case
                        assert on._v6_25_taus == off._v6_25_taus and on._v6_25_last == off._v6_25_last, case
                        assert on._v6_26_counts == off._v6_26_counts and on._v6_26_limit == off._v6_26_limit, case
                        assert on._v6_27_taus == off._v6_27_taus and on._v6_27_counts == off._v6_27_counts, case
                        assert on._v6_28_counts == off._v6_28_counts and on._v6_28_errors == 0, case
                        assert on._v6_29_errors == 0 and on._v617_errors == 0, case
                        seen += 1
                        differs += int(_touch(got) != _touch(want))
    assert seen == 5 * 6 * 5 * 2 * 2 and differs > 20                          # and the touch quote does differ


def test_the_gate_comes_after_s7s_bound_and_v6_19s_hold():
    """Past the lean centre S7 refuses the bid on its own: S9's gate is checked after it and claims nothing; a paced book
    places no adding bid (v6.4.1) and S9 never withholds one there -- a paced bid that reduces (inv below the lean) is
    S9's to withhold."""
    on, off, got, want = _pair(before=ABOVE + 0.05, inv=47.0, rest=_rest_l0)
    assert got == want and _touch(got) == ([], [])
    assert on._v6_27_counts.get("refused_centre") == 1 and "withheld" not in _counts(on) and _counts(on)[tm.FELL] == 1
    kw = dict(pace=t622._aged(t622.U_BOUND), used=t622.U_BOUND + 500.0)
    on, off, got, want = _pair(before=ABOVE + 0.05, rest=_rest_l0, inv=47.0, **kw)
    assert got == want and "withheld" not in _counts(on) and on._v617_counts.get("paced_states") == 1
    on, off, got, want = _pair(before=ABOVE + 0.05, rest=_rest_l0, **kw)
    assert _touch(want) == ([QUOTE_BUY], []) and _touch(got) == ([], []) and _counts(on)["withheld"] == 1
    assert _rest_of(got) == _rest_of(want)


def test_off_the_pass_is_v628s_exactly_and_s9_never_runs():
    """Switch off: what reaches the venue, the counters and the state left behind are v6.28's harness agent's (no v6.29
    attribute at all), and S9 reads, records, decides and counts nothing."""
    for now_fp, before in ((ABOVE, ABOVE + 0.05), (BELOW, BELOW - 0.05), (ABOVE, ABOVE), (None, ABOVE)):
        for inv in (None, 20.0, -20.0):
            for rest in (None, _rest_l0, _l0_and_touch(BUY), _l0_and_touch(SELL), t622._rest_doomed):
                for version in (62, 63):
                    page = _page(now_fp) if now_fp is not None else None
                    off, ref = _agent(on=False, page=page, inv=inv), _v628(page=page, inv=inv)
                    _seen(off, before)
                    for a in (off, ref):
                        if rest is not None:
                            rest(a)
                    case = (now_fp, before, inv, getattr(rest, "__name__", None), version)
                    assert _out(_request(off, version=version)) == _out(_request(ref, version=version)), case
                    assert t622._fingerprint(off) == t622._fingerprint(ref), case
                    assert off._v617_counts == ref._v617_counts and off._v633_counts == ref._v633_counts, case
                    assert off._v6_27_counts == ref._v6_27_counts and off._v6_28_counts == ref._v6_28_counts, case
                    assert _counts(off) == {} and off._v6_29_errors == 0 and off._v6_29_lookback is None, case
                    assert len(off._v6_29_pages.pages) == 1, case                  # the seeded page only


def test_an_error_falls_back_to_v628_for_that_book():
    """S9's look-back, history or decision raising: the book has no gate this request (v6.28's quote), the error counts,
    nothing raises into the request."""
    def boom(*a, **k):
        raise RuntimeError("boom")

    class BadPages(tm.PageHistory):
        def decide(self, *a, **k):
            raise RuntimeError("boom")

    for names, setup in ((dict(EXTRA, v6_29_lookback=boom), None), (None, "broken"), (None, "bad decide"),
                         (dict(EXTRA, v6_29_lookback=lambda limit: 1 / 0), None)):
        for before in (ABOVE + 0.05, ABOVE - 0.05):
            for rest in (_rest_l0, _l0_and_touch(BUY)):
                on, off = _agent(names=names), _v628()
                _seen(on, before)
                if setup == "broken":
                    on._v6_29_pages = "broken"
                elif setup == "bad decide":
                    bad = BadPages()
                    bad.pages = list(on._v6_29_pages.pages)
                    on._v6_29_pages = bad
                for a in (on, off):
                    rest(a)
                case = (setup, before, rest.__name__)
                assert _out(_request(on)) == _out(_request(off)), case
                assert on._v6_29_errors == 1 and _counts(on) == {"touch_decisions": 1}, case
    on = _agent(names=dict(EXTRA, V6_29PageHistory=boom))
    on._v6_29_pages = None                                          # no history yet and none can be built
    _rest_l0(on)
    assert _touch(_out(_request(on))) == ([QUOTE_BUY], []) and on._v6_29_errors == 1


def test_a_tally_error_never_changes_a_decision():
    on, ref = _agent(), _agent()
    for a in (on, ref):
        _seen(a, ABOVE + 0.05)
        _rest_l0(a)
    on._v6_29_counts = "x"                                          # every count raises inside S9's tally
    assert _out(_request(on)) == _out(_request(ref)) and _touch(_out(_request(on, ts=NOW + S, wall=1001.0))) == ([], [])
    assert on._v6_29_errors >= 4 and ref._v6_29_errors == 0 and _counts(ref)["withheld"] == 1


# ---- 3. the state row ------------------------------------------------------------------------------------------------

def test_the_state_row_comes_at_the_first_pass_every_100_ticks_and_at_the_first_error():
    flag = {"boom": False}

    def lookback(limit):
        if flag["boom"]:
            raise RuntimeError("boom")
        return tm.lookback_sim_s(limit)
    agent = _agent(names=dict(EXTRA, v6_29_lookback=lookback))
    _rest_l0(agent)
    _seen(agent, ABOVE + 0.05)
    for i, tick in enumerate((1, 2, 99, 100, 101)):
        agent._tick = tick
        _request(agent, ts=NOW + i * S, wall=1000.0 + i)
    rows = _rows(agent)
    assert [(r["reason"], r["tick"]) for r in rows] == [("start", 1), ("periodic", 100)]
    assert tuple(k for k in rows[0] if k not in ("reason", "tick")) == FIELDS
    first, periodic = rows
    assert first["version"] == tm.V6_29_TOUCH_MOVE_VERSION and first["touch_fp_move_on"] == 1
    assert (first["lookback_sim_s"], first["pages"], first["touch_decisions"], first["fp_fell"], first["withheld"]) == \
        (W20, 2, 1, 1, 1)
    assert (first["s8_save_due"], first["s8_save_asked"], first["s8_save_done"], first["errors"]) == (0, None, None, 0)
    assert periodic["touch_decisions"] == 4 and periodic["fp_fell"] == 4 and periodic["withheld"] == 4
    for k, (tick, boom) in enumerate(((102, True), (103, True), (104, False), (105, True))):
        flag["boom"] = boom
        agent._tick = tick
        _request(agent, ts=NOW + (5 + k) * S, wall=1005.0 + k)
    got = [(r["reason"], r["tick"], r["errors"]) for r in _rows(agent)]
    assert got == [("start", 1, 0), ("periodic", 100, 0), ("error", 102, 1), ("error", 105, 3)]


def test_the_row_counts_every_decision_and_why():
    on = _agent(page=None)
    _rest_l0(on)
    on._tick = 1
    _request(on)                                                    # no page: no fundamental
    for i, fp in enumerate((ABOVE, ABOVE - 0.05, ABOVE - 0.05)):    # no history, then it fell, then no move
        on._v616_feed = t616._Feed(_page(fp, at=NOW + (i + 1) * 25 * S))
        on._tick = 2 + i
        _request(on, ts=NOW + (i + 1) * 25 * S, wall=1001.0 + i)
    snap = on._v6_29_snapshot()
    assert (snap["touch_decisions"], snap["stale"], snap["no_history"], snap["fp_fell"], snap["zero_move"]) == \
        (4, 1, 1, 1, 1)
    assert (snap["withheld"], snap["cancelled"], snap["fp_rose"], snap["errors"]) == (1, 0, 0, 0)
    assert snap["lookback_sim_s"] == W20 and 1 <= snap["pages"] <= 2 and snap["page_resets"] == 0


def test_the_row_never_fails_the_request_and_waits_for_the_reply_when_the_request_defers_its_telemetry():
    agent, ref, deferred = _agent(), _agent(), _agent()
    emitted = agent.emitted
    agent._emit = lambda event, force=False, **kw: (1 / 0) if event == "V6_29_STATE" else emitted.append((event, kw))
    agent._tick = 1
    assert _out(_request(agent)) == _out(_request(ref))
    assert agent._v6_29_errors == 1 and _rows(agent) == []
    work = []
    deferred._v629_request_work = types.SimpleNamespace(add=lambda name, fn: work.append((name, fn)))
    deferred._tick = 1
    _request(deferred)
    mine = [fn for n, fn in work if n == "v6_29_state_row"]
    assert _rows(deferred) == [] and len(mine) == 1
    assert [n for n, _fn in work] == ["v623_state_row", "v625_state_row", "v626_state_row", "v627_state_row",
                                      "v628_state_row", "v6_29_state_row"]
    mine[0]()
    row = _rows(deferred)[0]
    assert row["reason"] == "start" and row["touch_decisions"] == 1 and row[tm.NO_HISTORY] == 1


def test_off_the_row_says_so_and_an_agent_without_the_build_has_none():
    agent = _agent(on=False)
    agent._tick = 1
    _request(agent)
    row = _rows(agent)[0]
    assert (row["touch_fp_move_on"], row["touch_decisions"], row["lookback_sim_s"], row["errors"]) == (0, 0, None, 0)
    older = _v628()                                                 # v6.28's harness: no switch at all
    older._tick = 1
    _request(older)
    assert not [e for e, _kw in older.emitted if e == "V6_29_STATE"]


# ---- 4. S8's first save ----------------------------------------------------------------------------------------------

def _launching(**kw):
    """A fresh UID's agent: v6.15's launch on S8's line, its first state reading book 3 empty (t628's case)."""
    kw.setdefault("used", 0.0)
    return _agent(page=None, launch=True, pace=fl.FlatLaunchLine(launch=True), **kw)


def test_a_fresh_uids_first_flat_launch_asks_for_one_session_save():
    agent = _launching()
    agent._tick = 1
    _request(agent)
    assert agent._v66_pace.launch_rates == {3: 1.0} and agent._v6_28_counts == {"flat_clip_book_states": 1}
    assert (agent._v6_29_save_due, agent._v6_29_save_asked, agent._v6_29_save_done) == (True, 1, None)
    assert agent._v6_29_launches_seen == 1 and agent._v6_29_errors == 0
    agent._tick = 2
    _request(agent, ts=NOW + S, wall=1001.0)                        # still due until a save carries it
    assert (agent._v6_29_save_due, agent._v6_29_save_asked) == (True, 1)
    row = _rows(agent)[0]
    assert (row["s8_save_due"], row["s8_save_asked"], row["s8_save_done"]) == (1, 1, None)


def test_the_request_and_its_state_are_v628s():
    """S8's first save changes nothing a request decides: the fresh UID's requests are v6.28's, request after request."""
    on, ref = _launching(), _v628(page=None, launch=True, pace=fl.FlatLaunchLine(launch=True), used=0.0)
    for i in range(3):
        ts = NOW + i * 600 * S
        for a in (on, ref):
            a.reading["used"] = 1_000.0 * i
        assert _out(_request(on, ts=ts, wall=1000.0 + i)) == _out(_request(ref, ts=ts, wall=1000.0 + i)), i
        assert t622._fingerprint(on) == t622._fingerprint(ref), i
    assert on._v6_28_counts == ref._v6_28_counts and on._v615_counts == ref._v615_counts


def test_a_restart_that_restores_its_launches_or_has_none_asks_for_no_save():
    """Restored from a v6.28 record (its flat rates are in the file), from a v6.27 record (no rates), or a book first
    seen with volume (no launch of this process): nothing to save early."""
    t_w = NOW - 3_000 * S
    for name, payload, used in (("v6.28 record", t628._saved(fl.FlatLaunchLine, t_w), 3_000.0),
                                ("v6.27 record", t628._saved(yw.YoungWindowLine, t_w), 3_000.0),
                                ("seen with volume", None, 0.3 * t628.CAP)):
        agent = _launching(used=used)
        agent._v621_restore_payload = None if payload is None else json.loads(json.dumps(payload))
        for i in range(3):
            agent._tick = 1 + i
            _request(agent, ts=NOW + i * S, wall=1000.0 + i)
        assert agent._v6_29_save_due is False and agent._v6_29_save_asked is None, name
        assert agent._v6_29_launches_seen == len(agent._v66_pace.launch_rates), name
    assert t628._saved(fl.FlatLaunchLine, t_w)["books"]["3"]["rate"] == 1.0


def test_one_save_only_then_the_normal_cadence():
    """A later flat launch (a book first seen empty after the first request) asks for nothing more: the periodic save
    carries it."""
    agent = _launching()
    agent._tick = 1
    _request(agent)
    agent._v6_29_saved()                                            # the first save is done
    assert (agent._v6_29_save_due, agent._v6_29_save_done) == (False, 1)
    agent._v66_pace.observe(9, NOW + S, 0.0, t621.FLOOR)            # another launch, recorded flat
    agent._tick = 2
    _request(agent, ts=NOW + S, wall=1001.0)
    assert agent._v66_pace.launch_rates == {3: 1.0, 9: 1.0} and agent._v6_29_launches_seen == 2
    assert (agent._v6_29_save_due, agent._v6_29_save_asked, agent._v6_29_save_done) == (False, 1, 1)


def test_s8_off_its_line_missing_or_an_older_agent_asks_for_no_save():
    for name, agent in (("S8 off", _agent(page=None, launch=True, s8=False, pace=yw.YoungWindowLine(launch=True))),
                        ("v6.27's line", _agent(page=None, launch=True, pace=yw.YoungWindowLine(launch=True)))):
        agent._tick = 1
        _request(agent)
        assert (agent._v6_29_save_due, agent._v6_29_save_asked, agent._v6_29_launches_seen) == (False, None, None), name
    older = _v628(page=None, launch=True, pace=fl.FlatLaunchLine(launch=True), used=0.0)   # no v6.29 state: v6.28's
    _request(older)
    assert older._v66_pace.launch_rates == {3: 1.0} and not hasattr(older, "_v6_29_save_due")


def test_an_error_in_the_first_save_asks_for_nothing_and_never_changes_a_request():
    def boom(*a, **k):
        raise RuntimeError("boom")
    for names in (dict(EXTRA, v6_29_launch_records=boom),):
        on, ref = _launching(names=names), _v628(page=None, launch=True, pace=fl.FlatLaunchLine(launch=True), used=0.0)
        assert _out(_request(on)) == _out(_request(ref))
        assert on._v6_29_save_due is False and on._v6_29_errors == 1
    agent = _launching()
    agent._v6_29_launches_seen = "x"                                # a broken record: compared, it raises
    _request(agent)
    assert agent._v6_29_save_due is False and agent._v6_29_save_asked is None and agent._v6_29_errors == 1


def _session_host():
    """The real _research_save_session / _research_read_session / _v621_session_state, v6.28's _v6_28_save and v6.29's
    _v6_29_saved, compiled in a class whose base stands in for Strategy1_Research with ITS cadence (one save every
    research_session_save_every_n requests, at once without an earlier one, any time when forced)."""
    import textwrap

    class _Base:
        def _research_save_session(self, force=False):
            if getattr(self, "_research_session_identity", None) is None:
                return
            tick = int(getattr(self, "_tick", 0) or 0)
            every = max(1, int(getattr(self, "_research_session_save_every_n", 100)))
            last = int(getattr(self, "_research_session_last_save_tick", -1))
            if not force and last >= 0 and tick - last < every:
                return
            try:
                with open(self.path, "w", encoding="utf-8") as fh:
                    json.dump({"schema": 1, "tick": tick}, fh)
                self._research_session_last_save_tick = tick
            except OSError:
                pass

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
        "_research_save_session", "_research_read_session", "_v621_session_state", "_v6_28_save", "_v6_29_saved")) + "\n"
    exec(compile(body, "<v629-session-host>", "exec"), scope)
    return scope["_Host"]


def _host(path, pace, *, due=False, tick=1, last=0):
    h = _session_host()()
    h.path, h.uid, h._tick = str(path), t621.UID, tick
    h._research_session_identity = types.SimpleNamespace(simulation_id=SIM)
    h._research_session_save_every_n, h._research_session_last_save_tick = 100, last
    h._v503_gate_session_state = lambda: {}
    h._v61_on = lambda: False
    h._v61_lots_from_session = lambda raw: None
    h._v504_mirror, h._v633_deep = None, None
    h.research_v621_pace_persist = True
    h.research_v628_flat_launch = True
    h._v621_errors, h._v621_prepared, h._v621_restore_payload, h._v66_pace = 0, True, None, pace
    h._v6_28_errors = 0
    h._v6_29_save_due, h._v6_29_save_asked, h._v6_29_save_done, h._v6_29_errors = due, (tick if due else None), None, 0
    return h


def test_the_hosts_cadence_is_strategy1_researchs():
    base = (STRATEGY / "Strategy1_Research.py").read_text()
    for line in ('every = max(1, int(getattr(self, "_research_session_save_every_n", 100)))',
                 'last = int(getattr(self, "_research_session_last_save_tick", -1))',
                 "if not force and last >= 0 and tick - last < every:", "self._research_session_last_save_tick = tick",
                 "self._research_save_session(force=False)"):
        assert base.count(line) == 1, line


def test_the_asked_save_is_forced_at_the_requests_save_path_then_the_cadence_runs(tmp_path):
    """Request 1 already saved before its pass (the session sync's on a reset, v5.0.3's gate as it opens), stamped with
    tick 0 -- respond counts the request's own: v6.28's save after the pass skips (the cadence) and so does its pace
    state (no save at this tick), so the flat rate first reaches the file at request 100.  v6.29 forces that one save at
    tick 1; requests 2-100 follow the cadence and 101 is the periodic save."""
    agent = _launching()
    agent._tick = 1
    _request(agent)
    pace = agent._v66_pace
    plain = _host(tmp_path / "v628.json", pace, due=False)
    with open(plain.path, "w", encoding="utf-8") as fh:
        json.dump({"schema": 1, "tick": 0}, fh)                     # the save earlier in request 1
    plain._research_save_session(force=False)
    assert yw.PACE_SESSION_KEY not in json.loads((tmp_path / "v628.json").read_text())   # k72's gap
    h = _host(tmp_path / "v629.json", pace, due=agent._v6_29_save_due)
    h._research_save_session(force=False)
    rows = json.loads((tmp_path / "v629.json").read_text())[yw.PACE_SESSION_KEY]["books"]
    assert rows["3"]["rate"] == 1.0 and (h._v6_29_save_due, h._v6_29_save_done) == (False, 1)
    want = yw.pace_state(pace, uid=t621.UID)
    fl.save_rates(pace, want)
    assert json.loads((tmp_path / "v629.json").read_text())[yw.PACE_SESSION_KEY] == json.loads(json.dumps(want))
    (tmp_path / "v629.json").write_text("{}")
    for tick in range(2, 101):
        h._tick = tick
        h._research_save_session(force=False)
    assert (tmp_path / "v629.json").read_text() == "{}" and h._research_session_last_save_tick == 1   # the cadence
    h._tick = 101
    h._research_save_session(force=False)
    assert json.loads((tmp_path / "v629.json").read_text())["tick"] == 101 and h._v6_29_save_done == 1


def test_a_restart_after_the_first_request_keeps_the_flat_launch(tmp_path):
    """The file the forced save wrote, restored by a new process (v6.21 S2 + v6.28's rates): book 3 is on its flat
    launch -- without the forced save the restart rebuilt it at v6.15's 2x (k72)."""
    agent = _launching()
    agent._tick = 1
    _request(agent)
    h = _host(tmp_path / "a.json", agent._v66_pace, due=True)
    h._research_save_session(force=False)
    payload = json.loads((tmp_path / "a.json").read_text())[yw.PACE_SESSION_KEY]
    line, got = t628._restored(fl.FlatLaunchLine, payload, now=NOW + 60 * S)
    assert got["flat"] == 1 and line.launch_rates == {3: 1.0} and line.rate_of(3) == 1.0


def test_a_failed_save_keeps_the_request_until_one_is_written(tmp_path):
    agent = _launching()
    agent._tick = 1
    _request(agent)
    h = _host(tmp_path / "a.json", agent._v66_pace, due=True)
    h._research_session_identity = None                             # the base saves nothing yet
    h._research_save_session(force=False)
    assert h._v6_29_save_due is True and h._v6_29_save_done is None and not (tmp_path / "a.json").exists()
    h._research_session_identity = types.SimpleNamespace(simulation_id=SIM)
    h.path = str(tmp_path / "missing" / "a.json")                   # the write fails (no such directory)
    h._tick = 2
    h._research_save_session(force=False)
    assert h._v6_29_save_due is True and h._v6_29_save_done is None
    h.path = str(tmp_path / "a.json")
    h._tick = 3
    h._research_save_session(force=False)
    assert (h._v6_29_save_due, h._v6_29_save_done) == (False, 3) and (tmp_path / "a.json").exists()


def test_without_v629_state_the_save_path_is_v628s(tmp_path):
    """t628's host has no v6.29 state: the save is v6.28's -- not forced, nothing marked."""
    agent = _launching()
    agent._tick = 1
    _request(agent)
    h = _host(tmp_path / "a.json", agent._v66_pace)
    for attr in ("_v6_29_save_due", "_v6_29_save_asked", "_v6_29_save_done", "_v6_29_errors"):
        delattr(h, attr)
    with open(h.path, "w", encoding="utf-8") as fh:
        json.dump({"schema": 1, "tick": 0}, fh)
    h._research_save_session(force=False)
    assert yw.PACE_SESSION_KEY not in json.loads((tmp_path / "a.json").read_text())
    assert not hasattr(h, "_v6_29_save_done")


# ---- 5. wiring -------------------------------------------------------------------------------------------------------

def test_the_switch_ships_on_and_agrees_with_its_agent_default():
    params = t617._params()
    assert "research_v629_touch_fp_move=1 " in t617._shipped() + " " and params.count("research_v629_touch_fp_move=") == 1
    assert 'research_v628_flat_launch=1 \\\nresearch_v629_touch_fp_move=1 \\\n' in LAUNCHER    # after v6.28's (v6.30's next)
    for key in ("research_v617_touch_quote=1", "research_v626_feed_clock=1", "research_v628_flat_launch=1",
                "research_v621_pace_persist=1", "research_v616_fair_value=1"):
        assert f" {key} " in f" {t617._shipped()} ", key                                      # what v6.29 sits on
    init = _src("_init_build_switches")
    lines = (SWITCH_LINE, "self._v6_29_pages = V6_29PageHistory()", "self._v6_29_lookback: float | None = None",
             "self._v6_29_counts: dict[str, int] = {}", "self._v6_29_errors = 0", "self._v6_29_errors_seen = 0",
             "self._v6_29_reported = False", "self._v6_29_erring = False",
             "self._v6_29_launches_seen: int | None = None", "self._v6_29_save_due = False",
             "self._v6_29_save_asked: int | None = None", "self._v6_29_save_done: int | None = None")
    for line in lines:
        assert SIMPLE.count(line) == 1 and line in init, line
    idx = [init.index(x) for x in lines]
    assert init.index("self._v6_28_erring = False") < idx[0] and idx == sorted(idx) and \
        idx[-1] < init.index("self._v69_counts: dict[str, int] = {}")
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_3_0"' in SIMPLE


def test_every_v629_name_the_agent_imports_is_the_modules_and_none_clashes_with_v629s_of_v6_2_9():
    tree = ast.parse(SIMPLE)
    imp = next(n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "research_v6_29_touch_move")
    for alias in imp.names:
        assert V6_29_NAMES[alias.asname or alias.name] is getattr(tm, alias.name), alias.asname
    assert {a.asname or a.name for a in imp.names} == set(V6_29_NAMES)
    assert SIMPLE.count("from research_v6_29_touch_move import (") == 1
    assert t623.V6_29_IDENTIFIERS == set(V6_29_NAMES) | {"research_v629_touch_fp_move"}
    for mod in (t624, t625, t626, t627, t628):
        assert mod.V6_29_IDENTIFIERS is t623.V6_29_IDENTIFIERS, mod.__name__
    bound = [a.asname or a.name for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names]
    for name in V6_29_NAMES:
        assert bound.count(name) == 1, name
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    names = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
    assert sorted(n for n in names if n.startswith("_v6_29_")) == sorted(METHODS)
    assert {n for n in names if n.startswith("_v629_")} == {"_v629_defer_on", "_v629_timing_on", "_v629_begin_request",
                                                             "_v629_end_request", "_v629_emit_timing"}   # v6.2.9's own
    assert not [p for p in STRATEGY.glob("research_v629_*.py") if "touch" in p.name]                   # no v6.2.9 name
    assert '"V6_29_STATE"' in _src("_v6_29_report") and '"v6_29_state_row"' in _src("_v6_29_report")
    assert "V629_" not in "".join(_src(n) for n in METHODS)


def test_each_line_is_wired_once_in_its_place():
    for line in (HOLD_LINE, CANCEL_LINE, WITHHOLD_LINE, REPORT_LINE, LAUNCHES_LINE, FIRST_LAUNCH_LINE, FORCE_LINE,
                 SAVED_LINE):
        assert SIMPLE.count(line) == 1, line
    touch = _src("_v617_touch")
    assert touch.index("v6_27_wide, bound = bound, self._v6_27_bound(book_id, clip, bound)") < \
        touch.index("v6_29_held = None") < touch.index('if bool(getattr(self, "research_v629_touch_fp_move", False)):') \
        < touch.index(HOLD_LINE) < touch.index("doomed = []")
    assert touch.index('self._v6_27_count("cancel_centre")') < touch.index("if why is None and side == v6_29_held:") < \
        touch.index(CANCEL_LINE) < touch.index('self._v6_29_count("cancelled")') < touch.index("kept += 1")
    assert touch.index('self._v6_27_count("refused_centre")') < touch.index(WITHHOLD_LINE) < \
        touch.index('self._v6_29_count("withheld")') < touch.index("if V617_TOUCH_SLOT in")
    body = _src("_v63_pass")
    assert body.index('v6_28_on = bool(getattr(self, "research_v628_flat_launch", False))') < \
        body.index("v6_29_launches = None") < \
        body.index('if v6_28_on and getattr(self, "_v6_29_save_due", None) is not None:') < body.index(LAUNCHES_LINE) \
        < body.index("for raw_id in sorted(books")
    assert body.index("self._v6_28_report()") < body.index("if v6_29_launches is not None:") < \
        body.index(FIRST_LAUNCH_LINE) < body.index('if getattr(self, "research_v629_touch_fp_move", None) is not None:') \
        < body.index(REPORT_LINE) < body.index("return placed_total")
    save = _src("_research_save_session")
    assert save.index(FORCE_LINE) < save.index("super()._research_save_session(force=force)")
    assert save.index("os.replace(tmp, path)") < save.index('if getattr(self, "_v6_29_save_due", False):') < \
        save.index(SAVED_LINE) < save.index("except (OSError, TypeError, ValueError, json.JSONDecodeError):")
    for name in ("_v617_book", "_v617_fundamental", "_v617_cancel", "_v633_book", "_v6_25_lean", "_v6_26_observe",
                 "_v621_pass", "_v66_pace_ref", "respond", "_research_final_validate_instructions"):
        text = _src(name)
        assert "v6_29" not in text and not any(n in text for n in t623.V6_29_IDENTIFIERS), name


# ---- 6. v6.28's code, statement by statement (the committed v6.28: 410cdd4) -------------------------------------------

V628_COMMIT = "410cdd4"       # the user's v6.28 commit (10-13 21:45 JST): its 22 paths are k72's verified ones
# v6.30 (the identity profile: D1 research_v630_deep_tier, D2 research_v630_s9_lookback) only adds -- its import, its
# _v6_30_ methods, its switches and state, and statements naming them in the deep layer, the pass and S9's decision (the
# if there keeps its else: v6.29's call) -- so the method-by-method checks below read v6.29 with v6.30 taken out
# (_Drop630; tests/test_research_v6_30.py checks it against the committed v6.29), and t628's _Drop629 takes v6.30's
# additions out with v6.29's.  v6.31 (S12b research_v631_touch_improve, the single-write save
# research_v631_single_write_save) only adds as well: _Drop630 takes its additions out too (tests/test_research_v6_31.py
# checks them against the committed v6.30).
V6_30_IDENTIFIERS = t623.V6_30_IDENTIFIERS
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


def _mentions630(node):
    for n in ast.walk(node):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.arg if isinstance(n, (ast.arg, ast.keyword)) else n.value if isinstance(n, ast.Constant) else None
        if isinstance(name, str) and (name in V6_30_IDENTIFIERS or name in V6_31_IDENTIFIERS
                                      or name in V6_32_IDENTIFIERS
                                      or name in V6_33_IDENTIFIERS
                                      or name in V6_34_IDENTIFIERS
                                      or name.startswith(("_v6_30_", "v6_30_", "_v6_31_", "v6_31_",
                                                          "_v6_32_", "v6_32_", "_v6_33_", "v6_33_",
                                                          "_v6_34_", "v6_34_"))):
            return True
    return False


class _Drop630(ast.NodeTransformer):
    """Takes out what v6.30 (and v6.31 after it) adds and nothing else -- its import, its _v6_30_ methods, statements
    naming its identifiers (an if on one keeps its else) -- keeping every docstring: v6.29 as committed (f72d9a3)."""

    def _stmts(self, stmts):
        out = []
        for st in stmts:
            if isinstance(st, ast.ImportFrom) and st.module in ("research_v630_identity_profile",
                                                                "research_v631_touch_improve",
                                                                "research_v6_32_fee_making",
                                                                "research_v6_32_state_fp",
                                                                "research_v6_33_touch_identity",
                                                                "research_v6_34_class1"):
                continue
            if isinstance(st, ast.FunctionDef) and st.name.startswith(("_v6_30_", "_v6_31_", "_v6_32_", "_v6_33_",
                                                                       "_v6_34_")):
                continue
            if isinstance(st, ast.If) and _mentions630(st.test):
                out.extend(self._stmts(st.orelse))
                continue
            st = self.visit(st)
            if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)) and _mentions630(st):
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
        out = subprocess.run(["git", "-c", "safe.directory=*", "show", f"{V628_COMMIT}:{rel}"], cwd=ROOT,
                             capture_output=True, text=True, timeout=120)
    except Exception:
        out = None
    if out is None or out.returncode != 0:
        pytest.skip(f"git or {V628_COMMIT} is not available here")
    return out.stdout


def _methods(tree):
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    out: dict = {}
    for n in cls.body:
        if isinstance(n, ast.FunctionDef):
            out.setdefault(n.name, []).append(ast.dump(n))
    return out


def test_v629s_strip_takes_out_its_seven_methods_and_its_statements_in_four_and_v627_is_under_both_strips():
    """Without git: Simple with v6.29's additions taken out (t628's _Drop629, v6.30's with them) has the same methods as
    v6.29 (Simple with v6.30 taken out, _Drop630) but v6.29's seven, and only the switch block, the touch quote, the
    pass and the session save carry v6.29 statements; v6.28's own methods name nothing of v6.29; with v6.28 taken out as
    well (t627's _Drop628) it is the committed v6.27."""
    stripped = t628._Drop629().visit(ast.parse(SIMPLE))
    assert not t628._mentions629(stripped)
    a, b = _methods(stripped), _methods(_Drop630().visit(ast.parse(SIMPLE)))
    assert set(b) - set(a) == set(METHODS) and not set(a) - set(b)
    changed = {k for k in a if a[k] != b[k]}
    assert changed == {"_init_build_switches", "_v617_touch", "_v63_pass", "_research_save_session"}, changed
    for n in ast.walk(ast.parse(SIMPLE)):
        if isinstance(n, ast.FunctionDef) and n.name.startswith("_v6_28_"):
            assert not t628._mentions629(n), n.name
    old = t628._v627("agents/strategy/Strategy1_Research_Simple.py")
    assert not t628._mentions629(ast.parse(old))
    assert ast.dump(t627._Drop628().visit(ast.parse(SIMPLE))) == ast.dump(ast.parse(old))


def test_every_v628_statement_is_in_v629_unchanged_and_v629_only_adds():
    """Simple of the committed v6.28 == Simple of v6.29 with v6.29's additions taken out, docstrings and all: no v6.28
    statement, argument, call or branch was changed or dropped."""
    old = _committed("agents/strategy/Strategy1_Research_Simple.py")
    assert not t628._mentions629(ast.parse(old)), "v6.28 already names a v6.29 identifier: the strip would hide a change"
    stripped = t628._Drop629().visit(ast.parse(SIMPLE))
    assert ast.dump(stripped) == ast.dump(ast.parse(old))
    assert not t628._mentions629(stripped)
    assert ast.dump(stripped) != ast.dump(ast.parse(SIMPLE))
    bad = SIMPLE.replace("self._v6_28_save(v621_state)            #", "self._v6_28_save(dict(v621_state))            #")
    assert bad != SIMPLE and ast.dump(t628._Drop629().visit(ast.parse(bad))) != ast.dump(ast.parse(old))


def test_only_the_switch_block_the_touch_quote_the_pass_and_the_save_changed_and_seven_methods_were_added():
    old = _committed("agents/strategy/Strategy1_Research_Simple.py")
    a, b = _methods(ast.parse(old)), _methods(_Drop630().visit(ast.parse(SIMPLE)))     # v6.29: v6.30 taken out
    assert set(b) - set(a) == set(METHODS) and not set(a) - set(b)
    changed = {k for k in a if a[k] != b[k]}
    assert changed == {"_init_build_switches", "_v617_touch", "_v63_pass", "_research_save_session"}, changed
    for k in ("_v617_book", "_v617_fundamental", "_v617_cancel", "_v633_book", "_v6_25_lean", "_v6_26_observe",
              "_v6_27_bound", "_v621_pass", "_v621_session_state", "_v66_pace_ref", "_v6_28_save", "_v6_28_clip",
              "_v6_28_report", "_v6_28_snapshot", "respond", "_research_final_validate_instructions"):
        assert a[k] == b[k], k
    for other in ("Strategy1_Research.py", "Strategy1.py", "research_v628_flat_launch.py", "research_v616_fair_value.py",
                  "research_v617_touch_quote.py", "research_v626_feed_clock.py", "research_v627_touch_centre.py",
                  "research_v625_lean.py", "research_v621_young_window.py", "research_v615_launch.py",
                  "research_v633_deep_layer.py", "research_v629_reply_path.py"):
        assert _committed(f"agents/strategy/{other}") == (STRATEGY / other).read_text(), other


def test_the_launcher_only_adds_to_v628s():
    """The committed v6.28 launcher line by line: three lines extended (the flag defaults, the v6_3_0 arm, PARAMS' last
    key), the rest added -- the v6.29 block and its gate entry.  v6.30 and v6.31 each extend the same three lines and
    add their block, two PARAMS keys and their gate entry."""
    old, new = _committed("run_strategy1_research_simple_multi.sh").splitlines(), LAUNCHER.splitlines()
    removed, added = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += old[i1:i2]
        if tag in ("replace", "insert"):
            added += new[j1:j2]
    defaults = next(x for x in old if x.startswith("A19X_BUILD=0;"))
    arm = next(x for x in old if x.startswith("  strategy1_direct_v6_3_0)"))
    assert removed == [defaults, arm, 'research_v628_flat_launch=1"']
    block = []
    for head in ("# v6.29 S9.", "# v6.30.", "# v6.31.", "# v6.32.", "# v6.33.", "# v6.34."):
        start = LAUNCHER.index(head)
        block += LAUNCHER[start:LAUNCHER.index("\nfi\n", start) + len("\nfi\n")].splitlines() + [""]
    assert sorted(added) == sorted([defaults + "; V6_29_BUILD=0; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0",
                                    arm + "; V6_29_BUILD=1; V6_30_BUILD=1; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1",
                                    "research_v628_flat_launch=1 \\", "research_v629_touch_fp_move=1 \\",
                                    "research_v630_deep_tier=1 \\", "research_v630_s9_lookback=1 \\",
                                    "research_v631_touch_improve=1 \\", "research_v631_single_write_save=1 \\",
                                    "research_v632_fee_making=1 \\",
                                    "research_v632_fee_weight=0.005 \\",
                                    "research_v632_state_fundamental=1 \\",
                                    "research_v633_touch_identity=1 \\",
                                    'research_v634_class1_entry=0"',
                                    "      tests/test_research_v6_29.py \\", "      tests/test_research_v6_30.py \\",
                                    "      tests/test_research_v6_31.py \\",
                                    "      tests/test_research_v6_32.py \\",
                                    "      tests/test_research_v6_33.py \\",
                                    "      tests/test_research_v6_34.py \\", *block])


# ---- 7. the launcher -------------------------------------------------------------------------------------------------

def test_the_v629_block_is_gated_preflighted_and_listed():
    assert "; V6_26_BUILD=0; V6_27_BUILD=0; V6_28_BUILD=0; V6_29_BUILD=0; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0\n" in LAUNCHER
    arm = next(x for x in LAUNCHER.splitlines() if x.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_27_BUILD=1; V6_28_BUILD=1; V6_29_BUILD=1; V6_30_BUILD=1; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1"), arm
    assert LAUNCHER.count('if [[ "${V6_29_BUILD:-0}" == "1" ]]; then') == 1 and LAUNCHER.count("V6_29_BUILD=") == 2
    assert "      tests/test_research_v6_28.py \\\n      tests/test_research_v6_29.py \\\n" in LAUNCHER
    assert LAUNCHER.index('echo "[preflight] v6.28 flat launch PASS') < LAUNCHER.index("# v6.29 S9.") < \
        LAUNCHER.index('echo "[preflight] v6.29 touch on the fp move PASS') < \
        LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')
    assert "V629_BUILD=0" in LAUNCHER and LAUNCHER.count("V629_BUILD=1") >= 1                 # v6.2.9's flag, untouched


def _block():
    start = LAUNCHER.index('if [[ "${V6_29_BUILD:-0}" == "1" ]]; then')
    return LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.29 touch on the fp move PASS', start)]


_PATTERN = r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\" \|\| \{\n\s+echo \"ERROR: ([^\"]+)\" >&2"
FILES = ("Strategy1_Research_Simple.py", "research_v6_29_touch_move.py")


def test_every_v629_preflight_pattern_is_in_the_file_it_checks():
    pats = re.findall(_PATTERN, _block())
    assert len(pats) == 12, pats
    for needle, name, _msg in pats:
        assert name in FILES and (STRATEGY / name).read_text().count(needle) == 1, (needle, name)
    assert {p[0] for p in pats} == {
        "from research_v6_29_touch_move import (", SWITCH_LINE, HOLD_LINE, CANCEL_LINE, WITHHOLD_LINE, REPORT_LINE,
        "return SIDE_BUY if d < 0.0 else SIDE_SELL", "if self.pages[i][0] <= cut:", LAUNCHES_LINE, FIRST_LAUNCH_LINE,
        FORCE_LINE, SAVED_LINE}


def _run_block(params, agent_path=STRATEGY, build="1"):
    start = LAUNCHER.index("# v6.29 S9.")
    stop = LAUNCHER.index('echo "[preflight] v6.29 touch on the fp move PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_29_BUILD": build}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=120)


def test_the_v629_block_passes_the_shipped_params_and_refuses_a_build_without_the_switch_or_what_it_sits_on():
    shipped = t617._shipped()
    out = _run_block(shipped)
    assert out.returncode == 0 and out.stderr == "", out.stderr
    assert out.stdout.startswith("[preflight] v6.29 touch on the fp move PASS (S9: the touch quote toward the "
                                 "fundamental is withheld"), out.stdout
    for bad in ("research_v629_touch_fp_move=0", "", "research_v629_touch_fp_move=true", "research_v629_touch_fp_move=11"):
        out = _run_block(shipped.replace("research_v629_touch_fp_move=1", bad))
        assert out.returncode == 1 and "without research_v629_touch_fp_move=1" in out.stderr and "PASS" not in out.stdout, bad
    for key in ("research_v617_touch_quote=1", "research_v626_feed_clock=1", "research_v628_flat_launch=1",
                "research_v621_pace_persist=1"):
        assert f" {key} " in f" {shipped} ", key
        out = _run_block(f" {shipped} ".replace(f" {key} ", f" {key[:-1]}0 "))
        assert out.returncode == 1 and f"v6.29 build without {key} in PARAMS" in out.stderr, key
        assert "PASS" not in out.stdout, key
    out = _run_block("", build="0")                                                   # another build: not run
    assert out.returncode == 0 and out.stdout == "" and out.stderr == ""


def test_the_v629_block_refuses_a_tree_without_each_line_it_checks(tmp_path):
    cases = re.findall(_PATTERN, _block())
    assert len(cases) == 12
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

