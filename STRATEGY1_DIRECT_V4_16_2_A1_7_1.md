# Strategy1-Direct V4.16.2 A1.7.1 — Exit Semantics Repair

## Purpose

A1.7.1 is a narrow correction to the A1.7.0 Direct exit path. The Agent67 A1.7.0 run showed strong productivity and latency but poor round-trip quality because negative Taker liquidation economics were being reused as the position-risk input. Wide spread/fees/slippage/impact could therefore manufacture `HARD_ESCAPE` immediately after a Maker fill.

A1.7.1 fixes only that semantic error. Persistent Maker execution, FastPath, acquisition sizing, exposure limits, entry economics, Taker-entry disablement, and quote-manager behavior remain frozen.

## Root cause fixed

The frozen Research base historically calls the exit chooser with executable `taker_net` in its `unrealized_bps` slot. In the A1.7.0 Direct overlay this caused risk classification to include:

- spread crossing;
- Taker fees;
- slippage;
- market-impact buffer.

That made expensive liquidation look like adverse mark-to-market loss and allowed premature `HARD_ESCAPE_CLIP` / `ABSOLUTE_PROTECTION_REDUCE` decisions.

A1.7.1 does **not** edit `Strategy1_Research.py`. The Direct overlay replaces that argument at the call boundary with the inventory snapshot's true mid-mark MTM (`inventory.unrealized_bps`).

## A1.7.1 exit rules

### Risk source

- `position_risk_bps` = true inventory mid-mark MTM only.
- `taker_net_bps` = executable completion economics after fee/slippage/impact.
- `taker_net_bps` never creates a risk band.
- Unknown MTM is treated as neutral/normal rather than falling back to Taker loss.

### NORMAL / DEFENSIVE

- executable Maker completion >= +1 bps -> Maker exit;
- otherwise non-negative Taker completion may close;
- otherwise wait.

### HARD_ESCAPE

A true MTM loss in the hard band may reduce by Taker only after the existing maturity/retry protections:

- minimum age defaults to `research_bounded_loss_escape_min_age_ticks=2`;
- a failed prior exit attempt may unlock hard reduction before the age floor;
- an executable positive Maker completion >= `research_positive_maker_veto_floor_bps` keeps veto authority until `research_positive_maker_veto_max_failed_exits` is reached.

### ABSOLUTE / catastrophic

True absolute MTM loss or catastrophic/MAX inventory state retains immediate Taker-reduction authority when mechanically executable. Non-executable exposure parks.

## Frozen A1.7.0 behavior

Unchanged:

- FastPath: 20 cheap / 16 deep;
- base Maker acquisition size: 0.25 BASE;
- observable Maker entry edge floor: 2.5 bps;
- Taker entry: OFF;
- touch-improvement cap: 6 bps;
- persistent quote KEEP/REPRICE/CANCEL lifecycle;
- quote-manager TTLs;
- exposure synchronization and one-live-order-batch contract;
- total/open-book exposure caps;
- no new learner, posterior, EWMA, lifecycle model, or score gate.

## New diagnostics

Each Direct inventory decision emits `A171_EXIT_DIAGNOSTIC` with:

- `position_risk_bps`;
- `maker_net_bps`;
- `taker_net_bps`;
- historical caller value for comparison;
- inventory age;
- failed-exit count;
- risk band;
- selected action and reason;
- hard-escape age floor;
- positive-Maker veto floor / retry limit;
- `risk_source=MID_MTM_EXCLUDES_CROSSING_COST`.

## Regression cases

The A1.7.1 tests explicitly prove:

1. fresh Maker +3 bps / Taker -20 bps / MTM 0 -> Maker, not hard escape;
2. unknown MTM never falls back to negative Taker economics;
3. true MTM -19 bps at age 1 -> fresh hard-risk grace;
4. true MTM -19 bps at age 2 -> hard Taker allowed;
5. a failed prior exit can unlock hard escape;
6. positive Maker veto dominates hard negative Taker until the retry limit;
7. the veto releases at the retry limit;
8. true absolute MTM loss reduces immediately;
9. catastrophic inventory emergency still reduces immediately;
10. the frozen Research base remains untouched.

## Runtime acceptance gate

Run 500-1,000 ticks first.

Primary targets:

- RT velocity >= 0.15/s, with A1.7.0 productivity ideally retained near 0.4/s;
- positive RT >= 55%, target >= 60%;
- Maker-ending positive >= 75%;
- Taker-risk-ending RT share < 25%;
- `HARD_ESCAPE_CLIP` at inventory age <= 1 near zero unless a failed exit explicitly unlocked it;
- realized RT PnL positive;
- p95 response < 120 ms;
- qualified-book breadth resumes beyond the prior ~30-book plateau.

Do not change productivity/entry logic until this exit-only correction is validated on testnet.
