"""v6.36 step withdrawal (A: the pre-step pull inside the book's radius; D: the stale-side hold) -- research_v636_step_withdrawal

v6.39 S4 (k129): methods of Strategy1_Research_Simple moved here VERBATIM (simple_parts/join.py puts them back at their
lines: the joined text is byte-identical to the pre-split file).  A mixin: no state, no logic of its own; `self` is the agent.
"""
from __future__ import annotations

from research_direct_exit_refresh import ABSENT_ENTRY_QUOTE_CANCEL, ABSENT_REPRICE_CANCEL
from research_v62_breadth import V62_STATE_EVERY_TICKS
from research_v6_36_step_withdrawal import (
    GRID_OK as V6_36_GRID_OK,
    V6_36_STEP_WITHDRAWAL_VERSION,
    block_phase as v6_36_block_phase,
    hold_phase as v6_36_hold_phase,
    l1_of as v6_36_l1_of,
    mid_of as v6_36_mid_of,
    placed_price as v6_36_placed_price,
    pre_step as v6_36_pre_step,
    side_of as v6_36_side_of,
    state_map as v6_36_state_map,
    step_grid as v6_36_step_grid,
    withdrawal as v6_36_withdrawal,
)


class V636Part:
    """v6.36 step withdrawal (A: the pre-step pull inside the book's radius; D: the stale-side hold) -- research_v636_step_withdrawal"""

    def _v6_36_request(self, state) -> None:
        """v6.36 (research_v636_step_withdrawal), in respond() once per request right after v6.32 mapped the state's own
        fundamentals, before any decision: the step grid from the configuration (v6_36_step_grid: K states per
        fp_update_period; unreadable or off the state grid: nothing is withdrawn), the step record takes this state (the
        agent's own fundamentals since its start: a step at the first state of each new block), every book's last
        two-sided L1 is kept (the replay's book L1), and this state's plan is set -- on the pre-step state (phase K - 1)
        each book with a radius and a mid gets A's (mid, r); on the step state and the next, when this block's step was
        seen, each book the step moved gets D's stale side.  Off: nothing (v6.35).  Never raises: an error counts and
        nothing is withdrawn this request."""
        req = {"phase": None, "block": None, "pre_step": False, "hold": False, "books": {}}
        self._v6_36_req = req
        if not bool(getattr(self, "research_v636_step_withdrawal", False)):
            return
        try:
            grid = v6_36_step_grid(getattr(state, "config", None))
            self._v6_36_grid = grid
            ok = grid.get("status") == V6_36_GRID_OK
            if not ok:
                self._v6_36_count("grid_" + str(grid.get("status")))
            record = self._v6_36_record
            if ok:
                record.resize(grid.get("window_steps"))
            now = int(getattr(state, "timestamp", 0) or 0)
            books = getattr(state, "books", None)
            fp = getattr(self, "_v6_32_fp", None)
            if isinstance(fp, tuple) and len(fp) >= 2 and fp[0] == now and isinstance(fp[1], dict):
                fps = fp[1]                                 # v6.32 P1's map of this very state
            else:
                fps = v6_36_state_map(books)
            where = v6_36_block_phase(now, grid)
            event = record.observe(now, None if where is None else where[0], fps)
            self._v6_36_count("record_" + event.replace("reset+", ""))
            l1 = self._v6_36_l1
            if event.startswith("reset+"):
                l1.clear()                                  # a clock that ran back: a new run
                self._v6_36_count("resets")
            raw = getattr(books, "_raw_books", None)
            items = list(raw.items()) if isinstance(raw, dict) else list((books or {}).items())
            for raw_id, book in items:
                bid, ask = v6_36_l1_of(book)
                if bid is not None and ask is not None:
                    l1[int(raw_id)] = (bid, ask)            # the replay's book L1: the last state with both sides
            if where is None:
                return
            block, phase = where
            req["block"], req["phase"] = block, phase
            excluded = grid.get("excluded") or frozenset()
            plan = req["books"]
            if v6_36_pre_step(phase, grid):
                req["pre_step"] = True
                self._v6_36_count("pre_step_states")
                pooled, pooled_read = None, False
                for raw_id, _book in items:
                    book_id = int(raw_id)
                    if book_id in excluded:
                        continue
                    own = record.steps.get(book_id)
                    if (own is None or len(own) < record.min_own) and not pooled_read:
                        pooled, pooled_read = record.pooled_bps(), True
                    radius, is_own = record.radius(book_id, pooled)
                    last = l1.get(book_id)
                    mid = v6_36_mid_of(last[0], last[1]) if last else None
                    if radius is None:
                        self._v6_36_count("pull_no_radius")
                    elif mid is None:
                        self._v6_36_count("pull_no_mid")
                    else:
                        plan[book_id] = (mid, radius, None)
                        self._v6_36_count("pull_own_radius" if is_own else "pull_pooled_radius")
            if v6_36_hold_phase(phase) and record.step_block == block:
                req["hold"] = True
                self._v6_36_count("hold_states")
                for raw_id, _book in items:
                    book_id = int(raw_id)
                    side = None if book_id in excluded else record.stale_side(book_id, block)
                    if side is not None:
                        prev = plan.get(book_id)
                        plan[book_id] = (prev[0], prev[1], side) if prev else (None, None, side)
                        self._v6_36_count("hold_books")
        except Exception:
            self._v6_36_errors = int(getattr(self, "_v6_36_errors", 0) or 0) + 1
            self._v6_36_req = {"phase": None, "block": None, "pre_step": False, "hold": False, "books": {}}

    def _v6_36_drop(self, response, state) -> None:
        """v6.36 in build_mm_strategy_instructions, right after the final validator and before the pending ledger
        records the placements: each limit placement on a book of this state's plan that A / D withdraw
        (v6_36_withdrawal on the price it would rest at: v6.1.1's wire price truncated as the venue does) is dropped --
        as a final-validator reject, so nothing records it as resting -- and a book whose resting orders A / D will
        cancel keeps one instruction free under max_instructions_per_book for that cancel: its placements go from the
        last one until it does (the cancel wins).  Never raises: an error counts and that book keeps its placements
        (v6.35)."""
        try:
            plan = (getattr(self, "_v6_36_req", None) or {}).get("books") or {}
            instructions = getattr(response, "instructions", None)
            if not plan or not isinstance(instructions, list) or not bool(
                    getattr(self, "research_v636_step_withdrawal", False)):
                return
            decimals = self._v61_price_decimals(state)
            lift = bool(self._v611_price_lift_on())
            v6_40_decs = getattr(self, "_v6_40_decs", None) or {}     # v6.40: per-book decimals of traded classes
            drop: set[int] = set()
            per_book: dict[int, list] = {}
            for ix in instructions:
                try:
                    book_id = int(self._get(ix, "bookId", "book_id"))
                except (TypeError, ValueError):
                    continue
                per_book.setdefault(book_id, []).append(ix)
                if book_id not in plan or str(self._get(ix, "type") or "").upper() != "PLACE_ORDER_LIMIT":
                    continue
                try:
                    mid, radius, hold_side = plan[book_id]
                    why = v6_36_withdrawal(
                        v6_36_side_of(self._get(ix, "direction", "side")),
                        v6_36_placed_price(self._get(ix, "price", "limitPrice", "limit_price"),
                                           (v6_40_decs.get(book_id, decimals) if v6_40_decs else decimals), lift),
                        mid=mid, radius=radius, hold_side=hold_side)
                    if why is not None:
                        drop.add(id(ix))
                        self._v6_36_count("dropped_" + str(why).lower())
                except Exception:
                    self._v6_36_errors = int(getattr(self, "_v6_36_errors", 0) or 0) + 1
            budget = int(getattr(self, "max_instructions_per_book", 5) or 5)
            already = self._v6_36_cancelled(response)
            for book_id in sorted(plan):
                try:
                    if not self._v6_36_doomed(book_id, plan[book_id], already):
                        continue
                    rows = [ix for ix in per_book.get(book_id, ()) if id(ix) not in drop]
                    n = len(rows)
                    if n + 1 <= budget:
                        continue
                    self._v6_36_count("cap_conflicts")
                    for ix in reversed(rows):
                        if n + 1 <= budget:
                            break
                        if str(self._get(ix, "type") or "").upper() in ("PLACE_ORDER_LIMIT", "PLACE_ORDER_MARKET"):
                            drop.add(id(ix))
                            n -= 1
                            self._v6_36_count("cap_dropped")
                except Exception:
                    self._v6_36_errors = int(getattr(self, "_v6_36_errors", 0) or 0) + 1
            if drop:
                instructions[:] = [ix for ix in instructions if id(ix) not in drop]
        except Exception:
            self._v6_36_errors = int(getattr(self, "_v6_36_errors", 0) or 0) + 1

    def _v6_36_cancel(self, response, state) -> None:
        """v6.36 in respond(), after the last post-pass: every resting order of ours (the state's account orders) on a
        book of this state's plan that A / D withdraw and that no cancel of this response names is cancelled -- one
        instruction per book, placed FIRST among that book's instructions (the validator lands the first one per book at
        the base delay); a book already at max_instructions_per_book has the ids merged into its first cancel
        instruction, re-issued first (its count unchanged), and without one keeps its orders this state (counted).  The
        cancels are registered as v6.2.14's are (A1.9's watch: an entry quote ENTRY_QUOTE_CANCEL, others REPRICE_CANCEL)
        once the response holds them.  Then the V6_36_STATE row.  Never raises: an error counts and that book (or, in
        the final reorder, the whole response) is left as it was."""
        try:
            plan = (getattr(self, "_v6_36_req", None) or {}).get("books") or {}
            instructions = getattr(response, "instructions", None)
            if plan and isinstance(instructions, list) and bool(getattr(self, "research_v636_step_withdrawal", False)):
                budget = int(getattr(self, "max_instructions_per_book", 5) or 5)
                already = self._v6_36_cancelled(response)
                made: dict[int, tuple] = {}
                for book_id in sorted(plan):
                    try:
                        doomed = self._v6_36_doomed(book_id, plan[book_id], already)
                        if not doomed:
                            continue
                        ids = [oid for oid, _entry, _why in doomed]
                        old = None
                        if self._count_book_instructions(response, book_id) >= budget:
                            old = next((ix for ix in instructions
                                        if self._get(ix, "bookId", "book_id") == book_id
                                        and str(self._get(ix, "type") or "").upper() == "CANCEL_ORDERS"), None)
                            if old is None:
                                self._v6_36_count("cap_unresolved")
                                continue
                        every = list(ids)
                        if old is not None:
                            every += [int(c.orderId) for c in (getattr(old, "cancellations", None) or ())
                                      if int(c.orderId) not in ids]
                        before = len(instructions)
                        response.cancel_orders(book_id=book_id, order_ids=every, delay=0)
                        new = instructions[before:]
                        del instructions[before:]
                        if len(new) != 1:
                            raise ValueError("one instruction expected")
                        made[book_id] = (new[0], old, doomed)
                        already.update((book_id, oid) for oid in ids)
                    except Exception:
                        self._v6_36_errors = int(getattr(self, "_v6_36_errors", 0) or 0) + 1
                if made:
                    self._v6_36_first(instructions, made)       # each book's withdrawal cancel first among its own
                    for book_id, (_new, old, doomed) in made.items():
                        for disposition, quote in ((ABSENT_REPRICE_CANCEL, False), (ABSENT_ENTRY_QUOTE_CANCEL, True)):
                            group = [oid for oid, entry, _why in doomed if entry == quote]
                            if group:
                                try:
                                    self._a19_note_exit_cancel(book_id, group, disposition)
                                except Exception:
                                    pass
                        self._v6_36_count("cancel_instructions")
                        if old is not None:
                            self._v6_36_count("merged")
                        for _oid, _entry, why in doomed:
                            self._v6_36_count("cancelled_" + str(why).lower())
        except Exception:
            self._v6_36_errors = int(getattr(self, "_v6_36_errors", 0) or 0) + 1
        self._v6_36_report()

    def _v6_36_first(self, instructions: list, made: dict) -> None:
        """v6.36: the response's instructions in their order, each book of ``made`` ({book: (its withdrawal cancel, the
        cancel it replaces or None, ...)}) with its withdrawal cancel placed right before the book's first instruction
        (appended for a book without one) and the replaced cancel left out -- set in one assignment, so an error leaves
        the list as it was (and raises: the caller then registers nothing)."""
        gone = {id(old) for _new, old, *_rest in made.values() if old is not None}
        out, first = [], set()
        for ix in instructions:
            if id(ix) in gone:
                continue
            b = self._get(ix, "bookId", "book_id")
            if b in made and b not in first:
                out.append(made[b][0])
                first.add(b)
            out.append(ix)
        out += [entry[0] for b, entry in made.items() if b not in first]
        instructions[:] = out

    def _v6_36_doomed(self, book_id: int, entry, already) -> list:
        """v6.36: [(order id, entry quote?, why)] of the book's resting orders -- the state's account orders: touch
        quote, deep level 0, ladder, exit, whatever placed it -- that A / D withdraw this state (v6_36_withdrawal on
        the plan's mid, radius and stale side) and that no cancel of this response names (``already``: (book, order
        id) pairs)."""
        mid, radius, hold_side = entry
        out = []
        for order in self._direct_account_orders(int(book_id)):
            oid = getattr(order, "id", None)
            if oid is None or (int(book_id), int(oid)) in already:
                continue
            why = v6_36_withdrawal(v6_36_side_of(getattr(order, "side", None)), getattr(order, "price", None),
                                   mid=mid, radius=radius, hold_side=hold_side)
            if why is not None:
                out.append((int(oid), bool(self._direct_is_entry_quote_order(int(book_id), order)), why))
        return out

    def _v6_36_cancelled(self, response) -> set:
        """v6.36: the (book, order id) pairs a cancel instruction of this response already names (the venue numbers
        orders per book)."""
        out = set()
        for ix in list(getattr(response, "instructions", None) or ()):
            cancellations = getattr(ix, "cancellations", None)
            if not cancellations:
                continue
            try:
                book_id = int(self._get(ix, "bookId", "book_id"))
            except (TypeError, ValueError):
                continue
            for c in cancellations:
                try:
                    out.add((book_id, int(getattr(c, "orderId"))))
                except (TypeError, ValueError, AttributeError):
                    continue
        return out

    def _v6_36_count(self, key: str, n: int = 1) -> None:
        """v6.36's tally.  Never raises: an error counts."""
        try:
            counts = getattr(self, "_v6_36_counts", None)
            if counts is None:
                counts = {}
                self._v6_36_counts = counts
            counts[key] = int(counts.get(key, 0) or 0) + int(n)
        except Exception:
            self._v6_36_errors = int(getattr(self, "_v6_36_errors", 0) or 0) + 1

    def _v6_36_report(self) -> None:
        """v6.36: the V6_36_STATE row -- the switch and version, the step grid (its source and status, the period, the
        publish interval, K, the books of a class on another grid), the step record (books, steps seen, gaps, breaks,
        resets, the pooled radius and the books' own radii p50), this request's phase, the pulls (A: cancelled resting /
        dropped new), the holds (D: the same), the instruction-cap conflicts and the errors -- at the first request, at
        the first request with an error after one without, and every V62_STATE_EVERY_TICKS ticks; after the reply when
        the request defers its telemetry (v6.2.9).  Never raises."""
        try:
            tick = int(getattr(self, "_tick", 0) or 0)
            errors = int(getattr(self, "_v6_36_errors", 0) or 0)
            erring = errors > int(getattr(self, "_v6_36_errors_seen", 0) or 0)
            was_erring = bool(getattr(self, "_v6_36_erring", False))
            self._v6_36_errors_seen, self._v6_36_erring = errors, erring
            if not getattr(self, "_v6_36_reported", False):
                reason = "start"
            elif erring and not was_erring:
                reason = "error"
            elif tick > 0 and tick % V62_STATE_EVERY_TICKS == 0:
                reason = "periodic"
            else:
                return
            self._v6_36_reported = True
            row = dict(self._v6_36_snapshot(), reason=reason, tick=tick)
            work = getattr(self, "_v629_request_work", None)
            if work is not None:
                work.add("v6_36_state_row", lambda: self._emit("V6_36_STATE", force=True, **row))   # after the reply
            else:
                self._emit("V6_36_STATE", force=True, **row)
        except Exception:
            self._v6_36_errors = int(getattr(self, "_v6_36_errors", 0) or 0) + 1

    def _v6_36_snapshot(self) -> dict:
        grid = dict(getattr(self, "_v6_36_grid", None) or {})
        record = getattr(self, "_v6_36_record", None)
        snap = record.snapshot() if record is not None else {}
        req = dict(getattr(self, "_v6_36_req", None) or {})
        counts = dict(getattr(self, "_v6_36_counts", None) or {})
        row = {"version": V6_36_STEP_WITHDRAWAL_VERSION,
               "step_withdrawal_on": int(bool(getattr(self, "research_v636_step_withdrawal", False))),
               "period_source": grid.get("source"), "period_status": grid.get("status"),
               "fp_update_period_ns": grid.get("period_ns"), "publish_interval_ns": grid.get("interval_ns"),
               "states_per_step": grid.get("k"), "step_ns": grid.get("step_ns"),
               "excluded_books": len(grid.get("excluded") or ()),
               "window_steps": snap.get("window_steps"), "min_own_steps": snap.get("min_own_steps"),
               "radius_pooled_bps": snap.get("radius_pooled_bps"), "radius_own_p50_bps": snap.get("radius_own_p50_bps"),
               "books_with_steps": int(snap.get("books_with_steps", 0) or 0),
               "books_own_radius": int(snap.get("books_own_radius", 0) or 0),
               "steps_seen": int(snap.get("steps_seen", 0) or 0), "step_gaps": int(snap.get("step_gaps", 0) or 0),
               "record_breaks": int(snap.get("breaks", 0) or 0), "resets": int(snap.get("resets", 0) or 0),
               "phase": req.get("phase"), "block": req.get("block"), "plan_books": len(req.get("books") or ()),
               "pulls": {"cancelled": int(counts.get("cancelled_pull", 0) or 0),
                         "dropped": int(counts.get("dropped_pull", 0) or 0)},
               "holds": {"cancelled": int(counts.get("cancelled_hold", 0) or 0),
                         "dropped": int(counts.get("dropped_hold", 0) or 0)},
               "cap_conflicts": int(counts.get("cap_conflicts", 0) or 0),
               "cap_dropped": int(counts.get("cap_dropped", 0) or 0), "merged": int(counts.get("merged", 0) or 0),
               "cap_unresolved": int(counts.get("cap_unresolved", 0) or 0),
               "counts": counts, "errors": int(getattr(self, "_v6_36_errors", 0) or 0)}
        return row
