# Strategy1-Direct V4.16.2 A1.7.3 — Partial-Fill Completion + Portfolio Liveness Repair

## Purpose

A1.7.3 keeps the A1.7.1 MTM-risk correction and A1.7.2 TRUE-WAIT execution boundary unchanged. It fixes the long-run liveness failure observed in Agent67/68 where legal 0.25 BASE orders partially filled, the remaining sub-minimum quantity expired before the next publish, and persistent dust eventually consumed enough of the 2.0 BASE portfolio cap to starve new Maker entries.

## Changes

### 1. Preserve the original legal partial remainder

Direct entry Maker orders now receive a hard GTT lifetime long enough to survive publish-cycle observation (2.5s minimum, normally 3x publish interval, hard cap 4s). The quote manager still evaluates/cancels/reprices every request, so this does not make stale quotes authoritative.

When a fill leaves dust, A1.7.3 records a recovery plan:

- flat -> dust: preserve the same-side legal remainder until the position reaches an actionable minimum clip;
- actionable position -> same-sign dust: preserve the flattening remainder until flat;
- crossed-through-zero dust: cancel the old remainder because continuing it would increase opposite-side exposure.

No new sub-minimum order is ever submitted.

### 2. Cancel the wrong-side sibling, keep only the useful remainder

At the next state update A1.7.3 services the partial recovery before ordinary quote maintenance. It preserves only the live order whose direction moves inventory toward the recovery target and cancels conflicting live orders.

### 3. Reserve one recovery clip while dust exists

Fresh-entry admission reserves:

- 0.25 BASE absolute headroom; and
- one productive active/open slot

whenever any parked dust exists. Dust remains fully counted as real exposure; it is not hidden from the 2.0 BASE risk cap.

### 4. Normalize mathematically irreducible dust

For residuals below half the exchange minimum (<0.125 BASE when minimum=0.25), the old opposite-side minimum-order compactor cannot guarantee lower absolute exposure. If the original remainder is gone, A1.7.3 can place one same-sign 0.25 post-only Maker normalization order. A full fill converts the fragment into actionable inventory that the normal A1.7.2 exit controller can liquidate.

Only one normalizer is prioritized per request.

### 5. Warm-state liveness escape

A warm upgrade may already start in an A1.7.2 dust-saturated state where less than 0.25 BASE headroom remains. After 12 consecutive headroom-blocked ticks with candidates present, only the selected dust-recovery normalizer may use a temporary recovery overflow of at most 0.125 BASE. Fresh entries never receive this exception.

The one-clip reserve is intended to prevent new A1.7.3 sessions from reaching this emergency path.

## Frozen behavior

Unchanged:

- FastPath 20 cheap / 16 deep;
- 0.25 BASE acquisition size;
- 2.5 bps Maker-entry edge floor;
- Taker entry OFF;
- A1.7.1 true mid-mark MTM risk semantics;
- A1.7.2 TRUE-WAIT and negative-aggressive-Maker block;
- qualification logic;
- 2.0 BASE normal portfolio cap;
- 8 total / 6 active productive books;
- no adaptive 5–10 BASE sizing;
- no new alpha learner or score gate.

`Strategy1_Research.py`, `Strategy1.py`, and `research_direct_exit.py` remain byte-for-byte unchanged.

## New diagnostics

- `A173_PARTIAL_FILL_RECOVERY`
- `A173_PARTIAL_REMAINDER_HOLD`
- `A173_PARTIAL_REMAINDER_CANCEL`
- `A173_PARTIAL_FILL_RELEASE`
- `A173_DUST_NORMALIZE`
- `A173_LIVENESS_RECOVERY`

Existing `research_partial_fill_hold_candidates` / `research_partial_fill_hold_quoted` counters are now driven by actual Direct partial fills.

## Runtime acceptance

Run long enough to test the failure mode, preferably >=8,000 ticks.

Targets:

- persistent dust <= 2–3 books;
- parked dust exposure <0.15–0.20 BASE;
- partial-fill hold counters >0 when partial fills occur;
- no >100-tick candidate-present capital-starvation interval;
- RT velocity >=0.12/s initially, target >=0.15/s;
- Maker-ending RT positive >=90%;
- overall positive RT >=60%;
- realized RT PnL remains positive through long runtime;
- qualified books remain >=80 without late collapse;
- p95 response <120 ms.

Do not add Agent41-style adaptive Maker sizing until this liveness gate passes.
