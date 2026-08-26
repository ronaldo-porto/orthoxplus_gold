# SN79 Agent Version Manifest

**Date:** 2026-08-27  
**Authoritative Research release:** V4.12.15 / Inventory Liveness  
**Rule:** Research-only inventory-liveness fix. V4.12.14 contract guard, BaseStrategy and AdaptiveAgent remain frozen.

## Research Agent

- **Live:** `agents/strategy/Strategy1_Research.py`
- **Policy:** `inventory_liveness_v4_12_15`
- **Inventory liveness helper:** `inventory_liveness_v4_12_15`
- **Contract guard:** `authoritative_l1_contract_guard_v4_12_14` (unchanged)
- **ONE_AWAY TTL helper:** `one_away_stale_ttl_v4_12_11` (unchanged)
- **Unified exit:** `bounded_stale_bridge_v4_12_10` (unchanged)
- **Archive:** `agents/strategy/__ver_st1_log__/Strategy1_Research_v4_12_15.py`
- **Archived liveness helper:** `agents/strategy/__ver_st1_log__/research_inventory_liveness_v4_12_15.py`
- **Versioned launcher:** `agents/strategy/__ver_st1_log__/run_strategy1_research_v4_12_15_test.sh`

### V4.12.15 contract

- `QUALIFIED` and `ONE_AWAY` cannot receive V4.12.15 bounded-loss subsidy.
- `TWO_AWAY` / `UNCOVERED` Maker rescue arms at 3 failed exits or 8 ticks.
- Bounded Taker rescue arms at 8 failed exits or 16 ticks.
- Default Maker / soft-Taker / hard-Taker floors: `-4 / -8 / -12 bps`.
- Absolute liveness rescue floor cannot be widened beyond `-12 bps` by config.
- Rescue Taker requires adverse evidence and Taker EV > WaitEV + 0.5 bps.
- Missed rescue windows park inventory instead of consuming active acquisition slots.
- Active / parked / total caps: `6 / 6 / 12`; total absolute BASE cap: `3.0`.
- Parked refresh default 20 ticks, material-touch threshold 8 bps.
- V4.12.14 authoritative-L1 contract guard remains byte-for-byte unchanged.
- Normal profitable Taker, ONE_AWAY completion logic, alpha/entry, scheduler/ranking and sizing are unchanged.

## BaseStrategy

- **Policy:** `base_v4_4_champion`
- **Status:** byte-for-byte unchanged.

## AdaptiveAgent

- **Policy:** `adaptive_v3_hjb_shadow`
- **Status:** byte-for-byte unchanged.

## Verification

- Research: **391 passed / 0 failed**
- Base/Adaptive: **126 passed / 0 failed**
- Shared strategy: **93 passed / 0 failed**
- Total strategy-focused: **610 passed / 0 failed**
- Focused V4.12.15 inventory-liveness tests: **13 passed / 0 failed**
- Python compile: **PASS**
- Research launcher syntax/preflight: **PASS**
- V4.12.10 + V4.12.11 + V4.12.14 + V4.12.15 preflight APIs: **PASS**
- Runtime promotion: **NOT claimed; inventory-liveness verification pending**

## SHA-256

| File | SHA-256 |
|---|---|
| `agents/strategy/Strategy1_Research.py` | `2007ef93a9ba75b319721c8ac339f30b5a8372748d03523c58c95af68b602d6d` |
| `agents/strategy/research_inventory_liveness.py` | `36d9a96fca1b2b7a8486125189f241f3d3f495f0357d633b95366abac02832ea` |
| `agents/strategy/research_contract_guard.py` | `e3e61783d2a17f370dad62255bc468e23d7ae7ce42f20b9a7cd1846145cdf1d0` |
| `agents/strategy/research_entry_size.py` | `7d5757ff93837ed06cce0ecdf3bac7c52355d6c94ddcc1b506abe38adb2dc3a5` |
| `agents/strategy/research_quote_hysteresis.py` | `c75872f9018b79396cd0cf7f6adaa8ae73543583cd3edcd4796867967d1df4d6` |
| `agents/strategy/research_unified_exit.py` | `da815ca6e2e8f4909d84f5fcdb77d3ab71d2475b0b044ccf73c12ca92644837a` |
| `agents/strategy/BaseStrategy.py` | `13a56d355558eec24df86dc34ea888524eeced8a575b19fcb3b27bffc55a3bf1` |
| `agents/strategy/AdaptiveAgent.py` | `3e75e6abce4d6a678f4976f10e4b30b5fa8be35f57a8743226a795f841c53448` |
| `run_strategy1_research_test_multi.sh` | `6ce9a34c7a145b7c964f96e4727c818b5682b75ec50dbce677486bd8c1593d3a` |
