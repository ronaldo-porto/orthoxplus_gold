# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Kappa accelerates profitable realization; it does not force losses."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)

from research_kappa_realization import (
    KAPPA_REALIZATION_VERSION,
    MODE_ONE_AWAY,
    MODE_QUALIFIED,
    MODE_TWO_AWAY,
    ONE_AWAY_BOOST,
    REASON_BLOCKED_LOSS,
    TWO_AWAY_BOOST,
    kappa_close_is_clearly_bad,
    kappa_realization_boost,
)
from research_realization import (
    ACTION_TAKER,
    MAKER_ACTIONS,
    evaluate_realization,
    kappa_completion_need,
)
from research_realization_ladder import clamp_ladder_bands


def _open(**overrides):
    params = dict(
        book=1,
        inventory_size=0.18,
        inventory_ratio=0.16,
        inventory_age=6.0,
        unrealized_pnl=4.0,
        expected_markout=0.4,
        volatility=0.001,
        imbalance=0.05,
        observations_remaining=0,
        volume_cap_headroom=0.80,
        recent_realized_pnl=0.01,
        adverse_selection_risk=0.02,
        fee_bps=1.0,
        spread_bps=2.5,
        slippage_bps=3.0,
        band="LONG",
    )
    params.update(overrides)
    return params


def test_one_away_is_strong_two_away_is_moderate_qualified_is_normal():
    one = kappa_realization_boost(
        observations_remaining=1, unrealized_pnl_bps=4.0,
    )
    two = kappa_realization_boost(
        observations_remaining=2, unrealized_pnl_bps=4.0,
    )
    done = kappa_realization_boost(
        observations_remaining=0, unrealized_pnl_bps=4.0, eligible=True,
    )
    assert one.mode == MODE_ONE_AWAY
    assert two.mode == MODE_TWO_AWAY
    assert done.mode == MODE_QUALIFIED
    assert one.boost == ONE_AWAY_BOOST
    assert two.boost == TWO_AWAY_BOOST
    assert done.boost == 0.0
    assert one.boost > two.boost > done.boost
    assert kappa_completion_need(1, 4.0) == one.boost
    assert kappa_completion_need(2, 4.0) == two.boost
    assert kappa_completion_need(0, 4.0) == 0.0

    one_dec = evaluate_realization(**_open(observations_remaining=1))
    two_dec = evaluate_realization(**_open(observations_remaining=2))
    done_dec = evaluate_realization(**_open(observations_remaining=0))
    assert one_dec.exit_urgency > two_dec.exit_urgency > done_dec.exit_urgency
    assert one_dec.kappa_realization is not None
    assert one_dec.kappa_realization.boost == ONE_AWAY_BOOST
    assert done_dec.kappa_realization.boost == 0.0


def test_kappa_does_not_force_a_losing_realization():
    assert kappa_close_is_clearly_bad(-8.0) is True
    assert kappa_close_is_clearly_bad(4.0) is False
    assert kappa_close_is_clearly_bad(2.0, crossing_cost_bps=5.0) is True
    blocked = kappa_realization_boost(
        observations_remaining=1, unrealized_pnl_bps=-8.0,
    )
    assert blocked.boost == 0.0
    assert blocked.taker_boost == 0.0
    assert blocked.reason == REASON_BLOCKED_LOSS
    assert kappa_completion_need(1, -8.0) == 0.0
    assert kappa_completion_need(2, -3.0) == 0.0

    losing = evaluate_realization(
        **_open(
            observations_remaining=1,
            unrealized_pnl=-8.0,
            expected_markout=-1.0,
            ladder_bands=clamp_ladder_bands(0.05, 0.08, 0.10),
        )
    )
    qualified_loss = evaluate_realization(
        **_open(
            observations_remaining=0,
            unrealized_pnl=-8.0,
            expected_markout=-1.0,
            ladder_bands=clamp_ladder_bands(0.05, 0.08, 0.10),
        )
    )
    assert losing.selected_action != ACTION_TAKER
    assert losing.selected_action in MAKER_ACTIONS
    assert losing.kappa_realization.boost == 0.0
    assert abs(losing.exit_urgency - qualified_loss.exit_urgency) < 1e-9

    thin_lock = kappa_realization_boost(
        observations_remaining=1,
        unrealized_pnl_bps=2.0,
        crossing_cost_bps=5.0,
    )
    assert thin_lock.boost == ONE_AWAY_BOOST
    assert thin_lock.taker_boost == 0.0


def test_research_wires_kappa_realization_boost():
    assert "RESEARCH_KAPPA_REALIZATION_VERSION" in RESEARCH_SRC
    assert KAPPA_REALIZATION_VERSION == "kappa_realization_v1"
    assert "kappa_realization_boost(" in RESEARCH_SRC
    realize = RESEARCH_SRC.split("def _research_evaluate_realization(")[1].split(
        "def _research_place_maker_exit("
    )[0]
    assert "_research_kappa_book(" in realize
    assert "kappa_realization_boost(" in realize
    assert "[S1R_REALIZATION]" in RESEARCH_SRC
    assert "kappa_boost" in RESEARCH_SRC.split("if typ == \"REALIZATION\":")[1].split(
        "if typ == \"EXIT_URGENCY\":"
    )[0]
