# SN79 St6.4 Research V4.12.13 — Pending-Reprice Guard Liveness Fix

**Date:** 2026-08-26  
**Scope:** Research Agent only. BaseStrategy and AdaptiveAgent are unchanged.

## Confirmed V4.12.12 runtime defect

V4.12.12 reduced immediate post-only `CONTRACT_VIOLATION` spam, but runtime
verification showed the guard's **32-tick decay** was shorter than real no-touch
gaps. Reject sequences such as `44 -> 77` and `709 -> 742 -> 776 -> 809`
therefore restarted at streak 1 before the safe-reprice branch could run.

Observed V4.12.12 telemetry:

- `REGISTER_REJECT > 0`
- `COOLDOWN_SKIP > 0`
- `NO_TOUCH_SKIP = 333`
- `REPRICE_RETRY = 0`
- `ACCEPT_CLEAR = 0`

The immediate spam was reduced, but the pending retry was losing liveness.

## V4.12.13 fix

`research_contract_guard.py` now models one rejection episode with:

- `first_reject_tick`
- `last_reject_tick`
- `blocked_until_tick`
- persistent `streak`

Behavior:

1. A real contract reject starts `PENDING_REPRICE`.
2. Cooldown remains **1 -> 2 -> 4 -> 8 ticks**, capped at 8.
3. `NO_TOUCH_SKIP` does **not** clear/reset the episode.
4. Rejects after 33–40+ tick gaps continue the same streak.
5. As soon as fresh bid/ask touch exists, the next Maker retry uses the existing
   safe post-only reprice rule and 1..3 tick cushion.
6. Accepted limit order clears the exact `(book, side)` state.
7. `FLAT` / `CROSS` clears both side guards for that book.
8. A separate **512-tick hard safety lifetime** prevents permanent state.
9. Session transition still clears all reject state.
10. Market/Taker instructions remain outside the guard.

## Frozen behavior

No changes to:

- V4.12.10 bounded stale Taker bridge;
- V4.12.11 ONE_AWAY exact-min and TTL rescue;
- alpha/signals;
- Kappa scheduler;
- candidate count (`10`);
- max open books (`6`);
- BaseStrategy;
- AdaptiveAgent.

## New/updated telemetry

`CONTRACT_REJECT_GUARD` can now show:

- `REGISTER_REJECT`
- `COOLDOWN_SKIP`
- `NO_TOUCH_SKIP` with `pending_reprice=1`
- `REPRICE_RETRY`
- `ACCEPT_CLEAR`
- `LIFECYCLE_CLEAR`
- `HARD_EXPIRE_CLEAR`

Summary additionally exposes lifecycle and hard-expiry clear counters and the
512-tick hard lifetime.

## Static verification

- Focused V4.12.13 liveness tests: **13 passed / 0 failed**
- Full Research: **372 passed / 0 failed**
- Base/Adaptive: **126 passed / 0 failed**
- Shared strategy: **93 passed / 0 failed**
- Total strategy-focused: **591 passed / 0 failed**
- Python compile: **PASS**
- Launcher syntax: **PASS**
- Launcher preflight: **PASS**

Preflight output:

```text
V4.12.3 RealizationDecision.unified_exit API OK
V4.12.10 bounded stale bridge API OK
V4.12.11 ONE_AWAY completion rescue API OK
V4.12.13 pending-reprice contract guard API OK
```

## Next runtime gate

Test **45–60 real minutes**. Extend toward 90 minutes only if too few contract
reject episodes occur.

PASS criteria:

1. `REPRICE_RETRY > 0` when a guarded book eventually regains fresh touch;
2. `ACCEPT_CLEAR > 0` and/or valid `LIFECYCLE_CLEAR` after guarded episodes;
3. reject streak survives >32-tick no-touch gaps instead of resetting to 1;
4. contract reject rate target `<0.5%` of limit placements;
5. no long same-book/side repeated rejection loop;
6. ONE_AWAY and Taker behavior do not regress.
