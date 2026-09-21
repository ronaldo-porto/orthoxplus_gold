"""v6.2.9: the reply path -- post-decision telemetry after the reply, and the round trip measured.

The validator delays every instruction by ``10 ms + (e^(5t) - 1)/(e^5 - 1) * 990 ms`` with ``t`` its measured
round trip over a 3 s timeout (reward.py ``compute_delay``) and publishes that round trip as ``call_time``:
1.088 s for UID 82 on testnet, where our orders land a median 43 ms after their state -- the curve's value at
1.07 s.  The mainnet top ten by trading score answer in 0.66-0.93 s.  Our own code is ~0.18 s of the round
trip; the telemetry after the last order decision is part of what the validator waits for.
"""
import asyncio
import re
import textwrap
import time
import types
from pathlib import Path

import pytest

import research_direct_idle_gc as idle
import research_v629_reply_path as rp
from _harness import extractor

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
PROTOCOL = (ROOT / "taos" / "im" / "protocol" / "__init__.py").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")

TELEMETRY = ["_v500_service", "_v501_service", "_v502_service", "_v503_service", "_v504_telemetry",
             "_v600_telemetry", "_v601_telemetry", "_v603_telemetry", "_v61_telemetry", "_v61_gap_telemetry",
             "_v611_telemetry", "_v62_telemetry"]


class FakeTimer:
    def __init__(self, delay, callback):
        self.delay, self.callback, self.cancelled = delay, callback, False

    def cancel(self):
        self.cancelled = True


class FakeLoop:
    def __init__(self):
        self.timers = []

    def call_later(self, delay, callback):
        timer = FakeTimer(delay, callback)
        self.timers.append(timer)
        return timer

    def run_due(self):
        due, self.timers = self.timers, []
        for timer in due:
            if not timer.cancelled:
                timer.callback()


def _state(nonce=None, books="books-object"):
    return types.SimpleNamespace(books=books, accounts={"acct": 1}, notices={"n": []}, config="cfg",
                                 timestamp=123, dendrite=types.SimpleNamespace(nonce=nonce))


def _clear_inputs(state):
    state.books = state.accounts = state.notices = state.config = None


# ---- the frozen view ------------------------------------------------------------------------------

def test_the_view_keeps_what_clear_inputs_removes_and_reads_the_rest_through():
    state = _state()
    view = rp.FrozenStateView(state)
    _clear_inputs(state)
    assert (view.books, view.accounts, view.notices, view.config) == ("books-object", {"acct": 1}, {"n": []}, "cfg")
    assert view.timestamp == 123
    state.timestamp = 124
    assert view.timestamp == 124


def test_the_view_writes_through_to_the_state():
    state = _state()
    view = rp.FrozenStateView(state)
    view.extra = 5
    assert state.extra == 5


def test_the_frozen_fields_are_exactly_what_clear_inputs_clears():
    body = PROTOCOL[PROTOCOL.index("def clear_inputs(self):"):]
    body = body[:body.index("return self")]
    assert sorted(re.findall(r"self\.(\w+) = None", body)) == sorted(rp.FROZEN_FIELDS)


def test_the_miner_clears_inputs_after_the_handler_returns():
    miner = (ROOT / "taos" / "im" / "neurons" / "miner.py").read_text()
    fwd = miner[miner.index("async def forward(\n"):]
    fwd = fwd[:fwd.index("def blacklist_forward(")]
    assert fwd.index("self.agent.handle(synapse)") < fwd.index("synapse.clear_inputs().compress()")


# ---- the send stamp -------------------------------------------------------------------------------

def test_recv_lag_is_entry_minus_the_validators_send_stamp():
    assert rp.recv_lag_ms(_state(nonce=1_000_000_000), 1_250_000_000) == pytest.approx(250.0)


def test_no_stamp_or_a_nonsense_stamp_is_not_a_lag():
    assert rp.recv_lag_ms(types.SimpleNamespace(), 1) is None
    assert rp.recv_lag_ms(_state(nonce=None), 1) is None
    assert rp.recv_lag_ms(_state(nonce="x"), 1) is None
    far = int((rp.MAX_PLAUSIBLE_LAG_MS + 1000.0) * 1e6)
    assert rp.recv_lag_ms(_state(nonce=0), far) is None


# ---- the queue ------------------------------------------------------------------------------------

def test_the_queue_runs_after_the_delay_in_order():
    loop, work, seen = FakeLoop(), rp.DeferredWork(delay_s=0.3), []
    work.add("a", lambda: seen.append("a"))
    work.add("b", lambda: seen.append("b"))
    assert work.schedule(loop) is True and seen == [] and loop.timers[0].delay == pytest.approx(0.3)
    loop.run_due()
    assert seen == ["a", "b"] and work.passes == 1 and work.pending == 0
    assert set(work.last_laps) == {"a", "b"}


def test_the_default_delay_follows_the_reply_and_precedes_the_idle_collection():
    assert 0.0 < rp.DEFER_DELAY_MS < idle.A1992_IDLE_DELAY_MS


def test_with_no_loop_the_queue_runs_inline_exactly_as_before():
    work, seen = rp.DeferredWork(), []
    work.add("a", lambda: seen.append(1))
    assert work.schedule(None) is False and seen == [1] and work.inline_runs == 1


def test_a_request_that_arrives_first_runs_the_queue_before_anything_else():
    loop, work, seen = FakeLoop(), rp.DeferredWork(), []
    work.add("a", lambda: seen.append(1))
    work.schedule(loop)
    assert work.flush() is True and seen == [1] and loop.timers[0].cancelled and work.early_flushes == 1
    loop.run_due()
    assert seen == [1]


def test_an_empty_flush_is_a_no_op():
    work = rp.DeferredWork()
    assert work.flush() is False and work.early_flushes == 0 and work.passes == 0


def test_a_failing_task_is_contained_and_the_rest_still_run():
    work, seen = rp.DeferredWork(), []

    def boom():
        raise RuntimeError("telemetry fault")
    work.add("boom", boom)
    work.add("ok", lambda: seen.append(1))
    work.run()
    assert work.errors == 1 and seen == [1]


def test_laps_book_each_service_inside_a_task():
    ticks = iter([0.0, 0.0, 0.010, 0.030, 0.031, 0.040])
    work = rp.DeferredWork(clock=lambda: next(ticks))
    work.add("telemetry", lambda: (work.lap("v500"), work.lap("v503")))
    assert work.run() == pytest.approx(40.0)
    assert work.last_laps == pytest.approx({"v500": 10.0, "v503": 20.0, "telemetry": 31.0})


def test_on_a_real_event_loop_the_queue_waits_for_the_handler():
    work, seen = rp.DeferredWork(delay_s=0.0), []

    async def forward():
        work.add("t", lambda: seen.append("deferred"))
        work.schedule(asyncio.get_running_loop())
        seen.append("handler returned")
        await asyncio.sleep(0.01)
    asyncio.run(forward())
    assert seen == ["handler returned", "deferred"]


def test_timing_snapshot():
    t = rp.ReplyTiming()
    t.note(lag_ms=100.0, reply_ms=150.0)
    t.note(lag_ms=None, reply_ms=50.0)
    s = t.snapshot()
    assert s["requests"] == 2 and s["no_send_stamp"] == 1
    assert s["recv_lag_ms_p50"] == 100.0 and s["reply_ms_max"] == 150.0


# ---- runtime: Simple.handle, executed from source -------------------------------------------------

class _Parent:
    def __init__(self, work, *, defer=True, timing=True):
        self._a1992_idle_gc = None
        self._v629_work = work
        self._v629_timing = rp.ReplyTiming()
        self._v629_request_work = None
        self.research_v629_defer_telemetry = defer
        self.research_v629_reply_timing = timing
        self._tick = 0
        self.rows = []
        self.events = []

    def _emit(self, event_type, force=False, **payload):
        self.rows.append((event_type, payload))

    def handle(self, state):
        """Stands for update() + respond(): respond queues its telemetry when handle() gave it a queue."""
        self._tick += 1
        tick, work = self._tick, self._v629_request_work
        self.events.append(("respond", tick, work is not None))
        if work is not None:
            work.add("post_reply_telemetry", lambda: self.events.append(("telemetry", tick)))
        else:
            self.events.append(("telemetry", tick))
        return f"response-{tick}"


def _harness(work, **kwargs):
    body = "".join(textwrap.indent(textwrap.dedent(_simple(name)), "    ") + "\n"
                   for name in ("handle", "_a1992_note_request", "_v629_begin_request", "_v629_end_request",
                                "_v629_defer_on", "_v629_timing_on", "_v629_emit_timing"))
    namespace = {"_Parent": _Parent, "running_loop": idle.running_loop, "time": time,
                 "A1992_IDLE_GC_VERSION": idle.A1992_IDLE_GC_VERSION, "v629_recv_lag_ms": rp.recv_lag_ms,
                 "V629_TIMING_EVERY_TICKS": rp.TIMING_EVERY_TICKS, "V629_REPLY_PATH_VERSION": rp.V629_REPLY_PATH_VERSION}
    exec("from __future__ import annotations\nclass Harness(_Parent):\n" + body, namespace)
    return namespace["Harness"](work, **kwargs)


def test_on_a_loop_the_telemetry_runs_after_the_reply():
    work = rp.DeferredWork(delay_s=0.0)
    agent = _harness(work)

    async def forward(state):
        response = agent.handle(state)
        agent.events.append(("replied", agent._tick))
        await asyncio.sleep(0.01)
        return response
    assert asyncio.run(forward(_state(nonce=time.time_ns()))) == "response-1"
    assert agent.events == [("respond", 1, True), ("replied", 1), ("telemetry", 1)]
    (row,) = [p for kind, p in agent.rows if kind == "V629_REPLY_TIMING"]
    assert row["timing"]["requests"] == 1 and row["timing"]["no_send_stamp"] == 0
    assert row["defer_telemetry_on"] == 1 and row["v629_reply_path_version"] == rp.V629_REPLY_PATH_VERSION


def test_a_request_that_arrives_first_sees_the_last_ones_telemetry_done():
    work = rp.DeferredWork(delay_s=60.0)
    agent = _harness(work)

    async def back_to_back():
        agent.handle(_state(nonce=time.time_ns()))
        agent.handle(_state(nonce=time.time_ns()))
    asyncio.run(back_to_back())
    assert agent.events[:3] == [("respond", 1, True), ("telemetry", 1), ("respond", 2, True)]
    assert work.early_flushes == 1


def test_without_a_loop_everything_runs_inline_as_in_v6_2_8():
    agent = _harness(rp.DeferredWork())
    assert agent.handle(_state()) == "response-1"
    assert agent.events == [("respond", 1, False), ("telemetry", 1)]
    (row,) = [p for kind, p in agent.rows if kind == "V629_REPLY_TIMING"]
    assert row["timing"]["no_send_stamp"] == 1


def test_the_defer_switch_off_is_v6_2_8_even_on_a_loop():
    agent = _harness(rp.DeferredWork(delay_s=0.0), defer=False)

    async def forward():
        agent.handle(_state())
        agent.events.append(("replied", 1))
    asyncio.run(forward())
    assert agent.events == [("respond", 1, False), ("telemetry", 1), ("replied", 1)]


def test_the_timing_switch_off_writes_no_rows():
    agent = _harness(rp.DeferredWork(), timing=False)
    agent.handle(_state())
    assert agent.rows == []


def test_a_failing_request_still_closes_the_request_window():
    class Boom(Exception):
        pass
    agent = _harness(rp.DeferredWork())

    def fail(self, state):
        raise Boom()
    original = _Parent.handle
    _Parent.handle = fail
    try:
        with pytest.raises(Boom):
            agent.handle(_state())
    finally:
        _Parent.handle = original
    assert agent._v629_request_work is None and agent._v629_timing.requests == 1


# ---- runtime: respond()'s telemetry block, executed from source -----------------------------------

def _telemetry_block():
    respond = _simple("respond")
    lines = respond.splitlines(keepends=True)
    a = next(i for i, ln in enumerate(lines) if "v629_work = getattr(self, \"_v629_request_work\", None)" in ln)
    b = next(i for i, ln in enumerate(lines) if ln.strip() == "_v629_post_reply_telemetry(state)")
    block = textwrap.dedent("".join(lines[a:b + 1]))
    namespace = {"V629FrozenStateView": rp.FrozenStateView}
    exec("def run_block(self, state):\n" + textwrap.indent(block, "    "), namespace)
    return namespace["run_block"]


class _TelemetryAgent:
    def __init__(self, work=None):
        self._v629_request_work = work
        self.calls = []


for _name in TELEMETRY:
    setattr(_TelemetryAgent, _name,
            lambda self, state, _n=_name: self.calls.append((_n, getattr(state, "books", None))))


def test_inline_the_block_calls_every_service_in_order_on_the_state():
    agent, state = _TelemetryAgent(), _state()
    _telemetry_block()(agent, state)
    assert agent.calls == [(n, "books-object") for n in TELEMETRY]


def test_queued_the_block_runs_later_on_a_view_that_survives_clear_inputs():
    work = rp.DeferredWork()
    agent, state = _TelemetryAgent(work), _state()
    _telemetry_block()(agent, state)
    assert agent.calls == [] and work.pending == 1
    _clear_inputs(state)
    work.run()
    assert agent.calls == [(n, "books-object") for n in TELEMETRY]
    assert set(work.last_laps) == {n.lstrip("_") for n in TELEMETRY} | {"post_reply_telemetry"}


def test_the_block_holds_every_telemetry_call_in_the_original_order_after_every_decision():
    respond = _simple("respond")
    start = respond.index("def _v629_post_reply_telemetry(state):")
    dispatch = respond.index("if v629_work is not None:", start)
    idx = [respond.index(f"self.{n}(state)") for n in TELEMETRY]
    assert start < idx[0] and idx == sorted(idx) and idx[-1] < dispatch
    assert respond.index("response = super().respond(state) if quiet is None else quiet") < start
    assert respond.index("self._a199_note_exit_stalls(response)") < start
    assert dispatch < respond.index('"DIRECT_SLOW_REQUEST"')
    assert respond.count("v629_lap(") == len(TELEMETRY)


def test_handle_runs_the_last_queue_first_and_schedules_this_one_last():
    handle = _simple("handle")
    begin = handle.index("v629_loop = self._v629_begin_request()")
    assert handle.index("v629_entry_ns = time.time_ns()") < begin < handle.index("collector = getattr(self, \"_a1992_idle_gc\", None)")
    finally_at = handle.index("finally:")
    end = handle.index("self._v629_end_request(state, entry_ns=v629_entry_ns, entry_t=v629_entry_t, loop=v629_loop)")
    assert finally_at < handle.index("collector.end_request(loop)") < end
    begin_src = _simple("_v629_begin_request")
    assert begin_src.index("work.flush()") < begin_src.index("loop = running_loop()")


# ---- switches, launcher, version ------------------------------------------------------------------

def test_both_switches_default_on_and_are_declared():
    for key in ("research_v629_defer_telemetry", "research_v629_reply_timing"):
        assert f'getattr(self.config, "{key}", True)' in SIMPLE
        assert f"{key}=1" in LAUNCHER


def test_the_preflight_guards_the_build():
    for needle in ("v6.2.9 module is not imported",
                   "v6.2.9 handle() does not run the previous request's deferred telemetry first",
                   "v6.2.9 handle() does not schedule the deferred telemetry after the reply",
                   "v6.2.9 respond() does not queue its telemetry on a frozen view",
                   "[preflight] v6.2.9 reply path PASS"):
        assert needle in LAUNCHER
    assert "tests/test_research_v6_2_9_reply_path.py" in LAUNCHER


def test_the_version_and_arm():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_10"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_10"' in SIMPLE
    assert "strategy1_direct_v6_2_9)" in LAUNCHER and "V628_BUILD=1; V629_BUILD=1 ;;" in LAUNCHER
    assert rp.V629_REPLY_PATH_VERSION == "reply_path_v6_2_9"
