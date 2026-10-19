"""v6.26: S6 the fundamental page's age judged in its own clocks -- research_v626_feed_clock.

v6.25 + one switch (research_v626_feed_clock, default on).  v6.16 calls the validator's fundamental page stale once the
state is more than MAX_STALE_SIM_S = 20 sim-s past the page's sim time (v7.0's number, OBSERVED at ~0.165 sim-s per
wall-s).  The page publishes every 10 sim-s, but the feed that carries it runs on wall time; at today's ~0.23-0.24 sim-s
per wall-s its normal on-schedule cycle crosses 20 sim-s and ~10% of requests saw a stale page: S5 dropped its lean and
S1 its hold, and the agent unwound toward-fp inventory against the fundamental.  S6:

    limit_sim_s = 2 x PAGE_STEP_SIM_S + rate x (FETCH_PERIOD_S + POLL_S + FETCH_TIMEOUT_S)

with the rate (sim-s per wall-s) measured from the agent's own requests (state-time span / wall span over the last 64,
at least 8, a state clock that runs back starting over), v6.16's 20 sim-s until it is known.  Every reader of the page
-- v6.17's touch quote (_v617_fundamental), v6.23 S1 and v6.25 S5 (_v6_23_fundamental) and v6.16's centre
(_v616_book, not reached live: v6.18 centre_off) -- judges the page with that limit.  Off, no limit is set: v6.25.

No test here touches the host's caches or the network: the fundamental comes from t616's injected feed.
"""
import ast
import difflib
import math
import os
import random
import re
import shutil
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v616_fair_value as fv  # noqa: E402
import research_v618_centre_off as co  # noqa: E402
import research_v626_feed_clock as fc  # noqa: E402
import test_research_v6_16 as t616  # noqa: E402
import test_research_v6_17 as t617  # noqa: E402
import test_research_v6_22 as t622  # noqa: E402
import test_research_v6_23 as t623  # noqa: E402
import test_research_v6_25 as t625  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
MODULE = (STRATEGY / "research_v626_feed_clock.py").read_text()
FAIR = (STRATEGY / "research_v616_fair_value.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000
NOW = t623.NOW
SIM = t616.SIM
ABOVE = t623.ABOVE
RATE = 0.23                                     # sim-s per wall-s: the 10-10 / 10-12 simulations' clock (~0.23-0.24)
LIMIT = 2 * 10.0 + RATE * (30.0 + 5.0 + 20.0)   # 32.65 sim-s
SWITCH_LINE = 'self.research_v626_feed_clock = self._as_bool(getattr(self.config, "research_v626_feed_clock", True))'
KEYWORD = 'max_stale_sim_s=getattr(self, "_v6_26_limit", None))'
HOOK_TEST = 'bool(getattr(self, "research_v626_feed_clock", False))'
REPORT_LINE = "self._v6_26_report()"
READERS = ("_v616_book", "_v617_fundamental", "_v6_23_fundamental")
FIELDS = ("version", "feed_clock_on", "rate", "limit_sim_s", "points", "resets", "requests", "rate_unknown", "fresh",
          "rescued", "stale", "no_page", "errors")
V626_NAMES = {"V626SimRate": fc.SimRate, "V626_FEED_CLOCK_VERSION": fc.V626_FEED_CLOCK_VERSION,
              "v626_page_verdict": fc.page_verdict, "v626_stale_limit": fc.stale_limit_sim_s,
              "v626_state_sim_id": fc.state_sim_id}
EXTRA = dict(t625.EXTRA, **V626_NAMES)
METHODS = ("_v6_26_observe", "_v6_26_report", "_v6_26_snapshot")


def _page(age_s, ts=NOW, fp=ABOVE, **kw):
    """t616's page of book 3's fundamental, ``age_s`` sim-s behind the state time ``ts``."""
    return t616._page({3: fp}, sim_ts_ns=int(ts - age_s * S), **kw)


# ---- 1. the module ------------------------------------------------------------------------------------------------------

def test_the_constants_are_the_specified_ones_and_each_is_labelled():
    assert fc.V626_FEED_CLOCK_VERSION == "feed_clock_v6_26"
    assert (fc.PAGE_STEP_SIM_S, fc.PAGE_STEPS, fc.RATE_WINDOW, fc.RATE_MIN_POINTS) == (10.0, 2, 64, 8)
    for name, label in (("PAGE_STEP_SIM_S", "# OBSERVED"), ("PAGE_STEPS", "# STRUCTURAL"), ("WALL_CYCLE_S", "# STRUCTURAL"),
                        ("RATE_WINDOW", "# STRUCTURAL"), ("RATE_MIN_POINTS", "# STRUCTURAL")):
        line = next(x for x in MODULE.splitlines() if x.startswith(name + " = "))
        assert label in line, name
    assert "STRUCTURAL:" in MODULE and "OBSERVED:" in MODULE
    assert set(fc.__all__) == {"FRESH", "NO_FEED", "OTHER_SIM", "PAGE_STEPS", "PAGE_STEP_SIM_S", "RATE_MIN_POINTS",
                               "RATE_WINDOW", "RESCUED", "STALE", "SimRate", "V626_FEED_CLOCK_VERSION", "WALL_CYCLE_S",
                               "page_verdict", "stale_limit_sim_s", "state_sim_id"}


def test_the_wall_cycle_is_the_feeds_own_constants_imported_never_duplicated():
    tree = ast.parse(MODULE)
    imp = next(n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "research_v616_fair_value")
    assert {a.name for a in imp.names} == {"FETCH_PERIOD_S", "FETCH_TIMEOUT_S", "MAX_STALE_SIM_S", "POLL_S",
                                           "same_simulation"}
    assigned = {t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    assert not assigned & {"FETCH_PERIOD_S", "FETCH_TIMEOUT_S", "MAX_STALE_SIM_S", "POLL_S"}
    assert fc.WALL_CYCLE_S == fv.FETCH_PERIOD_S + fv.POLL_S + fv.FETCH_TIMEOUT_S == 55.0
    assert fc.MAX_STALE_SIM_S == fv.MAX_STALE_SIM_S == 20.0
    assert (fv.FETCH_PERIOD_S, fv.POLL_S, fv.FETCH_TIMEOUT_S) == (30.0, 5.0, 20.0)       # the v6.16 feed, unchanged


def test_the_limit_is_two_page_steps_plus_the_feeds_wall_cycle_at_the_measured_rate():
    assert fc.stale_limit_sim_s(RATE) == pytest.approx(LIMIT) and LIMIT == pytest.approx(32.65)
    assert fc.stale_limit_sim_s(0.2328) == pytest.approx(20.0 + 0.2328 * 55.0)          # 10-12 sim: 32.80
    lo, hi = fc.stale_limit_sim_s(0.227), fc.stale_limit_sim_s(0.251)
    assert 32.4 < lo < hi < 33.9                                                       # "~32.5-33.8 at today's rate"
    assert fc.stale_limit_sim_s(0.1646) == pytest.approx(20.0 + 0.1646 * 55.0)          # v7.0's clock: 29.05
    assert fc.stale_limit_sim_s(1.0) == pytest.approx(75.0)
    assert fc.stale_limit_sim_s(0.0) == 20.0 == fv.MAX_STALE_SIM_S                      # a frozen clock: v6.16's
    assert fc.stale_limit_sim_s("0.23") == pytest.approx(LIMIT)                          # a number as text
    for unknown in (None, float("nan"), float("inf"), float("-inf"), "x", "", True, False, [], {}):
        assert fc.stale_limit_sim_s(unknown) == fv.MAX_STALE_SIM_S, unknown
    for negative in (-0.001, -1.0, -1e9):
        assert fc.stale_limit_sim_s(negative) == 20.0, negative                         # clamped at a rate of 0


def test_the_limit_never_falls_below_v616s_and_grows_with_the_rate():
    rates = [i / 100.0 for i in range(0, 301)]
    limits = [fc.stale_limit_sim_s(r) for r in rates]
    assert min(limits) == fv.MAX_STALE_SIM_S and limits == sorted(limits)
    assert all(b - a == pytest.approx(0.01 * 55.0) for a, b in zip(limits, limits[1:]))


def _rate(points, **kw):
    r = fc.SimRate(**kw)
    for w, t in points:
        r.observe(w, t)
    return r


def _clock(n, rate=RATE, wall0=1000.0, ts0=NOW, dw=1.0):
    return [(wall0 + i * dw, ts0 + int(round(i * dw * rate * S))) for i in range(n)]


def test_the_rate_is_the_state_time_span_over_the_wall_span_from_eight_requests_on():
    for n in range(1, 8):
        r = _rate(_clock(n))
        assert r.rate() is None and r.points == n, n                                   # fewer than 8: unknown
    r = _rate(_clock(8))
    assert r.rate() == pytest.approx(RATE) and r.points == 8 and r.resets == 0
    r = _rate(_clock(30, rate=0.2328, dw=3.7))
    assert r.rate() == pytest.approx(0.2328, rel=1e-6)
    pts = [(1000.0, NOW), (1001.0, NOW + 0 * S), (1002.0, NOW + 1 * S), (1010.0, NOW + 1 * S), (1011.0, NOW + 1 * S),
           (1012.0, NOW + 2 * S), (1013.0, NOW + 2 * S), (1020.0, NOW + 4 * S)]
    assert _rate(pts).rate() == pytest.approx(4.0 / 20.0)                             # a span, not a mean of steps
    assert _rate(_clock(9), min_points=9).rate() == pytest.approx(RATE) and _rate(_clock(8), min_points=9).rate() is None


def test_the_window_is_the_last_64_requests():
    slow, fast = _clock(100, rate=0.1), _clock(64, rate=0.3, wall0=1100.0, ts0=NOW + 10 * S)
    r = _rate(slow + fast)
    assert r.points == 64 and r.rate() == pytest.approx(0.3, rel=1e-6)                # only the last 64
    r = _rate(slow + fast[:63])
    w0, t0 = slow[-1]
    w1, t1 = fast[62]
    assert r.points == 64 and r.rate() == pytest.approx((t1 - t0) / 1e9 / (w1 - w0))     # one slow point still in
    assert _rate(_clock(10), window=4).points == 4


def test_a_state_clock_that_runs_back_starts_the_window_over_and_nothing_else_does():
    r = _rate(_clock(20))
    assert r.rate() == pytest.approx(RATE)
    r.observe(1100.0, NOW - 5 * S)                                                     # a new simulation: earlier clock
    assert (r.points, r.resets, r.rate()) == (1, 1, None)
    for w, t in _clock(7, wall0=1101.0, ts0=NOW - 5 * S + int(RATE * S)):
        r.observe(w, t)
    assert r.points == 8 and r.resets == 1 and r.rate() == pytest.approx(RATE)
    same = _rate(_clock(8))
    same.observe(2000.0, same._points[-1][1])                                          # the same state time: no reset
    assert same.resets == 0 and same.points == 9
    junk = _rate(_clock(8))
    for w, t in ((None, NOW), (float("nan"), NOW), (1e4, 0), (1e4, -S), (1e4, None), (True, NOW), (1e4, "x"),
                 (1e4, float("inf"))):
        assert junk.observe(w, t) is False, (w, t)                                   # ignored, never a reset
    assert junk.resets == 0 and junk.points == 8 and junk.rate() == pytest.approx(RATE)


def test_a_wall_span_that_is_not_positive_gives_no_rate_and_a_negative_state_span_reads_zero():
    assert _rate([(1000.0, NOW + i * S) for i in range(8)]).rate() is None              # zero wall span
    assert _rate([(1000.0 - i, NOW + i * S) for i in range(8)]).rate() is None          # wall clock backwards
    assert _rate([(1000.0 + i, NOW) for i in range(8)]).rate() == 0.0                    # a frozen state clock
    r = fc.SimRate()
    r._points.extend((1000.0 + i, NOW + (8 - i) * S) for i in range(8))                 # never built by observe
    assert r.rate() == 0.0                                                            # clamped at 0
    broken = fc.SimRate()
    broken._points = None
    assert broken.rate() is None and broken.observe(1.0, NOW) is False                  # never raises


def test_the_page_verdict_follows_v616s_order_and_splits_v616s_stale_at_the_limit():
    kw = dict(state_ts_ns=NOW, sim_id=SIM, limit_sim_s=LIMIT)
    assert fc.page_verdict(None, **kw) == fc.NO_FEED and fc.page_verdict({}, **kw) == fc.NO_FEED
    assert fc.page_verdict({"fp": [1.0]}, **kw) == fc.NO_FEED
    assert fc.page_verdict(_page(5, sim_id="20260929_2015"), **kw) == fc.OTHER_SIM
    assert fc.page_verdict(_page(5), state_ts_ns=0, sim_id=SIM, limit_sim_s=LIMIT) == fc.STALE
    assert fc.page_verdict(dict(_page(5), sim_ts_ns=None), **kw) == fc.STALE
    for age, want in ((0.0, fc.FRESH), (8.0, fc.FRESH), (20.0, fc.FRESH), (20.001, fc.RESCUED), (25.0, fc.RESCUED),
                      (LIMIT, fc.RESCUED), (LIMIT + 0.001, fc.STALE), (40.0, fc.STALE), (-30.0, fc.FRESH)):
        assert fc.page_verdict(_page(age), **kw) == want, age
    for bad in (None, float("nan"), -1.0, "x", True):
        assert fc.page_verdict(_page(25.0), state_ts_ns=NOW, sim_id=SIM, limit_sim_s=bad) == fc.STALE, bad
        assert fc.page_verdict(_page(19.0), state_ts_ns=NOW, sim_id=SIM, limit_sim_s=bad) == fc.FRESH, bad
    assert fc.page_verdict(_page(25.0), state_ts_ns=NOW, sim_id=SIM, limit_sim_s=10.0) == fc.STALE     # below v6.16's
    assert fc.page_verdict(_page(25.0), state_ts_ns=NOW, sim_id=SIM, limit_sim_s=25.0) == fc.RESCUED   # exactly at it
    assert fc.page_verdict(_page(25.001), state_ts_ns=NOW, sim_id=SIM, limit_sim_s=25.0) == fc.STALE
    assert fc.page_verdict(types.SimpleNamespace(), **kw) == fc.NO_FEED


def test_the_state_sim_id_is_derived_exactly_as_every_reader_of_the_page_derives_it():
    cfg = types.SimpleNamespace
    assert fc.state_sim_id(cfg(simulation_id=SIM)) == SIM
    assert fc.state_sim_id(cfg(simulation_id=None, logDir="/x/y/20261010_0347/")) == "20261010_0347"
    assert fc.state_sim_id(cfg(simulation_id="", logDir="C:\\logs\\20261010_0347")) == "20261010_0347"
    assert fc.state_sim_id(cfg()) is None and fc.state_sim_id(None) is None
    fn = next(n for n in ast.parse(MODULE).body if isinstance(n, ast.FunctionDef) and n.name == "state_sim_id")
    mine = [ast.unparse(s) for s in fn.body[1].body[:2]]                               # inside its try
    for reader in READERS:
        body = ast.parse(_src(reader)).body[0].body
        got = [ast.unparse(s) for s in body if ast.unparse(s).startswith(("sim_id = ", "if not sim_id"))]
        assert got == mine, reader


def test_the_feeds_on_schedule_cycle_stays_inside_the_limit_at_every_rate():
    """A model of the feed as the limit reads it: the page publishes every PAGE_STEP_SIM_S and is visible after a lag of
    less than one step; a fetch starts every FETCH_PERIOD_S wall-s, takes up to FETCH_TIMEOUT_S, and an agent loads the
    written page at its next poll (POLL_S).  The page's age at a request never passes the limit at any rate, while
    v6.16's 20 sim-s is passed in every cycle at today's 0.23 sim-s per wall-s (this model's fetch times are uniform up
    to the timeout, so its share is higher than the ~10% measured live)."""
    def ages(rate, seed):
        rng = random.Random(seed)
        lags = [rng.uniform(0.0, fc.PAGE_STEP_SIM_S * 0.999) for _ in range(20000)]
        n = 120
        fetch = [(j * fv.FETCH_PERIOD_S, rng.uniform(0.0, fv.FETCH_TIMEOUT_S)) for j in range(n)]

        def newest(sim):                                                              # the newest visible page
            k = int(sim // fc.PAGE_STEP_SIM_S)
            while k > 0 and k * fc.PAGE_STEP_SIM_S + lags[k] > sim:
                k -= 1
            return k * fc.PAGE_STEP_SIM_S
        written = sorted((start + dur, newest(rate * start + 500.0)) for start, dur in fetch)
        phase = rng.uniform(0.0, fv.POLL_S)
        out = []
        for i in range(int(fv.FETCH_PERIOD_S * 4), int(fv.FETCH_PERIOD_S * (n - 2))):
            w = float(i)
            poll = phase + math.floor((w - phase) / fv.POLL_S) * fv.POLL_S              # this agent's last poll
            page = max((p for at, p in written if at <= poll), default=None)
            out.append(rate * w + 500.0 - page)
        return out
    for rate in (0.05, 0.1646, 0.23, 0.2328, 0.24, 0.5, 1.0):
        for seed in (1, 2):
            a = ages(rate, seed)
            assert max(a) <= fc.stale_limit_sim_s(rate), (rate, seed, max(a))
    a = ages(0.23, 3)
    share = sum(x > fv.MAX_STALE_SIM_S for x in a) / len(a)
    assert 0.03 < share < 0.5, share                                                  # v6.16's limit: stale every cycle


# ---- 2. book_fundamental: the limit is a keyword, v6.16 without it --------------------------------------------------------

def test_book_fundamental_without_the_keyword_is_v616s_rule():
    kw = dict(state_ts_ns=NOW, sim_id=SIM)
    for age in (0.0, 8.0, 19.999, 20.0, 20.001, 25.0, 32.0, 40.0, 1e6):
        want = (None, "stale") if age > 20.0 else (ABOVE, "armed")
        assert fv.book_fundamental(_page(age), 3, 100.00, 100.03, **kw) == want, age
        assert fv.book_fundamental(_page(age), 3, 100.00, 100.03, max_stale_sim_s=None, **kw) == want, age
    for bad in (float("nan"), float("inf"), -1.0, "x", True, False, [1.0], {}):
        assert fv.book_fundamental(_page(25.0), 3, 100.00, 100.03, max_stale_sim_s=bad, **kw) == (None, "stale"), bad
        assert fv.book_fundamental(_page(19.0), 3, 100.00, 100.03, max_stale_sim_s=bad, **kw) == (ABOVE, "armed"), bad


def test_book_fundamental_honours_the_limit_it_is_handed_and_nothing_else_changes():
    kw = dict(state_ts_ns=NOW, sim_id=SIM, max_stale_sim_s=LIMIT)
    assert fv.book_fundamental(_page(25.0), 3, 100.00, 100.03, **kw) == (ABOVE, "armed")
    assert fv.book_fundamental(_page(LIMIT), 3, 100.00, 100.03, **kw) == (ABOVE, "armed")          # strictly beyond
    assert fv.book_fundamental(_page(LIMIT + 0.01), 3, 100.00, 100.03, **kw) == (None, "stale")
    for at in (25.0, 30.0):                                                          # exactly at the limit: fresh
        assert fv.book_fundamental(_page(at), 3, 100.00, 100.03, state_ts_ns=NOW, sim_id=SIM,
                                   max_stale_sim_s=at) == (ABOVE, "armed"), at
        assert fv.book_fundamental(_page(at + 0.001), 3, 100.00, 100.03, state_ts_ns=NOW, sim_id=SIM,
                                   max_stale_sim_s=at) == (None, "stale"), at
    assert fv.book_fundamental(_page(40.0), 3, 100.00, 100.03, **kw) == (None, "stale")
    assert fv.book_fundamental(_page(25.0), 3, 100.00, 100.03, state_ts_ns=NOW, sim_id=SIM,
                               max_stale_sim_s="32.65") == (ABOVE, "armed")
    assert fv.book_fundamental(_page(5.0), 3, 100.00, 100.03, state_ts_ns=NOW, sim_id=SIM,
                               max_stale_sim_s=0.0) == (None, "stale")
    assert fv.book_fundamental(_page(25.0), 3, 100.00, 100.03, state_ts_ns=0, sim_id=SIM, max_stale_sim_s=LIMIT)[1] == \
        "stale"
    assert fv.book_fundamental(None, 3, 100.00, 100.03, **kw)[1] == "no_feed"
    assert fv.book_fundamental(_page(25.0, sim_id="20260929_2015"), 3, 100.00, 100.03, **kw)[1] == "other_sim"
    assert fv.book_fundamental(_page(25.0), 5, 100.00, 100.03, **kw)[1] == "no_fundamental"
    assert fv.book_fundamental(_page(25.0), 3, 100.03, 100.00, **kw)[1] == "no_book"


def test_a_dead_feed_still_goes_stale():
    """The page stops: the state clock runs on at the measured rate and the page's age passes the limit."""
    page = _page(0.0)
    seen = []
    for k in range(0, 300, 5):                                                       # every 5 wall-s for 5 min
        ts = NOW + int(k * RATE * S)
        seen.append(fv.book_fundamental(page, 3, 100.00, 100.03, state_ts_ns=ts, sim_id=SIM, max_stale_sim_s=LIMIT)[1])
    first = seen.index("stale")
    assert set(seen[:first]) == {"armed"} and set(seen[first:]) == {"stale"}
    assert 140 <= first * 5 <= 145                                                   # 32.65 / 0.23 = 142 wall-s


# ---- 3. the agent: S6 compiled from the real Simple on t625's harness -------------------------------------------------------

def _hook_source():
    """respond()'s v6.26 hook, exactly as respond has it."""
    fn = ast.parse(_src("respond")).body[0]
    hooks = [st for st in fn.body if isinstance(st, ast.If) and ast.unparse(st.test) == HOOK_TEST.replace('"', "'")]
    assert len(hooks) == 1
    return "def _v626_hook(self, state):\n" + textwrap.indent(ast.unparse(hooks[0]), "    ") + "\n"


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
        ns = dict(scope)
        exec(compile(_hook_source(), "<respond v6.26 hook>", "exec"), ns)
        methods["_v626_hook"] = ns["_v626_hook"]
        _CLASSES[key] = type("P626", (base,), methods)
    return _CLASSES[key]


def _agent(*, on=True, names=None, **kw):
    """t625's harness agent (v6.25's pass, S5 on, S1's gap 2.3 bps) with v6.26's methods and respond's hook; the
    v6.26 state as _init_build_switches leaves it."""
    agent = t625._agent(**kw)
    agent.__class__ = _klass(type(agent), names)
    agent.research_v626_feed_clock = on
    agent._v6_26_rate = fc.SimRate()
    agent._v6_26_limit = None
    agent._v6_26_counts, agent._v6_26_errors, agent._v6_26_errors_seen = {}, 0, 0
    agent._v6_26_reported, agent._v6_26_erring = False, False
    return agent


def _hook(agent, *, ts, wall, version=62):
    agent._direct_current_state_timestamp_ns = int(ts)
    agent._direct_request_wall_started = float(wall)
    agent._v626_hook(t623._state(ts=ts, version=version))


def _request(agent, *, ts=NOW, wall=1000.0, version=62, book=None):
    """One request as respond() makes it: its state time and wall stamp, the v6.26 hook, then t625's request (v6.23's
    observation and the pass).  62: S1 inactive; 63: S1 active."""
    _hook(agent, ts=ts, wall=wall, version=version)
    return t625._run(agent, ts=ts, version=version, book=book)


def _prime(agent, *, ts=NOW, wall=1000.0, n=7, rate=RATE):
    """``n`` earlier requests at ``rate`` sim-s per wall-s, one wall-s apart, ending one wall-s before (wall, ts)."""
    for k in range(n, 0, -1):
        _hook(agent, ts=ts - int(round(k * rate * S)), wall=wall - k)


def _rows(agent):
    return [kw for event, kw in agent.emitted if event == "V626_STATE"]


_out = t622._out


def test_the_hook_feeds_the_rate_from_the_requests_wall_stamp_and_state_time():
    agent = _agent()
    _prime(agent)
    assert agent._v6_26_rate.points == 7 and agent._v6_26_limit == fv.MAX_STALE_SIM_S      # unknown: v6.16's limit
    _request(agent)
    assert agent._v6_26_rate.points == 8 and agent._v6_26_rate.rate() == pytest.approx(RATE)
    assert agent._v6_26_limit == pytest.approx(LIMIT)
    _request(agent, ts=NOW + int(10 * 0.5 * S), wall=1010.0)                            # a faster clock moves it
    assert agent._v6_26_limit == pytest.approx(fc.stale_limit_sim_s(agent._v6_26_rate.rate()))
    assert agent._v6_26_limit > LIMIT and agent._v6_26_errors == 0


def test_off_the_hook_does_nothing_and_no_reader_gets_a_limit():
    agent = _agent(on=False, page=_page(25.0))
    _prime(agent)
    out = _request(agent)
    assert agent._v6_26_limit is None and agent._v6_26_rate.points == 0 and agent._v6_26_counts == {}
    ref = t625._agent(page=_page(25.0))                                                 # v6.25's harness
    ref._direct_current_state_timestamp_ns = NOW
    assert _out(out) == _out(t625._run(ref, ts=NOW))
    assert agent._v617_counts == ref._v617_counts and agent._v6_25_taus == ref._v6_25_taus


def test_a_page_25_sim_s_old_is_fresh_at_todays_rate_and_stale_with_the_switch_off():
    """Every live reader: the touch quote (v6.17), S1 (active, 63) and S5's lean read the page; at 0.23 sim-s per wall-s
    a 25-s page is within the limit (32.65) -- the request is exactly the one a fresh page makes -- and with the switch
    off it is v6.16's stale page -- exactly v6.25's request."""
    paced = dict(used=t622.U_BOUND + 500.0)                                          # flat and paced: the lean decides
    for version in (62, 63):
        on, off = _agent(page=_page(25.0), pace=t622._aged(t622.U_BOUND), **paced), \
            _agent(on=False, page=_page(25.0), pace=t622._aged(t622.U_BOUND), **paced)
        fresh = _agent(on=False, page=_page(8.0), pace=t622._aged(t622.U_BOUND), **paced)
        for a in (on, off, fresh):
            _prime(a)
        got = {name: _out(_request(a, version=version)) for name, a in (("on", on), ("off", off), ("fresh", fresh))}
        assert got["on"] == got["fresh"] and got["on"] != got["off"], version
        assert on._v6_25_taus[3][0] == fresh._v6_25_taus[3][0] == pytest.approx(t625._tau(ABOVE))
        assert off._v6_25_taus[3][0] == 0.0
        assert on._v617_counts.get("book_armed") == 1 and "book_stale" not in on._v617_counts
        assert off._v617_counts.get("book_stale") == 1 and "book_armed" not in off._v617_counts
        if version == 63:                                                             # S1's own count (S5 keeps none)
            assert on._v6_23_counts.get("fp_armed") == 1 and "fp_stale" not in on._v6_23_counts
            assert off._v6_23_counts.get("fp_stale") == 1
        else:
            assert on._v6_23_counts.get("fp_armed") is None and off._v6_23_counts.get("fp_stale") is None
        assert on._v617_counts == fresh._v617_counts and on._v6_23_counts == fresh._v6_23_counts, version
        ref = t625._agent(page=_page(25.0), pace=t622._aged(t622.U_BOUND), **paced)
        ref._direct_current_state_timestamp_ns = NOW
        assert got["off"] == _out(t625._run(ref, ts=NOW, version=version)), version


PACED = dict(used=t622.U_BOUND + 500.0)                                          # flat and paced: the lean decides


def test_before_the_rate_is_known_the_limit_is_v616s_exactly():
    for n in (0, 3, 6):
        on = _agent(page=_page(25.0), pace=t622._aged(t622.U_BOUND), **PACED)
        off = _agent(on=False, page=_page(25.0), pace=t622._aged(t622.U_BOUND), **PACED)
        _prime(on, n=n)
        assert _out(_request(on)) == _out(_request(off)) == ([], []), n                 # v6.24's paced flat book
        assert on._v6_26_limit == fv.MAX_STALE_SIM_S and on._v6_25_taus[3][0] == 0.0, n
        assert on._v617_counts == off._v617_counts and on._v617_counts.get("book_stale") == 1, n


def test_a_page_40_sim_s_old_is_stale_even_at_todays_rate():
    on = _agent(page=_page(40.0), pace=t622._aged(t622.U_BOUND), **PACED)
    off = _agent(on=False, page=_page(40.0), pace=t622._aged(t622.U_BOUND), **PACED)
    for a in (on, off):
        _prime(a)
    assert _out(_request(on)) == _out(_request(off)) == ([], [])
    assert on._v6_26_limit == pytest.approx(LIMIT) and on._v6_25_taus[3][0] == 0.0
    assert on._v617_counts.get("book_stale") == 1


def test_a_page_v616_keeps_fresh_is_untouched():
    for age in (0.0, 8.0, 14.6, 20.0):
        on, off = _agent(page=_page(age)), _agent(on=False, page=_page(age))
        for a in (on, off):
            _prime(a)
        for version in (62, 63):
            assert _out(_request(on, version=version)) == _out(_request(off, version=version)), (age, version)
        assert on._v617_counts == off._v617_counts and on._v6_23_counts == off._v6_23_counts, age


def test_a_new_simulation_starts_the_rate_over_and_falls_back_to_v616s_limit():
    agent = _agent(page=_page(25.0))
    _prime(agent)
    _request(agent)
    assert agent._v6_26_limit == pytest.approx(LIMIT)
    back = NOW - 3_600 * S                                                             # the state clock runs back
    agent._v616_feed = t616._Feed(_page(25.0, ts=back))
    out = _request(agent, ts=back, wall=1001.0)
    assert agent._v6_26_rate.resets == 1 and agent._v6_26_rate.points == 1
    assert agent._v6_26_limit == fv.MAX_STALE_SIM_S and agent._v6_25_taus[3][0] == 0.0
    assert agent._v617_counts.get("book_stale") == 1 and agent._v6_26_counts.get("stale") == 8     # 7 primed at 20 s
    assert out is not None


def test_the_dead_v616_centre_reads_the_page_with_the_same_limit():
    """_v616_book is not reached live (v6.18's centre_off ships on), but it reads the page with the request's limit too."""
    params = t617._shipped()
    assert " research_v616_fair_value=1 " in f" {params} " and " research_v618_centre_off=1 " in f" {params} "
    assert co.centre_on(True, True) is False                                          # live: no centre
    agent = t616._fv_agent(_page(25.0))
    for limit, want in ((None, "book_stale"), (fv.MAX_STALE_SIM_S, "book_stale"), (LIMIT, "book_armed")):
        agent._v616_counts = {}
        if limit is not None:
            agent._v6_26_limit = limit
        got = agent._v616_book(3, 100.00, 100.03, 21.5, 0.01, t616._state({}).config)
        assert (got is None) is (want == "book_stale") and agent._v616_counts == {want: 1}, limit


def test_the_live_readers_under_the_shipped_params_are_the_touch_quote_and_s1_s5():
    params = f" {t617._shipped()} "
    for key in ("research_v617_touch_quote=1", "research_v625_lean=1", "research_v623_realized_mode=auto",
                "research_v626_feed_clock=1"):
        assert f" {key} " in params, key
    lean, touch = _src("_v6_25_lean"), _src("_v617_touch")
    assert "self._v6_23_fundamental(book_id, raw_bid, raw_ask, cfg)" in lean                 # S5: every request
    assert "self._v6_23_fundamental(book_id, raw_bid, raw_ask, cfg)" in _src("_v6_23_against")  # S1 when active
    assert "fp = self._v617_fundamental(book_id, raw_bid, raw_ask, cfg)" in touch           # the touch quote
    assert "centre = self._v616_book(book_id, raw_bid, raw_ask, depth, tick_size, cfg)" in _src("_v633_book")


def test_every_reader_of_the_page_hands_book_fundamental_the_requests_limit():
    tree = ast.parse(SIMPLE)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    calls = []
    for fn in cls.body:
        if isinstance(fn, ast.FunctionDef):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and ast.unparse(node.func) == "v616_book_fundamental":
                    calls.append((fn.name, node))
    assert sorted(name for name, _n in calls) == sorted(READERS)
    for name, node in calls:
        kws = {k.arg: ast.unparse(k.value) for k in node.keywords}
        assert kws == {"state_ts_ns": "int(getattr(self, '_direct_current_state_timestamp_ns', 0) or 0)",
                       "sim_id": "sim_id", "max_stale_sim_s": "getattr(self, '_v6_26_limit', None)"}, name
    assert SIMPLE.count(KEYWORD) == 3 and SIMPLE.count("v616_book_fundamental(") == 3


# ---- 4. the tally, the state row and errors ------------------------------------------------------------------------------

def test_each_request_is_tallied_by_its_pages_verdict():
    agent = _agent(page=_page(25.0))
    _prime(agent)
    _request(agent)
    assert agent._v6_26_counts == {"requests": 8, "rate_unknown": 7, "stale": 7, "rescued": 1}   # 23.4-24.8 s at 20
    agent._v616_feed = t616._Feed(_page(40.0, ts=NOW + int(RATE * S)))
    _request(agent, ts=NOW + int(RATE * S), wall=1001.0)
    agent._v616_feed = t616._Feed(_page(3.0, ts=NOW + int(2 * RATE * S)))
    _request(agent, ts=NOW + int(2 * RATE * S), wall=1002.0)
    agent._v616_feed = t616._Feed(None)
    _request(agent, ts=NOW + int(3 * RATE * S), wall=1003.0)
    agent._v616_feed = t616._Feed(_page(3.0, ts=NOW + int(4 * RATE * S), sim_id="20260929_2015"))
    _request(agent, ts=NOW + int(4 * RATE * S), wall=1004.0)
    assert agent._v6_26_counts == {"requests": 12, "rate_unknown": 7, "stale": 8, "rescued": 1, "fresh": 1,
                                   "no_feed": 1, "other_sim": 1}
    snap = agent._v6_26_snapshot()
    assert (snap["requests"], snap["rate_unknown"], snap["fresh"], snap["rescued"], snap["stale"], snap["no_page"]) == \
        (12, 7, 1, 1, 8, 2)


def test_the_state_row_comes_at_the_first_pass_every_100_ticks_and_at_the_first_error():
    agent = _agent(page=_page(25.0))
    for i, tick in enumerate((1, 2, 99, 100, 101)):
        agent._tick = tick
        _request(agent, ts=NOW + int(i * RATE * S), wall=1000.0 + i)
    rows = _rows(agent)
    assert [(r["reason"], r["tick"]) for r in rows] == [("start", 1), ("periodic", 100)]
    assert tuple(k for k in rows[0] if k not in ("reason", "tick")) == FIELDS
    first, periodic = rows
    assert first["version"] == fc.V626_FEED_CLOCK_VERSION and first["feed_clock_on"] == 1
    assert (first["rate"], first["limit_sim_s"], first["points"], first["resets"]) == (None, 20.0, 1, 0)
    assert (first["requests"], first["rate_unknown"], first["stale"], first["errors"]) == (1, 1, 1, 0)
    assert (periodic["points"], periodic["requests"], periodic["rate_unknown"]) == (4, 4, 4)
    for i, tick in enumerate(range(102, 106)):
        agent._tick = tick
        _request(agent, ts=NOW + int((5 + i) * RATE * S), wall=1005.0 + i)
    agent._tick = 200
    _request(agent, ts=NOW + int(9 * RATE * S), wall=1009.0)
    row = _rows(agent)[-1]
    assert (row["reason"], row["tick"], row["points"]) == ("periodic", 200, 10)
    assert row["rate"] == pytest.approx(RATE, abs=1e-5) and row["limit_sim_s"] == pytest.approx(LIMIT, abs=1e-3)


class _BoomRate:
    points, resets = 3, 0

    def observe(self, *a):
        raise RuntimeError("boom")

    def rate(self):
        return None


def test_an_error_falls_back_to_v616s_limit_never_fails_the_request_and_rows_once_until_a_clean_one():
    agent, off = _agent(page=_page(25.0)), _agent(on=False, page=_page(25.0))
    _prime(agent)
    agent._tick = 1
    _request(agent)
    assert agent._v6_26_limit == pytest.approx(LIMIT)
    good = agent._v6_26_rate
    agent._v6_26_rate = _BoomRate()
    for tick in (2, 3):
        agent._tick = tick
        assert _out(_request(agent, wall=1000.0 + tick)) == _out(_request(off, wall=1000.0 + tick))   # v6.16's limit
        assert agent._v6_26_limit == fv.MAX_STALE_SIM_S and agent._v6_25_taus[3][0] == 0.0
    assert agent._v6_26_errors == 2
    agent._v6_26_rate = good
    agent._tick = 4
    _request(agent, ts=NOW + int(4 * RATE * S), wall=1004.0)
    assert agent._v6_26_limit == pytest.approx(LIMIT) and agent._v6_25_taus[3][0] != 0.0
    agent._v6_26_rate = _BoomRate()
    agent._tick = 5
    _request(agent, ts=NOW + int(5 * RATE * S), wall=1005.0)
    assert [(r["reason"], r["tick"], r["errors"]) for r in _rows(agent)] == [("start", 1, 0), ("error", 2, 1),
                                                                            ("error", 5, 3)]
    agent._v6_26_rate = None                                                          # rebuilt on first use
    _request(agent, ts=NOW + int(6 * RATE * S), wall=1006.0)
    assert isinstance(agent._v6_26_rate, fc.SimRate) and agent._v6_26_rate.points == 1


def test_a_tally_error_only_counts_and_keeps_the_limit():
    boom = dict(EXTRA, v626_page_verdict=lambda *a, **k: 1 / 0)
    agent = _agent(page=_page(25.0), names=boom)
    _prime(agent)
    _request(agent)
    assert agent._v6_26_errors == 8 and agent._v6_26_counts == {}
    assert agent._v6_26_limit == pytest.approx(LIMIT) and agent._v6_25_taus[3][0] != 0.0


def test_the_row_never_fails_the_request_and_waits_for_the_reply_when_the_request_defers_its_telemetry():
    agent = _agent(page=_page(25.0))
    emitted = agent.emitted
    agent._emit = lambda event, force=False, **kw: (1 / 0) if event == "V626_STATE" else emitted.append((event, kw))
    agent._tick = 1
    assert _out(_request(agent)) == _out(_request(_agent(on=False, page=_page(25.0))))
    assert agent._v6_26_errors == 1 and _rows(agent) == []
    deferred = _agent(page=_page(25.0))
    work = []
    deferred._v629_request_work = types.SimpleNamespace(add=lambda name, fn: work.append((name, fn)))
    deferred._tick = 1
    _request(deferred)
    mine = [fn for n, fn in work if n == "v626_state_row"]
    assert _rows(deferred) == [] and len(mine) == 1
    assert [n for n, _fn in work] == ["v623_state_row", "v625_state_row", "v626_state_row"]
    mine[0]()
    assert [r["reason"] for r in _rows(deferred)] == ["start"]


def test_off_the_row_says_so_and_an_agent_without_the_build_has_none():
    agent = _agent(on=False)
    agent._tick = 1
    _request(agent)
    row = _rows(agent)[0]
    assert (row["feed_clock_on"], row["rate"], row["limit_sim_s"], row["points"], row["requests"], row["errors"]) == \
        (0, None, None, 0, 0, 0)
    older = t625._agent()                                                              # v6.25's harness: no switch
    older._tick = 1
    t625._run(older)
    assert not [e for e, _kw in older.emitted if e == "V626_STATE"]


# ---- 5. wiring -----------------------------------------------------------------------------------------------------------

def test_the_switch_ships_on_and_agrees_with_its_agent_default():
    params = t617._params()
    assert "research_v626_feed_clock=1 " in t617._shipped() + " " and params.count("research_v626_feed_clock=") == 1
    assert 'research_v625_lean_cap=48 \\\nresearch_v626_feed_clock=1 \\\n' in LAUNCHER      # after v6.25's keys (v6.27's next)
    init = _src("_init_build_switches")
    lines = (SWITCH_LINE, "self._v6_26_rate = V626SimRate()", "self._v6_26_limit: float | None = None",
             "self._v6_26_counts: dict[str, int] = {}", "self._v6_26_errors = 0", "self._v6_26_errors_seen = 0",
             "self._v6_26_reported = False", "self._v6_26_erring = False")
    for line in lines:
        assert SIMPLE.count(line) == 1 and line in init, line
    idx = [init.index(x) for x in lines]
    assert init.index("self._v6_25_erring = False") < idx[0] and idx == sorted(idx) and \
        idx[-1] < init.index("self._v69_counts: dict[str, int] = {}")
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_3_0"' in SIMPLE                  # the launcher's arm keys on it


def test_every_v626_name_the_agent_imports_is_the_modules():
    tree = ast.parse(SIMPLE)
    imp = next(n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "research_v626_feed_clock")
    for alias in imp.names:
        assert V626_NAMES[alias.asname or alias.name] is getattr(fc, alias.name), alias.asname
    assert {a.asname or a.name for a in imp.names} == set(V626_NAMES)
    assert SIMPLE.count("from research_v626_feed_clock import (") == 1
    assert t623.V626_IDENTIFIERS == set(V626_NAMES) | {"research_v626_feed_clock"}
    assert t623.V626_KEYWORDS == {"max_stale_sim_s"}


def test_the_hook_sits_where_respond_sets_the_requests_state_time_and_the_row_ends_the_pass():
    respond = _src("respond")
    body = ast.parse(respond).body[0].body
    first = [ast.unparse(s) for s in body[:4]]
    assert first[0] == "self._direct_current_state_timestamp_ns = int(getattr(state, 'timestamp', 0) or 0)"
    assert first[1] == "self._direct_request_wall_started = time.perf_counter()"
    assert first[2] == "if " + HOOK_TEST.replace('"', "'") + ":\n    self._v6_26_observe(state)"
    assert first[3].startswith("try:\n    self._v6_23_observe(state)")
    assert SIMPLE.count("self._v6_26_observe(state)") == 1
    pass_ = _src("_v63_pass")
    assert pass_.count(REPORT_LINE) == 1 and SIMPLE.count(REPORT_LINE) == 1
    assert pass_.index("self._v6_25_report(v6_25_req, v6_25_errors)") < pass_.index(REPORT_LINE) < \
        pass_.index("return placed_total")
    assert 'if getattr(self, "research_v626_feed_clock", None) is not None:' in pass_
    for name in ("_v633_book", "_v617_touch", "_v617_book", "_v6_23_against", "_v6_25_lean"):
        assert "_v6_26_" not in _src(name), name


# ---- 6. v6.25's code, statement by statement (the committed v6.25: 68dffca) -------------------------------------------------

V625_COMMIT = "68dffca"
# v6.27 (S7, research_v627_touch_centre) only adds -- its import, its _v6_27_ methods, its switch and state, and
# statements naming them in the pass and the touch quote -- so the method-by-method check reads v6.26 with v6.27 taken
# out (_Drop627; tests/test_research_v6_27.py strips it against the committed v6.26).  v6.28 (S8,
# research_v628_flat_launch) only adds as well: _Drop627 takes its additions out too (tests/test_research_v6_28.py
# strips them against the committed v6.27).  So does v6.29 (S9, research_v629_touch_fp_move, and S8's first save):
# _Drop627 takes its additions out too (tests/test_research_v6_29.py checks them against v6.28's footprint).  And v6.30
# (the identity profile, research_v630_deep_tier / research_v630_s9_lookback): _Drop627 takes its additions out too
# (tests/test_research_v6_30.py checks them against the committed v6.29).
V627_IDENTIFIERS = t623.V627_IDENTIFIERS
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


def _mentions627(node):
    for n in ast.walk(node):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.arg if isinstance(n, (ast.arg, ast.keyword)) else n.value if isinstance(n, ast.Constant) else None
        if isinstance(name, str) and (name in V627_IDENTIFIERS or name in V628_IDENTIFIERS
                                      or name in V6_29_IDENTIFIERS or name in V6_30_IDENTIFIERS
                                      or name in V6_31_IDENTIFIERS
                                      or name in V6_32_IDENTIFIERS
                                      or name in V6_33_IDENTIFIERS
                                      or name in V6_34_IDENTIFIERS
                                      or name in V6_36_IDENTIFIERS
                                      or name.startswith(("_v6_27_", "v6_27_", "_v6_28_", "v6_28_", "_v6_29_",
                                                          "v6_29_", "_v6_30_", "v6_30_", "_v6_31_", "v6_31_",
                                                          "_v6_32_", "v6_32_", "_v6_33_", "v6_33_",
                                                          "_v6_34_", "v6_34_", "_v6_36_", "v6_36_"))):
            return True
    return False


class _Drop627(ast.NodeTransformer):
    """Takes out what v6.27 (and v6.28, v6.29 and v6.30 after it) adds and nothing else -- its import, its _v6_27_
    methods, statements naming its identifiers (an if on one keeps its else) -- keeping every docstring: v6.26 as
    committed, for the method-by-method check against v6.25."""

    def _stmts(self, stmts):
        out = []
        for st in stmts:
            if isinstance(st, ast.ImportFrom) and st.module in ("research_v627_touch_centre", "research_v628_flat_launch",
                                                                "research_v6_29_touch_move",
                                                                "research_v630_identity_profile",
                                                                "research_v631_touch_improve",
                                                                "research_v6_32_fee_making",
                                                                "research_v6_32_state_fp",
                                                                "research_v6_33_touch_identity",
                                                                "research_v6_34_class1",
                                                                "research_v6_36_step_withdrawal"):
                continue
            if isinstance(st, ast.FunctionDef) and st.name.startswith(("_v6_27_", "_v6_28_", "_v6_29_", "_v6_30_",
                                                                       "_v6_31_", "_v6_32_", "_v6_33_", "_v6_34_",
                                                                       "_v6_36_")):
                continue
            if isinstance(st, ast.If) and _mentions627(st.test):
                out.extend(self._stmts(st.orelse))
                continue
            st = self.visit(st)
            if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)) and _mentions627(st):
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


def _v625(rel):
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "show", f"{V625_COMMIT}:{rel}"], cwd=ROOT,
                             capture_output=True, text=True, timeout=120)
    except Exception:
        out = None
    if out is None or out.returncode != 0:
        pytest.skip(f"git or {V625_COMMIT} is not available here")
    return out.stdout


def test_every_v625_statement_is_in_v626_unchanged_and_v626_only_adds_and_hands_the_limit():
    """Simple of the committed v6.25 == Simple of v6.26 with v6.26's additions and the readers' limit keyword taken out,
    docstrings and all: no v6.25 statement, argument, call or branch was changed or dropped."""
    old = _v625("agents/strategy/Strategy1_Research_Simple.py")
    assert not t625._mentions626(ast.parse(old)), "v6.25 already names a v6.26 identifier: the strip would hide a change"
    stripped = t625._Drop626().visit(ast.parse(SIMPLE))
    assert ast.dump(stripped) == ast.dump(ast.parse(old))
    assert not t625._mentions626(stripped) and "max_stale_sim_s" not in ast.unparse(stripped)
    assert ast.dump(t625._Drop626().visit(ast.parse(SIMPLE))) != ast.dump(ast.parse(SIMPLE))


def test_only_the_switch_block_respond_the_pass_and_the_three_readers_changed_and_three_methods_were_added():
    old = _v625("agents/strategy/Strategy1_Research_Simple.py")

    def methods(tree):
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
        out: dict = {}
        for n in cls.body:
            if isinstance(n, ast.FunctionDef):
                out.setdefault(n.name, []).append(ast.dump(n))
        return out
    assert not _mentions627(ast.parse(old))
    a, b = methods(ast.parse(old)), methods(_Drop627().visit(ast.parse(SIMPLE)))     # v6.26: v6.27 - v6.29 out
    assert not _mentions627(_Drop627().visit(ast.parse(SIMPLE)))
    assert set(b) - set(a) == set(METHODS) and not set(a) - set(b)
    changed = {k for k in a if a[k] != b[k]}
    assert changed == {"_init_build_switches", "respond", "_v63_pass", *READERS}, changed
    for other in ("Strategy1_Research.py", "Strategy1.py", "research_v625_lean.py", "research_v617_touch_quote.py",
                  "research_v623_validator_063.py"):
        assert _v625(f"agents/strategy/{other}") == (STRATEGY / other).read_text(), other


def test_research_v616_changes_only_book_fundamental_and_without_the_keyword_it_is_v625s():
    old = _v625("agents/strategy/research_v616_fair_value.py")
    a, b = ast.parse(old).body, ast.parse(FAIR).body
    assert len(a) == len(b)
    diff = [getattr(x, "name", None) for x, y in zip(a, b) if ast.dump(x) != ast.dump(y)]
    assert diff == ["book_fundamental"], diff
    ns: dict = {}
    exec(compile(old, "<v625 research_v616_fair_value>", "exec"), ns)
    pages = [None, {}, _page(5.0, sim_id="20260929_2015"), dict(_page(5.0), sim_ts_ns=None), _page(5.0, fp=-1.0)] + \
        [_page(age) for age in (0.0, 8.0, 19.9, 20.0, 20.1, 25.0, 32.6, 32.7, 40.0, -5.0)] + [t616._page({"3": 100.4})]
    for page in pages:
        for book in (3, "3", 5):
            for bid, ask in ((100.00, 100.03), (100.03, 100.00), (None, 100.03)):
                for ts in (NOW, 0, None, NOW + 5 * S):
                    kw = dict(state_ts_ns=ts, sim_id=SIM)
                    assert fv.book_fundamental(page, book, bid, ask, **kw) == \
                        ns["book_fundamental"](page, book, bid, ask, **kw), (page, book, bid, ts)


def test_the_launcher_only_adds_to_v625s():
    """The committed v6.25 launcher line by line: three lines extended (the flag defaults, the v6_3_0 arm, PARAMS' last
    key), the rest added -- the v6.26 block and its gate entry.  v6.27, v6.28, v6.29 and v6.30 each extend the same
    three lines and add their block and gate entry."""
    old, new = _v625("run_strategy1_research_simple_multi.sh").splitlines(), LAUNCHER.splitlines()
    removed, added = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += old[i1:i2]
        if tag in ("replace", "insert"):
            added += new[j1:j2]
    defaults = next(x for x in old if x.startswith("A19X_BUILD=0;"))
    arm = next(x for x in old if x.startswith("  strategy1_direct_v6_3_0)"))
    assert removed == [defaults, arm, 'research_v625_lean_cap=48"']
    block = []
    for head in ("# v6.26 S6.", "# v6.27 S7.", "# v6.28 S8.", "# v6.29 S9.", "# v6.30.", "# v6.31.", "# v6.32.",
                 "# v6.33.", "# v6.34.", "# v6.36."):
        start = LAUNCHER.index(head)
        block += LAUNCHER[start:LAUNCHER.index("\nfi\n", start) + len("\nfi\n")].splitlines() + [""]
    assert sorted(added) == sorted([defaults + "; V6_26_BUILD=0; V6_27_BUILD=0; V6_28_BUILD=0; V6_29_BUILD=0; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0; V6_36_BUILD=0",
                                    arm + "; V6_26_BUILD=1; V6_27_BUILD=1; V6_28_BUILD=1; V6_29_BUILD=1; V6_30_BUILD=1; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1; V6_36_BUILD=1",
                                    "research_v625_lean_cap=48 \\", "research_v626_feed_clock=1 \\",
                                    "research_v627_touch_centre=1 \\", "research_v628_flat_launch=1 \\",
                                    "research_v629_touch_fp_move=1 \\", "research_v630_deep_tier=1 \\",
                                    "research_v630_s9_lookback=1 \\", "research_v631_touch_improve=1 \\",
                                    "research_v631_single_write_save=1 \\",
                                    "research_v632_fee_making=1 \\",
                                    "research_v632_fee_weight=0.005 \\",
                                    "research_v632_state_fundamental=1 \\",
                                    "research_v633_touch_identity=1 \\",
                                    "research_v634_class1_entry=0 \\",
                                    'research_v636_step_withdrawal=1"', "      tests/test_research_v6_26.py \\",
                                    "      tests/test_research_v6_27.py \\", "      tests/test_research_v6_28.py \\",
                                    "      tests/test_research_v6_29.py \\", "      tests/test_research_v6_30.py \\",
                                    "      tests/test_research_v6_31.py \\",
                                    "      tests/test_research_v6_32.py \\",
                                    "      tests/test_research_v6_33.py \\",
                                    "      tests/test_research_v6_34.py \\",
                                    "      tests/test_research_v6_36.py \\",
                                    *block])


# ---- 7. the launcher -----------------------------------------------------------------------------------------------------

def test_the_v626_block_is_gated_preflighted_and_listed():
    assert "; V6_23_BUILD=0; V6_24_BUILD=0; V6_25_BUILD=0; V6_26_BUILD=0; V6_27_BUILD=0; V6_28_BUILD=0; V6_29_BUILD=0; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0; V6_36_BUILD=0\n" in LAUNCHER
    arm = next(x for x in LAUNCHER.splitlines() if x.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_24_BUILD=1; V6_25_BUILD=1; V6_26_BUILD=1; V6_27_BUILD=1; V6_28_BUILD=1; V6_29_BUILD=1; V6_30_BUILD=1; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1; V6_36_BUILD=1"), arm
    assert LAUNCHER.count('if [[ "${V6_26_BUILD:-0}" == "1" ]]; then') == 1 and LAUNCHER.count("V6_26_BUILD=") == 2
    assert "      tests/test_research_v6_25.py \\\n      tests/test_research_v6_26.py \\\n" in LAUNCHER
    assert LAUNCHER.index('echo "[preflight] v6.25 fundamental lean PASS') < LAUNCHER.index("# v6.26 S6.") < \
        LAUNCHER.index('echo "[preflight] v6.26 feed clock PASS') < \
        LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')


def _block():
    start = LAUNCHER.index('if [[ "${V6_26_BUILD:-0}" == "1" ]]; then')
    return LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.26 feed clock PASS', start)]


_PATTERN = r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\" \|\| \{\n\s+echo \"ERROR: ([^\"]+)\" >&2"
_COUNTED = r"\[\[ \"\$\(grep -cF '([^']+)' \"\$AGENT_PATH/([^\"]+)\"\)\" == \"(\d+)\" \]\] \|\| \{\n\s+echo \"ERROR: ([^\"]+)\" >&2"
FILES = ("Strategy1_Research_Simple.py", "research_v626_feed_clock.py", "research_v616_fair_value.py")


def test_every_v626_preflight_pattern_is_in_the_file_it_checks():
    pats = re.findall(_PATTERN, _block())
    assert len(pats) == 8, pats
    for needle, name, _msg in pats:
        assert name in FILES and (STRATEGY / name).read_text().count(needle) == 1, (needle, name)
    assert {p[0] for p in pats} == {
        "from research_v626_feed_clock import (", SWITCH_LINE, "self._v6_26_observe(state)", REPORT_LINE,
        "WALL_CYCLE_S = FETCH_PERIOD_S + POLL_S + FETCH_TIMEOUT_S",
        "return PAGE_STEPS * PAGE_STEP_SIM_S + max(0.0, r) * WALL_CYCLE_S",
        "sim_id: Any, max_stale_sim_s: Any = None) -> tuple[float | None, str]:",
        "if max(0.0, (now_ts - page_ts) / 1e9) > limit:"}
    counted = re.findall(_COUNTED, _block())
    assert [(c[0], c[1], c[2]) for c in counted] == [(KEYWORD, "Strategy1_Research_Simple.py", "3"),
                                                     ("v616_book_fundamental(", "Strategy1_Research_Simple.py", "3")]
    for needle, name, n, _msg in counted:
        assert (STRATEGY / name).read_text().count(needle) == int(n), needle


def _run_block(params, agent_path=STRATEGY, build="1"):
    start = LAUNCHER.index("# v6.26 S6.")
    stop = LAUNCHER.index('echo "[preflight] v6.26 feed clock PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_26_BUILD": build}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=120)


def test_the_v626_block_passes_the_shipped_params_and_refuses_a_build_without_the_switch():
    shipped = t617._shipped()
    out = _run_block(shipped)
    assert out.returncode == 0 and out.stderr == "", out.stderr
    assert out.stdout.startswith("[preflight] v6.26 feed clock PASS (S6: the fundamental page is stale beyond 2 page "
                                 "steps in sim-s + the feed's wall cycle"), out.stdout
    for bad in ("research_v626_feed_clock=0", "", "research_v626_feed_clock=true", "research_v626_feed_clock=11"):
        out = _run_block(shipped.replace("research_v626_feed_clock=1", bad))
        assert out.returncode == 1 and "without research_v626_feed_clock=1" in out.stderr and "PASS" not in out.stdout, bad
    out = _run_block("", build="0")                                                   # another build: not run
    assert out.returncode == 0 and out.stdout == "" and out.stderr == ""


def test_the_v626_block_refuses_a_tree_without_each_line_it_checks(tmp_path):
    cases = [(needle, name, msg) for needle, name, msg in re.findall(_PATTERN, _block())]
    cases += [(needle, name, msg) for needle, name, _n, msg in re.findall(_COUNTED, _block())]
    assert len(cases) == 10
    for i, (needle, name, msg) in enumerate(cases):
        tmp = tmp_path / str(i)
        tmp.mkdir()
        for n in FILES:
            shutil.copy(STRATEGY / n, tmp / n)
        text = (tmp / name).read_text()
        assert text.count(needle) >= 1, needle
        (tmp / name).write_text(text.replace(needle, "pass  # removed", 1))
        out = _run_block(t617._shipped(), agent_path=tmp)
        assert out.returncode == 1 and msg in out.stderr and "PASS" not in out.stdout, (needle, out.stderr)
    tmp = tmp_path / "fourth"
    tmp.mkdir()
    for n in FILES:
        shutil.copy(STRATEGY / n, tmp / n)
    text = (tmp / "Strategy1_Research_Simple.py").read_text()
    (tmp / "Strategy1_Research_Simple.py").write_text(text + "\n# fp, r = v616_book_fundamental(None, 3, 1, 2)\n")
    out = _run_block(t617._shipped(), agent_path=tmp)                                 # a fourth reader without the limit
    assert out.returncode == 1 and "a reader of the fundamental page was added or lost" in out.stderr
