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
POLICY_VER="$(sed -n 's/^SIMPLE_POLICY_VERSION = "\(.*\)"$/\1/p' "$AGENT_PATH/Strategy1_Research_Simple.py" | head -1)"
# A1.9.5 and A1.9.6 ship as their own policy versions.  The A1.9.2 / A1.9.2.1 /
# A1.9.3 / A1.9.4 guards below still apply to both -- those invariants are
# cumulative, not per-revision -- so they gate on A19X_BUILD rather than on one
# literal, and A1.9.6 keeps every A1.9.5 guard by setting A195_BUILD as well.
A19X_BUILD=0; A195_BUILD=0; A196_BUILD=0; A1961_BUILD=0; A197_BUILD=0; A198_BUILD=0; A199_BUILD=0; A1991_BUILD=0; A1992_BUILD=0; V500_BUILD=0; V501_BUILD=0
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
research_v501_activity_alignment=1"

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
      tests/test_research_v4_16_2_economics_contract.py \
      tests/test_research_v4_16_1_p0_runtime.py \
      tests/test_research_v4_16_0_simplified_authority.py
  echo "Strategy1 direct V4.16.2 ${POLICY_VER} preflight PASS"
  exit 0
fi

echo "[Strategy1_Research_Simple] version=${POLICY_VER}"
echo "[Strategy1_Research_Simple] pm2_name=$PM2_NAME netuid=$NETUID axon_port=$AXON_PORT"
echo "[Strategy1_Research_Simple] log_dir=$RESEARCH_DIR"

# Keep the strategy directory importable in the actual PM2/miner process, not
# only in the preflight subprocess.
export PYTHONPATH="$AGENT_PATH:$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"

exec "$SCRIPT_DIR/run_miner_multi.sh" \
  -i "$PM2_NAME" -e "$ENDPOINT" -w "$WALLET_NAME" -h "$HOTKEY_NAME" -u "$NETUID" -a "$AXON_PORT" \
  -g "$AGENT_PATH" -n Strategy1_Research_Simple -m "$PARAMS" "${EXTRA[@]}"
