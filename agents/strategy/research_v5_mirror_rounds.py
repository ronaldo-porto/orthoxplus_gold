# SPDX-License-Identifier: MIT
"""v5.0.4 H4: score the rounds the validator holds, not only the rounds this process was sent.

``kappa_3`` runs over every round the validator stored for the UID inside the 3 h lookback, traded or
not, and returns None until those rounds span 5,400 sim-s.  ``trade.update_trade_volumes`` stores a
round for EVERY UID on EVERY state it processes: ``_process_uid_trade_volumes`` runs over
``range(effective_max_uids)`` and writes ``realized_pnl_history[uid][timestamp] = {}`` whether the
miner answered or not.  At a simulation boundary ``shift_simulation_histories`` moves those rounds onto
the new clock and keeps them.

The agent's own copy of the score counted only the states this process was sent.

  * After every restart it reported no Kappa for 5,400 states, about 8 wall-hours at today's 0.181x.
  * For the first 3 h it scored the window on too few columns.  On UID 125, whose history began
    583 sim-s before its first request, the copy's Kappa could appear no earlier than 583 s after the
    validator's.
  * Across a simulation boundary inside one process the kept rounds stayed on the old clock, above
    every new one: the window stopped moving, and the round table was re-filtered on every request.

The fix is the state clock itself.  States are one publish interval apart (1 s; every recorded state of
UID 125 sits on that grid), so this UID's rounds are every state timestamp from the start of its
history to now.  That start is the declared one (H2), else the one this UID's own session file kept,
else this process's first state.  The realized PnL of rounds before this process began is rebuilt from
the per-fill events the session file already keeps: a state books the fills since the state before it
(``DetailedTemplateAgent.update``), so a fill at ``t`` belongs to the first state after ``t`` -- strictly
after: on UID 125's session file this rule rebuilds 4,312 of 4,312 stored observation stamps, and the
one fill stamped exactly on a state (50,299.000 s) was booked at the next one.

Telemetry only: nothing the strategy decides reads the copy of the score.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

V504_MIRROR_ROUNDS_VERSION = "direct_mirror_rounds_v5_0_4"

SESSION_KEY = "v504_mirror_rounds"

# DetailedTemplateAgent.update rounds each state's per-book bucket to 10 places.
BUCKET_DECIMALS = 10

START_DECLARED = "DECLARED"
START_RESTORED = "RESTORED"
START_FIRST_STATE = "FIRST_STATE"

BASIS_VALIDATOR_GRID = "VALIDATOR_GRID"
BASIS_OBSERVED = "OBSERVED"


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def positive_step(value: Any) -> int | None:
    step = _int_or_none(value)
    return step if step is not None and step > 0 else None


def validator_rounds(start_ns: Any, now_ns: Any, *, step_ns: Any, lookback_ns: Any) -> list[int]:
    """Every round the validator holds for this UID inside the lookback, oldest first, ending now."""
    now = _int_or_none(now_ns)
    step = positive_step(step_ns)
    if now is None or step is None:
        return []
    first = _int_or_none(start_ns)
    if first is None or first > now:
        first = now
    lookback = _int_or_none(lookback_ns) or 0
    if lookback > 0:
        first = max(first, now - lookback)
    count = (now - first) // step
    return list(range(now - count * step, now + 1, step))


def state_bucket(ts: Any, *, step_ns: int, phase_ns: int) -> int | None:
    """The state a fill at ``ts`` was booked at: the first state timestamp strictly after it."""
    value = _int_or_none(ts)
    if value is None:
        return None
    step = int(step_ns)
    offset = int(phase_ns) % step
    return ((value - offset) // step + 1) * step + offset


def rebuild_history(
    events_by_book: Mapping[Any, Iterable[Any]] | None,
    *,
    step_ns: Any,
    phase_ns: Any,
    before_ns: Any,
) -> dict[int, dict[int, float]]:
    """``{state_ts: {book: pnl}}`` for the states before ``before_ns``, from per-fill ``(ts, pnl)`` events.

    States from ``before_ns`` on were seen by this process, and their PnL is already in its own history.
    """
    step = positive_step(step_ns)
    phase = _int_or_none(phase_ns)
    before = _int_or_none(before_ns)
    if step is None or phase is None or before is None:
        return {}
    sums: dict[int, dict[int, float]] = {}
    for raw_book, rows in dict(events_by_book or {}).items():
        book = _int_or_none(raw_book)
        if book is None:
            continue
        for item in rows or ():
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            bucket = state_bucket(item[0], step_ns=step, phase_ns=phase)
            try:
                pnl = float(item[1])
            except (TypeError, ValueError):
                continue
            if bucket is None or bucket >= before or pnl != pnl:
                continue
            row = sums.setdefault(bucket, {})
            row[book] = row.get(book, 0.0) + pnl
    history: dict[int, dict[int, float]] = {}
    for bucket in sorted(sums):
        books = {
            book: round(value, BUCKET_DECIMALS)
            for book, value in sums[bucket].items()
            if round(value, BUCKET_DECIMALS) != 0.0
        }
        if books:
            history[bucket] = books
    return history


def merge_history(restored: Mapping[int, Any] | None, live: Mapping[Any, Any]) -> Mapping[Any, Any]:
    """The restored states under this process's own; its own states win on any shared timestamp."""
    if not restored:
        return live
    merged: dict[Any, Any] = dict(restored)
    merged.update(live)
    return merged


def resolve_start(*, declared: Any, restored: Any, first_state: Any) -> tuple[int | None, str | None]:
    """The start of this UID's rounds: declared, else the earliest this UID's evidence shows."""
    value = _int_or_none(declared)
    if value is not None:
        return value, START_DECLARED
    kept = _int_or_none(restored)
    first = _int_or_none(first_state)
    if kept is not None and (first is None or kept < first):
        return kept, START_RESTORED
    if first is not None:
        return first, START_FIRST_STATE
    return None, None


def rebase_keys(mapping: Mapping[Any, Any] | Iterable[Any], shift_ns: int, *, keep_from: Any = None) -> dict:
    """Every timestamp key moved by ``shift_ns``, as ``shift_simulation_histories`` moves the validator's."""
    floor = _int_or_none(keep_from)
    items = mapping.items() if isinstance(mapping, Mapping) else ((key, None) for key in mapping)
    out: dict = {}
    for key, value in items:
        ts = _int_or_none(key)
        if ts is None:
            continue
        moved = ts + int(shift_ns)
        if floor is not None and moved < floor:
            continue
        out[moved] = value
    return out


def session_state(start_ns: Any) -> dict[str, Any]:
    return {"version": V504_MIRROR_ROUNDS_VERSION, "start_ts": _int_or_none(start_ns)}


def restored_start(raw: Any) -> int | None:
    return _int_or_none(raw.get("start_ts")) if isinstance(raw, Mapping) else None


def new_mirror_state() -> dict[str, Any]:
    return {
        "restored_start": None,
        "start_ts": None,
        "start_source": None,
        "live_since": None,
        "pnl_known_from": None,
        "last_now": None,
        "history": None,
        "step_ns": None,
        "rebases": 0,
        "fallbacks": 0,
    }
