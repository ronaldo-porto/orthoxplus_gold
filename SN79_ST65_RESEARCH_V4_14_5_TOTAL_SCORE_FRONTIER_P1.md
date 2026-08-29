# SN79 St6.5 Research V4.14.5 — TOTAL_SCORE_FRONTIER P1

**Release date:** 2026-08-30  
**Research policy:** `total_score_frontier_v4_14_5`  
**Engine revision:** `lean_engine_p1_total_score_frontier_v4_14_5`  
**Baseline:** `SN79 St6.5 Research V4.14.4 — RealNet Defect Fix P1`  
**Scope:** Research Agent only. BaseStrategy and AdaptiveAgent remain frozen.  
**Primary objective:** maximize and sustain **TOTAL validator/network score**, not raw score, Kappa density, order count, or gross PnL in isolation.

---

## 1. Executive summary

V4.14.5 is a scheduler-authority correction.

The previous V4.14.x lineage successfully improved economic execution, inventory safety, Kappa completion, and RealNet defect handling, but it accumulated several score-scheduling concepts at the same time:

- ONE_AWAY / TWO_AWAY completion,
- 6/12/50 qualified-density targets,
- CORE continuation,
- RECYCLING continuation,
- CORE_PROBE,
- Wide-Kappa coverage,
- sticky cohort behavior,
- score-EV bonuses,
- expiry refresh,
- and dynamic lane-budget rewrites.

Each concept had a valid historical reason. Together, however, they created a structural problem: **already-qualified or repeatedly selected books could continue consuming scarce COMPLETION capacity while too few new books reached the validator's minimum three realized observations.**

The live symptom was exactly what was observed on Testnet/RealNet:

> The agent trades, fills, and realizes PnL, but too few distinct books participate enough to become Kappa-eligible. Kappa density stays low, the median remains dominated by inactive-book zeros, and TOTAL score stays at or near zero.

V4.14.5 does **not** add another optimization layer on top of those old schedulers. It establishes **one live score-scheduling authority**:

`TOTAL_SCORE_FRONTIER`

Risk/exit authority remains separate and higher priority. Economic hard gates remain separate. But for flat-book score acquisition, **one authority decides whether a book deserves scarce completion capacity**.

The strategy has only three score phases:

1. `IGNITION` — fewer than 41 Kappa-eligible books.
2. `SURVIVAL` — 41 through 79 Kappa-eligible books.
3. `FRONTIER` — 80 or more Kappa-eligible books.

The central behavior is:

- Before 41: aggressively create high-quality new 3-observation books.
- From 41 to 79: continue breadth, but also repair the current lower-tail Kappa median frontier.
- At 80+: stop blanket breadth/density pressure; trade qualified books only when they are score-frontier relevant, expiry-critical, inventory/risk required, or economically attractive.

The competitive deployment objective is **20+ TOTAL score with 25+ as the top-agent target**, because the user-observed top agents are above 25 on the dashboard scale. This is a runtime KPI, **not a guaranteed unit-test output**: the final network score is peer-relative and passes through reward-floor, Pareto and EMA stages outside the miner's direct control.

---

## 2. What “TOTAL score” means in this project

A learner must distinguish the following terms. Confusing them leads directly to bad strategy design.

### 2.1 Raw per-book Kappa

The validator computes realized Kappa-3 per book using realized PnL observations over the configured rolling window.

Current upstream defaults audited for this release:

- Kappa lookback: **3 simulation hours**.
- Minimum Kappa history span: **1.5 simulation hours**.
- Minimum non-zero realized observations per book: **3**.
- Kappa normalization range: **[-2.5, +2.5] -> [0, 1]**.
- Kappa threshold `tau`: **0.0**.

The Kappa implementation uses a downside lower-partial-moment formulation with regularization. Losses matter disproportionately because downside enters cubically.

### 2.2 Kappa aggregate score

Per-book normalized Kappa values are aggregated by the validator using:

1. inactivity handling,
2. median across the scoring set,
3. left-tail IQR outlier penalty.

Current inactivity allowance is **37.5%**.

For 128 books:

`floor(128 * 0.375) = 48`

Therefore the validator may ignore up to 48 no-Kappa books. If more than 48 books are inactive, each excess inactive book enters the scoring vector as `0.0`.

This makes **80 Kappa-eligible books** the full-breadth boundary:

`128 - 48 = 80`

### 2.3 PnL score

The legacy trading-score path also computes a realized-PnL component.

Current weights:

- Kappa score: **0.79**.
- PnL score: **0.21**.

So the miner must not trade bad-EV books merely to increase breadth. A book that becomes Kappa-eligible through poor exits may hurt both Kappa quality and PnL.

### 2.4 Trading score

Conceptually:

`trading_score = 0.79 * kappa_score + 0.21 * pnl_score`

This is still **not the final TOTAL/network score**.

### 2.5 Track-record EMA

The validator smooths trading score with a pre-reward track-record EMA. This is intended to reward sustained multi-window quality rather than one lucky scoring window.

### 2.6 Peer-relative soft reward floor

The current validator enables a soft floor based on the distribution of positive miner trading scores:

- percentile: **50th percentile / median positive miner**,
- softness: **0.5**.

A miner can therefore have a positive local/trading score while still receiving zero or heavily tapered reward if it is too weak relative to peers.

### 2.7 Pareto reward allocation

Post-floor trading rewards are passed through rank-sensitive Pareto allocation. Current default Pareto shape audited for this release is **1.42**.

### 2.8 Slow final validator EMA

Post-Pareto trading reward is then accumulated into the validator score with:

`moving_average_alpha = 0.008298755`

This means dashboard/network score rises slowly even after the strategy becomes competitive.

### 2.9 Consequence for strategy design

The correct optimization target is therefore **not**:

- “make raw Kappa positive”,
- “trade all 128 books”,
- “maximize RT volume”,
- “get 80 books at any cost”,
- “maximize one profitable CORE book”,
- or “maximize PnL today”.

The target is:

> Build enough **high-quality breadth** to survive the inactivity/median structure and peer floor, then continuously allocate execution capacity to the books with the largest marginal contribution to sustainable TOTAL score, without violating economic/risk constraints.

---

## 3. Why 41 and 80 are structural boundaries

When fewer than 80 books have valid Kappa, the validator's effective Kappa scoring vector contains:

`qualified_books + inactivity_zeros = 80 entries`

Let `q` be the number of valid/Kappa-eligible books while `q < 80`.

Then:

`zeros = 80 - q`

The median positions of an 80-element sorted vector are positions 39 and 40 in zero-based indexing.

### 3.1 Before 40

At `q <= 39`, enough zeros remain to occupy both median positions.

Result: aggregate Kappa median is structurally `0` before outlier handling.

### 3.2 At 40

At `q = 40` there are 40 zeros and 40 valid values.

The median is halfway between zero and the weakest valid book.

This is partial ignition, but still fragile.

### 3.3 At 41

At `q = 41` there are 39 zeros and 41 valid values.

Both median positions now land inside the valid-book population.

The current scoring pivot becomes the **1st and 2nd weakest valid books**.

This is why V4.14.5 defines:

`IGNITION: q < 41`

and transitions to `SURVIVAL` at 41.

### 3.4 Pivot movement from 41 to 80

For `41 <= q < 80`, the valid-book indices controlling the median are approximately:

`pivot_low  = q - 41`

`pivot_high = q - 40`

Examples:

| Kappa-eligible books | Valid-book median pivot, 0-based from weakest |
|---:|---:|
| 41 | 0 / 1 |
| 50 | 9 / 10 |
| 59 | 18 / 19 |
| 70 | 29 / 30 |
| 79 | 38 / 39 |
| 80 | 39 / 40 |
| 81 | 40 / 40 |

This is why “always optimize rank 40” is wrong before 80. The relevant frontier changes with `q`.

### 3.5 At 80 and above

At 80, inactivity zeros disappear.

For `q >= 80`, **all valid books participate** in the Kappa median. There is no fixed “best 80” exclusion of weak valid books.

Therefore after 80, blindly qualifying weak additional books can reduce the aggregate median or worsen the outlier structure.

This is why V4.14.5 sharply reduces fresh-breadth score value in `FRONTIER`.

---

## 4. Root cause in the V4.14.4 scheduler lineage

V4.14.4 fixed two important RealNet defects, but intentionally did not redesign the broader score scheduler.

The old scheduler lineage still contained concepts such as:

- phase density targets of 6 / 12 / 50 observations,
- CORE continuation,
- RECYCLING continuation,
- CORE_PROBE bootstrap,
- density-due completion,
- ONE_AWAY/TWO_AWAY completion,
- dynamic density-priority budget rewriting.

### 4.1 Already-qualified books were not really “done”

A book becomes Kappa-eligible at three observations.

But historical density logic could continue to treat the same book as score work until it reached 6, 12 or even 50 observations depending on phase.

That can be economically sensible for a strong book, but it is not automatically the best use of **score-acquisition capacity** while the miner has only a small population of Kappa-eligible books.

### 4.2 Completion pressure could starve coverage

Historically, when actionable completion/density work existed, the lane budget could be rewritten toward completion—for example, effectively reducing fresh COVERAGE from four slots to one.

If a small set of existing books kept presenting valid completion/CORE/density work, fresh-book participation grew too slowly.

The resulting feedback loop was:

`few qualified books`

`-> old qualified books keep completion priority`

`-> fresh coverage remains narrow`

`-> few new books reach OBS1/OBS2/OBS3`

`-> Kappa breadth remains low`

`-> total score remains weak/zero`

### 4.3 V4.14.4 retry quarantine did not solve this class

V4.14.4 correctly rotates hard-impossible candidates such as `TOXIC`, `NEGATIVE_EV`, and `AVOID` before lane allocation.

That fixes **impossible-candidate starvation**.

It does not fix **legitimate-but-low-marginal-score qualified-book monopolization**.

V4.14.5 addresses the second problem.

---

## 5. V4.14.5 design rule: one score scheduler authority

The most important architectural rule is:

> On live V4.14.5 flat-book rows, only `TOTAL_SCORE_FRONTIER` may create score-driven COMPLETION authority.

Old historical fields remain in data structures for test compatibility, diagnostics, and archived tooling, but they are forcibly non-authoritative on live rows:

- `density_due = False`
- `core_candidate = False`
- `recycling_candidate = False`
- `core_probe_candidate = False`

The new live fields are:

- `total_score_phase`
- `total_score_due`
- `total_score_value`
- `total_score_reason`

The lane classifier checks these live fields first.

### 5.1 Authority hierarchy

The high-level authority chain is intentionally small:

1. **Hard risk / catastrophic safety**
2. **Inventory / dust realization and V4.14.4 exit authority**
3. **TOTAL_SCORE_FRONTIER for flat-book score scheduling**
4. **ordinary economic coverage/opportunity**

Risk is allowed to override score scheduling because risk and score solve different problems.

Inside score scheduling, there is one owner.

### 5.2 Economic hard gates remain hard

`TOTAL_SCORE_FRONTIER` is not permission to manufacture score through bad trades.

Existing hard gates remain authoritative, including:

- `NEGATIVE_EV`,
- `TOXIC`,
- `AVOID`,
- entry feasibility,
- volume cap,
- inventory caps,
- total-position caps,
- aggregate BASE exposure cap,
- contract safety,
- lifecycle EV,
- V4.14.4 scheduler retry quarantine.

A high `total_score_value` cannot override a known economically invalid trade.

---

## 6. Three phases only

### 6.1 IGNITION — `< 41` Kappa-eligible books

Purpose:

> Move the miner from structurally zero/fragile Kappa breadth to a real valid-book median as quickly as economics safely allow.

Fixed lane budget:

- COVERAGE = **4**
- COMPLETION = **3**
- REALIZATION = **3**
- shared overflow = **1**

Key invariant:

> Completion demand can no longer dynamically collapse fresh COVERAGE from four slots to one.

Priority behavior:

1. Existing inventory/risk exits first.
2. Healthy ONE_AWAY unqualified books.
3. Healthy TWO_AWAY unqualified books.
4. Fresh healthy coverage.
5. Already-qualified books only through normal economic opportunity, not artificial density pressure.

Qualified median-frontier repair is intentionally not activated during IGNITION. Before 41, the highest structural value comes from getting more good books over the qualification boundary.

### 6.2 SURVIVAL — `41..79` Kappa-eligible books

Purpose:

> Strengthen the live aggregate score sufficiently to survive the peer-relative floor while continuing to replace inactivity zeros.

Fixed lane budget:

- COVERAGE = **2**
- COMPLETION = **5**
- REALIZATION = **3**
- shared overflow = **1**

The strategy now has two legitimate score-acquisition jobs:

1. qualify good incomplete books;
2. repair qualified books around the current Kappa-median pivot.

Strong qualified books far above the current pivot do **not** receive artificial completion pressure merely because they are historically CORE/productive.

### 6.3 FRONTIER — `>= 80` Kappa-eligible books

Purpose:

> Stop paying for breadth that no longer removes inactivity zeros and concentrate on median/frontier quality, expiry defense, realized PnL, and strong economic opportunities.

Fixed lane budget:

- COVERAGE = **2**
- COMPLETION = **4**
- REALIZATION = **3**
- shared overflow = **1**

Fresh books are still allowed, because the market changes and better books can appear, but their score pressure becomes very small.

There is no “keep increasing density to 50 observations” authority.

---

## 7. Per-book `total_score_value`

`total_score_value` is a **scheduler priority unit**, not a validator score point and not a prediction that the final dashboard score will increase by that amount.

The values are deliberately simple and deterministic.

### 7.1 ONE_AWAY

Condition:

- unqualified,
- exactly one observation remaining to the three-observation requirement,
- economics valid,
- not known INEFFICIENT.

Value:

`1.00`

Reason:

`QUALIFY_ONE_AWAY`

This is the highest normal flat-book score-acquisition value because one additional good RT can convert the book into a Kappa-eligible book immediately.

### 7.2 TWO_AWAY

Condition:

- unqualified,
- exactly two observations remaining,
- economics valid,
- not known INEFFICIENT.

Value:

`0.72`

Reason:

`QUALIFY_TWO_AWAY`

### 7.3 Fresh coverage

Fresh books remain in COVERAGE, not COMPLETION.

Phase-sensitive values:

- IGNITION: `0.55`
- SURVIVAL: `0.38`
- FRONTIER: `0.06`

Reason:

`FRESH_COVERAGE`

This expresses the correct marginal value:

- fresh discovery is very important when breadth is scarce;
- still useful during survival;
- not automatically useful once inactivity zeros are gone.

### 7.4 Critical expiry defense

A qualified book receives explicit score pressure when refresh is both required **and deadline-critical**.

Value:

`0.96`

Reason:

`EXPIRY_DEFENSE`

Important conflict fix:

> A non-critical legacy `needs_refresh=True` can no longer bypass the V4.14.5 authority and enter COMPLETION by itself.

### 7.5 Median-frontier repair

In SURVIVAL and FRONTIER, only a narrow band around the exact current lower-tail median pivot receives explicit qualified-book score pressure.

Typical value range:

approximately `0.48 .. 0.78`

Reason:

`MEDIAN_FRONTIER`

Value increases with:

- proximity to the current pivot,
- weakness of the current quality proxy,
- while remaining below healthy ONE_AWAY priority.

### 7.6 Qualified economic-only books

Qualified books outside the score frontier remain tradable through ordinary economic logic.

They receive no artificial densification authority.

Nominal score values:

- pre-frontier: `0.04`
- FRONTIER: `0.20`

Reason:

`ECONOMIC_ONLY`

These values help ordinary ranking but do not automatically convert the book into a COMPLETION candidate.

---

## 8. Quality proxy and frontier selection

V4.14.5 intentionally does **not** introduce a second large shadow-scoring model into live scheduling.

The frontier module uses a small quality proxy:

1. normalized current raw Kappa,
2. small recent realized-PnL adjustment,
3. conservative recent loss-rate deduction.

This proxy is only used to sort already-qualified books and identify the current median frontier.

It is not allowed to bypass economic gates.

Why this design is intentionally simple:

- exact validator shadowing would add another state/model authority;
- miner-side realized-history timing does not exactly match validator history;
- peer-relative floor information is not in the normal strategy request;
- a complex counterfactual score engine could add latency and another source of model drift.

P1 therefore uses the validator math only to identify the **structural boundaries and pivot**, while the live scheduler remains deterministic and lightweight.

A validator-exact shadow may be added later as **diagnostic telemetry only** after parity is independently proven.

---

## 9. INEFFICIENT / slow-progress rotation

V4.14.4 already handles hard rejects:

- `NEGATIVE_EV`
- `TOXIC`
- `AVOID`

with cross-tick scheduler quarantine.

V4.14.5 additionally treats productivity tier `INEFFICIENT` as a reason not to spend scarce score-completion capacity.

For an unqualified INEFFICIENT book:

- ONE_AWAY/TWO_AWAY `total_score_due` is turned off;
- its score value is reduced sharply;
- the book rotates back toward lower-priority coverage/economic handling rather than monopolizing completion.

For an already-qualified INEFFICIENT book:

- frontier/economic score pressure is sharply reduced;
- it cannot regain completion authority through old CORE flags.

This is deliberately simpler than adding another independent slow-fill state machine in P1. Existing productivity evidence is reused as an efficiency signal, not as a scheduling authority.

---

## 10. Hidden old-authority conflicts explicitly removed

A scheduler rewrite is incomplete if old behavior still has privileges downstream of lane allocation.

V4.14.5 therefore removes the following hidden conflicts.

### 10.1 Legacy flags cannot create live completion

For live V4.14.5 rows:

- CORE cannot create COMPLETION;
- RECYCLING cannot create COMPLETION;
- CORE_PROBE cannot create COMPLETION;
- `density_due` cannot create COMPLETION;
- observation count alone cannot create COMPLETION;
- non-critical `needs_refresh` cannot create COMPLETION.

Only:

`total_score_due == True`

can create flat-book score completion, subject to economics.

### 10.2 Legacy special selection disabled on live rows

The old V4.13.6 selection routine could reserve special completion slots for:

- positive ONE_AWAY,
- productive CORE/RECYCLING,
- CORE_PROBE,
- flywheel CORE.

That special path is preserved only for unannotated historical/test callers.

If live V4.14.5 annotations exist, it is skipped.

### 10.3 Dynamic 1/6 density budget rewrite removed from live scheduler

Historical `density_priority_budgets()` remains as compatibility code, but the live V4.14.5 lane budget no longer calls it.

This prevents the old behavior where one completion/density candidate could shrink fresh coverage to one slot during bootstrap.

### 10.4 Exact-min admission privilege follows new authority

Historically, productivity CORE/RECYCLING membership could receive a special exact-minimum-order admission path.

That would constitute hidden scheduling authority even if the lane allocator had changed.

V4.14.5 now builds:

`_research_total_score_due_ids`

from the annotated live rows and uses that set for the downstream `productive_qualified_core` privilege.

Old productivity CORE membership is telemetry only.

### 10.5 Qualified stale-TTL privilege follows new authority

Likewise, the qualified-CORE stale TTL helper no longer receives authority from historical productivity CORE/RECYCLING membership.

It receives `productive_qualified_core=True` only when the book is in the live total-score due set.

### 10.6 Historical 6/12/50 density preflight removed

The production Research launcher no longer preflights the historical 6/12/50 density-target scheduling contract as if it were active behavior.

The old helper code remains available for archived tests/telemetry, but deployment preflight validates V4.14.5 instead.

---

## 11. Mechanical gates retained without becoming score authorities

Not every old function is a competing strategy authority.

V4.14.5 retains several mechanical safety/capacity gates where they solve a separate invariant.

### 11.1 Breadth rotation gate

This gate may suppress a qualified book from **new acquisition** when incomplete productive work is waiting, but on live rows it consults `total_score_due`.

Therefore it cannot suppress a current V4.14.5 frontier/expiry book that the sole score authority has marked due.

It is a downstream enforcement gate, not an independent scorer.

### 11.2 Kappa conversion pressure / total-headroom gate

This gate protects real total-position headroom and preserves exploration when exposure capacity is tight.

It does not treat parked labels as an independent scarce resource.

This remains a capacity/risk mechanism rather than a score model.

### 11.3 Hard position/exposure caps

Still authoritative:

- active open-book cap,
- total open-book cap,
- aggregate absolute BASE cap.

No score value may bypass them.

---

## 12. V4.14.4 RealNet safety remains frozen

V4.14.5 is a score-scheduler change. It must not reopen the two RealNet defects fixed in V4.14.4.

### 12.1 Exit authority retained

Non-catastrophic bounded-loss corridor remains:

- above `-8 bps`: normal unified-exit economics;
- `-8 .. -18 bps`: soft bounded-loss region with bounded profitable-Maker veto;
- `-18 .. -25 bps`: hard escape authority;
- worse than `-25 bps`: park rather than uncontrolled forced crossing.

Catastrophic hard-risk remains separate.

### 12.2 Retry quarantine retained

Hard-rejected flat candidates still enter cross-tick quarantine before lane allocation.

Inventory, dust and hard-risk exits are not quarantined.

Material market/EV fingerprint change can reopen a candidate.

Successful quote clears backoff.

Session/simulation transition clears retry state.

---

## 13. Runtime behavior expected by phase

### 13.1 Early run / IGNITION

Expected log/behavior pattern:

- many distinct healthy books receive Maker coverage;
- ONE_AWAY and TWO_AWAY books consume completion slots;
- qualified strong books stop repeatedly consuming artificial density slots;
- fresh coverage reserve remains visible even when incomplete backlog exists;
- `kappa_eligible` should rise materially faster than under V4.14.4.

The purpose is **not** to maximize immediate realized PnL on the few strongest books. The purpose is to create enough economically healthy score contributors to leave the zero-score regime.

### 13.2 After 41 / SURVIVAL

Expected pattern:

- TOTAL score precursor should become structurally meaningful if lower-tail Kappa quality is acceptable;
- incomplete books continue to receive strong completion priority;
- weak qualified books near the current median pivot receive selective repair;
- far-above-median CORE books no longer have artificial scheduling dominance;
- fresh-book acquisition continues, but completion becomes more important.

### 13.3 At 80+ / FRONTIER

Expected pattern:

- blanket breadth pressure collapses;
- fresh books remain possible but are not automatically score-important;
- median-frontier repair, critical expiry defense and normal positive-EV opportunities dominate;
- a weak 81st/82nd book is not deliberately forced into Kappa eligibility merely for a density count.

---

## 14. Telemetry for a learner/operator

The main score-progress telemetry now includes V4.14.5 fields.

Important fields:

- `total_score_phase`
- `kappa_eligible`
- `score_qualified`
- `one_away`
- `two_away`
- `total_score_frontier_books`
- `total_score_pivot_low`
- `total_score_pivot_high`
- `total_score_inefficient_rotated`
- `score_target`
- `score_deficit`
- `productive_incomplete`
- `qualified_suppressed`
- `kappa_pressure_reason`
- `kappa_pressure_suppressed`
- current lane demand/grants

Historical fields such as productivity phase/core counts and flywheel phase remain for diagnosis, but **must not be interpreted as live scheduling authority**.

### 14.1 Healthy IGNITION signature

A healthy early run should show:

- `total_score_phase=IGNITION`
- `kappa_eligible` steadily increasing
- ONE_AWAY/TWO_AWAY pipeline replenishing
- COVERAGE demand being granted repeatedly
- old `productivity_core_books` may be non-zero in telemetry, but those books should not steal COMPLETION unless also `total_score_due`
- hard-rejected books rotating via V4.14.4 retry telemetry

### 14.2 Warning signs

Investigate immediately if:

- `kappa_eligible` is flat for a long period while the agent is trading;
- only the same few book IDs appear in fills/RTs;
- live COMPLETION contains books whose `total_score_due=0`;
- fresh COVERAGE repeatedly has demand but receives no effective slots during IGNITION;
- an INEFFICIENT book repeatedly receives completion grants;
- qualified non-frontier books continue receiving exact-min or stale-TTL privileges;
- RealNet bounded-loss authority regresses;
- total score rises briefly then persistently decays.

---

## 15. What V4.14.5 deliberately does NOT do

To keep the engine understandable and prevent authority conflicts, P1 deliberately does not add:

- a live exact clone of validator scoring;
- a peer-score HTTP query in `respond()`;
- a new ML score predictor;
- a separate WAVE_CROSS controller;
- a fourth/fifth/sixth phase;
- a second slow-fill scheduler state machine;
- forced 88/96-book breadth targets;
- 6/12/50 qualified density objectives;
- total-score overrides of negative EV or risk controls.

These ideas may be researched later only if runtime evidence demonstrates a specific unresolved bottleneck.

---

## 16. Why the local score proxy is not treated as exact validator truth

The upstream validator inserts an empty realized-PnL bucket on every miner update timestamp, even when no realized PnL occurs.

The strategy's historical local realized-PnL ledger is sparser.

Therefore a miner-local Kappa reconstruction can overestimate/alter the shape of the validator's actual Kappa series if it treats only realized-PnL timestamps as the time axis.

For V4.14.5:

- structural validator constants/boundaries are used;
- exact local Kappa shadow is **not** allowed to become a new live authority;
- actual Testnet/RealNet score remains the final truth.

If a future version adds a validator shadow, it must first prove parity for:

- empty timestamps,
- 1.5 h minimum history,
- 3 h lookback,
- simulation crossover,
- three-observation qualification,
- MAD normalization,
- downside LPM3 regularization,
- inactivity zeros,
- NumPy percentile/IQR behavior,
- PnL score,
- and aggregate score.

Until then, shadow output is diagnostic only.

---

## 17. Expected TOTAL-score objective

The user's observed top-agent dashboard TOTAL score is above **25**.

V4.14.5 is therefore not considered successful merely because raw/trading score becomes positive.

Runtime milestones should be interpreted approximately as:

- **0 -> positive:** score ignition / floor survival begins.
- **5+ TOTAL:** breadth fix is producing visible network value.
- **10+ TOTAL:** materially competitive relative behavior.
- **20+ TOTAL:** strong deployment target.
- **25+ TOTAL:** top-agent competitive objective.

These are **deployment targets, not guarantees**. TOTAL score depends on:

- our own Kappa/PnL quality,
- the peer score distribution,
- soft-floor position,
- Pareto rank,
- duration of sustained reward,
- validator moving-average history,
- market state and fill conditions.

The code should therefore be judged by sustained trajectory, not a single score sample.

---

## 18. Promotion criteria

Do not promote V4.14.5 to BaseStrategy/AdaptiveAgent merely because unit tests pass.

### Gate A — structural runtime correctness

Require logs to prove:

1. policy is `total_score_frontier_v4_14_5`;
2. phase transitions occur at 41 and 80 Kappa-eligible books;
3. IGNITION keeps its four-slot coverage reserve when demand exists;
4. qualified non-frontier/non-critical books do not enter COMPLETION through old flags;
5. old CORE/RECYCLING/CORE_PROBE sets are telemetry only;
6. non-critical legacy refresh cannot bypass new authority;
7. exact-min and stale-TTL qualified privileges follow `total_score_due`;
8. V4.14.4 RealNet exit and retry behavior remains correct.

### Gate B — participation improvement

Compared with V4.14.4 over comparable time:

- distinct books with fills must rise;
- distinct books with RTs must rise;
- OBS1/OBS2 population must be broad enough to sustain conversion;
- Kappa-eligible count must rise materially faster;
- repeated concentration on a handful of books must fall.

### Gate C — economic quality

Breadth improvement is invalid if it is bought with poor economics.

Require:

- no material deterioration in average negative RT loss;
- bounded-loss tail remains controlled;
- realized PnL remains acceptable/positive over the evaluation horizon;
- Maker economics are not destroyed;
- Taker remains selective and economically/risk justified;
- no rise in uncontrolled toxic fills.

### Gate D — TOTAL score

The decisive promotion gate:

- TOTAL score must become **strictly positive**;
- it must continue rising rather than revert to zero;
- target **5+** as first meaningful proof;
- target **10+** as stronger validation;
- strategy architecture is intended for **20+**, with **25+** competitive objective.

A version that improves Kappa density but remains structurally capped around weak TOTAL score is **not** a successful promotion candidate.

---

## 19. Rollback conditions

Rollback to V4.14.4 if any of these appear:

1. V4.14.5 reduces distinct-book conversion despite higher order count.
2. COMPLETION is again dominated by already-qualified strong books.
3. fresh COVERAGE is starved during IGNITION.
4. median-frontier repair creates repeated negative-EV trading.
5. TOTAL score stays at zero despite materially higher Kappa-eligible count and sufficient validator warmup.
6. loss tails worsen materially.
7. V4.14.4 exit-authority behavior regresses.
8. TOXIC/NEGATIVE_EV retry starvation reappears.
9. latency materially worsens from the scheduler change.

Rollback should not reintroduce the removed overlapping score authorities into V4.14.5. Diagnose the failing component first.

---

## 20. Active files changed in V4.14.5

Primary behavioral files:

- `agents/strategy/Strategy1_Research.py`
- `agents/strategy/research_execution_lanes.py`
- `agents/strategy/research_total_score_frontier.py` **(new)**
- `run_strategy1_research_test_multi.sh`

Validation:

- `tests/test_research_v4_14_5_total_score_frontier.py` **(new)**
- historical release-contract tests updated only where the authoritative Research policy identity changed, plus the four launcher contract tests that pinned the literal `research_candidate_count=10` (see §21.1):
  - `tests/test_research_v4_10_score_up.py`
  - `tests/test_research_v4_11_performance.py`
  - `tests/test_research_v4_12_11_one_away_completion.py`
  - `tests/test_research_v4_12_performance_core.py`

Documentation/release metadata:

- `SN79_ST65_RESEARCH_V4_14_5_TOTAL_SCORE_FRONTIER_P1.md`
- `agents/strategy/AGENT_VERSION_MANIFEST.md`
- `V4_14_5_FULL_RELEASE_MANIFEST.json`
- `V4_14_5_RESEARCH_SHA256SUMS.txt`

BaseStrategy and AdaptiveAgent code are not promoted/modified by this Research release.

---

## 21. Verification status

Final dedicated active Research regression after the single-authority conflict pass and the §21.1 defect-fix pass:

**524 PASS / 1 SKIP (525 collected)**

Additional gates:

- Python compilation: PASS
- active strategy tree `compileall`: PASS
- Research launcher `bash -n`: PASS
- Research launcher preflight: PASS
- V4.14.5 API preflight: PASS
- V4.14.4 RealNet safety preflight: PASS

The dedicated active Research suite remains the release acceptance signal. Repository-wide pytest is not used as the gate because the inherited project includes optional validator/GenTRX dependencies and archived version tests that are outside this Research-runtime release surface.

### 21.1 Defect-fix pass

A post-authoring audit of the shipped tree against this document found six defects. All six are fixed; each has a dedicated regression test in `tests/test_research_v4_14_5_total_score_frontier.py`.

**1. Gate A item 3 did not hold as shipped — IGNITION lost a COVERAGE slot.**

IGNITION reserves 4+3+3 = 10 slots plus 1 shared overflow, so `total_cap` is 11. The launcher shipped `research_candidate_count=10`, and that global cap is applied *after* overflow has already been spilled into REALIZATION/COMPLETION, in the order REALIZATION -> COMPLETION -> COVERAGE. Whenever the overflow slot was consumed upstream, COVERAGE was truncated from 4 to 3.

This is a milder form of the exact 4-to-1 collapse V4.14.5 exists to remove, and it contradicted the promotion gate. Two changes:

- the screen now raises the effective cap to `budgets.total_cap` so a low cap can never truncate a *reserved* lane slot;
- the launcher moves to `research_candidate_count=11`, which covers the `total_cap` of all three phases (11 / 11 / 10), so code and config agree.

The launcher's own V4.14.5 preflight now asserts the invariant under a realistic stress row set.

**2. The enable flag was half-wired and §19 rollback was not config-reversible.**

`apply_total_score_frontier` was called unconditionally; `research_total_score_frontier_enabled` only switched the lane budgets. Setting it to `0` therefore produced a hybrid — the new lane authority running against the old 3/5/3/1 slot plan — which is neither V4.14.4 nor V4.14.5 and was never tested. The flag now gates the annotation itself, so a disabled authority leaves `total_score_phase` empty and `classify_execution_lane` takes its legacy path coherently. Telemetry reports `total_score_phase=DISABLED` instead of masquerading as `IGNITION`.

Note that a *full* V4.14.4 restore is still a code revert, not a flag: the legacy CORE/RECYCLING/CORE_PROBE/density producers were removed from the live path by this release.

**3. Entry feasibility was invisible to the score authority.**

`econ_ok` considered only `economics_ok` and `completion_ev_ok`. A candidate already rejected by the V4.14.4 retry quarantine or by minimum-order sizing could still become `total_score_due`, and §10.4/§10.5 had just made that set the gatekeeper for the exact-min admission and qualified stale-TTL privileges. Lane selection filters infeasible flat books out, so no slot was wasted, but the two downstream privileges had lost the feasibility check the old productivity-CORE set carried incidentally. `entry_feasible` is now part of the hard-gate conjunction, consistent with §5.2.

**4. Stranded docstring in `completion_sort_key`.**

The V4.14.4 docstring became a no-op string expression after the new early return, making the legacy branch read like a separate function. Converted to a comment that says when the branch is reachable.

**5. `total_score_inefficient_rotated` over-counted.**

Inventory, dust and hard-risk rows take the early non-scheduled branch but were still counted as score rotations, which made the §14.2 operator warning signal unusable. The counter now excludes realization rows.

**6. The new test module could not be collected without `PYTHONPATH`.**

It imported `research_execution_lanes` with no `sys.path` bootstrap, unlike its V4.14.4-era siblings. Bootstrap added. Note this only fixes the new module; roughly half the historical research test files still rely on `PYTHONPATH=agents/strategy`, which remains the documented suite invocation.

---

## 22. Source references used for the design audit

The V4.14.5 design was checked against the current public SN79 upstream code available during release preparation (latest repository state audited around commit `f84fea099ecb51953a2e144940518cf363984c04`, 2026-08-28).

Relevant upstream files:

- `taos/im/config/__init__.py` — scoring/reward defaults.
- `taos/im/utils/kappa.py` — Kappa-3 calculation.
- `taos/im/validator/reward.py` — Kappa aggregation, inactive-book zeros, IQR penalty, PnL score, trading score, track-record EMA, reward floor and Pareto distribution.
- `taos/im/validator/trade.py` — FIFO realized PnL and empty PnL timestamp insertion.
- `taos/common/config/__init__.py` — slow final validator moving-average alpha.
- `taos/common/neurons/validator.py` — application of the post-Pareto moving average to validator score.

Always re-audit upstream scoring before a future promotion if SN79 validator code changes.

---

## 23. Learner mental model

A new learner should remember this compact model:

**First:** protect capital and inventory.  
**Second:** do not waste slots on impossible books.  
**Third:** while below 41, turn many good books into three-observation books.  
**Fourth:** from 41 to 79, keep converting breadth while repairing the current lower-tail median frontier.  
**Fifth:** at 80+, stop treating more breadth as automatically good; optimize frontier quality and real economics.  
**Always:** one live score-scheduler authority, hard economic gates, no hidden legacy privileges.

The objective is not “trade more.”

The objective is:

> **Use each scarce execution slot where it has the highest expected contribution to sustainable TOTAL validator score.**
