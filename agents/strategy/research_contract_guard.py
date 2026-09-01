# SPDX-License-Identifier: MIT
"""Authoritative-L1 post-only contract-rejection guard for Research V4.12.14.

The simulator returns CONTRACT_VIOLATION when a post-only limit would cross at
processing time (or when IOC/FOK conflicts with post-only). Research uses GTT
post-only Maker orders, so live CONTRACT_VIOLATION events are treated as a
stale-touch/post-only race.

V4.12.12 stopped immediate retry spam but expired state after 32 ticks. Runtime
verification showed live touch can remain unavailable for 33-40+ ticks, so the
guard could expire before it ever reached its safe-reprice branch. The same
book/side then restarted at streak=1 and could reject again.

V4.12.13 made a real reject a pending-reprice state. V4.12.14 fixes the
runtime L1 lookup: SN79 runs with ``lazy_load=1``, so ``state.books`` is often
``LazyBooks`` (a ``collections.abc.Mapping``), not a built-in ``dict``. The old
integration rejected every non-dict mapping and therefore emitted NO_TOUCH_SKIP
forever even though the parent quote builder had live L1. V4.12.14 resolves the
book by Mapping/duck-typed lookup and uses that same authoritative state object.

Pending-reprice contract:

1. suppress the immediate retry for a bounded number of ticks;
2. preserve the pending state while no fresh touch is available;
3. once a fresh touch exists, move the post-only quote away from the current
   opposite touch by 1..3 ticks, depending on the rejection streak;
4. preserve the rejection streak across long no-touch gaps;
5. clear on accepted limit order, flat/cross lifecycle transition, or a separate
   512-tick hard safety lifetime.

Market/Taker orders are outside this helper by design.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import Any

CONTRACT_GUARD_VERSION = "authoritative_l1_contract_guard_v4_12_14"
BASE_COOLDOWN_TICKS = 1
MAX_COOLDOWN_TICKS = 8
MAX_CUSHION_TICKS = 3
HARD_LIFETIME_TICKS = 512


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def resolve_book_from_state_mapping(books: Any, book_id: int) -> Any | None:
    """Resolve one order book from dict, LazyBooks, or mapping-like state.

    ``MarketSimulationStateUpdate.decompress(lazy=True)`` installs ``LazyBooks``,
    which implements ``collections.abc.Mapping`` but is intentionally not a
    built-in dict.  The parent strategy already consumes it through ``items()``
    and ``__getitem__``.  The contract guard must use the exact same mapping,
    not reject it with ``isinstance(books, dict)``.

    This helper is fail-closed: malformed/missing mappings return ``None``.
    """
    if books is None:
        return None
    try:
        key = int(book_id)
    except (TypeError, ValueError):
        return None
    if isinstance(books, Mapping):
        try:
            return books.get(key)
        except (KeyError, TypeError, ValueError):
            return None
    # Keep compatibility with mapping-like protocol objects that expose get()
    # or __getitem__() without registering with collections.abc.Mapping.
    getter = getattr(books, "get", None)
    if callable(getter):
        try:
            return getter(key)
        except (KeyError, TypeError, ValueError):
            return None
    try:
        return books[key]
    except (KeyError, TypeError, ValueError, IndexError):
        return None


@dataclass(frozen=True)
class ContractRejectState:
    streak: int
    first_reject_tick: int
    last_reject_tick: int
    blocked_until_tick: int


def register_contract_reject(
    previous: ContractRejectState | None,
    *,
    current_tick: int,
    base_cooldown_ticks: int = BASE_COOLDOWN_TICKS,
    max_cooldown_ticks: int = MAX_COOLDOWN_TICKS,
    hard_lifetime_ticks: int = HARD_LIFETIME_TICKS,
) -> ContractRejectState:
    """Advance the rejection streak and compute bounded exponential cooldown.

    A previous state remains part of the same reject episode until its hard
    lifetime expires. In particular, long NO_TOUCH gaps do not reset streak.
    """
    now = max(0, int(current_tick))
    base = max(1, int(base_cooldown_ticks))
    cap = max(base, min(16, int(max_cooldown_ticks)))
    hard = max(32, min(2048, int(hard_lifetime_ticks)))

    prev_streak = 0
    first_reject_tick = now
    if previous is not None:
        previous_first = max(0, int(previous.first_reject_tick))
        if now - previous_first <= hard:
            prev_streak = max(0, int(previous.streak))
            first_reject_tick = previous_first

    streak = prev_streak + 1
    cooldown = min(cap, base * (2 ** min(streak - 1, 3)))
    # onOrderRejected runs before respond() increments _tick. Including the
    # current next response in the blocked interval prevents an immediate retry.
    return ContractRejectState(
        streak=streak,
        first_reject_tick=first_reject_tick,
        last_reject_tick=now,
        blocked_until_tick=now + cooldown,
    )


def guard_is_active(
    state: ContractRejectState | None,
    *,
    current_tick: int,
    hard_lifetime_ticks: int = HARD_LIFETIME_TICKS,
) -> bool:
    """Return True while a pending-reprice episode is inside hard lifetime."""
    if state is None:
        return False
    now = max(0, int(current_tick))
    hard = max(32, min(2048, int(hard_lifetime_ticks)))
    age = now - max(0, int(state.first_reject_tick))
    return 0 <= age <= hard


def guard_should_skip(
    state: ContractRejectState | None,
    *,
    current_tick: int,
    hard_lifetime_ticks: int = HARD_LIFETIME_TICKS,
) -> bool:
    if not guard_is_active(
        state,
        current_tick=current_tick,
        hard_lifetime_ticks=hard_lifetime_ticks,
    ):
        return False
    return int(current_tick) <= int(state.blocked_until_tick)


def guarded_post_only_price(
    *,
    side: str,
    original_price: float,
    best_bid: float,
    best_ask: float,
    tick_size: float,
    reject_streak: int,
) -> float | None:
    """Return a safe post-only retry price from the *fresh* current touch.

    SELL is placed at least one tick above current best ask. BUY is placed at
    least one tick below current best bid. Repeated rejects add at most three
    ticks of cushion. A price already more passive is preserved.
    """
    original = _finite(original_price, -1.0)
    bid = _finite(best_bid, -1.0)
    ask = _finite(best_ask, -1.0)
    tick = _finite(tick_size, -1.0)
    if original <= 0.0 or bid <= 0.0 or ask <= 0.0 or ask < bid or tick <= 0.0:
        return None

    cushion = max(1, min(MAX_CUSHION_TICKS, int(reject_streak or 1)))
    token = str(side or "").strip().lower()
    if token == "sell":
        return max(original, ask + cushion * tick)
    if token == "buy":
        price = min(original, bid - cushion * tick)
        return price if price > 0.0 else None
    return None


def sanitize_post_only_limit_price(
    *,
    side: str,
    original_price: float,
    best_bid: float,
    best_ask: float,
    tick_size: float,
    safety_ticks: int = 1,
) -> float | None:
    """Reprice a Maker order against the latest authoritative L1.

    BUY must stay strictly below best ask. SELL must stay strictly above best
    bid. One safety tick is applied. Returns None when no legal price exists.
    """
    original = _finite(original_price, -1.0)
    bid = _finite(best_bid, -1.0)
    ask = _finite(best_ask, -1.0)
    tick = _finite(tick_size, -1.0)
    if original <= 0.0 or bid <= 0.0 or ask <= 0.0 or ask < bid or tick <= 0.0:
        return None
    cushion = max(1, int(safety_ticks or 1))
    token = str(side or "").strip().lower()
    if token == "buy":
        cap = ask - cushion * tick
        price = min(original, cap)
        if price <= 0.0 or price >= ask - 1e-12:
            return None
        return price
    if token == "sell":
        floor = bid + cushion * tick
        price = max(original, floor)
        if price <= bid + 1e-12:
            return None
        return price
    return None

