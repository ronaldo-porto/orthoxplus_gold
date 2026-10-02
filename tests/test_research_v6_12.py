"""v6.12: S1 on a held book the side that reduces the position rests its level-0 deep order at half the book's depth
(research_v612_reduce_depth); the adding side, the ladder's levels, a vacuum order and the paper record keep the depth.

The validator pays making per book as 2 x min(buy capture, sell capture), so the side behind is the one whose fills pay,
and on a held book that is the side that unwinds the position.  Over v6.11's first 2,512 ticks on sim 20260929_2015 our
uids kept 0.52-0.68 of their gross capture as making.  Replayed with the validator's arithmetic: making 32.1 -> 75.2 on
10-02's prints with the volume line on the remaining cap (+134%), +32% on this sim's first hours, +24% and +9% on the
previous sim's burst and paying spans.
"""
import math
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

import research_v612_reduce_depth as rd  # noqa: E402
import research_v633_deep_layer as dl  # noqa: E402
import research_v64_board as b64  # noqa: E402
import research_v69_deep_ladder as d69  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_4_board as t64  # noqa: E402
import test_research_v6_9 as t69  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
BUY, SELL = dl.SIDE_BUY, dl.SIDE_SELL
PRICE_LINE = "price = v633_deep_price(mid, eff_side[side], side, bid=raw_bid, ask=raw_ask, tick=tick_size,"
EFF_LINE = "eff_side = {s_: float(v612_level0_depth(s_, depth, inv, min_order)) for s_ in (V63_SIDE_BUY, V63_SIDE_SELL)}"


# ---- 1. the rule ------------------------------------------------------------------------------------------------------

def test_the_reducing_side_is_the_side_whose_fill_shrinks_the_position():
    assert rd.reducing_side(3.0, 0.25) == SELL and rd.reducing_side(-3.0, 0.25) == BUY
    assert rd.reducing_side(0.25, 0.25) == SELL and rd.reducing_side(-0.25, 0.25) == BUY      # the minimum order counts
    for dust in (0.0, 0.1, -0.2499, 1e-12):
        assert rd.reducing_side(dust, 0.25) is None, dust                                   # dust is not a position
    for bad in (None, "x", float("nan"), float("inf")):
        assert rd.reducing_side(bad, 0.25) is None, bad
    assert rd.reducing_side(0.01, None) == SELL and rd.reducing_side(-0.01, "x") == BUY      # an unread minimum: any


def test_only_the_reducing_side_moves_and_only_by_the_factor():
    assert rd.REDUCE_DEPTH_FACTOR == 0.5 and rd.V612_REDUCE_DEPTH_VERSION == "reduce_depth_v6_12"
    assert rd.level0_depth(SELL, 30.0, 3.0, 0.25) == 15.0 and rd.level0_depth(BUY, 30.0, 3.0, 0.25) == 30.0
    assert rd.level0_depth(BUY, 30.0, -3.0, 0.25) == 15.0 and rd.level0_depth(SELL, 30.0, -3.0, 0.25) == 30.0
    for side in (BUY, SELL):
        assert rd.level0_depth(side, 30.0, 0.1, 0.25) == 30.0                               # dust: both keep the depth
        assert rd.level0_depth(side, 30.0, 0.0, 0.25) == 30.0
    assert rd.level0_depth(SELL, 30.0, 3.0, 0.25, factor=0.75) == 22.5
    for factor in (0.0, -0.5, 1.5, None, "x", float("nan")):                                 # a bad factor moves nothing
        assert rd.level0_depth(SELL, 30.0, 3.0, 0.25, factor=factor) == 30.0, factor
    assert rd.level0_depth(SELL, None, 3.0, 0.25) is None and rd.level0_depth("x", 30.0, 3.0, 0.25) == 30.0
    assert rd.level0_depth(SELL, 30.0, None, 0.25) == 30.0
    assert math.isclose(rd.level0_depth(SELL, 21.5, 7.0, 0.25), 10.75)


# ---- 2. the deep pass -------------------------------------------------------------------------------------------------

def _agent(*, on=True, **kw):
    agent = t69._agent(**kw)
    if on:
        agent.research_v612_reduce_depth = True
    return agent


def _px(depth, side, **book):
    return t69._px(depth, side, **book)


def test_a_long_book_rests_its_ask_at_half_the_depth_and_its_bid_at_the_depth():
    agent = _agent(venue={3: 3.0})
    resp = t64._run(agent, {3: t63._book()})
    assert t63._placed(resp) == {(3, "BUY", 40031, _px(30.0, BUY), 2.0), (3, "SELL", 40032, _px(15.0, SELL), 2.0)}
    assert agent._v633_counts.get("placed_reducing_l0") == 1 and agent._v633_errors == 0


def test_a_short_book_rests_its_bid_at_half_the_depth():
    agent = _agent(venue={3: -3.0})
    resp = t64._run(agent, {3: t63._book()})
    assert t63._placed(resp) == {(3, "BUY", 40031, _px(15.0, BUY), 2.0), (3, "SELL", 40032, _px(30.0, SELL), 2.0)}
    assert agent._v633_counts.get("placed_reducing_l0") == 1


def test_a_flat_or_dust_book_and_the_switch_off_are_v611():
    for venue, on in (({3: 0.0}, True), ({3: 0.1}, True), ({3: -0.2}, True), ({3: 3.0}, False), ({3: -3.0}, False)):
        agent = _agent(venue=venue, on=on)
        resp = t64._run(agent, {3: t63._book()})
        assert t63._placed(resp) == t69._level(0), (venue, on)
        assert "placed_reducing_l0" not in agent._v633_counts, (venue, on)


def test_a_resting_reducing_order_is_judged_against_its_own_distance():
    agent = _agent(venue={3: 3.0})
    t69._rest(agent, 11, 0, 0)                                                       # the bid at the depth (30)
    t69._rest(agent, 12, 1, 0, price=_px(15.0, SELL))                                # the ask at half of it
    resp = t64._run(agent, {3: t63._book()})
    assert t63._cancelled(resp) == set()                                             # both in their bands
    assert t63._placed(resp) == {(3, "BUY", 40033, _px(45.0, BUY), 2.0), (3, "SELL", 40034, _px(45.0, SELL), 2.0)}
    # an ask left at the full depth is out of the reducing side's band (30 > 1.5 x 15) and is repriced
    agent = _agent(venue={3: 3.0})
    t69._rest(agent, 11, 0, 0)
    t69._rest(agent, 12, 1, 0)
    resp = t64._run(agent, {3: t63._book()})
    assert t63._cancelled(resp) == {(3, 12)} and agent._v633_counts.get("cancel_deep_reprice") == 1
    # level 0 of the ask waits for the removal; its p99 level is not held by it (v6.9 S1: ownership per level)
    assert t63._placed(resp) == {(3, "BUY", 40033, _px(45.0, BUY), 2.0), (3, "SELL", 40034, _px(45.0, SELL), 2.0)}
    # with the switch off the same half-depth ask is out of the depth's band (14 < 0.5 x 30) and goes
    agent = _agent(venue={3: 3.0}, on=False)
    t69._rest(agent, 11, 0, 0)
    t69._rest(agent, 12, 1, 0, price=_px(14.0, SELL))
    resp = t64._run(agent, {3: t63._book()})
    assert t63._cancelled(resp) == {(3, 12)}


def _record_layer(record, alpha=50.0):
    layer = dl.DeepLayer(clip=2.0, max_clips=d69.DEEP_MAX_CLIPS, sweep_quantile=0.97)
    db = dl.DeepBook()
    db.sweeps.extend(record)
    db.paper.fills = 1
    db.paper.buckets[dl.sampled_key(t63.NOW)] = [float(alpha), 0.0, 1.0, 0.0]
    layer.books[3] = db
    return layer


def test_each_sides_ladder_is_filtered_against_its_own_level_0():
    # p97 30, p99 30.4 (not a price step past the adding side's level 0), max 60
    record = [20.0] * 388 + [30.0] * 8 + [30.4] * 3 + [60.0]
    layer = _record_layer(record)
    assert layer.depth(3) == 30.0 and layer.sweep_depths(3, d69.LADDER_QUANTILES) == (30.4, 60.0)
    agent = _agent(venue={3: 3.0})
    agent._v633_deep = layer
    t69._rest(agent, 11, 0, 0)
    t69._rest(agent, 12, 1, 0, price=_px(15.0, SELL))
    resp = t64._run(agent, {3: t63._book()})
    # the bid (adding) skips p99 and takes the max level; the ask (reducing, level 0 at 15) takes p99 first
    assert t63._placed(resp) == {(3, "BUY", 40035, _px(60.0, BUY), 4.0), (3, "SELL", 40034, _px(30.4, SELL), 2.0)}


def test_a_vacuum_order_inside_a_blown_out_spread_is_unchanged():
    wide = dict(bid=100.00, ask=100.30)
    vb = b64.vacuum_price(100.15, 12.0, BUY, bid=100.00, ask=100.30, tick=t69.TICK, decimals=2)
    va = b64.vacuum_price(100.15, 12.0, SELL, bid=100.00, ask=100.30, tick=t69.TICK, decimals=2)
    agent = _agent(venue={3: 3.0})
    resp = t64._run(agent, {3: t63._book(**wide)})
    assert t63._placed(resp) == {(3, "BUY", 40031, vb, 2.0), (3, "SELL", 40032, va, 2.0)}
    assert "placed_reducing_l0" not in agent._v633_counts


def test_a_paced_long_book_keeps_its_deepest_bid_and_rests_its_ask_at_half_the_depth():
    agent = _agent(pace=True)
    agent.research_v612_reduce_depth = True
    resp = t69._paced(agent, 3.0)
    assert t63._placed(resp) == {(3, "BUY", 40035, _px(60.0, BUY), 4.0), (3, "SELL", 40032, _px(15.0, SELL), 2.0)}
    agent = _agent(pace=True, on=False)
    resp = t69._paced(agent, 3.0)                                                    # v6.11: the ask at the depth
    assert t63._placed(resp) == {(3, "BUY", 40035, _px(60.0, BUY), 4.0), (3, "SELL", 40032, _px(30.0, SELL), 2.0)}


def test_the_reducing_order_still_rests_strictly_behind_the_touch():
    agent = _agent(venue={3: 3.0})
    agent._v633_deep = _record_layer([1.0] * 400)                                    # depth 1 tick: half is inside
    resp = t64._run(agent, {3: t63._book()})
    asks = [p for p in t63._placed(resp) if p[1] == "SELL"]
    assert asks and all(p[3] >= 100.04 for p in asks), asks                           # ask 100.03 + one tick


# ---- 3. wiring --------------------------------------------------------------------------------------------------------

def _params():
    start = LAUNCHER.index('PARAMS="')
    return LAUNCHER[start:LAUNCHER.index('"\n', start + len('PARAMS="'))]


def _shipped():
    return _params()[len('PARAMS="'):].replace("\\\n", " ")


def test_the_switch_ships_on_and_agrees_with_the_agent_default():
    assert "research_v612_reduce_depth=1" in _params() and "research_v612_reduce_depth=0" not in _params()
    assert ('self.research_v612_reduce_depth = self._as_bool(getattr(self.config, "research_v612_reduce_depth", True))'
            in SIMPLE)
    assert SIMPLE.count(EFF_LINE) == 1 and SIMPLE.count(PRICE_LINE) == 1
    assert 'bool(getattr(self, "research_v612_reduce_depth", False))' in SIMPLE       # a harness agent stays v6.11


def test_the_v612_block_is_gated_preflighted_and_listed():
    assert "; V6_10_BUILD=0; V6_11_BUILD=0; V6_12_BUILD=0\n" in LAUNCHER
    arm = next(line for line in LAUNCHER.splitlines() if line.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_11_BUILD=1; V6_12_BUILD=1"), arm
    assert 'if [[ "${V6_12_BUILD:-0}" == "1" ]]; then' in LAUNCHER
    assert 'echo "[preflight] v6.12 reducing-side depth PASS (factor 0.5)"' in LAUNCHER
    assert "tests/test_research_v6_12.py" in LAUNCHER


def test_every_v612_preflight_pattern_is_in_the_file_it_checks():
    start = LAUNCHER.index('if [[ "${V6_12_BUILD:-0}" == "1" ]]; then')
    block = LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.12 reducing-side depth PASS', start)]
    pats = re.findall(r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\"", block)
    assert len(pats) == 4, pats
    for pat, name in pats:
        assert pat in (STRATEGY / name).read_text(), (pat, name)


def _run_block(params, agent_path=STRATEGY):
    start = LAUNCHER.index("# v6.12 S1.")
    stop = LAUNCHER.index('echo "[preflight] v6.12 reducing-side depth PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_12_BUILD": "1"}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_the_v612_block_passes_the_shipped_params_and_refuses_the_switch_off():
    out = _run_block(_shipped())
    assert out.returncode == 0 and "[preflight] v6.12 reducing-side depth PASS (factor 0.5)" in out.stdout, out.stderr
    for bad in ("research_v612_reduce_depth=0", "research_v612_reduce_depth=10", ""):
        out = _run_block(_shipped().replace("research_v612_reduce_depth=1", bad))
        assert out.returncode == 1 and "v6.12 S1 build without research_v612_reduce_depth=1" in out.stderr, repr(bad)


def test_the_v612_block_refuses_an_agent_that_prices_level_0_at_the_book_depth(tmp_path):
    for name in ("research_v612_reduce_depth.py",):
        (tmp_path / name).write_text((STRATEGY / name).read_text())
    (tmp_path / "Strategy1_Research_Simple.py").write_text(
        SIMPLE.replace(PRICE_LINE, "price = v633_deep_price(mid, depth, side, bid=raw_bid, ask=raw_ask, tick=tick_size,"))
    out = _run_block(_shipped(), agent_path=tmp_path)
    assert out.returncode == 1 and "v6.12 S1 the level-0 order is not priced at its side's depth" in out.stderr
    (tmp_path / "Strategy1_Research_Simple.py").write_text(SIMPLE)
    (tmp_path / "research_v612_reduce_depth.py").write_text(
        (STRATEGY / "research_v612_reduce_depth.py").read_text().replace("REDUCE_DEPTH_FACTOR = 0.5 ", "REDUCE_DEPTH_FACTOR = 0.25 "))
    out = _run_block(_shipped(), agent_path=tmp_path)
    assert out.returncode == 1 and "does not carry the replayed factor 0.5" in out.stderr
