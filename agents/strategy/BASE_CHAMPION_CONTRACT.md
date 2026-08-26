# BaseStrategy champion — AdaptiveAgent API / state contract

**Frozen:** `DEPLOY_POLICY_VERSION = 'base_v4_4_champion'`  
**Parent baseline:** `base_v4_1_1_maker_guard`  
**Rule:** AdaptiveAgent was not modified in Phase 2. Phase 3 must consume this contract. Do not reimplement Base engines.

Base is standalone: `class BaseStrategy(FinanceSimulationAgent)`. Runtime imports of `Strategy1*`, `research_*`, or sibling helpers (`score_ev`, `candidate_screen`, …) are forbidden.

---

## 1. Identity Adaptive must pin

| Symbol | Required value |
|---|---|
| `DEPLOY_POLICY_VERSION` | `base_v4_4_champion` |
| `BASE_CHAMPION` | `True` |
| `BASE_CHAMPION_FROZEN` | `True` |
| `REGIME_POLICY_VERSION` | `regime_v2` |
| `EXECUTION_POLICY_VERSION` | `execution_v1_frozen` |
| `SCORE_EV_POLICY_VERSION` | `score_ev_v3` |
| `REALIZATION_POLICY_VERSION` | `realization_v1` |
| `QUOTE_POLICY_VERSION` | `quote_hysteresis_ttl_v2` |
| `ADVERSE_POLICY_VERSION` | `ofi_markout_v1` |
| `ENTRY_SIZE_POLICY_VERSION` | `entry_size_v1` |
| `SCREEN_POLICY_VERSION` | `candidate_screen_v1` |

Import only:

```python
from BaseStrategy import (
    BaseStrategy,
    BookMemory,
    BookProfile,
    DirectionForecast,
    FillProbabilityEstimate,
    InventorySnapshot,
    RegimeParamSet,
)
```

---

## 2. Hard caps Adaptive must not loosen

These are champion invariants. Phase overlays may tighten, never raise.

| Cap | Champion default | Adaptive rule |
|---|---|---|
| `min_expected_alpha` | `0.18` | do not lower |
| `mm_base_size` | `0.25` | do not raise |
| `max_inventory_base` | `1.20` | do not raise |
| `max_mm_books_per_tick` | `4` | may temporarily lower; restore |
| `max_managed_books_per_tick` | `8` | do not raise |
| `mm_force_post_only` | `True` | stay maker-only |
| HJB live path | off | shadow telemetry only |
| Dust escape | not wired | do not add |
| Runtime Research import | forbidden | stay forbidden |

---

## 3. Methods Adaptive may override

Every override must call `super()` and must not construct orders.

| Method | Champion owner | Adaptive may |
|---|---|---|
| `initialize` | Base engines + flags | add Adaptive config/state after `super().initialize()` |
| `handle` | predict, screen, rank, quote, realize | wrap with phase controls; restore in `finally` |
| `estimate_fill_probability` | frozen hazard + fallback | overlay **only** when `_execution_last[book].fallback_reason` is set |
| `dynamic_order_size` | `allowed_entry_size` + hard caps | scale down by phase; never exceed Base size; never emit `< min_order` |
| `_place_skewed_quotes` | hysteresis, TTL, post-only, dust prevent | overlay `RegimeParamSet` then `super()`; HJB shadow after |
| `_global_book_rank` | Score-EV V3 / `select_rank` | quality adjust only; **no second Kappa one-away bonus** |
| `_select_dust_compaction_books` | parked-dust universe + theorem | rank/cooldown subset of `super()` universe |
| `_completion_observation_count` | Base realized-obs map | `max(super(), persisted session obs)` |
| `onTrade` | markout, hazard observe, dust, trips | update Adaptive memory after `super().onTrade` |

Adaptive must **not** override: `select_fast_candidates`, `_fast_screen`, `compute_score_ev`, `evaluate_realization`, `_update_ofi_from_state`, quote construction (`limit_order` / `cancel_order`).

---

## 4. Read-only Base state (do not write)

Read with `getattr`. Do not assign.

### Global / request

| Attribute | Type | Meaning |
|---|---|---|
| `_tick` | `int` | request counter |
| `_market_regime` | `str` | `NORMAL` / `STRESSED` / `TOXIC` / … |
| `_score_regime` | `str` | `COVERAGE` / `BALANCED` / `HARVEST` / … |
| `ttl_min_ms` | `float` | adaptive TTL floor |
| `ttl_max_ms` | `float` | adaptive TTL cap |
| `_last_profiles` | `list[BookProfile]` | last profile scan |
| `_last_screen` | `ScreenResult \| None` | last candidate screen |
| `_feature_cache` | `FeatureCache` | touch/deep cache |
| `_ofi` / `_ofi_last` | tracker / `dict[int, OfiSnapshot]` | real OFI, not imbalance |
| `_markout_by_book` | `dict[int, dict]` | shrunk markout |
| `_execution_hazard` | `FillHazardModel` | frozen model |
| `_research_parked_dust` | `dict[int, dict]` | parked dust book map |
| `_research_dust_compact_active` | `dict[int, int]` | book → submit tick |
| `_research_exchange_min_order_size` | `float` | exchange min clip |
| `_research_realized_observations_by_book` | `dict[int, int]` | Kappa obs |
| `_research_round_trip_closes` | `int` | completed trips |
| `_entry_size_last` | `dict[int, Any]` | last entry-size decision |

### Per-book maps

**`_score_ev_last[book] → ScoreEVBreakdown`**

`book, side, alpha, fill_prob_old, fill_prob_hazard, actionable_fill_prob, dust_prob, spread_capture_bps, expected_markout_bps, fees_bps, trading_ev, observation_count, required_observation_count, observations_remaining, completion_value, dust_cost, inventory_cost, latency_cost, activity_deficit_value, adverse_selection_risk, last_realization_time, recent_realized_pnl, inventory_state, lane, final_score, eligible, reject_reason`

`final_priority` is `final_score`. `eligible=False` means Adaptive must not force a quote.

**`_execution_last[book]`**

| Key | Type | Meaning |
|---|---|---|
| `legacy` | `FillProbabilityEstimate` | old fill model |
| `buy` / `sell` | `HazardPrediction \| None` | `any_fill, actionable_fill, dust, source, usable, n_at_risk, ttl_ms` |
| `fallback_reason` | `str` | empty ⇒ Base fill is authoritative |
| `model_confidence` | `float` | hazard confidence |

**`_quote_submit_snapshot[book]`**

`alpha, imbalance, inventory_util, inventory_state, toxic, quote_ev, chosen_ttl, ttl_reason, dust_probability, ofi_raw, ofi_normalized, ofi_fast, ofi_supported, ofi_source`

`imbalance` is **not** OFI. Use `ofi_fast` / `ofi_supported`.

**`_realization_last[book] → RealizationDecision`**

`exit_urgency, state, selected_action, maker_exit_ev, taker_allowed, trigger`

---

## 5. Temporary knobs Adaptive may write (must restore)

Snapshot before `super().handle`, restore in `finally`. Never persist a lowered cap.

| Knob | Champion default | Allowed overlay |
|---|---|---|
| `max_mm_books_per_tick` | `4` | lower only (`<=` saved champion value) |
| `research_kappa_completion_enabled` | `True` | may disable in OBSERVE |
| `research_kappa_completion_rank_bonus` | `0.3` | scale down, do not raise |
| `research_kappa_completion_relaxed_success_cap` | `<= 2` | may lower |
| `score_ev_one_away_weight` | `0.18` | scale down, do not raise |
| `score_ev_two_away_weight` | `0.06` | scale down, do not raise |
| `research_dust_compact_books_per_tick` | Base default | expand only around `super()` dust select, then restore |

`score_ev_new_book_weight` stays `0.0`.

---

## 6. Types Adaptive already imports

```text
FillProbabilityEstimate(buy: float, sell: float)

HazardPrediction(
    any_fill, actionable_fill, dust, source, usable, n_at_risk, ttl_ms
)

InventorySnapshot(
    net_base, inventory_ratio, band, vwap_entry, unrealized_bps,
    position_ticks, opened_at_ns, reason
)

RegimeParamSet(
    quote_enabled, alpha_enabled, spread_offset, skew_strength, size_mult,
    profit_target_bps, stop_loss_bps, min_fill_prob, buy_bias, sell_bias,
    skew_strength_mult, min_fill_prob_delta, edge_bias, quote_enabled_override
)
```

`band` values Adaptive already reads: `FLAT`, `LONG`, `SHORT`, `MAX_LONG`, `MAX_SHORT`.

---

## 7. Ownership boundaries

| Engine | Owner | Adaptive |
|---|---|---|
| Candidate screen + cache + fallback | Base | do not re-screen |
| Score-EV V3 + Kappa scheduler lanes | Base | weight overlay only |
| Realization / ExitUrgency | Base | do not add a second flatten |
| OFI + delayed markout | Base | read snapshot / Score-EV markout |
| Frozen fill hazard | Base | overlay iff fallback_reason |
| Quote hysteresis + adaptive TTL | Base | do not bypass HOLD |
| Dust park / compact theorem | Base | rank Base universe only |
| Order construction / post-only | Base | never call `limit_order` / `cancel_order` |
| Analytical HJB | Adaptive shadow | never submit |

---

## 8. `_adaptive_base_outputs(book_id)` contract

Adaptive already maps Base state to this dict. Phase 3 must keep the keys:

`market_regime, score_regime, ttl_min_ms, ttl_max_ms, fallback_reason, model_confidence, fill_hazard_any_buy, fill_hazard_any_sell, fill_hazard_usable, actionable_fill_probability, dust_probability, score_ev, kappa_completion_value, markout_estimate, trading_ev, observations_remaining, ofi, chosen_ttl, inventory_util, candidate_reject_reason`

`ofi` reads snapshot `ofi_fast` only when `ofi_supported` is set. `imbalance` is exposed separately and is never used as OFI.

---

## 9. Full fallback Adaptive must preserve

If `fast_candidate_screen_enabled` is off, the selected set is empty, or the screen raises, Base walks every book through the original `predict_direction` path. Adaptive must not disable that fallback or require the screen to succeed.

---

## 10. Phase 3 stop-lines

1. Do not import `research_*` or sibling helpers from Adaptive.
2. Do not add a second Score-EV or Kappa one-away bonus.
3. Do not enable live HJB.
4. Do not enable dust escape.
5. Do not raise hard caps above this contract.
6. Do not write `_score_ev_last`, `_execution_last`, `_quote_submit_snapshot`, `_feature_cache`, or `_ofi`.
