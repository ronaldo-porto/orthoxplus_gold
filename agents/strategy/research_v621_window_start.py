# SPDX-License-Identifier: MIT
"""v6.21 S2: where this UID's volume window started, read once from its own agent logs -- research_v621_window_start.

Why (mainnet 10-08 JST; scratchpad k36/window): v6.21 keeps a young book young until its window can roll off and aims
its spending at that roll-off; both need the time the window started, which the agent cannot ask the validator for.  A
UID launched before v6.21 (172, 10, 22, 209) has no saved pace state, but its own first report says it: the read-only
fleet run gave 172 t_w 17,744 sim-s, 10 18,964, 22 37,320, 209 36,889 (its two all-empty starts, the later one), and
every start of the aged four (237, 187, 104, 251) in this simulation already carried volume -> unknown, v6.20 exactly.
0 fills before t_w on any of the four; every book first filled 10-1,504 sim-s after it.  Cost 0.04-0.7 MB, 0.2-2.5 ms.

The rule (STRUCTURAL -- the validator's per-UID volume sums and the agent's own first report):

* A process's first report (V62_STATE at tick 1) records how many books its pace line saw and how many of them on an
  EMPTY window (v6.15+ ``v66.pace_line.launch.books``; v6.21 ``v66.pace_line.room.first_seen`` / ``first_empty``, which a
  restored launching set does not inflate).  When every book was seen and every one was empty, the validator held no
  volume of this UID at that state: every unit in the window now was traded at or after its sim time (the tick-1 TIMING
  ``timestamp``).  t_w = that time for the LATEST such start of this simulation (``V504_SESSION_OWNER.own_file`` at tick
  0 names it) -- the tightest bound; a re-registration or a full roll-off empties the window again.
* Unknown (v6.20 behaviour) when: no start of this simulation can be read; every start already shows volume (an aged
  UID); a later start cannot be read within the byte budget (it might hide a reset); the logs predate v6.15.
* Carry: when every start of this simulation shows volume (or none ran before this process), the previous simulation's
  anchor re-keyed the validator's way (new = 0 - (old_end - t), old_end the old run's length: its last state is no
  later) -- one seam back only, an anchor two runs old is at least one assessment period old.  A carried start already
  past its earliest roll-off is a window that rolls: unknown, v6.20.
* Order of starts: the first record's wall clock (run ids are local times and repeat at a DST fall-back).  Only the
  head of each of the UID's own rotated logs is read (to its first V62_STATE, a foreign simulation, or the budget); the
  running process's own file is skipped.

STRUCTURAL: the validator's volume window and its re-key at a new simulation; no fitted constant.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import time
from typing import Any

from research_session_state import sanitize_key

V621_WINDOW_START_VERSION = "window_start_v6_21"
LOG_NAME = re.compile(r"^strategy1_research_agent_(\d+)_(\d{8}_\d{6})\.jsonl(\.gz)?$")
OWN_FILE = re.compile(r"^research_session_.*?_(?:\d+|na)_(.+)\.uid(?:\d+|na)\.json$")
SESSION_NAME = re.compile(r"^research_session_.*?_(?:\d+|na)_(.+)\.uid(\d+)\.json$")
SAMPLING_NS = 600_000_000_000          # scoring.activity.trade_volume_sampling_interval: the validator's volume key
ASSESSMENT_NS = 86_400_000_000_000     # scoring.activity.trade_volume_assessment_period
HEAD_BUDGET = 4 << 20                  # bytes read from one log at most
TOTAL_BUDGET = 32 << 20                # bytes read from every log together at most

K_ALL_EMPTY = "ALL_EMPTY"
K_VOLUME = "VOLUME"
K_OTHER_SIM = "OTHER_SIM"
K_NO_REPORT = "NO_REPORT"              # the process never reported (the file ends before a V62_STATE): no information
K_UNREADABLE = "UNREADABLE"            # no V62_STATE within the budget, or no first-report counters
K_CONTINUATION = "CONTINUATION"        # no header, or a first V62_STATE after tick 1: not a process start

KNOWN = "KNOWN"
KNOWN_CARRIED = "KNOWN_CARRIED"
KNOWN_CARRIED_ROLLING = "KNOWN_CARRIED_ROLLING"
UNKNOWN_NO_SIM = "UNKNOWN_NO_SIM"
UNKNOWN_NO_DIR = "UNKNOWN_NO_DIR"
UNKNOWN_BUDGET = "UNKNOWN_BUDGET"
UNKNOWN_NO_READABLE_START = "UNKNOWN_NO_READABLE_START"
UNKNOWN_NO_EMPTY_START = "UNKNOWN_NO_EMPTY_START"
UNKNOWN_GAP_AFTER_ANCHOR = "UNKNOWN_GAP_AFTER_ANCHOR"
UNKNOWN_CARRY_UNVERIFIED = "UNKNOWN_CARRY_UNVERIFIED"   # the previous log's simulation is not the one just before
UNKNOWN_ERROR = "UNKNOWN_ERROR"
KNOWN_STATUSES = frozenset({KNOWN, KNOWN_CARRIED, KNOWN_CARRIED_ROLLING})
RETRY_STATUSES = frozenset({UNKNOWN_NO_SIM, UNKNOWN_NO_DIR, UNKNOWN_BUDGET, UNKNOWN_ERROR})   # a later start reads again


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def sim_key(sim_id: Any) -> str | None:
    """The simulation's part of the session file name (what V504_SESSION_OWNER.own_file carries)."""
    text = "" if sim_id is None else str(sim_id).strip()
    return sanitize_key(text) if text else None


def _first_report(rec: dict) -> tuple[int | None, int | None]:
    """(books seen, books seen on an empty window) at a process's first report."""
    v66 = rec.get("v66") if isinstance(rec.get("v66"), dict) else {}
    line = v66.get("pace_line") if isinstance(v66.get("pace_line"), dict) else {}
    room = line.get("room") if isinstance(line.get("room"), dict) else {}
    if room.get("first_seen") is not None and room.get("first_empty") is not None:
        return _int(room.get("first_seen")), _int(room.get("first_empty"))      # v6.21: not inflated by a restore
    launch = line.get("launch") if isinstance(line.get("launch"), dict) else None
    return _int(line.get("books")), (_int(launch.get("books")) if launch is not None else None)


def read_head(path: str, sim: str | None = None, *, budget: int = HEAD_BUDGET) -> dict[str, Any]:
    """The head facts of one agent log: the process header, its simulation (tick 0), its first state's sim time (tick
    1) and the first V62_STATE's counters.  Stops at that record, at another simulation, or at the byte budget."""
    out: dict[str, Any] = {"path": path, "bytes": 0, "header": False, "sim": None, "first_ts": None,
                           "first_wall_ns": None, "start_wall_ns": None, "v62_tick": None, "books": None,
                           "empty": None, "complete": False}
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rb") as fh:
            while out["bytes"] < budget:
                line = fh.readline(max(1, budget - out["bytes"]))
                if not line:
                    out["complete"] = True                       # the file ends inside the budget
                    break
                out["bytes"] += len(line)
                if not line.endswith(b"\n"):
                    continue                                     # a partial line (being written, or past the budget)
                if out["start_wall_ns"] is None and b'"wall_time_ns"' in line:
                    try:
                        out["start_wall_ns"] = _int(json.loads(line).get("wall_time_ns"))
                    except (ValueError, TypeError, AttributeError):
                        pass
                if b'"DEBUG_CONFIG"' in line or b'"RESEARCH_CONFIG"' in line:
                    out["header"] = True
                    continue
                if out["sim"] is None and b'"V504_SESSION_OWNER"' in line:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    m = OWN_FILE.match(str(rec.get("own_file") or ""))
                    out["sim"] = m.group(1) if m else None
                    if sim is not None and out["sim"] is not None and out["sim"] != sim:
                        break                                    # another simulation: nothing more to read here
                    continue
                if out["first_ts"] is None and b'TIMING"' in line and b'"timestamp"' in line:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("type") in ("TIMING", "RESPOND_TIMING") and _int(rec.get("timestamp")) is not None:
                        out["first_ts"] = _int(rec.get("timestamp"))
                        out["first_wall_ns"] = _int(rec.get("wall_time_ns"))
                    continue
                if b'"V62_STATE"' in line:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(rec, dict) or rec.get("type") != "V62_STATE":
                        continue
                    out["v62_tick"] = rec.get("tick")
                    out["books"], out["empty"] = _first_report(rec)
                    break
    except Exception as exc:                                     # OSError, EOFError, zlib.error: no information
        out["error"] = type(exc).__name__
    return out


def classify(head: dict[str, Any], sim: str, n_books: int) -> str:
    """What one log's head says about this simulation's window (K_*)."""
    if head.get("error") and head.get("v62_tick") is None:
        return K_UNREADABLE
    if head.get("sim") is not None and head.get("sim") != sim:
        return K_OTHER_SIM
    if head.get("v62_tick") is None:
        return K_NO_REPORT if head.get("complete") else K_UNREADABLE
    if not head.get("header") or head.get("v62_tick") != 1 or head.get("first_ts") is None:
        return K_CONTINUATION
    if head.get("sim") is None or head.get("books") is None or head.get("empty") is None:
        return K_UNREADABLE
    if int(head["books"]) == int(n_books) and int(head["empty"]) == int(n_books):
        return K_ALL_EMPTY
    return K_VOLUME


def shift_for_new_sim(t_ns: int, old_end_ns: int, new_start_ns: int = 0) -> int:
    """A new simulation re-keys the validator's volume history: new = new_start - (old_end - t) (new_start 0 for a fresh
    run, old_end the old run's last clock)."""
    return int(new_start_ns) - (int(old_end_ns) - int(t_ns))


def _scan(log_dir: str, uid: Any, exclude: str | None) -> list[tuple[str, str]]:
    own = _int(uid)
    skip = os.path.abspath(exclude) if exclude else None
    paths: list[tuple[str, str]] = []
    for ent in os.scandir(log_dir):
        m = LOG_NAME.match(ent.name)
        if not m or own is None or int(m.group(1)) != own or not ent.is_file():
            continue
        if skip is not None and os.path.abspath(ent.path) == skip:
            continue                                             # the running process's own file
        paths.append((m.group(2), ent.path))
    return paths


def _heads(paths: list[tuple[str, str]], sim: str, n_books: int, budget: int, left: list[int]) -> list | None:
    """Every log's head classified against ``sim``, in process order (the first record's wall clock); None once the
    total budget is spent."""
    heads = []
    for run_id, path in paths:
        if left[0] <= 0:
            return None
        h = read_head(path, sim, budget=min(int(budget), left[0]))
        left[0] -= int(h.get("bytes") or 0)
        if left[0] <= 0 and not h.get("complete") and h.get("v62_tick") is None:
            return None                                          # the total ran out inside this head
        h["run_id"] = run_id
        h["kind"] = classify(h, sim, n_books)
        heads.append(h)
    heads.sort(key=lambda h: (h.get("start_wall_ns") or 0, h["run_id"]))
    return heads


def _anchor(heads: list, sim: str) -> tuple[str, dict | None]:
    chain = [h for h in heads if h["kind"] in (K_ALL_EMPTY, K_VOLUME, K_UNREADABLE, K_CONTINUATION)]
    anchor_i = None
    for i, h in enumerate(chain):
        if h["kind"] == K_ALL_EMPTY:
            anchor_i = i                                         # the latest all-empty start wins
    if not any(h["kind"] in (K_ALL_EMPTY, K_VOLUME) for h in chain):
        return UNKNOWN_NO_READABLE_START, None
    if anchor_i is None:
        return UNKNOWN_NO_EMPTY_START, None
    if any(h["kind"] == K_UNREADABLE for h in chain[anchor_i + 1:]):
        return UNKNOWN_GAP_AFTER_ANCHOR, None                    # a later start we cannot read might be a reset
    return KNOWN, chain[anchor_i]


def _carries(heads: list, sim: str, status: str) -> bool:
    """May the previous simulation's anchor stand for this one?  Only when no start of this simulation emptied the
    window: every one of its starts shows volume, or none ran before this process; one this head cannot read might
    have been a reset."""
    if status not in (UNKNOWN_NO_EMPTY_START, UNKNOWN_NO_READABLE_START):
        return False
    return not any(h.get("sim") == sim and h["kind"] in (K_ALL_EMPTY, K_UNREADABLE) for h in heads)


def session_sims(log_dir: str, uid: Any) -> list[str]:
    """The simulations this UID kept a session in (its session files in the log dir), oldest first: the one just before
    this simulation is the only one a carry may come from."""
    own = _int(uid)
    out = set()
    if own is None:
        return []
    for ent in os.scandir(log_dir):
        m = SESSION_NAME.match(ent.name)
        if m and int(m.group(2)) == own:
            out.add(m.group(1))
    return sorted(out)


def _files(heads: list) -> list[dict[str, Any]]:
    return [{k: h.get(k) for k in ("run_id", "kind", "sim", "first_ts", "v62_tick", "books", "empty", "bytes")}
            for h in heads]


def derive_window_start(log_dir: str, uid: Any, sim_id: Any, n_books: int, *, exclude: str | None = None,
                        budget: int = HEAD_BUDGET, total_budget: int = TOTAL_BUDGET, old_end_ns: int | None = None,
                        now_ns: int | None = None) -> dict[str, Any]:
    """Once at start-up: the sim time at which every book of this UID was last seen empty in this simulation (or, every
    start of it showing volume, the previous one's re-keyed onto this clock), with how it was found.  Never raises."""
    t0 = time.perf_counter()
    out: dict[str, Any] = {"version": V621_WINDOW_START_VERSION, "status": UNKNOWN_ERROR, "t_w_ns": None,
                           "anchor": None, "files": [], "bytes": 0, "ms": 0.0}
    try:
        sim = sim_key(sim_id)
        if sim is None:
            out["status"] = UNKNOWN_NO_SIM
            return out
        try:
            paths = _scan(str(log_dir), uid, exclude)
        except OSError:
            out["status"] = UNKNOWN_NO_DIR
            return out
        left = [int(total_budget)]
        heads = _heads(paths, sim, int(n_books), budget, left)
        out["bytes"] = int(total_budget) - left[0]
        if heads is None:
            out["status"] = UNKNOWN_BUDGET
            return out
        out["files"] = _files(heads)
        status, anchor = _anchor(heads, sim)
        out["status"] = status
        if anchor is not None:
            out["t_w_ns"], out["anchor"] = int(anchor["first_ts"]), anchor["run_id"]
        elif old_end_ns and _carries(heads, sim, status):
            # every start of this simulation shows volume (or this process is its first): the previous simulation's
            # anchor, re-keyed the validator's way -- one seam back only
            here = [i for i, h in enumerate(heads) if h.get("sim") == sim]
            prev = [h for h in heads[:(here[0] if here else len(heads))] if h["kind"] == K_OTHER_SIM and h.get("sim")]
            before = [s for s in session_sims(str(log_dir), uid) if s < sim]
            if prev and (not before or before[-1] != prev[-1]["sim"]):
                # a process that ran through a whole simulation leaves no head in it: the UID's session files show the
                # simulation just before this one, and an anchor from any other is no carry
                out["status"] = UNKNOWN_CARRY_UNVERIFIED
                out["carry"] = {"sim": prev[-1]["sim"], "status": UNKNOWN_CARRY_UNVERIFIED,
                                "just_before": before[-1] if before else None}
            elif prev:
                prev_sim = prev[-1]["sim"]
                prev_heads = _heads(paths, prev_sim, int(n_books), budget, left)
                out["bytes"] = int(total_budget) - left[0]
                if prev_heads is None:
                    out["status"] = UNKNOWN_BUDGET
                    return out
                p_status, p_anchor = _anchor(prev_heads, prev_sim)
                out["carry"] = {"sim": prev_sim, "status": p_status}
                if p_anchor is not None:
                    t_w = shift_for_new_sim(int(p_anchor["first_ts"]), int(old_end_ns))
                    out.update(status=KNOWN_CARRIED, t_w_ns=t_w, anchor=p_anchor["run_id"])
                    key0 = (int(p_anchor["first_ts"]) // SAMPLING_NS) * SAMPLING_NS
                    if now_ns is not None and int(now_ns) > shift_for_new_sim(key0, int(old_end_ns)) + ASSESSMENT_NS:
                        out["status"] = KNOWN_CARRIED_ROLLING          # past its first possible roll-off
        if out["t_w_ns"] is not None:
            key0 = (int(out["t_w_ns"]) // SAMPLING_NS) * SAMPLING_NS
            out["first_key_ns"] = key0
            out["no_rolloff_before_ns"] = key0 + ASSESSMENT_NS     # the validator prunes key k once now > k + 86,400 s
    except Exception as exc:                                     # never raises: unknown is v6.20 behaviour
        out.update(status=UNKNOWN_ERROR, t_w_ns=None, error=type(exc).__name__)
    finally:
        out["ms"] = round((time.perf_counter() - t0) * 1e3, 2)
    return out


def window_of(result: Any) -> tuple[int | None, str | None]:
    """(t_w or None, the status a session file keeps): a young window's start with its status; an unknown one -- or a
    carried start already past its earliest roll-off, a window that rolls (v6.20 then) -- with its status; a failure a
    later start should read again (no simulation, no directory, budget, error) with None."""
    if not isinstance(result, dict):
        return None, None
    status = result.get("status")
    if status in RETRY_STATUSES or status is None:
        return None, None
    t_w = _int(result.get("t_w_ns")) if status in (KNOWN, KNOWN_CARRIED) else None
    return t_w, str(status)


__all__ = ["KNOWN", "KNOWN_CARRIED", "KNOWN_CARRIED_ROLLING", "KNOWN_STATUSES", "RETRY_STATUSES", "V621_WINDOW_START_VERSION",
           "classify", "derive_window_start", "read_head", "shift_for_new_sim", "sim_key", "window_of"]
