from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from research_entry_size import admit_minimum_order
from research_quote_hysteresis import (
    ONE_AWAY_STALE_TTL_VERSION,
    one_away_stale_completion_ttl,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "agents/strategy/Strategy1_Research.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_test_multi.sh").read_text()


def _admit(**overrides):
    common = dict(
        safe_size=0.04,          # 16% of the 0.25 venue minimum
        min_order=0.25,
        tolerance=0.20,
        trading_ev=0.05,
        inventory_risk=0.10,
        exit_capacity=0.055,     # 22% of the 0.25 venue minimum
        volume_headroom=1.0,
        remaining_inventory=1.20,
        enable_near_safe=True,
        min_trading_ev=0.0,
        max_inventory_risk=0.35,
        min_headroom=0.25,
        observations_remaining=1,
        enable_one_away_exact_min=True,
        one_away_min_trading_ev=0.0,
        one_away_min_safe_fraction=0.15,
        one_away_min_exit_fraction=0.20,
    )
    common.update(overrides)
    return admit_minimum_order(**common)


def test_release_contract_and_runner_values():
    assert 'RESEARCH_POLICY_VERSION = "simplified_kappa_productivity_v4_13_9"' in SRC
    assert ONE_AWAY_STALE_TTL_VERSION == "one_away_stale_ttl_v4_12_11"
    assert "research_one_away_exact_min_safe_fraction=0.15" in LAUNCHER
    assert "research_one_away_exact_min_exit_fraction=0.20" in LAUNCHER
    assert "research_candidate_count=10" in LAUNCHER
    assert "research_max_open_books=6" in LAUNCHER
    assert "research_unified_stale_bridge_roundtrip_floor_bps=-12.0" in LAUNCHER


def test_one_away_exact_min_survives_soft_sizing_but_keeps_hard_safety():
    d = _admit()
    assert d.allow
    assert d.size == 0.25
    assert d.trigger == "ONE_AWAY_EXACT_MIN"

    assert not _admit(trading_ev=0.0).allow
    assert not _admit(inventory_risk=0.36).allow
    assert not _admit(volume_headroom=0.20).allow
    assert not _admit(remaining_inventory=0.24).allow


def test_one_away_soft_floors_are_fail_closed_even_if_config_tries_to_widen():
    # The helper itself clamps the two soft floors, so a launcher/config cannot
    # silently make the exact-min path looser than the V4.12.11 contract.
    assert not _admit(
        safe_size=0.03,  # 12% < hard 15% floor
        one_away_min_safe_fraction=0.01,
    ).allow
    assert not _admit(
        exit_capacity=0.045,  # 18% < hard 20% floor
        one_away_min_exit_fraction=0.01,
    ).allow


def test_velocity_stale_one_away_gets_only_short_post_only_ttl_candidate():
    ttl, reason, used = one_away_stale_completion_ttl(
        chosen_ttl_ms=None,
        ttl_reason="STALE",
        completion_candidate=True,
        completion_samples=2,
        completion_target=3,
        trading_ev=0.05,
        market_regime="MIXED",
        min_ttl_ms=250.0,
    )
    assert used
    assert ttl == 250.0
    assert reason == "ONE_AWAY_STALE_SHORT"


def test_stale_ttl_override_rejects_non_one_away_bad_ev_or_stress():
    base = dict(
        chosen_ttl_ms=None, ttl_reason="STALE", completion_candidate=True,
        completion_samples=2, completion_target=3, trading_ev=0.05,
        market_regime="MIXED", min_ttl_ms=250.0,
    )
    for change in (
        {"completion_samples": 1},
        {"completion_candidate": False},
        {"trading_ev": 0.0},
        {"trading_ev": -0.01},
        {"market_regime": "TOXIC"},
        {"market_regime": "STRESSED"},
        {"ttl_reason": "TOXIC_SHORT"},
    ):
        args = dict(base)
        args.update(change)
        ttl, _, used = one_away_stale_completion_ttl(**args)
        assert ttl is None
        assert not used


def test_existing_ttl_is_never_rewritten():
    ttl, reason, used = one_away_stale_completion_ttl(
        chosen_ttl_ms=700.0, ttl_reason="ADVERSE_SHORT",
        completion_candidate=True, completion_samples=2, completion_target=3,
        trading_ev=0.05, market_regime="MIXED", min_ttl_ms=250.0,
    )
    assert ttl == 700.0
    assert reason == "ADVERSE_SHORT"
    assert not used
