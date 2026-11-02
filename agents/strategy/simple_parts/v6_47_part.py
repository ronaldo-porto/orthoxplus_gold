"""v6.47 (USER 11-01 20:16 JST option (c) "a second class-2 touch slot through an order-manager change"; 11-02 ~10:2x JST
"I decided option C"): v6.46 as built + the Round 5 Stage 4 pass 10 REPORTED arm S4P10P (classes 0 + 2 =
round5/s1r7/q/q_R7C2RQ.txt on round5/vm_r5c12.py 7f3b39e4: R11SAG ',s5sa=2' + c2rq; class 1 = R5B_R5TU, already v6.46),
each rule behind its own switch (code default 0: v6.46 on every path), all on in PARAMS --

class 0 (Stage 3 R11SAG):  research_v647_s5sa   0 / 1 / 2: a class-0 book's deep side (level 0 + ladder) holds its orders
                                                for the rest of the 30-s fp block while it is the k48 against side and
                                                the block's fp step moved >= aggap against it (1: only while it reduces
                                                the TRUE inventory; 2 = SAG, PARAMS);
class 2 (C2S2, the live form of c2rq):  research_v647_c2s2   0 / 1 / 2: a class-2 refresh cancel is a REPLACE through a second
                                                touch slot (slot B, client ids + 3 | 4, v6.9 slot 8) -- 1 = c2rq's two kinds
                                                (A1.9's size cancel of the reducing quote, C2RF's refresh of the adding
                                                one), 2 = the size cancel only;
                           research_v647_c2s2p  0 / 1: 1 = option P -- the replacement placement BEFORE the replaced row's
                                                cancel on the book (the engine's c2rq emit order: g1 / g3 cancels, p4
                                                touch placements, g5 refresh cancels); 0 = one cancel first (R7C2S2).
The rules are research_v647_rules' (R11SAG, switches) and research_v647_c2s2's (C2S2); the touch ids are
research_v617_touch_quote's (generalized to two slots, form D-a of round5/s1r7/c2s2_design.md).

This mixin, first among Simple's bases, carries the agent side; Simple's body changes in ONE statement (the final
validation's touch slot: per slot, F3) and its import line:

* R11SAG: V646Part._v6_46_deep_book overridden (found first in the MRO) -- the held sides join ``held`` for Simple's
  _v633_book beside S5AW; with S5SA on and S5AW off, _v633_book is bound on the instance to it (v6.46's pattern);
* C2S2: V646Part._v6_44_c2t_entry -> _v6_47_c2t_core (GENERATED from V646Part._v6_46_c2t_core, SP/v647/gen_v647.py: the
  touch rows split, C2T's own size clause, the cancel split, the slot rule, the slot's ids), with the replaced rows'
  cancel emitted AFTER the placements in a ``finally`` (option P); V644Part._v6_44_view -> _v6_47_view (slot B's ids
  left out of C2T's touch view, GENERATED); V641Part._v6_41_c2q_exit -> _v6_47_c2q_exit (B2b waits while EITHER slot is
  held, GENERATED); _a191_decide bound on the instance to _v6_47_a191_decide: A1.9's REPRICE on a C2T class-2 book is
  HOLD (C2T owns every order there -- its touch quotes by its own rules incl. the size clause, other layers' orders by
  CANCEL_OTHER), so no A1.9 cancel and no A1.9.1.2 book-side release can ever touch a slot-B reservation;
* the V6_40_STATE row carries this part's snapshot under "v6_47" (_v6_40_snapshot override).
State: the dying-row memo (C2T's cancels of the previous request, {(book, order id): tick}) is NOT persisted -- after a
restart a dying row is an ordinary row and is cancelled again (an extra cancel at worst: the A1.7.4.3.2 identity gate
ignores its stale notice); slot-B orders inherited across a restart are touch quotes by research_v617_touch_quote's ids
(the startup orphan cancel names them; C2T / C2Q manage them otherwise).  Every path never raises: an error counts
(_v6_47_errors, in the row) and the book runs v6.46's rule (C2T: its own fail-closed guard, nothing placed).  No taking.
STRUCTURAL."""
from __future__ import annotations

from taos.im.protocol.models import LoanSettlementOption, OrderDirection, STP, TimeInForce

from research_direct_exit_ledger import RestingInventoryView as V6_47RestingInventoryView
from research_direct_exit_refresh import ABSENT_REPRICE_CANCEL
from research_direct_exit_refresh import EXIT_HOLD as V6_47_EXIT_HOLD
from research_direct_exit_refresh import EXIT_REPRICE as V6_47_EXIT_REPRICE
from research_direct_exit_refresh import REASON_QUEUE_PRESERVED as V6_47_QUEUE_PRESERVED
from research_v617_touch_quote import CLIP_DEEP_CLIPS as V617_CLIP_DEEP_CLIPS
from research_v617_touch_quote import REST_TICKS as V617_REST_TICKS
from research_v617_touch_quote import TOUCH_SLOT as V617_TOUCH_SLOT
from research_v617_touch_quote import TOUCH_SLOT_B as V617_TOUCH_SLOT_B
from research_v617_touch_quote import client_ids as v617_client_ids
from research_v617_touch_quote import client_ids as v6_41_touch_ids
from research_v617_touch_quote import is_touch_client_id as v6_41_is_touch_cid
from research_v617_touch_quote import join_price as v617_join_price
from research_v617_touch_quote import placed_sides as v617_placed_sides
from research_v617_touch_quote import touch_held as v6_47_touch_held
from research_v6210_touch_improve import touch_view as v6210_touch_view
from research_v6214_touch_life import exit_client_ids as v6214_exit_client_ids
from research_v62_breadth import entry_client_ids as v62_entry_client_ids
from research_v62_breadth import lot_quantity as v62_lot_quantity
from research_v623_validator_063 import MODE_OFF as V6_47_V623_OFF
from research_v623_validator_063 import against_side as v6_47_against_side
from research_v623_validator_063 import parse_gap_bps as v6_47_gap_bps
from research_v633_deep_layer import own_client_ids as v633_own_client_ids
from research_v63_trend_target import SIDE_BUY as V63_SIDE_BUY, SIDE_SELL as V63_SIDE_SELL
from research_v63_trend_target import own_client_ids as v63_own_client_ids
from research_v644_c2t import CANCEL_OTHER as V6_44_CANCEL_OTHER
from research_v644_c2t import resting_verdict as v6_44_resting_verdict
from research_v644_c2t import touch_price as v6_44_touch_price
from research_v646_rules import CANCEL_C2RF as V6_46_CANCEL_C2RF
from research_v646_rules import c2_side_plan as v6_46_c2_side_plan
from research_v646_rules import c2rf_refresh as v6_46_c2rf_refresh
from research_v647_c2s2 import CANCEL_EXTRA as V6_47_CANCEL_EXTRA
from research_v647_c2s2 import CANCEL_RETRY as V6_47_CANCEL_RETRY
from research_v647_c2s2 import CANCEL_SIZE as V6_47_CANCEL_SIZE
from research_v647_c2s2 import HOLD_TAG as V6_47_HOLD_TAG
from research_v647_c2s2 import V647_C2S2_VERSION
from research_v647_c2s2 import cancel_plan as v6_47_cancel_plan
from research_v647_c2s2 import reducing as v6_47_reducing
from research_v647_c2s2 import size_shortfall as v6_47_size_shortfall
from research_v647_c2s2 import slot_decision as v6_47_slot_decision
from research_v647_c2s2 import split_rows as v6_47_split_rows
from research_v647_rules import SWITCHES as V6_47_SWITCHES
from research_v647_rules import V647_RULES_VERSION
from research_v647_rules import VALUES as V6_47_VALUES
from research_v647_rules import parse_value as v6_47_parse_value
from research_v647_rules import sa_sides as v6_47_sa_sides
from research_v647_rules import signed_step as v6_47_signed_step
from research_v647_rules import step_adverse as v6_47_step_adverse
from research_v6_40_classes import HOME_CLASS as V6_47_HOME
from research_v6_41_units import SIDE_BUY as V6_41_BUY, SIDE_SELL as V6_41_SELL
from research_v6_41_units import keeps_exit as v6_41_keeps_exit
from research_v6_41_units import reduce_exit as v6_41_reduce_exit
from research_v69_deep_ladder import quantity_within_free as v691_quantity_within_free

V6_47_KEYS = tuple(name[len("research_v647_"):] for name in V6_47_SWITCHES)


class V647Part:
    """v6.47 (research_v647_*): R11SAG on class 0's deep layer; C2S2 (the class-2 second touch slot, option P) in C2T"""

    # ---- switches and counters -----------------------------------------------------------------------------------

    def _v6_47_count(self, key: str, n: int = 1) -> None:
        counts = getattr(self, "_v6_47_counts", None)
        if counts is None:
            counts = {}
            self._v6_47_counts = counts
        counts[key] = int(counts.get(key, 0)) + int(n)

    def _v6_47_error(self) -> None:
        self._v6_47_errors = int(getattr(self, "_v6_47_errors", 0) or 0) + 1

    def _v6_47_tick(self) -> int:
        return int(getattr(self, "_tick", 0) or 0)

    def _v6_47_cfg(self) -> dict:
        """Every v6.47 switch, read once from the config (code default 0).  An unreadable value, or one whose dependency is
        off, runs off and counts an error: S5SA needs the fp step record (v6.36: research_v636_step_withdrawal, the
        engine's pst / ssd) and the k48 against side (v6.23 S1: research_v623_realized_mode not off and
        research_v623_against_gap_bps > 0 -- the engine's AG_ON at aggap); C2S2 needs C2T (research_v644_c2t), v6.17's
        touch quote, v6.46's C2RF and the A1.9 exit layer (the engine's c2t, c2rf, a19x, ftq / ftyield); C2S2P needs
        C2S2 = 1 (the only tested pairing)."""
        st = getattr(self, "_v6_47_state", None)
        if st is not None:
            return st
        cfg = getattr(self, "config", None)
        st = {}
        for name in V6_47_SWITCHES:
            key = name[len("research_v647_"):]
            v = v6_47_parse_value(getattr(cfg, name, 0), V6_47_VALUES[name])
            if v is None:
                self._v6_47_error()
                self._v6_47_count("unreadable_" + name)
                v = 0
            st[key] = int(v)
        s46 = self._v6_46_cfg() if callable(getattr(self, "_v6_46_cfg", None)) else {}
        c2t = bool(getattr(self, "_v6_44_switch", None) and self._v6_44_switch("research_v644_c2t"))
        touch = bool(getattr(self, "research_v617_touch_quote", False))
        a19 = bool(callable(getattr(self, "_a191_enabled", None)) and self._a191_enabled())
        step = bool(getattr(self, "research_v636_step_withdrawal", False)) and getattr(self, "_v6_36_record", None) is not None
        ag = (str(getattr(self, "research_v623_realized_mode", V6_47_V623_OFF) or V6_47_V623_OFF) != V6_47_V623_OFF
              and v6_47_gap_bps(getattr(self, "research_v623_against_gap_bps", 0.0)) > 0.0)
        for key, ok in (("s5sa", step and ag), ("c2s2", c2t and touch and bool(s46.get("c2rf")) and a19)):
            if st.get(key) and not ok:
                self._v6_47_error()
                self._v6_47_count("refused_" + key)
                st[key] = 0
        if st.get("c2s2p") and st.get("c2s2") != 1:
            self._v6_47_error()
            self._v6_47_count("refused_c2s2p")
            st["c2s2p"] = 0
        self._v6_47_state = st
        return st

    # ---- init: the instance bindings ---------------------------------------------------------------------------------

    def _v6_40_init(self) -> None:
        """V646Part's init (and every older part's), then v6.47's switches: with S5SA on, Simple's _v633_book is bound on
        the instance to _v6_46_deep_book (this part's override; V646Part binds it already with S5AW on); with C2S2 on,
        _a191_decide is bound to _v6_47_a191_decide over whatever V646Part bound.  Off: nothing.  Never raises past
        V646Part's."""
        super()._v6_40_init()
        try:
            self._v6_47_state = None
            self._v6_47_sent = {}
            self._v6_47_ctx = None
            st = self._v6_47_cfg()
            if st["s5sa"] and callable(getattr(type(self), "_v633_book", None)):
                if "_v633_book" not in getattr(self, "__dict__", {}):
                    self._v633_book = self._v6_46_deep_book
                self._v6_47_count("deep_bound")
            if st["c2s2"] and callable(getattr(type(self), "_a191_decide", None)):
                self._v6_47_a191_prev = getattr(self, "__dict__", {}).get("_a191_decide")
                self._a191_decide = self._v6_47_a191_decide
                self._v6_47_count("a191_bound")
        except Exception:
            self._v6_47_error()

    # ---- class 0: R11SAG on the deep layer ---------------------------------------------------------------------------

    def _v6_46_deep_book(self, response, book_id, deep, depth, raw_bid, raw_ask, inv, rows, deep_rows, already, req,
                         **kw) -> int:
        """S5SA on a class-0 book (not a foreign one): the R11SAG sides join ``held`` for the deep layer (level 0 and the
        ladder: a resting order cancelled, none placed -- the touch quote is not held), then V646Part's (S5AW, then
        Simple's _v633_book).  Never raises: an error counts and the call is V646Part's as is."""
        try:
            if self._v6_47_cfg()["s5sa"] and self._v6_45_class_of(book_id) == V6_47_HOME \
                    and int(book_id) not in (getattr(self, "_v6_23_foreign", None) or frozenset()):
                sides = self._v6_47_sa(book_id, raw_bid, raw_ask, inv, kw.get("cfg"))
                if sides:
                    kw["held"] = frozenset(kw.get("held") or ()) | sides
                    self._v6_47_count("sa_held_states")
                    for s_ in sorted(sides):
                        self._v6_47_count("sa_held_" + str(s_))
        except Exception:
            self._v6_47_error()
        return super()._v6_46_deep_book(response, book_id, deep, depth, raw_bid, raw_ask, inv, rows, deep_rows, already,
                                        req, **kw)

    def _v6_47_sa(self, book_id: int, raw_bid, raw_ask, inv, cfg) -> frozenset:
        """R11SAG's held sides of a class-0 book this request (research_v647_rules.sa_sides): v6.23 S1 active (the engine's
        k48 against side), this block's signed fp step from v6.36's StepRecord at the request's block, the against side
        at research_v623_against_gap_bps (= aggap) from the book's fundamental (v6.32 state fp, v6.23's reading) and the
        mid, the TRUE inventory = ``inv`` (inv - tau) + this request's tau (V646Part's store) for value 1."""
        st = self._v6_47_cfg()
        self._v6_47_count("sa_evals")
        if not bool(getattr(self, "_v6_23_active", False)):
            self._v6_47_count("sa_v623_inactive")
            return frozenset()
        req36 = getattr(self, "_v6_36_req", None) or {}
        step = v6_47_signed_step(getattr(self, "_v6_36_record", None), book_id, req36.get("block"))
        if step is None:
            self._v6_47_count("sa_no_step")
            return frozenset()
        gap = v6_47_gap_bps(getattr(self, "research_v623_against_gap_bps", 0.0))
        fp, _reason = self._v6_23_fundamental(book_id, raw_bid, raw_ask, cfg)
        if fp is None:
            self._v6_47_count("sa_no_fp")
            return frozenset()
        against = v6_47_against_side(0.5 * (float(raw_bid) + float(raw_ask)), fp, gap)
        if against is None:
            self._v6_47_count("sa_no_against")
            return frozenset()
        got = (getattr(self, "_v6_46_taus", None) or {}).get(int(book_id))
        tau = float(got[0]) if got is not None and int(got[1]) == self._v6_47_tick() else 0.0
        if v6_47_step_adverse(against, step, gap):
            self._v6_47_count("sa_adverse")
        return v6_47_sa_sides(against, step, gap, float(inv or 0.0) + tau, st["s5sa"])

    # ---- class 2: C2S2 in C2T ----------------------------------------------------------------------------------------

    def _v6_44_c2t_entry(self, response, state, book_id: int, deep, raw_bid, raw_ask, inv, other_rows, touch_rows,
                         already, req, *, paced: bool, held: frozenset, tick_size, min_order, budget: int, expiry: int,
                         cfg) -> int:
        """C2S2 on: _v6_47_c2t_core, then (``finally``, so also after an error past a placement) the replaced rows'
        cancel that follows the placements under option P.  Off: V646Part's."""
        if not self._v6_47_cfg()["c2s2"]:
            return super()._v6_44_c2t_entry(response, state, book_id, deep, raw_bid, raw_ask, inv, other_rows,
                                            touch_rows, already, req, paced=paced, held=held, tick_size=tick_size,
                                            min_order=min_order, budget=budget, expiry=expiry, cfg=cfg)
        self._v6_47_ctx = None
        try:
            return self._v6_47_c2t_core(response, state, book_id, deep, raw_bid, raw_ask, inv, other_rows, touch_rows,
                                        already, req, paced=paced, held=held, tick_size=tick_size, min_order=min_order,
                                        budget=budget, expiry=expiry, cfg=cfg)
        finally:
            self._v6_47_cancel_after(response, book_id, req, budget)

    def _v6_47_ctx_for(self, book_id: int) -> dict:
        """This request's C2S2 context of the book (made by _v6_47_rows); a missing one is the most conservative context
        (no replace: every cancelled side waits, as v6.46) and counts."""
        ctx = getattr(self, "_v6_47_ctx", None)
        if isinstance(ctx, dict) and ctx.get("book") == int(book_id) and ctx.get("tick") == self._v6_47_tick():
            return ctx
        self._v6_47_count("ctx_missing")
        ctx = {"book": int(book_id), "tick": self._v6_47_tick(), "mode": 0, "order_p": 0, "net_base": None,
               "dying_slots": {V63_SIDE_BUY: set(), V63_SIDE_SELL: set()}, "already_sides": set(), "replace": set(),
               "waits": set(), "after": [], "after_done": False}
        self._v6_47_ctx = ctx
        return ctx

    def _v6_47_net_base(self, book_id: int):
        """The book's TRUE position as A1.9's post-pass reads it (RestingInventoryView of the position tracker), or None
        with the A1.9 exit layer off or unreadable (no size clause then)."""
        if not (callable(getattr(self, "_a191_enabled", None)) and self._a191_enabled()):
            return None
        try:
            return float(V6_47RestingInventoryView.from_tracker(self._position_tracker_snapshot(int(book_id))).net_base)
        except Exception:
            self._v6_47_count("net_base_unreadable")
            return None

    def _v6_47_rows(self, book_id: int, touch_rows, already, doomed: list) -> list:
        """C2S2's split of the book's touch rows this request (research_v647_c2s2.split_rows): dying rows (C2T cancelled
        them the previous request; still live) are left out -- they hold their slot in _v69_live_slots' account view --,
        older second rows are doomed C2S2_EXTRA, rows whose cancel is two or more requests old C2S2_RETRY; the sides of
        rows another pass cancelled this response wait.  Makes the request's context; drops memo entries of orders no
        longer live.  Returns the rows C2T evaluates."""
        st = self._v6_47_cfg()
        b = int(book_id)
        now = self._v6_47_tick()
        memo = getattr(self, "_v6_47_sent", None)
        if memo is None:
            memo = {}
            self._v6_47_sent = memo
        sp = v6_47_split_rows(list(touch_rows or ()), sent=memo, book_id=b, now_tick=now, already=already or ())
        self._v6_47_ctx = {"book": b, "tick": now, "mode": int(st["c2s2"]), "order_p": int(st["c2s2p"]),
                           "net_base": self._v6_47_net_base(b), "dying_slots": sp["dying_slots"],
                           "already_sides": set(sp["already_sides"]), "replace": set(), "waits": set(), "after": [],
                           "after_done": False}
        for row in sp["extra"]:
            doomed.append((row, V6_47_CANCEL_EXTRA))
        for row in sp["retry"]:
            doomed.append((row, V6_47_CANCEL_RETRY))
        n_dying = sum(len(v) for v in sp["dying_slots"].values())
        if n_dying:
            self._v6_47_count("dying_rows", n_dying)
        if sp["already_sides"]:
            self._v6_47_count("other_pass_cancel_sides", len(sp["already_sides"]))
        for key in [k for k in memo if k[0] == b and k[1] not in sp["live"]]:
            memo.pop(key, None)
        return sp["evaluate"]

    def _v6_47_size_doomed(self, book_id: int, side, row) -> bool:
        """A1.9's size clause as C2T's own (the engine's a19x): a resting touch row on the side REDUCING the TRUE position
        with remaining + 1e-12 < 0.80 x |net|."""
        ctx = self._v6_47_ctx_for(book_id)
        nb = ctx.get("net_base")
        if nb is None or not v6_47_reducing(side, nb):
            return False
        hit = v6_47_size_shortfall(float(getattr(row, "remaining", None) or getattr(row, "quantity", None) or 0.0), nb)
        if hit:
            self._v6_47_count("size_doomed")
        return hit

    def _v6_47_note_sent(self, book_id: int, items) -> None:
        """The dying-row memo: C2T's cancel of these touch rows went out this request."""
        memo = getattr(self, "_v6_47_sent", None)
        if memo is None:
            memo = {}
            self._v6_47_sent = memo
        now = self._v6_47_tick()
        for row, _why in items or ():
            if v6_41_is_touch_cid(getattr(row, "client_id", None)):
                memo[(int(book_id), int(row.order_id))] = now

    def _v6_47_cancel_first(self, response, book_id: int, doomed: list, req, budget: int) -> set:
        """The book's cancels BEFORE the placements (research_v647_c2s2.cancel_plan): with option P every row of a side
        that is not a replace side (the engine's g1 / g3), else every doomed row (one cancel first).  Returns the sides
        that wait for the cancel to be seen: those cancelled here for another reason (or whose cancel the budget
        deferred), those another pass cancelled; replace sides with their cancel sent / to follow do not wait."""
        ctx = self._v6_47_ctx_for(book_id)
        plan = v6_47_cancel_plan(doomed, ctx["mode"], ctx["order_p"])
        first, after, replace = plan["first"], plan["after"], set(plan["replace"])
        sides_first = {V63_SIDE_BUY if int(row.side) == 0 else V63_SIDE_SELL for row, _why in first}
        sent = self._v617_cancel(response, book_id, first, req, budget) if first else set()
        emitted = bool(sent)
        if first and emitted:
            self._v6_47_note_sent(book_id, first)
        elif first:
            self._v6_47_count("first_cancel_deferred")
        waits = set(ctx["already_sides"])
        if ctx["order_p"]:
            waits |= sides_first
            ctx["after"] = list(after)
        else:
            waits |= sides_first - replace
            if first and not emitted:
                waits |= replace
                replace = set()
            ctx["after"] = []
        ctx["replace"] = replace - waits
        ctx["waits"] = waits
        if ctx["replace"]:
            self._v6_47_count("replace_sides", len(ctx["replace"]))
        return waits

    def _v6_47_slot(self, book_id: int, side):
        """The touch slot (9 = A, 8 = B) a new quote on ``side`` takes this request, or None (research_v647_c2s2
        .slot_decision on _v69_live_slots' acked + pending view; the reason counts)."""
        ctx = self._v6_47_ctx_for(book_id)
        held = (self._v69_live_slots(book_id) or {}).get(side) or frozenset()
        slot, why = v6_47_slot_decision(held, ctx["dying_slots"].get(side, ()), wait=side in ctx["waits"],
                                        replace=side in ctx["replace"], cancel_sent=True)
        self._v6_47_count(why)
        if slot is None and why == "slot_in_flight":
            self._v6_44_count("slot_in_flight")
        return slot

    def _v6_47_reserve(self, book_id: int) -> int:
        """1 while the replaced rows' cancel is still to follow the placements (option P): a placement leaves room for it."""
        ctx = self._v6_47_ctx_for(book_id)
        return 1 if (ctx.get("after") and not ctx.get("after_done")) else 0

    def _v6_47_placed(self, book_id: int, side, slot) -> None:
        ctx = self._v6_47_ctx_for(book_id)
        self._v6_47_count("placed_slot_" + ("b" if slot == V617_TOUCH_SLOT_B else "a"))
        if side in ctx["replace"]:
            self._v6_47_count("replace_placed")
            if ctx["order_p"]:
                self._v6_47_count("replace_placed_before_cancel")

    def _v6_47_cancel_after(self, response, book_id: int, req, budget: int) -> None:
        """Option P: the replaced rows' cancel, after the book's placements (the engine's g5), once.  Never raises."""
        try:
            ctx = getattr(self, "_v6_47_ctx", None)
            if not (isinstance(ctx, dict) and ctx.get("book") == int(book_id) and ctx.get("tick") == self._v6_47_tick()):
                return
            if not ctx.get("after") or ctx.get("after_done"):
                return
            ctx["after_done"] = True
            sent = self._v617_cancel(response, book_id, ctx["after"], req, budget)
            if sent:
                self._v6_47_note_sent(book_id, ctx["after"])
                self._v6_47_count("after_cancels")
            else:
                self._v6_47_count("after_cancel_deferred")
        except Exception:
            self._v6_47_error()

    def _v6_44_view(self, book_id: int):
        """C2S2 on: _v6_47_view (slot B's ids left out too); off: V644Part's."""
        if not self._v6_47_cfg()["c2s2"]:
            return super()._v6_44_view(book_id)
        return self._v6_47_view(book_id)

    def _v6_41_c2q_exit(self, response, book_id: int, rows, already, req, *, budget: int, raw_bid, raw_ask, inv, clip,
                        expiry) -> int:
        """C2S2 on: _v6_47_c2q_exit (B2b's exit waits while either touch slot is held); off: V641Part's."""
        if not self._v6_47_cfg()["c2s2"]:
            return super()._v6_41_c2q_exit(response, book_id, rows, already, req, budget=budget, raw_bid=raw_bid,
                                           raw_ask=raw_ask, inv=inv, clip=clip, expiry=expiry)
        return self._v6_47_c2q_exit(response, book_id, rows, already, req, budget=budget, raw_bid=raw_bid,
                                    raw_ask=raw_ask, inv=inv, clip=clip, expiry=expiry)

    def _v6_47_a191_decide(self, state, book_id: int, inventory, qty: float, desired_price, desired_action):
        """Bound as _a191_decide with C2S2 on: the verdict of the method bound before (V646Part's, else Simple's), except
        that a REPRICE on a C2T class-2 book (_v6_44_c2t_on) is HOLD -- C2T owns every order there (its touch quotes by
        its own rules, the A1.9 size clause included; other layers' orders by CANCEL_OTHER), so A1.9's post-pass never
        cancels one and its A1.9.1.2 book-side release never runs on the book.  The cached verdict is updated in place.
        Never raises: the verdict as it came."""
        prev = getattr(self, "_v6_47_a191_prev", None)
        if prev is not None:
            verdict = prev(state, book_id, inventory, qty, desired_price, desired_action)
        else:
            verdict = type(self)._a191_decide(self, state, book_id, inventory, qty, desired_price, desired_action)
        try:
            if not isinstance(verdict, dict) or verdict.get("decision") != V6_47_EXIT_REPRICE or verdict.get("v6_47"):
                return verdict
            if not self._v6_47_cfg()["c2s2"] or not self._v6_44_c2t_on(int(book_id)):
                return verdict
            verdict["v6_47"] = V6_47_HOLD_TAG
            verdict["v6_47_reason"] = verdict.get("reason")
            verdict["decision"] = V6_47_EXIT_HOLD
            verdict["reason"] = V6_47_QUEUE_PRESERVED
            self._a191_reprice_candidates = int(getattr(self, "_a191_reprice_candidates", 0) or 0) - 1
            self._v6_47_count("a191_reprice_held")
        except Exception:
            self._v6_47_error()
        return verdict

    # ---- the state row ---------------------------------------------------------------------------------------------

    def _v6_47_snapshot(self) -> dict:
        return {"version": V647_RULES_VERSION, "c2s2_version": V647_C2S2_VERSION, "switches": dict(self._v6_47_cfg()),
                "slots": {"a": int(V617_TOUCH_SLOT), "b": int(V617_TOUCH_SLOT_B)},
                "memo": len(getattr(self, "_v6_47_sent", None) or {}),
                "bound": sorted(k for k in ("_v633_book", "_a191_decide") if k in getattr(self, "__dict__", {})),
                "counts": dict(getattr(self, "_v6_47_counts", None) or {}),
                "errors": int(getattr(self, "_v6_47_errors", 0) or 0)}

    def _v6_40_snapshot(self) -> dict:
        """V646Part's snapshot plus this part's under "v6_47" (the V6_40_STATE row).  Never raises past V646Part's."""
        out = super()._v6_40_snapshot()
        try:
            out["v6_47"] = self._v6_47_snapshot()
        except Exception:
            self._v6_47_error()
        return out

    # ---- GENERATED (SP/v647/gen_v647.py; the parity test re-runs the transforms on this tree and compares ASTs) --------

    def _v6_47_c2t_core(self, response, state, book_id: int, deep, raw_bid, raw_ask, inv, other_rows, touch_rows, already, req, *, paced: bool, held: frozenset, tick_size, min_order, budget: int, expiry: int, cfg) -> int:
        """v6.47 GENERATED (SP/v647/gen_v647.py) from V646Part._v6_46_c2t_core: C2T on one class-2 book with C2S2 (research_v647_c2s2), every statement V646Part's except: the touch rows split first (_v6_47_rows: dying rows hold their slot, extra / retry rows doomed), A1.9's size clause as C2T's own replace kind (C2S2_SIZE), the non-replace cancels first (_v6_47_cancel_first), the slot rule (_v6_47_slot) in place of v6.17's one-slot test, one instruction kept for the replaced rows' cancel (_v6_47_reserve), the slot's own client ids, and the counters (_v6_47_placed).  The replaced rows' cancel follows the placements under option P (V647Part._v6_44_c2t_entry)."""
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
        touch_rows = self._v6_47_rows(book_id, touch_rows, already, doomed)
        for row in touch_rows or ():
            if int(row.order_id) in already:
                continue
            side = V63_SIDE_BUY if int(row.side) == 0 else V63_SIDE_SELL
            if self._v6_47_size_doomed(book_id, side, row):
                doomed.append((row, V6_47_CANCEL_SIZE))
                continue
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
        cancelled = self._v6_47_cancel_first(response, book_id, doomed, req, budget)
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
            slot = self._v6_47_slot(book_id, side)
            if slot is None:
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
            if self._count_book_instructions(response, book_id) >= budget - self._v6_47_reserve(book_id):
                self._v6_44_count('budget_deferred')
                continue
            (buy_cid, sell_cid) = v617_client_ids(book_id, slot)
            response.limit_order(book_id=book_id, direction=OrderDirection.BUY if side == V63_SIDE_BUY else OrderDirection.SELL, quantity=q, price=float(price), clientOrderId=buy_cid if side == V63_SIDE_BUY else sell_cid, stp=STP.CANCEL_BOTH, postOnly=True, timeInForce=TimeInForce.GTT, expiryPeriod=expiry, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
            placed += 1
            self._v6_47_placed(book_id, side, slot)
            req['placed_touch'] = int(req.get('placed_touch', 0) or 0) + 1
            self._v6_44_count('placed_' + str(side))
        return placed

    def _v6_47_c2q_exit(self, response, book_id: int, rows, already, req, *, budget: int, raw_bid, raw_ask, inv, clip, expiry) -> int:
        """v6.47 GENERATED (SP/v647/gen_v647.py) from V641Part._v6_41_c2q_exit: B2b's disarmed class-2 book, every statement V641Part's except its slot test -- the exit waits while EITHER touch slot (A or B) is held (v6_47_touch_held)."""
        try:
            g = (getattr(self, '_v6_40_grids', None) or {}).get(int(book_id))
            if g is None:
                return self._v6_40_c2q_cancel(response, book_id, rows, already, req, budget=budget)
            self._v6_40_count('c2q_off_book_states')
            pos = float(inv) * float(g.scale)
            want = v6_41_reduce_exit(pos, float(clip) * float(g.scale), g.min_order, g.vol_dec, raw_bid, raw_ask)
            (doomed, kept, others) = ([], 0, set())
            for r in rows:
                oid = int(r.order_id)
                if oid in already:
                    continue
                side = V6_41_BUY if int(r.side) == 0 else V6_41_SELL
                qty = getattr(r, 'remaining', None) or getattr(r, 'quantity', None)
                if want is not None and (not kept) and v6_41_is_touch_cid(getattr(r, 'client_id', None)) and v6_41_keeps_exit(side, r.price, qty, want, pos, g.tick):
                    kept += 1
                    continue
                doomed.append(oid)
                others.add(side)
            if doomed and self._count_book_instructions(response, book_id) < budget:
                response.cancel_orders(book_id=book_id, order_ids=sorted(doomed), delay=0)
                req['cancels'] = int(req.get('cancels', 0) or 0) + 1
                self._v6_40_count('c2q_cancelled', len(doomed))
                try:
                    self._a19_note_exit_cancel(book_id, sorted(doomed), ABSENT_REPRICE_CANCEL)
                except Exception:
                    pass
            if kept:
                self._v6_41_count('c2q_exit_kept')
                return 0
            if want is None:
                self._v6_41_count('c2q_exit_none')
                return 0
            (side, q, price) = want
            if side in others:
                self._v6_41_count('c2q_exit_wait_side')
                return 0
            if v6_47_touch_held(self._v69_live_slots(book_id), side):
                self._v6_41_count('c2q_exit_slot_in_flight')
                return 0
            if self._count_book_instructions(response, book_id) >= budget:
                self._v6_41_count('c2q_exit_budget')
                return 0
            (buy_cid, sell_cid) = v6_41_touch_ids(book_id)
            response.limit_order(book_id=book_id, direction=OrderDirection.BUY if side == V6_41_BUY else OrderDirection.SELL, quantity=float(q), price=round(float(price), int(g.dec)), clientOrderId=buy_cid if side == V6_41_BUY else sell_cid, stp=STP.CANCEL_BOTH, postOnly=True, timeInForce=TimeInForce.GTT, expiryPeriod=expiry, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
            self._v6_41_count('c2q_exit_placed_' + side)
            return 1
        except Exception:
            self._v6_41_errors = int(getattr(self, '_v6_41_errors', 0) or 0) + 1
            return self._v6_40_c2q_cancel(response, book_id, rows, already, req, budget=budget)

    def _v6_47_view(self, book_id: int):
        """v6.47 GENERATED (SP/v647/gen_v647.py) from V644Part._v6_44_view: C2T's touch view, every statement V644Part's except that slot B's client ids are left out too."""
        try:
            noted = getattr(self, '_v6_31_noted', None)
            if noted is None or noted[0] != int(book_id) or noted[1] != int(getattr(self, '_tick', 0) or 0):
                return None
            own_cids = v63_own_client_ids(book_id) | set(v62_entry_client_ids(book_id)) | set(v6214_exit_client_ids(book_id)) | v633_own_client_ids(book_id) | (set(v617_client_ids(book_id)) | set(v617_client_ids(book_id, V617_TOUCH_SLOT_B)))
            return v6210_touch_view(noted[2], own_ids=noted[3], own_cids=own_cids)
        except Exception:
            self._v6_44_errors = int(getattr(self, '_v6_44_errors', 0) or 0) + 1
            return None
