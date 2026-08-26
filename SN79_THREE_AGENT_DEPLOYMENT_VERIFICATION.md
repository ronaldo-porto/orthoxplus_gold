# SN79 Three-Agent Deployment Verification

## Verdict

**Research V4.12.15: STATIC PASS / runtime inventory-liveness verification pending.** BaseStrategy and AdaptiveAgent are unchanged.

## Exact V4.12.15 fix

V4.12.14 contract/L1 correctness is retained. V4.12.15 addresses the tick-600 failure where six stale positions saturated the six-book cap and a score-state zero-loss veto kept TWO_AWAY/uncovered inventory in `EMERGENCY` rather than using bounded early rescue.

The new Research-only authority:

- protects QUALIFIED and ONE_AWAY books from the new loss subsidy;
- uses touch-defined aggressive Maker rescue after 3 failed exits / 8 ticks;
- permits TWO_AWAY/uncovered bounded Taker rescue after 8 failed exits / 16 ticks only when adverse evidence exists and Taker EV beats WaitEV;
- applies soft/hard floors of `-8 / -12 bps` and cannot widen the absolute floor;
- parks a position when the active rescue window is missed instead of letting it consume an active acquisition slot;
- keeps parked positions inside total book/exposure risk limits and refreshes them on a bounded cadence.

## Capacity contract

- Active open books: 6
- Parked books: 6
- Total open books: 12
- Total absolute BASE: 3.0

Parked inventory releases active acquisition capacity but never disappears from total risk accounting.

## Regression

- Research: 391 passed
- Base/Adaptive: 126 passed
- Shared strategy: 93 passed
- **610 passed / 0 failed**
- V4.12.15 focused tests: 13 passed
- Root preflight: PASS
- Base SHA-256 unchanged: `13a56d355558eec24df86dc34ea888524eeced8a575b19fcb3b27bffc55a3bf1`
- Adaptive SHA-256 unchanged: `3e75e6abce4d6a678f4976f10e4b30b5fa8be35f57a8743226a795f841c53448`
- V4.12.14 contract guard SHA-256 unchanged: `e3e61783d2a17f370dad62255bc468e23d7ae7ce42f20b9a7cd1846145cdf1d0`

## Runtime gate

Run Research for 45–60 real minutes / roughly 600–900 ticks. Do not promote until logs prove that parked stale inventory no longer collapses acquisition, bounded rescue respects the logged floor, actionable realization tail improves, and ONE_AWAY/normal Taker economics do not regress.
