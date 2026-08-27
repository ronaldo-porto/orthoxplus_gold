# SN79 Agent Version Manifest

**Date:** 2026-08-27  
**Authoritative Research release:** V4.12.18 / Inventory-State Decoupling  
**Rule:** Research-only capacity/loss-authority decoupling on top of the Kappa Flywheel. BaseStrategy and AdaptiveAgent remain frozen.

## Research Agent

- **Live:** `agents/strategy/Strategy1_Research.py`
- **Policy:** `inventory_state_decoupling_v4_12_18`
- **Lanes:** `execution_lanes_v7_inventory_decoupled`
- **Kappa flywheel helper:** `kappa_flywheel_v4_12_18`
- **Inventory liveness helper:** `inventory_state_decoupling_v4_12_18`
- **Contract guard:** `authoritative_l1_contract_guard_v4_12_14` (unchanged)
- **ONE_AWAY base stale helper:** `one_away_stale_ttl_v4_12_11` (preserved)
- **ONE_AWAY velocity-stale helper:** `one_away_velocity_stale_ttl_v4_12_17` (250 ms preserved)
- **Unified exit:** `bounded_stale_bridge_v4_12_10` (unchanged)
- **Archive:** `agents/strategy/__ver_st1_log__/Strategy1_Research_v4_12_18.py`
- **Archived liveness:** `agents/strategy/__ver_st1_log__/research_inventory_liveness_v4_12_18.py`
- **Archived lanes:** `agents/strategy/__ver_st1_log__/research_execution_lanes_v4_12_18.py`
- **Archived hysteresis:** `agents/strategy/__ver_st1_log__/research_quote_hysteresis_v4_12_18.py`
- **Archived flywheel:** `agents/strategy/__ver_st1_log__/research_kappa_flywheel_v4_12_18.py`
- **Versioned launcher:** `agents/strategy/__ver_st1_log__/run_strategy1_research_v4_12_18_test.sh`

### V4.12.18 contract

- `KAPPA_BLOCKED_LOSS` may block a loss realization but cannot block parking/capacity release.
- `ONE_AWAY` / `QUALIFIED` remain excluded from the new bounded-loss subsidy but may enter `PARKED_PROTECTED`.
- `TWO_AWAY` / `UNCOVERED` retain V4.12.17 bounded liveness rescue and `PARKED_LIVENESS`.
- A new park cancels known resting orders for that book before holding.
- Every parked Maker refresh is recomputed at current legal post-only touch and submitted only at/above its stored protected floor.
- Default protected park arming: 4 failed exits or 8 ticks; default parked refresh: 25 ticks.
- Active capacity counts only ACTIVE nonflat books; total risk remains bounded by 12 open books / 3.0 absolute BASE by default.
- Kappa observation eligibility is separate from PnL-authority completeness. Restored books with incomplete new-ledger history remain Flywheel candidates with `FULL/PARTIAL/UNKNOWN` confidence.
- PnL confidence multipliers: 1.00 / 0.85 / 0.70. Missing PnL history is never fabricated.
- At least one fresh exploration slot remains fail-open while total hard exposure permits another position.
- Stable ONE_AWAY 1.5 bps touch cap and velocity-STALE 250 ms TTL remain unchanged.

## BaseStrategy

- **Policy:** `base_v4_4_champion`
- **Status:** byte-for-byte unchanged from V4.12.17.

## AdaptiveAgent

- **Policy:** `adaptive_v3_hjb_shadow`
- **Status:** byte-for-byte unchanged from V4.12.17.

## Verification

- Research: **420 passed / 0 failed**
- Base/Adaptive: **133 passed / 0 failed**
- Shared strategy: **90 passed / 0 failed**
- Total strategy-focused: **643 passed / 0 failed**
- Focused V4.12.18 tests: **11 passed / 0 failed**
- Root/versioned launcher syntax: **PASS**
- Full `RESEARCH_PREFLIGHT_ONLY=1` launcher run: **PASS**
- Runtime promotion: **NOT claimed; inventory-state decoupling live verification pending**

## SHA-256

| File | SHA-256 |
|---|---|
| `agents/strategy/Strategy1_Research.py` | `c7ec182f2b49680d4bd9385e28ff0664b18cdc5983a10df50481e04fe8ca377d` |
| `agents/strategy/research_inventory_liveness.py` | `c9f24d624171e028a4d2c215aea5fea2cffcb9390d79ccfda44b8b4dbaa86596` |
| `agents/strategy/research_execution_lanes.py` | `70ae62d3d2a72c1fd842b6ad675e33885b7d6764dc7405e8b2b4c361960c3c29` |
| `agents/strategy/research_quote_hysteresis.py` | `9683c2c82347b0f43e3b312bac1795754deaf0dbb79771a71d000feb048b6179` |
| `agents/strategy/research_kappa_flywheel.py` | `8cbc57d0989b82d26d6e083d21d9c39c154f32e5764141734330441bfbcf7942` |
| `agents/strategy/research_contract_guard.py` | `e3e61783d2a17f370dad62255bc468e23d7ae7ce42f20b9a7cd1846145cdf1d0` |
| `agents/strategy/research_unified_exit.py` | `da815ca6e2e8f4909d84f5fcdb77d3ab71d2475b0b044ccf73c12ca92644837a` |
| `agents/strategy/BaseStrategy.py` | `13a56d355558eec24df86dc34ea888524eeced8a575b19fcb3b27bffc55a3bf1` |
| `agents/strategy/AdaptiveAgent.py` | `3e75e6abce4d6a678f4976f10e4b30b5fa8be35f57a8743226a795f841c53448` |
| `run_strategy1_research_test_multi.sh` | `76aa009298a7fd9374b7c3fdff58641c2a5aa215d6211f556d01df7c83dfc920` |
| `run_miner_multi.sh` | `a5d2b63b2402835bd06f231677f4ba5679549529fc0521b7f62599076d88c98d` |
