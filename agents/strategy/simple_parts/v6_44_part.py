"""v6.44 (k173 EVENTS lines 110-111): the class-2 two-sided touch layer C2T, the fee gate's estimate per class FGK and
C2Q's guard on q2's confidence bound C2QB -- research_v644_c2t / research_v644_fgk / research_v644_c2qb in {0, 1}, code
default 0 (off: v6.43 on every path), PARAMS 1.  The rules are research_v644_c2t's.  This mixin, first among Simple's
bases, carries the agent side:

1. C2T: Simple's per-book pass calls _v6_44_c2t_on / _v6_44_c2t_book for a traded class-2 book right after v6.40's C2Q
   branch (a disarmed block still takes v6.41 B2b's exit there) -- one line of Simple, which replaces a blank line of
   the same pass so join.py's chunk lines stay put;
2. FGK: _v6_40_init / _v6_32_note override V640Part's / V632Part's (no line of Simple or of a part changes): S14's
   tracker is made as research_v644_c2t's ClassFeeTracker (C2Q's class tracker, which still sees every fill, plus S14's
   window per class), and the note gates each book on its own class's estimate;
3. C2QB: _v6_40_c2q_update overrides V641Part's (which runs V640Part's): a new evidence decision that disarms class 2
   stands only when the bound is below break-even, else the block is armed;
4. the V6_40_STATE row carries this part's snapshot under "v6_44" (_v6_40_snapshot override).

Every path never raises: an error counts (_v6_44_errors, in the row) and the book places nothing this request (C2T) or
reads v6.32's pooled estimate (FGK).  Unchanged: the rescue line (v6.43), tau and the deep layer on class 0, D4, C2Q,
A_r1 + D, l1t / l1c, the band, the aim, the hard cap, class 1.  No taking.  STRUCTURAL."""
from __future__ import annotations

from taos.im.protocol.models import LoanSettlementOption, OrderDirection, STP, TimeInForce

from research_v617_touch_quote import CLIP_DEEP_CLIPS as V617_CLIP_DEEP_CLIPS
from research_v617_touch_quote import REST_TICKS as V617_REST_TICKS
from research_v617_touch_quote import TOUCH_SLOT as V617_TOUCH_SLOT
from research_v617_touch_quote import client_ids as v617_client_ids
from research_v617_touch_quote import join_price as v617_join_price
from research_v617_touch_quote import placed_sides as v617_placed_sides
from research_v6210_touch_improve import touch_view as v6210_touch_view
from research_v6214_touch_life import exit_client_ids as v6214_exit_client_ids
from research_v62_breadth import entry_client_ids as v62_entry_client_ids
from research_v62_breadth import lot_quantity as v62_lot_quantity
from research_v633_deep_layer import own_client_ids as v633_own_client_ids
from research_v63_trend_target import SIDE_BUY as V63_SIDE_BUY, SIDE_SELL as V63_SIDE_SELL
from research_v63_trend_target import own_client_ids as v63_own_client_ids
from research_v644_c2t import CANCEL_OTHER as V6_44_CANCEL_OTHER
from research_v644_c2t import ClassFeeTracker as V6_44ClassFeeTracker
from research_v644_c2t import V644_C2T_VERSION as V6_44_C2T_VERSION
from research_v644_c2t import c2qb_alpha as v6_44_c2qb_alpha
from research_v644_c2t import c2qb_bound as v6_44_c2qb_bound
from research_v644_c2t import parse_switch as v6_44_parse_switch
from research_v644_c2t import resting_verdict as v6_44_resting_verdict
from research_v644_c2t import side_plan as v6_44_side_plan
from research_v644_c2t import touch_price as v6_44_touch_price
from research_v69_deep_ladder import quantity_within_free as v691_quantity_within_free
from research_v6_40_classes import CLASS2 as V6_44_CLASS2, HOME_CLASS as V6_44_CLASS0
from research_v6_40_classes import ClassCaptureTracker as V6_44ClassTracker

V6_44_SWITCHES = ("research_v644_c2t", "research_v644_fgk", "research_v644_c2qb")


class V644Part:
    """v6.44 (research_v644_c2t / research_v644_fgk): class 2's two-sided touch layer and its own fee-gate estimate"""

    # ---- switches and counters -----------------------------------------------------------------------------------

    def _v6_44_switch(self, name: str) -> bool:
        """A v6.44 switch, read once from the config (code default 0); an unreadable value runs off and counts."""
        cache = getattr(self, "_v6_44_switches", None)
        if cache is None:
            cache = {}
            self._v6_44_switches = cache
        on = cache.get(name)
        if on is None:
            try:
                got = v6_44_parse_switch(getattr(getattr(self, "config", None), name, 0))
                if got is None:
                    self._v6_44_errors = int(getattr(self, "_v6_44_errors", 0) or 0) + 1
                on = bool(got)
            except Exception:
                self._v6_44_errors = int(getattr(self, "_v6_44_errors", 0) or 0) + 1
                on = False
            cache[name] = on
        return bool(on)

    def _v6_44_count(self, key: str, n: int = 1) -> None:
        counts = getattr(self, "_v6_44_counts", None)
        if counts is None:
            counts = {}
            self._v6_44_counts = counts
        counts[key] = int(counts.get(key, 0)) + int(n)

    def _v6_44_class2(self, book_id) -> bool:
        g = (getattr(self, "_v6_40_grids", None) or {}).get(int(book_id))
        return g is not None and g.c == V6_44_CLASS2

    def _v6_44_snapshot(self) -> dict:
        tracker = getattr(self, "_v6_32_tracker", None)
        out = {"version": V6_44_C2T_VERSION,
               "c2t": int(self._v6_44_switch("research_v644_c2t")),
               "fgk": int(self._v6_44_switch("research_v644_fgk")),
               "c2qb": int(self._v6_44_switch("research_v644_c2qb")),
               "c2qb_last": dict(getattr(self, "_v6_44_c2qb_last", None) or {}),
               "counts": dict(getattr(self, "_v6_44_counts", None) or {}),
               "errors": int(getattr(self, "_v6_44_errors", 0) or 0)}
        try:
            if isinstance(tracker, V6_44ClassFeeTracker):
                for c in (V6_44_CLASS0, V6_44_CLASS2):
                    e = tracker.class_estimate(c)
                    out["est%d_bps" % c] = None if e is None else round(e * 1e4, 6)
                    out["fills%d" % c] = int(tracker.class_fills(c))
        except Exception:
            self._v6_44_errors = int(getattr(self, "_v6_44_errors", 0) or 0) + 1
        return out

    def _v6_40_snapshot(self) -> dict:
        """V640Part's snapshot plus this part's under "v6_44" (the V6_40_STATE row).  Never raises past V640Part's."""
        out = super()._v6_40_snapshot()
        try:
            out["v6_44"] = self._v6_44_snapshot()
        except Exception:
            self._v6_44_errors = int(getattr(self, "_v6_44_errors", 0) or 0) + 1
        return out

    # ---- C2T -------------------------------------------------------------------------------------------------------

    def _v6_44_c2t_on(self, book_id) -> bool:
        """C2T takes this book: the switch on, v6.17's touch quote on and a traded class-2 book.  Never raises."""
        try:
            return bool(self._v6_44_switch("research_v644_c2t") and getattr(self, "research_v617_touch_quote", False)
                        and self._v6_44_class2(book_id))
        except Exception:
            self._v6_44_errors = int(getattr(self, "_v6_44_errors", 0) or 0) + 1
            return False

    def _v6_44_c2t_book(self, response, state, book_id: int, deep, raw_bid, raw_ask, inv, other_rows, touch_rows,
                        already, req, *, paced: bool, held: frozenset, tick_size, min_order, budget: int, expiry: int,
                        cfg) -> int:
        """C2T's error guard: the placements added (0 .. 2).  Never raises: an error counts and the book places nothing
        this request (fail closed)."""
        try:
            return int(self._v6_44_c2t_entry(response, state, book_id, deep, raw_bid, raw_ask, inv, other_rows,
                                             touch_rows, already, req, paced=paced, held=held, tick_size=tick_size,
                                             min_order=min_order, budget=budget, expiry=expiry, cfg=cfg))
        except Exception:
            self._v6_44_errors = int(getattr(self, "_v6_44_errors", 0) or 0) + 1
            return 0

    def _v6_44_c2t_entry(self, response, state, book_id: int, deep, raw_bid, raw_ask, inv, other_rows, touch_rows,
                         already, req, *, paced: bool, held: frozenset, tick_size, min_order, budget: int, expiry: int,
                         cfg) -> int:
        """C2T on one class-2 book this request (the engine's c2t path; research_v644_c2t).

        Units: v6.17's touch clip (CLIP_DEEP_CLIPS x the deep layer's clip) and v6.10's bound, in class-0 units like
        ``inv`` (the pass divided the TRUE position by the class's scale); the order in the class's scale, minimum and
        volume grid.  Each side's plan (side_plan): the cap, the bound, the pace line, S14 (class 2's own estimate with
        FGK), D4, v6.19 / v6.41 B2b's hold.  A resting touch quote is cancelled when its side takes none or the touch moved
        REST_TICKS past it; an order of another layer of ours is cancelled.  A side with none kept takes one -- v6.17's
        join, v6.31 S12b's improve without the fp-edge test -- once nothing of ours is cancelled, placed or in flight on
        it.  Returns the placements added."""
        clip = float(V617_CLIP_DEEP_CLIPS) * float(getattr(deep, "clip", 0.0) or 0.0) if deep is not None else 0.0
        if clip <= 0.0:
            self._v6_44_count("no_units_states")
            return 0
        bound = float(self._v610_bound_base(deep, paced))
        scale, vmin, vdec = self._v6_40_units(book_id, min_order, cfg)
        inv = float(inv or 0.0)
        cap = float(self._research_volume_cap_quote(state) or 0.0)
        used = float(self._research_book_traded_volume(book_id) or 0.0)
        capped = cap > 0.0 and used >= cap
        gated = frozenset()
        if bool(getattr(self, "research_v632_fee_making", False)):
            gated = frozenset(self._v6_32_touch_sides(book_id))
        d4_off = bool(getattr(self, "research_v633_touch_identity", False)) and bool(self._v6_33_off(book_id))
        self._v6_44_count("book_states")
        plan = {}
        for side in (V63_SIDE_BUY, V63_SIDE_SELL):
            plan[side] = v6_44_side_plan(side, inventory=inv, clip=clip, bound=bound, capped=capped, paced=bool(paced),
                                         fee_gated=side in gated, held=side in (held or ()), d4_off=d4_off)
            self._v6_44_count("armed" if plan[side][1] is None else "held_" + str(plan[side][1]).lower())
        doomed, kept = [], set()
        for row in touch_rows or ():
            if int(row.order_id) in already:
                continue
            side = V63_SIDE_BUY if int(row.side) == 0 else V63_SIDE_SELL
            want, why_un = plan[side]
            why = v6_44_resting_verdict(side, float(row.price), want=float(want), why=why_un, bid=raw_bid, ask=raw_ask,
                                        tick=tick_size, rest_ticks=V617_REST_TICKS)
            if why is None:
                kept.add(side)
            else:
                doomed.append((row, why))
        for row in other_rows or ():
            if int(row.order_id) not in already:
                doomed.append((row, V6_44_CANCEL_OTHER))     # the deep layer / v6.3 roles: only the C2T quotes rest here
        for _row, why in doomed:
            self._v6_44_count("cancel_" + str(why).lower())
        cancelled = self._v617_cancel(response, book_id, doomed, req, budget)
        safety = int(getattr(self, "research_post_only_safety_ticks", 2) or 2)
        placed = 0
        for side in (V63_SIDE_BUY, V63_SIDE_SELL):
            want = float(plan[side][0])
            if want <= 0.0 or side in kept:
                continue
            if side in cancelled:
                self._v6_44_count("wait_cancel_seen")       # re-placed once the cancel is seen (A1.7.4.3.1)
                continue
            if side in v617_placed_sides(getattr(response, "instructions", None) or (), book_id):
                self._v6_44_count("side_taken")             # one maker order per book side per response
                continue
            if V617_TOUCH_SLOT in (self._v69_live_slots(book_id).get(side) or frozenset()):
                self._v6_44_count("slot_in_flight")
                continue
            join = v617_join_price(side, raw_bid, raw_ask, tick_size, safety)
            if join is None:
                self._v6_44_count("no_join")                # a crossed book or no postable touch
                continue
            price, how = v6_44_touch_price(side, self._v6_44_view(book_id), tick_size, join_price=join)
            if price is None:
                self._v6_44_count("no_price")
                continue
            self._v6_44_count("price_" + str(how))
            q = v62_lot_quantity(want * scale, vdec)
            if q + 1e-12 < vmin:
                self._v6_44_count("below_min_order")
                continue
            if self._v691_free_base_on():
                free = self._v691_free_for(book_id, side, price)
                sized = v691_quantity_within_free(q, free, vmin, vdec)
                if free is not None and sized + 1e-12 < q:
                    if sized + 1e-12 < vmin:
                        self._v6_44_count("no_free")
                        continue
                    self._v6_44_count("sized_by_free")
                    q = sized
            if self._count_book_instructions(response, book_id) >= budget:
                self._v6_44_count("budget_deferred")
                continue
            buy_cid, sell_cid = v617_client_ids(book_id)
            response.limit_order(
                book_id=book_id,
                direction=OrderDirection.BUY if side == V63_SIDE_BUY else OrderDirection.SELL,
                quantity=q, price=float(price), clientOrderId=buy_cid if side == V63_SIDE_BUY else sell_cid,
                stp=STP.CANCEL_BOTH, postOnly=True, timeInForce=TimeInForce.GTT, expiryPeriod=expiry,
                leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0,
            )
            placed += 1
            req["placed_touch"] = int(req.get("placed_touch", 0) or 0) + 1
            self._v6_44_count("placed_" + str(side))
        return placed

    def _v6_44_view(self, book_id: int):
        """v6.2.10's touch view of the book v6.31 S12b noted this request (every order of ours left out, as
        _v6_31_price builds it); None without a note of this book and request.  Never raises."""
        try:
            noted = getattr(self, "_v6_31_noted", None)
            if noted is None or noted[0] != int(book_id) or noted[1] != int(getattr(self, "_tick", 0) or 0):
                return None
            own_cids = (
                v63_own_client_ids(book_id) | set(v62_entry_client_ids(book_id))
                | set(v6214_exit_client_ids(book_id)) | v633_own_client_ids(book_id) | set(v617_client_ids(book_id))
            )
            return v6210_touch_view(noted[2], own_ids=noted[3], own_cids=own_cids)
        except Exception:
            self._v6_44_errors = int(getattr(self, "_v6_44_errors", 0) or 0) + 1
            return None

    # ---- FGK -------------------------------------------------------------------------------------------------------

    def _v6_40_init(self) -> None:
        """V640Part's init, then with FGK on: S14's tracker is FGK's ClassFeeTracker (C2Q's class tracker plus S14's
        window per class; every base result the same), made before any request.  Never raises past V640Part's."""
        super()._v6_40_init()
        try:
            if self._v6_44_switch("research_v644_fgk"):
                self._v6_32_tracker = V6_44ClassFeeTracker(weight=float(getattr(self, "research_v632_fee_weight", 0.0)
                                                                         or 0.0))
                self._v6_44_count("fgk_tracker_made")
        except Exception:
            self._v6_44_errors = int(getattr(self, "_v6_44_errors", 0) or 0) + 1

    def _v6_44_fgk_est(self, book_id: int):
        """(True, the estimate of the book's own class) while FGK reads it -- the switch on, S14's tracker FGK's and the
        book in its class map; else (False, None): v6.32's pooled estimate.  Never raises."""
        try:
            if not self._v6_44_switch("research_v644_fgk"):
                return False, None
            tracker = getattr(self, "_v6_32_tracker", None)
            if not isinstance(tracker, V6_44ClassFeeTracker):
                return False, None
            cls = tracker.class_of.get(int(book_id))
            if cls is None:
                return False, None
            return True, tracker.class_estimate(int(cls))
        except Exception:
            self._v6_44_errors = int(getattr(self, "_v6_44_errors", 0) or 0) + 1
            return False, None

    def _v6_32_note(self, book_id: int, inv) -> None:
        """V632Part's note with FGK: the book is gated on its own class's estimate (the pooled one is left as it is for
        every other reader).  FGK off or a book outside the class map: V632Part's as is."""
        own, est = self._v6_44_fgk_est(book_id)
        if not own:
            return super()._v6_32_note(book_id, inv)
        pooled = getattr(self, "_v6_32_est", None)
        self._v6_32_est = est
        try:
            super()._v6_32_note(book_id, inv)
        finally:
            self._v6_32_est = pooled

    # ---- C2QB ------------------------------------------------------------------------------------------------------

    def _v6_40_c2q_update(self) -> None:
        """V641Part's C2Q update (V640Part's rule, the saved verdict), then with C2QB on: a NEW decision of this block that
        disarms class 2 on the rule's numbers stands only when C2QB's bound on the tracker's class-2 fills is below 0;
        otherwise the block is armed (the verdict telemetry follows; v6.40's block counts stay the point rule's).
        Probes, armed decisions and a saved verdict without the rule's numbers: unchanged.  Never raises past V641Part's:
        an error counts and the decision stands."""
        st = getattr(self, "_v6_40_c2q", None)
        before = st.get("block") if isinstance(st, dict) else None
        prev_verdict = getattr(self, "_v6_40_c2q_verdict", None)
        prev_changed = bool(getattr(self, "_v6_40_c2q_changed", False))
        super()._v6_40_c2q_update()
        if not self._v6_44_switch("research_v644_c2qb"):
            return
        try:
            st = getattr(self, "_v6_40_c2q", None)
            if not isinstance(st, dict) or st.get("block") == before or st.get("probe", True) or st.get("armed", True):
                return
            tracker = getattr(self, "_v6_32_tracker", None)
            thr, q = st.get("thr"), st.get("q")
            if not isinstance(tracker, V6_44ClassTracker) or thr is None or q is None:
                self._v6_44_count("c2qb_no_numbers_blocks")
                return
            th = float(thr) * float(q)
            if th != th or th in (float("inf"), float("-inf")):
                self._v6_44_count("c2qb_no_numbers_blocks")
                return
            b = v6_44_c2qb_bound(tracker.v640, V6_44_CLASS2, th, v6_44_c2qb_alpha(tracker.window_ns))
            self._v6_44_c2qb_last = {"block": st.get("block"), "G": b["G"], "D": b["D"], "sD": b["sD"],
                                     "tq": b["tq"], "ub": b["ub"], "armed": bool(b["armed"])}
            if not b["armed"]:
                self._v6_44_count("c2qb_off_blocks")
                return
            st["armed"] = True
            st["c2qb_held"] = True
            self._v6_44_count("c2qb_held_armed_blocks" if b["G"] >= 2 else "c2qb_nobound_blocks")
            self._v6_40_c2q_verdict = "armed"
            if prev_verdict == "armed":
                self._v6_40_c2q_changed = prev_changed     # no verdict change after all
        except Exception:
            self._v6_44_errors = int(getattr(self, "_v6_44_errors", 0) or 0) + 1
