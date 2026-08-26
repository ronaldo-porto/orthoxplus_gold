# SN79 Research Test Agent V4.10

V4.10 is a Research-only score-up correction. BaseStrategy and AdaptiveAgent are intentionally unchanged.

## Critical changes

- Hard Taker authority: no `taker_authority=NONE` market exit can be created by the hybrid decision.
- Zero-loss score completion defaults: SCORE Taker cannot intentionally buy Kappa observations with negative expected net PnL.
- Live per-book fee rates: maker/taker economics use current account fee tiers instead of a fixed ~1 bps model.
- Rolling Kappa state: qualification uses non-zero realized-PnL buckets inside the exact 3h simulation lookback, not monotonic lifetime counters.
- Expiry-aware Kappa refresh: already-qualified books receive ranking pressure before required observations roll out.
- Minimum-order recheck cache: recently UNSAFE books stop consuming completion/coverage candidate slots for 20 ticks.
- Candidate deep-evaluation cap reduced 20 -> 12.
- Effective exposure guard: one same-side resting/pending order per book and no exit overshoot/reversal.
- Normal RISK direct Taker disabled; catastrophic hard-risk remains available.

## Promotion target

Maintain 45-50 rolling Kappa-qualified books with >=3 recent positive-quality realized observations while improving realized PnL and avoiding systematic Taker losses.
