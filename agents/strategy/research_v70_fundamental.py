# SPDX-License-Identifier: MIT
"""v7.0: the deep layer anchors on the fundamental price the validator publishes for every book.

Why (mainnet, sim 20260929_2015, measured 10-03 JST on UID 237's observatory and a poll of the validator's page;
scratchpad k12/fp, v70/vm70.py):

* The validator serves every book's fundamental price -- the simulator's hidden fair value that its background traders
  converge to -- on its public metrics page (``/metrics/books``, gauge ``book_gauges{book_gauge_name=
  "fundamental_price"}``), refreshed about every 10 sim-s.  Miners never receive it in the state, and it cannot be
  computed: the simulator's process is a diffusion with jumps whose random stream the external price only seeds.
* The market closes toward it slowly: the gap's median is 13 bps, its half-life 1,300-6,200 sim-s, and the next
  240 sim-s move +4.5 bps toward it on average (corr +0.35).  The field's alpha leaders trade toward it 81-91% of the
  time at ~0 delay; 59% of our maker's fills went against it, and they agree better with a 60-s-old fundamental than
  with the current one -- our resting orders are taken by whoever reads it first.
* Our fills by gap size, 120 sim-s later in our favour: toward a gap of 15 bps or more +1.8..+3.8 bps with capture
  94-99 per 1M; against it -4.5..-5.0 bps with capture 38-53 per 1M.
* Replayed with the validator's arithmetic (vm70 = the as-built v6.12 deep-ladder replay on UID 237's recording, the
  fundamental polled every 30 wall-s): sim 43,900-45,323 with this module's theta, v6.12 making 37.31 / alpha -99
  (kappa -0.090) -> making 38.41 / alpha +60 (kappa +0.055); the data 3 / 8 / 15 sim-s older: alpha +43 / +25 / -44 and
  making -0.5% / +0.6% / -6%; the toward side's bound x1 instead of x2: alpha +5.  On sim 43,900-45,153 with a fixed
  theta: 5 bps making 30.65 -> 31.11 / alpha -59 -> +110, 8 bps alpha +47; the toward side at 0.25 of the depth: making
  -7..-9%; the against side pushed deeper (x1.5): making 12.9 (refuted); refusing every fill against the fundamental:
  making collapses (refuted: the pay is 2 x min(buy, sell) per book, and the against side's fills still capture).

The rule (one switch, ``research_v70_fundamental``):

* On a book whose fundamental sits at least theta from the mid, the level-0 deep order on the side that trades TOWARD
  it rests at TOWARD_DEPTH_FACTOR of the book's depth, the other side's at the full depth (the v6.12 reducing-side pull
  does not apply against the fundamental), and the toward side may hold TOWARD_BOUND_FACTOR x the book's inventory
  bound.  Below theta, without a fresh fundamental of this simulation, or inside a blown-out spread (v6.4 vacuum), the
  book is exactly v6.12.
* STRUCTURAL: theta = THETA_Z x sqrt(sigma_s^2 x staleness + half_spread^2) in bps -- the fundamental's own diffusion
  since its publication (sigma per sim-second from the simulation's fp_sigma over its duration, both in the state's
  config) and the mid's half-spread, at two standard deviations; ~5-6 bps at the feed's usual ~8 sim-s.  A simulation
  without fp_sigma or duration has no theta and is never anchored.
* The feed: one GET of the page per FETCH_PERIOD_S per host, shared by every agent on it through a cache file in
  CACHE_DIR (an exclusive non-blocking lock picks the one process that fetches).  A failed fetch, a page of another
  simulation or data older than MAX_STALE_SIM_S disarms the rule; nothing here can block a response.
* OBSERVED: TOWARD_DEPTH_FACTOR 0.5 (replay grid 0.25 / 0.5: 0.5 kept the making), TOWARD_BOUND_FACTOR 2.0 (grid 1 /
  1.5 / 2), FETCH_PERIOD_S 30 (the page refreshes every ~50 wall-s), MAX_STALE_SIM_S 20 (the page's ~10 sim-s plus one
  fetch period; the replay with the data 15 sim-s older lost 6% of the making).
"""
from __future__ import annotations

import fcntl
import json
import math
import os
import re
import threading
import time
import urllib.request
from typing import Any, Callable

from research_v633_deep_layer import SIDE_BUY, SIDE_SELL

V70_FUNDAMENTAL_VERSION = "fundamental_anchor_v7_0"

FEED_URL = "http://84.32.70.8:9091/metrics/books"   # the validator's public metrics page (GET only)
CACHE_DIR = "/tmp/orthoxplus_fundamental"           # one cache per host, shared by every agent on it
FETCH_PERIOD_S = 30.0       # OBSERVED: the page refreshes every ~50 wall-s; one fetch per 30 s per host
FETCH_TIMEOUT_S = 20.0
POLL_S = 5.0                # how often an agent re-reads the shared cache
MAX_STALE_SIM_S = 20.0      # OBSERVED: the replay with the data 15 sim-s older lost 6% making; the feed runs <= ~17
THETA_Z = 2.0               # STRUCTURAL: two standard deviations of the published value's drift + the half-spread
TOWARD_DEPTH_FACTOR = 0.5   # OBSERVED: the replay grid 0.25 / 0.5 (0.5 kept the making)
TOWARD_BOUND_FACTOR = 2.0   # OBSERVED: the replay grid 1 / 1.5 / 2

_FP_PREFIX = 'book_gauges{book_gauge_name="fundamental_price",'
_BOOKS_PREFIX = "books{"
_LABEL = re.compile(r'(\w+)="([^"]*)"')
_SIM_ID_LEN = 13            # "20260929_2015": the page's sim_id label and the simulation's log directory


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _labels_value(line: str) -> tuple[dict[str, str], str] | None:
    head, sep, value = line.rpartition("}")
    if not sep:
        return None
    return dict(_LABEL.findall(head)), value.strip()


def parse_page(text: Any) -> dict[str, Any] | None:
    """The page's fundamental per book, its simulation and the sim time of its order books; None for a page that
    carries no fundamental or no sim time (a page that cannot be used).  Only the fundamental lines and one books line
    are parsed (a prefix test per line), so a 0.9 MB page costs a few milliseconds."""
    if not isinstance(text, str):
        return None
    fp: dict[int, float] = {}
    sim_id = None
    sim_ts = None
    for line in text.splitlines():
        if line.startswith(_FP_PREFIX):
            lv = _labels_value(line)
            if lv is None:
                continue
            labels, value = lv
            v = _finite(value)
            try:
                book = int(labels.get("book_id", ""))
            except ValueError:
                continue
            if v is not None and v > 0.0:
                fp[book] = v
                sim_id = sim_id or labels.get("sim_id")
        elif sim_ts is None and line.startswith(_BOOKS_PREFIX):
            lv = _labels_value(line)
            if lv is None:
                continue
            labels, _value = lv
            try:
                sim_ts = int(labels.get("timestamp", ""))
            except ValueError:
                sim_ts = None
            sim_id = sim_id or labels.get("sim_id")
    if not fp or sim_ts is None or sim_ts <= 0:
        return None
    return {"sim_ts_ns": sim_ts, "sim_id": sim_id, "fp": fp}


def gap_bps(fundamental: Any, mid: Any) -> float | None:
    """How far the fundamental sits from the mid, in bps (positive: above the mid)."""
    f, m = _finite(fundamental), _finite(mid)
    if f is None or m is None or f <= 0.0 or m <= 0.0:
        return None
    return math.log(f / m) * 1e4


def sigma_s_bps(fp_sigma: Any, duration_ns: Any) -> float | None:
    """The fundamental's diffusion per sqrt(sim-second), in bps: the simulator's sigma is over the whole simulation
    (its clock runs 0 -> 1 across ``duration``)."""
    s, d = _finite(fp_sigma), _finite(duration_ns)
    if s is None or d is None or s <= 0.0 or d <= 0.0:
        return None
    return s / math.sqrt(d / 1e9) * 1e4


def theta_bps(sigma_s: Any, staleness_s: Any, half_spread_bps: Any, *, z: Any = THETA_Z) -> float | None:
    """theta = z x sqrt(sigma_s^2 x staleness + half_spread^2); None when it cannot be formed."""
    s, t, h, zz = _finite(sigma_s), _finite(staleness_s), _finite(half_spread_bps), _finite(z)
    if s is None or t is None or h is None or zz is None or s < 0.0 or zz <= 0.0:
        return None
    return zz * math.sqrt(s * s * max(t, 0.0) + h * h)


def same_simulation(page_sim_id: Any, sim_id: Any) -> bool:
    """A page of another simulation never anchors a book; an unread id on either side is not a mismatch."""
    a = str(page_sim_id or "").strip()[:_SIM_ID_LEN]
    b = str(sim_id or "").strip()[:_SIM_ID_LEN]
    return not a or not b or a == b


def book_anchor(snapshot: Any, book_id: Any, bid: Any, ask: Any, *, state_ts_ns: Any, sim_id: Any, fp_sigma: Any,
                duration_ns: Any) -> tuple[str | None, str, float | None, float | None]:
    """(toward side or None, reason, gap bps, theta bps) for one book in one state.  Reasons: no_feed, other_sim,
    stale, no_fundamental, no_book, no_theta, below_theta, armed."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("fp"), dict):
        return None, "no_feed", None, None
    if not same_simulation(snapshot.get("sim_id"), sim_id):
        return None, "other_sim", None, None
    page_ts, now_ts = _finite(snapshot.get("sim_ts_ns")), _finite(state_ts_ns)
    if page_ts is None or now_ts is None or now_ts <= 0.0:
        return None, "stale", None, None
    staleness = max(0.0, (now_ts - page_ts) / 1e9)
    if staleness > MAX_STALE_SIM_S:
        return None, "stale", None, None
    fp = snapshot["fp"].get(book_id)
    if fp is None:
        try:
            fp = snapshot["fp"].get(str(int(book_id)))
        except (TypeError, ValueError):
            fp = None
    b, a = _finite(bid), _finite(ask)
    if fp is None:
        return None, "no_fundamental", None, None
    if b is None or a is None or b <= 0.0 or a <= b:
        return None, "no_book", None, None
    mid = 0.5 * (a + b)
    gap = gap_bps(fp, mid)
    th = theta_bps(sigma_s_bps(fp_sigma, duration_ns), staleness, 0.5 * (a - b) / mid * 1e4)
    if gap is None or th is None:
        return None, "no_theta", gap, th
    if abs(gap) < th:
        return None, "below_theta", gap, th
    return (SIDE_BUY if gap > 0.0 else SIDE_SELL), "armed", gap, th


def level0_depths(eff_side: Any, depth: Any, toward: Any, *, factor: Any = TOWARD_DEPTH_FACTOR) -> Any:
    """Each side's level-0 distance (ticks) on an anchored book: the toward side at ``depth x factor``, the other side
    at the full depth.  No toward side, or a depth or factor that cannot be read, leaves ``eff_side`` as it is."""
    d, f = _finite(depth), _finite(factor)
    if toward not in (SIDE_BUY, SIDE_SELL) or d is None or f is None or not (0.0 < f <= 1.0) or d <= 0.0:
        return eff_side
    return {s_: (d * f if s_ == toward else d) for s_ in (SIDE_BUY, SIDE_SELL)}


def side_bound(side: Any, bound: Any, toward: Any, *, factor: Any = TOWARD_BOUND_FACTOR) -> Any:
    """The inventory bound a side is judged against: ``bound x factor`` on the side toward the fundamental."""
    b, f = _finite(bound), _finite(factor)
    if toward is None or side != toward or b is None or f is None or not (1.0 <= f <= 4.0):
        return bound
    return b * f


class SharedFeed:
    """The page's fundamental for every agent on the host: a daemon thread re-reads a cache file every ``poll_s`` and,
    when the cache is older than ``period_s``, the one process that wins an exclusive non-blocking lock fetches the page,
    parses it and replaces the cache atomically.  ``snapshot()`` never blocks and never raises."""

    def __init__(self, *, url: str = FEED_URL, cache_dir: str = CACHE_DIR, period_s: float = FETCH_PERIOD_S,
                 poll_s: float = POLL_S, timeout_s: float = FETCH_TIMEOUT_S,
                 fetch: Callable[[str, float], str] | None = None, clock: Callable[[], float] = time.time) -> None:
        self.url, self.cache_dir = url, cache_dir
        self.period_s, self.poll_s, self.timeout_s = float(period_s), float(poll_s), float(timeout_s)
        self._fetch = fetch or self._http_get
        self._clock = clock
        self._snap: dict[str, Any] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.fetches = 0
        self.fetch_errors = 0
        self.reads = 0
        self.read_errors = 0
        self.last_error: str | None = None

    @staticmethod
    def _http_get(url: str, timeout: float) -> str:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "ignore")

    def _paths(self) -> tuple[str, str]:
        return os.path.join(self.cache_dir, "books.json"), os.path.join(self.cache_dir, "fetch.lock")

    def _read_cache(self) -> dict[str, Any] | None:
        path, _lock = self._paths()
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return None
        except Exception as exc:                                    # a torn or foreign file: not usable
            self.read_errors += 1
            self.last_error = "read: %s" % type(exc).__name__
            return None
        if not isinstance(data, dict) or not isinstance(data.get("fp"), dict) or _finite(data.get("wall")) is None:
            return None
        data["fp"] = {int(k): float(v) for k, v in data["fp"].items()}
        return data

    def _write_cache(self, data: dict[str, Any]) -> None:
        path, _lock = self._paths()
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({**data, "fp": {str(k): v for k, v in data["fp"].items()}}, fh)
        os.replace(tmp, path)

    def step(self) -> None:
        """One cycle: refresh the cache when it is due and this process holds the lock, then load it."""
        os.makedirs(self.cache_dir, exist_ok=True)
        now = float(self._clock())
        cached = self._read_cache()
        if cached is None or now - float(cached["wall"]) >= self.period_s:
            _path, lock_path = self._paths()
            with open(lock_path, "a") as lock:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held = True
                except OSError:
                    held = False                                      # another agent is fetching
                if held:
                    try:
                        cached = self._read_cache()                   # it may have just been refreshed
                        if cached is None or now - float(cached["wall"]) >= self.period_s:
                            self.fetches += 1
                            try:
                                page = parse_page(self._fetch(self.url, self.timeout_s))
                            except Exception as exc:
                                page = None
                                self.last_error = "fetch: %s" % type(exc).__name__
                            if page is None:
                                self.fetch_errors += 1
                            else:
                                self._write_cache({**page, "wall": now})
                    finally:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            cached = self._read_cache()
        if cached is not None:
            self.reads += 1
            self._snap = cached

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as exc:
                self.last_error = "step: %s" % type(exc).__name__
            self._stop.wait(self.poll_s)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="v70-fundamental", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict[str, Any] | None:
        return self._snap

    def status(self) -> dict[str, Any]:
        snap = self._snap or {}
        wall = _finite(snap.get("wall"))
        return {
            "fetches": self.fetches, "fetch_errors": self.fetch_errors, "reads": self.reads,
            "read_errors": self.read_errors, "last_error": self.last_error,
            "cache_age_s": (round(float(self._clock()) - wall, 1) if wall is not None else None),
            "sim_ts_ns": snap.get("sim_ts_ns"), "sim_id": snap.get("sim_id"), "books": len(snap.get("fp") or {}),
        }
