# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Fill-hazard maker-vs-taker EV is the primary hybrid comparison."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)
HYBRID_SRC = (ROOT / "agents" / "strategy" / "research_hybrid.py").read_text(
    encoding="utf-8"
)
HAZARD_SRC = (ROOT / "agents" / "strategy" / "research_fill_hazard.py").read_text(
    encoding="utf-8"
)
EXIT_EV_SRC = (ROOT / "agents" / "strategy" / "research_exit_hazard_ev.py").read_text(
    encoding="utf-8"
)

from research_exit_hazard_ev import (
    DEFAULT_MIN_SAMPLES,
    EXIT_HAZARD_EV_VERSION,
    PRIOR_ANY,
    REASON_MAKER_EV,
    REASON_TAKER_EV,
    compare_maker_taker_exit,
    expected_maker_exit_value,
    expected_taker_exit_value,
    shrink_exit_hazard,
)
from research_fill_hazard import HazardPrediction
from research_hybrid import hybrid_taker_decision
from research_taker_economics import (
    HoldingCostBreakdown,
    REASON_HOLDING_EXCEEDS_COST,
    TakerCostBreakdown,
    TakerEconomicsDecision,
)


def _pred(**kwargs) -> HazardPrediction:
    payload = dict(
        any_fill=0.50,
        actionable_fill=0.40,
        dust=0.05,
        source="cell",
        usable=True,
        n_at_risk=40,
        ttl_ms=500.0,
        remaining_any_fill=0.50,
    )
    payload.update(kwargs)
    return HazardPrediction(**payload)


def _econ(*, holding: float, taker: float, take: bool = True) -> TakerEconomicsDecision:
    return TakerEconomicsDecision(
        take=take,
        reason=REASON_HOLDING_EXCEEDS_COST if take else "TAKER_REJECTED_ECONOMICS",
        holding=HoldingCostBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, holding),
        taker=TakerCostBreakdown(0.0, 0.0, 0.0, 0.0, taker),
        expected_net_realization_pnl=0.0,
        net_floor_bps=0.0,
        economic_ok=take,
        floor_ok=True,
        catastrophic=False,
    )


def test_high_fill_probability_stays_maker_even_if_holding_exceeds_taker_cost():
    comparison = compare_maker_taker_exit(
        prediction=_pred(
            any_fill=0.90,
            actionable_fill=0.85,
            dust=0.02,
            remaining_any_fill=0.90,
        ),
        maker_profit=8.0,
        holding_cost=10.0,
        immediate_realization_value=6.0,
        taker_cost=5.0,
    )
    assert comparison.holding_cost_while_waiting > 0.0
    assert comparison.probs.any_fill >= 0.85
    assert comparison.expected_maker_exit_value > comparison.expected_taker_exit_value
    assert comparison.prefer_taker is False
    assert comparison.reason == REASON_MAKER_EV

    decision = hybrid_taker_decision(
        unrealized_pnl_bps=6.0,
        maker_exit_ev=8.0,
        crossing_cost_bps=5.0,
        economics=_econ(holding=10.0, taker=5.0, take=True),
        hazard=_pred(
            any_fill=0.90,
            actionable_fill=0.85,
            dust=0.02,
            remaining_any_fill=0.90,
        ),
    )
    assert decision.economics is not None
    assert decision.economics.take is True
    assert decision.take is False
    assert decision.reason == REASON_MAKER_EV


def test_low_fill_probability_prefers_taker_when_taker_ev_is_higher():
    comparison = compare_maker_taker_exit(
        prediction=_pred(
            any_fill=0.06,
            actionable_fill=0.03,
            dust=0.02,
            remaining_any_fill=0.06,
        ),
        maker_profit=8.0,
        holding_cost=4.0,
        immediate_realization_value=7.0,
        taker_cost=2.0,
    )
    assert comparison.prefer_taker is True
    assert comparison.reason == REASON_TAKER_EV
    assert comparison.expected_taker_exit_value > comparison.expected_maker_exit_value

    decision = hybrid_taker_decision(
        unrealized_pnl_bps=7.0,
        maker_exit_ev=8.0,
        crossing_cost_bps=2.0,
        economics=_econ(holding=4.0, taker=2.0, take=False),
        hazard=_pred(
            any_fill=0.06,
            actionable_fill=0.03,
            dust=0.02,
            remaining_any_fill=0.06,
        ),
    )
    assert decision.economics is not None
    assert decision.economics.take is False
    # St6.4 final: fill hazard feeds WAIT EV. A positive-EV immediate close with
    # clearly low Maker fill probability is owned by ECONOMIC authority; SCORE
    # authority is reserved for actual ONE_AWAY/TWO_AWAY completion work.
    assert decision.maker_taker_ev is not None
    assert decision.maker_taker_ev.prefer_taker is True
    assert decision.take is True
    assert decision.economic_authorized is True
    assert decision.aggressive_positive_ev_authorized is True
    assert decision.reason == "TAKER_AGGRESSIVE_POSITIVE_EV"

    legacy = hybrid_taker_decision(
        unrealized_pnl_bps=7.0,
        maker_exit_ev=8.0,
        crossing_cost_bps=2.0,
        economics=_econ(holding=4.0, taker=2.0, take=False),
        hazard=_pred(
            any_fill=0.06, actionable_fill=0.03, dust=0.02, remaining_any_fill=0.06,
        ),
        enable_sn79_action_utility=False,
        allow_aggressive_positive_ev_taker=False,
    )
    assert legacy.take is False
    assert legacy.qty_frac == 0.0
    assert decision.qty_frac > 0.0


def test_sparse_samples_shrink_toward_prior():
    observed = _pred(
        any_fill=0.95,
        actionable_fill=0.90,
        dust=0.02,
        remaining_any_fill=0.95,
        n_at_risk=3,
        usable=True,
        source="cell",
    )
    shrunk = shrink_exit_hazard(observed, min_samples=DEFAULT_MIN_SAMPLES)
    assert shrunk.n_at_risk == 3
    assert shrunk.usable is False
    assert shrunk.source == "shrunk"
    assert shrunk.any_fill < 0.95
    assert shrunk.any_fill > PRIOR_ANY
    ripe = shrink_exit_hazard(
        _pred(
            any_fill=0.95,
            actionable_fill=0.90,
            dust=0.02,
            remaining_any_fill=0.95,
            n_at_risk=40,
        )
    )
    assert ripe.usable is True
    assert abs(ripe.any_fill - 0.95) < 1e-12


def test_missing_hazard_uses_conservative_prior_not_certainty():
    missing = shrink_exit_hazard(None)
    assert missing.usable is False
    assert missing.source == "fallback"
    assert abs(missing.any_fill - PRIOR_ANY) < 1e-12
    assert abs(missing.fill_before_horizon - PRIOR_ANY) < 1e-12
    comparison = compare_maker_taker_exit(
        prediction=None,
        maker_profit=20.0,
        holding_cost=1.0,
        immediate_realization_value=2.0,
        taker_cost=1.0,
    )
    certain = expected_maker_exit_value(
        p_fill_horizon=1.0, maker_profit=20.0, holding_cost=1.0,
    )
    assert comparison.p_fill_horizon < 0.5
    assert comparison.expected_maker_exit_value < certain
    assert comparison.expected_taker_exit_value == expected_taker_exit_value(
        immediate_realization_value=2.0, taker_cost=1.0,
    )


def test_high_dust_probability_lowers_maker_exit_value():
    shared = dict(
        any_fill=0.70,
        remaining_any_fill=0.70,
        n_at_risk=40,
        usable=True,
        source="cell",
    )
    actionable = compare_maker_taker_exit(
        prediction=_pred(actionable_fill=0.65, dust=0.02, **shared),
        maker_profit=10.0,
        holding_cost=2.0,
        immediate_realization_value=4.0,
        taker_cost=3.0,
    )
    dusty = compare_maker_taker_exit(
        prediction=_pred(actionable_fill=0.10, dust=0.55, **shared),
        maker_profit=10.0,
        holding_cost=2.0,
        immediate_realization_value=4.0,
        taker_cost=3.0,
    )
    assert dusty.maker_profit < actionable.maker_profit
    assert dusty.expected_maker_exit_value < actionable.expected_maker_exit_value
    assert dusty.probs.dust > actionable.probs.dust
    assert dusty.probs.actionable_fill < actionable.probs.actionable_fill


def test_fill_hazard_model_is_preserved_and_research_wires_exit_ev():
    assert "class FillHazardModel" in HAZARD_SRC
    assert "def predict(self, features: HazardFeatures) -> HazardPrediction:" in HAZARD_SRC
    assert "class FillHazardModel" not in EXIT_EV_SRC
    assert "from research_fill_hazard import HazardPrediction" in EXIT_EV_SRC
    assert EXIT_HAZARD_EV_VERSION == "exit_hazard_ev_v2"
    assert "compare_maker_taker_exit(" in HYBRID_SRC
    assert "ExpectedTakerExitValue" in HYBRID_SRC or "expected_taker_exit_value" in HYBRID_SRC
    assert "RESEARCH_EXIT_HAZARD_EV_VERSION" in RESEARCH_SRC
    assert "def _research_exit_hazard_prediction" in RESEARCH_SRC
    assert "def _research_exit_fill_hazard" in RESEARCH_SRC
    assert "_research_exit_hazard_prediction(" in RESEARCH_SRC
    assert "research_enable_fill_hazard_exit_compare" in RESEARCH_SRC
    assert "expected_maker_exit_value" in RESEARCH_SRC
    assert "expected_taker_exit_value" in RESEARCH_SRC
    predict_src = HAZARD_SRC.split("def predict(self, features: HazardFeatures)")[1].split(
        "def select_policy_probability("
    )[0]
    assert "any_fill" in predict_src
    assert "actionable_fill" in predict_src
    assert "dust" in predict_src
    assert "remaining_any_fill" in predict_src
