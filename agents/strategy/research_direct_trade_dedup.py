# SPDX-License-Identifier: MIT
"""A1.7.4.1 exact TradeEvent replay de-duplication.

The validator/simulator may replay the same TradeEvent in adjacent state updates,
including after a simulation clock/session rebase.  Strategy accounting must be
idempotent: an exact own fill can mutate FIFO inventory, realized PnL, Kappa,
fill learning and Direct lifecycle state at most once.

This module deliberately has no economic authority.  It only fingerprints exact
TradeEvent payload identity and keeps a bounded process-lifetime cache.
"""
from __future__ import annotations

from collections import deque
from hashlib import sha256
from typing import Any, Hashable

DIRECT_TRADE_DEDUP_VERSION = "direct_trade_dedup_v4_16_2_a1_7_4_1"
DIRECT_TRADE_DEDUP_MAX_EVENTS = 32768


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _float_token(value: Any) -> str | None:
    try:
        return float(value).hex()
    except (TypeError, ValueError):
        return None


def trade_event_identity(event: Any) -> tuple[Hashable, ...] | None:
    """Return a stable exact identity for a TradeEvent-like object.

    ``tradeId`` is simulator-assigned, but we intentionally include book,
    timestamp, both exchange order ids, both agents, side, quantity and price.
    This prevents accidental collapse of different executions if an id is ever
    reused, while exact replayed payloads resolve to the same key.
    """
    trade_id = _int_or_none(getattr(event, "tradeId", None))
    book_id = _int_or_none(getattr(event, "bookId", None))
    if trade_id is None or book_id is None:
        return None
    return (
        book_id,
        trade_id,
        _int_or_none(getattr(event, "timestamp", None)),
        _int_or_none(getattr(event, "clientOrderId", None)),
        _int_or_none(getattr(event, "makerAgentId", None)),
        _int_or_none(getattr(event, "makerOrderId", None)),
        _int_or_none(getattr(event, "takerAgentId", None)),
        _int_or_none(getattr(event, "takerOrderId", None)),
        _int_or_none(getattr(event, "side", None)),
        _float_token(getattr(event, "quantity", None)),
        _float_token(getattr(event, "price", None)),
    )


class DirectTradeEventDeduper:
    """Bounded FIFO exact-event cache.

    The cache is designed to live for the Direct agent process lifetime, not the
    simulator session lifetime.  A timestamp regression therefore does not clear
    replay protection.  Bounded FIFO eviction prevents unbounded memory growth.
    """

    def __init__(self, max_events: int = DIRECT_TRADE_DEDUP_MAX_EVENTS) -> None:
        self.max_events = max(1, int(max_events or 1))
        self._seen: set[tuple[Hashable, ...]] = set()
        self._fifo: deque[tuple[Hashable, ...]] = deque()

    def __len__(self) -> int:
        return len(self._seen)

    def check_and_note(self, event: Any) -> tuple[bool, tuple[Hashable, ...] | None]:
        identity = trade_event_identity(event)
        if identity is None:
            # Missing simulator identity: preserve existing behavior rather than
            # risk suppressing a legitimate fill.
            return False, None
        if identity in self._seen:
            return True, identity
        self._seen.add(identity)
        self._fifo.append(identity)
        while len(self._fifo) > self.max_events:
            old = self._fifo.popleft()
            self._seen.discard(old)
        return False, identity

    @staticmethod
    def identity_hash(identity: tuple[Hashable, ...] | None) -> str | None:
        if identity is None:
            return None
        return sha256(repr(identity).encode("utf-8")).hexdigest()[:16]
