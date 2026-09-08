# Strategy1-Direct V4.16.2 A1.7.4.2 — Kappa-Safe Dust Compaction Guard

A1.7.4.2 is a narrow economic-safety patch on top of A1.7.4.1. The 2,445-tick
Agent67 runtime proved that the core engine is productive and profitable, but
also exposed one outsized stale-dust normalization: Book53 crossed roughly
-0.1987 BASE through zero with a full 0.25 Maker BUY and realized about -920 bps
/ -5.73 quote PnL. The old theorem only proved that absolute exposure decreases;
it did not bound realized loss.

## Change

Only the Direct **moderate-dust sign-cross compactor** receives the new guard.
Before publishing the minimum-size passive cleanup, A1.7.4.2 computes the
realization from tracked VWAP to the exact passive touch. The cleanup is allowed
only when that Maker realization is at least **-60 bps**. Unknown cost basis
fails closed for sign-cross compaction.

This deliberately keeps normal profitable/bounded CROSS_DUST available. It does
not disable CROSS_DUST globally.

Diagnostics:

- `A1742_DUST_KAPPA_ALLOW`
- `A1742_DUST_KAPPA_BLOCK`
- RUN_SUMMARY counters `direct_dust_kappa_allows` / `direct_dust_kappa_blocks`

## Frozen

- A1.7.4 recovery trigger/force: -8 / -12 bps
- A1.7.4 Maker floors: -25 / -30 / -35 bps
- A1.7.4.1 TradeEvent de-duplication
- A1.7.3.1 partial-remainder ownership, dust reserve and liveness trigger
- FastPath 20/16
- 0.25 BASE probe size
- Taker entry OFF
- 2.0 BASE total exposure cap
- qualification / score target logic

## Validation target

A long test should show Book53-style compaction blocked while ordinary bounded
CROSS_DUST continues to execute, with no reintroduction of >100-tick capital
starvation and no degradation of the A1.7.4.1 Maker/RT quality gates.
