# SN79 St6.4 Research V4.14.3 — Engine Cleanup P1

## Scope

This is a behavior-preserving engine cleanup over Research policy `wide_kappa_wave_v4_14_3`.
It does **not** change the V4.14.3 trading objective, Kappa scheduler, inventory-liveness thresholds,
bounded-loss thresholds, sizing economics, alpha engine, validator accounting, or hard risk caps.

Engine revision: `lean_engine_p1_v4_14_3`.

## Removed permanently unused paths

1. Strategy1 sim-time auto-tuner and tuning.json hot-reload.
2. Legacy built-in Kappa/demo branch in Strategy1's active response path.
3. Score-EV A/B fallback to the legacy global rank. Score-EV remains authoritative.
4. Fill-hazard policy takeover A/B branch. Fill hazard remains telemetry / exit-comparison input.
5. Experimental old-dust escape subsystem. Current dust economics/compaction remains.
6. Generic positive-EV minimum-order override. Explicit ONE_AWAY/TWO_AWAY/qualified-CORE exact-min paths remain.
7. Legacy direct non-catastrophic RISK_TAKER authority. Unified exit/inventory liveness remains authoritative; catastrophic safety remains.
8. Score-loss subsidy / negative Kappa-completion loss-floor path. Current active zero-loss score floor remains.
9. Permanently-disabled partial-fill “ONE_AWAY only” restriction; active policy already applied bounded hold to incomplete books generally.
10. Obsolete helper functions and unused imports attached to the paths above.

## Removed configuration surface

20 dead knobs were removed from active launchers/code:

- `enable_auto_tuning`
- `allow_tuning_config`
- `enable_kappa_strategy`
- `research_allow_score_loss_subsidy`
- `research_enable_dust_escape`
- `research_enable_risk_taker_direct`
- `research_enable_score_ev`
- `research_partial_fill_hold_one_away_only`
- `research_positive_ev_min_order_override`
- `research_positive_ev_min_safe_fraction`
- `research_positive_ev_min_exit_fraction`
- `research_positive_ev_min_trading_ev`
- `research_risk_direct_min_age_ticks`
- `research_risk_direct_failed_exit_count`
- `research_risk_direct_min_ev_advantage_bps`
- `research_sn79_max_score_subsidy_loss_bps`
- `research_sn79_one_away_loss_floor_bps`
- `research_sn79_two_away_loss_floor_bps`
- `research_sn79_uncovered_loss_floor_bps`
- `research_use_fill_hazard_for_policy`

`research_risk_direct_max_loss_bps` is intentionally retained because the value is still used by the catastrophic hard-risk safety floor.

## Size reduction

Across the eight active engine files touched in P1:

- Before: 18,757 lines
- After: 18,029 lines
- Removed: **728 lines**
- AST `if` nodes: 1,239 -> 1,181 (**-58 branches**)
- AST calls: 9,786 -> 9,452 (**-334 call sites in source AST**)
- Functions/classes removed: **20**
- Unused-import static audit: **0 candidates**

Largest reduction:

- `Strategy1.py`: 2,169 -> 1,914 (-255)
- `Strategy1_Research.py`: 12,780 -> 12,490 (-290)
- `research_hybrid.py`: 688 -> 625 (-63)

## Frozen active behavior

The following V4.14.3 components were not changed:

- `research_kappa_productivity.py`
- `research_inventory_liveness.py`
- `research_unified_exit.py`
- `research_execution_lanes.py`
- `research_lifecycle_ev.py`
- validator `taos/im/validator/trade.py`

Validator trade.py SHA-256 remains:

`137a4a7f26de9395a0028539a95411992c6ed0fa16ddd21682c04838121af0b8`

## Verification

- Research regression: **496/496 PASS**
- Python compilation of active strategy tree: PASS
- Research V4.14.3 launcher preflight: PASS
- Adaptive V4.13.9 launcher preflight: PASS
- Shell syntax checks on active launchers: PASS
- Validator hash: exact match

A repository-wide `pytest` collection was also attempted. It cannot be used as a clean acceptance signal in this container because unrelated GenTRX/validator tests require optional packages not installed here (`transformers`, `pyarrow`, `bittensor`, `loky`), and archived `__ver_st1_log__` tests can shadow active strategy modules. The dedicated active Research suite is green.

The Base V4.13.9 multi-launcher preflight remains stale against the V4.14.x Research helper version; the same failure exists in the original V4.14.3 package and was not introduced by this cleanup.

## What this cleanup does NOT fix

P1 deliberately does not alter the two remaining RealNet defects discovered in the 19-hour V4.14.3 run:

1. Exit-authority holes between liveness `-12 bps`, bounded-loss minimum age, and the bounded-loss lower floor.
2. Scheduler retry starvation where TOXIC/NEGATIVE_EV/AVOID candidates receive repeated grants instead of rotating quickly to executable fresh books.

Those should be optimized only after this lean engine baseline is accepted.
