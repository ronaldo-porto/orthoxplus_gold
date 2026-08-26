# SPDX-License-Identifier: MIT
"""Bounded post-only contract-rejection guard for Research V4.12.12.

The simulator returns CONTRACT_VIOLATION when a post-only limit would cross at
processing time (or when IOC/FOK conflicts with post-only). Research uses GTT
post-only Maker orders, so live CONTRACT_VIOLATION events are treated as a
stale-touch/post-only race.

The guard does not change normal Maker pricing. It activates only after a real
CONTRACT_VIOLATION for a specific (book, side):

1. suppress the immediate retry for a bounded number of ticks;
2. on the next retry, move the post-only quote away from the current opposite
   touch by 1..3 ticks, depending on the rejection streak;
3. clear the state as soon as an order on that book/side is accepted;
4. decay stale rejection state automatically.

Market/Taker orders are outside this helper by design.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

CONTRACT_GUARD_VERSION = "post_only_contract_guard_v4_12_12"
BASE_COOLDOWN_TICKS = 1
MAX_COOLDOWN_TICKS = 8
MAX_CUSHION_TICKS = 3
DECAY_TICKS = 32


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


@dataclass(frozen=True)
class ContractRejectState:
    streak: int
    last_reject_tick: int
    blocked_until_tick: int


def register_contract_reject(
    previous: ContractRejectState | None,
    *,
    current_tick: int,
    base_cooldown_ticks: int = BASE_COOLDOWN_TICKS,
    max_cooldown_ticks: int = MAX_COOLDOWN_TICKS,
) -> ContractRejectState:
    """Advance the rejection streak and compute bounded exponential cooldown."""
    now = max(0, int(current_tick))
    base = max(1, int(base_cooldown_ticks))
    cap = max(base, min(16, int(max_cooldown_ticks)))
    prev_streak = 0
    if previous is not None and now - int(previous.last_reject_tick) <= DECAY_TICKS:
        prev_streak = max(0, int(previous.streak))
    streak = prev_streak + 1
    cooldown = min(cap, base * (2 ** min(streak - 1, 3)))
    # onOrderRejected runs before respond() increments _tick. Including the
    # current next response in the blocked interval prevents an immediate retry.
    return ContractRejectState(
        streak=streak,
        last_reject_tick=now,
        blocked_until_tick=now + cooldown,
    )


def guard_is_active(state: ContractRejectState | None, *, current_tick: int) -> bool:
    if state is None:
        return False
    now = max(0, int(current_tick))
    return now - int(state.last_reject_tick) <= DECAY_TICKS


def guard_should_skip(state: ContractRejectState | None, *, current_tick: int) -> bool:
    if not guard_is_active(state, current_tick=current_tick):
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
    """Return a more passive post-only price after a real contract rejection.

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
