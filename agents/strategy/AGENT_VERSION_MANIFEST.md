# SN79 Agent Version Manifest

**Date:** 2026-08-27  
**Authoritative Research release:** V4.13.1 / Kappa Productivity Scheduler Correction  
**Rule:** Research-only scheduler correction on top of V4.13 and verified V4.12.18 inventory safety. BaseStrategy and AdaptiveAgent remain frozen.

## Research Agent

- **Live:** `agents/strategy/Strategy1_Research.py`
- **Policy:** `simplified_kappa_productivity_v4_13_1`
- **Productivity helper:** `simplified_kappa_productivity_v4_13_1`
- **Inventory liveness:** V4.12.18 preserved
- **Contract guard:** `authoritative_l1_contract_guard_v4_12_14` preserved
- **Unified exit:** `bounded_stale_bridge_v4_12_10` preserved
- **Velocity-STALE ONE_AWAY TTL:** 250 ms preserved
- **Archive:** `agents/strategy/__ver_st1_log__/Strategy1_Research_v4_13_1.py`
- **Archived productivity:** `agents/strategy/__ver_st1_log__/research_kappa_productivity_v4_13_1.py`
- **Archived lanes:** `agents/strategy/__ver_st1_log__/research_execution_lanes_v4_13_1.py`
- **Archived candidate screen:** `agents/strategy/__ver_st1_log__/research_candidate_screen_v4_13.py`
- **Archived hysteresis:** `agents/strategy/__ver_st1_log__/research_quote_hysteresis_v4_13.py`
- **Versioned launcher:** `agents/strategy/__ver_st1_log__/run_strategy1_research_v4_13_1_test.sh`

### V4.13.1 contract

- Hard order-sink evidence is evaluated before the sparse UNKNOWN gate: >=20 Maker quotes with zero fresh nonzero RTs or <5% Maker fill conversion is immediately INEFFICIENT.
- Productivity burden uses fresh V4.13+ nonzero RTs so restored lifetime RT history cannot make fresh quote churn look efficient.
- One qualified book with its first clean fresh positive RT may enter a single RECYCLING bootstrap slot; full CORE still requires three fresh nonzero RTs.
- Completion selection explicitly reserves one slot for that recycling bridge when completion capacity exists.
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

- Research: **434 passed / 0 failed**
- Base/Adaptive: **133 passed / 0 failed**
- Shared strategy: **90 passed / 0 failed**
- Total strategy-focused: **657 passed / 0 failed**
- Focused V4.13.1: **14 passed / 0 failed**
- Python compile: **PASS**
- Root launcher syntax/preflight: **PASS**
- Runtime promotion: **NOT claimed; Testnet productivity/latency validation pending**

## SHA-256

| File | SHA-256 |
|---|---|
| `agents/strategy/Strategy1_Research.py` | `1e9439e38bb860870aece6f3a4ddc7ac16a3af3b45c261ee9c05fdd58b7b455f` |
| `agents/strategy/research_kappa_productivity.py` | `59af88a8b2a4fff7459bd6f30470fa380e53134bab88589e35038d823f18c7b4` |
| `agents/strategy/research_execution_lanes.py` | `ec7053d725a1648ab9c4801f59d408929e415bd3bc77e69dce0f58e2339b281c` |
| `agents/strategy/research_candidate_screen.py` | `a34b5d9d6f29d063a85391c6afa1b53fa28fa7e4c2e910d9f2378c3f865a935b` |
| `agents/strategy/research_quote_hysteresis.py` | `4a18ddcafa105399e9c36841db6e9be98d17e343d010256607cd31096d9aa172` |
| `agents/strategy/research_inventory_liveness.py` | `c9f24d624171e028a4d2c215aea5fea2cffcb9390d79ccfda44b8b4dbaa86596` |
| `agents/strategy/research_contract_guard.py` | `e3e61783d2a17f370dad62255bc468e23d7ae7ce42f20b9a7cd1846145cdf1d0` |
| `agents/strategy/research_unified_exit.py` | `da815ca6e2e8f4909d84f5fcdb77d3ab71d2475b0b044ccf73c12ca92644837a` |
| `agents/strategy/BaseStrategy.py` | `13a56d355558eec24df86dc34ea888524eeced8a575b19fcb3b27bffc55a3bf1` |
| `agents/strategy/AdaptiveAgent.py` | `3e75e6abce4d6a678f4976f10e4b30b5fa8be35f57a8743226a795f841c53448` |
| `run_strategy1_research_test_multi.sh` | `fb68e7caf011183def867a9f5d23b772d9057f418d8ca7e751d32894edbdbf3e` |

## St6.4 emergency promotion — 2026-08-27

- Research: `simplified_kappa_productivity_v4_13_8` (frozen)
- Base champion: `base_v4_13_8_champion`
- Adaptive: `adaptive_v4_13_8_realtime`
- Promotion: Research V4.13.8 -> Base V4.13.8 -> Adaptive realtime
