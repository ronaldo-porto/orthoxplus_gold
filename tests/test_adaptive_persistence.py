# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Phase 3 Step 2: environment-scoped Adaptive persistence."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from adaptive_persistence import (
    CURRENT_SCHEMA,
    PERSISTENCE_KEY_FIELDS,
    apply_kappa_reset,
    apply_session_reset,
    build_identity,
    build_save_payload,
    classify_identity,
    decide_load,
    extract_execution_priors,
    identity_fingerprint,
    kappa_state_from_stats,
    parse_environment_key,
    state_filename,
)


def _identity(**overrides):
    base = build_identity(
        network="testnet",
        netuid=366,
        validator_environment="testnet_366_m3",
        base_version="base_v4_1_1_maker_guard",
        adaptive_version="adaptive_v3_rebase",
        schema=CURRENT_SCHEMA,
        min_order_size=0.25,
        simulation_family="im",
    )
    base.update(overrides)
    return base


def _legacy_v3_payload(*, requests=5000, env="testnet_366_m3"):
    return {
        "schema": 3,
        "version": "adaptive_v3_rebase",
        "environment_key": env,
        "total_requests": requests,
        "last_sim_timestamp": 99,
        "drift_until_request": 8000,
        "spread_baseline_bps": 12.0,
        "global": {
            "buy_quotes": [40, 20, 8],
            "buy_fills": [10, 4, 1],
            "sell_quotes": [30, 10, 2],
            "sell_fills": [8, 2, 0],
            "session_realized_obs": 17,
            "maker_realized_obs": 12,
            "maker_pnl_long_ewma": 0.04,
            "maker_pnl_short_ewma": -0.02,
            "dust_attempts": 6,
            "dust_fills": 1,
        },
        "books": {
            "7": {
                "buy_quotes": [12, 0, 0],
                "buy_fills": [3, 0, 0],
                "session_realized_obs": 3,
                "dust_attempts": 2,
                "dust_fills": 1,
            }
        },
    }


def test_parse_environment_key():
    assert parse_environment_key("testnet_366_m3") == ("testnet", 366)
    assert parse_environment_key("net_79_m3") == ("mainnet", 79)
    assert parse_environment_key("unscoped")[0] == "unknown"


def test_correct_environment_load_keeps_priors_but_observes():
    current = _identity()
    payload = build_save_payload(
        identity=current,
        global_stats={
            "buy_quotes": [8, 0, 0],
            "buy_fills": [2, 0, 0],
            "maker_pnl_long_ewma": 0.02,
        },
        book_stats={4: {"buy_quotes": [4, 0, 0], "buy_fills": [1, 0, 0]}},
    )
    payload["session_state"] = {"total_requests": 9999}
    payload["scoring_state"] = {"session_realized_obs": 9}
    payload["drift_baseline"] = {"drift_until_request": 5000}
    decision = decide_load(current, payload)
    assert decision.reason == "compatible"
    assert decision.phase == "OBSERVE"
    assert decision.total_requests == 0
    assert decision.prior_factor == 1.0
    assert decision.global_priors["buy_quotes"] == [8, 0, 0]
    assert decision.book_priors[4]["buy_fills"] == [1, 0, 0]


def test_network_mismatch_resets_priors_and_observes():
    current = _identity(network="mainnet", validator_environment="net_366_m3")
    saved = build_save_payload(
        identity=_identity(),
        global_stats={"buy_quotes": [50, 0, 0], "buy_fills": [20, 0, 0]},
        book_stats={},
    )
    decision = decide_load(current, saved)
    assert decision.reason == "network_mismatch"
    assert decision.phase == "OBSERVE"
    assert decision.total_requests == 0
    assert decision.global_priors["buy_quotes"] == [0, 0, 0]
    assert decision.book_priors == {}


def test_netuid_mismatch_resets_priors_and_observes():
    current = _identity(netuid=79, validator_environment="testnet_79_m3")
    saved = build_save_payload(
        identity=_identity(),
        global_stats={"buy_quotes": [50, 0, 0]},
        book_stats={},
    )
    decision = decide_load(current, saved)
    assert decision.reason == "netuid_mismatch"
    assert decision.phase == "OBSERVE"
    assert decision.global_priors["buy_quotes"] == [0, 0, 0]


def test_base_strategy_version_mismatch_isolates_priors():
    current = _identity(base_version="base_v4_4_champion")
    saved = build_save_payload(
        identity=_identity(base_version="base_other"),
        global_stats={"buy_quotes": [40, 0, 0], "buy_fills": [8, 0, 0], "dust_attempts": 8},
        book_stats={},
    )
    decision = decide_load(current, saved)
    assert decision.reason == "base_version_mismatch"
    assert decision.phase == "OBSERVE"
    assert decision.total_requests == 0
    assert decision.prior_factor == 0.0
    assert decision.global_priors["buy_quotes"] == [0, 0, 0]
    assert decision.book_priors == {}


def test_schema_migration_drops_stale_normal_clock():
    current = _identity()
    decision = decide_load(current, _legacy_v3_payload(requests=5000))
    assert decision.migrated_from == 3
    assert decision.phase == "OBSERVE"
    assert decision.total_requests == 0
    assert decision.global_priors["buy_quotes"] == [40, 20, 8]
    assert 7 in decision.book_priors
    assert "session_realized_obs" not in decision.global_priors


def test_corrupted_state_fallback():
    current = _identity()
    assert decide_load(current, None).reason == "missing"
    assert decide_load(current, "not-json").reason == "corrupted"
    assert decide_load(current, {"schema": 99, "global": {}}).reason == "corrupted"
    assert decide_load(current, {"schema": 4, "identity": {}}).reason == "corrupted"
    decision = decide_load(current, {"schema": 4, "identity": current})
    assert decision.reason == "corrupted"
    assert decision.phase == "OBSERVE"
    assert decision.total_requests == 0


def test_observe_reset_even_when_legacy_was_normal():
    current = _identity()
    decision = decide_load(current, _legacy_v3_payload(requests=12_000))
    assert decision.phase == "OBSERVE"
    assert decision.total_requests == 0
    reason, factor, _fields = classify_identity(current, current)
    assert reason == "compatible"
    assert factor == 1.0


def test_filename_is_environment_scoped():
    a = _identity()
    b = _identity(netuid=79, validator_environment="testnet_79_m3")
    assert state_filename(a) != state_filename(b)
    assert identity_fingerprint(a) != identity_fingerprint(b)
    assert "testnet_366_m3" in state_filename(a)


def test_filename_includes_base_version_and_schema():
    a = _identity()
    b = _identity(base_version="base_v4_4_champion")
    c = _identity(schema=CURRENT_SCHEMA + 1)
    assert set(PERSISTENCE_KEY_FIELDS) >= {
        "network",
        "netuid",
        "validator_environment",
        "simulation_family",
        "base_version",
        "schema",
    }
    assert identity_fingerprint(a) != identity_fingerprint(b)
    assert identity_fingerprint(a) != identity_fingerprint(c)
    assert state_filename(a) != state_filename(b)
    assert state_filename(a) != state_filename(c)


def test_save_payload_has_four_categories_and_no_session_clock():
    payload = build_save_payload(
        identity=_identity(),
        global_stats={"session_realized_obs": 9, "buy_quotes": [2, 0, 0]},
        book_stats={3: {"session_realized_obs": 4}},
        session_state={"total_requests": 9999, "phase": "NORMAL"},
        drift_state={"drift_until_request": 5000},
    )
    assert payload["schema"] == CURRENT_SCHEMA
    assert set(payload) >= {
        "execution_priors",
        "session_state",
        "kappa_state",
        "drift_state",
        "identity",
    }
    assert payload["session_state"]["total_requests"] == 9999
    assert payload["kappa_state"]["global_session_realized_obs"] == 9
    assert payload["drift_state"]["drift_until_request"] == 5000
    assert "total_requests" not in payload
    decision = decide_load(_identity(), payload)
    assert decision.phase == "OBSERVE"
    assert decision.total_requests == 0
    assert "session_realized_obs" not in decision.global_priors
    assert decision.global_priors["buy_quotes"] == [2, 0, 0]


def test_extract_priors_strips_session_kappa():
    priors = extract_execution_priors(
        {"buy_quotes": [1, 0, 0], "session_realized_obs": 9, "total_requests": 4000}
    )
    assert priors["buy_quotes"] == [1, 0, 0]
    assert "session_realized_obs" not in priors
    assert "total_requests" not in priors


def test_kappa_and_session_reset_do_not_touch_fill_priors():
    stats = {
        "buy_quotes": [8, 0, 0],
        "buy_fills": [2, 0, 0],
        "session_realized_obs": 6,
        "maker_realized_obs": 4,
        "dust_fail_streak": 3,
        "maker_realized_pnl_ewma": 0.02,
    }
    apply_kappa_reset(stats)
    assert stats["session_realized_obs"] == 0
    assert stats["maker_realized_obs"] == 0
    assert stats["buy_quotes"] == [8, 0, 0]
    apply_session_reset(stats)
    assert stats["dust_fail_streak"] == 0
    assert stats["buy_fills"] == [2, 0, 0]


def test_simulation_family_mismatch_isolates_state():
    current = _identity(simulation_family="im")
    saved = build_save_payload(
        identity=_identity(simulation_family="live"),
        global_stats={"buy_quotes": [30, 0, 0], "session_realized_obs": 8},
        book_stats={},
        session_state={"total_requests": 4000, "phase": "NORMAL"},
        kappa_state={"global_session_realized_obs": 8},
    )
    decision = decide_load(current, saved)
    assert decision.reason == "simulation_family_mismatch"
    assert decision.phase == "OBSERVE"
    assert decision.total_requests == 0
    assert decision.prior_factor == 0.0
    assert decision.global_priors["buy_quotes"] == [0, 0, 0]


def test_schema_mismatch_keeps_priors_drops_clocks():
    current = _identity(schema=CURRENT_SCHEMA)
    payload = build_save_payload(
        identity=_identity(schema=4),
        global_stats={"buy_quotes": [16, 4, 1], "session_realized_obs": 12},
        book_stats={5: {"buy_quotes": [3, 0, 0], "session_realized_obs": 4}},
        session_state={"total_requests": 9000, "phase": "NORMAL"},
        drift_state={"drift_until_request": 12000},
    )
    payload["schema"] = 4
    payload["identity"]["schema"] = 4
    decision = decide_load(current, payload)
    assert decision.reason == "schema_migrated"
    assert decision.phase == "OBSERVE"
    assert decision.total_requests == 0
    assert decision.prior_factor == 1.0
    assert decision.global_priors["buy_quotes"] == [16, 4, 1]
    assert "session_realized_obs" not in decision.global_priors


def test_saved_kappa_state_is_not_restored():
    current = _identity()
    payload = build_save_payload(
        identity=current,
        global_stats={"buy_quotes": [5, 0, 0]},
        book_stats={2: {"buy_quotes": [3, 0, 0], "session_realized_obs": 7}},
        kappa_state=kappa_state_from_stats(
            {"session_realized_obs": 11},
            {2: {"session_realized_obs": 7}},
        ),
        session_state={"total_requests": 8000, "phase": "NORMAL"},
    )
    assert payload["kappa_state"]["books"]["2"] == 7
    decision = decide_load(current, payload)
    assert decision.phase == "OBSERVE"
    assert decision.total_requests == 0
    assert "session_realized_obs" not in decision.global_priors
    assert "session_realized_obs" not in decision.book_priors[2]
