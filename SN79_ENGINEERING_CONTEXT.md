# SN79 Engineering Context

Source of truth for future Cursor work on Bittensor Subnet 79 miner agents.

**Audit date:** 2026-08-25  
**Scope:** current engineering context after Research V4.11 update. BaseStrategy, AdaptiveAgent and validator scoring code remain unchanged by V4.11.  
**Rule:** repository code wins over this brief if they disagree. Mismatches are listed explicitly.

Evidence labels used below:

- **OBSERVED:** present in current repository files.
- **BRIEF-REPORTED:** stated in the engineering brief; not re-verified from live logs in this audit.
- **INFERRED:** causal explanation consistent with code, not independently proven.
- **UNPROVEN:** external validator/runtime behavior not confirmed here.

---

### Current Research override: V4.11

Research now uses a hard 12-book deep-candidate cap, a sticky 10-book score-acquisition cohort, pre-screen Kappa-expiry refresh, lifecycle entry-cost ranking, bounded positive-EV minimum-order promotion, and QUIET-regime quote tightening/TTL extension. V4.10 hard Taker authority and zero-loss score-subsidy defaults are preserved. See `RESEARCH_V4_11_UPDATE.md`.

BaseStrategy and AdaptiveAgent are **not** promoted to these V4.11 changes yet.

## 1. Current exact file / version inventory

### Live deployment files (repo root + `agents/strategy/`)

| Track | File | Version marker | Parent | Runtime imports of sibling strategies |
|---|---|---|---|---|
| Research | `agents/strategy/Strategy1_Research.py` | `RESEARCH_POLICY_VERSION = "hybrid_score_taker_v4_11"` | `Strategy1_Debug` | **Yes:** `Strategy1.py`, `Strategy1_Debug.py` |
| Debug parent | `agents/strategy/Strategy1_Debug.py` | no policy version | `Strategy1` | `Strategy1.py` |
| Original parent | `agents/strategy/Strategy1.py` | none | `DetailedTemplateAgent` | `DetailedTemplateAgent.py` |
| Production base | `agents/strategy/BaseStrategy.py` | `DEPLOY_POLICY_VERSION = 'base_v4_1_1_maker_guard'` | `FinanceSimulationAgent` | **No** Strategy1 / Debug / Research / DetailedTemplateAgent |
| Adaptive | `agents/strategy/AdaptiveAgent.py` | `ADAPTIVE_VERSION = "adaptive_v2_strict"` | `BaseStrategy` | `BaseStrategy.py` only |

Internal telemetry mismatch on BaseStrategy (**OBSERVED**): `DEPLOY_POLICY_VERSION` is `base_v4_1_1_maker_guard`, but `RESEARCH_CONFIG` still emits `policy_version: 'deadlock_fix_v4_1_1_strict'`. Treat the class constant as authoritative.

### Launchers

| Expected by brief | Actual file | Internal runner | Default axon | Default PM2 | Default log dir |
|---|---|---|---|---|---|
| Miner 1 research | `run_strategy1_research_test.sh` | **`run_miner.sh`** | 8090 | hardcoded `miner` | `logs/strategy1_research` |
| Miner 1 research (multi) | `run_strategy1_research_test_multi.sh` | **`run_miner_multi.sh`** | **8091** | **sn79-m1** | **logs/m1_strategy1_research** |
| Miner 2 base | `run_base_strategy.sh` | **`run_miner.sh`** | 8090 | `miner` | `logs/base_strategy` |
| Miner 2 base (multi) | `run_base_strategy_multi.sh` | **`run_miner_multi.sh`** | **8092** | **sn79-m2** | **logs/m2_base_strategy** |
| Miner 3 adaptive | `run_adaptive_agent.sh` | **`run_miner.sh`** | 8090 | `miner` | `logs/adaptive_agent` |
| Miner 3 adaptive (multi) | `run_adaptive_agent_multi.sh` | **`run_miner_multi.sh`** | **8093** | **sn79-m3** | **logs/m3_adaptive_agent** |

`run_miner.sh` still does `pm2 delete miner` / `--name=miner`.  
`run_miner_multi.sh` uses `-i` / `PM2_NAME` and `pm2 delete "$PM2_NAME"`.

**Verdict:** the brief's topology exists, but it is implemented in the `*_multi.sh` files, not in the canonical names the brief listed.

### Historical archives (do not deploy unless a task says so)

- Research: `agents/strategy/__ver_st1_log__/` (`v2`, `v2_strict`, `v3_strict`, `v4_strict`, `v4_1_strict`, `v4_2_strict`, `Strategy1_Research_v4_11.py`)
- Base: `agents/strategy/__ver_base__/` (`BaseStrategy.py` = older `base_v4_1_standalone_optimized_v1`; `BaseStrategy_v4_1_1_strict.py`)
- Adaptive: `agents/strategy/__ver_adapt__/` (`AdaptiveAgent.py` = `adaptive_v1`; `AdaptiveAgent_v2_strict_deploy.py`)
- Legacy Strategy3/4/5 tracks remain at `agents/strategy/Strategy{3,4,5}.py` and `run_strategy{3,4,5}.sh`. They are **not** the current three-track architecture.

### Validator / scoring (relevant, not miner-owned)

- `taos/im/validator/reward.py` — Kappa-3 + realized PnL + soft floor + Pareto + GenTRX
- `taos/im/utils/kappa.py` — Kappa-3
- `taos/im/config/__init__.py` — `scoring.kappa.min_realized_observations` default **3**; `scoring.max_inactive_books` default **0.375**
- `taos/im/agents/__init__.py` — `FinanceSimulationAgent.handle`: `update → respond_simulation → report`

---

## 2. Class / inheritance architecture

```text
FinanceSimulationAgent  (taos/im/agents/__init__.py)
        │
        ├──────────────────────────────────────────────┐
        │                                              │
DetailedTemplateAgent                          BaseStrategy  [standalone flatten]
        │                                              │
    Strategy1                                          │
        │                                          AdaptiveAgent
    Strategy1_Debug
        │
    Strategy1_Research
```

### Research track (live)

```text
FinanceSimulationAgent
    → DetailedTemplateAgent
        → Strategy1
            → Strategy1_Debug
                → Strategy1_Research
```

Research **must** keep `Strategy1.py` and `Strategy1_Debug.py` beside it.

### Production / Adaptive track (live)

```text
FinanceSimulationAgent
    → BaseStrategy          # flattened copy of Template+S1+Debug+Research V4.1.1
        → AdaptiveAgent     # overlays only; quotes via super()._place_skewed_quotes
```

BaseStrategy **OBSERVED** does not import Strategy1 / Debug / Research / DetailedTemplateAgent / `importlib` loaders. Flattened methods are aliased at the bottom of `BaseStrategy.py` (`handle = _bsimpl_2_Strategy1_Debug_handle`, etc.).

AdaptiveAgent **OBSERVED** does not construct limit/market instructions itself. `_place_skewed_quotes` adapts `RegimeParamSet` then calls `super()._place_skewed_quotes(...)`.

---

## 3. Request execution flow

### Validator → miner

1. Validator publishes `MarketSimulationStateUpdate` (books, accounts, notices, config).
2. Miner axon receives synapse; agent `handle(state)` runs.
3. Response `FinanceAgentResponse.instructions` is delayed by validator `set_delays()` from dendrite process time (**OBSERVED** in `taos/im/validator/reward.py`). Slow miner CPU/network becomes simulated order delay.
4. Simulator matches/cancels; later ticks return `TradeEvent` / reject / cancel notices.

### Agent `handle` (all tracks)

Base path in `FinanceSimulationAgent.handle`:

```text
update(state)
    → respond_simulation(state) → respond(state)
    → report(state, response)
```

Strategy1 / BaseStrategy wrap this when latency/debug is on:

```text
update
    → respond
        → _tick++
        → _predict_all_books          # dominant CPU (BRIEF-REPORTED P50 ~38ms)
        → select_books_for_trading
        → classify_market_regime_from_profiles
        → build_mm_strategy_instructions
            → inventory / dust park / compaction
            → candidate ranking (normal vs Kappa-completion lanes)
            → quote construction + size/price normalize
            → maker-guard (Base V4.1.1; Research V4.2 force post-only in quote context)
        → instructions
    → report
    → SLOW_REQUEST if debug_enabled and total_ms >= debug_slow_request_ms (default 250)
```

AdaptiveAgent wraps `handle`:

```text
count request / detect sim-time rewind → session reset
apply phase controls (OBSERVE/BOOTSTRAP/NORMAL/DRIFT)
super().handle(state)                 # BaseStrategy execution
observe market / maybe detect DRIFT
emit ADAPTIVE_SUMMARY
persist state
restore phase controls
```

### Event learning after fills

- FIFO/VWAP / CROSS accounting on `onTrade`
- realized round-trip PnL into book memory
- fill-probability learning (distance buckets)
- Research V4.2: classify maker fill as FLAT / ACTIONABLE / DUST
- dust compact fill/fail bookkeeping + Adaptive dust cooldown
- Adaptive restart-safe Kappa observation counters

---

## 4. Research-agent evolution V1 → V4.2

Live file is V4.2 Strict. Archives under `__ver_st1_log__/`.

| Version | What it added (from code + brief) |
|---|---|
| Original Strategy1 | Global `regime.mode == "STRESSED"` forced every local book STRESSED (**OBSERVED** in `Strategy1.classify_book_archetype`). Bootstrap deadlock. |
| V1 | Decouple global STRESSED from local archetype; MM_BOOK fallback; adaptive P95/P99 spreads; inactive bootstrap; min-order sync; telemetry |
| V2 / V2 Strict | Signed inventory util; reservation-price skew; immediate inventory mgmt; age-60 touch/net aggressive-close gate; candidate backfill; toxic PnL sample floor; sparse-active YELLOW/GREEN |
| V3 | Safe dust parking; no naive min-size opposite close of sub-min inventory |
| V4 | Safe dust compaction (theorem) + Kappa-completion ranking; shared candidate budget starved completion |
| V4.1 | Isolated scheduler: total attempts 12, completion 4, normal 8, MM successes 4, completion successes 2 |
| **V4.2 Strict (live)** | Actionable-fill learning; bounded rank adjust; pending-2 bonus; partial-fill hold up to 750ms; adaptive compact cooldown; force MM post-only in quote context |

V4.2 defaults in `Strategy1_Research.initialize` (**OBSERVED**):

- `research_actionable_fill_min_samples=4`
- `prior_strength=6`, `prior_actionable=0.85`
- `actionable_fill_rank_weight=0.10`
- `dust_risk_target=0.15`, `dust_risk_rank_penalty=0.18`
- `kappa_one_away_bonus=0.10`
- `partial_fill_hold_max_ns=750_000_000`
- compact cooldown 100…600 ticks
- `research_force_mm_post_only=True`

`run_strategy1_research_test_multi.sh` passes these flags.  
`run_strategy1_research_test.sh` still comments “V4.1 Strict” and **omits** the V4.2 flags; class defaults still enable V4.2 if that launcher is used.

---

## 5. BaseStrategy evolution through V4.1.1

Live `BaseStrategy.py` is a flattened V4.1 research policy plus V4.1.1 maker guard.

V4.1.1 intended patch (**OBSERVED** in `_place_skewed_quotes` / initialize):

1. Normal MM `postOnly=True` by default (`mm_force_post_only`, default True).
2. Current-touch crossing guard (`mm_maker_guard_reprice`): BUY `<` best ask, SELL `>` best bid.
3. Expected PnL uses `is_maker=normal_mm_post_only`.
4. Maintenance quote pairs remain `post_only=True`.
5. Inventory-management / aggressive-close paths still use `_prefer_maker(book_id)` (not forced post-only).
6. `SLOW_REQUEST` telemetry when debug handle is active and `total_ms >= debug_slow_request_ms`.

`run_base_strategy_multi.sh` explicitly sets `mm_force_post_only=1 mm_maker_guard_reprice=1 debug_slow_request_ms=250.0`.  
`run_base_strategy.sh` does **not** pass those flags; code defaults still True.

Frozen economic parameters (launchers **OBSERVED**):

```text
min_expected_alpha=0.18
mm_base_size=0.25
max_inventory_base=1.20
max_mm_books_per_tick=4
max_managed_books_per_tick=8          # launcher; BaseStrategy initialize default is 4
mm_expiry_period_ns=500000000
aggressive_close_fee_buffer_bps=3.0
candidate_attempt_cap=12
kappa_completion_attempt_cap=4
kappa_completion_success_cap=2
kappa_completion_target=3
kappa_completion_rank_bonus=0.30
kappa_completion_fill_mult=0.70
kappa_completion_fill_floor=0.10
kappa_completion_relaxed_success_cap=2
kappa_completion_recent_pnl_floor=-0.01
```

BaseStrategy does **not** implement Research V4.2 actionable-fill / partial-fill-hold. That is Research-only.

---

## 6. AdaptiveAgent evolution through V2 Strict

Live `AdaptiveAgent.py`: `ADAPTIVE_VERSION = "adaptive_v2_strict"`, `ADAPTIVE_STATE_SCHEMA = 2`.

V1 (`__ver_adapt__/AdaptiveAgent.py`): fill-shift drift with `adaptive_drift_threshold=0.12`.

V2 Strict (**OBSERVED** composite trigger):

- spread: short/baseline `>= 1.30` **and** absolute increase `>= 4 bps`
- fill: abs shift `>= 0.005` and relative `>= 40%` (extreme if relative `>= 80%`)
- maker PnL: hard floor `≈ -0.02` or relative deterioration `≈ 0.35` vs positive long baseline
- window `≈ 250` requests, hold `≈ 500`
- DRIFT may start after OBSERVE (`adaptive_drift_start_requests`, default observe count)

Phases: OBSERVE → BOOTSTRAP → NORMAL, plus DRIFT overlay.

During DRIFT: fewer MM books, reduced size, more defensive spread, lower completion pressure, lower fill-trust blend, no aggressive tightening.

Persistence:

- `run_adaptive_agent_multi.sh` sets `ADAPTIVE_STATE_DIR=adaptive_state/m3`
- environment key default `testnet_${NETUID}_m3` or `net_${NETUID}_m3`
- `run_adaptive_agent.sh` (non-multi) defaults to `testnet_${NETUID}` **without `_m3`**

Session rewind (sim timestamp decrease) resets session-scoped scoring while keeping environment-level calibration.

---

## 7. Confirmed root causes and evidence

### 7.1 Original Strategy1 bootstrap deadlock — CONFIRMED IN CODE

`Strategy1.classify_book_archetype` still contains:

```text
if spread_bps >= stressed_cutoff or regime.mode == "STRESSED":
    return "STRESSED"
```

Research / BaseStrategy override this: local spread wins; global STRESSED is overlay (`research_trade_global_stress`), not local archetype.

**Do not copy Strategy1 archetype mapping into new code.**

### 7.2 Dust from partial maker fills — BRIEF-REPORTED, mechanism CONFIRMED IN CODE

Theorem in `_dust_compaction_safe_for_any_fill`:

```text
min_size > 0 and 0.5 * min_size < |q| < min_size
```

For `|q|=0.05`, `min_size=0.25`: not compactable. Parked as DUST. Parked dust → archetype `TOXIC_BOOK` / `PARKED_DUST` so MM cannot add new risk.

BRIEF-REPORTED end-state of one long run: 90 open, 89 dust, 4.1511 abs base; 65 Kappa-incomplete books of which 59 dust-locked. **Not re-read from logs in this audit.**

### 7.3 Shared completion/normal budget — FIXED IN V4.1 CODE

Research `_place_skewed_quotes` and BaseStrategy flattened equivalent isolate completion vs normal attempt/success caps.

### 7.4 Normal-MM accidental taker — FIXED IN BASE V4.1.1 CODE

Normal MM no longer delegates `postOnly` solely to `_prefer_maker()`. Authoritative `postOnly=True` plus touch reprice.

Research V4.2 uses `_research_force_maker_context` + `research_force_mm_post_only`.

### 7.5 Phase-mining freeze (legacy Strategy5) — NOT part of current three-track live agents

Older `Strategy3`/`Strategy5` still default `enable_phase_mining=True`. Irrelevant if miners run Research / Base / Adaptive.

---

## 8. Frozen invariants

Do not modify casually:

1. Inventory units = signed base / `max_inventory_base`
2. Hard inventory cap and side-size normalization / quantity rounding
3. Exact flat epsilon vs exchange min order
4. Dust detection and park
5. Compaction safety-for-any-fill proof (`|q| > 0.5 * min` and `|q| < min`)
6. Expected-PnL gates
7. FIFO / VWAP / CROSS accounting
8. Aggressive-close hard safety (age gate + touch/net after fee buffer)
9. Separated scheduler lanes (12 / 4 / 8, success caps)
10. Maker-only normal MM / maintenance; **not** inventory emergency exits
11. Global STRESSED must not force local STRESSED
12. AdaptiveAgent must not override hard safety methods or construct orders
13. Adaptive state isolated by environment key (testnet vs mainnet vs miner)
14. `min_expected_alpha=0.18`, `mm_base_size=0.25`, `max_inventory_base=1.20` unless an experiment explicitly changes one knob

---

## 9. Known weaknesses

| Item | Status |
|---|---|
| Dust creation from partial fills of min-size maker quotes | Dominant long-horizon structural issue (BRIEF-REPORTED); V4.2 attacks rate, not theorem |
| Dust compaction conversion ~1.21% | BRIEF-REPORTED; code still theorem-safe, Adaptive/V4.2 add cooldown |
| `predict_all_books` latency | BRIEF-REPORTED P50~38ms / P95~127ms; SLOW_REQUEST exists; **do not optimize until asked** |
| Canonical launchers vs multi launchers | Easy to start all three miners on `run_miner.sh` and kill each other |
| BaseStrategy RESEARCH_CONFIG policy string stale | `deadlock_fix_v4_1_1_strict` vs `base_v4_1_1_maker_guard` |
| BaseStrategy `max_managed_books_per_tick` default 4 vs launcher 8 | Launchers win if used |
| Adaptive non-multi env key lacks `_m3` | State-mix risk if both launchers used |
| Local Kappa counters ≠ validator Kappa | restart / lookback / inactive-book rules differ |
| Strategy1 parent still has deadlock mapping | Safe only because Research overrides it |

---

## 10. Current hypotheses

Label: **INFERENCE**, not proven reward.

1. Reducing dust-lock of incomplete books should raise books with ≥3 local observations.
2. Actionable-fill ranking should cut dust creation without collapsing maker PnL.
3. Partial-fill hold (same original 0.25 quote, longer GTT) can convert 0.05 residuals into actionable inventory without increasing max exposure.
4. Adaptive V2 DRIFT should cut size/pressure when median spread expands (maker economics deteriorate).
5. Maker-guard should drive accidental normal-MM taker fills to ~0.

Cooldown / ranking will **not** automatically increase on-chain incentive. Validator Kappa uses realized round-trips in a lookback window plus soft floor / Pareto.

---

## 11. Current telemetry

Research / Base (when `research_enabled` / `debug_enabled`):

- `[S1R_CONFIG]`, `[S1R_REGIME]`, `[S1R_POSITION]`, `POSITION_GUARD` (DUST_POSITION / HEARTBEAT / RELEASED)
- `TIMING`, **`SLOW_REQUEST`** (Base debug handle; needs `--log` / debug_enabled)
- dust compact attempts/orders/fills
- Kappa completion lane counters
- V4.2: actionable maker-fill, p_actionable, p_dust, partial-fill hold candidates

Adaptive:

- `ADAPTIVE_PHASE`, `ADAPTIVE_DRIFT`, `ADAPTIVE_QUOTE`, `ADAPTIVE_SUMMARY`
- persisted JSON under `adaptive_state/m3` (multi launcher)

Validator dashboard (external): volume, round-trip volume, activity, kappa_score, pnl_score, score, call_time, timeouts.

---

## 12. Multi-miner deployment topology

**Intended concurrent VPS setup (OBSERVED in `*_multi.sh`):**

| Miner | Agent | Launcher | PM2 | Axon | Logs | Extra |
|---|---|---|---|---|---|---|
| 1 | Strategy1_Research | `run_strategy1_research_test_multi.sh` | sn79-m1 | 8091 | `logs/m1_strategy1_research` | — |
| 2 | BaseStrategy | `run_base_strategy_multi.sh` | sn79-m2 | 8092 | `logs/m2_base_strategy` | `--log` optional |
| 3 | AdaptiveAgent | `run_adaptive_agent_multi.sh` | sn79-m3 | 8093 | `logs/m3_adaptive_agent` | `adaptive_state/m3` |

Shared: public IP, repository, netuid.  
Must not share: hotkey, axon port, PM2 name, adaptive state dir.

**Do not** use `run_strategy1_research_test.sh` / `run_base_strategy.sh` / `run_adaptive_agent.sh` together on one VPS: they call `run_miner.sh` → `pm2 delete miner`.

Default network in all current launchers: test.finney, **netuid 366** (not mainnet 79). Override with `-u 79` and the finney endpoint when deploying realnet.

---

## 13. Current test plan and metrics

Evaluation horizons (from brief; policy, not code):

- sanity: ~1h, ~6h
- useful: ~12h
- primary: **24 real hours**
- strong: 36–48h

Do not retune during a clean 24h unless correctness fails.

Track every serious run:

**Trading:** fills, maker/taker, completed cycles, win/loss, realized PnL, maker PnL, taker PnL, median PnL, hold age  

**Scoring:** books with 0/1/2/≥3 obs, incomplete books  

**Dust:** entries/releases, current dust books, abs base, age, incomplete+dust, compact attempts/fills/conversion  

**V4.2:** actionable vs dust maker-fill ratios, P_actionable, P_dust, partial-fill hold counts  

**Activity:** orders/tick, fills/1000 ticks, reject reasons, lane usage  

**Regime:** median/P90/P95/P99 spread, maker PnL by spread bucket  

**Latency:** P50/P90/P95/P99/max, `predict_all_books`, SLOW_REQUEST  

**Correctness:** normal-MM taker fills, rejects, invalid sizes, duplicate client IDs, inventory-cap hits  

Compare three miners only if logs prove unique UID/hotkey, unique log dir, distinct PM2, overlapping sim period.

---

## 14. Open questions that require new data

1. Are the three miners currently running via `*_multi.sh` or the single-miner scripts?
2. Has V4.2 reduced dust vs the BRIEF-REPORTED 89-dust run on a comparable horizon?
3. Accidental normal-MM taker count on live Base V4.1.1 / Research V4.2 / Adaptive?
4. Compaction conversion after V4.2/Adaptive cooldown vs 1.21%?
5. Validator Kappa eligibility vs local ≥3 counters for the same UID?
6. Exchange min order still ~0.25 on the validator they use?
7. Adaptive V2 DRIFT firing rate vs spread expansion in a 24h run?
8. `call_time` / SLOW_REQUEST distribution after the 250ms logger?

---

## 15. DO NOT REGRESS checklist

1. Do not reintroduce global-STRESSED → local STRESSED.
2. Do not globally lower fill thresholds just to trade more.
3. Do not globally increase order size to beat partial fills.
4. Do not force min-sized opposite orders against tiny dust.
5. Do not add unsafe same-side dust top-up without explicit proof.
6. Do not remove or weaken the dust exposure theorem.
7. Do not make all inventory exits post-only.
8. Do not make normal MM non-post-only.
9. Do not merge V4.1 completion/normal scheduler budgets.
10. Do not change alpha, size, spread, fill, Kappa, dust, and risk in one experiment.
11. Do not mix testnet/realnet/miner adaptive state.
12. Do not claim static tests equal profitability.
13. Do not treat historical counterfactuals as A/B tests.
14. Do not infer validator Kappa from local counters alone.
15. Do not optimize `predict_all_books` while changing trading policy.
16. Do not start concurrent miners with `run_miner.sh`.
17. Do not let AdaptiveAgent construct orders; keep `super()._place_skewed_quotes`.
18. Do not convert partial-fill hold into extra 0.25 rescue size.

---

## Mechanism map (where it lives)

| Mechanism | Research | BaseStrategy | Adaptive |
|---|---|---|---|
| Feature computation / prediction | Strategy1 / Template | flattened `_predict_all_books` | inherited |
| Regime | Research overlay | flattened Research classifier | observes spread; does not replace |
| Candidate ranking | Research + V4.2 quality adjust | V4.1 rank + completion bonus | bounded quality + one-away bonus |
| Kappa completion lanes | V4.1/V4.2 | V4.1 | phase may disable/scale |
| Fill probability | Strategy1 buckets | flattened | posterior blend overlay |
| Actionable-fill learning | **V4.2 only** | no | no (dust compact learning only) |
| Partial-fill hold | **V4.2 only** | no | no |
| Normal-MM maker guard | V4.2 quote context | **V4.1.1** | inherits Base |
| Inventory / aggressive close | Research + Strategy1 | flattened Research | not overridden |
| Dust park | Research | flattened | inherited |
| Dust compact theorem | Research | flattened | ranking/cooldown overlay only |
| Drift | n/a | n/a | **V2 composite** |
| Persistence | JSONL logs | JSONL if `--log` | **adaptive_state** |
| Latency | Debug parent | SLOW_REQUEST | wraps handle |

---

## Scoring reminder (validator)

Trading score ≈ `0.79 * Kappa-3 + 0.21 * realized PnL score`, then soft floor (median of positive scores) then Pareto. Kappa needs realized round-trips; default min observations **3**. Excess inactive books (above ~37.5%) score as 0. Volume without quality does not clear the floor.

Local completion target of 3 observations/book is a **scheduler heuristic** matching validator `min_realized_observations=3`. 70–80 eligible books is an operational target, not a protocol constant.
