# SPDX-License-Identifier: MIT
"""Research-local per-book SN79 volume cap.

The validator in ``taos/im/validator/query.py`` rejects non-cancel
instructions when:

    miner_volumes[instruction.bookId] >= capital_turnover_cap * miner_wealth

The cap is independent per book. Do not sum ``traded_volume`` across books.

Pure functions so unit tests do not import Strategy1 / bittensor.
"""
from __future__ import annotations

import math
import statistics
from typing import Any

REASON_OK = "OK"
REASON_CAP_REACHED = "CAP_REACHED"
REASON_INSUFFICIENT_HEADROOM = "INSUFFICIENT_HEADROOM"
REASON_NO_CONFIG = "NO_CONFIG"
REASON_NO_ACCOUNT = "NO_ACCOUNT"

CANCEL_INSTRUCTION = "CANCEL_ORDERS"


def _finite(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def volume_cap_quote(
    *,
    capital_turnover_cap: Any,
    miner_wealth: Any,
    volume_decimals: Any = None,
) -> float:
    """Per-book quote notional cap. ``<= 0`` means the validator will not apply a cap."""
    mult = _finite(capital_turnover_cap)
    wealth = _finite(miner_wealth)
    if mult is None or wealth is None or mult <= 0.0 or wealth <= 0.0:
        return 0.0
    raw = mult * wealth
    if volume_decimals is not None:
        try:
            raw = round(raw, int(volume_decimals))
        except (TypeError, ValueError):
            pass
    if not math.isfinite(raw) or raw <= 0.0:
        return 0.0
    return float(raw)


def book_traded_volume(account: Any) -> float:
    """Safe per-book used volume. Missing / invalid / negative values are 0."""
    if account is None:
        return 0.0
    used = _finite(getattr(account, "traded_volume", None), 0.0)
    if used is None or used < 0.0:
        return 0.0
    return float(used)


def volume_cap_remaining(cap_quote: float, used: float) -> float:
    cap = _finite(cap_quote, 0.0) or 0.0
    if cap <= 0.0:
        return 0.0
    return max(0.0, cap - max(0.0, _finite(used, 0.0) or 0.0))


def volume_cap_headroom(cap_quote: float, used: float) -> float:
    """Remaining / cap in ``[0, 1]``. No-cap (``cap <= 0``) is treated as full headroom."""
    cap = _finite(cap_quote, 0.0) or 0.0
    if cap <= 0.0:
        return 1.0
    return _clip01(volume_cap_remaining(cap, used) / cap)


def can_add_volume(
    *,
    cap_quote: float,
    used: float,
    quote_notional: Any,
) -> bool:
    """Miner-side admission: used < cap and requested notional fits in remaining.

    Validator only checks ``used >= cap``. This helper is conservative: a
    request larger than remaining is also blocked for that book.
    """
    cap = _finite(cap_quote, 0.0) or 0.0
    if cap <= 0.0:
        return True
    notional = _finite(quote_notional)
    if notional is None or notional < 0.0:
        return False
    remaining = volume_cap_remaining(cap, used)
    if remaining <= 0.0:
        return False
    return float(notional) <= remaining


def volume_cap_reason(
    *,
    cap_quote: float,
    used: float,
    quote_notional: Any = None,
    has_config: bool = True,
    has_account: bool = True,
) -> str:
    if not has_config:
        return REASON_NO_CONFIG
    if not has_account:
        return REASON_NO_ACCOUNT
    cap = _finite(cap_quote, 0.0) or 0.0
    if cap <= 0.0:
        return REASON_OK
    remaining = volume_cap_remaining(cap, used)
    if remaining <= 0.0:
        return REASON_CAP_REACHED
    if quote_notional is not None:
        notional = _finite(quote_notional)
        if notional is not None and notional > remaining:
            return REASON_INSUFFICIENT_HEADROOM
    return REASON_OK


def validator_admits_instruction(
    *,
    capital_turnover_cap: Any,
    miner_wealth: Any,
    traded_volume: Any,
    instruction_type: str,
    volume_decimals: Any = 2,
) -> bool:
    """Semantic parity with ``taos/im/validator/query.py`` per-book volume cap.

    Simulation mode: ``volume_cap = round(capital_turnover_cap * miner_wealth, volume_decimals)``.
    Non-cancel instructions are skipped when ``used >= volume_cap`` and ``volume_cap > 0``.
    ``CANCEL_ORDERS`` is always admitted by this rule.
    """
    cap = volume_cap_quote(
        capital_turnover_cap=capital_turnover_cap,
        miner_wealth=miner_wealth,
        volume_decimals=volume_decimals,
    )
    used = max(0.0, _finite(traded_volume, 0.0) or 0.0)
    token = str(instruction_type or "").upper()
    if cap > 0.0 and used >= cap and token != CANCEL_INSTRUCTION:
        return False
    return True


def aggregate_volume_cap_metrics(headrooms: list[float] | tuple[float, ...]) -> dict[str, Any]:
    values = [_clip01(_finite(h, 0.0) or 0.0) for h in headrooms]
    if not values:
        return {
            "books_cap_reached": 0,
            "books_headroom_lt_10pct": 0,
            "books_headroom_lt_25pct": 0,
            "median_headroom": None,
            "min_headroom": None,
            "book_count": 0,
        }
    return {
        "books_cap_reached": sum(1 for h in values if h <= 0.0),
        "books_headroom_lt_10pct": sum(1 for h in values if h < 0.10),
        "books_headroom_lt_25pct": sum(1 for h in values if h < 0.25),
        "median_headroom": float(statistics.median(values)),
        "min_headroom": float(min(values)),
        "book_count": len(values),
    }


def agent_has_volume_config(agent: Any, state: Any) -> bool:
    cfg = getattr(state, "config", None) if state is not None else None
    if cfg is not None and getattr(cfg, "miner_wealth", None) is not None:
        return True
    return getattr(agent, "_research_last_miner_wealth", None) is not None


def agent_volume_cap_quote(agent: Any, state: Any) -> float:
    cfg = getattr(state, "config", None) if state is not None else None
    wealth = None
    decimals = getattr(agent, "_research_volume_decimals", None)
    if cfg is not None:
        wealth = getattr(cfg, "miner_wealth", None)
        decimals = getattr(cfg, "volumeDecimals", decimals)
    if wealth is None:
        wealth = getattr(agent, "_research_last_miner_wealth", None)
    return volume_cap_quote(
        capital_turnover_cap=getattr(agent, "capital_turnover_cap", None),
        miner_wealth=wealth,
        volume_decimals=decimals,
    )


def agent_book_traded_volume(agent: Any, book_id: Any) -> float:
    try:
        bid = int(book_id)
    except (TypeError, ValueError):
        return 0.0
    accounts = getattr(agent, "accounts", None) or {}
    try:
        account = accounts.get(bid)
    except AttributeError:
        account = None
    if account is None:
        try:
            account = accounts[bid]
        except (KeyError, TypeError, IndexError):
            account = None
    return book_traded_volume(account)


def agent_has_account(agent: Any, book_id: Any) -> bool:
    try:
        bid = int(book_id)
    except (TypeError, ValueError):
        return False
    accounts = getattr(agent, "accounts", None) or {}
    try:
        return bid in accounts
    except TypeError:
        return False


def agent_volume_cap_remaining(agent: Any, state: Any, book_id: Any) -> float:
    cap = agent_volume_cap_quote(agent, state)
    if cap <= 0.0:
        return 0.0
    return volume_cap_remaining(cap, agent_book_traded_volume(agent, book_id))


def agent_volume_cap_headroom(agent: Any, state: Any, book_id: Any) -> float:
    cap = agent_volume_cap_quote(agent, state)
    return volume_cap_headroom(cap, agent_book_traded_volume(agent, book_id))


def agent_can_add_volume(
    agent: Any,
    state: Any,
    book_id: Any,
    quote_notional: Any,
) -> bool:
    cap = agent_volume_cap_quote(agent, state)
    used = agent_book_traded_volume(agent, book_id)
    return can_add_volume(cap_quote=cap, used=used, quote_notional=quote_notional)


def agent_volume_cap_reason(
    agent: Any,
    state: Any,
    book_id: Any,
    quote_notional: Any = None,
) -> str:
    return volume_cap_reason(
        cap_quote=agent_volume_cap_quote(agent, state),
        used=agent_book_traded_volume(agent, book_id),
        quote_notional=quote_notional,
        has_config=agent_has_volume_config(agent, state),
        has_account=agent_has_account(agent, book_id),
    )


def agent_volume_cap_snapshot(agent: Any, state: Any) -> dict[str, Any]:
    cap = agent_volume_cap_quote(agent, state)
    accounts = getattr(agent, "accounts", None) or {}
    try:
        book_ids = [int(book_id) for book_id in accounts]
    except TypeError:
        book_ids = []
    headrooms = [
        volume_cap_headroom(cap, agent_book_traded_volume(agent, book_id))
        for book_id in book_ids
    ]
    metrics = aggregate_volume_cap_metrics(headrooms)
    metrics["cap_quote"] = cap
    metrics["headrooms_by_book"] = {
        int(book_id): volume_cap_headroom(cap, agent_book_traded_volume(agent, book_id))
        for book_id in book_ids
    }
    return metrics
