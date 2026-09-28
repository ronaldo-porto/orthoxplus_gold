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
    # A1.9.7 P2: the venue sends a buy as the integer 0, and `str(side or "")`
    # turned that falsy zero into "".  Every buy identity was registered with
    # no side, its pending key never matched the "buy" reservation, and a buy
    # fill released nothing: 161 of 161 same-state buy fills on the A1.9.6.1
    # run waited three ticks for LOCAL_EXPIRY, while every sell released on
    # the fill.  A zero is still a side.
    token = "" if side is None else str(side).strip().lower()
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


def identity_key(book_id, exchange_order_id, *, book_keyed: bool = False):
    """The registry key of one exchange order.

    v6.6 S5: the venue numbers orders per book -- the same id is live on two books at once (mainnet 09-27: 72-73
    cancellations a day per UID landed on the other book's identity and were blocked, and a fill looked up by id alone
    could reduce another book's reservation).  Keyed by (book, id) every order is its own entry.  ``book_keyed`` False
    is the A1.7.4.3.2 key, the id alone.
    """
    oid = int(exchange_order_id)
    return (int(book_id), oid) if book_keyed else oid


def register_exchange_identity(
    registry: dict[int, ExchangeOrderIdentity],
    *,
    exchange_order_id,
    book_id,
    client_order_id,
    side,
    remaining_quantity: float = 0.0,
    max_entries: int = DIRECT_ORDER_IDENTITY_MAX,
    book_keyed: bool = False,
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
    registry[identity_key(bid, oid, book_keyed=book_keyed)] = row
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
    book_keyed: bool = False,
) -> tuple[str, ExchangeOrderIdentity | None]:
    """Pure identity gate for cancellation-driven ownership release.

    Returns one of: RELEASE, STALE_UNKNOWN, BOOK_MISMATCH, FAILED_KEEP.
    Only RELEASE grants authority to release a current owner.
    """
    try:
        oid = int(exchange_order_id)
    except (TypeError, ValueError):
        return "STALE_UNKNOWN", None
    if book_keyed:
        # v6.6 S5: a notice names its book; without one only an unambiguous id may stand for it.
        if notice_book_id is not None:
            try:
                identity = registry.get(identity_key(notice_book_id, oid, book_keyed=True))
            except (TypeError, ValueError):
                return "STALE_UNKNOWN", None
            if identity is None:
                return "STALE_UNKNOWN", None
            return ("RELEASE" if bool(success) else "FAILED_KEEP"), identity
        found = [row for key, row in registry.items() if isinstance(key, tuple) and key[1] == oid]
        if len(found) != 1:
            return "STALE_UNKNOWN", None
        return ("RELEASE" if bool(success) else "FAILED_KEEP"), found[0]
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
