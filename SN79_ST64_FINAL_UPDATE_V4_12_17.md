# SN79 St6.4 Final Update — Research V4.12.17

## Purpose

V4.12.17 is the **Kappa Flywheel Foundation** release. It converts the Research
agent from a mostly "reach three observations, then rotate" policy toward the
behavior required by the SN79 Kappa3 objective: build breadth first, then keep
high-quality qualified books producing additional minimum-size realized
observations while preserving strict downside controls.

This release is intentionally Research-only. It does not add new alpha
indicators and does not promote anything into BaseStrategy or AdaptiveAgent.

## Problems fixed from V4.12.15/V4.12.16

### 1. PARKED is a classification, not a six-slot resource

V4.12.15 could fill six parked slots and then reject every later park with
`PARK_CAP`, eventually recreating the old active-inventory bottleneck.

V4.12.17 removes PARK_CAP as an authority. The legacy
`research_max_parked_open_books=6` value remains only as a telemetry warning
threshold. Hard risk is still enforced by:

- max total open books: 12;
- max total absolute BASE: 3.0;
- existing per-book/inventory hard-risk gates.

A seventh stale position may therefore be classified PARKED without pretending
that the position disappeared from total risk.

### 2. Event-driven -8 -> -12 bps hard rescue

The previous hard rescue could become available too late because it depended on
age/failed-exit counters. V4.12.17 treats the price crossing itself as the
event:

- normal bounded Taker floor: -8 bps;
- if executable Taker PnL is below -8 but still at/above -12 bps, the hard
  rescue window is evaluated immediately;
- the absolute V4.12.17 liveness floor remains -12 bps and cannot be widened;
- below -12 bps the liveness authority PARKS rather than force-crossing.

The authority remains limited to `TWO_AWAY` and `UNCOVERED`; `ONE_AWAY` and
`QUALIFIED` remain protected from this loss subsidy.

### 3. True touch-defined aggressive Maker

For liveness-eligible stale inventory, `AGGRESSIVE_MAKER_EXIT` now means the
actual closest legal post-only touch rung. It is no longer pulled back to a
far-away breakeven/PnL-floor quote while still being labelled aggressive.

If the executable touch Maker price violates the bounded Maker floor:

- bounded Taker rescue is used only when independently authorized; otherwise
- the position is PARKED with `TOUCH_MAKER_BEYOND_FLOOR`.

Normal/protected exit paths retain their existing breakeven protection.

### 4. Restart-safe rolling realized-PnL authority

V4.12.16 ranked PnL readiness using `BookMemory.recent_pnl`, which is an EMA and
is not restart-authoritative. V4.12.17 adds
`research_kappa_flywheel.py` and persists per-book rolling nonzero realized-PnL
events in the Research session.

The flywheel derives:

- rolling realized count;
- positive / negative counts;
- rolling realized sum and mean;
- downside third moment;
- rolling loss rate;
- oldest/newest realized-PnL timestamps.

The module deliberately does **not** reimplement validator MAD-normalized
Kappa. Existing `raw_kappa` remains the Kappa source; rolling PnL is used for
restart-safe PnL readiness and density scheduling.

A legacy session that restores Kappa observation timestamps but has no rolling
PnL evidence fails closed rather than assuming `recent_pnl == 0` means
score-ready.

### 5. Breadth -> density Kappa flywheel

V4.12.17 adds three scheduling phases based on score-qualified breadth:

| Phase | Score-qualified books | Qualified density target | Core limit |
|---|---:|---:|---:|
| BOOTSTRAP | 0-40 | 6 observations | 8 |
| BREADTH | 41-79 | 12 observations | 24 |
| DENSITY | 80+ | 50 observations | 48 |

Qualified books are classified into density states including:

- `QUALIFIED_LOW_DENSITY`;
- `QUALIFIED_DEVELOPING`;
- `QUALIFIED_CORE`;
- `REFRESH_DUE`.

A qualified book that is still density-due can re-enter the completion lane;
qualification is no longer automatically the end of its lifecycle.

Dynamic default lane budgets are:

- BOOTSTRAP: coverage 4 / completion 3 / realization 3 / overflow 1;
- BREADTH: coverage 3 / completion 4 / realization 3 / overflow 1;
- DENSITY: coverage 2 / completion 5 / realization 3 / overflow 1.

The existing global candidate cap remains 10 and realization/risk selection
still has priority.

### 6. One exploration slot survives Kappa pressure

V4.12.16 could suppress all fresh uncovered books when conversion pressure was
active. V4.12.17 makes pressure depend only on true total-position headroom,
not parked-label count, and preserves at least one best fresh exploration book
while any total slot remains.

This prevents an unfillable ONE_AWAY/TWO_AWAY backlog from permanently freezing
breadth.

### 7. Fee/rebate-aware Kappa ordering

The existing lifecycle EV already includes live Maker fees, so V4.12.17 does
not double-count rebates. Maker fee/rebate is added as an explicit equal-EV
scheduler tie-breaker: more-negative Maker fee (larger rebate) wins when other
Kappa/economic evidence is comparable.

Default order size/sizing logic is unchanged.

### 8. Velocity-STALE ONE_AWAY TTL restored to 250 ms

The useful V4.12.16 stable ONE_AWAY touch cap of 1.5 bps is retained.

However, a `STALE` result from the adaptive TTL path represents high
microprice velocity, so the V4.12.16 900 ms stale override is removed. The
velocity-stale ONE_AWAY rescue is capped at 250 ms. Stable QUIET/exit TTL paths
remain separate and may still use their normal longer TTLs.

## Frozen components

Unchanged from the V4.12.16 input release:

- `research_contract_guard.py` — `authoritative_l1_contract_guard_v4_12_14`;
- `research_unified_exit.py` — `bounded_stale_bridge_v4_12_10`;
- `BaseStrategy.py`;
- `AdaptiveAgent.py`.

No new alpha signal, directional model, position-size escalation, or latency
optimization is included in V4.12.17.

## Deployment hygiene

The active Research launcher and `run_miner_multi.sh` are normalized to Linux LF.
The Research launcher now:

- enables `set -euo pipefail` before compatibility checks;
- resolves the repository when invoked from the root or version archive;
- provides `RESEARCH_PREFLIGHT_ONLY=1` for a no-miner-start validation pass;
- checks the V4.12.10, V4.12.11, V4.12.14 and V4.12.17 contracts before PM2.

## Static verification

- Research: **409 passed / 0 failed**
- Base/Adaptive: **133 passed / 0 failed**
- Shared strategy: **86 passed / 0 failed**
- Total strategy-focused: **628 passed / 0 failed**
- V4.12.17 focused Kappa-flywheel tests: **12 passed / 0 failed**
- Root launcher syntax: **PASS**
- Versioned launcher syntax: **PASS**
- Full launcher preflight-only run: **PASS**
- Python compile: **PASS** (release gate)

## Runtime promotion gate

V4.12.17 remains a Research candidate until a comparable live run proves:

1. no `PARK_CAP` liveness blocks;
2. acquisition does not collapse merely because parked inventory exceeds six;
3. event-driven rescue occurs inside the `-8 .. -12 bps` window when authorized;
4. no V4.12.17 liveness Taker crosses below the `-12 bps` absolute floor;
5. aggressive Maker exits are genuinely near live touch rather than far-away
   breakeven quotes;
6. at least one exploration slot survives Kappa conversion pressure;
7. qualified books continue accumulating density instead of stopping at three
   outcomes;
8. ONE_AWAY and the V4.12.14 contract guard do not regress;
9. marked inventory economics improve, not just headline realized PnL.

Latency remains a known separate blocker and is not claimed fixed in this release.
