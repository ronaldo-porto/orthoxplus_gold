#!/usr/bin/env bash
set -euo pipefail

# Resolve the repository before running any preflight.  The same launcher is
# archived under agents/strategy/__ver_st1_log__, so support both locations.
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

# V4.12.18 Inventory-State Decoupling deployment contract.
# Loss authority and capacity authority are independent: protected Kappa books
# may PARK/free active capacity without receiving bounded-loss Taker subsidy.
PYTHONPATH="agents/strategy${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV41218DECOUPLE'
from research_inventory_liveness import (
    INVENTORY_LIVENESS_VERSION,
    classify_liveness_stage,
    evaluate_bounded_rescue,
    evaluate_protected_parking,
    parked_refresh_due,
)
from research_execution_lanes import (
    LANE_COMPLETION,
    LaneBook,
    apply_kappa_conversion_pressure_gate,
    classify_execution_lane,
)
from research_kappa_flywheel import (
    KAPPA_FLYWHEEL_VERSION,
    PHASE_BOOTSTRAP,
    PHASE_BREADTH,
    PHASE_DENSITY,
    PNL_CONFIDENCE_FULL,
    PNL_CONFIDENCE_PARTIAL,
    PNL_CONFIDENCE_UNKNOWN,
    flywheel_phase,
    note_realized_pnl_event,
    phase_density_target,
    pnl_confidence,
    pnl_confidence_multiplier,
    rolling_book_economics,
    sanitize_realized_pnl_events,
)
from research_quote_hysteresis import (
    ONE_AWAY_CONVERSION_TTL_VERSION,
    one_away_stale_completion_ttl,
)

if INVENTORY_LIVENESS_VERSION != "inventory_state_decoupling_v4_12_18":
    raise SystemExit("ERROR: stale research_inventory_liveness.py for V4.12.18")
if KAPPA_FLYWHEEL_VERSION != "kappa_flywheel_v4_12_18":
    raise SystemExit("ERROR: stale research_kappa_flywheel.py for V4.12.18")
if ONE_AWAY_CONVERSION_TTL_VERSION != "one_away_velocity_stale_ttl_v4_12_17":
    raise SystemExit("ERROR: V4.12.17 short velocity-stale TTL was not preserved")

def stage(rem, failed=0, age=0, state="NORMAL", stop=False):
    return classify_liveness_stage(
        observations_remaining=rem, required_observations=3,
        failed_exit_count=failed, inventory_age=age,
        inventory_state=state, stop_loss_hit=stop,
        maker_failed_exits=3, maker_min_age_ticks=8,
        taker_failed_exits=8, taker_min_age_ticks=16,
        hard_failed_exits=12, hard_min_age_ticks=24,
        protected_park_failed_exits=4, protected_park_min_age_ticks=8,
        maker_floor_bps=-4.0, soft_taker_floor_bps=-8.0,
        hard_taker_floor_bps=-12.0,
    )

def rescue(st, taker, wait, state="NORMAL", stop=False):
    return evaluate_bounded_rescue(
        st, taker_net_bps=taker, wait_ev_bps=wait,
        expected_markout_bps=-2.0, adverse_selection_risk=0.50,
        stop_loss_hit=stop, inventory_state=state,
        min_ev_advantage_bps=0.50, adverse_markout_bps=1.0,
        adverse_risk_floor=0.25,
    )

# Core V4.12.18 invariant: protected books may PARK but cannot receive the
# liveness loss subsidy.
one = stage(1, failed=4, age=8)
if not one.protected or one.loss_rescue_eligible or not one.park_eligible or not one.protected_park_armed:
    raise SystemExit("ERROR: V4.12.18 ONE_AWAY capacity/loss decoupling failed")
pd = evaluate_protected_parking(one, executable_maker_net_bps=-5.0, protected_floor_bps=-1.0)
if not pd.park or pd.reason != "PROTECTED_STALE_NON_EXECUTABLE":
    raise SystemExit("ERROR: V4.12.18 protected parking contract failed")
pr = rescue(one, -5.0, -100.0, state="EMERGENCY", stop=True)
if pr.authorized or pr.park or pr.reason != "SCORE_STATE_PROTECTED":
    raise SystemExit("ERROR: V4.12.18 protected loss subsidy leak")
if evaluate_protected_parking(one, executable_maker_net_bps=-0.5, protected_floor_bps=-1.0).park:
    raise SystemExit("ERROR: V4.12.18 executable protected touch incorrectly parked")

# TWO_AWAY/UNCOVERED bounded-loss rescue is preserved.
soft = rescue(stage(2, failed=8, age=16), -7.0, -10.0)
if not soft.authorized or soft.allowed_loss_floor_bps != -8.0:
    raise SystemExit("ERROR: V4.12.18 soft bounded rescue contract failed")
price_hard = rescue(stage(2, failed=3, age=5), -9.1, -10.0)
if not price_hard.authorized or price_hard.park or price_hard.allowed_loss_floor_bps != -12.0:
    raise SystemExit("ERROR: V4.12.18 event-driven -8/-12 hard rescue failed")
beyond = rescue(stage(2, failed=20, age=30, state="EMERGENCY", stop=True), -12.1, -30.0, state="EMERGENCY", stop=True)
if beyond.authorized or not beyond.park:
    raise SystemExit("ERROR: V4.12.18 hard-floor parking contract failed")

# Park labels do not create a scarce park cap and one exploration slot survives
# real total-headroom pressure.
rows = [
    LaneBook(book_id=1, observations_remaining=1, economics_ok=True),
    LaneBook(book_id=2, observations_remaining=2, economics_ok=True),
    LaneBook(book_id=10, observations_remaining=3, is_uncovered=True, economics_ok=True, maker_ev=2.0, maker_ev_known=True),
    LaneBook(book_id=11, observations_remaining=3, is_uncovered=True, economics_ok=True, maker_ev=1.0, maker_ev_known=True),
]
gated, suppressed, productive, reason = apply_kappa_conversion_pressure_gate(
    rows, parked_open_books=9, max_parked_open_books=6,
    total_open_books=10, max_total_open_books=12, reserve_total_slots=3,
    exploration_slots=1, enabled=True,
)
by_id = {row.book_id: row for row in gated}
if reason != "TOTAL_HEADROOM" or productive != 2 or suppressed != {11} or not by_id[10].entry_feasible:
    raise SystemExit("ERROR: V4.12.18 exploration fail-open contract failed")

# Flywheel migration: valid Kappa observations remain usable even when an older
# session lacks the newer PnL ledger. Missing PnL lowers confidence only.
if pnl_confidence(3, 3) != PNL_CONFIDENCE_FULL:
    raise SystemExit("ERROR: V4.12.18 FULL PnL confidence contract failed")
if pnl_confidence(3, 1) != PNL_CONFIDENCE_PARTIAL:
    raise SystemExit("ERROR: V4.12.18 PARTIAL PnL confidence contract failed")
if pnl_confidence(3, 0) != PNL_CONFIDENCE_UNKNOWN:
    raise SystemExit("ERROR: V4.12.18 UNKNOWN PnL confidence contract failed")
if (pnl_confidence_multiplier(PNL_CONFIDENCE_FULL), pnl_confidence_multiplier(PNL_CONFIDENCE_PARTIAL), pnl_confidence_multiplier(PNL_CONFIDENCE_UNKNOWN)) != (1.0, 0.85, 0.70):
    raise SystemExit("ERROR: V4.12.18 PnL confidence multipliers changed")

# Qualified density work remains a completion-lane activity.
density = LaneBook(
    book_id=42, observations_remaining=0, kappa_eligible=True,
    density_due=True, economics_ok=True, rolling_observation_count=5,
    density_state="QUALIFIED_LOW_DENSITY", pnl_confidence="UNKNOWN",
    pnl_confidence_mult=0.70,
)
if classify_execution_lane(density) != LANE_COMPLETION:
    raise SystemExit("ERROR: V4.12.18 qualified-density completion lane failed")
if flywheel_phase(40) != PHASE_BOOTSTRAP or flywheel_phase(41) != PHASE_BREADTH or flywheel_phase(80) != PHASE_DENSITY:
    raise SystemExit("ERROR: V4.12.18 flywheel phase boundary failed")
if (phase_density_target(PHASE_BOOTSTRAP), phase_density_target(PHASE_BREADTH), phase_density_target(PHASE_DENSITY)) != (6, 12, 50):
    raise SystemExit("ERROR: V4.12.18 density target contract failed")

# Restart-safe realized PnL still round-trips exactly when present.
events = {}
events = note_realized_pnl_event(events, book_id=7, timestamp=100, realized_pnl=0.10, now=100, lookback_ns=1000)
events = note_realized_pnl_event(events, book_id=7, timestamp=200, realized_pnl=-0.03, now=200, lookback_ns=1000)
raw = {"7": [[ts, pnl] for ts, pnl in events[7]]}
restored = sanitize_realized_pnl_events(raw)
stats = rolling_book_economics(restored, 7, now=200, lookback_ns=1000)
if stats.nonzero_count != 2 or abs(stats.realized_sum - 0.07) > 1e-12:
    raise SystemExit("ERROR: V4.12.18 rolling realized-PnL restart contract failed")

# High-velocity ONE_AWAY TTL remains short; parking refresh defaults to 25 ticks.
ttl, ttl_reason, used = one_away_stale_completion_ttl(
    chosen_ttl_ms=None, ttl_reason="STALE", completion_candidate=True,
    completion_samples=2, completion_target=3, trading_ev=0.05,
    market_regime="QUIET", min_ttl_ms=250.0, stale_ttl_ms=900.0,
)
if not used or ttl != 250.0 or ttl_reason != "ONE_AWAY_VELOCITY_STALE_SHORT":
    raise SystemExit("ERROR: V4.12.18 velocity-stale ONE_AWAY TTL contract failed")
due, reason = parked_refresh_due(
    current_tick=24, last_refresh_tick=0, current_mid=100.0, last_mid=100.0,
    material_touch_move_bps=8.0,
)
if due or reason != "PARKED_COOLDOWN":
    raise SystemExit("ERROR: V4.12.18 25-tick parked cooldown failed")
due, reason = parked_refresh_due(
    current_tick=25, last_refresh_tick=0, current_mid=100.0, last_mid=100.0,
    material_touch_move_bps=8.0,
)
if not due or reason != "INTERVAL":
    raise SystemExit("ERROR: V4.12.18 parked interval refresh failed")

print("V4.12.18 Inventory-State Decoupling API OK")
PYV41218DECOUPLE

# V4.13.8 profitable Maker-exit persistence + V4.13.7 qualified/Core recycle deployment contract.
PYTHONPATH="agents/strategy${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV413PRODUCTIVITY'
from research_kappa_productivity import (
    KAPPA_PRODUCTIVITY_VERSION, ProductivitySnapshot, STATE_CORE, STATE_QUALIFIED,
    core_probe_eligible, kappa_state, priority_for_state, scheduler_phase, TIER_INEFFICIENT,
)
from research_execution_lanes import (
    COMPLETION_ECONOMICS_VERSION, LaneBook, LaneBudgets,
    LANE_COMPLETION, LANE_COVERAGE, LANE_REALIZATION,
    authoritative_execution_lane, density_priority_budgets,
    execution_completion_candidate, select_lane_candidates,
)
from research_inventory_liveness import (
    FRESH_MAKER_GRACE_VERSION, POSITIVE_MAKER_VETO_VERSION,
    classify_liveness_stage, evaluate_bounded_rescue, fresh_maker_grace_applies,
    positive_maker_rescue_veto_applies,
)
from research_entry_size import QUALIFIED_CORE_EXACT_MIN_VERSION, admit_minimum_order
from research_quote_hysteresis import (
    QUALIFIED_CORE_STALE_TTL_VERSION, qualified_core_stale_completion_ttl,
)

if KAPPA_PRODUCTIVITY_VERSION != "simplified_kappa_productivity_v4_13_9":
    raise SystemExit("ERROR: stale research_kappa_productivity.py for V4.13.8")
if QUALIFIED_CORE_EXACT_MIN_VERSION != "qualified_core_exact_min_v4_13_7":
    raise SystemExit("ERROR: stale V4.13.7 qualified-Core exact-min helper")
if QUALIFIED_CORE_STALE_TTL_VERSION != "qualified_core_velocity_stale_ttl_v4_13_7":
    raise SystemExit("ERROR: stale V4.13.7 qualified-Core stale-TTL helper")
core_admission = admit_minimum_order(
    safe_size=0.08273, min_order=0.25, trading_ev=0.06, inventory_risk=0.12,
    exit_capacity=0.08447, volume_headroom=0.80, remaining_inventory=1.20,
    observations_remaining=0, productive_qualified_core=True,
    enable_qualified_core_exact_min=True, qualified_core_min_exit_fraction=0.20,
)
if not core_admission.allow or core_admission.size != 0.25 or core_admission.trigger != "QUALIFIED_CORE_EXACT_MIN":
    raise SystemExit("ERROR: V4.13.7 qualified-Core exact-min recycle contract failed")
core_ttl, core_ttl_reason, core_ttl_used = qualified_core_stale_completion_ttl(
    chosen_ttl_ms=None, ttl_reason="STALE", completion_candidate=True,
    completion_samples=4, completion_target=3, productive_qualified_core=True,
    trading_ev=0.05, market_regime="NORMAL", min_ttl_ms=200.0, stale_ttl_ms=900.0,
)
if not core_ttl_used or core_ttl != 250.0 or core_ttl_reason != "QUALIFIED_CORE_VELOCITY_STALE_SHORT":
    raise SystemExit("ERROR: V4.13.7 qualified-Core stale-TTL recycle contract failed")
if scheduler_phase(40) != "BOOTSTRAP" or scheduler_phase(41) != "BALANCED" or scheduler_phase(80) != "DENSITY":
    raise SystemExit("ERROR: V4.13.2 breadth/density phase thresholds failed")

book115 = ProductivitySnapshot(
    book_id=115, observations=3, round_trips=3, maker_quotes=12, maker_fills=6,
    contract_rejects=0, realized_pnl=0.7644, positive_count=3, negative_count=0,
    maker_fee_bps=-12.49, fill_rate_hint=0.20, raw_kappa=0.5, ticks_since_last_rt=1,
    fresh_round_trips=3, fresh_positive_round_trips=3, fresh_negative_round_trips=0,
)
book98 = ProductivitySnapshot(
    book_id=98, observations=2, round_trips=2, maker_quotes=194, maker_fills=5,
    contract_rejects=22, realized_pnl=0.10, positive_count=2, negative_count=0,
    maker_fee_bps=-29.9, fill_rate_hint=0.03, raw_kappa=0.2, ticks_since_last_rt=20,
    fresh_round_trips=2, fresh_positive_round_trips=2, fresh_negative_round_trips=0,
)
if not book115.core_candidate or kappa_state(observations=3, core=True) != STATE_CORE:
    raise SystemExit("ERROR: V4.13.2 productive CORE recycling failed")
if book98.execution_tier != TIER_INEFFICIENT or book98.core_candidate:
    raise SystemExit("ERROR: V4.13.2 inefficient-book demotion failed")
if priority_for_state(book115, phase="DENSITY") <= priority_for_state(book98, phase="DENSITY"):
    raise SystemExit("ERROR: V4.13.2 Kappa-productivity ranking failed")
book92 = ProductivitySnapshot(
    book_id=92, observations=1, round_trips=0, maker_quotes=70, maker_fills=1,
    contract_rejects=0, realized_pnl=0.0, positive_count=0, negative_count=0,
    maker_fee_bps=-10.0, fill_rate_hint=0.02, fresh_round_trips=0,
)
if book92.execution_tier != TIER_INEFFICIENT:
    raise SystemExit("ERROR: V4.13.2 zero-RT order-sink demotion failed")
bridge = ProductivitySnapshot(
    book_id=115, observations=3, round_trips=1, maker_quotes=8, maker_fills=2,
    contract_rejects=0, realized_pnl=0.20, positive_count=1, negative_count=0,
    maker_fee_bps=-10.0, fill_rate_hint=0.20, raw_kappa=0.3, ticks_since_last_rt=1,
    fresh_round_trips=1, fresh_positive_round_trips=1, fresh_negative_round_trips=0,
)
if not bridge.recycling_candidate or bridge.core_candidate:
    raise SystemExit("ERROR: V4.13.2 CORE bootstrap bridge failed")

# V4.13.6 completion-density economics: known NEGATIVE_EV must not consume
# KAPPA_COMPLETION capacity; known-positive ONE_AWAY leads, and density demand
# shifts BOOTSTRAP acquisition slots from COVERAGE toward COMPLETION.
if COMPLETION_ECONOMICS_VERSION != "completion_density_v4_13_6":
    raise SystemExit("ERROR: stale V4.13.6 completion-density helper")
neg = LaneBook(
    book_id=901, observations_remaining=1, economics_ok=True,
    maker_ev=-0.05, maker_ev_known=True, completion_ev_known=True, completion_ev_ok=False,
)
pos = LaneBook(
    book_id=902, observations_remaining=1, economics_ok=True,
    maker_ev=0.05, maker_ev_known=True, completion_ev_known=True, completion_ev_ok=True,
)
core = LaneBook(
    book_id=903, observations_remaining=0, economics_ok=True, core_candidate=True,
    maker_ev=0.04, maker_ev_known=True, completion_ev_known=True, completion_ev_ok=True,
    kappa_productivity_tier="PRODUCTIVE",
)
if select_lane_candidates([neg, pos, core], LaneBudgets(0, 2, 0, 0), max_candidates=2).by_lane[LANE_COMPLETION] != [902, 903]:
    raise SystemExit("ERROR: V4.13.6 positive completion ordering/prefilter failed")
shift = density_priority_budgets(
    [pos, core], LaneBudgets(coverage_slots=4, completion_slots=3, realization_slots=3, shared_overflow_slots=1),
    enabled=True, min_candidates=1, aggressive_coverage=True,
)
if shift.coverage_slots != 1 or shift.completion_slots != 6 or shift.realization_slots != 3:
    raise SystemExit("ERROR: V4.13.6 density lane-budget shift failed")

# V4.13.2 Maker grace: reproduce the age-1 -9 bps event-driven rescue and
# verify a profitable executable Maker close blocks that Taker authority.
st = classify_liveness_stage(
    observations_remaining=3, failed_exit_count=0, inventory_age=1,
    inventory_state="NORMAL", stop_loss_hit=False,
    maker_failed_exits=3, maker_min_age_ticks=8,
    taker_failed_exits=8, taker_min_age_ticks=16,
    hard_failed_exits=12, hard_min_age_ticks=24,
    maker_floor_bps=-4.0, soft_taker_floor_bps=-8.0, hard_taker_floor_bps=-12.0,
)
resc = evaluate_bounded_rescue(
    st, taker_net_bps=-9.0, wait_ev_bps=-10.0, expected_markout_bps=-2.0,
    adverse_selection_risk=0.10, stop_loss_hit=False, inventory_state="NORMAL",
)
if FRESH_MAKER_GRACE_VERSION != "fresh_maker_grace_v4_13_2":
    raise SystemExit("ERROR: stale V4.13.2 fresh Maker grace helper")
if not resc.authorized or resc.reason != "PRICE_HARD_WINDOW_RESCUE":
    raise SystemExit("ERROR: V4.13.2 Maker-grace reproduction setup failed")
if not fresh_maker_grace_applies(
    st, resc, maker_net_bps=10.0, maker_executable=True, stop_loss_hit=False,
    inventory_state="NORMAL", hard_risk=False, grace_ticks=3.0,
):
    raise SystemExit("ERROR: V4.13.2 profitable fresh Maker grace failed")
if fresh_maker_grace_applies(
    st, resc, maker_net_bps=10.0, maker_executable=True, stop_loss_hit=False,
    inventory_state="NORMAL", hard_risk=True, grace_ticks=3.0,
):
    raise SystemExit("ERROR: V4.13.2 Maker grace blocked real RISK authority")

# V4.13.5 positive-Maker rescue authority: reproduce the V4.13.4 loss shape.
if POSITIVE_MAKER_VETO_VERSION != "positive_maker_veto_v4_13_5":
    raise SystemExit("ERROR: stale V4.13.5 positive Maker veto helper")
if not positive_maker_rescue_veto_applies(
    maker_net_bps=13.42, taker_net_bps=-10.79, maker_executable=True,
    failed_exit_count=2, stop_loss_hit=False, inventory_state="NORMAL",
    hard_risk=False, maker_positive_floor_bps=1.0, max_failed_exits=3,
):
    raise SystemExit("ERROR: V4.13.5 positive-Maker veto failed V4.13.4 loss reproduction")
if positive_maker_rescue_veto_applies(
    maker_net_bps=13.42, taker_net_bps=-10.79, maker_executable=True,
    failed_exit_count=3, stop_loss_hit=False, inventory_state="NORMAL",
    hard_risk=False, maker_positive_floor_bps=1.0, max_failed_exits=3,
):
    raise SystemExit("ERROR: V4.13.5 veto did not release after bounded failed exits")
if positive_maker_rescue_veto_applies(
    maker_net_bps=13.42, taker_net_bps=-10.79, maker_executable=True,
    failed_exit_count=1, stop_loss_hit=False, inventory_state="NORMAL",
    hard_risk=True, maker_positive_floor_bps=1.0, max_failed_exits=3,
):
    raise SystemExit("ERROR: V4.13.5 veto blocked true hard-risk authority")

# V4.13.2 CORE_PROBE: a Book83-like restored qualified UNKNOWN book must be
# eligible, and exactly one completion slot must prefer the probe.
probe_snap = ProductivitySnapshot(
    book_id=83, observations=4, round_trips=4, maker_quotes=0, maker_fills=0,
    contract_rejects=0, realized_pnl=0.1916, positive_count=3, negative_count=1,
    maker_fee_bps=-10.0, fill_rate_hint=0.20, raw_kappa=0.8,
    fresh_round_trips=0, fresh_positive_round_trips=0, fresh_negative_round_trips=0,
)
if not core_probe_eligible(
    probe_snap, kappa_eligible=True, maker_ev=0.035, maker_ev_known=True,
    flat_and_safe=True, entry_feasible=True, economics_ok=True,
    pnl_confidence="FULL", recent_realized_pnl=0.1916, raw_kappa=0.8,
):
    raise SystemExit("ERROR: V4.13.2 CORE_PROBE eligibility failed")
alloc = select_lane_candidates(
    [
        LaneBook(book_id=1, observations_remaining=1, kappa_productivity_tier="PRODUCTIVE", kappa_productivity_score=0.9),
        LaneBook(book_id=83, observations_remaining=0, density_due=True, core_probe_candidate=True, kappa_productivity_tier="UNKNOWN", kappa_productivity_score=0.6),
        LaneBook(book_id=115, observations_remaining=0, density_due=True, recycling_candidate=True, kappa_productivity_tier="PRODUCTIVE", kappa_productivity_score=0.7),
    ],
    LaneBudgets(coverage_slots=0, completion_slots=1, realization_slots=0, shared_overflow_slots=0),
    max_candidates=1,
)
if alloc.by_lane[LANE_COMPLETION] != [83]:
    raise SystemExit("ERROR: V4.13.4 CORE_PROBE slot reservation failed")
if not execution_completion_candidate(
    inventory_flat=True,
    core_probe_candidate=True,
    legacy_completion_candidate=False,
):
    raise SystemExit("ERROR: inherited V4.13.3 CORE_PROBE fallback failed")

# V4.13.4: the screen allocation is authoritative at execution for every
# productivity-completion state, not just CORE_PROBE. Reproduce Book122: a CORE
# book is already Kappa-eligible, so legacy execution fallback would say COVERAGE.
core_alloc = select_lane_candidates(
    [LaneBook(book_id=122, observations_remaining=0, core_candidate=True, economics_ok=True)],
    LaneBudgets(coverage_slots=0, completion_slots=1, realization_slots=0, shared_overflow_slots=0),
    max_candidates=1,
)
if core_alloc.by_lane[LANE_COMPLETION] != [122]:
    raise SystemExit("ERROR: V4.13.4 CORE screen allocation failed")
if authoritative_execution_lane(
    122, inventory_flat=True, allocation=core_alloc, fallback_lane=LANE_COVERAGE,
) != LANE_COMPLETION:
    raise SystemExit("ERROR: V4.13.4 CORE completion grant lost at execution")
if authoritative_execution_lane(
    122, inventory_flat=False, allocation=core_alloc, fallback_lane=LANE_COVERAGE,
) != LANE_REALIZATION:
    raise SystemExit("ERROR: V4.13.4 non-flat REALIZATION priority failed")

recycling_alloc = select_lane_candidates(
    [LaneBook(book_id=115, observations_remaining=0, recycling_candidate=True, economics_ok=True)],
    LaneBudgets(coverage_slots=0, completion_slots=1, realization_slots=0, shared_overflow_slots=0),
    max_candidates=1,
)
if authoritative_execution_lane(
    115, inventory_flat=True, allocation=recycling_alloc, fallback_lane=LANE_COVERAGE,
) != LANE_COMPLETION:
    raise SystemExit("ERROR: V4.13.4 RECYCLING completion grant lost at execution")

print("V4.13.7 CORE recycle + V4.13.6 density + V4.13.5 exit authority + V4.13.4 authoritative lanes OK")
PYV413PRODUCTIVITY
# Internal runner: run_miner_multi.sh
# SN79 launcher for BaseStrategy V4.12.18 Inventory-State Decoupling (Miner 1).
# Default PM2 name: sn79-m1 | Default Axon port: 8091

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
RESEARCH_DIR="${RESEARCH_DIR:-$REPO_ROOT/logs/m1_base_strategy}"

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
[[ -f "$AGENT_PATH/BaseStrategy.py" ]] || { echo "BaseStrategy.py missing" >&2; exit 1; }
grep -q 'DEPLOY_POLICY_VERSION = "base_v4_13_9_champion"' "$AGENT_PATH/BaseStrategy.py" || {
  echo "ERROR: BaseStrategy.py is not base_v4_13_9_champion" >&2
  exit 1
}
[[ -f "$AGENT_PATH/Strategy1_Debug.py" ]] || { echo "Strategy1_Debug.py missing" >&2; exit 1; }
[[ -f "$AGENT_PATH/research_fill_hazard.py" ]] || { echo "research_fill_hazard.py missing" >&2; exit 1; }
[[ -f "$AGENT_PATH/research_unified_exit.py" ]] || { echo "research_unified_exit.py missing" >&2; exit 1; }
[[ -f "$AGENT_PATH/research_entry_size.py" ]] || { echo "research_entry_size.py missing" >&2; exit 1; }

# V4.12.4 deployment guard: require completion exact-min admission API.
PYTHONPATH="$AGENT_PATH${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV4124ENTRY'
import inspect
import research_entry_size
required = {
    "enable_two_away_exact_min",
    "two_away_min_trading_ev",
    "two_away_max_inventory_risk",
    "two_away_min_exit_fraction",
    "two_away_min_headroom",
    "productive_qualified_core",
    "enable_qualified_core_exact_min",
    "qualified_core_min_trading_ev",
    "qualified_core_max_inventory_risk",
    "qualified_core_min_exit_fraction",
    "qualified_core_min_headroom",
}
sig = inspect.signature(research_entry_size.admit_minimum_order)
missing = sorted(required.difference(sig.parameters))
if missing:
    raise SystemExit(
        "ERROR: stale/incompatible research_entry_size.py for V4.12.4; "
        f"missing admit_minimum_order args={missing}; loaded={research_entry_size.__file__}"
    )
PYV4124ENTRY


# V4.12.9 deployment guard: rolling-deadline scheduler must suppress early
# qualified refresh while allowing critical refresh, and incomplete progress must
# expose its own rolling expiry deadline.
PYTHONPATH="$AGENT_PATH${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYV4129DEADLINE'
from research_execution_lanes import (
    LaneBook, apply_breadth_rotation_gate, completion_sort_key,
)
from research_kappa_state import kappa_expiry_from_timestamps

rows = [
    LaneBook(book_id=1, score_qualified=True, observations_remaining=0),
    LaneBook(book_id=2, score_qualified=True, observations_remaining=0,
             needs_refresh=True, deadline_urgency=0.20, deadline_critical=False),
    LaneBook(book_id=3, score_qualified=True, observations_remaining=0,
             needs_refresh=True, deadline_urgency=0.80, deadline_critical=True),
    LaneBook(book_id=10, observations_remaining=1, economics_ok=True,
             entry_feasible=True, deadline_urgency=0.10),
    LaneBook(book_id=11, observations_remaining=2, economics_ok=True,
             entry_feasible=True, deadline_urgency=0.70, deadline_critical=True),
]
gated, suppressed, productive = apply_breadth_rotation_gate(
    rows, enabled=True, min_productive_incomplete=2,
)
by_id = {row.book_id: row for row in gated}
if productive != 2 or suppressed != {1, 2}:
    raise SystemExit(
        "ERROR: stale/incompatible research_execution_lanes.py for V4.12.8; "
        f"productive={productive} suppressed={sorted(suppressed)}"
    )
if by_id[1].entry_feasible or by_id[2].entry_feasible or not by_id[3].entry_feasible:
    raise SystemExit("ERROR: V4.12.9 deadline breadth semantics failed")

# St6.4: one productive incomplete book is now sufficient to rotate stable
# qualified acquisition while breadth is below the protected target.
single = [
    LaneBook(book_id=21, score_qualified=True, observations_remaining=0),
    LaneBook(book_id=22, observations_remaining=1, economics_ok=True, entry_feasible=True),
]
single_gated, single_suppressed, single_productive = apply_breadth_rotation_gate(
    single, enabled=True, min_productive_incomplete=1,
)
if single_productive != 1 or single_suppressed != {21}:
    raise SystemExit(
        "ERROR: V4.12.9 single-incomplete breadth rotation failed; "
        f"productive={single_productive} suppressed={sorted(single_suppressed)}"
    )

# Critical one-RT refresh must outrank a noncritical one-away candidate.
critical_refresh = rows[2]
normal_one_away = rows[3]
if not (completion_sort_key(critical_refresh) < completion_sort_key(normal_one_away)):
    raise SystemExit("ERROR: V4.12.8 earliest-deadline completion ordering failed")

# Two incomplete observations must have a real progress deadline instead of None.
expiry = kappa_expiry_from_timestamps(
    99, [100, 200], now=950, lookback_ns=1000, required_observations=3,
    warning_horizon_frac=0.20,
)
if expiry.qualified or expiry.time_to_expiry_ns is None or expiry.expiry_urgency <= 0.0:
    raise SystemExit("ERROR: V4.12.8 incomplete-progress deadline semantics failed")
print("V4.12.8 rolling deadline scheduler API OK")
PYV4129DEADLINE

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
# BaseStrategy persists the events asynchronously instead.
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
PARAMS="enable_mm_strategy=1 enable_kappa_strategy=0 lazy_load=1 \
fast_update=1 sync_event_csv=0 history_len=0 \
mm_base_size=0.25 max_inventory_base=1.20 inventory_close_threshold=0.25 \
max_mm_books_per_tick=6 max_managed_books_per_tick=10 \
min_expected_alpha=0.18 min_expected_realized_pnl=0.0 \
mm_expiry_period_ns=500000000 maintenance_size_mult=0.25 \
passive_exit_only=1 aggressive_close_min_ticks=300 position_max_ticks=300 \
mm_skip_inactive_tier=1 toxic_loss_streak=4 enable_auto_tuning=0 allow_tuning_config=0 \
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
research_dust_compact_books_per_tick=2 research_kappa_completion_enabled=1 \
research_kappa_completion_target=3 research_kappa_completion_rank_bonus=0.30 \
research_kappa_completion_attempt_cap=6 research_kappa_completion_success_cap=3 \
research_kappa_completion_fill_mult=0.70 research_kappa_completion_fill_floor=0.08 \
research_kappa_completion_relaxed_success_cap=3 research_kappa_completion_recent_pnl_floor=-0.01 \
research_actionable_fill_enabled=1 research_actionable_fill_min_samples=4 \
research_actionable_fill_prior_strength=6.0 research_actionable_fill_prior_actionable=0.85 \
research_actionable_fill_rank_weight=0.10 research_dust_risk_rank_penalty=0.18 \
research_dust_risk_target=0.15 research_kappa_one_away_bonus=0.10 \
research_partial_fill_hold_enabled=1 research_partial_fill_hold_min_dust_prob=0.12 \
research_partial_fill_hold_one_away_only=0 research_partial_fill_hold_max_ns=750000000 \
research_force_mm_post_only=1 research_dust_compact_adaptive=1 \
research_dust_compact_cooldown_ticks=100 research_dust_compact_max_cooldown_ticks=600 \
research_dust_compact_prior_fill=0.02 research_dust_compact_prior_strength=8.0 \
research_enable_fill_hazard=1 research_use_fill_hazard_for_policy=0 \
research_enable_score_ev=1 research_completion_ev_cache_ticks=20 research_density_priority_enabled=1 research_density_priority_min_candidates=1 research_enable_score_velocity=1 research_score_velocity_weight=0.08 research_enable_quote_hysteresis=1 \
research_enable_adaptive_ttl=1 research_enable_dust_escape=0 research_ttl_min_ms=250 research_ttl_max_ms=1500 research_quiet_ttl_ms=1000 research_quiet_exit_ttl_ms=950 research_one_away_exit_ttl_ms=975 \
research_enable_fast_candidate_screen=1 research_candidate_count=10 research_cohort_size=8 research_cohort_exploration_slots=1 research_max_open_books=6 research_max_active_open_books=6 research_max_total_open_books=12 research_max_parked_open_books=6 research_max_total_abs_base=3.0 research_kappa_conversion_pressure_enabled=1 research_kappa_conversion_reserve_slots=3 research_kappa_exploration_slots=1 research_kappa_flywheel_enabled=1 research_kappa_productivity_enabled=1 research_core_probe_enabled=1 research_persistent_maker_enabled=1 research_hysteresis_min_price_ticks=3 research_post_only_safety_ticks=2 \
research_enable_lane_scheduler=1 research_enable_aggressive_coverage=1 research_coverage_slots=3 research_completion_slots=5 research_realization_slots=3 research_shared_overflow_slots=1 \
research_lifecycle_taker_exit_prob=0.30 research_lifecycle_slippage_bps=0.75 research_lifecycle_holding_bps=0.50 \
research_positive_ev_min_order_override=0 research_positive_ev_min_safe_fraction=0.35 research_positive_ev_min_exit_fraction=0.45 research_positive_ev_min_trading_ev=0.05 \
research_one_away_exact_min_enabled=1 research_one_away_exact_min_ev_bps=0.0 research_one_away_exact_min_safe_fraction=0.15 research_one_away_exact_min_exit_fraction=0.20 \
research_two_away_exact_min_enabled=1 research_two_away_exact_min_ev=0.0 research_two_away_exact_min_max_inventory_risk=0.35 research_two_away_exact_min_exit_fraction=0.20 research_two_away_exact_min_min_headroom=0.25 \
research_qualified_core_exact_min_enabled=1 research_qualified_core_exact_min_ev=0.0 research_qualified_core_exact_min_max_inventory_risk=0.35 research_qualified_core_exact_min_exit_fraction=0.20 research_qualified_core_exact_min_min_headroom=0.25 research_qualified_core_stale_ttl_enabled=1 research_qualified_core_stale_ttl_ms=250  research_profitable_exit_persistence_enabled=1 research_profitable_exit_ttl_ms=3000 research_profitable_exit_min_net_bps=0.0 research_profitable_exit_reprice_ticks=3 \
research_quote_tighten_mult=0.85 research_quote_width_floor_mult=0.80 research_enable_one_away_quiet_tightening=1 research_one_away_quiet_width_mult=0.60 research_one_away_quiet_min_ev=0.0 research_one_away_max_touch_bps=1.5 research_one_away_stale_ttl_ms=250 research_fill_distance_decay_bps=6.0 research_fill_distance_near_boost=1.35 research_fill_distance_floor_mult=0.10 research_fill_fallback_policy_weight=0.45 research_local_kappa_refresh_ticks=10 research_score_qualified_pnl_floor=0.0 research_score_qualified_kappa_floor=0.0 research_p95_target_ms=120 \
research_enable_inventory_state_v2=1 research_enable_exit_urgency_v2=1 \
research_enable_hybrid_realization_v2=1 research_enable_economic_taker=1 \
research_enable_precise_reduction_qty=1 research_enable_dust_economic_gate=1 \
research_enable_authoritative_kappa_state=1 research_enable_markout_v2=1 \
research_inventory_liveness_enabled=1 research_fresh_maker_grace_enabled=1 research_fresh_maker_grace_ticks=3 research_positive_maker_veto_enabled=1 research_positive_maker_veto_floor_bps=1.0 research_positive_maker_veto_max_failed_exits=3 research_liveness_maker_failed_exits=3 research_liveness_maker_min_age_ticks=8 research_liveness_maker_floor_bps=-4.0 research_liveness_taker_failed_exits=8 research_liveness_taker_min_age_ticks=16 research_liveness_hard_failed_exits=12 research_liveness_hard_min_age_ticks=24 research_protected_park_failed_exits=4 research_protected_park_min_age_ticks=8 research_liveness_soft_taker_floor_bps=-8.0 research_liveness_hard_taker_floor_bps=-12.0 research_liveness_min_ev_advantage_bps=0.50 research_liveness_adverse_markout_bps=1.0 research_liveness_adverse_risk_floor=0.25 research_parked_refresh_ticks=25 research_parked_touch_move_bps=8.0 research_enable_fill_hazard_exit_compare=1 research_enable_sn79_action_utility=1 research_enable_score_taker_direct=1 research_enable_economic_taker_direct=1 research_economic_direct_max_loss_bps=0.0 research_enable_aggressive_positive_ev_taker=1 research_aggressive_positive_ev_min_net_bps=0.0 research_aggressive_positive_ev_switch_margin_bps=0.50 research_aggressive_positive_ev_one_away_margin_bps=0.0 research_aggressive_positive_ev_failed_exit_count=8 research_aggressive_positive_ev_min_age_ticks=16 research_aggressive_positive_ev_max_maker_fill=0.08 research_aggressive_positive_ev_min_urgency=0.30 research_maker_escalate_failed_exit_count=8 research_one_away_maker_escalate_failed_exit_count=3 research_enable_risk_taker_direct=0 research_risk_direct_max_loss_bps=-10.0 research_risk_direct_min_age_ticks=24 research_risk_direct_failed_exit_count=3 research_risk_direct_min_ev_advantage_bps=1.0 research_failed_exit_penalty_bps=0.75 research_exit_age_penalty_bps_per_tick=0.03 research_cancel_before_taker=1 research_sn79_pnl_scale_bps=8.0 research_sn79_pnl_weight=1.0 research_sn79_round_trip_weight=0.30 research_sn79_kappa_weight=0.35 research_sn79_coverage_weight=0.15 research_sn79_capital_release_weight=0.15 research_sn79_risk_reduction_weight=0.20 research_sn79_velocity_weight=0.25 research_sn79_downside_weight=0.45 research_sn79_min_utility_margin=0.03 research_sn79_max_score_subsidy_loss_bps=0.0 research_sn79_one_away_loss_floor_bps=0.0 research_sn79_two_away_loss_floor_bps=0.0 research_sn79_uncovered_loss_floor_bps=0.0 research_allow_score_loss_subsidy=0 research_kappa_lookback_ns=10800000000000 research_kappa_expiry_warning_frac=0.20 research_kappa_expiry_rank_bonus=0.20 research_suppress_qualified_acquisition=1 research_qualified_suppression_min_incomplete=1 research_deadline_scheduler_enabled=1 research_deadline_critical_urgency=0.50 research_deadline_rank_bonus=0.25 research_score_target_books=88 research_stale_maker_rescue_enabled=1 research_stale_maker_rescue_failed_exits=4 research_stale_maker_rescue_critical_failed_exits=1 research_stale_maker_rescue_floor_bps=-1.0 research_entry_recheck_ticks=12 research_hybrid_partial_frac_cap=0.90 research_ladder_passive_max=0.15 research_ladder_competitive_max=0.30 research_ladder_aggressive_max=0.45 research_enable_unified_exit=1 research_unified_maker_net_floor_bps=0.0 research_unified_stale_bridge_roundtrip_floor_bps=-12.0 research_unified_profit_lock_min_bps=1.0 research_unified_profit_lock_drawdown_bps=2.0 research_unified_switch_margin_bps=0.50 research_enable_protective_taker=1 research_protective_taker_loss_floor_bps=-2.0 research_protective_taker_ev_advantage_bps=1.0 research_protective_taker_failed_exits=6 research_protective_taker_min_age_ticks=8 research_protective_taker_adverse_bps=2.0 research_early_escape_enabled=1 research_early_escape_failed_exits=3 research_early_escape_min_age_ticks=5 research_early_escape_drawdown_bps=1.5 research_early_escape_floor_headroom_bps=0.75 research_early_escape_ev_advantage_bps=0.50 research_session_save_every_n=100"

echo "[BaseStrategy] pm2_name=$PM2_NAME"
echo "[BaseStrategy] wallet=$WALLET_NAME"
echo "[BaseStrategy] hotkey=$HOTKEY_NAME"
echo "[BaseStrategy] netuid=$NETUID"
echo "[BaseStrategy] axon_port=$AXON_PORT"

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
PYTHONPATH="agents/strategy${PYTHONPATH:+:$PYTHONPATH}" python - <<'PYBASE4138'
from pathlib import Path
src = Path("agents/strategy/BaseStrategy.py").read_text(encoding="utf-8")
for token in (
    'DEPLOY_POLICY_VERSION = "base_v4_13_9_champion"',
    'BASE_CHAMPION_PARENT = "simplified_kappa_productivity_v4_13_9"',
    'PROFITABLE_EXIT_PERSISTENCE_VERSION = "profitable_maker_exit_persistence_v4_13_8"',
):
    if token not in src:
        raise SystemExit(f"ERROR: BaseStrategy V4.13.8 champion contract missing: {token}")
print("BaseStrategy V4.13.9 champion contract OK")
PYBASE4138
echo "[BaseStrategy] version=base_v4_13_9_champion"
echo "[BaseStrategy] log_dir=$RESEARCH_DIR"

if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then
  echo "V4.13.9 launcher preflight-only PASS"
  exit 0
fi

exec "$REPO_ROOT/run_miner_multi.sh" \
  -i "$PM2_NAME" -e "$ENDPOINT" -w "$WALLET_NAME" -h "$HOTKEY_NAME" -u "$NETUID" -a "$AXON_PORT" \
  -g "$AGENT_PATH" -n BaseStrategy -m "$PARAMS" "${EXTRA[@]}"
