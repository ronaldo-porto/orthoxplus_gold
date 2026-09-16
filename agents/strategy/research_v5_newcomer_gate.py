# SPDX-License-Identifier: MIT
"""v5.0.3 G1: a newcomer's first scored round must be a full one.

``reward.py apply_track_record_ema`` carries a per-UID standing that the reward floor and the Pareto
allocation are applied to.  It skips a UID only while its incoming trading score is 0 AND it has
never been scored (``cur == 0 and ema_n.get(uid, 0) == 0``); the first NONZERO score seeds the
standing at k=0, where alpha is 1, so the standing equals that score.  Every later round moves it by
at most ``1/(k+1)``.

The seed is therefore the single most valuable number a new registration produces, and the trading
score has two legs:

    trading = 0.79 * kappa_score + 0.21 * pnl_score

``kappa_score`` is 0 until the uid's stored rounds span ``min_lookback`` (5,400 sim-s): ``kappa_3``
returns None before that, and ``calculate_kappa_score`` returns 0.0.  ``pnl_score`` has no such
gate -- it is the median per-book daily return over the scored books, and it turns non-zero as soon
as more than half of them carry realized PnL (41 of the 80 that remain after the 48 inactive books
are ignored).

Measured on UID 18's own run (log 20260915_063311): 40 books carried realized PnL by tick 203 and 41
books had three closes by tick 260, both roughly 5,140 sim-s BEFORE the kappa gate could open.  So an
agent that trades from registration seeds its standing at a PnL-only score near 0.0001 and then
crawls upward at 1/k.  UID 18's standing was still under the floor cut (~0.21 against a field median
of 0.42) 19 hours later, which is why its weight and its emission were exactly 0 for its whole
immunity window.

The fix is to send no instructions until the kappa gate is open, while still answering every request
(the de-beta presence gate counts the last 50 responses, and `requests`/`success` must stay clean).
The first scored round is then a full one: on the same run's history that seed is ~0.39, above the
cut, and the weight follows within hours instead of days.

Nothing here is a threshold fitted to a log.  The 5,400 s span and the 5 s scoring interval are the
validator's own configuration, read the same way v5.0.1 reads them ([[research_v5_activity]]).

SAFETY.  The gate must never hold an agent that already has a track record, and must never hold one
that is carrying risk:

  * it arms only when there is NO prior evidence of any kind -- no restored observations, no realized
    PnL history, no round trips, and an activity belief whose earliest evidence is this process's
    first state rather than a restored observation;
  * it opens immediately, permanently, on any venue exposure, so a position is always managed;
  * once open it is recorded open in the session file, so a restart inside the window cannot re-arm
    it and cannot restart its clock (the persisted anchor wins whenever it is earlier);
  * with no gate estimate it does not arm at all.

Every one of those failure directions costs at most some quiet time.  The direction that costs a
registration -- seeding the standing on a PnL-only score -- is the one the gate removes.
"""
from __future__ import annotations

from typing import Any, Mapping

V503_NEWCOMER_GATE_VERSION = "direct_newcomer_gate_v5_0_3"

# One scoring interval past the validator's own gate, on top of the interval v5.0.1's belief already
# adds: the pass that first sees the full span can land one interval late, and a round trip realized
# in the same second as the gate is worth less than the seed it would spoil.
V503_GATE_MARGIN_NS = 5_000_000_000

GATE_UNARMED = "UNARMED"
GATE_QUIET = "QUIET"
GATE_OPEN = "OPEN"

# Why the gate is not holding instructions.
OPEN_GATE_REACHED = "GATE_REACHED"
OPEN_EXPOSURE = "EXPOSURE"
OPEN_RESTORED = "RESTORED_OPEN"
OPEN_NO_GATE = "NO_GATE_ESTIMATE"
OPEN_SWITCH_OFF = "SWITCH_OFF"

# Prior evidence that this uid is not a newcomer, in the order it is checked.
EVIDENCE_OBSERVATIONS = "OBSERVATIONS"
EVIDENCE_PNL_HISTORY = "PNL_HISTORY"
EVIDENCE_PNL_EVENTS = "PNL_EVENTS"
EVIDENCE_ROUND_TRIPS = "ROUND_TRIPS"
EVIDENCE_RESTORED_START = "RESTORED_START"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number and number not in (float("inf"), float("-inf")) else default


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def prior_evidence(
    *,
    observations: Mapping[Any, Any] | None = None,
    pnl_history: Mapping[Any, Any] | None = None,
    pnl_events: Mapping[Any, Any] | None = None,
    round_trip_closes: Any = 0,
    start_source: Any = None,
    first_state_source: str = "FIRST_STATE",
) -> str | None:
    """The first sign that this uid already has a track record, or None for a genuine newcomer.

    ``observations``/``pnl_events`` are the per-book maps the session restore fills in;
    ``pnl_history`` is the validator-shaped ``{ts: {book: pnl}}``.  ``start_source`` is the activity
    belief's evidence label: anything other than this process's first state means the belief was
    moved earlier by a restored observation, which only a uid with history has.
    """
    if any(int(count or 0) > 0 for count in (observations or {}).values()):
        return EVIDENCE_OBSERVATIONS
    for books in (pnl_history or {}).values():
        if any(_finite(pnl) != 0.0 for pnl in (books or {}).values()):
            return EVIDENCE_PNL_HISTORY
    if any(rows for rows in (pnl_events or {}).values()):
        return EVIDENCE_PNL_EVENTS
    if (_int_or_none(round_trip_closes) or 0) > 0:
        return EVIDENCE_ROUND_TRIPS
    if start_source is not None and str(start_source) != str(first_state_source):
        return EVIDENCE_RESTORED_START
    return None


def effective_anchor(persisted: Any, observed: Any) -> int | None:
    """The earliest first-state timestamp known for this registration.

    A restart inside the quiet window sees a later first state than the registration did, which
    would push the gate out.  The anchor persisted in the session file is earlier, so it wins --
    the same "earliest evidence" rule the activity belief already applies.
    """
    values = [value for value in (_int_or_none(persisted), _int_or_none(observed)) if value is not None]
    return min(values) if values else None


def gate_timestamp(
    anchor: Any,
    *,
    min_lookback_ns: int,
    scoring_interval_ns: int,
    margin_ns: int = V503_GATE_MARGIN_NS,
) -> int | None:
    """When the validator's kappa can first be non-zero for a uid whose rounds start at ``anchor``."""
    start = _int_or_none(anchor)
    if start is None:
        return None
    return start + int(min_lookback_ns) + int(scoring_interval_ns) + max(0, int(margin_ns))


def arm_decision(
    *,
    switch_on: bool,
    restored_open: bool,
    evidence: str | None,
    gate_ts: Any,
) -> tuple[bool, str | None]:
    """Whether to hold instructions at all, and the reason when it does not.

    Returns ``(armed, open_reason)``.  ``open_reason`` is None only when the gate arms.
    """
    if not bool(switch_on):
        return False, OPEN_SWITCH_OFF
    if bool(restored_open):
        return False, OPEN_RESTORED
    if evidence is not None:
        return False, evidence
    if _int_or_none(gate_ts) is None:
        return False, OPEN_NO_GATE
    return True, None


def exposure_abs(net_by_book: Mapping[Any, Any] | None, *, eps: Any = 0.0) -> float:
    """Total absolute venue exposure, ignoring anything inside the execution flat epsilon."""
    floor = abs(_finite(eps))
    total = 0.0
    for net in (net_by_book or {}).values():
        quantity = abs(_finite(net))
        if quantity > floor:
            total += quantity
    return total


def should_open(*, now: Any, gate_ts: Any, exposure: Any = 0.0) -> str | None:
    """Why an armed gate should open now, or None to keep holding instructions."""
    if _finite(exposure) > 0.0:
        return OPEN_EXPOSURE
    gate = _int_or_none(gate_ts)
    if gate is None:
        return OPEN_NO_GATE
    current = _int_or_none(now)
    if current is not None and current >= gate:
        return OPEN_GATE_REACHED
    return None


def seconds_to_gate(*, now: Any, gate_ts: Any) -> float | None:
    gate = _int_or_none(gate_ts)
    current = _int_or_none(now)
    if gate is None or current is None:
        return None
    return round((gate - current) / 1e9, 1)


def gate_state(*, armed: bool, open_reason: str | None) -> str:
    if open_reason is not None and not armed:
        return GATE_OPEN if open_reason in (
            OPEN_GATE_REACHED, OPEN_EXPOSURE, OPEN_RESTORED,
        ) else GATE_UNARMED
    return GATE_QUIET if armed else GATE_UNARMED


def session_state(*, opened: bool, anchor: Any, open_reason: str | None) -> dict[str, Any]:
    """What the session file carries across a restart."""
    return {
        "version": V503_NEWCOMER_GATE_VERSION,
        "opened": int(bool(opened)),
        "anchor_ts": _int_or_none(anchor),
        "open_reason": None if open_reason is None else str(open_reason),
    }


def restored_session(raw: Any) -> tuple[bool, int | None]:
    """``(opened, anchor_ts)`` from a session payload; a malformed payload restores nothing."""
    row = raw if isinstance(raw, Mapping) else {}
    opened = bool(_int_or_none(row.get("opened")) or 0)
    return opened, _int_or_none(row.get("anchor_ts"))
