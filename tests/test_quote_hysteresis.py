# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Production quote hysteresis, bounded TTL, and dust-prevention helpers."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from quote_hysteresis import (
    choose_ttl_ms,
    clamp_ttl_ms,
    predicted_dust_blocks_increase,
    should_replace_quote,
    would_create_dust,
)


def test_tiny_price_move_does_not_replace():
    dec = should_replace_quote(
        old_price=100.00,
        new_price=100.004,
        tick_size=0.01,
        min_price_ticks=1.0,
        old_ev=0.20,
        new_ev=0.21,
        ev_improve_threshold=0.04,
    )
    assert dec.cancel is False
    assert dec.reason == "HOLD"
    assert dec.price_delta_ticks < 1.0


def test_meaningful_price_move_does_replace():
    dec = should_replace_quote(
        old_price=100.00,
        new_price=100.03,
        tick_size=0.01,
        min_price_ticks=1.0,
    )
    assert dec.cancel is True
    assert dec.reason == "PRICE"


def test_hard_safety_cancels_immediately():
    dec = should_replace_quote(
        old_price=100.00,
        new_price=100.00,
        tick_size=0.01,
        hard_safety=True,
    )
    assert dec.cancel is True
    assert dec.reason == "HARD_SAFETY"
    toxic = should_replace_quote(
        old_price=100.00,
        new_price=100.00,
        tick_size=0.01,
        new_toxic=True,
    )
    assert toxic.cancel is True
    assert toxic.reason == "HARD_SAFETY"


def test_ttl_is_bounded_with_legacy_fallback_range():
    ttl, reason, info = choose_ttl_ms(
        baseline_ms=500.0,
        min_ms=200.0,
        max_ms=800.0,
        fill_hazard=0.90,
        volatility=0.0005,
        imbalance=0.02,
        toxicity=False,
        market_regime="QUIET",
    )
    assert ttl is not None
    assert 200.0 <= ttl <= 800.0
    assert clamp_ttl_ms(50.0, 200.0, 800.0) == 200.0
    assert clamp_ttl_ms(5000.0, 200.0, 800.0) == 800.0
    assert reason in {"STABLE_LONG", "BASELINE"}
    assert "fill_hazard" in info


def test_toxic_shortens_ttl_and_stale_skips():
    base, _, _ = choose_ttl_ms(
        baseline_ms=500.0, min_ms=150.0, max_ms=900.0, market_regime="NORMAL",
    )
    toxic, reason, _ = choose_ttl_ms(
        baseline_ms=500.0, min_ms=150.0, max_ms=900.0,
        toxicity=True, market_regime="TOXIC",
    )
    assert toxic is not None and base is not None
    assert toxic < base
    assert reason == "TOXIC_SHORT"
    stale, stale_reason, _ = choose_ttl_ms(
        baseline_ms=500.0, min_ms=200.0, max_ms=900.0,
        microprice_velocity=5.0, stale_velocity_ticks=3.0,
    )
    assert stale is None
    assert stale_reason == "STALE"


def test_prevents_sub_minimum_flat_to_dust():
    assert would_create_dust(
        inventory_before=0.0,
        signed_fill_qty=0.10,
        min_order_size=0.25,
    )
    assert not would_create_dust(
        inventory_before=0.0,
        signed_fill_qty=0.25,
        min_order_size=0.25,
    )


def test_allows_strict_dust_reduction_not_increase():
    assert would_create_dust(
        inventory_before=0.10,
        signed_fill_qty=0.05,
        min_order_size=0.25,
    )
    assert not would_create_dust(
        inventory_before=0.10,
        signed_fill_qty=0.25,
        min_order_size=0.25,
    )


def test_predicted_dust_blocks_exposure_increase_only():
    assert predicted_dust_blocks_increase(
        dust_prob=0.40,
        dust_target=0.15,
        inventory_before=0.0,
        signed_qty=0.25,
        usable=True,
    )
    assert not predicted_dust_blocks_increase(
        dust_prob=0.40,
        dust_target=0.15,
        inventory_before=0.40,
        signed_qty=-0.25,
        usable=True,
    )
    assert not predicted_dust_blocks_increase(
        dust_prob=0.40,
        dust_target=0.15,
        inventory_before=0.0,
        signed_qty=0.25,
        usable=False,
    )
