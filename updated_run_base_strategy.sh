#!/usr/bin/env bash
# SN79 launcher for standalone BaseStrategy (Miner 2).
# Default PM2 name: sn79-m2 | Default Axon port: 8092
#
# Core CLI style:
#   ./run_base_strategy.sh -w sw_ck_st4_m2 -h sw_hk_st4_m2 -u 366 -a 8092
#
# Enable detailed V4.1 research logging:
#   ./run_base_strategy.sh -w sw_ck_st4_m2 -h sw_hk_st4_m2 -u 366 -a 8092 --log
#
# -w wallet/coldkey
# -h hotkey
# -u netuid
# -a axon port
# -e endpoint (optional)
# -p extra miner parameter (optional, repeatable)
# -i PM2 process name (optional; default sn79-m2)
# --log detailed research/debug telemetry

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

WALLET_NAME="${WALLET_NAME:-taos}"
HOTKEY_NAME="${HOTKEY_NAME:-miner}"
ENDPOINT="${ENDPOINT:-wss://test.finney.opentensor.ai:443}"
NETUID="${NETUID:-366}"
AXON_PORT="${AXON_PORT:-8092}"
AGENT_PATH="${AGENT_PATH:-$REPO_ROOT/agents/strategy}"
PM2_NAME="${PM2_NAME:-sn79-m2}"

LOG_ENABLED=0
RESEARCH_EVERY_N="${RESEARCH_EVERY_N:-10}"
RESEARCH_BOOK="${RESEARCH_BOOK:--1}"
RESEARCH_JSONL="${RESEARCH_JSONL:-1}"
RESEARCH_CONSOLE="${RESEARCH_CONSOLE:-1}"
RESEARCH_QUEUE="${RESEARCH_QUEUE:-65536}"
RESEARCH_DIR="${RESEARCH_DIR:-$REPO_ROOT/logs/m2_base_strategy}"

# Remove --log first, then parse the remaining short options with getopts.
FILTERED_ARGS=()
for arg in "$@"; do
  if [[ "$arg" == "--log" ]]; then
    LOG_ENABLED=1
  else
    FILTERED_ARGS+=("$arg")
  fi
done
set -- "${FILTERED_ARGS[@]}"

EXTRA=()
OPTIND=1
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

[[ -f "$REPO_ROOT/run_miner.sh" ]] || { echo "run_miner.sh missing" >&2; exit 1; }
[[ -f "$AGENT_PATH/BaseStrategy.py" ]] || { echo "BaseStrategy.py missing: $AGENT_PATH/BaseStrategy.py" >&2; exit 1; }

if (( LOG_ENABLED == 1 )); then
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
  DEBUG_ENABLED=1
  RESEARCH_ENABLED=1
else
  export STRATEGY1_DEBUG=0
  export STRATEGY1_DEBUG_JSONL=0
  export STRATEGY1_RESEARCH=0
  export STRATEGY1_RESEARCH_JSONL=0
  export STRATEGY1_RESEARCH_CONSOLE=0
  DEBUG_ENABLED=0
  RESEARCH_ENABLED=0
fi

PARAMS="enable_mm_strategy=1 lazy_load=1 \
fast_update=1 sync_event_csv=0 history_len=0 \
mm_base_size=0.25 max_inventory_base=1.20 inventory_close_threshold=0.25 \
max_mm_books_per_tick=4 max_managed_books_per_tick=8 \
min_expected_alpha=0.18 min_expected_realized_pnl=0.0 \
mm_expiry_period_ns=500000000 maintenance_size_mult=0.25 \
passive_exit_only=1 aggressive_close_min_ticks=300 position_max_ticks=300 \
mm_skip_inactive_tier=1 toxic_loss_streak=4 \
verbose_log=0 log_every_n=100 log_mm_strategy=0 log_direction=0 log_book_profile=0 \
log_regime=0 log_momentum_pnl=0 log_book_memory=0 \
debug_enabled=${DEBUG_ENABLED} debug_every_n=${RESEARCH_EVERY_N} debug_jsonl=0 debug_book_id=${RESEARCH_BOOK} \
research_enabled=${RESEARCH_ENABLED} research_every_n=${RESEARCH_EVERY_N} research_book_id=${RESEARCH_BOOK} \
research_jsonl=${RESEARCH_JSONL} research_console=${RESEARCH_CONSOLE} research_queue_size=${RESEARCH_QUEUE} \
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
research_kappa_completion_attempt_cap=4 research_kappa_completion_success_cap=2 \
research_kappa_completion_fill_mult=0.70 research_kappa_completion_fill_floor=0.10 \
research_kappa_completion_relaxed_success_cap=2 research_kappa_completion_recent_pnl_floor=-0.01"

echo "[BaseStrategy] wallet=$WALLET_NAME"
echo "[BaseStrategy] hotkey=$HOTKEY_NAME"
echo "[BaseStrategy] netuid=$NETUID"
echo "[BaseStrategy] axon_port=$AXON_PORT"
echo "[BaseStrategy] endpoint=$ENDPOINT"
echo "[BaseStrategy] detailed_log=$LOG_ENABLED"
echo "[BaseStrategy] pm2_name=$PM2_NAME"
echo "[BaseStrategy] log_dir=$RESEARCH_DIR"

exec "$REPO_ROOT/run_miner.sh" \
  -i "$PM2_NAME" \
  -e "$ENDPOINT" \
  -w "$WALLET_NAME" \
  -h "$HOTKEY_NAME" \
  -u "$NETUID" \
  -a "$AXON_PORT" \
  -g "$AGENT_PATH" \
  -n BaseStrategy \
  -m "$PARAMS" \
  "${EXTRA[@]}"
