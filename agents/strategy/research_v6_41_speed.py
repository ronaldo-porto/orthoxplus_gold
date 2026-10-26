"""v6.41 speed (research_v641_speed; scratchpad k141 P3): reply-time changes with every decision as v6.39's.

k141 P1 (v6.39 on the k129 identity harness with the k115 stack sampler) located the reply's time; each change here
removes or moves work whose result no decision reads differently.  One switch; "if switch: new, else: the original".

  B  DEFER-FREE   the v6.38 P3 trade-parse cache holds the previous state's event lists until the next request's
                  update() clears it (its dealloc was 3.6 ms of the request): Simple queues trades_reset() in the
                  v6.29 post-reply queue.  A cache keyed by list identity: emptying it earlier changes no value.
  A  DEFER-SAVE   the session save (cadence 100 ticks; +115 ms live on that request) runs as the FIRST job of the
                  v6.29 post-reply queue instead of at the end of Research.handle: nothing between the two points
                  writes what the payload reads, and the queue always runs before the next request (flush).
  F1 MISS PATH    fast_getattr(obj, name, default) == getattr(obj, name, default) for every object; for a pydantic v2
                  model and a name its class does not define (not in any MRO __dict__, not private, not on the class)
                  it answers from the instance __dict__ / __pydantic_extra__ without raising AttributeError twice.
  D  SCAN-ONCE    (taos/im/protocol/models.py) the P5 events pass records each book's trade candidates and raw trade
                  dicts, read by trades_of and v6.32's prints filter instead of two more passes over every event.
  C  KAPPA-INC    the frozen rolling-kappa refresh rebuilt every book's observation set from the whole history on
                  each realized tick (O(history), growing ~18 h after a restart); kappa_refresh() computes the same
                  four outputs and the same cache key from the previous result plus the history tail it has not
                  seen, and runs the frozen body whenever an invariant it relies on cannot be checked.
  G  GC-HOLD      (research_direct_idle_gc) the F2 hold is kept across the idle gap, so the next request's decompress
                  pays no young-generation passes; the idle pass still collects every gap.
  H  SWEEP-KEY    (research_v633_deep_layer) the sweep record's sorted copy keyed on a mutation counter instead of
                  tuple(self) on every call.
STRUCTURAL.
"""
from __future__ import annotations

import importlib
from bisect import bisect_left, bisect_right

V6_41_SPEED_VERSION = "speed_v6_41"

# (module, switch) written by apply(); a module without the switch is reported, never fatal (another tree's taos)
SWITCHES = (
    ("taos.im.protocol.models", "_V641_SCAN_ONCE"),
    ("research_v6_38_fast_path", "_V641_SCAN_ONCE"),
    ("research_direct_idle_gc", "V641_HOLD_THROUGH_GAP"),
    ("research_v633_deep_layer", "_V641_VERSION_KEY"),
)


def apply(on) -> dict:
    value = bool(on)
    held = {}
    for module_name, switch in SWITCHES:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            held[switch + "@" + module_name] = None
            continue
        if isinstance(getattr(module, switch, None), bool):
            setattr(module, switch, value)
            held[switch + "@" + module_name] = getattr(module, switch)
        else:
            held[switch + "@" + module_name] = None
    return held


# ---------------------------------------------------------------------------------------------------- F1 miss path
try:
    from pydantic import BaseModel as _BaseModel
    _BM_GETATTR = _BaseModel.__dict__.get("__getattr__")
except Exception:                                   # pragma: no cover
    _BaseModel = None
    _BM_GETATTR = None

_PLAN: dict = {}
_OBJ_GETATTRIBUTE = object.__getattribute__


def _absent_plan(cls, name: str) -> bool:
    """True iff, for every instance of ``cls``, ``getattr(obj, name)`` resolves only through the instance __dict__,
    then pydantic v2's BaseModel.__getattr__ with ``name`` neither private nor a class attribute -- i.e. the class
    side defines nothing called ``name``.  Computed once per (class, name); classes are not mutated at runtime."""
    try:
        if _BaseModel is None or _BM_GETATTR is None:
            return False
        if not (isinstance(cls, type) and issubclass(cls, _BaseModel)):
            return False
        if getattr(cls, "__getattr__", None) is not _BM_GETATTR:
            return False                            # a subclass with its own __getattr__
        if cls.__getattribute__ is not object.__getattribute__:
            return False
        for klass in cls.__mro__:
            if name in klass.__dict__:
                return False                        # a field default, descriptor, property, slot or class attribute
        private = getattr(cls, "__private_attributes__", None) or {}
        if name in private:
            return False
        if hasattr(cls, name):                      # the metaclass side (ModelMetaclass.__getattr__ and its own)
            return False
        return True
    except Exception:
        return False


def fast_getattr(obj, name: str, default=None):
    """getattr(obj, name, default), exactly.  The fast branch replays what the interpreter does for such a class:
    object.__getattribute__ finds no type attribute, so the instance __dict__ answers; otherwise BaseModel.__getattr__
    runs: not private, then __pydantic_extra__, then hasattr(cls, name) is False -> AttributeError -> default."""
    cls = type(obj)
    key = (cls, name)
    plan = _PLAN.get(key)
    if plan is None:
        plan = _absent_plan(cls, name)
        _PLAN[key] = plan
    if not plan:
        return getattr(obj, name, default)
    try:
        inst = _OBJ_GETATTRIBUTE(obj, "__dict__")
    except AttributeError:
        return getattr(obj, name, default)
    if name in inst:
        return inst[name]
    try:
        extra = _OBJ_GETATTRIBUTE(obj, "__pydantic_extra__")
    except AttributeError:
        extra = None
    if extra and name in extra:
        return extra[name]
    return default


# ---------------------------------------------------------------------------------------------------- C kappa-inc
# The frozen body (Strategy1_Research._research_refresh_rolling_kappa_cache, v4.12.x) when its cache key misses:
#   live   = rolling_observation_timestamps(history, now=current, lookback): per book the sorted timestamps of the
#            in-window ([current - lookback, current]) buckets whose pnl is non-zero, books in first-appearance order;
#   merged = persisted (the previous result, as lists) then live, each timestamp int()-ed and kept when in the window,
#            a book entering the dict at its first SURVIVING timestamp;
#   out    = per book tuple(sorted(set)), persisted = lists of it, counts, key (persisted points after), next expiry.
# Invariants the incremental path relies on, each checked (else the frozen body runs and the state is rebuilt):
#   I1 persisted is the very dict this function (or the frozen body after it) wrote, with the same point count;
#   I2 the lookback is the same and > 0, current > 0 and current >= the previous current;
#   I3 no state timestamp ran back since the previous refresh (Simple.update notes every state: _v641_ts_backstep);
#   I4 the history's keys are ints in ascending order (the only writers insert the state's own timestamp, prune
#      keeps order, clear empties): then every bucket that can have changed since the previous refresh has a key >=
#      the largest key the previous refresh scanned (a bucket only changes while its key is the state's timestamp).
# Under I1-I4, persisted already holds every in-window observation of the buckets below that key (they were in the
# previous live, hence in the previous merged, and in-window now means in-window then), so
#   merged = filter(persisted) + the observations of the buckets with key >= the previous largest key,
# with F's order: books with a surviving persisted timestamp in persisted's order, then the others in the order the
# tail scan meets them (their every in-window observation is in the tail).


def _int_keys_sorted(keys) -> bool:
    try:
        if not keys:
            return True
        if set(map(type, keys)) != {int}:
            return False
        return keys == sorted(keys)
    except Exception:
        return False


def kappa_refresh(agent, frozen) -> None:
    """The frozen refresh's outputs, exactly; ``frozen()`` is the frozen method (super()), run whenever an invariant
    cannot be checked.  Never raises more than the frozen body would (an unexpected error -> the frozen body)."""
    history = getattr(agent, "realized_pnl_history", {}) or {}
    persisted = getattr(agent, "_research_persisted_observation_timestamps", {}) or {}
    now = getattr(agent, "_research_last_sim_ts", None)
    if now is None:
        now = max(history.keys(), default=0)
    current = int(now or 0)
    lookback = int(getattr(agent, "research_kappa_lookback_ns", 10_800_000_000_000))
    # -- the cache key, as the frozen body builds it (same values; sum / max at C speed where the types allow)
    try:
        history_points = sum(map(len, history.values()))
    except TypeError:
        history_points = sum(len(books or {}) for books in history.values())
    persisted_points = sum(len(rows or []) for rows in persisted.values())
    latest_history_ts = int(max(history.keys(), default=0) or 0)
    source_key = (lookback, len(history), history_points, latest_history_ts, persisted_points)
    next_expiry = getattr(agent, "_research_kappa_roll_next_expiry_ts", None)
    if (
        source_key == getattr(agent, "_research_kappa_roll_cache_key", None)
        and (next_expiry is None or current <= 0 or current <= int(next_expiry))
    ):
        return
    st = getattr(agent, "_v641_kappa", None)
    try:
        keys = list(history)
        ok = (
            st is not None and st.get("ok")
            and persisted is st["persisted"] and persisted_points == st["points"]           # I1
            and lookback == st["lookback"] and lookback > 0 and current > 0                  # I2
            and current >= st["current"]
            and not getattr(agent, "_v641_ts_backstep", False)                               # I3
            and _int_keys_sorted(keys)                                                       # I4
        )
    except Exception:
        ok = False
    if not ok:
        why = ("no_state" if st is None or not st.get("ok") else "persisted" if persisted is not st["persisted"]
               or persisted_points != st["points"] else "lookback" if lookback != st["lookback"] or lookback <= 0
               else "clock" if current <= 0 or current < st["current"] else "backstep"
               if getattr(agent, "_v641_ts_backstep", False) else "keys")
        counts = getattr(agent, "_v641_kappa_why", None)
        if counts is None:
            counts = agent._v641_kappa_why = {}
        counts[why] = counts.get(why, 0) + 1
        frozen()
        _rebuild_state(agent, current, lookback)
        return
    try:
        _incremental(agent, history, keys, persisted, current, lookback, latest_history_ts, history_points, st)
    except Exception:
        frozen()
        _rebuild_state(agent, current, lookback)


def _rebuild_state(agent, current, lookback) -> None:
    """After the frozen body: what the next incremental step starts from."""
    history = getattr(agent, "realized_pnl_history", {}) or {}
    keys = list(history)
    sorted_ok = _int_keys_sorted(keys)
    agent._v641_kappa = {
        "persisted": getattr(agent, "_research_persisted_observation_timestamps", None),
        "points": sum(len(rows or []) for rows in
                      (getattr(agent, "_research_persisted_observation_timestamps", {}) or {}).values()),
        "current": current, "lookback": lookback,
        "last_key": _last_key(keys, current) if sorted_ok else None,
        "ok": bool(sorted_ok),
    }
    agent._v641_ts_backstep = False
    agent._v641_kappa_full = int(getattr(agent, "_v641_kappa_full", 0) or 0) + 1


def _last_key(keys, current):
    """The largest history key <= current (keys ascending): every bucket with a larger key, or this one (a repeated
    state timestamp), is in the next refresh's tail."""
    i = bisect_right(keys, current)
    return keys[i - 1] if i > 0 else None


def _incremental(agent, history, keys, persisted, current, lookback, latest_history_ts, history_points, st) -> None:
    cutoff = current - lookback
    last_key = st["last_key"]
    start = 0 if last_key is None else bisect_left(keys, last_key)
    # the tail's observations, as rolling_observation_timestamps reads each bucket (in-window, float pnl != 0)
    new_rows: dict[int, list[int]] = {}
    for raw_ts in keys[start:]:
        books = history[raw_ts]
        try:
            ts = int(raw_ts)
        except (TypeError, ValueError):
            continue
        if ts < cutoff:
            continue
        if ts > current:
            continue
        for raw_book, raw_pnl in dict(books or {}).items():
            try:
                pnl = float(raw_pnl)
                book = int(raw_book)
            except (TypeError, ValueError):
                continue
            if pnl == 0.0:
                continue
            new_rows.setdefault(book, []).append(ts)
    timestamps: dict[int, tuple] = {}
    # books with a surviving persisted timestamp, in persisted's order (persisted holds int lists, ascending)
    for raw_book, rows in persisted.items():
        bid = int(raw_book)
        lo = bisect_left(rows, cutoff)
        if lo >= len(rows):
            continue
        add = new_rows.get(bid)
        if add:
            timestamps[bid] = tuple(sorted(set(rows[lo:]).union(add)))
        else:
            timestamps[bid] = tuple(rows[lo:]) if lo else tuple(rows)
    # the others, in the order the tail scan met them
    for bid, add in new_rows.items():
        if bid not in timestamps:
            timestamps[bid] = tuple(sorted(set(add)))
    new_persisted = {int(book): list(rows) for book, rows in timestamps.items()}
    counts = {int(book): len(rows) for book, rows in timestamps.items()}
    persisted_points_after = sum(len(rows) for rows in timestamps.values())
    key = (lookback, len(history), history_points, latest_history_ts, persisted_points_after)
    firsts = [rows[0] for rows in timestamps.values() if rows]
    expiry = (min(firsts) + lookback) if firsts else None          # == min(ts + lookback): rows ascending, lookback > 0
    new_last = _last_key(keys, current)
    agent._research_persisted_observation_timestamps = new_persisted
    agent._research_kappa_roll_ts_cache = timestamps
    agent._research_kappa_roll_count_cache = counts
    agent._research_kappa_roll_cache_key = key
    agent._research_kappa_roll_next_expiry_ts = expiry
    st["persisted"] = new_persisted
    st["points"] = persisted_points_after
    st["current"] = current
    st["last_key"] = new_last
    agent._v641_kappa_inc = int(getattr(agent, "_v641_kappa_inc", 0) or 0) + 1
