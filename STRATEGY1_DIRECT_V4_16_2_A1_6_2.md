# Strategy1-Direct V4.16.2 A1.6.2 — Final Liveness Closure

A1.6.2 is a narrow runtime-liveness correction over A1.6.1. Trading economics,
FastPath selection, order size, exposure limits, quote geometry, TTL, and exit
policy are unchanged.

## Runtime defects closed

1. **Dust selector wiring** — the Direct build override now calls
   `_select_dust_compaction_books(state)` before `_direct_compact_selected_dust()`.
   This closes the A1.6.1 path where compaction attempts/orders stayed at zero.

2. **Final open-book accounting** — Direct final validation now temporarily
   expands only `research_max_total_open_books` by the full current sub-minimum dust
   count while delegating placement checks to the authoritative Research final
   validator. Exact dust BASE remains in total absolute exposure, so the 2.0 BASE
   risk cap is unchanged.

3. **Placement-only final validation preserved** — cancellation/control
   instructions bypass quantity validation exactly as in A1.6.1.

## Frozen strategy parameters

- FastPath candidates: 20
- Deep candidates: 16
- Maker acquisition size: 0.25 BASE
- Absolute BASE cap: 2.0
- Total productive open-book cap: 8
- Maker geometry cap: 6 bps
- Maker TTL: 75 ms
- Directional Taker entry: OFF
- Observable entry/exit economics: unchanged

## Required runtime proof

First gate: 500–1,000 ticks.

- `dust_compact_attempts > 0` when theorem-safe dust exists
- `dust_compact_orders > 0` when placement is possible
- `OPEN_BOOK_CAP` rejects do not explode merely because dust count reaches 8
- RT velocity > 0.08/s; target > 0.10/s
- positive RT >= 60%
- Maker→Maker >= 85%
- p95 latency < 120 ms

If the first gate passes, continue the same process to 2,000–4,000 ticks before
persistent Maker execution or variable sizing is introduced.
