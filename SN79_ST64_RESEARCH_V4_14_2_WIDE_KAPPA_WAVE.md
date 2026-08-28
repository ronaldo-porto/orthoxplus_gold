# SN79 St6.4 Research V4.14.2 — Wide-Kappa Wave

V4.14.2 is a narrow RealNet breadth+density update on top of V4.14.1.

## Why
Agent23 showed positive realized PnL and nonzero raw Kappa, but final score stayed zero. The dominant structural problem is narrow cross-book Kappa breadth plus weak per-book Kappa (~0.15–0.20). Three non-zero realized observations only establish validator eligibility; they are not a quality target.

## Changes
1. Removed the V4.14 parked-count full COVERAGE veto. Parked count remains telemetry; only authoritative total-open / total-BASE headroom can suppress fresh coverage.
2. Increased cohort and pressure-gate exploration from 1 to 2. Density phase now keeps 2 coverage slots; balanced phase keeps 3.
3. Added bounded quality-density state: eligible books remain density-due through 6 observations. Positive-but-weak raw Kappa (<0.35) may continue only to 8 observations. Negative-Kappa or known-inefficient books do not receive forced density.
4. Preserved all V4.14.0/1 risk and validator alignment: 8 total books, 2.0 BASE, bounded-loss escape, V4.13.9 sticky contract guard, and validator history rebase.

## RealNet objective
Run two pipelines together: widen toward 40 then 80+ eligible books while densifying healthy books from 3 -> 6 -> selectively 8 observations. Avoid spending repeated cycles on dense favorites while the cross-book median is still zero-diluted.
