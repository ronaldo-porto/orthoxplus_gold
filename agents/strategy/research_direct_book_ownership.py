# SPDX-License-Identifier: MIT
"""A1.7.4.3.1 same-book pending-order ownership helpers.

A1.7.4.3 reserves submitted orders across request/account-snapshot gaps.  This
module closes the remaining same-response gap: two authorities may append the
same book/side before the final pending ledger exists.  It also makes duplicate
pending keys fail safe by summing, never overwriting, reserved quantity.
"""
from __future__ import annotations

from research_direct_inflight_reservation import PendingExposureOrder

DIRECT_BOOK_OWNERSHIP_VERSION = "direct_book_ownership_v4_16_2_a1_7_4_3_1"


def canonical_order_side(side) -> str:
    token = str(side or "").strip().lower()
    if token in {"buy", "bid", "b", "0"} or token.endswith(".buy"):
        return "buy"
    if token in {"sell", "ask", "s", "1"} or token.endswith(".sell"):
        return "sell"
    return token


def ownership_key(book_id: int, side) -> tuple[int, str]:
    return (int(book_id), canonical_order_side(side))


def reserve_pending_order(
    ledger: dict[tuple[int, str, str], PendingExposureOrder],
    row: PendingExposureOrder,
) -> tuple[PendingExposureOrder, bool]:
    """Insert one local reservation; aggregate an accidental duplicate key.

    The final validator should prevent same-book/side duplication.  Quantity
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
