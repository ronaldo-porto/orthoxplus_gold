# SN79 Strategy1-Direct Research V4.16.2 A1

## Purpose

This is a separate Research candidate. It does not overwrite the proven
`simplified_hybrid_authority_v4_16_2` baseline.

Target:

> Strategy1 directness + V4.16.2 lifecycle/exit/contract correctness.

## Active workflow

```text
Research fast screen / Kappa workload priority
        ↓
selected books
        ↓
hard mechanical safety
        ↓
V4.16.2 LifecycleEV
        ↓
TotalScore rank
        ↓
MakerUtility / TakerUtility / Skip
        ↓
entry
        ↓
non-flat inventory
        ↓
existing V4.16 PositionExitController
MakerExit / TakerExit / Wait
        ↓
final authoritative contract validation
        ↓
RT / PnL / Kappa learning
```

## Removed from the direct entry hot path

- separate maintenance economic authority
- separate directional-alpha entry authority
- lane attempt/success execution caps
- stale-maker rescue entry authority
- positive-maker veto entry authority
- adaptive TTL as an entry authority
- quote hysteresis as an entry authority
- duplicate expected-PnL gate after LifecycleEV
- duplicate fill-probability hard gate after LifecycleEV
- old RED/avoid-list economics as a hard entry gate

The original helpers remain in the repository because the baseline Research
agent still uses them and because the candidate intentionally preserves the
existing Research learning/session infrastructure.

## Preserved

- original Strategy1 signal/features
- V4.16.2 LifecycleEV including Taker-exit posterior/cost wiring
- TotalScore/Kappa priority
- Maker/Taker/Skip execution chooser
- corrected Research inventory representation
- V4.16 PositionExitController
- fill/markout/Taker-exit learning state
- session persistence
- volume/inventory/exposure safety
- final contract sanitization/validation
- RT timing and Research logging infrastructure

## Files added

- `agents/strategy/Strategy1_Research_Simple.py`
- `run_strategy1_research_simple_multi.sh`
- `tests/test_research_strategy1_direct_a1.py`

The root V4.16.2 launcher was line-ending-normalized from CRLF to LF only; its
strategy behavior was not changed.

## Static complexity

Baseline source:

- `Strategy1.py`: ~1,914 lines, 53 Strategy1 methods
- `Strategy1_Research.py`: ~12,888 lines, 226 Research methods

Direct candidate overlay:

- `Strategy1_Research_Simple.py`: 527 lines, 5 overridden methods

The candidate deliberately reuses Research infrastructure instead of copying
12k lines into another agent.

## Preflight

```bash
RESEARCH_PREFLIGHT_ONLY=1 ./run_strategy1_research_simple_multi.sh
```

Expected result:

```text
77 passed
Strategy1 direct V4.16.2 A1 preflight PASS
```

Full Research regression suite used during packaging:

```text
449 passed
```

## Testnet run

Example:

```bash
./run_strategy1_research_simple_multi.sh \
  -w sw_ck_st4_m1 \
  -h sw_hk_st4_m1 \
  -u 366 \
  -a 8091 \
  -i sn79-simple-m1
```

Run the baseline V4.16.2 separately for A/B comparison. Do not replace the
baseline until this candidate wins runtime validation.

## First gate: 100–150 ticks

Require:

- contract violations = 0
- RT velocity > 0.03/s; > 0.05/s preferred
- realized PnL dramatically better than V4.16.1
- net Kappa eligible increases
- ONE_AWAY → QUALIFIED conversions occur
- no executable inventory parked indefinitely
- `rt_missing_entry_submit = 0`
- `rt_missing_exit_submit = 0`
- late activity does not immediately collapse

Compare directly against baseline V4.16.2 and Strategy1 on:

1. realized PnL
2. completed RT count / velocity
3. qualified-book breadth
4. risk-Taker share
5. Maker/Taker fill mix
6. contract rejects
7. p50/p95 latency
8. open inventory / liveness

## Promotion rule

This A1 candidate is Research only.

```text
Research A/B PASS
  ↓
freeze Research champion
  ↓
Phase 2 BaseStrategy
  ↓
Base regression
  ↓
Phase 3 AdaptiveAgent
```

BaseStrategy and AdaptiveAgent are unchanged in this package.
