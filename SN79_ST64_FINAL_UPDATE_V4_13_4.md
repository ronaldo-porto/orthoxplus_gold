# SN79 St6.4 FINAL — V4.13.4 Authoritative Productivity Lane Fix

## Verdict carried from V4.13.3 long validation

V4.13.3 ran 752 ticks (~65.2 real minutes). Kappa quality improved, but the production flywheel failed:

- positive RT ratio: 4/6 = 66.7%
- realized PnL: ~+0.349 QUOTE
- placements/fill: ~11
- contract rejects: 0
- CORE: 0 -> 2
- RECYCLING: 0 -> 0
- Kappa eligible: 23 -> 21
- RT velocity decayed to ~0.008/s
- no fills after tick 475

Book122 provided direct runtime proof of the blocker. It completed two profitable all-Maker RTs, promoted to CORE at tick476, retained positive trading EV, then produced no more fills. Execution repeatedly logged `scheduler_lane=COVERAGE` + `LANE_NOT_GRANTED`.

## Root cause

Candidate screening already classified CORE / RECYCLING / CORE_PROBE / density_due / normal completion books into `KAPPA_COMPLETION` and granted them completion capacity.

Execution later rebuilt completion identity from a narrower predicate. Since qualified CORE/RECYCLING books are already Kappa-eligible, the legacy completion predicate returns false. V4.13.3 preserved this identity only for CORE_PROBE, leaving the same defect for the rest of the productivity lane.

The result was:

```text
SCREEN: CORE -> KAPPA_COMPLETION grant
EXECUTION: already eligible -> legacy completion false -> COVERAGE
GRANT CHECK: no COVERAGE grant -> LANE_NOT_GRANTED
```

This violated the existing design contract that candidate screening is the single authoritative lane allocator.

## V4.13.4 change

V4.13.4 changes only lane-authority propagation.

### 1. New pure helper

`research_execution_lanes.authoritative_execution_lane()`:

- non-flat inventory always resolves to `REALIZATION`;
- for a flat book, a current `KAPPA_COMPLETION` grant is authoritative;
- otherwise a current `COVERAGE` grant is authoritative;
- legacy predicate-derived lane is used only when no current grant exists.

### 2. Strategy execution follows allocation

`Strategy1_Research._place_skewed_quotes()` now resolves the flat book's lane from `_research_last_lanes.by_lane` before executing.

This preserves lane identity for:

- CORE
- RECYCLING
- CORE_PROBE
- density_due qualified books
- normal ONE_AWAY/TWO_AWAY completion
- COVERAGE

Non-flat inventory still overrides all stale entry grants with `REALIZATION`.

### 3. Completion behavior follows the authoritative lane

`completion_candidate` is now derived from the final authoritative lane (`lane == KAPPA_COMPLETION`). Therefore completion attempt/success accounting, relaxed fill policy, TTL handling, and completion telemetry all stay consistent with the scheduler allocation.

## Frozen behavior

V4.13.4 does **not** change:

- Maker Grace
- alpha/signal engine
- Score-EV hard gates
- Taker economics or thresholds
- inventory rescue floors
- parked inventory policy
- sizing
- candidate concurrency
- persistent Maker / hysteresis
- latency/ranking logic
- Kappa productivity thresholds

Book74-like CORE books with negative Score-EV remain blocked. Book122-like CORE books with positive Score-EV can now actually consume their granted completion lane and recycle.

## Verification

Regression coverage explicitly checks:

- exact Book122 CORE completion-grant preservation;
- RECYCLING completion identity;
- density_due completion identity;
- CORE_PROBE completion identity;
- normal one-away completion identity;
- COVERAGE identity;
- non-flat REALIZATION priority;
- fallback behavior only when no current grant exists.

Acceptance commands:

```bash
PYTHONPATH=agents/strategy pytest -q tests/test_research*.py
bash -n run_strategy1_research_test_multi.sh
RESEARCH_PREFLIGHT_ONLY=1 ./run_strategy1_research_test_multi.sh
```

## Next Testnet gate

Run V4.13.4 for 250–400 ticks first.

Required runtime proof:

```text
productive CORE LANE_NOT_GRANTED ~= 0
CORE actual re-quote/fill > 0
repeated CORE RT or RECYCLING activity > 0
positive RT ratio > 60%
placements/fill < 15
contract rejects = 0
RT velocity materially recovers from ~0.008/s
```

If these pass, continue the same build toward 600–900 ticks. Do not touch parking or latency until the productivity-lane fix is proven in runtime.
