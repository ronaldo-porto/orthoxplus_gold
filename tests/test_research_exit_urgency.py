# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research ExitUrgency V2: named components, no automatic taker."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)
from research_exit_urgency import (
    EXIT_URGENCY_COMPONENTS,
    EXIT_URGENCY_VERSION,
    EXIT_URGENCY_WEIGHTS,
    compute_exit_urgency_v2,
)
from research_realization import (
    ACTION_AGGRESSIVE,
    ACTION_TAKER,
    MAKER_ACTIONS,
    classify_exit_action,
    evaluate_realization,
    exit_urgency,
    exit_urgency_breakdown,
)


def _base(**overrides):
    params = dict(
        inventory_size=0.10,
        inventory_ratio=0.08,
        inventory_age=2.0,
        unrealized_pnl=0.0,
        expected_markout=0.0,
        volatility=0.0,
        ofi=0.0,
        inventory_sign=1.0,
        kappa_need=0.0,
        volume_cap_headroom=1.0,
        recent_realized_pnl=0.0,
        adverse_selection_risk=0.0,
        realization_failed=False,
    )
    params.update(overrides)
    return params


def test_weights_cover_every_named_component():
    assert set(EXIT_URGENCY_WEIGHTS) == set(EXIT_URGENCY_COMPONENTS)
    assert abs(sum(EXIT_URGENCY_WEIGHTS.values()) - 1.0) <= 1e-12
    assert EXIT_URGENCY_VERSION == "exit_urgency_v2"


def test_each_component_raises_urgency():
    calm = compute_exit_urgency_v2(**_base())
    cases = {
        "inventory_pressure": _base(inventory_size=0.90, inventory_ratio=0.80),
        "inventory_age_pressure": _base(inventory_age=40.0),
        "drawdown_pressure": _base(unrealized_pnl=-20.0),
        "volatility_pressure": _base(volatility=0.020),
        "adverse_flow_pressure": _base(ofi=-0.80, adverse_selection_risk=0.80),
        "markout_pressure": _base(expected_markout=-16.0),
        "kappa_pressure": _base(kappa_need=0.85),
        "volume_cap_pressure": _base(volume_cap_headroom=0.05),
        "realization_failure_pressure": _base(
            recent_realized_pnl=-0.08, realization_failed=True,
        ),
    }
    for name, params in cases.items():
        stressed = compute_exit_urgency_v2(**params)
        assert 0.0 <= getattr(stressed, name) <= 1.0
        assert getattr(stressed, name) > getattr(calm, name)
        assert stressed.urgency > calm.urgency
        log = stressed.as_log()
        assert name in log
        assert 0.0 <= log[name] <= 1.0
    assert 0.0 <= calm.urgency <= 1.0


def test_high_urgency_is_not_automatic_taker():
    breakdown = compute_exit_urgency_v2(
        **_base(
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
        )
    )
    assert breakdown.urgency >= 0.70
    assert classify_exit_action(breakdown.urgency) in {ACTION_AGGRESSIVE, ACTION_TAKER}

    decision = evaluate_realization(
        book=3,
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
    assert decision.exit_urgency >= 0.70
    assert decision.selected_action != ACTION_TAKER
    assert decision.selected_action in MAKER_ACTIONS
    assert decision.taker_economics is not None
    assert decision.taker_economics.economic_ok is False
    log = decision.as_log()
    for name in EXIT_URGENCY_COMPONENTS:
        assert name in log


def test_exit_urgency_wrapper_matches_v2():
    params = _base(inventory_age=18.0, unrealized_pnl=-8.0)
    breakdown = exit_urgency_breakdown(**params)
    assert exit_urgency(**params) == breakdown.urgency
    assert set(EXIT_URGENCY_COMPONENTS).issubset(breakdown.as_log())


def test_research_wires_exit_urgency_v2():
    assert "RESEARCH_EXIT_URGENCY_VERSION" in RESEARCH_SRC
    assert "exit_urgency_v2" in RESEARCH_SRC
    assert "EXIT_URGENCY" in RESEARCH_SRC
    assert "[S1R_EXIT_URGENCY]" in RESEARCH_SRC
    for name in EXIT_URGENCY_COMPONENTS:
        assert name in RESEARCH_SRC
    assert "realization_failed=failed" in RESEARCH_SRC


def test_realization_as_log_unpacks_with_tick_without_kwarg_collision():
    decision = evaluate_realization(
        book=3,
        inventory_size=0.40,
        inventory_ratio=0.35,
        inventory_age=12.0,
        unrealized_pnl=-4.0,
        expected_markout=-2.0,
        volatility=0.002,
        ofi=-0.20,
        volume_cap_headroom=0.40,
        band="LONG",
    )
    assert decision.urgency_breakdown is not None
    assert decision.ladder_rung is not None

    def emit(*, tick, **payload):
        return tick, payload

    tick, payload = emit(tick=31, **decision.as_log())
    assert tick == 31
    assert payload["book"] == 3
    assert payload["exit_urgency"] == decision.exit_urgency
    assert "inventory_pressure" in payload
    assert "proposed_rung" in payload


def test_explicit_exit_urgency_plus_log_dicts_is_the_live_typeerror():
    decision = evaluate_realization(
        book=3,
        inventory_size=0.40,
        inventory_ratio=0.35,
        inventory_age=12.0,
        unrealized_pnl=-4.0,
        expected_markout=-2.0,
        volatility=0.002,
        band="LONG",
    )
    urgency_log = decision.urgency_breakdown.as_log()
    ladder_log = decision.ladder_rung.as_log()

    def emit(**payload):
        return payload

    raised = False
    try:
        emit(
            exit_urgency=decision.exit_urgency,
            proposed_rung=decision.proposed_rung,
            **urgency_log,
            **ladder_log,
        )
    except TypeError as exc:
        raised = True
        assert "exit_urgency" in str(exc)
    assert raised


def test_research_realization_emit_uses_decision_as_log():
    manage = RESEARCH_SRC.split("def _research_manage_realization(")[1].split(
        "def _research_dust_age_ticks("
    )[0]
    emit = manage.split('"REALIZATION"')[1].split("if self.debug_enabled:")[0]
    assert "**decision.as_log()" in emit
    assert "exit_urgency=decision.exit_urgency" not in emit
    assert "**urgency_log" not in emit
    assert "**ladder_log" not in emit
