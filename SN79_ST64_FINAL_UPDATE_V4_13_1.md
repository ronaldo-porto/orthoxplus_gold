# SN79 St6.4 Research V4.13.1 — Kappa Productivity Scheduler Correction

## Purpose

V4.13.1 is the fast follow-up to the first V4.13 Testnet run. It changes only the two confirmed scheduler defects.

1. A Book92-like order sink could remain `UNKNOWN` forever because the sparse-evidence gate ran before the inefficiency gate.
2. Full `CORE` required three fresh RTs, but the scheduler rotated a good newly-qualified book before it could earn those three RTs.

No alpha, inventory-rescue, contract-guard, parking, sizing, or Base/Adaptive behavior is changed.

## Scheduler corrections

### Early order-sink demotion

A book is immediately `INEFFICIENT` when fresh runtime evidence shows either:
- at least 20 Maker quotes with zero fresh nonzero RTs;
- at least 20 Maker quotes with Maker fill conversion below 5%;
- at least 12 Maker quotes with contract-reject rate above 2.5%; or
- fresh placements per RT above 45 once a fresh RT exists.

This check runs before the sparse `UNKNOWN` gate.

### Fresh execution burden

Maker quote burden is compared only with fresh nonzero RTs recorded by the same V4.13+ runtime ledger. Restored lifetime RT history cannot make fresh churn look artificially efficient.

### CORE bootstrap bridge

A qualified book may earn one `RECYCLING` bridge slot after its first clean fresh positive RT when:
- observations >= 3;
- fresh RTs >= 1 and no fresh negative RT;
- Maker quotes >= 4 and Maker fills >= 2;
- placements/RT <= 15;
- Maker fill conversion >= 8%;
- contract reject rate <= 2%; and
- productivity score >= 0.30.

Only one recycling bridge is reserved globally. Full `CORE` still requires at least three fresh nonzero RTs plus the stricter productivity/loss gates.

## Preserved behavior

Unchanged:
- V4.12.18 inventory-state decoupling and protected parking;
- event-driven -8/-12 bps bounded rescue;
- V4.12.14 authoritative-L1 contract guard;
- V4.12.10 unified exit;
- 250 ms velocity-stale ONE_AWAY TTL;
- V4.13 cheap top-of-book scan;
- V4.13 persistent Maker lifecycle;
- V4.13 two-tick post-only safety buffer;
- 0.25 BASE minimum cycle sizing;
- BaseStrategy and AdaptiveAgent.

## Verification

- Focused V4.13.1: 14 passed / 0 failed
- Research: 434 passed / 0 failed
- Base/Adaptive + phase3 inheritance: 133 passed / 0 failed
- Shared strategy: 90 passed / 0 failed
- Total strategy-focused: 657 passed / 0 failed
- Python compile: PASS
- Root launcher syntax: PASS
- Full root launcher preflight: PASS

## Runtime target

Run 600–900 ticks. The decisive checks are:
- `productivity_inefficient > 0` for Book92-like sinks;
- `productivity_recycling_books > 0` after the first clean qualified RT;
- a recycled book advances beyond 3 observations;
- placements/fill remains <25;
- RT velocity remains >0.03/s;
- no inventory-liveness regression.
