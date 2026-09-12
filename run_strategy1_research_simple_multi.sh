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
# A1.9.5 ships as its own policy version.  The A1.9.2 / A1.9.2.1 / A1.9.3 /
# A1.9.4 guards below still apply to it -- those invariants are cumulative, not
# per-revision -- so they gate on A19X_BUILD rather than on one literal.
A19X_BUILD=0; A195_BUILD=0
case "$POLICY_VER" in
  strategy1_direct_v4_16_2_a1_9_4) A19X_BUILD=1 ;;
  strategy1_direct_v4_16_2_a1_9_5) A19X_BUILD=1; A195_BUILD=1 ;;
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
research_a195_breadth_lane_enabled=1"

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
      tests/test_research_v4_16_2_economics_contract.py \
      tests/test_research_v4_16_1_p0_runtime.py \
      tests/test_research_v4_16_0_simplified_authority.py
  echo "Strategy1 direct V4.16.2 A1.9.4 rebate conjunction preflight PASS"
  exit 0
fi

echo "[Strategy1_Research_Simple] version=strategy1_direct_v4_16_2_a1_9_4"
echo "[Strategy1_Research_Simple] pm2_name=$PM2_NAME netuid=$NETUID axon_port=$AXON_PORT"
echo "[Strategy1_Research_Simple] log_dir=$RESEARCH_DIR"

# Keep the strategy directory importable in the actual PM2/miner process, not
# only in the preflight subprocess.
export PYTHONPATH="$AGENT_PATH:$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"

exec "$SCRIPT_DIR/run_miner_multi.sh" \
  -i "$PM2_NAME" -e "$ENDPOINT" -w "$WALLET_NAME" -h "$HOTKEY_NAME" -u "$NETUID" -a "$AXON_PORT" \
  -g "$AGENT_PATH" -n Strategy1_Research_Simple -m "$PARAMS" "${EXTRA[@]}"
