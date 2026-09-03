# Strategy1-Direct V4.16.2 A1.4

## Scope

A1.4 is a narrow Agent-68 runtime-history correction on top of A1.3.
UID239 remains observation-only; no reservoir, 0.25-harvest, maturation, signed-fee rebate, or score-freeze hypothesis is implemented here.

## Why A1.4

A1.3 fixed Maker execution quality:

- Maker-origin completed RTs: 13
- positive / negative: 9 / 4
- Maker-origin positive ratio: ~69.2%
- Maker-origin realized PnL: about +0.566

However A1.3 became too inactive because it treated a high Taker-exit rate as lifecycle badness even when Maker->Taker realized outcomes were profitable. It also produced three Taker-origin RTs, all losing.

A1.4 therefore changes only two authorities.

## 1. Learned Maker realization shortfall

The inherited fixed future-Taker model effectively prices:

`P(Taker exit) * (crossing + Taker fee + slippage)`

A1.4 replaces that Direct entry cost with:

`P(Taker exit) * E[max(0, -gross realization bps) | Taker exit]`

`+ P(Taker exit) * positive Taker fee`

`+ holding risk`

Profitable Taker exits contribute zero negative shortfall. A high Taker-exit rate is therefore not intrinsically bad.

The estimator is hierarchical:

- weak cold-start Taker-rate prior: 0.55
- weak conditional-shortfall prior: 3 bps
- current-run global evidence
- book-specific evidence

A1.3 session state is migrated automatically. Older state without a shortfall field initializes from only the adverse part of the stored Taker gross-drift EWMA.

## 2. Maker quality no longer penalizes Taker frequency

A1.4 removes Taker-exit frequency from the bounded productivity badness score.

The remaining Direct Maker-quality adjustment uses:

- overall completed Maker-lifecycle adverse drift, regardless of exit role;
- rolling realized loss rate;
- rolling realized mean PnL.

There is still no blacklist, cooldown, toxic lane, or separate maintenance authority.

## 3. Taker-entry calibration tightened

A1.3 used a full directional score as an 8-bps expected move and admitted any positive TakerEV.

A1.4 uses:

- full-scale directional move: **4 bps**
- minimum Taker economic EV: **0.20**
- minimum net directional edge after crossing/fee/slippage/markout buffer: **2.0 bps**

Kappa/coverage cannot subsidize Taker entry.

Counterfactual replay of the three actual A1.3 Taker entries gives:

- tick 19 / book 89: TAKER -> **SKIP**
- tick 23 / book 89: TAKER -> **MAKER**
- tick 970 / book 7: TAKER -> **SKIP**

No PositionExitController Taker-exit logic was changed.

## Preserved A1.3 behavior

- Maker minimum EV: 0.030
- Direct Maker maximum inside-touch improvement: 6 bps
- Direct Maker maximum TTL: 75 ms
- dust capacity/liveness exemption: unchanged
- aggregate BASE risk: unchanged
- Kappa ranking / score target: unchanged
- PositionExitController: unchanged
- V4.16.2 Strategy1_Research baseline: unchanged
- BaseStrategy / AdaptiveAgent: unchanged

## UID239 status

Observation only. A1.4 does **not** implement:

- large Taker inventory seeding;
- ~50 BASE acquisition clips;
- ~300-second maturation;
- 0.25 micro-harvest;
- signed negative Taker fee/rebate economics;
- score-freeze / refresh behavior.

## Validation

- Direct A1.4 tests: 32/32 PASS
- Focused A1.4 + V4.16 preflight: 102/102 PASS
- Research regression surface: 474/474 PASS
- compileall: PASS
- launcher bash syntax/preflight: PASS

## Runtime validation targets

A1.4 should preserve A1.3 Maker quality while restoring activity:

- Maker-origin positive RT ratio: >=60%, preferably near A1.3's ~69%
- overall positive RT ratio: >55%, preferably >60%
- closed RT PnL: clearly positive
- Taker-origin entries: near zero unless exceptionally strong; Taker-origin PnL >=0
- RT velocity: >0.015/sec as an initial recovery target
- qualification velocity: materially faster than A1.3's +5 qualified books / 1,945 ticks
- dust deadlock: zero
- Maker inside-touch improvement: <=6 bps

Latency remains an engineering target, not a universal LifecycleEV penalty.
