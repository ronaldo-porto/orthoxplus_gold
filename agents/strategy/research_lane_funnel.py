# SPDX-License-Identifier: MIT
"""V4.15.3 per-request lane funnel telemetry.

Shows where selected capacity disappears after TOTAL_SCORE_FRONTIER grants
a lane. One compact record per request, broken down by COVERAGE / COMPLETION /
REALIZATION. RANK entry-EV is split into lifecycle (trading_ev < 0) versus
required-entry (0 <= trading_ev < bar).
"""
from __future__ import annotations

from typing import Any

LANE_FUNNEL_VERSION = "lane_funnel_v4_15_3"
LANES = ("COVERAGE", "COMPLETION", "REALIZATION")

STAGE_KEYS = (
    "lane_screened",
    "lane_shortlisted",
    "lane_fresh_feasible",
    "lane_total_score_selected",
    "lane_deep_predicted",
    "lane_lifecycle_ev_pass",
    "lane_required_entry_ev_pass",
    "lane_ev_pass",
    "lane_size_valid",
    "lane_ttl_valid",
    "lane_fill_prob_valid",
    "lane_quote_created",
    "lane_quote_submitted",
    "lane_quote_accepted",
    "lane_filled",
    "lane_rt_completed",
)

REJECT_KEYS = (
    "NEGATIVE_EV",
    "LIFECYCLE_EV",
    "REQUIRED_ENTRY_EV",
    "NON_POSITIVE_EDGE",
    "LOW_FILL_PROBABILITY",
    "ZERO_ORDER_SIZE",
    "TTL_STALE",
    "ADVERSE_SELECTION",
    "ACTIVE_OPEN_BOOK_CAP",
    "TOTAL_EXPOSURE_CAP",
    "CONTRACT_REJECT",
)

_REASON_TO_REJECT = {
    "NEGATIVE_EXPECTED_PNL": "NEGATIVE_EV",
    "NEGATIVE_EV": "NEGATIVE_EV",
    "LIFECYCLE_EV": "LIFECYCLE_EV",
    "REQUIRED_ENTRY_EV": "REQUIRED_ENTRY_EV",
    "NON_POSITIVE_EDGE": "NON_POSITIVE_EDGE",
    "LOW_FILL_PROBABILITY": "LOW_FILL_PROBABILITY",
    "ZERO_ORDER_SIZE": "ZERO_ORDER_SIZE",
    "SIZE_ZERO": "ZERO_ORDER_SIZE",
    "TTL_STALE": "TTL_STALE",
    "ADVERSE_SELECTION": "ADVERSE_SELECTION",
    "ACTIVE_OPEN_BOOK_CAP": "ACTIVE_OPEN_BOOK_CAP",
    "TOTAL_EXPOSURE_CAP": "TOTAL_EXPOSURE_CAP",
    "CONTRACT_REJECT": "CONTRACT_REJECT",
    "MAX_ACTIVE_OPEN_BOOKS": "ACTIVE_OPEN_BOOK_CAP",
    "MAX_TOTAL_OPEN_BOOKS": "TOTAL_EXPOSURE_CAP",
}


def empty_lane_counts() -> dict[str, int]:
    return {key: 0 for key in STAGE_KEYS}


def empty_reject_counts() -> dict[str, int]:
    return {key: 0 for key in REJECT_KEYS}


def empty_funnel() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "lane_funnel_version": LANE_FUNNEL_VERSION,
        "reject": empty_reject_counts(),
    }
    for lane in LANES:
        payload[lane] = empty_lane_counts()
    return payload


def _lane_name(lane: Any) -> str:
    name = str(lane or "").strip().upper()
    if name in {"KAPPA_COMPLETION", "COMPLETION"}:
        return "COMPLETION"
    if name in LANES:
        return name
    return "COVERAGE"


def bump(funnel: dict[str, Any], lane: Any, stage: str, n: int = 1) -> None:
    if not isinstance(funnel, dict) or n == 0:
        return
    bucket = funnel.setdefault(_lane_name(lane), empty_lane_counts())
    if stage in bucket:
        bucket[stage] = int(bucket.get(stage, 0) or 0) + int(n)


def bump_reject(funnel: dict[str, Any], reason: Any, n: int = 1) -> None:
    if not isinstance(funnel, dict) or n == 0:
        return
    key = _REASON_TO_REJECT.get(str(reason or "").strip().upper(), "")
    if not key:
        return
    rejects = funnel.setdefault("reject", empty_reject_counts())
    rejects[key] = int(rejects.get(key, 0) or 0) + int(n)


def compact_log(funnel: dict[str, Any], *, tick: Any = None, lane: Any = None) -> dict[str, Any]:
    """One compact [S1R_FUNNEL] record. If lane is set, emit that lane only."""
    src = funnel if isinstance(funnel, dict) else empty_funnel()
    reject = src.get("reject") if isinstance(src.get("reject"), dict) else {}
    lanes = [_lane_name(lane)] if lane else list(LANES)
    out: dict[str, Any] = {
        "event": "S1R_FUNNEL",
        "lane_funnel_version": LANE_FUNNEL_VERSION,
        "tick": tick,
        "reject_negative_ev": int(reject.get("NEGATIVE_EV", 0) or 0),
        "reject_lifecycle_ev": int(reject.get("LIFECYCLE_EV", 0) or 0),
        "reject_required_entry_ev": int(reject.get("REQUIRED_ENTRY_EV", 0) or 0),
        "reject_size_zero": int(reject.get("ZERO_ORDER_SIZE", 0) or 0),
        "reject_fill_prob": int(reject.get("LOW_FILL_PROBABILITY", 0) or 0),
        "reject_ttl": int(reject.get("TTL_STALE", 0) or 0),
        "reject_adverse": int(reject.get("ADVERSE_SELECTION", 0) or 0),
        "reject_non_positive_edge": int(reject.get("NON_POSITIVE_EDGE", 0) or 0),
        "reject_active_cap": int(reject.get("ACTIVE_OPEN_BOOK_CAP", 0) or 0),
        "reject_total_cap": int(reject.get("TOTAL_EXPOSURE_CAP", 0) or 0),
        "reject_contract": int(reject.get("CONTRACT_REJECT", 0) or 0),
    }
    for name in lanes:
        counts = src.get(name) if isinstance(src.get(name), dict) else {}
        prefix = name.lower()
        required_entry_pass = int(counts.get("lane_required_entry_ev_pass", 0) or 0)
        quote_ev_pass = int(counts.get("lane_ev_pass", 0) or 0)
        out[f"{prefix}_selected"] = int(counts.get("lane_total_score_selected", 0) or 0)
        out[f"{prefix}_fresh_feasible"] = int(counts.get("lane_fresh_feasible", 0) or 0)
        out[f"{prefix}_predicted"] = int(counts.get("lane_deep_predicted", 0) or 0)
        out[f"{prefix}_lifecycle_ev_pass"] = int(counts.get("lane_lifecycle_ev_pass", 0) or 0)
        out[f"{prefix}_required_entry_ev_pass"] = required_entry_pass
        out[f"{prefix}_ev_pass"] = required_entry_pass or quote_ev_pass
        out[f"{prefix}_size_valid"] = int(counts.get("lane_size_valid", 0) or 0)
        out[f"{prefix}_quoted"] = int(counts.get("lane_quote_created", 0) or 0)
        out[f"{prefix}_submitted"] = int(counts.get("lane_quote_submitted", 0) or 0)
        out[f"{prefix}_filled"] = int(counts.get("lane_filled", 0) or 0)
        out[f"{prefix}_rt"] = int(counts.get("lane_rt_completed", 0) or 0)
        out[f"{prefix}_screened"] = int(counts.get("lane_screened", 0) or 0)
        out[f"{prefix}_shortlisted"] = int(counts.get("lane_shortlisted", 0) or 0)
    if lane:
        name = _lane_name(lane)
        counts = src.get(name) if isinstance(src.get(name), dict) else {}
        required_entry_pass = int(counts.get("lane_required_entry_ev_pass", 0) or 0)
        quote_ev_pass = int(counts.get("lane_ev_pass", 0) or 0)
        out["lane"] = name
        out["selected"] = int(counts.get("lane_total_score_selected", 0) or 0)
        out["fresh_feasible"] = int(counts.get("lane_fresh_feasible", 0) or 0)
        out["predicted"] = int(counts.get("lane_deep_predicted", 0) or 0)
        out["lifecycle_ev_pass"] = int(counts.get("lane_lifecycle_ev_pass", 0) or 0)
        out["required_entry_ev_pass"] = required_entry_pass
        out["ev_pass"] = required_entry_pass or quote_ev_pass
        out["size_valid"] = int(counts.get("lane_size_valid", 0) or 0)
        out["quoted"] = int(counts.get("lane_quote_created", 0) or 0)
        out["submitted"] = int(counts.get("lane_quote_submitted", 0) or 0)
        out["filled"] = int(counts.get("lane_filled", 0) or 0)
        out["rt"] = int(counts.get("lane_rt_completed", 0) or 0)
    return out
