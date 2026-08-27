# SN79 St6.4 Adaptive V4.13.9 — Minimal Contract/Persistence Hotfix

## Purpose
V4.13.9 fixes the repeated profitable Maker-exit contract loop observed in the 779-tick Adaptive V4.13.8 run without adding a new cache or state machine.

Observed failure shape (Book106): profitable Maker exit desired price -> CONTRACT_VIOLATION -> authoritative guard safe reprice -> accepted -> V4.13.8 recomputed the old unsafe price -> repeated rejection.

## Minimal fix
1. Reuse the existing `(book, side)` contract-reject guard for live inventory exits.
2. Before V4.13.8 `PROFITABLE_EXIT_HOLD`, apply `guarded_post_only_price()` to the Maker exit price when an existing guard is active.
3. When the repaired Maker order is accepted and that side is still the current inventory exit side, retain the existing guard instead of clearing it.
4. Existing lifecycle logic clears the guard on FLAT/CROSS; the existing 512-tick hard lifetime remains the fallback expiry.
5. Non-exit accepted Maker orders keep the prior ACCEPT_CLEAR behavior.

No new safe-price dictionary, no new lifecycle state machine, no scheduler change, no Taker/economic change, and no Adaptive policy redesign.

## Authority ordering
For affected Maker exits only:
`raw exit price -> existing contract guard clamp -> V4.13.8 profitable HOLD/PERSIST -> instruction`

This makes contract legality authoritative before queue-persistence optimization.

## Frozen behavior
- V4.13.8 profitable Maker-exit persistence/hold
- V4.13.7 qualified-Core exact-min and stale-TTL rescue
- V4.13.6 density/deep-EV scheduler
- V4.13.5 Positive-Maker authority / Maker grace
- V4.13.4 authoritative execution lanes
- Score-EV, inventory hard safety, post-only rules, Taker floors, alpha/signals
- Adaptive EV/rank/size/regime overlays and 6/10 activity caps

## Runtime identity
- Research: `simplified_kappa_productivity_v4_13_9`
- Base: `base_v4_13_9_champion`
- Adaptive: `adaptive_v4_13_9_realtime`
- Hotfix contract: `sticky_exit_contract_guard_v4_13_9`

## Verification
- Python compile: PASS
- Research + active Adaptive + promotion + V4.13.9 contract regression: 552 passed
- Focused guard/persistence regression: 32 passed
- Research launcher preflight: PASS
- Base launcher preflight: PASS
- Adaptive launcher preflight: PASS

The full repository suite is not used as an acceptance surface in this container because unrelated validator tests require packages such as `bittensor` and `loky` that are not installed here.

## Immediate Testnet smoke gate
Run Adaptive V4.13.9 for about 100–150 ticks (longer only if needed to exercise a contract repair).

Required:
- repeated same book/side contract-reject loop = 0
- `EXIT_PRICE_CLAMP` / `ACCEPT_RETAIN_EXIT_GUARD` appear if a reject/repair opportunity occurs
- `PROFITABLE_EXIT_HOLD` remains active
- positive RT >= 65%
- realized PnL > 0
- RT velocity >= 0.02/s preferred
- placements/fill < 25
- `LANE_NOT_GRANTED = 0`
- no negative-Taker-over-positive-Maker regression

If those pass, promote this exact tree to `ADAPTIVE_REALNET_CANDIDATE_1` without another strategy redesign.
