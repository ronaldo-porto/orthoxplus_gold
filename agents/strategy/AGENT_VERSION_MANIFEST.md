# SN79 Agent Version Manifest

**Date:** 2026-08-27  
**Authoritative Research release:** V4.13 / Simplified Kappa Productivity Engine  
**Rule:** Research-only scheduler/hot-path simplification on top of verified V4.12.18 inventory safety. BaseStrategy and AdaptiveAgent remain frozen.

## Research Agent

- **Live:** `agents/strategy/Strategy1_Research.py`
- **Policy:** `simplified_kappa_productivity_v4_13`
- **Productivity helper:** `simplified_kappa_productivity_v4_13`
- **Inventory liveness:** V4.12.18 preserved
- **Contract guard:** `authoritative_l1_contract_guard_v4_12_14` preserved
- **Unified exit:** `bounded_stale_bridge_v4_12_10` preserved
- **Velocity-STALE ONE_AWAY TTL:** 250 ms preserved
- **Archive:** `agents/strategy/__ver_st1_log__/Strategy1_Research_v4_13.py`
- **Archived productivity:** `agents/strategy/__ver_st1_log__/research_kappa_productivity_v4_13.py`
- **Archived lanes:** `agents/strategy/__ver_st1_log__/research_execution_lanes_v4_13.py`
- **Archived candidate screen:** `agents/strategy/__ver_st1_log__/research_candidate_screen_v4_13.py`
- **Archived hysteresis:** `agents/strategy/__ver_st1_log__/research_quote_hysteresis_v4_13.py`
- **Versioned launcher:** `agents/strategy/__ver_st1_log__/run_strategy1_research_v4_13_test.sh`

### V4.13 contract

- Kappa eligibility is `NEW / BUILDING / QUALIFIED / CORE`; qualification is not the end of trading.
- Productive qualified books may be continuously recycled as CORE.
- Productivity ranking penalizes placements/RT, poor fill conversion, losses, and contract rejects; rebate alone cannot dominate.
- A legacy/restored book without fresh V4.13 quote/fill burden remains `UNKNOWN`, never falsely CORE.
- Known `INEFFICIENT` books are demoted ahead of sticky cohort membership.
- All-book Stage-1 fingerprint uses top-of-book only; event streams are not traversed there.
- Persistent Maker mode ignores signal-only churn but still reprices for price, TTL, inventory, toxicity, economic or hard-safety changes.
- Post-only safety gap defaults to 2 ticks; hysteresis material-price threshold defaults to 3 ticks.
- Initial active concurrency remains 6 for controlled validation.

## BaseStrategy

- **Policy:** `base_v4_4_champion`
- **Status:** byte-for-byte unchanged from V4.12.18.

## AdaptiveAgent

- **Policy:** `adaptive_v3_hjb_shadow`
- **Status:** byte-for-byte unchanged from V4.12.18.

## Verification

- Research: **431 passed / 0 failed**
- Base/Adaptive: **133 passed / 0 failed**
- Shared strategy: **90 passed / 0 failed**
- Total strategy-focused: **654 passed / 0 failed**
- Focused V4.13: **11 passed / 0 failed**
- Python compile: **PASS**
- Root launcher syntax/preflight: **PASS**
- Runtime promotion: **NOT claimed; Testnet productivity/latency validation pending**

## SHA-256

| File | SHA-256 |
|---|---|
| `agents/strategy/Strategy1_Research.py` | `fc6d7211235055e13fd47082d92aaa5f301c63d6bf7f7eda8dc2f872f9121b9c` |
| `agents/strategy/research_kappa_productivity.py` | `928b4857917619fc359e47564d930c64eed57e9aa5799e31809b62c6731d2c2b` |
| `agents/strategy/research_execution_lanes.py` | `f19b2a24c54279ee2f6290b66bc442e5b6464ae74ea1dfd11d5060b73d8facc2` |
| `agents/strategy/research_candidate_screen.py` | `a34b5d9d6f29d063a85391c6afa1b53fa28fa7e4c2e910d9f2378c3f865a935b` |
| `agents/strategy/research_quote_hysteresis.py` | `4a18ddcafa105399e9c36841db6e9be98d17e343d010256607cd31096d9aa172` |
| `agents/strategy/research_inventory_liveness.py` | `c9f24d624171e028a4d2c215aea5fea2cffcb9390d79ccfda44b8b4dbaa86596` |
| `agents/strategy/research_contract_guard.py` | `e3e61783d2a17f370dad62255bc468e23d7ae7ce42f20b9a7cd1846145cdf1d0` |
| `agents/strategy/research_unified_exit.py` | `da815ca6e2e8f4909d84f5fcdb77d3ab71d2475b0b044ccf73c12ca92644837a` |
| `agents/strategy/BaseStrategy.py` | `13a56d355558eec24df86dc34ea888524eeced8a575b19fcb3b27bffc55a3bf1` |
| `agents/strategy/AdaptiveAgent.py` | `3e75e6abce4d6a678f4976f10e4b30b5fa8be35f57a8743226a795f841c53448` |
| `run_strategy1_research_test_multi.sh` | `c4f730ca3c84dba3426d720e6ba6fae7759fea1cb336d45c36fc667a396596a3` |
