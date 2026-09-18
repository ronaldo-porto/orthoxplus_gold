"""v6.1 request memo: the rolling-kappa refresh and the profile's PnL scans, once per request.

Measured on mainnet UID 34 (v6.0.2, log 20260917_214432, 2026-09-18): median response 50 -> 114 ms
and p95 60 -> 134 ms over 8,000 ticks, garbage collection ruled out (0 gen-2 collections inside a
request).  The growth sat in two places, both scaling with the realized history until its 3 sim-h
window fills:

* screen 7.4 -> 40.7 ms: `_research_kappa_book` runs for all 128 books every request, and each call
  runs the frozen `_research_refresh_rolling_kappa_cache`, which rebuilds its cache key with a sum
  over every history timestamp and a max over every key -- on a cache HIT too.  That key alone:
  2.4 ms per request at tick 1,000, 16.9 ms at tick 8,000.
* ranking + order building 9.4 -> 30.9 ms: `build_book_profile` runs two full-history scans
  (`_pnl_observation_count`, `_realized_pnl_lookback`) for each of ~20 profiled books.

The memo is behaviour-neutral, and these tests prove it against the FROZEN methods, executed from
their own files: identical caches after every call, bit-identical sums, one frozen refresh per
request instead of one per book.
"""
import random
import textwrap
import typing
from pathlib import Path

import pytest

from _harness import extractor
from research_kappa_state import rolling_observation_timestamps

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
RESEARCH = (STRATEGY / "Strategy1_Research.py").read_text()
DETAILED = (STRATEGY / "DetailedTemplateAgent.py").read_text()

_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
_research = extractor(RESEARCH, cls_name="Strategy1_Research")
_detailed = extractor(DETAILED, cls_name="DetailedTemplateAgent")

S = 1_000_000_000
BOOKS = 128


def _body(sources):
    return "".join(textwrap.indent(textwrap.dedent(src), "    ") + "\n" for src in sources)


def _classes():
    scope = {"rolling_observation_timestamps": rolling_observation_timestamps, "Any": typing.Any}
    frozen = [_research("_research_refresh_rolling_kappa_cache"),
              _detailed("_pnl_observation_count"), _detailed("_realized_pnl_lookback")]
    exec("from __future__ import annotations\nclass Frozen:\n" + _body(frozen), scope)

    class Counting(scope["Frozen"]):
        """Counts the frozen refreshes that actually run."""

        def _research_refresh_rolling_kappa_cache(self):
            self.frozen_refreshes += 1
            return super()._research_refresh_rolling_kappa_cache()

    scope["Counting"] = Counting
    memo = [_simple(n) for n in ("_v61_memo_on", "_research_refresh_rolling_kappa_cache",
                                 "_v61_pnl_window", "_pnl_observation_count", "_realized_pnl_lookback")]
    exec("from __future__ import annotations\nclass Memo(Counting):\n" + _body(memo), scope)
    return Counting, scope["Memo"]


COUNTING, MEMO = _classes()


def _fresh(cls, *, lookback_s, memo=True):
    agent = cls()
    agent.frozen_refreshes = 0
    agent.realized_pnl_history = {}
    agent._research_persisted_observation_timestamps = {}
    agent._research_last_sim_ts = None
    agent.research_kappa_lookback_ns = lookback_s * S
    agent.pnl_lookback_ns = lookback_s * S
    agent._tick = 0
    agent.research_v61_request_memo = memo
    agent._v61_kappa_memo = None
    agent._v61_pnl_memo = None
    agent._v61_memo_hits = agent._v61_memo_misses = agent._v61_pnl_memo_builds = 0
    return agent


def _caches(agent):
    return (
        dict(agent._research_kappa_roll_count_cache),
        dict(agent._research_kappa_roll_ts_cache),
        agent._research_kappa_roll_next_expiry_ts,
        {b: list(r) for b, r in agent._research_persisted_observation_timestamps.items()},
    )


def _requests(seed, n, *, lookback_s):
    """(tick, now, history) per request, the way update() leaves them: the prune rebuilds the dict."""
    rng = random.Random(seed)
    history, now = {}, 70_000 * S
    for tick in range(1, n + 1):
        now += S
        bucket = {b: round(rng.uniform(-1.0, 1.0), 4) for b in rng.sample(range(BOOKS), rng.randint(0, 3))}
        if bucket:
            history.setdefault(now, {}).update(bucket)
        history = {ts: books for ts, books in history.items() if ts >= now - lookback_s * S}
        yield tick, now, history, rng.randint(1, 140)


# ---- T1 the refresh -------------------------------------------------------------------------------

@pytest.mark.parametrize("seed, lookback_s", [(1, 10_800), (2, 40), (3, 7), (4, 1)])
def test_t1_the_memo_leaves_every_cache_exactly_as_the_frozen_refresh_does(seed, lookback_s):
    frozen, memo = _fresh(COUNTING, lookback_s=lookback_s), _fresh(MEMO, lookback_s=lookback_s)
    requests = 0
    for tick, now, history, calls in _requests(seed, 120, lookback_s=lookback_s):
        requests += 1
        for agent in (frozen, memo):
            agent._tick, agent._research_last_sim_ts, agent.realized_pnl_history = tick, now, history
        for _ in range(calls):
            frozen._research_refresh_rolling_kappa_cache()
            memo._research_refresh_rolling_kappa_cache()
            assert _caches(memo) == _caches(frozen)
    assert memo.frozen_refreshes == requests, "one frozen refresh per request"
    assert frozen.frozen_refreshes > 10 * requests


def test_t1_a_history_replaced_inside_a_request_is_seen():
    memo = _fresh(MEMO, lookback_s=10_800)
    memo._tick, memo._research_last_sim_ts = 5, 70_005 * S
    memo.realized_pnl_history = {70_004 * S: {3: 0.5}}
    memo._research_refresh_rolling_kappa_cache()
    assert memo._research_kappa_roll_count_cache == {3: 1}
    memo.realized_pnl_history = {70_004 * S: {3: 0.5}, 70_005 * S: {9: 0.2}}
    memo._research_refresh_rolling_kappa_cache()
    assert memo._research_kappa_roll_count_cache == {3: 1, 9: 1} and memo.frozen_refreshes == 2


def test_t1_the_128_book_screen_costs_one_refresh():
    memo = _fresh(MEMO, lookback_s=10_800)
    memo._tick, memo._research_last_sim_ts = 7, 70_007 * S
    memo.realized_pnl_history = {70_000 * S + i * S: {i % BOOKS: 0.1} for i in range(7)}
    for _ in range(BOOKS):
        memo._research_refresh_rolling_kappa_cache()
    assert memo.frozen_refreshes == 1 and memo._v61_memo_hits == BOOKS - 1


def test_t1_off_is_the_frozen_refresh_every_time():
    agent = _fresh(MEMO, lookback_s=10_800, memo=False)
    agent._tick, agent._research_last_sim_ts = 1, 70_001 * S
    for _ in range(5):
        agent._research_refresh_rolling_kappa_cache()
    assert agent.frozen_refreshes == 5 and agent._v61_memo_hits == 0


# ---- T2 the profile's two scans ---------------------------------------------------------------------

def _history(rng, n=300):
    values = (0.0, -0.0, 0.1, -0.1, 1e-12, -3.25)
    history = {}
    for t in sorted(rng.sample(range(2_000), n)):
        history[t * S] = {
            b: rng.choice(values) if rng.random() < 0.3 else round(rng.uniform(-3.0, 3.0), 10)
            for b in rng.sample(range(24), rng.randint(1, 5))
        }
    return history


@pytest.mark.parametrize("seed", range(8))
def test_t2_count_and_sum_are_bit_identical_to_the_frozen_scans(seed):
    rng = random.Random(seed)
    frozen, memo = _fresh(COUNTING, lookback_s=600), _fresh(MEMO, lookback_s=600)
    for _ in range(6):
        history = _history(rng)
        frozen.realized_pnl_history = memo.realized_pnl_history = history
        current = rng.choice(list(history)) + rng.randint(0, 3) * S
        for book in range(-2, 27):
            assert memo._pnl_observation_count(book, current) == frozen._pnl_observation_count(book, current)
            got = memo._realized_pnl_lookback(book, current)
            want = frozen._realized_pnl_lookback(book, current)
            assert got == want and type(got) is type(want), (book, got, want)
    assert memo._v61_pnl_memo_builds == 6, "one pass per history, not one per book"


def test_t2_a_new_history_or_clock_rebuilds_the_window():
    memo = _fresh(MEMO, lookback_s=600)
    memo.realized_pnl_history = {100 * S: {1: 0.5}}
    assert memo._realized_pnl_lookback(1, 100 * S) == 0.5
    memo.realized_pnl_history = {100 * S: {1: 0.5}, 101 * S: {1: 0.25}}
    assert memo._realized_pnl_lookback(1, 101 * S) == 0.75
    assert memo._realized_pnl_lookback(1, 701 * S) == 0.25, "the lookback moves with the clock"
    assert memo._v61_pnl_memo_builds == 3


def test_t2_off_is_the_frozen_scan():
    agent = _fresh(MEMO, lookback_s=600, memo=False)
    agent.realized_pnl_history = {100 * S: {1: 0.5}}
    assert agent._pnl_observation_count(1, 100 * S) == 1 and agent._realized_pnl_lookback(1, 100 * S) == 0.5
    assert agent._v61_pnl_memo_builds == 0


# ---- T3 wiring ----------------------------------------------------------------------------------------

def test_t3_each_override_is_defined_once_and_falls_back_to_the_frozen_method():
    for name in ("_research_refresh_rolling_kappa_cache", "_pnl_observation_count", "_realized_pnl_lookback"):
        assert SIMPLE.count(f"    def {name}(") == 1, name
        assert f"super().{name}(" in _simple(name), name
    assert "research_v61_request_memo=1" in (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
