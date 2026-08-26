# SPDX-License-Identifier: MIT
"""Research session / Kappa observation integrity.

Kappa realized observations must survive miner reload and simulation-time
rewind for the same simulation. Session state may reset only when the
simulation ID, network/netuid, or schema is incompatible.

This module is pure policy. Strategy1_Research owns persistence I/O.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

CURRENT_SCHEMA = 1
COMPATIBLE_SCHEMAS = (1,)

REASON_SAME_SIMULATION = "SAME_SIMULATION"
REASON_FIRST_BIND = "FIRST_BIND"
REASON_RELOAD_SAME_SIMULATION = "RELOAD_SAME_SIMULATION"
REASON_MISSING_SIM_ID = "MISSING_SIM_ID"
REASON_SIM_ID_CHANGE = "SIM_ID_CHANGE"
REASON_NETWORK_CHANGE = "NETWORK_CHANGE"
REASON_NETUID_CHANGE = "NETUID_CHANGE"
REASON_INCOMPATIBLE_SCHEMA = "INCOMPATIBLE_SCHEMA"
REASON_INVALID_STATE = "INVALID_STATE"

RESET_REASONS = frozenset(
    {
        REASON_SIM_ID_CHANGE,
        REASON_NETWORK_CHANGE,
        REASON_NETUID_CHANGE,
        REASON_INCOMPATIBLE_SCHEMA,
        REASON_INVALID_STATE,
    }
)

ACTION_KEEP = "KEEP"
ACTION_RESTORE = "RESTORE"
ACTION_RESET = "RESET"


@dataclass(frozen=True)
class SessionIdentity:
    simulation_id: str | None
    network: str = "unknown"
    netuid: int | None = None
    schema: int = CURRENT_SCHEMA


@dataclass
class SessionSnapshot:
    identity: SessionIdentity
    observations: dict[int, int] = field(default_factory=dict)
    round_trip_samples: dict[int, int] = field(default_factory=dict)
    round_trip_closes: int = 0


@dataclass
class SessionDecision:
    action: str
    reason: str
    old_sim_id: str | None
    new_sim_id: str | None
    old_obs_total: int
    new_obs_total: int
    observations: dict[int, int]
    round_trip_samples: dict[int, int]
    round_trip_closes: int


def normalize_sim_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def known_network(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text or text == "unknown":
        return None
    return text


def observation_total(observations: Mapping[Any, Any] | None) -> int:
    if not observations:
        return 0
    total = 0
    for value in observations.values():
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if n > 0:
            total += n
    return total


def sanitize_count_map(raw: Any) -> dict[int, int] | None:
    """Return a non-negative int map, or None when the payload is invalid."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        return None
    out: dict[int, int] = {}
    for key, value in raw.items():
        try:
            book_id = int(key)
            count = int(value)
        except (TypeError, ValueError):
            return None
        if count < 0:
            return None
        if count > 0:
            out[book_id] = count
    return out


def merge_observations(*maps: Mapping[int, int] | None) -> dict[int, int]:
    """Per-book maximum. Totals are therefore non-decreasing across merges."""
    out: dict[int, int] = {}
    for mapping in maps:
        if not mapping:
            continue
        for key, value in mapping.items():
            try:
                book_id = int(key)
                count = int(value)
            except (TypeError, ValueError):
                continue
            if count < 0:
                continue
            out[book_id] = max(out.get(book_id, 0), count)
    return out


def increment_observation(
    observations: Mapping[int, int] | None,
    book_id: Any,
    delta: int = 1,
) -> dict[int, int]:
    """Add a realized observation. Refuses any update that would decrease a book."""
    out = merge_observations(observations)
    try:
        bid = int(book_id)
        step = int(delta)
    except (TypeError, ValueError):
        return out
    if step <= 0:
        return out
    out[bid] = out.get(bid, 0) + step
    return out


def enforce_monotonic(
    previous: Mapping[int, int] | None,
    candidate: Mapping[int, int] | None,
) -> dict[int, int]:
    """Never allow a same-simulation map to fall below the previous high-water."""
    return merge_observations(previous, candidate)


def should_reset_on_timestamp_rewind() -> bool:
    """Timestamp regression is not a new scoring episode."""
    return False


def infer_network(*, endpoint: str = "", environment_key: str = "") -> str:
    key = str(environment_key or "").strip().lower()
    if key.startswith("testnet"):
        return "testnet"
    if key.startswith("mainnet") or key.startswith("net_"):
        return "mainnet"
    ep = str(endpoint or "").lower()
    if "test" in ep:
        return "testnet"
    if ep:
        return "mainnet"
    return "unknown"


def resolve_netuid(*sources: Any) -> int | None:
    for source in sources:
        if source is None:
            continue
        try:
            return int(source)
        except (TypeError, ValueError):
            continue
    return None


def extract_simulation_id(state: Any) -> str | None:
    if state is None:
        return None
    cfg = getattr(state, "config", None)
    for obj in (cfg, state):
        if obj is None:
            continue
        sim_id = normalize_sim_id(getattr(obj, "simulation_id", None))
        if sim_id:
            return sim_id
        if isinstance(obj, Mapping):
            sim_id = normalize_sim_id(obj.get("simulation_id"))
            if sim_id:
                return sim_id
    log_dir = getattr(state, "logDir", None)
    if log_dir is None and cfg is not None:
        log_dir = getattr(cfg, "logDir", None)
    if log_dir:
        name = str(log_dir).replace("\\", "/").rstrip("/").split("/")[-1]
        return normalize_sim_id(name[:13] if name else None)
    return None


def sanitize_key(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return cleaned[:96] or "unscoped"


def state_filename(identity: SessionIdentity) -> str:
    sim = sanitize_key(identity.simulation_id or "unbound")
    net = sanitize_key(identity.network or "unknown")
    uid = "na" if identity.netuid is None else str(int(identity.netuid))
    return f"research_session_{net}_{uid}_{sim}.json"


def identity_reset_reason(
    saved: SessionIdentity,
    current: SessionIdentity,
) -> str | None:
    saved_sim = normalize_sim_id(saved.simulation_id)
    current_sim = normalize_sim_id(current.simulation_id)
    if saved_sim and current_sim and saved_sim != current_sim:
        return REASON_SIM_ID_CHANGE
    saved_net = known_network(saved.network)
    current_net = known_network(current.network)
    if saved_net and current_net and saved_net != current_net:
        return REASON_NETWORK_CHANGE
    if (
        saved.netuid is not None
        and current.netuid is not None
        and int(saved.netuid) != int(current.netuid)
    ):
        return REASON_NETUID_CHANGE
    if current.schema not in COMPATIBLE_SCHEMAS:
        return REASON_INCOMPATIBLE_SCHEMA
    if saved.schema not in COMPATIBLE_SCHEMAS:
        return REASON_INCOMPATIBLE_SCHEMA
    return None


def payload_reject_reason(raw: Any) -> str:
    if not isinstance(raw, Mapping):
        return REASON_INVALID_STATE
    try:
        schema = int(raw.get("schema"))
    except (TypeError, ValueError):
        return REASON_INVALID_STATE
    if schema not in COMPATIBLE_SCHEMAS:
        return REASON_INCOMPATIBLE_SCHEMA
    if sanitize_count_map(raw.get("observations")) is None:
        return REASON_INVALID_STATE
    if sanitize_count_map(raw.get("round_trip_samples")) is None:
        return REASON_INVALID_STATE
    try:
        closes = int(raw.get("round_trip_closes", 0))
    except (TypeError, ValueError):
        return REASON_INVALID_STATE
    if closes < 0:
        return REASON_INVALID_STATE
    identity = raw.get("identity")
    if identity is not None and not isinstance(identity, Mapping):
        return REASON_INVALID_STATE
    return ""


def parse_payload(raw: Any) -> SessionSnapshot | None:
    if raw is None:
        return None
    if payload_reject_reason(raw):
        return None
    assert isinstance(raw, Mapping)
    identity_raw = raw.get("identity") if isinstance(raw.get("identity"), Mapping) else {}
    try:
        schema = int(raw.get("schema", identity_raw.get("schema", CURRENT_SCHEMA)))
    except (TypeError, ValueError):
        schema = CURRENT_SCHEMA
    identity = SessionIdentity(
        simulation_id=normalize_sim_id(
            identity_raw.get("simulation_id", raw.get("simulation_id"))
        ),
        network=str(identity_raw.get("network", raw.get("network", "unknown")) or "unknown"),
        netuid=resolve_netuid(identity_raw.get("netuid", raw.get("netuid"))),
        schema=schema,
    )
    observations = sanitize_count_map(raw.get("observations")) or {}
    samples = sanitize_count_map(raw.get("round_trip_samples")) or {}
    try:
        closes = max(0, int(raw.get("round_trip_closes", 0)))
    except (TypeError, ValueError):
        closes = 0
    return SessionSnapshot(
        identity=identity,
        observations=observations,
        round_trip_samples=samples,
        round_trip_closes=closes,
    )


def build_payload(
    identity: SessionIdentity,
    observations: Mapping[int, int],
    round_trip_samples: Mapping[int, int],
    round_trip_closes: int,
) -> dict[str, Any]:
    obs = sanitize_count_map(observations) or {}
    samples = sanitize_count_map(round_trip_samples) or {}
    try:
        closes = max(0, int(round_trip_closes))
    except (TypeError, ValueError):
        closes = 0
    return {
        "schema": int(identity.schema),
        "identity": {
            "simulation_id": identity.simulation_id,
            "network": identity.network,
            "netuid": identity.netuid,
            "schema": int(identity.schema),
        },
        "observations": {str(k): int(v) for k, v in sorted(obs.items())},
        "round_trip_samples": {str(k): int(v) for k, v in sorted(samples.items())},
        "round_trip_closes": closes,
        "obs_total": observation_total(obs),
    }


def format_reset_fields(decision: SessionDecision, tick: int | None) -> dict[str, Any]:
    return {
        "tick": tick,
        "reason": decision.reason,
        "old_sim_id": decision.old_sim_id,
        "new_sim_id": decision.new_sim_id,
        "old_obs_total": int(decision.old_obs_total),
        "new_obs_total": int(decision.new_obs_total),
    }


def decide_session(
    *,
    current: SessionIdentity,
    bound: SessionIdentity | None,
    disk: Any = None,
    live_observations: Mapping[int, int] | None = None,
    live_round_trip_samples: Mapping[int, int] | None = None,
    live_round_trip_closes: int = 0,
) -> SessionDecision:
    live_obs = sanitize_count_map(live_observations) or {}
    live_samples = sanitize_count_map(live_round_trip_samples) or {}
    try:
        live_closes = max(0, int(live_round_trip_closes))
    except (TypeError, ValueError):
        live_closes = 0
    live_total = observation_total(live_obs)
    current_sim = normalize_sim_id(current.simulation_id)

    if not current_sim:
        bound_sim = None if bound is None else bound.simulation_id
        return SessionDecision(
            action=ACTION_KEEP,
            reason=REASON_MISSING_SIM_ID,
            old_sim_id=bound_sim,
            new_sim_id=None,
            old_obs_total=live_total,
            new_obs_total=live_total,
            observations=dict(live_obs),
            round_trip_samples=dict(live_samples),
            round_trip_closes=live_closes,
        )

    if bound is not None:
        reason = identity_reset_reason(bound, current)
        if reason:
            return SessionDecision(
                action=ACTION_RESET,
                reason=reason,
                old_sim_id=bound.simulation_id,
                new_sim_id=current_sim,
                old_obs_total=live_total,
                new_obs_total=0,
                observations={},
                round_trip_samples={},
                round_trip_closes=0,
            )
        return SessionDecision(
            action=ACTION_KEEP,
            reason=REASON_SAME_SIMULATION,
            old_sim_id=bound.simulation_id,
            new_sim_id=current_sim,
            old_obs_total=live_total,
            new_obs_total=live_total,
            observations=dict(live_obs),
            round_trip_samples=dict(live_samples),
            round_trip_closes=live_closes,
        )

    if disk is not None:
        reject = payload_reject_reason(disk)
        if reject:
            old_sim = None
            if isinstance(disk, Mapping):
                old_sim = normalize_sim_id(disk.get("simulation_id"))
                identity_block = disk.get("identity")
                if old_sim is None and isinstance(identity_block, Mapping):
                    old_sim = normalize_sim_id(identity_block.get("simulation_id"))
            if live_total > 0:
                return SessionDecision(
                    action=ACTION_KEEP,
                    reason=reject,
                    old_sim_id=old_sim,
                    new_sim_id=current_sim,
                    old_obs_total=live_total,
                    new_obs_total=live_total,
                    observations=dict(live_obs),
                    round_trip_samples=dict(live_samples),
                    round_trip_closes=live_closes,
                )
            return SessionDecision(
                action=ACTION_RESET,
                reason=reject,
                old_sim_id=old_sim,
                new_sim_id=current_sim,
                old_obs_total=0,
                new_obs_total=0,
                observations={},
                round_trip_samples={},
                round_trip_closes=0,
            )

    parsed = parse_payload(disk)
    if parsed is None:
        return SessionDecision(
            action=ACTION_KEEP,
            reason=REASON_FIRST_BIND,
            old_sim_id=None,
            new_sim_id=current_sim,
            old_obs_total=0,
            new_obs_total=live_total,
            observations=dict(live_obs),
            round_trip_samples=dict(live_samples),
            round_trip_closes=live_closes,
        )

    reason = identity_reset_reason(parsed.identity, current)
    if reason:
        return SessionDecision(
            action=ACTION_RESET,
            reason=reason,
            old_sim_id=parsed.identity.simulation_id,
            new_sim_id=current_sim,
            old_obs_total=observation_total(parsed.observations),
            new_obs_total=0,
            observations={},
            round_trip_samples={},
            round_trip_closes=0,
        )

    restored_obs = merge_observations(live_obs, parsed.observations)
    restored_samples = merge_observations(live_samples, parsed.round_trip_samples)
    restored_closes = max(live_closes, parsed.round_trip_closes)
    return SessionDecision(
        action=ACTION_RESTORE,
        reason=REASON_RELOAD_SAME_SIMULATION,
        old_sim_id=parsed.identity.simulation_id,
        new_sim_id=current_sim,
        old_obs_total=observation_total(parsed.observations),
        new_obs_total=observation_total(restored_obs),
        observations=restored_obs,
        round_trip_samples=restored_samples,
        round_trip_closes=restored_closes,
    )


DEFAULT_TRANSITION_QUARANTINE_TICKS = 2

SESSION_RUNTIME_MAP_ATTRS = (
    "_position_ticks",
    "_research_position_tick_seen",
    "_inventory_reason",
    "_research_realization_last",
    "_research_kappa_realization_last",
    "_research_inventory_state_last",
    "_research_same_side_last",
    "_research_last_realization_ts",
    "_research_entry_size_last",
    "_research_dust_econ_last",
    "_research_parked_dust",
    "_research_aggressive_context",
    "_research_score_ev_last",
    "_research_hazard_last",
    "_research_ofi_last",
    "_research_markout_by_book",
    "_research_markout_horizons",
    "_research_last_predictions",
    "_research_microprice_px",
    "_research_book_micro",
)


def session_requires_transition_quarantine(action: str, reason: str) -> bool:
    """True only for a real session reset (new sim / network / schema), not KEEP/RESTORE."""
    return str(action or "") == ACTION_RESET and str(reason or "") in RESET_REASONS


def format_transition_fields(
    *,
    tick: int | None,
    old_sim: str | None,
    new_sim: str | None,
    reason: str,
    quarantine: int,
    inventory_reconciled: int,
) -> dict[str, Any]:
    return {
        "tick": tick,
        "old_sim": old_sim,
        "new_sim": new_sim,
        "reason": reason,
        "quarantine": int(quarantine),
        "inventory_reconciled": int(inventory_reconciled),
    }


def reconcile_account_base(accounts: Any) -> dict[int, float]:
    """Live account base is the source of truth after a simulation change."""
    out: dict[int, float] = {}
    if not accounts:
        return out
    try:
        items = accounts.items()
    except AttributeError:
        return out
    for key, account in items:
        try:
            book_id = int(key)
        except (TypeError, ValueError):
            continue
        base = getattr(account, "base_balance", None)
        qty = 0.0
        for attr in ("total", "free"):
            raw = getattr(base, attr, None) if base is not None else None
            try:
                number = float(raw)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                qty = number
                break
        out[book_id] = qty
    return out


def clear_mapping_attr(agent: Any, name: str) -> None:
    obj = getattr(agent, name, None)
    if obj is None:
        return
    clearer = getattr(obj, "clear", None)
    if callable(clearer):
        clearer()


def clear_stale_session_runtime(agent: Any) -> None:
    """Drop session-scoped inventory / quote / fill metadata. Do not invent lots."""
    for name in SESSION_RUNTIME_MAP_ATTRS:
        clear_mapping_attr(agent, name)
    clear_mapping_attr(agent, "_open_positions")
    store = getattr(agent, "_research_quote_store", None)
    if store is not None:
        clearer = getattr(store, "clear", None)
        if callable(clearer):
            clearer()
    memories = getattr(agent, "book_memory", None)
    if isinstance(memories, dict):
        for mem in memories.values():
            if hasattr(mem, "recent_pnl"):
                try:
                    mem.recent_pnl = 0.0
                except (TypeError, AttributeError):
                    pass
            if hasattr(mem, "loss_streak"):
                try:
                    mem.loss_streak = 0
                except (TypeError, AttributeError):
                    pass


def taker_allowed_after_transition(*, quarantine: bool) -> bool:
    """Taker and emergency market closes are illegal during transition quarantine."""
    return not bool(quarantine)
