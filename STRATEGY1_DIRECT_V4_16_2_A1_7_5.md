# Strategy1-Direct V4.16.2 A1.7.5 — Relative Tail Authority

A1.7.5 is a combined economic correction on top of the runtime-validated
A1.7.4.5 candidate.  It changes no mechanical contract: ownership release,
in-flight reservation, TradeEvent de-duplication, TRUE-WAIT execution, FastPath
selection, 0.25 BASE sizing, the 2.0 BASE aggregate cap, the 2.5 bps base Maker
edge floor, the 15 bps QUIET entry floor, and disabled Taker entry are all
frozen exactly as shipped.

The patch addresses three defects that share one root cause: **an A1.7.4.x guard
blocks a bad action without providing a path to a good one**, so positions and
residuals accumulate instead of completing.

## Runtime evidence motivating the patch

Basis: agent-67 session `strategy1_research_agent_67_20260909_184012.jsonl`,
engine `strategy1_direct_v4_16_2_a1_7_4_5`, 2,900 ticks, 322 round trips.

The A1.7.4.x mechanical patches are confirmed working in that run — aggregate
exposure held at 1.39 BASE under the 2.0 cap, contract rejects fell from 1,250
to 4, ownership reserve/release balanced (3,544/3,541) with 2,797 stale cancels
correctly ignored, net realized PnL **+75.08**.  Those results are frozen.

The same run also shows `inventory_age_p90` at **1,329 ticks** (was 116) and a
risk-Taker round-trip share of **44.6%** against a `<25%` gate.

### Defect 1 — the forced-Taker leak

84 round trips crossed as a negative risk Taker while a better Maker was
executable:

| metric | value |
|---|---:|
| Maker strictly better than Taker | 82/84 (**97.6%**) |
| median / mean Maker advantage | **+15.8 / +21.1 bps** |
| median Maker net / Taker net | -19.0 / **-37.5 bps** |
| `catastrophic` / `hard_risk_trigger` | **0 / 0 on every event** |
| bands | HARD_ESCAPE 59, ABSOLUTE_PROTECTION 25 |

Joining those `(tick, book)` pairs back to `A171_EXIT_DIAGNOSTIC` and
`REALIZATION` gives the causal chain: **154/168 joined rows (91.7%) had
`failed_exit_count >= 2`**.  A position deteriorates, the A1.7.4 recovery
corridor opens, the recovery Maker is placed but is never hit because
`trade_rate ≈ 0` in QUIET, `failed_exit_count` climbs past
`DIRECT_RECOVERY_MAKER_MAX_FAILED_EXITS = 2` and permanently disables recovery
Maker, `RECOVERY_TAKER_REDUCE` needs `taker >= -25 bps` while the actual Taker
is -37 to -48, and the A1.7.4.4 veto needs `maker >= +10 bps` **absolute** while
the actual Maker is -19.  Nothing is left but a forced cross.

Two design errors compound.  `failed_exit_count` conflates *"Maker is
unachievable"* with *"the book is quiet"* — in QUIET it grows with time, not
information.  And A1.7.4.4 asks an **absolute** question (*is Maker good?*) when
the decision is inherently **relative** (*is Maker better than crossing?*).
`recovery_maker_allowed()` already gets this right; the veto overlay did not.

`A174_TAIL_COUNTERFACTUAL` (77 events) independently prices the leak at a median
of **34.3 bps** and a mean of **41.5 bps** of avoidable loss per tail event.

### Defect 2 — the dust guard permanently traps capital

| metric | value |
|---|---:|
| `A1742_DUST_KAPPA_BLOCK` events | 1,392 — **all on Book 80** |
| dust age across those blocks | 949 → **2,460 ticks** (still trapped at run end) |
| realization at first block (tick 1411) | **-60.18 bps** (floor -60.0, missed by 0.18) |
| median realization once trapped | **-202.5 bps**, min -320.1 |
| `research_dust_compact_fills` | **0** in 36 orders (ratio 0.007) |
| `research_parked_dust_abs_base` | **0.3902** = 20% of the 2.0 BASE cap |

The A1.7.4.2 floor is static and fail-closed with no escape.  It refused a
bounded **-60 bps** exit and thereby created an unbounded **-320 bps** frozen
position that also consumed a fifth of the exposure budget indefinitely.

### Defect 3 — the QUIET entry floor is unmeasured

475 `A1745_ENTRY_BLOCK_LOW_EDGE` events, blocked edges spanning
**10.07 – 14.99 bps** (median 12.4, spread 20.2 – 30.0 bps).  No blocked edge
fell below 10 bps, so the 15 bps floor rejects 100% of the QUIET opportunities
the funnel actually produces.  Coverage held up (`coverage_velocity` 0.0317 vs
0.0266), so this is not currently harmful — but there is no evidence either way
about whether those entries would have been profitable.

## Authority changes

### Change 1 — relative tail authority

`research_direct_positive_maker_kappa.py` gains a **second veto arm** beside the
frozen absolute one.  All existing preconditions still gate both arms
(`action == TAKER_EXIT`, reason in `{HARD_ESCAPE_CLIP,
ABSOLUTE_PROTECTION_REDUCE}`, `maker_executable`, `taker < 0`, and
`not catastrophic_hard_risk`).  The risk Taker is vetoed when **either**:

- **absolute arm (frozen A1.7.4.4)** — `maker >= +10 bps`; or
- **relative arm (new)** — `maker - taker >= 15 bps` **and** `maker >= the band
  floor` for the base decision's `risk_band`.

```
DIRECT_A175_MAKER_ADVANTAGE_BPS = 15.0        # = the observed median advantage
DIRECT_A175_BAND_FLOOR_BPS = {DEFENSIVE: -25.0, HARD_ESCAPE: -30.0, ABSOLUTE: -35.0}
```

The band floors are the existing `DIRECT_RECOVERY_MAKER_FLOOR_BPS`,
`DIRECT_HARD_RECOVERY_MAKER_FLOOR_BPS` and
`DIRECT_ABSOLUTE_RECOVERY_MAKER_FLOOR_BPS` constants re-used from
`research_direct_tail_recovery`, not redefined.  The two arms stay
distinguishable: `corridor_action` is `DIRECT_POSITIVE_MAKER_KAPPA_A1744` versus
`DIRECT_POSITIVE_MAKER_KAPPA_A175_RELATIVE`, and `classify_a1744_outcome()`
reports `A175_RELATIVE_MAKER_RISK_VETO` separately so
`A1744_TAKER_ALLOWED_MAKER_NOT_STRONG` keeps its original meaning.

`research_direct_tail_recovery.py` stops letting quiet-book non-fills disable
recovery Maker.  In the `BAND_DEFENSIVE` and `BAND_HARD_ESCAPE` arms only, the
bare failed-exit gate becomes:

```
failed < DIRECT_RECOVERY_MAKER_MAX_FAILED_EXITS
    or a175_failed_exit_override(maker_net_bps=maker, taker_net_bps=taker)
```

Floors, trigger and force thresholds, the worsening-slope test, the min-age
test, `RECOVERY_TAKER_REDUCE`, and the ABSOLUTE arm's stricter
`MAX_FAILED_EXITS = 1` are all unchanged.

**The hold is bounded.**  A1.7.4.4's veto had no time limit, which is why 160
vetoes landed on just 11 books with a median `failed_exit_count` of 12 (max 30).
`Strategy1_Research_Simple.py` now tracks a per-book tail budget of
`DIRECT_A175_TAIL_BUDGET_TICKS = 60` consecutive vetoed ticks.  On exhaustion
the veto releases to the base decision, `A175_TAIL_BUDGET_EXHAUSTED` is emitted,
and the book **does not re-arm until it goes flat** — the generic
`A1744_VETO_RELEASE` path deliberately cannot clear the exhausted flag, which is
what prevents veto oscillation.  The flag self-heals when the book is observed
flat.  This turns *hold forever* into *hold while it is demonstrably better,
then take the bounded loss*.

Replayed against the real 84 observed events: **42 recovered, mean +38.0 bps
each (1,598 bps total)**; residual leak mean **4.2 bps** against the `<5 bps`
gate; and 27 of the 42 still crossing are correctly below their band floor.

### Change 2 — age-escalated dust escape

`research_direct_dust_kappa.py` makes the floor a function of residual age via
`a175_age_escalated_floor_bps()`:

```
DIRECT_A175_DUST_PATIENCE_TICKS   = 600      # -60 bps holds unchanged below this
DIRECT_A175_DUST_ESCALATION_TICKS = 2000     # fully widened by here
DIRECT_A175_DUST_MAX_FLOOR_BPS    = -250.0
```

Flat at **-60 bps** through 600 ticks, interpolated linearly to **-250 bps** at
2,000 ticks, flat beyond.  Escalation can only ever widen the budget.
`UNKNOWN_COST_BASIS` still fails closed **at any age**, `NON_CROSS_COMPACTION`
is still always allowed, and every fresh residual keeps the identical -60 bps
budget — this only affects residuals the guard has already refused for hundreds
of ticks.  Verified against Book 80's exact inputs, it now clears at its first
block instead of decaying to -320 bps.

New reason code `AGE_ESCALATED_KAPPA_BUDGET`.  `as_log()` gains
`dust_kappa_effective_floor_bps`, `dust_kappa_age_escalated`, and
`dust_kappa_best_realization_bps` — the last records the best (least-negative)
realization seen per trapped book, so a later version can calibrate the patience
window against what was actually reachable.  The -60.18 bps miss at tick 1411 is
exactly the datum that was not captured last run.

### Change 3 — QUIET shadow measurement (telemetry only)

**The 15 bps QUIET entry floor is unchanged and still authoritative.**  A1.7.5
adds only the measurement, so a later version can recalibrate from data rather
than from a retrospective filter.

A bounded per-book shadow ledger (256 entries, FIFO) records each blocked entry
and, after `DIRECT_A175_SHADOW_HORIZON_TICKS = 200`, emits:

```
A175_QUIET_SHADOW_OUTCOME
  {book, tick, blocked_edge_bps, horizon_ticks,
   forward_mid_markout_bps, would_have_been_adverse}
```

This is strictly diagnostic and must never touch `effective_maker_min_edge_bps`
or any execution path; the A1.7.5 suite asserts the helpers leave
`effective_maker_min_edge_bps`, `response`, and `instructions` untouched.

**Honest limitation:** forward mid-markout measures *adverse selection*, not
*fill probability*.  It bounds the upside of relaxing the floor; it does not
prove that relaxing it would have been profitable.

## New telemetry

- `A175_RELATIVE_MAKER_RISK_VETO` — with `maker_advantage_bps`, `veto_arm`,
  `advantage_floor_bps`, `risk_band`, `tail_budget_ticks`, `tail_budget_used`
- `A175_TAIL_BUDGET_EXHAUSTED`
- `A175_QUIET_SHADOW_OUTCOME`
- `AGE_ESCALATED_KAPPA_BUDGET` reason on `A1742_DUST_KAPPA_*` records

`RUN_SUMMARY` gains `direct_a175_maker_advantage_bps`,
`direct_a175_relative_veto_count`, `direct_a175_tail_budget_ticks` /
`_releases` / `_books`, `direct_a175_shadow_horizon_ticks` / `_recorded` /
`_resolved` / `_adverse` / `_pending`, `direct_a175_dust_patience_ticks`,
`direct_a175_dust_escalation_ticks`, `direct_a175_dust_max_floor_bps`, and
`direct_a175_dust_age_escalated_allows`.

## Frozen mechanics and economics

Unchanged from A1.7.4.5:

- the A1.7.4.5 QUIET / zero-rebate 15 bps entry gate and its four conditions;
- the A1.7.4.4 absolute positive-Maker Kappa veto arm and its +10 bps floor;
- A1.7.4.3.2 exact exchange-order identity-safe ownership release;
- A1.7.4.3 strict in-flight 2.0 BASE aggregate exposure reservation;
- A1.7.4.2 base dust Kappa floor of **-60 bps** for all fresh residuals;
- A1.7.4.1 own-TradeEvent replay de-duplication;
- A1.7.4 recovery trigger / force / floor constants and the ABSOLUTE arm;
- A1.7.2 TRUE-WAIT execution semantics;
- A1.7.1 true-MTM risk semantics;
- FastPath 20/16 candidate selection;
- the 2.5 bps base observable Maker edge floor;
- Maker size **0.25 BASE** and the 2.0 BASE aggregate cap;
- directional Taker entry remains disabled.

**Catastrophic and MAX-exposure protection continue to bypass every overlay in
this release.**  Both new veto arms are skipped entirely when
`catastrophic_hard_risk` is set.

## Runtime acceptance gates

A fresh ≥2,900-tick run on the agent-67 environment, compared like-for-like.
Confirm `engine_version` reads `strategy1_direct_v4_16_2_a1_7_5` in the log head
before trusting any comparison — the A1.7.4.x chain sat unvalidated for two days
precisely because a stale engine kept running.

| gate | A1.7.4.5 baseline | A1.7.5 target |
|---|---:|---:|
| risk-Taker RT share | 44.6% (99/222) | **<25%** |
| forced crossings w/ better Maker | 84 | **<20** |
| mean thrown-away Maker advantage | 21.1 bps | **<5 bps** |
| `research_oldest_dust_ticks` | 2,414 | **<600** |
| `research_parked_dust_abs_base` | 0.3902 | **<0.25** |
| `research_dust_maker_fill_ratio` | 0.007 | **>0.10** |
| `inventory_age_p90` | 1,329 ticks | **<400** |
| net realized PnL / 2,900 ticks | +75.08 | **positive, ≥ baseline** |
| `research_total_abs_base` | 1.3902 | **≤2.0 (must not regress)** |
| RT velocity | 0.111/s | **≥0.10/s** |
| Maker-ending positive RT | 123 exits, +63.65 | **≥90% positive** |
| p95 respond | mean 29.7 / max 224 ms | **p95 <120 ms** |

Additionally confirm `A175_QUIET_SHADOW_OUTCOME` is emitted and that
`A1745_ENTRY_BLOCK_LOW_EDGE` counts and the effective floor are identical in
behaviour to A1.7.4.5 — the shadow ledger must be provably execution-inert.

## Deployment note

This repo (`RealNet_Adapt`, agent 68) is still running **A1.7.3.1**; its live log
`strategy1_research_agent_68_20260907_224919.jsonl` contains no `A174*`
telemetry at all, so the entire A1.7.4 → A1.7.4.5 chain has never executed here.
All A1.7.4.x runtime evidence above comes from the sibling `RealNet` checkout on
agent 67.  Decide explicitly whether agent 68 is restarted onto A1.7.5 or
deliberately held on A1.7.3.1 as a control.
