# SN79 Research V4.12 — Simple Performance Core (Optimization Pass 1)

Research Agent only. BaseStrategy and AdaptiveAgent are unchanged.

## Why this update

The V4.11.2 live log showed four structural bottlenecks: one realized RT could appear as two rolling Kappa observations, all 12 selected books could be occupied by inventory, generic `POSITIVE_EV_OVERRIDE` admitted 0.25 orders with weak modeled exit capacity, and books could remain on a competitive Maker exit after dozens of failed attempts.

V4.12 deliberately improves the small execution core instead of adding more strategy states.

## Changes

1. **Single canonical Kappa observation source**
   - Raw `onTrade` timestamps are no longer inserted into rolling Kappa authority.
   - `realized_pnl_history` buckets are canonical.
   - Raw trade timestamps remain diagnostic only.
   - Session persistence stores the canonical history-derived rolling cache.
   - This removes the observed `0 -> 1 -> 2` from one RT failure mode.

2. **Inventory concurrency cap**
   - Default `research_max_open_books=6`.
   - Existing inventory/risk exits are never blocked.
   - When six books are non-flat, flat-book acquisition is removed from fast-screen/cohort eligibility.
   - A second same-response gate counts planned new books and emits `trigger=OPEN_BOOK_CAP`, preventing bursts from overshooting the cap.

3. **Concentrated candidate set**
   - Deep candidate count: 12 -> 10.
   - Sticky acquisition cohort: 10 -> 8.
   - Priority lanes remain risk/realization first, then completion, then coverage.

4. **Remove weak generic minimum-order override**
   - `research_positive_ev_min_order_override=0` by default.
   - The strict `ONE_AWAY_EXACT_MIN` path remains enabled.
   - This prevents a generic positive EV score from promoting 0.25 when modeled exit capacity is only ~45–60% of the exchange minimum.

5. **Failure-driven Maker escalation**
   - General book: after 8 failed Maker exits, force AGGRESSIVE/near-touch Maker pricing.
   - ONE_AWAY book: after 3 failed Maker exits, force AGGRESSIVE/near-touch Maker pricing.
   - Half-threshold failure counts first move the book from passive to competitive.
   - Positive-EV Taker authority remains unchanged and still requires non-negative Taker EV.

## Runtime defaults

- `research_candidate_count=10`
- `research_cohort_size=8`
- `research_max_open_books=6`
- `research_positive_ev_min_order_override=0`
- `research_one_away_exact_min_enabled=1`
- `research_maker_escalate_failed_exit_count=8`
- `research_one_away_maker_escalate_failed_exit_count=3`
- `research_enable_aggressive_positive_ev_taker=1`

## Expected live behavior

- A single RT should advance rolling Kappa by exactly one observation.
- `actual_nonflat` should drain toward <= 6; new flat entries should show `OPEN_BOOK_CAP` while saturated.
- Books with 40–90 failed exits should no longer stay `COMPETITIVE_MAKER_EXIT`; they should become `AGGRESSIVE_MAKER_EXIT`.
- Generic `POSITIVE_EV_OVERRIDE` should disappear from live logs.
- `ONE_AWAY_EXACT_MIN` should still appear for hard-safe 2/3 completion books.
- Positive-EV Taker remains selective; negative `taker_ev` never becomes a market order.

## Verification

- New V4.12 tests: 7/7 passed.
- Full Research test family: 336 passed, 5 failed.
- The same five failures are present in untouched V4.11.2 baseline; V4.12 adds zero new Research-suite failures.
- Python compilation passed.
- Launcher shell syntax passed.

## Next step

Run one clean V4.12 testnet cycle and validate the five runtime invariants above. Only after that should production log output be simplified.
