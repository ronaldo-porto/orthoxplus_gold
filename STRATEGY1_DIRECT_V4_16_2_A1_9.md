# Strategy1-Direct V4.16.2 A1.9 — Queue-Preserving Maker Exit

**Phase A2 (this revision, A1.9.0.1) is measurement only. Runtime behaviour is
identical to A1.7.5.** A1.8 is fully reverted.

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
not. **Every queue-preservation feature shipped so far reads the agent's live
orders from `state.accounts[uid][book].orders`, and that view never carries the
resting exit at decision time.**

Measured over the A1.7.5 runtime log (29,183 ticks, 33,455 acknowledged limit
orders, 16,787 `PROFITABLE_EXIT_PERSIST` placements):

| Mechanism | Reads | Fired |
|---|---|---|
| `PROFITABLE_EXIT_HOLD` (V4.13.8 queue preservation) | `account.orders` | **0** |
| `A172_WAIT_CANCEL` (A1.7.2 unsafe-exit cancel) | `account.orders` | **0** |
| `A17431_BOOK_OWNERSHIP_BLOCK` | `account.orders` | **0** |
| `FINAL_CONTRACT_REJECT` / `INFLIGHT_BOOK_ORDER` | `account.orders` | **0** |
| `A172_WAIT_HOLD` `kept_profitable_orders` | `account.orders` | **0** across 39,733 events |

The exchange lifecycle stream says the opposite. From `ORDER_LIFECYCLE`:

- a profitable Maker exit rests a **median 3,000 ms** — three full 1,000 ms
  publish cycles — with p25 already at 3,000 ms
- **86%** of them end in expiry-cancel, only **14%** in a fill
- consecutive exit evaluations on a book are **one tick apart 76.4%** of the time
- **27.0%** of consecutive exit placements land while the previous exit is still
  live, and **9.8%** of all orders overlap in time with a later order on the same
  book *and* side

So the resting orders are real, the book is re-evaluated while they rest, and the
agent re-places over them. The hold path simply never sees them. Two consequences
follow, and both were misread before this measurement:

1. **The hold path never executes.** `Strategy1_Research.py:8297-8298` iterates
   `account.orders` before calling `hold_existing_profitable_maker_exit`. The
   loop body has never run in production, so the hysteresis rule has never
   evaluated a single order since V4.13.8. TTL is the *only* repricing clock.

2. **The one-live-order invariant is not enforced against the exchange.**
   It is backed solely by the local pending ledger, which is why 9.8% same-side
   overlap survives it. The earlier reading — that `INFLIGHT_BOOK_ORDER` was
   silently dropping repriced exits — is **wrong**: that rejection has never
   fired. Nothing is being dropped; nothing is being held either.

`Strategy1_Research.py:8348` still explains the intent in the codebase's own
words:

> `# Never keep a maker exit alive into the next publish cycle.`
> `# This increases live-time without permitting same-side stacking.`

The sub-cycle TTL was a workaround for a hold path that could not function.
A1.8 generalised that workaround to every profitable exit, shortening the only
clock that was actually running.

## A1.9.0.1 — resting-exit observability repair (this revision)

Phase A reported `resting_present = 0` on all 3,746 exit evaluations. That
answer was **blind, not economic**: the observer read the same empty
`account.orders` the frozen hold path reads. Phase A was therefore a faithful
reproduction of production behaviour and a valid negative result, but it could
not produce the distributions Phase B needs.

A1.9.0.1 rebuilds the live-order view from the notice stream the exchange
already sends, in `research_direct_exit_ledger.py`:

```
PLACE_ORDER_LIMIT -> RDPOL (LimitOrderPlacementEvent, carries orderId)
                  -> RDCO1 (OrderCancellationEvent) | EVENT_TRADE
```

`onOrderAccepted` / `onOrderCancelled` / `onTrade` feed it; each override
delegates to the frozen handler first. Market orders, rejected placements and
partial fills are handled explicitly — a partial fill decrements the remainder
and keeps the queue position visible.

**Validation by replay.** Feeding the real 29,183-tick log through the new
ledger, at the moment of each exit placement:

| View | Sees a resting close-side exit |
|---|---|
| `account.orders` (Phase A) | 0.0% |
| Ledger, all rows | 57.3% |
| Ledger, rows genuinely inside their TTL | **9.8%** |

The 9.8% independently reproduces the 9.8% same-side overlap measured directly
from the exchange stream, which cross-validates the ledger.

### The expiry-notice lag, and why it decides Phase B safety

The gap between 57.3% and 9.8% is not noise. A removal notice arrives one state
after the exchange acts, so at 47.5% of exit placements the ledger still holds a
row that has *already* reached its 3,000 ms TTL. Median resting age at
re-placement is **3,001 ms** — the replacement decision arrives at the instant of
expiry.

If Phase B held on such a row it would suppress the replacement exit and leave
the position with **no resting order at all**. `live_orders(max_age_ms=…)`
therefore treats any row at or past the TTL it was placed under as gone, and the
observer passes the TTL actually in force. The lagged rows remain visible to the
lifecycle/absent-reason accounting, which is what they are good for.

This also quantifies the A1.9 thesis: at TTL 3,000 ms the exit dies exactly when
the next evaluation wants it, so the queue-preservation ceiling is ~10%. Raising
the TTL to 4,000 ms is what converts the 47.5% "just expired" population into
genuine holds.

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

## Phase A run result (recorded)

The Phase A shadow run completed and was analysed independently. Verdict:
**strategy behaviour PASS, instrumentation FAIL** — the run reproduced A1.7.5
behaviour faithfully but could not produce the distributions Phase B needs.

Run: 1,412 ticks / ~1.98 h, 352 fills.

| Metric | Phase A |
|---|---|
| Maker / Taker fills | 278 (79.0%) / 74 (21.0%) |
| Completed round trips | 157, of which 86 positive (54.8%) |
| RT-close PnL | −13.80 |
| RT velocity | 0.1112 |
| Median / p90 exit wait | 18.34 s / 87.74 s |
| Max global exposure | 1.5001 BASE (limit 2.0) |
| Max per-book exposure | 0.3508 BASE |
| p95 latency | 103.3 ms |

157 round trips is far too small a sample to accept or reject economics, which
is why Phase A's only gate was behaviour-neutrality. The negative RT PnL and the
54.8% positive share are recorded, not acted on.

### Shadow classifier output

3,746 `A19_EXIT_EVAL` events:

| Class | Count | Share |
|---|---|---|
| `PERSIST_ELIGIBLE` | 734 | 19.6% |
| `BELOW_MIN_NET` | 2,551 | 68.1% |
| `SHORT_TTL_REGIME` | 461 | 12.3% |

`resting_present = 1` on **0 of 3,746**. That single number is the whole Phase A
failure, and its cause is the blind spot documented above.

### Gate verdicts

| Gate | Result |
|---|---|
| R1 — hold-rate measurable | **FAIL (blind)** — `resting_present` never fired |
| R2 — forgone edge | **NOT MEASURED** — requires a visible resting order |
| R3 — cancel acknowledgement | watchdog reported nothing; raw lifecycle reconstruction showed **470/470 cancels acked at exactly T+1** |
| R4 — ownership integrity | **PASS** — 0 overlapping ownership, 0 mismatched releases |
| Latency | **PASS** — p95 103.3 ms ≤ 120 ms |

R3 is the one that matters for Phase B, and it is satisfied by reconstruction:
the 2-tick reprice cycle's core assumption holds.

The watchdog was silent for a more basic reason than R1's. `A19_CANCEL_ACK` has
two halves, and both were broken differently:

- **Registration.** `_a19_note_exit_cancel` is called only from the
  `A172_WAIT_CANCEL` path, which builds its `cancel_ids` by iterating
  `account.orders`. That path fired 0 times, so no cancel was ever registered
  and the watch set stayed empty.
- **Resolution.** `_a19_settle_cancel_watch` decided an order had disappeared by
  checking it against a live-id set also derived from `account.orders`. Had a
  cancel ever been registered, it would have resolved instantly at age 0 and
  reported a false 0-tick ack.

A1.9.0.1 fixes the resolution half — the live-id set now comes from the ledger,
including expiry-lagged rows, so an ack is only recorded once the exchange has
actually confirmed the removal. **The registration half cannot be fixed without
a behaviour change**, because A1.7.5 never sends an agent-initiated cancel at
all. R3 therefore stays satisfied by offline reconstruction only, and
`A19_CANCEL_ACK` is expected to remain silent through A2. It becomes live on the
first Phase B reprice, which is also the first moment the agent has a cancel of
its own to measure.

### On the diagnosis

The independent analysis attributed the zero-fire to the observer sitting too
late in the pipeline, with earlier order-lifecycle authority preventing the
new-placement path from being reached while an order is live. The log does not
support that: the call site is reached one tick later 76.4% of the time, and
27.0% of consecutive exit placements land while the previous exit is still live.
The observation point is reached constantly, with a live order present. The
**data source** is the defect, not the pipeline position — which is why
A1.9.0.1 replaces the source and leaves the call site where it is.

## Phase plan

| Phase | Ships | Run | Gate |
|---|---|---|---|
| **A** | Shadow classifier + telemetry. No behaviour change. | done, 1,412 ticks | Behaviour PASS / instrumentation FAIL (recorded above). R1 blind, R2 not measured, R3 satisfied by reconstruction, R4 PASS |
| **A2** | A1.9.0.1 lifecycle ledger. Still no behaviour change. | ~800 ticks | `resting_present` ≈ 10% of placements; ledger vs account gap confirmed; p95 ≤ 120 ms |
| **B** | TTL 4000 (= 4× publish cadence) + directional classifier + cancel-then-replace. **Atomic.** | ~4,000 ticks, abort check at tick 500 | See below |
| **C** | WAIT-cancel floor 1.0 → 0.0 bps. Independent. | ~4,000 ticks | Only if B passes |
| **D** | Favourable-side bound (only if A2/B measures forgone edge material); TTL 5000 A/B. | — | — |

Phase B must not be split: raising the TTL without the cancel path is
known-harmful, since stale quotes would simply sit longer.

### A2 accept gate

A2 is an instrumentation run, so its gate is about measurement fidelity, not
economics:

- `direct_a1901_ledger_only_hits` ≫ `direct_a1901_account_resting_hits` — this is
  the blind spot, restated at runtime. The account view is expected to stay at 0.
- `resting_present = 1` on **≥ 8%** of exit evaluations. The replay predicts
  ~9.8%; materially below 8% means the ledger is not tracking correctly, not that
  the opportunity is absent.
- `direct_a1901_ledger_unmatched_removals` must not grow without bound relative
  to `ledger_accepted`; a rising ratio means notices are being missed.
- `ledger_swept` should stay small. A large sweep count means removal notices are
  not arriving and every age-based conclusion is suspect.
- RT metrics statistically indistinguishable from A1.7.5, and p95 ≤ 120 ms.
- `A19_CANCEL_ACK` is **expected to stay at 0** and is not a gate. A1.7.5 sends
  no agent-initiated cancel, so there is nothing for it to register; silence
  here is correct, not a regression.

Only after A2 does the forgone-edge distribution become meaningful, and only then
should the favourable-side bound in Phase D be considered.

### Phase B abort gate at tick 500

- hold rate over `PERSIST_ELIGIBLE` evaluations **≥ 35%** — recalibrated. The
  earlier ≥ 20% was set before the ceiling was known; at TTL 3,000 the ceiling is
  ~10%, and TTL 4,000 should convert most of the 47.5% expiry-lag population.
- ownership replay clean, and **same-side exchange overlap ≤ 9.8%** — the A1.7.5
  rate. Phase B adds an explicit cancel, so overlap must not rise above the level
  that already exists without one.
- `A19_CANCEL_NOT_ACKED` ≤ 5% of reprices.
- **no book left without a resting exit for more than 2 consecutive ticks while
  holding inventory** — this is the expiry-lag failure mode and it is the one
  defect that would be invisible in aggregate PnL until it costs a tail loss.

The `INFLIGHT_BOOK_ORDER` reduction target from the previous revision is
withdrawn: that rejection never fires, so it cannot fall.

### Phase B accept criteria

vs A1.7.5, per tick: RT velocity ≥ 0.110; positive-RT share ≥ 80%; Maker share on
fills ≥ 90.7%; AGGRESSIVE ladder share ≤ 40%; p95 latency ≤ 120 ms; ownership
replay clean.

Reject on Maker share below 90.7% — that is adverse selection on longer-resting
quotes materialising, which is the genuine open risk of this design.

### Deferred: enforcing the invariant against the ledger

The ledger is an accurate live-order view and could back
`_direct_book_has_live_order`, closing the 9.8% same-side overlap. That is a
**behaviour change and a safety improvement**, so it belongs in neither A nor A2.
It is recorded here as a candidate for its own phase after B, and must not be
bundled into an economics experiment.

## Verification

- All direct regression suites: **295 passed, 11 skipped, 0 failed**
- A1.9.0.1 suite: **32 passed**
- Preflight gate closed: seven pre-existing suites (`a1_5` … `a1_7_0`) and
  `a1_7_4_3_1` were outside the launcher's `RESEARCH_PREFLIGHT_ONLY` list, so
  their assertions never gated a deploy. All pass; all are now listed, and a
  test asserts the list stays closed against `tests/` rather than by memory.
- `python -m py_compile`: PASS
- Frozen base `Strategy1_Research.py` unchanged (sha verified against A1.7.5)
- Only `_a19_*` state written by the new code path; `research_profitable_exit_ttl_ms`
  is read-only in the overlay
- Ledger module holds no strategy authority: no `response`, `cancel_orders`,
  `place_order`, `_emit` or `self.accounts` reference (asserted by test)

## Note on the A1.8 manifest

`STRATEGY1_DIRECT_V4_16_2_A1_8_MANIFEST.json` records `frozen_file_sha256`
values that do not match the files. Three of five are wrong —
`Strategy1_Research.py`, `research_direct_positive_maker_kappa.py` and
`research_direct_tail_recovery.py` — although those files are provably unchanged
across A1.7.5, A1.8 and now. The A1.8 freeze attestation was never computed from
the files. A1.9's manifest checksums are computed from the actual files and
carry `frozen_file_sha256_verified: true`.
