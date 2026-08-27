#!/usr/bin/env bash
# Internal runner: run_miner_multi.sh
# SN79 launcher for AdaptiveAgent V4.13.9 realtime (BaseStrategy V4.13.9 champion) (Miner 3).
# Default PM2 name: sn79-m3 | Default Axon port: 8093
# Requires agents/strategy/AdaptiveAgent.py with ADAPTIVE_VERSION=adaptive_v4_13_9_realtime.
#
# Normal:
#   ./run_adaptive_agent.sh -w sw_ck_st4_m3 -h sw_hk_st4_m3 -u 366 -a 8093
#
# Detailed BaseStrategy + Adaptive telemetry:
#   ./run_adaptive_agent.sh -w sw_ck_st4_m3 -h sw_hk_st4_m3 -u 366 -a 8093 --log

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

WALLET_NAME="${WALLET_NAME:-taos}"
HOTKEY_NAME="${HOTKEY_NAME:-miner}"
ENDPOINT="${ENDPOINT:-wss://test.finney.opentensor.ai:443}"
NETUID="${NETUID:-366}"
AXON_PORT="${AXON_PORT:-8093}"
AGENT_PATH="${AGENT_PATH:-$REPO_ROOT/agents/strategy}"
PM2_NAME="${PM2_NAME:-sn79-m3}"

LOG_ENABLED=0
LOG_EVERY_N="${LOG_EVERY_N:-10}"
LOG_BOOK="${LOG_BOOK:--1}"
LOG_JSONL="${LOG_JSONL:-1}"
LOG_CONSOLE="${LOG_CONSOLE:-1}"
LOG_QUEUE="${LOG_QUEUE:-65536}"
LOG_DIR="${LOG_DIR:-$REPO_ROOT/logs/m3_adaptive_agent}"

EXTRA=()

usage() {
  cat <<'EOF'
Usage:
  ./run_adaptive_agent.sh -w WALLET -h HOTKEY -u NETUID -a AXON_PORT [--log]

Options:
  -w, --wallet NAME       wallet/coldkey
  -h, --hotkey NAME       wallet hotkey
  -u, --netuid N          subnet netuid
  -a, --axon-port PORT    axon port
  -e, --endpoint URL      subtensor endpoint
  -p VALUE                extra run_miner parameter (repeatable)
  -i, --pm2-name NAME     PM2 process name (default sn79-m3)

  --log                   enable detailed BaseStrategy V4.13.9 telemetry
  --every N               log sample cadence
  --book ID               one-book telemetry filter; -1 = all
  --log-dir PATH          JSONL directory
  --no-console            disable research console output
  --no-jsonl              disable research JSONL output
  --help                  show this help

Environment:
  ADAPTIVE_ENVIRONMENT_KEY
  ADAPTIVE_STATE_DIR
EOF
}

need_value() {
  local opt="$1"
  local argc="$2"
  if (( argc < 2 )); then
    echo "ERROR: $opt requires a value" >&2
    exit 2
  fi
}

while (($#)); do
  case "$1" in
    -w|--wallet)
      need_value "$1" "$#"; WALLET_NAME="$2"; shift 2 ;;
    -h|--hotkey)
      need_value "$1" "$#"; HOTKEY_NAME="$2"; shift 2 ;;
    -u|--netuid)
      need_value "$1" "$#"; NETUID="$2"; shift 2 ;;
    -a|--axon-port)
      need_value "$1" "$#"; AXON_PORT="$2"; shift 2 ;;
    -e|--endpoint)
      need_value "$1" "$#"; ENDPOINT="$2"; shift 2 ;;
    -p)
      need_value "$1" "$#"; EXTRA+=(-p "$2"); shift 2 ;;
    -i|--pm2-name)
      need_value "$1" "$#"; PM2_NAME="$2"; shift 2 ;;
    --log)
      LOG_ENABLED=1; shift ;;
    --every)
      need_value "$1" "$#"; LOG_EVERY_N="$2"; shift 2 ;;
    --book)
      need_value "$1" "$#"; LOG_BOOK="$2"; shift 2 ;;
    --log-dir)
      need_value "$1" "$#"; LOG_DIR="$2"; shift 2 ;;
    --no-console)
      LOG_CONSOLE=0; shift ;;
    --no-jsonl)
      LOG_JSONL=0; shift ;;
    --help)
      usage; exit 0 ;;
    --)
      shift; EXTRA+=("$@"); break ;;
    *)
      echo "ERROR: unknown option: $1" >&2
      usage >&2
      exit 2 ;;
  esac
done

[[ -f "$REPO_ROOT/run_miner_multi.sh" ]] || {
  echo "ERROR: run_miner_multi.sh missing: $REPO_ROOT/run_miner_multi.sh" >&2
  exit 1
}
[[ -f "$AGENT_PATH/BaseStrategy.py" ]] || {
  echo "ERROR: BaseStrategy.py missing: $AGENT_PATH/BaseStrategy.py" >&2
  exit 1
}
if ! grep -q 'DEPLOY_POLICY_VERSION = "base_v4_13_9_champion"' "$AGENT_PATH/BaseStrategy.py"; then
  echo "ERROR: AdaptiveAgent V4.13.9 requires live BaseStrategy V4.13.9 champion." >&2
  echo 'Expected DEPLOY_POLICY_VERSION = "base_v4_13_9_champion" in BaseStrategy.py' >&2
  exit 1
fi
if ! grep -q 'BASE_CHAMPION_PARENT = "simplified_kappa_productivity_v4_13_9"' "$AGENT_PATH/BaseStrategy.py"; then
  echo "ERROR: AdaptiveAgent requires BaseStrategy promoted from frozen Research V4.13.9." >&2
  exit 1
fi
[[ -f "$AGENT_PATH/AdaptiveAgent.py" ]] || {
  echo "ERROR: AdaptiveAgent.py missing: $AGENT_PATH/AdaptiveAgent.py" >&2
  exit 1
}
if ! grep -q 'ADAPTIVE_VERSION = "adaptive_v4_13_9_realtime"' "$AGENT_PATH/AdaptiveAgent.py"; then
  echo "ERROR: AdaptiveAgent.py is not adaptive_v4_13_9_realtime." >&2
  echo "Deploy the V4.13.9 realtime AdaptiveAgent.py before starting this launcher." >&2
  exit 1
fi

export ADAPTIVE_STATE_DIR="${ADAPTIVE_STATE_DIR:-$REPO_ROOT/adaptive_state/m3}"
mkdir -p "$ADAPTIVE_STATE_DIR"

if [[ -z "${ADAPTIVE_ENVIRONMENT_KEY:-}" ]]; then
  if [[ "$ENDPOINT" == *test* ]]; then
    export ADAPTIVE_ENVIRONMENT_KEY="testnet_${NETUID}_m3"
  else
    export ADAPTIVE_ENVIRONMENT_KEY="net_${NETUID}_m3"
  fi
fi

if (( LOG_ENABLED == 1 )); then
  export STRATEGY1_DEBUG=1
  export STRATEGY1_DEBUG_JSONL=0
  export STRATEGY1_DEBUG_EVERY_N="$LOG_EVERY_N"
  export STRATEGY1_DEBUG_BOOK="$LOG_BOOK"
  export STRATEGY1_RESEARCH=1
  export STRATEGY1_RESEARCH_EVERY_N="$LOG_EVERY_N"
  export STRATEGY1_RESEARCH_BOOK="$LOG_BOOK"
  export STRATEGY1_RESEARCH_JSONL="$LOG_JSONL"
  export STRATEGY1_RESEARCH_CONSOLE="$LOG_CONSOLE"
  export STRATEGY1_RESEARCH_QUEUE="$LOG_QUEUE"
  export STRATEGY1_RESEARCH_DIR="$LOG_DIR"
  mkdir -p "$LOG_DIR"
else
  export STRATEGY1_DEBUG=0
  export STRATEGY1_DEBUG_JSONL=0
  export STRATEGY1_RESEARCH=0
  export STRATEGY1_RESEARCH_JSONL=0
  export STRATEGY1_RESEARCH_CONSOLE=0
fi

# BaseStrategy V4.13.9 policy remains frozen. Adaptive parameters are bounded
# execution-calibration overlays, not replacements for risk invariants.
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
debug_enabled=${LOG_ENABLED} debug_every_n=${LOG_EVERY_N} debug_jsonl=0 debug_book_id=${LOG_BOOK} \
research_enabled=${LOG_ENABLED} research_every_n=${LOG_EVERY_N} research_book_id=${LOG_BOOK} \
research_jsonl=${LOG_JSONL} research_console=${LOG_CONSOLE} research_compact_console=1 research_queue_size=${LOG_QUEUE} \
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
research_enable_score_ev=1 research_completion_ev_cache_ticks=20 research_density_priority_enabled=1 research_density_priority_min_candidates=1 research_enable_score_velocity=1 research_score_velocity_weight=0.08 research_enable_quote_hysteresis=1 \
research_enable_adaptive_ttl=1 research_enable_dust_escape=0 research_ttl_min_ms=250 research_ttl_max_ms=1500 research_quiet_ttl_ms=1000 research_quiet_exit_ttl_ms=950 research_one_away_exit_ttl_ms=975 \
research_enable_fast_candidate_screen=1 research_candidate_count=10 research_cohort_size=8 research_cohort_exploration_slots=1 research_max_open_books=6 research_max_active_open_books=6 research_max_total_open_books=12 research_max_parked_open_books=6 research_max_total_abs_base=3.0 research_kappa_conversion_pressure_enabled=1 research_kappa_conversion_reserve_slots=3 research_kappa_exploration_slots=1 research_kappa_flywheel_enabled=1 research_kappa_productivity_enabled=1 research_core_probe_enabled=1 research_persistent_maker_enabled=1 research_hysteresis_min_price_ticks=3 research_post_only_safety_ticks=2 \
research_enable_lane_scheduler=1 research_enable_aggressive_coverage=1 research_coverage_slots=3 research_completion_slots=5 research_realization_slots=3 research_shared_overflow_slots=1 \
research_lifecycle_taker_exit_prob=0.30 research_lifecycle_slippage_bps=0.75 research_lifecycle_holding_bps=0.50 \
research_positive_ev_min_order_override=0 research_positive_ev_min_safe_fraction=0.35 research_positive_ev_min_exit_fraction=0.45 research_positive_ev_min_trading_ev=0.05 \
research_one_away_exact_min_enabled=1 research_one_away_exact_min_ev_bps=0.0 research_one_away_exact_min_safe_fraction=0.15 research_one_away_exact_min_exit_fraction=0.20 \
research_two_away_exact_min_enabled=1 research_two_away_exact_min_ev=0.0 research_two_away_exact_min_max_inventory_risk=0.35 research_two_away_exact_min_exit_fraction=0.20 research_two_away_exact_min_min_headroom=0.25 \
research_qualified_core_exact_min_enabled=1 research_qualified_core_exact_min_ev=0.0 research_qualified_core_exact_min_max_inventory_risk=0.35 research_qualified_core_exact_min_exit_fraction=0.20 research_qualified_core_exact_min_min_headroom=0.25 research_qualified_core_stale_ttl_enabled=1 research_qualified_core_stale_ttl_ms=250  research_profitable_exit_persistence_enabled=1 research_profitable_exit_ttl_ms=3000 research_profitable_exit_min_net_bps=0.0 research_profitable_exit_reprice_ticks=3 \
research_quote_tighten_mult=0.85 research_quote_width_floor_mult=0.80 research_enable_one_away_quiet_tightening=1 research_one_away_quiet_width_mult=0.60 research_one_away_quiet_min_ev=0.0 research_one_away_max_touch_bps=1.5 research_one_away_stale_ttl_ms=250 research_fill_distance_decay_bps=6.0 research_fill_distance_near_boost=1.35 research_fill_distance_floor_mult=0.10 research_fill_fallback_policy_weight=0.45 research_local_kappa_refresh_ticks=10 research_score_qualified_pnl_floor=0.0 research_score_qualified_kappa_floor=0.0 research_p95_target_ms=120 \
research_enable_inventory_state_v2=1 research_enable_exit_urgency_v2=1 \
research_enable_hybrid_realization_v2=1 research_enable_economic_taker=1 \
research_enable_precise_reduction_qty=1 research_enable_dust_economic_gate=1 \
research_enable_authoritative_kappa_state=1 research_enable_markout_v2=1 \
research_inventory_liveness_enabled=1 research_fresh_maker_grace_enabled=1 research_fresh_maker_grace_ticks=3 research_positive_maker_veto_enabled=1 research_positive_maker_veto_floor_bps=1.0 research_positive_maker_veto_max_failed_exits=3 research_liveness_maker_failed_exits=3 research_liveness_maker_min_age_ticks=8 research_liveness_maker_floor_bps=-4.0 research_liveness_taker_failed_exits=8 research_liveness_taker_min_age_ticks=16 research_liveness_hard_failed_exits=12 research_liveness_hard_min_age_ticks=24 research_protected_park_failed_exits=4 research_protected_park_min_age_ticks=8 research_liveness_soft_taker_floor_bps=-8.0 research_liveness_hard_taker_floor_bps=-12.0 research_liveness_min_ev_advantage_bps=0.50 research_liveness_adverse_markout_bps=1.0 research_liveness_adverse_risk_floor=0.25 research_parked_refresh_ticks=25 research_parked_touch_move_bps=8.0 research_enable_fill_hazard_exit_compare=1 research_enable_sn79_action_utility=1 research_enable_score_taker_direct=1 research_enable_economic_taker_direct=1 research_economic_direct_max_loss_bps=0.0 research_enable_aggressive_positive_ev_taker=1 research_aggressive_positive_ev_min_net_bps=0.0 research_aggressive_positive_ev_switch_margin_bps=0.50 research_aggressive_positive_ev_one_away_margin_bps=0.0 research_aggressive_positive_ev_failed_exit_count=8 research_aggressive_positive_ev_min_age_ticks=16 research_aggressive_positive_ev_max_maker_fill=0.08 research_aggressive_positive_ev_min_urgency=0.30 research_maker_escalate_failed_exit_count=8 research_one_away_maker_escalate_failed_exit_count=3 research_enable_risk_taker_direct=0 research_risk_direct_max_loss_bps=-10.0 research_risk_direct_min_age_ticks=24 research_risk_direct_failed_exit_count=3 research_risk_direct_min_ev_advantage_bps=1.0 research_failed_exit_penalty_bps=0.75 research_exit_age_penalty_bps_per_tick=0.03 research_cancel_before_taker=1 research_sn79_pnl_scale_bps=8.0 research_sn79_pnl_weight=1.0 research_sn79_round_trip_weight=0.30 research_sn79_kappa_weight=0.35 research_sn79_coverage_weight=0.15 research_sn79_capital_release_weight=0.15 research_sn79_risk_reduction_weight=0.20 research_sn79_velocity_weight=0.25 research_sn79_downside_weight=0.45 research_sn79_min_utility_margin=0.03 research_sn79_max_score_subsidy_loss_bps=0.0 research_sn79_one_away_loss_floor_bps=0.0 research_sn79_two_away_loss_floor_bps=0.0 research_sn79_uncovered_loss_floor_bps=0.0 research_allow_score_loss_subsidy=0 research_kappa_lookback_ns=10800000000000 research_kappa_expiry_warning_frac=0.20 research_kappa_expiry_rank_bonus=0.20 research_suppress_qualified_acquisition=1 research_qualified_suppression_min_incomplete=1 research_deadline_scheduler_enabled=1 research_deadline_critical_urgency=0.50 research_deadline_rank_bonus=0.25 research_score_target_books=88 research_stale_maker_rescue_enabled=1 research_stale_maker_rescue_failed_exits=4 research_stale_maker_rescue_critical_failed_exits=1 research_stale_maker_rescue_floor_bps=-1.0 research_entry_recheck_ticks=12 research_hybrid_partial_frac_cap=0.90 research_ladder_passive_max=0.15 research_ladder_competitive_max=0.30 research_ladder_aggressive_max=0.45 research_enable_unified_exit=1 research_unified_maker_net_floor_bps=0.0 research_unified_stale_bridge_roundtrip_floor_bps=-12.0 research_unified_profit_lock_min_bps=1.0 research_unified_profit_lock_drawdown_bps=2.0 research_unified_switch_margin_bps=0.50 research_enable_protective_taker=1 research_protective_taker_loss_floor_bps=-2.0 research_protective_taker_ev_advantage_bps=1.0 research_protective_taker_failed_exits=6 research_protective_taker_min_age_ticks=8 research_protective_taker_adverse_bps=2.0 research_early_escape_enabled=1 research_early_escape_failed_exits=3 research_early_escape_min_age_ticks=5 research_early_escape_drawdown_bps=1.5 research_early_escape_floor_headroom_bps=0.75 research_early_escape_ev_advantage_bps=0.50 research_session_save_every_n=100 \
adaptive_enabled=1 adaptive_environment_key=${ADAPTIVE_ENVIRONMENT_KEY} adaptive_persistence_enabled=1 adaptive_save_every_n=100 \
adaptive_observe_requests=100 adaptive_normal_after_requests=400 adaptive_fill_min_samples=6 adaptive_fill_full_confidence_samples=24 adaptive_fill_prior_strength=8 \
adaptive_bootstrap_fill_blend=0.25 adaptive_normal_fill_blend=0.55 adaptive_drift_fill_blend=0.20 adaptive_fill_max_delta=0.12 adaptive_fill_overlay_enabled=0 \
adaptive_max_widen=0.15 adaptive_max_tighten=0.05 adaptive_max_size_cut=0.30 adaptive_max_exit_boost=0.15 adaptive_min_side_scale=0.55 adaptive_pnl_scale=0.03 adaptive_target_maker_fill=0.20 adaptive_rank_max_adjust=0.05 \
adaptive_drift_window_requests=100 adaptive_drift_start_requests=100 adaptive_drift_min_quotes=20 adaptive_drift_min_windows=2 adaptive_drift_min_samples=30 adaptive_drift_min_window_samples=15 adaptive_drift_min_signals=1 adaptive_drift_hold_requests=200 adaptive_drift_recovery_requests=200 \
adaptive_dust_enabled=1 adaptive_dust_cooldown_ticks=100 adaptive_dust_max_cooldown_ticks=600 adaptive_dust_prior_fill=0.02 adaptive_dust_prior_strength=20 adaptive_telemetry_every_n=25 \
adaptive_hjb_shadow_enabled=1 adaptive_hjb_overlay_enabled=1 adaptive_hjb_policy_enabled=0"

echo "[AdaptiveAgent] wallet=$WALLET_NAME"
echo "[AdaptiveAgent] hotkey=$HOTKEY_NAME"
echo "[AdaptiveAgent] netuid=$NETUID"
echo "[AdaptiveAgent] axon_port=$AXON_PORT"
echo "[AdaptiveAgent] endpoint=$ENDPOINT"
echo "[AdaptiveAgent] environment=$ADAPTIVE_ENVIRONMENT_KEY"
echo "[AdaptiveAgent] detailed_log=$LOG_ENABLED"
echo "[AdaptiveAgent] pm2_name=$PM2_NAME"
echo "[AdaptiveAgent] log_dir=$LOG_DIR"
echo "[AdaptiveAgent] state_dir=$ADAPTIVE_STATE_DIR"
echo "[AdaptiveAgent] version=adaptive_v4_13_9_realtime"
echo "[AdaptiveAgent] base_policy=base_v4_13_9_champion"

if [[ "${ADAPTIVE_PREFLIGHT_ONLY:-0}" == "1" ]]; then
  grep -q 'research_profitable_exit_persistence_enabled=1' <<<"$PARAMS" || { echo "ERROR: missing V4.13.8 exit persistence" >&2; exit 1; }
  grep -q 'research_density_priority_enabled=1' <<<"$PARAMS" || { echo "ERROR: missing V4.13.6 density policy" >&2; exit 1; }
  grep -q 'research_qualified_core_exact_min_enabled=1' <<<"$PARAMS" || { echo "ERROR: missing V4.13.7 CORE recycle" >&2; exit 1; }
  grep -q 'adaptive_observe_requests=100' <<<"$PARAMS" || { echo "ERROR: realtime adaptive phase config missing" >&2; exit 1; }
  echo "AdaptiveAgent V4.13.9 realtime launcher preflight PASS"
  exit 0
fi

exec "$REPO_ROOT/run_miner_multi.sh" \
  -i "$PM2_NAME" \
  -e "$ENDPOINT" \
  -w "$WALLET_NAME" \
  -h "$HOTKEY_NAME" \
  -u "$NETUID" \
  -a "$AXON_PORT" \
  -g "$AGENT_PATH" \
  -n AdaptiveAgent \
  -m "$PARAMS" \
  "${EXTRA[@]}"
