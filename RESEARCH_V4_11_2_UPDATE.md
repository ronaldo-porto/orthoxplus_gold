# SN79 Research V4.11.2 — Aggressive Positive-EV Completion

V4.11.2 is a **Research-only** update on top of V4.11.1. BaseStrategy and AdaptiveAgent are unchanged.

## 1. Aggressive positive-EV Taker authority

The new authority exists to realize profitable inventory faster when the legacy holding-cost gate is too conservative.

A normal aggressive Taker is authorized only when all of the following are true:

1. `research_enable_economic_taker=1`
2. `research_enable_economic_taker_direct=1`
3. `research_enable_aggressive_positive_ev_taker=1`
4. `expected_taker_exit_value >= research_aggressive_positive_ev_min_net_bps`
5. `expected_taker_exit_value > expected_maker_exit_value + switch_margin`
6. At least one explicit realization trigger is present:
   - `ONE_AWAY` (`observations_remaining == 1`)
   - `failed_exit_count >= 8`
   - inventory / exit-wait age `>= 16` ticks
   - maker fill probability `< 5%`
   - exit urgency `>= 0.30`

Default economics are intentionally strict:

- normal positive-EV floor: `0.0 bps`
- normal switch margin: `0.50 bps`
- ONE_AWAY switch margin: `0.0 bps`
- negative score subsidy remains disabled
- `authority=NONE` still cannot execute Taker
- catastrophic hard-risk behavior remains separate

When this authority fires, the requested reduce fraction is raised to **90%**. The existing exact-reduction layer may flatten fully when a 90% partial would leave unsizable dust.

### New Taker telemetry

`[S1R_TAKER_DECISION]` now includes:

- `pos_ev_auth`
- `pos_ev_trigger`
- `pos_ev_adv`
- `pos_ev_margin`
- `pos_ev_floor`

`[S1R_HYBRID_SUMMARY]` now reports `p<N>` in the authority counters for aggressive positive-EV authorizations.

## 2. ONE_AWAY exact-0.25 feasibility

V4.11.1 correctly recognizes rolling ONE_AWAY books, but logs showed positive-EV 2/3-observation books still blocked by multiplicative soft sizing. Book 43 was the representative shape: safe size ~0.183, exit capacity ~0.243, min order 0.25, positive TradingEV.

V4.11.2 adds a separate completion-only admission path. An `UNSAFE` soft-sized book may be promoted to exactly the exchange minimum when:

- authoritative rolling Kappa state says `observations_remaining == 1`
- `trading_ev > 0.0`
- `safe_size >= 50% * min_order`
- `expected_exit_capacity >= 90% * min_order`
- full-min inventory risk is within the existing hard configured bound
- volume-cap headroom passes the existing bound
- remaining inventory room can legally hold the exact min order

The admission is logged as:

`admission=NEAR_SAFE trigger=ONE_AWAY_EXACT_MIN promoted=1 size=0.25`

This path does **not** apply to negative-EV books or books with materially insufficient exit capacity.

## 3. Defaults in the Research launcher

```text
research_enable_aggressive_positive_ev_taker=1
research_aggressive_positive_ev_min_net_bps=0.0
research_aggressive_positive_ev_switch_margin_bps=0.50
research_aggressive_positive_ev_one_away_margin_bps=0.0
research_aggressive_positive_ev_failed_exit_count=8
research_aggressive_positive_ev_min_age_ticks=16
research_aggressive_positive_ev_max_maker_fill=0.05
research_aggressive_positive_ev_min_urgency=0.30

research_one_away_exact_min_enabled=1
research_one_away_exact_min_ev_bps=0.0
research_one_away_exact_min_safe_fraction=0.50
research_one_away_exact_min_exit_fraction=0.90
```

## 4. Verification

New V4.11.2 regression tests cover:

- positive ONE_AWAY Taker bypasses the legacy `econ.take` gate
- explicit trigger is mandatory
- negative Taker EV remains blocked even for ONE_AWAY / old / failed inventory
- failed exits can activate a positive-EV Taker when it beats WAIT
- aggressive positive-EV reduction targets 90%
- the Book-43-style ONE_AWAY shape promotes to exactly 0.25
- negative EV and insufficient exit capacity remain blocked
- launcher and Strategy wiring are present

Focused V4.11.2 tests: **7 passed**.

Full Research suite: **329 passed, 5 failed**. The same 5 failures are present in the V4.11.1 baseline, so V4.11.2 introduces **0 new Research-suite failures**.

Python compilation and both primary Research launchers pass syntax validation.

## 5. Expected live invariants

A healthy V4.11.2 log should show cases such as:

```text
[S1R_KAPPA] ... obs=2 remaining=1 ... lane=COMPLETION
[S1R_ENTRY_SIZE] ... admission=NEAR_SAFE trigger=ONE_AWAY_EXACT_MIN ... min_order=0.25
```

and profitable fast realization such as:

```text
[S1R_TAKER_DECISION] ... taker_ev=>=0 wait_ev=<taker_ev pos_ev_auth=1 pos_ev_trigger=ONE_AWAY authority=ECONOMIC direct_auth=1
```

A negative Taker must still show `pos_ev_auth=0` and must never execute through `authority=NONE`.
