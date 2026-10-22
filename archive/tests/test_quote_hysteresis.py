# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Production quote hysteresis, bounded TTL, and dust-prevention helpers."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from quote_hysteresis import (
    choose_ttl_ms,
    clamp_ttl_ms,
    ofi_reversed,
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


def test_ofi_reversal_replaces_imbalance_does_not():
    hold = should_replace_quote(
        old_price=100.00,
        new_price=100.004,
        tick_size=0.01,
        min_price_ticks=2.0,
        old_imbalance=0.60,
        new_imbalance=-0.55,
        old_ofi=None,
        new_ofi=None,
    )
    assert hold.cancel is False
    assert hold.reason == "HOLD"
    assert ofi_reversed(None, -0.40) is False
    replace = should_replace_quote(
        old_price=100.00,
        new_price=100.004,
        tick_size=0.01,
        min_price_ticks=2.0,
        old_ofi=0.35,
        new_ofi=-0.30,
    )
    assert replace.cancel is True
    assert replace.reason == "OFI"


def test_one_tick_move_holds_with_frozen_default():
    dec = should_replace_quote(
        old_price=100.00,
        new_price=100.01,
        tick_size=0.01,
    )
    assert dec.cancel is False
    assert dec.reason == "HOLD"
    assert abs(dec.price_delta_ticks - 1.0) < 1e-9


def test_imbalance_does_not_shorten_ttl_without_ofi():
    calm, reason, _ = choose_ttl_ms(
        baseline_ms=500.0, min_ms=200.0, max_ms=800.0,
        imbalance=0.90, ofi=None, volatility=0.001,
    )
    assert calm is not None
    assert reason == "BASELINE"
    short, short_reason, _ = choose_ttl_ms(
        baseline_ms=500.0, min_ms=200.0, max_ms=800.0,
        ofi=0.40, volatility=0.001,
    )
    assert short is not None and calm is not None
    assert short < calm
    assert short_reason == "ADVERSE_SHORT"


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
