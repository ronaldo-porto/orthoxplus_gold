# Research Hybrid Exec V4.4 Update

Phase 1 only. BaseStrategy.py and AdaptiveAgent.py were not modified.

## Correctness / execution changes

- Made taker economics authoritative: fill-hazard maker-vs-taker EV may veto an economic taker, but cannot authorize a taker that failed the holding-cost/net-floor gate. Catastrophic hard risk remains the only override.
- Made fast-screen lane allocation the single execution-lane authority. Execution no longer re-applies a second incompatible reserved/overflow cap, and successful lane usage is counted only after a quote is actually placed.
- Coverage ranking now prefers productive positive-EV opportunities while preserving cold uncovered exploration; unknown EV is not treated as known negative EV.
- Session synchronization now fails closed into transition quarantine on exceptions.
- Missing/sparse markout uses the conservative adverse prior in fast realization screening, realization, dust decisions, inventory urgency, and quote adverse-selection fallback.
- Fast REALIZATION-lane urgency now uses cached markout, OFI/adverse risk, Kappa need, and per-book volume-cap headroom.
- Kappa universe now includes zero-observation books observed in state/accounts, preventing inflated breadth summaries.
- Score-EV latency now uses previous-request strategy-latency EWMA instead of markout-evaluation CPU time.
- Session persistence default changed from every request to every 100 ticks/requests; transition saves remain forced.
- Removed synchronous `flush=True` lane console output; JSONL lane telemetry remains.

## Score velocity

- Added bounded empirical ScoreVelocity bonus using completion/activity value, actionable-fill probability, and relative inventory-realization time.
- Tracks per-book and global realization-time medians; cold books shrink to the global prior / neutral factor.
- Feature flag: `research_enable_score_velocity` (default on).
- Weight: `research_score_velocity_weight` (default 0.08, bounded).

## Version / launch

- Research policy version: `hybrid_exec_v4_4`.
- `run_strategy1_research_test_multi.sh` guard/version updated.
- Launch config explicitly sets `research_session_save_every_n=100`, `research_enable_score_velocity=1`, and `research_score_velocity_weight=0.08`.

## Validation in update environment

- `python -m compileall -q agents/strategy`: PASS
- `bash -n run_strategy1_research_test_multi.sh`: PASS
- `pytest -q tests/test_research_*.py`: 280 passed
- Full `pytest -q tests`: collection blocked by unavailable external/runtime packages (`bittensor`, installed `taos` package path, `GenTRX`) in this environment; this is not a Research test failure.

## Live validation still required

Do not promote to BaseStrategy until Testnet logs verify:

- positive/controlled taker PnL,
- higher CoverageVelocity and RoundTripVelocity,
- faster Kappa qualification,
- lower inventory age without worse drawdown/dust,
- no session-transition taker event,
- markout `OK` samples appearing,
- acceptable p95 response latency.
