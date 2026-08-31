# SN79 St6.5 Research V4.15.1 — Lean Authority Cleanup P2

## 1. Purpose

V4.15.1 is a **behavior-preserving codebase cleanup** of V4.15.0. It does not introduce another trading strategy layer. Its purpose is to make the current five-authority Research engine easier to reason about, test, and optimize without reintroducing historical scheduler conflicts.

The target workflow remains:

```text
HARD SAFETY
    ↓
EXECUTABILITY
    ↓
LIFECYCLE EV
    ↓
TOTAL SCORE VALUE
    ↓
EXECUTION / EXIT
```

The competitive objective remains sustainable **TOTAL validator score**, with runtime validation required before any claim about score level.

---

## 2. Why this cleanup was necessary

V4.15.0 fixed several authority problems, but the source tree still carried historical implementations from V4.11–V4.14. This created three risks:

1. **False authority:** old fields/functions could look active even when TOTAL_SCORE was supposed to own scheduling.
2. **Maintenance risk:** a future patch could accidentally reconnect a legacy branch and silently change behavior.
3. **Learner confusion:** engineers had to understand CORE, RECYCLING, cohort, density, flywheel, productivity, score-qualified overlays, and the new TOTAL_SCORE engine at the same time.

V4.15.1 removes those obsolete concepts from the live Research control plane instead of setting them to false.

---

## 3. The current five-authority engine

### 3.1 HARD SAFETY

Hard Safety answers only: **Is this action legally/safely admissible?**

Preserved systems include:

- session/simulation transition protection;
- hard inventory and aggregate BASE limits;
- active/total open-book limits;
- venue volume cap;
- dust prevention and compaction safety;
- minimum-order hard safety;
- balance and contract/post-only protection;
- catastrophic risk;
- V4.14.4 bounded-loss final RealNet exit authority.

Hard Safety is intentionally independent of score optimization. It may veto a high-score-value action.

### 3.2 EXECUTABILITY

Executability answers: **Can this book produce a usable order now?**

V4.15 retains short memory for recent downstream mechanical failures:

- `TTL_STALE`;
- `ZERO_ORDER_SIZE`;
- `LOW_FILL_PROBABILITY`;
- `NON_POSITIVE_EDGE`;
- `NEGATIVE_EXPECTED_PNL`;
- `ADVERSE_SELECTION`.

These cooldowns are distinct from V4.14.4 hard scheduler quarantine for `TOXIC` / `NEGATIVE_EV`. The two mechanisms must not be merged: one handles transient mechanical unexecutability; the other handles hard economic/toxicity failures.

### 3.3 LIFECYCLE EV

Lifecycle EV prices the **complete expected round trip**, not only the Maker entry. V4.15's empirical Maker/Taker realization posterior remains active. The configured `research_lifecycle_taker_exit_prob=0.30` is a prior; observed exits update the live probability.

This matters because previous logs showed a large mismatch between the old 30% assumption and actual Taker realization frequency.

### 3.4 TOTAL SCORE VALUE

This is the sole score-acquisition authority.

Phases:

| Phase | Rolling Kappa-eligible books | Coverage | Completion | Realization | Shared overflow |
|---|---:|---:|---:|---:|---:|
| IGNITION | `<41` | 4 | 3 | 3 | 1 |
| SURVIVAL | `41..79` | 2 | 5 | 3 | 1 |
| FRONTIER | `>=80` | 2 | 4 | 3 | 1 |

Priority is driven by current score contribution: ONE_AWAY, TWO_AWAY, healthy fresh coverage, critical expiry, and qualified median-frontier repair. Execution quality may change ordering but cannot create its own score authority.

### 3.5 EXECUTION / EXIT

Candidate reserves are ordered per lane. Lane capacity is consumed on successful placement rather than initial selection, so a failed candidate can be replaced by a bounded reserve candidate in the same response.

Exit authority remains V4.14.4. V4.15.1 does not weaken the bounded loss corridor to manufacture more completed RTs.

---

## 4. What was physically removed

### Removed Research modules

```text
research_cohort.py
research_kappa_flywheel.py
research_kappa_productivity.py
research_capacity_saturation.py
```

### Removed score/scheduler concepts from live Research

```text
cohort membership
CORE
RECYCLING
CORE_PROBE
density_due
flywheel priority
productivity as scheduling authority
score_qualified as a second qualification authority
breadth-rotation authority
Kappa-conversion-pressure authority
legacy execution-lane fallback scheduler
```

### Renamed the last hidden CORE privilege

V4.15.0 still used old names for a behavior that had already become TOTAL_SCORE-due only:

```text
qualified_core exact-min
qualified_core stale-TTL rescue
```

V4.15.1 removes that vocabulary. The behavior is now explicitly:

```text
TOTAL_SCORE_FRONTIER_EXACT_MIN
TOTAL_SCORE_STALE_*
```

and can activate only when the same TOTAL_SCORE authority marks the book due.

---

## 5. Code-size result

| Surface | V4.15.0 | V4.15.1 | Change |
|---|---:|---:|---:|
| `Strategy1_Research.py` | 12,911 lines | 12,349 | -562 |
| `research_execution_lanes.py` | 949 | 416 | -533 |
| Combined primary surface | 13,860 | 12,765 | **-1,095** |

The remaining 12k-line Research class is still large. V4.15.1 deliberately does **not** perform a full `QuoteDecision` rewrite because that would mix a major execution refactor with a cleanup release.

---

## 6. Launcher cleanup

The Research launcher now resolves wallet/hotkey/netuid/agent path **before** Python preflight, so preflight imports the exact strategy tree that will execute.

Removed launcher contracts:

- V4.12.9 breadth-rotation preflight;
- old `score_qualified` fixtures;
- old `qualified_core` exact-min API contract;
- old `research_qualified_core_*` knobs.

Current TOTAL_SCORE exact-min and stale-TTL behavior is directly tested in the V4.15.1 preflight.

The launcher currently contains **233 unique `research_*` assignments and zero duplicates**. This config surface is still large and is a future simplification target, but V4.15.1 does not remove knobs unless behavior-equivalence is proven.

---

## 7. Verification gate

Working-tree verification:

- **427 / 427** active Research/component tests PASS;
- `python -m compileall -q agents/strategy taos` PASS;
- `bash -n run_strategy1_research_test_multi.sh` PASS;
- `RESEARCH_PREFLIGHT_ONLY=1 ...` PASS;
- V4.14.4 RealNet final exit authority probe PASS;
- V4.14.4 scheduler retry/quarantine probe PASS;
- V4.15.1 TOTAL_SCORE geometry/backfill probe PASS;
- V4.15.1 exact-min/stale-TTL TOTAL_SCORE privilege probes PASS.

Protected hashes are unchanged:

```text
taos/im/validator/trade.py
137a4a7f26de9395a0028539a95411992c6ed0fa16ddd21682c04838121af0b8

agents/strategy/BaseStrategy.py
76941d948ce3bac0057e9ef788a3c6e93dab4f4bc79ce7b5b00733a67211f0cb

agents/strategy/AdaptiveAgent.py
6421fe403c4a51cb80ffd67bc37cd92be9dcbe646968819ceeabd42f1eb234c1
```

A repository-wide `pytest -q` cannot be used in the current minimal analysis environment because optional/runtime packages (`bittensor`, `transformers`, `pyarrow`, `loky`) are absent. That collection failure is dependency-related, not a V4.15.1 Research regression.

---

## 8. What must NOT be changed in this release

Do not add another scheduler or score state. Do not weaken the V4.14.4 loss authority. Do not restore CORE/cohort/productivity authority for compatibility. Do not promote Base or Adaptive yet.

The next legitimate step after packaging is a Testnet runtime smoke that measures:

1. scheduler/authorized → QUOTED conversion;
2. TTL/size/edge/fill rejection mix;
3. Maker/Taker realization mix;
4. negative RT ratio;
5. OBS0→OBS1→OBS2→OBS3 conversion;
6. rolling Kappa-eligible breadth;
7. response p95 latency.

Only after runtime proof should this Research candidate enter Phase 2 promotion.

---

## 9. Known Phase-2 defect

`BaseStrategy.py` still subclasses the live `Strategy1_Research`. Therefore the label `base_v4_13_9_champion` is not a true code-isolated freeze. V4.15.1 intentionally leaves this untouched.

If V4.15.1 becomes the Research champion, Phase 2 must create a truly frozen Base from the validated Research implementation; Phase 3 must then rebase Adaptive on that exact frozen Base.
