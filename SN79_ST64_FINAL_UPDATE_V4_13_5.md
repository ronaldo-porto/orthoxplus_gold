# SN79 St6.4 — V4.13.5

## Scope

V4.13.5 is a focused exit-authority correction built on the verified V4.13.4 lane-authority baseline. It does not change alpha, scheduler lanes, CORE/RECYCLING logic, sizing, parking, concurrency, or latency/ranking behavior.

## Confirmed V4.13.4 blocker

The 859-tick V4.13.4 run fixed lane authority (`LANE_NOT_GRANTED=0`, `RECYCLING 0→1`) but produced 6 negative RTs. Five negative Taker exits occurred while the same exit state had a clearly positive Maker close and no true hard-risk/stop/emergency condition:

- Book14: Maker +13.42 bps vs Taker -10.79 bps, failed exits 2
- Book44: Maker +18.57 bps vs Taker -10.87 bps, failed exits 1
- Book41: Maker +5.35 bps vs Taker -6.63 bps, failed exits 2
- Book70: Maker +13.89 bps vs Taker -9.14 bps, failed exits 1
- Book55: Maker +10.10 bps vs Taker -4.74 bps, failed exits 1

Book29 remains the counterexample that must still be rescuable: Maker -12.67 bps vs Taker -10.05 bps.

## V4.13.5 Positive-Maker Rescue Veto

New pure policy helper: `positive_maker_rescue_veto_applies()` (`positive_maker_veto_v4_13_5`).

Ordinary negative Taker rescue is vetoed when all are true:

- current Maker RT net >= +1.0 bps
- current Taker RT net < 0 bps
- Maker exit is executable
- failed exit count < 3
- no stop loss
- no MAX_LONG/MAX_SHORT hard inventory condition
- state is not EXIT_ONLY or EMERGENCY

When active, the strategy keeps/refreshes the current Maker exit and emits `POSITIVE_MAKER_VETO`.

The veto releases at failed exit count >=3 so inventory cannot be trapped indefinitely. Existing rescue logic then regains authority.

True hard-risk, stop-loss, EXIT_ONLY, EMERGENCY, non-executable Maker, and non-positive Maker cases bypass the veto immediately.

The V4.13.2 Fresh Maker Grace remains unchanged and still protects the special zero-failed-exit hard-window case.

## Defaults

- `research_positive_maker_veto_enabled=1`
- `research_positive_maker_veto_floor_bps=1.0`
- `research_positive_maker_veto_max_failed_exits=3`

## Frozen behavior

Unchanged from V4.13.4:

- authoritative screening→execution lane propagation
- CORE_PROBE / CORE / RECYCLING productivity logic
- Score-EV hard gates
- alpha/signals
- Taker economic calculations and rescue floors
- parking
- sizing
- active concurrency
- persistent Maker / hysteresis
- latency/ranking logic

## Verification

- Exact five V4.13.4 harmful rescue shapes: vetoed
- Book29 negative-Maker case: veto does not apply
- failed exits 0–2: protected; failed exits >=3: veto released
- true hard-risk / stop / EXIT_ONLY / EMERGENCY: veto bypassed
- Maker below +1 bps or unexecutable: veto does not apply
- Taker >=0: veto does not apply
- full Research suite: 456 passed
- launcher bash syntax: PASS
- launcher preflight: PASS
- Python compile: PASS

## Next Testnet gate

Run V4.13.5 for 250–400 ticks first. Primary runtime proof:

- negative Taker while Maker >= +1 bps, failed exits <3, no hard risk: approximately 0
- `POSITIVE_MAKER_VETO` events lead to Maker fills or controlled release after repeated failures
- positive RT ratio >60%
- realized PnL >0
- Maker close share increases materially
- `LANE_NOT_GRANTED=0`
- CORE/RECYCLING preserved
- placements/fill <15
- contract rejects approximately 0

If healthy, continue the same build toward 600–900 ticks.
