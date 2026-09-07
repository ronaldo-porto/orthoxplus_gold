# Strategy1-Direct V4.16.2 A1.7.4 — Genuine Tail-Risk Recovery

## Purpose

A1.7.4 is the first economic patch after A1.7.3.1 repaired partial-fill/liveness mechanics. It keeps the profitable A1.7.2 TRUE-WAIT Maker engine and A1.7.3.1 bound-remainder publisher intact, but intervenes before genuine adverse inventory reaches the observed HARD/ABSOLUTE forced-Taker tail.

Across recent A1.7.2/A1.7.3 observation runs, the repeated structure was:

- Maker-ending RTs were commonly ~90–97% positive;
- `NORMAL_TAKER_NONNEGATIVE` was ~98–100% positive;
- HARD/ABSOLUTE risk exits were almost always negative;
- true HARD onset clustered around roughly -19.5 bps MTM;
- forced HARD/ABS Taker completion was commonly around -45 to -50 bps.

A1.7.4 therefore adds a bounded recovery corridor. It does **not** weaken true-MTM risk semantics, re-enable Taker entry, change FastPath, change 0.25 BASE sizing, or change liveness parameters.

## Observation-driven initial corridor

These are explicit A/B-test constants, not a trained/learned model:

- recovery starts at true MTM `<= -8 bps` (the existing DEFENSIVE boundary);
- true MTM `<= -12 bps` forces recovery consideration even if short-term slope is flat;
- otherwise recovery requires worsening MTM slope `<= -0.50 bps/tick`;
- minimum recovery age: `4 ticks`;
- DEFENSIVE recovery Maker floor: `-25 bps`;
- HARD recovery Maker floor: `-30 bps`;
- MTM-only ABSOLUTE recovery Maker floor: `-35 bps`;
- recovery Maker must be at least `10 bps` better than crossing immediately;
- failed-Maker limit before bounded early reduction: `2`;
- bounded pre-HARD recovery Taker floor: `-25 bps`, only after failed Maker recovery and true MTM `<= -10 bps`;
- observed forced-tail reference used for telemetry: `-48 bps`.

The intent is not to make negative exits normal. The intent is to accept a controlled smaller loss only when it is materially cheaper than the repeatedly observed forced tail.

## Recovery sequence

```text
NORMAL / shallow DEFENSIVE
        |
        v
A1.7.2 TRUE WAIT / profitable Maker
        |
        | true MTM worsens
        v
RECOVERY corridor
        |
        +--> bounded Maker concession if materially better than Taker
        |
        +--> after failed Maker attempts, bounded recovery Taker only if >= -25 bps
        |
        v
HARD_ESCAPE
        |
        +--> one/two bounded Maker recovery opportunities when >= -30 bps
        |
        v
forced HARD Taker only when recovery is unavailable/failed

MTM-only ABSOLUTE
        |
        +--> one bounded Maker recovery opportunity when >= -35 bps and >=10 bps better than Taker
        |
        v
ABSOLUTE Taker

MAX_LONG / MAX_SHORT catastrophic exposure
        |
        v
immediate frozen A1.7.2 protection (no A1.7.4 delay)
```

## Safety invariants

A1.7.4 preserves:

- A1.7.1 risk source: true inventory MTM only;
- spread, fees, slippage and impact cannot create a HARD risk band;
- age-0/1 false HARD escapes remain blocked by the frozen exit module;
- catastrophic/MAX exposure bypasses recovery;
- positive Maker veto remains authoritative;
- ordinary negative `AGGRESSIVE_MAKER_EXIT` remains blocked;
- negative Maker placement is allowed only when the current-tick A1.7.4 recovery authority explicitly approves it and the final executable Maker net remains above the reason-specific floor;
- Taker entry remains OFF.

## Bounded recovery Taker

A1.7.4 can authorize `RECOVERY_TAKER_REDUCE` only when all of the following hold:

1. inventory is already in the recovery corridor;
2. at least two Maker recovery attempts have failed;
3. true MTM is at or below -10 bps;
4. reduction is mechanically executable;
5. current Taker completion is no worse than -25 bps.

It is logged as `taker_authority=RECOVERY`, not as normal economic Taker entry/exit authority.

## Partial-fill/liveness behavior frozen from A1.7.3.1

Unchanged:

- exact `makerOrderId` binding for partial remainders;
- no fresh 0.25 replacement while a bound remainder owns the book;
- 4-second hard partial hold;
- one-clip dust recovery reserve;
- liveness trigger: 12 blocked ticks;
- warm-state recovery overflow max: 0.125 BASE;
- no new sub-minimum orders.

## Entry/productivity behavior frozen

Unchanged:

- FastPath: 20 cheap / 16 deep;
- acquisition size: 0.25 BASE;
- Maker entry edge floor: 2.5 bps;
- Taker entry: OFF;
- max total absolute exposure: 2.0 BASE;
- max active books: 6;
- max total open books: 8;
- qualification logic;
- adaptive Maker sizing: OFF.

## New diagnostics

- `A174_RECOVERY_DECISION`
- `A174_RECOVERY_MAKER_PLACE`
- `A174_RECOVERY_MAKER_BLOCK`
- `A174_TAIL_COUNTERFACTUAL`
- `A174_RECOVERY_OUTCOME`

`A174_TAIL_COUNTERFACTUAL` records the best Maker completion seen after entering the tail corridor and compares it with the eventual HARD/ABS Taker completion.

`A174_RECOVERY_OUTCOME` records the realized lifecycle result for a lifecycle where A1.7.4 recovery authority was used.

## Runtime acceptance targets

Primary A1.7.4 gates:

- risk-Taker RT share `<25%`, target `<20%`;
- Maker-ending positive RT `>=90%`;
- overall positive RT `>=60%`;
- `NORMAL_TAKER_NONNEGATIVE` positive ratio `>=95%`;
- realized RT PnL positive;
- RT velocity `>=0.10/s`, target `0.12–0.15/s`;
- persistent dust books `<=2–3`;
- no candidate-present/no-placement interval `>100 ticks`;
- p95 response `<120 ms`.

The first observation run should focus on whether recovery Maker/Taker decisions reduce the number and average loss of `HARD_ESCAPE_CLIP` / `ABSOLUTE_PROTECTION_REDUCE` without materially damaging A1.7.2 Maker quality.
