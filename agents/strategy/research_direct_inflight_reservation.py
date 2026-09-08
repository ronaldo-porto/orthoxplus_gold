# SPDX-License-Identifier: MIT
"""A1.7.4.3 strict local in-flight exposure reservation helpers.

The validator account snapshot can lag an instruction by one request.  The
existing A1.6.3 reservation only sees acknowledged ``account.orders`` and can
therefore temporarily forget a just-submitted order.  These pure helpers define
a short-lived local bridge reservation until the exchange snapshot or a terminal
notice/fill takes ownership.
"""
from __future__ import annotations

from dataclasses import dataclass

DIRECT_INFLIGHT_RESERVATION_VERSION = "direct_inflight_reservation_v4_16_2_a1_7_4_3"
DIRECT_PENDING_LIMIT_FALLBACK_TICKS = 3
DIRECT_PENDING_MARKET_FALLBACK_TICKS = 2


@dataclass
class PendingExposureOrder:
    book_id: int
    side: str
    quantity: float
    client_order_id: int | str | None
    submitted_tick: int
    submitted_timestamp_ns: int
    expiry_period_ns: int
    order_kind: str

    def key(self) -> tuple[int, str, str]:
        cid = "" if self.client_order_id is None else str(self.client_order_id)
        return (int(self.book_id), cid, str(self.side).lower())


def pending_order_live(
    row: PendingExposureOrder,
    *,
    current_tick: int,
    current_timestamp_ns: int,
) -> bool:
    """Conservative liveness for a locally submitted but unacknowledged order."""
    tick_age = max(0, int(current_tick) - int(row.submitted_tick))
    kind = str(row.order_kind or "").upper()
    if "LIMIT" in kind and int(row.expiry_period_ns or 0) > 0:
        submitted = int(row.submitted_timestamp_ns or 0)
        now = int(current_timestamp_ns or 0)
        # A simulator timestamp reset must never prematurely release a pending
        # reservation.  Fall back to the bounded request-age guard instead.
        if submitted > 0 and now >= submitted:
            if now >= submitted + int(row.expiry_period_ns):
                return False
        return tick_age <= DIRECT_PENDING_LIMIT_FALLBACK_TICKS
    return tick_age <= DIRECT_PENDING_MARKET_FALLBACK_TICKS


def reduce_pending_quantity(row: PendingExposureOrder, fill_quantity: float, *, eps: float = 1e-12) -> float:
    """Apply one de-duplicated own fill to a local pending reservation."""
    remaining = max(0.0, float(row.quantity) - max(0.0, float(fill_quantity or 0.0)))
    row.quantity = remaining
    return 0.0 if remaining <= float(eps) else remaining
