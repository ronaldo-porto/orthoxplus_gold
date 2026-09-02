# Strategy1-Direct V4.16.2 A1.2 — Maker Lifecycle Quality

## Scope

A1.2 is a narrow Research overlay on the unchanged V4.16.2 Research baseline. It preserves A1.1's successful LifecycleEV and separate Taker-economics corrections and targets the failure observed in Agent 68 A1.1: high activity and fast Kappa qualification, but too many Maker entries ending as losing Taker exits.

No changes were made to `Strategy1.py`, `Strategy1_Research.py`, `BaseStrategy.py`, `AdaptiveAgent.py`, validator trade logic, Kappa ranking, or PositionExitController.

## Runtime basis: Agent 68 A1.1

The A1.1 validation log contained 265 ticks, 2,820 ranked candidates, 1,425 entry decisions, 275 fills, and 120 completed round trips. It reached 20 qualified books rapidly, proving that the A1 lifecycle dead-gate was fixed. Quality remained poor: 50 positive / 70 negative RT and negative realized PnL.

The dominant split was:

- Maker -> Maker: 29 RT, 20 positive / 9 negative, strongly positive aggregate PnL.
- Maker -> Taker: 74 RT, 19 positive / 55 negative, strongly negative aggregate PnL.

Therefore A1.2 does not reduce Kappa pressure or redesign exits. It tries to reject/downrank Maker inventory that has demonstrated poor realization quality.

## A1.2 changes

### 1. Maker minimum economic margin

A1.1 accepted any positive MakerEV. A1.2 requires:

`MakerEV >= 0.030`

This is a model-error margin, not a LifecycleEV hard veto. Positive Taker economics remain independent and can still win when MakerEV is below the margin.

Historical A1.1 entry-decision replay shows that a 0.030 margin would have retained about 61.6% of A1.1 Maker decisions before any learned quality adjustment, so it is intentionally far from the A1 dead-trading regime.

### 2. Learned Maker realization drift

The Direct overlay now tracks every lifecycle that is opened by a Maker fill. When that position returns to flat it records:

- entry price;
- exit price;
- signed gross realization drift in bps;
- Maker-exit vs Taker-exit style;
- per-book EWMA of all gross drift;
- per-book EWMA of Maker->Taker gross drift;
- per-book Taker-exit rate.

A negative Maker->Taker drift creates a confidence-weighted realization-drift penalty. Sparse/new books receive little or no penalty.

The drift deduction is capped at `0.030` EV.

### 3. Bounded per-book productivity adjustment

Restart-safe rolling economics already maintained by Research are reused:

- realized observation count;
- loss rate;
- realized mean PnL.

These are blended with the Direct Maker->Taker rate into one small productivity deduction. There is no blacklist, cooldown, toxic lane, or loss-streak veto.

The productivity deduction is capped at `0.020` EV.

The combined Maker quality deduction is capped at:

`0.040` EV

This protects against recreating A1's over-restrictive gate.

### 4. Rank and execution integration

The learned quality adjustment has two effects only:

1. it reduces book rank so repeatedly poor Maker books lose priority;
2. it reduces Maker lifecycle value before Maker/Taker/Skip choice.

It does **not** mark the whole candidate economically ineligible. This means an independently positive directional Taker trade remains possible.

### 5. Earlier portfolio admission

A1.1 produced 999 final contract-validation drops, mostly `EXPOSURE_HEADROOM`. A1.2 calculates available open-book/active-book/absolute-inventory slots before acquisition and caps successful new-exposure books to that capacity.

Final contract validation remains the last authority.

## Explicitly unchanged

- A1.1 removal of latency as a hard LifecycleEV penalty;
- A1.1 removal of duplicate adverse-selection penalty;
- separate directional TakerEV;
- no Kappa subsidy for negative Taker entries;
- Kappa/TotalScore ranking logic;
- candidate screen counts;
- Taker alpha scale;
- PositionExitController;
- stop/protective exit thresholds;
- score target of 80 books.

## New telemetry

`SIMPLE_CONFIG` adds:

- `direct_quality_version`
- `maker_min_ev`
- `maker_quality_max_penalty`

`DIRECT_MAKER_LIFECYCLE` records completed Maker-opened lifecycle learning.

`ENTRY_DECISION` adds:

- `maker_lifecycle_ev_adjusted`
- `maker_realization_drift_penalty`
- `maker_productivity_penalty`
- `maker_quality_penalty`
- `maker_lifecycle_samples`
- `maker_taker_exit_rate`
- `maker_gross_bps_ewma`
- `maker_taker_gross_bps_ewma`
- `rolling_samples`
- `rolling_loss_rate`
- `rolling_realized_mean`
- `maker_min_ev`
- `maker_ev_margin`

Build stats add:

- `portfolio_open_slots`
- `portfolio_headroom_stop`

## Validation completed

- A1.2 direct unit tests: 21 passed.
- A1.2 + V4.16 focused preflight: 91 passed.
- Research regression suite with strategy path on `PYTHONPATH`: 463 passed.
- `python -m compileall -q agents/strategy`: PASS.
- `bash -n run_strategy1_research_simple_multi.sh`: PASS.
- Baseline Strategy1 / V4.16.2 Research / Base / Adaptive / validator trade hashes: unchanged from A1.1.

## Next runtime gate

A1.2 should be judged on whether it preserves A1.1's qualification velocity while improving the following:

- positive RT ratio: target >50%, strong >55-60%;
- Maker->Taker completion rate: target <55%, strong <45-50%;
- realized PnL: target >= 0 and preferably clearly positive;
- qualified-book growth must remain broad;
- no return to the A1 lifecycle-eligibility collapse;
- final `EXPOSURE_HEADROOM` reject count should fall sharply;
- p95 latency should be monitored but is not an economic veto.

The Direct Maker lifecycle EWMA is intentionally session-local in A1.2; restart-safe rolling PnL still contributes immediately after a restored session. Persisting the new lifecycle-specific state should be considered only after runtime evidence shows it is useful.
