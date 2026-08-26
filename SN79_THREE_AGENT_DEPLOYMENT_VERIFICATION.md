# SN79 Three-Agent Deployment Verification

## Verdict

**Research V4.12.17: STATIC PASS / runtime Kappa-flywheel verification pending.** BaseStrategy and AdaptiveAgent are unchanged.

## Research V4.12.17

V4.12.17 is the Kappa Flywheel Foundation candidate. It keeps the successful V4.12.14 contract guard and V4.12.10 bounded stale bridge while correcting the inventory/scheduler defects exposed by V4.12.15/V4.12.16.

Key release contracts:

- PARKED is a state classification; six parked labels cannot veto book #7+.
- Total risk remains bounded by 12 open books / 3.0 absolute BASE by default.
- TWO_AWAY/UNCOVERED rescue uses `-8 bps` normal floor and an event-driven `-12 bps` hard window.
- ONE_AWAY/QUALIFIED remain loss-subsidy protected.
- Aggressive Maker is the actual post-only touch price; if that price violates its floor, the strategy uses separately authorized bounded Taker rescue or PARK.
- Kappa scheduling now has BOOTSTRAP/BREADTH/DENSITY phases and continues cycling qualified low-density/core books.
- One fresh exploration slot survives Kappa conversion pressure while total headroom remains.
- Rolling per-book realized-PnL evidence is persisted across restart.
- Stable ONE_AWAY 1.5 bps tightening remains; velocity-STALE override is capped at 250 ms.
- Maker fee/rebate is used as a scheduler tie-breaker on otherwise comparable opportunities.

## Frozen components

- `BaseStrategy.py`: byte-for-byte unchanged from V4.12.16 input.
- `AdaptiveAgent.py`: byte-for-byte unchanged from V4.12.16 input.
- `research_contract_guard.py`: unchanged, V4.12.14 authoritative-L1 guard.
- `research_unified_exit.py`: unchanged, V4.12.10 bounded stale bridge.

## Regression

- Research: **409 passed**
- Base/Adaptive: **133 passed**
- Shared strategy: **86 passed**
- **628 passed / 0 failed**
- Focused V4.12.17 tests: **12 passed**
- root launcher `bash -n`: PASS
- archived launcher `bash -n`: PASS
- `RESEARCH_PREFLIGHT_ONLY=1`: PASS

## Runtime gate

Do not promote to BaseStrategy until live logs prove:

- no parking-cap liveness veto;
- acquisition continues with >6 parked books when total exposure permits;
- hard rescue is triggered inside the actual `-8 .. -12 bps` price window;
- no liveness Taker below -12 bps;
- touch-defined Maker exits materially reduce exit distance/churn;
- a fresh exploration slot survives completion pressure;
- score-qualified books continue accumulating observations beyond three;
- contract rejection and ONE_AWAY behavior do not regress;
- marked inventory economics improve.

Latency is intentionally not claimed fixed by this release.
