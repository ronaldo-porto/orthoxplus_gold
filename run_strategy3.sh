#!/bin/bash
# Launch Strategy3 (survival-first inventory-aware MM) via run_miner.sh.
#
# Usage:
#   ./agents/strategy/run_strategy3.sh -w <coldkey> -h <hotkey> -u 79 -a 8091
#   ./agents/strategy/run_strategy3.sh -w taos -h miner   # uses defaults below

set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

WALLET_NAME="${WALLET_NAME:-taos}"
HOTKEY_NAME="${HOTKEY_NAME:-miner}"
NETUID="${NETUID:-79}"
AXON_PORT="${AXON_PORT:-8091}"
AGENT_PATH="${AGENT_PATH:-$REPO_ROOT/agents/strategy}"

# Optional CLI passthrough: -w -h -u -a -e -p
EXTRA=()
while getopts w:h:u:a:e:p: flag; do
  case "${flag}" in
    w) WALLET_NAME=${OPTARG};;
    h) HOTKEY_NAME=${OPTARG};;
    u) NETUID=${OPTARG};;
    a) AXON_PORT=${OPTARG};;
    e) EXTRA+=(-e "${OPTARG}");;
    p) EXTRA+=(-p "${OPTARG}");;
  esac
done

PARAMS="enable_mm_strategy=1 lazy_load=1 enable_separate_alpha=0 \
mm_base_size=0.25 max_inventory_base=1.20 inventory_close_threshold=0.25 \
max_mm_books_per_tick=4 max_managed_books_per_tick=4 \
min_expected_alpha=0.18 min_expected_realized_pnl=0.0 \
enable_floor_awareness=1 score_floor_guard_ratio=1.05 \
weak_book_score_quantile=0.35 weak_book_size_mult=0.5 \
min_floor_expected_pnl=0.0001 \
verbose_log=0 log_every_n=100 log_mm_strategy=1"

exec ./run_miner.sh \
  -w "$WALLET_NAME" \
  -h "$HOTKEY_NAME" \
  -u "$NETUID" \
  -a "$AXON_PORT" \
  -g "$AGENT_PATH" \
  -n Strategy3 \
  -m "$PARAMS" \
  "${EXTRA[@]}"
