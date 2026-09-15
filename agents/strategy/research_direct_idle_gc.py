# SPDX-License-Identifier: MIT
"""A1.9.9.2: CPython's full garbage-collection pass runs between requests, not inside one.

Measured on the A1.9.9.1 run, log 20260914_141324, RESPOND_TIMING ticks 1-8,778.  p95
response was 173.3 ms over ticks 8,001-8,500, and 52 of those 500 ticks were over 120 ms.
The ticks are two populations.  On a slow tick one phase carries a single excess of about
the same size wherever it lands.  Over ticks 6,001-8,778, 222 of 280 landed in full_predict
(median excess 123.9 ms), 30 in the screen (106.9 ms), 22 in the unattributed residual and 2
in logging (98.5 ms); only 1 of the 280 had a second phase more than 30 ms over its median.
Logging cannot spend 98 ms predicting.  Nor does the excess land by time: order building is
16% of a tick and caught 2 of the 280.  It lands by allocation, which is what makes CPython
run a collection, and it grows with the heap -- about 70 ms before tick 3,000, 124 ms by
8,500 -- while full_predict's median stayed at 13.7-14.7 ms.  A full pass over 1.2M tracked
synthetic objects takes 117 ms on the same host (Python 3.12).

Taking the one excess out of each slow tick puts p95 at 54.9 ms for ticks 8,001-8,500, with
2 ticks over 120 ms.  The A1.9.9 run, log 20260914_083519, has the same shape: p95 118.3 ms
at tick 4,000, and 45.3 ms without the excess.

Consecutive responses were at least 3,853 ms apart (median 4,726), so the pass can be paid
for while the process is idle:

  * generations 0 and 1 stay automatic -- they cost the young objects, not the heap;
  * the automatic full pass is switched off by raising its trigger, the count of
    generation-1 collections since the last full pass;
  * after each request the miner's event loop is asked to run the full pass
    ``A1992_IDLE_DELAY_MS`` later.  The request runs synchronously on that loop, so the
    pass can only start after the request has returned, and a request that arrives first
    cancels it.

The strategy never observes a collection itself (no finalizers, weak references or gc calls
on its path).  Two paths read its own speed and will see a faster agent: the 100 ms
pre-submit freshness gate on new entries (research_direct_fastpath) and Score-EV's latency
cost, 0.04 x min(1, EWMA / 50 ms) over earlier responses.  On the A1.9.9.1 run 1,013 ticks
took over 100 ms, against 30 without the excess.

Automatic collection comes back for the rest of the session when a request runs without an
event loop after install, when ``A1992_FALLBACK_RESPONSES`` requests pass without an idle
pass, or when the pass raises.  Nothing is installed with no running loop -- the test
harness and a sync FastAPI route -- or on an interpreter whose generation-2 trigger has not
been checked (CPython 3.8-3.13; the miner venv is 3.10).
"""
from __future__ import annotations

import asyncio
import gc as _gc
import sys
import time
from typing import Any, Callable

A1992_IDLE_GC_VERSION = "direct_idle_gc_v4_16_2_a1_9_9_2"

# CPython runs generation 2 once this many generation-1 collections have passed since the last
# full pass.  2**30 is never reached.
A1992_FULL_THRESHOLD_OFF = 1 << 30
# After a response: longer than compressing and sending one, far below the 3,853 ms minimum gap.
A1992_IDLE_DELAY_MS = 500.0
# Requests without an idle pass before automatic collection comes back (~95 s at 4.7 s a tick).
A1992_FALLBACK_RESPONSES = 20
# Counting tracked objects walks the heap, so only every Nth idle pass does it.
A1992_HEAP_SAMPLE_EVERY = 100

FALLBACK_NO_EVENT_LOOP = "NO_EVENT_LOOP"
FALLBACK_NO_IDLE_GAP = "NO_IDLE_GAP"
FALLBACK_COLLECT_ERROR = "COLLECT_ERROR"
SKIP_NO_EVENT_LOOP = "NO_EVENT_LOOP"
SKIP_UNSUPPORTED = "UNSUPPORTED_GC"
SKIP_ALREADY_OWNED = "ALREADY_OWNED"
SKIP_THRESHOLD_ERROR = "THRESHOLD_ERROR"


def generational_gc_supported(
    implementation: str | None = None, version: tuple[int, ...] | None = None,
) -> bool:
    """True where generation 2 is triggered by the generation-1 count this module raises."""
    name = sys.implementation.name if implementation is None else implementation
    ver = tuple(sys.version_info[:2]) if version is None else tuple(version[:2])
    return name == "cpython" and (3, 8) <= ver <= (3, 13)


def running_loop() -> Any:
    """The event loop running this thread's request, or None."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


class IdleCollector:
    """Owns CPython's full collection for one agent process."""

    def __init__(
        self,
        *,
        gc_module: Any = _gc,
        clock: Callable[[], float] = time.perf_counter,
        delay_s: float = A1992_IDLE_DELAY_MS / 1000.0,
        fallback_responses: int = A1992_FALLBACK_RESPONSES,
        supported: bool | None = None,
    ) -> None:
        self._gc = gc_module
        self._clock = clock
        self.delay_s = max(0.0, float(delay_s))
        self.fallback_responses = max(1, int(fallback_responses))
        self.supported = generational_gc_supported() if supported is None else bool(supported)
        self.installed = False
        self.saved_threshold: tuple[int, ...] | None = None
        self.fallback_reason = ""
        self.skip_reason = ""
        self.in_request = False
        self._timer: Any = None
        self._begin_collections = (0, 0, 0)
        self.requests = 0
        self.request_collections = (0, 0, 0)
        self.request_full_total = 0
        self.responses_since_idle = 0
        self.idle_passes = 0
        self.idle_cancelled = 0
        self.idle_skipped_busy = 0
        self.idle_last_ms = 0.0
        self.idle_max_ms = 0.0
        self.idle_total_ms = 0.0
        self.idle_last_unreachable = 0
        self.tracked_objects: int | None = None

    # ---- request boundary ---------------------------------------------------------------------

    def begin_request(self, loop: Any) -> None:
        self.in_request = True
        self.requests += 1
        if self._timer is not None:
            self._cancel_timer()
            self.idle_cancelled += 1
        if not self.installed and not self.fallback_reason:
            self._install(loop)
        self._begin_collections = self._collections()

    def end_request(self, loop: Any) -> None:
        now = self._collections()
        self.request_collections = tuple(
            max(0, now[gen] - self._begin_collections[gen]) for gen in range(3)
        )
        self.request_full_total += self.request_collections[2]
        self.in_request = False
        if not self.installed:
            return
        self.responses_since_idle += 1
        if loop is None:
            self._fall_back(FALLBACK_NO_EVENT_LOOP)
        elif self.responses_since_idle > self.fallback_responses:
            self._fall_back(FALLBACK_NO_IDLE_GAP)
        else:
            try:
                self._timer = loop.call_later(self.delay_s, self.collect_idle)
            except Exception:
                self._fall_back(FALLBACK_NO_EVENT_LOOP)

    def collect_idle(self) -> int | None:
        """The full pass, run by the loop after the request that scheduled it has returned."""
        self._timer = None
        if self.in_request or not self.installed:
            self.idle_skipped_busy += 1
            return None
        started = self._clock()
        try:
            unreachable = int(self._gc.collect())
        except Exception:
            self._fall_back(FALLBACK_COLLECT_ERROR)
            return None
        ms = (self._clock() - started) * 1000.0
        self.idle_passes += 1
        self.idle_last_ms = ms
        self.idle_max_ms = max(self.idle_max_ms, ms)
        self.idle_total_ms += ms
        self.idle_last_unreachable = unreachable
        self.responses_since_idle = 0
        if (self.idle_passes - 1) % A1992_HEAP_SAMPLE_EVERY == 0:
            try:
                self.tracked_objects = len(self._gc.get_objects())
            except Exception:
                pass
        return unreachable

    # ---- interpreter ---------------------------------------------------------------------------

    def _install(self, loop: Any) -> None:
        if not self.supported:
            self.skip_reason = SKIP_UNSUPPORTED
            return
        if loop is None:
            self.skip_reason = SKIP_NO_EVENT_LOOP
            return
        try:
            saved = tuple(int(x) for x in self._gc.get_threshold())
            if saved[2] >= A1992_FULL_THRESHOLD_OFF:
                self.skip_reason = SKIP_ALREADY_OWNED
                return
            self._gc.set_threshold(saved[0], saved[1], A1992_FULL_THRESHOLD_OFF)
        except Exception:
            self.skip_reason = SKIP_THRESHOLD_ERROR
            return
        self.saved_threshold = saved
        self.installed = True
        self.skip_reason = ""

    def _fall_back(self, reason: str) -> None:
        self._cancel_timer()
        if self.saved_threshold is not None:
            try:
                self._gc.set_threshold(*self.saved_threshold)
            except Exception:
                pass
        self.installed = False
        self.fallback_reason = reason

    def _cancel_timer(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _collections(self) -> tuple[int, int, int]:
        try:
            stats = self._gc.get_stats()
            return tuple(int(stats[gen].get("collections", 0) or 0) for gen in range(3))
        except Exception:
            return (0, 0, 0)

    def snapshot(self) -> dict[str, Any]:
        return {
            "installed": int(self.installed),
            "fallback_reason": self.fallback_reason,
            "skip_reason": self.skip_reason,
            "saved_threshold": list(self.saved_threshold) if self.saved_threshold else None,
            "delay_ms": round(self.delay_s * 1000.0, 3),
            "python": "%s %d.%d.%d" % (sys.implementation.name, *sys.version_info[:3]),
            "requests": self.requests,
            "request_gen0": self.request_collections[0],
            "request_gen1": self.request_collections[1],
            "request_gen2": self.request_collections[2],
            "request_full_total": self.request_full_total,
            "responses_since_idle": self.responses_since_idle,
            "idle_passes": self.idle_passes,
            "idle_cancelled": self.idle_cancelled,
            "idle_skipped_busy": self.idle_skipped_busy,
            "idle_last_ms": round(self.idle_last_ms, 3),
            "idle_max_ms": round(self.idle_max_ms, 3),
            "idle_mean_ms": round(self.idle_total_ms / self.idle_passes, 3) if self.idle_passes else 0.0,
            "idle_last_unreachable": self.idle_last_unreachable,
            "tracked_objects": self.tracked_objects,
        }
