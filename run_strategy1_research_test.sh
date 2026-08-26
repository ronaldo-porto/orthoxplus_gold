#!/usr/bin/env bash
# SN79 testnet launcher for Strategy1_Research V4.12.1 Execution Conversion Research.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

WALLET_NAME="${WALLET_NAME:-taos}"
HOTKEY_NAME="${HOTKEY_NAME:-miner}"
ENDPOINT="${ENDPOINT:-wss://test.finney.opentensor.ai:443}"
NETUID="${NETUID:-366}"
AXON_PORT="${AXON_PORT:-8090}"
AGENT_PATH="${AGENT_PATH:-$REPO_ROOT/agents/strategy}"
RESEARCH_EVERY_N="${RESEARCH_EVERY_N:-10}"
RESEARCH_BOOK="${RESEARCH_BOOK:--1}"
RESEARCH_JSONL="${RESEARCH_JSONL:-1}"
RESEARCH_CONSOLE="${RESEARCH_CONSOLE:-1}"
RESEARCH_QUEUE="${RESEARCH_QUEUE:-65536}"
RESEARCH_DIR="${RESEARCH_DIR:-$REPO_ROOT/logs/strategy1_research}"

EXTRA=()
while getopts "w:h:u:a:e:p:" flag; do
  case "$flag" in
    w) WALLET_NAME="$OPTARG" ;;
    h) HOTKEY_NAME="$OPTARG" ;;
    u) NETUID="$OPTARG" ;;
    a) AXON_PORT="$OPTARG" ;;
    e) ENDPOINT="$OPTARG" ;;
    p) EXTRA+=(-p "$OPTARG") ;;
    *) exit 2 ;;
  esac
done

[[ -f "$REPO_ROOT/run_miner.sh" ]] || { echo "run_miner.sh missing" >&2; exit 1; }
[[ -f "$AGENT_PATH/Strategy1_Research.py" ]] || { echo "Strategy1_Research.py missing" >&2; exit 1; }
grep -q 'RESEARCH_POLICY_VERSION = "simple_performance_core_v4_12_1"' "$AGENT_PATH/Strategy1_Research.py" || { echo "ERROR: Strategy1_Research.py is not simple_performance_core_v4_12_1" >&2; exit 1; }
[[ -f "$AGENT_PATH/Strategy1_Debug.py" ]] || { echo "Strategy1_Debug.py missing" >&2; exit 1; }

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

# V4.1 Strict preserves V4 trade quality while isolating normal MM and Kappa-completion scheduler budgets.
# Full JSONL is preserved; console is sampled by RESEARCH_EVERY_N.
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
research_one_away_exact_min_enabled=1 research_one_away_exact_min_ev_bps=0.0 research_one_away_exact_min_safe_fraction=0.50 research_one_away_exact_min_exit_fraction=0.90 \
research_quote_tighten_mult=0.85 research_quote_width_floor_mult=0.80 research_enable_one_away_quiet_tightening=1 research_one_away_quiet_width_mult=0.60 research_one_away_quiet_min_ev=0.0 research_local_kappa_refresh_ticks=10 research_score_qualified_pnl_floor=0.0 research_score_qualified_kappa_floor=0.0 research_p95_target_ms=120 \
research_enable_inventory_state_v2=1 research_enable_exit_urgency_v2=1 \
research_enable_hybrid_realization_v2=1 research_enable_economic_taker=1 \
research_enable_precise_reduction_qty=1 research_enable_dust_economic_gate=1 \
research_enable_authoritative_kappa_state=1 research_enable_markout_v2=1 \
research_enable_fill_hazard_exit_compare=1 research_enable_sn79_action_utility=1 research_enable_score_taker_direct=1 research_enable_economic_taker_direct=1 research_economic_direct_max_loss_bps=0.0 research_enable_aggressive_positive_ev_taker=1 research_aggressive_positive_ev_min_net_bps=0.0 research_aggressive_positive_ev_switch_margin_bps=0.50 research_aggressive_positive_ev_one_away_margin_bps=0.0 research_aggressive_positive_ev_failed_exit_count=8 research_aggressive_positive_ev_min_age_ticks=16 research_aggressive_positive_ev_max_maker_fill=0.05 research_aggressive_positive_ev_min_urgency=0.30 research_maker_escalate_failed_exit_count=8 research_one_away_maker_escalate_failed_exit_count=3 research_enable_risk_taker_direct=0 research_risk_direct_max_loss_bps=-10.0 research_risk_direct_min_age_ticks=24 research_risk_direct_failed_exit_count=3 research_risk_direct_min_ev_advantage_bps=1.0 research_failed_exit_penalty_bps=0.75 research_exit_age_penalty_bps_per_tick=0.03 research_cancel_before_taker=1 research_sn79_pnl_scale_bps=8.0 research_sn79_pnl_weight=1.0 research_sn79_round_trip_weight=0.30 research_sn79_kappa_weight=0.35 research_sn79_coverage_weight=0.15 research_sn79_capital_release_weight=0.15 research_sn79_risk_reduction_weight=0.20 research_sn79_velocity_weight=0.25 research_sn79_downside_weight=0.45 research_sn79_min_utility_margin=0.03 research_sn79_max_score_subsidy_loss_bps=0.0 research_sn79_one_away_loss_floor_bps=0.0 research_sn79_two_away_loss_floor_bps=0.0 research_sn79_uncovered_loss_floor_bps=0.0 research_allow_score_loss_subsidy=0 research_kappa_lookback_ns=10800000000000 research_kappa_expiry_warning_frac=0.20 research_kappa_expiry_rank_bonus=0.20 research_entry_recheck_ticks=12 research_hybrid_partial_frac_cap=0.90 research_ladder_passive_max=0.15 research_ladder_competitive_max=0.30 research_ladder_aggressive_max=0.45 research_session_save_every_n=100"

exec "$REPO_ROOT/run_miner.sh" \
  -e "$ENDPOINT" -w "$WALLET_NAME" -h "$HOTKEY_NAME" -u "$NETUID" -a "$AXON_PORT" \
  -g "$AGENT_PATH" -n Strategy1_Research -m "$PARAMS" "${EXTRA[@]}"
