"""v6.42 (scratchpad k150): k149's L1T rule as a live port -- research_v642_l1t in {0, 1, 2, 3} -- and k151's bound of its
carried size -- research_v642_l1c in {0, 1, 3, 4} (only with l1t 1 / 2; 4 with l1t 2) --, code and PARAMS default 0 (off: v6.41 on every path).  The rule is research_v6_42_l1t's (pure); this mixin, first among Simple's bases, carries the
agent side.  Simple carries the hooks (tests/_v642_undo.py lists them; their reverse is v6.41's text byte for byte):

1. the v6.3 pass, per book right after v6.25 S5's lean: _v6_42_note keeps the book's TRUE position and this request's
   lean tau (v6.25's own record of it) and whether the book is class 0 (no class grid, not foreign, the home class of
   research_v6_40_classes) -- the deep layer is handed v6.25's inv - tau, the gate reads the true position and tau;
2. the v6.9 deep-ladder placement (_v633_book, where each side's level-0 and ladder sizes are set):
   a. before the resting orders are judged: _v6_42_fold -- per side the L1 size a NEW level-0 order carries while the
      gate is open (0.0: closed; vm749 _l1f_q; l1c: within the gap to tau, vm751) and whether our live level 0 there
      carries it (the tag; vm749 o.l1f);
   b. after they are judged: _v6_42_doom -- a carrying level 0 the gate no longer opens is cancelled (re-placed at its
      own size by the ladder rule: vm749 l1t_l0_unfold), one larger than q0 + the l1c bound is re-placed (vm751
      l1c_l0_resize), and while one rests a resting L1 is cancelled (vm749 l1f_l1);
   c. after the cancels went out: a cancelled level 0 carries nothing any more;
   d. in the placement loop: L1 is not placed while our level 0 carries its size; a new level 0 takes q0 + q1 (before
      v6.9.1's free-balance sizing, which still bounds the order); after it is placed the tag follows it and a resting
      L1 is cancelled in the same response (vm749 cancels it in the same state).

Level 0 = v6.3.3's deep order (client ids 40000 + 10 x book + 1 / 2); L1 = v6.9's p99 level (+ 3 / 4, one deep clip) =
vm749 LADDER[0]; L2 (the max level, + 5 / 6) is not touched.  Unchanged: tau, D4, A_r1 + D, the touch quote, S12b, the
depths and prices, the bound, pacing, every class-1 / class-2 book.  No taking.  STRUCTURAL."""
from __future__ import annotations

from research_direct_exit_refresh import ABSENT_REPRICE_CANCEL as V6_42_REPRICE_CANCEL
from research_v62_breadth import V62_STATE_EVERY_TICKS
from research_v6_40_classes import HOME_CLASS as V6_42_HOME, STATUS_OK as V6_42_STATUS_OK
from research_v6_40_classes import STATUS_SINGLE as V6_42_STATUS_SINGLE
from research_v633_deep_layer import deep_level as v6_42_deep_level
from research_v69_deep_ladder import slot_taken as v6_42_slot_taken
from research_v6_42_l1t import (
    CANCEL_L1_HELD as V6_42_CANCEL_L1_HELD,
    CANCEL_RESIZE as V6_42_CANCEL_RESIZE,
    CANCEL_UNFOLD as V6_42_CANCEL_UNFOLD,
    FOLD_LEVEL as V6_42_FOLD_LEVEL,
    L1C_GATES as V6_42_L1C_GATES,
    L1C_NAMES as V6_42_L1C_NAMES,
    L1C_STEP_GATES as V6_42_L1C_STEP_GATES,
    MODE_NAMES as V6_42_MODE_NAMES,
    SIDES as V6_42_SIDES,
    V6_42_L1T_VERSION,
    against_step as v6_42_against_step,
    bounded as v6_42_bounded,
    bounded_fold as v6_42_bounded_fold,
    parse_l1c as v6_42_parse_l1c,
    resizes as v6_42_resizes,
    toward as v6_42_toward,
    carried_quantity as v6_42_carried_quantity,
    fold_quantity as v6_42_fold_quantity,
    gate as v6_42_gate,
    level0_quantity as v6_42_level0_quantity,
    parse_mode as v6_42_parse_mode,
    step_sign as v6_42_step_sign,
    unfolds as v6_42_unfolds,
)


class V642FoldPlan:
    """One book's L1T plan for one request: per side the size a new level 0 carries (q1; 0.0 = closed) and whether our
    live level 0 carries it (carry, the tag)."""

    __slots__ = ("book", "q0", "q1", "carry", "carried_now", "vd", "l0_doomed", "bounded", "refold")

    def __init__(self, book, vd=4):
        self.book = int(book)
        self.q0 = 0.0
        self.q1 = {s: 0.0 for s in V6_42_SIDES}
        self.carry = {s: False for s in V6_42_SIDES}
        self.carried_now = {s: False for s in V6_42_SIDES}
        self.l0_doomed = {s: False for s in V6_42_SIDES}
        self.bounded = {s: False for s in V6_42_SIDES}
        self.refold = {s: False for s in V6_42_SIDES}
        self.vd = vd

    def holds_l1(self, side) -> bool:
        """L1 rests nowhere on this side while our level 0 carries its size -- or while a carrying level 0 re-placed for
        the l1c bound waits for its new order (vm751 re-places it in the same state: L1 stays held)."""
        return bool(self.carry.get(side, False) or self.refold.get(side, False))

    def carried(self, side, q):
        """A new level 0's size: q + L1's while the gate is open (q1 > 0), else q."""
        q1 = float(self.q1.get(side, 0.0) or 0.0)
        if q1 > 0.0:
            self.carried_now[side] = True
            return v6_42_carried_quantity(q, q1, self.vd)
        self.carried_now[side] = False
        return q


class V642Part:
    """v6.42 (research_v642_l1t): level 0 carries L1's size on a class-0 book side while the L1T gate is open"""

    # ---- switch and state (init) ------------------------------------------------------------------------------------

    def _v6_41_init(self) -> None:
        """v6.41's init (unchanged), then v6.42's."""
        super()._v6_41_init()
        self._v6_42_init()

    def _v6_42_init(self) -> None:
        """v6.42: research_v642_l1t (code default 0 = v6.41) and the empty state.  An unreadable value runs off and counts
        an error (the launcher refuses such a build).  Never raises."""
        self._v6_42_counts: dict[str, float] = {}
        self._v6_42_errors = 0
        self._v6_42_reported = False
        self._v6_42_books: dict[int, tuple] = {}      # book -> (true inventory, tau, class 0) noted this request
        self._v6_42_carry: dict[tuple, bool] = {}     # (book, side) -> our live level 0 there carries L1's size
        self._v6_42_seen: set = set()                 # (book, side) the layer has judged since this process started
        self._v6_42_refold: dict[tuple, bool] = {}    # (book, side) -> a carrying level 0 re-placed for the l1c bound
        self.research_v642_l1t = 0
        self.research_v642_l1c = 0
        try:
            mode = v6_42_parse_mode(getattr(getattr(self, "config", None), "research_v642_l1t", 0))
            if mode is None:
                self._v6_42_errors += 1
                mode = 0
            self.research_v642_l1t = int(mode)
        except Exception:
            self._v6_42_errors += 1
            self.research_v642_l1t = 0
        try:
            l1c = v6_42_parse_l1c(getattr(getattr(self, "config", None), "research_v642_l1c", 0))
            if l1c is None or (l1c and self.research_v642_l1t not in V6_42_L1C_STEP_GATES.get(l1c, V6_42_L1C_GATES)):
                self._v6_42_errors += 1                   # unreadable, l1c without l1t 1 / 2, AG-CAP without l1t 2: off
                l1c = 0
            self.research_v642_l1c = int(l1c)
        except Exception:
            self._v6_42_errors += 1
            self.research_v642_l1c = 0

    def _v6_42_count(self, key: str, n: float = 1) -> None:
        try:
            counts = getattr(self, "_v6_42_counts", None)
            if counts is None:
                counts = self._v6_42_counts = {}
            counts[key] = counts.get(key, 0) + n
        except Exception:
            self._v6_42_errors = int(getattr(self, "_v6_42_errors", 0) or 0) + 1

    def _v6_42_home(self, book_id) -> bool:
        """A home-class (class 0) book of v6.40's class map; without a class list (one market) every book the caller
        passes (not foreign, no class grid) is; an unreadable class list: none (fail closed -- v6.41)."""
        table = getattr(self, "_v6_40_table", None)
        if not table or table.get("status") == V6_42_STATUS_SINGLE:
            return True
        if table.get("status") != V6_42_STATUS_OK:
            return False
        return (table.get("book_class") or {}).get(int(book_id)) == V6_42_HOME

    # ---- hook 1: the v6.3 pass --------------------------------------------------------------------------------------

    def _v6_42_note(self, book_id, now_ts, inv, eligible) -> None:
        """Hook 1 (the v6.3 pass, per book right after v6.25 S5's lean): this book's TRUE position, this request's lean
        tau (v6.25's own record for this state time; 0 without one) and whether it is a class-0 book.  Never raises: an
        error counts and the book's gate stays closed this request."""
        b = None
        try:
            b = int(book_id)
            tau = 0.0
            last = (getattr(self, "_v6_25_taus", None) or {}).get(b)
            if last is not None and int(last[1]) == int(now_ts):
                tau = float(last[0])
            self._v6_42_books[b] = (float(inv), tau, bool(eligible) and self._v6_42_home(b))
        except Exception:
            self._v6_42_errors = int(getattr(self, "_v6_42_errors", 0) or 0) + 1
            if b is not None:
                getattr(self, "_v6_42_books", {}).pop(b, None)

    # ---- hook 2: the deep ladder's placement ------------------------------------------------------------------------

    def _v6_42_fold(self, book_id, inv, vac, levels_side, clip, size_scale, min_order, cfg) -> V642FoldPlan:
        """Hook 2a (_v633_book, before its resting orders are judged): the book's plan.  ``inv`` is what the layer reads
        (v6.25's inv - tau); the gate reads the noted TRUE position and tau (a note that does not give back ``inv``
        closes the gate).  Never raises: an error counts and the plan is closed on both sides (a carrying level 0 is
        then re-placed at its own size: v6.41's ladder)."""
        b = int(book_id)
        vd = getattr(cfg, "volumeDecimals", 4)
        plan = V642FoldPlan(b, vd)
        try:
            plan.q0 = v6_42_level0_quantity(clip, size_scale, min_order, vd)
            carry = getattr(self, "_v6_42_carry", None)
            if carry is None:
                carry = self._v6_42_carry = {}
            if any(carry.get((b, s)) for s in V6_42_SIDES):
                slots = self._v69_live_slots(b)
                for s in V6_42_SIDES:
                    if carry.get((b, s)) and not v6_42_slot_taken(slots, s, 0):
                        carry.pop((b, s), None)                # our carrying level 0 left the book (filled / expired)
                        self._v6_42_count("carry_gone_side_states")
                plan.carry = {s: bool(carry.get((b, s))) for s in V6_42_SIDES}
            refold = getattr(self, "_v6_42_refold", None)
            if refold is None:
                refold = self._v6_42_refold = {}
            mode = int(getattr(self, "research_v642_l1t", 0) or 0)
            l1c = int(getattr(self, "research_v642_l1c", 0) or 0)
            note = (getattr(self, "_v6_42_books", None) or {}).pop(b, None)
            if not mode or note is None:
                for s in V6_42_SIDES:
                    refold.pop((b, s), None)
                return plan
            true_inv, tau, home = note
            if not home:
                self._v6_42_count("not_class0_book_states")
                return plan
            if vac is not None or tau == 0.0:
                self._v6_42_count("vacuum_book_states" if vac is not None else "no_lean_book_states")
                return plan
            if abs(float(inv) - (true_inv - tau)) > 1e-9:
                self._v6_42_count("lean_mismatch_book_states")   # not this request's note: closed
                return plan
            step = 0
            record = getattr(self, "_v6_36_record", None)
            if record is not None:
                # the sign of this block's fp step on the book (v6.36's record; 0 unless its step block is this request's)
                block = (getattr(self, "_v6_36_req", None) or {}).get("block")
                step = v6_42_step_sign(record.direction, record.step_block, block, b)
            q0 = plan.q0
            for s in V6_42_SIDES:
                self._v6_42_count("gate_eval_side_states")
                if not v6_42_gate(mode, s, true_inv, tau, step):
                    continue
                self._v6_42_count("gate_open_side_states")
                q1 = v6_42_fold_quantity((levels_side or {}).get(s), clip, size_scale, min_order, vd)
                if l1c and q1 > 0.0:
                    # research_v642_l1c: the carried size within the gap to tau, on the TRUE position (vm751 l1c)
                    tw = v6_42_toward(s, tau)
                    ag = v6_42_against_step(s, step)            # AG-CAP: the toward side trades against the block's step
                    if v6_42_bounded(l1c, tw, ag):
                        plan.bounded[s] = True
                        self._v6_42_count("l1c_eval_side_states")
                        q1b = v6_42_bounded_fold(l1c, q0, q1, abs(true_inv - tau), tw, min_order, vd, ag)
                        if q1b < q1 - 1e-12:
                            self._v6_42_count("l1c_bound_side_states")
                        if q1b <= 0.0:
                            self._v6_42_count("l1c_no_fold_side_states")
                        q1 = q1b
                if q1 > 0.0:
                    plan.q1[s] = q1
                    self._v6_42_count("fold_side_states")
                else:
                    self._v6_42_count("open_no_l1_side_states")
            for s in V6_42_SIDES:
                if refold.get((b, s)) and plan.q1.get(s, 0.0) > 0.0:
                    plan.refold[s] = True                       # the bound's re-place is due: L1 stays held meanwhile
                else:
                    refold.pop((b, s), None)
        except Exception:
            self._v6_42_errors = int(getattr(self, "_v6_42_errors", 0) or 0) + 1
            plan.q1 = {s: 0.0 for s in V6_42_SIDES}
            plan.refold = {s: False for s in V6_42_SIDES}
        return plan

    def _v6_42_doom(self, book_id, plan, doomed, resting, deep_rows, already) -> None:
        """Hook 2b (_v633_book, after its resting orders are judged; edits ``doomed`` / ``resting`` in place): a resting
        carrying level 0 with nothing to carry now is cancelled (vm749 l1t_l0_unfold) -- the ladder rule re-places
        level 0 at its own size and L1 returns; while it keeps resting (or is still in flight) a resting L1 is
        cancelled (vm749 l1f_l1).  A carrying level 0 cancelled for another reason this request (by the layer, or
        earlier in this response: ``already``) holds no L1.  At the first look at a side since the process started (a
        restart: the tags are not in the session), a resting level 0 placed larger than this request's level-0 size is
        taken as carrying.  Never raises: an error counts and nothing more is cancelled."""
        try:
            b = int(book_id)
            seen = getattr(self, "_v6_42_seen", None)
            if seen is None:
                seen = self._v6_42_seen = set()
            for s in V6_42_SIDES:
                if (b, s) in seen:
                    continue
                seen.add((b, s))
                row0 = resting.get((s, 0))
                if (not plan.carry.get(s) and row0 is not None and plan.q0 > 0.0
                        and float(getattr(row0, "quantity", 0.0) or 0.0) > plan.q0 + 1e-9):
                    # the first look at this side since the process started (a restart): a resting level 0 placed larger
                    # than this request's level 0 carries L1's size -- its tag was lost with the old process
                    self._v6_42_carry[(b, s)] = True
                    plan.carry[s] = True
                    self._v6_42_count("carry_inferred_at_start")
            for s in V6_42_SIDES:
                if not plan.carry.get(s):
                    continue
                if any(int(r.order_id) in already and self._v6_42_side_of(r) == s
                       and v6_42_deep_level(getattr(r, "client_id", None)) == 0 for r in deep_rows):
                    self._v6_42_carry.pop((b, s), None)          # cancelled earlier in this response: the tag goes
                    plan.carry[s] = False
                    self._v6_42_count("carry_cancelled")
                    continue
                row0 = resting.get((s, 0))
                if row0 is not None:
                    if v6_42_unfolds(True, plan.q1.get(s, 0.0)):
                        resting.pop((s, 0), None)
                        doomed.append((row0, V6_42_CANCEL_UNFOLD))
                        plan.l0_doomed[s] = True
                        self._v6_42_count("unfold_cancels")
                        continue
                    rem = getattr(row0, "remaining", None)
                    if plan.bounded.get(s) and v6_42_resizes(getattr(row0, "quantity", None) if rem is None else rem,
                                                             plan.q0, plan.q1.get(s, 0.0)):
                        # research_v642_l1c: larger than q0 + the current bound -- re-placed (vm751 l1c_l0_resize); L1
                        # stays held (vm751 places the new carrying level 0 in the same state)
                        resting.pop((s, 0), None)
                        doomed.append((row0, V6_42_CANCEL_RESIZE))
                        plan.l0_doomed[s] = True
                        plan.refold[s] = True
                        self._v6_42_refold[(b, s)] = True
                        self._v6_42_count("l1c_resize_cancels")
                elif any(self._v6_42_side_of(r) == s and v6_42_deep_level(getattr(r, "client_id", None)) == 0
                         for r, _why in doomed):
                    plan.l0_doomed[s] = True                    # cancelled for another reason: no L1 held by it
                    continue
                row1 = resting.pop((s, V6_42_FOLD_LEVEL), None)
                if row1 is not None:
                    doomed.append((row1, V6_42_CANCEL_L1_HELD))
                    self._v6_42_count("l1_held_cancels")
        except Exception:
            self._v6_42_errors = int(getattr(self, "_v6_42_errors", 0) or 0) + 1

    @staticmethod
    def _v6_42_side_of(row) -> str:
        """A deep row's side token (the venue's 0 = buy, else sell -- v6.3.3's reading)."""
        return V6_42_SIDES[0] if int(row.side) == 0 else V6_42_SIDES[1]

    def _v6_42_cancelled(self, book_id, plan, cancelled) -> None:
        """Hook 2c (_v633_book, after its cancels went out): a level 0 cancelled this request carries nothing any more
        (its tag goes; L1 may be placed again).  Never raises."""
        try:
            b = int(book_id)
            for s in V6_42_SIDES:
                if (s, 0) in cancelled:
                    if self._v6_42_carry.pop((b, s), None):
                        self._v6_42_count("carry_cancelled")
                    plan.carry[s] = False
        except Exception:
            self._v6_42_errors = int(getattr(self, "_v6_42_errors", 0) or 0) + 1

    def _v6_42_placed(self, response, book_id, side, plan, resting, budget, req) -> None:
        """Hook 2d (_v633_book, right after a level-0 order was placed): the tag follows the new order (it carries L1's
        size or not), and a resting L1 is cancelled with a carrying one in the same response while the book's
        instruction budget allows (else on the next request, by hook 2b).  Never raises."""
        try:
            b = int(book_id)
            refold = getattr(self, "_v6_42_refold", None)
            if refold is not None:
                refold.pop((b, side), None)                     # a new level 0: any re-place is done
            plan.refold[side] = False
            if not plan.carried_now.get(side):
                self._v6_42_carry.pop((b, side), None)
                plan.carry[side] = False
                return
            self._v6_42_carry[(b, side)] = True
            plan.carry[side] = True
            self._v6_42_count("carried_l0_placed")
            self._v6_42_count("carried_q", float(plan.q1.get(side, 0.0) or 0.0))
            row1 = resting.get((side, V6_42_FOLD_LEVEL))
            if row1 is None:
                return
            if self._count_book_instructions(response, b) >= budget:
                self._v6_42_count("l1_cancel_deferred")
                return
            ids = [int(row1.order_id)]
            response.cancel_orders(book_id=b, order_ids=ids, delay=0)
            resting.pop((side, V6_42_FOLD_LEVEL), None)
            req["cancels"] += 1
            self._v6_42_count("l1_cancels_with_fold")
            try:
                self._a19_note_exit_cancel(b, ids, V6_42_REPRICE_CANCEL)
            except Exception:
                pass
        except Exception:
            self._v6_42_errors = int(getattr(self, "_v6_42_errors", 0) or 0) + 1

    # ---- telemetry ------------------------------------------------------------------------------------------------------

    def _v6_40_request(self, state) -> None:
        """v6.41's request (v6.40's and the V6_41_STATE row, unchanged), then the V6_42_STATE row."""
        super()._v6_40_request(state)
        self._v6_42_report()

    def _v6_42_report(self) -> None:
        """v6.42: the V6_42_STATE row -- at the first request (the start row: the mode), at the first one with a new error
        and every V62_STATE_EVERY_TICKS requests; after the reply when the request defers its telemetry.  Never raises."""
        try:
            tick = int(getattr(self, "_tick", 0) or 0)
            errors = int(getattr(self, "_v6_42_errors", 0) or 0)
            erring = errors > int(getattr(self, "_v6_42_errors_seen", 0) or 0)
            self._v6_42_errors_seen = errors
            if not getattr(self, "_v6_42_reported", False):
                reason = "start"
            elif erring:
                reason = "error"
            elif tick > 0 and tick % V62_STATE_EVERY_TICKS == 0:
                reason = "periodic"
            else:
                return
            self._v6_42_reported = True
            mode = int(getattr(self, "research_v642_l1t", 0) or 0)
            l1c = int(getattr(self, "research_v642_l1c", 0) or 0)
            carry = getattr(self, "_v6_42_carry", None) or {}
            row = {"version": V6_42_L1T_VERSION, "reason": reason, "tick": tick, "l1t": mode,
                   "l1t_name": V6_42_MODE_NAMES.get(mode, "?"), "l1c": l1c, "l1c_name": V6_42_L1C_NAMES.get(l1c, "?"),
                   "carrying_sides": sum(1 for v in carry.values() if v),
                   "counts": {k: (round(v, 4) if isinstance(v, float) else v)
                              for k, v in (getattr(self, "_v6_42_counts", None) or {}).items()},
                   "errors": errors}
            work = getattr(self, "_v629_request_work", None)
            if work is not None:
                work.add("v6_42_state_row", lambda: self._emit("V6_42_STATE", force=True, **row))
            else:
                self._emit("V6_42_STATE", force=True, **row)
        except Exception:
            self._v6_42_errors = int(getattr(self, "_v6_42_errors", 0) or 0) + 1
