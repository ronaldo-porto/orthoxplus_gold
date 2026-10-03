"""v7.0: S1 the deep layer anchors on the fundamental price the validator publishes for every book
(research_v70_fundamental).  On a book whose fundamental sits at least theta from the mid, the level-0 order on the side
trading toward it rests at half the depth and that side may hold twice the book's bound; the other side rests at the full
depth.  Without a fresh fundamental of this simulation, below theta, or inside a vacuum spread, the book is v6.12.

The field's alpha leaders trade toward the published fundamental 81-91% of the time at ~0 delay; 59% of our maker's fills
went against it.  Replayed with the validator's arithmetic (as-built v6.12 + this rule with its own theta, UID 237's
recording, sim 43,900-45,323): making 37.31 -> 38.41, alpha -99 -> +60 (kappa -0.090 -> +0.055).
"""
import ast
import fcntl
import json
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

import research_v633_deep_layer as dl  # noqa: E402
import research_v70_fundamental as fa  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_4_board as t64  # noqa: E402
import test_research_v6_9 as t69  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
BUY, SELL = dl.SIDE_BUY, dl.SIDE_SELL
EFF_LINE = "eff_side = {s_: float(d_) for s_, d_ in v70_level0_depths(eff_side, depth, toward).items()}"
ROOM_LINE = "return bool(deep.room(side_, inv, v70_side_bound(side_, bound, toward)))"
SIM = "20260929_2015"


def _page(fps, *, ts=44_327_000_000_000, sim=SIM, books_line=True, extra=""):
    lines = ["# HELP book_gauges Book gauges.", "# TYPE book_gauges gauge"]
    for b, v in fps.items():
        lines.append('book_gauges{book_gauge_name="bid",book_id="%d",level="0",netuid="79",sim_id="%s"} 1.0' % (b, sim))
        lines.append('book_gauges{book_gauge_name="fundamental_price",book_id="%d",level="0",netuid="79",sim_id="%s",'
                     'wallet="5EWw"} %s' % (b, sim, v))
    if books_line:
        lines.append('books{ask_1="100.03",bid_1="100.0",book_gauge_name="books",book_id="0",netuid="79",sim_id="%s",'
                     'timestamp="%d"} 1.0' % (sim, ts))
    return "\n".join(lines) + "\n" + extra


# ---- 1. the rule ------------------------------------------------------------------------------------------------------

def test_the_page_yields_each_books_fundamental_its_simulation_and_its_sim_time():
    p = fa.parse_page(_page({0: "100.25", 3: "99.5", 7: "101"}))
    assert p == {"sim_ts_ns": 44_327_000_000_000, "sim_id": SIM, "fp": {0: 100.25, 3: 99.5, 7: 101.0}}
    bad = _page({0: "100.25", 1: "nan", 2: "-3", 4: "x"}) + 'book_gauges{book_gauge_name="fundamental_price",book_id="z"} 5\n'
    assert fa.parse_page(bad)["fp"] == {0: 100.25}                                    # unreadable values are skipped
    assert fa.parse_page(_page({0: "100.25"}, books_line=False)) is None              # no sim time: unusable
    assert fa.parse_page(_page({})) is None                                           # no fundamental: unusable
    for junk in (None, b"bytes", 3, ""):
        assert fa.parse_page(junk) is None


def test_gap_sigma_and_theta():
    assert math.isclose(fa.gap_bps(101.0, 100.0), math.log(1.01) * 1e4)
    assert fa.gap_bps(99.0, 100.0) < 0 and fa.gap_bps(None, 100.0) is None and fa.gap_bps(100.0, 0.0) is None
    # sigma is over the whole simulation (its clock runs 0 -> 1 across the duration): per sqrt(sim-second) in bps
    assert math.isclose(fa.sigma_s_bps(0.034, 86_400 * 10**9), 0.034 / math.sqrt(86_400) * 1e4)
    for s, d in ((None, 1e9), (0.0, 1e9), (0.03, 0.0), ("x", 1e9), (-0.1, 1e9)):
        assert fa.sigma_s_bps(s, d) is None, (s, d)
    assert math.isclose(fa.theta_bps(1.0, 8.0, 0.5), 2.0 * math.sqrt(8.0 + 0.25))
    assert math.isclose(fa.theta_bps(1.0, -5.0, 0.5), 1.0)                            # negative staleness: none
    assert fa.theta_bps(None, 8.0, 0.5) is None and fa.theta_bps(1.0, 8.0, None) is None
    assert fa.THETA_Z == 2.0 and fa.TOWARD_DEPTH_FACTOR == 0.5 and fa.TOWARD_BOUND_FACTOR == 2.0
    assert fa.V70_FUNDAMENTAL_VERSION == "fundamental_anchor_v7_1" and fa.MAX_STALE_SIM_S == 20.0


def test_only_a_page_of_this_simulation_anchors():
    assert fa.same_simulation(SIM, SIM) and fa.same_simulation(SIM, SIM + "_extra")
    assert not fa.same_simulation(SIM, "20261005_2045")
    assert fa.same_simulation(None, SIM) and fa.same_simulation(SIM, "")              # an unread id is no mismatch


def _snap(fp=101.0, *, ts=1_000 * 10**9, sim=SIM):
    return {"sim_ts_ns": ts, "sim_id": sim, "fp": {3: fp}, "wall": 0.0}


KW = dict(sim_id=SIM, fp_sigma=0.034, duration_ns=86_400 * 10**9)


def test_a_book_is_anchored_toward_its_fundamental_only_when_it_sits_at_least_theta_away():
    now = 1_008 * 10**9                                                              # 8 sim-s after the page
    toward, why, gap, th = fa.book_anchor(_snap(101.0), 3, 100.00, 100.03, state_ts_ns=now, **KW)
    assert (toward, why) == (BUY, "armed") and gap > th > 0
    toward, why, _g, _t = fa.book_anchor(_snap(99.0), 3, 100.00, 100.03, state_ts_ns=now, **KW)
    assert (toward, why) == (SELL, "armed")
    toward, why, gap, th = fa.book_anchor(_snap(100.05), 3, 100.00, 100.03, state_ts_ns=now, **KW)
    assert (toward, why) == (None, "below_theta") and abs(gap) < th
    # theta widens with the page's age: a 9-bps gap is anchored 2 sim-s after the page and not 19 sim-s after it
    fp9 = 100.015 * math.exp(9e-4)
    assert fa.book_anchor(_snap(fp9), 3, 100.00, 100.03, state_ts_ns=1_002 * 10**9, **KW)[1] == "armed"
    assert fa.book_anchor(_snap(fp9), 3, 100.00, 100.03, state_ts_ns=1_019 * 10**9, **KW)[1] == "below_theta"


def test_every_unusable_input_leaves_the_book_unanchored():
    now = 1_008 * 10**9
    cases = [
        ((None, 3, 100.0, 100.03), dict(state_ts_ns=now, **KW), "no_feed"),
        (({"fp": "x"}, 3, 100.0, 100.03), dict(state_ts_ns=now, **KW), "no_feed"),
        ((_snap(sim="20261005_2045"), 3, 100.0, 100.03), dict(state_ts_ns=now, **KW), "other_sim"),
        ((_snap(), 3, 100.0, 100.03), dict(state_ts_ns=1_021 * 10**9, **KW), "stale"),          # 21 sim-s old
        ((_snap(), 3, 100.0, 100.03), dict(state_ts_ns=0, **KW), "stale"),
        ((_snap(), 9, 100.0, 100.03), dict(state_ts_ns=now, **KW), "no_fundamental"),
        ((_snap(), 3, 100.03, 100.0), dict(state_ts_ns=now, **KW), "no_book"),                  # crossed
        ((_snap(), 3, None, 100.03), dict(state_ts_ns=now, **KW), "no_book"),
        ((_snap(), 3, 100.0, 100.03), dict(state_ts_ns=now, sim_id=SIM, fp_sigma=None, duration_ns=1e9), "no_theta"),
        ((_snap(), 3, 100.0, 100.03), dict(state_ts_ns=now, sim_id=SIM, fp_sigma=0.03, duration_ns=None), "no_theta"),
    ]
    for args, kw, why in cases:
        toward, reason, _g, _t = fa.book_anchor(*args, **kw)
        assert toward is None and reason == why, (args, kw, reason)
    # a page ahead of the state is not negative staleness; a string book key is read
    snap = {"sim_ts_ns": 2_000 * 10**9, "sim_id": SIM, "fp": {"3": 101.0}}
    assert fa.book_anchor(snap, 3, 100.0, 100.03, state_ts_ns=now, **KW)[:2] == (BUY, "armed")


def test_the_toward_side_moves_to_the_factor_and_the_other_side_keeps_v612():
    v612 = {BUY: 30.0, SELL: 15.0}                                                     # long: v6.12 pulls the ask
    assert fa.level0_depths(v612, 30.0, BUY) == {BUY: 15.0, SELL: 15.0}                # v7.1: the pull stays
    assert fa.level0_depths(v612, 30.0, SELL) == {BUY: 30.0, SELL: 15.0}
    assert fa.level0_depths({BUY: 30.0, SELL: 30.0}, 30.0, BUY) == {BUY: 15.0, SELL: 30.0}    # flat: the full depth
    for bad in (None, {BUY: 30.0}, {BUY: "x", SELL: 30.0}):
        assert fa.level0_depths(bad, 30.0, BUY) is bad, bad
    for toward in (None, "x"):
        assert fa.level0_depths(v612, 30.0, toward) is v612
    for depth in (None, "x", 0.0, -3.0):
        assert fa.level0_depths(v612, depth, BUY) is v612
    for factor in (0.0, 1.5, None, float("nan")):
        assert fa.level0_depths(v612, 30.0, BUY, factor=factor) is v612, factor


def test_only_the_toward_side_is_judged_against_the_larger_bound():
    assert fa.side_bound(BUY, 24.0, BUY) == 48.0 and fa.side_bound(SELL, 24.0, BUY) == 24.0
    assert fa.side_bound(SELL, 24.0, None) == 24.0 and fa.side_bound(BUY, None, BUY) is None
    for factor in (0.5, 5.0, None):
        assert fa.side_bound(BUY, 24.0, BUY, factor=factor) == 24.0, factor


# ---- 2. the shared feed -----------------------------------------------------------------------------------------------

class _Clock:
    def __init__(self, t=1_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _feed(tmp_path, page=None, clock=None):
    calls = []

    def fetch(url, timeout):
        calls.append((url, timeout))
        if isinstance(page, Exception):
            raise page
        return page if page is not None else _page({0: "100.25", 3: "99.5"})

    feed = fa.SharedFeed(cache_dir=str(tmp_path / "cache"), fetch=fetch, clock=clock or _Clock())
    return feed, calls


def test_one_fetch_per_period_per_host_and_every_agent_reads_the_cache(tmp_path):
    clock = _Clock()
    a, calls_a = _feed(tmp_path, clock=clock)
    b, calls_b = _feed(tmp_path, clock=clock)
    a.step()
    assert len(calls_a) == 1 and a.snapshot()["fp"] == {0: 100.25, 3: 99.5}
    b.step()                                                                          # the cache is fresh: no fetch
    assert calls_b == [] and b.snapshot()["fp"] == {0: 100.25, 3: 99.5} and b.snapshot()["sim_id"] == SIM
    clock.t += fa.FETCH_PERIOD_S - 1
    a.step(); b.step()
    assert len(calls_a) + len(calls_b) == 1
    clock.t += 1
    b.step()
    assert len(calls_b) == 1 and calls_b[0] == (fa.FEED_URL, fa.FETCH_TIMEOUT_S)
    assert a.status()["fetches"] == 1 and b.status()["books"] == 2 and b.status()["cache_age_s"] == 0.0


def test_the_fetcher_rechecks_the_cache_once_it_holds_the_lock(tmp_path):
    clock = _Clock()
    a, calls_a = _feed(tmp_path, clock=clock)
    a.step()
    clock.t += fa.FETCH_PERIOD_S                                                      # the cache is now due
    b, calls_b = _feed(tmp_path, clock=clock)
    real, seen = b._read_cache, []

    def racing():
        seen.append(1)
        out = real()
        if len(seen) == 1:
            a.step()                          # b saw the due cache; another agent refreshes it before b locks
        return out

    b._read_cache = racing
    b.step()
    assert len(calls_a) == 2 and calls_b == [] and b.snapshot()["wall"] == clock.t


def test_a_held_lock_means_another_agent_is_fetching(tmp_path):
    feed, calls = _feed(tmp_path)
    os.makedirs(feed.cache_dir, exist_ok=True)
    with open(os.path.join(feed.cache_dir, "fetch.lock"), "a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        feed.step()
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    assert calls == [] and feed.snapshot() is None
    feed.step()
    assert len(calls) == 1 and feed.snapshot() is not None


def test_a_failed_or_unusable_fetch_keeps_the_last_page_and_counts(tmp_path):
    clock = _Clock()
    good, _ = _feed(tmp_path, clock=clock)
    good.step()
    first = good.snapshot()
    clock.t += fa.FETCH_PERIOD_S
    for page in (OSError("down"), "<html>maintenance</html>"):
        bad, calls = _feed(tmp_path, page=page, clock=clock)
        bad.step()
        assert len(calls) == 1 and bad.fetch_errors == 1 and bad.snapshot() == first, page
    assert bad.last_error is None or bad.last_error.startswith("fetch")


def test_a_torn_or_foreign_cache_is_not_used(tmp_path):
    feed, calls = _feed(tmp_path, page=OSError("down"))
    os.makedirs(feed.cache_dir, exist_ok=True)
    path = os.path.join(feed.cache_dir, "books.json")
    for content in ('{"fp": {"0": 1', '["a"]', json.dumps({"fp": {"0": 1.0}})):          # torn, foreign, no wall
        with open(path, "w") as fh:
            fh.write(content)
        feed.step()
        assert feed.snapshot() is None, content
    assert feed.read_errors >= 1


def test_the_thread_starts_once_and_stops(tmp_path):
    feed, calls = _feed(tmp_path)
    feed.poll_s = 0.01
    feed.start()
    thread = feed._thread
    feed.start()
    assert feed._thread is thread and thread.daemon
    thread.join(0.2)
    feed.stop()
    thread.join(1.0)
    assert not thread.is_alive() and len(calls) == 1 and feed.snapshot() is not None


# ---- 3. the deep pass -------------------------------------------------------------------------------------------------

def _agent(*, toward=None, on=True, raises=False, **kw):
    agent = t69._agent(**kw)
    agent.research_v612_reduce_depth = True
    if on:
        agent.research_v70_fundamental = True
    calls = []

    def _toward(self, book_id, raw_bid, raw_ask, cfg):
        calls.append(book_id)
        if raises:
            raise RuntimeError("feed")
        return toward

    agent._v70_toward = types.MethodType(_toward, agent)
    agent.calls = calls
    return agent


def _px(depth, side, **book):
    return t69._px(depth, side, **book)


def test_an_anchored_flat_book_rests_the_toward_side_at_half_the_depth():
    agent = _agent(toward=BUY)
    resp = t64._run(agent, {3: t63._book()})
    assert t63._placed(resp) == {(3, "BUY", 40031, _px(15.0, BUY), 2.0), (3, "SELL", 40032, _px(30.0, SELL), 2.0)}
    assert agent._v70_counts == {"placed_toward_l0": 1, "placed_against_l0": 1} and agent.calls == [3]
    agent = _agent(toward=SELL)
    resp = t64._run(agent, {3: t63._book()})
    assert t63._placed(resp) == {(3, "BUY", 40031, _px(30.0, BUY), 2.0), (3, "SELL", 40032, _px(15.0, SELL), 2.0)}


def test_the_side_against_the_fundamental_keeps_the_v612_pull():
    # long book, fundamental above: the bid (toward) at 15; the ask (against, reducing) keeps v6.12's half depth (v7.1)
    agent = _agent(toward=BUY, venue={3: 3.0})
    resp = t64._run(agent, {3: t63._book()})
    assert t63._placed(resp) == {(3, "BUY", 40031, _px(15.0, BUY), 2.0), (3, "SELL", 40032, _px(15.0, SELL), 2.0)}
    assert "placed_reducing_l0" not in agent._v633_counts
    # long book, fundamental below: the reducing side is also the toward side
    agent = _agent(toward=SELL, venue={3: 3.0})
    resp = t64._run(agent, {3: t63._book()})
    assert t63._placed(resp) == {(3, "BUY", 40031, _px(30.0, BUY), 2.0), (3, "SELL", 40032, _px(15.0, SELL), 2.0)}


def test_an_unanchored_book_the_switch_off_and_a_vacuum_are_v612():
    for agent in (_agent(toward=None, venue={3: 3.0}), _agent(on=False, raises=True, venue={3: 3.0})):
        resp = t64._run(agent, {3: t63._book()})
        assert t63._placed(resp) == {(3, "BUY", 40031, _px(30.0, BUY), 2.0), (3, "SELL", 40032, _px(15.0, SELL), 2.0)}
        assert agent._v633_counts.get("placed_reducing_l0") == 1 and not getattr(agent, "_v70_counts", {})
    agent = _agent(raises=True, venue={3: 3.0})                                     # the vacuum is never anchored
    resp = t64._run(agent, {3: t63._book(bid=100.00, ask=100.30)})
    assert agent.calls == [] and len(t63._placed(resp)) == 2


def test_a_failing_anchor_is_counted_and_the_book_is_v612():
    agent = _agent(raises=True, venue={3: 3.0})
    resp = t64._run(agent, {3: t63._book()})
    assert agent._v70_errors == 1 and agent.calls == [3]
    assert t63._placed(resp) == {(3, "BUY", 40031, _px(30.0, BUY), 2.0), (3, "SELL", 40032, _px(15.0, SELL), 2.0)}


def test_the_toward_side_may_hold_twice_the_bound_and_the_other_side_does_not():
    probe = _agent(toward=None)
    bound = probe._v610_bound_base(probe._v633_deep, False)
    assert bound > 0
    # long at the bound: unanchored the bid has no room; anchored toward BUY it does (2 x bound)
    agent = _agent(toward=None, venue={3: bound})
    assert {p[1] for p in t63._placed(t64._run(agent, {3: t63._book()}))} == {"SELL"}
    agent = _agent(toward=BUY, venue={3: bound})
    assert t63._placed(t64._run(agent, {3: t63._book()})) == {(3, "BUY", 40031, _px(15.0, BUY), 2.0),
                                                                (3, "SELL", 40032, _px(15.0, SELL), 2.0)}
    # short at the bound with the fundamental above: the ask is the against side and keeps the plain bound
    agent = _agent(toward=BUY, venue={3: -bound})
    assert {p[1] for p in t63._placed(t64._run(agent, {3: t63._book()}))} == {"BUY"}
    assert agent._v70_counts == {"placed_toward_l0": 1}                              # only the toward side placed
    # long at the bound with the fundamental below: the ask is the toward side, the bid has no room
    agent = _agent(toward=SELL, venue={3: bound})
    assert {p[1] for p in t63._placed(t64._run(agent, {3: t63._book()}))} == {"SELL"}
    assert agent._v70_counts == {"placed_toward_l0": 1}


def test_on_a_fast_book_the_toward_side_keeps_twice_the_volatility_scaled_bound():
    import test_research_v6_6_1 as t661
    sums = {3: 40.0, 4: 10.0, 5: 10.0}            # book 3 at 4 x the median volatility: a quarter of the bound (1 base)

    def anchored(toward, venue):
        agent = t661._agent(venue, sums=sums)
        agent.research_v70_fundamental = True
        agent._v70_toward = types.MethodType(lambda self, b, bid, ask, cfg: toward, agent)
        return agent

    # long 1.5 is past a quarter of the bound: v6.6.1 rests only the reducing sell ...
    assert t661._sides(t64._run(anchored(None, {3: 1.5}), {3: t63._book()})) == {"SELL"}
    # ... and with the fundamental above, the bid toward it may hold twice that (2 base) and rests
    assert t661._sides(t64._run(anchored(BUY, {3: 1.5}), {3: t63._book()})) == {"BUY", "SELL"}
    assert t661._sides(t64._run(anchored(BUY, {3: 2.5}), {3: t63._book()})) == {"SELL"}       # past twice: none


def test_a_resting_toward_order_at_the_full_depth_is_repriced_closer():
    agent = _agent(toward=BUY)
    t69._rest(agent, 11, 0, 0)                                                       # the bid rests at the depth (30)
    resp = t64._run(agent, {3: t63._book()})
    assert (3, 11) in t63._cancelled(resp)                                           # 30 > 1.5 x 15: out of its band


# ---- 4. the agent's anchor method ------------------------------------------------------------------------------------

def _anchor_method():
    src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
    scope = {"v70_book_anchor": fa.book_anchor, "V70SharedFeed": fa.SharedFeed}
    for name in ("_v70_toward", "_v70_count", "_v70_snapshot"):
        exec(compile(ast.Module(body=[ast.parse(src(name)).body[0]], type_ignores=[]), f"<{name}>", "exec"), scope)
    scope["V70_FUNDAMENTAL_VERSION"] = fa.V70_FUNDAMENTAL_VERSION
    return scope


def test_the_agent_reads_the_simulation_sigma_and_duration_from_the_state_config():
    scope = _anchor_method()
    feed = types.SimpleNamespace(snapshot=lambda: _snap(101.0), status=lambda: {"books": 1})
    cls = type("A", (), {"_v70_toward": scope["_v70_toward"], "_v70_count": scope["_v70_count"],
                         "_v70_feed_ref": lambda self: feed})
    agent = cls()
    agent._direct_current_state_timestamp_ns = 1_008 * 10**9
    cfg = types.SimpleNamespace(simulation_id=SIM, fp_sigma=0.034, duration=86_400 * 10**9)
    assert agent._v70_toward(3, 100.00, 100.03, cfg) == BUY
    cfg = types.SimpleNamespace(simulation_id=None, logDir="/x/20261005_2045_sim", fp_sigma=0.034, duration=86_400 * 10**9)
    assert agent._v70_toward(3, 100.00, 100.03, cfg) is None                          # a new simulation's directory
    cfg = types.SimpleNamespace(simulation_id=SIM, fp_sigma=None, duration=None)
    assert agent._v70_toward(3, 100.00, 100.03, cfg) is None
    assert agent._v70_counts == {"book_armed": 1, "book_other_sim": 1, "book_no_theta": 1}
    snap = scope["_v70_snapshot"](types.SimpleNamespace(_v70_counts={"book_armed": 1}, _v70_errors=0, _v70_feed=feed))
    assert snap == {"version": "fundamental_anchor_v7_1", "counts": {"book_armed": 1}, "errors": 0, "feed": {"books": 1}}


# ---- 5. wiring --------------------------------------------------------------------------------------------------------

def _params():
    start = LAUNCHER.index('PARAMS="')
    return LAUNCHER[start:LAUNCHER.index('"\n', start + len('PARAMS="'))]


def _shipped():
    return _params()[len('PARAMS="'):].replace("\\\n", " ")


def test_the_switch_ships_on_and_agrees_with_the_agent_default():
    assert "research_v70_fundamental=1" in _params() and "research_v70_fundamental=0" not in _params()
    assert ('self.research_v70_fundamental = self._as_bool(getattr(self.config, "research_v70_fundamental", True))'
            in SIMPLE)
    assert SIMPLE.count(EFF_LINE) == 1 and SIMPLE.count(ROOM_LINE) == 1
    assert 'if vac is None and bool(getattr(self, "research_v70_fundamental", False)):' in SIMPLE   # harness stays v6.12
    assert "fundamental_on=int(bool(getattr(self, \"research_v70_fundamental\", False)))," in SIMPLE
    assert 'fundamental=(self._v70_snapshot() if getattr(self, "research_v70_fundamental", None) is not None else {}),' in SIMPLE


def test_the_v70_block_is_gated_preflighted_and_listed():
    assert "; V6_10_BUILD=0; V6_11_BUILD=0; V6_12_BUILD=0; V6_13_BUILD=0; V7_0_BUILD=0\n" in LAUNCHER
    arm = next(line for line in LAUNCHER.splitlines() if line.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_13_BUILD=1; V7_0_BUILD=1"), arm
    assert 'if [[ "${V7_0_BUILD:-0}" == "1" ]]; then' in LAUNCHER
    assert 'echo "[preflight] v7.1 fundamental anchor PASS (theta 2 sd, toward depth 0.5, toward bound 2.0, against side v6.12)"' in LAUNCHER
    assert "tests/test_research_v7_0.py" in LAUNCHER


def test_every_v70_preflight_pattern_is_in_the_file_it_checks():
    import re
    start = LAUNCHER.index('if [[ "${V7_0_BUILD:-0}" == "1" ]]; then')
    block = LAUNCHER[start:LAUNCHER.index('echo "[preflight] v7.1 fundamental anchor PASS', start)]
    pats = re.findall(r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\"", block)
    assert len(pats) == 7, pats
    for pat, name in pats:
        assert pat in (STRATEGY / name).read_text(), (pat, name)


def _run_block(params, agent_path=STRATEGY):
    start = LAUNCHER.index("# v7.0 S1.")
    stop = LAUNCHER.index('echo "[preflight] v7.1 fundamental anchor PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V7_0_BUILD": "1"}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_the_v70_block_passes_the_shipped_params_and_refuses_the_switch_off():
    out = _run_block(_shipped())
    assert out.returncode == 0 and "[preflight] v7.1 fundamental anchor PASS" in out.stdout, out.stderr
    for bad in ("research_v70_fundamental=0", "research_v70_fundamental=10", ""):
        out = _run_block(_shipped().replace("research_v70_fundamental=1", bad))
        assert out.returncode == 1 and "v7.0 S1 build without research_v70_fundamental=1" in out.stderr, repr(bad)


def test_the_v70_block_refuses_an_agent_without_the_rule_or_a_module_with_other_factors(tmp_path):
    module = (STRATEGY / "research_v70_fundamental.py").read_text()
    (tmp_path / "research_v70_fundamental.py").write_text(module)
    (tmp_path / "Strategy1_Research_Simple.py").write_text(SIMPLE.replace(EFF_LINE, "pass"))
    out = _run_block(_shipped(), agent_path=tmp_path)
    assert out.returncode == 1 and "does not anchor level 0 on the fundamental" in out.stderr
    (tmp_path / "Strategy1_Research_Simple.py").write_text(SIMPLE.replace(ROOM_LINE, "return bool(deep.room(side_, inv, bound))"))
    out = _run_block(_shipped(), agent_path=tmp_path)
    assert out.returncode == 1 and "is not judged against its bound" in out.stderr
    (tmp_path / "Strategy1_Research_Simple.py").write_text(SIMPLE)
    for old, new, msg in (("THETA_Z = 2.0 ", "THETA_Z = 1.0 ", "two standard deviations"),
                          ("TOWARD_DEPTH_FACTOR = 0.5 ", "TOWARD_DEPTH_FACTOR = 0.25 ", "toward depth 0.5"),
                          ("TOWARD_BOUND_FACTOR = 2.0 ", "TOWARD_BOUND_FACTOR = 4.0 ", "toward bound 2.0"),
                          ("(d * f if s_ == toward else kept[s_])", "(d * f if s_ == toward else d)", "does not keep v6.12")):
        (tmp_path / "research_v70_fundamental.py").write_text(module.replace(old, new))
        out = _run_block(_shipped(), agent_path=tmp_path)
        assert out.returncode == 1 and msg in out.stderr, (old, out.stderr)
