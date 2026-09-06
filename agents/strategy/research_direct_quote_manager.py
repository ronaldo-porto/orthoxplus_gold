# SPDX-License-Identifier: MIT
"""Deterministic persistent-Maker execution helpers for Strategy1-Direct A1.7.

This module is intentionally *not* a learned model.  It only decides whether an
already-resting Maker batch should be kept or canceled/repriced from current
observable state.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable

DIRECT_QUOTE_MANAGER_VERSION = "direct_quote_manager_v4_16_2_a1_7_0"

# Conservative first persistent-Maker experiment.  These are execution lifetimes,
# not trade-entry gates.  Current A1.6 observable economics remain authoritative.
DIRECT_QUOTE_QUIET_TTL_MS = 750.0
DIRECT_QUOTE_NORMAL_TTL_MS = 500.0
DIRECT_QUOTE_TREND_TTL_MS = 350.0
DIRECT_QUOTE_STRESSED_TTL_MS = 150.0
DIRECT_QUOTE_MAX_TTL_MS = 750.0

DIRECT_QUOTE_REPRICE_TICKS = 2.0
DIRECT_QUOTE_REPRICE_BPS = 1.5
DIRECT_QUOTE_MAX_TOUCH_DRIFT_BPS = 6.0

ACTION_KEEP = "KEEP"
ACTION_CANCEL_EDGE = "CANCEL_EDGE"
ACTION_CANCEL_REPRICE = "CANCEL_REPRICE"
ACTION_CANCEL_INVALID = "CANCEL_INVALID"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def maker_ttl_ms_for_regime(regime: Any) -> float:
    token = str(getattr(regime, "value", regime) or "").upper()
    if token == "QUIET":
        ttl = DIRECT_QUOTE_QUIET_TTL_MS
    elif token in {"TREND_UP", "TREND_DOWN", "TREND"}:
        ttl = DIRECT_QUOTE_TREND_TTL_MS
    elif token in {"STRESSED", "TOXIC"}:
        ttl = DIRECT_QUOTE_STRESSED_TTL_MS
    else:
        ttl = DIRECT_QUOTE_NORMAL_TTL_MS
    return max(1.0, min(DIRECT_QUOTE_MAX_TTL_MS, float(ttl)))


def maker_expiry_ns_for_regime(regime: Any) -> int:
    return int(round(maker_ttl_ms_for_regime(regime) * 1_000_000.0))


def reprice_threshold(*, mid: float, tick_size: float) -> float:
    tick_term = max(0.0, _finite(tick_size)) * DIRECT_QUOTE_REPRICE_TICKS
    bps_term = max(0.0, _finite(mid)) * DIRECT_QUOTE_REPRICE_BPS / 10_000.0
    return max(tick_term, bps_term, 1e-12)


def _touch_drift_bps(*, side: str, price: float, best_bid: float, best_ask: float) -> float:
    px = _finite(price)
    bid = _finite(best_bid)
    ask = _finite(best_ask)
    mid = 0.5 * (bid + ask) if bid > 0.0 and ask > bid else 0.0
    if mid <= 0.0:
        return float("inf")
    token = str(side or "").lower()
    if token == "buy":
        # Positive only when the quote has fallen behind current best bid.
        return max(0.0, (bid - px) / mid * 10_000.0)
    return max(0.0, (px - ask) / mid * 10_000.0)


@dataclass(frozen=True)
class QuoteBatchDecision:
    action: str
    reason: str
    max_price_delta: float = 0.0
    max_touch_drift_bps: float = 0.0


def decide_quote_batch(
    *,
    existing: Iterable[tuple[str, float]],
    desired_bid: float,
    desired_ask: float,
    best_bid: float,
    best_ask: float,
    current_edge_bps: float,
    min_edge_bps: float,
    tick_size: float,
) -> QuoteBatchDecision:
    """KEEP a current Maker batch only while it remains economically/price valid.

    No historical observations or learned state are used.  A reprice is expressed
    as CANCEL_REPRICE; the caller waits for a later account snapshot before placing
    the replacement, preserving A1.6.3 one-live-batch safety.
    """
    bid = _finite(best_bid)
    ask = _finite(best_ask)
    if bid <= 0.0 or ask <= bid:
        return QuoteBatchDecision(ACTION_CANCEL_INVALID, "BAD_BOOK")
    if _finite(current_edge_bps) + 1e-12 < max(0.0, _finite(min_edge_bps)):
        return QuoteBatchDecision(ACTION_CANCEL_EDGE, "EDGE_BELOW_ENTRY_FLOOR")

    rows = list(existing)
    if not rows:
        return QuoteBatchDecision(ACTION_CANCEL_INVALID, "NO_EXISTING_QUOTES")

    mid = 0.5 * (bid + ask)
    threshold = reprice_threshold(mid=mid, tick_size=tick_size)
    max_delta = 0.0
    max_drift = 0.0
    for raw_side, raw_price in rows:
        side = str(raw_side or "").lower()
        px = _finite(raw_price)
        if px <= 0.0 or side not in {"buy", "sell"}:
            return QuoteBatchDecision(ACTION_CANCEL_INVALID, "BAD_EXISTING_QUOTE")
        if (side == "buy" and px >= ask) or (side == "sell" and px <= bid):
            return QuoteBatchDecision(ACTION_CANCEL_INVALID, "WOULD_CROSS")
        desired = _finite(desired_bid if side == "buy" else desired_ask)
        if desired <= 0.0:
            return QuoteBatchDecision(ACTION_CANCEL_INVALID, "BAD_DESIRED_QUOTE")
        delta = abs(px - desired)
        drift = _touch_drift_bps(
            side=side, price=px, best_bid=bid, best_ask=ask,
        )
        max_delta = max(max_delta, delta)
        max_drift = max(max_drift, drift)
        if drift > DIRECT_QUOTE_MAX_TOUCH_DRIFT_BPS + 1e-12:
            return QuoteBatchDecision(
                ACTION_CANCEL_REPRICE, "TOO_FAR_FROM_TOUCH", max_delta, max_drift,
            )
        if delta > threshold + 1e-12:
            return QuoteBatchDecision(
                ACTION_CANCEL_REPRICE, "PRICE_MOVED", max_delta, max_drift,
            )

    return QuoteBatchDecision(ACTION_KEEP, "VALID", max_delta, max_drift)


def keep_unselected_quote(
    *,
    existing: Iterable[tuple[str, float]],
    best_bid: float,
    best_ask: float,
    current_edge_bps: float,
    min_edge_bps: float,
) -> bool:
    """Cheap validity check for a live quote when the book misses deep top-K."""
    bid = _finite(best_bid)
    ask = _finite(best_ask)
    if bid <= 0.0 or ask <= bid:
        return False
    if _finite(current_edge_bps) + 1e-12 < max(0.0, _finite(min_edge_bps)):
        return False
    rows = list(existing)
    if not rows:
        return False
    for raw_side, raw_price in rows:
        side = str(raw_side or "").lower()
        px = _finite(raw_price)
        if px <= 0.0 or side not in {"buy", "sell"}:
            return False
        if (side == "buy" and px >= ask) or (side == "sell" and px <= bid):
            return False
        drift = _touch_drift_bps(side=side, price=px, best_bid=bid, best_ask=ask)
        if drift > DIRECT_QUOTE_MAX_TOUCH_DRIFT_BPS + 1e-12:
            return False
    return True
