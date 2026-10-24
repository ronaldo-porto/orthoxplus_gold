"""v6.40 the three-class layout (SN79 0.6.5): per-class plumbing, the class arms (C2P / C2D / C2J / C2Q / C1W) and k138's
class-guard / S14 fixes -- research_v640_* (scratchpad k139; rules in research_v6_40_classes).

A mixin, first among Simple's bases: every v6.40 method lives here; Simple and the older parts carry only the hooks that
call them (tests/_v640_undo.py lists those edits; their reverse is v6.39's text byte for byte).  Two methods are
GENERATED from the live source by the build (scratchpad k139/build/gen_part.py) and pinned by tests/test_research_v6_40.py
(their AST with the documented substitutions reversed == the original's):
  _v6_40_deep_core  = v6.3.3's _v633_book with every depth rule in the book's depth unit ``du`` -- every ``tick_size``
                      read becomes ``du`` except the touch margins: v633_deep_price / v64_vacuum_price become
                      deep_price_du / vacuum_price_du(du=du, tick=tick_size) (depth in du, at least one venue tick behind
                      / inside the touch: vm738's deep layer) -- and the placed size in the class's units: the lot
                      v62_lot_quantity(q_ x scale, the class's volume decimals), checked against the class's minimum
                      (also v6.9.1's free-base sizing), the machinery itself in class-0 units (k137's size rule);
  _v6_40_touch_core = v6.17's _v617_touch with its rest band from _v6_40_touch_units (C2J on class 2: the class's depth
                      unit, REST_TICKS / + 1; else v6.17's own), the new quote's price from _v6_40_touch_price (C2J's join
                      or one depth unit behind; else v6.17's join + v6.31's improve, moved there from the copy) and the
                      placed size in the class's units as above.
Off (every switch at its PARAMS default) nothing here decides anything: the grids are empty, the foreign set is v6.23's
and every routed call goes to the v6.39 method."""
from __future__ import annotations

import math
from types import SimpleNamespace

from research_direct_exit_refresh import ABSENT_REPRICE_CANCEL
from research_v62_breadth import V62_STATE_EVERY_TICKS
from research_v617_touch_quote import (REST_TICKS as V6_40_REST_TICKS, THRESHOLD_BPS as V617_THRESHOLD_BPS,
                                       edge_bps as v617_edge_bps, wanted_price as v617_wanted_price)
from research_v623_validator_063 import LAYOUT_FOREIGN as V623_LAYOUT_FOREIGN, LAYOUT_SAME as V623_LAYOUT_SAME
from research_v623_validator_063 import LAYOUT_UNREADABLE as V623_LAYOUT_UNREADABLE
from research_v6_32_fee_making import maker_fee_rate as v6_32_maker_fee_rate
from research_v6_36_step_withdrawal import l1_of as v6_36_l1_of, mid_of as v6_36_mid_of
from research_v611_wire import lift_instruction_prices as v611_lift_instruction_prices
from research_v6_40_classes import (
    CANCEL_C2D as V6_40_CANCEL_C2D,
    CANCEL_C2Q as V6_40_CANCEL_C2Q,
    CLASS1 as V6_40_CLASS1,
    CLASS2 as V6_40_CLASS2,
    ClassCaptureTracker as V6_40ClassTracker,
    ClassGaugeFeed as V6_40ClassGaugeFeed,
    DEFAULT_TRADED as V6_40_DEFAULT_TRADED,
    HOME_CLASS as V6_40_HOME,
    STATUS_OK as V6_40_STATUS_OK,
    V6_40_CLASSES_VERSION,
    block_of as v6_40_block_of,
    book_grids as v6_40_book_grids,
    c2j_behind_price as v6_40_c2j_behind_price,
    c2j_rest_ticks as v6_40_c2j_rest_ticks,
    c2q_decision as v6_40_c2q_decision,
    class_books as v6_40_class_books,
    class_digest as v6_40_class_digest,
    class_table as v6_40_class_table,
    deep_price_du as v6_40_deep_price_du,
    flush_ns as v6_40_flush_ns,
    init_price_foreign as v6_40_init_price_foreign,
    layout_key as v6_40_layout_key,
    live_pool_factor as v6_40_live_pool_factor,
    making_beta as v6_40_making_beta,
    merge_foreign as v6_40_merge_foreign,
    parse_level as v6_40_parse_level,
    parse_pf as v6_40_parse_pf,
    parse_traded as v6_40_parse_traded,
    vacuum_price_du as v6_40_vacuum_price_du,
)
V6_40_MARKET_FACTOR = 3.0      # STRUCTURAL: a book's mid within x3 of its class's initial price is that class's market
                                # (0.6.5's classes sit 10x apart: 300 / 29.57; a run's fundamental drifts far less)
V6_40_C1W_RADIUS = 1e9          # C1W: a radius no order of ours can be outside (|p - mid| <= 1e9 x mid; v6.36's in_radius
                                # reads a finite radius only)
# ---- GENERATED IMPORTS (k139 build/gen_part.py: Simple's own imports of the copies' names) ----
from typing import Any
from taos.im.protocol.models import LoanSettlementOption
from taos.im.protocol.models import OrderDirection
from taos.im.protocol.models import STP
from taos.im.protocol.models import TimeInForce
from research_v617_touch_quote import CANCEL_BOUND as V617_CANCEL_BOUND
from research_v617_touch_quote import CANCEL_NO_FP as V617_CANCEL_NO_FP
from research_v617_touch_quote import CANCEL_PACED as V617_CANCEL_PACED
from research_v617_touch_quote import CLIP_DEEP_CLIPS as V617_CLIP_DEEP_CLIPS
from research_v617_touch_quote import TOUCH_SLOT as V617_TOUCH_SLOT
from research_v619_surplus_hold import CANCEL_SURPLUS as V619_CANCEL_SURPLUS
from research_v623_validator_063 import CANCEL_AGAINST as V623_CANCEL_AGAINST
from research_v633_deep_layer import CANCEL_NO_ROOM as V633_CANCEL_NO_ROOM
from research_v633_deep_layer import CANCEL_OWNS_BOOK as V633_CANCEL_OWNS_BOOK
from research_v633_deep_layer import CANCEL_REPRICE as V633_CANCEL_REPRICE
from research_v63_trend_target import SIDE_BUY as V63_SIDE_BUY
from research_v63_trend_target import SIDE_SELL as V63_SIDE_SELL
from research_v641_pace import CANCEL_PACED as V641_CANCEL_PACED
from research_v66_add_spacing import CANCEL_SPACING as V66_CANCEL_SPACING
from research_v69_deep_ladder import CANCEL_LEVEL_OFF as V69_CANCEL_LEVEL_OFF
from research_v69_deep_ladder import LADDER_QUANTILES as V69_LADDER_QUANTILES
from research_v69_deep_ladder import WHOLE_SIDE as V69_WHOLE_SIDE
from research_v6_29_touch_move import CANCEL_FP_MOVE as V6_29_CANCEL_FP_MOVE
from research_v6_32_fee_making import CANCEL_FEE_GATE as V6_32_CANCEL_FEE_GATE
from research_v6_33_touch_identity import CANCEL_TOUCH_IDENTITY as V6_33_CANCEL_TOUCH_IDENTITY
from research_v612_reduce_depth import level0_depth as v612_level0_depth
from research_v617_touch_quote import client_ids as v617_client_ids
from research_v617_touch_quote import placed_sides as v617_placed_sides
from research_v617_touch_quote import room as v617_room
from research_v617_touch_quote import toward_side as v617_toward_side
from research_v617_touch_quote import verdict as v617_verdict
from research_v618_centre_off import centre_on as v618_centre_on
from research_v620_room_pace import scaled_quantity as v620_scaled_quantity
from research_v62_breadth import lot_quantity as v62_lot_quantity
from research_v633_deep_layer import client_ids as v633_client_ids
from research_v633_deep_layer import deep_level as v633_deep_level
from research_v633_deep_layer import level_client_ids as v633_level_client_ids
from research_v633_deep_layer import needs_reprice as v633_needs_reprice
from research_v641_pace import adds as v641_adds
from research_v64_board import vacuum_depth as v64_vacuum_depth
from research_v661_vol_bound import room as v661_room
from research_v66_add_spacing import adds_to_position as v66_adds_to_position
from research_v69_deep_ladder import quantity_within_free as v691_quantity_within_free
from research_v69_deep_ladder import deep_first_keeps as v69_deep_first_keeps
from research_v69_deep_ladder import ladder_levels as v69_ladder_levels
from research_v69_deep_ladder import level_quantity as v69_level_quantity
from research_v69_deep_ladder import live_slots as v69_live_slots
from research_v69_deep_ladder import slot_taken as v69_slot_taken
from research_v6_38_kbl import join as v6_38_kbl_join
from research_v6_38_kbl import verdict as v6_38_kbl_verdict


class V640Part:
    """v6.40 the three-class layout (research_v640_*): per-class plumbing, the class arms and the class-guard / S14 fixes"""

    # ---- switches and state (init) -----------------------------------------------------------------------------------

    def _v6_40_init(self) -> None:
        """v6.40, in _init_build_switches after v6.39's statements: every research_v640_* key with its default (the
        traded classes "0", every arm 0, the four B4 fixes 1) and the empty per-request state.  With C2Q on, S14's
        tracker is made as C2Q's class-split capture tracker (its base results unchanged).  Never raises: an error counts
        and every switch keeps its default."""
        self._v6_40_counts: dict[str, int] = {}
        self._v6_40_errors = 0
        self._v6_40_grids: dict = {}
        self._v6_40_grids_all: dict = {}
        self._v6_40_vgrids: dict = {}
        self._v6_40_decs: dict = {}
        self._v6_40_table = None
        self._v6_40_key = None
        self._v6_40_digest = ""
        self._v6_40_last_foreign = None
        self._v6_40_c1 = frozenset()
        self._v6_40_c2 = frozenset()
        self._v6_40_c2q = {"armed": True, "probe": True, "block": None}
        self._v6_40_c2q_tick = None
        self._v6_40_gauges = None           # C2Q's class-gauge feed (started by C2Q's first decision only)
        self._v6_40_sim_id = None
        self._v6_40_reported = False
        self._v6_40_layout_changed = False
        self._v6_40_rejects: dict[str, int] = {}     # venue rejects since start, "c<class>:<reason>" (telemetry only)
        self._v6_40_c2q_verdict = None      # C2Q's last block verdict (armed / disarmed / probe; telemetry only)
        self._v6_40_c2q_changed = False
        cfg = getattr(self, "config", None)
        raw_traded = getattr(cfg, "research_v640_traded_classes", V6_40_DEFAULT_TRADED)
        traded = v6_40_parse_traded(raw_traded)
        self._v6_40_traded_raw = str(raw_traded)
        self._v6_40_traded_ok = traded is not None
        self._v6_40_traded = traded if traded is not None else frozenset({V6_40_HOME})
        self.research_v640_traded_classes = "+".join(str(c) for c in sorted(self._v6_40_traded))
        self.research_v640_c2du = int(v6_40_parse_level(getattr(cfg, "research_v640_c2du", 0), (0, 1)))
        self.research_v640_c2d = int(v6_40_parse_level(getattr(cfg, "research_v640_c2d", 0), (0, 1)))
        self.research_v640_c2j = int(v6_40_parse_level(getattr(cfg, "research_v640_c2j", 0), (0, 1, 2)))
        self.research_v640_c2q = int(v6_40_parse_level(getattr(cfg, "research_v640_c2q", 0), (0, 1)))
        self.research_v640_c2q_pf = v6_40_parse_pf(getattr(cfg, "research_v640_c2q_pf", 0))
        self.research_v640_c1w = int(v6_40_parse_level(getattr(cfg, "research_v640_c1w", 0), (0, 1)))
        self.research_v640_guard_hold = self._as_bool(getattr(cfg, "research_v640_guard_hold", True))
        self.research_v640_guard_init_price = self._as_bool(getattr(cfg, "research_v640_guard_init_price", True))
        self.research_v640_s14_class_key = self._as_bool(getattr(cfg, "research_v640_s14_class_key", True))
        self.research_v640_s14_flush_spec = self._as_bool(getattr(cfg, "research_v640_s14_flush_spec", True))
        try:
            if self.research_v640_c2q:
                # C2Q: S14's tracker carries the class split (k124's K3Q capture tracker underneath; S14's own estimate
                # and counts are the base class's exactly)
                self._v6_32_tracker = V6_40ClassTracker(weight=float(getattr(self, "research_v632_fee_weight", 0.0)
                                                                     or 0.0))
        except Exception:
            self._v6_40_errors += 1

    def _v6_40_count(self, key: str, n: int = 1) -> None:
        """v6.40's tally.  Never raises."""
        try:
            counts = getattr(self, "_v6_40_counts", None)
            if counts is None:
                counts = {}
                self._v6_40_counts = counts
            counts[key] = int(counts.get(key, 0) or 0) + int(n)
        except Exception:
            self._v6_40_errors = int(getattr(self, "_v6_40_errors", 0) or 0) + 1

    def _v6_40_arms_on(self) -> bool:
        return bool(getattr(self, "research_v640_c2d", 0) or getattr(self, "research_v640_c2j", 0)
                    or getattr(self, "research_v640_c2q", 0) or getattr(self, "research_v640_c1w", 0))

    # ---- the class map, the foreign set and the grids (in v6.23's observe) ------------------------------------------

    def _v6_40_observe(self, state, foreign, layout, guard_on: bool):
        """v6.40 in _v6_23_observe right after v6.23's layout read: (foreign, layout) of this request.  The class map is
        read when the configuration's class list changed (layout_key); the traded non-home classes get their per-book
        grids (the pass, the final validator, the lift, v6.36's drop read them); with the class guard on, the foreign
        set is v6.23's + B4(3)'s initial-price books less the traded classes' books, and an UNREADABLE class list keeps
        the last readable foreign set (B4(2), research_v640_guard_hold).  Default (home only) on a readable layout of
        today: v6.23's set exactly and no grid.  Never raises: an error counts, no grid this request and (guard_hold) the
        last readable foreign set."""
        try:
            cfg = getattr(state, "config", None)
            self._v6_40_sim_id = getattr(cfg, "simulation_id", None) if not isinstance(cfg, dict) else cfg.get("simulation_id")
            key = v6_40_layout_key(cfg)
            table = self._v6_40_table
            if table is None or key != self._v6_40_key:
                table = v6_40_class_table(cfg)
                self._v6_40_table, self._v6_40_key = table, key
                self._v6_40_digest = v6_40_class_digest(cfg)
                traded = self._v6_40_traded
                grids = v6_40_book_grids(table, traded, bool(self.research_v640_c2du))
                self._v6_40_grids = grids
                self._v6_40_vgrids = {b: (g.tick, g.dec, g.min_order) for b, g in grids.items()}
                self._v6_40_decs = {b: g.dec for b, g in grids.items()}
                self._v6_40_c1 = v6_40_class_books(table, V6_40_CLASS1)
                self._v6_40_c2 = v6_40_class_books(table, V6_40_CLASS2)
                tracker = getattr(self, "_v6_32_tracker", None)
                if isinstance(tracker, V6_40ClassTracker):
                    tracker.class_of = dict(table.get("book_class") or {})
                self._v6_40_layout_changed = True
                self._v6_40_count("layout_reads")
                self._v6_40_grids_all = grids
            off = self._v6_40_market_check(state)        # this request's traded books on another market
            if off:
                foreign = frozenset(foreign) | off
            if not guard_on:
                return foreign, layout
            if layout == V623_LAYOUT_UNREADABLE:
                held = self._v6_40_last_foreign
                if self.research_v640_guard_hold and held is not None:
                    self._v6_40_count("guard_held_states")       # B4(2): fail closed -- the last readable set
                    return held, layout
                return foreign, layout
            extra = v6_40_init_price_foreign(cfg, table) if self.research_v640_guard_init_price else frozenset()
            if extra - frozenset(foreign):
                self._v6_40_count("init_price_foreign_states")
            out = v6_40_merge_foreign(foreign, table, init_price=extra, traded=self._v6_40_traded) | off
            if out != frozenset(foreign):
                layout = V623_LAYOUT_FOREIGN if out else V623_LAYOUT_SAME
            self._v6_40_last_foreign = out
            return out, layout
        except Exception:
            self._v6_40_errors = int(getattr(self, "_v6_40_errors", 0) or 0) + 1
            self._v6_40_grids, self._v6_40_vgrids, self._v6_40_decs = {}, {}, {}
            self._v6_40_grids_all = {}
            self._v6_40_table, self._v6_40_key = None, None
            held = getattr(self, "_v6_40_last_foreign", None)
            if guard_on and getattr(self, "research_v640_guard_hold", False) and held is not None:
                return held, layout
            return foreign, layout

    def _v6_40_market_check(self, state) -> frozenset:
        """v6.40 (STRUCTURAL safety): a traded non-home book trades only while its market is its class's -- its L1 mid within
        a factor MARKET_FACTOR of the class's initial price; a book whose price says another market (the validator's
        three-class map on the old simulator, 23:50-00:05 JST 10-24: books 64-95 at 300 mapped as the 29.57 class) is
        foreign this request: no grid, its cancels kept, no new order.  Never raises: an error makes every traded
        non-home book foreign this request (fail closed).  Returns the books held."""
        grids = getattr(self, "_v6_40_grids_all", None) or {}
        if not grids:
            return frozenset()
        try:
            books = getattr(state, "books", None)
            raw = getattr(books, "_raw_books", None)
            src = raw if isinstance(raw, dict) else (books or {})
            table = self._v6_40_table or {}
            p0 = {r["c"]: r.get("init_price") for r in (table.get("classes") or ())}
            off = set()
            for b, g in grids.items():
                book = src.get(b, src.get(str(b))) if hasattr(src, "get") else None
                bid, ask = v6_36_l1_of(book) if book is not None else (None, None)
                mid = v6_36_mid_of(bid, ask)
                ref = p0.get(g.c)
                if mid is not None and ref and not (ref / V6_40_MARKET_FACTOR <= mid <= ref * V6_40_MARKET_FACTOR):
                    off.add(int(b))
            if off:
                self._v6_40_count("market_mismatch_book_states", len(off))
                self._v6_40_grids = {b: g for b, g in grids.items() if b not in off}
            else:
                self._v6_40_grids = grids
            self._v6_40_vgrids = {b: (g.tick, g.dec, g.min_order) for b, g in self._v6_40_grids.items()}
            self._v6_40_decs = {b: g.dec for b, g in self._v6_40_grids.items()}
            return frozenset(off)
        except Exception:
            self._v6_40_errors = int(getattr(self, "_v6_40_errors", 0) or 0) + 1
            self._v6_40_grids, self._v6_40_vgrids, self._v6_40_decs = {}, {}, {}
            return frozenset(int(b) for b in grids)

    def _v6_40_c1_market(self, state, books) -> frozenset:
        """v6.34's class-1 entry (C1W's path) under the same market check: the books of its map whose L1 mid is within
        MARKET_FACTOR of class 1's initial price (a book without an L1 stays); an unreadable class map or an error:
        none (fail closed: they stay foreign)."""
        try:
            table = self._v6_40_table or {}
            ref = next((r.get("init_price") for r in (table.get("classes") or ()) if r["c"] == V6_40_CLASS1), None)
            if not ref:
                return frozenset() if table.get("status") == V6_40_STATUS_OK else frozenset(books)
            src = getattr(getattr(state, "books", None), "_raw_books", None)
            if not isinstance(src, dict):
                src = getattr(state, "books", None) or {}
            keep = set()
            for b in books:
                book = src.get(b, src.get(str(b))) if hasattr(src, "get") else None
                mid = v6_36_mid_of(*v6_36_l1_of(book)) if book is not None else None
                if mid is None or ref / V6_40_MARKET_FACTOR <= mid <= ref * V6_40_MARKET_FACTOR:
                    keep.add(int(b))
            if len(keep) < len(books):
                self._v6_40_count("c1_market_mismatch_book_states", len(books) - len(keep))
            return frozenset(keep)
        except Exception:
            self._v6_40_errors = int(getattr(self, "_v6_40_errors", 0) or 0) + 1
            return frozenset()

    def _v6_40_held_foreign(self) -> frozenset:
        """B4(2), in respond() when v6.23's observe raised: the last readable foreign set (none read yet: empty, v6.22)."""
        held = getattr(self, "_v6_40_last_foreign", None)
        if held is not None:
            self._v6_40_count("guard_held_raises")
            return held
        return frozenset()

    # ---- S14 (B4 5 / 6) ------------------------------------------------------------------------------------------------

    def _v6_40_s14_key(self, cfg, key):
        """B4(5): S14's minimum-size cache key -- (simulation id, the class list's digest); no simulation id: as v6.39
        (no cache)."""
        if not key:
            return key
        digest = getattr(self, "_v6_40_digest", None)
        if digest is None:
            digest = v6_40_class_digest(cfg)
        return (key, digest)

    def _v6_40_flush(self, tracker, spec) -> None:
        """B4(6): the tracker's capture flush for this state's validator release (60 sim-s below 0.6.5, 300 from it)."""
        try:
            want = int(v6_40_flush_ns(spec))
            if int(getattr(tracker, "flush_ns", want)) != want:
                tracker.flush_ns = want
                self._v6_40_count("flush_set_%ds" % (want // 1_000_000_000))
        except Exception:
            self._v6_40_errors = int(getattr(self, "_v6_40_errors", 0) or 0) + 1

    # ---- the request (after v6.36's) -----------------------------------------------------------------------------------

    def _v6_40_request(self, state) -> None:
        """v6.40 in respond() right after v6.36 set this state's plan: C2Q's maker fee rates for its tracker's next
        releases; C1W (research_v640_c1w, with v6.34's class-1 entry and v6.36's step withdrawal): on the pre-step state
        every class-1 book of v6.34's map joins v6.36's plan with a radius no price can leave -- every resting order of ours on
        it is cancelled and every new one dropped (v6.36's own drop / cancel), so none rests through the step; then the
        V6_40_STATE row.  Never raises: an error counts and nothing changes this request."""
        try:
            if self.research_v640_c2q:
                tracker = getattr(self, "_v6_32_tracker", None)
                if isinstance(tracker, V6_40ClassTracker):
                    rates = {}
                    for b, acct in (getattr(self, "accounts", None) or {}).items():
                        rate = v6_32_maker_fee_rate(acct)
                        if rate is not None:
                            rates[int(b)] = float(rate)
                    tracker.rates = rates
            if (self.research_v640_c1w and bool(getattr(self, "research_v634_class1_entry", False))
                    and bool(getattr(self, "research_v636_step_withdrawal", False))):
                req = getattr(self, "_v6_36_req", None) or {}
                if req.get("pre_step"):
                    plan = req.setdefault("books", {})
                    l1 = getattr(self, "_v6_36_l1", None) or {}
                    for b in sorted(getattr(self, "_v6_34_c1", None) or ()):
                        prev = plan.get(b)
                        last = l1.get(b)
                        mid = v6_36_mid_of(last[0], last[1]) if last else None
                        if mid is None:
                            mid = prev[0] if prev and prev[0] else 1.0
                        plan[b] = (mid, V6_40_C1W_RADIUS, prev[2] if prev else None)
                        self._v6_40_count("c1w_pull_books")
                    self._v6_40_count("c1w_pre_step_states")
        except Exception:
            self._v6_40_errors = int(getattr(self, "_v6_40_errors", 0) or 0) + 1
        self._v6_40_report()

    # ---- the pass: per-book grid, routing -------------------------------------------------------------------------------

    def _v6_40_du(self, book_id: int, tick_size):
        g = (getattr(self, "_v6_40_grids", None) or {}).get(int(book_id))
        return tick_size if g is None else g.du

    def _v6_40_units(self, book_id: int, min_order, cfg):
        """(size scale, the class's minimum, its volume decimals) of a book -- a traded non-home class's (k137's size
        rule), else (1, ``min_order``, the top level's volume decimals): v6.39's."""
        g = (getattr(self, "_v6_40_grids", None) or {}).get(int(book_id))
        if g is None:
            return 1.0, min_order, getattr(cfg, "volumeDecimals", 4)
        return g.scale, g.min_order, g.vol_dec

    def _v6_40_caps_books(self, books, n: int) -> int:
        """v6.3's universe caps count every book in its own class's units: a scaled class's book as ``scale`` books."""
        try:
            grids = getattr(self, "_v6_40_grids", None) or {}
            total = sum((grids[int(b)].scale if int(b) in grids else 1.0) for b in books)
            return max(int(n), int(math.ceil(total - 1e-9)))
        except Exception:
            self._v6_40_errors = int(getattr(self, "_v6_40_errors", 0) or 0) + 1
            return int(n)

    def _v6_40_book(self, response, book_id, deep, depth, raw_bid, raw_ask, inv, rows, deep_rows, touch_rows, already,
                    req, *, touch_on: bool, tick_size, dec, clip, min_order, budget, expiry, cfg, paced: bool = False,
                    bound_scale: float = 1.0, held: frozenset = frozenset(), size_scale: float = 1.0,
                    cap_skip: bool = False, against: frozenset = frozenset()) -> int:
        """v6.40, in the pass for a book of a traded non-home class (research_v640_traded_classes): v6.3.3's deep layer
        (_v6_40_deep_core: the depth rules in ``du``, the sizes in the class's scale) then, with the touch quote on, its
        touch quote (_v6_40_touch_book) -- the calls and arguments of v6.39's two branches (the launch clip counted as
        there).  ``inv`` is in class-0 units (the pass divided it by the scale)."""
        if clip > float(getattr(deep, "clip", 0.0) or 0.0) + 1e-12:
            self._v615_count("launch_clip_book_states")
        placed = self._v6_40_deep_core(
            response, book_id, deep, depth, raw_bid, raw_ask, inv, rows, deep_rows, already, req,
            tick_size=tick_size, dec=dec, clip=clip, min_order=min_order, budget=budget, expiry=expiry, cfg=cfg,
            paced=paced, bound_scale=bound_scale, held=held, size_scale=size_scale, cap_skip=cap_skip, against=against)
        if touch_on:
            placed += self._v6_40_touch_book(response, book_id, deep, raw_bid, raw_ask, inv, touch_rows, already, req,
                                             tick_size=tick_size, min_order=min_order, budget=budget, expiry=expiry,
                                             cfg=cfg, paced=paced, held=held, size_scale=size_scale, cap_skip=cap_skip)
        self._v6_40_count("book_states_c%d" % int(((getattr(self, "_v6_40_grids", None) or {}).get(int(book_id))
                                                   or SimpleNamespace(c=-1)).c))
        return placed

    def _v6_40_touch_book(self, response, book_id: int, deep, raw_bid, raw_ask, inv, touch_rows, already, req,
                          **kw) -> int:
        """v6.40: a traded non-home book's touch quote -- class 2 with C2D: none (a resting one cancelled); else v6.17's
        quote on the class's grid and size scale (_v6_40_touch_core): C2J on class 2 never improves (join / one depth
        unit behind) and reads its rest band in depth units; otherwise v6.17's join and v6.31's improve as on class 0
        (C2P, the plain class-1 / class-2 enables).  Never raises: an error counts and nothing is placed."""
        if not bool(getattr(self, "research_v617_touch_quote", False)):
            return 0
        g = (getattr(self, "_v6_40_grids", None) or {}).get(int(book_id))
        c2 = g is not None and g.c == V6_40_CLASS2
        try:
            if c2 and self.research_v640_c2d:
                doomed = [(row, V6_40_CANCEL_C2D) for row in touch_rows if int(row.order_id) not in already]
                self._v617_cancel(response, book_id, doomed, req, int(kw.get("budget", 5)))
                self._v6_40_count("c2d_book_states")
                return 0
            if c2 and self.research_v640_c2j:
                self._v6_40_count("c2j_book_states")
            return int(self._v6_40_touch_core(response, book_id, deep, raw_bid, raw_ask, inv, touch_rows, already, req,
                                              **kw))
        except Exception:
            self._v6_40_errors = int(getattr(self, "_v6_40_errors", 0) or 0) + 1
            return 0

    def _v6_40_c2j_on(self, book_id: int) -> bool:
        g = (getattr(self, "_v6_40_grids", None) or {}).get(int(book_id))
        return bool(g is not None and g.c == V6_40_CLASS2 and getattr(self, "research_v640_c2j", 0))

    def _v6_40_touch_units(self, book_id: int, tick_size):
        """The touch quote's rest band: C2J on class 2 -- (the class's depth unit, REST_TICKS (c2j=1) or REST_TICKS + 1
        (c2j=2)); otherwise v6.17's own (the venue tick, REST_TICKS)."""
        if not self._v6_40_c2j_on(book_id):
            return tick_size, V6_40_REST_TICKS
        g = self._v6_40_grids[int(book_id)]
        return g.unit, v6_40_c2j_rest_ticks(self.research_v640_c2j, V6_40_REST_TICKS)

    def _v6_40_touch_price(self, side, raw_bid, raw_ask, fp, tick_size, book_id):
        """A new touch quote's price: C2J c2j=2 one depth unit behind the best price (vm739 _c2j_behind) when its own
        edge clears the hurdle; C2J c2j=1 v6.17's join (never improved); otherwise v6.17's join (its post-only cushion,
        the fp-edge hurdle) and, when it exists, v6.31 S12b's improve -- v6.17's own order of the two; None: no price."""
        c2j = int(getattr(self, "research_v640_c2j", 0) or 0) if self._v6_40_c2j_on(book_id) else 0
        if c2j == 2:
            g = self._v6_40_grids[int(book_id)]
            px = v6_40_c2j_behind_price(side, bid=raw_bid, ask=raw_ask, du=g.unit, decimals=g.dec)
            if px is None:
                return None
            e = v617_edge_bps(side, px, fp)
            return px if e is not None and e >= float(V617_THRESHOLD_BPS) - 1e-9 else None
        price = v617_wanted_price(side, bid=raw_bid, ask=raw_ask, fundamental=fp, tick=tick_size,
                                  safety_ticks=int(getattr(self, "research_post_only_safety_ticks", 2) or 2))
        if c2j == 1 or price is None:
            return price
        if bool(getattr(self, "research_v631_touch_improve", False)):
            price = self._v6_31_price(book_id, side, price, fp, tick_size)
        return price

    # ---- C2Q -------------------------------------------------------------------------------------------------------------

    def _v6_40_c2q_off(self, book_id: int) -> bool:
        """C2Q: is this class-2 book below the break-even this request (the decision of its 30-s block)?  Off, another
        class, an error: False."""
        if not getattr(self, "research_v640_c2q", 0):
            return False
        g = (getattr(self, "_v6_40_grids", None) or {}).get(int(book_id))
        if g is None or g.c != V6_40_CLASS2:
            return False
        try:
            tick = int(getattr(self, "_tick", 0) or 0)
            if self._v6_40_c2q_tick != tick:
                self._v6_40_c2q_tick = tick
                self._v6_40_c2q_update()
            return not bool(self._v6_40_c2q.get("armed", True))
        except Exception:
            self._v6_40_errors = int(getattr(self, "_v6_40_errors", 0) or 0) + 1
            return False

    def _v6_40_c2q_update(self) -> None:
        """C2Q once per request (first class-2 book): at the first state of each 30-s block the break-even rule on the
        own mirror (c2q_decision) -- S14's class-split sums over the making window (released past the horizon) and the
        making mirror's beta of classes 0 and 2 -- with the configured pool factor; between blocks the block's
        decision stands."""
        now = int(getattr(self, "_direct_current_state_timestamp_ns", 0) or 0)
        blk = v6_40_block_of(now)
        st = self._v6_40_c2q
        if blk is not None and blk == st.get("block"):
            return
        tracker = getattr(self, "_v6_32_tracker", None)
        if not isinstance(tracker, V6_40ClassTracker):
            st.update(armed=True, probe=True, block=blk)
            self._v6_40_count("c2q_no_tracker")
            return
        r_all, c_all = tracker.sums()
        r0, c0 = tracker.class_sums(V6_40_HOME)
        r2, c2 = tracker.class_sums(V6_40_CLASS2)
        mirror = getattr(self, "_v62_mirror", None)
        uid = int(getattr(self, "uid", 0) or 0)
        buy = dict(mirror.buy_sums.get(uid) or {}) if mirror is not None else {}
        sell = dict(mirror.sell_sums.get(uid) or {}) if mirror is not None else {}
        table = self._v6_40_table or {}
        b0 = v6_40_making_beta(buy, sell, v6_40_class_books(table, V6_40_HOME))
        b2 = v6_40_making_beta(buy, sell, self._v6_40_c2)
        pf, src = self._v6_40_pool_factor(now)
        d = v6_40_c2q_decision(r_all, c_all, r0, c0, r2, c2, b0, b2, pf)
        st.clear()
        st.update(d, block=blk, b0=b0, b2=b2, r0=r0, c0=c0, r2=r2, c2=c2, pf=pf)
        st["pf_source"] = src
        self._v6_40_count("c2q_pf_" + src)
        self._v6_40_count("c2q_probe_blocks" if d["probe"] else ("c2q_armed_blocks" if d["armed"] else "c2q_off_blocks"))
        verdict = "probe" if d["probe"] else ("armed" if d["armed"] else "disarmed")
        if verdict != self._v6_40_c2q_verdict:
            # telemetry: the next V6_40_STATE row (reason "c2q") carries the new verdict
            if self._v6_40_c2q_verdict is not None:
                self._v6_40_count("c2q_verdict_changes")
            self._v6_40_c2q_verdict = verdict
            self._v6_40_c2q_changed = True

    def _v6_40_pool_factor(self, now_ns):
        """C2Q's pool factor for this block: the validator's published class pools (class_gauges weight x making_credit of
        classes 0 and 2 on the report page, the host-shared feed started here on first use) from the last report of this
        simulation at or before the state -- vm739's c2qpf rule -- else the configured fallback (research_v640_c2q_pf).
        Returns (pf, source)."""
        feed = getattr(self, "_v6_40_gauges", None)
        if feed is None:
            feed = V6_40ClassGaugeFeed()
            feed.start()
            self._v6_40_gauges = feed
        table = self._v6_40_table or {}
        names = {r["c"]: r["name"] for r in (table.get("classes") or ())}
        return v6_40_live_pool_factor(feed.snapshot(), now_ns, getattr(self, "_v6_40_sim_id", None), names.get(V6_40_HOME, ""),
                                      names.get(V6_40_CLASS2, ""), self.research_v640_c2q_pf)

    def _v6_40_c2q_cancel(self, response, book_id: int, rows, already, req, *, budget: int) -> int:
        """C2Q disarmed: every order of ours on the class-2 book (every layer's rows) is cancelled in one instruction and
        none is placed (vm739: all removed, no placement).  Returns 0 placements."""
        try:
            ids = sorted({int(r.order_id) for r in rows if int(r.order_id) not in already})
            self._v6_40_count("c2q_off_book_states")
            if ids and self._count_book_instructions(response, book_id) < budget:
                response.cancel_orders(book_id=book_id, order_ids=ids, delay=0)
                req["cancels"] = int(req.get("cancels", 0) or 0) + 1
                self._v6_40_count("c2q_cancelled", len(ids))
                try:
                    self._a19_note_exit_cancel(book_id, ids, ABSENT_REPRICE_CANCEL)
                except Exception:
                    pass
        except Exception:
            self._v6_40_errors = int(getattr(self, "_v6_40_errors", 0) or 0) + 1
        return 0

    # ---- the frozen base's maker sanitizer, the final validator, the lift, v6.36's drop -------------------------------------

    def _research_sanitize_maker_instructions(self, response, state) -> None:
        """v6.40: the frozen base's maker sanitizer (Strategy1_Research, unchanged) with each traded non-home class's maker
        orders sent through it on their class's own grid -- a shim state carrying the class's price / volume decimals and
        the class's minimum as the exchange minimum for that call (the base rounds every maker price to the top level's
        decimals: a class-2 price would land on the 0.01 grid); every other instruction goes through ONE call of the base
        exactly as in v6.39, and the response keeps the original order of what survives.  No grid (the default): the
        base itself."""
        grids = getattr(self, "_v6_40_grids", None) or {}
        if not grids:
            return super()._research_sanitize_maker_instructions(response, state)
        instructions = list(getattr(response, "instructions", None) or [])
        groups: dict = {}
        rest = []
        for ix in instructions:
            try:
                g = grids.get(int(self._get(ix, "bookId", "book_id")))
            except (TypeError, ValueError):
                g = None
            if g is not None and self._research_instruction_is_maker(ix):
                groups.setdefault(g.c, (g, []))[1].append(ix)
            else:
                rest.append(ix)
        if not groups:
            return super()._research_sanitize_maker_instructions(response, state)
        response.instructions[:] = rest
        super()._research_sanitize_maker_instructions(response, state)
        kept = {id(ix) for ix in (getattr(response, "instructions", None) or ())}
        saved = getattr(self, "_research_exchange_min_order_size", 0.25)
        for _c, (g, ixs) in sorted(groups.items()):
            shim_state = SimpleNamespace(books=getattr(state, "books", None),
                                         config=SimpleNamespace(priceDecimals=g.dec, volumeDecimals=g.vol_dec))
            shim = SimpleNamespace(instructions=list(ixs))
            try:
                self._research_exchange_min_order_size = g.min_order
                super()._research_sanitize_maker_instructions(shim, shim_state)
            finally:
                self._research_exchange_min_order_size = saved
            kept.update(id(ix) for ix in shim.instructions)
            self._v6_40_count("sanitized_c%d" % int(g.c), len(shim.instructions))
        response.instructions[:] = [ix for ix in instructions if id(ix) in kept]


    def _v6_40_lift(self, instructions, decimals) -> dict:
        """v6.1.1's wire-price lift with each traded non-home book on its own price decimals (the home books on the top
        level's, exactly as v6.39)."""
        decs = getattr(self, "_v6_40_decs", None) or {}
        groups: dict = {}
        for ix in list(instructions or ()):
            b = ix.get("bookId") if isinstance(ix, dict) else getattr(ix, "bookId", None)
            try:
                d = decs.get(int(b), decimals)
            except (TypeError, ValueError):
                d = decimals
            groups.setdefault(d, []).append(ix)
        moved = {"buy": 0, "sell": 0, "other": 0}
        for d in sorted(groups, key=lambda x: (x != decimals, x)):
            for side, n in v611_lift_instruction_prices(groups[d], d).items():
                moved[side] = int(moved.get(side, 0) or 0) + int(n)
        return moved

    # ---- telemetry -------------------------------------------------------------------------------------------------------

    def onOrderRejected(self, event) -> None:
        """v6.40 telemetry: the base handlers unchanged (first), then the venue reject tallied by the book's class and the
        event's reason (V6_40_STATE monitor "rejects").  Never raises."""
        super().onOrderRejected(event)
        try:
            reason = str(getattr(event, "message", "") or "").upper()[:48] or "UNKNOWN"
            try:
                book_id = int(getattr(event, "bookId", None))
            except (TypeError, ValueError):
                book_id = None
            cls = -1
            if book_id is not None:
                for r in ((getattr(self, "_v6_40_table", None) or {}).get("classes") or ()):
                    if book_id in r["books"]:
                        cls = int(r["c"])
                        break
            key = "c%d:%s" % (cls, reason)
            self._v6_40_rejects[key] = int(self._v6_40_rejects.get(key, 0) or 0) + 1
        except Exception:
            self._v6_40_errors = int(getattr(self, "_v6_40_errors", 0) or 0) + 1

    def _v6_40_monitor(self) -> dict:
        """The V6_40_STATE monitor (G2's live read; pure reads, telemetry only): with class 2 traded, on its books our
        open orders (count, notional = sum of quantity x price, from the state's accounts), our position (sum of |net|
        and its notional at v6.36's last mid) and our making-window notional (the making mirror's own buy + sell; class
        0's beside it); C2Q's verdict with q2, q, the break-even thr x q, the pool factor and its source; the venue
        rejects since start by class and reason.  Never raises: an error counts and the fields read so far are
        returned."""
        out: dict = {"rejects": dict(getattr(self, "_v6_40_rejects", None) or {})}
        try:
            c2 = (sorted(getattr(self, "_v6_40_c2", None) or ())
                  if V6_40_CLASS2 in (getattr(self, "_v6_40_traded", None) or ()) else [])   # traded class 2 only
            l1 = getattr(self, "_v6_36_l1", None) or {}
            n = 0
            open_notional = position = position_notional = 0.0
            for b in c2:
                for o in self._direct_account_orders(b):
                    n += 1
                    try:
                        open_notional += float(o.quantity) * float(o.price or 0.0)
                    except (TypeError, ValueError, AttributeError):
                        pass
                inv = abs(float(self._direct_signed_inventory(b) or 0.0))
                position += inv
                last = l1.get(b)
                mid = v6_36_mid_of(last[0], last[1]) if last else None
                if mid:
                    position_notional += inv * float(mid)
            mirror = getattr(self, "_v62_mirror", None)
            uid = int(getattr(self, "uid", 0) or 0)
            buy = dict(mirror.buy_sums.get(uid) or {}) if mirror is not None else {}
            sell = dict(mirror.sell_sums.get(uid) or {}) if mirror is not None else {}
            home = v6_40_class_books(getattr(self, "_v6_40_table", None) or {}, V6_40_HOME)
            out.update(c2_books=len(c2), c2_open_orders=n, c2_open_notional=round(open_notional, 4),
                       c2_position=round(position, 6), c2_position_notional=round(position_notional, 4),
                       c2_making_notional=round(sum(float(buy.get(b, 0.0) or 0.0) + float(sell.get(b, 0.0) or 0.0)
                                                    for b in c2), 4),
                       c0_making_notional=round(sum(float(buy.get(b, 0.0) or 0.0) + float(sell.get(b, 0.0) or 0.0)
                                                    for b in home), 4))
            st = getattr(self, "_v6_40_c2q", None) or {}
            if not getattr(self, "research_v640_c2q", 0):
                out["c2q"] = "off"
            else:
                q, thr = st.get("q"), st.get("thr")
                out.update(c2q=getattr(self, "_v6_40_c2q_verdict", None) or "none", q=q, q2=st.get("q2"), thr=thr,
                           break_even=(None if q is None or thr is None else float(thr) * float(q)), pf=st.get("pf"),
                           pf_source=st.get("pf_source"), block=st.get("block"))
        except Exception:
            self._v6_40_errors = int(getattr(self, "_v6_40_errors", 0) or 0) + 1
        return out

    def _v6_40_snapshot(self) -> dict:
        table = getattr(self, "_v6_40_table", None) or {}
        classes = [{"c": r["c"], "name": r["name"], "books": len(r["books"]), "tick": r["tick"], "min": r["min_order"],
                    "p0": r["init_price"], "du": r.get("du")} for r in (table.get("classes") or ())]
        grids = getattr(self, "_v6_40_grids", None) or {}
        return {"version": V6_40_CLASSES_VERSION, "traded": getattr(self, "research_v640_traded_classes", ""),
                "traded_ok": int(bool(getattr(self, "_v6_40_traded_ok", True))),
                "c2du": int(getattr(self, "research_v640_c2du", 0) or 0),
                "c2d": int(getattr(self, "research_v640_c2d", 0) or 0),
                "c2j": int(getattr(self, "research_v640_c2j", 0) or 0),
                "c2q": int(getattr(self, "research_v640_c2q", 0) or 0),
                "c2q_pf": getattr(self, "research_v640_c2q_pf", None),
                "c1w": int(getattr(self, "research_v640_c1w", 0) or 0),
                "guard_hold": int(bool(getattr(self, "research_v640_guard_hold", False))),
                "guard_init_price": int(bool(getattr(self, "research_v640_guard_init_price", False))),
                "s14_class_key": int(bool(getattr(self, "research_v640_s14_class_key", False))),
                "s14_flush_spec": int(bool(getattr(self, "research_v640_s14_flush_spec", False))),
                "layout_status": table.get("status"), "digest": getattr(self, "_v6_40_digest", ""),
                "classes": classes, "grid_books": len(grids),
                "held_foreign": (None if getattr(self, "_v6_40_last_foreign", None) is None
                                 else len(self._v6_40_last_foreign)),
                "c2q_state": {k: v for k, v in (getattr(self, "_v6_40_c2q", None) or {}).items()
                              if isinstance(v, (int, float, bool, str, type(None)))},
                "class_gauges": (self._v6_40_gauges.status() if getattr(self, "_v6_40_gauges", None) is not None
                                 else None),
                "monitor": self._v6_40_monitor(),
                "counts": dict(getattr(self, "_v6_40_counts", None) or {}),
                "errors": int(getattr(self, "_v6_40_errors", 0) or 0)}

    def _v6_40_report(self) -> None:
        """v6.40: the V6_40_STATE row -- at the first request, on a change of the class layout, at the first request with
        an error after one without, at the request after a change of C2Q's verdict, and every V62_STATE_EVERY_TICKS
        ticks (requests); after the reply when the request defers its telemetry (v6.2.9).  Never raises."""
        try:
            tick = int(getattr(self, "_tick", 0) or 0)
            errors = int(getattr(self, "_v6_40_errors", 0) or 0)
            erring = errors > int(getattr(self, "_v6_40_errors_seen", 0) or 0)
            was_erring = bool(getattr(self, "_v6_40_erring", False))
            self._v6_40_errors_seen, self._v6_40_erring = errors, erring
            if not getattr(self, "_v6_40_reported", False):
                reason = "start"
            elif getattr(self, "_v6_40_layout_changed", False):
                reason = "layout"
            elif erring and not was_erring:
                reason = "error"
            elif getattr(self, "_v6_40_c2q_changed", False):
                reason = "c2q"
            elif tick > 0 and tick % V62_STATE_EVERY_TICKS == 0:
                reason = "periodic"
            else:
                return
            self._v6_40_reported = True
            self._v6_40_layout_changed = False
            self._v6_40_c2q_changed = False
            row = dict(self._v6_40_snapshot(), reason=reason, tick=tick)
            work = getattr(self, "_v629_request_work", None)
            if work is not None:
                work.add("v6_40_state_row", lambda: self._emit("V6_40_STATE", force=True, **row))   # after the reply
            else:
                self._emit("V6_40_STATE", force=True, **row)
        except Exception:
            self._v6_40_errors = int(getattr(self, "_v6_40_errors", 0) or 0) + 1

    # ---- GENERATED (k139 build/gen_part.py; pinned by tests/test_research_v6_40.py) ----


    def _v6_40_deep_core(self, response, book_id, deep, depth, raw_bid, raw_ask, inv, rows, deep_rows, already, req, *, tick_size, dec, clip, min_order, budget, expiry, cfg, paced: bool=False, cap_skip: bool=False, against: frozenset=frozenset(), bound_scale: float=1.0, held: frozenset=frozenset(), size_scale: float=1.0) -> int:
        """v6.3.3: an open book's two sides are the deep layer's -- one order per side, as the A1.7.4.3 contract requires.

        Its v6.3 orders are cancelled; a deep order that drifted out of [0.5, 1.5] x depth from the mid or has no room
        left is cancelled; a side with no order of ours, nothing cancelled on it this request and room takes one deep
        order.  A cancelled side takes nothing until the removal has been seen (the v6.2.15 S2 side ownership).

        v6.9 S1 (the ladder): each side also rests an order at the book's sweep-record p99 and max, each judged by the
        same band on its own depth; ownership is per (side, level), so a resting level does not keep the next out,
        and a side still takes one new order per request (shallow first).  S3: ahead of its volume line, the adding
        side keeps its deepest level only.

        v6.23 S1: ``against`` names the (side, level) pairs that hold no order this request -- the side trading against
        the book's fresh fundamental under the 0.6.3 realized basis; a resting one is cancelled, none is placed.  Empty
        (the default): v6.22.
        """
        du = self._v6_40_du(book_id, tick_size)
        scale, vmin, vdec = self._v6_40_units(book_id, min_order, cfg)
        req['deep_books'] += 1
        mid = 0.5 * (float(raw_bid) + float(raw_ask))
        v6_30_tier = 0
        if bool(getattr(self, 'research_v630_deep_tier', False)):
            v6_30_tier = self._v6_30_tier(book_id)
        vac = None
        if bool(getattr(self, 'research_v64_vacuum', False)) and self._v64_vacuum_on():
            vac = v64_vacuum_depth(raw_bid, raw_ask, du)
            if vac is not None:
                self._v64_count('vacuum_book_states')
        eff = float(vac) if vac is not None else float(depth)
        eff_side = {V63_SIDE_BUY: eff, V63_SIDE_SELL: eff}
        if vac is None and bool(getattr(self, 'research_v612_reduce_depth', False)):
            try:
                eff_side = {s_: float(v612_level0_depth(s_, depth, inv, min_order)) for s_ in (V63_SIDE_BUY, V63_SIDE_SELL)}
            except Exception:
                self._v633_errors = int(getattr(self, '_v633_errors', 0) or 0) + 1
                eff_side = {V63_SIDE_BUY: eff, V63_SIDE_SELL: eff}
        centre = None
        v618_off = bool(getattr(self, 'research_v618_centre_off', False))
        if v618_off and bool(getattr(self, 'research_v616_fair_value', False)):
            self._v618_count('centre_off_books')
        if v618_centre_on(getattr(self, 'research_v616_fair_value', False), v618_off):
            try:
                centre = self._v616_book(book_id, raw_bid, raw_ask, depth, du, cfg)
            except Exception:
                self._v616_errors = int(getattr(self, '_v616_errors', 0) or 0) + 1
                centre = None
        unspaced: set[str] = set()
        if bool(getattr(self, 'research_v66_add_spacing', False)) and self._v66_spacing_on():
            try:
                spacing = self._v66_spacing_ref()
                for side in (V63_SIDE_BUY, V63_SIDE_SELL):
                    if not v66_adds_to_position(side, inv):
                        continue
                    c_ = float(centre[side]) if centre is not None else mid
                    if vac is not None:
                        want = v6_40_vacuum_price_du(c_, vac, side, bid=raw_bid, ask=raw_ask, du=du, tick=tick_size, decimals=dec)
                    else:
                        want = v6_40_deep_price_du(c_, depth, side, bid=raw_bid, ask=raw_ask, du=du, tick=tick_size, decimals=dec)
                    if v6_30_tier:
                        want = self._v6_30_deeper(want, side, v6_30_tier, c_, eff_side[side], vac is not None, raw_bid, raw_ask, du, dec)[0]
                    if not spacing.allows(book_id, side, want, inv, eff, du):
                        unspaced.add(side)
            except Exception:
                self._v66_errors = int(getattr(self, '_v66_errors', 0) or 0) + 1
                unspaced = set()
        scaled = float(bound_scale) < 1.0 - 1e-12
        bound = self._v610_bound_base(deep, paced)

        def _plain(side_: str) -> bool:
            return bool(deep.room(side_, inv, bound))

        def _room(side_: str) -> bool:
            if scaled:
                return bool(v661_room(side_, inv, bound * float(bound_scale)))
            return _plain(side_)
        ladder = bool(self._v69_ladder_on())
        deep_first = bool(ladder and self._v69_deep_first_on())
        level_depth: dict[int, float] = {}
        levels_side: dict = {V63_SIDE_BUY: [], V63_SIDE_SELL: []}
        if ladder:
            try:
                depths = deep.sweep_depths(book_id, V69_LADDER_QUANTILES)
                if depths is not None:
                    level_depth = {i + 1: float(d) for i, d in enumerate(depths)}
                levels_side = {s_: v69_ladder_levels(depths, eff_side[s_]) for s_ in (V63_SIDE_BUY, V63_SIDE_SELL)}
            except Exception:
                self._v69_errors = int(getattr(self, '_v69_errors', 0) or 0) + 1
                level_depth, levels_side = ({}, {V63_SIDE_BUY: [], V63_SIDE_SELL: []})
        v6_32_fee: frozenset = frozenset()
        if bool(getattr(self, 'research_v632_fee_making', False)):
            v6_32_fee = self._v6_32_deep_pairs(book_id)
        v6_38_ladder: frozenset = frozenset()
        if getattr(self, '_v6_38_l3_on', False):
            v6_38_ladder = self._v6_38_kbl_ladder(book_id, levels_side)
        doomed = [(row, V633_CANCEL_OWNS_BOOK) for row in rows if int(row.order_id) not in already]
        resting: dict[Any, Any] = {}
        for row in deep_rows:
            if int(row.order_id) in already:
                continue
            side = V63_SIDE_BUY if int(row.side) == 0 else V63_SIDE_SELL
            level = int(v633_deep_level(getattr(row, 'client_id', None)) or 0)
            if level and (not ladder):
                doomed.append((row, V69_CANCEL_LEVEL_OFF))
            elif against and (side, level) in against:
                doomed.append((row, V623_CANCEL_AGAINST))
            elif v6_32_fee and (side, level) in v6_32_fee:
                doomed.append((row, V6_32_CANCEL_FEE_GATE))
                self._v6_32_count('deep_cancels')
            elif v6_38_ladder and (side, level) in v6_38_ladder:
                doomed.append((row, self._v6_38_kbl_ladder_why(book_id)))
            elif side in held:
                doomed.append((row, V619_CANCEL_SURPLUS))
            elif not _room(side):
                doomed.append((row, V633_CANCEL_NO_ROOM))
                if _plain(side):
                    self._v661_count('cancel_vol_bound')
            elif paced and v641_adds(side, inv) and (not (deep_first and v69_deep_first_keeps(level))):
                doomed.append((row, V641_CANCEL_PACED))
            elif level == 0 and side in unspaced:
                doomed.append((row, V66_CANCEL_SPACING))
                self._v66_count('cancel_spacing')
            elif centre is not None and self._v616_judged(row, side, level, centre, eff_side, level_depth, du):
                doomed.append((row, V633_CANCEL_REPRICE))
                self._v616_count('cancel_centre_reprice')
            elif centre is not None:
                resting[(side, level) if ladder else side] = row
            elif v633_needs_reprice(float(row.price), mid, eff_side[side] if level == 0 else level_depth.get(level, 0.0), du):
                doomed.append((row, V633_CANCEL_REPRICE))
            else:
                resting[(side, level) if ladder else side] = row
        cancelled: set = set()
        if doomed and self._count_book_instructions(response, book_id) < budget:
            ids = [int(row.order_id) for row, _why in doomed]
            try:
                response.cancel_orders(book_id=book_id, order_ids=ids, delay=0)
            except Exception:
                self._v633_errors = int(getattr(self, '_v633_errors', 0) or 0) + 1
            else:
                req['cancels'] += 1
                for row, why in doomed:
                    (self._v641_count if paced and why == V641_CANCEL_PACED else self._v633_count)('cancel_' + str(why).lower())
                    side = V63_SIDE_BUY if int(row.side) == 0 else V63_SIDE_SELL
                    if not ladder:
                        cancelled.add(side)
                        continue
                    level = v633_deep_level(getattr(row, 'client_id', None))
                    cancelled.add((side, V69_WHOLE_SIDE if level is None else int(level)))
                    if level:
                        self._v69_count('cancel_l%d_%s' % (int(level), str(why).lower()))
                try:
                    self._a19_note_exit_cancel(book_id, ids, ABSENT_REPRICE_CANCEL)
                except Exception:
                    pass
        if cap_skip:
            return 0
        live_sides: set = set()
        slots: dict = {}
        if ladder:
            try:
                slots = self._v69_live_slots(book_id)
            except Exception:
                slots = v69_live_slots([(r.side, getattr(r, 'client_id', None)) for r in list(rows) + list(deep_rows)])
        else:
            try:
                live_sides = set(self._v6215_live_sides(book_id))
            except Exception:
                live_sides = {V63_SIDE_BUY if int(r.side) == 0 else V63_SIDE_SELL for r in list(rows) + list(deep_rows)}
        candidates = {s_: [(0, float(depth))] + [(k, d) for k, d, _clips in levels_side[s_]] for s_ in (V63_SIDE_BUY, V63_SIDE_SELL)}
        placed = 0
        spent = False
        for side in (V63_SIDE_BUY, V63_SIDE_SELL):
            if spent:
                break
            if side in held:
                continue
            if not ladder and (side in resting or side in live_sides or side in cancelled):
                continue
            if not _plain(side):
                continue
            if not _room(side):
                self._v661_count('placement_vol_bound')
                continue
            adding = bool(paced and v641_adds(side, inv))
            for level, level_d in candidates[side]:
                if against and (side, level) in against:
                    continue
                if v6_32_fee and (side, level) in v6_32_fee:
                    self._v6_32_count('deep_withheld')
                    continue
                if v6_38_ladder and (side, level) in v6_38_ladder:
                    self._v6_38_l3_count('kbl_ladder_withheld')
                    continue
                if ladder and ((side, level) in resting or v69_slot_taken(slots, side, level) or (side, level) in cancelled or ((side, V69_WHOLE_SIDE) in cancelled)):
                    continue
                if adding and (not (deep_first and v69_deep_first_keeps(level))):
                    continue
                if level == 0 and side in unspaced:
                    self._v66_count('placement_spaced_out')
                    continue
                q_ = v69_level_quantity(clip, level) if level else clip
                if size_scale < 1.0:
                    q_ = v620_scaled_quantity(q_, size_scale, min_order)
                q = v62_lot_quantity(q_ * scale, vdec)
                if q + 1e-12 < vmin:
                    self._v633_count('below_min_order')
                    continue
                if level:
                    price = v6_40_deep_price_du(mid, level_d, side, bid=raw_bid, ask=raw_ask, du=du, tick=tick_size, decimals=dec)
                elif vac is not None:
                    price = v6_40_vacuum_price_du(mid, vac, side, bid=raw_bid, ask=raw_ask, du=du, tick=tick_size, decimals=dec)
                else:
                    price = v6_40_deep_price_du(mid, eff_side[side], side, bid=raw_bid, ask=raw_ask, du=du, tick=tick_size, decimals=dec)
                if centre is not None:
                    try:
                        price = self._v616_target(side, level, level_d, centre, eff_side, vac, raw_bid, raw_ask, du, dec)
                        self._v616_count('placed_centred' if abs(float(centre[side]) - mid) > 1e-12 else 'placed_mid')
                    except Exception:
                        self._v616_errors = int(getattr(self, '_v616_errors', 0) or 0) + 1
                v6_30_why = None
                if v6_30_tier:
                    price, v6_30_why = self._v6_30_deeper(price, side, v6_30_tier, centre[side] if centre is not None else mid, level_d if level else eff_side[side], level == 0 and vac is not None, raw_bid, raw_ask, du, dec)
                if self._v691_free_base_on():
                    try:
                        free = self._v691_free_for(book_id, side, price)
                        sized = v691_quantity_within_free(q, free, vmin, vdec)
                    except Exception:
                        self._v69_errors = int(getattr(self, '_v69_errors', 0) or 0) + 1
                        free, sized = (None, q)
                    if free is not None and sized + 1e-12 < q:
                        if sized <= 0.0:
                            self._v69_count('no_free_%s_l%d' % ('base' if side == V63_SIDE_SELL else 'quote', level))
                            continue
                        self._v69_count('sized_by_free_%s_l%d' % ('base' if side == V63_SIDE_SELL else 'quote', level))
                        q = sized
                if self._count_book_instructions(response, book_id) >= budget:
                    self._v633_count('budget_deferred')
                    spent = True
                    break
                buy_cid, sell_cid = v633_level_client_ids(book_id, level) if level else v633_client_ids(book_id)
                try:
                    response.limit_order(book_id=book_id, direction=OrderDirection.BUY if side == V63_SIDE_BUY else OrderDirection.SELL, quantity=q, price=float(price), clientOrderId=buy_cid if side == V63_SIDE_BUY else sell_cid, stp=STP.CANCEL_BOTH, postOnly=True, timeInForce=TimeInForce.GTT, expiryPeriod=expiry, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
                except Exception:
                    self._v633_errors = int(getattr(self, '_v633_errors', 0) or 0) + 1
                    continue
                placed += 1
                req['placed_deep'] += 1
                if v6_30_why is not None:
                    self._v6_30_placed(level, v6_30_why)
                if level:
                    self._v69_count('placed_l%d' % level)
                    if adding:
                        self._v69_count('paced_kept_deepest')
                elif vac is not None:
                    self._v64_count('placed_vacuum')
                elif eff_side[side] < float(depth) - 1e-09:
                    self._v633_count('placed_reducing_l0')
                break
        return placed


    def _v6_40_touch_core(self, response, book_id: int, deep, raw_bid, raw_ask, inv, touch_rows, already, req, *, tick_size, min_order, budget, expiry, cfg, paced: bool, held: frozenset=frozenset(), size_scale: float=1.0, cap_skip: bool=False) -> int:
        """v6.17: the book's touch quote -- ONE post-only order joining the touch on the side that trades toward the
        book's fresh fundamental, while its own price is THRESHOLD_BPS on the safe side of it.

        A resting quote that flipped side, lost its edge, fell REST_TICKS behind the touch, would add while the book runs
        ahead of its volume line, or whose fill would pass the deep layer's bound is cancelled.  A side takes a new
        quote only with no quote of ours live or pending on it, none cancelled on it this request (A1.7.4.3.1), and no
        placement of this response on it (the deep layer places first).  Returns the placements added (0 or 1).
        """
        unit, rest = self._v6_40_touch_units(book_id, tick_size)
        scale, vmin, vdec = self._v6_40_units(book_id, min_order, cfg)
        fp = self._v617_fundamental(book_id, raw_bid, raw_ask, cfg)
        mid = 0.5 * (float(raw_bid) + float(raw_ask))
        toward = v617_toward_side(mid, fp) if fp is not None else None
        v6_38_win = None
        if getattr(self, '_v6_38_l3_on', False) and fp is not None:
            v6_38_win = self._v6_38_kbl_window(book_id)
            if v6_38_win is not None:
                toward = v6_38_win
        clip = float(V617_CLIP_DEEP_CLIPS) * float(getattr(deep, 'clip', 0.0) or 0.0)
        bound = self._v610_bound_base(deep, paced)
        v6_27_wide = None
        if bool(getattr(self, 'research_v627_touch_centre', False)):
            v6_27_wide, bound = (bound, self._v6_27_bound(book_id, clip, bound))
        v6_29_held = None
        if bool(getattr(self, 'research_v629_touch_fp_move', False)):
            v6_29_held = self._v6_29_held(book_id, fp, cfg)
        if v6_38_win is not None:
            v6_29_held = None
        v6_32_gated: frozenset = frozenset()
        if bool(getattr(self, 'research_v632_fee_making', False)):
            v6_32_gated = self._v6_32_touch_sides(book_id)
        v6_33_off = False
        if bool(getattr(self, 'research_v633_touch_identity', False)):
            v6_33_off = self._v6_33_off(book_id)
        doomed = []
        kept = 0
        for row in touch_rows:
            if int(row.order_id) in already:
                continue
            side = V63_SIDE_BUY if int(row.side) == 0 else V63_SIDE_SELL
            if fp is None:
                why = V617_CANCEL_NO_FP
            else:
                why = v617_verdict(side, float(row.price), toward=toward, bid=raw_bid, ask=raw_ask, fundamental=fp, tick=unit, rest_ticks=rest)
            if v6_38_win is not None:
                why = v6_38_kbl_verdict(side, float(row.price), window=v6_38_win, bid=raw_bid, ask=raw_ask, tick=tick_size)
            if why is None and paced and v641_adds(side, inv):
                why = V617_CANCEL_PACED
            if why is None and side in held:
                why = V619_CANCEL_SURPLUS
            if why is None and (not v617_room(side, inv, clip, bound)):
                why = V617_CANCEL_BOUND
                if v6_27_wide is not None and v617_room(side, inv, clip, v6_27_wide):
                    self._v6_27_count('cancel_centre')
            if why is None and side == v6_29_held:
                why = V6_29_CANCEL_FP_MOVE
                self._v6_29_count('cancelled')
            if why is None and v6_32_gated and (side in v6_32_gated):
                why = V6_32_CANCEL_FEE_GATE
                self._v6_32_count('touch_cancels')
            if why is None and v6_33_off:
                why = V6_33_CANCEL_TOUCH_IDENTITY
                self._v6_33_count('touch_cancels')
            if why is None:
                kept += 1
            else:
                doomed.append((row, why))
        cancelled = self._v617_cancel(response, book_id, doomed, req, budget)
        if cap_skip:
            return 0
        if kept or toward is None:
            return 0
        if toward in cancelled:
            self._v617_count('wait_cancel_seen')
            return 0
        if toward in v617_placed_sides(getattr(response, 'instructions', None) or (), book_id):
            self._v617_count('yield_to_deep')
            return 0
        if paced and v641_adds(toward, inv):
            self._v617_count('paced_states')
            return 0
        if toward in held:
            self._v619_count('touch_held_states')
            return 0
        if not v617_room(toward, inv, clip, bound):
            self._v617_count('bound_states')
            if v6_27_wide is not None and v617_room(toward, inv, clip, v6_27_wide):
                self._v6_27_count('refused_centre')
            return 0
        if toward == v6_29_held:
            self._v6_29_count('withheld')
            return 0
        if v6_32_gated and toward in v6_32_gated:
            self._v6_32_count('touch_withheld')
            return 0
        if v6_33_off:
            self._v6_33_count('touch_withheld')
            return 0
        try:
            if V617_TOUCH_SLOT in (self._v69_live_slots(book_id).get(toward) or frozenset()):
                self._v617_count('slot_in_flight')
                return 0
        except Exception:
            self._v617_errors = int(getattr(self, '_v617_errors', 0) or 0) + 1
            return 0
        price = self._v6_40_touch_price(toward, raw_bid, raw_ask, fp, tick_size, book_id)
        if v6_38_win is not None:
            price = v6_38_kbl_join(toward, raw_bid, raw_ask, tick_size, int(getattr(self, 'research_post_only_safety_ticks', 2) or 2))
        if price is None:
            self._v617_count('no_price')
            return 0
        if v6_38_win is not None:
            price = self._v6_38_kbl_price(book_id, toward, price, fp, tick_size)
        q_ = v620_scaled_quantity(clip, size_scale, min_order) if size_scale < 1.0 else clip
        if getattr(self, '_v6_38_l3_on', False):
            v6_38_q = self._v6_38_touch_size(book_id, toward, inv, clip, size_scale, min_order, v6_38_win)
            if v6_38_q is not None:
                q_ = v6_38_q
        q = v62_lot_quantity(q_ * scale, vdec)
        if q + 1e-12 < vmin:
            self._v617_count('below_min_order')
            return 0
        if self._v691_free_base_on():
            try:
                free = self._v691_free_for(book_id, toward, price)
                sized = v691_quantity_within_free(q, free, vmin, vdec)
            except Exception:
                self._v617_errors = int(getattr(self, '_v617_errors', 0) or 0) + 1
                free, sized = (None, q)
            if free is not None and sized + 1e-12 < q:
                if sized <= 0.0:
                    self._v617_count('no_free')
                    return 0
                self._v617_count('sized_by_free')
                q = sized
        if self._count_book_instructions(response, book_id) >= budget:
            self._v617_count('budget_deferred')
            return 0
        buy_cid, sell_cid = v617_client_ids(book_id)
        try:
            response.limit_order(book_id=book_id, direction=OrderDirection.BUY if toward == V63_SIDE_BUY else OrderDirection.SELL, quantity=q, price=float(price), clientOrderId=buy_cid if toward == V63_SIDE_BUY else sell_cid, stp=STP.CANCEL_BOTH, postOnly=True, timeInForce=TimeInForce.GTT, expiryPeriod=expiry, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
        except Exception:
            self._v617_errors = int(getattr(self, '_v617_errors', 0) or 0) + 1
            return 0
        req['placed_touch'] = int(req.get('placed_touch', 0) or 0) + 1
        self._v617_count('placed_' + str(toward))
        return 1
