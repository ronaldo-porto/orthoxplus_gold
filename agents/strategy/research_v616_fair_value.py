# SPDX-License-Identifier: MIT
"""v6.16: the deep side that trades toward the published fundamental is centred on it.

Why (mainnet 10-05/10-06 JST; scratchpad k19/):

* The simulator's background traders converge on a hidden fundamental (its STA agents weight it 1.4 against a chartist
  1.9 and noise 0.7); the validator publishes it for every book on its public metrics page.  On the new simulation
  (20261005_1128) the mid moves toward it by 0.20 x the gap in 60 sim-s and 0.48 x in 300 sim-s.
* Our deep ladder is centred on the mid, so its fills sit symmetrically around the fundamental: on the new simulation
  fills more than 15 bps against it captured -65 per 1M (alpha -1,157 per 1M), fills toward it 112-673 per 1M (alpha
  +295..+1,909).  The makers that quote around it out-earn us on both halves of the pay in both regimes seen (previous
  simulation: their deep fills' 60-s markout +3.7..+6.0 bps vs our -2.5..-3.1; making 42-45 per 1M vs our 20-28).
* Replayed with the validator's arithmetic (k19/vm616.py; v6.14 = the as-built ladder, making share per window):
  previous simulation (quiet trend, persistent gap; 7 windows with the fundamental) +19.0%, ahead in 6 of 7, the 3
  partly covered windows +26.7%, alpha per 1M -379 vs -634; new simulation (the gap flips ~12 times per book per hour)
  +41.8% over sim 658-3,921 (opening burst +21%, after it +191%), alpha +721 vs +97.
* Refuted on the same data: a price limit on the other side (no order beyond the fundamental, at 0 / -5 / -15 bps,
  alone, with this centre or only while the side has the capture surplus) won on the new simulation (+36..+89%) and
  lost on the previous one (-78..+6%): with a persistent gap it pulls the other side out of the market and the book
  goes one-sided (v7.0's failure).  Only the centre is positive in both.

The rule (one switch, ``research_v616_fair_value``), on a book with a fresh fundamental of this simulation:

* The side that trades TOWARD the fundamental is centred on it, never more than one level-0 depth from the mid: its
  level 0, its ladder and its vacuum order move toward the market by min(|gap|, depth), never inside the touch; the
  other side keeps the mid.  Resting orders are judged by v6.3.3's band around their side's centre.
* Without a fresh page of this simulation the book is exactly v6.15.  Sizes, bounds and pacing are unchanged.

STRUCTURAL: the fundamental is the simulator's own reversion target; the cap is the layer's own depth.  The feed (one
GET per FETCH_PERIOD_S per host through a shared cache) and its constants are v7.0's (OBSERVED there).
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

from research_v633_deep_layer import REPRICE_HIGH, REPRICE_LOW, SIDE_BUY, SIDE_SELL

V616_FAIR_VALUE_VERSION = "fair_value_v6_16"
FEED_URL = "http://84.32.70.8:9091/metrics/books"   # the validator's public metrics page (GET only)
CACHE_DIR = "/tmp/orthoxplus_fundamental"           # one cache per host, shared by every agent on it
FETCH_PERIOD_S = 30.0       # OBSERVED (v7.0): the page refreshes every ~50 wall-s; one fetch per 30 s per host
FETCH_TIMEOUT_S = 20.0
POLL_S = 5.0                # how often an agent re-reads the shared cache
MAX_STALE_SIM_S = 20.0      # OBSERVED (v7.0): the page's ~10 sim-s plus one fetch period

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


def same_simulation(page_sim_id: Any, sim_id: Any) -> bool:
    """A page of another simulation never anchors a book; an unread id on either side is not a mismatch."""
    a = str(page_sim_id or "").strip()[:_SIM_ID_LEN]
    b = str(sim_id or "").strip()[:_SIM_ID_LEN]
    return not a or not b or a == b


def book_fundamental(snapshot: Any, book_id: Any, bid: Any, ask: Any, *, state_ts_ns: Any,
                     sim_id: Any) -> tuple[float | None, str]:
    """(fundamental, reason) for one book in one state; the fundamental is None unless it is usable.
    Reasons: no_feed, other_sim, stale, no_fundamental, no_book, armed."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("fp"), dict):
        return None, "no_feed"
    if not same_simulation(snapshot.get("sim_id"), sim_id):
        return None, "other_sim"
    page_ts, now_ts = _finite(snapshot.get("sim_ts_ns")), _finite(state_ts_ns)
    if page_ts is None or now_ts is None or now_ts <= 0.0:
        return None, "stale"
    if max(0.0, (now_ts - page_ts) / 1e9) > MAX_STALE_SIM_S:
        return None, "stale"
    fp = snapshot["fp"].get(book_id)
    if fp is None:
        try:
            fp = snapshot["fp"].get(int(book_id))
        except (TypeError, ValueError):
            fp = None
    if fp is None:
        try:
            fp = snapshot["fp"].get(str(int(book_id)))
        except (TypeError, ValueError):
            fp = None
    f, b, a = _finite(fp), _finite(bid), _finite(ask)
    if f is None or f <= 0.0:
        return None, "no_fundamental"
    if b is None or a is None or b <= 0.0 or a <= b:
        return None, "no_book"
    return f, "armed"


def side_centres(mid: Any, fundamental: Any, depth: Any, tick: Any) -> dict[str, float] | None:
    """Each side's centre: the side that trades toward the fundamental is centred on it, never more than ``depth`` ticks
    from the mid; the other side keeps the mid.  None when an input is unusable."""
    m, f, d, t = _finite(mid), _finite(fundamental), _finite(depth), _finite(tick)
    if m is None or f is None or d is None or t is None or m <= 0.0 or f <= 0.0 or d < 0.0 or t <= 0.0:
        return None
    cap = d * t
    shift = max(-cap, min(cap, f - m))
    toward = SIDE_BUY if shift > 0.0 else SIDE_SELL
    out = {SIDE_BUY: m, SIDE_SELL: m}
    if shift != 0.0:
        out[toward] = m + shift
    return out


def needs_reprice(price: Any, centre: Any, depth: Any, tick: Any, *, low: float = REPRICE_LOW,
                  high: float = REPRICE_HIGH) -> bool:
    """v6.3.3's band, around the order's side's centre: repriced when it left [low, high] x depth from it."""
    dist = abs(float(price) - float(centre)) / float(tick)
    return dist < low * float(depth) or dist > high * float(depth)


class SharedFeed:
    """The page's fundamental for every agent on the host: a daemon thread re-reads a cache file every ``poll_s`` and,
    when the cache is older than ``period_s``, the one process that wins an exclusive non-blocking lock fetches the page,
    parses it and replaces the cache atomically.  ``snapshot()`` never blocks and never raises.  (v7.0's feed.)"""

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
            self._thread = threading.Thread(target=self._run, name="v616-fair-value", daemon=True)
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
