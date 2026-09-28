"""v6.5: the deep layer's orders are twice the touch clip and a book holds three of them; the final validator's exposure
cap follows the deep bound.

Replay 09-27 over the validator's window (13,988 states, partial fills by print size, the side-ownership delay, the
v6.4.1 pacer on each UID's remaining allowance): alpha +55..+93%, making +37..+59%, skill 3.72 -> 4.90 on UID 94's
allowance and 2.83 -> 5.75 on UID 104's.  At v6.4.1's size (x1.0, two clips) every rule here is v6.4.1.
"""
import ast
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v633_deep_layer as dl  # noqa: E402
import research_v63_trend_target as tt  # noqa: E402
import research_v65_deep_clips as dc  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_3_3_deep_layer as d633  # noqa: E402
import test_research_v6_4_board as t64  # noqa: E402
from _harness import extractor  # noqa: E402
from research_direct_exposure import add_order_to_batch  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
TICK = 0.01
EXTRA = {"V65_DEEP_CLIPS_VERSION": dc.V65_DEEP_CLIPS_VERSION, "v65_deep_clip": dc.deep_clip,
         "v65_max_clips": dc.max_clips, "v65_book_bound": dc.book_bound, "v65_caps_for": dc.caps_for,
         "V633DeepLayer": dl.DeepLayer, "V62_DEFAULT_LOOKBACK_NS": dl.LOOKBACK_NS, "v63_caps_for": tt.caps_for,
         "V63_TREND_TARGET_VERSION": tt.V63_TREND_TARGET_VERSION}


def _method(name):
    scope = dict(EXTRA)
    exec(compile(ast.Module(body=[ast.parse(_src(name)).body[0]], type_ignores=[]), f"<{name}>", "exec"), scope)
    return scope[name]


def _obj(mult=2.0, clips=3.0, *, deep_on=True, touch_clip=1.0, **attrs):
    """The v6.5 methods on a bare object; mult=None leaves the switches unset (an agent from before v6.5)."""
    o = types.SimpleNamespace(mm_base_size=0.25, research_kappa_lookback_ns=0, _v633_deep=None, _tick=0,
                              _v63_caps_applied=False, _v63_counts={}, events=[], **attrs)
    if mult is not None:
        o.research_v65_deep_clip_mult = mult
        o.research_v65_deep_max_clips = clips
    o._v63_clip = lambda: touch_clip
    o._v633_on = lambda: deep_on
    for n in ("_v65_deep_clip", "_v65_max_clips", "_v65_snapshot", "_v633_deep_ref", "_v63_apply_caps"):
        setattr(o, n, types.MethodType(_method(n), o))
    o._v63_count = lambda key, n=1: o._v63_counts.__setitem__(key, o._v63_counts.get(key, 0) + n)
    o._emit = lambda *a, **k: o.events.append((a, k))
    return o


def _layer(clip=2.0, max_clips=3.0, books=(3,), depth=21.5, alpha=50.0, sweeps=dl.SWEEP_MIN):
    """d633._deep_layer at a given size: an open board, a known depth and a paying paper record."""
    layer = dl.DeepLayer(clip=clip, max_clips=max_clips)
    for b in books:
        db = dl.DeepBook()
        db.sweeps.extend([depth] * sweeps)
        db.paper.fills = 1
        db.paper.buckets[dl.sampled_key(t63.NOW)] = [float(alpha), 0.0, 1.0, 0.0]
        layer.books[b] = db
    return layer


def _sides(resp):
    return {(p[1], p[4]) for p in d633._deep_placed(resp)}


# ---- 1. the module --------------------------------------------------------------------------------------------------

def test_the_deep_clip_is_the_touch_clip_times_the_multiple_never_below_the_minimum_order():
    assert dc.CLIP_MULT == 2.0 and dc.MAX_CLIPS == 2.0 and dc.V641_CLIP_MULT == 1.0          # v6.6 S3: two clips
    assert dc.deep_clip(1.0, 2.0, 0.25) == 2.0
    assert dc.deep_clip(1.0, 1.0, 0.25) == 1.0                                  # v6.4.1
    assert dc.deep_clip(1.0, "2.0") == 2.0
    for bad in (None, "x", 0.0, -2.0, float("nan"), float("inf")):
        assert dc.deep_clip(1.0, bad, 0.25) == 1.0                              # a bad multiple is v6.4.1's
    assert dc.deep_clip(0.1, 2.0, 0.25) == 0.25                                 # never below the minimum order


def test_the_bound_is_at_least_one_clip_and_otherwise_v6_3_3s():
    assert dc.max_clips(3.0) == 3.0 and dc.max_clips("3.0") == 3.0
    assert dc.max_clips(0.5) == 1.0                                             # a smaller bound never holds an order
    assert dc.max_clips(None) == dl.DEEP_MAX_CLIPS == 2.0 and dc.max_clips("x") == 2.0
    assert dc.book_bound(2.0, 3.0) == 6.0 and dc.book_bound(1.0, None) == 2.0


def test_the_exposure_cap_is_the_deep_bound_plus_one_deep_order_in_flight_per_book():
    want = {"research_max_total_abs_base": 1024.0, "research_a195_max_seed_abs_base": 1024.0}
    assert dc.caps_for(128, clip=2.0, max_clips=3.0) == want                     # (3 + 1) x 2 base x 128 books
    assert dc.caps_for(128, clip=1.0, max_clips=2.0) == tt.caps_for(128, clip=1.0)   # v6.4.1: v6.3's three clips
    assert dc.caps_for(128, clip=2.0, max_clips=2.0)["research_max_total_abs_base"] == 768.0     # S1 alone
    assert dc.caps_for(128, clip=1.0, max_clips=3.0)["research_max_total_abs_base"] == 512.0     # S2 alone


def test_under_v6_3s_cap_the_validator_would_refuse_the_new_room():
    # the final validator charges each order its worst-case fill: a book at 5 base with a 2-base adding order reaches 7
    worst = add_order_to_batch(net=5.0, buy_before=0.0, sell_before=0.0, side="buy", quantity=2.0,
                               min_order=0.25, eps=1e-9).new_worst_abs
    assert abs(worst - 7.0) < 1e-9
    assert 128 * worst > tt.caps_for(128, clip=1.0)["research_max_total_abs_base"]        # 896 > 384: refused
    # the most a book can reach is its bound (room() stops adding at it) plus one order in flight
    assert 128 * (dc.book_bound(2.0, 3.0) + 2.0) == dc.caps_for(128, clip=2.0, max_clips=3.0)["research_max_total_abs_base"]


# ---- 2. the layer ---------------------------------------------------------------------------------------------------

def test_the_layer_holds_its_own_bound_in_room_and_book_open():
    layer = _layer(clip=2.0, max_clips=3.0)
    assert layer.update_board() is True
    assert layer.room(dl.SIDE_BUY, 5.9) and not layer.room(dl.SIDE_BUY, 6.0)
    assert layer.room(dl.SIDE_SELL, -5.9) and not layer.room(dl.SIDE_SELL, -6.0)
    assert layer.book_open(3, 30.0, 6.0) and not layer.book_open(3, 30.0, 6.5)
    assert layer.book_open(3, 30.0, 6.5, inventory_bound=False)
    old = dl.DeepLayer()                                                         # v6.3.3 / v6.4.1 unchanged
    assert old.clip == 1.0 and old.max_clips == dl.DEEP_MAX_CLIPS == 2.0
    assert old.room(dl.SIDE_BUY, 1.9) and not old.room(dl.SIDE_BUY, 2.0)
    snap = layer.snapshot()
    assert snap["clip"] == 2.0 and snap["max_clips"] == 3.0


def _paper_inventory(layer, states=6):
    """Sell prints far through the paper bid, one state after another."""
    for i in range(states):
        trades = [{"p": 99.50, "q": 10.0, "s": 1, "Ma": -1, "Ta": 7}] if i else []
        layer.observe(3, t63.NOW + i * 1_000_000_000, trades, bid=100.00, ask=100.03, tick=TICK, decimals=2)
    return layer.books[3].paper


def test_the_paper_record_fills_at_the_layers_clip_up_to_its_bound():
    paper = _paper_inventory(_layer(clip=2.0, max_clips=3.0, sweeps=200))
    assert paper.inv == 6.0 and paper.fills == 1 + 3                            # three clips of 2, then full
    paper = _paper_inventory(_layer(clip=1.0, max_clips=2.0, sweeps=200))
    assert paper.inv == 2.0 and paper.fills == 1 + 2                            # v6.4.1: two clips of 1


# ---- 3. the pass ----------------------------------------------------------------------------------------------------

def test_deep_orders_rest_at_the_layers_clip_while_the_touch_clip_stays():
    agent = t64._agent(_layer(clip=2.0, max_clips=3.0))
    resp = t64._run(agent, {3: t63._book()})
    assert _sides(resp) == {("BUY", 2.0), ("SELL", 2.0)}
    assert agent._v63_clip() == 1.0                                              # the touch layers keep their clip
    resp = t64._run(t64._agent(d633._deep_layer()), {3: t63._book()})
    assert _sides(resp) == {("BUY", 1.0), ("SELL", 1.0)}                         # v6.4.1


def test_vacuum_orders_rest_at_the_layers_clip_too():
    agent = t64._agent(_layer(clip=2.0, max_clips=3.0))
    resp = t64._run(agent, {3: t63._book(bid=100.00, ask=100.40)})              # a 40-tick spread
    placed = d633._deep_placed(resp)
    assert {p[4] for p in placed} == {2.0} and len(placed) == 2
    assert all(100.00 < p[3] < 100.40 for p in placed)                           # inside the gap
    assert agent._v64_counts.get("placed_vacuum") == 2


def test_a_long_book_keeps_adding_until_the_v6_5_bound():
    resp = t64._run(t64._agent(_layer(clip=2.0, max_clips=3.0), venue={3: 4.0}), {3: t63._book()})
    assert {s for s, _q in _sides(resp)} == {"BUY", "SELL"}                      # 4 < 6: room to add
    resp = t64._run(t64._agent(_layer(clip=2.0, max_clips=3.0), venue={3: 6.0}), {3: t63._book()})
    assert {s for s, _q in _sides(resp)} == {"SELL"}                             # at the bound: reduce only
    resp = t64._run(t64._agent(d633._deep_layer(), venue={3: 4.0}), {3: t63._book()})
    assert {s for s, _q in _sides(resp)} == {"SELL"}                             # v6.4.1: past its 2-base bound


# ---- 4. the agent ---------------------------------------------------------------------------------------------------

def test_the_layer_is_built_at_its_own_clip_and_bound():
    o = _obj(2.0, 3.0)
    deep = o._v633_deep_ref()
    assert (deep.clip, deep.max_clips) == (2.0, 3.0) and o._v633_deep is deep and o._v633_deep_ref() is deep
    deep = _obj(1.0, 2.0)._v633_deep_ref()
    assert (deep.clip, deep.max_clips) == (1.0, 2.0)                             # v6.4.1's size
    deep = _obj(None)._v633_deep_ref()
    assert (deep.clip, deep.max_clips) == (1.0, 2.0)                             # an agent from before v6.5
    deep = _obj("bad", "bad")._v633_deep_ref()
    assert (deep.clip, deep.max_clips) == (1.0, 2.0)                             # a bad parameter never stops it


def _caps(o, books=128):
    o.research_max_total_abs_base, o.research_a195_max_seed_abs_base = 64.0, 32.0      # the v6.2 caps, live
    o._v63_apply_caps(types.SimpleNamespace(books={b: None for b in range(books)}))
    return o.research_max_total_abs_base, o.research_a195_max_seed_abs_base


def test_the_exposure_cap_follows_the_deep_bound():
    assert _caps(_obj(2.0, 3.0)) == (1024.0, 1024.0)
    assert _caps(_obj(1.0, 2.0)) == (384.0, 384.0)                               # v6.4.1
    assert _caps(_obj(None)) == (384.0, 384.0)
    assert _caps(_obj(2.0, 3.0, deep_on=False)) == (384.0, 384.0)                 # no deep layer, no deep bound
    o = _obj(2.0, 3.0)
    _caps(o)
    assert o._v63_counts == {"caps_applied": 1} and o.events[0][1]["after"]["research_max_total_abs_base"] == 1024.0


def test_the_state_row_reports_the_deep_size():
    o = _obj(2.0, 3.0)
    o._v633_deep_ref()
    o.research_max_total_abs_base = 1024.0
    snap = o._v65_snapshot()
    assert snap == {"version": "deep_clips_v6_5", "mult": 2.0, "clip": 2.0, "max_clips": 3.0,
                    "book_bound_base": 6.0, "cap_total_abs_base": 1024.0}
    assert ('deep_clips=(self._v65_snapshot() if getattr(self, "research_v65_deep_clip_mult", None) is not None else {}),'
            in _src("_v62_telemetry"))


# ---- 5. wiring ------------------------------------------------------------------------------------------------------

def test_the_switches_default_to_the_replayed_size_ship_in_params_and_are_preflighted():
    assert ('self.research_v65_deep_clip_mult = float(getattr(self.config, "research_v65_deep_clip_mult", V65_CLIP_MULT))'
            in SIMPLE)
    assert ('self.research_v65_deep_max_clips = float(getattr(self.config, "research_v65_deep_max_clips", V65_MAX_CLIPS))'
            in SIMPLE)
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"\n', start + len('PARAMS="'))]
    # v6.6: the multiple comes from --deep_clip_mult (1.0 or 2.0, default 2.0) and S3 sets two clips of room
    assert "research_v65_deep_clip_mult=${DEEP_CLIP_MULT}" in params and "research_v65_deep_max_clips=2.0" in params
    assert 'DEEP_CLIP_MULT="${DEEP_CLIP_MULT:-2.0}"' in LAUNCHER
    assert 'echo "[preflight] v6.5 deep clips PASS (deep_clip_mult=${DEEP_CLIP_MULT})"' in LAUNCHER
    assert "tests/test_research_v6_5_deep_clips.py" in LAUNCHER


def test_the_pass_places_deep_orders_at_the_layers_clip():
    src = _src("_v63_pass")
    assert "tick_size=tick_size, dec=dec, clip=deep.clip, min_order=min_order" in src
    assert "clip=clip, min_order=min_order, budget=budget, expiry=expiry, cfg=cfg,\n                    paced=paced" not in src
