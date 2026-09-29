"""v6.7: the deep layer rests at the 97th percentile of each book's own sweep depths, not the 90th.

Mainnet 09-29: the validator pays proportional_both (half captured-spread share, half net-alpha share) under a
500k-per-book-per-simulation volume cap, so pay per unit of volume decides.  Replay with that arithmetic on two
stretches of sim 20260924_1653: 2 base at p97 vs the live 1-base p90 +24.1% / +25.4% pay share, ahead in every window.
"""
import ast
import math
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v633_deep_layer as dl  # noqa: E402
import research_v64_board as b64  # noqa: E402
import research_v67_deep_depth as dd  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_4_board as t64  # noqa: E402
import test_research_v6_5_deep_clips as t65  # noqa: E402
import test_research_v6_6 as t66  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
TICK = 0.01
EXTRA = {"v67_sweep_quantile": dd.sweep_quantile, "V67_DEEP_DEPTH_VERSION": dd.V67_DEEP_DEPTH_VERSION}
SWEEPS = [float(x) for x in range(1, 401)]            # a full record: p90 = 361 ticks, p97 = 389 ticks


def _method(name):
    scope = dict(EXTRA)
    exec(compile(ast.Module(body=[ast.parse(_src(name)).body[0]], type_ignores=[]), f"<{name}>", "exec"), scope)
    return scope[name]


def _layer(q, sweeps=SWEEPS, books=(3,), alpha=50.0):
    """An open board, a known depth from ``sweeps`` and a paying paper record, at quantile ``q``."""
    layer = dl.DeepLayer(clip=2.0, max_clips=2.0, sweep_quantile=q)
    for b in books:
        db = dl.DeepBook()
        db.sweeps.extend(sweeps)
        db.paper.fills = 1
        db.paper.buckets[dl.sampled_key(t63.NOW)] = [float(alpha), 0.0, 1.0, 0.0]
        layer.books[b] = db
    return layer


def _pass(q, book=None):
    agent = t66._agent({})
    agent._v633_deep = _layer(q)
    return t64._run(agent, {3: book or t63._book()})


def _deep(q, bid=100.00, ask=100.03):
    d = dl.quantile(SWEEPS, q)
    mid = 0.5 * (bid + ask)
    return {("BUY", dl.deep_price(mid, d, dl.SIDE_BUY, bid=bid, ask=ask, tick=TICK, decimals=2), 2.0),
            ("SELL", dl.deep_price(mid, d, dl.SIDE_SELL, bid=bid, ask=ask, tick=TICK, decimals=2), 2.0)}


# ---- 1. the rule ------------------------------------------------------------------------------------------------------

def test_the_quantile_is_p97_with_the_switch_and_v633s_p90_without():
    assert dd.DEEP_QUANTILE == 0.97 and dl.SWEEP_QUANTILE == 0.9
    assert dd.sweep_quantile(True) == 0.97 and dd.sweep_quantile(False) == 0.9
    for bad in (1.0, 0.0, 1.5, -0.2, "x", None, float("nan"), float("inf")):
        assert dd.sweep_quantile(True, bad) == 0.9                                  # unreadable: v6.3.3's depth


# ---- 2. the layer -----------------------------------------------------------------------------------------------------

def test_the_layer_rests_at_its_own_quantile_and_defaults_to_p90():
    assert dl.DeepLayer().sweep_quantile == 0.9                                    # every earlier caller unchanged
    assert _layer(0.9).depth(3) == 361.0 and _layer(0.97).depth(3) == 389.0
    assert _layer(0.97, sweeps=SWEEPS[:dl.SWEEP_MIN - 1]).depth(3) is None         # below SWEEP_MIN: unknown
    assert _layer(0.97).snapshot()["sweep_quantile"] == 0.97


def test_the_paper_record_rests_at_the_same_depth_as_the_live_orders():
    for q in (0.9, 0.97):
        layer = _layer(q)
        d = layer.observe(3, t63.NOW, [], bid=100.00, ask=100.03, tick=TICK, decimals=2)
        assert d == dl.quantile(SWEEPS, q)
        paper = layer.books[3].paper
        assert paper.bid == dl.deep_price(100.015, d, dl.SIDE_BUY, bid=100.00, ask=100.03, tick=TICK, decimals=2)
        assert paper.ask == dl.deep_price(100.015, d, dl.SIDE_SELL, bid=100.00, ask=100.03, tick=TICK, decimals=2)


# ---- 3. the agent -----------------------------------------------------------------------------------------------------

def _obj(on, *, with_v67=True):
    o = t65._obj(2.0, 2.0)
    if with_v67:
        o.research_v67_deep_depth = on
        o._v67_sweep_quantile = types.MethodType(_method("_v67_sweep_quantile"), o)
    return o


def test_the_agent_builds_its_layer_at_p97_with_the_switch_and_p90_without():
    deep = _obj(True)._v633_deep_ref()
    assert deep.sweep_quantile == 0.97 and (deep.clip, deep.max_clips) == (2.0, 2.0)
    assert _obj(False)._v633_deep_ref().sweep_quantile == 0.9
    assert _obj(True, with_v67=False)._v633_deep_ref().sweep_quantile == 0.9       # the v6.5 harness's p90 stub


def test_live_deep_orders_rest_at_the_p97_depth():
    assert t66._sides(_pass(0.97)) == _deep(0.97)
    assert t66._sides(_pass(0.9)) == _deep(0.9)                                    # the switch off is v6.6.1
    (_s, buy97, _q), = [x for x in _deep(0.97) if x[0] == "BUY"]
    (_s, buy90, _q), = [x for x in _deep(0.9) if x[0] == "BUY"]
    assert buy97 < buy90                                                           # further from the mid


def test_a_blown_out_spread_still_rests_in_the_vacuum():
    wide = t63._book(bid=100.00, ask=100.30)                                       # vacuum 0.8 x 15 = 12 ticks
    vac = b64.vacuum_depth(100.00, 100.30, TICK)
    want = {("BUY", b64.vacuum_price(100.15, vac, dl.SIDE_BUY, bid=100.00, ask=100.30, tick=TICK, decimals=2), 2.0),
            ("SELL", b64.vacuum_price(100.15, vac, dl.SIDE_SELL, bid=100.00, ask=100.30, tick=TICK, decimals=2), 2.0)}
    assert t66._sides(_pass(0.97, wide)) == want == t66._sides(_pass(0.9, wide))


# ---- 4. wiring --------------------------------------------------------------------------------------------------------

def test_the_switch_defaults_on_ships_in_params_and_is_preflighted():
    assert 'self.research_v67_deep_depth = self._as_bool(getattr(self.config, "research_v67_deep_depth", True))' in SIMPLE
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"\n', start + len('PARAMS="'))]
    assert "research_v67_deep_depth=1" in params and "research_v65_deep_clip_mult=${DEEP_CLIP_MULT}" in params
    assert 'DEEP_CLIP_MULT="${DEEP_CLIP_MULT:-2.0}"' in LAUNCHER                 # replayed at 2 base, the default
    assert 'echo "[preflight] v6.7 deep depth p97 PASS"' in LAUNCHER
    assert "tests/test_research_v6_7.py" in LAUNCHER


def test_the_launcher_parses():
    out = subprocess.run(["bash", "-n", str(ROOT / "run_strategy1_research_simple_multi.sh")], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_the_state_row_reports_v6_7():
    assert ('v67=(self._v67_snapshot() if getattr(self, "research_v67_deep_depth", None) is not None else {}),'
            in _src("_v62_telemetry"))
    o = _obj(True)
    o._v67_snapshot = types.MethodType(_method("_v67_snapshot"), o)
    assert o._v67_snapshot() == {"version": "deep_depth_v6_7", "deep_depth_on": 1, "sweep_quantile": 0.97}
    o._v633_deep_ref()
    assert o._v67_snapshot()["sweep_quantile"] == 0.97
    assert math.isclose(_obj(False)._v633_deep_ref().snapshot()["sweep_quantile"], 0.9)
