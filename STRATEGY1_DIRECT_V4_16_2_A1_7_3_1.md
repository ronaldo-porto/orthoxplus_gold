# Strategy1-Direct V4.16.2 A1.7.3.1 — Bound Partial-Remainder Publisher Repair

## Purpose

A1.7.3.1 is a narrow mechanical correction to A1.7.3. It does **not** tune trading parameters. The 240-tick Agent68 observation proved that A1.7.3 correctly detected a partial exit on Book111, but the downstream publisher allowed the old dust-compaction path to replace the original legal remainder with a fresh minimum-size order.

Observed failure:

- Book111 inventory before fill: `+0.2500 BASE`
- Maker partial exit fill: `SELL 0.0498`
- inventory after fill: `+0.2002 BASE`
- original resting Maker order id: `754818`
- A1.7.3 decision: `EXIT_COMPLETE`, `preserve_existing_remainder=1`
- next request: the generic dust compactor was still allowed to create a fresh `SELL 0.25`

A full replacement fill could cross flat to `-0.0498 BASE` dust. A1.7.3.1 therefore gives the exact partially-filled resting order ownership of the book until its bounded hold ends.

## Changes

### 1. Bind recovery to the exact resting Maker order

The TradeEvent's `makerOrderId` is captured when a Maker partial fill creates sub-minimum inventory. Recovery state now stores:

- `bound_order_id`
- `hold_start_timestamp_ns`
- desired recovery side
- recovery target inventory

Only that exact order id can be treated as the legal partial remainder.

### 2. Publisher-level replacement block

While the bound remainder hold is active:

- theorem-safe opposite-side dust compaction is blocked;
- irreducible-dust normalization is blocked;
- new Maker-exit replacement placement is blocked;
- generic entry-quote cancellation cannot cancel the bound order;
- A1.7.2 WAIT cleanup cannot cancel the bound order.

Conflicting sibling orders, including same-side replacement batches, may be cancelled. The exact bound remainder remains protected.

### 3. Bounded simulator-time hold

The hold uses the existing A1.7.3 liveness parameters unchanged:

- minimum partial hold: 2.5 seconds
- publish multiplier: 3
- hard hold ceiling: 4.0 seconds

No threshold was changed from observation data. The current simulator timestamp is compared with the partial-fill timestamp. When the hard hold ends, ownership is released and the existing A1.7.3 dust-normalization/liveness fallback can take over.

### 4. Account-snapshot lag is handled conservatively

If the exact bound order is temporarily absent from the local account snapshot during the active hold, A1.7.3.1 still blocks a fresh full-clip replacement. This prevents the Book111 failure from recurring merely because the account snapshot trails the TradeEvent.

`research_partial_fill_hold_quoted` increments only when the exact bound order is actually visible and retained.

## Frozen behavior and parameters

Unchanged:

- FastPath: 20 cheap / 16 deep
- acquisition size: 0.25 BASE
- Maker-entry edge floor: 2.5 bps
- Taker entry: OFF
- normal max total absolute BASE: 2.0
- max total open books: 8
- max active books: 6
- A1.7.1 true-MTM risk semantics
- A1.7.2 TRUE-WAIT execution
- negative aggressive-Maker block
- A1.7.3 one-clip dust reserve
- A1.7.3 liveness trigger: 12 ticks
- A1.7.3 recovery overflow cap: 0.125 BASE
- qualification logic
- adaptive Maker sizing remains OFF

`Strategy1_Research.py`, `Strategy1.py`, `research_direct_exit.py`, and `research_direct_fastpath.py` remain byte-for-byte unchanged.

## Diagnostics

Existing A1.7.3 diagnostics remain. A1.7.3.1 adds:

- `A1731_PARTIAL_REMAINDER_PENDING`
- `A1731_PARTIAL_REMAINDER_EXPIRE`
- `A1731_PARTIAL_REPLACEMENT_BLOCK`

`A173_PARTIAL_FILL_RECOVERY` now includes `bound_order_id` and `hold_start_timestamp_ns`.

## Required runtime proof

The next observation run should prove the exact Book111 invariant:

1. a legal 0.25 order partially fills;
2. `A173_PARTIAL_FILL_RECOVERY` records the exact `bound_order_id`;
3. no fresh 0.25 replacement is submitted while the hold is active;
4. the exact remainder either continues filling or naturally expires;
5. only after hold expiry may A1.7.3 normalization/liveness fallback act.

After this short mechanical proof passes, resume the long >=8,000-tick A1.7.3 liveness validation before any parameter tuning or Agent41-style adaptive sizing.
