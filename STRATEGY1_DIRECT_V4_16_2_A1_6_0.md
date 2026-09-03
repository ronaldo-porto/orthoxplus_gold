# Strategy1-Direct V4.16.2 A1.6.0 — Observable FastPath

## Purpose
A1.6.0 is a simplification release. It keeps the A1.5/A1.5.1 latency gains but removes learned lifecycle/quality authority from Direct entry and replaces normal exit utility arbitration with current observable net economics.

## Runtime evidence motivating the change
A1.5.1 reached ~29 ms p50 / ~36 ms p95 response latency and grew qualified books 44→59 in 750 ticks, but produced only 7 positive / 21 negative RTs. Maker→Maker was 5/6 positive (+1.419 PnL) while Maker→Taker was 2/22 positive (-1.449 PnL). The losing path was dominated by normal/defensive Taker exits selected by internal utility comparisons.

## A1.6.0 entry authority
1. Cheap 128-book observable scan.
2. Rank with current top-of-book spread, current signed Maker fee, top liquidity, Kappa need, and deterministic fairness.
3. Select 20 FastPath candidates (bounded 16–24).
4. Build expensive profiles for at most 16, while always retaining inventory books.
5. Current Maker edge is `0.5 * spread_bps - signed_maker_fee_bps`.
6. Maker acquisition requires current edge >= 2.5 bps.
7. Directional Taker ENTRY remains disabled.
8. No learned quality, future Taker probability, future exit-fee forecast, markout posterior, rolling PnL posterior, realization-time model, or latency penalty can authorize/veto Direct entry.

## Candidate rotation
After 3 consecutive current-edge skips on a book, the book receives a deterministic 4-tick acquisition cooldown. Inventory books are never suppressed by this cooldown.

## Exit authority
For NORMAL/DEFENSIVE inventory:
- Maker exit when current Maker completion net >= +1.0 bps.
- Otherwise Taker only when current Taker completion net >= 0 bps.
- Otherwise WAIT.

For HARD_ESCAPE / ABSOLUTE_PROTECTION:
- Negative Taker reduction remains allowed/required when mechanically executable.
- Non-executable dust/position is parked.

This removes the ordinary MakerUtility/TakerUtility/WaitUtility race from Direct A1.6 without changing frozen Research source code.

## Preserved proven controls
- Maker touch improvement cap: 6 bps.
- Maker GTT cap: 75 ms.
- Pre-submit freshness budget: 100 ms.
- Dust exemption cap: 8 books.
- Score breadth target: 80 books.
- Directional Taker entry: OFF.
- Existing final contract validation and hard portfolio/volume safety.

## Validation
- A1.6-specific: 19/19 PASS.
- Research regression: 461 PASS, 2 historical Direct contracts skipped.
- Focused launcher preflight: 89/89 PASS.
- `compileall`: PASS.
- `bash -n run_strategy1_research_simple_multi.sh`: PASS.

## First runtime acceptance gate
Run 500–1000 simulation ticks and verify:
- p50 < 60 ms; p95 < 100 ms.
- RT velocity >= 0.05/s minimum, >= 0.07/s target.
- Positive RT > 50% minimum, >= 55–60% target.
- Directional Taker entries = 0.
- NORMAL/DEFENSIVE negative Taker exits collapse materially.
- Qualification growth >= ~10 new qualified books / 1000 ticks while deficit remains.
- Median RT PnL approximately >= 0 and total PnL not dependent on one extreme winner.
