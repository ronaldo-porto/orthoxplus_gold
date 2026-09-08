# Strategy1-Direct V4.16.2 A1.7.4.3 — Strict In-Flight Exposure Reservation

A1.7.4.3 is a narrow mechanical-capacity patch on top of A1.7.4.2. The extended
Agent67 A1.7.4.2 runtime remained highly productive/profitable but showed the
configured `research_max_total_abs_base=2.0` was not a strict realized ceiling:
aggregate inventory was observed above 2.0 BASE (including roughly 2.25 and a
peak near 2.50 BASE). The existing A1.6.3 validator reserves orders already
visible in `account.orders`, but it had no local bridge for a placement that was
submitted in the previous response and had not yet appeared in the next account
snapshot.

## Change

A1.7.4.3 adds a local pending-placement reservation ledger at the final Direct
publisher boundary.

- Every final emitted LIMIT/MARKET placement is reserved immediately after
  final validation.
- The local reservation remains authoritative until the same client order is
  visible in `account.orders`, a terminal notice removes it, a de-duplicated own
  fill reduces/completes it, or the bounded exchange-lifetime fallback expires.
- Partial fills reduce the local reserved quantity rather than clearing the
  reservation completely.
- Simulator timestamp regression/reset cannot prematurely release a pending
  reservation; bounded tick-age fallback is used instead.
- `_direct_book_has_live_order()` now treats locally pending placements as book
  ownership, preventing a newer batch from racing an unacknowledged batch.
- Existing acknowledged account orders and local pending orders are merged into
  one worst-case directional BASE reservation before admission.
- The old A1.7.3 temporary recovery overflow is disabled at the Direct final
  admission boundary. `research_max_total_abs_base=2.0` is now intended as an
  absolute cap; recovery must use the existing reserved headroom instead.

Diagnostics:

- `A1743_INFLIGHT_RESERVE`
- `A1743_STRICT_EXPOSURE_BLOCK`
- `direct_pending_exposure_orders`
- `direct_pending_exposure_recorded`
- `direct_pending_exposure_acked`
- `direct_pending_exposure_expired`
- `direct_pending_exposure_fill_reductions`
- `direct_strict_exposure_blocks`

## Frozen

- A1.7.4.2 Kappa-safe dust compaction floor: -60 bps
- A1.7.4.1 exact own-TradeEvent de-duplication
- A1.7.4 tail recovery trigger/force: -8 / -12 bps
- A1.7.4 recovery Maker floors: -25 / -30 / -35 bps
- A1.7.3.1 bound partial-remainder publisher logic and dust reserve
- FastPath 20/16
- 0.25 BASE probe size
- Taker entry OFF
- qualification / score-target logic
- Strategy1 and Strategy1_Research base engines

## Validation target

The next runtime must show:

1. `research_total_abs_base <= 2.0` at every observed summary/fill reconstruction
   except pre-existing warm-start exposure that was already above cap before the
   candidate took control;
2. no new placement is admitted while `filled + acknowledged + local-pending`
   worst-case exposure would exceed 2.0 BASE;
3. partial fills do not release the unfilled reservation;
4. RT velocity, Maker quality, Kappa tail protection, and dust liveness remain
   materially unchanged from A1.7.4.2.
