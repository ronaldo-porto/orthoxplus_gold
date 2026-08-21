# SN79 THREE-AGENT DEPLOYMENT

**Date:** 2026-08-21  
**Scope:** version integration and deployment prep only. Strategy economics were not retuned. Miners were not started, stopped, published, or registered.

Live agent Python files were already the approved versions. This task copied them to the required canonical archive names, added `__ver_adaptive__/`, and tightened launcher version reporting/guards.

==================================================
SN79 THREE-AGENT DEPLOYMENT
==================================================

## RESEARCH
--------
Live source: `agents/strategy/Strategy1_Research.py`  
Version: Strategy1 Research V4.2 Strict  
Policy marker: `RESEARCH_POLICY_VERSION = "dust_actionable_v4_2_strict"` (`Strategy1_Research.py:83`)  
Archive source: `agents/strategy/__ver_st1_log__/Strategy1_Research_v4_2_strict.py`  
Live SHA: `008cc602f4271664ba23d6fba0b4cefa833b11e1703fa16217c9979413ca666d`  
Archive SHA: `008cc602f4271664ba23d6fba0b4cefa833b11e1703fa16217c9979413ca666d`  
Hashes equal: **PASS**

Live launcher: `run_strategy1_research_test_multi.sh`  
Versioned launcher: `agents/strategy/__ver_st1_log__/run_strategy1_research_v4_2_strict_test.sh`  
PM2: `sn79-m1`  
Port: `8091`  
Agent name: `Strategy1_Research`  
Log dir: `logs/m1_strategy1_research`  
Version guard: fails unless live file contains `dust_actionable_v4_2_strict`  
run_miner_multi: **PASS** (exec only `run_miner_multi.sh`; no `run_miner.sh`)  
Syntax: **PASS** (`python -m py_compile` + `bash -n`)  
MRO: `Strategy1_Research` → `Strategy1_Debug` → `Strategy1` → `DetailedTemplateAgent` → `FinanceSimulationAgent`

## BASE
----
Live source: `agents/strategy/BaseStrategy.py`  
Version: BaseStrategy V4.1.1 Strict Maker Guard  
Policy marker: `DEPLOY_POLICY_VERSION = 'base_v4_1_1_maker_guard'` (`BaseStrategy.py:332`)  
Archive source: `agents/strategy/__ver_base__/BaseStrategy_v4_1_1_strict.py`  
Live SHA: `a9f700bc0664298eb2ff515577cab53ca631b141a04a0d126a9077536576c687`  
Archive SHA: `a9f700bc0664298eb2ff515577cab53ca631b141a04a0d126a9077536576c687`  
Hashes equal: **PASS**

Live launcher: `run_base_strategy_multi.sh`  
Versioned launcher: `agents/strategy/__ver_base__/run_base_strategy_v4_1_1_strict.sh`  
PM2: `sn79-m2`  
Port: `8092`  
Agent name: `BaseStrategy`  
Log dir: `logs/m2_base_strategy`  
Version guard: fails unless live file contains `DEPLOY_POLICY_VERSION = 'base_v4_1_1_maker_guard'`  
run_miner_multi: **PASS**  
Syntax: **PASS**  
MRO: `BaseStrategy` → `FinanceSimulationAgent`  
Wrappers/importlib/`__ver_base__` runtime import: **absent** (full standalone source)

Frozen launcher knobs confirmed present: `min_expected_alpha=0.18`, `mm_base_size=0.25`, `max_inventory_base=1.20`, `max_mm_books_per_tick=4`, `max_managed_books_per_tick=8`, `mm_force_post_only=1`, `mm_maker_guard_reprice=1`.

## ADAPTIVE
--------
Live source: `agents/strategy/AdaptiveAgent.py`  
Version: AdaptiveAgent V2 Strict  
Policy marker: `ADAPTIVE_VERSION = "adaptive_v2_strict"` (`AdaptiveAgent.py:66`)  
Archive source: `agents/strategy/__ver_adaptive__/AdaptiveAgent_v2_strict.py`  
Live SHA: `facae9cd624be1354ce226ae371674f0d7b17523a541dfae25359cba17081fa1`  
Archive SHA: `facae9cd624be1354ce226ae371674f0d7b17523a541dfae25359cba17081fa1`  
Hashes equal: **PASS**

Parent BaseStrategy: live `agents/strategy/BaseStrategy.py` via `from BaseStrategy import BaseStrategy`  
MRO: `AdaptiveAgent` → `BaseStrategy` → `FinanceSimulationAgent`  
Environment isolation: `ADAPTIVE_ENVIRONMENT_KEY=testnet_<netuid>_m3` or `net_<netuid>_m3`  
State directory: `adaptive_state/m3`  
Quotes: `_place_skewed_quotes` adapts `RegimeParamSet` then calls `super()._place_skewed_quotes` (does not construct orders)

Live launcher: `run_adaptive_agent_multi.sh`  
Versioned launcher: `agents/strategy/__ver_adaptive__/run_adaptive_agent_v2_strict.sh`  
PM2: `sn79-m3`  
Port: `8093`  
Agent name: `AdaptiveAgent`  
Log dir: `logs/m3_adaptive_agent`  
Version guard: fails unless Adaptive is `adaptive_v2_strict` **and** Base is `base_v4_1_1_maker_guard`  
run_miner_multi: **PASS**  
Syntax: **PASS**

## RUNNER
------
`run_miner_multi.sh` SHA: `a5d2b63b2402835bd06f231677f4ba5679549529fc0521b7f62599076d88c98d`  
Syntax: **PASS** (`bash -n`)  
supports `-i`: **PASS** (`i) PM2_NAME=${OPTARG};;`)  
generic `pm2 delete miner` absent: **PASS**  
unique process behavior: **PASS** (`pm2 delete "$PM2_NAME"`)  
`run_miner_multi.sh` was not modified.

## INVARIANTS
----------
Base hard risk changes: **none**. Live `BaseStrategy.py` byte-identical to the already-approved V4.1.1 archive. No alpha/size/inventory/lane/dust-theorem edits.  
Research unsafe dust rescue: **none**. Live `Strategy1_Research.py` byte-identical to V4.2 archive. No top-up/rescue/global fill-threshold edits.  
Adaptive hard-risk overrides: **none**. Live `AdaptiveAgent.py` byte-identical to V2 archive. No new inventory/dust/execution-safety overrides added.  
Unexpected policy changes: **none** in agent source.

Launcher-only changes:
- Root Base launcher now echoes `version=base_v4_1_1_maker_guard`.
- Root Adaptive launcher now also guards live Base V4.1.1 and echoes `base_policy=base_v4_1_1_maker_guard`.
- Versioned launchers walk up to repo root so they still target live agent files.

Pre-existing cosmetic note (unchanged): Base `RESEARCH_CONFIG` still labels `policy_version: 'deadlock_fix_v4_1_1_strict'` while `DEPLOY_POLICY_VERSION` is `base_v4_1_1_maker_guard`.

## FINAL VERDICT
-------------
**PASS**

READY TO RESTART THREE MINERS: **YES**

Miners were not started by this task. Restart only when explicitly requested.
