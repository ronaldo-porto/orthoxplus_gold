# Strategy1-Direct V4.16.2 A1.8 — Cycle-Bounded Maker Exit Realization

A1.8 is a **STRUCTURAL useful-volume update** on top of A1.7.5.  It does not
increase position size or active-book count and adds no market-specific entry
heuristic.  The only trading change is the lifetime of a profitable resting
Maker exit.

## Why this lever

The A1.7.5 runtime showed that useful-volume throughput is dominated by exit
realization time, while the six-book ceiling is not continuously binding.  The
Direct final validator correctly allows only one live order batch per book.
Under A1.7.5, however, a profitable Maker exit can use the legacy 3000 ms
persistence window even though existing exit-cycle TTLs are about one publish
cycle (QUIET 950 ms, ONE_AWAY 975 ms).  While that order remains live, the
ownership invariant intentionally blocks replacement, so a newer touch/ladder
state cannot refresh the quote.

## One clean change

A1.8 keeps profitable Maker persistence enabled but caps its Direct effective
TTL at the existing exit-cycle ceiling:

```
effective_profitable_exit_ttl
    = min(legacy_profitable_exit_ttl,
          max(quiet_exit_ttl, one_away_exit_ttl))
```

With the shipped A1.7.5 configuration this is:

```
3000 ms -> 975 ms
```

This is not a new observed threshold.  It reuses the already-established exit
cycle TTLs.  If those existing TTLs change, A1.8 follows them.  The helper can
only shorten persistence; it never lengthens an already-shorter TTL.

The expected effect is that a profitable Maker exit remains live for the
current market cycle, then becomes eligible to refresh from the next state
instead of staying stale for several cycles.  Price formation, maker ladder,
and economics are unchanged.

## Frozen from A1.7.5

- `mm_base_size = 0.25 BASE`
- `research_max_open_books = 6`
- `research_max_active_open_books = 6`
- `research_max_total_abs_base = 2.0 BASE`
- A1.7.5 relative tail authority and 60-tick budget
- A1.7.4.5 QUIET/no-rebate entry gate (15 bps)
- A1.7.4.3.2 exact ownership release and in-flight reservation
- Maker exit prices and PASSIVE/COMPETITIVE/AGGRESSIVE ladder thresholds
- all Taker authorization, recovery, and catastrophic paths
- FastPath and latency logic
- dust handling

A1.8 therefore cannot obtain more volume by loosening Taker exits or entry
quality.  The experiment asks only whether fresher Maker-exit quotes shorten
realization time.

## Telemetry

Startup:

- `A18_EXIT_REFRESH_CONFIG`
- `direct_exit_refresh_version=direct_exit_refresh_v4_16_2_a1_8`
- `a18_legacy_profitable_exit_ttl_ms`
- `a18_profitable_exit_ttl_ms`

Runtime / summary:

- `direct_a18_legacy_profitable_exit_ttl_ms`
- `direct_a18_profitable_exit_ttl_ms`

Existing `PROFITABLE_EXIT_PERSIST` remains authoritative and now reports the
shorter chosen TTL when A1.8 applies.

## Runtime gate

Compare directly with A1.7.5.  A1.8 is useful only if Maker realization
improves without converting quality into Taker volume.

Primary targets:

- median / p90 `rt_exit_wait_s` lower
- RT velocity and RT BASE/QUOTE per tick higher
- Maker-ending positive ratio approximately preserved
- Taker-ending share not increased
- Kappa downside tail not worsened
- global exposure remains <= 2.0 BASE
- ownership mismatch / same-book stacking remains zero
- p95 response remains < 120 ms

Rollback to A1.7.5 if exit wait does not improve materially or if Maker quality,
Taker share, Kappa tail, ownership, or latency regress.
