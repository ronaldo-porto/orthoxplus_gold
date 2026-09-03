# Strategy1-Direct V4.16.2 A1.5.1

A1.5.1 is a narrow runtime-correction release built from the first A1.5 Agent 68 validation log.

## Why A1.5.1 exists

A1.5 proved the FastPath latency architecture: p50 fell from about 149 ms to about 28 ms and p95 from about 229 ms to about 30 ms. However, trading productivity collapsed to about 0.0023 RT/s, qualification did not progress, and one visible Maker->Maker round trip was gross-positive but net-negative because two large Maker fees were not priced correctly.

The log also exposed a definite score-breadth bug: with 44 qualified books and an 80-book target, Direct FastPath reported score_deficit=0 because it used `research_kappa_completion_target=3` (observations per book) instead of `research_score_target_books=80`.

## A1.5.1 changes

### 1. Correct score deficit

FastPath now uses:

- `research_score_target_books`, falling back to
- `research_total_score_full_breadth_books`, then 80.

The per-book observation requirement (`research_kappa_completion_target=3`) is no longer used as a breadth target.

### 2. True Maker lifecycle fee economics

A1.5's compressed post-lifecycle Maker fee term is removed.

A1.5.1 prices directly in bps:

`Maker entry fee + role-weighted expected exit fee + residual learned net downside + holding risk`

The Maker entry and expected Maker-exit fees are signed. Taker exit fees remain conservatively non-negative in this branch; UID239 rebate behavior remains observation-only.

Because learned downside is based on net realized bps and therefore already contains fees, A1.5.1 subtracts only the expected explicit fees on the same Taker-exit path from learned Taker shortfall before adding the remaining residual downside. Maker-exit fees stay on the complementary Maker-exit path. This prevents double-counting without erasing adverse Taker drift.

### 3. Soften migrated A1.5/A1.4 quality authority

Legacy quality history is retained but treated as a weak prior under the new fee/execution regime:

- initial authority weight: 0.20
- book-specific full authority after 8 fresh A1.5.1 Maker lifecycles
- global full authority after 64 fresh A1.5.1 Maker lifecycles

Migration baselines are persisted so a miner restart does not suddenly restore legacy history to full authority.

### 4. Keep successful A1.5 FastPath behavior

Unchanged:

- cheap 128-book pass -> bounded top-K deep evaluation
- candidate count bounds
- selected-only expensive profiles/economics
- 100 ms pre-submit freshness budget
- 6 bps Maker inside-touch cap
- 75 ms Maker GTT cap
- dust liveness exemption
- Maker minimum EV = 0.030
- directional Taker ENTRY disabled
- PositionExitController / Taker EXIT unchanged
- Kappa target = 80 books

## Validation

- A1.5.1 Direct tests: 45/45 PASS
- Focused A1.5.1 + V4.16 preflight: 115/115 PASS
- Research regression: 487 PASS, 1 historical A1.5 version-contract test skipped
- compileall: PASS
- launcher bash syntax: PASS
- launcher preflight: PASS

## Next runtime targets

A1.5.1 must preserve A1.5 latency while restoring trading productivity:

- p95 response <= 120 ms, preferably near A1.5's ~30 ms
- RT velocity >= 0.05/s initially, target 0.07-0.10/s
- positive RT ratio > 55%, target >= 60%
- directional Taker-origin entries = 0
- qualification growth resumes from the current breadth state
- closed RT PnL positive without dependence on a few large outliers
- Direct FastPath score deficit equals `max(0, 80 - qualified_books)`

A1.5.1 is still Phase-1 Research. Do not promote to BaseStrategy until runtime validation passes.
