# SN79 St6.4 Research V4.13 — Simplified Kappa Productivity Engine

## Purpose

V4.13 is the first deliberate simplification release. It keeps the validated V4.12.18 inventory/contract safety core while replacing the hot-path scheduling emphasis with a small Kappa-productivity authority derived from Testnet evidence.

The decisive V4.12.18 examples were:
- Book115: 3 positive RTs in 12 placements, high Maker conversion, then wrongly abandoned after qualification.
- Book98: 194 placements for 2 RTs plus repeated contract rejects, yet it consumed most execution effort because nominal rebate/coverage remained attractive.

V4.13 therefore treats qualification as the start of density production, not the finish, and penalizes books that consume excessive instructions per realized RT.

## Simplified core

1. Cheap all-book scan using top-of-book only.
2. Kappa-productivity scheduler chooses the book.
3. Existing alpha stack is retained as side/execution evidence, not a competing scheduler.
4. V4.12.18 inventory/exit safety remains authoritative.
5. Persistent Maker lifecycle avoids signal-only cancel/reprice churn.
6. Contract/risk guard remains the final safety layer.

## Kappa-productivity state

- NEW: 0 observations
- BUILDING: 1–2 observations
- QUALIFIED: 3+ observations
- CORE: qualified plus fresh proven execution productivity

Scheduler phases:
- BOOTSTRAP: 0–40 Kappa-eligible books; 60% breadth / 25% completion / 15% density objective weights.
- BALANCED: 41–79; 30% / 35% / 35%.
- DENSITY: 80+; 15% / 20% / 65%.

A CORE book must have fresh V4.13 execution evidence; historical RT state without a V4.13 quote/fill ledger remains UNKNOWN rather than being falsely promoted.

## Productivity score

The scheduler combines:
- placements per completed RT;
- Maker fill conversion;
- realized outcome quality/loss rate;
- Maker rebate contribution;
- raw Kappa quality when available;
- recent RT freshness;
- contract-reject penalty.

Known inefficient books are demoted even if they are already cohort members or have attractive nominal rebate/EV. Unknown books retain an exploration path.

## Hot-path simplification

`book_touch_fingerprint()` is top-of-book only. The all-128-book Stage-1 scan no longer traverses event streams. Full event parsing remains Stage-2 work for selected/open books.

This specifically targets the previous screen-all-books latency tail and ineffective feature cache.

## Persistent Maker lifecycle

With `research_persistent_maker_enabled=1`, signal-only alpha/OFI/regime changes do not cancel an otherwise valid resting Maker. Reprice still occurs for:
- material price movement;
- TTL/staleness;
- inventory-state change;
- toxicity/hard safety;
- economic invalidation.

Default replacement threshold is 3 ticks.

## Latency-aware post-only safety

Opening/tightening prices maintain a configurable safety gap from the opposing touch. Default is 2 ticks (`research_post_only_safety_ticks=2`) to reduce fragile one-tick post-only rejects while staying competitive.

## Preserved V4.12.18 safety

Unchanged:
- protected parking / inventory-state decoupling;
- event-driven -8 / -12 bps liveness rescue;
- protected parked Maker floor;
- V4.12.14 authoritative-L1 contract guard;
- V4.12.10 unified bounded stale bridge;
- 250 ms velocity-STALE ONE_AWAY TTL;
- minimum 0.25 BASE cycle support;
- BaseStrategy and AdaptiveAgent.

Initial active concurrency remains 6 intentionally so V4.13 scheduler/latency behavior can be isolated before any 6→8→10 adaptive-concurrency experiment.

## Verification

- Research: 431 passed / 0 failed
- Base/Adaptive: 133 passed / 0 failed
- Shared strategy: 90 passed / 0 failed
- Total: 654 passed / 0 failed
- Focused V4.13: 11 passed / 0 failed
- Python compile: PASS
- root launcher syntax: PASS
- full root launcher preflight: PASS

Runtime promotion is not claimed until Testnet verifies CORE recycling, inefficient-book demotion, reduced churn/latency, and preserved inventory liveness.
