# SN79 St6.4 Research V4.14.1 — Validator Trade History Alignment

## Validator change audited

The supplied `taos/im/validator/trade.py` differs from the prior codebase only in `shift_simulation_histories()`: when realized-PnL history is rebased across a simulation restart, **every retained timestamp is preserved, including empty `{}` PnL buckets**.

Old behavior rebuilt timestamps only by iterating `books.items()`. Empty buckets therefore vanished. Because validator Kappa uses the first/last retained timestamps for `min_lookback`, a crossover could shrink the apparent assessment span and produce `kappa=None -> score 0`.

New behavior uses:

```python
self.realized_pnl_history[uid][ts] = dict(books)
```

so zero-PnL timestamps survive the crossover.

## What did NOT change

- FIFO long/short matching
- Maker/Taker side mapping
- Fee allocation/proration
- Realized PnL formula
- Round-trip quantity/value accounting
- Trade-volume accounting
- Inventory mark-to-market accounting

Therefore V4.14.0 bounded-loss economics, Taker floors, Maker persistence, entry sizing and volume logic are unchanged.

## Direct influence on Research logic

The validator now keeps the scoring episode continuous across simulation restarts, while the Research session layer previously treated a `SIM_ID_CHANGE` as a full Kappa-evidence reset. That would make the miner scheduler forget books that the validator still considers Kappa-qualified.

V4.14.1 fixes only that mismatch:

1. On `SIM_ID_CHANGE`, execution/inventory/session runtime is still cleared and quarantined.
2. Rolling Kappa observation timestamps are rebased using the validator formula:
   `new_ts - (old_ts - old_history_ts)`.
3. Rolling realized-PnL evidence is rebased the same way.
4. Miner-side sparse FIFO PnL history is rebased and retained through the `SimulationStartEvent` reset.
5. Negative timestamps are explicitly allowed because retained old-simulation history legitimately lives before new-simulation `t=0`.
6. Network/netuid/schema-invalid transitions still fully reset evidence.

Runtime event: `VALIDATOR_HISTORY_CARRY`.

## Frozen from V4.14.0

- bounded-loss escape corridor (`-8 .. -25 bps`, age >=2, >=2 bps deterioration)
- max total open books = 8
- parked threshold = 4
- max total abs BASE = 2.0
- Positive-Maker veto failed-exit threshold = 4
- V4.13.9 sticky contract guard
- V4.13.8 profitable Maker persistence
- V4.13.7 Core quoteability
- V4.13.6 Kappa productivity scheduler

## Expected benefit

At validator simulation crossovers, the miner should no longer re-bootstrap Kappa coverage from zero while the validator score remains continuous. This should reduce redundant acquisition, protect breadth, and improve long-run capital efficiency without adding hot-path computation.
