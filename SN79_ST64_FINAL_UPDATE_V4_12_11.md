# SN79 St6.4 Research V4.12.11 — ONE_AWAY Completion Rescue

## Why this release exists

UID68 V4.12.10 runtime verification showed the stale-Taker bridge worked, but
ONE_AWAY completion was still structurally blocked:

- 702 genuine ONE_AWAY KAPPA_COMPLETION decisions
- 0 successful ONE_AWAY quotes
- 447 TTL_STALE skips
- 213 ZERO_ORDER_SIZE skips
- ONE_AWAY backlog grew 4 -> 19 while score-qualified books stayed flat/down

This release changes only the ONE_AWAY third-observation execution path.

## Changes

### 1. Exact-minimum ONE_AWAY admission

The old ONE_AWAY soft gates required safe-size >=50% and modeled exit capacity
>=90% of the 0.25 venue minimum. V4.12.10 log replay showed those soft estimates
were commonly ~15–25% and ~20–30% even when lifecycle EV remained positive.

V4.12.11 therefore uses hard-clamped soft floors:

- safe-size >= 15% of the venue minimum
- modeled exit capacity >= 20% of the venue minimum

The following hard gates remain mandatory:

- ONE_AWAY only (one observation remaining)
- lifecycle/trading EV > 0
- full-minimum inventory risk <= configured hard risk ceiling
- volume headroom passes
- full 0.25 inventory room exists

The helper clamps the 15%/20% floors, so config cannot loosen them further.

### 2. Velocity-stale ONE_AWAY Maker TTL rescue

If adaptive TTL returns STALE only because of microprice velocity, a flat,
positive-EV, non-toxic/non-stressed ONE_AWAY completion candidate receives the
existing minimum Maker TTL (250 ms) instead of being dropped.

This does not cross the spread and remains inside the existing post-only Maker
quote context. It never overrides TOXIC/STRESSED, non-positive EV, TWO_AWAY,
uncovered work, or any non-STALE rejection reason.

Telemetry event: `ONE_AWAY_TTL_RESCUE`.

## Frozen from V4.12.10

- bounded stale Taker bridge and -12 bps hard RT ceiling
- candidate_count=10
- max_open_books=6
- score target=88
- breadth/deadline scheduler
- stale Maker rescue
- BaseStrategy and AdaptiveAgent
- alpha/regime/HJB logic

## Static verification

- Research: 359 passed / 0 failed
- Base + Adaptive: 126 passed / 0 failed
- Shared strategy helpers: 93 passed / 0 failed
- Total: 578 passed / 0 failed
- Python compile: PASS
- root launcher bash syntax: PASS
- versioned launcher bash syntax: PASS
- full launcher preflight: PASS

Environment-dependent validator tests are not claimed in this sandbox because
optional runtime packages such as bittensor are not installed.

## Runtime verification window

First go/no-go: 60–90 real minutes, or once ~150–300 ONE_AWAY completion
decisions are observed. Extend to 2–3 hours only if the result is borderline.

Primary PASS evidence:

1. ONE_AWAY KAPPA_COMPLETION produces non-zero QUOTE outcomes.
2. `ONE_AWAY_TTL_RESCUE` appears and leads to actual short-TTL Maker submits.
3. ONE_AWAY ZERO_ORDER_SIZE rate drops sharply from the V4.12.10 30.3% level.
4. ONE_AWAY TTL_STALE no longer dominates valid positive-EV completion work.
5. ONE_AWAY backlog turns over instead of growing.
6. score-qualified/eligible breadth is non-decreasing and begins to rise.
7. RT conversion remains >= 0.45 and realized PnL remains positive.
8. V4.12.10 Taker bridge behavior remains bounded.
