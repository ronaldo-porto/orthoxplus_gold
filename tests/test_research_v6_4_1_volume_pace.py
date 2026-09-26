"""v6.4.1: each book spends the validator's volume cap evenly over the time left in the simulation.

UID 94 on v6.4 (09-27 04:00 JST): ~293k of the 500k-quote cap used per book by sim-s ~34,000, 6 books capped, ~11.5
quote per sim-s per book -- most books would be capped ~27 wall-h later, long before the simulation ends, and a capped book
takes no order at all.  The cap is summed over the 86,400-s assessment period, which a simulation never outlives.
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
import research_v641_pace as vp  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_3_3_deep_layer as d633  # noqa: E402
import test_research_v6_4_board as t64  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000
NOW = t63.NOW
CAP = 500_000.0
EXTRA = {"V641VolumePace": vp.VolumePace, "V641_PACE_VERSION": vp.V641_PACE_VERSION, "v641_adds": vp.adds,
         "v641_duration_ns": vp.duration_ns, "V641_CANCEL_PACED": vp.CANCEL_PACED}


def _method(name):
    scope = dict(EXTRA)
    exec(compile(ast.Module(body=[ast.parse(_src(name)).body[0]], type_ignores=[]), f"<{name}>", "exec"), scope)
    return scope[name]


def _with_globals(fn):
    g = dict(fn.__globals__)
    g.update(EXTRA)
    return types.FunctionType(fn.__code__, g, fn.__name__, fn.__defaults__, fn.__closure__)


def _agent(layer, *, pace=True, used=None, rate=None, **kw):
    """A v6.4 pass agent with v6.4.1; `rate` (quote per sim-s over the last 600 s) seeds each book's reported volume."""
    agent = t64._agent(layer, **kw)
    cls = type(agent)
    methods = {n: _with_globals(getattr(cls, n)) for n in ("_v63_pass", "_v633_book")}
    for n in ("_v641_on", "_v641_count", "_v641_pace_ref", "_v641_snapshot"):
        methods[n] = _method(n)
    used = dict(used or {})
    methods["_research_volume_cap_quote"] = lambda self, state: CAP
    methods["_research_book_traded_volume"] = lambda self, book_id: used.get(int(book_id), 0.0)
    agent.__class__ = type("P641", (cls,), methods)
    agent.research_v641_volume_pace = pace
    agent._v641_counts, agent._v641_errors = {}, 0
    agent._v641_pace = vp.VolumePace()
    for b, r in (rate or {}).items():
        agent._v641_pace.observe(b, NOW - 600 * S, used.get(b, 0.0) - r * 600)
    return agent


def _state(books, duration=None):
    cfg = types.SimpleNamespace(priceDecimals=2, volumeDecimals=4)
    if duration is not None:
        cfg.duration, cfg.time_unit = int(duration), "ns"
    return types.SimpleNamespace(books=books, timestamp=NOW, config=cfg)


def _run(agent, books, duration=None):
    resp = t63._Resp()
    agent._v63_pass(resp, _state(books, duration), {})
    return resp


# ---- 1. the module --------------------------------------------------------------------------------------------------

def test_the_horizon_is_the_simulations_end_never_longer_than_the_assessment_period():
    cfg = types.SimpleNamespace(duration=86_400 * S, time_unit="ns")
    assert vp.duration_ns(cfg) == 86_400 * S
    assert vp.duration_ns(types.SimpleNamespace(duration=86_400, time_unit="s")) == 86_400 * S
    assert vp.duration_ns(None) is None and vp.duration_ns(types.SimpleNamespace()) is None
    assert vp.horizon_ns(34_000 * S, 86_400 * S) == 52_400 * S
    assert vp.horizon_ns(34_000 * S, None) == vp.ASSESSMENT_NS                   # unknown length: one period
    assert vp.horizon_ns(34_000 * S, 3 * 86_400 * S) == vp.ASSESSMENT_NS         # a longer simulation rolls volume off
    assert vp.horizon_ns(90_000 * S, 86_400 * S) == vp.MIN_HORIZON_NS
    assert vp.PACE_WINDOW_NS == 600 * S and vp.ASSESSMENT_NS == 86_400 * S


def test_the_paced_rate_spends_what_is_left_evenly():
    assert abs(vp.paced_rate(CAP, 293_000.0, 52_400 * S) - 207_000.0 / 52_400) < 1e-9
    assert vp.paced_rate(CAP, CAP, 52_400 * S) == 0.0 and vp.paced_rate(CAP, 600_000.0, 52_400 * S) == 0.0


def test_a_side_adds_unless_it_reduces_the_inventory():
    assert vp.adds(vp.SIDE_BUY, 0.0) and vp.adds(vp.SIDE_SELL, 0.0)
    assert vp.adds(vp.SIDE_BUY, 1.0) and not vp.adds(vp.SIDE_SELL, 1.0)
    assert vp.adds(vp.SIDE_SELL, -1.0) and not vp.adds(vp.SIDE_BUY, -1.0)


def test_the_rate_is_read_over_the_sampling_interval_from_the_reported_volume():
    pace = vp.VolumePace()
    for i in range(0, 901):
        pace.observe(3, i * S, 10.0 * i)
    assert abs(pace.rate(3) - 10.0) < 1e-9
    q = pace.books[3]
    assert q[-1][0] - q[0][0] <= 600 * S + S and q[1][0] > 300 * S           # one anchor at most one step past the window
    fresh = vp.VolumePace()
    fresh.observe(3, 0, 500.0); fresh.observe(3, 30 * S, 800.0)
    assert fresh.rate(3) is None                                            # under a minute of samples
    fresh.observe(3, 70 * S, 100.0)                                         # the venue's number fell: no negative rate
    assert fresh.rate(3) == 0.0


def test_a_book_is_paced_only_while_it_spends_faster_than_its_share():
    pace = vp.VolumePace()
    pace.observe(3, (34_000 - 600) * S, 293_000.0 - 6_000.0)                # 10 quote per sim-s
    pace.observe(3, 34_000 * S, 293_000.0)
    assert pace.paced(3, 34_000 * S, 293_000.0, CAP, 86_400 * S)            # 10 > 207,000 / 52,400 = 3.95
    assert 3 in pace.paced_now and pace.snapshot()["paced_now"] == 1
    slow = vp.VolumePace()
    slow.observe(3, (34_000 - 600) * S, 293_000.0 - 1_800.0)                # 3 quote per sim-s
    slow.observe(3, 34_000 * S, 293_000.0)
    assert not slow.paced(3, 34_000 * S, 293_000.0, CAP, 86_400 * S)
    assert not pace.paced(3, 34_000 * S, 293_000.0, 0.0, 86_400 * S)       # no cap, no pacing
    assert not vp.VolumePace().paced(3, 34_000 * S, 293_000.0, CAP, 86_400 * S)   # no rate yet


def test_a_new_simulation_starts_the_samples_over():
    pace = vp.VolumePace()
    pace.observe(3, 80_000 * S, 400_000.0)
    pace.observe(3, 80_600 * S, 406_000.0)
    pace.observe(4, 700 * S, 0.0)                                            # the clock went back > 1 h
    assert pace.rebases == 1 and set(pace.books) == {4} and pace.rate(4) is None


# ---- 2. the pass ----------------------------------------------------------------------------------------------------

USED = {3: 300_000.0}


def test_a_deep_book_ahead_of_its_pace_places_no_adding_order_and_cancels_the_ones_resting():
    agent = _agent(d633._deep_layer(), used=USED, rate={3: 10.0})
    t63.t14._rest(agent, 21, 3, 0, 99.80, cid=40031)
    resp = _run(agent, {3: t63._book()})
    assert (3, 21) in t63._cancelled(resp) and agent._v641_counts.get("cancel_v641_paced") == 1
    assert not d633._deep_placed(resp)                                       # flat: both sides would add
    assert agent._v63_last["volume_paced"] == 1 and agent._v641_counts.get("paced_book_states") == 1


def test_a_paced_long_book_still_rests_its_reducing_side():
    agent = _agent(d633._deep_layer(), used=USED, rate={3: 10.0}, venue={3: 1.0})
    resp = _run(agent, {3: t63._book()})
    assert {(p[1], p[2]) for p in d633._deep_placed(resp)} == {("SELL", 40032)}


def test_a_book_within_its_pace_trades_as_v6_4():
    agent = _agent(d633._deep_layer(), used=USED, rate={3: 1.0})
    resp = _run(agent, {3: t63._book()})
    assert {(p[1], p[2]) for p in d633._deep_placed(resp)} == {("BUY", 40031), ("SELL", 40032)}
    assert agent._v63_last["volume_paced"] == 0


def test_the_time_left_sets_the_share():
    # 4 quote per sim-s: over one assessment period the share is 200,000 / 86,400 = 2.3 (paced); with the simulation
    # ending at 86,400 s it is 200,000 / 37,589 = 5.3 (not paced)
    agent = _agent(d633._deep_layer(), used=USED, rate={3: 4.0})
    assert agent._v641_pace is not None
    _run(agent, {3: t63._book()})
    assert agent._v63_last["volume_paced"] == 1
    agent = _agent(d633._deep_layer(), used=USED, rate={3: 4.0})
    _run(agent, {3: t63._book()}, duration=86_400 * S)
    assert agent._v63_last["volume_paced"] == 0


def test_a_paced_v632_book_only_works_its_inventory_down():
    agent = _agent(d633._deep_layer(alpha=-10.0), used=USED, rate={3: 10.0})          # board shut: v6.3.2
    resp = _run(agent, {3: t63._book()})
    assert t63._placed(resp) == set()                                                    # flat: no making layer
    agent = _agent(d633._deep_layer(alpha=-10.0), used=USED, rate={3: 10.0}, venue={3: 1.0})
    resp = _run(agent, {3: t63._book()})
    assert {p[1] for p in t63._placed(resp)} == {"SELL"}
    agent = _agent(d633._deep_layer(alpha=-10.0), used=USED, rate={3: 1.0})
    resp = _run(agent, {3: t63._book()})
    assert t63._placed(resp) == {(3, "BUY", 65031, 100.01, 1.0), (3, "SELL", 65032, 100.02, 1.0)}


def test_without_the_switch_nothing_is_paced():
    agent = _agent(d633._deep_layer(), used=USED, rate={3: 10.0}, pace=False)
    resp = _run(agent, {3: t63._book()})
    assert {(p[1], p[2]) for p in d633._deep_placed(resp)} == {("BUY", 40031), ("SELL", 40032)}
    assert agent._v641_counts == {}


def test_the_pass_samples_the_reported_volume_every_state():
    agent = _agent(d633._deep_layer(), used=USED)
    _run(agent, {3: t63._book()})
    assert list(agent._v641_pace.books[3]) == [(NOW, 300_000.0)]


def test_pacing_needs_v63_and_its_own_switch():
    for v63_on, sw, want in ((True, True, True), (True, False, False), (False, True, False)):
        obj = types.SimpleNamespace(research_v641_volume_pace=sw)
        obj._v63_on = lambda v63_on=v63_on: v63_on
        assert _method("_v641_on")(obj) is want


# ---- 3. wiring ------------------------------------------------------------------------------------------------------

def test_the_switch_defaults_on_ships_in_params_and_is_preflighted():
    assert ('self.research_v641_volume_pace = self._as_bool(getattr(self.config, "research_v641_volume_pace", True))'
            in SIMPLE)
    start = LAUNCHER.index('PARAMS="')
    assert "research_v641_volume_pace=1" in LAUNCHER[start:LAUNCHER.index('"\n', start + len('PARAMS="'))]
    assert 'echo "[preflight] v6.4.1 volume pace PASS"' in LAUNCHER
    assert "tests/test_research_v6_4_1_volume_pace.py" in LAUNCHER


def test_the_state_row_reports_the_pace():
    tele = _src("_v62_telemetry")
    assert 'volume_pace_on=int(bool(getattr(self, "research_v641_volume_pace", False)) and self._v641_on()),' in tele
    snap = _method("_v641_snapshot")(types.SimpleNamespace(_v641_counts={"paced_book_states": 2}, _v641_errors=0,
                                                           _v641_pace=vp.VolumePace()))
    assert snap["paced_book_states"] == 2 and snap["version"] == "volume_pace_v6_4_1" and snap["paced_now"] == 0
