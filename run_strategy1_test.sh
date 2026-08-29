#!/usr/bin/env bash
# Launch Strategy1 on the SN79 testnet.
#
# Usage from the sn-79 repository root:
#   chmod +x run_strategy1_test.sh run_miner.sh
#   ./run_strategy1_test.sh -w <wallet> -h <hotkey>
#   ./run_strategy1_test.sh -w taos -h miner -u 366 -a 8090
#
# Overrides:
#   -e testnet websocket endpoint
#   -u testnet netuid
#   -a axon port
#   -p wallet path

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
ENDPOINT="${ENDPOINT:-wss://test.finney.opentensor.ai:443}"
NETUID="${NETUID:-366}"
# Strategy1 uses 8090 by default so it can coexist with the repository's
# Strategy3/4/5/6 test launchers on 8091/8092/8093/8094.
AXON_PORT="${AXON_PORT:-8090}"
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

# Keep the testnet baseline close to Strategy1.initialize() defaults.
# Expensive/log-heavy features are disabled to protect response latency.
PARAMS="enable_mm_strategy=1 lazy_load=1 \
fast_update=1 sync_event_csv=0 history_len=0 \
mm_base_size=0.25 max_inventory_base=1.20 inventory_close_threshold=0.25 \
max_mm_books_per_tick=4 max_managed_books_per_tick=4 \
min_expected_alpha=0.18 min_expected_realized_pnl=0.0 \
mm_expiry_period_ns=500000000 maintenance_size_mult=0.25 \
passive_exit_only=1 aggressive_close_min_ticks=300 position_max_ticks=300 \
mm_skip_inactive_tier=1 toxic_loss_streak=4 \
\
verbose_log=0 log_every_n=100 log_mm_strategy=1 \
log_direction=0 log_book_profile=0 log_regime=0 log_momentum_pnl=0 \
log_book_memory=0"

exec "$REPO_ROOT/run_miner.sh" \
  -e "$ENDPOINT" \
  -w "$WALLET_NAME" \
  -h "$HOTKEY_NAME" \
  -u "$NETUID" \
  -a "$AXON_PORT" \
  -g "$AGENT_PATH" \
  -n Strategy1 \
  -m "$PARAMS" \
  "${EXTRA[@]}"
