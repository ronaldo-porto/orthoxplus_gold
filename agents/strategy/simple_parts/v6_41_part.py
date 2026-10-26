"""v6.41 the final SN79 0.6.5 build (scratchpad k144): per-class units of the legacy layers (B2), C2Q's inventory (B2b),
per-class statistic pools (B7) and the post-reply session save of the speed work (B1) -- research_v641_* (rules in
research_v6_41_units; B1's in research_v6_41_speed).

A mixin, first among Simple's bases: every v6.41 method lives here; Simple and the older parts carry only the hooks that
call them (tests/_v641_undo.py lists those edits; their reverse is v6.40.1's text byte for byte).

B2  research_v641_unit_safety (STRUCTURAL): the layers v6.40 left in the top level's units run in each traded non-home
    class's own units -- the contract-reject guard's reprice (the class's venue tick / decimals), dust parking (the
    class's minimum), the dust compactor and normalizer (each class separately, in its units: minimum, decimals, its own
    "one book per request"), the partial-fill recovery's dust test (the class's minimum), v6.2.14's touch life and
    A1.9.1's exit reprice (the class's tick) and the frozen maker exit (the class's decimals / minimum).  A home-class
    book, or any book with the switch off or no class traded: the v6.40.1 call itself.  v6.40's final validator stays the
    choke point behind all of them.
B2b research_v641_c2q_reduce (STRUCTURAL; live finding 10-24): C2Q disarmed = stop making, not freeze inventory -- every
    order of ours on the class-2 book is cancelled except ONE reduce-only passive exit (reduce_exit: the reducing side,
    one clip and never more than the position, joining that side's best price, post-only, the class's grid and minimum)
    placed once no other order of ours rests on that side; the dust normalizer (which adds) skips class 2 while
    disarmed.  During a C2Q probe the adding side of a class-2 book carrying a clip or more holds (probe_held).
    research_v641_c2q_persist: C2Q's window (the K3Q sums and their class split) and its last block verdict with the
    making betas it used are saved in the session (keyed on the simulation id) and put back after a relaunch inside
    the same simulation (in-memory only before: every relaunch was a probe); a new simulation id starts fresh.
B7  research_v641_s9_page (STRUCTURAL, rule parity): v6.29 S9's page history observes every request's page once, before
    the books -- vm739's S9 reference is the newest page at or before t - W on EVERY state (fmg / fmh: FP_H from the
    whole page tape), where the agent recorded a page only on requests with a touch decision.
(v6.42 candidates, not built here -- coordinator 21:1x JST 10-24: per-class v6.6.1 / v6.36 pools; S14's estimate per
class.)"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager

from research_direct_exit_refresh import ABSENT_REPRICE_CANCEL
from research_v62_breadth import V62_STATE_EVERY_TICKS
from research_v617_touch_quote import (TOUCH_SLOT as V6_41_TOUCH_SLOT, client_ids as v6_41_touch_ids,
                                       is_touch_client_id as v6_41_is_touch_cid)
from research_v6_40_classes import CLASS2 as V6_41_CLASS2, ClassCaptureTracker as V6_41ClassTracker
from research_v6_40_classes import c2q_decision as v6_41_c2q_decision
from research_v6_41_speed import apply as v6_41_speed_apply, fast_getattr as v6_41_fast_getattr
from research_v6_41_speed import kappa_refresh as v6_41_kappa_refresh
from research_v6_41_units import (
    SIDE_BUY as V6_41_BUY,
    SIDE_SELL as V6_41_SELL,
    V6_41_UNITS_VERSION,
    keeps_exit as v6_41_keeps_exit,
    probe_held as v6_41_probe_held,
    reduce_exit as v6_41_reduce_exit,
    unit_state as v6_41_unit_state,
)
from research_v626_feed_clock import state_sim_id as v6_41_state_sim_id
from research_v6_29_touch_move import PageHistory as V6_41PageHistory
from taos.im.protocol.models import LoanSettlementOption, OrderDirection, STP, TimeInForce
from taos.im.protocol.models import v641_raw_trades as _v6_41_raw_trades_fn

V6_41_C2Q_SESSION_KEY = "v641_c2q_window"
V6_41_C2Q_SESSION_VERSION = 1


class V641Part:
    """v6.41 (research_v641_*): per-class units of the legacy layers, C2Q's inventory, per-class pools"""

    # ---- switches and state (init) -----------------------------------------------------------------------------------

    def _v6_41_init(self) -> None:
        """v6.41, in _init_build_switches after v6.40's: every research_v641_* key of B2 / B2b / B7 (code default off =
        v6.40.1; the launcher ships each on) and the empty state.  With B7 on, v6.6.1's record is made per class (its
        class map read from v6.40's at every pass).  Never raises: an error counts and every switch stays off."""
        self._v6_41_counts: dict[str, int] = {}
        self._v6_41_errors = 0
        self._v6_41_reported = False
        self._v6_41_c2q_restore = None
        self.research_v641_unit_safety = False
        self.research_v641_c2q_reduce = False
        self.research_v641_c2q_persist = False
        self.research_v641_s9_page = False
        self._v6_41_c2q_saved = None
        try:
            cfg = getattr(self, "config", None)
            self.research_v641_unit_safety = self._as_bool(getattr(cfg, "research_v641_unit_safety", False))
            self.research_v641_c2q_reduce = self._as_bool(getattr(cfg, "research_v641_c2q_reduce", False))
            self.research_v641_c2q_persist = self._as_bool(getattr(cfg, "research_v641_c2q_persist", False))
            self.research_v641_s9_page = self._as_bool(getattr(cfg, "research_v641_s9_page", False))
        except Exception:
            self._v6_41_errors += 1

    def _v6_41_count(self, key: str, n: int = 1) -> None:
        try:
            counts = getattr(self, "_v6_41_counts", None)
            if counts is None:
                counts = self._v6_41_counts = {}
            counts[key] = int(counts.get(key, 0) or 0) + int(n)
        except Exception:
            self._v6_41_errors = int(getattr(self, "_v6_41_errors", 0) or 0) + 1

    # ---- B2: the units of a book -----------------------------------------------------------------------------------------

    def _v6_41_grid(self, book_id):
        """B2: the class grid of a book of a traded non-home class (v6.40's, the market check aside: the class's units do
        not change with one request's mid); None for a home-class book, with the switch off or no class traded."""
        if not getattr(self, "research_v641_unit_safety", False):
            return None
        grids = getattr(self, "_v6_40_grids_all", None) or {}
        if not grids:
            return None
        try:
            return grids.get(int(book_id))
        except (TypeError, ValueError):
            return None

    def _v6_41_min(self, book_id, min_size):
        """B2: the minimum order of the book's class (``min_size`` -- the top level's -- for a home-class book)."""
        g = self._v6_41_grid(book_id)
        return min_size if g is None else float(g.min_order)

    def _v6_41_tick(self, book_id, tick_size):
        """B2: the venue tick of the book's class (``tick_size`` for a home-class book)."""
        g = self._v6_41_grid(book_id)
        return tick_size if g is None else float(g.tick)

    def _v6_41_book_state(self, state, book_id):
        """B2: the state seen in the book's class's price / volume decimals (the state itself for a home-class book)."""
        g = self._v6_41_grid(book_id)
        if g is None:
            return state
        self._v6_41_count("unit_state_c%d" % int(g.c))
        return v6_41_unit_state(state, g.dec, g.vol_dec)

    @contextmanager
    def _v6_41_min_scope(self, g):
        """B2: inside, the exchange minimum is the class's (the frozen base reads one attribute); restored after."""
        if g is None:
            yield
            return
        saved = getattr(self, "_research_exchange_min_order_size", 0.25)
        self._research_exchange_min_order_size = float(g.min_order)
        try:
            yield
        finally:
            self._research_exchange_min_order_size = saved

    def _v6_41_groups(self, book_ids) -> list:
        """B2: [(grid or None, [books])] -- the home / ungridded books first (grid None), then each traded non-home class in
        class order; one group (None, all) with the switch off or no class traded."""
        home, by_class = [], {}
        for b in book_ids:
            g = self._v6_41_grid(b)
            if g is None:
                home.append(b)
            else:
                by_class.setdefault(int(g.c), (g, []))[1].append(b)
        return [(None, home)] + [by_class[c] for c in sorted(by_class)]

    # ---- B2: the frozen base's paths that take a book ------------------------------------------------------------------------

    def _refresh_dust_state(self, book_id, net_base, *, emit: bool = True):
        """B2: dust parking on the book's class's minimum (the frozen base's code; a home-class book: unchanged)."""
        g = self._v6_41_grid(book_id)
        if g is None:
            return super()._refresh_dust_state(book_id, net_base, emit=emit)
        with self._v6_41_min_scope(g):
            return super()._refresh_dust_state(book_id, net_base, emit=emit)

    def _research_place_maker_exit(self, response, state, book_id, *args, **kwargs):
        """B2: the frozen maker exit (price rounding, its contract-reject clamp, the minimum) in the book's class's units
        (reached from Simple's override; dead while v6.3 runs, kept unit-aware for any path that reaches it)."""
        g = self._v6_41_grid(book_id)
        if g is None:
            return super()._research_place_maker_exit(response, state, book_id, *args, **kwargs)
        self._v6_41_count("maker_exit_c%d" % int(g.c))
        with self._v6_41_min_scope(g):
            return super()._research_place_maker_exit(response, v6_41_unit_state(state, g.dec, g.vol_dec), book_id,
                                                      *args, **kwargs)

    def _research_apply_contract_reject_guard(self, response, state):
        """B2: the frozen contract-reject guard (Strategy1_Research, unchanged) with each traded non-home class's maker
        orders repriced on their class's venue tick / decimals: one call of the base on every other instruction exactly as
        before, one per class on a state in that class's decimals; the response keeps the original order of what survives;
        the tallies summed.  No class traded: the base itself."""
        instructions = list(getattr(response, "instructions", None) or [])
        groups: dict = {}
        rest = []
        for ix in instructions:
            try:
                g = self._v6_41_grid(int(self._get(ix, "bookId", "book_id")))
            except (TypeError, ValueError):
                g = None
            if g is not None and self._research_instruction_is_maker(ix):
                groups.setdefault(int(g.c), (g, []))[1].append(ix)
            else:
                rest.append(ix)
        if not groups:
            return super()._research_apply_contract_reject_guard(response, state)
        response.instructions[:] = rest
        out = dict(super()._research_apply_contract_reject_guard(response, state) or {})
        kept = {id(ix) for ix in (getattr(response, "instructions", None) or ())}
        for _c, (g, ixs) in sorted(groups.items()):
            shim = type("V641Shim", (), {})()
            shim.instructions = list(ixs)
            res = super()._research_apply_contract_reject_guard(shim, v6_41_unit_state(state, g.dec, g.vol_dec)) or {}
            kept.update(id(ix) for ix in shim.instructions)
            for k in ("skips", "reprices"):
                out[k] = int(out.get(k, 0) or 0) + int(res.get(k, 0) or 0)
            out["active"] = res.get("active", out.get("active", 0))
            self._v6_41_count("guard_c%d" % int(g.c), len(ixs))
        response.instructions[:] = [ix for ix in instructions if id(ix) in kept]
        return out

    def _select_dust_compaction_books(self, state):
        """B2: the dust compactor's selection per class -- the frozen ranking on each class's parked books with its own
        minimum and its own books-per-tick (the home books exactly as before).  No class traded: the base itself."""
        parked = getattr(self, "_research_parked_dust", None) or {}
        groups = self._v6_41_groups(list(parked)) if parked else [(None, [])]
        if len(groups) == 1:
            return super()._select_dust_compaction_books(state)
        out: set = set()
        try:
            for g, books in groups:
                if not books and g is None:
                    continue
                self._research_parked_dust = {b: parked[b] for b in books}
                with self._v6_41_min_scope(g):
                    out |= set(super()._select_dust_compaction_books(state) or ())
        finally:
            self._research_parked_dust = parked
        return out

    def _v6_41_compact(self, response, state) -> int:
        """B2: Simple's dust compactor run per class -- the home books' selection on the real state exactly as before, each
        traded non-home class's on a state in its decimals with its minimum (the clip it places: one class minimum)."""
        selected = set(getattr(self, "_research_dust_compact_ids_this_tick", None) or set())
        groups = self._v6_41_groups(sorted(selected)) if selected else [(None, [])]
        if len(groups) == 1:
            return self._direct_compact_selected_dust(response, state)
        placed = 0
        try:
            for g, books in groups:
                if not books:
                    continue
                self._research_dust_compact_ids_this_tick = set(books)
                with self._v6_41_min_scope(g):
                    placed += int(self._direct_compact_selected_dust(
                        response, state if g is None else v6_41_unit_state(state, g.dec, g.vol_dec)) or 0)
                if g is not None:
                    self._v6_41_count("compact_runs_c%d" % int(g.c))
        finally:
            self._research_dust_compact_ids_this_tick = selected
        return placed

    def _v6_41_normalize(self, response, state, **kw) -> int:
        """B2 / B2b: Simple's dust normalizer per class (each class's parked books, its minimum and decimals, its own one
        book per request; the home books exactly as before); while C2Q is disarmed class 2 is left out (a normalizer
        clip adds to |inventory|: research_v641_c2q_reduce)."""
        parked = getattr(self, "_research_parked_dust", None) or {}
        blocked = frozenset()
        if getattr(self, "research_v641_c2q_reduce", False) and self._v6_41_c2q_disarmed():
            blocked = frozenset(getattr(self, "_v6_40_c2", None) or ())
        groups = self._v6_41_groups(list(parked)) if parked else [(None, [])]
        if len(groups) == 1 and not (blocked and any(int(b) in blocked for b in parked)):
            return self._direct_normalize_irreducible_dust(response, state, **kw)
        placed = 0
        try:
            for g, books in groups:
                keep = [b for b in books if int(b) not in blocked]
                if len(keep) < len(books):
                    self._v6_41_count("normalize_c2q_blocked", len(books) - len(keep))
                if not keep:
                    continue
                self._research_parked_dust = {b: parked[b] for b in keep}
                with self._v6_41_min_scope(g):
                    placed += int(self._direct_normalize_irreducible_dust(
                        response, state if g is None else v6_41_unit_state(state, g.dec, g.vol_dec), **kw) or 0)
        finally:
            self._research_parked_dust = parked
        return placed

    # ---- B2b: C2Q's inventory --------------------------------------------------------------------------------------------

    def _v6_41_c2q_disarmed(self) -> bool:
        st = getattr(self, "_v6_40_c2q", None) or {}
        return bool(getattr(self, "research_v640_c2q", 0)) and not bool(st.get("armed", True))

    def _v6_41_c2q_exit(self, response, book_id: int, rows, already, req, *, budget: int, raw_bid, raw_ask, inv, clip,
                        expiry) -> int:
        """B2b (research_v641_c2q_reduce), in the pass for a class-2 book C2Q disarmed this block: every order of ours on it
        is cancelled in one instruction except the reduce-only exit to keep (keeps_exit: the reducing side, at that side's
        best price, never more than the position); with none kept, one is placed (reduce_exit: one clip in the class's
        units, never more than the position, joining the reducing side's best price, post-only, the touch quote's client
        id and slot) once no other order of ours rests on that side and none was cancelled there this request.  ``inv``
        is the pass's inventory (class-0 units), ``clip`` the deep layer's.  Never raises: an error counts and v6.40's
        cancel-all runs instead.  Returns the placements (0 or 1)."""
        try:
            g = (getattr(self, "_v6_40_grids", None) or {}).get(int(book_id))
            if g is None:
                return self._v6_40_c2q_cancel(response, book_id, rows, already, req, budget=budget)
            self._v6_40_count("c2q_off_book_states")
            pos = float(inv) * float(g.scale)
            want = v6_41_reduce_exit(pos, float(clip) * float(g.scale), g.min_order, g.vol_dec, raw_bid, raw_ask)
            doomed, kept, others = [], 0, set()
            for r in rows:
                oid = int(r.order_id)
                if oid in already:
                    continue
                side = V6_41_BUY if int(r.side) == 0 else V6_41_SELL
                qty = getattr(r, "remaining", None) or getattr(r, "quantity", None)
                if (want is not None and not kept and v6_41_is_touch_cid(getattr(r, "client_id", None))
                        and v6_41_keeps_exit(side, r.price, qty, want, pos, g.tick)):
                    kept += 1
                    continue
                doomed.append(oid)
                others.add(side)
            if doomed and self._count_book_instructions(response, book_id) < budget:
                response.cancel_orders(book_id=book_id, order_ids=sorted(doomed), delay=0)
                req["cancels"] = int(req.get("cancels", 0) or 0) + 1
                self._v6_40_count("c2q_cancelled", len(doomed))
                try:
                    self._a19_note_exit_cancel(book_id, sorted(doomed), ABSENT_REPRICE_CANCEL)
                except Exception:
                    pass
            if kept:
                self._v6_41_count("c2q_exit_kept")
                return 0
            if want is None:
                self._v6_41_count("c2q_exit_none")
                return 0
            side, q, price = want
            if side in others:
                self._v6_41_count("c2q_exit_wait_side")          # an order of ours still rests / was cancelled there
                return 0
            if V6_41_TOUCH_SLOT in (self._v69_live_slots(book_id).get(side) or frozenset()):
                self._v6_41_count("c2q_exit_slot_in_flight")
                return 0
            if self._count_book_instructions(response, book_id) >= budget:
                self._v6_41_count("c2q_exit_budget")
                return 0
            buy_cid, sell_cid = v6_41_touch_ids(book_id)
            response.limit_order(book_id=book_id, direction=OrderDirection.BUY if side == V6_41_BUY else OrderDirection.SELL,
                                 quantity=float(q), price=round(float(price), int(g.dec)),
                                 clientOrderId=buy_cid if side == V6_41_BUY else sell_cid, stp=STP.CANCEL_BOTH,
                                 postOnly=True, timeInForce=TimeInForce.GTT, expiryPeriod=expiry, leverage=0.0,
                                 settlement_option=LoanSettlementOption.NONE, delay=0)
            self._v6_41_count("c2q_exit_placed_" + side)
            return 1
        except Exception:
            self._v6_41_errors = int(getattr(self, "_v6_41_errors", 0) or 0) + 1
            return self._v6_40_c2q_cancel(response, book_id, rows, already, req, budget=budget)

    def _v6_41_probe_held(self, book_id: int, inv, deep, held) -> frozenset:
        """B2b, in the pass for an armed class-2 book: during a C2Q probe the side that would add to |inventory| holds in
        every layer (v6.19's held set) once the book carries one deep clip or more; else ``held`` as it is."""
        try:
            st = getattr(self, "_v6_40_c2q", None) or {}
            if not getattr(self, "research_v640_c2q", 0) or not st.get("probe", False):
                return held
            g = (getattr(self, "_v6_40_grids", None) or {}).get(int(book_id))
            if g is None or int(g.c) != V6_41_CLASS2:
                return held
            more = v6_41_probe_held(inv, float(getattr(deep, "clip", 0.0) or 0.0))
            if more - frozenset(held):
                self._v6_41_count("c2q_probe_held_book_states")
                return frozenset(held) | more
            return held
        except Exception:
            self._v6_41_errors = int(getattr(self, "_v6_41_errors", 0) or 0) + 1
            return held

    def _v6_40_c2q_update(self) -> None:
        """B2b(3): a C2Q window saved by the last run of this simulation is put back into the tracker before the first
        decision (after S14 opened its window this request), then v6.40's decision.  While a restored verdict is held
        and v6.40's rule can only probe because the making mirror (restarted empty) has no beta yet: the first block
        after the relaunch takes the saved verdict itself (the relaunch starts where the last run stood); each later
        block the rule on the current sums with the saved betas (the mirror inputs the last run used), or the saved
        verdict without them; the first block the live mirror can decide drops the saved verdict."""
        if getattr(self, "_v6_41_c2q_restore", None) is not None:
            self._v6_41_apply_c2q_restore()
        st = getattr(self, "_v6_40_c2q", None)
        blk_before = st.get("block") if isinstance(st, dict) else None
        super()._v6_40_c2q_update()
        saved = getattr(self, "_v6_41_c2q_saved", None)
        st = getattr(self, "_v6_40_c2q", None)
        if saved is None or not isinstance(st, dict) or st.get("block") == blk_before:
            return
        try:
            b0, b2 = st.get("b0"), st.get("b2")
            mirror_ready = b0 is not None and float(b0) > 0.0 and b2 is not None
            if not st.get("probe", False) or mirror_ready:
                self._v6_41_c2q_saved = None              # the live rule decides from here on
                self._v6_41_count("c2q_saved_verdict_released")
                return
            sb0, sb2 = saved.get("b0"), saved.get("b2")
            d = None
            first = bool(getattr(self, "_v6_41_c2q_first", False))
            self._v6_41_c2q_first = False
            if not first and sb0 is not None and float(sb0) > 0.0 and sb2 is not None:
                tracker = getattr(self, "_v6_32_tracker", None)
                r_all, c_all = tracker.sums()
                d = v6_41_c2q_decision(r_all, c_all, st.get("r0"), st.get("c0"), st.get("r2"), st.get("c2"), sb0, sb2,
                                       st.get("pf"))
            if d is None or d.get("probe", True):
                d = {"armed": bool(saved.get("armed", True)), "probe": bool(saved.get("probe", True))}
                self._v6_41_count("c2q_saved_verdict_blocks")
            else:
                self._v6_41_count("c2q_saved_betas_blocks")
            st.update(d)
            st.update(b0=sb0, b2=sb2)                     # the betas this decision stands on (a later save keeps them)
            verdict = "probe" if st.get("probe") else ("armed" if st.get("armed") else "disarmed")
            if verdict != getattr(self, "_v6_40_c2q_verdict", None):
                self._v6_40_c2q_verdict = verdict
                self._v6_40_c2q_changed = True
        except Exception:
            self._v6_41_errors = int(getattr(self, "_v6_41_errors", 0) or 0) + 1

    def _v6_41_save_c2q(self, payload) -> None:
        """B2b(3) (research_v641_c2q_persist), in the session save: C2Q's window -- K3Q's (ts, R, C) and its class split
        (ts, class, R, C) -- once it holds class-2 evidence, with the last block's verdict and the making betas it used,
        keyed on the simulation id.  Never raises."""
        try:
            tracker = getattr(self, "_v6_32_tracker", None)
            if not isinstance(tracker, V6_41ClassTracker):
                return
            if not any(int(cls) == V6_41_CLASS2 for _ts, cls, _r, _c in tracker.v640):
                return
            payload[V6_41_C2Q_SESSION_KEY] = {
                "version": V6_41_C2Q_SESSION_VERSION, "sim": str(getattr(self, "_v6_40_sim_id", None) or ""),
                "k3q": [[int(ts), float(r), float(c)] for ts, r, c in tracker.k3q],
                "cls": [[int(ts), int(cls), float(r), float(c)] for ts, cls, r, c in tracker.v640],
                "verdict": self._v6_41_c2q_state()}
            self._v6_41_count("c2q_saved")
        except Exception:
            self._v6_41_errors = int(getattr(self, "_v6_41_errors", 0) or 0) + 1

    def _v6_41_c2q_state(self) -> dict | None:
        """The last block's C2Q verdict and the making betas it used (None before a decision)."""
        st = getattr(self, "_v6_40_c2q", None) or {}
        if st.get("block") is None:
            return None

        def num(v):
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            return f if f == f and abs(f) != float("inf") else None
        return {"armed": bool(st.get("armed", True)), "probe": bool(st.get("probe", True)), "block": st.get("block"),
                "b0": num(st.get("b0")), "b2": num(st.get("b2"))}

    def _v6_41_read_c2q(self, raw) -> None:
        """B2b(3), in the session read: the saved C2Q window, put back at the first C2Q decision."""
        try:
            if getattr(self, "_v6_41_c2q_done", False):
                return                                    # one restore per process: the first read of the session
            got = raw.get(V6_41_C2Q_SESSION_KEY) if isinstance(raw, dict) else None
            if isinstance(got, dict) and int(got.get("version", 0) or 0) == V6_41_C2Q_SESSION_VERSION:
                self._v6_41_c2q_restore = got
                self._v6_41_count("c2q_read")
        except Exception:
            self._v6_41_errors = int(getattr(self, "_v6_41_errors", 0) or 0) + 1

    def _v6_41_apply_c2q_restore(self) -> None:
        got, self._v6_41_c2q_restore = self._v6_41_c2q_restore, None
        self._v6_41_c2q_done = True
        try:
            tracker = getattr(self, "_v6_32_tracker", None)
            if not isinstance(tracker, V6_41ClassTracker) or tracker.k3q or tracker.v640:
                self._v6_41_count("c2q_restore_skipped")
                return
            sim = str(getattr(self, "_v6_40_sim_id", None) or "")
            if got.get("sim") and sim and got.get("sim") != sim:
                self._v6_41_count("c2q_restore_other_sim")
                return
            tracker.k3q = deque((int(ts), float(r), float(c)) for ts, r, c in got.get("k3q") or ())
            tracker.k3q_r = sum(r for _ts, r, _c in tracker.k3q)
            tracker.k3q_c = sum(c for _ts, _r, c in tracker.k3q)
            tracker.v640 = deque((int(ts), int(cls), float(r), float(c)) for ts, cls, r, c in got.get("cls") or ())
            sums: dict = {}
            for _ts, cls, r, c in tracker.v640:
                acc = sums.setdefault(cls, [0.0, 0.0])
                acc[0] += r
                acc[1] += c
            tracker.v640_sums = sums
            verdict = got.get("verdict")
            if isinstance(verdict, dict) and not verdict.get("probe", True):
                self._v6_41_c2q_saved = dict(verdict)         # held until the live mirror can decide
                self._v6_41_c2q_first = True                  # the first block after the relaunch: the verdict itself
            self._v6_41_count("c2q_restored")
        except Exception:
            self._v6_41_errors = int(getattr(self, "_v6_41_errors", 0) or 0) + 1

    # ---- B7: S9's page once per request ---------------------------------------------------------------------------------

    def _v6_41_s9_page(self, state) -> None:
        """B7 (research_v641_s9_page; STRUCTURAL, rule parity), in the pass once per request before the books: v6.29 S9's
        page history observes this request's page -- exactly the observe _v6_29_held makes at the first touch decision of
        a request (the state's own fundamentals with v6.32 P1, else the feed's page; the same state time and simulation
        id), so the later per-book observes of the request find the page already kept.  vm739's S9 reads its reference
        (the newest page at or before t - W) on every state; the agent recorded a page only on requests where some book
        reached a touch decision -- with class 2 traded its books take their own path (C2D: no touch core), requests
        without a class-0 touch decision recorded none and class 0's S9 found no reference page where the traded-"0" run
        had one (the G2 class-0 drift: k144 probe, book 55 tick 297 "no_history" vs "fp_fell").  Never raises: an error
        counts and the request is as before."""
        try:
            if not bool(getattr(self, "research_v629_touch_fp_move", False)):
                return
            pages = getattr(self, "_v6_29_pages", None)
            if pages is None:
                pages = V6_41PageHistory()
                self._v6_29_pages = pages
            cfg = getattr(state, "config", None)
            now_ts = int(getattr(self, "_direct_current_state_timestamp_ns", 0) or 0)
            snap = None
            if bool(getattr(self, "research_v632_state_fundamental", False)):
                snap = self._v6_32_s9_snapshot()
            if snap is None:
                feed = getattr(self, "_v616_feed", None)
                snap = feed.snapshot() if feed is not None else None
            if pages.observe(snap, now_ts, v6_41_state_sim_id(cfg)):
                self._v6_41_count("s9_pages_recorded")
        except Exception:
            self._v6_41_errors = int(getattr(self, "_v6_41_errors", 0) or 0) + 1

    # ---- B1: the deferred session save, D's recorded prints --------------------------------------------------------------

    _v6_41_raw_trades = staticmethod(_v6_41_raw_trades_fn)     # B1 D: v6.32's prints from the P5 pass's record (or None)
    _v6_41_apply = staticmethod(v6_41_speed_apply)             # B1: the module switches of D / G / H
    _v6_41_getattr = staticmethod(v6_41_fast_getattr)          # B1 F1: == getattr without pydantic's misses

    def _v6_41_kappa(self, frozen) -> None:
        """B1 C: the frozen rolling-kappa refresh's outputs from the previous result + the unseen history tail
        (research_v6_41_speed.kappa_refresh; ``frozen`` = the frozen body, its fallback)."""
        v6_41_kappa_refresh(self, frozen)

    def _v641_deferred_save(self) -> None:
        """v6.41 B1 A: the request's cadence save, run after the reply (the first post-reply job)."""
        self._v641_saving = True
        try:
            self._research_save_session(force=False)
        finally:
            self._v641_saving = False

    # ---- telemetry ------------------------------------------------------------------------------------------------------

    def _v6_40_request(self, state) -> None:
        """v6.40's request (unchanged), then the V6_41_STATE row."""
        super()._v6_40_request(state)
        self._v6_41_report()

    def _v6_41_report(self) -> None:
        """v6.41: the V6_41_STATE row -- at the first request, at the first one with a new error and every
        V62_STATE_EVERY_TICKS requests; after the reply when the request defers its telemetry.  Never raises."""
        try:
            tick = int(getattr(self, "_tick", 0) or 0)
            errors = int(getattr(self, "_v6_41_errors", 0) or 0)
            erring = errors > int(getattr(self, "_v6_41_errors_seen", 0) or 0)
            self._v6_41_errors_seen = errors
            if not getattr(self, "_v6_41_reported", False):
                reason = "start"
            elif erring:
                reason = "error"
            elif tick > 0 and tick % V62_STATE_EVERY_TICKS == 0:
                reason = "periodic"
            else:
                return
            self._v6_41_reported = True
            row = {"version": V6_41_UNITS_VERSION, "reason": reason, "tick": tick,
                   "speed": int(bool(getattr(self, "research_v641_speed", False))),
                   "unit_safety": int(bool(getattr(self, "research_v641_unit_safety", False))),
                   "c2q_reduce": int(bool(getattr(self, "research_v641_c2q_reduce", False))),
                   "c2q_persist": int(bool(getattr(self, "research_v641_c2q_persist", False))),
                   "s9_page": int(bool(getattr(self, "research_v641_s9_page", False))),
                   "c2q_saved_verdict": getattr(self, "_v6_41_c2q_saved", None),
                   "counts": dict(getattr(self, "_v6_41_counts", None) or {}), "errors": errors}
            work = getattr(self, "_v629_request_work", None)
            if work is not None:
                work.add("v6_41_state_row", lambda: self._emit("V6_41_STATE", force=True, **row))
            else:
                self._emit("V6_41_STATE", force=True, **row)
        except Exception:
            self._v6_41_errors = int(getattr(self, "_v6_41_errors", 0) or 0) + 1
