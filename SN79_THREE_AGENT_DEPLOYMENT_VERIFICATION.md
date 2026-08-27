# SN79 Three-Agent Deployment Verification

## Verdict

**Research V4.12.18: STATIC PASS / runtime inventory-state verification pending.** BaseStrategy and AdaptiveAgent are unchanged.

## Research V4.12.18

V4.12.18 fixes the V4.12.17 deadlock where ONE_AWAY/QUALIFIED loss protection also prevented capacity release. The release separates bounded-loss authority from parking authority while preserving the Kappa Flywheel and all validated execution-safety components.

Key release contracts:

- ONE_AWAY/QUALIFIED may `PARK_PROTECTED` and release active capacity without receiving the TWO_AWAY/UNCOVERED bounded-loss subsidy.
- `KAPPA_BLOCKED_LOSS` cannot veto parking.
- Newly parked books cancel known resting orders; protected parked refreshes may submit only when current legal post-only touch remains at/above the stored protected floor.
- Unsafe refreshes emit `PROTECTED_REFRESH_BLOCKED` and hold instead of silently realizing a large Maker loss.
- Default protected park arming is 4 failed exits or 8 ticks; parked refresh defaults to 25 ticks.
- Active-open capacity excludes parked books while total-open and aggregate BASE risk limits remain authoritative.
- Restored Kappa-eligible books remain Flywheel candidates even if the new rolling PnL ledger is incomplete. Incomplete history receives PARTIAL/UNKNOWN confidence rather than fabricated PnL.
- One fresh exploration slot remains fail-open whenever hard total headroom exists.
- V4.12.14 contract guard, V4.12.10 unified exit, and 250 ms velocity-STALE ONE_AWAY behavior are unchanged.

## Frozen components

- `BaseStrategy.py`: byte-for-byte unchanged from V4.12.17.
- `AdaptiveAgent.py`: byte-for-byte unchanged from V4.12.17.
- `research_contract_guard.py`: unchanged, V4.12.14 authoritative-L1 guard.
- `research_unified_exit.py`: unchanged, V4.12.10 bounded stale bridge.
- `research_quote_hysteresis.py`: unchanged, including 250 ms velocity-STALE ONE_AWAY TTL.

## Regression

- Research: **420 passed**
- Base/Adaptive: **133 passed**
- Shared strategy: **90 passed**
- **643 passed / 0 failed**
- Focused V4.12.18 tests: **11 passed**
- root launcher `bash -n`: PASS
- archived launcher `bash -n`: PASS
- `RESEARCH_PREFLIGHT_ONLY=1`: PASS

## Runtime gate

Do not promote to BaseStrategy until live logs prove:

- stale protected ONE_AWAY/QUALIFIED inventory parks and frees active capacity;
- protected parking does not grant bounded-loss Taker authority;
- no parked Maker fill occurs below its stored `protected_floor_bps`;
- unsafe protected refreshes block, then safely unpark/requote only when executable economics recover;
- acquisition does not remain zero for >20 ticks while total hard exposure still permits another position;
- migrated Kappa-eligible books populate Flywheel candidates/core despite PARTIAL/UNKNOWN PnL confidence;
- qualified books continue accumulating observations beyond three;
- contract rejection remains <0.5%;
- liveness Taker never breaches -12 bps;
- velocity-STALE ONE_AWAY TTL remains 250 ms.

Latency is intentionally not claimed fixed by this release.
