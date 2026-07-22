#!/bin/bash
# Launch Strategy4 (constrained Alpha-AS/GLFT + L3 MM) via run_miner.sh.
#
# Usage:
#   ./agents/strategy/run_strategy4.sh -w <coldkey> -h <hotkey> -u 79 -a 8091
#   ./agents/strategy/run_strategy4.sh -w taos -h miner
#
# Keep alpha_policy_mode=deterministic until multi-seed races look stable,
# then optionally switch to ucb (see Strategy4_GUIDE.md).

set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
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

PARAMS="enable_mm_strategy=1 lazy_load=1 alpha_policy_mode=deterministic \
enable_separate_alpha=0 allow_legacy_auto_tuning=0 \
enable_floor_awareness=1 score_floor_guard_ratio=1.05 \
weak_book_score_quantile=0.35 weak_book_size_mult=0.50 \
mm_base_size=0.20 max_inventory_base=1.20 inventory_close_threshold=0.25 \
max_mm_books_per_tick=4 max_managed_books_per_tick=4 \
cautious_inventory_util=0.45 reduce_only_inventory_util=0.72 \
liquidate_inventory_util=0.98 hard_stop_loss_bps=55 \
min_side_edge_bps=0.08 fee_buffer_bps=0.20 \
base_risk_aversion=0.85 alpha_shift_spreads=0.32 inventory_shift_spreads=0.55 \
markout_horizon_ns=2000000000 \
verbose_log=0 log_every_n=100"

exec ./run_miner.sh \
  -w "$WALLET_NAME" \
  -h "$HOTKEY_NAME" \
  -u "$NETUID" \
  -a "$AXON_PORT" \
  -g "$AGENT_PATH" \
  -n Strategy4 \
  -m "$PARAMS" \
  "${EXTRA[@]}"
