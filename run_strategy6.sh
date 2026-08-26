#!/usr/bin/env bash
# Launch Strategy6 (Strategy4 execution + latency-aware HJB/AS) on SN79 mainnet.
#
# Ubuntu usage (from repo root, same directory as run_miner.sh):
#   chmod +x run_strategy6.sh run_miner.sh
#   ./run_strategy6.sh -w <coldkey> -h <hotkey> -u 79 -a 8091
#   ./run_strategy6.sh -w taos -h miner
#
# Strategy6 keeps Strategy1 signals + Strategy4 order/risk controls and adds
# latency-aware HJB quoting, runtime min-order synchronization, hard inventory
# caps, dust protection, and same-book instruction priority.
#
# Keep alpha_policy_mode=deterministic until multi-seed/testnet races are stable.
# The latency caution/disable thresholds below intentionally match the recovery
# profile used by Strategy4 mainnet launchers so ~0.7-0.9s call_time does not
# disable every flat book before Strategy6 can rebuild activity.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

if [[ ! -f "$REPO_ROOT/run_miner.sh" ]]; then
  echo "error: run_miner.sh not found next to $0" >&2
  exit 1
fi
if [[ ! -f "$REPO_ROOT/agents/strategy/Strategy6.py" ]]; then
  echo "error: agents/strategy/Strategy6.py not found" >&2
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

# Strip accidental trailing commas/spaces in wallet/hotkey names.
WALLET_NAME="${WALLET_NAME%,}"
WALLET_NAME="${WALLET_NAME%"${WALLET_NAME##*[![:space:]]}"}"
HOTKEY_NAME="${HOTKEY_NAME%,}"
HOTKEY_NAME="${HOTKEY_NAME%"${HOTKEY_NAME##*[![:space:]]}"}"

# Competitive baseline / recovery-safe defaults.
# Tune only after paired multi-seed simulation + testnet benchmarking.
PARAMS="enable_mm_strategy=1 lazy_load=1 \
alpha_policy_mode=deterministic enable_separate_alpha=0 allow_legacy_auto_tuning=0 \
enable_phase_mining=0 \
enable_floor_awareness=1 score_floor_guard_ratio=1.02 \
left_tail_new_risk_enabled=1 weak_book_score_quantile=0.25 weak_book_size_mult=0.75 \
latency_cautious_ms=1200 latency_disable_ms=2500 \
mm_base_size=0.25 max_inventory_base=1.20 inventory_close_threshold=0.25 \
max_mm_books_per_tick=6 max_managed_books_per_tick=6 \
min_expected_alpha=0.12 min_expected_realized_pnl=0.0 min_economic_signal=0.04 \
cautious_inventory_util=0.55 reduce_only_inventory_util=0.80 \
liquidate_inventory_util=0.98 hard_stop_loss_bps=55 \
min_side_edge_bps=0.05 fee_buffer_bps=0.10 \
base_risk_aversion=0.85 alpha_shift_spreads=0.32 inventory_shift_spreads=0.55 \
markout_horizon_ns=2000000000 \
s6_hjb_gamma=0.18 s6_hjb_kappa=1.50 s6_hjb_horizon=1.00 \
s6_hjb_inventory_extra=0.18 s6_hjb_base_half_spread=0.06 \
s6_hjb_vol_spread_weight=0.10 s6_hjb_intensity_spread_weight=0.06 \
s6_hjb_latency_spread_weight=0.12 \
s6_alpha_latency_decay=0.85 s6_gamma_vol_weight=0.10 s6_gamma_latency_weight=0.20 \
s6_validator_timeout_ms=3000 s6_delay_min_ms=10 s6_delay_max_ms=1000 s6_delay_curve=5 \
s6_network_buffer_ms=15 s6_inventory_priority_bonus=50 s6_alpha_priority_bonus=2 \
verbose_log=0 log_every_n=50 log_mm_strategy=1"

exec "$REPO_ROOT/run_miner.sh" \
  -w "$WALLET_NAME" \
  -h "$HOTKEY_NAME" \
  -u "$NETUID" \
  -a "$AXON_PORT" \
  -g "$AGENT_PATH" \
  -n Strategy6 \
  -m "$PARAMS" \
  "${EXTRA[@]}"
