# SN79 St6.4 Research V4.13.2 — Maker Grace + CORE_PROBE

## Purpose

V4.13.2 is the focused correction after the V4.13.1 speed-pass / Kappa-fail run. It changes only the two confirmed blockers:

1. fresh Maker entries were being closed one tick later by event-driven `PRICE_HARD_WINDOW_RESCUE` at roughly -8 to -12 bps even when a profitable executable Maker close existed;
2. qualified Kappa-eligible books with good restored history but no fresh V4.13 execution evidence had no guaranteed path to earn the first fresh RT required for `RECYCLING` / `CORE`.

Sink demotion, parking, sizing/risk admission, contract guard, alpha, lane budgets, and the V4.12.18 hard rescue floors remain frozen.

## 1. Fresh-position Maker Grace

New policy helper: `fresh_maker_grace_v4_13_2`.

The grace applies only when all are true:
- the liveness helper authorized `PRICE_HARD_WINDOW_RESCUE`;
- inventory age <= 3 ticks (configurable downward, capped at 3);
- failed exits == 0;
- selected Maker round-trip net > 0 bps;
- the Maker touch is executable inside the existing Maker floor;
- no stop-loss;
- no MAX_LONG / MAX_SHORT hard inventory band;
- no legacy `RISK` Taker authority;
- state is not `EXIT_ONLY` / `EMERGENCY`.

Result: keep/try Maker for the short grace instead of converting the fresh position into an immediate bounded-loss Taker close.

The grace does **not** change:
- the -8 bps soft Taker rescue floor;
- the absolute -12 bps hard floor;
- parking when loss is beyond the hard floor;
- later age/failed-exit rescue stages;
- explicit hard-risk / stop / emergency authority.

Launcher defaults:

```text
research_fresh_maker_grace_enabled=1
research_fresh_maker_grace_ticks=3
```

## 2. CORE_PROBE

Exactly one qualified Kappa-eligible fresh-`UNKNOWN` book may receive a protected completion slot when:
- it is flat and not dust/hard-risk;
- normal entry admission is feasible;
- economics are allowed;
- Maker EV is known and > 0;
- fresh RT count == 0;
- execution tier is still `UNKNOWN`, not `INEFFICIENT`;
- historical realized PnL is non-negative when PnL history is available;
- historical raw Kappa is non-negative when Kappa history is available.

Candidate ranking favors:
1. higher historical raw Kappa;
2. higher recent realized PnL;
3. higher positive Maker EV;
4. higher productivity score.

The selected probe is forced into `KAPPA_COMPLETION` and owns the first special completion slot before the existing V4.13.1 recycling bridge. Under tight but nonzero total-position headroom, the probe is preserved as the one bootstrap escape slot.

When normal admission allows the entry, CORE_PROBE entry size is clamped to exactly the exchange minimum order size. It does not widen inventory or min-order safety.

Lifecycle:
- first clean positive fresh RT -> normal V4.13.1 `RECYCLING` eligibility;
- three sufficiently good fresh RTs -> existing `CORE` rules;
- first fresh negative RT -> density privilege is removed/demoted;
- poor quote/fill efficiency -> existing `INEFFICIENT` sink demotion remains authoritative.

Launcher default:

```text
research_core_probe_enabled=1
```

## Preserved V4.13.1 behavior

Unchanged:
- early Book92-like `INEFFICIENT` demotion;
- fresh placements/RT accounting;
- one-slot recycling bridge after the first clean fresh positive RT;
- lane budgets: COVERAGE 3 / COMPLETION 5 / REALIZATION 3 / overflow 1;
- candidate_count 10, active-open cap 6, total-open cap 12, total abs base cap 3.0;
- persistent Maker and 3-tick replace hysteresis;
- 2-tick post-only safety;
- V4.12.18 parking and -8/-12 rescue contracts;
- V4.12.14 contract guard;
- existing alpha/signal engine and Taker economics.

## Explicitly deferred

Not changed in V4.13.2:
- COMPLETION candidates rejected by hard `NEGATIVE_EV`;
- ranking p95 latency (~103 ms in the V4.13.1 run);
- new indicators / alpha;
- BaseStrategy / AdaptiveAgent;
- concurrency 6 -> 8.

## Verification

- Python compile: PASS
- V4.13.2 focused + V4.13/V4.12.18 regression: **35 passed / 0 failed**
- All Research tests: **444 passed / 0 failed**
- `run_strategy1_research_test_multi.sh` shell syntax: PASS
- full multi-launcher preflight-only contract: PASS
- repository-wide collection was not used as an acceptance gate because unrelated GenTRX/validator tests require optional packages not installed in the audit container (`transformers`, `pyarrow`, `bittensor`, `loky`).

## Immediate Testnet validation

Run 250–400 ticks first.

Acceptance gates:

```text
Maker share > 85%
one-tick negative Taker rescues ~= 0
positive RT ratio > 60%
CORE or RECYCLING > 0
Kappa eligible > 24 or clearly progressing
placements/fill < 15
contract rejects = 0
```

If these pass, continue the same V4.13.2 build to the 600–900 tick Testnet sprint run.
