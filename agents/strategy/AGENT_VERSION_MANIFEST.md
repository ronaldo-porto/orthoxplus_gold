# SN79 Agent Version Manifest

**Date:** 2026-08-27  
**Authoritative Research release:** V4.12.17 / Kappa Flywheel Foundation  
**Rule:** Research-only Kappa breadth/density + inventory-liveness foundation. BaseStrategy and AdaptiveAgent remain frozen.

## Research Agent

- **Live:** `agents/strategy/Strategy1_Research.py`
- **Policy:** `kappa_flywheel_v4_12_17`
- **Lanes:** `execution_lanes_v6_kappa_flywheel`
- **Kappa flywheel helper:** `kappa_flywheel_v4_12_17`
- **Inventory liveness helper:** `inventory_liveness_v4_12_17`
- **Contract guard:** `authoritative_l1_contract_guard_v4_12_14` (unchanged)
- **ONE_AWAY base stale helper:** `one_away_stale_ttl_v4_12_11` (preserved)
- **ONE_AWAY velocity-stale helper:** `one_away_velocity_stale_ttl_v4_12_17`
- **Unified exit:** `bounded_stale_bridge_v4_12_10` (unchanged)
- **Archive:** `agents/strategy/__ver_st1_log__/Strategy1_Research_v4_12_17.py`
- **Archived liveness:** `agents/strategy/__ver_st1_log__/research_inventory_liveness_v4_12_17.py`
- **Archived lanes:** `agents/strategy/__ver_st1_log__/research_execution_lanes_v4_12_17.py`
- **Archived hysteresis:** `agents/strategy/__ver_st1_log__/research_quote_hysteresis_v4_12_17.py`
- **Archived flywheel:** `agents/strategy/__ver_st1_log__/research_kappa_flywheel_v4_12_17.py`
- **Versioned launcher:** `agents/strategy/__ver_st1_log__/run_strategy1_research_v4_12_17_test.sh`

### V4.12.17 contract

- `QUALIFIED` and `ONE_AWAY` remain protected from the new liveness loss subsidy.
- `TWO_AWAY` / `UNCOVERED` use `-8 bps` normal and immutable `-12 bps` absolute liveness floors.
- Crossing from below `-8` while still at/above `-12` opens the hard rescue evaluation immediately.
- `PARKED` is a classification; the old value `6` is telemetry warning only, not a parking veto.
- Hard total risk remains `12` open books and `3.0 BASE` absolute exposure by default.
- Liveness aggressive Maker uses actual post-only touch; a floor-violating touch is Taker-rescued only when independently authorized, otherwise parked.
- Kappa flywheel phases: `<41 BOOTSTRAP`, `41..79 BREADTH`, `>=80 DENSITY`.
- Density targets: `6 / 12 / 50`; core limits: `8 / 24 / 48`.
- At least one fresh exploration slot remains fail-open under true total-headroom pressure.
- Rolling realized-PnL evidence is persisted/restored and is no longer inferred from the `recent_pnl` EMA default.
- Stable ONE_AWAY touch cap remains `1.5 bps`; velocity-STALE ONE_AWAY override is capped at `250 ms`.
- Maker fee/rebate is an explicit equal-EV Kappa scheduler tie-breaker without double-counting existing fee-aware EV.

## BaseStrategy

- **Policy:** `base_v4_4_champion`
- **Status:** byte-for-byte unchanged from V4.12.16 input.

## AdaptiveAgent

- **Policy:** `adaptive_v3_hjb_shadow`
- **Status:** byte-for-byte unchanged from V4.12.16 input.

## Verification

- Research: **409 passed / 0 failed**
- Base/Adaptive: **133 passed / 0 failed**
- Shared strategy: **86 passed / 0 failed**
- Total strategy-focused: **628 passed / 0 failed**
- Focused V4.12.17 tests: **12 passed / 0 failed**
- Root/versioned launcher syntax: **PASS**
- Full `RESEARCH_PREFLIGHT_ONLY=1` launcher run: **PASS**
- Runtime promotion: **NOT claimed; Kappa flywheel live verification pending**

## SHA-256

| File | SHA-256 |
|---|---|
| `agents/strategy/Strategy1_Research.py` | `cb529f1201ae73e44486ab54f49cf2be4ec0a817445ae229a0be194b2e3342fa` |
| `agents/strategy/research_inventory_liveness.py` | `9414a40d8a63df592ede13ee68e5e9d00f0c03565ab467a46efaa852e979b637` |
| `agents/strategy/research_execution_lanes.py` | `54effb609aa6a92659e4cca931731a17eabae81a16ce2d90739a6f250653e37c` |
| `agents/strategy/research_quote_hysteresis.py` | `9683c2c82347b0f43e3b312bac1795754deaf0dbb79771a71d000feb048b6179` |
| `agents/strategy/research_kappa_flywheel.py` | `d79fe9598391b95fd1229931dd4769a6470f62c298736603ac5e688d7aa41737` |
| `agents/strategy/research_contract_guard.py` | `e3e61783d2a17f370dad62255bc468e23d7ae7ce42f20b9a7cd1846145cdf1d0` |
| `agents/strategy/research_unified_exit.py` | `da815ca6e2e8f4909d84f5fcdb77d3ab71d2475b0b044ccf73c12ca92644837a` |
| `agents/strategy/BaseStrategy.py` | `13a56d355558eec24df86dc34ea888524eeced8a575b19fcb3b27bffc55a3bf1` |
| `agents/strategy/AdaptiveAgent.py` | `3e75e6abce4d6a678f4976f10e4b30b5fa8be35f57a8743226a795f841c53448` |
| `run_strategy1_research_test_multi.sh` | `2c1a82da1a45c0879624c933517af259a5be1d81699356f48ab10004dbb75be6` |
| `run_miner_multi.sh` | `a5d2b63b2402835bd06f231677f4ba5679549529fc0521b7f62599076d88c98d` |
