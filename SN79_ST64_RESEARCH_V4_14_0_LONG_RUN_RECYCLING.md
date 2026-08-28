# SN79 St6.4 Research V4.14.0 — Long-Run Recycling

Policy: `long_run_recycling_v4_14_0`

## Why this release exists

The 13,516-tick / ~19.4-hour Research run showed strong local realized quality but catastrophic long-run capital trapping:

- 76 RT, 71 positive / 5 negative locally.
- Kappa eligible peaked near 20 and ended at 3.
- CORE 6 -> 2, RECYCLING stayed 0.
- Parked inventory reached 10 books.
- `TOTAL_HEADROOM` suppressed acquisition for ~76.9% of ticks.
- Several small early losses (-10 to -20 bps) became multi-hour -1000 to -5000 bps parked positions.

V4.14.0 changes the objective from "avoid negative RTs" to "recycle bounded downside before it becomes tail inventory".

## Minimal runtime changes

### 1. Bounded-loss escape corridor

New helper/version:

- `BOUNDED_LOSS_ESCAPE_VERSION = "bounded_loss_escape_v4_14_0"`

A non-flat position is forced to Taker protection when all are true:

- inventory age >= 2 ticks,
- current round-trip Taker net is between -8 bps and -25 bps,
- current Taker net has deteriorated by at least 2 bps from the best Taker net seen in the lifecycle.

This authority runs after Positive-Maker evaluation but before protected/liveness parking, so it can override Kappa/Positive-Maker parking while the loss is still bounded.

It uses the already-existing per-book peak Taker value; no new runtime map or state machine is added.

Representative long-run regressions:

- Book24: -11.43 -> -16.28 bps at age 2 now exits instead of later reaching ~-2708 bps.
- Book25: -10.49 -> -14.82 bps at age 2 now exits instead of later reaching ~-3889 bps.

### 2. Parked-cap coverage gate

When parked inventory reaches the configured parked threshold:

- new `COVERAGE` acquisition is disabled,
- existing inventory realization remains enabled,
- KAPPA_COMPLETION / CORE / recycling work remains enabled.

Telemetry pressure reason: `PARKED_RECYCLE`.

This is deliberately simpler than another inventory scheduler.

### 3. Long-run capacity profile

Research launcher defaults:

- `research_max_active_open_books=6`
- `research_max_total_open_books=8`
- `research_max_parked_open_books=4`
- `research_max_total_abs_base=2.0`
- `research_positive_maker_veto_max_failed_exits=4`

Bounded-loss parameters:

- `research_bounded_loss_escape_enabled=1`
- `research_bounded_loss_escape_min_age_ticks=2`
- `research_bounded_loss_escape_floor_bps=-25.0`
- `research_bounded_loss_escape_drawdown_bps=2.0`

## Frozen unchanged

- V4.13.9 sticky contract guard.
- V4.13.8 profitable Maker persistence/HOLD.
- V4.13.7 qualified/Core exact-min and stale-TTL rescue.
- V4.13.6 density/deep-EV scheduler.
- V4.13.5 Positive-Maker logic itself.
- V4.13.4 authoritative execution lanes.
- Score-EV NEGATIVE_EV gate.
- Alpha/signals, fees, normal sizing, candidate deep analysis, latency/ranking implementation.

## Verification

- New V4.14.0 long-run regression tests: 5 passed.
- Active Research test surface: 489 passed.
- Python compile: PASS.
- Bash syntax: PASS.
- Research launcher preflight: PASS.

## Runtime acceptance

Do not judge only from the first 150 ticks. The important proof is sustained recycling.

Watch:

- `BOUNDED_LOSS_ESCAPE > 0` when adverse positions enter the corridor.
- parked books normally <= 4 and fall after escape events.
- `TOTAL_HEADROOM` / `PARKED_RECYCLE` pressure stays temporary, not dominant.
- Kappa eligible does not repeat the 20 -> 3 long-run collapse.
- CORE remains stable/rising and RECYCLING becomes non-zero.
- RT velocity remains sustainable after the early run.
- controlled negative RTs are acceptable; multi-hour tail inventory is not.

This package is a **Research-only promotion candidate**. BaseStrategy and AdaptiveAgent are not promoted to V4.14.0 yet.
