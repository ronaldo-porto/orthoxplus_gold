"""A1.9.9.2: CPython's full collection runs in the gap between requests, not inside one.

Measured on the A1.9.9.1 run, log 20260914_141324, ticks 1-8,778.  p95 was 173.3 ms over ticks
8,001-8,500.  A slow tick carries one excess of about the same size in whichever phase it lands --
124 ms in full_predict, 107 ms in the screen, 98 ms in logging -- and it lands by allocation, not
by time.  Removing that one excess puts p95 at 54.9 ms.  Responses were at least 3,853 ms apart.
"""
import ast
import asyncio
import gc
import textwrap
from pathlib import Path

import research_direct_idle_gc as idle
from _harness import extractor

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
MODULE = (STRATEGY / "research_direct_idle_gc.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()


class FakeGC:
    def __init__(self, threshold=(700, 10, 10)):
        self.threshold = tuple(threshold)
        self.set_calls = []
        self.collections = [0, 0, 0]
        self.collect_calls = 0
        self.on_collect = None
        self.fail = False

    def get_threshold(self):
        return self.threshold

    def set_threshold(self, *threshold):
        self.threshold = tuple(threshold)
        self.set_calls.append(tuple(threshold))

    def collect(self, generation=2):
        if self.fail:
            raise RuntimeError("collect")
        self.collect_calls += 1
        if self.on_collect:
            self.on_collect()
        self.collections[2] += 1
        return 7

    def get_stats(self):
        return [{"collections": n} for n in self.collections]

    def get_objects(self):
        return [object()] * 5


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


def _collector(fake=None, **kwargs):
    kwargs.setdefault("supported", True)
    return idle.IdleCollector(gc_module=fake or FakeGC(), **kwargs)


def _request(collector, loop, *, full_passes_inside=0):
    collector.begin_request(loop)
    collector._gc.collections[2] += full_passes_inside
    collector.end_request(loop)


# ---- the collector ------------------------------------------------------------------------------

def test_nothing_is_installed_without_a_running_event_loop():
    fake = FakeGC()
    collector = _collector(fake)
    assert fake.set_calls == [] and not collector.installed
    _request(collector, None)
    assert fake.set_calls == [] and not collector.installed
    assert collector.skip_reason == idle.SKIP_NO_EVENT_LOOP and collector.fallback_reason == ""


def test_only_the_full_pass_trigger_is_raised():
    fake, loop = FakeGC(), FakeLoop()
    collector = _collector(fake)
    collector.begin_request(loop)
    assert fake.set_calls == [(700, 10, idle.A1992_FULL_THRESHOLD_OFF)]
    assert collector.installed and collector.saved_threshold == (700, 10, 10)


def test_the_full_pass_runs_after_the_request_returns():
    fake, loop = FakeGC(), FakeLoop()
    collector = _collector(fake)
    collector.begin_request(loop)
    assert fake.collect_calls == 0 and loop.timers == []
    collector.end_request(loop)
    (timer,) = loop.timers
    assert timer.delay == idle.A1992_IDLE_DELAY_MS / 1000.0 and fake.collect_calls == 0
    seen = []
    fake.on_collect = lambda: seen.append(collector.in_request)
    loop.run_due()
    assert fake.collect_calls == 1 and seen == [False]
    snap = collector.snapshot()
    assert snap["idle_passes"] == 1 and snap["idle_last_unreachable"] == 7
    assert snap["responses_since_idle"] == 0 and snap["tracked_objects"] == 5
    # The idle pass is not charged to the next request.
    _request(collector, loop)
    assert collector.snapshot()["request_gen2"] == 0


def test_a_request_that_arrives_first_cancels_the_pass():
    fake, loop = FakeGC(), FakeLoop()
    collector = _collector(fake)
    _request(collector, loop)
    (first,) = loop.timers
    collector.begin_request(loop)
    assert first.cancelled and collector.idle_cancelled == 1
    collector.end_request(loop)
    loop.run_due()
    assert fake.collect_calls == 1 and collector.responses_since_idle == 0


def test_requests_with_no_idle_gap_bring_automatic_collection_back():
    fake, loop = FakeGC(), FakeLoop()
    collector = _collector(fake)
    for _ in range(idle.A1992_FALLBACK_RESPONSES + 1):
        collector.begin_request(loop)
        collector.end_request(loop)
    assert collector.fallback_reason == idle.FALLBACK_NO_IDLE_GAP and not collector.installed
    assert fake.threshold == (700, 10, 10) and fake.collect_calls == 0
    assert all(timer.cancelled for timer in loop.timers)
    # Terminal for the session: nothing is installed again.
    _request(collector, loop)
    assert fake.set_calls[-1] == (700, 10, 10) and not collector.installed


def test_losing_the_loop_or_a_failing_pass_brings_automatic_collection_back():
    fake, loop = FakeGC(), FakeLoop()
    collector = _collector(fake)
    collector.begin_request(loop)
    collector.end_request(None)
    assert collector.fallback_reason == idle.FALLBACK_NO_EVENT_LOOP and fake.threshold == (700, 10, 10)
    fake, loop = FakeGC(), FakeLoop()
    collector = _collector(fake)
    _request(collector, loop)
    fake.fail = True
    loop.run_due()
    assert collector.fallback_reason == idle.FALLBACK_COLLECT_ERROR and fake.threshold == (700, 10, 10)


def test_a_full_pass_inside_a_request_is_counted():
    fake, loop = FakeGC(), FakeLoop()
    collector = _collector(fake)
    _request(collector, loop, full_passes_inside=1)
    snap = collector.snapshot()
    assert snap["request_gen2"] == 1 and snap["request_full_total"] == 1


def test_an_unchecked_interpreter_or_an_owned_threshold_is_left_alone():
    assert idle.generational_gc_supported("cpython", (3, 10))
    assert idle.generational_gc_supported("cpython", (3, 13))
    assert not idle.generational_gc_supported("cpython", (3, 14))
    assert not idle.generational_gc_supported("pypy", (3, 10))
    fake, loop = FakeGC(), FakeLoop()
    collector = _collector(fake, supported=False)
    _request(collector, loop)
    assert fake.set_calls == [] and collector.skip_reason == idle.SKIP_UNSUPPORTED and loop.timers == []
    fake = FakeGC(threshold=(700, 10, idle.A1992_FULL_THRESHOLD_OFF))
    collector = _collector(fake)
    _request(collector, loop)
    assert fake.set_calls == [] and collector.skip_reason == idle.SKIP_ALREADY_OWNED


def test_on_a_real_event_loop_the_pass_waits_for_the_handler():
    fake = FakeGC()
    collector = _collector(fake, delay_s=0.0)
    order = []
    fake.on_collect = lambda: order.append(("collect", collector.in_request))

    def handle():
        loop = idle.running_loop()
        collector.begin_request(loop)
        order.append("respond")
        collector.end_request(loop)

    async def forward():
        handle()
        order.append("forward_return")
        await asyncio.sleep(0.01)

    asyncio.run(forward())
    assert order == ["respond", "forward_return", ("collect", False)]
    assert idle.running_loop() is None


def test_on_this_interpreter_the_raised_trigger_stops_automatic_full_passes():
    if not idle.generational_gc_supported():
        return

    def full_passes(count):
        before = gc.get_stats()[2]["collections"]
        keep = [[i] for i in range(count)]
        after = gc.get_stats()[2]["collections"]
        del keep
        return after - before

    saved, enabled = gc.get_threshold(), gc.isenabled()
    gc.enable()
    gc.freeze()  # the test process's own heap would otherwise hold the pending ratio down
    try:
        gc.collect()
        gc.set_threshold(100, 10, 10)
        assert full_passes(300_000) > 0
        gc.collect()
        gc.set_threshold(100, 10, idle.A1992_FULL_THRESHOLD_OFF)
        assert full_passes(300_000) == 0
        before = gc.get_stats()[2]["collections"]
        gc.collect()
        assert gc.get_stats()[2]["collections"] == before + 1
    finally:
        gc.set_threshold(*saved)
        gc.unfreeze()
        if not enabled:
            gc.disable()


# ---- runtime: Simple.handle, executed from source -----------------------------------------------

_method_source = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")


class _Parent:
    def __init__(self, collector):
        self._a1992_idle_gc = collector
        self._tick = 0
        self.rows = []
        self.inside = []

    def _emit(self, event_type, force=False, **payload):
        self.rows.append((event_type, payload))

    def handle(self, state):
        self._tick += 1
        collector = self._a1992_idle_gc
        self.inside.append(collector.in_request if collector is not None else None)
        if state == "boom":
            raise RuntimeError("boom")
        return f"response-{self._tick}"


def _harness(collector):
    # Compiled inside a class body so the method's zero-argument super() resolves.
    body = "".join(textwrap.indent(textwrap.dedent(_method_source(name)), "    ") + "\n"
                   for name in ("handle", "_a1992_note_request"))
    namespace = {"_Parent": _Parent, "running_loop": idle.running_loop,
                 "A1992_IDLE_GC_VERSION": idle.A1992_IDLE_GC_VERSION}
    exec("from __future__ import annotations\nclass Harness(_Parent):\n" + body, namespace)
    return namespace["Harness"](collector)


def test_simple_handle_wraps_the_whole_request_and_logs_each_tick():
    fake = FakeGC()
    agent = _harness(_collector(fake, delay_s=0.0))

    async def forward(state):
        response = agent.handle(state)
        await asyncio.sleep(0.01)
        return response

    assert asyncio.run(forward("s1")) == "response-1"
    assert asyncio.run(forward("s2")) == "response-2"
    assert agent.inside == [True, True] and fake.collect_calls == 2
    (state_row,) = [p for kind, p in agent.rows if kind == "A1992_IDLE_GC_STATE"]
    assert state_row["installed"] == 1 and state_row["saved_threshold"] == [700, 10, 10]
    assert state_row["a1992_idle_gc_version"] == idle.A1992_IDLE_GC_VERSION
    ticks = [p for kind, p in agent.rows if kind == "A1992_GC_TICK"]
    assert [row["tick"] for row in ticks] == [1, 2]
    assert ticks[1]["idle_passes"] == 1 and ticks[1]["request_gen2"] == 0


def test_a_failing_request_still_closes_the_request_window():
    fake, loop = FakeGC(), FakeLoop()
    collector = _collector(fake)
    agent = _harness(collector)
    try:
        agent.handle("boom")
    except RuntimeError:
        pass
    else:
        raise AssertionError("the request error must propagate")
    assert agent.inside == [True] and collector.in_request is False
    assert [kind for kind, _ in agent.rows] == ["A1992_IDLE_GC_STATE", "A1992_GC_TICK"]


def test_the_switch_off_restores_a1_9_9_1():
    agent = _harness(None)
    assert agent.handle("s1") == "response-1"
    assert agent.inside == [None] and agent.rows == []


# ---- nothing the strategy decides can see the change --------------------------------------------

def test_no_strategy_module_observes_when_the_collector_runs():
    paths = sorted(STRATEGY.glob("Strategy1*.py")) + sorted(STRATEGY.glob("research_*.py"))
    paths += [STRATEGY / "DetailedTemplateAgent.py", STRATEGY / "candidate_screen.py"]
    offenders = []
    for path in paths:
        if path.name == "research_direct_idle_gc.py":
            continue
        text = path.read_text(errors="replace")
        for token in ("import weakref", "weakref.", "def __del__", "import gc", "gc.collect(",
                      "gc.set_threshold(", "gc.callbacks", "gc.freeze("):
            if token in text:
                offenders.append((path.name, token))
    assert not offenders, offenders


# ---- wiring ------------------------------------------------------------------------------------

def test_the_idle_collector_is_wired_and_launched():
    handle = _method_source("handle")
    begin, call, end = (handle.index("collector.begin_request(loop)"),
                        handle.index("return super().handle(state)"),
                        handle.index("collector.end_request(loop)"))
    assert begin < call < handle.index("finally:") < end
    assert "self.research_a1992_idle_gc = self._as_bool(" in SIMPLE
    assert "IdleCollector(delay_s=" in SIMPLE
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_8"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_8"' in SIMPLE
    assert idle.A1992_IDLE_GC_VERSION.endswith("a1_9_9_2")
    for key in ("direct_a1992_version", "direct_a1992_idle_gc", "direct_a1992_installed",
                "direct_a1992_fallback_reason", "direct_a1992_request_full_passes",
                "direct_a1992_idle_passes", "direct_a1992_idle_cancelled", "direct_a1992_idle_max_ms"):
        assert f'stats["{key}"]' in SIMPLE, key
    assert ("strategy1_direct_v4_16_2_a1_9_9_2) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; "
            "A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1") in LAUNCHER
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"', start + len('PARAMS="'))]
    for switch in ("research_a1992_idle_gc=1", "research_a1991_pending_owns_book=1",
                   "research_a199_exit_pending_authority=1", "research_a199_epoch_resync=1"):
        assert switch in params, switch
    assert "tests/test_research_a1_9_9_2_idle_gc.py" in LAUNCHER
    assert "[preflight] A1.9.9.2 idle-gap garbage collection PASS" in LAUNCHER
    for literal in ("set_threshold(saved[0], saved[1], A1992_FULL_THRESHOLD_OFF)",
                    "self._timer = loop.call_later(self.delay_s, self.collect_idle)"):
        assert literal in MODULE and literal in LAUNCHER, literal
    assert "collector.begin_request(loop)" in LAUNCHER
