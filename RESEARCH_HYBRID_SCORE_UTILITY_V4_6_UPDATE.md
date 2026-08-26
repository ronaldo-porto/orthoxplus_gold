# SN79 Research Hybrid Score Utility V4.6

## Purpose

V4.6 is a focused live-log correctness patch on top of V4.5. It does not change BaseStrategy or AdaptiveAgent.

## Fix 1 — discrete minimum-order admission

The first V4.5 Testnet sample showed 416 entry-size evaluations with 0 admissions. Most high-EV candidates were `NEAR_SAFE` around 0.20–0.225 BASE against a 0.25 minimum, but were rejected because expected exit capacity (often 0.243–0.248) was required to be >= 0.25 exactly.

V4.6 keeps `UNSAFE` rejection and all EV/inventory/headroom gates, but treats expected exit capacity as a forecast rather than an exchange hard limit. For a NEAR_SAFE candidate it now requires:

`expected_exit_capacity >= min_order * (1 - near_safe_tolerance)`

The actual 0.25-minimum inventory risk is still evaluated separately before promotion. This permits low-risk, positive-EV discrete maker entries while still rejecting materially unsafe clips.

## Fix 2 — restart-safe RoundTripVelocity

V4.5 restored lifetime/session round-trip count but divided it by elapsed time since the new process started. A restored count of 65 therefore appeared as 65, 32.5, 21.7, ... despite no new round trips.

V4.6 uses `VelocityState.completed_round_trips`, which starts at zero for the current runtime and increments only when this process observes a new FLAT/CROSS round trip. Hybrid summary and ScoreRegime now consume this current-run velocity rather than restored lifetime closes.

## Expected Testnet behavior

- Positive-EV `NEAR_SAFE` books with low full-minimum inventory risk should emit 0.25 maker orders.
- Truly `UNSAFE` books remain blocked.
- Inventory should begin to form, enabling REALIZATION / SN79 action-utility / taker policy to actually run.
- `round_trip_velocity` should start at 0 after a reload and rise only when new round trips complete.

## Promotion

Research only. Do not promote to BaseStrategy until live Testnet evidence confirms increased maker entry throughput, actual realization/taker activity, faster Kappa qualification, and controlled downside.

## Local verification

- Research test suite: **287 passed**
- `py_compile`: **PASS** for `Strategy1_Research.py`, `research_entry_size.py`, and `research_velocity.py`
- Research launcher shell syntax (`bash -n`): **PASS**
- Replayed live-log admission examples:
  - `safe=0.22212584`, `exit_capacity=0.24814015`, `min=0.25` -> `NEAR_SAFE`, **ALLOW 0.25**
  - `safe=0.22588612`, `exit_capacity=0.24846351`, `min=0.25` -> `NEAR_SAFE`, **ALLOW 0.25**
  - `safe=0.16881674`, `exit_capacity=0.24346926`, `min=0.25` -> `UNSAFE`, **REJECT**
