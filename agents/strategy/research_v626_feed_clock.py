# SPDX-License-Identifier: MIT
"""v6.26 S6: the fundamental page's age is judged in its own clocks -- research_v626_feed_clock.

Why (UID 115 on v6.25, 10-12 JST; scratchpad k60 fpage60 / stalefill60, k61 feedage61 / rule61; memory
fp-feed-stale-limit-sim-rate):

* v6.16 calls the validator's fundamental page stale once the state is more than MAX_STALE_SIM_S = 20 sim-s past the
  page's sim time.  That number was v7.0's (OBSERVED there, "the page's ~10 sim-s plus one fetch period"), when the
  simulation ran ~0.165 sim-s per wall-s: 20 sim-s was ~121 wall-s, more than two of the feed's 55-s on-schedule
  cycles.  The page's age is two different clocks, though: the page itself publishes every 10 sim-s (and lags the
  simulation by up to one step), while the feed that carries it to the agent runs on WALL time (one fetch per
  FETCH_PERIOD_S, each agent re-reading the host cache every POLL_S, a fetch allowed FETCH_TIMEOUT_S).  The simulations
  of 10-10 and 10-12 run ~0.23-0.24 sim-s per wall-s, so the feed's normal on-schedule cycle alone crosses 20 sim-s:
  ~10% of requests see a stale page (10-10 sim 10.1%, 10-12 sim 10.7%, a stale episode every ~4 min).
* When the page is stale, v6.25 S5 drops the lean (tau 0) and v6.23 S1 withholds nothing, so the agent unwinds the
  inventory it built toward the fundamental: orders placed at page age 15-20 sim-s earned +3.9 bps vs the
  fundamental, at 20-25 sim-s -12.8 bps (75% filled against it).  The fundamental moves only p50 2.4 / 3.8 bps over
  20 / 30 sim-s, so a page one feed cycle old is still the anchor; dropping it is what costs.
* Replayed on the recorded pages and the agent's own request times (k61 rule61, this rule exactly): stale requests
  10.7% -> 0.35% (10-12 sim) and 10.1% -> 0.12% (10-10 sim); the limit is ~32.5-33.8 sim-s at today's rate.

The rule (one switch, ``research_v626_feed_clock``, default on):

    limit_sim_s = PAGE_STEPS x PAGE_STEP_SIM_S + rate x (FETCH_PERIOD_S + POLL_S + FETCH_TIMEOUT_S)

* ``rate`` is the simulation's sim-s per wall-s, measured by the agent from its own requests: the state-time span over
  the wall span of its last RATE_WINDOW requests (at least RATE_MIN_POINTS of them, a positive wall span; clamped at 0).
  A state time that runs back (a new simulation) starts the window over.  Until the rate is known the limit is v6.16's
  MAX_STALE_SIM_S -- exactly v6.25.
* Every reader of the page (v6.17's touch quote, v6.23 S1, v6.25 S5 through it, v6.16's centre) judges the page with
  this request's limit; everything else about the page (another simulation, no fundamental, no book) is v6.16's.  A
  dead feed still goes stale: its age keeps growing in sim time and the limit is finite.
* At a rate of 0 the limit is 2 x 10 = 20 sim-s = v6.16's: the rule never calls a page stale that v6.16 keeps.

STRUCTURAL: the feed's wall cycle is the feed's own constants (imported from research_v616_fair_value, never
duplicated); two page steps = the page at most one refresh plus its publish lag behind at a fetch; the rate is measured,
not set.  OBSERVED: PAGE_STEP_SIM_S (the validator page publishes every 10 sim-s: v7.0's "page's ~10 sim-s", the 10-10
and 10-12 recorders' step p50 10.0).  The window (64 requests) and the minimum (8) are estimator choices, not sized to
any measurement of this defect: the rate is a ratio of spans, so the window only sets how fast a changed rate is
followed.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any

from research_v616_fair_value import (
    FETCH_PERIOD_S,
    FETCH_TIMEOUT_S,
    MAX_STALE_SIM_S,
    POLL_S,
    same_simulation,
)

V626_FEED_CLOCK_VERSION = "feed_clock_v6_26"
PAGE_STEP_SIM_S = 10.0      # OBSERVED: the validator page publishes every 10 sim-s (recorders 10-10 / 10-12 p50 10.0)
PAGE_STEPS = 2              # STRUCTURAL: at a fetch the page is at most one refresh plus its publish lag behind
WALL_CYCLE_S = FETCH_PERIOD_S + POLL_S + FETCH_TIMEOUT_S    # STRUCTURAL: the feed's own on-schedule wall cycle
RATE_WINDOW = 64            # STRUCTURAL: the rate is the span over the agent's last 64 requests (an estimator choice)
RATE_MIN_POINTS = 8         # STRUCTURAL: no rate from fewer than 8 requests (an estimator choice)

# The verdicts of one request's page (page_verdict).
NO_FEED, OTHER_SIM, FRESH, RESCUED, STALE = "no_feed", "other_sim", "fresh", "rescued", "stale"


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


class SimRate:
    """The simulation's sim-s per wall-s, from the (wall time, state time) pair of each of the agent's requests: the
    state-time span over the wall span of the last ``window`` pairs.  Never raises."""

    def __init__(self, window: int = RATE_WINDOW, min_points: int = RATE_MIN_POINTS) -> None:
        self.window = max(2, int(window))
        self.min_points = max(2, int(min_points))
        self._points: deque = deque(maxlen=self.window)
        self.resets = 0

    @property
    def points(self) -> int:
        return len(self._points)

    def observe(self, wall_s: Any, state_ts_ns: Any) -> bool:
        """Add one request's pair (wall seconds, state time in ns).  A state time behind the last one -- a new
        simulation -- starts the window over first.  A pair without a finite wall time or a positive state time is
        ignored (False)."""
        try:
            w, t = _finite(wall_s), _finite(state_ts_ns)
            if w is None or t is None or t <= 0.0:
                return False
            if self._points and t < self._points[-1][1]:
                self._points.clear()
                self.resets += 1
            self._points.append((w, t))
            return True
        except Exception:
            return False

    def rate(self) -> float | None:
        """sim-s per wall-s over the window, clamped at 0; None with fewer than ``min_points`` pairs or a wall span that
        is not positive."""
        try:
            if len(self._points) < self.min_points:
                return None
            (w0, t0), (w1, t1) = self._points[0], self._points[-1]
            span = w1 - w0
            if not span > 0.0:
                return None
            r = (t1 - t0) / 1e9 / span
            return max(0.0, r) if math.isfinite(r) else None
        except Exception:
            return None


def stale_limit_sim_s(rate: Any) -> float:
    """The page's freshness limit in sim-s: PAGE_STEPS page steps plus the feed's wall cycle at ``rate`` sim-s per
    wall-s (a negative rate counts as 0); v6.16's MAX_STALE_SIM_S while the rate is unknown (None or not a number)."""
    try:
        r = _finite(rate)
        if r is None:
            return MAX_STALE_SIM_S
        return PAGE_STEPS * PAGE_STEP_SIM_S + max(0.0, r) * WALL_CYCLE_S
    except Exception:
        return MAX_STALE_SIM_S


def state_sim_id(cfg: Any) -> Any:
    """The simulation id of a state's config, derived exactly as the page's readers derive it (its simulation_id, else
    the last part of its log directory)."""
    try:
        sim_id = getattr(cfg, "simulation_id", None) if cfg is not None else None
        if not sim_id and cfg is not None:
            log_dir = getattr(cfg, "logDir", None)
            sim_id = str(log_dir).replace("\\", "/").rstrip("/").split("/")[-1] if log_dir else None
        return sim_id
    except Exception:
        return None


def page_verdict(snapshot: Any, *, state_ts_ns: Any, sim_id: Any, limit_sim_s: Any) -> str:
    """One request's page, in v6.16's order: NO_FEED (no page), OTHER_SIM (a page of another simulation), STALE (no
    usable sim time, or older than ``limit_sim_s``), RESCUED (older than v6.16's MAX_STALE_SIM_S, within the limit: v6.16
    would have called it stale), FRESH.  An unusable limit is v6.16's.  Never raises."""
    try:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("fp"), dict):
            return NO_FEED
        if not same_simulation(snapshot.get("sim_id"), sim_id):
            return OTHER_SIM
        page_ts, now_ts = _finite(snapshot.get("sim_ts_ns")), _finite(state_ts_ns)
        if page_ts is None or now_ts is None or now_ts <= 0.0:
            return STALE
        limit = _finite(limit_sim_s)
        if limit is None or limit < 0.0:
            limit = MAX_STALE_SIM_S
        age = max(0.0, (now_ts - page_ts) / 1e9)
        if age > limit:
            return STALE
        return RESCUED if age > MAX_STALE_SIM_S else FRESH
    except Exception:
        return NO_FEED


__all__ = ["FRESH", "NO_FEED", "OTHER_SIM", "PAGE_STEPS", "PAGE_STEP_SIM_S", "RATE_MIN_POINTS", "RATE_WINDOW", "RESCUED",
           "STALE", "SimRate", "V626_FEED_CLOCK_VERSION", "WALL_CYCLE_S", "page_verdict", "stale_limit_sim_s",
           "state_sim_id"]
