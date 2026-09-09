# SN79 Research V4.12.16 Kappa Conversion Pre-Deploy

**Status:** static candidate only; runtime promotion is not claimed  
**Date:** 2026-08-26  
**Scope:** `Strategy1_Research` only

## Decision

UID 235's useful signal is broad Maker throughput and rapid inventory realization, not a directional SELL rule. V4.12.16 therefore improves the probability that scarce fills become score-qualified Kappa3 books while retaining the V4.12.15 loss boundary.

Do not widen Taker loss floors or copy UID 235's observed side imbalance.

## V4.12.15 evidence

- Score-qualified books moved only from 4 to 6 in roughly 540 ticks.
- Five bounded rescue Takers respected the soft `-8 bps` floor.
- Eight books parked and the six-book parked pool saturated.
- More than 200 `PARK_CAP` blocks appeared after saturation.
- ONE_AWAY entry quotes averaged about `4.1 bps` from touch.
- ONE_AWAY quote TTL averaged about `589 ms`; only 4 of 55 observed Maker entry quotes filled.
- Response p95 remained about `145 ms`, above the `120 ms` target.

## Candidate policy

1. Under parked-cap saturation or total headroom of three books or fewer, suppress only new zero-observation coverage while productive ONE_AWAY/TWO_AWAY books exist.
2. Within equal completion cost/deadline, prefer books already meeting the non-negative realized-PnL score floor, then the closest negative book.
3. For positive-EV, non-toxic QUIET ONE_AWAY books, cap Maker distance at `1.5 bps` from own touch.
4. Convert a velocity-stale ONE_AWAY skip to a bounded `900 ms` post-only Maker TTL rather than the old `250 ms` minimum.
5. Make a liveness PARK decision authoritative over the legacy stale Taker bridge for that response.

Safety invariant: existing inventory, risk exits, qualified deadline refreshes, post-only behavior, active/parked/total exposure caps, and the V4.12.15 `-4/-8/-12 bps` floors remain unchanged.

## Candidate defaults

| Parameter | Value |
|---|---:|
| Kappa pressure gate | enabled |
| Reserved total slots | 3 |
| ONE_AWAY touch cap | 1.5 bps |
| ONE_AWAY stale TTL | 900 ms |
| Active / parked / total books | 6 / 6 / 12 |
| Total absolute BASE | 3.0 |
| Maker / soft-Taker / hard-Taker floor | -4 / -8 / -12 bps |

## Runtime promotion gates

Do not promote until a fresh, isolated testnet run reaches at least 3,600 ticks and satisfies:

- no `ERROR`, unauthorized Taker, or liveness Taker below `-12 bps`;
- no liveness loss subsidy for `QUALIFIED` or `ONE_AWAY`;
- ONE_AWAY Maker-entry fill conversion at least `9.5%` (baseline about `7.3%`);
- score-qualified velocity at least `4.6` new books per 1,000 ticks (25% above the short V4.12.15 baseline);
- `PARK_CAP` blocks per 100 ticks at least 50% below the comparable V4.12.15 window;
- response p95 no worse than `150 ms`, with a follow-up target below `120 ms`;
- non-negative cumulative realized PnL over the evaluation window.

Compare equal-length windows and report both raw and per-1,000-tick rates; the source V4.12.15 log was still growing during analysis.

## Required telemetry

- `SCORE_PROGRESS`: `score_qualified`, `one_away`, `two_away`, `kappa_pressure_reason`, `kappa_pressure_suppressed`.
- `SCHED`: completion attempts/successes and execution-lane use.
## Candidate SHA-256

- Strategy: `6c986621585a675694d9a2ce817a3b0e7f0470aa55417400f64b4e3b5989871b`
- Execution lanes: `fbeeb8ec660ce12a63bffadef0bdecba29d068ba2428467fa62b82a5c69663dc`
- Quote/TTL helper: `ce45ac60f595f5c1d7a258bd02c746e8cc6be1ffdcd16c23d7b615cd1a3676c5`
- Launcher: `9d5b9c359a0085a8057a48fd1cc8ab2df56a0c0d0c18b9f896db221be0e2b6ce`

- `QUOTE`/`FILL`: completion observation count, distance from touch, TTL, Maker/Taker role.
