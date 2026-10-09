# SPDX-License-Identifier: MIT
"""v6.24: S1's auto mode keys on the making basis the validator pays -- research_v624_pay_basis.

Why (10-09/10 JST; scratchpad k54: the 0.6.3 switch-over watch, k48: the calibrated 0.6.3 replay):

* SN79 0.6.3 accumulates and publishes BOTH making bases at every setting and pays ONE: the validator's launch flag
  ``--scoring.debeta.making_basis`` (argparse choices captured | realized, upstream default realized) picks it, and
  upstream reward.py (making_basis_sums) pays the realized sums only when it reads 'realized' -- any other value pays
  the captured ones.  The release says which bases exist; the flag says which one pays.
* SN79's only validator with a permit (UID 0, 84.32.70.8) restarted on 0.6.3 at ~23:50 JST 10-09 (its simulation went
  on) with the flag at CAPTURED: its public config page (/metrics/validator, ~29 KB) carries
  config_scoring_debeta_making_basis="captured" in the labels of its neuron_info_info line, and the board's
  debeta_making_basis flag is 0.0 for every UID.  The realized basis is published, not paid.
* v6.23's S1 keys auto on the release (the state's version, 63 from a 0.6.3 validator), so from 23:55:27 JST it has
  withheld the deep side trading against each fundamental on all eleven UIDs while the validator still pays captured:
  a rule that only pays under the realized basis, costing captured making (the k48 replay -7.6%, live about -6%).

The rule (one switch, ``research_v624_pay_basis``, default on; off, the build is v6.23):

* S4: S1's auto mode reads the paying basis the validator publishes -- realized: active, captured: inactive, whatever
  its release.  While this host has never read it (no cache, the page unreadable since the first attempt) auto keeps
  v6.23's rule (a 0.6.3 validator on): an unknown basis is v6.23 exactly.  Modes 1 and 0 never read the page and are
  unchanged; so is everything S1 does while active.
* The basis is the value of the label config_scoring_debeta_making_basis anywhere on the validator's config page, read
  as upstream reads the flag: 'realized' (case and surrounding space aside) is realized, any other non-empty value
  captured; a page without the label or with an empty one, or no page at all, reads nothing.
* One reading per host (v6.16's SharedFeed): a daemon thread per agent re-reads a per-host cache file every POLL_S, and
  when the last ATTEMPT is FETCH_PERIOD_S old the one process that wins an exclusive non-blocking lock GETs the page and
  replaces the cache atomically.  The cache keeps the LAST KNOWN basis: a failed GET or a page without the label never
  replaces it, it only stamps the attempt (so a failing page is retried once per period, not at every poll), and the
  file outlives the agents -- a restarted agent decides its first request on the host's last known basis.  No response
  ever waits on the page.

STRUCTURAL: the validator's own published paying basis (its launch flag, read as its reward.py reads it); upstream's two
basis names; one cache per host; FETCH_PERIOD_S (the flag moves only with a validator restart, so one GET per host per
5 min bounds the lag at one period and one poll) and POLL_S (a tenth of it).  OBSERVED: FETCH_TIMEOUT_S (the page took
2-22 s, 10-09/10 JST).  No fitted constant.
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

from research_v623_validator_063 import MODE_AUTO, MODE_OFF, MODE_ON, parse_mode
from research_v623_validator_063 import realized_active as _v623_realized_active

V624_PAY_BASIS_VERSION = "pay_basis_v6_24"
CONFIG_URL = "http://84.32.70.8:9091/metrics/validator"   # STRUCTURAL: the validator's public config page (GET only)
CACHE_DIR = "/tmp/orthoxplus_validator_config"            # STRUCTURAL: one cache per host, shared by every agent on it
FETCH_PERIOD_S = 300.0      # STRUCTURAL: the flag moves only with a validator restart; one GET per host per 5 min
FETCH_TIMEOUT_S = 60.0      # OBSERVED: the page took 2-22 s (10-09/10 JST)
POLL_S = 30.0               # STRUCTURAL: an agent re-reads the host's cache ten times per fetch period
BASIS_CAPTURED = "captured"   # STRUCTURAL: upstream's basis names (the flag's argparse choices) --
BASIS_REALIZED = "realized"   # STRUCTURAL: -- and the one value its reward.py pays the realized sums on

_BASES = (BASIS_CAPTURED, BASIS_REALIZED)
_BASIS_LABEL = re.compile(r'(?<!\w)config_scoring_debeta_making_basis="([^"]*)"')


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def _snap_basis(snap: Any) -> str | None:
    basis = snap.get("basis") if isinstance(snap, dict) else None
    return basis if basis in _BASES else None


# ---- the page -------------------------------------------------------------------------------------------------------

def parse_basis(text: Any) -> str | None:
    """The paying basis a config page publishes: the value of its config_scoring_debeta_making_basis label -- 'realized'
    (case and surrounding space aside) is realized, any other non-empty value captured (upstream: anything but
    'realized' pays captured).  None for a page without the label, an empty value or an input that is not text."""
    if not isinstance(text, str):
        return None
    match = _BASIS_LABEL.search(text)
    if match is None:
        return None
    value = match.group(1).strip().lower()
    if not value:
        return None
    return BASIS_REALIZED if value == BASIS_REALIZED else BASIS_CAPTURED


# ---- S4: the rule ----------------------------------------------------------------------------------------------------

def reads_basis(mode: Any) -> bool:
    """S4: only S1's auto mode reads the published basis; 1 and 0 decide without it (the page is never fetched)."""
    return mode == MODE_AUTO


def realized_active(mode: Any, spec: Any, basis: Any) -> bool:
    """S4: does S1's realized rule act on this state?  on: always; off: never; auto: the paying basis the validator
    publishes when this host has read one (realized: active, captured: inactive, whatever the release), else v6.23's
    rule (a 0.6.3 validator on: the state's version >= 63)."""
    if mode == MODE_AUTO and basis in _BASES:
        return basis == BASIS_REALIZED
    return _v623_realized_active(mode, spec)


# ---- one reading per host --------------------------------------------------------------------------------------------

class SharedBasis:
    """The validator's paying basis for every agent on the host (v6.16's SharedFeed, timed by the last attempt): a daemon
    thread re-reads a cache file every ``poll_s`` and, when the last attempt is ``period_s`` old (or stamped ahead of the
    clock), the one process that wins an exclusive non-blocking lock GETs the page and replaces the cache atomically --
    with the basis it read, or, when it read none, with the last known basis and seen time and a new attempt time.
    ``start()`` loads the cache before the thread runs, so a restarted agent starts from the host's last known basis.
    ``snapshot()``, ``basis()`` and ``status()`` never block and never raise."""

    def __init__(self, *, url: str = CONFIG_URL, cache_dir: str = CACHE_DIR, period_s: float = FETCH_PERIOD_S,
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
        return os.path.join(self.cache_dir, "basis.json"), os.path.join(self.cache_dir, "fetch.lock")

    def _read_cache(self) -> dict[str, Any] | None:
        """The host's cache -- {basis: realized | captured | None, seen: wall s | None, attempt: wall s} -- or None for a
        missing, torn or foreign file (a GET is then due)."""
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
        if not isinstance(data, dict) or data.get("basis") not in (None,) + _BASES:
            return None
        attempt = _finite(data.get("attempt"))
        if attempt is None:
            return None
        return {"basis": data.get("basis"), "seen": _finite(data.get("seen")), "attempt": attempt}

    def _write_cache(self, data: dict[str, Any]) -> None:
        path, _lock = self._paths()
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)

    def _known(self, cached: dict[str, Any] | None) -> tuple[str | None, float | None]:
        """(basis, seen) to keep: the cache's, or -- when it carries none (a lost or foreign file replaced by an attempt
        that read nothing) -- this instance's own last reading.  A known basis is only ever replaced by one the page
        published."""
        for src in (cached, self._snap):
            if isinstance(src, dict) and src.get("basis") in _BASES:
                return src["basis"], _finite(src.get("seen"))
        return None, None

    def _adopt(self, cached: dict[str, Any]) -> None:
        basis, seen = self._known(cached)
        self._snap = {"basis": basis, "seen": seen, "attempt": cached["attempt"]}
        self.reads += 1

    def _due(self, cached: dict[str, Any] | None, now: float) -> bool:
        """A GET is due without a usable cache, once the last ATTEMPT (success or not) is a period old, or when it is
        stamped ahead of this clock (a clock that stepped back cannot time the period)."""
        return cached is None or not (0.0 <= now - float(cached["attempt"]) < self.period_s)

    def _refresh(self, cached: dict[str, Any] | None, now: float) -> None:
        """One GET, under the lock.  A page with the label writes its basis (seen = attempt = now); a failed GET or a page
        without it keeps the last known basis and its seen time and only stamps the attempt."""
        self.fetches += 1
        basis = None
        try:
            basis = parse_basis(self._fetch(self.url, self.timeout_s))
            if basis is None:
                self.last_error = "fetch: no basis label"
        except Exception as exc:
            self.last_error = "fetch: %s" % type(exc).__name__
        if basis is None:
            self.fetch_errors += 1
            basis, seen = self._known(cached)
        else:
            seen = now
        self._write_cache({"basis": basis, "seen": seen, "attempt": now})

    def load(self) -> None:
        """Read the host's cache once -- no GET, no lock.  Never raises."""
        try:
            cached = self._read_cache()
            if cached is not None:
                self._adopt(cached)
        except Exception as exc:
            self.last_error = "load: %s" % type(exc).__name__

    def step(self) -> None:
        """One cycle: refresh the cache when its last attempt is due and this process holds the lock, then load it."""
        os.makedirs(self.cache_dir, exist_ok=True)
        now = float(self._clock())
        cached = self._read_cache()
        if self._due(cached, now):
            _path, lock_path = self._paths()
            with open(lock_path, "a") as lock:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held = True
                except OSError:
                    held = False                                      # another agent of this host is fetching
                if held:
                    try:
                        cached = self._read_cache()                   # it may have just been refreshed
                        if self._due(cached, now):
                            self._refresh(cached, now)
                    finally:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            cached = self._read_cache()
        if cached is not None:
            self._adopt(cached)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as exc:
                self.last_error = "step: %s" % type(exc).__name__
            self._stop.wait(self.poll_s)

    def start(self) -> None:
        """Load the host's cache now, then start the thread (once per instance)."""
        if self._thread is None:
            self.load()
            self._thread = threading.Thread(target=self._run, name="v624-pay-basis", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict[str, Any] | None:
        return self._snap

    def basis(self) -> str | None:
        """The host's last known paying basis (realized | captured), or None while it has never read one."""
        return _snap_basis(self._snap)

    def status(self) -> dict[str, Any]:
        """The state row's view: GETs, failed GETs, the last error, the basis and the ages of its reading (seen) and of
        the last attempt, in wall seconds.  Never raises."""
        out: dict[str, Any] = {
            "fetches": self.fetches, "fetch_errors": self.fetch_errors, "reads": self.reads,
            "read_errors": self.read_errors, "last_error": self.last_error, "basis": _snap_basis(self._snap),
            "seen_age_s": None, "attempt_age_s": None,
        }
        try:
            snap = self._snap if isinstance(self._snap, dict) else {}
            now = float(self._clock())
            seen, attempt = _finite(snap.get("seen")), _finite(snap.get("attempt"))
            out["seen_age_s"] = round(now - seen, 1) if seen is not None else None
            out["attempt_age_s"] = round(now - attempt, 1) if attempt is not None else None
        except Exception as exc:
            out["last_error"] = "status: %s" % type(exc).__name__
        return out


__all__ = ["BASIS_CAPTURED", "BASIS_REALIZED", "CACHE_DIR", "CONFIG_URL", "FETCH_PERIOD_S", "FETCH_TIMEOUT_S",
           "MODE_AUTO", "MODE_OFF", "MODE_ON", "POLL_S", "SharedBasis", "V624_PAY_BASIS_VERSION", "parse_basis",
           "parse_mode", "reads_basis", "realized_active"]
