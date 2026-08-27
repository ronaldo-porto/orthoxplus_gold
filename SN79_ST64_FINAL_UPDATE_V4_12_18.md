# SN79 St6.4 Research V4.12.18 — Inventory-State Decoupling

## Purpose

V4.12.17 proved that Kappa loss protection and inventory-capacity protection were incorrectly coupled. ONE_AWAY / QUALIFIED inventory could remain loss-protected but also consume all six active realization slots for thousands of ticks, stopping coverage and the Kappa flywheel.

V4.12.18 fixes that specific deadlock without widening protected-book loss authority.

## Core policy

### Separate loss authority from parking authority

- `UNCOVERED` / `TWO_AWAY`: bounded-loss rescue eligible **and** park eligible.
- `ONE_AWAY` / `QUALIFIED`: bounded-loss rescue **not** granted by V4.12.18, but park eligible.
- Protected books may transition to `PARKED_PROTECTED` and release active capacity when their closest legal post-only touch exit violates the existing protected Maker floor.
- Liveness books use `PARKED_LIVENESS` when bounded rescue is not currently safe.

`KAPPA_BLOCKED_LOSS` may therefore block a loss realization, but it can no longer block the capacity-release decision.

### Protected parked Maker invariant

On a new protected park:

1. cancel known accepted resting orders for that book;
2. submit no replacement in the same response;
3. on future refresh, recompute the closest legal post-only exit from current L1;
4. submit only if estimated round-trip Maker net bps is at or above the stored protected floor;
5. otherwise emit `PROTECTED_REFRESH_BLOCKED` and remain parked.

This prevents a parked protected position from later realizing a large Maker loss through a stale floor-violating refresh.

### Park refresh / churn

- Default parked refresh interval: 25 ticks.
- Refresh can also occur on material touch/risk conditions already present in the liveness engine.
- Unsafe protected refreshes hold rather than cancel/replace toward an unacceptable loss.

### Flywheel migration authority

The scheduler now separates:

- `kappa_eligible`: observation count meets the Kappa eligibility threshold;
- PnL authority confidence: `FULL`, `PARTIAL`, or `UNKNOWN`.

A session upgraded from an older version can retain valid Kappa observation progress even if the new rolling realized-PnL ledger is incomplete. Missing PnL history is **not fabricated**; it lowers ranking confidence instead of making every restored Kappa-eligible book unusable for Flywheel core selection.

Confidence multipliers:

- `FULL`: 1.00
- `PARTIAL`: 0.85
- `UNKNOWN`: 0.70

### Exploration fail-open

The V4.12.17 one-slot exploration invariant remains: when total-open and aggregate-exposure headroom allow a new position, Kappa conversion pressure cannot reduce fresh exploration capacity to zero indefinitely.

## Preserved contracts

V4.12.18 does **not** change:

- V4.12.14 authoritative-L1 post-only contract guard;
- V4.12.10 unified bounded stale/Taker bridge;
- 250 ms velocity-STALE ONE_AWAY rescue TTL;
- minimum-size / exact-minimum ONE_AWAY behavior;
- BaseStrategy;
- AdaptiveAgent;
- existing signal/alpha stack;
- standard 0.25 BASE Kappa-cycle sizing defaults.

## Main telemetry

New/strengthened events and fields include:

- `PARK_PROTECTED`
- `PARK_LIVENESS`
- `PARK_CANCEL`
- `PARK_CANCEL_BLOCKED`
- `PROTECTED_REFRESH_BLOCKED`
- `PROTECTED_REFRESH_SUBMIT`
- `PARK_REFRESH_SUBMIT`
- `UNPARK_EXECUTABLE`
- park state / protected floor / loss-rescue and park eligibility
- Kappa eligibility and PnL-confidence fields

## Verification

- Research tests: **420 passed / 0 failed**
- Base/Adaptive tests: **133 passed / 0 failed**
- Shared strategy tests: **90 passed / 0 failed**
- Total strategy-focused: **643 passed / 0 failed**
- Focused V4.12.18 tests: **11 passed / 0 failed**
- Python compile: PASS
- Root launcher syntax: PASS
- Versioned launcher syntax: PASS
- `RESEARCH_PREFLIGHT_ONLY=1`: PASS

Runtime promotion is intentionally not claimed until live SN79 logs verify the state transitions.

## First live gate

Run approximately 45–60 real minutes / 600–900 ticks initially. Require:

1. stale/non-executable ONE_AWAY or QUALIFIED inventory can emit `PARK_PROTECTED` and release an active slot;
2. protected books receive no new bounded-loss Taker subsidy from this patch;
3. no parked Maker fill occurs below its logged `protected_floor_bps`;
4. unsafe touch emits `PROTECTED_REFRESH_BLOCKED`; a later safe touch may emit `UNPARK_EXECUTABLE` + `PROTECTED_REFRESH_SUBMIT`;
5. acquisition does not remain at zero for >20 ticks when total hard exposure permits another position;
6. restored Kappa-eligible books produce a non-zero Flywheel candidate/core set even when PnL confidence is `PARTIAL`/`UNKNOWN`;
7. qualified books continue receiving observations beyond three;
8. contract rejection remains <0.5%;
9. velocity-STALE ONE_AWAY TTL remains 250 ms;
10. bounded liveness Taker never breaches the immutable -12 bps hard floor.
