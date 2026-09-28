"""v6.6.1: the deep bound scaled by the book's own volatility against the median book's.

Mainnet 09-28: book 23 (263 -> 317 -> 262 around 11:00 JST) and book 2 (288 -> 316 at 18:18 JST) spiked; the deep and
vacuum orders re-centred on the spiked mid and the reverting flow filled the adding side to the whole bound.  Book 23
alone took UID 94's trading from 0.915 to 0.776 and UID 104's from 0.827 to 0.644.  Replay through 20:31 JST: UID 94
0.619 -> 0.845 and UID 104 0.601 -> 0.757 with this bound, making -1..-4%.
"""
import ast
import sys
import types
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v633_deep_layer as dl  # noqa: E402
import research_v63_trend_target as tt  # noqa: E402
import research_v661_vol_bound as vb  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_4_board as t64  # noqa: E402
import test_research_v6_6 as t66  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000
NOW = t63.NOW
EXTRA = dict(t66.EXTRA)
EXTRA.update({"V661VolBound": vb.VolBound, "V661_VOL_BOUND_VERSION": vb.V661_VOL_BOUND_VERSION, "v661_room": vb.room})
V661_METHODS = ("_v661_count", "_v661_vol_on", "_v661_vol_ref", "_v661_snapshot")
LM = tt.log_mid_bps(100.00, 100.03)          # t63._book()'s mid, as the pass reads it


def _method(name):
    scope = dict(EXTRA)
    exec(compile(ast.Module(body=[ast.parse(_src(name)).body[0]], type_ignores=[]), f"<{name}>", "exec"), scope)
    return scope[name]


def _with661(fn):
    g = dict(fn.__globals__)
    g.update(EXTRA)
    return types.FunctionType(fn.__code__, g, fn.__name__, fn.__defaults__, fn.__closure__)


def _vol(sums):
    """A VolBound whose books already hold these 600-s sums (one sample each, 100 s old) and sit at the book's mid."""
    v = vb.VolBound()
    for b, s in sums.items():
        v.q[b] = deque([(NOW - 100 * S, float(s))])
        v.sums[b] = float(s)
        v.prev[b] = LM
    return v


def _agent(venue, *, sums=None, on=True):
    agent = t66._agent(venue)
    cls = type(agent)
    methods = {n: _with661(getattr(cls, n)) for n in ("_v63_pass", "_v633_book")}
    for n in V661_METHODS:
        methods[n] = _method(n)
    agent.__class__ = type("P661", (cls,), methods)
    agent.research_v661_vol_bound = on
    agent._v661_vol = _vol(sums) if sums is not None else None
    agent._v661_counts, agent._v661_errors = {}, 0
    return agent


def _sides(resp):
    return {s for s, _p, _q in t66._sides(resp)}


# ---- 1. the rule ------------------------------------------------------------------------------------------------------

def test_a_book_at_or_below_the_median_keeps_the_whole_bound_and_a_faster_one_keeps_median_over_its_own():
    assert vb.bound_scale(10.0, 10.0) == 1.0 and vb.bound_scale(5.0, 10.0) == 1.0
    assert vb.bound_scale(40.0, 10.0) == 0.25 and abs(vb.bound_scale(30.0, 10.0) - 1 / 3) < 1e-12
    for bad in ((None, 10.0), (40.0, None), (40.0, 0.0), (float("nan"), 10.0), ("x", 10.0)):
        assert vb.bound_scale(*bad) == 1.0                                         # unreadable: v6.6's bound


def test_room_is_the_deep_layers_room_at_the_limit_given():
    layer = dl.DeepLayer(clip=2.0, max_clips=2.0)
    for inv in (-4.0, -3.9, 0.0, 3.9, 4.0):
        for side in (vb.SIDE_BUY, vb.SIDE_SELL):
            assert vb.room(side, inv, 4.0) == layer.room(side, inv)
    assert not vb.room(vb.SIDE_BUY, 1.0, 1.0) and vb.room(vb.SIDE_SELL, 1.0, 1.0)  # at the limit: reduce only
    assert not vb.room(vb.SIDE_BUY, None, 4.0) and not vb.room("x", 0.0, 4.0)


def test_the_window_is_the_validators_sampling_interval():
    assert vb.VOL_WINDOW_NS == dl.SAMPLE_NS == 600 * S


def test_volatility_is_the_sum_of_log_mid_moves_over_the_window_and_the_median_is_per_request():
    v = vb.VolBound()
    for i, lm in enumerate((0.0, 10.0, 5.0, 5.0)):                                 # moves 10, 5, 0
        v.observe(3, NOW + i * S, lm)
    assert v.sums[3] == 15.0 and len(v.q[3]) == 3
    v.observe(3, NOW + 601 * S, 105.0)                                             # 600 s after the first move: kept
    assert v.sums[3] == 115.0 and len(v.q[3]) == 4
    v.observe(3, NOW + 700 * S, 105.0)                                             # the first three moves age out
    assert v.sums[3] == 100.0 and len(v.q[3]) == 2
    for b, (a, c) in ((4, (0.0, 20.0)), (5, (0.0, 30.0))):
        v.observe(b, NOW + 699 * S, a)
        v.observe(b, NOW + 700 * S, c)
    assert v.begin_pass() == 30.0                                                  # median of 20 / 30 / 100
    assert v.scale(3) == 0.3 and v.scale(4) == 1.0 and v.scale(5) == 1.0 and v.scale(99) == 1.0
    snap = v.snapshot()
    assert snap["version"] == "vol_bound_v6_6_1" and snap["scaled_now"] == 1 and snap["min_scale"] == 0.3


def test_a_new_simulation_forgets_every_book():
    v = _vol({3: 40.0, 4: 10.0})
    v.maybe_rebase(NOW)
    v.maybe_rebase(NOW - 2 * 3_600 * S)
    assert v.rebases == 1 and v.q == {} and v.begin_pass() == 0.0


# ---- 2. the pass -----------------------------------------------------------------------------------------------------

def test_a_book_four_times_the_median_volatility_only_reduces_past_a_quarter_of_the_bound():
    # clip 2, bound 4 -> 1 base at a quarter: long 1.5 is past it, so no adding buy; the reducing sell still rests
    sums = {3: 40.0, 4: 10.0, 5: 10.0}
    resp = t64._run(_agent({3: 1.5}, sums=sums), {3: t63._book()})
    assert _sides(resp) == {"SELL"}
    agent = _agent({3: 1.5}, sums=sums)
    t64._run(agent, {3: t63._book()})
    assert agent._v661_counts.get("placement_vol_bound") == 1
    assert agent._v661_snapshot()["vol_bound"]["scaled_now"] == 1
    # the same book at the median volatility, and v6.6 without the switch, add as before
    assert _sides(t64._run(_agent({3: 1.5}, sums={3: 10.0, 4: 10.0, 5: 10.0}), {3: t63._book()})) == {"BUY", "SELL"}
    assert _sides(t64._run(_agent({3: 1.5}, sums=sums, on=False), {3: t63._book()})) == {"BUY", "SELL"}


def test_under_the_scaled_bound_both_sides_still_rest():
    resp = t64._run(_agent({3: 0.5}, sums={3: 40.0, 4: 10.0, 5: 10.0}), {3: t63._book()})
    assert _sides(resp) == {"BUY", "SELL"}                                         # 0.5 < 1.0: room on both sides


def test_a_resting_add_past_the_scaled_bound_is_cancelled_and_the_reducing_order_stays():
    agent = _agent({3: 1.5}, sums={3: 40.0, 4: 10.0, 5: 10.0})
    t63.t14._rest(agent, 21, 3, 0, t66.BUY_PX, cid=40031)
    t63.t14._rest(agent, 22, 3, 1, t66.SELL_PX, cid=40032)
    resp = t64._run(agent, {3: t63._book()})
    assert t63._cancelled(resp) == {(3, 21)} and t63._placed(resp) == set()
    assert agent._v661_counts.get("cancel_vol_bound") == 1
    assert agent._v633_counts.get("cancel_deep_no_room") == 1


def test_the_pass_reads_every_books_mid_into_the_record():
    agent = _agent({3: 0.0}, sums={4: 10.0, 5: 10.0})
    t64._run(agent, {3: t63._book()})
    assert 3 in agent._v661_vol.prev and agent._v661_vol.prev[3] == LM


def test_an_agent_from_before_v6_6_1_is_v6_6():
    old = t66._agent({3: 1.5})                                                    # no v6.6.1 attributes at all
    assert _sides(t64._run(old, {3: t63._book()})) == {"BUY", "SELL"}


# ---- 3. wiring -------------------------------------------------------------------------------------------------------

def test_the_switch_defaults_on_ships_in_params_and_is_preflighted():
    assert 'self.research_v661_vol_bound = self._as_bool(getattr(self.config, "research_v661_vol_bound", True))' in SIMPLE
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"\n', start + len('PARAMS="'))]
    assert "research_v661_vol_bound=1" in params
    assert 'echo "[preflight] v6.6.1 volatility-scaled deep bound PASS"' in LAUNCHER
    assert "tests/test_research_v6_6_1.py" in LAUNCHER


def test_the_launcher_parses():
    import subprocess
    out = subprocess.run(["bash", "-n", str(ROOT / "run_strategy1_research_simple_multi.sh")], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_the_median_is_read_before_the_books_and_each_book_before_it_is_quoted():
    src = _src("_v63_pass")
    assert src.index("vol.begin_pass()") < src.index("for raw_id in sorted(books")
    assert src.index("vol.observe(book_id, now_ts, lm)") < src.index("placed_total += self._v633_book(")
    assert "paced=paced, bound_scale=bound_scale," in src


def test_the_state_row_reports_v6_6_1():
    assert ('v661=(self._v661_snapshot() if getattr(self, "research_v661_vol_bound", None) is not None else {}),'
            in _src("_v62_telemetry"))
    snap = _agent({3: 0.0})._v661_snapshot()
    assert snap["vol_bound_on"] == 1 and snap["errors"] == 0 and snap["vol_bound"]["version"] == "vol_bound_v6_6_1"
