# SPDX-License-Identifier: MIT
"""A1.7.4.3.2 identity-safe same-book pending-order ownership helpers.

A1.7.4.3.1 prevents duplicate same-book/side placement inside one response and
keeps a local pending reservation across account-snapshot gaps. Runtime evidence
showed a deeper release bug: a stale cancellation for an older exchange order
could release ownership of a newer order that happened to share the same
book/side.  This module adds an exact exchange-order identity registry so only
the order named by a placement/cancellation/fill can release its ownership.
"""
from __future__ import annotations

from dataclasses import dataclass

from research_direct_inflight_reservation import PendingExposureOrder

DIRECT_BOOK_OWNERSHIP_VERSION = "direct_book_ownership_v4_16_2_a1_7_4_3_2"
DIRECT_ORDER_IDENTITY_MAX = 32768


def canonical_order_side(side) -> str:
    token = str(side or "").strip().lower()
    if token in {"buy", "bid", "b", "0"} or token.endswith(".buy"):
        return "buy"
    if token in {"sell", "ask", "s", "1"} or token.endswith(".sell"):
        return "sell"
    return token


def ownership_key(book_id: int, side) -> tuple[int, str]:
    return (int(book_id), canonical_order_side(side))


@dataclass
class ExchangeOrderIdentity:
    exchange_order_id: int
    book_id: int
    client_order_id: str
    side: str
    remaining_quantity: float = 0.0

    def pending_key(self) -> tuple[int, str, str]:
        return (int(self.book_id), str(self.client_order_id), canonical_order_side(self.side))


def register_exchange_identity(
    registry: dict[int, ExchangeOrderIdentity],
    *,
    exchange_order_id,
    book_id,
    client_order_id,
    side,
    remaining_quantity: float = 0.0,
    max_entries: int = DIRECT_ORDER_IDENTITY_MAX,
) -> ExchangeOrderIdentity | None:
    """Register exact exchange->client ownership without guessing missing ids."""
    if exchange_order_id is None or client_order_id is None or book_id is None:
        return None
    try:
        oid = int(exchange_order_id)
        bid = int(book_id)
        qty = max(0.0, float(remaining_quantity or 0.0))
    except (TypeError, ValueError):
        return None
    row = ExchangeOrderIdentity(
        exchange_order_id=oid,
        book_id=bid,
        client_order_id=str(client_order_id),
        side=canonical_order_side(side),
        remaining_quantity=qty,
    )
    registry[oid] = row
    # dict preserves insertion order; keep the correctness cache bounded.
    limit = max(128, int(max_entries or DIRECT_ORDER_IDENTITY_MAX))
    while len(registry) > limit:
        oldest = next(iter(registry))
        registry.pop(oldest, None)
    return row


def reduce_identity_quantity(identity: ExchangeOrderIdentity, fill_quantity: float, *, eps: float = 1e-12) -> float:
    remaining = max(0.0, float(identity.remaining_quantity or 0.0) - max(0.0, float(fill_quantity or 0.0)))
    identity.remaining_quantity = remaining
    return 0.0 if remaining <= float(eps) else remaining



def cancellation_identity_decision(
    registry: dict[int, ExchangeOrderIdentity],
    *,
    exchange_order_id,
    notice_book_id,
    success: bool,
) -> tuple[str, ExchangeOrderIdentity | None]:
    """Pure identity gate for cancellation-driven ownership release.

    Returns one of: RELEASE, STALE_UNKNOWN, BOOK_MISMATCH, FAILED_KEEP.
    Only RELEASE grants authority to release a current owner.
    """
    try:
        oid = int(exchange_order_id)
    except (TypeError, ValueError):
        return "STALE_UNKNOWN", None
    identity = registry.get(oid)
    if identity is None:
        return "STALE_UNKNOWN", None
    if notice_book_id is not None:
        try:
            if int(notice_book_id) != int(identity.book_id):
                return "BOOK_MISMATCH", identity
        except (TypeError, ValueError):
            return "BOOK_MISMATCH", identity
    if not bool(success):
        return "FAILED_KEEP", identity
    return "RELEASE", identity

def reserve_pending_order(
    ledger: dict[tuple[int, str, str], PendingExposureOrder],
    row: PendingExposureOrder,
) -> tuple[PendingExposureOrder, bool]:
    """Insert one local reservation; aggregate an accidental duplicate key.

    The final validator should prevent same-book/side duplication. Quantity
    aggregation is a second mechanical safety net so a duplicate client id can
    never overwrite and under-reserve already emitted exposure.
    """
    key = row.key()
    existing = ledger.get(key)
    if existing is None:
        ledger[key] = row
        return row, False
    existing.quantity = max(0.0, float(existing.quantity)) + max(0.0, float(row.quantity))
    existing.expiry_period_ns = max(int(existing.expiry_period_ns or 0), int(row.expiry_period_ns or 0))
    existing.submitted_tick = min(int(existing.submitted_tick), int(row.submitted_tick))
    old_ts = int(existing.submitted_timestamp_ns or 0)
    new_ts = int(row.submitted_timestamp_ns or 0)
    if old_ts <= 0:
        existing.submitted_timestamp_ns = new_ts
    elif new_ts > 0:
        existing.submitted_timestamp_ns = min(old_ts, new_ts)
    return existing, True
