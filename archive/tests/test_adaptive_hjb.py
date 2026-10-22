# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Phase 3 Step 3.5: Adaptive HJB controller is shadow-only."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from adaptive_hjb import (
    HjbConfig,
    HjbOverlayBounds,
    HjbState,
    adaptive_gamma,
    compute_hjb_quote,
    propose_hjb_overlay,
    reservation_price,
    run_shadow_campaign,
    shadow_results_allow_overlay,
)


def _state(**overrides) -> HjbState:
    data = dict(
        mid=100.0,
        microprice=100.0,
        spread=0.20,
        alpha=0.0,
        inventory=0.0,
        sigma=5e-4,
        fill_hazard_buy=0.22,
        fill_hazard_sell=0.22,
        actionable_buy=0.18,
        actionable_sell=0.18,
        ofi=0.0,
        markout_bps=0.0,
        toxicity=0.0,
        latency_ms=80.0,
        drawdown=0.0,
        phase="NORMAL",
        base_size=0.25,
        regime="CHOP",
    )
    data.update(overrides)
    return HjbState(**data)


def test_long_inventory_lowers_reservation():
    flat = compute_hjb_quote(_state(sigma=0.01))
    long = compute_hjb_quote(_state(inventory=0.80, sigma=0.01))
    assert flat is not None and long is not None
    assert long.inventory_term > 0.0
    assert long.reservation < flat.reservation
    assert long.bid_size < long.ask_size
    assert long.bid_size <= 0.25
    assert long.ask_size <= 0.25


def test_short_inventory_raises_reservation():
    flat = compute_hjb_quote(_state(sigma=0.01))
    short = compute_hjb_quote(_state(inventory=-0.80, sigma=0.01))
    assert flat is not None and short is not None
    assert short.inventory_term < 0.0
    assert short.reservation > flat.reservation
    assert short.ask_size < short.bid_size


def test_flat_inventory_skew_is_approximately_zero():
    flat = compute_hjb_quote(_state(inventory=0.0, alpha=0.0, sigma=0.01))
    assert flat is not None
    assert abs(flat.inventory) < 1e-12
    assert abs(flat.inventory_term) < 1e-12
    expected, term = reservation_price(
        microprice=100.0,
        alpha_term=0.0,
        inventory=0.0,
        gamma=flat.gamma,
        sigma=0.01,
        horizon=1.0,
        mid=100.0,
        latency_penalty=flat.latency_penalty,
        adverse_penalty=flat.adverse_penalty,
    )
    assert abs(term) < 1e-12
    assert abs(flat.reservation - expected) < 1e-9


def test_hjb_state_requires_real_inputs():
    state = _state(
        inventory=0.40,
        sigma=0.008,
        alpha=0.25,
        fill_hazard_buy=0.19,
        fill_hazard_sell=0.21,
        markout_bps=-1.5,
        latency_ms=120.0,
        regime="STRESSED",
    )
    quote = compute_hjb_quote(state)
    assert quote is not None
    assert quote.inventory == 0.40
    assert quote.sigma == 0.008
    assert quote.alpha == 0.25
    assert quote.fill_hazard_buy == 0.19
    assert quote.fill_hazard_sell == 0.21
    assert quote.markout_bps == -1.5
    assert quote.latency_penalty > 0.0
    assert quote.regime == "STRESSED"


def test_gamma_rises_with_risk_state():
    cfg = HjbConfig()
    calm = adaptive_gamma(_state(), cfg)
    inv = adaptive_gamma(_state(inventory=0.80), cfg)
    tox = adaptive_gamma(_state(toxicity=1.0), cfg)
    dd = adaptive_gamma(_state(drawdown=1.0), cfg)
    drift = adaptive_gamma(_state(phase="DRIFT"), cfg)
    vol = adaptive_gamma(_state(sigma=4e-3), cfg)
    assert inv > calm
    assert tox > calm
    assert dd > calm
    assert drift > calm
    assert vol > calm
    assert cfg.gamma_min <= drift <= cfg.gamma_max


def test_adverse_and_latency_widen():
    calm = compute_hjb_quote(_state())
    toxic = compute_hjb_quote(_state(toxicity=1.0, markout_bps=-8.0, inventory=0.40))
    late = compute_hjb_quote(_state(latency_ms=500.0, inventory=0.40))
    assert calm is not None and toxic is not None and late is not None
    assert toxic.adverse_penalty > calm.adverse_penalty
    assert late.latency_penalty > calm.latency_penalty
    assert (toxic.ask - toxic.bid) > (calm.ask - calm.bid)


def test_positive_alpha_raises_reservation():
    flat = compute_hjb_quote(_state())
    alpha = compute_hjb_quote(_state(alpha=0.70, microprice=100.03))
    assert flat is not None and alpha is not None
    assert alpha.reservation > flat.reservation


def test_shadow_campaign_never_activates_policy():
    evidence = run_shadow_campaign()
    assert evidence["n"] >= 10
    assert evidence["policy_activated"] == 0
    assert all(row["policy_activated"] == 0 for row in evidence["rows"])
    assert evidence["mean_reservation_shift_long"] < 0.0
    assert evidence["mean_reservation_shift_short"] > 0.0
    assert evidence["mean_inventory_term_long"] > 0.0
    assert evidence["mean_inventory_term_short"] < 0.0
    flat = next(r for r in evidence["rows"] if r["scenario"] == "flat_calm")
    long = next(r for r in evidence["rows"] if r["scenario"] == "long_inventory")
    short = next(r for r in evidence["rows"] if r["scenario"] == "short_inventory")
    assert abs(flat["inventory_term"]) < 1e-12
    assert long["reservation"] < flat["reservation"]
    assert short["reservation"] > flat["reservation"]
    assert evidence["mean_gamma_drift"] > evidence["mean_gamma_normal"]
    assert evidence["max_bid_size"] <= evidence["base_size"] + 1e-12
    assert evidence["max_ask_size"] <= evidence["base_size"] + 1e-12
    assert long["hjb_bid_size"] < long["hjb_ask_size"]


def test_invalid_market_returns_none():
    assert compute_hjb_quote(_state(mid=0.0)) is None
    assert compute_hjb_quote(_state(spread=0.0)) is None


def test_shadow_results_allow_conservative_overlay():
    evidence = run_shadow_campaign()
    assert shadow_results_allow_overlay(evidence) is True


def test_overlay_holds_when_ev_is_not_positive_and_not_defensive():
    quote = compute_hjb_quote(_state(inventory=0.0, markout_bps=0.0, toxicity=0.0))
    assert quote is not None
    decision = propose_hjb_overlay(
        quote,
        base_bid=99.95,
        base_ask=100.05,
        mid=100.0,
        spread=0.20,
        base_ev=quote.estimated_ev + 1.0,
        phase="NORMAL",
    )
    assert decision.applied is False
    assert decision.reason == "HJB_EV_NOT_POSITIVE"
    assert decision.size_scale == 1.0


def test_overlay_is_bounded_and_never_increases_size():
    quote = compute_hjb_quote(_state(inventory=0.70, markout_bps=-6.0, toxicity=0.80, sigma=0.01))
    assert quote is not None
    decision = propose_hjb_overlay(
        quote,
        base_bid=99.95,
        base_ask=100.05,
        mid=100.0,
        spread=0.20,
        base_ev=0.0,
        phase="NORMAL",
        bounds=HjbOverlayBounds(),
        base_size=0.25,
    )
    assert decision.applied is True
    assert decision.size_scale <= 1.0 + 1e-12
    assert decision.exit_urgency_scale >= 1.0 - 1e-12
    assert abs(decision.center_shift_frac) <= 0.05 + 1e-12
    assert 1.0 - 0.02 - 1e-12 <= decision.spread_scale <= 1.0 + 0.06 + 1e-12
    assert decision.buy_bias_scale <= 1.0 + 1e-12
    assert decision.center_shift_frac <= 1e-12


def test_overlay_observe_holds():
    quote = compute_hjb_quote(_state(inventory=0.80, markout_bps=-8.0, toxicity=1.0))
    decision = propose_hjb_overlay(
        quote,
        base_bid=99.95,
        base_ask=100.05,
        mid=100.0,
        spread=0.20,
        base_ev=-1.0,
        phase="OBSERVE",
    )
    assert decision.applied is False
    assert decision.reason == "HOLD_PHASE"
