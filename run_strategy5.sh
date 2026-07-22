#!/bin/bash
# Launch Strategy5 (floor-aware HJB/AS on Strategy3) via run_miner.sh.
#
# Usage:
#   chmod +x agents/strategy/run_strategy5.sh
#   ./agents/strategy/run_strategy5.sh -w <coldkey> -h <hotkey> -u 79 -a 8091

set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

WALLET_NAME="${WALLET_NAME:-taos}"
HOTKEY_NAME="${HOTKEY_NAME:-miner}"
NETUID="${NETUID:-79}"
AXON_PORT="${AXON_PORT:-8091}"
AGENT_PATH="${AGENT_PATH:-$REPO_ROOT/agents/strategy}"

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
mm_base_size=0.20 max_inventory_base=1.20 inventory_close_threshold=0.25 \
max_mm_books_per_tick=4 max_managed_books_per_tick=4 \
min_expected_alpha=0.18 min_expected_realized_pnl=0.0 \
enable_floor_awareness=1 floor_guard_ratio=1.05 \
hjb_floor_edge_boost=0.15 hjb_weak_book_size_mult=0.5 \
hjb_left_tail_quote_enabled=0 \
hjb_gamma=0.15 hjb_kappa=1.5 hjb_horizon=1.0 hjb_alpha_shift=0.28 \
hjb_vol_floor=0.0005 hjb_fallback_to_s3=1 \
verbose_log=0 log_every_n=100 log_mm_strategy=1"

exec ./run_miner.sh \
  -w "$WALLET_NAME" \
  -h "$HOTKEY_NAME" \
  -u "$NETUID" \
  -a "$AXON_PORT" \
  -g "$AGENT_PATH" \
  -n Strategy5 \
  -m "$PARAMS" \
  "${EXTRA[@]}"
