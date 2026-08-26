# SN79 Research Hybrid Score-Taker V4.7

## Scope

V4.7 is a focused Research-Agent update on top of `hybrid_score_utility_v4_6`. It does **not** modify `BaseStrategy.py` or `AdaptiveAgent.py`.

Policy marker:

```text
RESEARCH_POLICY_VERSION = "hybrid_score_utility_v4_7"
RESEARCH_HYBRID_VERSION = "hybrid_maker_taker_v3"
RESEARCH_LADDER_VERSION = "realization_ladder_v2"
```

## Why V4.7 exists

The full V4.6 live log showed profitable maker trading but slow inventory turnover:

- 37 fills, all maker
- 16 completed round trips
- +1.6826 QUOTE realized PnL
- median holding time about 47 ticks
- p90 holding time about 161.5 ticks
- 1,488 taker evaluations
- 67 `TAKER_DECISION` records with `sn79_take=1`
- those 67 signals came from Books 4 and 11
- 0 actual `SELECTIVE_TAKER_EXIT` actions

The blocker was orchestration: the hybrid/action-utility engine could authorize a score-positive taker, but `apply_realization_ladder()` still required urgency to reach the top taker-eligible band. In the live run, urgency never reached that threshold, so every valid score-taker signal was downgraded back to a maker exit.

The same run also exposed phantom REALIZATION demand: flattened FIFO dictionary keys were still classified as inventory by the fast screen, consuming realization candidate slots after the actual position was flat.

## V4.7 changes

### 1. SCORE_TAKER now bypasses maker-rung urgency

V4.7 separates two concepts:

- **RISK/ECONOMIC TAKER**: existing path remains ladder-gated unless hard safety is active.
- **SCORE_TAKER**: when bounded SN79 action utility passes, it may directly select `SELECTIVE_TAKER_EXIT` even if the urgency ladder currently says passive/competitive/aggressive maker.

The direct path is feature-gated:

```text
research_enable_score_taker_direct=1
```

The existing score guardrails are unchanged:

```text
research_sn79_min_utility_margin=0.03
research_sn79_max_score_subsidy_loss_bps=-2.0
```

So V4.7 does **not** blindly cross inventory. It executes the score-taker signals that V4.6 already judged worthwhile while preserving the bounded loss floor.

### 2. Phantom REALIZATION demand fixed

Fast-screen inventory authority is now actual non-flat net quantity only:

```python
has_inv = qty > flat_epsilon
```

FIFO dictionary membership no longer counts as inventory.

Empty FIFO keys are also pruned when the book is flat. New lane telemetry reports:

```text
actual_nonflat_inventory
stale_empty_position_keys
```

This should stop flattened historical books from stealing REALIZATION candidate capacity from COVERAGE and KAPPA_COMPLETION.

### 3. Taker authorization telemetry added

Realization/taker logs now distinguish:

```text
economic_taker_authorized
score_taker_authorized
direct_taker_authorized
sn79_take
sn79_utility_margin
```

This makes it possible to verify whether a taker was selected because of the score-utility path, the legacy economic path, or hard safety.

### 4. Cancel resting orders before taker close

V4.7 enables:

```text
research_cancel_before_taker=1
```

Before a market close, resting orders on that book are cancelled when instruction capacity permits. This reduces the risk that a stale maker exit remains live after the taker flattens the position and later reopens inventory in the opposite direction.

If the response does not have room for both CANCEL and MARKET instructions, V4.7 fails safe instead of flattening while leaving stale close orders live.

### 5. Existing profitable maker engine preserved

No change was made to the core maker-entry edge model, markout V2, exact reducing quantity, dust guards, per-book cap logic, entry-size V4.6 fix, or current-run round-trip velocity baseline.

The intended behavior is:

```text
MAKER ENTRY
  -> inventory
  -> maker exit when fast/economic
  -> SCORE_TAKER when bounded SN79 utility materially beats waiting
  -> faster round trip / Kappa progression / capital release
```

## Offline evidence from the V4.6 log

The original V4.6 log contained 67 score-taker authorizations:

```text
Book 4:  30 signals, ticks 83..145
  taker net range: -0.6033 .. +1.7578 bps
  utility margin:  +1.1591 .. +1.8767

Book 11: 37 signals, ticks 148..292
  taker net range: -1.9274 .. -1.2576 bps
  utility margin:  +1.1053 .. +1.5020
```

Both remain inside the configured -2 bps score-subsidy floor. V4.7 can now act on these signals without waiting for urgency to reach the top ladder band.

This is only an offline policy replay implication, not a profitability claim. The next Testnet run must verify actual fills and score effects.

## Verification

```text
PYTHONPATH=agents/strategy pytest -q tests/test_research_*.py
293 passed

python -m py_compile ...
PASS

bash -n run_strategy1_research_test_multi.sh
PASS
```

Focused V4.7 tests cover:

- policy version
- phantom inventory source contract
- direct SCORE_TAKER bypass at low urgency
- legacy non-direct ladder behavior
- feature-gated rollback of direct score taker
- transition quarantine still blocking direct taker

## Next Testnet metrics

The first V4.7 run should specifically verify:

- `score_taker_authorized > 0`
- `direct_taker_authorized > 0`
- actual `SELECTIVE_TAKER_EXIT > 0`
- Taker fill count / realized Taker PnL
- median and p90 inventory age decrease
- RoundTripVelocity increases
- RoundTripConversion increases
- Kappa one-away/two-away books convert faster
- Kappa-qualified breadth increases
- `realization_demand` tracks `actual_nonflat_inventory` rather than stale FIFO keys
- maker realized PnL and downside remain controlled

Do not promote to BaseStrategy until those live conditions are verified.
