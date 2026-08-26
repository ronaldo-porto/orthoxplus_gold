# SN79 Research V4.11.1 — Kappa State / Authority Fix

## Root cause
V4.11 had two Kappa counters with different semantics:

- `_research_realized_observations_by_book`: lifetime/session count, restored across miner reloads.
- `realized_pnl_history`: rolling validator-aligned observations used by the scheduler.

Quote/FILL/POSITION telemetry could show `kappa_obs=2` from the lifetime map while the scheduler saw `0` from the rolling 3h window. The completion lane therefore appeared to disagree with the book state.

## Fix

- Rolling timestamp evidence is now the single decision authority for:
  - Kappa book state
  - completion lane
  - scheduler buckets
  - realization Kappa pressure
  - quote/FILL/POSITION Kappa telemetry
- Lifetime/session counts remain diagnostics only.
- Every realized observation records its simulation timestamp immediately.
- Rolling timestamp evidence is persisted in the research session JSON and restored after miner reload.
- Live `realized_pnl_history` and persisted timestamps are merged with timestamp de-duplication and 3h pruning.
- Legacy session snapshots that contain counts but no timestamps do **not** fabricate recent observations. They restart rolling Kappa progress from verifiable evidence.

## Expected runtime invariant
For any book in the same tick:

`QUOTE.kappa_obs == KAPPA.obs == SCHED bucket state == completion-lane state`

A fresh second observation must immediately produce:

`obs=2 -> remaining=1 -> KAPPA_COMPLETION / ONE_AWAY`

## Scope
Research agent only. BaseStrategy and AdaptiveAgent are unchanged.
