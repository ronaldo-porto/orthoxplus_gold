# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Phase 3 Step 5: Adaptive HJB controller is shadow-only."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from adaptive_hjb import (
    HjbConfig,
    HjbState,
    adaptive_gamma,
    compute_hjb_quote,
    run_shadow_campaign,
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
    flat = compute_hjb_quote(_state())
    long = compute_hjb_quote(_state(inventory=0.80))
    assert flat is not None and long is not None
    assert long.reservation < flat.reservation
    assert long.bid_size < long.ask_size
    assert long.bid_size <= 0.25
    assert long.ask_size <= 0.25


def test_short_inventory_raises_reservation():
    flat = compute_hjb_quote(_state())
    short = compute_hjb_quote(_state(inventory=-0.80))
    assert flat is not None and short is not None
    assert short.reservation > flat.reservation
    assert short.ask_size < short.bid_size


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


def test_positive_alpha_and_ofi_raise_reservation():
    flat = compute_hjb_quote(_state())
    alpha = compute_hjb_quote(_state(alpha=0.70, ofi=0.40, microprice=100.03))
    assert flat is not None and alpha is not None
    assert alpha.reservation > flat.reservation


def test_shadow_campaign_never_activates_policy():
    evidence = run_shadow_campaign()
    assert evidence["n"] >= 10
    assert evidence["policy_activated"] == 0
    assert all(row["policy_activated"] == 0 for row in evidence["rows"])
    assert evidence["mean_reservation_shift_long"] < 0.0
    assert evidence["mean_reservation_shift_short"] > 0.0
    assert evidence["mean_gamma_drift"] > evidence["mean_gamma_normal"]
    assert evidence["max_bid_size"] <= evidence["base_size"] + 1e-12
    assert evidence["max_ask_size"] <= evidence["base_size"] + 1e-12
    long = next(r for r in evidence["rows"] if r["scenario"] == "long_inventory")
    assert long["hjb_bid_size"] < long["hjb_ask_size"]


def test_invalid_market_returns_none():
    assert compute_hjb_quote(_state(mid=0.0)) is None
    assert compute_hjb_quote(_state(spread=0.0)) is None
