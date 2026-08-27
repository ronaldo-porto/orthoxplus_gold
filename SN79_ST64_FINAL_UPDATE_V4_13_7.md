# SN79 St6.4 FINAL Update — V4.13.7

## Purpose

V4.13.7 is a focused qualified/Core execution-feasibility correction on top of the verified V4.13.6 density scheduler.

The 829-tick V4.13.6 long run proved that density creation worked: Kappa eligible rose 9→14, ONE_AWAY fell 6→2, TWO_AWAY fell 53→49, CORE grew 3→6, positive RT ratio was 76.9%, realized PnL was about +1.098 QUOTE, and `LANE_NOT_GRANTED=0`.

However, trading stopped completely after about tick 475. Post-500 KAPPA_COMPLETION decisions were dominated by `ZERO_ORDER_SIZE` and `TTL_STALE` on already-qualified positive-EV books. V4.13.7 fixes only those two execution blockers.

## Change 1 — Qualified/Core exact-minimum recycle admission

Added `qualified_core_exact_min_v4_13_7` in `research_entry_size.py`.

A scheduler-proven CORE/RECYCLING book may use exactly one venue-minimum Maker clip when all of the following are true:

- book is already qualified (`observations_remaining == 0`),
- book is in the active productivity CORE or RECYCLING set,
- deep `trading_ev > configured floor` (default 0),
- inventory risk is below the existing bounded threshold,
- volume headroom is healthy,
- modeled exit capacity is at least the configured fraction of the venue minimum,
- hard inventory room and global open/exposure caps still allow the clip.

Default configuration:

- `research_qualified_core_exact_min_enabled=1`
- `research_qualified_core_exact_min_ev=0.0`
- `research_qualified_core_exact_min_max_inventory_risk=0.35`
- `research_qualified_core_exact_min_exit_fraction=0.20`
- `research_qualified_core_exact_min_min_headroom=0.25`

Admission trigger: `QUALIFIED_CORE_EXACT_MIN`.

This is not a global size relaxation. Unknown/ordinary qualified books do not receive the override; scheduler-proven productivity is required.

## Change 2 — Qualified/Core bounded velocity-STALE Maker TTL

Added `qualified_core_velocity_stale_ttl_v4_13_7` in `research_quote_hysteresis.py`.

The existing ONE_AWAY STALE rescue remains unchanged. V4.13.7 adds a second narrow fallback only when:

- the current authoritative lane is KAPPA_COMPLETION,
- the book is scheduler-proven CORE/RECYCLING,
- the book is already qualified (`completion_samples >= target`),
- deep trading EV is positive,
- the only TTL rejection reason is `STALE`,
- regime is neither TOXIC nor STRESSED.

The fallback remains Maker/post-only and uses a short bounded TTL no longer than 250 ms under the normal default configuration.

Event: `QUALIFIED_CORE_TTL_RESCUE`.

Defaults:

- `research_qualified_core_stale_ttl_enabled=1`
- `research_qualified_core_stale_ttl_ms=250`

## Frozen behavior

V4.13.7 intentionally does not change:

- V4.13.6 deep-EV completion cache, density priority, or dynamic lane budget,
- V4.13.5 Positive-Maker Veto / Fresh Maker Grace,
- V4.13.4 authoritative execution-lane propagation,
- Score-EV `NEGATIVE_EV` hard gate,
- alpha/signals,
- normal sizing outside the exact-minimum productive-Core override,
- Maker/Taker economics or rescue floors,
- parking,
- concurrency,
- persistent Maker/hysteresis,
- contract guard,
- latency/ranking implementation.

## Verification

- Focused V4.13.7 CORE-recycle tests: 8 passed.
- Focused V4.13.7 + V4.13.6 density/entry/TTL/flywheel regression: 60 passed.
- All Research tests: 472 passed.
- Python compile: PASS.
- Root multi-launcher bash syntax: PASS.
- `RESEARCH_PREFLIGHT_ONLY=1 ./run_strategy1_research_test_multi.sh`: PASS.

Exact regressions include the V4.13.6 post-500 size shapes:

- Book67-like safe size 0.08273 / exit capacity 0.08447 / venue min 0.25,
- Book46-like 0.08479 / 0.09843 / 0.25,
- Book87-like 0.06201 / 0.07156 / 0.25,

plus negative-EV, unknown-productivity, excessive-risk, poor-headroom, weak-exit-capacity, TOXIC/STRESSED, and non-STALE rejection cases.

## Testnet acceptance

Run V4.13.7 through at least 600–900 ticks; the critical proof is after tick 500:

- limit quotes after tick500 > 0,
- fills after tick500 > 0,
- CORE RT after tick500 > 0,
- positive-EV CORE `ZERO_ORDER_SIZE` drops sharply,
- positive-EV CORE `TTL_STALE` drops sharply,
- Kappa eligible continues rising or at minimum remains stable,
- positive RT ratio remains >60%,
- realized PnL remains positive,
- RT velocity remains >0.02/s and trends back toward >0.03/s,
- `LANE_NOT_GRANTED=0` remains true.

Do not optimize parking or latency until this second-half recycling path is proven live.
