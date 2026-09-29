"""v6.8: the deep layer owns the book -- S1 no touch fallback, S2 a gate per book, S3 the layer's record across a restart,
S4 nothing that takes, S5 a budget line on the validator's rolling volume window.

Mainnet 09-29 (sim 20260924_1653), the validator's capture arithmetic on the recorded prints: touch fills +0.0..+2.7 bps
in every sim-hour, 20+ ticks deep +9..+15; our v6.3.2 fallback traded 1.4-2.9M quote an hour per agent at +0.0..+0.1
whenever the pooled board was shut, and a restart shut it; the 500k/book window rolls across simulations.
"""
import ast
import json
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v633_deep_layer as dl  # noqa: E402
import research_v641_pace as v641  # noqa: E402
import research_v66_pace_line as pl  # noqa: E402
import research_v68_deep_owns as d68  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_3_3_deep_layer as d633  # noqa: E402
import test_research_v6_4_board as t64  # noqa: E402
import test_research_v6_5_deep_clips as t65  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
RESEARCH = (STRATEGY / "Strategy1_Research.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
TICK = 0.01
S = 1_000_000_000
CAP = 500_000.0
EXTRA = {"v68_per_book_gate": d68.per_book_gate, "v68_restore_deep": d68.restore_deep, "v68_no_take": d68.no_take,
         "V68_DEEP_OWNS_VERSION": d68.V68_DEEP_OWNS_VERSION, "V68RollingPaceLine": d68.RollingPaceLine,
         "V66PaceLine": pl.PaceLine, "Any": object}
SWITCHES = ("research_v68_no_touch_fallback", "research_v68_book_gate", "research_v68_deep_persist",
            "research_v68_post_only", "research_v68_rolling_budget")


def _method(name):
    scope = dict(EXTRA)
    exec(compile(ast.Module(body=[ast.parse(_src(name)).body[0]], type_ignores=[]), f"<{name}>", "exec"), scope)
    return scope[name]


def _bind(obj, *names):
    for n in names:
        setattr(obj, n, types.MethodType(_method(n), obj))
    return obj


def _counting(**attrs):
    o = types.SimpleNamespace(_v68_counts={}, _v68_errors=0, _tick=7, events=[], **attrs)
    _bind(o, "_v68_count")
    o._emit = lambda *a, **k: o.events.append((a, k))
    return o


def _record(layer, book, *, sweeps=(12.0, 14.0, 30.5), inv=2.0, p_last=100.02, fills=3,
            buckets=((600 * S, [1.5, 2.0, 4.0, 0.03]),)):
    db = dl.DeepBook()
    db.sweeps.extend(sweeps)
    db.paper.inv, db.paper.p_last, db.paper.fills = inv, p_last, fills
    db.paper.bid, db.paper.ask = 99.80, 100.25
    db.prev_touch = (100.00, 100.03)
    for k, v in buckets:
        db.paper.buckets[k] = list(v)
    layer.books[book] = db
    return db


# ---- 1. the rules -----------------------------------------------------------------------------------------------------

def test_s1_the_touch_is_no_fallback_only_with_the_switch_and_a_deep_layer():
    assert d68.touch_fallback_retired(True, True) is True
    assert d68.touch_fallback_retired(False, True) is False                       # v6.7: the v6.3.2 fallback
    assert d68.touch_fallback_retired(True, False) is False                       # no layer: the fallback stays


def test_s2_a_book_opens_on_its_own_record_while_the_pooled_board_is_shut():
    layer = d633._deep_layer(books=(3, 5), alpha=-5.0)
    layer.books[5].paper.buckets[dl.sampled_key(t63.NOW)] = [-20.0, 0.0, 1.0, 0.0]
    assert layer.update_board() is False and layer.per_book_gate is False          # mean -12.5: the pool is shut
    assert not layer.book_open(3, 18.0, 0.0) and not layer.book_open(5, 18.0, 0.0)
    layer.per_book_gate = True
    assert layer.book_open(3, 18.0, 0.0)                                           # -5 >= -9: its own record opens it
    assert not layer.book_open(5, 18.0, 0.0)                                       # -20 < -9: v6.3.3's per-book floor
    assert not layer.book_open(7, 18.0, 0.0)                                       # no record, no depth
    assert dl.DeepLayer().per_book_gate is False and dl.DeepLayer(per_book_gate=True).snapshot()["per_book_gate"] == 1
    assert d68.per_book_gate(True) is True and d68.per_book_gate(0) is False


def test_s3_the_record_round_trips_through_the_session_file():
    layer = dl.DeepLayer(clip=2.0, max_clips=2.0, sweep_quantile=0.97)
    _record(layer, 3)
    _record(layer, 9, sweeps=[float(x) for x in range(1, 41)], inv=-4.0, p_last=None, fills=0, buckets=())
    layer.last_prune_ts = 1234 * S
    payload = json.loads(json.dumps(d68.deep_state(layer)))                       # what the session file holds
    fresh = dl.DeepLayer(clip=2.0, max_clips=2.0, sweep_quantile=0.97)
    assert d68.restore_deep(fresh, payload) == 2 and fresh.last_prune_ts == 1234 * S
    for b in (3, 9):
        a, r = layer.books[b], fresh.books[b]
        assert list(r.sweeps) == list(a.sweeps) and r.sweeps.maxlen == dl.SWEEP_KEEP
        assert (r.paper.inv, r.paper.p_last, r.paper.fills) == (a.paper.inv, a.paper.p_last, a.paper.fills)
        assert r.paper.buckets == a.paper.buckets and r.paper.alpha() == a.paper.alpha()
        assert fresh.depth(b) == layer.depth(b)
        assert (r.paper.bid, r.paper.ask, r.prev_touch) == (None, None, None)     # re-placed / re-read next state
    assert fresh.depth(9) == 39.0                                                  # p97 of 1..40, known at once


def test_s3_a_restore_never_overwrites_a_live_layer_and_drops_what_it_cannot_read():
    live = dl.DeepLayer()
    _record(live, 3)
    assert d68.restore_deep(live, d68.deep_state(live)) == 0                     # a layer that has seen a state
    empty = dl.DeepLayer()
    for bad in (None, [], {"books": []}, {"books": {"x": {}}}, {"books": {"4": "row"}}):
        assert d68.restore_deep(empty, bad) == 0 and empty.books == {}
    n = d68.restore_deep(empty, {"books": {"4": {"sweeps": [0.5, "a", 3.0, float("nan")], "inv": "?", "fills": "x",
                                                  "buckets": {"1": [1, 2, 3], "2": [1, 2, 3, 4], "k": [1, 2, 3, 4]}}},
                                 "last_prune_ts": "never"})
    assert n == 1 and list(empty.books[4].sweeps) == [3.0] and empty.books[4].paper.inv == 0.0
    assert empty.books[4].paper.fills == 0 and empty.books[4].paper.buckets == {2: [1.0, 2.0, 3.0, 4.0]}
    assert empty.last_prune_ts is None


def _lim(post=False, tif=1, cid=80031):
    return types.SimpleNamespace(type="PLACE_ORDER_LIMIT", postOnly=post, timeInForce=tif, clientOrderId=cid)


def _set(obj, name, value):
    setattr(obj, name, value)
    return True


def test_s4_every_limit_leaves_post_only_and_nothing_that_can_only_take_leaves():
    exit_ = _lim()
    deep = _lim(post=True, cid=40031)
    cancel = types.SimpleNamespace(type="CANCEL_ORDERS")
    market = types.SimpleNamespace(type="PLACE_ORDER_MARKET")
    close = types.SimpleNamespace(type="CLOSE_POSITIONS")
    ioc, fok, named = _lim(tif=2), _lim(tif=3), _lim(tif=types.SimpleNamespace(name="IOC"))
    kept, converted, dropped = d68.no_take([exit_, deep, cancel, market, close, ioc, fok, named], _set)
    assert kept == [exit_, deep, cancel] and (converted, dropped) == (1, 5)
    assert exit_.postOnly is True and deep.postOnly is True
    as_dict = {"type": "PLACE_ORDER_LIMIT", "postOnly": "false", "timeInForce": 0}
    assert d68.no_take([as_dict], lambda o, n, v: o.__setitem__(n, v) or True) == ([as_dict], 1, 0)
    assert as_dict["postOnly"] is True
    stuck = _lim()
    assert d68.no_take([stuck], lambda o, n, v: False) == ([], 0, 1)             # cannot be made post-only: not sent


def test_s5_the_line_rises_at_the_cap_per_assessment_period_from_the_first_report():
    w = v641.ASSESSMENT_NS
    assert w == 86_400 * S and v641.PACE_WINDOW_NS == 600 * S
    assert d68.rolling_line(CAP, 0.0, 0, 0) == CAP * 600 / 86_400                  # one sampling window early
    assert abs(d68.rolling_line(CAP, 347_000.0, 0, 11_550 * S) - (347_000 + CAP * 12_150 / 86_400)) < 1e-6
    assert d68.rolling_line(CAP, 347_000.0, 0, 10 * w) == CAP                      # never above the cap
    assert d68.rolling_line(CAP, 600_000.0, 0, S) == CAP                           # over the cap: the cap
    assert d68.rolling_line(CAP, 100.0, 50 * S, 0) >= 100.0                        # a clock behind: not below u0
    # UID 104 on 09-29: 347k/book with 11,550 sim-s of the simulation left
    v66 = pl.PaceLine()
    v66.observe(3, 74_850 * S, 347_000.0)
    rolling = d68.RollingPaceLine()
    rolling.observe(3, 74_850 * S, 347_000.0)
    end = 86_400 * S
    assert v66.line(3, end - 600 * S, CAP, end) == CAP                             # v6.6: capped at the seam
    assert rolling.line(3, end - 600 * S, CAP, end) < 0.84 * CAP                   # rolling: 417k, room carried over


def test_s5_the_line_carries_across_a_new_simulation_and_ignores_volume_ageing_out():
    line = d68.RollingPaceLine()
    line.observe(3, 70_000 * S, 300_000.0)
    line.observe(3, 86_000 * S, 400_000.0)
    before = line.line(3, 86_000 * S, CAP)
    line.observe(3, 50 * S, 390_000.0)                                             # the next simulation's first state
    assert line.rebases == 1 and line.restarts == 0
    assert abs(line.line(3, 50 * S, CAP) - before) < 1e-6                         # the same place on the line
    assert line.start[3] == ((70_000 - 86_000 + 50) * S, 300_000.0)
    assert not line.paced(3, 50 * S, 390_000.0, CAP, None)
    assert line.paced(3, 50 * S, before + 1.0, CAP, None)
    line.observe(3, 60 * S, 250_000.0)                                             # old volume freed: room, no restart
    assert line.start[3] == ((70_000 - 86_000 + 50) * S, 300_000.0) and line.restarts == 0
    v66 = pl.PaceLine()
    v66.observe(3, 70_000 * S, 300_000.0)
    v66.observe(3, 60_000 * S, 250_000.0)
    v66.observe(3, 60_001 * S, 240_000.0)
    assert v66.restarts == 1                                                       # v6.6 read the same as a new budget
    assert line.snapshot()["rolling"] == 1 and line.snapshot()["version"] == pl.V66_PACE_LINE_VERSION


# ---- 2. the agent -----------------------------------------------------------------------------------------------------

def _shut_board_pass(*, fallback=False, book_gate=False):
    layer = d633._deep_layer(books=(3, 5), alpha=-5.0)
    layer.books[5].paper.buckets[dl.sampled_key(t63.NOW)] = [-20.0, 0.0, 1.0, 0.0]
    layer.per_book_gate = book_gate
    agent = t64._agent(layer)
    agent.research_v68_no_touch_fallback = fallback
    agent._v68_counts, agent._v68_errors = {}, 0
    return agent, t64._run(agent, {3: t63._book(), 5: t63._book()})


def _deep_sides(book):
    return {(book, "BUY", 40001 + 10 * book, dl.deep_price(100.015, 21.5, dl.SIDE_BUY, bid=100.00, ask=100.03,
                                                         tick=TICK, decimals=2), 1.0),
            (book, "SELL", 40002 + 10 * book, dl.deep_price(100.015, 21.5, dl.SIDE_SELL, bid=100.00, ask=100.03,
                                                          tick=TICK, decimals=2), 1.0)}


def _touch_sides(book):
    return {(book, "BUY", 65001 + 10 * book, 100.01, 1.0), (book, "SELL", 65002 + 10 * book, 100.02, 1.0)}


def test_the_shut_board_book_quotes_nothing_with_s1_and_the_v632_touch_without():
    agent, resp = _shut_board_pass()                                               # v6.7: both books at the touch
    assert t63._placed(resp) == _touch_sides(3) | _touch_sides(5)
    agent, resp = _shut_board_pass(fallback=True)                                  # S1: nothing at all
    assert t63._placed(resp) == set() and agent._v68_counts == {"idle_book_states": 2}
    assert agent._v63_last["board_idle"] == 2 and agent._v63_last["board_owns"] == 0


def test_s2_trades_the_book_its_record_opens_and_s1_keeps_the_other_off_the_touch():
    agent, resp = _shut_board_pass(book_gate=True)                                 # S2 alone: 3 deep, 5 at the touch
    assert t63._placed(resp) == _deep_sides(3) | _touch_sides(5)
    agent, resp = _shut_board_pass(fallback=True, book_gate=True)                  # v6.8: 3 deep, 5 quiet
    assert t63._placed(resp) == _deep_sides(3) and agent._v68_counts == {"idle_book_states": 1}


def test_s1_cancels_a_resting_touch_order_on_a_book_it_idles():
    agent, _ = _shut_board_pass(fallback=True)
    t63.t14._rest(agent, 11, 5, 0, 100.01, cid=65051)
    resp = t64._run(agent, {3: t63._book(), 5: t63._book()})
    assert t63._cancelled(resp) == {(5, 11)} and t63._placed(resp) == set()


def _deep_ref_obj(book_gate, persist=False, payload=None):
    o = t65._obj(2.0, 2.0)
    o.research_v68_book_gate, o.research_v68_deep_persist = book_gate, persist
    o._v68_deep_restore = payload
    o._v68_counts, o._v68_errors = {}, 0
    _bind(o, "_v68_per_book_gate", "_v68_apply_deep_restore", "_v68_count")
    o._v633_deep_ref = types.MethodType(t65._method("_v633_deep_ref"), o)
    return o


def test_the_agent_builds_its_layer_with_the_per_book_gate_and_restores_its_record_once():
    assert _deep_ref_obj(True)._v633_deep_ref().per_book_gate is True
    assert _deep_ref_obj(False)._v633_deep_ref().per_book_gate is False
    saved = dl.DeepLayer()
    _record(saved, 3)
    saved.last_prune_ts = 99 * S
    payload = json.loads(json.dumps(d68.deep_state(saved)))
    o = _deep_ref_obj(True, persist=True, payload=payload)
    deep = o._v633_deep_ref()
    assert set(deep.books) == {3} and deep.last_prune_ts == 99 * S and o._v68_deep_restore is None
    assert o._v68_counts == {"restored_books": 1}
    assert [a[0] for a, _ in o.events] == ["V68_DEEP_RESTORE"] and o.events[0][1]["books"] == 1
    assert o._v633_deep_ref() is deep and o._v68_counts == {"restored_books": 1}   # once
    off = _deep_ref_obj(True, persist=False, payload=payload)
    assert off._v633_deep_ref().books == {} and off._v68_counts == {}              # the switch off: v6.7


def test_the_session_file_carries_the_record_for_this_simulation():
    save = _src("_research_save_session")
    assert "payload[V68_DEEP_SESSION_KEY] = v68_deep_state(self._v633_deep)" in save
    assert save.index('payload["direct_v61_lots"]') < save.index("payload[V68_DEEP_SESSION_KEY]")
    read = _src("_research_read_session")
    assert "raw.get(V68_DEEP_SESSION_KEY)" in read and "self._v68_apply_deep_restore(self._v633_deep)" in read
    assert d68.DEEP_SESSION_KEY == "deep_layer_v6_8"
    # the file is one per uid and simulation (v5.0.4 H1 + the parent's name), so no record crosses a seam
    assert "return uid_session_path(path, getattr(self, \"uid\", None))" in _src("_research_session_path")


def test_s4_runs_before_the_maker_sanitizer_so_a_converted_exit_is_priced_to_rest():
    o = _counting(research_v68_post_only=True)
    o._research_set_instruction_attr = _set
    _bind(o, "_v68_no_take")
    exit_, market = _lim(), types.SimpleNamespace(type="PLACE_ORDER_MARKET")
    resp = types.SimpleNamespace(instructions=[exit_, market])
    o._v68_no_take(resp)
    assert resp.instructions == [exit_] and exit_.postOnly is True
    assert o._v68_counts == {"made_post_only": 1, "dropped_taking": 1}
    body = SIMPLE[SIMPLE.index("self._v68_no_take(response)") - 400:SIMPLE.index("self._v68_no_take(response)") + 700]
    assert body.index("self._v68_no_take(response)") < body.index("self._research_sanitize_maker_instructions(response, state)")
    assert body.index("self._research_sanitize_maker_instructions(response, state)") < body.index(
        "self._research_final_validate_instructions(response, state)")
    assert 'flag = self._get(instruction, "postOnly", "post_only")' in RESEARCH       # the sanitizer's maker test


def test_s5_the_agent_paces_on_the_rolling_line_with_the_switch_and_v66s_without():
    for on, cls in ((True, d68.RollingPaceLine), (False, pl.PaceLine)):
        o = types.SimpleNamespace(_v66_pace=None, research_v68_rolling_budget=on)
        _bind(o, "_v66_pace_ref")
        assert type(o._v66_pace_ref()) is cls


# ---- 3. wiring --------------------------------------------------------------------------------------------------------

def test_the_switches_default_on_ship_in_params_and_are_preflighted():
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"\n', start + len('PARAMS="'))]
    for key in SWITCHES:
        assert f'self.{key} = self._as_bool(getattr(self.config, "{key}", True))' in SIMPLE, key
        assert f"{key}=1" in params, key
    assert 'echo "[preflight] v6.8 deep owns the book PASS"' in LAUNCHER
    assert "tests/test_research_v6_8.py" in LAUNCHER


def test_the_launcher_parses():
    out = subprocess.run(["bash", "-n", str(ROOT / "run_strategy1_research_simple_multi.sh")], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_the_state_row_reports_v6_8():
    assert ('v68=(self._v68_snapshot() if getattr(self, "research_v68_no_touch_fallback", None) is not None else {}),'
            in _src("_v62_telemetry"))
    o = types.SimpleNamespace(_v68_counts={"idle_book_states": 4}, _v68_errors=0, _v633_deep=dl.DeepLayer(per_book_gate=True),
                              _v66_pace=d68.RollingPaceLine(), **{k: True for k in SWITCHES})
    _bind(o, "_v68_snapshot")
    snap = o._v68_snapshot()
    assert snap == {"idle_book_states": 4, "version": "deep_owns_v6_8", "errors": 0, "no_touch_fallback_on": 1,
                    "book_gate_on": 1, "deep_persist_on": 1, "post_only_on": 1, "rolling_budget_on": 1,
                    "per_book_gate": 1, "rolling_line": 1}
