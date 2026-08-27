# SN79 St6.4 Final Update V4.13.8

## Purpose

V4.13.8 is a focused Maker-realization patch built on V4.13.7. It does not change density scheduling, Core entry admission, rescue authority, alpha, sizing, or latency policy.

The 708-tick V4.13.7 run proved that qualified/Core books can quote after tick500, but productive Core quotes rarely converted to fills. Several long-lived inventories showed slightly positive Maker exit economics while repeated short TTL expiry/cancel/replacement reset queue priority.

## Changes

### Profitable Maker exit persistence

New policy version: `profitable_maker_exit_persistence_v4_13_8`.

A Maker exit may use a longer bounded GTT only when its current round-trip Maker net is positive and the market regime is not TOXIC/STRESSED.

Default values:

- `research_profitable_exit_persistence_enabled=1`
- `research_profitable_exit_ttl_ms=3000`
- `research_profitable_exit_min_net_bps=0.0`
- `research_profitable_exit_reprice_ticks=3`

The persistence TTL is capped at 5000 ms. Normal/non-positive exits keep their prior TTL behavior.

### Queue-priority hold

If an accepted same-side Maker exit already exists, V4.13.8 keeps it live instead of replacing it when:

- Maker net remains positive;
- resting quantity is at least 80% of the newly desired exit quantity;
- desired exit price is within 3 ticks of the resting price.

A material touch move, insufficient resting size, non-positive Maker net, or normal hard-safety/Taker authority restores existing behavior.

Runtime events:

- `PROFITABLE_EXIT_PERSIST`
- `PROFITABLE_EXIT_HOLD`

## Frozen behavior

Unchanged from the verified prior versions:

- V4.13.7 qualified/Core exact-min entry and bounded stale TTL;
- V4.13.6 Kappa-density scheduler and deep-EV prefilter;
- V4.13.5 Positive-Maker Veto / Fresh Maker Grace;
- V4.13.4 authoritative lane propagation;
- Score-EV `NEGATIVE_EV` hard gate;
- Taker rescue floors/economics;
- parking state machine;
- alpha/signals;
- normal entry sizing;
- concurrency;
- ranking/latency implementation.

## Verification

- Focused persistence + hysteresis tests: 18 passed before final integration assertion.
- Full Research suite: **479 passed**.
- Python compile: PASS.
- Launcher bash syntax: PASS.
- `RESEARCH_PREFLIGHT_ONLY=1` launcher: PASS.
- Active V4.13.8 source snapshots are copied into `agents/strategy/__ver_st1_log__/`.

## Testnet acceptance

Run 600–900 ticks. The important proof is not merely that quotes continue after tick500; V4.13.7 already established that.

V4.13.8 must show:

- `PROFITABLE_EXIT_PERSIST > 0` when positive Maker exits occur;
- `PROFITABLE_EXIT_HOLD > 0` on stable resting exits;
- `TTL_EXPIRED` on positive Maker exits drops materially;
- parked inventory count/age falls rather than grows;
- productive CORE RT after tick500 > 0;
- RT velocity sustains >0.015/s initially and moves back toward >0.02–0.03/s;
- positive RT ratio >60%;
- realized PnL >0;
- Kappa eligible stable/rising;
- `LANE_NOT_GRANTED=0`.
