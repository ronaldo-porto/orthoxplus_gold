# SN79 St6.4 Research V4.14.3 — Dust-Slot + Two-Stage Kappa Protection

## Purpose

V4.14.3 is a narrow RealNet correction on top of V4.14.2 Wide-Kappa Wave.
It addresses two concrete Agent23 log failures without changing the alpha engine,
validator accounting, Kappa breadth scheduler, hard risk limits, or position sizing.

## RealNet evidence addressed

Agent23 V4.14.1 produced strong economics but weak Kappa breadth/quality:

- 245 fills, 207 Maker / 38 Taker
- 109 completed RTs
- 75/75 Maker-closed RTs positive
- 32 negative RTs, 30 created by BOUNDED_LOSS_ESCAPE
- 107/128 books still at zero observations
- 5 of 6 active slots occupied by sub-minimum dust while total BASE was only ~0.65 / 2.0

The resulting failure chain was:

1. sub-minimum dust consumed productive active-book capacity;
2. new breadth acquisition was blocked even though true BASE headroom remained;
3. the V4.14 single-stage -8..-25 bps escape converted many profitable Maker exits into negative Taker observations;
4. breadth stayed narrow and per-book Kappa stayed weak.

## Change 1 — Dust no longer consumes productive active slots

A real dust position still counts against:

- `actual_nonflat_inventory`
- `research_max_total_open_books = 8`
- `research_max_total_abs_base = 2.0`

But it no longer counts against:

- `research_max_active_open_books = 6`

This preserves hard risk accounting while allowing the remaining true total-book headroom
to be used for fresh Kappa acquisition.

New telemetry:

- `productive_active_nonflat_inventory`
- `dust_nonflat_inventory`

## Change 2 — Two-stage bounded-loss control

The old V4.14 behavior used one corridor:

- Taker `-8 .. -25 bps` + age/drawdown => immediate Taker recycle.

V4.14.3 splits this into two stages:

### Soft stage: `-8 .. -18 bps`

If Maker is still meaningfully profitable and the lifecycle is still young:

- keep Maker;
- do not create an unnecessary negative Kappa observation.

The hold is bounded by the already-existing knobs:

- Maker positive floor: `research_positive_maker_veto_floor_bps = 1.0`
- failed-exit bound: `research_positive_maker_veto_max_failed_exits = 4`
- age bound: `research_liveness_maker_min_age_ticks = 8`

Telemetry: `BOUNDED_LOSS_SOFT_HOLD`.

### Hard stage: `<= -18 bps` down to `-25 bps`

The original V4.14 tail-risk protection remains authoritative:

- force Taker recycle even if Maker is positive;
- prevent the old multi-hour parked-tail failure.

`BOUNDED_LOSS_ESCAPE` now includes `stage=HARD_ESCAPE` or `stage=SOFT_ESCAPE`.

## Frozen behavior

The following are unchanged:

- V4.14.2 Wide-Kappa breadth/density scheduler
- 2 exploration paths
- 3 observations = eligibility, 6 observation minimum density, bounded extension to 8
- max active productive books = 6
- max total open books = 8
- max total BASE = 2.0
- Positive-Maker veto = 4 failed exits
- V4.14.1 validator history alignment
- validator `trade.py` FIFO/fee/RT accounting
- V4.13.9 contract guard
- V4.13.8 profitable Maker persistence
- alpha/signal engine

## Verification

- focused V4.14.0/2/3 regression: 16 passed
- active Research regression: 504 passed
- Python compile: PASS
- launcher bash syntax: PASS
- launcher preflight: PASS
- validator `trade.py`: exact byte-for-byte match with supplied validator file

## Runtime acceptance signals

The next Agent23 run should show:

1. `dust_nonflat_inventory` may be >0 while `productive_active_nonflat_inventory` remains below 6;
2. fresh COVERAGE continues until the true 8-book / 2.0-BASE caps bind;
3. `BOUNDED_LOSS_SOFT_HOLD` appears for profitable Maker exits in the -8..-18 bps Taker zone;
4. negative RT share from `BOUNDED_LOSS_ESCAPE` falls sharply;
5. eligible books rise faster;
6. median per-book Kappa rises rather than merely widening weak books;
7. no recurrence of multi-hour -1000+ bps parked tails.
