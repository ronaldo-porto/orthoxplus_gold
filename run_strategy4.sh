#!/usr/bin/env bash
# Launch Strategy4 (constrained Alpha-AS/GLFT + L3 MM) via run_miner.sh.
#
# Ubuntu usage (from repo root, same directory as run_miner.sh):
#   chmod +x run_strategy4.sh run_miner.sh
#   ./run_strategy4.sh -w <coldkey> -h <hotkey> -u 79 -a 8091
#   ./run_strategy4.sh -w taos -h miner
#
# Keep alpha_policy_mode=deterministic until multi-seed races look stable,
# then optionally switch to ucb (see agents/strategy/Strategy4_GUIDE.md).
#
# Recovery note: UID with call_time ~0.7–0.9s + floor/left-tail gates can
# enter DISABLED on all flat books and never trade. Params below raise the
# latency disable threshold and temporarily relax floor left-tail blocks so
# quoting can restart. Re-tighten after volume/activity > 0.

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

# Strip accidental trailing commas/spaces in wallet names (common CLI typo).
WALLET_NAME="${WALLET_NAME%,}"
WALLET_NAME="${WALLET_NAME%"${WALLET_NAME##*[![:space:]]}"}"
HOTKEY_NAME="${HOTKEY_NAME%,}"
HOTKEY_NAME="${HOTKEY_NAME%"${HOTKEY_NAME##*[![:space:]]}"}"

# Trade-activity recovery:
# - phase mining OFF (no OBSERVE/COOLDOWN quote freeze)
# - latency_disable_ms above observed ~0.7–0.9s call_time (default 800 kills flat books)
# - floor awareness ON but allow left-tail new risk so death-spiral can break
# - softer EV gates; hard inventory/stop-loss kept
PARAMS="enable_mm_strategy=1 lazy_load=1 alpha_policy_mode=deterministic \
enable_separate_alpha=0 allow_legacy_auto_tuning=0 \
enable_phase_mining=0 \
enable_floor_awareness=1 score_floor_guard_ratio=1.02 \
left_tail_new_risk_enabled=1 \
weak_book_score_quantile=0.25 weak_book_size_mult=0.75 \
latency_cautious_ms=1200 latency_disable_ms=2500 \
mm_base_size=0.25 max_inventory_base=1.40 inventory_close_threshold=0.30 \
max_mm_books_per_tick=8 max_managed_books_per_tick=8 \
min_expected_alpha=0.10 min_economic_signal=0.04 \
cautious_inventory_util=0.55 reduce_only_inventory_util=0.80 \
liquidate_inventory_util=0.98 hard_stop_loss_bps=55 \
min_side_edge_bps=0.02 fee_buffer_bps=0.10 \
base_risk_aversion=0.75 alpha_shift_spreads=0.28 inventory_shift_spreads=0.50 \
markout_horizon_ns=2000000000 \
verbose_log=0 log_every_n=50 log_mm_strategy=1"

exec "$REPO_ROOT/run_miner.sh" \
  -w "$WALLET_NAME" \
  -h "$HOTKEY_NAME" \
  -u "$NETUID" \
  -a "$AXON_PORT" \
  -g "$AGENT_PATH" \
  -n Strategy4 \
  -m "$PARAMS" \
  "${EXTRA[@]}"
