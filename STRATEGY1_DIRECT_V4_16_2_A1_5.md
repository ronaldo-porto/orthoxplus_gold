# Strategy1-Direct V4.16.2 A1.5 — FastPath + Net Downside

## Scope

A1.5 is an isolated Strategy1-Direct Research overlay built from Agent-68 A1.4 runtime evidence. UID239 remains observation-only; no reservoir, micro-harvest, maturation, or signed-rebate behavior is copied into this branch.

## Why A1.5

A1.4 restored activity and qualification velocity, but the long run exposed three blockers:

- response latency degraded to roughly p50 ~149 ms / p95 ~229 ms;
- 31 directional Taker-origin RTs produced only 2 positive outcomes;
- Maker->Taker learning used gross price shortfall, so fee-driven net losses could look harmless.

A1.4 also spent most RT capacity on already-qualified books while score deficit remained.

## A1.5 changes

### 1. Direct FastPath

The Direct candidate no longer calls the inherited heavy full-universe Research screen in its hot path.

Per request:

1. Perform a cheap 128-book priority pass using inventory, Kappa completion state, top-of-book spread, cached Direct quality, cached profile economics, and fairness age.
2. Prioritize ONE_AWAY / TWO_AWAY / unqualified books while score deficit is positive.
3. Throttle ordinary already-qualified books with a 5-tick cadence and a bounded qualified share.
4. Deep-predict only the bounded top-K set (launcher currently pins 11; code fallback default is 12, hard bounded 8-16).
5. Build expensive book profiles only for those selected books rather than all 128.
6. Continue full-universe inventory management independently of acquisition shortlist membership.

This removes `super()._research_fast_screen(state)` and `build_all_book_profiles(...)` from the Direct hot path.

### 2. Freshness budget

The 75 ms Maker GTT remains, but A1.5 no longer assumes GTT alone guarantees fresh execution.

Before submitting new Maker exposure, A1.5 checks actual wall-clock decision-path age. If more than 100 ms has already elapsed, the new Maker quote is skipped (`DIRECT_FRESHNESS_SKIP`). Inventory exits are not disabled by this guard.

### 3. Directional Taker entry disabled

A1.3 + A1.4 produced 34 Taker-origin RTs with only 2 positive outcomes. A1.5 therefore disables Direct Taker acquisition completely.

- Taker counterfactual EV remains calculated/loggable for research.
- Kappa cannot re-enable Taker acquisition.
- PositionExitController retains full authority to use Taker for inventory realization/risk reduction.

### 4. Net-realized Maker lifecycle learning

A1.5 learns each Maker-opened lifecycle from cumulative **net realized PnL**, including partial reductions and fees.

The lifecycle tracker accumulates:

- realized PnL across all reductions;
- realized entry notional;
- whether any Taker reduction was used;
- final net realized bps;
- gross entry-to-final price bps for diagnostics only.

This fixes cases where gross price movement was positive but fees made the actual RT negative.

### 5. Kappa-3-like downside severity

For Maker->Taker history, A1.5 tracks both:

- EWMA of net negative shortfall;
- EWMA of cubed net negative shortfall, converted back to a bps LPM3-style severity.

The lifecycle cost blends ordinary shortfall and cubic downside severity, so rare large losses matter more without treating every Taker exit as bad.

### 6. Score-efficient workload

While the 80-book target is incomplete, unfinished books dominate the expensive prediction budget. Already-qualified books are no longer allowed to absorb most of the Direct workload simply because they have repeatedly traded before.

### 7. Telemetry sampling

High-frequency Direct diagnostic events are sampled every 25 ticks where safe. Fill/lifecycle/contract/risk accounting remains authoritative. This reduces Python object/queue pressure without removing the data needed for runtime analysis.

## Preserved from A1.3/A1.4

- Maker minimum economic EV: `0.030`
- Maker maximum inside-touch improvement: `6 bps`
- Maker GTT cap: `75 ms`
- dust liveness / bounded dust exemption
- Kappa target: `80`
- LifecycleEV latency hard gate: OFF
- duplicate adverse hard gate: OFF
- PositionExitController: unchanged
- Strategy1.py: unchanged
- Strategy1_Research.py: unchanged
- BaseStrategy.py: unchanged
- AdaptiveAgent.py: unchanged
- validator logic: unchanged

## Verification

- Direct A1.5 tests: **39/39 PASS**
- Focused A1.5 + V4.16 safety set: **145/145 PASS**
- Launcher preflight: **109/109 PASS**
- Research regression: **481/481 PASS**
- `compileall`: **PASS**
- launcher `bash -n`: **PASS**

The full repository suite is not collectable in this environment because optional project dependencies are absent (`transformers`, `pyarrow`, `bittensor`, `loky`). This does not affect the Research-specific verification above.

## Runtime validation gates

A1.5 must prove these on Agent 68 before promotion:

- p50 response: target `<80-100 ms`
- p95 response: target `<120 ms`, at minimum a large improvement from A1.4 ~229 ms
- screening/ranking cost: materially lower than A1.4 (~71 ms screen + ~38 ms ranking medians)
- Taker-origin entry RTs: `0`
- overall positive RT ratio: `>55%`, target `>60%`
- Maker-origin positive ratio: preserve strong quality
- closed RT PnL: positive without dependence on a few large outliers
- qualification growth: continue toward 80 without spending most RTs on already-qualified books
- dust deadlock: zero
- placements/fill: remain `<25`

A1.5 is a Research candidate only. Do not promote to Base until the runtime gates pass.
