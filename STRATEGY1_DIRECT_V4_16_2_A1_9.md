# Strategy1-Direct V4.16.2 A1.9 — Queue-Preserving Maker Exit

**Phase A (this revision) is measurement only. Runtime behaviour is identical to
A1.7.5.** A1.8 is fully reverted.

## Why A1.8 failed

A1.8 capped the profitable Maker-exit TTL at the existing exit-cycle TTL
(3000 ms → 975 ms) against a verified 1,000 ms publish cadence. Measured over
3,870 ticks against the A1.7.5 baseline, per tick:

| Metric | A1.7.5 | A1.8 | Δ |
|---|---|---|---|
| RT velocity | 0.1046 | 0.0798 | −23.7% |
| Positive RT | 0.0849 | 0.0475 | −44% |
| RT PnL rate | 0.02429 | 0.00505 | −79% |
| Maker fills | 0.1915 | 0.1284 | −33% |
| Maker fills / limit placement | 11.1% | 6.3% | −43% |
| `A172_WAIT_HOLD` | 0.151 | 1.366 | ~9× |
| AGGRESSIVE ladder share | ~51% | ~80% | — |
| p90 exit wait | 93.41 s | 147.43 s | +57.8% |

The median exit wait did improve (36.03 s → 31.47 s), but that is a **selection
effect**: with a one-cycle TTL only trivially-fillable exits survive, so the easy
half got faster while the hard half was starved of queue time. Mean and p90 both
worsened sharply.

## The actual defect

The bottleneck diagnosis — Maker exit realization — was right. The mechanism was
not. Two structural defects make **TTL, not the hysteresis rule, the real
repricing clock**:

1. **The reprice never reaches the exchange.**
   `_research_final_validate_instructions` drops any placement on a book that
   already holds a live order (`INFLIGHT_BOOK_ORDER` / `PREEXISTING_BOOK_ORDER`),
   and nothing cancels the stale exit first. When
   `hold_existing_profitable_maker_exit` declines to hold, the base falls
   through, builds the repriced order, and the validator silently discards it.
   The stale quote then rides to expiry. This is why the observed re-quote
   cadence is 4 ticks rather than 1.

2. **The hold test is symmetric when the economics are not.**
   `hold_existing_profitable_maker_exit` compares `abs(old − new)` against
   `reprice_ticks`. For a SELL exit a *rise* in the desired price leaves the
   resting order more aggressive than needed — closer to filling, same lifecycle
   net — so holding is strictly better. The symmetric test cancels it anyway.

`Strategy1_Research.py:8348` confirms the diagnosis in the codebase's own words:

> `# Never keep a maker exit alive into the next publish cycle.`
> `# This increases live-time without permitting same-side stacking.`

The sub-cycle TTL was a deliberate **workaround** for the missing
cancel-then-replace path. A1.8 generalised that workaround to every profitable
exit.

## The A1.9 mechanism

Hold a resting profitable exit by queue position; reprice only for a structural
reason, via an explicit cancel that lets the replacement land on the next state.

`classify_resting_maker_exit` returns HOLD unless one of these fires:

| Reason | Condition |
|---|---|
| `SIZE_SHORTFALL` | resting qty < 80% of desired qty |
| `NET_BELOW_FLOOR` | resting order's own lifecycle net < exit target |
| `LADDER_ESCALATION` | desired rung more urgent than the resting rung |
| `STALE_BEHIND_TOUCH` | **adverse** drift ≥ `reprice_ticks` (default 3.0) |

The favourable half-plane is held. `reprice_ticks` keeps its existing 3.0
default — **A1.9 introduces no new threshold.** The resting order is judged on
its *own* price, never on the current cycle's desired price.

Holding through price drift is structurally justified, not fitted: measured
`exit_p_fill_horizon` is ~0.043–0.049 and essentially flat across
PASSIVE/COMPETITIVE/AGGRESSIVE, i.e. price aggression buys almost no fill
probability while time-in-book does. Repricing spends the entire queue position
for close to nothing.

### Load-bearing side effect

`_research_note_exit_attempt` early-returns on `placed=False`, so a HOLD
returning 0 does **not** increment `failed_exit_count`. A resting order stops
being counted as a failed exit — this is the mechanism that should reverse the
AGGRESSIVE-ladder share. Age-based escalation (`first_tick`/`since` and the
60-tick `DIRECT_A175_TAIL_BUDGET_TICKS` valve) is unaffected, so genuinely stuck
inventory still escapes on schedule.

## Phase A scope — measurement only

The classifier runs on every exit evaluation and its decision is **discarded**.
Only `_a19_*` telemetry state is written; no threshold, limit or authority is
mutated. `research_profitable_exit_ttl_ms` keeps its 3000 ms A1.7.5 default.

New telemetry:

| Event | Answers |
|---|---|
| `A19_EXIT_EVAL` | eval class, resting present, absent reason, shadow decision, drift, forgone edge, tenure |
| `A19_EXIT_LIFECYCLE` | how each resting exit ended: `EXPIRED` / `FILLED` / `WAIT_CANCEL` / `NEG_AGGRESSIVE_CANCEL` |
| `A19_CANCEL_ACK` | ticks between sending a cancel and the order disappearing |
| `A19_CANCEL_NOT_ACKED` | cancels still live after 2 ticks |
| `A19_EXIT_REFRESH_CONFIG` | effective vs target TTL, observed publish interval |

`A19_CANCEL_ACK` is the important one: Phase B's 2-tick reprice cycle assumes a
cancel sent at T is gone by T+1. Phase A measures that on cancels that already
happen today, rather than assuming it.

### The hold-rate denominator

`exit_eval_class` deliberately mirrors `profitable_maker_exit_ttl_ms`'s own
eligibility test. TOXIC/STRESSED regimes and sub-floor exits correctly keep the
short base TTL and cannot hold a queue position, so they are excluded from the
gate rather than counted as misses.

## Phase plan

| Phase | Ships | Run | Gate |
|---|---|---|---|
| **A** | Shadow classifier + telemetry. No behaviour change. | ~800 ticks | Cancel-ack latency measured; eligibility and forgone-edge distributions known; RT metrics match A1.7.5 |
| **B** | TTL 4000 (= 4× publish cadence) + directional classifier + cancel-then-replace. **Atomic.** | ~4,000 ticks, abort check at tick 500 | See below |
| **C** | WAIT-cancel floor 1.0 → 0.0 bps. Independent. | ~4,000 ticks | Only if B passes |
| **D** | Favourable-side bound (only if Phase A/B measures forgone edge material); TTL 5000 A/B. | — | — |

Phase B must not be split: raising the TTL without the cancel path is
known-harmful, since stale quotes would simply sit longer.

**Phase B abort gate at tick 500** — hold rate over `PERSIST_ELIGIBLE`
evaluations ≥ 20%; `INFLIGHT_BOOK_ORDER` rejects/tick down ≥ 50%; ownership
replay clean; `A19_CANCEL_NOT_ACKED` ≤ 5% of reprices.

**Phase B accept criteria** vs A1.7.5, per tick: RT velocity ≥ 0.110; positive-RT
share ≥ 80%; Maker share on fills ≥ 90.7%; AGGRESSIVE ladder share ≤ 40%; p95
latency ≤ 120 ms; ownership replay clean.

Reject on Maker share below 90.7% — that is adverse selection on longer-resting
quotes materialising, which is the genuine open risk of this design.

## Verification

- All direct regression suites: **263 passed, 11 skipped, 0 failed**
- A1.9 Phase A suite: **22 passed**
- `python -m py_compile`: PASS
- Frozen base `Strategy1_Research.py` unchanged (sha verified against A1.7.5)
- Only `_a19_*` state written by the new code path; `research_profitable_exit_ttl_ms`
  is read-only in the overlay

## Note on the A1.8 manifest

`STRATEGY1_DIRECT_V4_16_2_A1_8_MANIFEST.json` records `frozen_file_sha256`
values that do not match the files. Three of five are wrong —
`Strategy1_Research.py`, `research_direct_positive_maker_kappa.py` and
`research_direct_tail_recovery.py` — although those files are provably unchanged
across A1.7.5, A1.8 and now. The A1.8 freeze attestation was never computed from
the files. A1.9's manifest checksums are computed from the actual files and
carry `frozen_file_sha256_verified: true`.
