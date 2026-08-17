#!/usr/bin/env bash
# Launch Strategy6 on the SN79-compatible test network.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

WALLET_NAME="${WALLET_NAME:-taos}"
HOTKEY_NAME="${HOTKEY_NAME:-miner}"
ENDPOINT="${ENDPOINT:-wss://test.finney.opentensor.ai:443}"
NETUID="${NETUID:-366}"
AXON_PORT="${AXON_PORT:-8093}"
AGENT_PATH="${AGENT_PATH:-$REPO_ROOT/agents/strategy}"

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

# Conservative first-race defaults.  Tune only after collecting per-book
# realized-PnL, Kappa, markout, fill-rate and response-latency distributions.
PARAMS="enable_mm_strategy=1 lazy_load=1 enable_separate_alpha=0 \
alpha_policy_mode=deterministic enable_floor_awareness=1 \
mm_base_size=0.25 max_inventory_base=1.20 inventory_close_threshold=0.25 \
max_mm_books_per_tick=6 max_managed_books_per_tick=6 \
min_expected_alpha=0.12 min_side_edge_bps=0.05 \
base_risk_aversion=0.85 alpha_shift_spreads=0.32 inventory_shift_spreads=0.55 \
s6_hjb_gamma=0.18 s6_hjb_kappa=1.50 s6_hjb_horizon=1.0 \
s6_hjb_inventory_extra=0.18 s6_hjb_base_half_spread=0.06 \
s6_hjb_vol_spread_weight=0.10 s6_hjb_intensity_spread_weight=0.06 \
s6_hjb_latency_spread_weight=0.12 s6_alpha_latency_decay=0.85 \
s6_network_buffer_ms=15 s6_inventory_priority_bonus=50 \
verbose_log=0 log_every_n=100 log_mm_strategy=1"

exec "$REPO_ROOT/run_miner.sh" \
  -e "$ENDPOINT" \
  -w "$WALLET_NAME" \
  -h "$HOTKEY_NAME" \
  -u "$NETUID" \
  -a "$AXON_PORT" \
  -g "$AGENT_PATH" \
  -n Strategy6 \
  -m "$PARAMS" \
  "${EXTRA[@]}"
