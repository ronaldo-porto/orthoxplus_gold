#!/usr/bin/env bash
# SN79 launcher for AdaptiveAgent(BaseStrategy).
#
# Normal:
#   ./run_adaptive_agent.sh -w sw_ck_st4_m1 -h sw_hk_st4_m1 -u 366 -a 8092
#
# Detailed BaseStrategy + Adaptive telemetry:
#   ./run_adaptive_agent.sh -w sw_ck_st4_m1 -h sw_hk_st4_m1 -u 366 -a 8092 --log

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

WALLET_NAME="${WALLET_NAME:-taos}"
HOTKEY_NAME="${HOTKEY_NAME:-miner}"
ENDPOINT="${ENDPOINT:-wss://test.finney.opentensor.ai:443}"
NETUID="${NETUID:-366}"
AXON_PORT="${AXON_PORT:-8090}"
AGENT_PATH="${AGENT_PATH:-$REPO_ROOT/agents/strategy}"

LOG_ENABLED=0
LOG_EVERY_N="${LOG_EVERY_N:-10}"
LOG_BOOK="${LOG_BOOK:--1}"
LOG_JSONL="${LOG_JSONL:-1}"
LOG_CONSOLE="${LOG_CONSOLE:-1}"
LOG_QUEUE="${LOG_QUEUE:-65536}"
LOG_DIR="${LOG_DIR:-$REPO_ROOT/logs/adaptive_agent}"

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

  --log                   enable detailed BaseStrategy V4.1 telemetry
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

[[ -f "$REPO_ROOT/run_miner.sh" ]] || {
  echo "ERROR: run_miner.sh missing: $REPO_ROOT/run_miner.sh" >&2
  exit 1
}
[[ -f "$AGENT_PATH/BaseStrategy.py" ]] || {
  echo "ERROR: BaseStrategy.py missing: $AGENT_PATH/BaseStrategy.py" >&2
  exit 1
}
[[ -f "$AGENT_PATH/AdaptiveAgent.py" ]] || {
  echo "ERROR: AdaptiveAgent.py missing: $AGENT_PATH/AdaptiveAgent.py" >&2
  exit 1
}

if [[ -z "${ADAPTIVE_ENVIRONMENT_KEY:-}" ]]; then
  if [[ "$ENDPOINT" == *test* ]]; then
    export ADAPTIVE_ENVIRONMENT_KEY="testnet_${NETUID}"
  else
    export ADAPTIVE_ENVIRONMENT_KEY="net_${NETUID}"
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

# BaseStrategy V4.1 policy remains frozen. Adaptive parameters are bounded
# execution-calibration overlays, not replacements for risk invariants.
PARAMS="enable_mm_strategy=1 lazy_load=1 \
fast_update=1 sync_event_csv=0 history_len=0 log_latency=0 \
mm_base_size=0.25 max_inventory_base=1.20 inventory_close_threshold=0.25 \
max_mm_books_per_tick=4 max_managed_books_per_tick=8 \
min_expected_alpha=0.18 min_expected_realized_pnl=0.0 \
mm_expiry_period_ns=500000000 maintenance_size_mult=0.25 \
passive_exit_only=1 aggressive_close_min_ticks=300 position_max_ticks=300 \
mm_skip_inactive_tier=1 toxic_loss_streak=4 \
verbose_log=0 log_every_n=100 log_mm_strategy=0 log_direction=0 log_book_profile=0 \
log_regime=0 log_momentum_pnl=0 log_book_memory=0 \
debug_enabled=${LOG_ENABLED} debug_every_n=${LOG_EVERY_N} debug_jsonl=0 debug_book_id=${LOG_BOOK} \
research_enabled=${LOG_ENABLED} research_every_n=${LOG_EVERY_N} research_book_id=${LOG_BOOK} \
research_jsonl=${LOG_JSONL} research_console=${LOG_CONSOLE} research_queue_size=${LOG_QUEUE} \
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
research_kappa_completion_relaxed_success_cap=2 research_kappa_completion_recent_pnl_floor=-0.01 \
adaptive_enabled=1 adaptive_environment_key=${ADAPTIVE_ENVIRONMENT_KEY} \
adaptive_persistence_enabled=1 adaptive_save_every_n=250 \
adaptive_observe_requests=1000 adaptive_normal_after_requests=3000 \
adaptive_fill_min_samples=8 adaptive_fill_full_confidence_samples=40 \
adaptive_fill_prior_strength=8 adaptive_bootstrap_fill_blend=0.25 \
adaptive_normal_fill_blend=0.60 adaptive_drift_fill_blend=0.20 \
adaptive_fill_max_delta=0.15 adaptive_max_widen=0.18 adaptive_max_tighten=0.06 \
adaptive_max_size_cut=0.35 adaptive_pnl_scale=0.03 adaptive_target_maker_fill=0.20 \
adaptive_rank_max_adjust=0.06 adaptive_drift_window_requests=250 \
adaptive_drift_min_quotes=30 adaptive_drift_threshold=0.12 adaptive_drift_hold_requests=500"

echo "[AdaptiveAgent] wallet=$WALLET_NAME"
echo "[AdaptiveAgent] hotkey=$HOTKEY_NAME"
echo "[AdaptiveAgent] netuid=$NETUID"
echo "[AdaptiveAgent] axon_port=$AXON_PORT"
echo "[AdaptiveAgent] endpoint=$ENDPOINT"
echo "[AdaptiveAgent] environment=$ADAPTIVE_ENVIRONMENT_KEY"
echo "[AdaptiveAgent] detailed_log=$LOG_ENABLED"

exec "$REPO_ROOT/run_miner.sh" \
  -e "$ENDPOINT" \
  -w "$WALLET_NAME" \
  -h "$HOTKEY_NAME" \
  -u "$NETUID" \
  -a "$AXON_PORT" \
  -g "$AGENT_PATH" \
  -n AdaptiveAgent \
  -m "$PARAMS" \
  "${EXTRA[@]}"
