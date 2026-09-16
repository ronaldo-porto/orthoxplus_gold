# SPDX-License-Identifier: MIT
"""v5.0.4 H1/H2: whose history the session file holds, and when the validator began keeping it.

H1, THE SESSION FILE BELONGS TO ONE UID.  The research session file is named after the network, the
subnet and the simulation (``research_session_{network}_{netuid}_{sim}.json``), not after the miner,
so two registrations launched from one tree share one file.  That happened on 2026-09-16: UID 125 was
launched from the tree and research directory UID 18 had used, and restored UID 18's evidence as its
own.

  * The v5.0.3 quiet gate read it as prior evidence and never armed (``V503_QUIET_GATE`` at tick 0:
    UNARMED, ``OBSERVATIONS``).  UID 125 traded from its first request and seeded its standing on a
    PnL-only score, the trap the gate exists to close.
  * The v5.0.1 activity belief took the earliest restored observation (sim 37,198 s, UID 18's) as the
    start of UID 125's history, so every book looked activated and the activation drive was inert.

The file now carries the miner UID in its name and an owner record inside it.  A payload owned by
another UID is refused.  A legacy file, written before the owner record existed, cannot say whose it
is, so it is adopted only when the operator says it belongs to this UID.

H2, THE HISTORY START IS DECLARED, NOT GUESSED.  The validator clears a UID's history when the UID is
registered (``trade.reset_agent_histories``) and from then on writes one round for it on every state,
answered or not (``trade._process_uid_trade_volumes``).  Its Kappa gate is that start plus 5,400 sim-s.
Measured on UID 125: registered at sim 47,405 s, first request at 47,988 s, Kappa window open at
52,805 s = 47,405 + 5,400.  The agent sees neither the registration nor the rounds it was not sent, so
v5.0.1 and v5.0.3 infer the start from evidence: the first state, and the oldest restored observation.
That inference fails in the cases a restart creates.

  * An established UID restarted where its own session file is missing -- a new simulation, a new
    tree, or H1 refusing a foreign file -- looks like a newcomer.  The quiet gate would hold it for
    90 sim-min and the activity belief would call every book gate-closed.
  * A newcomer launched some time after it registered gets a gate estimate late by that delay.

``research_v504_history_anchor`` states the start instead.

  ``auto``             v5.0.3's inference, unchanged.  Right for a fresh registration, and for a
                       restart that finds this UID's own session file.
  ``established``      the Kappa gate opened more than one Kappa lookback (3 sim-h) ago.  Every
                       observation still in the window was then made after the gate, so every
                       eligible book is activated, and nothing is held.
  ``<sim>@<seconds>``  the validator began this UID's history at that timestamp of that simulation.
                       In a later simulation it means ``established``.

A declared start is only as good as the declaration.  One EARLIER than the real registration opens the
quiet gate early, which is the failure the gate exists to prevent.  When unsure, declare a later value:
a later start only delays the gate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from research_v5_activity import ACTIVITY_SAMPLING_NS, KAPPA_MIN_LOOKBACK_NS, SCORING_INTERVAL_NS

V504_REGISTRATION_IDENTITY_VERSION = "direct_registration_identity_v5_0_4"

# taos/im/config: scoring.kappa.lookback, the window every Kappa-3 and every activation is read in.
KAPPA_LOOKBACK_NS = 10_800_000_000_000

# ---- H1: the owner of a session payload ----------------------------------------------------------

OWNER_KEY = "v504_session_owner"

OWNER_OWN = "OWN"
OWNER_FOREIGN = "FOREIGN"
OWNER_UNOWNED = "UNOWNED"
OWNER_MISSING = "MISSING"
OWNER_INVALID = "INVALID"

# Where the evidence this process restored came from.
SOURCE_UID_FILE = "UID_FILE"
SOURCE_LEGACY_ADOPTED = "LEGACY_ADOPTED"
SOURCE_LEGACY_IGNORED = "LEGACY_IGNORED"
SOURCE_LEGACY_INVALID = "LEGACY_INVALID"
SOURCE_FOREIGN_REFUSED = "FOREIGN_REFUSED"
SOURCE_UNOWNED_REFUSED = "UNOWNED_REFUSED"
SOURCE_INVALID = "INVALID"
SOURCE_NONE = "NONE"

LEGACY_IGNORE = "ignore"
LEGACY_ADOPT = "adopt"


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def uid_token(uid: Any) -> str:
    value = _int_or_none(uid)
    return f"uid{value}" if value is not None and value >= 0 else "uidna"


def uid_session_path(legacy_path: str, uid: Any) -> str:
    """``research_session_unknown_79_20260913_0722.json`` -> ``research_session_unknown_79_20260913_0722.uid125.json``.

    A suffix, not a new field in the middle: the legacy name's own parts may contain underscores, and
    this way the two files sort side by side, which is how an operator sees what was adopted.
    """
    path = str(legacy_path)
    stem = path[: -len(".json")] if path.endswith(".json") else path
    return f"{stem}.{uid_token(uid)}.json"


def owner_record(uid: Any) -> dict[str, Any]:
    return {"version": V504_REGISTRATION_IDENTITY_VERSION, "uid": _int_or_none(uid)}


def owner_of(raw: Any) -> int | None:
    if not isinstance(raw, Mapping):
        return None
    record = raw.get(OWNER_KEY)
    return _int_or_none(record.get("uid")) if isinstance(record, Mapping) else None


def owner_verdict(raw: Any, uid: Any) -> str:
    """How a payload read from disk relates to this UID."""
    if raw is None:
        return OWNER_MISSING
    if not isinstance(raw, Mapping) or _int_or_none(raw.get("schema")) is None:
        return OWNER_INVALID
    owner = owner_of(raw)
    mine = _int_or_none(uid)
    if owner is None or mine is None or mine < 0:
        return OWNER_UNOWNED
    return OWNER_OWN if owner == mine else OWNER_FOREIGN


def legacy_mode(value: Any) -> str | None:
    """``ignore`` or ``adopt``; None for anything else, which the caller reports and treats as ``ignore``."""
    text = "" if value is None else str(value).strip().lower()
    if text in ("", LEGACY_IGNORE):
        return LEGACY_IGNORE
    if text == LEGACY_ADOPT:
        return LEGACY_ADOPT
    return None


@dataclass(frozen=True)
class SessionChoice:
    source: str
    payload: Any
    own_verdict: str
    legacy_verdict: str | None = None
    owner_uid: int | None = None


def choose_session(
    own: Any,
    *,
    uid: Any,
    legacy: Any = None,
    legacy_exists: bool = False,
    mode: str = LEGACY_IGNORE,
) -> SessionChoice:
    """The payload this UID restores from.

    ``own`` is what the UID's own file held (None when it does not exist).  ``legacy`` is the
    un-suffixed file's payload, which the caller reads only when ``mode`` is ``adopt``.  An unreadable
    own file passes through, as the base passes it through: the session decision resets it.
    """
    verdict = owner_verdict(own, uid)
    if verdict == OWNER_OWN:
        return SessionChoice(SOURCE_UID_FILE, own, verdict, owner_uid=owner_of(own))
    if verdict == OWNER_INVALID:
        return SessionChoice(SOURCE_INVALID, own, verdict)
    adopt = mode == LEGACY_ADOPT
    if verdict == OWNER_UNOWNED and adopt:
        # A legacy payload saved under the new name by hand is still a legacy payload.
        return SessionChoice(SOURCE_LEGACY_ADOPTED, own, verdict)
    refused = {
        OWNER_FOREIGN: SOURCE_FOREIGN_REFUSED,
        OWNER_UNOWNED: SOURCE_UNOWNED_REFUSED,
    }.get(verdict)
    refused_owner = owner_of(own)
    if not legacy_exists:
        return SessionChoice(refused or SOURCE_NONE, None, verdict, owner_uid=refused_owner)
    if not adopt:
        return SessionChoice(refused or SOURCE_LEGACY_IGNORED, None, verdict, owner_uid=refused_owner)
    legacy_verdict = owner_verdict(legacy, uid)
    if legacy_verdict in (OWNER_OWN, OWNER_UNOWNED):
        return SessionChoice(SOURCE_LEGACY_ADOPTED, legacy, verdict, legacy_verdict, owner_of(legacy))
    if legacy_verdict == OWNER_FOREIGN:
        return SessionChoice(SOURCE_FOREIGN_REFUSED, None, verdict, legacy_verdict, owner_of(legacy))
    if legacy_verdict == OWNER_INVALID:
        return SessionChoice(SOURCE_LEGACY_INVALID, None, verdict, legacy_verdict)
    return SessionChoice(refused or SOURCE_NONE, None, verdict, legacy_verdict, refused_owner)


# ---- H2: the declared start of the UID's validator history ---------------------------------------

ANCHOR_AUTO = "auto"
ANCHOR_ESTABLISHED = "established"

MODE_AUTO = "AUTO"
MODE_ESTABLISHED = "ESTABLISHED"
MODE_DECLARED = "DECLARED"
MODE_INVALID = "INVALID"

PIN_ESTABLISHED = "ESTABLISHED"
PIN_DECLARED = "DECLARED"
PIN_DECLARED_CLAMPED = "DECLARED_CLAMPED"
PIN_EARLIER_SIMULATION = "DECLARED_EARLIER_SIMULATION"
PIN_UNKNOWN_SIMULATION = "UNKNOWN_SIMULATION"

# How far before this process's first state an established UID's history is placed.  One Kappa
# lookback puts every observation still in the window after the gate; the Kappa gate, the activation
# bucket before it and the one it falls in, and one scoring interval put the activation floor behind
# that as well.  The Kappa copy's round grid then covers the whole lookback from the first state on.
ESTABLISHED_LEAD_NS = (
    KAPPA_LOOKBACK_NS + KAPPA_MIN_LOOKBACK_NS + 2 * ACTIVITY_SAMPLING_NS + SCORING_INTERVAL_NS
)


@dataclass(frozen=True)
class HistoryAnchor:
    mode: str
    raw: str = ""
    simulation_id: str | None = None
    start_ns: int | None = None


def parse_history_anchor(value: Any) -> HistoryAnchor:
    """``auto``, ``established`` or ``<simulation id>@<sim seconds>``; anything else is INVALID.

    ``--agent.params`` splits on ``=`` and turns anything numeric into a float, so the declared form
    always carries an ``@``, and a bare number is never a valid declaration.
    """
    text = "" if value is None else str(value).strip()
    lowered = text.lower()
    if lowered in ("", ANCHOR_AUTO):
        return HistoryAnchor(MODE_AUTO, text)
    if lowered == ANCHOR_ESTABLISHED:
        return HistoryAnchor(MODE_ESTABLISHED, text)
    simulation, sep, seconds = text.rpartition("@")
    if sep and simulation and seconds and not any(ch.isspace() or ch in "=@" for ch in simulation):
        try:
            value_s = float(seconds)
        except ValueError:
            value_s = float("nan")
        if math.isfinite(value_s):
            return HistoryAnchor(MODE_DECLARED, text, simulation, int(round(value_s * 1_000_000_000)))
    return HistoryAnchor(MODE_INVALID, text)


@dataclass(frozen=True)
class HistoryPin:
    """The history start every consumer uses; ``start_ns`` None keeps v5.0.3's inference."""

    start_ns: int | None
    source: str
    established: bool = False


def _simulation(value: Any) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def resolve_history_pin(
    anchor: HistoryAnchor | None,
    *,
    simulation_id: Any,
    first_state_ns: Any,
) -> HistoryPin | None:
    """What a declaration means for this process, or None when there is nothing to pin (yet)."""
    if anchor is None or anchor.mode not in (MODE_ESTABLISHED, MODE_DECLARED):
        return None
    first = _int_or_none(first_state_ns)
    if first is None:
        return None
    if anchor.mode == MODE_ESTABLISHED:
        return HistoryPin(first - ESTABLISHED_LEAD_NS, PIN_ESTABLISHED, True)
    current = _simulation(simulation_id)
    if current is None:
        # The declaration names a simulation this process cannot confirm: infer, as v5.0.3 does.
        return HistoryPin(None, PIN_UNKNOWN_SIMULATION, False)
    if _simulation(anchor.simulation_id) != current:
        # The validator shifts a UID's rounds onto each new simulation's clock and keeps them.
        return HistoryPin(first - ESTABLISHED_LEAD_NS, PIN_EARLIER_SIMULATION, True)
    start = int(anchor.start_ns)
    if start > first:
        # The validator is sending states, so it began this UID's history no later than the first one.
        return HistoryPin(first, PIN_DECLARED_CLAMPED, False)
    return HistoryPin(start, PIN_DECLARED, False)
