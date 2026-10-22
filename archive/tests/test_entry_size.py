# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Production frozen entry-size control and volume-cap headroom."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from entry_size import allowed_entry_size


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
    assert decision.trigger == "HARD_CAP" or decision.entry_size <= 0.25 + 1e-12


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


def test_missing_signals_are_conservative_not_invented():
    missing = allowed_entry_size(
        base_size=0.25,
        max_inventory=1.20,
        hard_max_entry=0.25,
        expected_markout=0.0,
        ofi_against=0.0,
        volume_cap_headroom=1.0,
    )
    assert 0.0 <= missing.entry_size <= 0.25 + 1e-12
    toxic = _flat_liquid(expected_markout=-12.0, ofi_against=1.0, toxicity=0.80)
    assert toxic.entry_size < missing.entry_size
    assert toxic.risk_factor < missing.risk_factor


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
    assert decision.entry_size <= 0.25 + 1e-12
    remaining = _flat_liquid(
        base_size=0.25,
        hard_max_entry=0.25,
        existing_inventory=1.10,
        max_inventory=1.20,
    )
    assert remaining.entry_size <= 0.10 + 1e-12
    assert remaining.inventory_after_full_fill <= 1.20 + 1e-12
    assert remaining.hard_cap <= 0.10 + 1e-12
