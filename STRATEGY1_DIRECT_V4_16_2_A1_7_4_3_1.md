# Strategy1-Direct V4.16.2 A1.7.4.3.1 — Same-Book Pending Order Ownership

A1.7.4.3.1 is a narrow mechanical publisher repair on A1.7.4.3. Runtime evidence
showed the aggregate 2.0 BASE cap was repaired, but two same-side 0.25 orders
could still be emitted for one book inside the same response before the local
pending ledger was recorded, allowing Book2-style inventory stacking above one
intended clip.

## Changes

- Final validation owns `(book, side)` immediately after the first placement is accepted.
- A second placement on the same book/side in the same response is rejected with `SAME_REQUEST_BOOK_SIDE_OWNED`.
- A BUY and SELL may still coexist as the existing two-sided Maker batch.
- Acknowledged or A1.7.4.3 locally-pending orders continue to own the book across requests.
- Partial fills retain ownership until the reserved remainder is fully filled, cancelled, rejected, acknowledged by the exchange snapshot, or expires.
- Duplicate pending keys aggregate reserved quantity rather than overwriting it, providing a fail-safe against under-reservation.

## New diagnostics

- `A17431_BOOK_OWNERSHIP_RESERVE`
- `A17431_BOOK_OWNERSHIP_BLOCK`
- `A17431_BOOK_OWNERSHIP_RELEASE`
- `direct_book_ownership_reserves`
- `direct_book_ownership_blocks`
- `direct_book_ownership_releases`

## Frozen

A1.7.4.3 strict 2.0 BASE aggregate cap, A1.7.4.2 Kappa-safe dust guard,
A1.7.4.1 TradeEvent de-duplication, A1.7.4 tail recovery, A1.7.3.1 liveness,
FastPath 20/16, 0.25 BASE size, Taker entry OFF, and qualification/economic
thresholds are unchanged.
