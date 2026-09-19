"""v5.0.2: dust liveness from inventory truth and terminal-order truth.

UID 18 on RealNet (log 20260915_063311, v5.0.0) stopped trading after tick 9,229: admission read zero
slots because 3.54 BASE counted as dust against a 2.0 BASE dust class.  Half of it was full positions.
F1 keeps a flat lifecycle's residue out of the next entry, F2 recognizes a clip a unit or two short at
the startup seed and the rewind reseed, F3 stops a refused book from holding the compactor's slots,
and F4 frees a book once the venue has processed an unfilled market exit.  The fixtures are that
run's own numbers.
"""
import ast
import collections
import textwrap
import typing
from pathlib import Path
from types import SimpleNamespace

import research_direct_legacy_baseline as lb
import research_direct_session_epoch as ep
import research_v5_dust_liveness as dl
from research_direct_book_ownership import DIRECT_BOOK_OWNERSHIP_VERSION, canonical_order_side
from research_direct_inflight_reservation import PendingExposureOrder, pending_order_live
from research_direct_inventory_truth import build_seed_plan
from _harness import extractor

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
RESEARCH = (STRATEGY / "Strategy1_Research.py").read_text()
STRATEGY1 = (STRATEGY / "Strategy1.py").read_text()
EPOCH = (STRATEGY / "research_direct_session_epoch.py").read_text()
LEGACY = (STRATEGY / "research_direct_legacy_baseline.py").read_text()
LIVENESS = (STRATEGY / "research_v5_dust_liveness.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
MIN = 0.25
EPS = 0.5e-4      # the execution flat epsilon at 4 volume decimals
TOL = 2e-4        # two base units at 4 base decimals


# ---- harness: the real method sources over a plain agent ----------------------------------------------

_TREES = {}


# This suite also extracts from other agent files, so the class name is not pinned --
# the same "any class in the text handed to it" rule its own copy used.
_method_source = extractor(SIMPLE)


# Frozen methods come from the frozen files, so the tests run the code that runs live.
SOURCES = {
    "_select_dust_compaction_books": RESEARCH, "_dust_compaction_learning_snapshot": RESEARCH,
    "_is_compactable_dust": RESEARCH, "_is_dust_qty": RESEARCH, "_record_dust_compaction_attempt": RESEARCH,
    "_execution_flat_epsilon": RESEARCH, "_position_tracker_snapshot": STRATEGY1,
}

NAMES = {
    "Any": typing.Any,
    "PositionTracker": lambda net, vwap, opened, longs, shorts: SimpleNamespace(
        net_qty=net, vwap_entry=vwap, opened_at_ns=opened, long_qty=longs, short_qty=shorts),
    "canonical_order_side": canonical_order_side, "DIRECT_BOOK_OWNERSHIP_VERSION": DIRECT_BOOK_OWNERSHIP_VERSION,
    "PendingExposureOrder": PendingExposureOrder, "a196_ledger_total_abs": lb.ledger_abs,
    "RESIDUE_FLAT": dl.RESIDUE_FLAT, "RESIDUE_NONE": dl.RESIDUE_NONE,
    "V502_DUST_LIVENESS_VERSION": dl.V502_DUST_LIVENESS_VERSION,
    "V502_STATE_EVERY_TICKS": dl.V502_STATE_EVERY_TICKS,
    "clip_tolerance_base": dl.clip_tolerance_base, "flat_residue": dl.flat_residue,
    "refusal_cooldown_ticks": dl.refusal_cooldown_ticks,
    "unique_market_reservation": dl.unique_market_reservation,
    "RESEED_REAL": ep.RESEED_REAL, "RESEED_DUST": ep.RESEED_DUST, "RESEED_DEFER": ep.RESEED_DEFER,
    "RESEED_CLIP": ep.RESEED_CLIP, "CLEAR_EPOCH": "CLEAR_EPOCH", "clear_book_runtime": ep.clear_book_runtime,
}


class _Agent:
    """The attributes the v5.0.2 code and the frozen compactor read, at their live defaults."""

    def __init__(self):
        self.emitted = []
        self._tick = 0
        self._open_positions = {}
        self._v502_residue = {}
        self._v502_counts = {}
        self._v502_refusal_streak = {}
        self._v502_market_notices = set()
        self._v502_compaction_seen = set()
        self._v502_errors = 0
        self.research_v502_flat_residue = True
        self.research_v502_clip_recognition = True
        self.research_v502_compactor_turn = True
        self.research_v502_market_terminal = True
        self._a1961_base_decimals = 4
        self._research_volume_decimals = 4
        self._research_exchange_min_order_size = MIN
        self._a196_legacy_dust_ledger = {}
        self._a1961_fee_residue = {}
        self._a1961_pending_seed = {}
        self._direct_pending_exposure_orders = {}
        # Strategy1_Research defaults for the compactor.
        self.research_dust_compact_enabled = True
        self.research_dust_safe_close = True
        self.research_dust_compact_min_fraction = 0.50
        self.research_dust_compact_adaptive = True
        self.research_dust_compact_prior_strength = 8.0
        self.research_dust_compact_prior_fill = 0.02
        self.research_dust_warn_ticks = 1000
        self.research_dust_compact_books_per_tick = 2
        self.research_dust_compact_cooldown_ticks = 8
        self.research_dust_compact_max_cooldown_ticks = 40
        self._research_dust_compact_learning = {}
        self._research_dust_compact_cooldown_skips = 0
        self._research_parked_dust = {}

    def _emit(self, event_type, force=False, **payload):
        self.emitted.append((event_type, payload))

    def _a196_volume_decimals(self, state=None):
        return 4

    def _a199_pending_table(self):
        return {}

    def _a199_note_transition(self, *args, **kwargs):
        return None


def _harness(*names):
    body = "".join(
        textwrap.indent(textwrap.dedent(_method_source(n, SOURCES.get(n, SIMPLE))), "    ") + "\n"
        for n in names
    )
    namespace = dict(NAMES, _Base=_Agent)
    exec("from __future__ import annotations\nclass Harness(_Base):\n" + body, namespace)
    return namespace["Harness"]()


def _lot(tick, qty, price):
    return (tick, qty, price, 0.0)


def _rows(agent, kind):
    return [payload for event, payload in agent.emitted if event == kind]


# ---- F1: a lifecycle ends at zero -----------------------------------------------------------------------

F1 = ("_v502_settle_flat_residue", "_v502_add_residue", "_v502_residue_abs", "_v502_count",
      "_position_tracker_snapshot", "_execution_flat_epsilon")


def test_the_residues_the_run_left_at_flat_are_classified():
    cases = {
        2.7569718699993473e-05: dl.RESIDUE_FLAT,    # book 121, after its 0.2501 exit at tick 7,952
        4.8999943739969076e-05: dl.RESIDUE_FLAT,    # book 68, the report's -0.249951 example
        -4.999999999999449e-05: dl.RESIDUE_FLAT,    # book 37
        -3.400002812997682e-05: dl.RESIDUE_FLAT,    # book 116
        5.699990479612893e-11: dl.RESIDUE_NOISE,    # book 94: 0.2500000000569999 - 0.25
        6.938893903907228e-18: dl.RESIDUE_NOISE,
        9.280034799999393e-05: dl.RESIDUE_NONE,     # book 47: past the epsilon, the round trip never closed
        0.0: dl.RESIDUE_NONE,
        -0.2499724302813: dl.RESIDUE_NONE,
    }
    for residue, kind in cases.items():
        assert dl.flat_residue(residue, flat_eps=EPS)[0] == kind, residue


def test_book_121_enters_a_whole_clip_once_its_residue_is_settled():
    # Tick 7,952 bought 0.2501 against -0.2500724 and left +0.0000276.  Tick 8,899 sold 0.25 and the
    # tracker read -0.2499724 -- dust, parked with no exit logic -- against a venue -0.25.
    for switch in (True, False):
        agent = _harness(*F1)
        agent.research_v502_flat_residue = switch
        agent._open_positions[121] = {"longs": [_lot(7952, 2.7569718699993473e-05, 238.36)], "shorts": []}
        local = agent._position_tracker_snapshot(121).net_qty + agent._v502_residue.get(121, 0.0)
        agent._v502_settle_flat_residue(121)
        assert agent._position_tracker_snapshot(121).net_qty + agent._v502_residue.get(121, 0.0) == local
        agent._open_positions[121]["shorts"].append(_lot(8899, 0.25, 240.53))
        net = agent._position_tracker_snapshot(121).net_qty
        dust = abs(net) + 1e-12 < MIN              # Strategy1_Research._is_dust_qty's upper bound
        if switch:
            assert net == -MIN and not dust
            assert agent._v502_residue == {121: 2.7569718699993473e-05}
            (row,) = _rows(agent, "V502_FLAT_RESIDUE")
            assert row["book"] == 121 and row["residue"] == 2.7569718699993473e-05
            assert agent._v502_counts == {"flat_residue_moved": 1}
        else:
            assert abs(net - (-0.2499724302813)) < 1e-12 and dust
            assert agent._v502_residue == {} and agent.emitted == []


def test_float_noise_is_zeroed_and_not_recorded():
    agent = _harness(*F1)
    agent._open_positions[94] = {"longs": [_lot(6604, 0.2500000000569999, 389.43)], "shorts": [_lot(6662, 0.25, 388.51)]}
    assert agent._v502_settle_flat_residue(94) == dl.RESIDUE_NOISE
    assert agent._position_tracker_snapshot(94).net_qty == 0.0
    assert agent._v502_residue == {} and agent.emitted == []
    assert agent._v502_counts == {"flat_noise_zeroed": 1}


def test_an_open_book_or_one_past_the_epsilon_is_left_alone():
    agent = _harness(*F1)
    agent._open_positions[47] = {"longs": [_lot(1, 9.280034799999393e-05, 306.13)], "shorts": []}
    agent._open_positions[5] = {"longs": [_lot(1, 0.25, 300.0)], "shorts": []}
    for book in (47, 5, 6):
        assert agent._v502_settle_flat_residue(book) == dl.RESIDUE_NONE, book
    assert agent._position_tracker_snapshot(47).net_qty == 9.280034799999393e-05
    assert agent._position_tracker_snapshot(5).net_qty == 0.25
    assert agent._v502_residue == {} and agent.emitted == [] and 6 not in agent._open_positions


def test_every_reader_of_local_base_adds_the_residue_back():
    agent = _harness(*F1, "_a195_local_base_by_book", "_a196_ledger_abs", "_a1961_fee_residue_abs")
    agent._open_positions[121] = {"longs": [_lot(7952, 2.7569718699993473e-05, 238.36)], "shorts": []}
    agent._a1961_fee_residue = {121: -0.0001}
    local = agent._a195_local_base_by_book({121: None})
    exposure = agent._a196_ledger_abs()
    agent._v502_settle_flat_residue(121)
    assert agent._a195_local_base_by_book({121: None}) == local           # reconciliation is unchanged
    assert abs(agent._a196_ledger_abs() - (exposure + 2.7569718699993473e-05)) < 1e-15
    assert ("fee_residue=residue.get(book_id, 0.0) + v502_residue.get(book_id, 0.0)"
            in _method_source("_a199_service_resync"))                     # and the rewind reseed keeps it


# ---- F2: a clip is a clip ---------------------------------------------------------------------------------

# A199_EPOCH_RESEED at tick 7,715: book, venue_net, tracker_before, local_before, A1.9.9 action and target.
RESEED_ROWS = (
    (18, -0.1551, -0.155, -0.155, "DUST", -0.1551),
    (36, 0.2498, 0.25, 0.25, "DUST", 0.2498),
    (37, 0.2341, 0.24995, 0.23415, "DUST", 0.2499),
    (47, 0.2432, 0.2499928, 0.2431928, "REAL", 0.25),
    (85, -0.2499, -0.24995552, -0.24995552, "DUST", -0.2499),
    (94, -0.2499, -0.25, -0.25, "DUST", -0.2499),
    (116, 0.2499, 0.249966, 0.249966, "DUST", 0.2499),
)


def _reseed(book, venue, tracker, local, *, clip_tolerance, mid=300.0):
    return ep.plan_book_reseed(
        book_id=book, venue_net=venue, tracker_net=tracker, ledger=0.0,
        fee_residue=local - tracker,          # ledger + fee residue on that book, as the reseed read it
        pending=0.0, min_order=MIN, volume_decimals=4, mid=mid, clip_tolerance=clip_tolerance,
    )


def test_the_rewind_reseed_rebuilds_each_frozen_clip_as_one_clip():
    frozen = 0.0
    for book, venue, tracker, local, action, target in RESEED_ROWS:
        old = _reseed(book, venue, tracker, local, clip_tolerance=0.0)
        assert (old.action, round(old.target, 8)) == (action, target), book       # A1.9.9, as logged
        new = _reseed(book, venue, tracker, local, clip_tolerance=TOL)
        if action == "DUST" and abs(target) > 0.2:
            frozen += abs(target)
            assert new.action == ep.RESEED_CLIP, book
            assert new.tracker_after == (MIN if target > 0 else -MIN), book
            assert abs(new.tracker_after + new.ledger_delta - target) < 1e-12, book   # local base unchanged
            assert 0.0 < abs(new.ledger_delta) <= TOL + 1e-12 and new.ledger_delta * target < 0, book
        else:
            assert (new.action, new.tracker_after, new.ledger_delta) == (old.action, old.tracker_after, old.ledger_delta)
    assert round(frozen, 4) == 1.2494


def test_a_clip_without_a_price_waits_and_the_tolerance_is_two_units():
    no_mid = _reseed(94, -0.2499, -0.25, -0.25, clip_tolerance=TOL, mid=None)
    assert no_mid.action == ep.RESEED_DEFER
    assert dl.clip_tolerance_base(4) == TOL and dl.clip_tolerance_base(None) == 0.0
    assert dl.clip_split(0.2498, min_order=MIN, tolerance=TOL) == (0.25, 0.2498 - 0.25)
    assert dl.clip_split(-0.2499, min_order=MIN, tolerance=TOL) == (-0.25, -0.2499 + 0.25)
    assert dl.clip_split(0.2497, min_order=MIN, tolerance=TOL) is None          # three units: still dust
    for qty in (0.25, 0.2501, 0.24999999999999997, 0.1661, 0.0):
        assert dl.clip_split(qty, min_order=MIN, tolerance=TOL) is None, qty
    assert dl.clip_split(0.2499, min_order=MIN, tolerance=0.0) is None


# A195_RECONCILE venue_net_map at tick 10,525: what a restart would seed from.
VENUE_AT_RESTART = {
    121: -0.25, 116: 0.2499, 85: -0.2499, 94: -0.2499, 28: -0.2343, 37: 0.2341, 36: 0.2244, 93: -0.182,
    21: -0.1788, 76: 0.1661, 48: -0.1619, 90: 0.1589, 77: -0.1575, 18: -0.1551, 44: -0.1547, 25: 0.1497,
    80: -0.1368,
}


def test_a_restart_seeds_the_inherited_clips_whole():
    plan = build_seed_plan(
        venue_net_by_book=VENUE_AT_RESTART, local_net_by_book={},
        mid_by_book={book: 300.0 for book in VENUE_AT_RESTART}, min_order=MIN, tick=1,
    )
    old = lb.split_seed_plan(plan, volume_decimals=4, min_order=MIN)
    new = lb.split_seed_plan(plan, volume_decimals=4, min_order=MIN, clip_tolerance=TOL)
    assert sorted(lot.book_id for lot in old.tracker_lots) == [121]
    assert {lot.book_id: lot.net_base for lot in new.tracker_lots} == {121: -0.25, 116: 0.25, 85: -0.25, 94: -0.25}
    assert new.clip_lots == 3 and set(new.clip_residue) == {85, 94, 116}
    assert all(abs(abs(value) - 0.0001) < 1e-12 for value in new.clip_residue.values())
    assert set(old.ledger) - set(new.ledger) == {85, 94, 116}
    assert round(old.legacy_dust_abs - new.legacy_dust_abs, 4) == 0.7497
    assert new.inherited_real == {121: -0.25, 116: 0.25, 85: -0.25, 94: -0.25}
    assert new.as_log()["v502_clip_lots"] == 3
    assert lb.split_seed_plan(plan, volume_decimals=4, min_order=MIN, clip_tolerance=0.0) == old   # A1.9.6


def test_the_agent_writes_a_reseeded_clip_and_its_shortfall():
    agent = _harness("_a199_apply_reseed", "_v502_add_residue", "_v502_count", "_v502_residue_abs",
                     "_position_tracker_snapshot")
    agent._a199_deferred = {}
    agent._open_positions = collections.defaultdict(lambda: {"longs": [], "shorts": []})
    plan = _reseed(94, -0.2499, -0.25, -0.25, clip_tolerance=TOL, mid=388.5)
    assert agent._a199_apply_reseed(plan, tick=7715) is True
    assert agent._open_positions[94] == {"longs": [], "shorts": [(7715, 0.25, 388.5, 0.0)]}
    assert set(agent._v502_residue) == {94} and abs(agent._v502_residue[94] - 0.0001) < 1e-12
    assert abs(agent._position_tracker_snapshot(94).net_qty + agent._v502_residue[94] - (-0.2499)) < 1e-12
    (row,) = _rows(agent, "A199_EPOCH_RESEED")
    assert row["action"] == ep.RESEED_CLIP and row["tracker_after"] == -0.25
    assert agent._v502_counts == {"clip_reseed": 1} and agent._a196_legacy_dust_ledger == {}


# ---- F3: a refusal costs the turn -------------------------------------------------------------------------

F3 = ("_select_dust_compaction_books", "_dust_compaction_learning_snapshot", "_is_compactable_dust",
      "_is_dust_qty", "_record_dust_compaction_attempt", "_execution_flat_epsilon",
      "_v502_note_compaction_refusal", "_v502_note_compaction_allowed", "_v502_count")

# Tracked dust at tick 10,500.  21 and 76 are refused (-8,565 and -1,404 bps against a -250 bps floor).
PARKED_AT_10500 = {21: -0.1788, 76: 0.1661, 48: -0.1619, 90: 0.1592, 77: -0.1575, 44: -0.1547, 25: 0.1497, 80: -0.1368}
REFUSED = {21, 76}


def _run_compactor(turn, ticks=200):
    agent = _harness(*F3)
    agent.research_v502_compactor_turn = turn
    agent._research_parked_dust = {book: {"net_base": qty, "first_tick": 0} for book, qty in PARKED_AT_10500.items()}
    state = SimpleNamespace(books={book: None for book in PARKED_AT_10500})
    picks = collections.Counter()
    for tick in range(10_000, 10_000 + ticks):
        agent._tick = tick
        for book in sorted(agent._select_dust_compaction_books(state)):
            picks[book] += 1
            if book in REFUSED:
                agent._v502_note_compaction_refusal(book)
            else:
                agent._v502_note_compaction_allowed(book)
                agent._record_dust_compaction_attempt(book)       # frozen: cooldown after an attempt
    return picks, agent


def test_two_refused_books_no_longer_hold_both_compactor_slots():
    before, _ = _run_compactor(False)
    assert dict(before) == {21: 200, 76: 200}       # ticks 8,000-10,500 of the run: nobody else, ever
    after, agent = _run_compactor(True)
    assert set(after) == set(PARKED_AT_10500)
    # Refused at 0, 8, 24, 48, 80, 120 and 160: the compactor's own backoff, 8 ticks up to 40.
    assert after[21] == after[76] == 7
    assert agent._v502_counts["compactor_refusal_turns"] == 14
    assert [dl.refusal_cooldown_ticks(s, base=8, maximum=40) for s in range(1, 8)] == [8, 16, 24, 32, 40, 40, 40]


def test_an_allowed_evaluation_ends_the_streak_and_the_floor_is_unchanged():
    agent = _harness(*F3)
    agent._tick = 100
    assert agent._v502_note_compaction_refusal(21) == 8
    agent._tick = 108
    assert agent._v502_note_compaction_refusal(21) == 16
    agent._v502_note_compaction_allowed(21)
    agent._tick = 124
    assert agent._v502_note_compaction_refusal(21) == 8
    assert agent._research_dust_compact_learning[21]["next_allowed_tick"] == 132
    assert agent._research_dust_compact_learning[21]["attempts"] == 0     # a refusal is not an attempt
    agent.research_v502_compactor_turn = False
    assert agent._v502_note_compaction_refusal(21) == 0
    kappa = (STRATEGY / "research_direct_dust_kappa.py").read_text()
    for literal in ("DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS = -60.0", "DIRECT_A175_DUST_PATIENCE_TICKS = 600",
                    "DIRECT_A175_DUST_ESCALATION_TICKS = 2000", "DIRECT_A175_DUST_MAX_FLOOR_BPS = -250.0"):
        assert literal in kappa, literal
    compact = _method_source("_direct_compact_selected_dust")
    assert (compact.index('"A1742_DUST_KAPPA_BLOCK"') < compact.index("self._v502_note_compaction_refusal(int(book_id))")
            < compact.index('"A1742_DUST_KAPPA_ALLOW"'))


# ---- F4: a market order ends when the venue processes it --------------------------------------------------

F4 = ("_v502_note_market_notice", "_v502_release_market_terminal", "_v502_count", "_direct_pending_ledger",
      "_direct_emit_book_ownership_release", "_position_tracker_snapshot", "_execution_flat_epsilon")


class MarketOrderPlacementEvent(SimpleNamespace):
    pass


class LimitOrderPlacementEvent(SimpleNamespace):
    pass


def _market(book, side, tick):
    return PendingExposureOrder(
        book_id=book, side=side, quantity=MIN, client_order_id=None, submitted_tick=tick,
        submitted_timestamp_ns=tick * 1_000_000_000, expiry_period_ns=0, order_kind="PLACE_ORDER_MARKET",
    )


def _notice(cls, book, side):
    notice = cls(bookId=book, side=side, orderId=1, success=True, quantity=MIN, clientOrderId=None)
    return notice, type(notice).__name__.upper()


def test_an_unfilled_market_exit_frees_its_book_on_the_next_state():
    agent = _harness(*F4)
    agent._open_positions[82] = {"longs": [], "shorts": [_lot(8009, MIN, 301.73)]}
    row = _market(82, "buy", 8015)
    agent._direct_pending_exposure_orders = {row.key(): row}
    agent._tick = 8015
    notice, phase = _notice(MarketOrderPlacementEvent, 82, 0)      # the venue sends a buy as 0
    assert agent._v502_note_market_notice(notice, phase=phase) is True
    assert agent._v502_release_market_terminal() == 1
    assert agent._direct_pending_exposure_orders == {}
    assert [r["reason"] for r in _rows(agent, "A17431_BOOK_OWNERSHIP_RELEASE")] == ["V502_MARKET_TERMINAL"]
    (terminal,) = _rows(agent, "V502_MARKET_TERMINAL")
    assert terminal["book"] == 82 and terminal["net_base"] == -MIN and terminal["held_ticks"] == 1
    assert agent._v502_release_market_terminal() == 0              # a notice is used once


def test_a_filled_exit_and_a_limit_order_keep_their_hold():
    agent = _harness(*F4)
    filled = _market(36, "buy", 10015)           # book 36's taker buy closed its short at tick 10,015
    limit = PendingExposureOrder(
        book_id=7, side="sell", quantity=MIN, client_order_id=5, submitted_tick=10015,
        submitted_timestamp_ns=0, expiry_period_ns=3_000_000_000, order_kind="PLACE_ORDER_LIMIT",
    )
    agent._direct_pending_exposure_orders = {filled.key(): filled, limit.key(): limit}
    agent._open_positions[7] = {"longs": [], "shorts": [_lot(1, MIN, 1.0)]}
    for cls, book, side in ((MarketOrderPlacementEvent, 36, 0), (LimitOrderPlacementEvent, 7, 1)):
        notice, phase = _notice(cls, book, side)
        agent._v502_note_market_notice(notice, phase=phase)
    assert agent._v502_release_market_terminal() == 0
    assert set(agent._direct_pending_exposure_orders) == {filled.key(), limit.key()}
    assert agent._v502_counts == {"market_filled_kept": 1}


def test_with_the_switch_off_a_market_exit_waits_for_local_expiry():
    agent = _harness(*F4)
    agent.research_v502_market_terminal = False
    agent._open_positions[82] = {"longs": [], "shorts": [_lot(8009, MIN, 301.73)]}
    row = _market(82, "buy", 8015)
    agent._direct_pending_exposure_orders = {row.key(): row}
    notice, phase = _notice(MarketOrderPlacementEvent, 82, 0)
    assert agent._v502_note_market_notice(notice, phase=phase) is False
    assert agent._v502_release_market_terminal() == 0 and list(agent._direct_pending_exposure_orders) == [row.key()]


def test_book_82_resends_every_tick_instead_of_every_fourth():
    # ABSOLUTE decided at tick 8,015 and the market buys went out at 8,015, 8,019, 8,023, 8,027 and 8,031:
    # each held the book until pending_order_live let it go, later in the request.  The fifth filled.
    def sends(terminal):
        agent = _harness(*F4)
        agent.research_v502_market_terminal = terminal
        agent._open_positions[82] = {"longs": [], "shorts": [_lot(8009, MIN, 301.73)]}
        ledger = agent._direct_pending_ledger()
        out = []
        for tick in range(8015, 8032):
            agent._tick = tick - 1                                   # respond() has not advanced it yet
            agent._v502_release_market_terminal()                   # top of respond(), v5.0.2
            if not ledger:                                           # the exit decides only on a free book
                out.append(tick)
                row = _market(82, "buy", tick)
                ledger[row.key()] = row
                notice, phase = _notice(MarketOrderPlacementEvent, 82, 0)
                agent._v502_note_market_notice(notice, phase=phase)  # arrives with the next state
            for key, row in list(ledger.items()):                    # _direct_reconcile_pending_exposure
                if not pending_order_live(row, current_tick=tick, current_timestamp_ns=tick * 1_000_000_000):
                    ledger.pop(key)
        return out

    assert sends(False) == [8015, 8019, 8023, 8027, 8031]
    assert sends(True) == list(range(8015, 8032))


def test_unique_market_reservation_refuses_ambiguity():
    market = _market(82, "buy", 1)
    limit = PendingExposureOrder(82, "buy", MIN, 9, 1, 0, 3_000_000_000, "PLACE_ORDER_LIMIT")
    other = _market(82, "sell", 1)
    ledger = {market.key(): market, limit.key(): limit, other.key(): other}
    assert dl.unique_market_reservation(ledger, book_id=82, side=0) == (market.key(), 1)
    assert dl.unique_market_reservation(ledger, book_id=82, side="sell") == (other.key(), 1)
    assert dl.unique_market_reservation(ledger, book_id=83, side=0) == (None, 0)
    twin = _market(82, "buy", 2)
    ledger2 = {("82", "a", "buy"): market, ("82", "b", "buy"): twin}
    assert dl.unique_market_reservation(ledger2, book_id=82, side="buy") == (None, 2)


# ---- telemetry, wiring, launcher --------------------------------------------------------------------------

def test_the_state_row_reports_near_full_dust_and_the_books_the_compactor_reached():
    agent = _harness("_v502_service", "_v502_residue_abs")
    agent._tick = 10_600
    agent._research_parked_dust = {121: {"net_base": -0.2499724302813}, 76: {"net_base": 0.1661}}
    agent._v502_compaction_seen = {21, 76, 44}
    agent._v502_refusal_streak = {21: 3}
    agent._research_dust_compact_learning = {21: {"next_allowed_tick": 10_624}}
    agent._a196_admission_last = {"final_slots": 0, "binding": "ABS", "dust_abs": 3.5357, "dust_exempt_abs": 2.0}
    agent._v502_service(None)
    (row,) = _rows(agent, "V502_DUST_STATE")
    assert row["near_full_parked"] == 1 and row["near_full_books"] == [121]
    assert row["compaction_books"] == [21, 44, 76] and row["refusal_cooling_books"] == [21]
    assert row["admission_binding"] == "ABS" and row["clip_tolerance"] == TOL
    assert agent._v502_compaction_seen == set()
    agent._tick = 10_601
    agent._v502_service(None)
    assert len(_rows(agent, "V502_DUST_STATE")) == 1


def test_v5_0_2_is_wired_and_launched():
    on_trade = _method_source("onTrade")
    assert on_trade.index("super().onTrade(event, validator)") < on_trade.index("self._v502_settle_flat_residue(int(book_id))")
    assert "v502_residue.get(book_id, 0.0)" in _method_source("_a195_local_base_by_book")
    assert "clip_tolerance=clip_tolerance" in _method_source("_a199_service_resync")
    assert "elif plan.action == RESEED_CLIP:" in _method_source("_a199_apply_reseed")
    assert "+ self._v502_residue_abs()" in _method_source("_a196_ledger_abs")
    seed = _method_source("_a195_seed_inventory_from_venue")
    assert "clip_tolerance=self._v502_clip_tolerance(state)" in seed and "split.clip_residue" in seed
    notices = _method_source("_log_notices")
    assert (notices.index("self._direct_note_placement_identity_notice(notice, phase=phase)")
            < notices.index("self._v502_note_market_notice(notice, phase=phase)"))
    respond = _method_source("respond")
    assert (respond.index("self._a199_service_resync(state)") < respond.index("self._v502_release_market_terminal()")
            < respond.index("response = super().respond(state)"))
    assert respond.index("self._v501_service(state)") < respond.index("self._v502_service(state)")
    for switch in ("research_v502_flat_residue", "research_v502_clip_recognition",
                   "research_v502_compactor_turn", "research_v502_market_terminal"):
        assert f'self.{switch} = self._as_bool(\n            getattr(self.config, "{switch}", True)' in SIMPLE, switch
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_3"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_3"' in SIMPLE
    assert dl.V502_DUST_LIVENESS_VERSION.endswith("v5_0_2")
    for key in ("direct_v502_dust_liveness_version", "direct_v502_flat_residue", "direct_v502_clip_recognition",
                "direct_v502_compactor_turn", "direct_v502_market_terminal", "direct_v502_residue_abs",
                "direct_v502_flat_residue_moved", "direct_v502_clips", "direct_v502_refusal_turns",
                "direct_v502_market_released", "direct_v502_errors", "direct_v501_errors"):
        assert f'stats["{key}"]' in SIMPLE, key
    assert ("strategy1_direct_v5_0_2) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; "
            "A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; V502_BUILD=1 ;;") in LAUNCHER
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"', start + len('PARAMS="'))]
    for switch in ("research_v502_flat_residue=1", "research_v502_clip_recognition=1",
                   "research_v502_compactor_turn=1", "research_v502_market_terminal=1",
                   "research_v501_activity_alignment=1", "research_a1961_fee_residue_ledger=1"):
        assert switch in params, switch
    assert "tests/test_research_v5_0_2_dust_liveness.py" in LAUNCHER
    assert "[preflight] v5.0.2 dust liveness PASS" in LAUNCHER
    guards = {
        "self._v502_settle_flat_residue(int(book_id))": SIMPLE,
        "fee_residue=residue.get(book_id, 0.0) + v502_residue.get(book_id, 0.0),": SIMPLE,
        "return plan(RESEED_CLIP, tracker_after=clip[0], ledger_delta=clip[1], px=price)": EPOCH,
        "clip = clip_split(net, min_order=floor, tolerance=clip_tolerance)": LEGACY,
        "self._v502_note_compaction_refusal(int(book_id))": SIMPLE,
        "self._v502_release_market_terminal()": SIMPLE,
    }
    for literal, source in guards.items():
        assert literal in source and literal in LAUNCHER, literal


def test_the_frozen_baseline_is_untouched_and_the_helpers_are_pure():
    assert 'RESEARCH_POLICY_VERSION = "simplified_hybrid_authority_v4_16_2"' in RESEARCH
    assert "v502" not in RESEARCH and "v502" not in STRATEGY1
    tree = ast.parse(LIVENESS)
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imported == {"__future__", "typing", "research_direct_book_ownership"}
