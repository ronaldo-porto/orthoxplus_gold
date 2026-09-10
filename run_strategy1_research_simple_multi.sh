#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

WALLET_NAME="${WALLET_NAME:-taos}"
HOTKEY_NAME="${HOTKEY_NAME:-miner}"
ENDPOINT="${ENDPOINT:-wss://test.finney.opentensor.ai:443}"
NETUID="${NETUID:-366}"
AXON_PORT="${AXON_PORT:-8091}"
AGENT_PATH="${AGENT_PATH:-$SCRIPT_DIR/agents/strategy}"
PM2_NAME="${PM2_NAME:-sn79-simple-m1}"
RESEARCH_EVERY_N="${RESEARCH_EVERY_N:-10}"
RESEARCH_BOOK="${RESEARCH_BOOK:--1}"
RESEARCH_JSONL="${RESEARCH_JSONL:-1}"
RESEARCH_CONSOLE="${RESEARCH_CONSOLE:-1}"
RESEARCH_QUEUE="${RESEARCH_QUEUE:-65536}"
RESEARCH_DIR="${RESEARCH_DIR:-$SCRIPT_DIR/logs/m1_strategy1_research_simple}"

EXTRA=()

# Support the explicit long-form PM2 process-name argument requested for
# multi-miner launches.  Keep -i as a backwards-compatible alias.
# Examples:
#   ./run_strategy1_research_simple_multi.sh --pm2_name sn79-a17-m1 ...
#   ./run_strategy1_research_simple_multi.sh --pm2_name=sn79-a17-m1 ...
_normalized_args=()
while (($#)); do
  case "$1" in
    --pm2_name)
      [[ $# -ge 2 && -n "${2:-}" ]] || { echo "ERROR: --pm2_name requires a value" >&2; exit 2; }
      _normalized_args+=(-i "$2")
      shift 2
      ;;
    --pm2_name=*)
      _pm2_value="${1#*=}"
      [[ -n "$_pm2_value" ]] || { echo "ERROR: --pm2_name requires a value" >&2; exit 2; }
      _normalized_args+=(-i "$_pm2_value")
      shift
      ;;
    *)
      _normalized_args+=("$1")
      shift
      ;;
  esac
done
set -- "${_normalized_args[@]}"
unset _normalized_args

while getopts "w:h:u:a:e:p:i:" flag; do
  case "$flag" in
    w) WALLET_NAME="$OPTARG" ;;
    h) HOTKEY_NAME="$OPTARG" ;;
    u) NETUID="$OPTARG" ;;
    a) AXON_PORT="$OPTARG" ;;
    e) ENDPOINT="$OPTARG" ;;
    p) EXTRA+=(-p "$OPTARG") ;;
    i) PM2_NAME="$OPTARG" ;;
    *) exit 2 ;;
  esac
done

[[ -f "$SCRIPT_DIR/run_miner_multi.sh" ]] || { echo "ERROR: run_miner_multi.sh missing" >&2; exit 1; }
[[ -f "$AGENT_PATH/Strategy1_Research_Simple.py" ]] || { echo "ERROR: Strategy1_Research_Simple.py missing" >&2; exit 1; }
grep -q 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_1"' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
  echo "ERROR: wrong Strategy1 direct candidate" >&2
  exit 1
}
grep -q 'RESEARCH_POLICY_VERSION = "simplified_hybrid_authority_v4_16_2"' "$AGENT_PATH/Strategy1_Research.py" || {
  echo "ERROR: baseline Strategy1_Research.py must remain V4.16.2" >&2
  exit 1
}

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

# A1.9.1 Phase B: the first BEHAVIOURAL revision of A1.9. Phase A proved the
# observer sees live resting exits (960 sightings / 260 ticks) and that the
# classifier separates them cleanly. B acts on that decision.
#
# The behavioural delta is exactly two things, and they are one mechanism -- they
# must not be split, because raising the TTL without the cancel path is the
# known-harmful configuration:
#   1. research_profitable_exit_ttl_ms 3000 -> 4000, which is 4x the verified
#      1,000 ms publish cadence, closing the ~1 s dead window between TTL expiry
#      and the next re-quote (the exit-TTL duty-cycle defect).
#   2. an explicit cancel for a resting exit the classifier calls stale, emitted
#      AFTER the frozen chain so the shared 5-instruction book budget is known.
#      No replacement is placed in the same response: the cancellation must be
#      visible in a later state (the A1.7.4.3.1 ownership rule).
# HOLD is the ABSENCE of an action -- it leaves the resting order untouched -- so
# every new risk lives in the reprice cancels. Everything else stays frozen:
# size 0.25, 6 active books, 2.0 BASE cap, QUIET gate, Taker and tail authority.
# Legacy Research knobs keep their source defaults but do not own the direct hot path.
PARAMS="enable_mm_strategy=1 lazy_load=1 fast_update=1 sync_event_csv=0 history_len=0 \
mm_base_size=0.25 max_inventory_base=1.20 max_mm_books_per_tick=6 max_managed_books_per_tick=10 \
min_expected_alpha=0.18 mm_expiry_period_ns=500000000 \
verbose_log=0 log_every_n=100 log_mm_strategy=0 log_direction=0 log_book_profile=0 log_regime=0 log_momentum_pnl=0 log_book_memory=0 \
debug_enabled=1 debug_every_n=${RESEARCH_EVERY_N} debug_jsonl=0 debug_book_id=${RESEARCH_BOOK} \
research_enabled=1 research_every_n=${RESEARCH_EVERY_N} research_book_id=${RESEARCH_BOOK} research_jsonl=${RESEARCH_JSONL} research_console=${RESEARCH_CONSOLE} research_compact_console=1 research_queue_size=${RESEARCH_QUEUE} \
research_neutral_fallback=1 research_sync_min_order=1 research_fix_inventory_util=1 research_fix_quote_reservation=1 \
research_enable_fast_candidate_screen=1 research_candidate_count=20 research_cheap_shortlist_count=24 \
research_max_open_books=6 research_max_active_open_books=6 research_max_total_open_books=8 research_max_total_abs_base=2.0 \
research_post_only_safety_ticks=2 research_local_kappa_refresh_ticks=10 research_score_target_books=80 research_total_score_ignition_books=41 research_total_score_full_breadth_books=80 \
research_lifecycle_taker_exit_prob=0.30 research_lifecycle_slippage_bps=0.75 research_lifecycle_holding_bps=0.50 \
research_positive_maker_veto_enabled=1 research_positive_maker_veto_floor_bps=1.0 research_positive_maker_veto_max_failed_exits=4 research_bounded_loss_escape_min_age_ticks=2.0 \
research_session_save_every_n=100 research_p95_target_ms=120 \
research_profitable_exit_ttl_ms=4000 research_a191_queue_preservation_enabled=1"

if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then
  python -m py_compile "$AGENT_PATH/Strategy1_Research_Simple.py"
  PYTHONPATH="$AGENT_PATH:$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    python -m pytest -q \
      tests/test_research_strategy1_direct_a1_5.py \
      tests/test_research_strategy1_direct_a1_5_1.py \
      tests/test_research_strategy1_direct_a1_6_0.py \
      tests/test_research_strategy1_direct_a1_6_1.py \
      tests/test_research_strategy1_direct_a1_6_2.py \
      tests/test_research_strategy1_direct_a1_6_3.py \
      tests/test_research_strategy1_direct_a1_7_0.py \
      tests/test_research_strategy1_direct_a1_7_1.py \
      tests/test_research_strategy1_direct_a1_7_2.py \
      tests/test_research_strategy1_direct_a1_7_3.py \
      tests/test_research_strategy1_direct_a1_7_4.py \
      tests/test_research_strategy1_direct_a1_7_4_1.py \
      tests/test_research_strategy1_direct_a1_7_4_2.py \
      tests/test_research_strategy1_direct_a1_7_4_3.py \
      tests/test_research_strategy1_direct_a1_7_4_3_1.py \
      tests/test_research_strategy1_direct_a1_7_4_3_2.py \
      tests/test_research_strategy1_direct_a1_7_4_4.py \
      tests/test_research_strategy1_direct_a1_7_4_5.py \
      tests/test_research_strategy1_direct_a1_7_5.py \
      tests/test_research_strategy1_direct_a1_9_0.py \
      tests/test_research_strategy1_direct_a1_9_0_1.py \
      tests/test_research_strategy1_direct_a1_9_0_2.py \
      tests/test_research_strategy1_direct_a1_9_0_3.py \
      tests/test_research_strategy1_direct_a1_9_1.py \
      tests/test_research_v4_16_2_economics_contract.py \
      tests/test_research_v4_16_1_p0_runtime.py \
      tests/test_research_v4_16_0_simplified_authority.py
  echo "Strategy1 direct V4.16.2 A1.9.1 Phase B preflight PASS"
  exit 0
fi

echo "[Strategy1_Research_Simple] version=strategy1_direct_v4_16_2_a1_9_1"
echo "[Strategy1_Research_Simple] pm2_name=$PM2_NAME netuid=$NETUID axon_port=$AXON_PORT"
echo "[Strategy1_Research_Simple] log_dir=$RESEARCH_DIR"

# Keep the strategy directory importable in the actual PM2/miner process, not
# only in the preflight subprocess.
export PYTHONPATH="$AGENT_PATH:$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"

exec "$SCRIPT_DIR/run_miner_multi.sh" \
  -i "$PM2_NAME" -e "$ENDPOINT" -w "$WALLET_NAME" -h "$HOTKEY_NAME" -u "$NETUID" -a "$AXON_PORT" \
  -g "$AGENT_PATH" -n Strategy1_Research_Simple -m "$PARAMS" "${EXTRA[@]}"
