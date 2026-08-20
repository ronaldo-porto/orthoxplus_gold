#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# BaseStrategy deploy launcher.
#
# Default:
#   bash run_base_strategy.sh
#
# Enable V4.1 detailed telemetry:
#   bash run_base_strategy.sh --log
#
# This launcher does NOT tune the V4.1 policy.  It preserves the exact fixed
# economics/structural parameters and only controls observability.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="${SN79_ROOT:-$SCRIPT_DIR}"

# Support placing this script either at repo root or in agents/strategy/.
if [[ -f "$REPO_ROOT/run_miner.sh" ]]; then
  :
elif [[ -f "$SCRIPT_DIR/../../run_miner.sh" ]]; then
  REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
elif [[ -f "$SCRIPT_DIR/../../../run_miner.sh" ]]; then
  REPO_ROOT="$(cd -- "$SCRIPT_DIR/../../.." && pwd -P)"
fi

AGENT_PATH="${AGENT_PATH:-$REPO_ROOT/agents/strategy}"
BUILDER="$AGENT_PATH/build_base_strategy.py"
PUBLIC_AGENT="$AGENT_PATH/BaseStrategy.py"
FLAT_AGENT="$AGENT_PATH/_BaseStrategy_flat.py"

WALLET_NAME="${WALLET_NAME:-taos}"
HOTKEY_NAME="${HOTKEY_NAME:-miner}"
ENDPOINT="${ENDPOINT:-wss://test.finney.opentensor.ai:443}"
NETUID="${NETUID:-366}"
AXON_PORT="${AXON_PORT:-8090}"

LOG_ENABLED=0
LOG_EVERY_N="${LOG_EVERY_N:-10}"
LOG_BOOK="${LOG_BOOK:--1}"
LOG_JSONL="${LOG_JSONL:-1}"
LOG_CONSOLE="${LOG_CONSOLE:-1}"
LOG_QUEUE="${LOG_QUEUE:-65536}"
LOG_DIR="${LOG_DIR:-$REPO_ROOT/logs/base_strategy}"
FORCE_REBUILD=0
EXTRA=()

usage() {
  cat <<'EOF'
Usage: bash run_base_strategy.sh [options] [-- extra miner args]

Options:
  --log                 Enable V4.1 detailed [S1R_*] telemetry.
  --every N             Detailed log sample cadence (default 10).
  --book ID             Detailed log one book only; -1 means all (default -1).
  --log-dir PATH        JSONL output directory.
  --no-console          JSONL only when --log is enabled.
  --no-jsonl            Console only when --log is enabled.
  --rebuild             Force regeneration of _BaseStrategy_flat.py.
  --wallet NAME         Wallet name (default: taos).
  --hotkey NAME         Hotkey name (default: miner).
  --endpoint URL        Subtensor endpoint.
  --netuid N            Netuid (default: 366).
  --axon-port N         Axon port (default: 8090).
  -h, --help            Show this help.

Environment equivalents:
  WALLET_NAME HOTKEY_NAME ENDPOINT NETUID AXON_PORT AGENT_PATH
  LOG_EVERY_N LOG_BOOK LOG_JSONL LOG_CONSOLE LOG_QUEUE LOG_DIR
EOF
}

while (($#)); do
  case "$1" in
    --log)
      LOG_ENABLED=1; shift ;;
    --every)
      [[ $# -ge 2 ]] || { echo "--every requires N" >&2; exit 2; }
      LOG_EVERY_N="$2"; shift 2 ;;
    --book)
      [[ $# -ge 2 ]] || { echo "--book requires ID" >&2; exit 2; }
      LOG_BOOK="$2"; shift 2 ;;
    --log-dir)
      [[ $# -ge 2 ]] || { echo "--log-dir requires PATH" >&2; exit 2; }
      LOG_DIR="$2"; shift 2 ;;
    --no-console)
      LOG_CONSOLE=0; shift ;;
    --no-jsonl)
      LOG_JSONL=0; shift ;;
    --rebuild)
      FORCE_REBUILD=1; shift ;;
    --wallet)
      WALLET_NAME="$2"; shift 2 ;;
    --hotkey)
      HOTKEY_NAME="$2"; shift 2 ;;
    --endpoint)
      ENDPOINT="$2"; shift 2 ;;
    --netuid)
      NETUID="$2"; shift 2 ;;
    --axon-port)
      AXON_PORT="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    --)
      shift
      EXTRA+=("$@")
      break ;;
    *)
      EXTRA+=("$1")
      shift ;;
  esac
done

[[ -f "$PUBLIC_AGENT" ]] || {
  echo "ERROR: missing $PUBLIC_AGENT" >&2
  exit 2
}
[[ -f "$BUILDER" ]] || {
  echo "ERROR: missing $BUILDER" >&2
  exit 2
}
[[ -f "$AGENT_PATH/Strategy1_Research_v4_1_strict.py" ]] || {
  echo "ERROR: missing V4.1 research reference in $AGENT_PATH" >&2
  exit 2
}

PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="${PYTHON_FALLBACK:-python3}"
fi

# Build only when needed. This happens before the miner starts and therefore
# never contributes to validator-measured response latency.
if (( FORCE_REBUILD == 1 )); then
  "$PYTHON" "$BUILDER" --strategy-dir "$AGENT_PATH" --output "$FLAT_AGENT"
else
  if ! "$PYTHON" "$BUILDER" \
      --strategy-dir "$AGENT_PATH" \
      --output "$FLAT_AGENT" \
      --check-current >/dev/null 2>&1; then
    "$PYTHON" "$BUILDER" --strategy-dir "$AGENT_PATH" --output "$FLAT_AGENT"
  fi
fi

# ------------------------------------------------------------
# Logging is a launcher concern.
# ------------------------------------------------------------
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

# ------------------------------------------------------------
# V4.1 fixed deploy policy.
# Do NOT tune here; AdaptiveAgent will own adaptive parameters later.
# ------------------------------------------------------------
PARAMS="enable_mm_strategy=1 enable_kappa_strategy=0 lazy_load=1 \
fast_update=1 sync_event_csv=0 history_len=0 \
mm_base_size=0.25 max_inventory_base=1.20 inventory_close_threshold=0.25 \
max_mm_books_per_tick=4 max_managed_books_per_tick=8 \
min_expected_alpha=0.18 min_expected_realized_pnl=0.0 \
mm_expiry_period_ns=500000000 maintenance_size_mult=0.25 \
passive_exit_only=1 aggressive_close_min_ticks=300 position_max_ticks=300 \
mm_skip_inactive_tier=1 toxic_loss_streak=4 enable_auto_tuning=0 allow_tuning_config=0 \
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
research_kappa_completion_relaxed_success_cap=2 research_kappa_completion_recent_pnl_floor=-0.01"

echo "[BaseStrategy] agent=$PUBLIC_AGENT"
echo "[BaseStrategy] flat=$FLAT_AGENT"
echo "[BaseStrategy] detailed_log=$LOG_ENABLED"
echo "[BaseStrategy] network=$ENDPOINT netuid=$NETUID"

exec "$REPO_ROOT/run_miner.sh" \
  -e "$ENDPOINT" \
  -w "$WALLET_NAME" \
  -h "$HOTKEY_NAME" \
  -u "$NETUID" \
  -a "$AXON_PORT" \
  -g "$AGENT_PATH" \
  -n BaseStrategy \
  -m "$PARAMS" \
  "${EXTRA[@]}"
