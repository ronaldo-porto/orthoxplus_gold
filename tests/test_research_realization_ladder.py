# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research hybrid realization ladder. Bands are Research-tunable."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)

from research_hybrid import REASON_REJECT_COST, REASON_REJECT_LADDER
from research_realization import (
    ACTION_AGGRESSIVE,
    ACTION_COMPETITIVE,
    ACTION_PASSIVE,
    ACTION_TAKER,
    MAKER_ACTIONS,
    classify_exit_action,
    evaluate_realization,
)
from research_realization_ladder import (
    BAND_AGGRESSIVE,
    BAND_COMPETITIVE,
    BAND_PASSIVE,
    BAND_TAKER_ELIGIBLE,
    DEFAULT_LADDER_AGGRESSIVE_MAX,
    DEFAULT_LADDER_COMPETITIVE_MAX,
    DEFAULT_LADDER_PASSIVE_MAX,
    LADDER_RUNGS,
    LADDER_VERSION,
    apply_realization_ladder,
    clamp_ladder_bands,
    classify_realization_rung,
)


def test_default_research_bands_are_the_initial_ladder():
    bands = clamp_ladder_bands()
    assert bands.passive_max == DEFAULT_LADDER_PASSIVE_MAX == 0.25
    assert bands.competitive_max == DEFAULT_LADDER_COMPETITIVE_MAX == 0.50
    assert bands.aggressive_max == DEFAULT_LADDER_AGGRESSIVE_MAX == 0.70
    assert LADDER_RUNGS == (
        ACTION_PASSIVE,
        ACTION_COMPETITIVE,
        ACTION_AGGRESSIVE,
        ACTION_TAKER,
    )


def test_classify_follows_configurable_bands():
    defaults = clamp_ladder_bands()
    assert classify_realization_rung(0.00, defaults).band == BAND_PASSIVE
    assert classify_realization_rung(0.24, defaults).proposed_action == ACTION_PASSIVE
    assert classify_realization_rung(0.25, defaults).proposed_action == ACTION_COMPETITIVE
    assert classify_realization_rung(0.49, defaults).band == BAND_COMPETITIVE
    assert classify_realization_rung(0.50, defaults).proposed_action == ACTION_AGGRESSIVE
    assert classify_realization_rung(0.70, defaults).band == BAND_AGGRESSIVE
    assert classify_realization_rung(0.70, defaults).taker_eligible is False
    eligible = classify_realization_rung(0.71, defaults)
    assert eligible.band == BAND_TAKER_ELIGIBLE
    assert eligible.proposed_action == ACTION_TAKER
    assert eligible.taker_eligible is True
    assert eligible.maker_action == ACTION_AGGRESSIVE
    assert classify_exit_action(0.71) == ACTION_TAKER

    tight = clamp_ladder_bands(0.10, 0.20, 0.30)
    assert classify_realization_rung(0.25, tight).proposed_action == ACTION_AGGRESSIVE
    assert classify_realization_rung(0.31, tight).taker_eligible is True


def test_clamp_keeps_bands_ordered_and_open():
    bands = clamp_ladder_bands(0.80, 0.20, 1.5)
    assert 0.0 <= bands.passive_max <= bands.competitive_max <= bands.aggressive_max < 1.0


def test_eligible_rung_does_not_auto_take():
    rung = classify_realization_rung(0.85)
    action, trigger = apply_realization_ladder(
        rung=rung,
        hybrid_take=False,
        hybrid_reason="MAKER_LADDER",
        hard_safety=False,
        transition_quarantine=False,
        cost=12.0,
        risk=4.0,
        state="EXIT_ONLY",
    )
    assert action == ACTION_AGGRESSIVE
    assert trigger == REASON_REJECT_COST
    assert action != ACTION_TAKER


def test_economics_cannot_skip_to_taker_before_eligible():
    rung = classify_realization_rung(0.40)
    action, trigger = apply_realization_ladder(
        rung=rung,
        hybrid_take=True,
        hybrid_reason="TAKER_LOCK_PROFIT",
        hard_safety=False,
        transition_quarantine=False,
        cost=3.0,
        risk=2.0,
        state="NORMAL",
    )
    assert rung.taker_eligible is False
    assert action == ACTION_COMPETITIVE
    assert trigger == REASON_REJECT_LADDER


def test_hard_safety_may_skip_maker_rungs():
    rung = classify_realization_rung(0.10)
    action, trigger = apply_realization_ladder(
        rung=rung,
        hybrid_take=True,
        hybrid_reason="EMERGENCY_HARD",
        hard_safety=True,
        transition_quarantine=False,
        cost=20.0,
        risk=4.0,
        state="EMERGENCY",
    )
    assert action == ACTION_TAKER
    assert trigger == "EMERGENCY_HARD"


def test_evaluate_logs_rung_and_respects_custom_bands():
    cheap = evaluate_realization(
        book=4,
        inventory_size=0.20,
        inventory_ratio=0.18,
        inventory_age=10.0,
        unrealized_pnl=12.0,
        expected_markout=0.2,
        fee_bps=1.0,
        spread_bps=2.0,
        slippage_bps=1.0,
        volume_cap_headroom=0.80,
        maker_fill_hazard=0.35,
        band="LONG",
        ladder_bands=clamp_ladder_bands(0.05, 0.08, 0.10),
    )
    log = cheap.as_log()
    assert cheap.proposed_rung == ACTION_TAKER
    assert cheap.taker_eligible is True
    if cheap.taker_economics is not None and cheap.taker_economics.take:
        assert cheap.selected_action == ACTION_TAKER
    else:
        assert cheap.selected_action in MAKER_ACTIONS
    assert log["proposed_rung"] == ACTION_TAKER
    assert log["ladder_band"] == BAND_TAKER_ELIGIBLE
    assert log["ladder_aggressive_max"] == 0.10

    expensive = evaluate_realization(
        book=5,
        inventory_size=0.95,
        inventory_ratio=0.88,
        inventory_age=50.0,
        unrealized_pnl=-22.0,
        expected_markout=-10.0,
        volatility=0.010,
        ofi=-0.70,
        adverse_selection_risk=0.70,
        volume_cap_headroom=0.10,
        realization_failed=True,
        fee_bps=25.0,
        spread_bps=20.0,
        slippage_bps=15.0,
        band="LONG",
    )
    assert expensive.taker_eligible is True
    assert expensive.proposed_rung == ACTION_TAKER
    assert expensive.selected_action in MAKER_ACTIONS
    assert expensive.selected_action != ACTION_TAKER


def test_research_wires_configurable_ladder():
    assert "RESEARCH_LADDER_VERSION" in RESEARCH_SRC
    assert LADDER_VERSION in RESEARCH_SRC
    assert "research_ladder_passive_max" in RESEARCH_SRC
    assert "research_ladder_competitive_max" in RESEARCH_SRC
    assert "research_ladder_aggressive_max" in RESEARCH_SRC
    assert "ladder_bands=getattr(self, \"_research_ladder_bands\", None)" in RESEARCH_SRC
    assert "[S1R_LADDER]" in RESEARCH_SRC
    assert "LADDER" in RESEARCH_SRC
