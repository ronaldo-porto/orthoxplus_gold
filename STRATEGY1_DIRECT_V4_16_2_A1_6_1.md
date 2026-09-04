# Strategy1-Direct V4.16.2 A1.6.1 — Liveness Repair

## Scope

A1.6.1 is a narrow long-run liveness repair for the A1.6.0 Observable FastPath candidate. It does not change the proven entry/exit economics, acquisition size, inventory absolute cap, FastPath width, Maker TTL, or Taker-entry policy.

## Runtime evidence that triggered the patch

The 4,229-tick A1.6.0 Agent68 run retained strong trade quality but progressively lost activity. Late-run state reached roughly 15 parked dust books and ~1.54 BASE of dust inside the 2.0 BASE absolute cap. Dust compaction counters stayed at zero, while forced inventory consumed most deep FastPath capacity.

## Changes

1. **Direct dust compaction is now executable.**
   - A1.6.0 inherited the Research dust selector but skipped dust before the inherited placement path could run.
   - A1.6.1 explicitly services selector-approved dust.
   - Only theorem-safe residuals in `(0.5 * min_order, min_order)` are compacted with a minimum-size passive opposite-side order.
   - Exact absolute BASE accounting is preserved.

2. **Dust is removed from productive FastPath/deep capacity.**
   - Sub-minimum dust is never forced into the 20-candidate / 16-deep acquisition lane.
   - All dust books are excluded from productive open-book slot counting.
   - Dust still counts in `total_abs_base_inventory`, so the hard 2.0 BASE absolute risk cap remains authoritative.

3. **Non-placement instructions bypass order-quantity validation.**
   - The inherited final validator assumes an order `quantity` field.
   - `CANCEL_ORDERS` has no order quantity and could be reported as `INVALID_QTY`.
   - A1.6.1 sends only `PLACE_ORDER_LIMIT` / `PLACE_ORDER_MARKET` through the authoritative order validator and preserves cancellation/control instructions unchanged.

## Frozen A1.6.0 behavior

Unchanged:
- current observable Maker entry economics;
- Maker minimum current edge;
- 20 FastPath candidates / 16 deep candidates;
- 0.25 BASE acquisition size;
- 2.0 BASE absolute portfolio cap;
- 6 bps Maker geometry cap;
- 75 ms acquisition Maker TTL;
- directional Taker entry disabled;
- observable NORMAL/DEFENSIVE Maker/Wait/Taker exit rules;
- HARD_ESCAPE / ABSOLUTE protection behavior.

No new learned model or posterior was introduced.

## Validation

- A1.6.1-specific tests: **25 passed**
- launcher focused preflight: **95 passed**
- Research regression suite: **485 passed, 3 skipped**
- `compileall`: PASS
- launcher shell syntax: PASS
- Frozen strategy hashes verified unchanged for `Strategy1.py`, `Strategy1_Research.py`, `BaseStrategy.py`, `AdaptiveAgent.py`, `research_direct_economics.py`, and `research_direct_exit.py`.

## First runtime gate

Run 500–1,000 ticks first. Required evidence:
- `research_dust_compact_attempts > 0` and orders appear when compactable dust exists;
- parked dust BASE/books stop monotonically increasing;
- dust does not appear in forced/deep FastPath inventory;
- RT velocity recovers above 0.08/s, target >0.10/s;
- positive RT >=60%;
- Maker→Maker >=85%;
- p95 response <120 ms;
- no new capacity deadlock.

If that gate passes, continue the same process to 2,000–4,000 ticks before any sizing/persistence upgrade.
