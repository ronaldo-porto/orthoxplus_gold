# Strategy1-Direct V4.16.2 A1.7.0 — Persistent Maker Execution

## Purpose

A1.7.0 is an execution-only follow-up to the long-run A1.6.3 validation.  It does **not** add learned trade authority, another lifecycle model, or a new score gate.  A1.6.3 observable entry economics, exposure synchronization, risk exits, sizing, and FastPath remain frozen.

The only experiment is whether a valid Maker quote can remain live longer and preserve queue/fill opportunity instead of being destroyed by a fixed 75 ms replacement cycle.

## Deterministic quote lifecycle

For an already-resting Direct entry quote:

- `KEEP` when the current A1.6 entry edge remains above the existing 2.5 bps floor, the quote is still post-only, remains within the current 6 bps touch-drift envelope, and the desired quote has not moved materially.
- `CANCEL_REPRICE` when the desired quote moves by at least the larger of 2 ticks or 1.5 bps.  Replacement waits for a later account snapshot so A1.6.3 one-live-batch safety remains intact.
- `CANCEL_EDGE` when the current observable Maker edge falls below the existing A1.6 floor.
- `CANCEL_INVALID` for crossing/bad-book conditions.

A live quote that temporarily misses the deep top-K may remain live if the same cheap observable edge and touch-validity checks still pass.  No historical win/loss state is consulted.

## Fixed regime TTLs

These are execution lifetimes, not trade gates:

- QUIET: 750 ms
- NORMAL/other: 500 ms
- TREND_UP / TREND_DOWN: 350 ms
- STRESSED / TOXIC: 150 ms
- hard maximum: 750 ms

Freshness budget still applies to **new exposure only**.  KEEP/CANCEL maintenance remains available on slow requests.

## Exposure ownership

A1.6.3 invariants stay authoritative:

- one unresolved exposure-changing order batch per book;
- outstanding orders reserve worst-case BASE exposure;
- risk-reducing orders are legal when over-cap;
- persistent entry quotes are canceled immediately once inventory opens;
- legitimate inventory-exit orders are not replaced by the entry quote manager;
- isolated dust compaction remains unchanged.

## Frozen trading parameters

- FastPath: 20 cheap candidates / 16 deep candidates
- acquisition size: 0.25 BASE
- total absolute exposure cap: 2.0 BASE
- total productive open-book cap: 8
- active open-book cap: 6
- quote geometry cap: 6 bps
- Taker entry: OFF
- current observable Maker entry edge: unchanged, minimum 2.5 bps
- NORMAL/DEFENSIVE/HARD_ESCAPE exit authority: unchanged
- dust/exposure code: unchanged

## No learned authority

A1.7.0 does not introduce any new EWMA, posterior, book-quality learner, future fill model, future PnL model, future exit-fee model, Taker probability model, or testnet-history-trained veto.

Observation data is used only to validate fixed execution parameters: quote keeps, reprices, cancels, fill age, placements/fill, QUIET fill efficiency, RT velocity, RT quality, and latency.

## Runtime telemetry

New records/counters include:

- `DIRECT_QUOTE_LIFECYCLE`: KEEP/CANCEL and reason
- `DIRECT_SLOW_REQUEST`: emitted only when response wall time exceeds 100 ms
- cumulative quote-manager counters in MM stats: keeps, cancels, reprices, new batches, unselected keeps

## Acceptance gate

First run: 500–1,000 ticks.

Then, if stable: 2,000–4,000 ticks.

Primary targets:

- no exposure/liveness regression;
- RT velocity >= 0.10/s, strong >= 0.15/s;
- positive RT >= 55%, target >= 60%;
- Maker-ending positive >= 75%;
- QUIET entry-decisions/Maker-fill < 8, strong < 6 (A1.6.3 was ~12.9);
- Taker-risk-ending RT share < 25%;
- p95 response < 120 ms;
- qualified books >= 80 and stable.

## Explicitly deferred

A1.7.0 does not include larger sizing, target-inventory scaling, portfolio micro-harvest, Taker acquisition, new learned models, or a new lifecycle EV layer.  Those remain later milestones only after persistent Maker execution proves safe.
