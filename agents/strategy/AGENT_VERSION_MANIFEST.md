# SN79 Agent Version Manifest

**Date:** 2026-09-01  
**Authoritative Research release:** V4.15.1 / LEAN_AUTHORITY_CLEANUP P2  
**Promotion rule:** Research first. BaseStrategy and AdaptiveAgent remain unchanged until runtime validation.

## Research Agent — ACTIVE CANDIDATE

- **Live:** `agents/strategy/Strategy1_Research.py`
- **Policy:** `lean_authority_cleanup_v4_15_1`
- **Engine:** `lean_authority_cleanup_p2_v4_15_1`
- **Score acquisition:** `total_score_only_v4_15_1`
- **Score authority:** `research_total_score_frontier.py`
- **Execution lanes:** single TOTAL_SCORE lane model (`COVERAGE`, `KAPPA_COMPLETION`, `REALIZATION`)
- **Lifecycle entry:** empirical Maker/Taker realization posterior retained from V4.15.0
- **Mechanical executability cooldown:** retained from V4.15.0
- **Same-request success backfill:** retained from V4.15.0
- **RealNet exit authority:** V4.14.4 preserved
- **TOXIC/NEGATIVE_EV scheduler quarantine:** V4.14.4 preserved
- **Validator source/accounting:** unchanged

## V4.15.1 cleanup contract

1. `TOTAL_SCORE_FRONTIER` is the only score-acquisition authority.
2. Historical cohort / CORE / RECYCLING / CORE_PROBE / density / flywheel / productivity scheduling authorities are physically absent from the live Research path.
3. The old dual execution-lane fallback is physically removed.
4. `score_qualified` is not a second qualification authority. Rolling Kappa state plus TOTAL_SCORE phase owns score acquisition.
5. Execution quality is a soft ranking signal only.
6. A qualified-book exact-minimum or stale-TTL privilege can occur only when the same TOTAL_SCORE authority marks that book due; old `qualified_core` vocabulary and launcher knobs are removed.
7. V4.15.0 adaptive lifecycle realization probability, mechanical reject cooldown, and success-based reserve backfill are preserved.
8. V4.14.4 bounded-loss exit authority and retry quarantine remain authoritative and unchanged.
9. No Base/Adaptive promotion occurs in this release.

## Total-score phases

- **IGNITION:** `<41` rolling Kappa-eligible books — `Coverage/Completion/Realization = 4/3/3 +1 overflow`
- **SURVIVAL:** `41..79` — `2/5/3 +1 overflow`
- **FRONTIER:** `>=80` — `2/4/3 +1 overflow`

## Codebase cleanup

Compared with V4.15.0:

- `Strategy1_Research.py`: **12,911 → 12,349 lines** (`-562`)
- `research_execution_lanes.py`: **949 → 416 lines** (`-533`)
- combined primary Research+scheduler surface: **13,860 → 12,765 lines** (`-1,095`)
- removed obsolete Research modules: `research_cohort.py`, `research_kappa_flywheel.py`, `research_kappa_productivity.py`, `research_capacity_saturation.py`
- removed historical Research snapshot directories from the deployable tree; Base/Adaptive snapshots remain for promotion provenance
- removed stale V4.12.9 breadth-scheduler and qualified-CORE launcher preflights
- removed obsolete V4.14.5 resync tooling from the deployable release

## Verification

- Active Research/component tests: **427 PASS**
- Python compileall (`agents/strategy`, `taos`): **PASS**
- Research launcher `bash -n`: **PASS**
- V4.15.1 clean-authority preflight: **PASS**
- V4.14.4 RealNet exit/scheduler safety preflight: **PASS**
- Research launcher `research_*` assignments: **233 unique / 0 duplicates**
- Live residue scan: no CORE/cohort/flywheel/productivity/breadth-rotation/conversion-pressure score authority references
- Validator `trade.py` SHA256: `137a4a7f26de9395a0028539a95411992c6ed0fa16ddd21682c04838121af0b8` (unchanged)
- BaseStrategy SHA256: `76941d948ce3bac0057e9ef788a3c6e93dab4f4bc79ce7b5b00733a67211f0cb` (unchanged)
- AdaptiveAgent SHA256: `6421fe403c4a51cb80ffd67bc37cd92be9dcbe646968819ceeabd42f1eb234c1` (unchanged)

The repository-wide `pytest -q` is not a release gate in this minimal analysis environment because optional/runtime dependencies including `bittensor`, `transformers`, `pyarrow`, and `loky` are not installed. The Research-focused suite is dependency-isolated and passes.

## Known remaining issue

`BaseStrategy.py` still subclasses the live `Strategy1_Research`, so its `base_v4_13_9_champion` label is not behaviorally isolated. This is intentionally **not** repaired in Research Phase 1. If V4.15.1 becomes the runtime champion, Phase 2 must create a genuinely frozen Base implementation before Phase 3 rebases Adaptive.
