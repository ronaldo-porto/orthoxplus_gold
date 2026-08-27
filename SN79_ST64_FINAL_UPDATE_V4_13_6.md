# SN79 St6.4 FINAL Update — V4.13.6

## Verdict that triggered this patch

V4.13.5 long Testnet validation reached 696 ticks / ~60.7 real minutes. Exit quality was acceptable (4 positive / 2 negative RTs, ~+0.11175 QUOTE, no negative-Taker-over-positive-Maker failure), but Kappa production collapsed:

- Kappa eligible: **15 -> 10**
- upward eligibility transitions: **3**
- expiry/downward transitions: **8**
- TWO_AWAY: **50 -> 55**
- RECYCLING: **1 -> 0**
- final RT velocity: **~0.00863/s**
- detailed KAPPA_COMPLETION decisions: **343/364 NEGATIVE_EV (~94.2%)**

The dominant blocker is therefore Kappa-density / productive-completion scheduling, not exit rescue.

## V4.13.6 scope

V4.13.6 changes only the Kappa-density scheduler. V4.13.5 exit authority and V4.13.4 authoritative execution-lane propagation remain frozen.

### 1. Bounded deep-EV cache for the cheap screen

The latest deep Score-EV `trading_ev` for a ranked book is cached for **20 ticks**.

At the next cheap 128-book screen:

- known `trading_ev > 0` => positive completion evidence;
- known `trading_ev <= 0` => do **not** consume KAPPA_COMPLETION capacity;
- unknown / expired cache => fail-open so the book can be rediscovered.

This is a capacity pre-filter only. The final Score-EV `NEGATIVE_EV` hard gate is unchanged and remains authoritative at execution.

### 2. Density-first completion order

For known-positive completion work, priority is:

1. ONE_AWAY
2. productive CORE / RECYCLING
3. CORE_PROBE
4. TWO_AWAY
5. refresh / density maintenance

Unknown-EV work retains the previous productivity-efficiency protection, so an `INEFFICIENT` ONE_AWAY cannot crowd out a proven productive CORE merely because it is closer to qualification.

The special bootstrap balance is preserved:

- best known-positive ONE_AWAY first;
- known-positive CORE/RECYCLING bridge next;
- when flywheel EV is unknown, CORE_PROBE keeps its V4.13.2 bootstrap priority over an unknown recycling bridge.

### 3. Dynamic density lane budget

V4.13.5 BOOTSTRAP reserved **4 COVERAGE / 3 COMPLETION / 3 REALIZATION** even with a large ONE_AWAY/TWO_AWAY backlog.

When at least one economically-feasible density candidate exists, V4.13.6 dynamically shifts acquisition capacity to:

- **1 COVERAGE**
- **6 KAPPA_COMPLETION**
- **3 REALIZATION**
- shared overflow unchanged

When density demand clears, the normal phase budget returns automatically.

The one COVERAGE slot preserves exploration; all REALIZATION safety capacity remains untouched.

## Frozen behavior

No changes to:

- V4.13.5 Positive-Maker Veto / Fresh Maker Grace
- V4.13.4 authoritative lane propagation
- final `NEGATIVE_EV` Score-EV hard gate
- alpha/signals
- Maker/Taker economics or rescue floors
- parking
- sizing
- concurrency
- persistent Maker / hysteresis
- contract guard
- latency/ranking implementation

## Default V4.13.6 settings

- `research_completion_ev_cache_ticks=20`
- `research_density_priority_enabled=1`
- `research_density_priority_min_candidates=1`

## Verification

- Focused density/execution tests: **36 passed**
- All Research tests: **464 passed**
- Python compile: **PASS**
- launcher bash syntax: **PASS**
- launcher preflight: **PASS**

## Testnet acceptance target

Run V4.13.6 for 250–400 ticks first. Required evidence:

- known `NEGATIVE_EV` completion candidates stop repeatedly consuming completion slots;
- `KAPPA_COMPLETION` quote conversion materially improves from V4.13.5;
- ONE_AWAY starts converting to qualified books;
- `RECYCLING > 0` and/or repeated productive CORE RTs;
- Kappa eligible stops shrinking, preferably begins rising;
- RT velocity materially improves from ~0.0086/s toward **>0.015/s first**, then **>0.03/s**;
- positive RT ratio remains **>60%**;
- realized PnL remains positive;
- placements/fill remains **<15**;
- `LANE_NOT_GRANTED=0`.

Do not move to latency/concurrency/parking optimization until this density scheduler is proven at runtime.
