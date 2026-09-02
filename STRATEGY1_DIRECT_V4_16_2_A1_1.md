# SN79 Strategy1-Direct Research V4.16.2 A1.1

## Status

Active St6.7 Research candidate. This remains Phase 1 only.
`Strategy1_Research.py` V4.16.2 baseline, `BaseStrategy.py`, `AdaptiveAgent.py`,
and validator trade logic are not changed by A1.1.

## Why A1.1 exists

Agent 68 A1 runtime (`strategy1_research_agent_68_20260902_152314.jsonl`) showed:

- 3,238 requests
- 45,330 ranked candidates
- 37,165 candidates with positive TradingEV
- only 10 lifecycle-eligible candidates
- 9 entry decisions
- 4 fills, all Taker
- 2 completed RTs, both negative
- realized PnL about -0.423
- 0 qualified Kappa books
- no new entry decision after tick 32

The main failure was not screening breadth. It was the A1 economic authority:

1. global strategy latency saturated near a 0.04 per-book LifecycleEV penalty;
2. expected markout was already inside TradingEV, then adverse-selection risk was
   subtracted again;
3. Maker LifecycleEV was fill-weighted upstream and then multiplied by fill
   probability a second time in MakerUtility;
4. Taker reused Maker LifecycleEV and Kappa completion value could make a weak
   immediate crossing look attractive.

## A1.1 authority

```text
Research fast screen / Kappa workload priority
        ↓
selected flat book
        ↓
hard mechanical safety
        ↓
Direct LifecycleEV
  TradingEV
  - dust cost
  - inventory cost
  (latency = telemetry, not hard EV gate)
  (adverse risk = telemetry, markout already priced once)
        ↓
TotalScore rank
        ↓
separate execution economics
  MakerEV = Direct LifecycleEV - Maker fee term
  TakerEV = directional expected move
            - half-spread crossing
            - Taker fee
            - slippage
            - conservative markout buffer
        ↓
Maker / Taker / Skip
        ↓
final contract validation
        ↓
non-flat inventory → existing V4.16 PositionExitController
```

## Important A1.1 invariants

- Positive Kappa/coverage value can rank an economically valid candidate.
- Kappa/coverage cannot make negative Taker economics positive.
- Neutral fallback can never create a Taker entry.
- A wide-spread Book-14-style entry (~16.5 bps half-spread) cannot select Taker
  even with a full-scale directional signal under the bounded A1.1 mapping.
- Latency remains logged and should still be optimized, but it cannot globally
  veto every book.
- Expected markout is charged once in Maker lifecycle economics.
- Existing hard safety, exposure, volume, contract, and PositionExitController
  authorities are preserved.

## Files changed/added

- `agents/strategy/Strategy1_Research_Simple.py`
- `agents/strategy/research_direct_economics.py` (new)
- `run_strategy1_research_simple_multi.sh`
- `tests/test_research_strategy1_direct_a1_1.py`
- `STRATEGY1_DIRECT_V4_16_2_A1_1.md`
- `STRATEGY1_DIRECT_V4_16_2_A1_1_MANIFEST.json`

The old A1 document/manifest remain in the repository as historical artifacts.

## Validation

Focused Direct A1.1 tests:

```text
15 passed
```

Research regression tests:

```text
457 passed
```

Launcher preflight:

```bash
RESEARCH_PREFLIGHT_ONLY=1 ./run_strategy1_research_simple_multi.sh
```

Expected:

```text
85 passed
Strategy1 direct V4.16.2 A1.1 preflight PASS
```

The repository-wide test collection also contains validator tests requiring
runtime dependencies such as `bittensor` and `loky`; those are not available in
the packaging environment and are not part of the Direct preflight.

## Testnet gate

Do not promote to Base from static tests. Run A1.1 on Testnet and verify the new
runtime funnel:

1. ranked candidates
2. positive TradingEV
3. Direct LifecycleEV eligible
4. Maker/Taker/Skip decision counts
5. Maker placements/fills
6. Taker entries and `taker_economic_ev`
7. completed RT count and velocity
8. positive/negative RT
9. realized PnL
10. ONE_AWAY → QUALIFIED conversion
11. qualified-book breadth
12. placements/fill
13. inventory age
14. contract rejects
15. p50/p95 response latency

First hard expectation: the old `37,165 positive TradingEV → 10 eligible` collapse
must disappear without creating repeated negative Taker RTs.

## Workflow

```text
Phase 1: Strategy1-Direct A1.1 Testnet
        ↓
strict runtime PASS
        ↓
freeze Research champion
        ↓
Phase 2: clean isolated BaseStrategy
        ↓
Phase 3: AdaptiveAgent
```
