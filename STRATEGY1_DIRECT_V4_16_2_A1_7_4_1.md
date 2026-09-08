# Strategy1-Direct V4.16.2 A1.7.4.1 — TradeEvent Replay De-duplication Repair

## Purpose

A1.7.4.1 is a narrow correctness patch on top of A1.7.4. The triggering runtime evidence showed exact own `TradeEvent` payloads being delivered again on a later request after a simulation/session clock reset. The previous Direct path processed each replay through FIFO inventory, realized PnL, fill learning, round-trip accounting, Kappa observations, and lifecycle state a second time.

That made the affected runtime statistically invalid: one real 0.25 fill could become two internal fills, flat positions could become artificial opposite inventory, and reconstructed absolute exposure could exceed the configured 2.0 BASE cap without a corresponding second exchange trade.

A1.7.4.1 prevents that failure before any parent fill/accounting path runs. It does not retune economics.

## Scope

Changed:

- add exact own-`TradeEvent` process-lifetime de-duplication before `super().onTrade(...)`;
- preserve the de-dup cache across normal tick progression and simulation timestamp/session rebases;
- keep the cache bounded to 32,768 exact event identities;
- emit `DUPLICATE_TRADE_EVENT_SKIPPED` for every suppressed replay;
- expose de-dup version/cache/skip counters in Direct telemetry.

Frozen from A1.7.4/A1.7.3.1:

- recovery trigger `-8 bps`;
- forced recovery consideration `-12 bps`;
- recovery worsening threshold `-0.50 bps/tick`;
- DEFENSIVE/HARD/ABSOLUTE recovery Maker floors `-25/-30/-35 bps`;
- bounded recovery Taker floor `-25 bps`;
- A1.7.1 true-MTM risk semantics;
- A1.7.2 TRUE-WAIT execution;
- A1.7.3.1 bound partial remainder/liveness logic;
- FastPath 20/16;
- 0.25 BASE acquisition size;
- 2.5 bps Maker entry floor;
- Taker entry OFF;
- 2.0 BASE normal total absolute exposure cap;
- qualification logic.

## Exact identity

A replay identity contains:

- `bookId`;
- `tradeId`;
- `timestamp`;
- `clientOrderId`;
- maker agent and maker order IDs;
- taker agent and taker order IDs;
- side;
- exact float tokens for quantity and price.

`tradeId` alone is intentionally not used. The composite identity avoids suppressing a distinct trade if an identifier is ever reused. Missing identity fields that prevent a reliable key fail open: the event is processed normally rather than risk dropping a legitimate fill.

## Runtime behavior

```text
own TradeEvent arrives
        |
        v
exact identity already seen?
   | yes              | no
   v                  v
DUPLICATE_          remember identity
TRADE_EVENT_        in bounded cache
SKIPPED                 |
   |                    v
 return before      parent FIFO/PnL/fill/RT
 parent accounting  accounting runs exactly once
```

The cache is intentionally Direct process state, not session runtime state. A simulator clock moving backwards or a session transition therefore does not automatically erase replay protection. FIFO eviction bounds memory growth.

## Diagnostics

New event:

- `DUPLICATE_TRADE_EVENT_SKIPPED`

Fields include tick, timestamp, book, trade ID, maker/taker order IDs, quantity, price, a short identity hash, cache size, and cumulative skip count. Compact console prints these skips immediately as `S1R_DUP_TRADE_SKIP`.

## Verification contract

The dedicated regression proves:

1. two exact deliveries of the same own trade call parent accounting once;
2. internal inventory in the synthetic accounting proof changes by 0.25, not 0.50;
3. a same-`tradeId` event with different quantity is not suppressed;
4. replay protection remains after the observed timestamp moves backwards;
5. the cache evicts oldest identities when bounded capacity is exceeded;
6. the production guard is located before `super().onTrade(...)`.

## First runtime acceptance

Run 1,500–2,000 clean ticks before any A1.7.4 economic retune. Check:

- every exact replay produces `DUPLICATE_TRADE_EVENT_SKIPPED`;
- the replay produces no second `FILL`, `POSITION`, RT, PnL, or Kappa mutation;
- no artificial 0.25 -> 0.50 same-event inventory doubling;
- no replay-created flat -> opposite-position transition;
- total absolute inventory remains explainable by unique exchange fills;
- A1.7.4 recovery thresholds remain unchanged.

Only after this correctness gate passes should the recovery Maker floor be reconsidered from observation data.
