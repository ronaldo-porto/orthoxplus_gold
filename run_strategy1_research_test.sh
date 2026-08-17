#!/usr/bin/env bash
# SN79 testnet launcher for Strategy1_Research.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

WALLET_NAME="${WALLET_NAME:-taos}"
HOTKEY_NAME="${HOTKEY_NAME:-miner}"
ENDPOINT="${ENDPOINT:-wss://test.finney.opentensor.ai:443}"
NETUID="${NETUID:-366}"
AXON_PORT="${AXON_PORT:-8090}"
AGENT_PATH="${AGENT_PATH:-$REPO_ROOT/agents/strategy}"
RESEARCH_EVERY_N="${RESEARCH_EVERY_N:-1}"
RESEARCH_BOOK="${RESEARCH_BOOK:--1}"
RESEARCH_JSONL="${RESEARCH_JSONL:-1}"
RESEARCH_CONSOLE="${RESEARCH_CONSOLE:-1}"
RESEARCH_QUEUE="${RESEARCH_QUEUE:-8192}"
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

# Trading values intentionally match the Strategy1 test baseline.
PARAMS="enable_mm_strategy=1 enable_kappa_strategy=0 lazy_load=1 \
fast_update=1 sync_event_csv=0 history_len=0 \
mm_base_size=0.25 max_inventory_base=1.20 inventory_close_threshold=0.25 \
max_mm_books_per_tick=4 max_managed_books_per_tick=4 \
min_expected_alpha=0.18 min_expected_realized_pnl=0.0 \
mm_expiry_period_ns=500000000 maintenance_size_mult=0.25 \
passive_exit_only=1 aggressive_close_min_ticks=300 position_max_ticks=300 \
mm_skip_inactive_tier=1 toxic_loss_streak=4 enable_auto_tuning=0 allow_tuning_config=0 \
verbose_log=0 log_every_n=100 log_mm_strategy=0 log_direction=0 log_book_profile=0 \
log_regime=0 log_momentum_pnl=0 log_book_memory=0 \
debug_enabled=1 debug_every_n=${RESEARCH_EVERY_N} debug_jsonl=0 debug_book_id=${RESEARCH_BOOK} \
research_enabled=1 research_every_n=${RESEARCH_EVERY_N} research_book_id=${RESEARCH_BOOK} \
research_jsonl=${RESEARCH_JSONL} research_console=${RESEARCH_CONSOLE} research_queue_size=${RESEARCH_QUEUE}"

exec "$REPO_ROOT/run_miner.sh" \
  -e "$ENDPOINT" -w "$WALLET_NAME" -h "$HOTKEY_NAME" -u "$NETUID" -a "$AXON_PORT" \
  -g "$AGENT_PATH" -n Strategy1_Research -m "$PARAMS" "${EXTRA[@]}"
