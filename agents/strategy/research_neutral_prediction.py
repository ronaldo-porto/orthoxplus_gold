# SPDX-License-Identifier: MIT
"""V4.16.1 neutral Maker fallback when directional prediction is unavailable.

Missing directional alpha is not a hard entry gate. A hard-safe FLAT book with
valid L1 can still be evaluated on passive Maker economics (spread, fees, fill,
markout, holding, inventory). This helper never invents positive alpha.
"""
from __future__ import annotations

import math
from typing import Any

NEUTRAL_PREDICTION_VERSION = "neutral_prediction_v4_16_1"

SOURCE_DIRECTIONAL = "DIRECTIONAL"
SOURCE_NEUTRAL = "NEUTRAL_MAKER_FALLBACK"
SOURCE_TERMINAL = "NO_PREDICTION"

ATTR_SOURCE = "prediction_source"
ATTR_NEUTRAL = "neutral_fallback_used"
ATTR_REASON = "neutral_fallback_reason"
ATTR_ALPHA = "alpha_directional"

DIRECTION_HOLD = "HOLD"
DIRECTION_THRESHOLD = 1e-12


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def l1_is_valid(book: Any) -> bool:
    bids = getattr(book, "bids", None) or []
    asks = getattr(book, "asks", None) or []
    if not bids or not asks:
        return False
    try:
        bid = float(bids[0].price)
        ask = float(asks[0].price)
    except (TypeError, ValueError, AttributeError, IndexError):
        return False
    if not math.isfinite(bid) or not math.isfinite(ask):
        return False
    if bid <= 0.0 or ask <= 0.0 or ask < bid:
        return False
    return True


def directional_prediction_unavailable(forecast: Any) -> bool:
    """True when there is no usable directional signal."""
    if forecast is None:
        return True
    token = str(getattr(forecast, "direction", "") or "").upper()
    score = _finite(getattr(forecast, "score", 0.0), float("nan"))
    if not math.isfinite(score):
        return True
    if token in {"", "HOLD", "NONE", "UNAVAILABLE"}:
        return True
    if abs(score) <= DIRECTION_THRESHOLD:
        return True
    return False


def is_neutral_forecast(forecast: Any) -> bool:
    if forecast is None:
        return False
    source = str(getattr(forecast, ATTR_SOURCE, "") or "").upper()
    return source == SOURCE_NEUTRAL or bool(getattr(forecast, ATTR_NEUTRAL, False))


def can_use_neutral_fallback(
    *,
    book: Any,
    inventory_flat: bool = True,
    risk_safe: bool = True,
    toxic: bool = False,
    unsafe: bool = False,
    invalid_l1: bool | None = None,
    volume_capped: bool = False,
    exposure_capped: bool = False,
    inventory_capped: bool = False,
    contract_unsafe: bool = False,
    hard_adverse: bool = False,
) -> tuple[bool, str]:
    """Return (allowed, reason). Reason is the block/allow token."""
    if not inventory_flat:
        return False, "NOT_FLAT"
    if toxic:
        return False, "TOXIC"
    if unsafe or not risk_safe:
        return False, "UNSAFE"
    if volume_capped:
        return False, "VOLUME_CAP"
    if exposure_capped:
        return False, "TOTAL_EXPOSURE_CAP"
    if inventory_capped:
        return False, "MAX_INVENTORY"
    if contract_unsafe:
        return False, "CONTRACT"
    if hard_adverse:
        return False, "ADVERSE_SELECTION"
    missing_l1 = bool(invalid_l1) if invalid_l1 is not None else (not l1_is_valid(book))
    if missing_l1:
        return False, "INVALID_L1"
    return True, SOURCE_NEUTRAL


def make_neutral_forecast(book_id: int) -> Any:
    """Minimal HOLD forecast. Directional alpha is identically zero."""
    from types import SimpleNamespace

    return SimpleNamespace(
        book_id=int(book_id),
        direction=DIRECTION_HOLD,
        score=0.0,
        momentum_m=0.0,
        flow_f=0.0,
        trade_t=0.0,
        log_return=None,
        imbalance=0.0,
        trade_imbalance=0.0,
        **{
            ATTR_SOURCE: SOURCE_NEUTRAL,
            ATTR_NEUTRAL: True,
            ATTR_REASON: SOURCE_NEUTRAL,
            ATTR_ALPHA: 0.0,
        },
    )


def tag_neutral_forecast(forecast: Any, *, reason: str = SOURCE_NEUTRAL) -> Any:
    """Mark an existing HOLD/empty forecast as a neutral Maker context."""
    if forecast is None:
        return None
    try:
        setattr(forecast, ATTR_SOURCE, SOURCE_NEUTRAL)
        setattr(forecast, ATTR_NEUTRAL, True)
        setattr(forecast, ATTR_REASON, str(reason or SOURCE_NEUTRAL))
        setattr(forecast, ATTR_ALPHA, 0.0)
        setattr(forecast, "direction", DIRECTION_HOLD)
        setattr(forecast, "score", 0.0)
    except Exception:
        return forecast
    return forecast


def tag_directional_forecast(forecast: Any) -> Any:
    if forecast is None:
        return None
    try:
        setattr(forecast, ATTR_SOURCE, SOURCE_DIRECTIONAL)
        setattr(forecast, ATTR_NEUTRAL, False)
        setattr(forecast, ATTR_REASON, None)
        score = _finite(getattr(forecast, "score", 0.0))
        setattr(forecast, ATTR_ALPHA, float(score))
    except Exception:
        return forecast
    return forecast


def prediction_source_of(forecast: Any) -> str:
    if forecast is None:
        return SOURCE_TERMINAL
    source = str(getattr(forecast, ATTR_SOURCE, "") or "").strip().upper()
    if source in {SOURCE_NEUTRAL, SOURCE_DIRECTIONAL, SOURCE_TERMINAL}:
        return source
    if is_neutral_forecast(forecast):
        return SOURCE_NEUTRAL
    if directional_prediction_unavailable(forecast):
        return SOURCE_TERMINAL
    return SOURCE_DIRECTIONAL
