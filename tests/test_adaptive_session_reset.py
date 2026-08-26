# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Phase 3 Step 3.2: Adaptive session/Kappa reset isolation."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents" / "strategy"))

from adaptive_drift import PhaseClocks, current_phase, enter_or_extend_drift
from adaptive_persistence import (
    CURRENT_SCHEMA,
    PERSISTENCE_KEY_FIELDS,
    apply_kappa_reset,
    apply_session_reset,
    build_identity,
    build_save_payload,
    decide_load,
    identity_fingerprint,
    state_filename,
)

ADAPTIVE = (ROOT / "agents" / "strategy" / "AdaptiveAgent.py").read_text(encoding="utf-8")


def _identity(**overrides):
    base = build_identity(
        network="testnet",
        netuid=366,
        validator_environment="testnet_366_m3",
        base_version="base_v4_4_champion",
        adaptive_version="adaptive_v3_hjb_shadow",
        schema=CURRENT_SCHEMA,
        min_order_size=0.25,
        simulation_family="im",
    )
    base.update(overrides)
    return base


def test_persistence_key_includes_required_fields():
    assert PERSISTENCE_KEY_FIELDS == (
        "network",
        "netuid",
        "validator_environment",
        "simulation_family",
        "base_version",
        "schema",
    )


def test_incompatible_simulations_do_not_share_files():
    champion = _identity()
    other_base = _identity(base_version="base_v4_1_1_maker_guard")
    other_schema = _identity(schema=4)
    other_sim = _identity(simulation_family="paper")
    other_net = _identity(network="mainnet", validator_environment="net_366_m3")
    fingerprints = {
        identity_fingerprint(champion),
        identity_fingerprint(other_base),
        identity_fingerprint(other_schema),
        identity_fingerprint(other_sim),
        identity_fingerprint(other_net),
    }
    assert len(fingerprints) == 5
    names = {
        state_filename(champion),
        state_filename(other_base),
        state_filename(other_schema),
        state_filename(other_sim),
        state_filename(other_net),
    }
    assert len(names) == 5


def test_load_never_restores_session_kappa_or_drift():
    current = _identity()
    payload = build_save_payload(
        identity=current,
        global_stats={
            "buy_quotes": [9, 1, 0],
            "session_realized_obs": 21,
            "maker_realized_obs": 14,
        },
        book_stats={8: {"session_realized_obs": 5, "buy_quotes": [2, 0, 0]}},
        session_state={"total_requests": 12000, "phase": "NORMAL"},
        drift_state={"drift_until_request": 15000, "recovery_until_request": 15500},
    )
    decision = decide_load(current, payload)
    assert decision.phase == "OBSERVE"
    assert decision.total_requests == 0
    assert decision.global_priors["buy_quotes"] == [9, 1, 0]
    assert "session_realized_obs" not in decision.global_priors
    assert "maker_realized_obs" not in decision.global_priors
    assert "session_realized_obs" not in decision.book_priors[8]


def test_kappa_reset_clears_episode_memory_only():
    stats = {
        "buy_quotes": [4, 0, 0],
        "buy_fills": [1, 0, 0],
        "session_realized_obs": 9,
        "maker_realized_obs": 6,
        "taker_realized_obs": 3,
        "dust_fail_streak": 4,
    }
    apply_kappa_reset(stats)
    assert stats["session_realized_obs"] == 0
    assert stats["maker_realized_obs"] == 0
    assert stats["taker_realized_obs"] == 0
    assert stats["buy_quotes"] == [4, 0, 0]
    apply_session_reset(stats)
    assert stats["dust_fail_streak"] == 0
    assert stats["buy_fills"] == [1, 0, 0]


def test_observe_bootstrap_normal_then_drift():
    clocks = PhaseClocks(
        observe_requests=1000,
        normal_after_requests=3000,
        total_requests=0,
    )
    assert current_phase(clocks) == "OBSERVE"
    clocks.total_requests = 999
    assert current_phase(clocks) == "OBSERVE"
    clocks.total_requests = 1000
    assert current_phase(clocks) == "BOOTSTRAP"
    clocks.total_requests = 2999
    assert current_phase(clocks) == "BOOTSTRAP"
    clocks.total_requests = 3000
    assert current_phase(clocks) == "NORMAL"
    clocks = enter_or_extend_drift(clocks, hold_requests=500, recovery_requests=500)
    assert current_phase(clocks) == "DRIFT"
    clocks.total_requests = clocks.drift_until_request
    assert current_phase(clocks) == "BOOTSTRAP"
    clocks.total_requests = clocks.recovery_until_request
    assert current_phase(clocks) == "NORMAL"


def test_session_reset_returns_to_observe():
    clocks = PhaseClocks(
        observe_requests=1000,
        normal_after_requests=3000,
        drift_until_request=8000,
        recovery_until_request=8500,
        total_requests=4200,
    )
    assert current_phase(clocks) == "DRIFT"
    clocks.total_requests = 0
    clocks.drift_until_request = 0
    clocks.recovery_until_request = 0
    assert current_phase(clocks) == "OBSERVE"


def test_adaptive_reset_logging_and_isolation_hooks():
    assert "ADAPTIVE_RESET" in ADAPTIVE
    assert "def _adaptive_log_reset(" in ADAPTIVE
    assert 'self._adaptive_reset_session_scoped_state("sim_timestamp_rewind")' in ADAPTIVE
    assert "apply_session_reset(" in ADAPTIVE
    assert "kappa_state_from_stats(" in ADAPTIVE
    assert "session_state={" in ADAPTIVE
    assert "kappa_state=" in ADAPTIVE
    assert "drift_state={" in ADAPTIVE
    assert "execution_priors" in ADAPTIVE or "build_save_payload(" in ADAPTIVE
    assert "base_version=str(getattr(self, \"DEPLOY_POLICY_VERSION\", \"unknown\"))" in ADAPTIVE
    assert "schema=int(self.ADAPTIVE_STATE_SCHEMA)" in ADAPTIVE
    assert 'family = "im"' in ADAPTIVE
    assert "ADAPTIVE_STATE_SCHEMA = CURRENT_SCHEMA" in ADAPTIVE
    assert "Episode-only Adaptive Kappa memory" in ADAPTIVE
    assert 'reason=f"load_{decision.reason}"' in ADAPTIVE
    assert "Never restore a phase clock" in ADAPTIVE
    assert "self._research_realized_observations_by_book.clear()" in ADAPTIVE
