# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research inventory-reducing quantity vs simulator min-order rules."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)
MANAGE = RESEARCH_SRC.split("def _research_manage_realization(")[1].split(
    "def _research_dust_age_ticks("
)[0]

from research_exit_quantity import (
    EXIT_QTY_VERSION,
    REASON_EXACT,
    REASON_MIN_LEGAL,
    REASON_REJECT_LARGER_OPPOSITE,
    REASON_SAFER_RESIDUAL,
    choose_reduce_quantity,
    exchange_min_order_size,
    inventory_after_reduce,
    round_volume,
    volume_increment,
)


def test_simulator_min_order_is_max_of_config_and_volume_tick():
    assert volume_increment(4) == 0.0001
    assert exchange_min_order_size(0.25, 4) == 0.25
    assert exchange_min_order_size(0.0, 4) == 0.0001
    assert exchange_min_order_size(0.25, 8) == 0.25


def test_exact_reducing_quantity_is_used_when_legal():
    decision = choose_reduce_quantity(
        inventory=0.40, desired=0.40, min_order=0.25, volume_decimals=4,
    )
    assert decision.quantity == 0.40
    assert decision.reason == REASON_EXACT
    assert abs(decision.inventory_after) <= 1e-12
    assert decision.quantity != 0.25


def test_does_not_blindly_send_min_when_flatten_is_legal():
    decision = choose_reduce_quantity(
        inventory=0.80, desired=0.80, min_order=0.25, volume_decimals=4,
    )
    assert decision.quantity == 0.80
    assert abs(decision.inventory_after) <= 1e-12


def test_partial_legal_clip_is_kept():
    decision = choose_reduce_quantity(
        inventory=0.80, desired=0.36, min_order=0.25, volume_decimals=4,
    )
    assert decision.quantity == 0.36
    assert abs(decision.inventory_after - 0.44) <= 1e-12


def test_illegal_partial_uses_min_same_side_reduction():
    decision = choose_reduce_quantity(
        inventory=0.80, desired=0.10, min_order=0.25, volume_decimals=4,
    )
    assert decision.quantity == 0.25
    assert decision.reason == REASON_MIN_LEGAL
    assert abs(decision.inventory_after - 0.55) <= 1e-12


def test_never_creates_larger_opposite_exposure():
    decision = choose_reduce_quantity(
        inventory=0.10, desired=0.10, min_order=0.25, volume_decimals=4,
    )
    assert decision.quantity == 0.0
    assert decision.reason == REASON_REJECT_LARGER_OPPOSITE
    assert abs(decision.inventory_after - 0.10) <= 1e-12
    assert abs(inventory_after_reduce(0.10, 0.25)) > 0.10


def test_smaller_overshoot_is_safest_legal_reduction():
    decision = choose_reduce_quantity(
        inventory=0.18, desired=0.18, min_order=0.25, volume_decimals=4,
    )
    assert decision.quantity == 0.25
    assert decision.reason == REASON_SAFER_RESIDUAL
    assert abs(decision.inventory_after) + 1e-12 < 0.18


def test_dust_leftover_flattens_when_flatten_is_legal():
    decision = choose_reduce_quantity(
        inventory=0.40, desired=0.26, min_order=0.25, volume_decimals=4,
    )
    assert decision.quantity == 0.40
    assert decision.reason == REASON_SAFER_RESIDUAL
    assert abs(decision.inventory_after) <= 1e-12


def test_round_volume_does_not_promote_to_min():
    assert round_volume(0.18, 4) == 0.18
    assert round_volume(0.18444, 4) == 0.1844


def test_research_wires_exit_quantity():
    assert "RESEARCH_EXIT_QTY_VERSION" in RESEARCH_SRC
    assert EXIT_QTY_VERSION in RESEARCH_SRC
    assert "choose_reduce_quantity(" in MANAGE
    assert "_round_order_size(" not in MANAGE
    assert "[S1R_EXIT_QTY]" in RESEARCH_SRC
    assert "EXIT_QTY" in RESEARCH_SRC
