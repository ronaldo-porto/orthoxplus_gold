#!/usr/bin/env bash

# V4.12.14 deployment compatibility checks.
# Structural AST check avoids false incompatibility from harmless annotation or
# formatting differences while still requiring RealizationDecision.unified_exit.
python - <<'PYV41212REALIZATION'
import ast
from pathlib import Path

path = Path("agents/strategy/research_realization.py")
if not path.is_file():
    raise SystemExit(
        "ERROR: missing agents/strategy/research_realization.py required by V4.12.3"
    )

tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
realization = next(
    (node for node in tree.body
     if isinstance(node, ast.ClassDef) and node.name == "RealizationDecision"),
    None,
)
if realization is None:
    raise SystemExit(
        "ERROR: stale/incompatible research_realization.py: missing RealizationDecision"
    )

has_unified_exit = any(
    isinstance(item, ast.AnnAssign)
    and isinstance(item.target, ast.Name)
    and item.target.id == "unified_exit"
    for item in realization.body
)
if not has_unified_exit:
    raise SystemExit(
        "ERROR: stale/incompatible research_realization.py: "
        "V4.12.3 requires RealizationDecision.unified_exit"
    )

print("V4.12.3 RealizationDecision.unified_exit API OK")
PYV41212REALIZATION
if [ ! -f agents/strategy/research_unified_exit.py ]; then
  echo "ERROR: missing agents/strategy/research_unified_exit.py required by V4.12.3" >&2
  exit 1
fi
if ! grep -q 'UNIFIED_EXIT_VERSION = "bounded_stale_bridge_v4_12_10"' agents/strategy/research_unified_exit.py; then
  echo "ERROR: stale/incompatible research_unified_exit.py: V4.12.10 bounded stale bridge required" >&2
  exit 1
fi
PYTHONPATH="agents/strategy${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV41210BRIDGE'
from research_unified_exit import bounded_stale_direct_bridge
common = dict(
    legacy_direct_authorized=True,
    positive_ev_authorized=True,
    sn79_take=True,
    legacy_taker_ev_bps=0.01,
    legacy_wait_ev_bps=-20.0,
    p_maker_fill=0.01,
    maker_fill_evidence=True,
    failed_exit_count=20,
    inventory_age=30.0,
    min_failed_exits=8,
    min_age_ticks=16.0,
    max_maker_fill=0.08,
    ev_advantage_bps=0.50,
    roundtrip_loss_floor_bps=-12.0,
)
if not bounded_stale_direct_bridge(actual_roundtrip_taker_net_bps=-5.2, **common):
    raise SystemExit("ERROR: V4.12.10 bounded stale bridge positive contract failed")
if bounded_stale_direct_bridge(actual_roundtrip_taker_net_bps=-12.01, **common):
    raise SystemExit("ERROR: V4.12.10 bounded stale bridge hard floor failed")
if bounded_stale_direct_bridge(actual_roundtrip_taker_net_bps=-15.0, roundtrip_loss_floor_bps=-30.0, **{k:v for k,v in common.items() if k != 'roundtrip_loss_floor_bps'}):
    raise SystemExit("ERROR: V4.12.10 bridge floor widening guard failed")
print("V4.12.10 bounded stale bridge API OK")
PYV41210BRIDGE

# V4.12.11 ONE_AWAY completion rescue guard.
PYTHONPATH="agents/strategy${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV41211ONEAWAY'
from research_entry_size import admit_minimum_order
from research_quote_hysteresis import (
    ONE_AWAY_STALE_TTL_VERSION,
    one_away_stale_completion_ttl,
)
if ONE_AWAY_STALE_TTL_VERSION != "one_away_stale_ttl_v4_12_11":
    raise SystemExit("ERROR: stale research_quote_hysteresis.py for V4.12.11")
d = admit_minimum_order(
    safe_size=0.04, min_order=0.25, trading_ev=0.05, inventory_risk=0.10,
    exit_capacity=0.055, volume_headroom=1.0, remaining_inventory=1.2,
    observations_remaining=1, enable_one_away_exact_min=True,
    one_away_min_safe_fraction=0.15, one_away_min_exit_fraction=0.20,
)
if not d.allow or abs(d.size - 0.25) > 1e-12 or d.trigger != "ONE_AWAY_EXACT_MIN":
    raise SystemExit("ERROR: V4.12.11 ONE_AWAY exact-min positive contract failed")
if admit_minimum_order(
    safe_size=0.03, min_order=0.25, trading_ev=0.05, inventory_risk=0.10,
    exit_capacity=0.055, volume_headroom=1.0, remaining_inventory=1.2,
    observations_remaining=1, enable_one_away_exact_min=True,
    one_away_min_safe_fraction=0.01, one_away_min_exit_fraction=0.01,
).allow:
    raise SystemExit("ERROR: V4.12.11 ONE_AWAY safe-size hard floor failed")
ttl, reason, used = one_away_stale_completion_ttl(
    chosen_ttl_ms=None, ttl_reason="STALE", completion_candidate=True,
    completion_samples=2, completion_target=3, trading_ev=0.05,
    market_regime="MIXED", min_ttl_ms=250.0,
)
if not used or ttl != 250.0 or reason != "ONE_AWAY_STALE_SHORT":
    raise SystemExit("ERROR: V4.12.11 ONE_AWAY stale TTL rescue contract failed")
ttl, _, used = one_away_stale_completion_ttl(
    chosen_ttl_ms=None, ttl_reason="STALE", completion_candidate=True,
    completion_samples=2, completion_target=3, trading_ev=0.05,
    market_regime="TOXIC", min_ttl_ms=250.0,
)
if used or ttl is not None:
    raise SystemExit("ERROR: V4.12.11 toxic TTL override guard failed")
print("V4.12.11 ONE_AWAY completion rescue API OK")
PYV41211ONEAWAY

# V4.12.14 authoritative-L1 pending-reprice post-only retry guard.
PYTHONPATH="agents/strategy${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV41214CONTRACT'
from research_contract_guard import (
    CONTRACT_GUARD_VERSION,
    HARD_LIFETIME_TICKS,
    guard_is_active,
    guard_should_skip,
    guarded_post_only_price,
    register_contract_reject,
    resolve_book_from_state_mapping,
)
if CONTRACT_GUARD_VERSION != "authoritative_l1_contract_guard_v4_12_14":
    raise SystemExit("ERROR: stale research_contract_guard.py for V4.12.14")
if HARD_LIFETIME_TICKS != 512:
    raise SystemExit("ERROR: V4.12.14 hard-lifetime contract changed")
s = register_contract_reject(None, current_tick=44)
if not guard_should_skip(s, current_tick=45):
    raise SystemExit("ERROR: V4.12.14 immediate retry cooldown failed")
if not guard_is_active(s, current_tick=77):
    raise SystemExit("ERROR: V4.12.14 pending state did not survive old 32-tick gap")
s2 = register_contract_reject(s, current_tick=77)
if s2.streak != 2 or s2.first_reject_tick != 44:
    raise SystemExit("ERROR: V4.12.14 reject streak reset across no-touch gap")
if not guard_should_skip(s2, current_tick=79) or guard_should_skip(s2, current_tick=80):
    raise SystemExit("ERROR: V4.12.14 bounded second-reject cooldown failed")
if guard_is_active(s, current_tick=44 + HARD_LIFETIME_TICKS + 1):
    raise SystemExit("ERROR: V4.12.14 hard lifetime did not expire")
p = guarded_post_only_price(
    side="sell", original_price=309.03, best_bid=309.02, best_ask=309.40,
    tick_size=0.01, reject_streak=2,
)
if p is None or abs(p - 309.42) > 1e-12:
    raise SystemExit("ERROR: V4.12.14 SELL fresh-touch safe reprice failed")
p = guarded_post_only_price(
    side="buy", original_price=309.39, best_bid=309.02, best_ask=309.40,
    tick_size=0.01, reject_streak=2,
)
if p is None or abs(p - 309.00) > 1e-12:
    raise SystemExit("ERROR: V4.12.14 BUY fresh-touch safe reprice failed")
from collections.abc import Mapping
class LazyBooksLike(Mapping):
    def __init__(self): self._d = {54: object()}
    def __getitem__(self, key): return self._d[key]
    def __iter__(self): return iter(self._d)
    def __len__(self): return len(self._d)
lazy_books = LazyBooksLike()
if isinstance(lazy_books, dict) or resolve_book_from_state_mapping(lazy_books, 54) is None:
    raise SystemExit("ERROR: V4.12.14 LazyBooks/Mapping L1 resolution failed")
print("V4.12.14 authoritative-L1 contract guard API OK")
PYV41214CONTRACT
# Internal runner: run_miner_multi.sh
# SN79 testnet launcher for Strategy1_Research V4.12.14 Authoritative-L1 Guard Research (Miner 1).
# Default PM2 name: sn79-m1 | Default Axon port: 8091
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

WALLET_NAME="${WALLET_NAME:-taos}"
HOTKEY_NAME="${HOTKEY_NAME:-miner}"
ENDPOINT="${ENDPOINT:-wss://test.finney.opentensor.ai:443}"
NETUID="${NETUID:-366}"
AXON_PORT="${AXON_PORT:-8091}"
AGENT_PATH="${AGENT_PATH:-$REPO_ROOT/agents/strategy}"
PM2_NAME="${PM2_NAME:-sn79-m1}"
RESEARCH_EVERY_N="${RESEARCH_EVERY_N:-10}"
RESEARCH_BOOK="${RESEARCH_BOOK:--1}"
RESEARCH_JSONL="${RESEARCH_JSONL:-1}"
RESEARCH_CONSOLE="${RESEARCH_CONSOLE:-1}"
RESEARCH_QUEUE="${RESEARCH_QUEUE:-65536}"
RESEARCH_DIR="${RESEARCH_DIR:-$REPO_ROOT/logs/m1_strategy1_research}"

EXTRA=()
while getopts "w:h:u:a:e:p:i:" flag; do
  case "$flag" in
    w) WALLET_NAME="$OPTARG" ;;
    h) HOTKEY_NAME="$OPTARG" ;;
    u) NETUID="$OPTARG" ;;
    a) AXON_PORT="$OPTARG" ;;
    e) ENDPOINT="$OPTARG" ;;
    p) EXTRA+=(-p "$OPTARG") ;;
    i) PM2_NAME="$OPTARG" ;;
    *) exit 2 ;;
  esac
done

[[ -f "$REPO_ROOT/run_miner_multi.sh" ]] || { echo "run_miner_multi.sh missing" >&2; exit 1; }
[[ -f "$AGENT_PATH/Strategy1_Research.py" ]] || { echo "Strategy1_Research.py missing" >&2; exit 1; }
grep -q 'RESEARCH_POLICY_VERSION = "authoritative_l1_guard_v4_12_14"' "$AGENT_PATH/Strategy1_Research.py" || {
  echo "ERROR: Strategy1_Research.py is not authoritative_l1_guard_v4_12_14" >&2
  exit 1
}
[[ -f "$AGENT_PATH/Strategy1_Debug.py" ]] || { echo "Strategy1_Debug.py missing" >&2; exit 1; }
[[ -f "$AGENT_PATH/research_fill_hazard.py" ]] || { echo "research_fill_hazard.py missing" >&2; exit 1; }
[[ -f "$AGENT_PATH/research_unified_exit.py" ]] || { echo "research_unified_exit.py missing" >&2; exit 1; }
[[ -f "$AGENT_PATH/research_entry_size.py" ]] || { echo "research_entry_size.py missing" >&2; exit 1; }

# V4.12.4 deployment guard: require completion exact-min admission API.
PYTHONPATH="$AGENT_PATH${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV4124ENTRY'
import inspect
import research_entry_size
required = {
    "enable_two_away_exact_min",
    "two_away_min_trading_ev",
    "two_away_max_inventory_risk",
    "two_away_min_exit_fraction",
    "two_away_min_headroom",
}
sig = inspect.signature(research_entry_size.admit_minimum_order)
missing = sorted(required.difference(sig.parameters))
if missing:
    raise SystemExit(
        "ERROR: stale/incompatible research_entry_size.py for V4.12.4; "
        f"missing admit_minimum_order args={missing}; loaded={research_entry_size.__file__}"
    )
PYV4124ENTRY


# V4.12.9 deployment guard: rolling-deadline scheduler must suppress early
# qualified refresh while allowing critical refresh, and incomplete progress must
# expose its own rolling expiry deadline.
PYTHONPATH="$AGENT_PATH${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV4129DEADLINE'
from research_execution_lanes import (
    LaneBook, apply_breadth_rotation_gate, completion_sort_key,
)
from research_kappa_state import kappa_expiry_from_timestamps

rows = [
    LaneBook(book_id=1, score_qualified=True, observations_remaining=0),
    LaneBook(book_id=2, score_qualified=True, observations_remaining=0,
             needs_refresh=True, deadline_urgency=0.20, deadline_critical=False),
    LaneBook(book_id=3, score_qualified=True, observations_remaining=0,
             needs_refresh=True, deadline_urgency=0.80, deadline_critical=True),
    LaneBook(book_id=10, observations_remaining=1, economics_ok=True,
             entry_feasible=True, deadline_urgency=0.10),
    LaneBook(book_id=11, observations_remaining=2, economics_ok=True,
             entry_feasible=True, deadline_urgency=0.70, deadline_critical=True),
]
gated, suppressed, productive = apply_breadth_rotation_gate(
    rows, enabled=True, min_productive_incomplete=2,
)
by_id = {row.book_id: row for row in gated}
if productive != 2 or suppressed != {1, 2}:
    raise SystemExit(
        "ERROR: stale/incompatible research_execution_lanes.py for V4.12.8; "
        f"productive={productive} suppressed={sorted(suppressed)}"
    )
if by_id[1].entry_feasible or by_id[2].entry_feasible or not by_id[3].entry_feasible:
    raise SystemExit("ERROR: V4.12.9 deadline breadth semantics failed")

# St6.4: one productive incomplete book is now sufficient to rotate stable
# qualified acquisition while breadth is below the protected target.
single = [
    LaneBook(book_id=21, score_qualified=True, observations_remaining=0),
    LaneBook(book_id=22, observations_remaining=1, economics_ok=True, entry_feasible=True),
]
single_gated, single_suppressed, single_productive = apply_breadth_rotation_gate(
    single, enabled=True, min_productive_incomplete=1,
)
if single_productive != 1 or single_suppressed != {21}:
    raise SystemExit(
        "ERROR: V4.12.9 single-incomplete breadth rotation failed; "
        f"productive={single_productive} suppressed={sorted(single_suppressed)}"
    )

# Critical one-RT refresh must outrank a noncritical one-away candidate.
critical_refresh = rows[2]
normal_one_away = rows[3]
if not (completion_sort_key(critical_refresh) < completion_sort_key(normal_one_away)):
    raise SystemExit("ERROR: V4.12.8 earliest-deadline completion ordering failed")

# Two incomplete observations must have a real progress deadline instead of None.
expiry = kappa_expiry_from_timestamps(
    99, [100, 200], now=950, lookback_ns=1000, required_observations=3,
    warning_horizon_frac=0.20,
)
if expiry.qualified or expiry.time_to_expiry_ns is None or expiry.expiry_urgency <= 0.0:
    raise SystemExit("ERROR: V4.12.8 incomplete-progress deadline semantics failed")
print("V4.12.8 rolling deadline scheduler API OK")
PYV4129DEADLINE

# V4.12.10 deployment guard: require the latest unified-exit version, then
# preserve the V4.12.6 safety probes and matched V4.12.2 hazard API.
PYTHONPATH="$AGENT_PATH${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV4122'
import inspect
import research_fill_hazard
import research_unified_exit
if getattr(research_unified_exit, "UNIFIED_EXIT_VERSION", "") != "bounded_stale_bridge_v4_12_10":
    raise SystemExit(
        "ERROR: stale/incompatible research_unified_exit.py; "
        f"loaded={research_unified_exit.__file__}"
    )
# V4.12.6 semantic guard: preserve V4.12.5 EMERGENCY safety and verify early escape
# an unlimited-loss Taker. Only the explicit catastrophic/max-inventory flag
# may select HARD_RISK_TAKER.
_probe = research_unified_exit.choose_unified_exit(
    maker_net_bps=0.5, taker_net_bps=-56.0, wait_ev_bps=-8.0,
    maker_price=100.01, taker_price=99.4, breakeven_px=100.0,
    p_maker_fill=0.05, peak_taker_net_bps=0.5, failed_exit_count=30,
    inventory_age=50, observations_remaining=1, expected_markout_bps=-5.0,
    adverse_selection_risk=1.0, inventory_state="EMERGENCY",
    stop_loss_hit=True, hard_emergency=False, protective_loss_floor_bps=-2.0,
)
if _probe.action == research_unified_exit.ACTION_HARD_RISK_TAKER:
    raise SystemExit(
        "ERROR: stale/unsafe research_unified_exit.py: EMERGENCY-only still authorizes HARD_RISK_TAKER"
    )
# V4.12.6 early-escape guard: after a few failed Maker exits, a bounded
# protective Taker inside the unchanged -2 bps floor should execute when waiting
# is materially worse. The floor itself must still block -2.01 bps.
_early = research_unified_exit.choose_unified_exit(
    maker_net_bps=0.5, taker_net_bps=-1.2, wait_ev_bps=-5.0,
    maker_price=100.01, taker_price=99.98, breakeven_px=100.0,
    p_maker_fill=0.04, peak_taker_net_bps=1.0, failed_exit_count=3,
    inventory_age=4, observations_remaining=1, expected_markout_bps=0.0,
    adverse_selection_risk=0.0, inventory_state="NORMAL",
    stop_loss_hit=False, hard_emergency=False, protective_loss_floor_bps=-2.0,
    early_escape_enabled=True, early_escape_failed_exits=3,
    early_escape_min_age_ticks=5, early_escape_drawdown_bps=1.5,
    early_escape_floor_headroom_bps=0.75, early_escape_ev_advantage_bps=0.5,
)
if _early.action != research_unified_exit.ACTION_TAKER_PROTECT or not _early.early_escape_trigger:
    raise SystemExit(
        "ERROR: stale/incompatible research_unified_exit.py: V4.12.6 early escape probe failed"
    )
_floor = research_unified_exit.choose_unified_exit(
    maker_net_bps=0.5, taker_net_bps=-2.01, wait_ev_bps=-10.0,
    maker_price=100.01, taker_price=99.97, breakeven_px=100.0,
    p_maker_fill=0.01, peak_taker_net_bps=1.0, failed_exit_count=20,
    inventory_age=30, observations_remaining=1, expected_markout_bps=-5.0,
    adverse_selection_risk=1.0, inventory_state="EMERGENCY",
    stop_loss_hit=True, hard_emergency=False, protective_loss_floor_bps=-2.0,
)
if _floor.action != research_unified_exit.ACTION_KEEP_MAKER:
    raise SystemExit(
        "ERROR: unsafe V4.12.6 protective floor: loss below -2 bps was authorized"
    )
required = {
    "distance_decay_bps",
    "distance_near_boost",
    "distance_floor_mult",
    "fallback_policy_weight",
}
sig = inspect.signature(research_fill_hazard.FillHazardModel)
missing = sorted(required.difference(sig.parameters))
if missing:
    raise SystemExit(
        "ERROR: stale/incompatible research_fill_hazard.py for V4.12.2; "
        f"missing FillHazardModel args={missing}; loaded={research_fill_hazard.__file__}"
    )
print(f"V4.12.3 unified exit + FillHazard API OK: {research_unified_exit.__file__} | {research_fill_hazard.__file__}")
PYV4122

# Parent Debug still computes the decision records. Its synchronous JSONL is disabled;
# Strategy1_Research persists the events asynchronously instead.
export STRATEGY1_DEBUG=1
export STRATEGY1_DEBUG_JSONL=0
export STRATEGY1_DEBUG_EVERY_N="$RESEARCH_EVERY_N"
export STRATEGY1_DEBUG_BOOK="$RESEARCH_BOOK"
export STRATEGY1_RESEARCH=1
export STRATEGY1_RESEARCH_EVERY_N="$RESEARCH_EVERY_N"
export STRATEGY1_RESEARCH_BOOK="$RESEARCH_BOOK"
export STRATEGY1_RESEARCH_JSONL="$RESEARCH_JSONL"
export STRATEGY1_RESEARCH_CONSOLE="$RESEARCH_CONSOLE"
export STRATEGY1_RESEARCH_QUEUE="$RESEARCH_QUEUE"
export STRATEGY1_RESEARCH_DIR="$RESEARCH_DIR"
mkdir -p "$RESEARCH_DIR"

# V4.3 Phase 5 hysteresis + adaptive TTL. Dust escape stays experimental/off.
# Hard dust/Kappa invariants and Phase 4 Score-EV ranking are unchanged.
PARAMS="enable_mm_strategy=1 enable_kappa_strategy=0 lazy_load=1 \
fast_update=1 sync_event_csv=0 history_len=0 \
mm_base_size=0.25 max_inventory_base=1.20 inventory_close_threshold=0.25 \
max_mm_books_per_tick=6 max_managed_books_per_tick=10 \
min_expected_alpha=0.18 min_expected_realized_pnl=0.0 \
mm_expiry_period_ns=500000000 maintenance_size_mult=0.25 \
passive_exit_only=1 aggressive_close_min_ticks=300 position_max_ticks=300 \
mm_skip_inactive_tier=1 toxic_loss_streak=4 enable_auto_tuning=0 allow_tuning_config=0 \
verbose_log=0 log_every_n=100 log_mm_strategy=0 log_direction=0 log_book_profile=0 \
log_regime=0 log_momentum_pnl=0 log_book_memory=0 \
debug_enabled=1 debug_every_n=${RESEARCH_EVERY_N} debug_jsonl=0 debug_book_id=${RESEARCH_BOOK} \
research_enabled=1 research_every_n=${RESEARCH_EVERY_N} research_book_id=${RESEARCH_BOOK} \
research_jsonl=${RESEARCH_JSONL} research_console=${RESEARCH_CONSOLE} research_compact_console=1 research_queue_size=${RESEARCH_QUEUE} \
research_fix_global_stress=1 research_neutral_fallback=1 \
research_adaptive_spread_thresholds=1 research_stress_percentile=0.95 research_toxic_percentile=0.99 \
research_stress_floor_bps=8.0 research_toxic_floor_bps=10.0 \
research_stress_fallback_bps=35.0 research_toxic_fallback_bps=40.0 research_toxic_gap_bps=2.0 \
research_inactive_bootstrap=1 research_trade_global_stress=1 research_global_stress_size_mult=0.35 \
research_sync_min_order=1 research_promote_min_order=1 research_bootstrap_maintenance_min_order=1 \
research_bootstrap_dead_as_mm=1 research_bootstrap_extreme_vol_mult=1.75 \
research_fix_inventory_util=1 research_fix_quote_reservation=1 \
research_bootstrap_manage_min_clip=1 research_bootstrap_allow_aggressive_close=1 \
research_bootstrap_force_close_ticks=60 research_dust_safe_close=1 research_rotate_jsonl=1 \
research_candidate_backfill=1 research_candidate_attempt_cap=12 \
research_aggressive_close_touch_gate=1 research_aggressive_close_fee_buffer_bps=3.0 \
research_aggressive_close_min_net_bps=0.0 research_toxic_pnl_min_samples=3 \
research_toxic_pnl_hard_floor=-0.05 research_yellow_sparse_active=1 \
research_green_sparse_active=1 research_dust_park_enabled=1 \
research_dust_heartbeat_ticks=250 research_dust_warn_ticks=1000 \
research_dust_compact_enabled=1 research_dust_compact_min_fraction=0.50 \
research_dust_compact_books_per_tick=2 research_kappa_completion_enabled=1 \
research_kappa_completion_target=3 research_kappa_completion_rank_bonus=0.30 \
research_kappa_completion_attempt_cap=6 research_kappa_completion_success_cap=3 \
research_kappa_completion_fill_mult=0.70 research_kappa_completion_fill_floor=0.08 \
research_kappa_completion_relaxed_success_cap=3 research_kappa_completion_recent_pnl_floor=-0.01 \
research_actionable_fill_enabled=1 research_actionable_fill_min_samples=4 \
research_actionable_fill_prior_strength=6.0 research_actionable_fill_prior_actionable=0.85 \
research_actionable_fill_rank_weight=0.10 research_dust_risk_rank_penalty=0.18 \
research_dust_risk_target=0.15 research_kappa_one_away_bonus=0.10 \
research_partial_fill_hold_enabled=1 research_partial_fill_hold_min_dust_prob=0.12 \
research_partial_fill_hold_one_away_only=0 research_partial_fill_hold_max_ns=750000000 \
research_force_mm_post_only=1 research_dust_compact_adaptive=1 \
research_dust_compact_cooldown_ticks=100 research_dust_compact_max_cooldown_ticks=600 \
research_dust_compact_prior_fill=0.02 research_dust_compact_prior_strength=8.0 \
research_enable_fill_hazard=1 research_use_fill_hazard_for_policy=0 \
research_enable_score_ev=1 research_enable_score_velocity=1 research_score_velocity_weight=0.08 research_enable_quote_hysteresis=1 \
research_enable_adaptive_ttl=1 research_enable_dust_escape=0 research_ttl_min_ms=250 research_ttl_max_ms=1500 research_quiet_ttl_ms=1000 research_quiet_exit_ttl_ms=950 research_one_away_exit_ttl_ms=975 \
research_enable_fast_candidate_screen=1 research_candidate_count=10 research_cohort_size=8 research_cohort_exploration_slots=1 research_max_open_books=6 \
research_enable_lane_scheduler=1 research_enable_aggressive_coverage=1 research_coverage_slots=3 research_completion_slots=5 research_realization_slots=3 research_shared_overflow_slots=1 \
research_lifecycle_taker_exit_prob=0.30 research_lifecycle_slippage_bps=0.75 research_lifecycle_holding_bps=0.50 \
research_positive_ev_min_order_override=0 research_positive_ev_min_safe_fraction=0.35 research_positive_ev_min_exit_fraction=0.45 research_positive_ev_min_trading_ev=0.05 \
research_one_away_exact_min_enabled=1 research_one_away_exact_min_ev_bps=0.0 research_one_away_exact_min_safe_fraction=0.15 research_one_away_exact_min_exit_fraction=0.20 \
research_two_away_exact_min_enabled=1 research_two_away_exact_min_ev=0.0 research_two_away_exact_min_max_inventory_risk=0.35 research_two_away_exact_min_exit_fraction=0.20 research_two_away_exact_min_min_headroom=0.25 \
research_quote_tighten_mult=0.85 research_quote_width_floor_mult=0.80 research_enable_one_away_quiet_tightening=1 research_one_away_quiet_width_mult=0.60 research_one_away_quiet_min_ev=0.0 research_one_away_max_touch_bps=5.0 research_fill_distance_decay_bps=6.0 research_fill_distance_near_boost=1.35 research_fill_distance_floor_mult=0.10 research_fill_fallback_policy_weight=0.45 research_local_kappa_refresh_ticks=10 research_score_qualified_pnl_floor=0.0 research_score_qualified_kappa_floor=0.0 research_p95_target_ms=120 \
research_enable_inventory_state_v2=1 research_enable_exit_urgency_v2=1 \
research_enable_hybrid_realization_v2=1 research_enable_economic_taker=1 \
research_enable_precise_reduction_qty=1 research_enable_dust_economic_gate=1 \
research_enable_authoritative_kappa_state=1 research_enable_markout_v2=1 \
research_enable_fill_hazard_exit_compare=1 research_enable_sn79_action_utility=1 research_enable_score_taker_direct=1 research_enable_economic_taker_direct=1 research_economic_direct_max_loss_bps=0.0 research_enable_aggressive_positive_ev_taker=1 research_aggressive_positive_ev_min_net_bps=0.0 research_aggressive_positive_ev_switch_margin_bps=0.50 research_aggressive_positive_ev_one_away_margin_bps=0.0 research_aggressive_positive_ev_failed_exit_count=8 research_aggressive_positive_ev_min_age_ticks=16 research_aggressive_positive_ev_max_maker_fill=0.08 research_aggressive_positive_ev_min_urgency=0.30 research_maker_escalate_failed_exit_count=8 research_one_away_maker_escalate_failed_exit_count=3 research_enable_risk_taker_direct=0 research_risk_direct_max_loss_bps=-10.0 research_risk_direct_min_age_ticks=24 research_risk_direct_failed_exit_count=3 research_risk_direct_min_ev_advantage_bps=1.0 research_failed_exit_penalty_bps=0.75 research_exit_age_penalty_bps_per_tick=0.03 research_cancel_before_taker=1 research_sn79_pnl_scale_bps=8.0 research_sn79_pnl_weight=1.0 research_sn79_round_trip_weight=0.30 research_sn79_kappa_weight=0.35 research_sn79_coverage_weight=0.15 research_sn79_capital_release_weight=0.15 research_sn79_risk_reduction_weight=0.20 research_sn79_velocity_weight=0.25 research_sn79_downside_weight=0.45 research_sn79_min_utility_margin=0.03 research_sn79_max_score_subsidy_loss_bps=0.0 research_sn79_one_away_loss_floor_bps=0.0 research_sn79_two_away_loss_floor_bps=0.0 research_sn79_uncovered_loss_floor_bps=0.0 research_allow_score_loss_subsidy=0 research_kappa_lookback_ns=10800000000000 research_kappa_expiry_warning_frac=0.20 research_kappa_expiry_rank_bonus=0.20 research_suppress_qualified_acquisition=1 research_qualified_suppression_min_incomplete=1 research_deadline_scheduler_enabled=1 research_deadline_critical_urgency=0.50 research_deadline_rank_bonus=0.25 research_score_target_books=88 research_stale_maker_rescue_enabled=1 research_stale_maker_rescue_failed_exits=4 research_stale_maker_rescue_critical_failed_exits=1 research_stale_maker_rescue_floor_bps=-1.0 research_entry_recheck_ticks=12 research_hybrid_partial_frac_cap=0.90 research_ladder_passive_max=0.15 research_ladder_competitive_max=0.30 research_ladder_aggressive_max=0.45 research_enable_unified_exit=1 research_unified_maker_net_floor_bps=0.0 research_unified_stale_bridge_roundtrip_floor_bps=-12.0 research_unified_profit_lock_min_bps=1.0 research_unified_profit_lock_drawdown_bps=2.0 research_unified_switch_margin_bps=0.50 research_enable_protective_taker=1 research_protective_taker_loss_floor_bps=-2.0 research_protective_taker_ev_advantage_bps=1.0 research_protective_taker_failed_exits=6 research_protective_taker_min_age_ticks=8 research_protective_taker_adverse_bps=2.0 research_early_escape_enabled=1 research_early_escape_failed_exits=3 research_early_escape_min_age_ticks=5 research_early_escape_drawdown_bps=1.5 research_early_escape_floor_headroom_bps=0.75 research_early_escape_ev_advantage_bps=0.50 research_session_save_every_n=100"

echo "[Strategy1_Research] pm2_name=$PM2_NAME"
echo "[Strategy1_Research] wallet=$WALLET_NAME"
echo "[Strategy1_Research] hotkey=$HOTKEY_NAME"
echo "[Strategy1_Research] netuid=$NETUID"
echo "[Strategy1_Research] axon_port=$AXON_PORT"
echo "[Strategy1_Research] version=authoritative_l1_guard_v4_12_14"
echo "[Strategy1_Research] log_dir=$RESEARCH_DIR"

exec "$REPO_ROOT/run_miner_multi.sh" \
  -i "$PM2_NAME" -e "$ENDPOINT" -w "$WALLET_NAME" -h "$HOTKEY_NAME" -u "$NETUID" -a "$AXON_PORT" \
  -g "$AGENT_PATH" -n Strategy1_Research -m "$PARAMS" "${EXTRA[@]}"
