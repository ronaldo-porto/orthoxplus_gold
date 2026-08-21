# SN79 Agent Version Manifest

**Date:** 2026-08-21  
**Task:** version integration / deployment prep. No miners were started.  
**Rule:** live filenames stay stable; `__ver_*` directories are archives only. Runtime never imports from `__ver_*`.

---

## Research Agent

- **Live:** `agents/strategy/Strategy1_Research.py`
- **Version:** Strategy1 Research V4.2 Strict
- **Policy:** `dust_actionable_v4_2_strict`
- **Parent chain:** `Strategy1_Research` → `Strategy1_Debug` → `Strategy1` → `DetailedTemplateAgent` → `FinanceSimulationAgent`
- **Archive:** `agents/strategy/__ver_st1_log__/Strategy1_Research_v4_2_strict.py`
- **Root launcher:** `run_strategy1_research_test_multi.sh`
- **Versioned launcher:** `agents/strategy/__ver_st1_log__/run_strategy1_research_v4_2_strict_test.sh`
- **PM2:** `sn79-m1`
- **Port:** `8091`
- **Logs:** `logs/m1_strategy1_research`
- **Historical kept:** `__ver_st1_log__/Strategy1_Research_v4_2_strict_deploy.py` and prior v2–v4.1 files

---

## BaseStrategy

- **Live:** `agents/strategy/BaseStrategy.py`
- **Version:** BaseStrategy V4.1.1 Strict Maker Guard
- **Policy:** `base_v4_1_1_maker_guard`
- **Parent chain:** `BaseStrategy` → `FinanceSimulationAgent`
- **Archive:** `agents/strategy/__ver_base__/BaseStrategy_v4_1_1_strict.py`
- **Root launcher:** `run_base_strategy_multi.sh`
- **Versioned launcher:** `agents/strategy/__ver_base__/run_base_strategy_v4_1_1_strict.sh`
- **PM2:** `sn79-m2`
- **Port:** `8092`
- **Logs:** `logs/m2_base_strategy`
- **Historical kept:** `__ver_base__/BaseStrategy.py` (`base_v4_1_standalone_optimized_v1`), `BaseStrategy_Old.py`, `BaseStrategy_Opt.py`

Note: `RESEARCH_CONFIG` still emits `policy_version: 'deadlock_fix_v4_1_1_strict'`. The class constant `DEPLOY_POLICY_VERSION` is authoritative.

---

## AdaptiveAgent

- **Live:** `agents/strategy/AdaptiveAgent.py`
- **Version:** AdaptiveAgent V2 Strict
- **Policy:** `adaptive_v2_strict`
- **Parent chain:** `AdaptiveAgent` → `BaseStrategy` → `FinanceSimulationAgent`
- **Import:** `from BaseStrategy import BaseStrategy` (live `agents/strategy/BaseStrategy.py` only)
- **Archive:** `agents/strategy/__ver_adaptive__/AdaptiveAgent_v2_strict.py`
- **Root launcher:** `run_adaptive_agent_multi.sh`
- **Versioned launcher:** `agents/strategy/__ver_adaptive__/run_adaptive_agent_v2_strict.sh`
- **PM2:** `sn79-m3`
- **Port:** `8093`
- **Logs:** `logs/m3_adaptive_agent`
- **State:** `adaptive_state/m3`
- **Environment key:** `testnet_<netuid>_m3` or `net_<netuid>_m3`
- **Historical kept:** `agents/strategy/__ver_adapt__/` (`adaptive_v1` and `AdaptiveAgent_v2_strict_deploy.py`)

---

## SHA-256

### Live agents

| File | SHA-256 |
|---|---|
| `agents/strategy/Strategy1_Research.py` | `008cc602f4271664ba23d6fba0b4cefa833b11e1703fa16217c9979413ca666d` |
| `agents/strategy/BaseStrategy.py` | `a9f700bc0664298eb2ff515577cab53ca631b141a04a0d126a9077536576c687` |
| `agents/strategy/AdaptiveAgent.py` | `facae9cd624be1354ce226ae371674f0d7b17523a541dfae25359cba17081fa1` |

### Archived agents

| File | SHA-256 |
|---|---|
| `agents/strategy/__ver_st1_log__/Strategy1_Research_v4_2_strict.py` | `008cc602f4271664ba23d6fba0b4cefa833b11e1703fa16217c9979413ca666d` |
| `agents/strategy/__ver_base__/BaseStrategy_v4_1_1_strict.py` | `a9f700bc0664298eb2ff515577cab53ca631b141a04a0d126a9077536576c687` |
| `agents/strategy/__ver_adaptive__/AdaptiveAgent_v2_strict.py` | `facae9cd624be1354ce226ae371674f0d7b17523a541dfae25359cba17081fa1` |

Live vs archive agent hashes: **equal**.

### Live launchers

| File | SHA-256 |
|---|---|
| `run_strategy1_research_test_multi.sh` | `7347e41b28af471b747bb61f4de86dcf368b8d612898e00f623fca4abace1ed3` |
| `run_base_strategy_multi.sh` | `c43818255ea22a8d137655c104d54c41c8f85993e0e2be319037e64c71a6ea39` |
| `run_adaptive_agent_multi.sh` | `acef8831eac455c6ca0b645e64beefad79cb79392f8b23109560fe9b2f34fdae` |
| `run_miner_multi.sh` | `a5d2b63b2402835bd06f231677f4ba5679549529fc0521b7f62599076d88c98d` |

### Versioned launchers

| File | SHA-256 |
|---|---|
| `agents/strategy/__ver_st1_log__/run_strategy1_research_v4_2_strict_test.sh` | `d9ad39e44d85eb97690fbdb3d8323b8a846f0df37d4b1af16125bffbab8d029b` |
| `agents/strategy/__ver_base__/run_base_strategy_v4_1_1_strict.sh` | `3d6b1fe77c3820a4d7fd8f20812512d5e6f813ba0397fcbee982c72e97c98449` |
| `agents/strategy/__ver_adaptive__/run_adaptive_agent_v2_strict.sh` | `9512f001d1f2b8883ae2a776d9d978d992e75150bef42c242fc37c1b847e07fe` |

Versioned launchers are **not** byte-identical to root launchers. They add a repo-root walk-up so they still call live `agents/strategy/*.py` when executed from an archive directory. Agent name, PM2, port, params, version guards, and `run_miner_multi.sh` usage match the root launchers.

---

## Runtime topology

```text
run_strategy1_research_test_multi.sh  →  run_miner_multi.sh  -i sn79-m1  -a 8091  -n Strategy1_Research
run_base_strategy_multi.sh            →  run_miner_multi.sh  -i sn79-m2  -a 8092  -n BaseStrategy
run_adaptive_agent_multi.sh           →  run_miner_multi.sh  -i sn79-m3  -a 8093  -n AdaptiveAgent
```
