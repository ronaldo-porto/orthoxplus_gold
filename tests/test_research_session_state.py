# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research session integrity: Kappa observations persist for one simulation."""
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)

from research_hybrid import REASON_REJECT_TRANSITION, hybrid_taker_decision
from research_realization import ACTION_TAKER, evaluate_realization
from research_session_state import (
    ACTION_KEEP,
    ACTION_RESET,
    ACTION_RESTORE,
    CURRENT_SCHEMA,
    DEFAULT_TRANSITION_QUARANTINE_TICKS,
    REASON_FIRST_BIND,
    REASON_INCOMPATIBLE_SCHEMA,
    REASON_INVALID_STATE,
    REASON_MISSING_SIM_ID,
    REASON_NETUID_CHANGE,
    REASON_NETWORK_CHANGE,
    REASON_RELOAD_SAME_SIMULATION,
    REASON_SAME_SIMULATION,
    REASON_SIM_ID_CHANGE,
    RESET_REASONS,
    SessionIdentity,
    build_payload,
    clear_stale_session_runtime,
    decide_session,
    enforce_monotonic,
    extract_simulation_id,
    format_reset_fields,
    format_transition_fields,
    increment_observation,
    observation_total,
    parse_payload,
    payload_reject_reason,
    reconcile_account_base,
    session_requires_transition_quarantine,
    should_reset_on_timestamp_rewind,
    taker_allowed_after_transition,
)


def _id(sim="SIM_A", network="testnet", netuid=366, schema=CURRENT_SCHEMA):
    return SessionIdentity(
        simulation_id=sim,
        network=network,
        netuid=netuid,
        schema=schema,
    )


def _payload(sim="SIM_A", obs=None, samples=None, closes=0, **identity):
    return build_payload(
        _id(sim=sim, **identity),
        obs or {1: 2, 2: 1},
        samples or {1: 1},
        closes,
    )


def test_normal_tick_progression_never_decreases():
    obs = {}
    totals = []
    for book in (3, 3, 7, 3):
        obs = increment_observation(obs, book)
        totals.append(observation_total(obs))
    assert obs == {3: 3, 7: 1}
    assert totals == [1, 2, 3, 4]
    assert all(later >= earlier for earlier, later in zip(totals, totals[1:]))

    bound = _id()
    current = _id()
    decision = decide_session(
        current=current,
        bound=bound,
        live_observations=obs,
        live_round_trip_samples={3: 1},
        live_round_trip_closes=1,
    )
    assert decision.action == ACTION_KEEP
    assert decision.reason == REASON_SAME_SIMULATION
    assert decision.observations == obs
    assert decision.new_obs_total == 4
    assert decision.new_obs_total >= decision.old_obs_total


def test_same_simulation_restart_restores_observations():
    disk = _payload(sim="SIM_A", obs={4: 3, 8: 2}, samples={4: 2}, closes=5)
    decision = decide_session(
        current=_id("SIM_A"),
        bound=None,
        disk=disk,
        live_observations={},
    )
    assert decision.action == ACTION_RESTORE
    assert decision.reason == REASON_RELOAD_SAME_SIMULATION
    assert decision.observations == {4: 3, 8: 2}
    assert decision.round_trip_samples == {4: 2}
    assert decision.round_trip_closes == 5
    assert decision.new_obs_total == 5

    reloaded = increment_observation(decision.observations, 4)
    assert reloaded[4] == 4
    assert observation_total(reloaded) == 6
    assert observation_total(reloaded) >= decision.new_obs_total


def test_same_simulation_reload_merges_live_and_disk_with_max():
    disk = _payload(sim="SIM_A", obs={1: 2, 2: 5}, closes=2)
    decision = decide_session(
        current=_id("SIM_A"),
        bound=None,
        disk=disk,
        live_observations={1: 4, 9: 1},
        live_round_trip_closes=1,
    )
    assert decision.action == ACTION_RESTORE
    assert decision.observations == {1: 4, 2: 5, 9: 1}
    assert decision.round_trip_closes == 2
    assert decision.new_obs_total == 10


def test_simulation_id_change_resets_and_logs_fields():
    live = {1: 4, 2: 3}
    decision = decide_session(
        current=_id("SIM_B"),
        bound=_id("SIM_A"),
        live_observations=live,
        live_round_trip_closes=6,
    )
    assert decision.action == ACTION_RESET
    assert decision.reason == REASON_SIM_ID_CHANGE
    assert decision.old_sim_id == "SIM_A"
    assert decision.new_sim_id == "SIM_B"
    assert decision.old_obs_total == 7
    assert decision.new_obs_total == 0
    assert decision.observations == {}
    assert decision.round_trip_closes == 0

    fields = format_reset_fields(decision, tick=12)
    assert fields == {
        "tick": 12,
        "reason": "SIM_ID_CHANGE",
        "old_sim_id": "SIM_A",
        "new_sim_id": "SIM_B",
        "old_obs_total": 7,
        "new_obs_total": 0,
    }


def test_network_and_netuid_change_reset():
    net = decide_session(
        current=_id("SIM_A", network="mainnet"),
        bound=_id("SIM_A", network="testnet"),
        live_observations={1: 2},
    )
    assert net.action == ACTION_RESET
    assert net.reason == REASON_NETWORK_CHANGE

    uid = decide_session(
        current=_id("SIM_A", netuid=79),
        bound=_id("SIM_A", netuid=366),
        live_observations={1: 2},
    )
    assert uid.action == ACTION_RESET
    assert uid.reason == REASON_NETUID_CHANGE


def test_invalid_disk_does_not_wipe_live_observations():
    live = {2: 4}
    decision = decide_session(
        current=_id("SIM_A"),
        bound=None,
        disk={"schema": 1, "observations": {"1": -1}},
        live_observations=live,
    )
    assert decision.action == ACTION_KEEP
    assert decision.reason == REASON_INVALID_STATE
    assert decision.observations == live
    assert decision.new_obs_total == 4


def test_invalid_state_is_refused():
    for raw, reason in (
        ("not-json", REASON_INVALID_STATE),
        ({"schema": 1, "observations": {"1": -3}}, REASON_INVALID_STATE),
        ({"schema": 1, "observations": {"x": 1}}, REASON_INVALID_STATE),
        ({"schema": 1, "observations": [], "round_trip_samples": {}}, REASON_INVALID_STATE),
        ({"schema": 1, "observations": {}, "round_trip_samples": {}, "round_trip_closes": -1}, REASON_INVALID_STATE),
    ):
        assert payload_reject_reason(raw) == reason
        assert parse_payload(raw) is None
        decision = decide_session(current=_id("SIM_A"), bound=None, disk=raw)
        assert decision.action == ACTION_RESET
        assert decision.reason == reason
        assert decision.observations == {}
        assert decision.new_obs_total == 0


def test_incompatible_schema_resets():
    raw = {
        "schema": 99,
        "identity": {"simulation_id": "SIM_A", "network": "testnet", "netuid": 366, "schema": 99},
        "observations": {"1": 8},
        "round_trip_samples": {},
        "round_trip_closes": 3,
    }
    assert payload_reject_reason(raw) == REASON_INCOMPATIBLE_SCHEMA
    decision = decide_session(current=_id("SIM_A"), bound=None, disk=raw)
    assert decision.action == ACTION_RESET
    assert decision.reason == REASON_INCOMPATIBLE_SCHEMA
    assert decision.old_sim_id == "SIM_A"
    assert decision.new_obs_total == 0


def test_timestamp_rewind_is_not_a_reset():
    assert should_reset_on_timestamp_rewind() is False
    before = {1: 5}
    after = increment_observation(before, 2)
    kept = enforce_monotonic(before, after)
    assert observation_total(kept) >= observation_total(before)
    decision = decide_session(
        current=_id("SIM_A"),
        bound=_id("SIM_A"),
        live_observations=before,
    )
    assert decision.action == ACTION_KEEP
    assert decision.reason == REASON_SAME_SIMULATION
    assert REASON_SAME_SIMULATION not in RESET_REASONS


def test_missing_sim_id_does_not_wipe_live_counts():
    live = {5: 3}
    decision = decide_session(
        current=_id(sim=None),
        bound=_id("SIM_A"),
        live_observations=live,
    )
    assert decision.action == ACTION_KEEP
    assert decision.reason == REASON_MISSING_SIM_ID
    assert decision.observations == live
    assert decision.new_obs_total == 3


def test_first_bind_without_disk_starts_empty_or_keeps_live():
    empty = decide_session(current=_id("SIM_A"), bound=None, disk=None)
    assert empty.action == ACTION_KEEP
    assert empty.reason == REASON_FIRST_BIND
    assert empty.observations == {}

    live = decide_session(
        current=_id("SIM_A"),
        bound=None,
        disk=None,
        live_observations={1: 1},
    )
    assert live.reason == REASON_FIRST_BIND
    assert live.observations == {1: 1}


def test_extract_simulation_id_from_state_config_and_logdir():
    state = SimpleNamespace(config=SimpleNamespace(simulation_id="ABC1234567890"))
    assert extract_simulation_id(state) == "ABC1234567890"

    fallback = SimpleNamespace(config=None, logDir="/tmp/logs/SIMLOCKIDXXXX")
    assert extract_simulation_id(fallback) == "SIMLOCKIDXXXX"


def test_increment_refuses_non_positive_and_invalid_book():
    obs = {1: 2}
    assert increment_observation(obs, 1, 0) == {1: 2}
    assert increment_observation(obs, 1, -4) == {1: 2}
    assert increment_observation(obs, "bad", 1) == {1: 2}
    assert enforce_monotonic({1: 5}, {1: 2, 2: 1}) == {1: 5, 2: 1}


def test_reset_reasons_are_explicit_and_complete():
    assert RESET_REASONS == {
        REASON_SIM_ID_CHANGE,
        REASON_NETWORK_CHANGE,
        REASON_NETUID_CHANGE,
        REASON_INCOMPATIBLE_SCHEMA,
        REASON_INVALID_STATE,
    }


def test_quarantine_only_on_real_session_reset():
    assert session_requires_transition_quarantine(ACTION_RESET, REASON_SIM_ID_CHANGE)
    assert session_requires_transition_quarantine(ACTION_RESET, REASON_NETWORK_CHANGE)
    assert not session_requires_transition_quarantine(ACTION_KEEP, REASON_SAME_SIMULATION)
    assert not session_requires_transition_quarantine(ACTION_KEEP, REASON_FIRST_BIND)
    assert not session_requires_transition_quarantine(
        ACTION_RESTORE, REASON_RELOAD_SAME_SIMULATION
    )
    assert DEFAULT_TRANSITION_QUARANTINE_TICKS >= 1
    assert taker_allowed_after_transition(quarantine=True) is False
    assert taker_allowed_after_transition(quarantine=False) is True


def test_transition_fields_match_console_contract():
    fields = format_transition_fields(
        tick=17,
        old_sim="SIM_A",
        new_sim="SIM_B",
        reason=REASON_SIM_ID_CHANGE,
        quarantine=2,
        inventory_reconciled=1,
    )
    assert fields == {
        "tick": 17,
        "old_sim": "SIM_A",
        "new_sim": "SIM_B",
        "reason": "SIM_ID_CHANGE",
        "quarantine": 2,
        "inventory_reconciled": 1,
    }


def test_reconcile_account_base_uses_live_total():
    accounts = {
        3: SimpleNamespace(base_balance=SimpleNamespace(total=0.4, free=0.1)),
        4: SimpleNamespace(base_balance=SimpleNamespace(total=None, free=0.25)),
    }
    live = reconcile_account_base(accounts)
    assert live[3] == 0.4
    assert live[4] == 0.25


def test_stale_inventory_cannot_generate_taker_after_sim_id_change():
    session = decide_session(
        current=_id("SIM_B"),
        bound=_id("SIM_A"),
        live_observations={1: 9},
        live_round_trip_closes=4,
    )
    assert session.action == ACTION_RESET
    assert session.reason == REASON_SIM_ID_CHANGE
    assert session_requires_transition_quarantine(session.action, session.reason)

    class _Store:
        def __init__(self):
            self.live = {(7, 1): "stale-quote"}
            self.pending = ["stale-markout"]

        def clear(self):
            self.live.clear()
            self.pending.clear()

    class _Mem:
        recent_pnl = -18.0
        loss_streak = 6

    agent = SimpleNamespace(
        _open_positions={7: [{"qty": 1.5, "entry": 100.0}]},
        _position_ticks={7: 80},
        _research_position_tick_seen={7: 1},
        _inventory_reason={7: "MAX_LONG"},
        _research_realization_last={7: "EMERGENCY"},
        _research_quote_store=_Store(),
        book_memory={7: _Mem()},
    )
    clear_stale_session_runtime(agent)
    assert agent._open_positions == {}
    assert agent._position_ticks == {}
    assert agent._inventory_reason == {}
    assert agent._research_quote_store.live == {}
    assert agent._research_quote_store.pending == []
    assert agent.book_memory[7].recent_pnl == 0.0
    assert agent.book_memory[7].loss_streak == 0
    assert taker_allowed_after_transition(quarantine=True) is False

    stale = dict(
        book=7,
        inventory_size=1.5,
        inventory_ratio=0.99,
        inventory_age=80.0,
        unrealized_pnl=-40.0,
        expected_markout=-12.0,
        volatility=0.01,
        imbalance=-0.5,
        observations_remaining=0,
        volume_cap_headroom=1.0,
        band="MAX_LONG",
        stop_loss_hit=True,
        hard_emergency=True,
        fee_bps=8.0,
        spread_bps=10.0,
        slippage_bps=6.0,
    )
    blocked = evaluate_realization(**stale, transition_quarantine=True)
    live = evaluate_realization(**stale, transition_quarantine=False)
    hybrid = hybrid_taker_decision(
        hard_emergency=True,
        unrealized_pnl_bps=-40.0,
        crossing_cost_bps=8.0,
        transition_quarantine=True,
    )
    assert hybrid.take is False
    assert hybrid.reason == REASON_REJECT_TRANSITION
    assert blocked.selected_action != ACTION_TAKER
    assert blocked.taker_allowed is False
    assert blocked.hybrid_reason == REASON_REJECT_TRANSITION
    assert live.selected_action == ACTION_TAKER


def test_research_handle_resolves_session_before_inventory_and_orders():
    handle = RESEARCH_SRC.split("def handle(")[1].split("def respond(")[0]
    assert handle.index("self._research_sync_session(state)") < handle.index(
        "response = super().handle(state)"
    )
    assert handle.index("self._research_sync_session(state)") < handle.index(
        "self._research_evaluate_markouts(state)"
    )
    assert "_research_in_transition_quarantine" in handle
    assert "_research_transition_reconcile_cancels" in handle
    close = RESEARCH_SRC.split("def _execute_aggressive_close(")[1].split(
        "def _manage_inventory("
    )[0]
    assert "taker_allowed_after_transition" in close
    quotes = RESEARCH_SRC.split("def _place_skewed_quotes(")[1].split(
        "def _place_directional_round_trip("
    )[0]
    assert "_research_in_transition_quarantine" in quotes
    assert "transition_quarantine=self._research_in_transition_quarantine()" in RESEARCH_SRC
    assert "[S1R_SESSION_TRANSITION]" in RESEARCH_SRC
    assert "old_sim=" in RESEARCH_SRC
    assert "inventory_reconciled=" in RESEARCH_SRC
