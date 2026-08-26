# SN79 Research Hybrid Score Utility V4.5

## Scope

Phase 1 Research Agent only. `BaseStrategy.py` and `AdaptiveAgent.py` were not modified.

Policy version:

`hybrid_score_utility_v4_5`

Hybrid version:

`hybrid_maker_taker_v2`

## Objective change

The previous V4.4 normal-risk taker policy was anchored to standalone execution economics:

- holding cost versus taker cost;
- maker-vs-taker fill-hazard EV.

V4.5 keeps that path, but adds a second bounded authorization path based on combined SN79 utility. A taker can now be selected when immediate execution PnL alone is not the entire optimum, provided the action improves total Research utility through faster round-trip realization, Kappa completion/breadth, coverage rotation, capital release, risk reduction, and score velocity.

Conceptually:

`U(action) = PnL + RoundTrip + Kappa + Coverage + CapitalRelease + RiskReduction + Velocity - Downside`

The action comparison is between immediate taker realization and waiting for maker execution.

## New module

`agents/strategy/research_action_utility.py`

Adds:

- `SN79ActionUtilityDecision`
- `evaluate_sn79_action_utility()`
- bounded Kappa completion value
- bounded coverage/redeployment value
- maker-fill-horizon discounted WAIT utility
- immediate TAKER utility
- downside penalty
- configurable maximum score-subsidized negative PnL floor
- utility-derived reduction-size recommendation

## Taker authorization

Normal-risk taker now has two complementary paths:

1. Existing execution-economic path:
   - taker economics passes; and
   - maker-vs-taker hazard EV does not prefer waiting.

2. New SN79 utility path:
   - total taker utility exceeds wait utility by the configured margin; and
   - taker net PnL stays above the bounded score-subsidy loss floor.

Catastrophic hard-risk remains the only unbounded safety override.

The new path is not a blanket taker-frequency switch. Large negative exits cannot be justified by Kappa/round-trip value alone.

## Default V4.5 Research settings

The Testnet multi launcher enables:

- `research_enable_sn79_action_utility=1`
- `research_sn79_pnl_scale_bps=8.0`
- `research_sn79_pnl_weight=1.0`
- `research_sn79_round_trip_weight=0.30`
- `research_sn79_kappa_weight=0.35`
- `research_sn79_coverage_weight=0.15`
- `research_sn79_capital_release_weight=0.15`
- `research_sn79_risk_reduction_weight=0.20`
- `research_sn79_velocity_weight=0.25`
- `research_sn79_downside_weight=0.45`
- `research_sn79_min_utility_margin=0.03`
- `research_sn79_max_score_subsidy_loss_bps=-2.0`
- `research_hybrid_partial_frac_cap=0.90`

The realization ladder is intentionally earlier for this Research experiment:

- passive max: `0.15`
- competitive max: `0.30`
- aggressive-maker max: `0.45`
- above that: taker eligible, still subject to utility/economic authorization.

## Telemetry

The existing REALIZATION / HYBRID / TAKER_DECISION records now include:

- `sn79_taker_utility`
- `sn79_wait_utility`
- `sn79_utility_margin`
- `sn79_taker_net_pnl_bps`
- `sn79_maker_expected_pnl_bps`
- `sn79_round_trip_value`
- `sn79_kappa_value`
- `sn79_coverage_value`
- `sn79_capital_release_value`
- `sn79_risk_reduction_value`
- `sn79_velocity_value`
- `sn79_downside_penalty`
- `sn79_recommended_qty_frac`

These fields are intended for the next Testnet log analysis.

## Tests

Research test suite:

`284 passed`

Additional V4.5 tests cover:

- positive-PnL fast round-trip taker;
- bounded slightly-negative score-utility realization;
- large negative exits blocked by score-subsidy floor;
- high maker-fill/good maker EV prefers waiting;
- new score-utility path can authorize a taker when the legacy holding-cost gate alone is false;
- disabling `research_enable_sn79_action_utility` restores V4.4-style authorization behavior.

Compilation and launcher syntax checks also pass.

## Testnet validation targets

Do not judge V4.5 from raw taker count alone. Compare against the prior Research run on:

- Round-Trip Volume and RoundTripVelocity
- realized PnL
- taker realized PnL / average taker PnL
- Kappa eligible-book count and qualification velocity
- Kappa3 / Kappa3 Score breadth
- coverage velocity
- median/p90 inventory realization time
- inventory/dust growth
- max drawdown/downside
- response latency
- taker utility margin distribution
- count/value of score-subsidized negative takers

A successful V4.5 should use taker materially more often while keeping the negative score-subsidy bounded and improving realized round-trip/Kappa throughput.
