# Strategy4 — Constrained Alpha-AS/GLFT + L3 Agent for TAOS SN79

## Files

- `Strategy4.py`: deployable agent implementation.
- Dependency: place it in the same agent directory as `Strategy1.py` and `DetailedTemplateAgent.py`.

## Architecture

1. **L3 fair value** — reuses Strategy1's momentum, L1 flow, L2–L5 imbalance, trade imbalance, microprice, microprice velocity, and trade-sign persistence forecast.
2. **AS reservation price** — predicted fair value is shifted opposite to signed inventory.
3. **GLFT-inspired half-spread** — quote width responds to trade intensity, volatility, toxicity, inventory utilization, and adaptive risk aversion.
4. **Side-specific EV** — BUY and SELL orders are independently gated using fill probability, fair-value edge, fees, adverse-selection markout, inventory cost, and latency cost.
5. **Hard risk state machine** — `NORMAL`, `CAUTIOUS`, `REDUCE_ONLY`, `LIQUIDATE`, `DISABLED`.
6. **Constrained Alpha policy** — the policy selects only bounded multipliers for gamma, spread, alpha, size, and expiry. It cannot bypass hard risk rules.
7. **Order-level learning** — each active MM order records its side, distance, action, context, inventory, fair value, and expected edge. Delayed post-fill markouts update adverse-selection estimates and optional contextual-UCB values.

## Main corrections over Strategy1

- Inventory limits and utilization are calculated in base units.
- UP/DOWN alpha shifts both quotes through a fair-price center.
- Long inventory lowers the reservation price; short inventory raises it.
- WALL and TREND biases are signed.
- Inventory management runs before avoid-list and entry gates.
- Fill-learning uses one distance-from-touch definition and exact active-order metadata.
- Fill rate uses filled orders, while quantity fill rate uses filled/submitted quantity.
- MM and separate alpha orders cannot be opened on the same book in one tick.
- Separate directional alpha is disabled by default because direction is already embedded in fair value.
- Position state is not cleared until trade notices confirm the position is flat.
- Grace-period logging no longer dereferences a missing summary.

## Recommended first launch

Wrapper (preferred):

```bash
chmod +x agents/strategy/run_strategy4.sh
./agents/strategy/run_strategy4.sh -w <coldkey> -h <hotkey> -u 79 -a 8091
```

Or via `run_miner.sh` directly:

```bash
./run_miner.sh \
  -w <coldkey> -h <hotkey> -u 79 -a 8091 \
  -g "$PWD/agents/strategy" \
  -n Strategy4 \
  -m "enable_mm_strategy=1 lazy_load=1 alpha_policy_mode=deterministic \
enable_separate_alpha=0 mm_base_size=0.20 max_inventory_base=1.20 \
max_mm_books_per_tick=4 max_managed_books_per_tick=4 verbose_log=0 log_every_n=100"
```

Keep `alpha_policy_mode=deterministic` until multi-seed tests prove the quote and risk behavior is stable.

## Optional constrained online policy

After offline/local validation:

```text
alpha_policy_mode=ucb
policy_exploration=0.20
policy_min_samples=8
policy_save_every=100
```

Policy values are persisted by default to:

```text
<output_dir>/constrained_alpha_policy.json
```

The policy chooses among five bounded profiles:

| Action | Use |
|---|---|
| defensive | Wider, smaller, more risk-averse |
| balanced | Baseline |
| alpha | Stronger fair-value shift, smaller size |
| liquid | Tighter and larger in liquid MM books |
| toxic | Very wide, small, and defensive |

`toxic` is forced for unsafe states; the UCB policy explores only the first four actions in `NORMAL` mode.

## Important parameters

### Inventory and survival

| Parameter | Default | Meaning |
|---|---:|---|
| `max_inventory_base` | inherited, commonly 1.2 | Absolute per-book base inventory limit |
| `cautious_inventory_util` | 0.45 | Enter cautious quoting |
| `reduce_only_inventory_util` | 0.72 | Stop increasing risk |
| `liquidate_inventory_util` | 0.98 | Force aggressive close |
| `hard_stop_loss_bps` | 55 | Force close on position loss |

### Reservation price

| Parameter | Default | Meaning |
|---|---:|---|
| `base_risk_aversion` | 0.85 | Base AS/GLFT gamma |
| `alpha_shift_spreads` | 0.32 | Directional fair-value shift |
| `inventory_shift_spreads` | 0.55 | Inventory reservation-price pressure |
| `max_fair_shift_spreads` | 0.75 | Maximum directional center shift |

### Spread and EV

| Parameter | Default | Meaning |
|---|---:|---|
| `glft_spread_weight` | 0.12 | Liquidity/intensity spread component |
| `vol_spread_weight` | 0.20 | Volatility spread component |
| `toxicity_spread_weight` | 0.35 | Toxic-flow spread component |
| `inventory_spread_weight` | 0.25 | Inventory-risk spread component |
| `fee_buffer_bps` | 0.15 | Conservative per-fill fee/cost buffer |
| `min_side_edge_bps` | 0.05 | Minimum fill-adjusted side EV |
| `markout_horizon_ns` | 2,000,000,000 | Post-fill adverse-selection horizon |

## Required validation sequence

1. **Behavioral quote tests**
   - UP signal moves fair value and both quote prices upward.
   - DOWN signal moves them downward.
   - Long inventory lowers reservation price.
   - Short inventory raises reservation price.
   - Deeper quotes receive lower fill estimates.

2. **Inventory tests**
   - Utilization equals `abs(net_base) / max_inventory_base`.
   - No submitted side can push projected inventory beyond the hard cap.
   - Avoided books with inventory still receive exit instructions.
   - Rejected or partial market closes retain position age/reason.

3. **Order-learning tests**
   - Partial fills count as one filled order but accumulate filled quantity.
   - Fill learning is attributed to the active client-order distance bucket.
   - Negative post-fill markout widens/gates the affected side.

4. **Race tests**
   - Run many seeds and configurations.
   - Compare against Strategy1, a clean AS baseline, RandomMakerAgent, and ArbitrageAgent.
   - Measure Kappa-3, PnL, round-trip volume, max drawdown, inventory duration, markout, fill rate, quantity fill rate, p50/p95 response latency, and risk-mode time.

5. **Deployment checks**
   - Confirm the current TAOS version accepts the exact instruction fields.
   - Confirm deterministic client IDs replace or coexist as expected.
   - Tune `fee_buffer_bps` against validator fee/accounting behavior.
   - Verify `event.clientOrderId`, `event.quantity`, and `event.price` are populated for maker fills in your deployed version.

## Validation completed here

- Python syntax compilation: passed.
- AST/import-shape validation with dependency stubs: passed.
- Isolated quote-math checks: passed.
  - Positive signal produced upward fair-value displacement.
  - Long inventory lowered reservation price.
  - Short inventory raised reservation price.
  - Touch fill estimates exceeded deeper-quote estimates.

Full runtime and profitability validation still requires the user's TAOS environment, `DetailedTemplateAgent.py`, simulator configuration, accounts, notices, fees, and multiple local races.
