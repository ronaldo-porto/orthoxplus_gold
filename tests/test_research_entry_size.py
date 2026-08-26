# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research V4.3 Phase 1.5: dynamic per-book entry size."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)

from research_entry_size import (
    ADMISSION_NEAR_SAFE,
    ADMISSION_SAFE,
    ADMISSION_UNSAFE,
    admit_minimum_order,
    allowed_entry_size,
    classify_min_order_band,
)


def _flat_liquid(**overrides):
    params = dict(
        base_size=0.25,
        existing_inventory=0.0,
        max_inventory=1.20,
        inventory_age=0.0,
        volatility=0.001,
        toxicity=0.0,
        expected_markout=0.5,
        volume_cap_headroom=1.0,
        exit_rate=None,
        recent_drawdown=0.0,
        hard_max_entry=0.25,
    )
    params.update(overrides)
    return allowed_entry_size(**params)


def test_flat_liquid_book_allows_normal_size():
    decision = _flat_liquid()
    assert decision.entry_size == 0.25
    assert decision.entry_size == decision.hard_cap
    assert decision.expected_exit_capacity + 1e-12 >= decision.entry_size
    assert decision.inventory_after_full_fill == decision.entry_size
    assert 0.0 <= decision.inventory_risk_after_fill <= 1.0
    assert decision.inventory_factor == 1.0
    assert decision.liquidity_factor == 1.0
    assert decision.volume_headroom_factor == 1.0
    assert decision.risk_factor == 1.0


def test_high_inventory_reduces_size():
    flat = _flat_liquid()
    loaded = _flat_liquid(existing_inventory=0.80)
    assert loaded.entry_size < flat.entry_size
    assert loaded.inventory_factor < flat.inventory_factor
    assert loaded.inventory_after_full_fill <= 1.20 + 1e-12
    assert loaded.entry_size <= 1.20 - 0.80 + 1e-12


def test_low_volume_headroom_reduces_size():
    full = _flat_liquid(volume_cap_headroom=1.0)
    tight = _flat_liquid(volume_cap_headroom=0.10)
    assert tight.entry_size < full.entry_size
    assert tight.volume_headroom_factor < full.volume_headroom_factor


def test_poor_realization_capacity_reduces_size():
    healthy = _flat_liquid(exit_rate=0.20)
    slow = _flat_liquid(exit_rate=0.0, inventory_age=40.0, toxicity=0.35)
    assert slow.expected_exit_capacity < healthy.expected_exit_capacity
    assert slow.entry_size < healthy.entry_size
    assert slow.entry_size <= slow.expected_exit_capacity + 1e-12


def test_hard_cap_always_wins():
    decision = _flat_liquid(
        base_size=1.00,
        hard_max_entry=0.25,
        existing_inventory=0.0,
        volume_cap_headroom=1.0,
        exit_rate=1.0,
        toxicity=0.0,
        expected_markout=2.0,
        recent_drawdown=0.0,
        volatility=0.0005,
    )
    assert decision.uncapped_size > 0.25
    assert decision.entry_size <= 0.25 + 1e-12
    assert decision.entry_size == 0.25
    assert decision.trigger == "HARD_CAP"

    remaining = _flat_liquid(
        base_size=0.25,
        hard_max_entry=0.25,
        existing_inventory=1.10,
        max_inventory=1.20,
    )
    assert remaining.entry_size <= 0.10 + 1e-12
    assert remaining.hard_cap <= 0.10 + 1e-12
    assert remaining.inventory_after_full_fill <= 1.20 + 1e-12


def _admit(**overrides):
    params = dict(
        safe_size=0.22,
        min_order=0.25,
        tolerance=0.20,
        trading_ev=0.04,
        inventory_risk=0.12,
        exit_capacity=0.30,
        volume_headroom=0.80,
        remaining_inventory=1.20,
        enable_near_safe=True,
    )
    params.update(overrides)
    return admit_minimum_order(**params)


def test_safe_size_at_or_above_min_is_allowed():
    decision = _admit(safe_size=0.25)
    assert classify_min_order_band(0.25, 0.25, 0.20) == ADMISSION_SAFE
    assert decision.band == ADMISSION_SAFE
    assert decision.allow is True
    assert decision.size == 0.25
    assert decision.promoted is False
    above = _admit(safe_size=0.40)
    assert above.allow is True
    assert above.size == 0.40


def test_near_safe_promotes_only_when_gates_pass():
    decision = _admit()
    assert classify_min_order_band(0.22, 0.25, 0.20) == ADMISSION_NEAR_SAFE
    assert decision.band == ADMISSION_NEAR_SAFE
    assert decision.allow is True
    assert decision.size == 0.25
    assert decision.promoted is True
    assert decision.trigger == "NEAR_SAFE"


def test_near_safe_exit_capacity_uses_discrete_tolerance_not_exact_minimum():
    # Live V4.5 logs repeatedly had ~0.22 safe size and ~0.248 expected exit
    # capacity against a 0.25 exchange minimum.  That should not be rejected
    # solely for being 0.002 below the hard order quantum.
    allowed = _admit(safe_size=0.222, exit_capacity=0.248, tolerance=0.20)
    assert allowed.band == ADMISSION_NEAR_SAFE
    assert allowed.allow is True
    assert allowed.size == 0.25
    assert allowed.trigger == "NEAR_SAFE"

    # But materially insufficient exit capacity remains blocked.
    blocked = _admit(safe_size=0.222, exit_capacity=0.199, tolerance=0.20)
    assert blocked.allow is False
    assert blocked.trigger == "NEAR_SAFE_EXIT"


def test_near_safe_rejects_without_positive_ev_or_capacity():
    no_ev = _admit(trading_ev=0.0)
    assert no_ev.allow is False
    assert no_ev.size == 0.0
    assert no_ev.trigger == "NEAR_SAFE_EV"
    risk = _admit(inventory_risk=0.80)
    assert risk.allow is False
    assert risk.trigger == "NEAR_SAFE_INVENTORY_RISK"
    exit_blocked = _admit(exit_capacity=0.10)
    assert exit_blocked.allow is False
    assert exit_blocked.trigger == "NEAR_SAFE_EXIT"
    tight = _admit(volume_headroom=0.05)
    assert tight.allow is False
    assert tight.trigger == "NEAR_SAFE_HEADROOM"


def test_unsafe_never_promotes_even_with_perfect_gates():
    assert classify_min_order_band(0.10, 0.25, 0.20) == ADMISSION_UNSAFE
    decision = _admit(
        safe_size=0.10,
        trading_ev=1.0,
        inventory_risk=0.0,
        exit_capacity=1.0,
        volume_headroom=1.0,
        remaining_inventory=1.20,
    )
    assert decision.band == ADMISSION_UNSAFE
    assert decision.allow is False
    assert decision.size == 0.0
    assert decision.trigger == "UNSAFE"
    assert decision.promoted is False


def test_tolerance_is_configurable_and_zero_disables_near_safe():
    assert classify_min_order_band(0.22, 0.25, 0.0) == ADMISSION_UNSAFE
    assert classify_min_order_band(0.22, 0.25, 0.20) == ADMISSION_NEAR_SAFE
    tight = _admit(tolerance=0.0)
    assert tight.allow is False
    wide = _admit(safe_size=0.18, tolerance=0.30)
    assert wide.band == ADMISSION_NEAR_SAFE
    assert wide.allow is True


def test_research_wires_discrete_min_order_admission():
    assert "research_enable_min_order_admission" in RESEARCH_SRC
    assert "research_min_order_tolerance" in RESEARCH_SRC
    assert "admit_minimum_order(" in RESEARCH_SRC
    size_fn = RESEARCH_SRC.split("def dynamic_order_size(")[1].split(
        "def _research_live_quote("
    )[0]
    assert "admit_minimum_order(" in size_fn
    assert "research_enable_min_order_admission" in size_fn
    # Blind leftover-room promote is only the A/B fallback.
    assert size_fn.index("research_enable_min_order_admission") < size_fn.index(
        "self.research_promote_min_order"
    )
