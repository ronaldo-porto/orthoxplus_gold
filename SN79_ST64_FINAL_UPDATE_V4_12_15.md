# SN79 St6.4 Final Update — Research V4.12.15

## Purpose

Fix the tick-600 inventory-liveness failure observed on V4.12.14 without changing the successful V4.12.14 contract guard, ONE_AWAY completion policy, normal profitable Taker authority, alpha/entry engine, or breadth scheduler.

V4.12.14 correctly repaired LazyBooks/L1 handling, but the live run still reached `6/6` open inventory slots, collapsed acquisition to zero, and allowed TWO_AWAY/uncovered positions with early bounded exits near roughly `-6.5 .. -12 bps` to deteriorate into much larger losses because the score path retained an effective zero-loss veto.

## V4.12.15 design

### 1. Explicit inventory-liveness authority

A new independent helper, `research_inventory_liveness.py`, classifies inventory by score state and age/failed-exit stage.

Loss subsidy is intentionally narrow:

- `QUALIFIED`: protected; no V4.12.15 loss subsidy.
- `ONE_AWAY`: protected; no V4.12.15 loss subsidy.
- `TWO_AWAY`: eligible.
- `UNCOVERED`: eligible.

Default stages:

- Maker rescue: failed exits >= 3 or age >= 8 ticks.
- Taker rescue: failed exits >= 8 or age >= 16 ticks.
- Hard rescue window: stop-loss / `EXIT_ONLY` / `EMERGENCY`, failed exits >= 12, or age >= 24 ticks.

### 2. Bounded rescue floors

- Maker rescue floor: `-4 bps`.
- Soft Taker rescue floor: `-8 bps`.
- Absolute hard Taker rescue floor: `-12 bps`.
- Config cannot widen the absolute floor beyond `-12 bps`.
- Taker rescue additionally requires adverse evidence and Taker value to beat WaitEV by at least `0.5 bps`.
- If the current executable loss has already missed the active rescue window, the position is parked rather than dumped.
- A position already beyond the absolute `-12 bps` V4.12.15 floor is never force-crossed by this new liveness authority.

Pre-existing catastrophic/max-inventory risk behavior outside the V4.12.15 authority is unchanged.

### 3. Executable Maker escalation

For eligible stale inventory, V4.12.15 forces the existing `AGGRESSIVE_MAKER_EXIT` price rung, which is defined from current L1 touch, then applies the bounded Maker loss floor. This prevents a far-away breakeven quote from being treated as an aggressive exit.

### 4. Active vs parked inventory capacity

The old single six-book acquisition bottleneck is split into conservative limits:

- max active open books: `6`
- max parked books: `6`
- max total open books: `12`
- max total absolute BASE exposure: `3.0`

Parked inventory releases an **active acquisition slot**, but still counts against total book and aggregate BASE risk caps. Planned same-response opens are also included in the gate.

### 5. Parked-position low-churn refresh

Parked positions are not scheduled as active realization every request. They are reconsidered on:

- 20-tick default refresh interval (config clamped to 10..30),
- material mid/touch move (default 8 bps), or
- hard-risk override.

A newly parked position does not add another Maker order in the same response; any already accepted resting order is left alone.

### 6. Telemetry

Added explicit events/counters:

- `RESCUE_ARMED`
- `RESCUE_MAKER`
- `RESCUE_TAKER`
- `RESCUE_BLOCKED`
- `PARK_POSITION`
- `PARK_REFRESH`
- `PARKED_HOLD`
- `UNPARK_POSITION`
- active / parked / total open-book counts
- total absolute BASE exposure

## Frozen components

Byte-for-byte unchanged from V4.12.14:

- `research_contract_guard.py` (`authoritative_l1_contract_guard_v4_12_14`)
- `research_unified_exit.py` (`bounded_stale_bridge_v4_12_10`)
- `research_quote_hysteresis.py` / ONE_AWAY stale-TTL policy
- `BaseStrategy.py`
- `AdaptiveAgent.py`

The normal profitable Taker path, score scheduler/ranking, entry alpha, quote sizing, and candidate count remain unchanged.

## Static verification

- Research: **391 passed / 0 failed**
- Base/Adaptive: **126 passed / 0 failed**
- Shared strategy: **93 passed / 0 failed**
- Total strategy-focused: **610 passed / 0 failed**
- V4.12.15 focused inventory-liveness tests: **13 passed / 0 failed**
- Python compile: **PASS**
- root/compatibility/versioned launcher syntax: **PASS**
- V4.12.10 bridge preflight: **PASS**
- V4.12.11 ONE_AWAY preflight: **PASS**
- V4.12.14 authoritative-L1 guard preflight: **PASS**
- V4.12.15 inventory-liveness preflight: **PASS**

## Runtime promotion gate

Run Research for roughly 45–60 real minutes / 600–900 ticks.

Required evidence before promotion:

- stale parked inventory never drives acquisition cohort to zero by itself;
- active open books may remain capped at 6 while total open books can exceed 6 only under the total/risk caps;
- bounded `RESCUE_TAKER` occurs only at or above its logged floor;
- no V4.12.15 rescue Taker below `-12 bps`;
- positions beyond the active rescue floor produce `PARK_POSITION` instead of endless per-tick realization churn;
- no `EMERGENCY` TWO_AWAY/uncovered full-size position remains active for hundreds of ticks with a zero-loss veto;
- actionable OPEN->FLAT p90 target `<150 ticks`;
- final-interval placements/fill target `<40`;
- contract rejection rate remains `<0.5%`;
- ONE_AWAY, normal Taker, PnL, and score-qualified trajectory do not regress.

V4.12.15 is a Research candidate only. No BaseStrategy/AdaptiveAgent promotion is claimed.
