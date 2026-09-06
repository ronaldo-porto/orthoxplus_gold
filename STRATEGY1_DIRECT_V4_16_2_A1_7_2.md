# Strategy1-Direct V4.16.2 A1.7.2 — True-WAIT Execution Repair

## Purpose

A1.7.2 is a narrow follow-up to A1.7.1. Runtime evidence from Agent67 and the older warm Agent68 run showed that A1.7.1 correctly separated true MTM risk from Taker liquidation cost, but the frozen realization loop still mapped every non-Taker Direct decision back to a legacy Maker rung. As a result, `WAIT` could become `AGGRESSIVE_MAKER_EXIT` and realize a loss even though the Direct authority explicitly rejected both negative Maker/Taker completion.

A1.7.2 fixes that execution leak without changing FastPath, acquisition size, entry economics, qualification logic, quote-manager entry behavior, or Taker-entry policy.

## Root causes fixed

### 1. WAIT was not terminal

The frozen base returns a legacy Maker rung for every non-Taker Direct decision. A1.7.2 records the current-tick Direct authority and enforces it again at the final Maker-placement boundary.

When Direct selects `WAIT`:

- the outward realization token is rewritten to `WAIT`;
- no new Maker exit is placed;
- stale close-side resting exits with current lifecycle net below +1 bps are cancelled;
- an already-resting profitable Maker exit may keep its queue position;
- a WAIT cancellation is not counted as a failed realization attempt.

### 2. Negative AGGRESSIVE Maker was a hidden loss-realization path

`AGGRESSIVE_MAKER_EXIT` is now blocked when the exact lifecycle `maker_net_bps` passed to final placement is negative. Positive aggressive Maker exits remain available.

### 3. MTM-only ABSOLUTE threw away obviously profitable Maker exits

A1.7.2 preserves A1.7.1 true-MTM risk classification but distinguishes MTM-only ABSOLUTE from catastrophic/MAX exposure:

- MTM-only `ABSOLUTE_PROTECTION` + executable Maker >= +1 bps gets one bounded Maker attempt;
- after one failed attempt, hard reduction is released;
- catastrophic/MAX exposure bypasses the grace and keeps immediate Taker reduction authority.

## Frozen behavior

Unchanged from A1.7.1:

- FastPath: 20 cheap / 16 deep;
- base Maker acquisition size: 0.25 BASE;
- Maker entry edge floor: 2.5 bps;
- Taker entry: OFF;
- touch-improvement cap: 6 bps;
- persistent entry quote manager;
- exposure synchronization and one-live-order-batch contract;
- open-book / total-absolute-base limits;
- qualification and persistence logic;
- no new learner, posterior, score gate, or adaptive sizing.

`Strategy1_Research.py` and `Strategy1.py` remain byte-for-byte frozen.

## New runtime diagnostics

- `A172_EXIT_AUTHORITY`: Direct action versus outward realization action.
- `A172_WAIT_HOLD`: proves WAIT placed no new Maker order.
- `A172_WAIT_CANCEL`: stale negative close-side resting exits cancelled while profitable ones are retained.
- `A172_NEGATIVE_AGGRESSIVE_BLOCK`: negative aggressive Maker realization blocked.

`A171_EXIT_DIAGNOSTIC` is retained for cross-version risk/economics comparison.

## Regression requirements

The A1.7.2 tests prove:

1. A1.7.1 true-MTM risk semantics remain intact;
2. MTM-only ABSOLUTE receives one profitable Maker chance;
3. that grace releases after one failed attempt;
4. catastrophic/MAX exposure bypasses the grace;
5. Direct WAIT is rewritten as an explicit terminal WAIT token;
6. WAIT returns before frozen Maker placement;
7. WAIT cancellation evaluates the actual resting order lifecycle economics;
8. profitable resting Maker exits are preserved;
9. negative `AGGRESSIVE_MAKER_EXIT` returns before frozen placement;
10. positive aggressive Maker remains available;
11. frozen `Strategy1_Research.py` contains no A1.7.2 patch.

## Testnet acceptance gate

Run at least 600–1,000 ticks before changing strategy architecture.

Primary targets:

- positive RT >= 55%, target >= 60%;
- Maker-ending positive >= 75%;
- risk-Taker-ending RT share < 25%, target < 20%;
- `WAIT -> actual Maker close` leakage = 0;
- negative `AGGRESSIVE_MAKER_EXIT` placements = 0;
- realized RT PnL > 0;
- RT velocity >= 0.15/s;
- qualified breadth continues beyond ~60 books;
- p95 response < 120 ms.

Do not add Agent41-style adaptive 5–10 BASE Maker scaling until this gate passes. Scaling before exit quality is positive would only magnify lifecycle losses.
