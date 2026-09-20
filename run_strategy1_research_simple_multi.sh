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
# v5.0.4 operator settings; see the v5.0.4 preflight block below for what each one means.
#   HISTORY_ANCHOR  auto | established | <simulation id>@<sim seconds>   (H2)
#   LEGACY_SESSION  ignore | adopt                                       (H1)
#   RECORDER_MAX_MB compressed MB the state recorder may write           (H3)
# Prefer the --history_anchor flag (parsed below); it overrides the environment variable.
# With neither, the anchor is auto.
HISTORY_ANCHOR_SOURCE="default"
[[ -n "${HISTORY_ANCHOR:-}" ]] && HISTORY_ANCHOR_SOURCE="env"
HISTORY_ANCHOR="${HISTORY_ANCHOR:-auto}"
LEGACY_SESSION="${LEGACY_SESSION:-ignore}"
RECORDER_MAX_MB="${RECORDER_MAX_MB:-8192}"
# v6.0.0 operator settings; see the v6.0.0 preflight block below.
#   SHORT_LOT_FRACTION   short-lot boundary as a fraction of the minimum order, above 0.5 and below 1.0
#   INHERITED_SHORT_LOTS park | exit   what a restart does with a single lot the seed rebuilt
SHORT_LOT_FRACTION="${SHORT_LOT_FRACTION:-0.6667}"
INHERITED_SHORT_LOTS="${INHERITED_SHORT_LOTS:-park}"
# v6.0.2 operator setting; see the v6.0.2 preflight block below.  --max_active_books overrides it.
#   MAX_ACTIVE_BOOKS  productive books held at once, 6 to 8 (the frozen Research clamp is 8, and
#                     8 x 0.25 is exactly the 2.0 BASE cap).  6 restores v6.0.1.
MAX_ACTIVE_BOOKS="${MAX_ACTIVE_BOOKS:-8}"

EXTRA=()

# Support the explicit long-form PM2 process-name argument requested for
# multi-miner launches.  Keep -i as a backwards-compatible alias.
# Examples:
#   ./run_strategy1_research_simple_multi.sh --pm2_name sn79-a17-m1 ...
#   ./run_strategy1_research_simple_multi.sh --pm2_name=sn79-a17-m1 ...
# --history_anchor auto | established | <sim>@<seconds>  (v5.0.4 H2; omitted = auto)
#   established  every restart of a UID that already has a score history
#   auto         a fresh registration only
#   ./run_strategy1_research_simple_multi.sh --pm2_name sn79-m67 --history_anchor established ...
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
    --max_active_books)
      [[ $# -ge 2 && -n "${2:-}" ]] || { echo "ERROR: --max_active_books requires 6, 7 or 8" >&2; exit 2; }
      MAX_ACTIVE_BOOKS="$2"
      shift 2
      ;;
    --max_active_books=*)
      [[ -n "${1#*=}" ]] || { echo "ERROR: --max_active_books requires 6, 7 or 8" >&2; exit 2; }
      MAX_ACTIVE_BOOKS="${1#*=}"
      shift
      ;;
    --history_anchor)
      [[ $# -ge 2 && -n "${2:-}" ]] || { echo "ERROR: --history_anchor requires auto, established or <sim>@<seconds>" >&2; exit 2; }
      HISTORY_ANCHOR="$2"; HISTORY_ANCHOR_SOURCE="flag"
      shift 2
      ;;
    --history_anchor=*)
      [[ -n "${1#*=}" ]] || { echo "ERROR: --history_anchor requires auto, established or <sim>@<seconds>" >&2; exit 2; }
      HISTORY_ANCHOR="${1#*=}"; HISTORY_ANCHOR_SOURCE="flag"
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

# v6.0.1 O2: the endpoint defaults to testnet, so a mainnet launch without -e would run on the
# wrong network.  Refuse a netuid and endpoint that belong to different networks.
if [[ "$NETUID" == "79" && "$ENDPOINT" == *"test."* ]]; then
  echo "ERROR: netuid 79 is mainnet but the endpoint is testnet (${ENDPOINT})." >&2
  echo "       Pass -e wss://entrypoint-finney.opentensor.ai:443" >&2
  exit 2
fi
if [[ "$NETUID" != "79" && "$ENDPOINT" == *"entrypoint-finney"* ]]; then
  echo "ERROR: netuid ${NETUID} is not mainnet SN79 but the endpoint is mainnet (${ENDPOINT})." >&2
  exit 2
fi

[[ -f "$SCRIPT_DIR/run_miner_multi.sh" ]] || { echo "ERROR: run_miner_multi.sh missing" >&2; exit 1; }
[[ -f "$AGENT_PATH/Strategy1_Research_Simple.py" ]] || { echo "ERROR: Strategy1_Research_Simple.py missing" >&2; exit 1; }
POLICY_VER="$(sed -n 's/^SIMPLE_POLICY_VERSION = "\(.*\)"$/\1/p' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1)"
# A1.9.5 and A1.9.6 ship as their own policy versions.  The A1.9.2 / A1.9.2.1 /
# A1.9.3 / A1.9.4 guards below still apply to both -- those invariants are
# cumulative, not per-revision -- so they gate on A19X_BUILD rather than on one
# literal, and A1.9.6 keeps every A1.9.5 guard by setting A195_BUILD as well.
A19X_BUILD=0; A195_BUILD=0; A196_BUILD=0; A1961_BUILD=0; A197_BUILD=0; A198_BUILD=0; A199_BUILD=0; A1991_BUILD=0; A1992_BUILD=0; V500_BUILD=0; V501_BUILD=0; V502_BUILD=0; V503_BUILD=0; V504_BUILD=0; V600_BUILD=0; V601_BUILD=0; V602_BUILD=0; V603_BUILD=0; V610_BUILD=0; V611_BUILD=0; V620_BUILD=0; V621_BUILD=0; V622_BUILD=0; V623_BUILD=0; V624_BUILD=0; V625_BUILD=0; V626_BUILD=0
case "$POLICY_VER" in
  strategy1_direct_v4_16_2_a1_9_4) A19X_BUILD=1 ;;
  strategy1_direct_v4_16_2_a1_9_5) A19X_BUILD=1; A195_BUILD=1 ;;
  strategy1_direct_v4_16_2_a1_9_6) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1 ;;
  strategy1_direct_v4_16_2_a1_9_6_1) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1 ;;
  strategy1_direct_v4_16_2_a1_9_7) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1 ;;
  strategy1_direct_v4_16_2_a1_9_8) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1 ;;
  strategy1_direct_v4_16_2_a1_9_9) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1 ;;
  strategy1_direct_v4_16_2_a1_9_9_1) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1 ;;
  strategy1_direct_v4_16_2_a1_9_9_2) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1 ;;
  strategy1_direct_v5_0_0) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1 ;;
  strategy1_direct_v5_0_1) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1 ;;
  strategy1_direct_v5_0_2) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; V502_BUILD=1 ;;
  strategy1_direct_v5_0_3) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; V502_BUILD=1; V503_BUILD=1 ;;
  strategy1_direct_v5_0_4) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; V502_BUILD=1; V503_BUILD=1; V504_BUILD=1 ;;
  strategy1_direct_v6_0_0) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; V502_BUILD=1; V503_BUILD=1; V504_BUILD=1; V600_BUILD=1 ;;
  strategy1_direct_v6_0_1) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; V502_BUILD=1; V503_BUILD=1; V504_BUILD=1; V600_BUILD=1; V601_BUILD=1 ;;
  strategy1_direct_v6_0_2) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; V502_BUILD=1; V503_BUILD=1; V504_BUILD=1; V600_BUILD=1; V601_BUILD=1; V602_BUILD=1 ;;
  strategy1_direct_v6_0_3) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; V502_BUILD=1; V503_BUILD=1; V504_BUILD=1; V600_BUILD=1; V601_BUILD=1; V602_BUILD=1; V603_BUILD=1 ;;
  strategy1_direct_v6_1_0) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; V502_BUILD=1; V503_BUILD=1; V504_BUILD=1; V600_BUILD=1; V601_BUILD=1; V602_BUILD=1; V603_BUILD=1; V610_BUILD=1 ;;
  strategy1_direct_v6_1_1) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; V502_BUILD=1; V503_BUILD=1; V504_BUILD=1; V600_BUILD=1; V601_BUILD=1; V602_BUILD=1; V603_BUILD=1; V610_BUILD=1; V611_BUILD=1 ;;
  strategy1_direct_v6_2_0) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; V502_BUILD=1; V503_BUILD=1; V504_BUILD=1; V600_BUILD=1; V601_BUILD=1; V602_BUILD=1; V603_BUILD=1; V610_BUILD=1; V611_BUILD=1; V620_BUILD=1 ;;
  strategy1_direct_v6_2_1) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; V502_BUILD=1; V503_BUILD=1; V504_BUILD=1; V600_BUILD=1; V601_BUILD=1; V602_BUILD=1; V603_BUILD=1; V610_BUILD=1; V611_BUILD=1; V620_BUILD=1; V621_BUILD=1 ;;
  strategy1_direct_v6_2_2) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; V502_BUILD=1; V503_BUILD=1; V504_BUILD=1; V600_BUILD=1; V601_BUILD=1; V602_BUILD=1; V603_BUILD=1; V610_BUILD=1; V611_BUILD=1; V620_BUILD=1; V621_BUILD=1; V622_BUILD=1 ;;
  strategy1_direct_v6_2_3) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; V502_BUILD=1; V503_BUILD=1; V504_BUILD=1; V600_BUILD=1; V601_BUILD=1; V602_BUILD=1; V603_BUILD=1; V610_BUILD=1; V611_BUILD=1; V620_BUILD=1; V621_BUILD=1; V622_BUILD=1; V623_BUILD=1 ;;
  strategy1_direct_v6_2_4) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; V502_BUILD=1; V503_BUILD=1; V504_BUILD=1; V600_BUILD=1; V601_BUILD=1; V602_BUILD=1; V603_BUILD=1; V610_BUILD=1; V611_BUILD=1; V620_BUILD=1; V621_BUILD=1; V622_BUILD=1; V623_BUILD=1; V624_BUILD=1 ;;
  strategy1_direct_v6_2_5) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; V502_BUILD=1; V503_BUILD=1; V504_BUILD=1; V600_BUILD=1; V601_BUILD=1; V602_BUILD=1; V603_BUILD=1; V610_BUILD=1; V611_BUILD=1; V620_BUILD=1; V621_BUILD=1; V622_BUILD=1; V623_BUILD=1; V624_BUILD=1; V625_BUILD=1 ;;
  strategy1_direct_v6_2_6) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; V502_BUILD=1; V503_BUILD=1; V504_BUILD=1; V600_BUILD=1; V601_BUILD=1; V602_BUILD=1; V603_BUILD=1; V610_BUILD=1; V611_BUILD=1; V620_BUILD=1; V621_BUILD=1; V622_BUILD=1; V623_BUILD=1; V624_BUILD=1; V625_BUILD=1; V626_BUILD=1 ;;
  *)
    echo "ERROR: wrong Strategy1 direct candidate (SIMPLE_POLICY_VERSION=${POLICY_VER:-unset})" >&2
    exit 1
    ;;
esac
grep -q 'RESEARCH_POLICY_VERSION = "simplified_hybrid_authority_v4_16_2"' "$AGENT_PATH/Strategy1_Research.py" || {
  echo "ERROR: baseline Strategy1_Research.py must remain V4.16.2" >&2
  exit 1
}

# A1.9.1.1 activation guard.  The A1.9.1 run shipped engine_version=a1_9_1 while
# the runtime still reported Phase A shadow mode and emitted zero reprice
# cancels: a 4,000 ms TTL with no stale-cancel path, which is precisely the
# split the A1.9 design says must never run. A behavioural build must prove it
# can report behaviour_change=1, and must not hardcode Phase A anywhere.
if grep -qE 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_(1|2)' "$AGENT_PATH/Strategy1_Research_Simple.py"; then
  grep -q 'def _a19_behaviour_change' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.1 build cannot report behaviour_change from runtime state" >&2
    exit 1
  }
  if grep -qE 'a19_phase="A_SHADOW_MEASUREMENT"|phase="A2_LEDGER_SHADOW_MEASUREMENT"|behaviour_change=0,' "$AGENT_PATH/Strategy1_Research_Simple.py"; then
    echo "ERROR: A1.9.1 build still hardcodes Phase A telemetry; refusing the" >&2
    echo "       silent hybrid that invalidated the first 500-tick run" >&2
    exit 1
  fi
  A191_BUILD=1
fi

# A1.9.2 activation guard.  Same failure mode, different phase: a build that
# reports a1_9_2 while the admission gate can never fire would burn another
# 4,000 ticks before anyone noticed.  Both halves must be provable up front.
if [[ "$A19X_BUILD" == "1" ]]; then
  grep -q 'def _a192_behaviour_change' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.2 build cannot report behaviour_change from runtime state" >&2
    exit 1
  }
  grep -q 'def _a192_admission_verdict' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.2 build has no admission verdict; the gate is not wired" >&2
    exit 1
  }
  # Must match the CALL, not the def line: grepping the bare name matches the
  # definition and can never fail, which is how a guard passes a build whose
  # gate is unreachable.
  grep -q 'self\._a192_admission_verdict(' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.2 admission verdict is defined but never called" >&2
    exit 1
  }
  A192_BUILD=1
fi

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
# size 0.25, 6 active books (v6.0.2: MAX_ACTIVE_BOOKS), 2.0 BASE cap, QUIET gate, Taker and tail authority.
# Legacy Research knobs keep their source defaults but do not own the direct hot path.
# v6.0.2: only a v6.0.2 build may change the active-book cap; earlier builds keep 6.
if [[ "$V602_BUILD" != "1" ]]; then
  MAX_ACTIVE_BOOKS=6
fi
PARAMS="enable_mm_strategy=1 lazy_load=1 fast_update=1 sync_event_csv=0 history_len=0 \
mm_base_size=0.25 max_inventory_base=1.20 max_mm_books_per_tick=6 max_managed_books_per_tick=10 \
min_expected_alpha=0.18 mm_expiry_period_ns=500000000 \
verbose_log=0 log_every_n=100 log_mm_strategy=0 log_direction=0 log_book_profile=0 log_regime=0 log_momentum_pnl=0 log_book_memory=0 \
debug_enabled=1 debug_every_n=${RESEARCH_EVERY_N} debug_jsonl=0 debug_book_id=${RESEARCH_BOOK} \
research_enabled=1 research_every_n=${RESEARCH_EVERY_N} research_book_id=${RESEARCH_BOOK} research_jsonl=${RESEARCH_JSONL} research_console=${RESEARCH_CONSOLE} research_compact_console=1 research_queue_size=${RESEARCH_QUEUE} \
research_neutral_fallback=1 research_sync_min_order=1 research_fix_inventory_util=1 research_fix_quote_reservation=1 \
research_enable_fast_candidate_screen=1 research_candidate_count=20 research_cheap_shortlist_count=24 \
research_max_open_books=${MAX_ACTIVE_BOOKS} research_max_active_open_books=${MAX_ACTIVE_BOOKS} research_max_total_open_books=8 research_max_total_abs_base=2.0 \
research_post_only_safety_ticks=2 research_local_kappa_refresh_ticks=10 research_score_target_books=80 research_total_score_ignition_books=41 research_total_score_full_breadth_books=80 \
research_lifecycle_taker_exit_prob=0.30 research_lifecycle_slippage_bps=0.75 research_lifecycle_holding_bps=0.50 \
research_positive_maker_veto_enabled=1 research_positive_maker_veto_floor_bps=1.0 research_positive_maker_veto_max_failed_exits=4 research_bounded_loss_escape_min_age_ticks=2.0 \
research_session_save_every_n=100 research_p95_target_ms=120 \
research_profitable_exit_ttl_ms=4000 research_a191_queue_preservation_enabled=1 \
research_a192_book_risk_admission_enabled=1 \
research_a1921_severity_priority_enabled=1 \
research_a193_breadth_admission_enabled=1 \
research_a194_rebate_conjunction_enabled=1 \
research_a195_reconcile_observe=1 research_a195_dust_capacity_class=1 \
research_a195_taker_floor_enforce=1 research_a195_taker_floor_bps=-25.0 \
research_a195_inventory_truth_enabled=1 research_a195_startup_orphan_cancel=1 \
research_a195_breadth_lane_enabled=1 \
research_a196_legacy_dust_ledger=1 research_a196_inherited_parked_allowance=1 \
research_a196_quantity_grid_snap=1 \
research_a1961_seed_quote_guard=1 research_a1961_fee_residue_ledger=1 \
research_a197_postfill_protect=1 \
research_a198_absolute_taker_authority=1 \
research_a199_exit_pending_authority=1 research_a199_epoch_resync=1 \
research_a1991_pending_owns_book=1 \
research_a1992_idle_gc=1 \
research_v500_analytics=1 \
research_v501_activity_alignment=1 \
research_v502_flat_residue=1 research_v502_clip_recognition=1 \
research_v502_compactor_turn=1 research_v502_market_terminal=1 \
research_v503_newcomer_gate=1 research_v503_state_recorder=1 \
research_v503_fifo_fee_exact=1 research_v503_book_kappa_rows=1 \
research_v504_session_per_uid=1 research_v504_legacy_session=${LEGACY_SESSION} \
research_v504_history_anchor=${HISTORY_ANCHOR} \
research_v504_disk_budget=1 research_v503_recorder_max_mb=${RECORDER_MAX_MB} \
research_v504_mirror_rounds=1 \
research_v600_short_lots=1 research_v600_short_lot_min_fraction=${SHORT_LOT_FRACTION} \
research_v600_inherited_short_lots=${INHERITED_SHORT_LOTS} \
research_v601_workable_dust_reserve=1 \
research_v603_short_lot_release=1 \
research_v61_no_loss=1 \
research_v61_state_gap_repair=1 research_v61_request_memo=1 \
research_v611_floor_reprice=1 research_v611_price_lift=1 \
research_v62_breadth=1 \
research_v621_managed_universe=1 \
research_v622_seed_at_breadth=1 \
research_v623_premium_floor=1 \
research_v624_release_life=1 \
research_v625_two_sided=1 \
research_v625_cap_pace=1 \
research_v626_loss_budget=1 \
research_v626_capture_balance=1 \
research_v626_quote_life=1"

# Every PARAMS key must be read by name somewhere in the agent code.  A misspelled key is
# otherwise completely silent: the agent takes its source default, the launcher still reports the
# build, and the feature is simply off.  Only 41 of the 100 keys had a hand-written guard, so 59
# were typo-exposed -- research_a195_taker_floor_bps is the sharpest of them, since losing it
# restores the unbounded taker exit A1.9.5 exists to prevent.  One corpus pass, ~1.2 s, no
# allow-list: all 100 keys resolve as of 2026-09-18.
PARAM_IDENTIFIERS="$(grep -rhoE '[A-Za-z_][A-Za-z0-9_]*' --include=*.py "$AGENT_PATH" "$SCRIPT_DIR/taos" | sort -u)"
PARAM_UNKNOWN=""
PARAM_COUNT=0
for _tok in $PARAMS; do
  _key="${_tok%%=*}"
  [[ "$_key" == "$_tok" || -z "$_key" ]] && continue
  PARAM_COUNT=$((PARAM_COUNT + 1))
  grep -qxF "$_key" <<< "$PARAM_IDENTIFIERS" || PARAM_UNKNOWN="$PARAM_UNKNOWN $_key"
done
if [[ -n "$PARAM_UNKNOWN" ]]; then
  echo "ERROR: PARAMS key read by no agent code (typo?):$PARAM_UNKNOWN" >&2
  exit 1
fi
echo "[preflight] PARAMS keys resolve to agent code PASS (${PARAM_COUNT} keys)"

# Checked against the PARAMS VALUE, not the script text: grepping the file would
# match this guard's own source line and always pass.
if [[ "${A191_BUILD:-0}" == "1" ]]; then
  [[ "$PARAMS" == *"research_a191_queue_preservation_enabled=1"* ]] || {
    echo "ERROR: A1.9.1 build without research_a191_queue_preservation_enabled=1 in PARAMS" >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_profitable_exit_ttl_ms=4000"* ]] || {
    echo "ERROR: A1.9.1 build without the 4000 ms exit TTL. The TTL raise and the" >&2
    echo "       stale-cancel path are one mechanism and must not be split." >&2
    exit 1
  }
  echo "[preflight] A1.9.1 behavioural activation guard PASS"
fi

if [[ "${A192_BUILD:-0}" == "1" ]]; then
  [[ "$PARAMS" == *"research_a192_book_risk_admission_enabled=1"* ]] || {
    echo "ERROR: A1.9.2 build without research_a192_book_risk_admission_enabled=1" >&2
    echo "       in PARAMS. The engine would report a1_9_2 with the admission" >&2
    echo "       gate disabled -- the silent hybrid A1.9.1 already cost twice." >&2
    exit 1
  }
  echo "[preflight] A1.9.2 book-risk admission activation guard PASS"
fi

# A1.9.2.1 activation guard.  A1.9.2 suppressed in arrival order: the books that
# got budget and the books denied by the cap had identical severity (median
# 40.73 both).  A build that reports a1_9_2_1 while the severity path can never
# fire would repeat A1.9.2 under a new name and cost another run.
if [[ "$A19X_BUILD" == "1" ]]; then
  for _fn in _a1921_severity_threshold _a1921_shrink_risk _a1921_seed_severity_history; do
    grep -q "def $_fn" "$AGENT_PATH/Strategy1_Research_Simple.py" || {
      echo "ERROR: A1.9.2.1 build is missing $_fn" >&2
      exit 1
    }
    grep -q "self\.$_fn(" "$AGENT_PATH/Strategy1_Research_Simple.py" || {
      echo "ERROR: A1.9.2.1 $_fn is defined but never called -- the severity" >&2
      echo "       path is inert and the run would repeat A1.9.2." >&2
      exit 1
    }
  done
  grep -q 'reason": A192_ALLOW_SEVERITY_RANK' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.2.1 never returns ALLOW_SEVERITY_RANK; prioritisation" >&2
    echo "       cannot change any admission decision." >&2
    exit 1
  }
  # The cap is deliberately NOT retuned in this revision.  Holding it fixed is
  # what makes the allocation change attributable; moving it in the same run
  # would confound the two and repeat the mistake A1.9.2.1 exists to avoid.
  grep -q 'A192_MAX_SUPPRESSION_PCT = 35.0' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.2.1 changed the suppression cap. The cap must stay 35.0:" >&2
    echo "       allocation and budget size cannot both move in one run." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_a1921_severity_priority_enabled=1"* ]] || {
    echo "ERROR: A1.9.2.1 build without research_a1921_severity_priority_enabled=1" >&2
    echo "       in PARAMS. The engine would report a1_9_2_1 while allocating in" >&2
    echo "       arrival order -- exactly the A1.9.2 behaviour under a new name." >&2
    exit 1
  }
  echo "[preflight] A1.9.2.1 severity-prioritised budget activation guard PASS"
fi

# A1.9.3 activation guard.  Kappa observations expire on a rolling window, so
# qualification breadth is a FLOW: measured over 1,713 ticks, qualified books
# track rt_velocity * window / required (0.0885 * 2286 / 3 = 67.4 predicted vs
# 64 observed).  NEGATIVE_CURRENT_EDGE made a book ineligible BEFORE the
# completion ladder and the frozen base's expiry/deadline rank bonuses could
# apply, so every mechanism built to hold breadth was unreachable in exactly
# the positive-fee regime where breadth is hardest to hold.
if [[ "$A19X_BUILD" == "1" ]]; then
  for _fn in _a193_breadth_override _a193_breadth_lane _a193_breadth_budget; do
    grep -q "def $_fn" "$AGENT_PATH/Strategy1_Research_Simple.py" || {
      echo "ERROR: A1.9.3 build is missing $_fn" >&2
      exit 1
    }
  done
  if [[ "$A195_BUILD" == "1" ]]; then
    # A1.9.5 step 4 RETIRES this call site, and the retirement is measured, not
    # assumed: across two runs (9,738 + 4,688 ticks) the override produced zero
    # admits and zero denies, because one-away books never reach
    # NEGATIVE_CURRENT_EDGE -- all 1,421 one-away RANK rows of the A1.9.5 run
    # were eligible=True, reject_reason=None, with no negative trading_ev.
    # There was nothing at this stage to override.  So the guard INVERTS rather
    # than disappearing: the dead site must be gone AND the relocation must be
    # present.  Dropping the check outright would let a later edit delete
    # breadth authority in silence -- the A1.9.3 inert-feature failure wearing
    # a new costume, which is exactly what this guard family exists to catch.
    grep -q 'RETIRED in A1.9.5 step 4' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
      echo "ERROR: A1.9.5 build without the step-4 retirement record at the" >&2
      echo "       A1.9.3 hook site. Retire it with its evidence, or revert." >&2
      exit 1
    }
    grep -q 'self\._a195_breadth_relief(' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
      echo "ERROR: A1.9.5 retired the A1.9.3 hook without wiring" >&2
      echo "       _a195_breadth_relief at the A1.7.4.5 floor -- breadth then" >&2
      echo "       has NO authority anywhere and the run is inert. That is the" >&2
      echo "       precise failure the retirement was meant to end." >&2
      exit 1
    }
  else
    grep -q 'self\._a193_breadth_override(' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
      echo "ERROR: A1.9.3 _a193_breadth_override is defined but never called --" >&2
      echo "       breadth-critical books stay unreachable and the run is inert." >&2
      exit 1
    }
    # It must override NEGATIVE_CURRENT_EDGE and nothing else.  TOXIC,
    # INVENTORY_BLOCKED, UNSAFE and VOLUME_CAP stay hard rejects.
    grep -q 'if reject == "NEGATIVE_CURRENT_EDGE":' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
      echo "ERROR: A1.9.3 does not scope its override to NEGATIVE_CURRENT_EDGE." >&2
      exit 1
    }
  fi
  # THE safety property: books two or more observations away must get nothing.
  # One round trip does not change their qualification state, so admitting them
  # at negative edge buys volume, not breadth -- which is the activity
  # controller this revision deliberately is not.
  grep -q 'if int(remaining) == 1:' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.3 lane selection is not restricted to remaining == 1;" >&2
    echo "       it would admit books a single round trip cannot qualify." >&2
    exit 1
  }
  # The cost ceiling must be absolute, not spread-scaled.  Replaying 4,442 RANK
  # records, a spread-only bound admitted a -26.30 bps entry at +54.60 bps fee.
  grep -q 'float(self.A192_TAIL_SHORTFALL_FLOOR_BPS),' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.3 cost bound is not anchored to the material-harm floor;" >&2
    echo "       a wide spread could licence an arbitrarily expensive entry." >&2
    exit 1
  }
  grep -q 'A192_MAX_SUPPRESSION_PCT = 35.0' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.3 changed the A1.9.2 suppression cap. It stays 35.0:" >&2
    echo "       admission and budget size cannot both move in one run." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_a193_breadth_admission_enabled=1"* ]] || {
    echo "ERROR: A1.9.3 build without research_a193_breadth_admission_enabled=1" >&2
    echo "       in PARAMS. The engine would report a1_9_3 while leaving every" >&2
    echo "       breadth-critical book unreachable -- A1.9.2.1 under a new name." >&2
    exit 1
  }
  if [[ "$A195_BUILD" == "1" ]]; then
    echo "[preflight] A1.9.3 hook retired; breadth relocated to A1.7.4.5 PASS"
  else
    echo "[preflight] A1.9.3 breadth-critical admission activation guard PASS"
  fi
fi

# ---------------------------------------------------------------- A1.9.4
# The A1.9.3 run put 85.6% of its 2,189.0 bps of A174_TAIL_COUNTERFACTUAL
# avoidable loss through ONE branch: `if fee <= 0.0` returned ALLOW_REBATE_ENTRY
# before reading a single history field.  Books 97/39/61 (mean entry fee
# -48.9/-10.7/-7.8 bps, persisted net_bps_ewma -58.5/-29.2/-13.6) were admitted
# on all 116 of their entries through it, and their damage ranked in exactly
# the order of their rebate depth.  A1.9.4 keeps fee sign as a discriminator
# and removes it as immunity.
if [[ "$A19X_BUILD" == "1" ]]; then
  for _fn in _a194_enabled _a194_rebate_bps; do
    grep -q "def $_fn" "$AGENT_PATH/Strategy1_Research_Simple.py" || {
      echo "ERROR: A1.9.4 build is missing $_fn" >&2
      exit 1
    }
  done
  # THE property: the unconditional waiver must be gone.  A bare `if fee <= 0.0:`
  # returning ALLOW_REBATE_ENTRY is the exact line that cost the A1.9.3 run.
  grep -q 'if fee <= 0.0 and not self._a194_enabled():' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.4 still short-circuits on fee <= 0 unconditionally --" >&2
    echo "       rebate books keep their A1.9.2 immunity and the run is inert." >&2
    exit 1
  }
  # The coverage test must read history, so it has to sit after _a192_book_risk.
  _gate=$(python3 - "$AGENT_PATH/Strategy1_Research_Simple.py" <<'EOF'
import pathlib, sys
b = pathlib.Path(sys.argv[1]).read_text().split("def _a192_admission_verdict")[1].split("\n    def ")[0]
b = b.split(chr(34) * 3, 2)[2]   # drop the docstring: it names the same constants
print(int(b.index("risk = self._a192_book_risk(bid)") < b.index("A194_ALLOW_REBATE_COVERED")))
EOF
)
  [[ "$_gate" == "1" ]] || {
    echo "ERROR: A1.9.4 evaluates the rebate before reading book history." >&2
    echo "       That is the A1.9.2 ordering the revision exists to reverse." >&2
    exit 1
  }
  # Severity stays fee-blind.  A1.9.2.1 measured Spearman(fee, pnl) = +0.024
  # inside the flagged set and the A1.9.3 damage ranked by rebate DEPTH, so a
  # rebate credit against severity would rank the worst books safest.
  grep -q 'severity = self._a1921_severity(risk)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.4 made the A1.9.2.1 severity ranking fee-dependent." >&2
    exit 1
  }
  # Suppression budget and detector thresholds stay put: admission ordering and
  # budget size cannot both move in one run or neither is attributable.
  for _const in 'A192_MAX_SUPPRESSION_PCT = 35.0' 'A192_NET_BPS_FLOOR = 0.0' \
                'A192_TAIL_SHORTFALL_FLOOR_BPS = 5.0' 'A192_MIN_BOOK_SAMPLES = 5'; do
    grep -q "$_const" "$AGENT_PATH/Strategy1_Research_Simple.py" || {
      echo "ERROR: A1.9.4 moved '$_const'. It stays fixed for this revision." >&2
      exit 1
    }
  done
  [[ "$PARAMS" == *"research_a194_rebate_conjunction_enabled=1"* ]] || {
    echo "ERROR: A1.9.4 build without research_a194_rebate_conjunction_enabled=1" >&2
    echo "       in PARAMS. The engine would report a1_9_4 while every rebate" >&2
    echo "       book keeps its waiver -- A1.9.3 under a new name." >&2
    exit 1
  }
  echo "[preflight] A1.9.4 rebate conjunction activation guard PASS"
fi

# A1.9.5 activation guard.  Steps 1+2 ran 4,688 ticks and produced the first
# breadth gain of the series (qualified 53 -> 70) together with a 3.71x breach
# of the phase's own cubic-downside abort gate (0.0277 -> 0.1027).  Both came
# from one mechanism: F3 freed capacity, the capacity became taker exits, and
# every taker exit ships its loss floor declared and unenforced.  The guards
# below pin the four invariants that make steps 2.5/3/4 safe to run at all.
if [[ "$A195_BUILD" == "1" ]]; then
  for _mod in research_direct_taker_bound research_direct_inventory_truth \
              research_direct_breadth_lane; do
    [[ -f "$AGENT_PATH/$_mod.py" ]] || {
      echo "ERROR: A1.9.5 build is missing $_mod.py" >&2
      exit 1
    }
  done

  # F8, step 2.5.  THE property: the taker exit must carry a bound.  The frozen
  # base calls response.market_order() with max_slippage omitted, and
  # instructions.py serialises a missing bound as 0.0 -- which the venue reads
  # as UNBOUNDED, not as zero loss.  147 of 442 round trips realised past their
  # own declared floor because of it, worst -262 bps against a -25 bps floor.
  grep -q 'def _execute_aggressive_close' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.5 does not override _execute_aggressive_close; the taker" >&2
    echo "       exit still ships unbounded and F8 is inert." >&2
    exit 1
  }
  grep -q 'placed = super()._execute_aggressive_close(' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.5 reimplements the taker close instead of delegating to" >&2
    echo "       the frozen base. The fee gate, volume cap, balance checks and" >&2
    echo "       cancel-before-taker ordering must stay exactly as frozen." >&2
    exit 1
  }
  # The zero-floor inversion: seven triggers declare a floor of exactly 0.0 --
  # their STRICTEST floor.  Forwarding it verbatim turns the strictest floor
  # into no floor.  The clamp must be strictly positive.
  grep -q 'A195_MIN_SLIPPAGE_FRACTION = 1e-4' "$AGENT_PATH/research_direct_taker_bound.py" || {
    echo "ERROR: A1.9.5 taker bound has no positive minimum slippage fraction." >&2
    echo "       A declared floor of 0.0 would ship as max_slippage=0.0, which" >&2
    echo "       the wire format means as UNBOUNDED. That inverts the gate." >&2
    exit 1
  }

  # F2, step 3.  Venue net position is total - initial.  The legacy helper
  # reads `total` -- the whole account balance on the book, two orders of
  # magnitude out and of the wrong sign (book 101: total 79.9076,
  # initial 80.5658, true net -0.6582).
  grep -q 'venue_net_base' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.5 seeding does not use venue_net_base; reconciling from" >&2
    echo "       reconcile_account_base seeds the account balance as a position." >&2
    exit 1
  }
  # A1.9.0.3 invariant, reintroduced by a new path and caught by the test diff:
  # an unregistered cancel falls through to EXPIRED and overstates exchange-side
  # expiry.  The startup orphan cancel is ours and must say so.
  grep -q 'ABSENT_ORPHAN_CANCEL' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.5 startup orphan cancel does not register a reason." >&2
    echo "       Unregistered cancels read as EXPIRED -- the A1.9.0.3 defect." >&2
    exit 1
  }

  # Step 4.  Relief restores the BASE floor and never goes below it.  Admitting
  # a negative or sub-base edge buys breadth by destroying kappa, which is the
  # trade A1.9.2/A1.9.4 exist to prevent and G10 exists to detect.
  grep -q 'relieved_min_edge_bps=base,' "$AGENT_PATH/research_direct_breadth_lane.py" || {
    echo "ERROR: A1.9.5 breadth relief does not floor at the A1.7.4.5 base." >&2
    echo "       Relief below base buys volume at the cost of cubic downside." >&2
    exit 1
  }
  grep -q 'if remaining != 1:' "$AGENT_PATH/research_direct_breadth_lane.py" || {
    echo "ERROR: A1.9.5 breadth relief is not restricted to one-away books;" >&2
    echo "       two-away relief buys volume, not breadth." >&2
    exit 1
  }

  for _p in research_a195_taker_floor_enforce=1 \
            research_a195_inventory_truth_enabled=1 \
            research_a195_breadth_lane_enabled=1; do
    [[ "$PARAMS" == *"$_p"* ]] || {
      echo "ERROR: A1.9.5 build without $_p in PARAMS. The engine would report" >&2
      echo "       a1_9_5 while the step it names does nothing -- A1.9.3 again." >&2
      exit 1
    }
  done
  echo "[preflight] A1.9.5 taker bound / inventory truth / breadth relief PASS"
fi

# A1.9.6 activation guard.  A1.9.5 step 3 made inventory true and closed
# admission: round trips fell 442 -> 10 in 2,060 ticks.  109 legacy-dust books
# written into the tracker read non-FLAT and left the enterable universe (397 of
# the prior run's 442 round trips were on them), an inherited lot the loss floor
# parked held 52% of productive BASE, and float-noisy quantities left one-unit
# residues the venue's truncation never cleared.  One guard per fix.
if [[ "$A196_BUILD" == "1" ]]; then
  [[ -f "$AGENT_PATH/research_direct_legacy_baseline.py" ]] || {
    echo "ERROR: A1.9.6 build is missing research_direct_legacy_baseline.py" >&2
    exit 1
  }

  # F9.  The seed routes through the split.  Appending plan.lots whole puts every
  # legacy-dust book in the tracker, where the entry builder skips it.
  grep -qF 'for lot in split.tracker_lots:' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.6 seed does not route through split_seed_plan; legacy dust" >&2
    echo "       would leave ~109 books non-FLAT and unenterable, as in A1.9.5." >&2
    exit 1
  }
  if grep -qF 'for lot in plan.lots:' "$AGENT_PATH/Strategy1_Research_Simple.py"; then
    echo "ERROR: A1.9.6 still appends plan.lots to the tracker." >&2
    exit 1
  fi
  # The ledger is still exposure, on both gates -- otherwise legacy dust is
  # invisible risk again, the pre-A1.9.5 state the seed existed to end.
  # Whole-line matches: `abs_now += ...` is a substring of the `dust_abs_now`
  # line, so a plain substring grep would pass with the charge deleted.
  for _re in '^[[:space:]]+abs_now \+= a196_ledger_abs_now$' \
             '^[[:space:]]+filled_abs \+= a196_ledger_abs_now$'; do
    grep -qE "$_re" "$AGENT_PATH/Strategy1_Research_Simple.py" || {
      echo "ERROR: A1.9.6 legacy ledger is not charged to exposure ($_re)." >&2
      exit 1
    }
  done
  # The ledger is excused by the F3 legacy bonus.  Without the dust class it is
  # charged in full and admission reads zero from the first tick.
  [[ "$PARAMS" == *"research_a195_dust_capacity_class=1"* ]] || {
    echo "ERROR: A1.9.6 legacy ledger requires research_a195_dust_capacity_class=1." >&2
    exit 1
  }

  # F10.  The allowance lands on BOTH gates: admission alone moves the block
  # into the final validator, where STRICT_EXPOSURE_HEADROOM refuses the entry.
  grep -qF 'a196_inherited = self._a196_inherited_parked_report(max_abs=max_abs)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.6 inherited-parked allowance is not applied at admission." >&2
    exit 1
  }
  grep -qF 'filled_abs - self._a196_inherited_parked_exempt(max_abs=max_abs)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.6 inherited-parked allowance is not applied in the final" >&2
    echo "       validator; the block would move downstream, not go away." >&2
    exit 1
  }
  # And it is bounded.  An unbounded allowance is an unbounded position.
  grep -q '^A196_INHERITED_PARKED_MAX_FRACTION = 0.5$' "$AGENT_PATH/research_direct_legacy_baseline.py" || {
    echo "ERROR: A1.9.6 inherited-parked allowance is not capped at 0.5 x max_abs." >&2
    exit 1
  }

  # F11.  Placements go on the grid after the chain has built them, and a grid
  # value whose double sits below its decimal is lifted by one ulp -- without
  # the lift a clean 0.2501 can still execute as 0.2500.
  grep -qF 'self._a196_snap_outgoing_quantities(response, state)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.6 does not snap outgoing quantities; noisy exits keep" >&2
    echo "       leaving one-unit residues behind." >&2
    exit 1
  }
  grep -qF 'math.nextafter(q, math.inf)' "$AGENT_PATH/research_direct_legacy_baseline.py" || {
    echo "ERROR: A1.9.6 grid snap has no one-ulp lift." >&2
    exit 1
  }

  for _p in research_a196_legacy_dust_ledger=1 \
            research_a196_inherited_parked_allowance=1 \
            research_a196_quantity_grid_snap=1; do
    [[ "$PARAMS" == *"$_p"* ]] || {
      echo "ERROR: A1.9.6 build without $_p in PARAMS. The engine would report" >&2
      echo "       a1_9_6 while the fix it names does nothing." >&2
      exit 1
    }
  done
  echo "[preflight] A1.9.6 legacy baseline / inherited parked / quantity grid PASS"
fi

# A1.9.6.1 activation guard.  The first A1.9.6 run priced two inherited lots from
# crossed quotes (book 99: bid 411.37 / ask 285.28) and held one of them on
# +1,805 bps of profit that never existed for 2,326 ticks; every fee-paying buy
# left the venue a BASE unit below the tracker; and the loss-floor abort rule
# measured trigger timing instead of F8.  One guard per fix.
if [[ "$A1961_BUILD" == "1" ]]; then
  [[ -f "$AGENT_PATH/research_direct_venue_integrity.py" ]] || {
    echo "ERROR: A1.9.6.1 build is missing research_direct_venue_integrity.py" >&2
    exit 1
  }

  # Seed pricing: the mid comes from a touch that is a market, and a crossed
  # touch is refused -- otherwise a crossed book becomes a cost basis again.
  grep -qF 'mid = touch_mid(getattr(book, "bids", None), getattr(book, "asks", None))' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.6.1 seed does not price from a validated touch." >&2
    exit 1
  }
  grep -qF 'if bid is None or ask is None or ask < bid:' "$AGENT_PATH/research_direct_venue_integrity.py" || {
    echo "ERROR: A1.9.6.1 valid_touch does not refuse a crossed quote." >&2
    exit 1
  }
  # A REAL lot without a price waits -- and stays charged on both gates meanwhile.
  grep -qF 'self._a1961_service_pending_seed(state)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.6.1 never retries the lots the seed could not price;" >&2
    echo "       they would stay out of the tracker for the whole run." >&2
    exit 1
  }
  for _re in '^[[:space:]]+abs_now \+= a1961_pending_abs_now$' \
             '^[[:space:]]+filled_abs \+= a1961_pending_abs_now$'; do
    grep -qE "$_re" "$AGENT_PATH/Strategy1_Research_Simple.py" || {
      echo "ERROR: A1.9.6.1 waiting inherited lots are not charged to exposure ($_re)." >&2
      exit 1
    }
  done

  # Fee residue: mirrored once per trade, rounded up exactly as the venue does.
  grep -qF 'self._a1961_note_fee_residue(event)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.6.1 does not mirror BASE-denominated fees; reconcile" >&2
    echo "       divergence would grow by a unit per fee-paying buy." >&2
    exit 1
  }
  grep -qF 'rounding=ROUND_CEILING' "$AGENT_PATH/research_direct_venue_integrity.py" || {
    echo "ERROR: A1.9.6.1 fee residue does not round up like ClearingManager." >&2
    exit 1
  }

  for _p in research_a1961_seed_quote_guard=1 \
            research_a1961_fee_residue_ledger=1; do
    [[ "$PARAMS" == *"$_p"* ]] || {
      echo "ERROR: A1.9.6.1 build without $_p in PARAMS. The engine would report" >&2
      echo "       a1_9_6_1 while the fix it names does nothing." >&2
      exit 1
    }
  done
  echo "[preflight] A1.9.6.1 seed quote guard / fee residue / taker outcome PASS"
fi

# A1.9.7.  Measured on the A1.9.6.1 run (log 20260913_053425): 579 of 600 round
# trips were first evaluated two or more ticks after their opening fill.  Every
# buy fill waited three ticks for LOCAL_EXPIRY because side 0 read as no side, and
# the tick after every fill skipped exit management while the other entry quote
# was cancelled -- book 81 went from -137 to -597 bps across that one tick.
if [[ "$A197_BUILD" == "1" ]]; then
  [[ -f "$AGENT_PATH/research_direct_postfill_protection.py" ]] || {
    echo "ERROR: A1.9.7 build is missing research_direct_postfill_protection.py" >&2
    exit 1
  }

  # P2: a buy side sent as the integer 0 is still a side.
  grep -qF 'token = "" if side is None else str(side).strip().lower()' "$AGENT_PATH/research_direct_book_ownership.py" || {
    echo "ERROR: A1.9.7 canonical_order_side still drops side 0; buy fills cannot" >&2
    echo "       release their reservation and wait for LOCAL_EXPIRY." >&2
    exit 1
  }

  # P1: the post-fill tick evaluates an ABSOLUTE position instead of skipping it,
  # only an ABSOLUTE one, and never places a maker exit beside the cancel.
  grep -qF 'a197_postfill = self._a197_postfill_candidate(book_id, inventory, mid)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.7 never evaluates a position on the tick after its entry fill." >&2
    exit 1
  }
  grep -qE '^[[:space:]]+return m is not None and classify_risk_band\(m\) == BAND_ABSOLUTE$' "$AGENT_PATH/research_direct_postfill_protection.py" || {
    echo "ERROR: A1.9.7 post-fill protection is not limited to ABSOLUTE positions." >&2
    exit 1
  }
  grep -qF 'if getattr(self, "_a197_postfill_book", None) == int(book_id):' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.7 could place a maker exit in the response that cancels the" >&2
    echo "       entry quotes -- the cancel-and-replace A1.7.4.3.1 forbids." >&2
    exit 1
  }
  grep -qF 'stripped = strip_book_limit_orders(' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.7 does not remove limit placements from a post-fill response." >&2
    exit 1
  }

  # P1 sign-flip watch and P3 exit-gap record, both fed by the own-fill hook.
  grep -qF 'self._a197_note_own_fill(book_id=int(book_id), before=float(before), after=float(after))' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.7 does not watch protective exits for a sign flip or record exit gaps." >&2
    exit 1
  }

  [[ "$PARAMS" == *"research_a197_postfill_protect=1"* ]] || {
    echo "ERROR: A1.9.7 build without research_a197_postfill_protect=1 in PARAMS. The" >&2
    echo "       engine would report a1_9_7 while the post-fill tick stays blind." >&2
    exit 1
  }
  echo "[preflight] A1.9.7 post-fill protection / order-side identity / exit gap PASS"
fi

# A1.9.8.  Measured on the A1.9.7 run (log 20260913_185830, ticks 1-4,000): 26
# round trips were handed a resting maker exit priced at a loss at their first
# ABSOLUTE evaluation -- the A1.7.4 recovery maker or the A1.7.5 relative veto --
# instead of the taker.  None was positive, 24 still ended on a taker, and they
# carried 58% of all cubic downside.  Book 95 went from -40 to -110..-130 bps in
# the two ticks that maker exit held the book, three times in 38 ticks.
if [[ "$A198_BUILD" == "1" ]]; then
  [[ -f "$AGENT_PATH/research_direct_absolute_authority.py" ]] || {
    echo "ERROR: A1.9.8 build is missing research_direct_absolute_authority.py" >&2
    exit 1
  }

  # Both loss-recovery arms must be named, or the unnamed one still replaces the taker.
  grep -qF 'if reason == A198_RECOVERY_REASON:' "$AGENT_PATH/research_direct_absolute_authority.py" || {
    echo "ERROR: A1.9.8 does not override the A1.7.4 ABSOLUTE recovery maker." >&2
    exit 1
  }
  grep -qF 'if reason == A198_VETO_REASON and _token(decision, "corridor_action") == A198_RELATIVE_CORRIDOR:' "$AGENT_PATH/research_direct_absolute_authority.py" || {
    echo "ERROR: A1.9.8 does not override the A1.7.5 relative veto in ABSOLUTE." >&2
    exit 1
  }
  # HARD_ESCAPE and DEFENSIVE keep their recovery makers in this build.
  grep -qE '^[[:space:]]+if _token\(base_decision, "risk_band"\) != BAND_ABSOLUTE:$' "$AGENT_PATH/research_direct_absolute_authority.py" || {
    echo "ERROR: A1.9.8 taker authority is not limited to ABSOLUTE_PROTECTION." >&2
    exit 1
  }

  # Must match the CALL in the chooser: a module nobody calls restores nothing.
  grep -qF 'decision, a198_arm = restore_absolute_taker(' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.8 never restores the ABSOLUTE taker in the Direct chooser." >&2
    exit 1
  }

  [[ "$PARAMS" == *"research_a198_absolute_taker_authority=1"* ]] || {
    echo "ERROR: A1.9.8 build without research_a198_absolute_taker_authority=1 in PARAMS. The" >&2
    echo "       engine would report a1_9_8 while a loss-recovery maker still replaces the taker." >&2
    exit 1
  }
  echo "[preflight] A1.9.8 ABSOLUTE taker authority PASS"
fi

# A1.9.9.  Measured on the A1.9.8 run (log 20260914_012543, ticks 1-4,000).  Book 49
# reached ABSOLUTE and parked on 216 of its 218 evaluations because its published
# touch was crossed; it exited at -1,211 bps, 92.6% of all cubic downside.  At tick
# 2,467 the validator resumed from a checkpoint 36 s earlier; six books diverged from
# the venue by 1.2498 BASE and stayed diverged to tick 4,000.
if [[ "$A199_BUILD" == "1" ]]; then
  for module in research_direct_risk_state.py research_direct_session_epoch.py; do
    [[ -f "$AGENT_PATH/$module" ]] || {
      echo "ERROR: A1.9.9 build is missing $module" >&2
      exit 1
    }
  done

  # A pending ABSOLUTE exit must refuse WAIT, PARK and a loss maker -- not just name them.
  grep -qF 'if action in (ACTION_WAIT, ACTION_PARK_EXIT):' "$AGENT_PATH/research_direct_risk_state.py" || {
    echo "ERROR: A1.9.9 lets a pending ABSOLUTE exit WAIT or PARK." >&2
    exit 1
  }
  grep -qF 'if action == ACTION_MAKER_EXIT and maker + 1e-12 < float(grace_floor_bps):' "$AGENT_PATH/research_direct_risk_state.py" || {
    echo "ERROR: A1.9.9 lets a pending ABSOLUTE exit rest a maker priced at a loss." >&2
    exit 1
  }
  # Dust, a short quantity and an empty book side stay on the frozen PARK path.
  grep -qF 'executable = qty + 1e-12 >= floor and not bool(is_dust) and bool(touch_two_sided)' "$AGENT_PATH/research_direct_risk_state.py" || {
    echo "ERROR: A1.9.9 could send a taker for dust or into an empty book side." >&2
    exit 1
  }
  # Must match the CALL in the chooser: an authority nobody asks decides nothing.
  grep -qF 'decision, a199_rule, a198_arm = self._a199_authorize_exit(' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.9 exit authority is never called from the Direct chooser." >&2
    exit 1
  }

  # Session epoch: a same-simulation rewind is a boundary, seen before its trades are ingested.
  grep -qF 'if now < last:' "$AGENT_PATH/research_direct_session_epoch.py" || {
    echo "ERROR: A1.9.9 does not treat a simulation clock rewind as a session boundary." >&2
    exit 1
  }
  for call in 'self._a199_observe_epoch(state)' 'self._a199_service_resync(state)' \
              'self._a199_strip_resync_exposure(response)' 'if self._a199_entry_blocked(book_id):'; do
    grep -qF "$call" "$AGENT_PATH/Strategy1_Research_Simple.py" || {
      echo "ERROR: A1.9.9 session epoch is not wired: missing $call" >&2
      exit 1
    }
  done

  [[ "$PARAMS" == *"research_a199_exit_pending_authority=1"* ]] || {
    echo "ERROR: A1.9.9 build without research_a199_exit_pending_authority=1 in PARAMS. The" >&2
    echo "       engine would report a1_9_9 while an ABSOLUTE position can still park." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_a199_epoch_resync=1"* ]] || {
    echo "ERROR: A1.9.9 build without research_a199_epoch_resync=1 in PARAMS. A clock rewind" >&2
    echo "       would again leave the tracker trading positions the venue does not hold." >&2
    exit 1
  }
  echo "[preflight] A1.9.9 exit-pending authority / session epoch resync PASS"
fi

# A1.9.9.1.  Measured on the A1.9.9 run (log 20260914_083519, ticks 1-3,732).  A1.9.9
# let a pending ABSOLUTE position rest any maker at or above +1 bps.  Book 74 rested an
# A1.7.4.4 maker for 109 ticks and then for 129; a resting exit is not evaluated, so the
# A1.7.5 hold budget never ran out.  2 of the 14 pending episodes that rested a positive
# maker across A1.9.7-A1.9.9 ended positive.
if [[ "$A1991_BUILD" == "1" ]]; then
  # A pending position must refuse every maker exit, not only one priced at a loss.
  grep -qF 'if action == ACTION_MAKER_EXIT and not allow_positive_maker:' "$AGENT_PATH/research_direct_risk_state.py" || {
    echo "ERROR: A1.9.9.1 lets a pending ABSOLUTE exit rest a positive maker." >&2
    exit 1
  }
  # Must match the CALL: a rule the chooser never switches on refuses nothing.
  grep -qF 'allow_positive_maker=not bool(getattr(self, "research_a1991_pending_owns_book", True)),' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.9.1 pending ownership is never passed to the exit authority." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_a1991_pending_owns_book=1"* ]] || {
    echo "ERROR: A1.9.9.1 build without research_a1991_pending_owns_book=1 in PARAMS. The" >&2
    echo "       engine would report a1_9_9_1 while a pending position can still rest a maker." >&2
    exit 1
  }
  echo "[preflight] A1.9.9.1 pending position owns its book PASS"
fi

# A1.9.9.2.  Measured on the A1.9.9.1 run (log 20260914_141324, ticks 1-8,778).  p95 was 173.3 ms
# over ticks 8,001-8,500.  A slow tick carries one excess of about the same size in whichever
# phase it lands (124 ms in full_predict, 107 ms in the screen, 98 ms in logging) and it lands by
# allocation, not by time: CPython's full collection.  Without it p95 is 54.9 ms.  Responses were
# at least 3,853 ms apart, so the pass runs in that gap.  Trading logic is unchanged; the 100 ms
# entry freshness gate and Score-EV's latency cost see a faster agent.
if [[ "$A1992_BUILD" == "1" ]]; then
  grep -qF 'set_threshold(saved[0], saved[1], A1992_FULL_THRESHOLD_OFF)' "$AGENT_PATH/research_direct_idle_gc.py" || {
    echo "ERROR: A1.9.9.2 never stops CPython's automatic full collection inside a request." >&2
    exit 1
  }
  # Without the scheduled pass the heap is only collected once the fallback gives up.
  grep -qF 'self._timer = loop.call_later(self.delay_s, self.collect_idle)' "$AGENT_PATH/research_direct_idle_gc.py" || {
    echo "ERROR: A1.9.9.2 never schedules the idle full pass." >&2
    exit 1
  }
  grep -qF 'collector.begin_request(loop)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: A1.9.9.2 idle collector never wraps a request." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_a1992_idle_gc=1"* ]] || {
    echo "ERROR: A1.9.9.2 build without research_a1992_idle_gc=1 in PARAMS. The" >&2
    echo "       engine would report a1_9_9_2 while full collections still run inside requests." >&2
    exit 1
  }
  echo "[preflight] A1.9.9.2 idle-gap garbage collection PASS"
fi

# v5.0.0.  Telemetry only: a round-trip ledger fed by the rows the strategy already writes, and a
# local copy of the validator's score.  Replaying the A1.9.9.1 log (20260914_141324) through the
# ledger gives back its 810 round trips and 79.60 of realized PnL.  The validator stores a bucket
# for every round, traded or not, so a mirror that skips empty rounds puts Kappa-3 on another scale.
if [[ "$V500_BUILD" == "1" ]]; then
  grep -qF 'analytics.observe(event_type, payload)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.0 analytics never read the rows the strategy writes." >&2
    exit 1
  }
  grep -qF 'self._v500_service(state)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.0 analytics are never flushed; no V500_RT or V500_SCORE row would be written." >&2
    exit 1
  }
  grep -qF '    for ts in rounds:' "$AGENT_PATH/research_v5_score_mirror.py" || {
    echo "ERROR: v5.0.0 score mirror does not count every round; its Kappa-3 is not the validator's." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_v500_analytics=1"* ]] || {
    echo "ERROR: v5.0.0 build without research_v500_analytics=1 in PARAMS. The engine would" >&2
    echo "       report v5_0_0 while writing none of its analytics." >&2
    exit 1
  }
  echo "[preflight] v5.0.0 analytics / validator score mirror PASS"
fi

# v5.0.1.  The validator scores a Kappa-eligible book at its normalized Kappa-3 times an activity
# factor that starts at 0.0 and becomes 1.0 only with a round trip once the uid's Kappa gate opens
# (reward.py, default flags).  Replaying UID 18's RealNet log (20260915_063311) that way gives both
# activity means the dashboard showed (0.2656, 0.2969) and a Kappa-3 score of 0 to tick 8,489.  The
# fast screen and the rank now treat an eligible book the validator has not activated as one round
# trip away, as they treat a one-away book.
if [[ "$V501_BUILD" == "1" ]]; then
  grep -qF 'remaining, qualified = 1, False' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.1 fast screen still counts a book the validator scores 0.0 as qualified." >&2
    exit 1
  }
  grep -qF 'activation_value, score_state = self._v501_activation_value(bid, remaining)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.1 rank never values activating a cold book." >&2
    exit 1
  }
  grep -qF 'return (int(gate_ts) // int(sampling_ns)) * int(sampling_ns) - int(sampling_ns)' "$AGENT_PATH/research_v5_activity.py" || {
    echo "ERROR: v5.0.1 activation floor is not the validator's: the first pass after the gate" >&2
    echo "       counts a round trip in the bucket before the gate's bucket." >&2
    exit 1
  }
  grep -qF 'weighted[book] = weighted_kappa(norm, factor)' "$AGENT_PATH/research_v5_score_mirror.py" || {
    echo "ERROR: v5.0.1 score mirror ignores the activity factor; V500_SCORE would repeat v5.0.0's error." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_v501_activity_alignment=1"* ]] || {
    echo "ERROR: v5.0.1 build without research_v501_activity_alignment=1 in PARAMS. The engine would" >&2
    echo "       report v5_0_1 while ranking cold books as v5.0.0 did." >&2
    exit 1
  }
  echo "[preflight] v5.0.1 validator activity alignment PASS"
fi

# v5.0.2.  UID 18 on RealNet (log 20260915_063311) stopped trading after tick 9,229: admission read
# zero slots because 3.54 BASE counted as dust against a 2.0 BASE dust class.  Half of it was full
# positions -- residue a flat lifecycle carried into the next entry (F1) and clips a unit or two short
# that the rewind reseed froze in the ledger (F2).  The compactor spent both its slots on books the
# loss floor refuses (F3), and an unfilled market exit held its book for four ticks (F4).
if [[ "$V502_BUILD" == "1" ]]; then
  grep -qF 'self._v502_settle_flat_residue(int(book_id))' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.2 F1 missing: a flat lifecycle's residue rides into the next entry and parks a full clip as dust." >&2
    exit 1
  }
  grep -qF 'fee_residue=residue.get(book_id, 0.0) + v502_residue.get(book_id, 0.0),' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.2 rewind reseed ignores the residue ledger; every settled book would read as diverged." >&2
    exit 1
  }
  grep -qF 'return plan(RESEED_CLIP, tracker_after=clip[0], ledger_delta=clip[1], px=price)' "$AGENT_PATH/research_direct_session_epoch.py" || {
    echo "ERROR: v5.0.2 F2 missing: the rewind reseed freezes a clip one unit short in the ledger." >&2
    exit 1
  }
  grep -qF 'clip = clip_split(net, min_order=floor, tolerance=clip_tolerance)' "$AGENT_PATH/research_direct_legacy_baseline.py" || {
    echo "ERROR: v5.0.2 F2 missing: the startup seed sends an inherited clip one unit short to the ledger." >&2
    exit 1
  }
  grep -qF 'self._v502_note_compaction_refusal(int(book_id))' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.2 F3 missing: a dust book the loss floor refuses keeps the compactor's slot." >&2
    exit 1
  }
  grep -qF 'self._v502_release_market_terminal()' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.2 F4 missing: an unfilled market exit holds its book until local expiry." >&2
    exit 1
  }
  for v502_switch in research_v502_flat_residue research_v502_clip_recognition research_v502_compactor_turn research_v502_market_terminal; do
    [[ "$PARAMS" == *"${v502_switch}=1"* ]] || {
      echo "ERROR: v5.0.2 build without ${v502_switch}=1 in PARAMS." >&2
      exit 1
    }
  done
  echo "[preflight] v5.0.2 dust liveness PASS"
fi

# v5.0.3.  Phase 0 + Phase 1 of the top-20 roadmap.
# G1, the newcomer quiet gate: reward.py seeds a uid's track-record standing at its FIRST NON-ZERO
# trading score, and trading = 0.79*kappa + 0.21*pnl.  The PnL leg turns non-zero once 41 books
# carry realized PnL -- tick 203 on UID 18's own run -- while kappa cannot be non-zero until the
# stored rounds span 5,400 sim-s.  Trading from registration therefore seeds the standing at ~0.0001
# and it crawls up at 1/k: UID 18's weight and emission were exactly 0 for its whole immunity
# window.  A newcomer now answers every request with no instructions until its kappa gate is open.
# G2, the state recorder: every book's trades carry both counterparties' uids, so the field is
# measurable rather than guessable.  It needs the lazy raw books to cost nothing.
# G3, the validator's FIFO: a partial close must prorate the fill's fee, or a maker rebate is
# counted twice.  That branch fired on 247 of 2,247 reducing fills (11.0%) and is worth about +114
# at the median fill fee, roughly half of the 212 by which this agent's ledger (+1,803) ran ahead
# of the validator's FIFO (+1,591); the rest of that gap is not yet explained.
if [[ "$V503_BUILD" == "1" ]]; then
  grep -qF 'quiet = self._v503_gate_response(state)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.3 G1 missing: the newcomer gate never runs, so the first scored round would" >&2
    echo "       seed the track-record standing on a PnL-only score, as v5.0.0 did on UID 18." >&2
    exit 1
  }
  grep -qF 'response = super().respond(state) if quiet is None else quiet' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.3 G1 missing: the frozen chain still runs while the gate holds, so orders" >&2
    echo "       would be placed and ownership reserved during the quiet window." >&2
    exit 1
  }
  grep -qF 'armed, reason = arm_decision(' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.3 G1 missing: the gate arms without checking for prior evidence, so it could" >&2
    echo "       hold an established miner that already has a track record." >&2
    exit 1
  }
  grep -qF 'reason = should_open(' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.3 G1 missing: the gate never opens on exposure, so a position could sit" >&2
    echo "       unmanaged through the quiet window." >&2
    exit 1
  }
  grep -qF 'close_fee = fee * remaining_qty * quantity_inv' "$AGENT_PATH/research_v5_validator_fifo.py" || {
    echo "ERROR: v5.0.3 G3 missing: the partial close does not prorate the fill fee, so a maker" >&2
    echo "       rebate is counted twice and realized PnL is overstated." >&2
    exit 1
  }
  grep -qF 'def _match_trade_fifo(self, book_id, is_buy, quantity, price, fee, timestamp):' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.3 G3 not wired: the agent still scores itself with the inherited matcher." >&2
    exit 1
  }
  grep -qF 'recorder.capture(' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.3 G2 not wired: the observatory records nothing." >&2
    exit 1
  }
  for v503_switch in research_v503_newcomer_gate research_v503_state_recorder \
                     research_v503_fifo_fee_exact research_v503_book_kappa_rows; do
    [[ "$PARAMS" == *"${v503_switch}=1"* ]] || {
      echo "ERROR: v5.0.3 build without ${v503_switch}=1 in PARAMS." >&2
      exit 1
    }
  done
  if [[ "$PARAMS" == *"research_v503_state_recorder=1"* ]]; then
    [[ "$PARAMS" == *"lazy_load=1"* ]] || {
      echo "ERROR: v5.0.3 G2 needs lazy_load=1: without the lazy raw books the recorder would have" >&2
      echo "       to parse 128 books on the request path, or record nothing at all." >&2
      exit 1
    }
  fi
  echo "[preflight] v5.0.3 newcomer gate + observatory PASS"
fi

# v5.0.4.  Registration identity and the observatory budget.
# H1: the session file was named after network, subnet and simulation, not the miner, so UID 125,
# launched from UID 18's tree on 2026-09-16, restored UID 18's evidence: the quiet gate never armed
# and the activity belief took UID 18's history start.  The file now carries the UID.
# H2: the validator starts a UID's history at registration and writes a round on every state; the
# agent can only infer that start.  HISTORY_ANCHOR states it:
#   auto         a fresh registration, or a restart that finds this UID's own session file;
#   established  a UID whose Kappa gate opened more than 3 sim-h ago (every restart of an
#                established miner, and every restart at a new simulation);
#   <sim>@<s>    the simulation id and sim second of the registration block, e.g.
#                20260913_0722@47405 for UID 125.  Declare LATER when unsure: an earlier value
#                opens the quiet gate early.
# H3: the recorder budget counted uncompressed JSON (5x the disk it protects); it now counts disk.
# H4: the score copy runs over every round the validator holds, not only the ones this process saw.
if [[ "$V504_BUILD" == "1" ]]; then
  if ! [[ "$HISTORY_ANCHOR" == "auto" || "$HISTORY_ANCHOR" == "established" \
          || "$HISTORY_ANCHOR" =~ ^[A-Za-z0-9_.-]+@[0-9]+(\.[0-9]+)?$ ]]; then
    echo "ERROR: v5.0.4 HISTORY_ANCHOR='${HISTORY_ANCHOR}' is not auto, established or <sim>@<seconds>." >&2
    exit 1
  fi
  if ! [[ "$LEGACY_SESSION" == "ignore" || "$LEGACY_SESSION" == "adopt" ]]; then
    echo "ERROR: v5.0.4 LEGACY_SESSION='${LEGACY_SESSION}' is not ignore or adopt." >&2
    exit 1
  fi
  if ! [[ "$RECORDER_MAX_MB" =~ ^[0-9]+$ ]]; then
    echo "ERROR: v5.0.4 RECORDER_MAX_MB='${RECORDER_MAX_MB}' is not a whole number of MB (0 = unlimited)." >&2
    exit 1
  fi
  grep -qF 'return uid_session_path(path, getattr(self, "uid", None))' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.4 H1 missing: the session file is still shared by every UID launched from this tree." >&2
    exit 1
  }
  grep -qF 'raw = self._v504_read_session(identity)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.4 H1 not wired: the session read does not check whose payload it restores." >&2
    exit 1
  }
  grep -qF 'belief.pin(pin.start_ns, pin.source)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.4 H2 not wired: a declared history start never reaches the activity belief." >&2
    exit 1
  }
  grep -qF 'self._v504_service(state)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.4 H2/H4 not wired: the declared start is not pinned before the quiet gate." >&2
    exit 1
  }
  grep -qF 'spent = self.disk_bytes if self.budget_basis == BUDGET_DISK else self.bytes_written' "$AGENT_PATH/research_v5_observatory.py" || {
    echo "ERROR: v5.0.4 H3 missing: the recorder budget still counts uncompressed payload." >&2
    exit 1
  }
  grep -qF 'chosen = self._v504_mirror_inputs(now_ts)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v5.0.4 H4 not wired: the score copy still counts only this process's rounds." >&2
    exit 1
  }
  for v504_switch in research_v504_session_per_uid research_v504_disk_budget research_v504_mirror_rounds; do
    [[ "$PARAMS" == *"${v504_switch}=1"* ]] || {
      echo "ERROR: v5.0.4 build without ${v504_switch}=1 in PARAMS." >&2
      exit 1
    }
  done
  if [[ "$NETUID" == "79" && "$HISTORY_ANCHOR" == "auto" ]]; then
    echo "[preflight] v5.0.4 NOTE: mainnet with HISTORY_ANCHOR=auto.  Right for a fresh registration only;" >&2
    echo "            an established UID restarted without its own session file would be held quiet." >&2
    echo "            Restarting an established UID?  Pass --history_anchor established." >&2
  fi
  echo "[preflight] v5.0.4 registration identity + disk budget PASS (anchor=${HISTORY_ANCHOR} legacy=${LEGACY_SESSION} recorder_mb=${RECORDER_MAX_MB})"
fi

# v6.0.0.  Short lots.
# On UID 125 every position that stayed parked for hours was 0.19-0.24999 BASE: off-grid entry fills,
# short entry fills and partial exits.  The build loop skipped anything under the minimum order before
# exit handling, so they lost their exits and their protection.  A position between the boundary
# (SHORT_LOT_FRACTION x minimum order) and the minimum order is now exited like a full lot; the exit
# clip is the minimum order, which is legal because it leaves a strictly smaller opposite leftover.
# INHERITED_SHORT_LOTS=park keeps every single lot a restart inherits (the seed prices it at today's
# quote, so its real loss is invisible) parked until its position changes; exit lets it trade like any lot.
if [[ "$V600_BUILD" == "1" ]]; then
  if ! python3 -c 'import sys; f=float(sys.argv[1]); sys.exit(0 if 0.5 < f < 1.0 else 1)' "$SHORT_LOT_FRACTION" 2>/dev/null; then
    echo "ERROR: v6.0.0 SHORT_LOT_FRACTION='${SHORT_LOT_FRACTION}' is not a number above 0.5 and below 1.0." >&2
    exit 1
  fi
  if ! [[ "$INHERITED_SHORT_LOTS" == "park" || "$INHERITED_SHORT_LOTS" == "exit" ]]; then
    echo "ERROR: v6.0.0 INHERITED_SHORT_LOTS='${INHERITED_SHORT_LOTS}' is not park or exit." >&2
    exit 1
  fi
  grep -qF 'dust_skip = self._v600_skip_management(book_id, qty_abs, eps=eps, min_order=min_size_local)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.0.0 S1 missing: the build loop still skips every position under the minimum order." >&2
    exit 1
  }
  grep -qF 'is_dust = bool(has_inv and self._v600_counts_as_dust(bid, qty, eps=eps, min_order=min_size))' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.0.0 S1 not wired: admission does not use the short-lot predicate." >&2
    exit 1
  }
  grep -qF 'and not self._v600_counts_as_dust(bid, abs(float(net)), eps=eps, min_order=min_size)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.0.0 S1 not wired: the live validator does not count short lots as active books." >&2
    exit 1
  }
  grep -qF 'inventory_qty = self._v600_executable_qty(inventory_qty, exit_kwargs.get("min_order", 0.25))' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.0.0 S1 not wired: the position risk state never sees a short lot as executable." >&2
    exit 1
  }
  grep -qF 'self._v600_chooser_kwargs(exit_kwargs)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.0.0 S1 not wired: the exit chooser still judges a short lot as unexecutable." >&2
    exit 1
  }
  grep -qF 'self._v600_settle_leftover(int(book_id))' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.0.0 S1 not wired: a short-lot exit's leftover is not settled." >&2
    exit 1
  }
  grep -qF 'self._v600_note_inherited_clip(int(clip_book), float(min_order))' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.0.0 S1 not wired: rebuilt clips are not parked at startup." >&2
    exit 1
  }
  grep -qF 'self._v600_note_inherited_clip(int(inherited_book), abs(float(inherited_net)))' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.0.0 S1 not wired: inherited single lots are not parked at startup." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_v600_short_lots=1"* ]] || {
    echo "ERROR: v6.0.0 build without research_v600_short_lots=1 in PARAMS." >&2
    exit 1
  }
  echo "[preflight] v6.0.0 short lots PASS (fraction=${SHORT_LOT_FRACTION} inherited=${INHERITED_SHORT_LOTS})"
fi

# v6.0.1.  The dust recovery reserve (one active slot, one open book, one clip of BASE) is held
# only for dust the normalizer can work: under half a lot and not a parked inherited lot.  In the
# v6.0.0 testnet run five parked full lots held it in every admission row.
if [[ "$V601_BUILD" == "1" ]]; then
  [[ -f "$AGENT_PATH/research_v601_capacity.py" ]] || {
    echo "ERROR: v6.0.1 research_v601_capacity.py missing." >&2
    exit 1
  }
  grep -qF 'reserve_dust_now = int(self._v601_reserve_dust(diag, dust_now))' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.0.1 C1 missing: admission still reserves for every dust book." >&2
    exit 1
  }
  [[ "$(grep -cF 'dust_count=reserve_dust_now,' "$AGENT_PATH/Strategy1_Research_Simple.py")" == "4" ]] || {
    echo "ERROR: v6.0.1 C1 not wired: the reserve, the gate, its counterfactual and the admission row must all use the reserve count." >&2
    exit 1
  }
  grep -qF '"v601_workable_dust_inventory": int(workable_dust),' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.0.1 C1 not wired: the fast screen does not count workable dust." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_v601_workable_dust_reserve=1"* ]] || {
    echo "ERROR: v6.0.1 build without research_v601_workable_dust_reserve=1 in PARAMS." >&2
    exit 1
  }
  echo "[preflight] v6.0.1 workable-dust reserve PASS"
fi

# v6.0.2.  Active-book cap 6 -> 8.  On v6.0.1 (UID 68 testnet, ticks 0-2,622) every zero-slot
# admission row was ACTIVE-bound, 62% of rows with all 6 books held.  The frozen Research clamp
# allows 8, the total-open cap is already 8, and 8 x 0.25 BASE is exactly the 2.0 BASE cap, so no
# other limit moves.
if [[ "$V602_BUILD" == "1" ]]; then
  [[ "$MAX_ACTIVE_BOOKS" =~ ^[678]$ ]] || {
    echo "ERROR: v6.0.2 MAX_ACTIVE_BOOKS='${MAX_ACTIVE_BOOKS}' is not 6, 7 or 8." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_max_active_open_books=${MAX_ACTIVE_BOOKS} "* && "$PARAMS" == *"research_max_open_books=${MAX_ACTIVE_BOOKS} "* ]] || {
    echo "ERROR: v6.0.2 PARAMS does not carry the active-book cap ${MAX_ACTIVE_BOOKS}." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_max_total_open_books=8 research_max_total_abs_base=2.0"* ]] || {
    echo "ERROR: v6.0.2 must keep the total-open cap 8 and the 2.0 BASE cap." >&2
    exit 1
  }
  grep -qF 'min(8, int(getattr(' "$AGENT_PATH/Strategy1_Research.py" || {
    echo "ERROR: v6.0.2 expects the frozen Research active-book clamp of 8." >&2
    exit 1
  }
  grep -qF 'cap_max_active=int(terms.get("max_active", 0) or 0),' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.0.2 admission rows do not report the active-book cap." >&2
    exit 1
  }
  echo "[preflight] v6.0.2 active-book cap PASS (max_active_books=${MAX_ACTIVE_BOOKS})"
fi

# v6.0.3.  The A1.7.3 partial-fill recovery still used the pre-v6.0.0 dust rule, so a short lot
# (2/3 lot up to one lot) kept its recovery row after the bounded hold expired, and with no bound
# order every order on the book counted as conflicting: the v6.0.0 lot exit was cancelled at the
# next request.  On UID 68 (v6.0.2, ticks 0-5,123) that was 19 episodes, 121 cancels and 9 forced
# exits.  v6.0.3 releases the row and cancels nothing; dust keeps the v6.0.2 path exactly.
if [[ "$V603_BUILD" == "1" ]]; then
  [[ -f "$AGENT_PATH/research_v603_recovery.py" ]] || {
    echo "ERROR: v6.0.3 research_v603_recovery.py missing." >&2
    exit 1
  }
  grep -qF 'if disposition == V603_DISPOSITION_RELEASE:' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.0.3 D1 missing: the recovery handler has no release branch." >&2
    exit 1
  }
  grep -qF 'self._v603_note_release(' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.0.3 D1 not wired: releases are not counted or emitted." >&2
    exit 1
  }
  # The release must run before the order scan: a release that cancels is the defect itself.
  V603_REL_LINE="$(grep -nF 'if disposition == V603_DISPOSITION_RELEASE:' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1 | cut -d: -f1)"
  V603_CANCEL_LINE="$(grep -nF 'order_ids=conflicting_ids' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1 | cut -d: -f1)"
  [[ -n "$V603_REL_LINE" && -n "$V603_CANCEL_LINE" && "$V603_REL_LINE" -lt "$V603_CANCEL_LINE" ]] || {
    echo "ERROR: v6.0.3 D1 out of order: the release branch must precede the remainder cancel." >&2
    exit 1
  }
  grep -qF 'def _v603_telemetry' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.0.3 V603_STATE telemetry missing." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_v603_short_lot_release=1"* ]] || {
    echo "ERROR: v6.0.3 build without research_v603_short_lot_release=1 in PARAMS." >&2
    exit 1
  }
  echo "[preflight] v6.0.3 short-lot release PASS"
fi

# v6.1.  The A1.7.1 risk band reads a mid MTM that excludes fees, while the validator's FIFO
# charges BOTH legs' fees when a lot closes -- and on mainnet both are rebates.  On UID 34
# (v6.0.2, ticks 0-3,647) all 349 ABSOLUTE evaluations chose the taker while the agent's own
# fee-inclusive maker close read +120 bps median, and 148 of 149 of those takers realized a loss.
# v6.1 prices every close against the lot the validator would actually close: no market order
# leaves below its fee-inclusive break-even, a maker exit rests AT that break-even, and the
# A1.9.1 classifier judges it by the same arithmetic.
if [[ "$V610_BUILD" == "1" ]]; then
  [[ -f "$AGENT_PATH/research_v61_lot_floor.py" ]] || {
    echo "ERROR: v6.1 research_v61_lot_floor.py missing." >&2
    exit 1
  }
  grep -qF 'ok, detail = self._v61_taker_verdict(' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.1 executor choke point missing: takers are not priced against the FIFO lot." >&2
    exit 1
  }
  grep -qF 'decision, v61_rewrite = self._v61_rewrite_exit(' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.1 exit rewrite not wired into the chooser." >&2
    exit 1
  }
  grep -qF 'close_price, v61_net, v61_floored = self._v61_apply_floor(' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.1 placement floor missing: maker exits are not floored." >&2
    exit 1
  }
  # The refusal must precede the frozen market order, or the loss is already on the wire.
  V61_REFUSE_LINE="$(grep -nF 'ok, detail = self._v61_taker_verdict(' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1 | cut -d: -f1)"
  V61_SUPER_LINE="$(grep -nF 'placed = super()._execute_aggressive_close(' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1 | cut -d: -f1)"
  [[ -n "$V61_REFUSE_LINE" && -n "$V61_SUPER_LINE" && "$V61_REFUSE_LINE" -lt "$V61_SUPER_LINE" ]] || {
    echo "ERROR: v6.1 choke point out of order: the refusal must precede the market order." >&2
    exit 1
  }
  # The floor must be applied before the A1.9.1 classifier reads desired_price, or every
  # floored order is cancelled as STALE_BEHIND_TOUCH at the next request.
  V61_FLOOR_LINE="$(grep -nF 'close_price, v61_net, v61_floored = self._v61_apply_floor(' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1 | cut -d: -f1)"
  V61_DECIDE_LINE="$(grep -nF 'verdict = self._a191_decide(' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1 | cut -d: -f1)"
  [[ -n "$V61_FLOOR_LINE" && -n "$V61_DECIDE_LINE" && "$V61_FLOOR_LINE" -lt "$V61_DECIDE_LINE" ]] || {
    echo "ERROR: v6.1 floor applied after the A1.9.1 classifier." >&2
    exit 1
  }
  grep -qF 'def _v61_telemetry' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.1 V61_STATE telemetry missing." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_v61_no_loss=1"* ]] || {
    echo "ERROR: v6.1 build without research_v61_no_loss=1 in PARAMS." >&2
    exit 1
  }
  echo "[preflight] v6.1 no-loss FIFO floor PASS"
fi

# v6.1 state-gap repair.  From 09-18 06:35 the mainnet validator sent some states twice and skipped
# the next; the fills inside a skipped state were never reported, and by tick 8,075 UID 34 held
# 15.4 BASE on 39 books its tracker did not know about (the validator's own balances agree).  A
# forward gap in the state clock now opens the A1.9.9 resync for one pass against venue truth, and
# a book diverged by a lot at two consecutive checks goes to the A1.9.9 deferred reseed.
# v6.1 request memo (behaviour-neutral): the rolling-kappa refresh and the two realized-PnL scans in
# build_book_profile run once per request, not once per book (screen 7.4 -> 40.7 ms over a run).
if [[ "$V610_BUILD" == "1" ]]; then
  [[ -f "$AGENT_PATH/research_v61_state_gap.py" ]] || {
    echo "ERROR: v6.1 research_v61_state_gap.py missing." >&2
    exit 1
  }
  grep -qF 'self._v61_observe_state_step(state)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.1 state-gap observer not wired into update()." >&2
    exit 1
  }
  # The clock step must be read before A1.9.9 moves its own last-timestamp mark.
  V61_STEP_LINE="$(grep -nF 'self._v61_observe_state_step(state)' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1 | cut -d: -f1)"
  V61_EPOCH_LINE="$(grep -nF 'self._a199_observe_epoch(state)' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1 | cut -d: -f1)"
  [[ -n "$V61_STEP_LINE" && -n "$V61_EPOCH_LINE" && "$V61_STEP_LINE" -lt "$V61_EPOCH_LINE" ]] || {
    echo "ERROR: v6.1 state-gap observer must run before the A1.9.9 epoch observer." >&2
    exit 1
  }
  # The one-pass window must be open before the A1.9.9 resync is serviced, or it never runs.
  V61_ARM_LINE="$(grep -nF 'self._v61_service_gap_repair(state)' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1 | cut -d: -f1)"
  V61_SERVICE_LINE="$(grep -nF '            self._a199_service_resync(state)' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1 | cut -d: -f1)"
  [[ -n "$V61_ARM_LINE" && -n "$V61_SERVICE_LINE" && "$V61_ARM_LINE" -lt "$V61_SERVICE_LINE" ]] || {
    echo "ERROR: v6.1 state-gap repair must be armed before the A1.9.9 resync is serviced." >&2
    exit 1
  }
  grep -qF 'def _research_refresh_rolling_kappa_cache(self) -> None:' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.1 request memo missing: the kappa refresh still rescans the history per book." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_v61_state_gap_repair=1"* && "$PARAMS" == *"research_v61_request_memo=1"* ]] || {
    echo "ERROR: v6.1 build without research_v61_state_gap_repair=1 / research_v61_request_memo=1 in PARAMS." >&2
    exit 1
  }
  echo "[preflight] v6.1 state-gap repair PASS"
  echo "[preflight] v6.1 request memo PASS"
fi

# v6.1.1.  Two defects from the v6.1.0 testnet read (UID 82, ticks 1-1,014, 2026-09-18).
# (1) The A1.9.1.1 reprice seed judged a held lot's floored exit against the bare passive touch --
# ~1,100 ticks away by design -- and cancelled it one request after it was placed: 2,265 cancels.
# The seed now compares against the touch floored at the lot's break-even, the price the placement
# path itself sends.  (2) The venue truncates a price's binary expansion: 1,838 of 3,915 testnet
# and 1,804 of 3,573 mainnet placements landed one tick below the price sent.  Each outgoing limit
# price is lifted one ulp when its double sits below its decimal.
if [[ "$V611_BUILD" == "1" ]]; then
  [[ -f "$AGENT_PATH/research_v611_wire.py" ]] || {
    echo "ERROR: v6.1.1 research_v611_wire.py missing." >&2
    exit 1
  }
  grep -qF 'desired_price = self._v611_seed_comparand(' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.1.1 reprice seed still compares floored exits with the bare touch." >&2
    exit 1
  }
  # The comparand must be floored inside _a191_decide, before the classifier reads it.
  V611_SEED_LINE="$(grep -nF 'desired_price = self._v611_seed_comparand(' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1 | cut -d: -f1)"
  V611_CLASSIFY_LINE="$(grep -nF 'decision, reason = classify_resting_maker_exit(' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1 | cut -d: -f1)"
  [[ -n "$V611_SEED_LINE" && -n "$V611_CLASSIFY_LINE" && "$V611_SEED_LINE" -lt "$V611_CLASSIFY_LINE" ]] || {
    echo "ERROR: v6.1.1 seed comparand floored after the A1.9.1 classifier." >&2
    exit 1
  }
  grep -qF 'self._v611_lift_outgoing_prices(response, state)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.1.1 price lift not wired: half of all limit orders land one tick low." >&2
    exit 1
  }
  # The lift must see the final placement set: after the frozen chain has built it.
  V611_SUPER_LINE="$(grep -nF 'response = super().respond(state) if quiet is None else quiet' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1 | cut -d: -f1)"
  V611_LIFT_LINE="$(grep -nF 'self._v611_lift_outgoing_prices(response, state)' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1 | cut -d: -f1)"
  [[ -n "$V611_SUPER_LINE" && -n "$V611_LIFT_LINE" && "$V611_SUPER_LINE" -lt "$V611_LIFT_LINE" ]] || {
    echo "ERROR: v6.1.1 price lift runs before the placement set is built." >&2
    exit 1
  }
  grep -qF 'def _v611_telemetry' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.1.1 V611_STATE telemetry missing." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_v611_floor_reprice=1"* && "$PARAMS" == *"research_v611_price_lift=1"* ]] || {
    echo "ERROR: v6.1.1 build without research_v611_floor_reprice=1 / research_v611_price_lift=1 in PARAMS." >&2
    exit 1
  }
  echo "[preflight] v6.1.1 floor-aware reprice seed PASS"
  echo "[preflight] v6.1.1 price lift PASS"
fi

# v6.2.0 breadth.  The de-beta making term (validator 0.6.1 rung 2, live since 2026-09-18) is a sum
# over books of two-sided centred-mid capture, ranked among makers, and every (uid, book) is capped at
# 500k quote per 24 h -- so the lever is the number of books quoted, not lot size.  The engine quoted
# ~8 books (the ranker's top-20 admitted into 8 slots at 2.0 BASE): making 199.5, rank 0.07.  v6.2
# quotes a symmetric bid + ask at the touch on every valid flat book, one lot each, and derives the
# portfolio caps from the universe.  The exit path, the floors and the reprice rules are unchanged.
if [[ "$V620_BUILD" == "1" ]]; then
  [[ -f "$AGENT_PATH/research_v62_breadth.py" && -f "$AGENT_PATH/research_v62_making_mirror.py" ]] || {
    echo "ERROR: v6.2 research_v62_breadth.py / research_v62_making_mirror.py missing." >&2
    exit 1
  }
  grep -qF 'v62_placed = self._v62_acquire(response, state, stats)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2 acquisition pass not wired into build_mm_strategy_instructions." >&2
    exit 1
  }
  # The breadth pass must replace the ranked candidate loop, not run beside it: it sits before the
  # frozen loop's first line inside the same method.
  V62_ACQ_LINE="$(grep -nF 'v62_placed = self._v62_acquire(response, state, stats)' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1 | cut -d: -f1)"
  V62_FROZEN_LINE="$(grep -nF '# One flat-entry path. No maintenance branch and no separate alpha branch.' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1 | cut -d: -f1)"
  V62_VETO_LINE="$(grep -nF '# Only contract/risk safety may veto the already-decided actions here.' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1 | cut -d: -f1)"
  [[ -n "$V62_ACQ_LINE" && -n "$V62_FROZEN_LINE" && -n "$V62_VETO_LINE" && "$V62_ACQ_LINE" -lt "$V62_FROZEN_LINE" && "$V62_FROZEN_LINE" -lt "$V62_VETO_LINE" ]] || {
    echo "ERROR: v6.2 acquisition pass is not in place of the ranked candidate loop." >&2
    exit 1
  }
  grep -qF 'self._v62_apply_caps(state)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2 universe caps not applied: the 8-slot / 2.0 BASE model would still bind." >&2
    exit 1
  }
  grep -qF 'self._v62_feed_mirror(state)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2 making mirror not fed from update()." >&2
    exit 1
  }
  grep -qF 'def _v62_telemetry' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2 V62_STATE telemetry missing." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_v62_breadth=1"* ]] || {
    echo "ERROR: v6.2 build without research_v62_breadth=1 in PARAMS." >&2
    exit 1
  }
  echo "[preflight] v6.2 breadth PASS"
fi

if [[ "$V621_BUILD" == "1" ]]; then
  # v6.2.1: the fast-path screen's bound must be the universe at breadth, or the forced-inventory
  # list is truncated to the A1.6.1 clamp and every book beyond it holds a lot with no exit.
  grep -qF 'cap_override: int | None = None' "$AGENT_PATH/research_direct_fastpath.py" || {
    echo "ERROR: v6.2.1 select_fastpath_rows has no cap_override; forced inventory is still clamped at 24." >&2
    exit 1
  }
  grep -qF 'cap_override=v621_cap' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2.1 managed universe not passed to the fast-path screen." >&2
    exit 1
  }
  grep -qF 'def _v621_cap_override' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2.1 _v621_cap_override missing." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_v621_managed_universe=1"* ]] || {
    echo "ERROR: v6.2.1 build without research_v621_managed_universe=1 in PARAMS." >&2
    exit 1
  }
  echo "[preflight] v6.2.1 managed universe PASS"
fi

if [[ "$V622_BUILD" == "1" ]]; then
  # v6.2.2: the startup seed at breadth -- the universe's seed bound before the first seed, and the
  # session's own lots written instead of synthetic parked ones.
  grep -qF '"research_a195_max_seed_abs_base": 2.0 * float(n) * q' "$AGENT_PATH/research_v62_breadth.py" || {
    echo "ERROR: v6.2.2 universe caps carry no seed bound; the A1.9.5 24 BASE bound would leave books unseeded." >&2
    exit 1
  }
  grep -qF 'seed_lots, restored = self._v622_seed_lots(int(lot.book_id), side, lot)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2.2 startup seed does not consult the session's lots." >&2
    exit 1
  }
  grep -qF '# v6.2.2: the universe'"'"'s caps, including the seed bound, before the first seed (respond()).' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2.2 caps are not applied from update() before the first seed." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_v622_seed_at_breadth=1"* ]] || {
    echo "ERROR: v6.2.2 build without research_v622_seed_at_breadth=1 in PARAMS." >&2
    exit 1
  }
  echo "[preflight] v6.2.2 seed at breadth PASS"
fi

if [[ "$V623_BUILD" == "1" ]]; then
  # v6.2.3: the v6.1 floor binds only on a book in the Kappa-3 premium branch (>= 3 non-zero periods,
  # none below tau); everywhere else the maker exit keeps its own price.  Taker verdict stays strict.
  grep -qF 'KAPPA_MIN_REALIZED_OBSERVATIONS = 3' "$AGENT_PATH/research_v623_premium_floor.py" || {
    echo "ERROR: v6.2.3 premium test does not use the validator's min_realized_observations." >&2
    exit 1
  }
  grep -qF 'if self._v623_release(' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2.3 placement floor is not scoped to the premium branch." >&2
    exit 1
  }
  grep -qF 'floor_net_bps=self._v623_resting_floor_bps(bid, state),' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2.3 classifier would cancel every release as NET_BELOW_FLOOR." >&2
    exit 1
  }
  grep -qF 'and not self._v623_lifted(int(book_id), state)[0]' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2.3 negative-aggressive guard would block every aggressive release." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_v623_premium_floor=1"* ]] || {
    echo "ERROR: v6.2.3 build without research_v623_premium_floor=1 in PARAMS." >&2
    exit 1
  }
  echo "[preflight] v6.2.3 premium floor PASS"
fi

if [[ "$V624_BUILD" == "1" ]]; then
  # v6.2.4: a v6.2.3 release takes the exit order life -- the frozen persistence gate reads the lifted floor.
  grep -qF 'with self._v624_release_life(int(book_id), state):' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2.4 releases would go out with the base TTL." >&2
    exit 1
  }
  grep -qF 'self.research_profitable_exit_min_net_bps = float("-inf")' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2.4 does not lift the persistence threshold on a lifted book." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_v624_release_life=1"* ]] || {
    echo "ERROR: v6.2.4 build without research_v624_release_life=1 in PARAMS." >&2
    exit 1
  }
  echo "[preflight] v6.2.4 release life PASS"
fi

if [[ "$V625_BUILD" == "1" ]]; then
  # v6.2.5: two sides on every book (rule A) and the clip paced to the validator turnover cap (rule B).
  grep -qF 'sides = self._v625_sides(facts, clip=clip, flat_eps=eps, state=state, mid=mid)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2.5 acquisition does not ask for the per-side verdict." >&2
    exit 1
  }
  grep -qF 'if sides is not None and sides.get(side) != V625_REASON_OK:' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2.5 placement ignores the per-side verdict." >&2
    exit 1
  }
  grep -qF 'clip = self._v625_clip(book_id, state, facts, mid=mid)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2.5 quotes are not sized by the pacing controller." >&2
    exit 1
  }
  grep -qF 'PACE_PERIOD_NS = 86_400_000_000_000' "$AGENT_PATH/research_v625_cap_paced.py" || {
    echo "ERROR: v6.2.5 pace period is not the validator 24 sim-h assessment period." >&2
    exit 1
  }
  grep -qF 'BAND_CLIPS = 2.0' "$AGENT_PATH/research_v625_cap_paced.py" || {
    echo "ERROR: v6.2.5 inventory band is not one held clip plus one opposite." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_v625_two_sided=1"* ]] || {
    echo "ERROR: v6.2.5 build without research_v625_two_sided=1 in PARAMS." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_v625_cap_pace=1"* ]] || {
    echo "ERROR: v6.2.5 build without research_v625_cap_pace=1 in PARAMS." >&2
    exit 1
  }
  echo "[preflight] v6.2.5 cap-paced maker PASS"
fi

if [[ "$V626_BUILD" == "1" ]]; then
  # v6.2.6: clean closes (median loss budget), balanced capture, and the exit's quote life.
  grep -qF 'if v626_spend_allowed(' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2.6 releases do not consult the median loss budget." >&2
    exit 1
  }
  grep -qF 'side_qty = self._v626_side_clips(book_id, clip)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2.6 quotes are not sized by the capture balance." >&2
    exit 1
  }
  grep -qF 'if v626_side_already_instructed(response, int(book_id), direction):' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2.6 would still place a side this response already owns." >&2
    exit 1
  }
  grep -qF 'caps = v626_open_book_caps(v62_universe_caps(n, V625_BAND_CLIPS * lot))' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2.6 open-book caps are not widened for the guard double count." >&2
    exit 1
  }
  grep -qF 'return int(max(0.0, min(5000.0, persistent)) * 1_000_000)' "$AGENT_PATH/Strategy1_Research_Simple.py" || {
    echo "ERROR: v6.2.6 touch quotes do not take the persistent exit life." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_v626_loss_budget=1"* ]] || {
    echo "ERROR: v6.2.6 build without research_v626_loss_budget=1 in PARAMS." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_v626_capture_balance=1"* ]] || {
    echo "ERROR: v6.2.6 build without research_v626_capture_balance=1 in PARAMS." >&2
    exit 1
  }
  [[ "$PARAMS" == *"research_v626_quote_life=1"* ]] || {
    echo "ERROR: v6.2.6 build without research_v626_quote_life=1 in PARAMS." >&2
    exit 1
  }
  echo "[preflight] v6.2.6 balanced maker PASS"
fi

if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then
  python -m py_compile "$AGENT_PATH/Strategy1_Research_Simple.py"
  # The gate runs through tests/run_tests.py, which uses pytest when it is importable and the
  # bundled stub otherwise.  It used to call `python -m pytest` directly: this host has no pytest,
  # so under `set -e` the gate aborted before its PASS line and no test has gated a launch since
  # the v4.16 series.  The same 53 files, run this way, are green (2026-09-18).
  PYTHONPATH="$AGENT_PATH:$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    python "$SCRIPT_DIR/tests/run_tests.py" \
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
      tests/test_research_strategy1_direct_a1_9_1_1.py \
      tests/test_research_strategy1_direct_a1_9_1_2.py \
      tests/test_research_strategy1_direct_a1_9_2.py \
      tests/test_research_strategy1_direct_a1_9_2_1.py \
      tests/test_research_strategy1_direct_a1_9_4.py \
      tests/test_research_a1_9_5_reconcile.py \
      tests/test_research_a1_9_5_dust_capacity.py \
      tests/test_research_a1_9_5_taker_bound.py \
      tests/test_research_a1_9_5_inventory_truth.py \
      tests/test_research_a1_9_5_breadth_lane.py \
      tests/test_research_a1_9_6_legacy_baseline.py \
      tests/test_research_a1_9_6_1_venue_integrity.py \
      tests/test_research_a1_9_7_postfill_protection.py \
      tests/test_research_a1_9_8_absolute_authority.py \
      tests/test_research_a1_9_9_controllers.py \
      tests/test_research_a1_9_9_1_pending_owner.py \
      tests/test_research_a1_9_9_2_idle_gc.py \
      tests/test_research_v5_0_0_analytics.py \
      tests/test_research_v5_0_1_activity.py \
      tests/test_research_v5_0_2_dust_liveness.py \
      tests/test_research_v5_0_3_newcomer_observatory.py \
      tests/test_research_v5_0_4_registration_identity.py \
      tests/test_research_v6_0_0_short_lots.py \
      tests/test_research_v6_0_1_capacity.py \
      tests/test_research_v6_0_2_active_cap.py \
      tests/test_research_v6_0_3_short_lot_release.py \
      tests/test_research_v6_1_0_lot_floor.py \
      tests/test_research_v6_1_0_no_loss.py \
      tests/test_research_v6_1_0_state_gap.py \
      tests/test_research_v6_1_0_request_memo.py \
      tests/test_research_v6_1_1_wire.py \
      tests/test_research_v6_2_0_breadth.py \
      tests/test_research_v6_2_0_making_mirror.py \
      tests/test_research_v6_2_1_managed_universe.py \
      tests/test_research_v6_2_2_seed_at_breadth.py \
      tests/test_research_v6_2_3_premium_floor.py \
      tests/test_research_v6_2_4_release_life.py \
      tests/test_research_v6_2_5_cap_paced.py \
      tests/test_research_v6_2_6_balanced_maker.py \
      tests/test_version_pins.py \
      tests/test_preflight_gate.py \
      tests/test_wiring_integrity.py \
      tests/test_research_v4_16_2_economics_contract.py \
      tests/test_research_v4_16_1_p0_runtime.py \
      tests/test_research_v4_16_0_simplified_authority.py
  echo "Strategy1 direct V4.16.2 ${POLICY_VER} preflight PASS"
  exit 0
fi

echo "[Strategy1_Research_Simple] version=${POLICY_VER}"
echo "[Strategy1_Research_Simple] pm2_name=$PM2_NAME netuid=$NETUID axon_port=$AXON_PORT"
echo "[Strategy1_Research_Simple] history_anchor=$HISTORY_ANCHOR (from $HISTORY_ANCHOR_SOURCE)"
echo "[Strategy1_Research_Simple] max_active_books=$MAX_ACTIVE_BOOKS"
echo "[Strategy1_Research_Simple] log_dir=$RESEARCH_DIR"

# Keep the strategy directory importable in the actual PM2/miner process, not
# only in the preflight subprocess.
export PYTHONPATH="$AGENT_PATH:$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"

exec "$SCRIPT_DIR/run_miner_multi.sh" \
  -i "$PM2_NAME" -e "$ENDPOINT" -w "$WALLET_NAME" -h "$HOTKEY_NAME" -u "$NETUID" -a "$AXON_PORT" \
  -g "$AGENT_PATH" -n Strategy1_Research_Simple -m "$PARAMS" "${EXTRA[@]}"
