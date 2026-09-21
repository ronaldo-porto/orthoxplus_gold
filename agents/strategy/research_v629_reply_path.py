# SPDX-License-Identifier: MIT
"""v6.2.9: the reply path -- what the validator waits for, and nothing else.

Why (2026-09-22, validator ground truth plus the recorded order stream):

The validator turns each response's round-trip time into a delay on that miner's instructions
(taos/im/validator/reward.py ``compute_delay``): ``delay = 10 ms + (e^(5t) - 1) / (e^5 - 1) * 990 ms``
with ``t = process_time / neuron.timeout`` and a 3 s timeout.  It publishes the round trip as the
``call_time`` miner gauge.  Ours on testnet is 1.088 s, which the recorded order stream confirms: our
orders land a median 43 ms after their state, exactly the curve's value at 1.07 s.  On mainnet the ten
miners with the highest trading score answer in 0.66-0.93 s, all in the fastest quarter, and the fastest
quarter of makers marks +0.7 / +5.8 bps at 60 s on its entries against -12.6 / -5.5 for the slowest.
A later instruction is a staler quote: the mechanism is direct.

Our own code is ~0.18 s of the 1.088 s.  Part of it is telemetry that runs after every order has been
decided: the v5.0.0 analytics, the v5.0.3 observatory capture, and the v5.0.x / v6.x state rows.  The
validator waits for all of it.  This build moves it into the gap after the reply, and measures the parts
of the round trip the agent can see.

Two switches, one mechanism each:

* **D1 -- deferred telemetry.**  The post-decision telemetry block is queued instead of run, and the
  queue runs ``DEFER_DELAY_MS`` after the request returns, on the miner's event loop -- the same place and
  mechanism as the A1.9.9.2 idle collection.  The request runs synchronously on that loop, so the queue
  can only start once the reply is on its way.  Decisions are unchanged by construction: nothing in the
  queue feeds the request it came from (it ran after every order was final), and it always completes
  before the next request touches any state -- a request that arrives first runs the queue at its start.
  The miner clears ``books``/``accounts``/``notices``/``config`` off the synapse after the handler
  returns, so queued work reads a frozen view that keeps those references.  With no running loop (the
  test harness, a sync route) everything runs inline exactly as before.
* **D2 -- reply timing.**  Per request: ``recv_lag`` = our wall clock at handler entry minus the
  validator's send stamp (``synapse.dendrite.nonce``, ``time.time_ns()`` at send) -- the upload, the
  axon and the decompression; ``reply`` = handler entry to return; ``deferred`` = the queue's run time,
  per task.  A summary row every ``TIMING_EVERY_TICKS`` requests.  Measurement only.
"""
from __future__ import annotations

import math
import time
from typing import Any, Callable

V629_REPLY_PATH_VERSION = "reply_path_v6_2_9"

# After a response: longer than compressing and sending one, and ahead of the A1.9.9.2 collection
# (500 ms), so the collection also sweeps what the telemetry allocated.  Far below the ~3.8 s minimum gap.
DEFER_DELAY_MS = 300.0
TIMING_EVERY_TICKS = 100
MAX_SAMPLES = 1024
# A send stamp further off than this is not a send stamp (clock skew, a replayed or synthetic state).
MAX_PLAUSIBLE_LAG_MS = 60_000.0

FROZEN_FIELDS = ("books", "accounts", "notices", "config")


class FrozenStateView:
    """A state whose cleared-after-reply fields are pinned; everything else reads through.

    ``clear_inputs()`` sets the four FROZEN_FIELDS to None on the synapse once the handler returns.  The
    view holds the original objects (a LazyBooks, the accounts map, ...) so queued work sees exactly what
    the request saw.  Attribute writes go to the underlying state.
    """

    __slots__ = ("_state", "_frozen")

    def __init__(self, state: Any) -> None:
        object.__setattr__(self, "_state", state)
        object.__setattr__(self, "_frozen", {k: getattr(state, k, None) for k in FROZEN_FIELDS})

    def __getattr__(self, name: str) -> Any:
        frozen = object.__getattribute__(self, "_frozen")
        if name in frozen:
            return frozen[name]
        return getattr(object.__getattribute__(self, "_state"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(object.__getattribute__(self, "_state"), name, value)


def recv_lag_ms(state: Any, now_ns: int) -> float | None:
    """Milliseconds from the validator's send stamp to ``now_ns``; None when there is no usable stamp."""
    try:
        nonce = getattr(getattr(state, "dendrite", None), "nonce", None)
        if nonce is None:
            return None
        lag = (int(now_ns) - int(nonce)) / 1e6
    except (TypeError, ValueError):
        return None
    if not math.isfinite(lag) or lag < -MAX_PLAUSIBLE_LAG_MS or lag > MAX_PLAUSIBLE_LAG_MS:
        return None
    return lag


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


class DeferredWork:
    """A FIFO of zero-argument callables run after the reply, timed per task."""

    def __init__(self, *, clock: Callable[[], float] = time.perf_counter, delay_s: float = DEFER_DELAY_MS / 1000.0):
        self._clock = clock
        self.delay_s = max(0.0, float(delay_s))
        self._queue: list[tuple[str, Callable[[], Any]]] = []
        self._timer: Any = None
        self._lap_t: float | None = None
        self._laps: dict[str, float] = {}
        self.passes = 0
        self.early_flushes = 0
        self.inline_runs = 0
        self.errors = 0
        self.last_ms = 0.0
        self.last_laps: dict[str, float] = {}
        self.samples: list[float] = []
        self.lap_samples: dict[str, list[float]] = {}

    @property
    def pending(self) -> int:
        return len(self._queue)

    def add(self, name: str, fn: Callable[[], Any]) -> None:
        self._queue.append((str(name), fn))

    def lap(self, name: str) -> None:
        """Inside a running task: book the time since the previous lap (or the task's start) to ``name``."""
        if self._lap_t is None:
            return
        now = self._clock()
        self._laps[name] = self._laps.get(name, 0.0) + (now - self._lap_t) * 1000.0
        self._lap_t = now

    def schedule(self, loop: Any) -> bool:
        """Run the queue ``delay_s`` after this point on ``loop``; inline when there is no loop."""
        if not self._queue:
            return False
        if loop is None:
            self.inline_runs += 1
            self.run()
            return False
        self._cancel()
        try:
            self._timer = loop.call_later(self.delay_s, self.run)
            return True
        except Exception:
            self.inline_runs += 1
            self.run()
            return False

    def flush(self) -> bool:
        """At the start of a request: the previous request's queue runs NOW if its timer has not fired."""
        if not self._queue:
            self._cancel()
            return False
        self._cancel()
        self.early_flushes += 1
        self.run()
        return True

    def run(self) -> float:
        self._timer = None
        queue, self._queue = self._queue, []
        if not queue:
            return 0.0
        started = self._clock()
        self._laps = {}
        for name, fn in queue:
            task_started = self._clock()
            self._lap_t = task_started
            try:
                fn()
            except Exception:
                self.errors += 1
            # The task's whole time under its own name; laps it booked inside sit under theirs.
            self._laps[name] = self._laps.get(name, 0.0) + (self._clock() - task_started) * 1000.0
        self._lap_t = None
        ms = (self._clock() - started) * 1000.0
        self.passes += 1
        self.last_ms = ms
        self.last_laps = dict(self._laps)
        _bounded_append(self.samples, ms)
        for k, v in self._laps.items():
            _bounded_append(self.lap_samples.setdefault(k, []), v)
        return ms

    def _cancel(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def snapshot(self) -> dict[str, Any]:
        return {
            "passes": self.passes, "early_flushes": self.early_flushes, "inline_runs": self.inline_runs,
            "errors": self.errors, "pending": self.pending,
            "deferred_ms_p50": _r(percentile(self.samples, 0.5)),
            "deferred_ms_p95": _r(percentile(self.samples, 0.95)),
            "lap_ms_p50": {k: _r(percentile(v, 0.5)) for k, v in sorted(self.lap_samples.items())},
        }


class ReplyTiming:
    """Rolling samples of the parts of the round trip the agent can see."""

    def __init__(self) -> None:
        self.recv_lag: list[float] = []
        self.reply: list[float] = []
        self.requests = 0
        self.no_stamp = 0

    def note(self, *, lag_ms: float | None, reply_ms: float) -> None:
        self.requests += 1
        if lag_ms is None:
            self.no_stamp += 1
        else:
            _bounded_append(self.recv_lag, float(lag_ms))
        _bounded_append(self.reply, float(reply_ms))

    def snapshot(self) -> dict[str, Any]:
        return {
            "requests": self.requests, "no_send_stamp": self.no_stamp,
            "recv_lag_ms_p10": _r(percentile(self.recv_lag, 0.10)),
            "recv_lag_ms_p50": _r(percentile(self.recv_lag, 0.50)),
            "recv_lag_ms_p95": _r(percentile(self.recv_lag, 0.95)),
            "reply_ms_p50": _r(percentile(self.reply, 0.50)),
            "reply_ms_p95": _r(percentile(self.reply, 0.95)),
            "reply_ms_max": _r(max(self.reply) if self.reply else None),
        }


def _bounded_append(xs: list[float], v: float) -> None:
    xs.append(v)
    if len(xs) > MAX_SAMPLES:
        del xs[: len(xs) - MAX_SAMPLES]


def _r(v: float | None) -> float | None:
    return None if v is None else round(float(v), 3)
