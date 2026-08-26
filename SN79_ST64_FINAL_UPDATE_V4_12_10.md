# SN79 St6.4 Final Blocker Fix — V4.12.10

**Date:** 2026-08-26

V4.12.10 is the single allowed blocker correction after the V4.12.9 UID68 runtime gate failed.

## Confirmed V4.12.9 blocker

The legacy hybrid/SN79 layer could produce a valid positive-EV direct Taker decision, but the final unified-exit layer recomputed total round-trip economics including the already-paid entry Maker fee and then returned `KEEP_MAKER`. The wrapper erased the direct authority. Runtime result: 2,649 Taker evaluations, 0 Taker orders/fills, while inventory-age p90 reached ~534 ticks.

The two calculations answer different questions:

- incremental Taker-vs-WAIT EV: the entry fee is sunk and common to both future choices;
- total round-trip realized PnL: the entry fee must remain included for score/risk accounting.

V4.12.10 keeps both. Incremental economics may authorize the switch, but total round-trip loss still has a hard ceiling.

## Exact fix

A `TAKER_STALE_BRIDGE` may override final `KEEP_MAKER` only when **all** conditions hold:

1. legacy direct Taker authority is already valid;
2. aggressive positive-EV authority is already valid;
3. SN79 action utility also says TAKE;
4. real Maker-fill evidence exists;
5. failed exits >= 8;
6. inventory age >= 16 ticks;
7. Maker fill probability <= 8%;
8. incremental Taker EV >= 0 bps;
9. incremental Taker EV beats incremental WAIT/Maker EV by >= 0.50 bps;
10. actual total round-trip Taker net >= **-12 bps**.

The -12 bps floor is hard-clamped in code. Runtime config may make it stricter but cannot widen it below -12 bps.

This is **not** a general negative-EV Taker or score-loss subsidy. Existing risk-direct Taker remains disabled and existing score-subsidy floors remain zero.

## UID68 log replay safety check

Replaying the V4.12.9 804-tick log through the new predicate finds only two first eligible stale positions:

- book 7, tick ~149: failed exits 131, age 133, Maker fill ~1.17%, incremental Taker EV +14.15 bps, total RT net -10.95 bps;
- book 8, tick ~208: failed exits 188, age 190, Maker fill ~1.29%, incremental Taker EV ~+0.004 bps, total RT net -5.17 bps.

So this change is deliberately narrow: it would have released two severely stale capital slots rather than opening broad Taker traffic.

## Frozen

- candidate_count = 10
- max_open_books = 6
- score target = 88
- qualified suppression trigger = 1 productive incomplete book
- stale Maker rescue floor = -1 bps
- normal protective Taker floor = -2 bps
- risk-direct Taker = OFF
- score-loss subsidy = OFF / 0 bps floors
- positive-EV incremental floor = 0 bps
- p95 latency target = 120 ms
- BaseStrategy and AdaptiveAgent unchanged

## Static/regression verification

- Research suite: **353 passed / 0 failed**
- Base + Adaptive: **133 passed / 0 failed**
- Shared strategy regressions: **90 passed / 0 failed**
- Total strategy-focused tests: **576 passed / 0 failed**
- Strategy Python compile: **PASS**
- Research launcher `bash -n`: **PASS**

Full repository collection is not claimed in this sandbox because optional runtime dependencies (`bittensor`, `transformers`, `pyarrow`, `loky`) are absent.

## Runtime release gate

V4.12.10 must still prove on Research/Testnet that Taker becomes non-zero without uncontrolled loss, stale inventory tail contracts materially, Kappa breadth stops decaying, and Maker PnL remains healthy. No further architecture iteration is authorized.
