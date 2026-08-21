# SPDX-License-Identifier: MIT
"""Environment-scoped AdaptiveAgent persistence.

Not an execution engine. AdaptiveAgent owns phases and overlays; this module
only decides what disk state is legal to reload. Scoring / drift / session
clocks never resume a new process into NORMAL.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

CURRENT_SCHEMA = 4
COMPATIBLE_SCHEMAS = (1, 2, 3, 4)
PRIOR_DISCOUNT_SOFT_MISMATCH = 0.25
MIN_ORDER_REL_EPS = 0.05

HARD_IDENTITY_FIELDS = (
    "network",
    "netuid",
    "validator_environment",
    "simulation_family",
)
SOFT_IDENTITY_FIELDS = (
    "base_version",
    "adaptive_version",
    "min_order_size",
)

EXECUTION_VECTOR_KEYS = ("buy_quotes", "buy_fills", "sell_quotes", "sell_fills")
EXECUTION_INT_KEYS = (
    "maker_fills",
    "taker_fills",
    "dust_attempts",
    "dust_fills",
)
EXECUTION_FLOAT_KEYS = (
    "maker_realized_pnl_ewma",
    "maker_pnl_long_ewma",
    "taker_realized_pnl_ewma",
    "taker_exit_age_ewma",
)
SESSION_INT_ZERO = (
    "session_realized_obs",
    "maker_realized_obs",
    "taker_realized_obs",
    "dust_selections",
    "dust_fail_streak",
)
SESSION_TICK_RESET = (
    "dust_last_selection_tick",
    "dust_last_attempt_tick",
    "dust_last_fill_tick",
    "dust_last_accounted_submit_tick",
    "dust_last_success_submit_tick",
)


def empty_execution_priors() -> dict[str, Any]:
    return {
        "buy_quotes": [0, 0, 0],
        "buy_fills": [0, 0, 0],
        "sell_quotes": [0, 0, 0],
        "sell_fills": [0, 0, 0],
        "maker_fills": 0,
        "taker_fills": 0,
        "dust_attempts": 0,
        "dust_fills": 0,
        "maker_realized_pnl_ewma": 0.0,
        "maker_pnl_long_ewma": 0.0,
        "taker_realized_pnl_ewma": 0.0,
        "taker_exit_age_ewma": 0.0,
    }


def apply_session_reset(stats: dict[str, Any]) -> dict[str, Any]:
    """Keep execution priors; clear scoring / dust-session clocks."""
    stats["session_realized_obs"] = 0
    stats["maker_realized_obs"] = 0
    stats["taker_realized_obs"] = 0
    stats["dust_selections"] = 0
    stats["dust_fail_streak"] = 0
    stats["maker_pnl_short_ewma"] = float(stats.get("maker_pnl_long_ewma", 0.0) or 0.0)
    stats["realized_pnl_ewma"] = float(stats.get("maker_realized_pnl_ewma", 0.0) or 0.0)
    for key in SESSION_TICK_RESET:
        stats[key] = -1
    return stats


def sanitize_key(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return cleaned[:96] or "unscoped"


def parse_environment_key(key: str) -> tuple[str, int | None]:
    """Return (network, netuid) from keys like testnet_366_m3 or net_79_m3."""
    raw = str(key or "").strip().lower()
    if not raw or raw == "unscoped":
        return "unknown", None
    if raw.startswith("testnet"):
        network = "testnet"
        rest = raw[len("testnet"):].lstrip("_-")
    elif raw.startswith("net_"):
        network = "mainnet"
        rest = raw[4:]
    elif raw.startswith("mainnet"):
        network = "mainnet"
        rest = raw[len("mainnet"):].lstrip("_-")
    else:
        network = "unknown"
        rest = raw
    match = re.search(r"(\d+)", rest)
    netuid = int(match.group(1)) if match else None
    return network, netuid


def infer_network(*, endpoint: str = "", environment_key: str = "") -> str:
    parsed, _ = parse_environment_key(environment_key)
    if parsed != "unknown":
        return parsed
    ep = str(endpoint or "").lower()
    if "test" in ep:
        return "testnet"
    if ep:
        return "mainnet"
    return "unknown"


def build_identity(
    *,
    network: str,
    netuid: int | None,
    validator_environment: str,
    base_version: str,
    adaptive_version: str,
    schema: int = CURRENT_SCHEMA,
    min_order_size: float,
    simulation_family: str = "im",
) -> dict[str, Any]:
    try:
        uid = None if netuid is None else int(netuid)
    except (TypeError, ValueError):
        uid = None
    try:
        min_size = float(min_order_size)
        if not math.isfinite(min_size) or min_size < 0.0:
            min_size = 0.0
    except (TypeError, ValueError):
        min_size = 0.0
    return {
        "network": str(network or "unknown"),
        "netuid": uid,
        "validator_environment": str(validator_environment or "unscoped"),
        "base_version": str(base_version or "unknown"),
        "adaptive_version": str(adaptive_version or "unknown"),
        "schema": int(schema),
        "min_order_size": min_size,
        "simulation_family": str(simulation_family or "im"),
    }


def identity_fingerprint(identity: dict[str, Any]) -> str:
    hard = {k: identity.get(k) for k in HARD_IDENTITY_FIELDS}
    blob = json.dumps(hard, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def state_filename(identity: dict[str, Any]) -> str:
    env = sanitize_key(str(identity.get("validator_environment") or "unscoped"))
    return f"adaptive_state_{env}_{identity_fingerprint(identity)}.json"


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _vector3(value: Any) -> list[int]:
    if not isinstance(value, list) or len(value) != 3:
        return [0, 0, 0]
    out: list[int] = []
    for item in value:
        try:
            out.append(max(0, int(item)))
        except (TypeError, ValueError):
            out.append(0)
    return out


def extract_execution_priors(raw: Any) -> dict[str, Any]:
    priors = empty_execution_priors()
    if not isinstance(raw, dict):
        return priors
    for key in EXECUTION_VECTOR_KEYS:
        priors[key] = _vector3(raw.get(key))
    for key in EXECUTION_INT_KEYS:
        priors[key] = max(0, _as_int(raw.get(key, 0)))
    for key in EXECUTION_FLOAT_KEYS:
        priors[key] = _as_float(raw.get(key, 0.0))
    return priors


def discount_execution_priors(priors: dict[str, Any], factor: float) -> dict[str, Any]:
    scale = max(0.0, min(1.0, float(factor)))
    out = empty_execution_priors()
    if scale <= 0.0:
        return out
    for key in EXECUTION_VECTOR_KEYS:
        out[key] = [int(v * scale) for v in _vector3(priors.get(key))]
    for key in EXECUTION_INT_KEYS:
        out[key] = int(max(0, _as_int(priors.get(key, 0))) * scale)
    for key in EXECUTION_FLOAT_KEYS:
        out[key] = _as_float(priors.get(key, 0.0)) * scale
    return out


def reset_dust_priors(priors: dict[str, Any]) -> dict[str, Any]:
    priors = dict(priors)
    priors["dust_attempts"] = 0
    priors["dust_fills"] = 0
    return priors


def min_order_mismatch(saved: float | None, current: float | None) -> bool:
    a = _as_float(saved, 0.0)
    b = _as_float(current, 0.0)
    ref = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / ref > MIN_ORDER_REL_EPS


def complete_legacy_identity(
    saved: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    """Fill missing legacy identity fields only when the environment key matches."""
    out = dict(saved)
    env = str(out.get("validator_environment") or out.get("environment_key") or "")
    if env and env == str(current.get("validator_environment") or ""):
        for key in ("network", "netuid", "simulation_family"):
            if out.get(key) in (None, "", "unknown"):
                out[key] = current.get(key)
        if out.get("base_version") in (None, "", "unknown"):
            out["base_version"] = current.get("base_version")
        if out.get("adaptive_version") in (None, "", "unknown"):
            out["adaptive_version"] = current.get("adaptive_version")
        if _as_float(out.get("min_order_size"), 0.0) <= 0.0:
            out["min_order_size"] = current.get("min_order_size")
    out["validator_environment"] = env or str(out.get("validator_environment") or "")
    return out


def identity_from_legacy(payload: dict[str, Any]) -> dict[str, Any]:
    env = str(payload.get("environment_key") or payload.get("validator_environment") or "")
    network, netuid = parse_environment_key(env)
    return {
        "network": network,
        "netuid": netuid,
        "validator_environment": env,
        "base_version": str(payload.get("base_version") or "unknown"),
        "adaptive_version": str(payload.get("version") or payload.get("adaptive_version") or "unknown"),
        "schema": _as_int(payload.get("schema"), 0),
        "min_order_size": _as_float(payload.get("min_order_size"), 0.0),
        "simulation_family": str(payload.get("simulation_family") or ""),
    }


def migrate_payload(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    schema = _as_int(raw.get("schema"), -1)
    if schema not in COMPATIBLE_SCHEMAS:
        return None
    if schema == CURRENT_SCHEMA and isinstance(raw.get("identity"), dict):
        priors_block = raw.get("execution_priors")
        if not isinstance(priors_block, dict):
            return None
        books_in = priors_block.get("books", {})
        books: dict[str, dict[str, Any]] = {}
        if isinstance(books_in, dict):
            for key, value in books_in.items():
                books[str(key)] = extract_execution_priors(value)
        return {
            "schema": CURRENT_SCHEMA,
            "identity": dict(raw["identity"]),
            "execution_priors": {
                "global": extract_execution_priors(priors_block.get("global")),
                "books": books,
            },
            "scoring_state": {},
            "drift_baseline": {},
            "session_state": {},
            "migrated_from": CURRENT_SCHEMA,
        }
    if schema in (1, 2, 3):
        books_in = raw.get("books", {})
        books: dict[str, dict[str, Any]] = {}
        if isinstance(books_in, dict):
            for key, value in books_in.items():
                books[str(key)] = extract_execution_priors(value)
        return {
            "schema": CURRENT_SCHEMA,
            "identity": identity_from_legacy(raw),
            "execution_priors": {
                "global": extract_execution_priors(raw.get("global")),
                "books": books,
            },
            "scoring_state": {},
            "drift_baseline": {},
            "session_state": {},
            "migrated_from": schema,
        }
    return None


def classify_identity(
    current: dict[str, Any],
    saved: dict[str, Any],
) -> tuple[str, float, list[str]]:
    """Return (reason, prior_factor, mismatch_fields)."""
    mismatches: list[str] = []
    for field in HARD_IDENTITY_FIELDS:
        if current.get(field) != saved.get(field):
            mismatches.append(field)
    if mismatches:
        return f"{mismatches[0]}_mismatch", 0.0, mismatches

    factor = 1.0
    reason = "compatible"
    for field in SOFT_IDENTITY_FIELDS:
        if field == "min_order_size":
            if min_order_mismatch(saved.get(field), current.get(field)):
                mismatches.append(field)
                factor *= PRIOR_DISCOUNT_SOFT_MISMATCH
                reason = "min_order_mismatch"
        elif current.get(field) != saved.get(field):
            mismatches.append(field)
            factor *= PRIOR_DISCOUNT_SOFT_MISMATCH
            if reason == "compatible":
                reason = f"{field}_mismatch"
    return reason, factor, mismatches


@dataclass
class LoadDecision:
    reason: str
    prior_factor: float
    mismatches: list[str]
    phase: str
    total_requests: int
    global_priors: dict[str, Any]
    book_priors: dict[int, dict[str, Any]]
    migrated_from: int | None = None


def decide_load(current_identity: dict[str, Any], raw: Any) -> LoadDecision:
    """Pure load policy. Always returns OBSERVE (requests=0)."""
    empty = LoadDecision(
        reason="missing",
        prior_factor=0.0,
        mismatches=[],
        phase="OBSERVE",
        total_requests=0,
        global_priors=empty_execution_priors(),
        book_priors={},
        migrated_from=None,
    )
    if raw is None:
        return empty
    migrated = migrate_payload(raw)
    if migrated is None:
        empty.reason = "corrupted"
        return empty

    saved_identity = complete_legacy_identity(migrated["identity"], current_identity)
    saved_identity["schema"] = CURRENT_SCHEMA
    reason, factor, mismatches = classify_identity(current_identity, saved_identity)
    if factor <= 0.0:
        empty.reason = reason
        empty.mismatches = mismatches
        empty.migrated_from = migrated.get("migrated_from")
        return empty

    global_priors = discount_execution_priors(
        migrated["execution_priors"]["global"], factor
    )
    if "min_order_size" in mismatches:
        global_priors = reset_dust_priors(global_priors)
    books: dict[int, dict[str, Any]] = {}
    for key, priors in migrated["execution_priors"]["books"].items():
        try:
            book_id = int(key)
        except (TypeError, ValueError):
            continue
        book_priors = discount_execution_priors(priors, factor)
        if "min_order_size" in mismatches:
            book_priors = reset_dust_priors(book_priors)
        books[book_id] = book_priors
    return LoadDecision(
        reason=reason,
        prior_factor=factor,
        mismatches=mismatches,
        phase="OBSERVE",
        total_requests=0,
        global_priors=global_priors,
        book_priors=books,
        migrated_from=migrated.get("migrated_from"),
    )


def merge_priors_into_stats(stats: dict[str, Any], priors: dict[str, Any]) -> dict[str, Any]:
    stats.update(extract_execution_priors(priors))
    return apply_session_reset(stats)


def build_save_payload(
    *,
    identity: dict[str, Any],
    global_stats: dict[str, Any],
    book_stats: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    books = {
        str(int(book_id)): extract_execution_priors(stats)
        for book_id, stats in sorted(book_stats.items())
    }
    return {
        "schema": CURRENT_SCHEMA,
        "identity": dict(identity),
        "execution_priors": {
            "global": extract_execution_priors(global_stats),
            "books": books,
        },
        "scoring_state": {},
        "drift_baseline": {},
        "session_state": {},
    }
