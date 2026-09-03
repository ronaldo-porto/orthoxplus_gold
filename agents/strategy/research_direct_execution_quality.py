# SPDX-License-Identifier: MIT
"""A1.3 execution-quality controls learned from Agent-68 A1.2 runtime.

These are execution mechanics, not a new economics lane:
* untradeable dust is excluded from productive open-book capacity while still
  remaining inside absolute BASE risk;
* Maker quotes are capped at the empirically productive touch-improvement band;
* Maker GTT is capped at the empirically productive freshness window.
"""
from __future__ import annotations

import math
from typing import Any

DIRECT_EXECUTION_QUALITY_VERSION = "direct_execution_quality_v4_16_2_a1_3"
DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS = 6.0
DIRECT_MAKER_MAX_TTL_MS = 75.0
DIRECT_DUST_EXEMPT_CAP = 8


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def cap_maker_quote_geometry(
    *,
    bid: float,
    ask: float,
    bid_px: float,
    ask_px: float,
    price_decimals: int,
    max_touch_improvement_bps: float = DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS,
) -> tuple[float, float, dict[str, float]]:
    """Cap how far a post-only quote may chase *inside* the touch.

    Positive improvement means a more aggressive quote inside the spread.
    Passive quotes outside touch are left untouched.
    """
    b = _finite(bid)
    a = _finite(ask)
    bp = _finite(bid_px)
    ap = _finite(ask_px)
    if b <= 0.0 or a <= b:
        return bp, ap, {
            "raw_buy_improvement_bps": 0.0,
            "raw_sell_improvement_bps": 0.0,
            "buy_improvement_bps": 0.0,
            "sell_improvement_bps": 0.0,
        }
    mid = 0.5 * (b + a)
    limit_bps = max(0.0, _finite(max_touch_improvement_bps, DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS))
    raw_buy = (bp - b) / mid * 10_000.0
    raw_sell = (a - ap) / mid * 10_000.0
    max_delta = mid * limit_bps / 10_000.0
    bp2 = min(bp, b + max_delta)
    ap2 = max(ap, a - max_delta)
    dec = max(0, int(price_decimals))
    bp2 = round(bp2, dec)
    ap2 = round(ap2, dec)
    # Clamping moves quotes away from each other; preserve a final fail-safe.
    if bp2 >= ap2:
        return bp, ap, {
            "raw_buy_improvement_bps": raw_buy,
            "raw_sell_improvement_bps": raw_sell,
            "buy_improvement_bps": raw_buy,
            "sell_improvement_bps": raw_sell,
        }
    return bp2, ap2, {
        "raw_buy_improvement_bps": raw_buy,
        "raw_sell_improvement_bps": raw_sell,
        "buy_improvement_bps": (bp2 - b) / mid * 10_000.0,
        "sell_improvement_bps": (a - ap2) / mid * 10_000.0,
    }


def direct_maker_expiry_ns(
    base_expiry_ns: int,
    *,
    max_ttl_ms: float = DIRECT_MAKER_MAX_TTL_MS,
) -> int:
    cap_ns = max(1, int(round(max(0.001, _finite(max_ttl_ms, DIRECT_MAKER_MAX_TTL_MS)) * 1_000_000.0)))
    try:
        base = int(base_expiry_ns)
    except (TypeError, ValueError):
        base = cap_ns
    if base <= 0:
        return cap_ns
    return min(base, cap_ns)


def effective_total_open_books(
    *, actual_nonflat: int, dust_nonflat: int, cap: int = DIRECT_DUST_EXEMPT_CAP,
) -> int:
    return max(0, int(actual_nonflat or 0) - dust_exempt_count(dust_nonflat, cap))


def dust_exempt_count(dust_nonflat: int, cap: int = DIRECT_DUST_EXEMPT_CAP) -> int:
    return min(max(0, int(dust_nonflat or 0)), max(0, int(cap or 0)))
