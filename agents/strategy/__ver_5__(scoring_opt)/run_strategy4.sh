#!/bin/bash
# Launch Strategy4 from __ver_5__(scoring_opt) on Ubuntu via run_miner.sh / pm2.
#
# Usage (from repo root OR this directory):
#   chmod +x "agents/strategy/__ver_5__(scoring_opt)/run_strategy4.sh"
#   ./agents/strategy/__ver_5__(scoring_opt)/run_strategy4.sh -w <coldkey> -h <hotkey> -u 79 -a 8091
#
# Example:
#   ./agents/strategy/__ver_5__(scoring_opt)/run_strategy4.sh -w taos -h miner -u 79 -a 8091
#
# Keep alpha_policy_mode=deterministic until multi-seed races look stable,
# then optionally switch to ucb (see Strategy4_GUIDE.md).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# agents/strategy/__ver_5__(scoring_opt) -> repo root is 3 levels up
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PARENT_STRATEGY_DIR="$REPO_ROOT/agents/strategy"
cd "$REPO_ROOT"

if [[ ! -f "$REPO_ROOT/run_miner.sh" ]]; then
  echo "ERROR: run_miner.sh not found at $REPO_ROOT" >&2
  echo "Check that this script lives under agents/strategy/__ver_5__(scoring_opt)/" >&2
  exit 1
fi

if [[ ! -f "$SCRIPT_DIR/Strategy4.py" ]]; then
  echo "ERROR: Strategy4.py missing in $SCRIPT_DIR" >&2
  exit 1
fi

# Agent loader imports Strategy4 from --agent.path; Strategy4 also needs
# Strategy1 / DetailedTemplateAgent / rolling_scoring from the parent folder.
link_dep() {
  local name="$1"
  local src="$PARENT_STRATEGY_DIR/$name"
  local dst="$SCRIPT_DIR/$name"
  if [[ -e "$dst" || -L "$dst" ]]; then
    return 0
  fi
  if [[ ! -f "$src" ]]; then
    echo "ERROR: required dependency missing: $src" >&2
    exit 1
  fi
  ln -s "$src" "$dst"
  echo "Linked dependency: $name -> $src"
}

link_dep "Strategy1.py"
link_dep "DetailedTemplateAgent.py"
link_dep "rolling_scoring.py"

WALLET_NAME="${WALLET_NAME:-taos}"
HOTKEY_NAME="${HOTKEY_NAME:-miner}"
NETUID="${NETUID:-79}"
AXON_PORT="${AXON_PORT:-8091}"
# Always load THIS ver_5 folder (not agents/strategy parent).
AGENT_PATH="${AGENT_PATH:-$SCRIPT_DIR}"

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

# Trade-activity first: phase mining OFF, more books, softer EV/floor gates.
PARAMS="enable_mm_strategy=1 lazy_load=1 alpha_policy_mode=deterministic \
enable_separate_alpha=0 allow_legacy_auto_tuning=0 \
enable_phase_mining=0 enable_floor_awareness=1 score_floor_guard_ratio=1.02 \
weak_book_score_quantile=0.25 weak_book_size_mult=0.75 \
mm_base_size=0.25 max_inventory_base=1.40 inventory_close_threshold=0.30 \
max_mm_books_per_tick=8 max_managed_books_per_tick=8 \
min_expected_alpha=0.12 min_economic_signal=0.05 \
cautious_inventory_util=0.55 reduce_only_inventory_util=0.80 \
liquidate_inventory_util=0.98 hard_stop_loss_bps=55 \
min_side_edge_bps=0.03 fee_buffer_bps=0.10 \
base_risk_aversion=0.75 alpha_shift_spreads=0.28 inventory_shift_spreads=0.50 \
markout_horizon_ns=2000000000 \
verbose_log=0 log_every_n=100"

echo "Deploying ver_5 Strategy4"
echo "  REPO_ROOT=$REPO_ROOT"
echo "  AGENT_PATH=$AGENT_PATH"
echo "  wallet=$WALLET_NAME hotkey=$HOTKEY_NAME netuid=$NETUID axon=$AXON_PORT"

exec ./run_miner.sh \
  -w "$WALLET_NAME" \
  -h "$HOTKEY_NAME" \
  -u "$NETUID" \
  -a "$AXON_PORT" \
  -g "$AGENT_PATH" \
  -n Strategy4 \
  -m "$PARAMS" \
  "${EXTRA[@]}"
