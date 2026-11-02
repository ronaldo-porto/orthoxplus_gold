"""v6.46 (USER D3 10-30 ~14:1x JST "GO only if 4.3 AND 4.4 pass"; Round 5 Stage 4 combined arm S4P7: 4.3 PASS 10-31 03:58,
4.4 PASS on the held-out T251 10-31 07:43 JST): v6.45 + the S4P7 rules, each behind its own switch (code default 0:
v6.45 on every path), all on in PARAMS but C2RQ, which is NOT built (BLOCKED, SP/v646 hand-off) --

class 2 (Stage 1 R6C2RQ):  research_v646_c2np  C2BC's centre without Pt;
                           research_v646_c2tb  C2T's tie at the centre resolved by the TRUE inventory;
                           research_v646_c2rf  a resting adding-side C2T quote lives one state (re-placed once seen);
class 1 (Stage 2 R5TU):    research_v646_c1exx the class-1 touch quote exempt from the A1.9 size-shortfall cancel;
                           research_v646_c1tu  the class-1 centre-side touch quote sized max(clip, |inv - tau|);
class 0 (Stage 3 R10LKAW): research_v646_s5tcu the class-0 centre-side touch quote sized max(clip, |inv - tau|);
                           research_v646_s5x0  the class-0 touch quote exempt from the A1.9 size-shortfall cancel;
                           research_v646_s5lkc the lean centre x s3kh while the book's front-load multiple is above 1;
                           research_v646_s5aw  the deep layer holds the adding (vs the centre) capture-surplus side.
The rules are research_v646_rules' (the engine's arithmetic, round5/vm_r5m4.py 907cbe99 + round5/vm_r5b5.py dd01c9f4).

This mixin, first among Simple's bases, carries the agent side WITHOUT a line in Simple's body:

* mixin methods it overrides (found before the older parts' in the MRO): V645Part._v6_45_lean (S5LKC: the BSL tau of a
  class-0 book x s3kh -- v6.25's tau store and tally follow it, so everything downstream reads the scaled centre),
  V645Part._v6_45_c2bc (C2NP), V644Part._v6_44_c2t_book (the TRUE inventory noted before V645Part centres it) and
  _v6_44_c2t_entry (C2TB / C2RF on: _v6_46_c2t_core), V640Part._v6_40_touch_core (C1TU on: _v6_46_touch_core);
* Simple's own methods it replaces on the INSTANCE (an instance attribute is found before the class's method; v6.45's
  pattern for _v6_25_lean), bound in the _v6_40_init override only while their switch is on: _v617_touch ->
  _v6_46_touch (S5TCU), _a191_decide -> _v6_46_a191_decide (S5X0 / C1EXX), _v633_book -> _v6_46_deep_book (S5AW);
  each runs Simple's own method (``type(self).<name>``) or its GENERATED copy (SP/v646/gen_v646.py: one statement
  changed, the parity test compares the ASTs);
* the V6_40_STATE row carries this part's snapshot under "v6_46" (_v6_40_snapshot override).
No new persisted state: C2NP reads v6.45's gap ring, S5LKC v6.21's line state, S5AW the making mirror, the rest is per
request.  Every path never raises: an error counts (_v6_46_errors, in the row) and the book runs v6.45's rule (C2T: its
own fail-closed rule).  No taking.  STRUCTURAL."""
from __future__ import annotations

import math

from taos.im.protocol.models import LoanSettlementOption, OrderDirection, STP, TimeInForce

from research_direct_exit_refresh import EXIT_HOLD as V6_46_EXIT_HOLD
from research_direct_exit_refresh import EXIT_REPRICE as V6_46_EXIT_REPRICE
from research_direct_exit_refresh import REASON_QUEUE_PRESERVED as V6_46_QUEUE_PRESERVED
from research_direct_exit_refresh import REASON_SIZE_SHORTFALL as V6_46_SIZE_SHORTFALL
from research_direct_exit_refresh import classify_resting_maker_exit as v6_46_classify_exit
from research_v617_touch_quote import CANCEL_BOUND as V617_CANCEL_BOUND
from research_v617_touch_quote import CANCEL_NO_FP as V617_CANCEL_NO_FP
from research_v617_touch_quote import CANCEL_PACED as V617_CANCEL_PACED
from research_v617_touch_quote import CLIP_DEEP_CLIPS as V617_CLIP_DEEP_CLIPS
from research_v617_touch_quote import REST_TICKS as V617_REST_TICKS
from research_v617_touch_quote import TOUCH_SLOT as V617_TOUCH_SLOT
from research_v617_touch_quote import client_ids as v617_client_ids
from research_v617_touch_quote import is_touch_client_id as v6_46_is_touch_cid
from research_v617_touch_quote import join_price as v617_join_price
from research_v617_touch_quote import placed_sides as v617_placed_sides
from research_v617_touch_quote import room as v617_room
from research_v617_touch_quote import toward_side as v617_toward_side
from research_v617_touch_quote import verdict as v617_verdict
from research_v617_touch_quote import wanted_price as v617_wanted_price
from research_v619_surplus_hold import CANCEL_SURPLUS as V619_CANCEL_SURPLUS
from research_v620_room_pace import scaled_quantity as v620_scaled_quantity
from research_v62_breadth import lot_quantity as v62_lot_quantity
from research_v63_trend_target import SIDE_BUY as V63_SIDE_BUY, SIDE_SELL as V63_SIDE_SELL
from research_v641_pace import adds as v641_adds
from research_v644_c2t import CANCEL_OTHER as V6_44_CANCEL_OTHER
from research_v644_c2t import resting_verdict as v6_44_resting_verdict
from research_v644_c2t import touch_price as v6_44_touch_price
from research_v645_bsl import lbw_on as v6_46_lbw_on
from research_v645_frontload import FrontLoadLine as V6_46FrontLoadLine
from research_v646_rules import CANCEL_C2RF as V6_46_CANCEL_C2RF
from research_v646_rules import EXEMPT_TAG as V6_46_EXEMPT_TAG
from research_v646_rules import SWITCHES as V6_46_SWITCHES
from research_v646_rules import V646_RULES_VERSION
from research_v646_rules import aw_sides as v6_46_aw_sides
from research_v646_rules import c2_side_plan as v6_46_c2_side_plan
from research_v646_rules import c2np_tau as v6_46_c2np_tau
from research_v646_rules import c2rf_refresh as v6_46_c2rf_refresh
from research_v646_rules import front_k as v6_46_front_k
from research_v646_rules import lkc_tau as v6_46_lkc_tau
from research_v646_rules import parse_switch as v6_46_parse_switch
from research_v646_rules import tcu_size as v6_46_tcu_size
from research_v69_deep_ladder import quantity_within_free as v691_quantity_within_free
from research_v6_29_touch_move import CANCEL_FP_MOVE as V6_29_CANCEL_FP_MOVE
from research_v6_32_fee_making import CANCEL_FEE_GATE as V6_32_CANCEL_FEE_GATE
from research_v6_33_touch_identity import CANCEL_TOUCH_IDENTITY as V6_33_CANCEL_TOUCH_IDENTITY
from research_v6_38_kbl import join as v6_38_kbl_join
from research_v6_38_kbl import verdict as v6_38_kbl_verdict
from research_v6_40_classes import CLASS1 as V6_46_CLASS1
from research_v6_40_classes import HOME_CLASS as V6_46_HOME

V6_46_KEYS = tuple(name[len("research_v646_"):] for name in V6_46_SWITCHES)


class V646Part:
    """v6.46 (research_v646_*): S4P7's class-2 (C2NP, C2TB, C2RF), class-1 (C1EXX, C1TU) and class-0 (S5TCU, S5X0, S5LKC,
    S5AW) rules; C2RQ not built"""

    # ---- switches and counters -----------------------------------------------------------------------------------

    def _v6_46_count(self, key: str, n: int = 1) -> None:
        counts = getattr(self, "_v6_46_counts", None)
        if counts is None:
            counts = {}
            self._v6_46_counts = counts
        counts[key] = int(counts.get(key, 0)) + int(n)

    def _v6_46_error(self) -> None:
        self._v6_46_errors = int(getattr(self, "_v6_46_errors", 0) or 0) + 1

    def _v6_46_cfg(self) -> dict:
        """Every v6.46 switch, read once from the config (code default 0).  An unreadable value, or one whose dependency
        is off, runs off and counts an error: C2NP / C2TB need v6.45's C2BC (itself C2T + bsl 2); C2RF C2T; C1EXX /
        S5X0 the A1.9 exit layer and v6.17's touch quote; C1TU / S5TCU the lean (v6.25 + BSL) and the touch quote;
        S5LKC the lean and v6.45's front-load; S5AW the lean and the making mirror (v6.19)."""
        st = getattr(self, "_v6_46_state", None)
        if st is not None:
            return st
        cfg = getattr(self, "config", None)
        st = {}
        for key in V6_46_KEYS:
            v = v6_46_parse_switch(getattr(cfg, "research_v646_" + key, 0))
            if v is None:
                self._v6_46_error()
                self._v6_46_count("unreadable_research_v646_" + key)
                v = 0
            st[key] = int(v)
        s45 = self._v6_45_cfg() if callable(getattr(self, "_v6_45_cfg", None)) else {}
        c2t = bool(getattr(self, "_v6_44_switch", None) and self._v6_44_switch("research_v644_c2t"))
        touch = bool(getattr(self, "research_v617_touch_quote", False))
        a19 = bool(callable(getattr(self, "_a191_enabled", None)) and self._a191_enabled())
        lean = bool(getattr(self, "research_v625_lean", False)) and bool(s45.get("bsl"))
        mirror = bool(getattr(self, "research_v619_surplus_hold", False))
        needs = {"c2np": bool(s45.get("c2bc")), "c2tb": bool(s45.get("c2bc")), "c2rf": c2t,
                 "c1exx": a19 and touch, "s5x0": a19 and touch, "c1tu": lean and touch, "s5tcu": lean and touch,
                 "s5lkc": lean and bool(s45.get("front")), "s5aw": lean and mirror}
        for key, ok in needs.items():
            if st.get(key) and not ok:
                self._v6_46_error()
                self._v6_46_count("refused_" + key)
                st[key] = 0
        self._v6_46_state = st
        return st

    # ---- init: the instance bindings ---------------------------------------------------------------------------------

    def _v6_40_init(self) -> None:
        """V645Part's init (and every older part's), then v6.46's switches; with S5TCU / S5X0 or C1EXX / S5AW on, Simple's
        _v617_touch / _a191_decide / _v633_book are bound on the instance to this part's (see the module docstring).
        Off: nothing is bound.  Never raises past V645Part's."""
        super()._v6_40_init()
        try:
            self._v6_46_state = None
            self._v6_46_taus = {}
            self._v6_46_c2 = None
            st = self._v6_46_cfg()
            if st["s5tcu"] and callable(getattr(type(self), "_v617_touch", None)):
                self._v617_touch = self._v6_46_touch
                self._v6_46_count("touch_bound")
            if (st["s5x0"] or st["c1exx"]) and callable(getattr(type(self), "_a191_decide", None)):
                self._a191_decide = self._v6_46_a191_decide
                self._v6_46_count("a191_bound")
            if st["s5aw"] and callable(getattr(type(self), "_v633_book", None)):
                self._v633_book = self._v6_46_deep_book
                self._v6_46_count("deep_bound")
        except Exception:
            self._v6_46_error()

    def _v6_46_tick(self) -> int:
        return int(getattr(self, "_tick", 0) or 0)

    # ---- class 0: S5LKC on the lean ----------------------------------------------------------------------------------

    def _v6_45_lean(self, book_id: int, raw_bid, raw_ask, cfg, inv, now_ts: int, req) -> tuple:
        """V645Part's lean, then S5LKC on a class-0 book: while its front-load multiple (the engine's _s3_k on its launch
        line) is above 1, tau x s3kh -- v6.25's tau store (which v6.27's touch centre reads), the tally and the returned
        inv - tau carry the scaled centre.  The request's centre is noted for S5TCU / C1TU.  Never raises: an error
        counts and the book keeps V645Part's lean."""
        out = super()._v6_45_lean(book_id, raw_bid, raw_ask, cfg, inv, now_ts, req)
        b = int(book_id)
        tau = 0.0
        try:
            taus = getattr(self, "_v6_25_taus", None) or {}
            got = taus.get(b)
            tau = float(got[0]) if got is not None and int(got[1]) == int(now_ts) else 0.0
            st = self._v6_46_cfg()
            if st["s5lkc"] and tau != 0.0 and self._v6_45_class_of(b) == V6_46_HOME:
                pace = getattr(self, "_v66_pace", None)
                if isinstance(pace, V6_46FrontLoadLine):
                    k = v6_46_front_k(pace, b, int(now_ts))
                    t2 = float(v6_46_lkc_tau(tau, k, self._v6_45_cfg()["s3kh"]))
                    self._v6_46_count("lkc_evals")
                    if t2 != tau:
                        taus[b] = (t2, int(now_ts))
                        x = float(inv or 0.0)
                        if isinstance(req, dict) and "abs_tau_sum" in req:
                            req["abs_tau_sum"] += abs(t2) - abs(tau)
                            req["max_abs_tau"] = max(float(req["max_abs_tau"]), abs(t2))
                            req["abs_inv_minus_tau_sum"] += abs(x - t2) - abs(x - tau)
                        out = (x - t2, out[1])
                        tau = t2
                        self._v6_46_count("lkc_scaled")
        except Exception:
            self._v6_46_error()
        try:
            store = getattr(self, "_v6_46_taus", None)
            if store is None:
                store = {}
                self._v6_46_taus = store
            store[b] = (float(tau), self._v6_46_tick())
        except Exception:
            self._v6_46_error()
        return out

    # ---- class 2: C2NP, C2TB, C2RF -----------------------------------------------------------------------------------

    def _v6_45_c2bc(self, book_id: int, raw_bid, raw_ask, cfg, now_ts: int, mid: float) -> None:
        """C2NP on: C2BC's centre of a C2T class-2 book this request from gd = g - gbar (V645Part's rule without the Pt
        term; the gap ring and its LBW mean are V645Part's).  Off: V645Part's.  Never raises: an error counts and the
        centre is 0."""
        if not self._v6_46_cfg()["c2np"]:
            return super()._v6_45_c2bc(book_id, raw_bid, raw_ask, cfg, now_ts, mid)
        b = int(book_id)
        tau2 = 0.0
        try:
            st = self._v6_45_cfg()
            rings = self._v6_45_rings_ref()
            fp, _reason = self._v6_23_fundamental(book_id, raw_bid, raw_ask, cfg)
            ring = rings.mid.get(b)
            if fp is not None and float(fp) > 0.0 and mid > 0.0 and ring is not None and ring.started:
                g = math.log(float(fp) / mid) * 1e4
                young = v6_46_lbw_on(st["lbw"], True)
                gring = rings.push_gap(b, now_ts, g)
                tau2 = float(v6_46_c2np_tau(g, gring.mean(young), getattr(self, "research_v623_against_gap_bps", 0.0),
                                            getattr(self, "research_v625_lean_slope", 0.0),
                                            getattr(self, "research_v625_lean_cap", 0.0)))
                self._v6_45_count("c2bc_evals")
                self._v6_46_count("c2np_evals")
                if tau2:
                    self._v6_45_count("c2bc_armed")
        except Exception:
            self._v6_46_error()
            tau2 = 0.0
        store = getattr(self, "_v6_45_c2tau", None)
        if store is None:
            store = {}
            self._v6_45_c2tau = store
        store[b] = (tau2, int(now_ts))

    def _v6_44_c2t_book(self, response, state, book_id: int, deep, raw_bid, raw_ask, inv, other_rows, touch_rows,
                        already, req, *, paced: bool, held: frozenset, tick_size, min_order, budget: int, expiry: int,
                        cfg) -> int:
        """The book's TRUE inventory (class-0 units, before V645Part centres it on tau2) noted for C2TB / C2RF, then
        V645Part's C2T book as is."""
        try:
            self._v6_46_c2 = (int(book_id), self._v6_46_tick(), float(inv or 0.0))
        except Exception:
            self._v6_46_error()
            self._v6_46_c2 = None
        return super()._v6_44_c2t_book(response, state, book_id, deep, raw_bid, raw_ask, inv, other_rows, touch_rows,
                                       already, req, paced=paced, held=held, tick_size=tick_size, min_order=min_order,
                                       budget=budget, expiry=expiry, cfg=cfg)

    def _v6_46_c2_inputs(self, book_id: int, inv) -> tuple:
        """(the TRUE inventory noted for this book and request -- else ``inv`` --, C2TB on, C2RF on)."""
        st = self._v6_46_cfg()
        got = getattr(self, "_v6_46_c2", None)
        if got is not None and int(got[0]) == int(book_id) and int(got[1]) == self._v6_46_tick():
            true_inv = float(got[2])
        else:
            true_inv = float(inv or 0.0)
            self._v6_46_count("c2_true_inv_missing")
        return true_inv, bool(st["c2tb"]), bool(st["c2rf"])

    def _v6_44_c2t_entry(self, response, state, book_id: int, deep, raw_bid, raw_ask, inv, other_rows, touch_rows,
                         already, req, *, paced: bool, held: frozenset, tick_size, min_order, budget: int, expiry: int,
                         cfg) -> int:
        """C2TB or C2RF on: _v6_46_c2t_core (V644Part's entry with both rules); both off: V644Part's."""
        st = self._v6_46_cfg()
        if not (st["c2tb"] or st["c2rf"]):
            return super()._v6_44_c2t_entry(response, state, book_id, deep, raw_bid, raw_ask, inv, other_rows,
                                            touch_rows, already, req, paced=paced, held=held, tick_size=tick_size,
                                            min_order=min_order, budget=budget, expiry=expiry, cfg=cfg)
        return self._v6_46_c2t_core(response, state, book_id, deep, raw_bid, raw_ask, inv, other_rows, touch_rows,
                                    already, req, paced=paced, held=held, tick_size=tick_size, min_order=min_order,
                                    budget=budget, expiry=expiry, cfg=cfg)

    # ---- classes 0 / 1: the touch size (S5TCU / C1TU) -------------------------------------------------------------------

    def _v6_46_tcu_clip(self, book_id: int, toward, inv, clip):
        """The touch quote's size before v6.20's scale: S5TCU on a class-0 book, C1TU on a class-1 book -- max(clip,
        |inv - tau|) on the centre's side while short of it (research_v646_rules.tcu_size; ``inv`` = inv - tau, tau this
        request's centre); else ``clip``.  Never raises: an error counts and the size is ``clip``."""
        try:
            st = self._v6_46_cfg()
            c = self._v6_45_class_of(book_id)
            if not ((st["s5tcu"] and c == V6_46_HOME) or (st["c1tu"] and c == V6_46_CLASS1)):
                return clip
            got = (getattr(self, "_v6_46_taus", None) or {}).get(int(book_id))
            tau = float(got[0]) if got is not None and int(got[1]) == self._v6_46_tick() else 0.0
            out = v6_46_tcu_size(toward, inv, tau, clip)
            tag = "c%d" % int(c)
            if tau:
                self._v6_46_count("tcu_evals_" + tag)
            if out > float(clip):
                self._v6_46_count("tcu_up_" + tag)
            return out
        except Exception:
            self._v6_46_error()
            return clip

    def _v6_40_touch_core(self, response, book_id: int, deep, raw_bid, raw_ask, inv, touch_rows, already, req, **kw) -> int:
        """C1TU on: _v6_46_touch_core (V640Part's touch with TCU's size); off: V640Part's."""
        if not self._v6_46_cfg()["c1tu"]:
            return super()._v6_40_touch_core(response, book_id, deep, raw_bid, raw_ask, inv, touch_rows, already, req,
                                             **kw)
        return self._v6_46_touch_core(response, book_id, deep, raw_bid, raw_ask, inv, touch_rows, already, req, **kw)

    # ---- classes 0 / 1: the touch quote's A1.9 size exemption (S5X0 / C1EXX) --------------------------------------------

    def _v6_46_a191_decide(self, state, book_id: int, inventory, qty: float, desired_price, desired_action):
        """Bound as _a191_decide with S5X0 or C1EXX on: Simple's verdict, except that a REPRICE for SIZE_SHORTFALL on our
        TOUCH quote of a class-0 (S5X0) / class-1 (C1EXX) book is decided again without the size test -- the engine's
        exx / s5x0 (the touch slot exempt from the A1.9 size cancel); every other reason still applies (the classifier's
        own order, its REMAINING_TTL deferral).  The cached verdict is updated in place.  Never raises: Simple's verdict."""
        verdict = type(self)._a191_decide(self, state, book_id, inventory, qty, desired_price, desired_action)
        try:
            if (not isinstance(verdict, dict) or verdict.get("decision") != V6_46_EXIT_REPRICE
                    or verdict.get("reason") != V6_46_SIZE_SHORTFALL or verdict.get("v6_46")):
                return verdict
            st = self._v6_46_cfg()
            b = int(book_id)
            c = self._v6_45_class_of(b)
            if not ((st["s5x0"] and c == V6_46_HOME) or (st["c1exx"] and c == V6_46_CLASS1)):
                return verdict
            st_b = self._v6_41_book_state(state, b) if getattr(self, "research_v641_unit_safety", False) else state
            now_ns = int(getattr(st_b, "timestamp", 0) or 0)
            net_base = float(getattr(inventory, "net_base", 0.0) or 0.0)
            row = self._a191_live_exit_row(b, net_base, now_ns)
            if row is None or int(row.order_id) != int(verdict.get("order_id", -1)) \
                    or not v6_46_is_touch_cid(getattr(row, "client_id", None)):
                return verdict
            decision, reason = v6_46_classify_exit(
                existing_price=row.price, desired_price=float(verdict["desired_price"]),
                tick_size=self._a19_tick_size(st_b), long_position=net_base > 0.0,
                existing_qty=float(row.remaining or row.quantity),
                desired_qty=abs(float(qty)) if qty else abs(net_base),
                existing_net_bps=self._a19_resting_net_bps(b, inventory, row.price),
                floor_net_bps=self._v623_resting_floor_bps(b, st_b),
                existing_action=row.action, desired_action=desired_action,
                reprice_ticks=float(getattr(self, "research_profitable_exit_reprice_ticks", 3.0) or 3.0),
                min_qty_fraction=0.0,
            )
            if decision == V6_46_EXIT_REPRICE and float(verdict.get("remaining_ms", 0.0)) < self.A191_MIN_REMAINING_TTL_MS:
                decision, reason = V6_46_EXIT_HOLD, V6_46_QUEUE_PRESERVED
                verdict["deferred"] = "REMAINING_TTL"
            tag = "c%d" % int(c)
            verdict["v6_46"] = V6_46_EXEMPT_TAG
            verdict["reason"] = reason
            if decision != V6_46_EXIT_REPRICE:
                verdict["decision"] = decision
                self._a191_reprice_candidates = int(getattr(self, "_a191_reprice_candidates", 0) or 0) - 1
                self._v6_46_count("size_exempt_kept_" + tag)
            else:
                self._v6_46_count("size_exempt_other_reason_" + tag)
        except Exception:
            self._v6_46_error()
        return verdict

    # ---- class 0: S5AW on the deep layer -----------------------------------------------------------------------------

    def _v6_46_deep_book(self, response, book_id, deep, depth, raw_bid, raw_ask, inv, rows, deep_rows, already, req,
                         **kw) -> int:
        """Bound as _v633_book with S5AW on: on a class-0 book the deep side that adds relative to the centre (``inv`` =
        inv - tau) and leads the making window's capture strictly joins ``held`` for the deep layer (level 0 and the
        ladder: a resting order cancelled, none placed; the touch quote is not held); then Simple's _v633_book.
        Never raises: an error counts and the call is Simple's as is."""
        try:
            if self._v6_46_cfg()["s5aw"] and self._v6_45_class_of(book_id) == V6_46_HOME:
                mirror = getattr(self, "_v62_mirror", None)
                if mirror is not None:
                    buy, sell = mirror.book_capture(int(book_id))
                    aw = v6_46_aw_sides(inv, buy, sell)
                    self._v6_46_count("aw_evals")
                    if aw:
                        kw["held"] = frozenset(kw.get("held") or ()) | aw
                        self._v6_46_count("aw_held_states")
        except Exception:
            self._v6_46_error()
        return type(self)._v633_book(self, response, book_id, deep, depth, raw_bid, raw_ask, inv, rows, deep_rows,
                                     already, req, **kw)

    # ---- the state row ---------------------------------------------------------------------------------------------

    def _v6_46_snapshot(self) -> dict:
        return {"version": V646_RULES_VERSION, "switches": dict(self._v6_46_cfg()), "c2rq": "not built (BLOCKED)",
                "bound": sorted(k for k in ("_v617_touch", "_a191_decide", "_v633_book")
                                if k in getattr(self, "__dict__", {})),
                "counts": dict(getattr(self, "_v6_46_counts", None) or {}),
                "errors": int(getattr(self, "_v6_46_errors", 0) or 0)}

    def _v6_40_snapshot(self) -> dict:
        """V645Part's snapshot plus this part's under "v6_46" (the V6_40_STATE row).  Never raises past V645Part's."""
        out = super()._v6_40_snapshot()
        try:
            out["v6_46"] = self._v6_46_snapshot()
        except Exception:
            self._v6_46_error()
        return out

    # ---- GENERATED (SP/v646/gen_v646.py; the parity test re-runs the transforms on this tree and compares ASTs) --------

    def _v6_46_touch(self, response, book_id: int, deep, raw_bid, raw_ask, inv, touch_rows, already, req, *, tick_size, min_order, budget, expiry, cfg, paced: bool, held: frozenset=frozenset(), size_scale: float=1.0, cap_skip: bool=False) -> int:
        """v6.46 GENERATED (SP/v646/gen_v646.py) from Strategy1_Research_Simple._v617_touch: v6.17's class-0 touch quote, every statement Simple's except its size -- t_ = self._v6_46_tcu_clip(...) (S5TCU) in place of clip before v6.20's scale.  Bound as _v617_touch with research_v646_s5tcu on."""
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
            (v6_27_wide, bound) = (bound, self._v6_27_bound(book_id, clip, bound))
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
                why = v617_verdict(side, float(row.price), toward=toward, bid=raw_bid, ask=raw_ask, fundamental=fp, tick=tick_size)
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
        price = v617_wanted_price(toward, bid=raw_bid, ask=raw_ask, fundamental=fp, tick=tick_size, safety_ticks=int(getattr(self, 'research_post_only_safety_ticks', 2) or 2))
        if v6_38_win is not None:
            price = v6_38_kbl_join(toward, raw_bid, raw_ask, tick_size, int(getattr(self, 'research_post_only_safety_ticks', 2) or 2))
        if price is None:
            self._v617_count('no_price')
            return 0
        if bool(getattr(self, 'research_v631_touch_improve', False)):
            price = self._v6_31_price(book_id, toward, price, fp, tick_size)
        if v6_38_win is not None:
            price = self._v6_38_kbl_price(book_id, toward, price, fp, tick_size)
        t_ = self._v6_46_tcu_clip(book_id, toward, inv, clip)
        q_ = v620_scaled_quantity(t_, size_scale, min_order) if size_scale < 1.0 else t_
        if getattr(self, '_v6_38_l3_on', False):
            v6_38_q = self._v6_38_touch_size(book_id, toward, inv, clip, size_scale, min_order, v6_38_win)
            if v6_38_q is not None:
                q_ = v6_38_q
        q = v62_lot_quantity(q_, getattr(cfg, 'volumeDecimals', 4))
        if q + 1e-12 < min_order:
            self._v617_count('below_min_order')
            return 0
        if self._v691_free_base_on():
            try:
                free = self._v691_free_for(book_id, toward, price)
                sized = v691_quantity_within_free(q, free, min_order, getattr(cfg, 'volumeDecimals', 4))
            except Exception:
                self._v617_errors = int(getattr(self, '_v617_errors', 0) or 0) + 1
                (free, sized) = (None, q)
            if free is not None and sized + 1e-12 < q:
                if sized <= 0.0:
                    self._v617_count('no_free')
                    return 0
                self._v617_count('sized_by_free')
                q = sized
        if self._count_book_instructions(response, book_id) >= budget:
            self._v617_count('budget_deferred')
            return 0
        (buy_cid, sell_cid) = v617_client_ids(book_id)
        try:
            response.limit_order(book_id=book_id, direction=OrderDirection.BUY if toward == V63_SIDE_BUY else OrderDirection.SELL, quantity=q, price=float(price), clientOrderId=buy_cid if toward == V63_SIDE_BUY else sell_cid, stp=STP.CANCEL_BOTH, postOnly=True, timeInForce=TimeInForce.GTT, expiryPeriod=expiry, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
        except Exception:
            self._v617_errors = int(getattr(self, '_v617_errors', 0) or 0) + 1
            return 0
        req['placed_touch'] = int(req.get('placed_touch', 0) or 0) + 1
        self._v617_count('placed_' + str(toward))
        return 1

    def _v6_46_touch_core(self, response, book_id: int, deep, raw_bid, raw_ask, inv, touch_rows, already, req, *, tick_size, min_order, budget, expiry, cfg, paced: bool, held: frozenset=frozenset(), size_scale: float=1.0, cap_skip: bool=False) -> int:
        """v6.46 GENERATED (SP/v646/gen_v646.py) from V640Part._v6_40_touch_core: a traded non-home book's touch quote, every statement V640Part's except its size -- t_ = self._v6_46_tcu_clip(...) (C1TU on class 1) in place of clip before v6.20's scale."""
        (unit, rest) = self._v6_40_touch_units(book_id, tick_size)
        (scale, vmin, vdec) = self._v6_40_units(book_id, min_order, cfg)
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
            (v6_27_wide, bound) = (bound, self._v6_27_bound(book_id, clip, bound))
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
        t_ = self._v6_46_tcu_clip(book_id, toward, inv, clip)
        q_ = v620_scaled_quantity(t_, size_scale, min_order) if size_scale < 1.0 else t_
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
                (free, sized) = (None, q)
            if free is not None and sized + 1e-12 < q:
                if sized <= 0.0:
                    self._v617_count('no_free')
                    return 0
                self._v617_count('sized_by_free')
                q = sized
        if self._count_book_instructions(response, book_id) >= budget:
            self._v617_count('budget_deferred')
            return 0
        (buy_cid, sell_cid) = v617_client_ids(book_id)
        try:
            response.limit_order(book_id=book_id, direction=OrderDirection.BUY if toward == V63_SIDE_BUY else OrderDirection.SELL, quantity=q, price=float(price), clientOrderId=buy_cid if toward == V63_SIDE_BUY else sell_cid, stp=STP.CANCEL_BOTH, postOnly=True, timeInForce=TimeInForce.GTT, expiryPeriod=expiry, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
        except Exception:
            self._v617_errors = int(getattr(self, '_v617_errors', 0) or 0) + 1
            return 0
        req['placed_touch'] = int(req.get('placed_touch', 0) or 0) + 1
        self._v617_count('placed_' + str(toward))
        return 1

    def _v6_46_c2t_core(self, response, state, book_id: int, deep, raw_bid, raw_ask, inv, other_rows, touch_rows, already, req, *, paced: bool, held: frozenset, tick_size, min_order, budget: int, expiry: int, cfg) -> int:
        """v6.46 GENERATED (SP/v646/gen_v646.py) from V644Part._v6_44_c2t_entry: C2T on one class-2 book, every statement V644Part's except: the TRUE inventory and the C2TB / C2RF switches first (_v6_46_c2_inputs), the plan's adding test with C2TB (v6_46_c2_side_plan), and C2RF's one-state life on a resting adding-side quote."""
        (true_inv, tie_break, refresh) = self._v6_46_c2_inputs(book_id, inv)
        clip = float(V617_CLIP_DEEP_CLIPS) * float(getattr(deep, 'clip', 0.0) or 0.0) if deep is not None else 0.0
        if clip <= 0.0:
            self._v6_44_count('no_units_states')
            return 0
        bound = float(self._v610_bound_base(deep, paced))
        (scale, vmin, vdec) = self._v6_40_units(book_id, min_order, cfg)
        inv = float(inv or 0.0)
        cap = float(self._research_volume_cap_quote(state) or 0.0)
        used = float(self._research_book_traded_volume(book_id) or 0.0)
        capped = cap > 0.0 and used >= cap
        gated = frozenset()
        if bool(getattr(self, 'research_v632_fee_making', False)):
            gated = frozenset(self._v6_32_touch_sides(book_id))
        d4_off = bool(getattr(self, 'research_v633_touch_identity', False)) and bool(self._v6_33_off(book_id))
        self._v6_44_count('book_states')
        plan = {}
        for side in (V63_SIDE_BUY, V63_SIDE_SELL):
            plan[side] = v6_46_c2_side_plan(side, inventory=inv, true_inventory=true_inv, tie_break=tie_break, clip=clip, bound=bound, capped=capped, paced=bool(paced), fee_gated=side in gated, held=side in (held or ()), d4_off=d4_off)
            self._v6_44_count('armed' if plan[side][1] is None else 'held_' + str(plan[side][1]).lower())
        (doomed, kept) = ([], set())
        for row in touch_rows or ():
            if int(row.order_id) in already:
                continue
            side = V63_SIDE_BUY if int(row.side) == 0 else V63_SIDE_SELL
            if refresh and v6_46_c2rf_refresh(side, true_inv):
                doomed.append((row, V6_46_CANCEL_C2RF))
                continue
            (want, why_un) = plan[side]
            why = v6_44_resting_verdict(side, float(row.price), want=float(want), why=why_un, bid=raw_bid, ask=raw_ask, tick=tick_size, rest_ticks=V617_REST_TICKS)
            if why is None:
                kept.add(side)
            else:
                doomed.append((row, why))
        for row in other_rows or ():
            if int(row.order_id) not in already:
                doomed.append((row, V6_44_CANCEL_OTHER))
        for (_row, why) in doomed:
            self._v6_44_count('cancel_' + str(why).lower())
        cancelled = self._v617_cancel(response, book_id, doomed, req, budget)
        safety = int(getattr(self, 'research_post_only_safety_ticks', 2) or 2)
        placed = 0
        for side in (V63_SIDE_BUY, V63_SIDE_SELL):
            want = float(plan[side][0])
            if want <= 0.0 or side in kept:
                continue
            if side in cancelled:
                self._v6_44_count('wait_cancel_seen')
                continue
            if side in v617_placed_sides(getattr(response, 'instructions', None) or (), book_id):
                self._v6_44_count('side_taken')
                continue
            if V617_TOUCH_SLOT in (self._v69_live_slots(book_id).get(side) or frozenset()):
                self._v6_44_count('slot_in_flight')
                continue
            join = v617_join_price(side, raw_bid, raw_ask, tick_size, safety)
            if join is None:
                self._v6_44_count('no_join')
                continue
            (price, how) = v6_44_touch_price(side, self._v6_44_view(book_id), tick_size, join_price=join)
            if price is None:
                self._v6_44_count('no_price')
                continue
            self._v6_44_count('price_' + str(how))
            q = v62_lot_quantity(want * scale, vdec)
            if q + 1e-12 < vmin:
                self._v6_44_count('below_min_order')
                continue
            if self._v691_free_base_on():
                free = self._v691_free_for(book_id, side, price)
                sized = v691_quantity_within_free(q, free, vmin, vdec)
                if free is not None and sized + 1e-12 < q:
                    if sized + 1e-12 < vmin:
                        self._v6_44_count('no_free')
                        continue
                    self._v6_44_count('sized_by_free')
                    q = sized
            if self._count_book_instructions(response, book_id) >= budget:
                self._v6_44_count('budget_deferred')
                continue
            (buy_cid, sell_cid) = v617_client_ids(book_id)
            response.limit_order(book_id=book_id, direction=OrderDirection.BUY if side == V63_SIDE_BUY else OrderDirection.SELL, quantity=q, price=float(price), clientOrderId=buy_cid if side == V63_SIDE_BUY else sell_cid, stp=STP.CANCEL_BOTH, postOnly=True, timeInForce=TimeInForce.GTT, expiryPeriod=expiry, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
            placed += 1
            req['placed_touch'] = int(req.get('placed_touch', 0) or 0) + 1
            self._v6_44_count('placed_' + str(side))
        return placed
