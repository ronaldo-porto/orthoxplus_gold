from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from research_entry_size import (
    QUALIFIED_CORE_EXACT_MIN_VERSION,
    admit_minimum_order,
)
from research_quote_hysteresis import (
    QUALIFIED_CORE_STALE_TTL_VERSION,
    qualified_core_stale_completion_ttl,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(encoding="utf-8")


def _core_admit(**overrides):
    params = dict(
        safe_size=0.083,
        min_order=0.25,
        tolerance=0.20,
        trading_ev=0.06,
        inventory_risk=0.12,
        exit_capacity=0.085,
        volume_headroom=0.80,
        remaining_inventory=1.20,
        observations_remaining=0,
        productive_qualified_core=True,
        enable_qualified_core_exact_min=True,
        qualified_core_min_trading_ev=0.0,
        qualified_core_max_inventory_risk=0.35,
        qualified_core_min_exit_fraction=0.20,
        qualified_core_min_headroom=0.25,
    )
    params.update(overrides)
    return admit_minimum_order(**params)


def test_v4137_versions():
    assert QUALIFIED_CORE_EXACT_MIN_VERSION == "qualified_core_exact_min_v4_13_7"
    assert QUALIFIED_CORE_STALE_TTL_VERSION == "qualified_core_velocity_stale_ttl_v4_13_7"
    assert 'RESEARCH_POLICY_VERSION = "long_run_recycling_v4_14_1"' in SRC


def test_book67_shape_gets_exact_minimum_core_recycle():
    d = _core_admit(safe_size=0.08273, exit_capacity=0.08447)
    assert d.allow is True
    assert d.size == 0.25
    assert d.promoted is True
    assert d.trigger == "QUALIFIED_CORE_EXACT_MIN"


def test_book46_and_book87_shapes_get_exact_minimum_core_recycle():
    d46 = _core_admit(safe_size=0.08479, exit_capacity=0.09843)
    d87 = _core_admit(safe_size=0.06201, exit_capacity=0.07156)
    assert d46.allow and d46.size == 0.25
    assert d87.allow and d87.size == 0.25


def test_core_override_requires_proven_productivity_and_positive_ev():
    not_core = _core_admit(productive_qualified_core=False)
    assert not_core.allow is False
    assert not_core.trigger == "UNSAFE"
    no_ev = _core_admit(trading_ev=0.0)
    assert no_ev.allow is False
    assert no_ev.trigger == "UNSAFE"


def test_core_override_keeps_hard_risk_headroom_exit_capacity_checks():
    risk = _core_admit(inventory_risk=0.50)
    headroom = _core_admit(volume_headroom=0.10)
    exit_block = _core_admit(exit_capacity=0.02)
    assert risk.allow is False
    assert headroom.allow is False
    assert exit_block.allow is False


def test_core_stale_ttl_rescues_positive_productive_core_only():
    ttl, reason, used = qualified_core_stale_completion_ttl(
        chosen_ttl_ms=None,
        ttl_reason="STALE",
        completion_candidate=True,
        completion_samples=5,
        completion_target=3,
        productive_qualified_core=True,
        trading_ev=0.05,
        market_regime="NORMAL",
        min_ttl_ms=200.0,
        stale_ttl_ms=900.0,
    )
    assert used is True
    assert ttl == 250.0
    assert reason == "QUALIFIED_CORE_VELOCITY_STALE_SHORT"


def test_core_stale_ttl_does_not_override_negative_ev_or_toxicity_or_unknown():
    base = dict(
        chosen_ttl_ms=None,
        ttl_reason="STALE",
        completion_candidate=True,
        completion_samples=3,
        completion_target=3,
        productive_qualified_core=True,
        trading_ev=0.05,
        market_regime="NORMAL",
        min_ttl_ms=200.0,
        stale_ttl_ms=250.0,
    )
    for change in (
        {"trading_ev": -0.01},
        {"market_regime": "TOXIC"},
        {"market_regime": "STRESSED"},
        {"productive_qualified_core": False},
        {"completion_samples": 2},
        {"ttl_reason": "ADVERSE_SHORT"},
    ):
        args = dict(base)
        args.update(change)
        ttl, _, used = qualified_core_stale_completion_ttl(**args)
        assert ttl is None
        assert used is False


def test_strategy_wires_productive_core_to_size_and_ttl_without_broad_relaxation():
    assert "productive_qualified_core=bool(productive_qualified_core)" in SRC
    assert "research_qualified_core_exact_min_enabled" in SRC
    assert "qualified_core_stale_completion_ttl(" in SRC
    assert '"QUALIFIED_CORE_TTL_RESCUE"' in SRC
    # V4.13.6 and older safety/economic authorities stay present.
    assert "research_completion_ev_cache_ticks" in SRC
    assert "positive_maker_rescue_veto_applies" in SRC
    assert "authoritative_execution_lane" in SRC
