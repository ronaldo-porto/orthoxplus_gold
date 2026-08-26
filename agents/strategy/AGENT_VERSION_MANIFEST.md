# SN79 Agent Version Manifest

**Date:** 2026-08-26  
**Authoritative Research release:** V4.12.14 / Authoritative-L1 Contract Guard  
**Rule:** Research-only LazyBooks/L1 correctness fix. BaseStrategy and AdaptiveAgent remain frozen.

## Research Agent

- **Live:** `agents/strategy/Strategy1_Research.py`
- **Policy:** `authoritative_l1_guard_v4_12_14`
- **Contract guard:** `authoritative_l1_contract_guard_v4_12_14`
- **ONE_AWAY TTL helper:** `one_away_stale_ttl_v4_12_11` (unchanged)
- **Unified exit:** `bounded_stale_bridge_v4_12_10` (unchanged)
- **Archive:** `agents/strategy/__ver_st1_log__/Strategy1_Research_v4_12_14.py`
- **Archived contract helper:** `agents/strategy/__ver_st1_log__/research_contract_guard_v4_12_14.py`
- **Versioned launcher:** `agents/strategy/__ver_st1_log__/run_strategy1_research_v4_12_14_test.sh`

### V4.12.14 contract

- Root cause fixed: `lazy_load=1` makes `state.books` a `LazyBooks` `Mapping`, not necessarily a built-in `dict`.
- Contract guard now resolves current L1 from the same authoritative `state.books` Mapping consumed by the parent quote builder.
- Submitted-quote lifecycle registration uses the same Mapping-safe L1 lookup, restoring `mid`, `spread`, touch distance and fill-hazard snapshot inputs under lazy loading.
- No telemetry/cache price fallback is used for safe reprice; missing true L1 still fails closed.
- V4.12.13 pending state, 1->2->4->8 cooldown, 1..3 tick cushion and 512-tick hard lifetime are unchanged.
- Market/Taker instructions are never modified.
- V4.12.11 ONE_AWAY behavior and V4.12.10 Taker bridge are unchanged.
- `candidate_count=10`, `max_open_books=6`, score target `88`, p95 target `120 ms` unchanged.

## BaseStrategy

- **Policy:** `base_v4_4_champion`
- **Status:** byte-for-byte unchanged.

## AdaptiveAgent

- **Policy:** `adaptive_v3_hjb_shadow`
- **Status:** byte-for-byte unchanged.

## Verification

- Research: **378 passed / 0 failed**
- Base/Adaptive: **126 passed / 0 failed**
- Shared strategy: **93 passed / 0 failed**
- Total strategy-focused: **597 passed / 0 failed**
- Focused LazyBooks/contract tests: **19 passed / 0 failed**
- Python compile: **PASS**
- Research/Base/Adaptive launcher syntax: **PASS**
- V4.12.10 + V4.12.11 + V4.12.14 preflight APIs: **PASS**
- Runtime promotion: **NOT claimed; short authoritative-L1 verification pending**

## SHA-256

| File | SHA-256 |
|---|---|
| `agents/strategy/Strategy1_Research.py` | `a7a7cdb2af06291bfab3c1a4be853e3232be7eaf2ab44423793d0c95d373f8bd` |
| `agents/strategy/research_contract_guard.py` | `e3e61783d2a17f370dad62255bc468e23d7ae7ce42f20b9a7cd1846145cdf1d0` |
| `agents/strategy/research_entry_size.py` | `7d5757ff93837ed06cce0ecdf3bac7c52355d6c94ddcc1b506abe38adb2dc3a5` |
| `agents/strategy/research_quote_hysteresis.py` | `c75872f9018b79396cd0cf7f6adaa8ae73543583cd3edcd4796867967d1df4d6` |
| `agents/strategy/research_unified_exit.py` | `da815ca6e2e8f4909d84f5fcdb77d3ab71d2475b0b044ccf73c12ca92644837a` |
| `agents/strategy/BaseStrategy.py` | `13a56d355558eec24df86dc34ea888524eeced8a575b19fcb3b27bffc55a3bf1` |
| `agents/strategy/AdaptiveAgent.py` | `3e75e6abce4d6a678f4976f10e4b30b5fa8be35f57a8743226a795f841c53448` |
| `run_strategy1_research_test_multi.sh` | `c8196607f56c4df08b4a508e051d7d71aa8e7b8eaeb6f87ab245b1933d6238c9` |
