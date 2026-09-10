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

## A1.9.0.1 — resting-exit observability repair (ledger; superseded by A1.9.0.2)

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

## A1.9.0.2 — live resting-exit observer (PASSED)

A1.9.0.1 shipped and **still measured `resting_present = 0`**:

| Counter | A1.9.0.1 run (298 ticks) |
|---|---|
| `A19_EXIT_EVAL` | 492 |
| `PERSIST_ELIGIBLE` | 255 |
| `resting_present = 1` | **0** |
| shadow HOLD/REPRICE decisions | **0** |

The ledger was not at fault — it did detect the old exit orders. The **call
site** was. Both A1.9.0 and A1.9.0.1 hung the observer inside
`_research_place_maker_exit`, which only runs once the strategy has decided to
place a *new* exit. That moment is structurally after the old exit is dead, for
two independent reasons:

1. the frozen final validator **drops a placement onto a book that still holds a
   live order**, so a book whose exit is resting never reaches the placement
   path at all; and
2. the 3,000 ms exit TTL **expires a full second before** the ~4,000 ms re-quote
   cycle comes back round (the exit-TTL duty-cycle defect).

So the placement path can only ever observe the one instant when the order is
already gone. The 9.8% "inside TTL" figure in the table above is not the
mechanism's ceiling — **it is an artifact of sampling at placement time.**

### The change

Observe every open-inventory book **each tick, at the top of `respond`**, before
the frozen decision chain and therefore before every live-order and ownership
gate that would skip the book. The book is examined because it *carries a
position*, not because a placement is pending.

This is sound because the SDK calls `update(state)` — which drives
`onOrderAccepted` / `onOrderCancelled` — **before** `respond(state)`, so the
ledger read at the top of `respond` is already this tick's truth.

Measured on the same lifecycle in test, the observer now sees the exit for its
whole resting life rather than at one dead instant:

| Tick | `resting_present` | age | account view | shadow decision |
|---|---|---|---|---|
| 1 | 1 | 0 ms | 0 | HOLD / QUEUE_PRESERVED |
| 2 | 1 | 1,000 ms | 0 | HOLD / QUEUE_PRESERVED |
| 3 | 1 | 2,000 ms | 0 | HOLD / QUEUE_PRESERVED |

`resting_present_account` stays 0 throughout — the A1.9.0.1 control is retained
precisely so this contrast keeps being measured.

### The load-bearing constraint: no position ageing

The obvious way to read the position in the new pass is `_net_inventory(book,
mid)`. **It must not be used.** That method advances `_position_ticks` once per
`_tick`, guarded by `_research_position_tick_seen[book] != current_tick`, and
this pass runs *before* the base increments `_tick`. For a book that was not
evaluated on the previous tick the guard does not hold, so the pre-pass would
age the position once and the tick's real call would age it again. Position age
drives the exit escalation ladder, so double-ageing is a live trading-behaviour
change — exactly what a measurement-only revision must not do.

The observer therefore reads `_position_tracker_snapshot` (pure) through
`RestingInventoryView`, a frozen two-field view carrying only `net_base` and
`vwap_entry` — all the shadow classifier needs. A test asserts
`self._net_inventory(` never appears in the observer body.

### Comparand and scope

Outside the placement path there is no "desired" ladder rung, so the observer
compares the resting quote against **the passive touch** — the price the book
would quote if it repriced right now, which is precisely what a HOLD gives up.
`desired_action` is passed as `None`; `ladder_rung(None)` is `-1`, so escalation
cannot fire on a missing comparand rather than on a real escalation.

Cancel-ack settlement deliberately **stays** in the placement path, where
`_tick` has already been incremented and the measured latency is on the right
clock. The new pass only peeks at the cancel watch without consuming it. The
A1.9.0.1 run already confirmed acks are effectively T+1, so cancel latency is
not the blocker.

### A newly measured blind spot

`live_orders(max_age_ms=…)` silently drops rows whose acknowledgement carried no
usable timestamp, because their age is unknowable. That is indistinguishable
from expiry lag unless counted — and would read as a permanently blind observer,
the exact failure this revision exists to rule out. `ledger_untimed` /
`direct_a1902_tick_untimed_rows` now count it. It is expected to be 0:
`SimulationEvent.timestamp` is a real property on the placement notice.

### Still measurement only

No instruction, no cancel, no threshold write; the observer is wrapped so a
fault cannot cost a trading tick. `research_profitable_exit_ttl_ms` keeps its
3,000 ms default. **A1.9.1 remains blocked** until a run shows
`direct_a1902_tick_resting_hits > 0` with a real HOLD/REPRICE distribution.

## A1.9.0.3 — lifecycle attribution repair (PASSED)

A1.9.0.2 **passed the observability gate**:

| Metric | A1.9.0.2 run |
|---|---|
| `resting_present = 1` | 665 |
| shadow HOLD / REPRICE | 329 / 162 |
| HOLD rate on persist-eligible live exits | ~68.8% (gate was 20%) |
| forgone edge on HOLD | median 0 bps, p90 ~1.22 bps |
| cancel acks | 60/60 at T+1 |
| Maker share / positive RT / RT velocity | 95.1% / 87.2% / ~0.221/s |
| ownership violations / max exposure / p95 | 0 / ~1.64 BASE / ~100 ms |

The one defect was instrumentation: **some explicitly cancelled exits were
labelled EXPIRED**. Investigation found this was not one bug but four.

### The four defects

1. **Two cancel paths never registered.** Only `_direct_cancel_unsafe_wait_exits`
   called `_a19_note_exit_cancel`. `_direct_cancel_entry_quotes` and the
   partial-remainder cancel (`A173_PARTIAL_REMAINDER_CANCEL`) did not, so any
   tracked order they killed fell through to EXPIRED.
2. **The cancel lookup was not keyed to the tracked order.** It scanned for *any*
   watched order on the book, so it could attribute a different order's reason to
   this one.
3. **The reason could already be consumed.** `_a19_settle_cancel_watch` **pops**
   watch entries from the placement path, so the reason could be gone before the
   tick observer noticed the disappearance one or two states later.
4. **Entry quotes were adopted as Maker exits.** On a long book the entry ASK
   rests on the close side, and the ledger keys rows by book+side only. An entry
   quote could therefore be adopted as "the resting exit" — inflating
   `resting_present` and the hold rate, and reporting EXPIRED when the quote
   manager cancelled it.

Defect 4 is the consequential one: **the A1.9.0.2 counts above may be inflated**,
so the A1.9.0.3 run re-establishes them on true exits only before Phase B is
gated on them.

### The repair

Disposition is now resolved from **ranked evidence** rather than inference:

1. the ledger's own removal cause — a fill is a fill;
2. a cancel **we** registered for that **exact order id**;
3. only with no notice at all, the position-shrank fallback.

A cancellation notice we never asked for is a real expiry — the exchange retires
an order at TTL through the same notice — so EXPIRED now means the exchange
retired it, not "we could not tell". Supporting changes:

- the ledger keeps a bounded `removal_causes` memo so the cause **outlives the
  row**, cleared on `reset()` because a restart reuses order ids;
- a `_a19_cancel_reason` memo keyed by order id outlives the cancel *watch*, so
  the placement path consuming a watch entry can no longer erase the reason;
- all three `cancel_orders` sites register a reason, guarded by a test that
  fails if any future `cancel_orders` call lands unregistered;
- entry quotes are excluded by the same client-id convention the cancel path
  uses (`_direct_entry_quote_client_ids`, now the single source of truth);
- `LEDGER_SWEEP` is its own disposition — bounded memory is not a measured
  expiry and must never inflate the expiry rate.

Per-disposition tallies ship as `direct_a1903_disposition_*`, with
`direct_a1903_agent_cancelled_lifecycles` separating cancels we asked for from
exchange-side expiry.

### Verification

The repair is mutation-tested — reverting each of the three fixes in turn is
caught by 4, 1 and 2 tests respectively. Notably, reverting the disposition logic
does **not** break the simple WAIT-cancel case, which is exactly why the defect
showed up as *some* cancels mislabelled rather than all of them.

Still measurement only; **run 150–200 ticks**, then A1.9.1 Phase B.

## A1.9.1 — Queue-Preserving Maker Exit, behavioural Phase B (this revision)

Phase A is complete and passed. Over 260 ticks (95.4% QUIET): 960 live
resting-exit sightings, HOLD/REPRICE 455/457 on the true persist-eligible
population, a clean reason split (QUEUE_PRESERVED vs STALE_BEHIND_TOUCH with no
mixed reasons), HOLD forgone edge median **0.00 bps** / p90 **1.55 bps**, drift
separation of median 1 tick (HOLD) vs 12 ticks (REPRICE), and cancel acks
**74/74 at exactly T+1**.

### What actually changes

**HOLD is the absence of an action.** Today's baseline already rides the exit to
expiry — the frozen hold hook at `Strategy1_Research.py:8296` reads
`account.orders`, the blind view, which is why `PROFITABLE_EXIT_HOLD` has fired
0 times in every run. So the behavioural delta is exactly two things, and they
are one mechanism that must not be split (raising TTL without the cancel path is
the known-harmful configuration):

1. `research_profitable_exit_ttl_ms` **3000 → 4000** — 4x the verified 1,000 ms
   publish cadence — closing the ~1 s dead window between expiry and the next
   re-quote. Set through PARAMS, where the frozen base clamps it to
   [1000, 5000]. **Never** mutated in `initialize()`; that is how A1.8 did it.
2. An **explicit cancel** for a resting exit the classifier calls stale.

Everything else stays frozen: size 0.25, 6 active books, 2.0 BASE cap, QUIET
gate, Taker authority, A1.7.5 tail authority.

### Where the cancel is emitted, and why it is not the placement path

Measured on the A1.9.0.1 build, the placement path saw a live resting exit on
**0 of 492 calls** while the tick observer saw **960 in 260 ticks**. A book whose
exit is resting may simply never reach a placement decision, so a cancel emitted
only from `_research_place_maker_exit` would never fire.

The cancel therefore goes out in a **post-pass after the frozen chain has built
the response**, which also means the shared per-book instruction budget is known
at that point and a cancel can never displace a placement the strategy already
decided on. HOLD is still enforced *in* the placement path, by returning 0 —
correct whether or not that path runs.

```
T    classifier says REPRICE  ->  cancel the exact order id (post-pass)
T+1  cancellation visible     ->  normal placement path may re-enter the book
```

No replacement is placed in the same response. That is the A1.7.4.3.1 ownership
rule, and the 1-tick gap is the accepted cost — cheaper than today's ~1 s dead
window.

### Two gates on REPRICE

**Remaining TTL.** Cancelling an order inside one publish cycle of its expiry
spends an instruction and a tick to achieve exactly what expiry achieves for
free, so below 1,000 ms remaining the verdict falls back to HOLD
(`deferred=REMAINING_TTL`). Structural, not fitted.

**Instruction budget (R3).** Cancels share the 5-instruction per-book budget
with placements. When it is exhausted the verdict falls back to HOLD rather than
spending the last slot on a teardown that cannot be replaced
(`A191_REPRICE_DEFERRED`, `deferred=INSTRUCTION_BUDGET`).

### Why HOLD should also reduce ladder escalation

`_research_note_exit_attempt` early-returns on `placed=False`
(`Strategy1_Research.py:7639`), so returning 0 for a preserved order means it
**stops counting as a failed exit** and stops driving the AGGRESSIVE ladder.
AGGRESSIVE share falling is therefore a predicted *success* signal, not only a
tripwire.

### A measurement caveat carried into the gate

The 49.9% Phase-A hold rate is an **observation-level** rate: each resting order
is re-observed every tick (960 observations across 234 lifecycles ≈ 4.1 each),
and in shadow mode a stale order votes REPRICE again every tick until it
expires, while being unable to fill. Under Phase B it is cancelled on its
*first* REPRICE vote and leaves the population. So the per-order reprice rate is
lower than 49.9%, and real cancel volume will be below 457/260 ≈ 1.76/tick.

`A19_TICK_OBSERVE` now carries `order_id`, so a per-order hold rate can be
computed directly rather than inferred. `direct_a191_hold_share_pct` reports the
acted-on split (holds vs reprice cancels), which is the Phase-B analogue.

### Verification

- All direct regression suites: **381 passed, 11 skipped, 0 failed**
  (A1.9.0.3 was 347 — the delta is exactly the 34 new A1.9.1 tests)
- Mutation-tested: removing the remaining-TTL gate, the instruction-budget
  guard, or the once-only cancel flag is caught by 1 test each
- The A1.7.2 frozen landmine holds — `return super()._research_place_maker_exit`
  is present exactly once and unwrapped

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

### The falsifiable prediction

Phase B is not a hope that longer resting helps. The measurements make it
arithmetic, and therefore refutable.

Measured `exit_p_fill_horizon` is 0.043–0.049 per publish cycle and essentially
flat across PASSIVE/COMPETITIVE/AGGRESSIVE. If cycles were independent, a TTL of
*n* cycles fills with probability `1 − (1 − p)^n`:

| TTL | Cycles | Predicted fill | Measured |
|---|---|---|---|
| 3,000 ms | 3 | 12.4 – 14.0% | **14%** |
| 4,000 ms | 4 | 16.1 – 18.2% | — |
| 5,000 ms | 5 | 19.7 – 22.2% | — |

The measured 14% lands at the top of the predicted 3-cycle band. That is a third
independent confirmation of the same picture, arrived at from per-cycle fill
probability rather than from the lifecycle stream or the ledger replay.

**So Phase B predicts Maker exit realization rises from ~14% to ~17%, a ~+30%
relative gain, and the expiry share falls from 86% to ~83%.**

This is the cleanest gate in the whole programme:

- Fill share lands in **16–18%** → the flat-`p_fill` model holds, the mechanism
  works, and Phase D's TTL 5,000 A/B is justified by the same arithmetic.
- Fill share stays near **14%** → resting time is not what limits the fill.
  Something else gates it, and Phase B should be reverted rather than tuned.
- Fill share rises **but Maker share on fills drops below 90.7%** → the fills
  bought by the extra cycle are adverse selection. This is the outcome that
  invalidates the A1.9 thesis rather than refining it, and it is why Maker share
  is a *reject* criterion and not merely a metric.

The third case is the real risk of this design and it deserves naming plainly:
the whole programme assumes the 86% that expire are unlucky rather than
unwanted. If they expire because the market has already moved away from them,
then holding them longer does not convert them — it only lets the informed
traders reach them. The fill-share and Maker-share pair separates those two
worlds in a single run.

## Destination: what "seriously competitive" means numerically

Phase B's accept criteria are *non-regression* gates against A1.7.5. They are
not the destination. The destination for a top-tier agent, reached no earlier
than A2.0:

| Metric | A1.7.5 today | Destination |
|---|---|---|
| Positive RT share | ~81.2% | ≥ 88–90% |
| Maker-ending positive RT | ~99.7% | ≥ 98% (hold) |
| Maker share on fills | 90.7% | ≥ 93–95% |
| Taker-ending RT share | ~19% | ≤ 8–10% |
| RT velocity | ~0.105 | ≥ 0.120–0.130 |
| Median exit wait | 36.03 s | < 30 s |
| p90 exit wait | 93.41 s | < 70–80 s |
| p95 latency | ~106 ms | < 100–110 ms |
| Ownership / exposure violations | 0 | 0 (hold) |

Note the shape of that table: only two rows are throughput. The rest are
downside and realization quality. That ordering is not a preference, it follows
from the score. Kappa-3 divides by the cube root of the third lower partial
moment, so a single −0.60 round trip contributes 0.216 of cubic downside while
three −0.20 round trips contribute 0.024 — 9× less, for identical total loss.
Adding volume before the tail is controlled raises the numerator and the
denominator together.

This is why slot expansion (A1.8) and size increases stay closed, and why
throughput is A2.0 rather than A1.9: **the Taker tail must collapse first, and
the tail collapses by making Maker exits realize, not by trading more.**

### Deferred: enforcing the invariant against the ledger

The ledger is an accurate live-order view and could back
`_direct_book_has_live_order`, closing the 9.8% same-side overlap. That is a
**behaviour change and a safety improvement**, so it belongs in neither A nor A2.
It is recorded here as a candidate for its own phase after B, and must not be
bundled into an economics experiment.

## Verification

- All direct regression suites: **347 passed, 11 skipped, 0 failed**
  (A1.9.0.2 was 320 passed — the delta is exactly the 27 new A1.9.0.3 tests;
  no pre-existing test changed status)
- A1.9.0.3 suite: **27 passed**, mutation-tested against all three fixes
- A1.9.0.2 suite: **25 passed**, including behavioural coverage that binds the
  extracted observer to a stub exchange and replays a full exit lifecycle
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
