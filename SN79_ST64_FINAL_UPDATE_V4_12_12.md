# SN79 St6.4 Research V4.12.12 — Post-Only Contract-Reject Guard

**Date:** 2026-08-26  
**Scope:** Research Agent only. BaseStrategy and AdaptiveAgent are unchanged.

## Confirmed live blocker

The V4.12.11 UID68 run showed repeated `LimitOrderPlacementEvent` failures with
`CONTRACT_VIOLATION`, all on SELL Maker orders. One Book 43 episode retried the
same post-only SELL for dozens of consecutive ticks. This wasted instruction
capacity and prolonged inventory realization even though ONE_AWAY completion and
the V4.12.10 Taker bridge were already functioning.

Simulator inspection shows Research uses GTT post-only limit orders; for that
path, `CONTRACT_VIOLATION` is consistent with a post-only order crossing the live
book by the time the simulator processes it. This is a stale-touch race, not a
reason to widen Taker authority or change alpha.

## V4.12.12 fix

New helper: `agents/strategy/research_contract_guard.py`

Policy markers:

- `RESEARCH_POLICY_VERSION = "post_only_reject_guard_v4_12_12"`
- `CONTRACT_GUARD_VERSION = "post_only_contract_guard_v4_12_12"`

The guard is dormant until a **real** `CONTRACT_VIOLATION` is received for one
specific `(book, side)`.

After a rejection:

1. immediate same-book/side post-only Maker retry is suppressed;
2. repeated rejects use bounded exponential cooldown: **1 -> 2 -> 4 -> 8 ticks**;
3. after cooldown, SELL retry is at least current best ask + 1..3 ticks and BUY
   retry is at most current best bid - 1..3 ticks;
4. an already-more-passive price is preserved;
5. accepted limit order clears the guard immediately;
6. stale guard state expires after 32 ticks;
7. missing live touch fails closed while the guard is active instead of blindly
   resubmitting the rejected quote.

## Hard scope boundary

The guard does **not** alter:

- Market/Taker orders;
- V4.12.10 bounded stale Taker bridge;
- V4.12.11 ONE_AWAY exact-min/TTL rescue;
- alpha/signals;
- Kappa scheduler/breadth target;
- candidate count (`10`);
- max open books (`6`);
- BaseStrategy;
- AdaptiveAgent.

## Telemetry

New `CONTRACT_REJECT_GUARD` records expose:

- `REGISTER_REJECT`
- `COOLDOWN_SKIP`
- `NO_TOUCH_SKIP`
- `REPRICE_RETRY`
- `ACCEPT_CLEAR`

Run summary fields:

- `research_contract_rejects`
- `research_contract_guard_skips`
- `research_contract_guard_reprices`
- `research_contract_guard_accept_clears`
- `research_contract_guard_active`
- `research_contract_guard_version`

## Static verification

- Research tests: **366 passed / 0 failed**
- Base/Adaptive + shared strategy regressions: **212 passed / 0 failed**
- Total strategy-focused: **578 passed / 0 failed**
- Python compile: **PASS**
- Research/Base/Adaptive launcher `bash -n`: **PASS**
- V4.12.10 stale bridge preflight: **PASS**
- V4.12.11 ONE_AWAY preflight: **PASS**
- V4.12.12 contract guard preflight: **PASS**
- BaseStrategy SHA unchanged: **PASS**
- AdaptiveAgent SHA unchanged: **PASS**

## Next live verification

A **45–60 real minute** Research run is enough for this deterministic bug fix.
If no contract reject occurs in that interval, continue until the first few
rejects or at most ~90 minutes.

PASS criteria:

1. no repeated same-book/side `CONTRACT_VIOLATION` loop;
2. after a reject, telemetry shows `REGISTER_REJECT` followed by
   `COOLDOWN_SKIP` and/or `REPRICE_RETRY`, then ideally `ACCEPT_CLEAR`;
3. no run of more than 3 consecutive contract rejects for one `(book, side)`;
4. total contract-reject rate falls materially from V4.12.11 (target <0.5% of
   limit placement events, but loop elimination is the primary gate);
5. Market/Taker activity is unchanged by the guard;
6. ONE_AWAY backlog/qualification behavior does not regress.

Runtime promotion is **not** claimed until this check passes.
