# SN79 St6.4 Final Research Update — V4.12.9

This release is intentionally narrow. It does not add a new alpha model, regime engine, HJB layer, or open-book expansion.

## Confirmed changes

1. **Operational breadth target**
   - `research_score_target_books`: 45 -> 88.
   - The target now controls breadth suppression through `score_deficit > 0`; it is no longer telemetry-only.
   - At/above 88, forced breadth suppression turns off.

2. **Faster incomplete-book rotation**
   - `research_qualified_suppression_min_incomplete`: 2 -> 1.
   - One productive ONE_AWAY/TWO_AWAY book can now reclaim new-acquisition capacity from stable-qualified books.
   - Critical expiry refresh remains protected.

3. **SCORE-Taker correctness**
   - SCORE authority is reserved for actual Kappa progress: `0 < observations_remaining < required_observations`.
   - SCORE authority also requires actual Maker-fill evidence (hazard, scalar fill estimate, or failed-exit evidence).
   - Missing hazard does not imply a dead Maker.

4. **Authority-state correctness**
   - If an authority is eligible but its direct feature gate is disabled, telemetry now preserves the latent eligibility instead of erasing it.
   - This separates `eligible but gated` from `not eligible`.

5. **Bounded positive-EV Taker unlock**
   - The positive-EV path can use the actual hazard prediction, not only the scalar fallback.
   - Low Maker fill trigger: 5% -> 8%.
   - It still requires non-negative Taker EV, EV superiority by the configured margin, and an explicit trigger.
   - A 40% Maker fill probability does not qualify as low-fill.

## Frozen safety/performance controls

- `candidate_count=10`
- `max_open_books=6`
- `research_stale_maker_rescue_floor_bps=-1.0`
- `research_protective_taker_loss_floor_bps=-2.0`
- `research_aggressive_positive_ev_min_net_bps=0.0`
- `research_enable_risk_taker_direct=0`
- `research_p95_target_ms=120`

## Runtime release gate

Do not promote to BaseStrategy until the next Research run shows:

- eligible/qualified breadth rising rather than decaying;
- ONE_AWAY turnover improving;
- TWO_AWAY backlog stable or declining;
- RT conversion >= 0.45;
- RT velocity above the V4.14.8 baseline;
- inventory-age p90 substantially below the prior ~292-tick tail, target <150;
- Maker realized PnL remains positive;
- Taker is non-zero only through bounded authority;
- p95 trends toward/under 120 ms.

One confirmed blocker fix maximum after this runtime test.
