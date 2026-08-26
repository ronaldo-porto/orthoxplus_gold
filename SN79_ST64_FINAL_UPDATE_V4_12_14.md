# SN79 St6.4 Final Update — Research V4.12.14

## Purpose

Fix the exact runtime disconnection that kept V4.12.13 at `REPRICE_RETRY=0` and produced hundreds of `NO_TOUCH_SKIP` events.

## Confirmed root cause

The Research launcher runs with `lazy_load=1`. The protocol's lazy decompression installs `LazyBooks`, which implements `collections.abc.Mapping` but is **not** a built-in `dict`. V4.12.13 used a built-in-dict-only lookup in two places:

```python
books.get(book_id) if isinstance(books, dict) else None
```

Therefore real lazy-loaded books were discarded as `None` even when the parent strategy had just used their bids/asks.

This caused two linked problems:

1. the post-only contract guard could not see authoritative L1 and emitted `NO_TOUCH_SKIP` instead of `REPRICE_RETRY`;
2. submitted-quote lifecycle snapshots lost `mid`, `spread` and touch-distance inputs, weakening fill-hazard/quote telemetry under lazy loading.

## V4.12.14 implementation

- Added `resolve_book_from_state_mapping()` supporting `Mapping`, `dict`, and mapping-like objects.
- Contract guard now reads the exact current L1 from `state.books`.
- Submitted-quote registration uses the same Mapping-safe resolver.
- No synthetic price reconstruction and no telemetry-price fallback.
- `NO_TOUCH_SKIP` now reports `no_touch_reason` and `books_type`.
- `REPRICE_RETRY` reports `touch_source=STATE_BOOKS_MAPPING` and `books_type`.
- All V4.12.13 liveness/backoff protections remain unchanged.

## Frozen

- V4.12.10 bounded stale Taker bridge
- V4.12.11 ONE_AWAY exact-min / stale-TTL rescue
- maker economics and alpha
- breadth/deadline scheduler
- candidate_count=10
- max_open_books=6
- BaseStrategy / AdaptiveAgent

## Verification

- Research: **378 passed**
- Base/Adaptive: **126 passed**
- Shared strategy: **93 passed**
- Total: **597 passed / 0 failed**
- Exact LazyBooks-like Mapping reproduction included
- Root launcher preflight verifies non-dict Mapping resolution before starting miner

## Runtime gate

A **30–45 real-minute** run should now be enough because the fix is deterministic. Required evidence:

- `REPRICE_RETRY > 0` when a guarded Maker retry occurs with L1;
- `touch_source=STATE_BOOKS_MAPPING`;
- `ACCEPT_CLEAR > 0` or legitimate lifecycle clear after a retry;
- `NO_TOUCH_SKIP` should be rare and have a genuine reason (`BOOK_NOT_FOUND`, `EMPTY_BIDS`, `EMPTY_ASKS`, `PRICE_MISSING`), not dominate hundreds of ticks;
- contract violation rate remains <0.5%;
- ONE_AWAY/Taker behavior does not regress.
