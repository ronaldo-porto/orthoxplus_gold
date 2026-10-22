#!/usr/bin/env bash
# Launch Strategy5 (floor-aware HJB/AS on Strategy3) via run_miner.sh.
#
# Ubuntu usage (from repo root, same directory as run_miner.sh):
#   chmod +x run_strategy5.sh run_miner.sh
#   ./run_strategy5.sh -w <coldkey> -h <hotkey> -u 79 -a 8091
#   ./run_strategy5.sh -w taos -h miner
#
# Recovery note: Strategy5 inherits Strategy3 phase mining (default ON).
# With score≈0, the FSM parks in OBSERVE/COOLDOWN and cancels all new quotes,
# so volume never restarts. This launcher disables phase mining and relaxes
# left-tail / EV gates until activity is back, then re-tighten for score climb.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

if [[ ! -f "$REPO_ROOT/run_miner.sh" ]]; then
  echo "error: run_miner.sh not found next to $0" >&2
  exit 1
fi
chmod +x "$REPO_ROOT/run_miner.sh" 2>/dev/null || true

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

# Strip accidental trailing commas/spaces in wallet names.
WALLET_NAME="${WALLET_NAME%,}"
WALLET_NAME="${WALLET_NAME%"${WALLET_NAME##*[![:space:]]}"}"
HOTKEY_NAME="${HOTKEY_NAME%,}"
HOTKEY_NAME="${HOTKEY_NAME%"${HOTKEY_NAME##*[![:space:]]}"}"

# Recovery / trade-restart defaults for UID stuck at volume=0:
# - enable_phase_mining=0  → force ACTIVE (no OBSERVE/COOLDOWN quote freeze)
# - left_tail quoting allowed so floor death-spiral can break
# - slightly softer EV / more books than stock S5 score-hold profile
PARAMS="enable_mm_strategy=1 lazy_load=1 enable_separate_alpha=0 \
enable_phase_mining=0 \
enable_floor_awareness=1 floor_guard_ratio=1.02 score_floor_guard_ratio=1.02 \
left_tail_new_risk_enabled=1 \
hjb_left_tail_quote_enabled=1 \
mm_base_size=0.22 max_inventory_base=1.30 inventory_close_threshold=0.28 \
max_mm_books_per_tick=6 max_managed_books_per_tick=6 \
min_expected_alpha=0.12 min_expected_realized_pnl=0.0 \
hjb_floor_edge_boost=0.10 hjb_weak_book_size_mult=0.65 \
hjb_gamma=0.15 hjb_kappa=1.5 hjb_horizon=1.0 hjb_alpha_shift=0.28 \
hjb_vol_floor=0.0005 hjb_fallback_to_s3=1 \
verbose_log=0 log_every_n=50 log_mm_strategy=1"

exec "$REPO_ROOT/run_miner.sh" \
  -w "$WALLET_NAME" \
  -h "$HOTKEY_NAME" \
  -u "$NETUID" \
  -a "$AXON_PORT" \
  -g "$AGENT_PATH" \
  -n Strategy5 \
  -m "$PARAMS" \
  "${EXTRA[@]}"
