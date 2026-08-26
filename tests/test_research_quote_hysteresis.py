# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research V4.3 Phase 5: hysteresis, adaptive TTL, dust escape."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from research_quote_hysteresis import (
    choose_ttl_ms,
    clamp_ttl_ms,
    dust_escape_allowed,
    ofi_reversed,
    projected_inventory_after,
    should_replace_quote,
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
    assert dec.price_delta_ticks >= 2.0


def test_alpha_reversal_does_replace():
    dec = should_replace_quote(
        old_price=100.00,
        new_price=100.002,
        tick_size=0.01,
        min_price_ticks=1.0,
        old_alpha=0.25,
        new_alpha=-0.22,
    )
    assert dec.cancel is True
    assert dec.reason == "ALPHA"


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


def test_ttl_is_bounded():
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


def test_toxic_state_shortens_ttl():
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
    adv, adv_reason, _ = choose_ttl_ms(
        baseline_ms=500.0, min_ms=150.0, max_ms=900.0,
        volatility=0.02, imbalance=-0.6, market_regime="NORMAL",
    )
    assert adv is not None and adv < base
    assert adv_reason == "ADVERSE_SHORT"


def test_safe_state_may_lengthen_ttl():
    base, _, _ = choose_ttl_ms(
        baseline_ms=500.0, min_ms=200.0, max_ms=900.0, market_regime="NORMAL",
    )
    long, reason, _ = choose_ttl_ms(
        baseline_ms=500.0, min_ms=200.0, max_ms=900.0,
        fill_hazard=0.50, volatility=0.001, imbalance=0.05,
        toxicity=False, market_regime="QUIET",
    )
    assert long is not None and base is not None
    assert long > base
    assert reason == "STABLE_LONG"
    stale, stale_reason, _ = choose_ttl_ms(
        baseline_ms=500.0, min_ms=200.0, max_ms=900.0,
        microprice_velocity=5.0, stale_velocity_ticks=3.0,
    )
    assert stale is None
    assert stale_reason == "STALE"


def test_one_tick_theoretical_move_holds():
    dec = should_replace_quote(
        old_price=100.00,
        new_price=100.01,
        tick_size=0.01,
        min_price_ticks=2.0,
        old_ev=0.20,
        new_ev=0.22,
        ev_improve_threshold=0.06,
        chosen_ttl=500.0,
        order_age_ms=80.0,
    )
    assert dec.cancel is False
    assert dec.reason == "HOLD"
    assert dec.cancel_reason == "HOLD"
    assert dec.quote_age == 80.0
    assert dec.chosen_ttl == 500.0
    log = dec.as_log(book=3, side="buy")
    assert log["cancel_reason"] == "HOLD"
    assert log["old_ev"] == 0.20
    assert log["new_ev"] == 0.22
    assert log["quote_age"] == 80.0
    assert log["chosen_ttl"] == 500.0


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


def test_inventory_regime_toxicity_ttl_and_ev_replace():
    inv = should_replace_quote(
        old_price=100.00, new_price=100.00, tick_size=0.01,
        old_inventory_state="FLAT", new_inventory_state="LONG",
    )
    assert inv.cancel is True and inv.reason == "INVENTORY"
    regime = should_replace_quote(
        old_price=100.00, new_price=100.00, tick_size=0.01,
        old_regime="NORMAL", new_regime="STRESSED",
    )
    assert regime.cancel is True and regime.reason == "REGIME"
    hold_util = should_replace_quote(
        old_price=100.00, new_price=100.00, tick_size=0.01,
        old_toxic=False, new_toxic=False,
        old_regime="NORMAL", new_regime="NORMAL",
        old_inventory_util=0.10, new_inventory_util=0.12,
    )
    assert hold_util.cancel is False
    tox = should_replace_quote(
        old_price=100.00, new_price=100.00, tick_size=0.01,
        old_toxic=False, new_toxic=False,
        old_regime="NORMAL", new_regime="NORMAL",
        old_inventory_util=0.10, new_inventory_util=0.40,
    )
    assert tox.cancel is True and tox.reason == "INVENTORY"
    tox_on = should_replace_quote(
        old_price=100.00, new_price=100.00, tick_size=0.01,
        old_toxic=False, new_toxic=True,
    )
    assert tox_on.cancel is True and tox_on.reason == "HARD_SAFETY"
    tox_off = should_replace_quote(
        old_price=100.00, new_price=100.00, tick_size=0.01,
        old_toxic=True, new_toxic=False,
    )
    assert tox_off.cancel is True and tox_off.reason == "TOXICITY"
    ttl = should_replace_quote(
        old_price=100.00, new_price=100.00, tick_size=0.01,
        order_age_ms=450.0, ttl_ms=500.0, ttl_replace_frac=0.85,
    )
    assert ttl.cancel is True and ttl.reason == "TTL_EXPIRED"
    ev = should_replace_quote(
        old_price=100.00, new_price=100.00, tick_size=0.01,
        old_ev=0.10, new_ev=0.20, ev_improve_threshold=0.06,
    )
    assert ev.cancel is True and ev.reason == "EV"
    tiny_ev = should_replace_quote(
        old_price=100.00, new_price=100.00, tick_size=0.01,
        old_ev=0.10, new_ev=0.12, ev_improve_threshold=0.06,
    )
    assert tiny_ev.cancel is False and tiny_ev.reason == "HOLD"


def test_dust_escape_reduces_absolute_inventory():
    ok, after, reason = dust_escape_allowed(
        inventory_before=0.18,
        reduce_qty=0.25,
        age_ticks=800,
        min_age_ticks=400,
        benefit_bps=4.0,
        cost_bps=2.0,
    )
    assert ok is True
    assert reason == "ESCAPE"
    assert abs(after) < 0.18
    assert projected_inventory_after(0.18, 0.25) == 0.18 - 0.25


def test_unsafe_zero_cross_increasing_exposure_is_rejected():
    ok, after, reason = dust_escape_allowed(
        inventory_before=0.08,
        reduce_qty=0.25,
        age_ticks=800,
        min_age_ticks=400,
        benefit_bps=10.0,
        cost_bps=2.0,
    )
    assert ok is False
    assert reason == "EXPOSURE_INCREASE"
    assert abs(after) > abs(0.08)
    young, _, young_reason = dust_escape_allowed(
        inventory_before=0.18,
        reduce_qty=0.25,
        age_ticks=10,
        min_age_ticks=400,
        benefit_bps=10.0,
        cost_bps=2.0,
    )
    assert young is False
    assert young_reason == "TOO_YOUNG"
