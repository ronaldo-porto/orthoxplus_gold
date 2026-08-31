#!/usr/bin/env bash
set -euo pipefail

# Resolve the repository before running any preflight.  The same launcher is
# Resolve the active repository root; legacy Research snapshot launchers were removed in V4.15.1.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/run_miner_multi.sh" ]]; then
  REPO_ROOT="$SCRIPT_DIR"
elif [[ -f "$SCRIPT_DIR/../../../run_miner_multi.sh" ]]; then
  REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
else
  echo "ERROR: cannot resolve repository root from $SCRIPT_DIR" >&2
  exit 1
fi
cd "$REPO_ROOT"

# V4.15.1 single launcher configuration. Resolve arguments before any Python
# preflight so every check imports the exact strategy tree that will be run.
WALLET_NAME="${WALLET_NAME:-taos}"
HOTKEY_NAME="${HOTKEY_NAME:-miner}"
ENDPOINT="${ENDPOINT:-wss://test.finney.opentensor.ai:443}"
NETUID="${NETUID:-366}"
AXON_PORT="${AXON_PORT:-8091}"
AGENT_PATH="${AGENT_PATH:-$REPO_ROOT/agents/strategy}"
PM2_NAME="${PM2_NAME:-sn79-m1}"
RESEARCH_EVERY_N="${RESEARCH_EVERY_N:-10}"
RESEARCH_BOOK="${RESEARCH_BOOK:--1}"
RESEARCH_JSONL="${RESEARCH_JSONL:-1}"
RESEARCH_CONSOLE="${RESEARCH_CONSOLE:-1}"
RESEARCH_QUEUE="${RESEARCH_QUEUE:-65536}"
RESEARCH_DIR="${RESEARCH_DIR:-$REPO_ROOT/logs/m1_strategy1_research}"

EXTRA=()
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

[[ -f "$REPO_ROOT/run_miner_multi.sh" ]] || { echo "run_miner_multi.sh missing" >&2; exit 1; }
[[ -f "$AGENT_PATH/Strategy1_Research.py" ]] || { echo "Strategy1_Research.py missing" >&2; exit 1; }
grep -q 'RESEARCH_POLICY_VERSION = "lean_authority_cleanup_v4_15_1"' "$AGENT_PATH/Strategy1_Research.py" || {
  echo "ERROR: Strategy1_Research.py is not lean_authority_cleanup_v4_15_1" >&2
  exit 1
}
for required in \
  research_execution_lanes.py research_total_score_frontier.py research_clean_authority.py \
  research_entry_size.py research_quote_hysteresis.py research_realnet_exit_authority.py \
  research_scheduler_retry.py research_unified_exit.py; do
  [[ -f "$AGENT_PATH/$required" ]] || { echo "ERROR: missing $AGENT_PATH/$required" >&2; exit 1; }
done

# V4.12.18 deployment compatibility checks.
# Structural AST check avoids false incompatibility from harmless annotation or
# formatting differences while still requiring RealizationDecision.unified_exit.
python - <<'PYV41212REALIZATION'
import ast
from pathlib import Path

path = Path("agents/strategy/research_realization.py")
if not path.is_file():
    raise SystemExit(
        "ERROR: missing agents/strategy/research_realization.py required by V4.12.3"
    )

tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
realization = next(
    (node for node in tree.body
     if isinstance(node, ast.ClassDef) and node.name == "RealizationDecision"),
    None,
)
if realization is None:
    raise SystemExit(
        "ERROR: stale/incompatible research_realization.py: missing RealizationDecision"
    )

has_unified_exit = any(
    isinstance(item, ast.AnnAssign)
    and isinstance(item.target, ast.Name)
    and item.target.id == "unified_exit"
    for item in realization.body
)
if not has_unified_exit:
    raise SystemExit(
        "ERROR: stale/incompatible research_realization.py: "
        "V4.12.3 requires RealizationDecision.unified_exit"
    )

print("V4.12.3 RealizationDecision.unified_exit API OK")
PYV41212REALIZATION
if [ ! -f agents/strategy/research_unified_exit.py ]; then
  echo "ERROR: missing agents/strategy/research_unified_exit.py required by V4.12.3" >&2
  exit 1
fi
if ! grep -q 'UNIFIED_EXIT_VERSION = "bounded_stale_bridge_v4_12_10"' agents/strategy/research_unified_exit.py; then
  echo "ERROR: stale/incompatible research_unified_exit.py: V4.12.10 bounded stale bridge required" >&2
  exit 1
fi
PYTHONPATH="agents/strategy${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV41210BRIDGE'
from research_unified_exit import bounded_stale_direct_bridge
common = dict(
    legacy_direct_authorized=True,
    positive_ev_authorized=True,
    sn79_take=True,
    legacy_taker_ev_bps=0.01,
    legacy_wait_ev_bps=-20.0,
    p_maker_fill=0.01,
    maker_fill_evidence=True,
    failed_exit_count=20,
    inventory_age=30.0,
    min_failed_exits=8,
    min_age_ticks=16.0,
    max_maker_fill=0.08,
    ev_advantage_bps=0.50,
    roundtrip_loss_floor_bps=-12.0,
)
if not bounded_stale_direct_bridge(actual_roundtrip_taker_net_bps=-5.2, **common):
    raise SystemExit("ERROR: V4.12.10 bounded stale bridge positive contract failed")
if bounded_stale_direct_bridge(actual_roundtrip_taker_net_bps=-12.01, **common):
    raise SystemExit("ERROR: V4.12.10 bounded stale bridge hard floor failed")
if bounded_stale_direct_bridge(actual_roundtrip_taker_net_bps=-15.0, roundtrip_loss_floor_bps=-30.0, **{k:v for k,v in common.items() if k != 'roundtrip_loss_floor_bps'}):
    raise SystemExit("ERROR: V4.12.10 bridge floor widening guard failed")
print("V4.12.10 bounded stale bridge API OK")
PYV41210BRIDGE

# V4.12.11 ONE_AWAY completion rescue guard.
PYTHONPATH="agents/strategy${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV41211ONEAWAY'
from research_entry_size import admit_minimum_order
from research_quote_hysteresis import (
    ONE_AWAY_STALE_TTL_VERSION,
    one_away_stale_completion_ttl,
)
if ONE_AWAY_STALE_TTL_VERSION != "one_away_stale_ttl_v4_12_11":
    raise SystemExit("ERROR: stale research_quote_hysteresis.py for V4.12.11")
d = admit_minimum_order(
    safe_size=0.04, min_order=0.25, trading_ev=0.05, inventory_risk=0.10,
    exit_capacity=0.055, volume_headroom=1.0, remaining_inventory=1.2,
    observations_remaining=1, enable_one_away_exact_min=True,
    one_away_min_safe_fraction=0.15, one_away_min_exit_fraction=0.20,
)
if not d.allow or abs(d.size - 0.25) > 1e-12 or d.trigger != "ONE_AWAY_EXACT_MIN":
    raise SystemExit("ERROR: V4.12.11 ONE_AWAY exact-min positive contract failed")
if admit_minimum_order(
    safe_size=0.03, min_order=0.25, trading_ev=0.05, inventory_risk=0.10,
    exit_capacity=0.055, volume_headroom=1.0, remaining_inventory=1.2,
    observations_remaining=1, enable_one_away_exact_min=True,
    one_away_min_safe_fraction=0.01, one_away_min_exit_fraction=0.01,
).allow:
    raise SystemExit("ERROR: V4.12.11 ONE_AWAY safe-size hard floor failed")
ttl, reason, used = one_away_stale_completion_ttl(
    chosen_ttl_ms=None, ttl_reason="STALE", completion_candidate=True,
    completion_samples=2, completion_target=3, trading_ev=0.05,
    market_regime="MIXED", min_ttl_ms=250.0,
)
if not used or ttl != 250.0 or reason != "ONE_AWAY_STALE_SHORT":
    raise SystemExit("ERROR: V4.12.11 ONE_AWAY stale TTL rescue contract failed")
ttl, _, used = one_away_stale_completion_ttl(
    chosen_ttl_ms=None, ttl_reason="STALE", completion_candidate=True,
    completion_samples=2, completion_target=3, trading_ev=0.05,
    market_regime="TOXIC", min_ttl_ms=250.0,
)
if used or ttl is not None:
    raise SystemExit("ERROR: V4.12.11 toxic TTL override guard failed")
print("V4.12.11 ONE_AWAY completion rescue API OK")
PYV41211ONEAWAY

# V4.12.14 authoritative-L1 pending-reprice post-only retry guard.
PYTHONPATH="agents/strategy${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV41214CONTRACT'
from research_contract_guard import (
    CONTRACT_GUARD_VERSION,
    HARD_LIFETIME_TICKS,
    guard_is_active,
    guard_should_skip,
    guarded_post_only_price,
    register_contract_reject,
    resolve_book_from_state_mapping,
)
if CONTRACT_GUARD_VERSION != "authoritative_l1_contract_guard_v4_12_14":
    raise SystemExit("ERROR: stale research_contract_guard.py for V4.12.14")
if HARD_LIFETIME_TICKS != 512:
    raise SystemExit("ERROR: V4.12.14 hard-lifetime contract changed")
s = register_contract_reject(None, current_tick=44)
if not guard_should_skip(s, current_tick=45):
    raise SystemExit("ERROR: V4.12.14 immediate retry cooldown failed")
if not guard_is_active(s, current_tick=77):
    raise SystemExit("ERROR: V4.12.14 pending state did not survive old 32-tick gap")
s2 = register_contract_reject(s, current_tick=77)
if s2.streak != 2 or s2.first_reject_tick != 44:
    raise SystemExit("ERROR: V4.12.14 reject streak reset across no-touch gap")
if not guard_should_skip(s2, current_tick=79) or guard_should_skip(s2, current_tick=80):
    raise SystemExit("ERROR: V4.12.14 bounded second-reject cooldown failed")
if guard_is_active(s, current_tick=44 + HARD_LIFETIME_TICKS + 1):
    raise SystemExit("ERROR: V4.12.14 hard lifetime did not expire")
p = guarded_post_only_price(
    side="sell", original_price=309.03, best_bid=309.02, best_ask=309.40,
    tick_size=0.01, reject_streak=2,
)
if p is None or abs(p - 309.42) > 1e-12:
    raise SystemExit("ERROR: V4.12.14 SELL fresh-touch safe reprice failed")
p = guarded_post_only_price(
    side="buy", original_price=309.39, best_bid=309.02, best_ask=309.40,
    tick_size=0.01, reject_streak=2,
)
if p is None or abs(p - 309.00) > 1e-12:
    raise SystemExit("ERROR: V4.12.14 BUY fresh-touch safe reprice failed")
from collections.abc import Mapping
class LazyBooksLike(Mapping):
    def __init__(self): self._d = {54: object()}
    def __getitem__(self, key): return self._d[key]
    def __iter__(self): return iter(self._d)
    def __len__(self): return len(self._d)
lazy_books = LazyBooksLike()
if isinstance(lazy_books, dict) or resolve_book_from_state_mapping(lazy_books, 54) is None:
    raise SystemExit("ERROR: V4.12.14 LazyBooks/Mapping L1 resolution failed")
print("V4.12.14 authoritative-L1 contract guard API OK")
PYV41214CONTRACT

# Lean V4.15.1 preflight keeps only current authority + protected risk contracts.
PYTHONPATH="$AGENT_PATH${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV4144REALNET'
from research_realnet_exit_authority import (
    ACTION_PARK, ACTION_TAKER_ESCAPE, REALNET_EXIT_AUTHORITY_VERSION, arbitrate_realnet_exit,
)
from research_scheduler_retry import SCHEDULER_RETRY_VERSION, SchedulerRetryGuard
if REALNET_EXIT_AUTHORITY_VERSION != "realnet_exit_authority_v4_14_4":
    raise SystemExit("ERROR: stale V4.14.4 RealNet exit-authority helper")
if SCHEDULER_RETRY_VERSION != "scheduler_retry_rotation_v4_14_4":
    raise SystemExit("ERROR: stale V4.14.4 scheduler retry helper")
hard = arbitrate_realnet_exit(
    taker_net_bps=-20.0, maker_net_bps=3.0, maker_executable=True,
    failed_exit_count=0, inventory_age=0, liveness_park=True, liveness_floor_bps=-12.0,
)
if hard.action != ACTION_TAKER_ESCAPE:
    raise SystemExit("ERROR: V4.14.4 -18..-25 hard exit authority contract failed")
park = arbitrate_realnet_exit(
    taker_net_bps=-25.01, maker_net_bps=-5.0, maker_executable=True,
    failed_exit_count=100, inventory_age=100, adverse_evidence=True,
)
if park.action != ACTION_PARK:
    raise SystemExit("ERROR: V4.14.4 absolute bounded-loss floor contract failed")
g = SchedulerRetryGuard(negative_ev_base_ticks=8, toxic_base_ticks=16, max_cooldown_ticks=64)
d = g.record_reject(7, tick=100, reason="NEGATIVE_EV", fingerprint=("NEGATIVE_EV", -3.0))
if not d.blocked or not g.should_skip(7, tick=101, fingerprint=("NEGATIVE_EV", -3.0)).blocked:
    raise SystemExit("ERROR: V4.14.4 scheduler quarantine contract failed")
g.reset()
if g.should_skip(7, tick=0, fingerprint=("NEGATIVE_EV", -3.0)).blocked:
    raise SystemExit("ERROR: V4.14.4 scheduler session reset contract failed")
print("V4.14.4 RealNet exit authority + scheduler retry rotation API OK")
PYV4144REALNET
PYTHONPATH="$AGENT_PATH${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV4145TOTALSCORE'
from research_execution_lanes import LaneBook, classify_execution_lane, LANE_COMPLETION, LANE_COVERAGE
from research_total_score_frontier import (
    TOTAL_SCORE_FRONTIER_VERSION, PHASE_IGNITION, PHASE_SURVIVAL, PHASE_FRONTIER,
    apply_total_score_frontier, phase_budget_tuple, scoring_pivot_indices,
)
if TOTAL_SCORE_FRONTIER_VERSION != "total_score_frontier_v4_15_1":
    raise SystemExit("ERROR: stale V4.15.1 total-score helper")
if phase_budget_tuple(PHASE_IGNITION) != (4, 3, 3, 1):
    raise SystemExit("ERROR: V4.15.1 ignition budgets changed")
if phase_budget_tuple(PHASE_SURVIVAL) != (2, 5, 3, 1):
    raise SystemExit("ERROR: V4.15.1 survival budgets changed")
if phase_budget_tuple(PHASE_FRONTIER) != (2, 4, 3, 1):
    raise SystemExit("ERROR: V4.15.1 frontier budgets changed")
if scoring_pivot_indices(41) != (0, 1) or scoring_pivot_indices(80) != (39, 40):
    raise SystemExit("ERROR: V4.15.1 score-pivot math changed")
rows = [
    LaneBook(book_id=1, rolling_observation_count=2, observations_remaining=1, kappa_eligible=False, economics_ok=True, completion_ev_ok=True),
    LaneBook(book_id=2, rolling_observation_count=3, observations_remaining=0, kappa_eligible=True, economics_ok=True, completion_ev_ok=True, raw_kappa=1.0),
    LaneBook(book_id=3, rolling_observation_count=0, observations_remaining=3, kappa_eligible=False, economics_ok=True, completion_ev_ok=True),
]
planned, plan = apply_total_score_frontier(rows, qualified_books=10)
by_id = {r.book_id: r for r in planned}
if plan.phase != PHASE_IGNITION:
    raise SystemExit("ERROR: V4.15.1 ignition phase failed")
if not by_id[1].total_score_due or classify_execution_lane(by_id[1]) != LANE_COMPLETION:
    raise SystemExit("ERROR: V4.15.1 ONE_AWAY must own completion priority")
if by_id[2].total_score_due or classify_execution_lane(by_id[2]) != LANE_COVERAGE:
    raise SystemExit("ERROR: V4.15.1 qualified book stole completion capacity during ignition")
if classify_execution_lane(by_id[3]) != LANE_COVERAGE:
    raise SystemExit("ERROR: V4.15.1 fresh book must remain coverage")
# Gate A item 3: overflow spilled into REALIZATION/COMPLETION must not consume
# COVERAGE's reserved slots once the global candidate cap truncates the lanes.
from research_execution_lanes import LANE_REALIZATION, normalize_lane_budgets, select_lane_candidates
cov, comp, real, over = phase_budget_tuple(PHASE_IGNITION)
budgets = normalize_lane_budgets(coverage_slots=cov, completion_slots=comp, realization_slots=real, shared_overflow_slots=over)
stress = [LaneBook(book_id=100 + i, has_inventory=True, exit_urgency=0.5) for i in range(5)]
stress += [LaneBook(book_id=200 + i, rolling_observation_count=2, observations_remaining=1, kappa_eligible=False, economics_ok=True, completion_ev_ok=True, maker_ev=1.0, maker_ev_known=True) for i in range(6)]
stress += [LaneBook(book_id=300 + i, rolling_observation_count=0, observations_remaining=3, kappa_eligible=False, economics_ok=True, completion_ev_ok=True, maker_ev=1.0, maker_ev_known=True, is_uncovered=True) for i in range(40)]
stress, _ = apply_total_score_frontier(stress, qualified_books=10)
alloc = select_lane_candidates(stress, budgets, max_candidates=budgets.total_cap)
if alloc.used[LANE_COVERAGE] != cov:
    raise SystemExit("ERROR: V4.15.1 IGNITION coverage reserve collapsed under the candidate cap")
print("V4.15.1 TOTAL_SCORE_FRONTIER API OK")
PYV4145TOTALSCORE

PYTHONPATH="$AGENT_PATH${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV415CLEAN'
from research_clean_authority import (
    CLEAN_AUTHORITY_VERSION, execution_reject_cooldown,
    posterior_taker_exit_probability,
)
from research_execution_lanes import (
    LANE_COMPLETION, LaneBook, authoritative_execution_lane,
    normalize_lane_budgets, select_lane_candidates,
)
from research_entry_size import (
    TOTAL_SCORE_FRONTIER_EXACT_MIN_VERSION, admit_minimum_order,
)
from research_quote_hysteresis import (
    TOTAL_SCORE_STALE_TTL_VERSION, total_score_stale_completion_ttl,
)
if CLEAN_AUTHORITY_VERSION != "clean_authority_v4_15_1":
    raise SystemExit("ERROR: stale V4.15 clean-authority helper")
if TOTAL_SCORE_FRONTIER_EXACT_MIN_VERSION != "total_score_frontier_exact_min_v4_15_1":
    raise SystemExit("ERROR: stale V4.15.1 TOTAL_SCORE exact-min helper")
if TOTAL_SCORE_STALE_TTL_VERSION != "total_score_velocity_stale_ttl_v4_15_1":
    raise SystemExit("ERROR: stale V4.15.1 TOTAL_SCORE stale-TTL helper")
if not execution_reject_cooldown({"tick": 10, "reason": "ZERO_ORDER_SIZE"}, tick=11).blocked:
    raise SystemExit("ERROR: V4.15 mechanical executability cooldown failed")
if execution_reject_cooldown({"tick": 10, "reason": "TOXIC"}, tick=11).blocked:
    raise SystemExit("ERROR: V4.15 hard-risk/quarantine reason leaked into mechanical cooldown")
p = posterior_taker_exit_probability(
    maker_exits=28, taker_exits=117, prior=0.30, prior_strength=8, min_samples=4, cap=0.90,
)
if not (0.75 < p < 0.82):
    raise SystemExit(f"ERROR: V4.15 adaptive lifecycle posterior failed: {p}")
rows = [LaneBook(book_id=i, observations_remaining=1, economics_ok=True, total_score_phase="IGNITION", total_score_due=True) for i in range(1, 5)]
budgets = normalize_lane_budgets(coverage_slots=0, completion_slots=1, realization_slots=0, shared_overflow_slots=0)
alloc = select_lane_candidates(rows, budgets, max_candidates=1)
if alloc.by_lane[LANE_COMPLETION] != [1] or alloc.pool_by_lane[LANE_COMPLETION][:3] != [1, 2, 3]:
    raise SystemExit("ERROR: V4.15 ordered backfill pool failed")
if authoritative_execution_lane(2, inventory_flat=True, allocation=alloc) != LANE_COMPLETION:
    raise SystemExit("ERROR: V4.15 reserve lane authorization failed")
exact = admit_minimum_order(
    safe_size=0.05, min_order=0.25, trading_ev=0.10, inventory_risk=0.10,
    exit_capacity=0.10, volume_headroom=1.0, remaining_inventory=1.0,
    observations_remaining=0, total_score_due=True,
    enable_total_score_frontier_exact_min=True,
    total_score_frontier_min_exit_fraction=0.20,
)
if not exact.allow or exact.trigger != "TOTAL_SCORE_FRONTIER_EXACT_MIN":
    raise SystemExit("ERROR: V4.15.1 TOTAL_SCORE exact-min privilege failed")
ttl, reason, used = total_score_stale_completion_ttl(
    chosen_ttl_ms=None, ttl_reason="STALE", completion_candidate=True,
    completion_samples=3, completion_target=3, total_score_due=True,
    trading_ev=0.10, market_regime="NORMAL", min_ttl_ms=250.0, stale_ttl_ms=250.0,
)
if not used or ttl != 250.0 or not reason.startswith("TOTAL_SCORE"):
    raise SystemExit("ERROR: V4.15.1 TOTAL_SCORE stale-TTL privilege failed")
print("V4.15.1 lean-authority + adaptive lifecycle + success-backfill API OK")
PYV415CLEAN
[[ -f "$REPO_ROOT/taos/im/validator/trade.py" ]] || { echo "validator trade.py missing" >&2; exit 1; }
grep -q 'Preserve EVERY timestamp' "$REPO_ROOT/taos/im/validator/trade.py" || {
  echo "ERROR: validator trade.py missing restart empty-timestamp preservation fix" >&2
  exit 1
}

PYTHONPATH="$AGENT_PATH${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV4141HISTORY'
from research_session_state import (
    VALIDATOR_HISTORY_ALIGNMENT_VERSION,
    rebase_observation_timestamps,
)
if VALIDATOR_HISTORY_ALIGNMENT_VERSION != "validator_history_alignment_v4_14_1":
    raise SystemExit("ERROR: V4.14.1 validator-history alignment helper missing")
probe = rebase_observation_timestamps(
    {7: [600, 800, 1000]}, old_ts=1000, new_ts=0, lookback_ns=500
)
if probe != {7: [-400, -200, 0]}:
    raise SystemExit(f"ERROR: V4.14.1 validator-history rebase mismatch: {probe}")
print("V4.14.1 validator history alignment API OK")
PYV4141HISTORY

# V4.15.1: legacy V4.12.4 qualified-CORE and V4.12.9 breadth-scheduler
# preflights were removed. Current exact-min/TTL privileges are verified below
# against the single TOTAL_SCORE authority.

# V4.12.10 deployment guard: require the latest unified-exit version, then
# preserve the V4.12.6 safety probes and matched V4.12.2 hazard API.
PYTHONPATH="$AGENT_PATH${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV4122'
import inspect
import research_fill_hazard
import research_unified_exit
if getattr(research_unified_exit, "UNIFIED_EXIT_VERSION", "") != "bounded_stale_bridge_v4_12_10":
    raise SystemExit(
        "ERROR: stale/incompatible research_unified_exit.py; "
        f"loaded={research_unified_exit.__file__}"
    )
# V4.12.6 semantic guard: preserve V4.12.5 EMERGENCY safety and verify early escape
# an unlimited-loss Taker. Only the explicit catastrophic/max-inventory flag
# may select HARD_RISK_TAKER.
_probe = research_unified_exit.choose_unified_exit(
    maker_net_bps=0.5, taker_net_bps=-56.0, wait_ev_bps=-8.0,
    maker_price=100.01, taker_price=99.4, breakeven_px=100.0,
    p_maker_fill=0.05, peak_taker_net_bps=0.5, failed_exit_count=30,
    inventory_age=50, observations_remaining=1, expected_markout_bps=-5.0,
    adverse_selection_risk=1.0, inventory_state="EMERGENCY",
    stop_loss_hit=True, hard_emergency=False, protective_loss_floor_bps=-2.0,
)
if _probe.action == research_unified_exit.ACTION_HARD_RISK_TAKER:
    raise SystemExit(
        "ERROR: stale/unsafe research_unified_exit.py: EMERGENCY-only still authorizes HARD_RISK_TAKER"
    )
# V4.12.6 early-escape guard: after a few failed Maker exits, a bounded
# protective Taker inside the unchanged -2 bps floor should execute when waiting
# is materially worse. The floor itself must still block -2.01 bps.
_early = research_unified_exit.choose_unified_exit(
    maker_net_bps=0.5, taker_net_bps=-1.2, wait_ev_bps=-5.0,
    maker_price=100.01, taker_price=99.98, breakeven_px=100.0,
    p_maker_fill=0.04, peak_taker_net_bps=1.0, failed_exit_count=3,
    inventory_age=4, observations_remaining=1, expected_markout_bps=0.0,
    adverse_selection_risk=0.0, inventory_state="NORMAL",
    stop_loss_hit=False, hard_emergency=False, protective_loss_floor_bps=-2.0,
    early_escape_enabled=True, early_escape_failed_exits=3,
    early_escape_min_age_ticks=5, early_escape_drawdown_bps=1.5,
    early_escape_floor_headroom_bps=0.75, early_escape_ev_advantage_bps=0.5,
)
if _early.action != research_unified_exit.ACTION_TAKER_PROTECT or not _early.early_escape_trigger:
    raise SystemExit(
        "ERROR: stale/incompatible research_unified_exit.py: V4.12.6 early escape probe failed"
    )
_floor = research_unified_exit.choose_unified_exit(
    maker_net_bps=0.5, taker_net_bps=-2.01, wait_ev_bps=-10.0,
    maker_price=100.01, taker_price=99.97, breakeven_px=100.0,
    p_maker_fill=0.01, peak_taker_net_bps=1.0, failed_exit_count=20,
    inventory_age=30, observations_remaining=1, expected_markout_bps=-5.0,
    adverse_selection_risk=1.0, inventory_state="EMERGENCY",
    stop_loss_hit=True, hard_emergency=False, protective_loss_floor_bps=-2.0,
)
if _floor.action != research_unified_exit.ACTION_KEEP_MAKER:
    raise SystemExit(
        "ERROR: unsafe V4.12.6 protective floor: loss below -2 bps was authorized"
    )
required = {
    "distance_decay_bps",
    "distance_near_boost",
    "distance_floor_mult",
    "fallback_policy_weight",
}
sig = inspect.signature(research_fill_hazard.FillHazardModel)
missing = sorted(required.difference(sig.parameters))
if missing:
    raise SystemExit(
        "ERROR: stale/incompatible research_fill_hazard.py for V4.12.2; "
        f"missing FillHazardModel args={missing}; loaded={research_fill_hazard.__file__}"
    )
print(f"V4.12.3 unified exit + FillHazard API OK: {research_unified_exit.__file__} | {research_fill_hazard.__file__}")
PYV4122

# Parent Debug still computes the decision records. Its synchronous JSONL is disabled;
# Strategy1_Research persists the events asynchronously instead.
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

# V4.3 Phase 5 hysteresis + adaptive TTL. Dust escape stays experimental/off.
# Hard dust/Kappa invariants and Phase 4 Score-EV ranking are unchanged.
PARAMS="enable_mm_strategy=1 lazy_load=1 \
fast_update=1 sync_event_csv=0 history_len=0 \
mm_base_size=0.25 max_inventory_base=1.20 inventory_close_threshold=0.25 \
max_mm_books_per_tick=6 max_managed_books_per_tick=10 \
min_expected_alpha=0.18 min_expected_realized_pnl=0.0 \
mm_expiry_period_ns=500000000 maintenance_size_mult=0.25 \
passive_exit_only=1 aggressive_close_min_ticks=300 position_max_ticks=300 \
mm_skip_inactive_tier=1 toxic_loss_streak=4 \
verbose_log=0 log_every_n=100 log_mm_strategy=0 log_direction=0 log_book_profile=0 \
log_regime=0 log_momentum_pnl=0 log_book_memory=0 \
debug_enabled=1 debug_every_n=${RESEARCH_EVERY_N} debug_jsonl=0 debug_book_id=${RESEARCH_BOOK} \
research_enabled=1 research_every_n=${RESEARCH_EVERY_N} research_book_id=${RESEARCH_BOOK} \
research_jsonl=${RESEARCH_JSONL} research_console=${RESEARCH_CONSOLE} research_compact_console=1 research_queue_size=${RESEARCH_QUEUE} \
research_fix_global_stress=1 research_neutral_fallback=1 \
research_adaptive_spread_thresholds=1 research_stress_percentile=0.95 research_toxic_percentile=0.99 \
research_stress_floor_bps=8.0 research_toxic_floor_bps=10.0 \
research_stress_fallback_bps=35.0 research_toxic_fallback_bps=40.0 research_toxic_gap_bps=2.0 \
research_inactive_bootstrap=1 research_trade_global_stress=1 research_global_stress_size_mult=0.35 \
research_sync_min_order=1 research_promote_min_order=1 research_bootstrap_maintenance_min_order=1 \
research_bootstrap_dead_as_mm=1 research_bootstrap_extreme_vol_mult=1.75 \
research_fix_inventory_util=1 research_fix_quote_reservation=1 \
research_bootstrap_manage_min_clip=1 research_bootstrap_allow_aggressive_close=1 \
research_bootstrap_force_close_ticks=60 research_dust_safe_close=1 research_rotate_jsonl=1 \
research_candidate_backfill=1 research_candidate_attempt_cap=12 \
research_aggressive_close_touch_gate=1 research_aggressive_close_fee_buffer_bps=3.0 \
research_aggressive_close_min_net_bps=0.0 research_toxic_pnl_min_samples=3 \
research_toxic_pnl_hard_floor=-0.05 research_yellow_sparse_active=1 \
research_green_sparse_active=1 research_dust_park_enabled=1 \
research_dust_heartbeat_ticks=250 research_dust_warn_ticks=1000 \
research_dust_compact_enabled=1 research_dust_compact_min_fraction=0.50 \
research_dust_compact_books_per_tick=2 research_dust_moderate_age_ticks=16 \
research_kappa_completion_enabled=1 \
research_kappa_completion_target=3 research_kappa_completion_rank_bonus=0.30 \
research_kappa_completion_attempt_cap=6 research_kappa_completion_success_cap=3 \
research_kappa_completion_fill_mult=0.70 research_kappa_completion_fill_floor=0.08 \
research_kappa_completion_relaxed_success_cap=3 research_kappa_completion_recent_pnl_floor=-0.01 \
research_actionable_fill_enabled=1 research_actionable_fill_min_samples=4 \
research_actionable_fill_prior_strength=6.0 research_actionable_fill_prior_actionable=0.85 \
research_actionable_fill_rank_weight=0.10 research_dust_risk_rank_penalty=0.18 \
research_dust_risk_target=0.15 research_kappa_one_away_bonus=0.10 \
research_partial_fill_hold_enabled=1 research_partial_fill_hold_min_dust_prob=0.12 \
research_partial_fill_hold_max_ns=750000000 \
research_force_mm_post_only=1 research_dust_compact_adaptive=1 \
research_dust_compact_cooldown_ticks=8 research_dust_compact_max_cooldown_ticks=40 \
research_dust_compact_prior_fill=0.02 research_dust_compact_prior_strength=8.0 \
research_enable_fill_hazard=1 \
research_completion_ev_cache_ticks=20 research_total_score_ignition_books=41 research_total_score_full_breadth_books=80 research_total_score_frontier_band=2 research_enable_score_velocity=1 research_score_velocity_weight=0.08 research_enable_quote_hysteresis=1 \
research_enable_adaptive_ttl=1 research_ttl_min_ms=250 research_ttl_max_ms=1500 research_quiet_ttl_ms=1000 research_quiet_exit_ttl_ms=950 research_one_away_exit_ttl_ms=975 \
research_enable_fast_candidate_screen=1 research_candidate_count=11 research_max_open_books=6 research_max_active_open_books=6 research_max_total_open_books=8 research_max_parked_open_books=4 research_max_total_abs_base=2.0 research_persistent_maker_enabled=1 research_hysteresis_min_price_ticks=3 research_post_only_safety_ticks=2 \
\
research_lifecycle_taker_exit_prob=0.30 research_lifecycle_slippage_bps=0.75 research_lifecycle_holding_bps=0.50 \
research_backfill_predict_reserve_per_lane=3 \
\
research_one_away_exact_min_enabled=1 research_one_away_exact_min_ev_bps=0.0 research_one_away_exact_min_safe_fraction=0.15 research_one_away_exact_min_exit_fraction=0.20 \
research_two_away_exact_min_enabled=1 research_two_away_exact_min_ev=0.0 research_two_away_exact_min_max_inventory_risk=0.35 research_two_away_exact_min_exit_fraction=0.20 research_two_away_exact_min_min_headroom=0.25 \
research_total_score_exact_min_enabled=1 research_total_score_exact_min_ev=0.0 research_total_score_exact_min_max_inventory_risk=0.35 research_total_score_exact_min_exit_fraction=0.20 research_total_score_exact_min_min_headroom=0.25 research_total_score_stale_ttl_enabled=1 research_total_score_stale_ttl_ms=250  research_profitable_exit_persistence_enabled=1 research_profitable_exit_ttl_ms=3000 research_profitable_exit_min_net_bps=0.0 research_profitable_exit_reprice_ticks=3 \
research_quote_width_floor_mult=0.80 research_enable_one_away_quiet_tightening=1 research_one_away_quiet_width_mult=0.60 research_one_away_quiet_min_ev=0.0 research_one_away_max_touch_bps=1.5 research_one_away_stale_ttl_ms=250 research_fill_distance_decay_bps=6.0 research_fill_distance_near_boost=1.35 research_fill_distance_floor_mult=0.10 research_fill_fallback_policy_weight=0.45 research_local_kappa_refresh_ticks=10 research_p95_target_ms=120 \
research_enable_inventory_state_v2=1 research_enable_exit_urgency_v2=1 \
research_enable_hybrid_realization_v2=1 research_enable_economic_taker=1 \
research_enable_precise_reduction_qty=1 research_enable_dust_economic_gate=1 \
research_enable_authoritative_kappa_state=1 research_enable_markout_v2=1 \
research_inventory_liveness_enabled=1 research_fresh_maker_grace_enabled=1 research_fresh_maker_grace_ticks=3 research_positive_maker_veto_enabled=1 research_positive_maker_veto_floor_bps=1.0 research_positive_maker_veto_max_failed_exits=4 research_liveness_maker_failed_exits=3 research_liveness_maker_min_age_ticks=8 research_liveness_maker_floor_bps=-4.0 research_liveness_taker_failed_exits=8 research_liveness_taker_min_age_ticks=16 research_liveness_hard_failed_exits=12 research_liveness_hard_min_age_ticks=24 research_protected_park_failed_exits=4 research_protected_park_min_age_ticks=8 research_liveness_soft_taker_floor_bps=-8.0 research_liveness_hard_taker_floor_bps=-12.0 research_bounded_loss_escape_enabled=1 research_bounded_loss_escape_min_age_ticks=2 research_bounded_loss_escape_floor_bps=-25.0 research_bounded_loss_escape_hard_trigger_bps=-18.0 research_bounded_loss_escape_drawdown_bps=2.0 research_liveness_min_ev_advantage_bps=0.50 research_liveness_adverse_markout_bps=1.0 research_liveness_adverse_risk_floor=0.25 research_parked_refresh_ticks=25 research_parked_touch_move_bps=8.0 research_enable_fill_hazard_exit_compare=1 research_enable_sn79_action_utility=1 research_enable_score_taker_direct=1 research_enable_economic_taker_direct=1 research_economic_direct_max_loss_bps=0.0 research_enable_aggressive_positive_ev_taker=1 research_aggressive_positive_ev_min_net_bps=0.0 research_aggressive_positive_ev_switch_margin_bps=0.50 research_aggressive_positive_ev_one_away_margin_bps=0.0 research_aggressive_positive_ev_failed_exit_count=8 research_aggressive_positive_ev_min_age_ticks=16 research_aggressive_positive_ev_max_maker_fill=0.08 research_aggressive_positive_ev_min_urgency=0.30 research_maker_escalate_failed_exit_count=8 research_one_away_maker_escalate_failed_exit_count=3 research_risk_direct_max_loss_bps=-10.0 research_failed_exit_penalty_bps=0.75 research_exit_age_penalty_bps_per_tick=0.03 research_cancel_before_taker=1 research_sn79_pnl_scale_bps=8.0 research_sn79_pnl_weight=1.0 research_sn79_round_trip_weight=0.30 research_sn79_kappa_weight=0.35 research_sn79_coverage_weight=0.15 research_sn79_capital_release_weight=0.15 research_sn79_risk_reduction_weight=0.20 research_sn79_velocity_weight=0.25 research_sn79_downside_weight=0.45 research_sn79_min_utility_margin=0.03 research_kappa_lookback_ns=10800000000000 research_kappa_expiry_warning_frac=0.20 research_kappa_expiry_rank_bonus=0.20 research_deadline_scheduler_enabled=1 research_deadline_critical_urgency=0.50 research_deadline_rank_bonus=0.25 research_score_target_books=80 research_stale_maker_rescue_enabled=1 research_stale_maker_rescue_failed_exits=4 research_stale_maker_rescue_critical_failed_exits=1 research_stale_maker_rescue_floor_bps=-1.0 research_entry_recheck_ticks=12 research_hybrid_partial_frac_cap=0.90 research_ladder_passive_max=0.15 research_ladder_competitive_max=0.30 research_ladder_aggressive_max=0.45 research_enable_unified_exit=1 research_unified_maker_net_floor_bps=0.0 research_unified_stale_bridge_roundtrip_floor_bps=-12.0 research_unified_profit_lock_min_bps=1.0 research_unified_profit_lock_drawdown_bps=2.0 research_unified_switch_margin_bps=0.50 research_enable_protective_taker=1 research_protective_taker_loss_floor_bps=-2.0 research_protective_taker_ev_advantage_bps=1.0 research_protective_taker_failed_exits=6 research_protective_taker_min_age_ticks=8 research_protective_taker_adverse_bps=2.0 research_early_escape_enabled=1 research_early_escape_failed_exits=3 research_early_escape_min_age_ticks=5 research_early_escape_drawdown_bps=1.5 research_early_escape_floor_headroom_bps=0.75 research_early_escape_ev_advantage_bps=0.50 research_session_save_every_n=100"

echo "[Strategy1_Research] pm2_name=$PM2_NAME"
echo "[Strategy1_Research] wallet=$WALLET_NAME"
echo "[Strategy1_Research] hotkey=$HOTKEY_NAME"
echo "[Strategy1_Research] netuid=$NETUID"
echo "[Strategy1_Research] axon_port=$AXON_PORT"

PYTHONPATH="agents/strategy${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV4138EXITPERSIST'
from research_quote_hysteresis import (
    PROFITABLE_EXIT_PERSISTENCE_VERSION, profitable_maker_exit_ttl_ms,
    hold_existing_profitable_maker_exit,
)
if PROFITABLE_EXIT_PERSISTENCE_VERSION != "profitable_maker_exit_persistence_v4_13_8":
    raise SystemExit("ERROR: stale V4.13.8 profitable exit persistence helper")
ttl, active = profitable_maker_exit_ttl_ms(
    baseline_ttl_ms=950.0, maker_net_bps=0.08, market_regime="NORMAL",
    persistent_ttl_ms=3000.0,
)
if not active or ttl != 3000.0:
    raise SystemExit("ERROR: V4.13.8 profitable exit TTL contract failed")
ttl2, active2 = profitable_maker_exit_ttl_ms(
    baseline_ttl_ms=950.0, maker_net_bps=0.0, market_regime="NORMAL",
    persistent_ttl_ms=3000.0,
)
if active2 or ttl2 != 950.0:
    raise SystemExit("ERROR: V4.13.8 non-positive exit must retain baseline TTL")
if not hold_existing_profitable_maker_exit(
    existing_price=100.00, desired_price=100.02, tick_size=0.01,
    existing_qty=0.25, desired_qty=0.25, maker_net_bps=0.08, reprice_ticks=3.0,
):
    raise SystemExit("ERROR: V4.13.8 profitable exit hold contract failed")
print("V4.13.8 profitable Maker exit persistence API OK")
PYV4138EXITPERSIST

grep -q 'PROFITABLE_EXIT_PERSISTENCE_VERSION = "profitable_maker_exit_persistence_v4_13_8"' agents/strategy/research_quote_hysteresis.py || {
  echo "ERROR: V4.13.8 profitable Maker exit persistence helper missing" >&2
  exit 1
}
# Round-trip phase timing is measurement-only, but a silently broken module
# would void the whole run: the throughput attribution it produces is the
# reason for launching. Fail here instead of after the fact.
PYTHONPATH="$AGENT_PATH${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYRTPHASE'
from research_rt_phase_timing import RT_PHASE_VERSION, RoundTripPhaseState

S = 1_000_000_000
state = RoundTripPhaseState()
state.note_entry_submit(1, 0)
state.note_entry_fill(1, 10 * S)
state.note_exit_submit(1, 100 * S)
sample = state.note_round_trip(1, 130 * S)
if (sample["entry_wait_s"], sample["hold_s"], sample["exit_wait_s"]) != (10.0, 90.0, 30.0):
    raise SystemExit(f"ERROR: rt phase split wrong: {sample}")

snap = state.snapshot(simulation_time=3600.0)
if snap["rt_phase_version"] != RT_PHASE_VERSION or snap["rt_per_sim_hour"] != 1.0:
    raise SystemExit(f"ERROR: rt phase snapshot wrong: {snap}")
if abs(snap["rt_implied_concurrency"] - 130.0 / 3600.0) > 1e-9:
    raise SystemExit("ERROR: rt implied concurrency (Little's Law) wrong")
print(f"V4.14.5 round-trip phase timing OK ({RT_PHASE_VERSION})")
PYRTPHASE

grep -q 'from research_rt_phase_timing import RoundTripPhaseState' agents/strategy/Strategy1_Research.py || {
  echo "ERROR: Strategy1_Research is not wired to round-trip phase timing" >&2
  exit 1
}

echo "[Strategy1_Research] version=lean_authority_cleanup_v4_15_1"
echo "[Strategy1_Research] log_dir=$RESEARCH_DIR"

if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then
  echo "V4.15.1 lean-authority + V4.14.4 RealNet safety preflight-only PASS"
  exit 0
fi

exec "$REPO_ROOT/run_miner_multi.sh" \
  -i "$PM2_NAME" -e "$ENDPOINT" -w "$WALLET_NAME" -h "$HOTKEY_NAME" -u "$NETUID" -a "$AXON_PORT" \
  -g "$AGENT_PATH" -n Strategy1_Research -m "$PARAMS" "${EXTRA[@]}"
