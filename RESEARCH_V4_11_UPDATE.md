# SN79 Research Test Agent V4.11

V4.11 is the aggressive-performance Research successor to V4.10. BaseStrategy and AdaptiveAgent are intentionally unchanged.

## Implemented

- **Hard candidate cap = 12.** Lane reserves can no longer expand deep prediction back to 20 books.
- **Sticky 8–12 book cohort.** Default cohort size is 10 with one exploration slot. In-progress books stay selected until qualification/unsafe rejection instead of rotating across the whole 128-book universe.
- **Finish-before-rotate priority.** 2 completed observations (one-away) > expiring qualified > 1 completed observation > new coverage.
- **Expiry pressure before deep prediction.** Qualified books approaching rolling-window expiry enter the completion lane during the cheap screen.
- **OBS-qualified vs SCORE-qualified.** Three observations are not treated as sufficient by themselves; recent PnL and available raw-Kappa quality must also pass the configured floor.
- **Full lifecycle entry cost.** Maker entry ranking now includes expected realization fee, taker-spread crossing probability, slippage and holding-risk cost using live per-book fees.
- **Positive-EV minimum-order override.** A strongly positive lifecycle candidate can promote exactly one exchange-minimum clip even when multiplicative sizing falls below the old 0.20 near-safe boundary, subject to inventory/exit/headroom gates.
- **Aggressive QUIET execution.** Safe cohort quotes tighten by default and non-adverse QUIET TTL stretches toward 1000 ms, bounded by 1500 ms. Toxic/adverse shortening remains authoritative.
- **Higher Research throughput.** `max_mm_books_per_tick` 4 -> 6 and Kappa completion success cap 2 -> 3.
- **New telemetry.** `[S1R_COHORT]` and `[S1R_SCORE_PROGRESS]` expose concentration and qualification progress.

## Preserved V4.10 safety

- `authority=NONE` cannot create a Taker market exit.
- Score-loss subsidy remains disabled.
- Normal risk-direct Taker remains disabled.
- Live maker/taker fees remain authoritative.
- Effective visible/resting/current-response exposure guard remains active.
- BaseStrategy and AdaptiveAgent are not modified.

## Runtime defaults

- candidate cap: **12**
- sticky cohort: **10**
- exploration slots: **1**
- lanes: **3 coverage / 5 completion / 3 realization / 1 overflow**
- max MM books/tick: **6**
- completion attempts/successes: **6 / 3**
- QUIET TTL target/max: **1000 / 1500 ms**
- quote tighten multiplier: **0.85**
- positive-EV min-order safe fraction: **0.35**
- positive-EV exit-capacity fraction: **0.45**
- lifecycle taker-exit probability: **0.30**

## Verification

V4.11-focused + V4.10 safety tests: **41 passed**.

Research suite: **317 passed, 5 failed**. The same 5 failures are already present in the V4.10 baseline, so V4.11 adds **0 new research-suite failures**.

## Still Research-only / not yet promoted

V4.11 still needs testnet evidence for fill-rate, round-trip velocity, realized PnL, SCORE-qualified book count and Kappa3 median before any BaseStrategy promotion.

Persistent cross-request validator-pending exposure tracking remains a later P1 item; V4.11 preserves the existing visible/resting/current-response exposure guard.

## V4.11.1 State / Authority Fix

The rolling Kappa timestamp state is now the single authority across scheduler, completion lanes, realization and telemetry. Rolling timestamp evidence is persisted/restored across miner reloads; lifetime observation counters are diagnostic only. See `RESEARCH_V4_11_1_STATE_AUTHORITY_FIX.md`.

## V4.11.2 Aggressive Positive-EV Completion

V4.11.2 adds a hard-bounded aggressive positive-EV Taker authority plus a strict ONE_AWAY exact-minimum completion path. Negative Taker subsidy remains disabled. See `RESEARCH_V4_11_2_UPDATE.md`.
