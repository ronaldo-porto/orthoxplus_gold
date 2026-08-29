# SN79 Agent Version Manifest

**Date:** 2026-08-30  
**Authoritative Research release:** V4.14.5 / TOTAL_SCORE_FRONTIER P1  
**Promotion rule:** Research first. BaseStrategy and AdaptiveAgent remain frozen until Testnet/RealNet runtime evidence satisfies the V4.14.5 promotion gates.

## Research Agent — ACTIVE

- **Live:** `agents/strategy/Strategy1_Research.py`
- **Policy:** `total_score_frontier_v4_14_5`
- **Engine:** `lean_engine_p1_total_score_frontier_v4_14_5`
- **Score authority:** `research_total_score_frontier.py`
- **Execution lanes:** `execution_lanes_v8_total_score_single_authority`
- **Score acquisition:** `total_score_acquisition_v4_14_5`
- **RealNet exit authority:** V4.14.4 preserved
- **Scheduler retry quarantine:** V4.14.4 preserved
- **Validator source/accounting:** unchanged

### V4.14.5 authority contract

1. Hard risk and inventory realization remain above score scheduling.
2. Flat-book score scheduling has exactly one live authority: `total_score_due`.
3. Live CORE, RECYCLING, CORE_PROBE and `density_due` flags are forced non-authoritative.
4. Historical 6/12/50 observation densification is not a live scheduling target.
5. Score phases are only:
   - IGNITION: `<41` Kappa-eligible books
   - SURVIVAL: `41..79`
   - FRONTIER: `>=80`
6. Fixed lane budgets prevent completion demand from collapsing fresh coverage:
   - IGNITION `4/3/3 +1 overflow`
   - SURVIVAL `2/5/3 +1 overflow`
   - FRONTIER `2/4/3 +1 overflow`
7. Critical expiry may become `total_score_due`; non-critical legacy refresh cannot bypass the authority.
8. Hidden exact-min and qualified stale-TTL privileges are bound to `_research_total_score_due_ids`, not historical productivity CORE membership.
9. Economic hard gates and V4.14.4 TOXIC/NEGATIVE_EV retry rotation remain authoritative.
10. 80 is the full-breadth boundary, not a command to qualify every book. Above 80, additional weak breadth is not automatically rewarded.

### Versioned snapshots

- `agents/strategy/__ver_st1_log__/Strategy1_Research_v4_14_5.py`
- `agents/strategy/__ver_st1_log__/research_execution_lanes_v4_14_5.py`
- `agents/strategy/__ver_st1_log__/research_total_score_frontier_v4_14_5.py`
- `agents/strategy/__ver_st1_log__/run_strategy1_research_v4_14_5_test.sh`

### Detailed learner handoff

Read first:

`SN79_ST65_RESEARCH_V4_14_5_TOTAL_SCORE_FRONTIER_P1.md`

This document defines the scoring math, authority model, expected runtime behavior, telemetry, validation gates, failure modes, and rollback conditions.

## BaseStrategy — FROZEN

- **Policy:** `base_v4_13_9_champion`
- **Kappa productivity policy:** `simplified_kappa_productivity_v4_13_9`
- **Status:** not promoted to V4.14.5 in this release.

## AdaptiveAgent — FROZEN

- **Adaptive version:** `adaptive_v4_13_9_realtime`
- **Base policy inherited:** `base_v4_13_9_champion`
- **Status:** not promoted to V4.14.5 in this release.

## Verification

- Active Research suite: **519 / 519 PASS**
- Python compilation: **PASS**
- Active strategy compileall: **PASS**
- Research launcher shell syntax: **PASS**
- V4.14.5 launcher preflight: **PASS**
- V4.14.4 RealNet safety preflight: **PASS**
- Runtime promotion: **NOT YET CLAIMED** — Testnet/RealNet evidence required.

## Deployment objective

The observed top-agent TOTAL score is above 25 on the dashboard scale. V4.14.5 is architected to remove the current zero/low-score breadth bottleneck and target a sustained **20+ TOTAL score**, with **25+** as the competitive runtime objective. This is not a deterministic guarantee because the final score depends on peer-relative floor, Pareto rank, EMA history, and live execution quality.
