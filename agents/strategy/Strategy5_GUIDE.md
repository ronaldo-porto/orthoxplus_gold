# Strategy5 — Floor-Aware HJB/AS on Strategy3

## Role

Preferred scoring-aware production candidate for the July 19, 2026 SN79 model:
**Strategy3 survival stack + AS/HJB quote overlay + soft-floor quote pressure.**

Trading score target: stay above the soft floor / median under
`0.79 * Kappa + 0.21 * realized PnL` before Pareto (`shape=1.0`).

## Inheritance

`Strategy5(Strategy3)` — does **not** reimplement inventory caps, avoid-book
repair, risk cancels, grace, or floor book classification. Those come from
Strategy3. Separate directional alpha stays **off** by default; direction is
expressed via fair-value reservation shift.

## Floor-aware HJB behavior

| Book class | Behavior |
|---|---|
| Strong (positive PnL + good fills) | Mild tighten; normal HJB overlay |
| Weak (≤ own score quantile) | Size × `hjb_weak_book_size_mult`; positive-EV sides only |
| Left-tail | Inventory repair only (`hjb_left_tail_quote_enabled=0`) |
| Below floor guard | Stronger expected-edge gate + wider spreads |

## Config defaults

| Param | Default |
|---|---:|
| `enable_floor_awareness` | true |
| `floor_guard_ratio` | 1.05 |
| `hjb_floor_edge_boost` | 0.15 |
| `hjb_weak_book_size_mult` | 0.5 |
| `hjb_left_tail_quote_enabled` | false |
| `enable_separate_alpha` | false |

## Telemetry

Each MM tick logs:

```text
[HJB_FLOOR] estimated_trading_score=… estimated_soft_floor_score=…
score_to_median=… weak_books=… left_tail_books=… hjb_fallback_count=…
```

## Launch

```bash
chmod +x agents/strategy/run_strategy5.sh
./agents/strategy/run_strategy5.sh -w <coldkey> -h <hotkey> -u 79 -a 8091
```
