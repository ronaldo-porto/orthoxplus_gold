"""v6.16: S1 the deep side that trades toward the published fundamental is centred on it -- research_v616_fair_value.

The simulator's background traders converge on a hidden fundamental the validator publishes per book.  Our ladder was
centred on the mid, so its fills sat symmetrically around the fundamental and the ones against it lost on both halves of
the pay.  On a book with a fresh fundamental of this simulation the side trading toward it is centred on it, at most one
level-0 depth from the mid; the other side keeps the mid.  Replay (10-06): +19.0% making share on the previous
simulation's quiet trend, +41.8% on the new one; a price limit on the other side was refuted (won one, lost the other).
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

import research_v616_fair_value as fv  # noqa: E402
import research_v633_deep_layer as dl  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_4_1_volume_pace as t641  # noqa: E402
import test_research_v6_15 as t615  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000
NOW = t63.NOW
TICK = 0.01
SIM = "20261005_1128"
BUY, SELL = fv.SIDE_BUY, fv.SIDE_SELL
BOOK_LINE = "centre = self._v616_book(book_id, raw_bid, raw_ask, depth, tick_size, cfg)"
PRICE_LINE = "price = self._v616_target(side, level, level_d, centre, eff_side, vac, raw_bid, raw_ask, tick_size, dec)"
JUDGE_LINE = "elif centre is not None and self._v616_judged(row, side, level, centre, eff_side, level_depth, tick_size):"
EXTRA = {"V616SharedFeed": fv.SharedFeed, "V616_FAIR_VALUE_VERSION": fv.V616_FAIR_VALUE_VERSION,
         "v616_book_fundamental": fv.book_fundamental, "v616_needs_reprice": fv.needs_reprice,
         "v616_side_centres": fv.side_centres}
METHODS = ("_v616_count", "_v616_feed_ref", "_v616_book", "_v616_target", "_v616_judged", "_v616_snapshot")


def _page(fp_by_book, *, sim_ts_ns=NOW - 8 * S, sim_id=SIM):
    return {"sim_ts_ns": sim_ts_ns, "sim_id": sim_id, "fp": dict(fp_by_book), "wall": 0.0}


# ---- 1. the module ------------------------------------------------------------------------------------------------------

def test_the_page_parser_reads_every_books_fundamental_its_simulation_and_sim_time():
    text = "\n".join([
        "# HELP book_gauges ...",
        'book_gauges{book_gauge_name="fundamental_price",book_id="3",sim_id="20261005_1128"} 100.4',
        'book_gauges{book_gauge_name="fundamental_price",book_id="5",sim_id="20261005_1128"} 99.1',
        'book_gauges{book_gauge_name="mid",book_id="3",sim_id="20261005_1128"} 100.0',
        'books{book_id="3",sim_id="20261005_1128",timestamp="3981000000000"} 1',
    ])
    page = fv.parse_page(text)
    assert page == {"sim_ts_ns": 3_981 * S, "sim_id": SIM, "fp": {3: 100.4, 5: 99.1}}
    assert fv.parse_page("") is None and fv.parse_page(None) is None
    assert fv.parse_page(text.replace("timestamp", "ts")) is None                       # no sim time: unusable


def test_a_book_is_armed_only_on_a_fresh_page_of_this_simulation():
    kw = dict(state_ts_ns=NOW, sim_id=SIM)
    assert fv.book_fundamental(_page({3: 100.4}), 3, 100.00, 100.03, **kw) == (100.4, "armed")
    assert fv.book_fundamental(None, 3, 100.00, 100.03, **kw)[1] == "no_feed"
    assert fv.book_fundamental(_page({3: 100.4}, sim_id="20260929_2015"), 3, 100.00, 100.03, **kw)[1] == "other_sim"
    assert fv.book_fundamental(_page({3: 100.4}, sim_ts_ns=NOW - 21 * S), 3, 100.00, 100.03, **kw)[1] == "stale"
    assert fv.book_fundamental(_page({3: 100.4}, sim_ts_ns=NOW - 20 * S), 3, 100.00, 100.03, **kw)[1] == "armed"
    assert fv.book_fundamental(_page({3: 100.4}), 3, 100.00, 100.03, state_ts_ns=0, sim_id=SIM)[1] == "stale"
    assert fv.book_fundamental(_page({5: 100.4}), 3, 100.00, 100.03, **kw)[1] == "no_fundamental"
    assert fv.book_fundamental(_page({3: 100.4}), 3, 100.03, 100.00, **kw)[1] == "no_book"
    assert fv.book_fundamental(_page({"3": 100.4}), 3, 100.00, 100.03, **kw)[0] == 100.4       # a cache's string keys
    assert fv.book_fundamental(_page({3: 100.4}, sim_id=None), 3, 100.00, 100.03, **kw)[1] == "armed"   # unread id


def test_the_side_toward_the_fundamental_is_centred_on_it_at_most_one_depth_from_the_mid():
    c = fv.side_centres(100.015, 100.40, 21.5, TICK)
    assert c[SELL] == 100.015 and abs(c[BUY] - (100.015 + 0.215)) < 1e-12                # capped at one depth
    c = fv.side_centres(100.015, 100.05, 21.5, TICK)
    assert c[SELL] == 100.015 and abs(c[BUY] - 100.05) < 1e-12                            # inside one depth: on it
    c = fv.side_centres(100.015, 99.60, 21.5, TICK)
    assert c[BUY] == 100.015 and abs(c[SELL] - (100.015 - 0.215)) < 1e-12                 # below: the offer comes down
    assert fv.side_centres(100.015, 100.015, 21.5, TICK) == {BUY: 100.015, SELL: 100.015}
    assert fv.side_centres(None, 100.4, 21.5, TICK) is None and fv.side_centres(100.0, 100.4, 21.5, 0.0) is None


def test_a_resting_order_is_judged_by_the_band_around_its_sides_centre():
    c = 100.015 + 0.215
    assert not fv.needs_reprice(99.99, c, 21.5, TICK)                                     # 24 ticks: inside the band
    assert fv.needs_reprice(99.80, c, 21.5, TICK)                                         # 43 ticks: beyond 1.5 x depth
    assert fv.needs_reprice(100.20, c, 21.5, TICK)                                        # 3 ticks: inside 0.5 x depth
    assert dl.needs_reprice(99.80, 100.015, 21.5, TICK) is False                           # v6.15 would have kept it
    for px in (99.70, 99.80, 99.90, 100.00):                                              # around the mid: v6.3.3's band
        assert fv.needs_reprice(px, 100.015, 21.5, TICK) == dl.needs_reprice(px, 100.015, 21.5, TICK)


# ---- 2. the agent -------------------------------------------------------------------------------------------------------

def _method(name, base=None):
    scope = dict(base or {})
    scope.update(t615.EXTRA)
    scope.update(EXTRA)
    exec(compile(ast.Module(body=[ast.parse(_src(name)).body[0]], type_ignores=[]), f"<{name}>", "exec"), scope)
    return scope[name]


def _with16(fn):
    g = dict(fn.__globals__)
    g.update(EXTRA)
    return types.FunctionType(fn.__code__, g, fn.__name__, fn.__defaults__, fn.__closure__)


class _Feed:
    def __init__(self, snap):
        self.snap = snap

    def snapshot(self):
        return self.snap

    def status(self):
        return {"books": len((self.snap or {}).get("fp") or {})}


def _fv_agent(page, *, on=True):
    agent = t615._launch_agent({3: 300_000.0}, launch=False)
    cls = type(agent)
    methods = {n: _with16(getattr(cls, n)) for n in ("_v63_pass", "_v633_book")}
    for n in METHODS:
        methods[n] = _method(n, getattr(cls, "_v633_book").__globals__)    # the deep layer's helpers
    agent.__class__ = type("P616", (cls,), methods)
    agent.research_v616_fair_value = on
    agent._v616_counts, agent._v616_errors = {}, 0
    agent._v616_feed = _Feed(page)
    agent._direct_current_state_timestamp_ns = NOW
    return agent


def _state(books):
    cfg = types.SimpleNamespace(priceDecimals=2, volumeDecimals=4, fp_sigma=0.0277, duration=86_400 * S,
                                time_unit="ns", simulation_id=SIM)
    return types.SimpleNamespace(books=books, timestamp=NOW, config=cfg)


def _run(agent):
    resp = t63._Resp()
    agent._v63_pass(resp, _state({3: t63._book()}), {})
    return resp


def _level0(resp):
    return {(d, px) for (b, d, cid, px, _q) in t63._placed(resp) if b == 3 and cid in (40031, 40032)}


def test_without_a_usable_fundamental_the_book_is_v615_exactly():
    base = _level0(_run(_fv_agent(None, on=False)))
    assert base == {("BUY", 99.80), ("SELL", 100.23)}                                       # 21.5 ticks from 100.015
    for page in (None, _page({3: 100.4}, sim_id="20260929_2015"), _page({3: 100.4}, sim_ts_ns=NOW - 30 * S),
                 _page({5: 100.4})):
        agent = _fv_agent(page)
        assert _level0(_run(agent)) == base, page
        assert agent._v616_errors == 0 and not any(k.startswith("placed_") for k in agent._v616_counts)


def test_the_side_toward_the_fundamental_comes_toward_the_market_and_the_other_side_keeps_the_mid():
    agent = _fv_agent(_page({3: 100.40}))                                                    # 38 bps above the mid
    assert _level0(_run(agent)) == {("BUY", 99.99), ("SELL", 100.23)}                       # bid one tick behind the touch
    assert agent._v616_counts.get("book_armed") == 1 and agent._v616_errors == 0
    assert agent._v616_counts.get("placed_centred") == 1 and agent._v616_counts.get("placed_mid") == 1
    agent = _fv_agent(_page({3: 99.60}))                                                     # below: the mirror image
    assert _level0(_run(agent)) == {("BUY", 99.80), ("SELL", 100.04)}
    agent = _fv_agent(_page({3: 100.10}))                                                    # 8.5 ticks above: part way
    part = dl.deep_price(100.10, 21.5, BUY, bid=100.00, ask=100.03, tick=TICK, decimals=2)
    assert _level0(_run(agent)) == {("BUY", part), ("SELL", 100.23)} and 99.80 < part < 99.99
    agent = _fv_agent(_page({3: 100.015}))                                                   # on the mid: v6.15's prices
    assert _level0(_run(agent)) == {("BUY", 99.80), ("SELL", 100.23)}
    assert agent._v616_counts.get("placed_mid") == 2


def test_resting_orders_off_their_sides_new_centre_are_repriced_and_the_rest_kept():
    agent = _fv_agent(_page({3: 100.40}))
    t63.t14._rest(agent, 21, 3, 0, 99.80, cid=40031)                                        # v6.15's bid
    t63.t14._rest(agent, 22, 3, 1, 100.23, cid=40032)                                       # v6.15's offer
    resp = _run(agent)
    assert t63._cancelled(resp) == {(3, 21)}                                                 # the bid moves, the offer stays
    assert agent._v616_counts.get("cancel_centre_reprice") == 1
    agent = _fv_agent(_page({3: 100.40}))
    t63.t14._rest(agent, 21, 3, 0, 99.99, cid=40031)
    t63.t14._rest(agent, 22, 3, 1, 100.23, cid=40032)
    resp = _run(agent)
    assert t63._cancelled(resp) == set() and _level0(resp) == set()                         # both in place: kept
    # each side is judged around ITS centre: fp 100.10 puts the bid's at 100.10 and leaves the offer's at the mid
    agent = _fv_agent(_page({3: 100.10}))
    t63.t14._rest(agent, 21, 3, 0, 99.76, cid=40031)                                        # 34 ticks below its centre
    t63.t14._rest(agent, 22, 3, 1, 100.35, cid=40032)                                       # 33.5 ticks above the mid
    assert t63._cancelled(_run(agent)) == {(3, 21), (3, 22)}
    agent = _fv_agent(_page({3: 100.10}))
    t63.t14._rest(agent, 21, 3, 0, 99.80, cid=40031)                                        # 30 ticks: in its band
    t63.t14._rest(agent, 22, 3, 1, 100.33, cid=40032)                                       # 31.5 ticks: in its band
    assert t63._cancelled(_run(agent)) == set()


def test_the_switch_is_in_the_state_record():
    assert 'fair_value_on=int(bool(getattr(self, "research_v616_fair_value", False))),' in SIMPLE
    assert "fair_value=(self._v616_snapshot()" in SIMPLE
    snap = _fv_agent(_page({3: 100.40}))._v616_snapshot()
    assert snap["version"] == "fair_value_v6_16" and snap["errors"] == 0 and snap["feed"] == {"books": 1}


# ---- 3. the feed --------------------------------------------------------------------------------------------------------

def test_the_feed_fetches_once_per_period_through_the_shared_cache(tmp_path=None):
    import tempfile
    tmp = Path(tmp_path) if tmp_path else Path(tempfile.mkdtemp())
    page = 'book_gauges{book_gauge_name="fundamental_price",book_id="3",sim_id="X"} 100.4\nbooks{timestamp="5"} 1\n'
    calls, clock = [], [1_000.0]
    feed = fv.SharedFeed(cache_dir=str(tmp), fetch=lambda url, to: calls.append(url) or page, clock=lambda: clock[0])
    feed.step()
    assert calls == [fv.FEED_URL] and feed.snapshot()["fp"] == {3: 100.4}
    clock[0] += fv.FETCH_PERIOD_S - 1
    feed.step()
    assert len(calls) == 1                                                                   # the cache is fresh
    other = fv.SharedFeed(cache_dir=str(tmp), fetch=lambda url, to: calls.append(url) or page, clock=lambda: clock[0])
    other.step()
    assert len(calls) == 1 and other.snapshot()["sim_ts_ns"] == 5                            # a second agent reads it
    clock[0] += 2
    bad = fv.SharedFeed(cache_dir=str(tmp), fetch=lambda url, to: (_ for _ in ()).throw(OSError()), clock=lambda: clock[0])
    bad.step()
    assert bad.fetch_errors == 1 and bad.snapshot()["fp"] == {3: 100.4}                      # a failed fetch keeps the cache


# ---- 4. wiring ----------------------------------------------------------------------------------------------------------

def _params():
    start = LAUNCHER.index('PARAMS="')
    return LAUNCHER[start:LAUNCHER.index('"\n', start + len('PARAMS="'))]


def _shipped():
    return _params()[len('PARAMS="'):].replace("\\\n", " ")


def test_the_switch_ships_on_and_agrees_with_the_agent_default():
    params = _params()
    assert "research_v616_fair_value=1" in params and "research_v616_fair_value=0" not in params
    assert 'self.research_v616_fair_value = self._as_bool(getattr(self.config, "research_v616_fair_value", True))' in SIMPLE
    for line in (BOOK_LINE, PRICE_LINE, JUDGE_LINE):
        assert SIMPLE.count(line) == 1, line
    # v6.15's level-0 price line is kept verbatim (the v6.12 preflight greps it); v6.16 re-prices after it
    assert SIMPLE.index("price = v633_deep_price(mid, eff_side[side], side, bid=raw_bid, ask=raw_ask, tick=tick_size,") \
        < SIMPLE.index(PRICE_LINE)


def test_the_v616_block_is_gated_preflighted_and_listed():
    assert "; V6_14_BUILD=0; V6_15_BUILD=0; V6_16_BUILD=0; V6_17_BUILD=0; V6_18_BUILD=0; V6_19_BUILD=0; V6_20_BUILD=0; V6_21_BUILD=0; V6_22_BUILD=0; V6_23_BUILD=0; V6_24_BUILD=0; V6_25_BUILD=0; V6_26_BUILD=0; V6_27_BUILD=0; V6_28_BUILD=0; V6_29_BUILD=0\n" in LAUNCHER   # v6.17 appends its own
    arm = next(line for line in LAUNCHER.splitlines() if line.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_14_BUILD=1; V6_15_BUILD=1; V6_16_BUILD=1; V6_17_BUILD=1; V6_18_BUILD=1; V6_19_BUILD=1; V6_20_BUILD=1; V6_21_BUILD=1; V6_22_BUILD=1; V6_23_BUILD=1; V6_24_BUILD=1; V6_25_BUILD=1; V6_26_BUILD=1; V6_27_BUILD=1; V6_28_BUILD=1; V6_29_BUILD=1"), arm
    assert 'if [[ "${V6_16_BUILD:-0}" == "1" ]]; then' in LAUNCHER
    assert "tests/test_research_v6_16.py" in LAUNCHER


def test_every_v616_preflight_pattern_is_in_the_file_it_checks():
    start = LAUNCHER.index('if [[ "${V6_16_BUILD:-0}" == "1" ]]; then')
    block = LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.16 fair value PASS', start)]
    pats = re.findall(r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\"", block)
    assert len(pats) == 5, pats
    for pat, name in pats:
        assert pat in (STRATEGY / name).read_text(), (pat, name)


def _run_block(params, agent_path=STRATEGY):
    start = LAUNCHER.index("# v6.16 S1.")
    stop = LAUNCHER.index('echo "[preflight] v6.16 fair value PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_16_BUILD": "1"}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_the_v616_block_passes_the_shipped_params_and_refuses_the_switch_off():
    out = _run_block(_shipped())
    assert out.returncode == 0 and "[preflight] v6.16 fair value PASS" in out.stdout, out.stderr
    for bad in ("research_v616_fair_value=0", "research_v616_fair_value=10", ""):
        out = _run_block(_shipped().replace("research_v616_fair_value=1", bad))
        assert out.returncode == 1 and "v6.16 S1 build without research_v616_fair_value=1" in out.stderr, repr(bad)


def test_the_v616_block_refuses_an_agent_without_the_fundamental_the_centre_or_the_judgement(tmp_path=None):
    import tempfile
    tmp = Path(tmp_path) if tmp_path else Path(tempfile.mkdtemp())
    for f in STRATEGY.glob("*.py"):
        (tmp / f.name).write_text(f.read_text())
    for line, why in ((BOOK_LINE, "does not read the fundamental"), (PRICE_LINE, "not priced around its side's centre"),
                      (JUDGE_LINE, "not judged around their side's centre")):
        (tmp / "Strategy1_Research_Simple.py").write_text(SIMPLE.replace(line, "pass  # removed"))
        out = _run_block(_shipped(), agent_path=tmp)
        assert out.returncode == 1 and why in out.stderr, why
    (tmp / "Strategy1_Research_Simple.py").write_text(SIMPLE)
    src = (STRATEGY / "research_v616_fair_value.py").read_text()
    (tmp / "research_v616_fair_value.py").write_text(src.replace("def side_centres(", "def no_side_centres("))
    out = _run_block(_shipped(), agent_path=tmp)
    assert out.returncode == 1 and "has no side centres" in out.stderr
