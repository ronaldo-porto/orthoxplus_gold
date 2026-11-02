"""v6.48 (USER 11-02 ~22:5x JST, reply to the coordinator's two Round 6 decisions: "1. Go  2. Live testnet A/B of the sweep
hold"): v6.47 as committed (94d2eb0) + two switches, code default 0 (v6.47 on every path), both on in PARAMS --

FPREC  research_v648_fprec      0 / 1: the observatory records each book's state fundamental (``fv`` = the wire's ``f``,
                                       verbatim; research_v648_fprec, installed in the recorder's shape at start);
SBH    research_v648_sbh        0 / 1 / 2: the Round 6 arm SBH (engine round6/vm_r6.py c6b13b83 ``r6sb``): a class-0 book's
                                       deep side (level 0 + ladder) that a taker print of this state swept >= 1 tick past
                                       the PREVIOUS state's touch is held for the rest of that 30-s block (2 = SBR: only
                                       while the side reduces the TRUE inventory);
       research_v648_sbh_scope  0 / 1 / 2: 0 = every class-0 book (the engine's form); 1 = the EVEN class-0 books only
                                       (0, 2, ..., 62); 2 = the class-0 books listed in research_v648_sbh_books (exactly
                                       32 distinct ids in 0..63: the coordinator's balanced split, PARAMS) -- every other
                                       class-0 book is the in-agent A/B control (v6.47 behaviour; its marks and would-be
                                       holds are counted, never acted on).
The rules are research_v648_rules' (SBH, switches) and research_v648_fprec's (the shape).

This mixin, first among Simple's bases, carries the agent side; Simple's body is unchanged (its import line and bases only):

* the marks: ``_v6_40_request`` (once per request in respond(), before any decision; V642Part's / V641Part's / V640Part's
  first) runs the sweep pass -- per class-0 book of the state its raw L1 (v6.36's l1_of: the wire's first levels, None for
  an empty side = the engine's R6PT), its trades (the request's one parse: v6.38's shared cache with the fast path on,
  which the books loop reads again; trade_of otherwise) against the previous pass's touch -> research_v648_rules.swept_sides,
  each swept side marked with this state's block (research_v648_rules.block_of = the engine's int(ts_s // 30)); a previous
  touch counts only when the previous pass took this book (else nothing is marked: the engine's missing touch); a clock that
  ran back starts the marks over.  The deep layer's own sweep record (DeepLayer.observe / sweep_depths) is not used: it
  keeps the depths without their side, and its previous touch skips a state whose touch the agent could not read;
* the hold: V647Part._v6_46_deep_book overridden (found first in the MRO) -- on a class-0 book (not a foreign one) in scope,
  each side whose mark is this request's block joins ``held`` for Simple's _v633_book beside R11SAG / S5AW (level 0 and
  the ladder: a resting order cancelled with v6.19's reason, none placed -- the touch quote is never held; no placement
  path of its own, no release); with SBH on and neither S5AW nor S5SA binding it, _v633_book is bound on the instance to it
  (v6.46's / v6.47's pattern);
* the V6_40_STATE row carries this part's snapshot under "v6_48" (_v6_40_snapshot override): the scope, the treated list
  and its digest (r7_split.py's), every brief counter split treated / control (``*_treated`` / ``*_control``: sb_marks,
  sb_evals, sb_held, sb_held_red / sb_held_add, sb_held_sole; sb_would_hold_control = the control's would-be holds;
  sb_held_control stays 0) and even / odd, the overlaps with the other deep holds on the same side (L4) and FPREC's states
  / books with fv / books without.
State: the marks and the previous touches are NOT persisted -- after a restart no side is held until a sweep is seen
against a touch this process saw (the engine's first state: no R6PT).  Every path never raises: an error counts
(_v6_48_errors, in the row) and the book runs v6.47's rule (no SBH hold).  No taking.  STRUCTURAL."""
from __future__ import annotations

import research_v648_fprec as v6_48_fprec
from research_v6_36_step_withdrawal import l1_of as v6_48_l1_of
from research_v6_38_fast_path import trades_of as v6_48_trades_of
from research_v6_40_classes import HOME_CLASS as V6_48_HOME
from research_v633_deep_layer import trade_of as v6_48_trade_of
from research_v63_trend_target import SIDE_BUY as V6_48_BUY, SIDE_SELL as V6_48_SELL
from research_v646_rules import aw_sides as v6_48_aw_sides
from research_v648_rules import BOOKS_KEY as V6_48_BOOKS_KEY
from research_v648_rules import SCOPE_LIST as V6_48_SCOPE_LIST
from research_v648_rules import SWITCHES as V6_48_SWITCHES
from research_v648_rules import V648_RULES_VERSION
from research_v648_rules import VALUES as V6_48_VALUES
from research_v648_rules import block_of as v6_48_block_of
from research_v648_rules import books_sha as v6_48_books_sha
from research_v648_rules import books_valid as v6_48_books_valid
from research_v648_rules import group_tag as v6_48_group_tag
from research_v648_rules import hold_decision as v6_48_hold_decision
from research_v648_rules import in_scope as v6_48_in_scope
from research_v648_rules import parity_tag as v6_48_parity_tag
from research_v648_rules import parse_books as v6_48_parse_books
from research_v648_rules import parse_value as v6_48_parse_value
from research_v648_rules import swept_sides as v6_48_swept_sides

V6_48_KEYS = tuple(name[len("research_v648_"):] for name in V6_48_SWITCHES)
V6_48_SIDES = (V6_48_BUY, V6_48_SELL)
# the brief's counters, present (0) from the first row so the control's held == 0 is explicit -- split treated / control
# (the A/B, whatever the scope) and even / odd
V6_48_COUNTERS = tuple(f"{k}_{t}" for k in ("sb_marks", "sb_evals", "sb_held", "sb_held_red", "sb_held_add",
                                            "sb_would_hold", "sb_held_sole")
                       for t in ("treated", "control", "even", "odd"))


class V648Part:
    """v6.48 (research_v648_*): FPREC in the observatory; SBH (the Round 6 sweep-block hold, book-scoped) on class 0's deep layer"""

    # ---- switches and counters -----------------------------------------------------------------------------------

    def _v6_48_count(self, key: str, n: int = 1) -> None:
        counts = getattr(self, "_v6_48_counts", None)
        if counts is None:
            counts = {k: 0 for k in V6_48_COUNTERS}
            self._v6_48_counts = counts
        counts[key] = int(counts.get(key, 0)) + int(n)

    def _v6_48_error(self) -> None:
        self._v6_48_errors = int(getattr(self, "_v6_48_errors", 0) or 0) + 1

    def _v6_48_cfg(self) -> dict:
        """Every v6.48 switch, read once from the config (code default 0).  An unreadable value runs off and counts an
        error; an unreadable SCOPE turns SBH off too (it fails open to v6.47: never a wider treated set).  SBH needs the
        deep layer (research_v633_deep_layer: the held path it joins); the scope only means something with SBH on.
        Scope 2 needs research_v648_sbh_books = exactly 32 distinct ids in 0..63, scope 0 / 1 none at all: either way a
        list that does not fit turns SBH off (an error counted; never a guessed treated set).  The treated list is kept
        as ``_v6_48_books`` (scope 2) and its digest as ``_v6_48_books_sha``."""
        st = getattr(self, "_v6_48_state", None)
        if st is not None:
            return st
        cfg = getattr(self, "config", None)
        self._v6_48_books, self._v6_48_books_sha = (), None
        st = {}
        bad = set()
        for name in V6_48_SWITCHES:
            key = name[len("research_v648_"):]
            v = v6_48_parse_value(getattr(cfg, name, 0), V6_48_VALUES[name])
            if v is None:
                self._v6_48_error()
                self._v6_48_count("unreadable_" + name)
                bad.add(key)
                v = 0
            st[key] = int(v)
        if "sbh_scope" in bad and st.get("sbh"):
            self._v6_48_count("refused_sbh_scope_unreadable")
            st["sbh"] = 0
        if st.get("sbh") and not bool(getattr(self, "research_v633_deep_layer", False)):
            self._v6_48_error()
            self._v6_48_count("refused_sbh")
            st["sbh"] = 0
        books = v6_48_parse_books(getattr(cfg, V6_48_BOOKS_KEY, None))
        if st.get("sbh") and st.get("sbh_scope") == V6_48_SCOPE_LIST:
            if v6_48_books_valid(books):
                self._v6_48_books, self._v6_48_books_sha = frozenset(books), v6_48_books_sha(books)
            else:
                self._v6_48_error()
                self._v6_48_count("refused_sbh_books")
                st["sbh"] = 0
        elif st.get("sbh") and books != ():
            self._v6_48_error()                 # scope 0 / 1 carry no list: an ambiguous treated set runs off
            self._v6_48_count("refused_sbh_books_unexpected")
            st["sbh"] = 0
        if st.get("sbh_scope") and not st.get("sbh"):
            self._v6_48_count("refused_sbh_scope")
            st["sbh_scope"] = 0
        self._v6_48_state = st
        return st

    # ---- init: the shape and the instance binding -----------------------------------------------------------------

    def _v6_40_init(self) -> None:
        """V647Part's init (and every older part's), then v6.48's switches: with FPREC on, the recorder's shape takes the
        ``fv`` keys (research_v648_fprec.install); with SBH on, Simple's _v633_book is bound on the instance to
        _v6_46_deep_book (this part's override) unless V646Part / V647Part bound it already.  Off: nothing.  Never
        raises past V647Part's."""
        super()._v6_40_init()
        try:
            self._v6_48_state = None
            self._v6_48_marks = {}          # book -> {side: block of its last >= 1-tick sweep}
            self._v6_48_prev = {}           # book -> (bid, ask, state ts) of the last pass that took it (the engine's R6PT)
            self._v6_48_pass_ts = None      # the state time of the last complete sweep pass
            self._v6_48_req = None          # this request's pass: {"ts": state ts, "block": its 30-s block}
            st = self._v6_48_cfg()
            if st["fprec"]:
                if v6_48_fprec.install():
                    self._v6_48_count("fprec_installed")
                else:
                    self._v6_48_error()
            if st["sbh"] and callable(getattr(type(self), "_v633_book", None)):
                if "_v633_book" not in getattr(self, "__dict__", {}):
                    self._v633_book = self._v6_46_deep_book
                self._v6_48_count("deep_bound")
        except Exception:
            self._v6_48_error()

    # ---- class 0: SBH, the marks (once per request, before any decision) ------------------------------------------------

    def _v6_40_request(self, state) -> None:
        """V642Part's request (V641Part's, V640Part's and their rows, unchanged), then SBH's sweep pass.  Never raises: an
        error counts and no side is held this request."""
        super()._v6_40_request(state)
        try:
            self._v6_48_req = None
            if self._v6_48_cfg()["sbh"]:
                self._v6_48_sweep_pass(state)
        except Exception:
            self._v6_48_req = None
            self._v6_48_error()

    def _v6_48_trades(self, book) -> list:
        """The book's trades as the books loop parses them: v6.38's shared parse (one per event list a request) with the
        fast path on, else the deep layer's trade_of per event."""
        events = getattr(book, "events", None)
        if bool(getattr(self, "research_v638_fast_path", False)):
            return v6_48_trades_of(events)
        return [t for t in (v6_48_trade_of(ev) for ev in (events or ())) if t is not None]

    def _v6_48_sweep_pass(self, state) -> None:
        """The engine's r6sb marks for this state: every class-0 book's prints against the previous state's touch, then
        this state's touch kept (both sides or None each).  Sets this request's pass (state time, block) last, so a pass
        that did not finish holds nothing."""
        now = int(getattr(state, "timestamp", 0) or 0)
        block = v6_48_block_of(now)
        books = getattr(state, "books", None)
        if books is None or block is None:
            self._v6_48_count("sb_no_state")
            return
        last = self._v6_48_pass_ts
        marks, prev = self._v6_48_marks, self._v6_48_prev
        if last is not None and now < last:
            marks.clear()                       # a clock that ran back: a new run, the marks and touches start over
            prev.clear()
            last = None
            self._v6_48_count("sb_clock_back")
        tick = 10.0 ** (-int(self._v61_price_decimals(state)))
        raw = getattr(books, "_raw_books", None)
        ids = list(raw.keys()) if isinstance(raw, dict) else list(books.keys())
        for raw_id in ids:
            try:
                b = int(raw_id)
                if self._v6_45_class_of(b) != V6_48_HOME:
                    continue
                book = books[raw_id]
                bid, ask = v6_48_l1_of(book)
                pt = prev.get(b)
                prev[b] = (bid, ask, now)
                if pt is None or pt[2] != last or pt[0] is None or pt[1] is None:
                    continue                    # no previous state's touch with both sides: nothing marked
                sides = v6_48_swept_sides(self._v6_48_trades(book), pt[0], pt[1], tick)
                if sides:
                    m = marks.setdefault(b, {})
                    tags = self._v6_48_tags(b)
                    for s_ in sides:
                        if m.get(s_) != block:
                            for tag in tags:
                                self._v6_48_count("sb_marks_" + tag)
                        m[s_] = block
            except Exception:
                self._v6_48_error()
        self._v6_48_pass_ts = now
        self._v6_48_req = {"ts": now, "block": block}

    # ---- class 0: SBH, the hold (the deep layer's held sides) -------------------------------------------------------

    def _v6_46_deep_book(self, response, book_id, deep, depth, raw_bid, raw_ask, inv, rows, deep_rows, already, req,
                         **kw) -> int:
        """SBH on a class-0 book (not a foreign one): the sides held this request join ``held`` for the deep layer (level
        0 and the ladder: a resting order cancelled, none placed -- the touch quote is not held), then V647Part's (R11SAG,
        then V646Part's S5AW, then Simple's _v633_book).  Never raises: an error counts and the call is V647Part's as is."""
        sides, pre = frozenset(), None
        try:
            if self._v6_48_cfg()["sbh"]:
                sides, pre = self._v6_48_sb(book_id, inv, kw)
                if sides:
                    kw["held"] = frozenset(kw.get("held") or ()) | sides
                    self._v6_48_count("sb_held_states")
        except Exception:
            sides, pre = frozenset(), None
            self._v6_48_error()
        out = super()._v6_46_deep_book(response, book_id, deep, depth, raw_bid, raw_ask, inv, rows, deep_rows, already,
                                       req, **kw)
        if sides and pre is not None:
            try:
                self._v6_48_overlap(book_id, sides, inv, pre)
            except Exception:
                self._v6_48_error()
        return out

    def _v6_48_tags(self, book_id: int) -> tuple:
        """(treated | control, even | odd) of a class-0 book: the counters' two splits."""
        st = self._v6_48_cfg()
        return (v6_48_group_tag(book_id, st["sbh_scope"], getattr(self, "_v6_48_books", ()) or ()), v6_48_parity_tag(book_id))

    def _v6_48_true_inv(self, book_id: int, inv) -> float:
        """The TRUE inventory: ``inv`` (inv - tau) + this request's tau (V646Part's store), as V647Part's R11SAR reads it."""
        got = (getattr(self, "_v6_46_taus", None) or {}).get(int(book_id))
        tau = float(got[0]) if got is not None and int(got[1]) == int(getattr(self, "_tick", 0) or 0) else 0.0
        return float(inv or 0.0) + tau

    def _v6_48_sb(self, book_id, inv, kw) -> tuple:
        """SBH's held sides of one class-0 book this request (research_v648_rules.hold_decision on this request's pass):
        (sides, the pre-call view for the overlap counters) -- empty outside class 0, on a foreign book, without this
        request's pass, without a mark in this block, or (scope 1) on an odd book (counted as the control's would-hold)."""
        b = int(book_id)
        if self._v6_45_class_of(b) != V6_48_HOME or b in (getattr(self, "_v6_23_foreign", None) or frozenset()):
            return frozenset(), None
        req = self._v6_48_req
        now = int(getattr(self, "_direct_current_state_timestamp_ns", 0) or 0)
        if not req or req.get("ts") != now:
            self._v6_48_count("sb_no_pass")
            return frozenset(), None
        m = self._v6_48_marks.get(b)
        if not m:
            return frozenset(), None
        st = self._v6_48_cfg()
        tags = self._v6_48_tags(b)
        treated = v6_48_in_scope(b, st["sbh_scope"], getattr(self, "_v6_48_books", ()) or ())
        true_inv = self._v6_48_true_inv(b, inv)
        held = set()
        for side in V6_48_SIDES:
            evaluated, hold, red = v6_48_hold_decision(m.get(side), req["block"], side, true_inv, st["sbh"])
            if not evaluated:
                continue
            for tag in tags:
                self._v6_48_count("sb_evals_" + tag)
            if not hold:
                continue
            if not treated:
                for tag in tags:
                    self._v6_48_count("sb_would_hold_" + tag)
                continue
            for tag in tags:
                self._v6_48_count("sb_held_" + tag)
                self._v6_48_count(("sb_held_red_" if red else "sb_held_add_") + tag)
            held.add(side)
        if not held:
            return frozenset(), None
        sa = dict(getattr(self, "_v6_47_counts", None) or {})
        pre = {"held": frozenset(kw.get("held") or ()), "against": frozenset(kw.get("against") or ()),
               "sa": {s_: int(sa.get("sa_held_" + s_, 0) or 0) for s_ in held}}
        return frozenset(held), pre

    def _v6_48_overlap(self, book_id, sides, inv, pre) -> None:
        """L4: which other deep hold took the same side this request (the decision is their union; each rule counts its
        own): v6.19's surplus hold (Simple's ``held``), v6.23 S1's level-0 against side, R11SAG (V647Part's own count
        moved in this call), S5AW (its rule on the same inputs); a side none of them held is SBH's alone (the engine's
        sb_held, whose check comes after k48 / S5AW / S5SA there)."""
        b = int(book_id)
        tags = self._v6_48_tags(b)
        sa_now = dict(getattr(self, "_v6_47_counts", None) or {})
        aw = frozenset()
        s46 = self._v6_46_cfg() if callable(getattr(self, "_v6_46_cfg", None)) else {}
        mirror = getattr(self, "_v62_mirror", None)
        if s46.get("s5aw") and mirror is not None:
            buy, sell = mirror.book_capture(b)
            aw = v6_48_aw_sides(inv, buy, sell)
        for side in sides:
            hits = [name for name, hit in (
                ("v619", side in pre["held"]),
                ("v623", (side, 0) in pre["against"]),
                ("sa", int(sa_now.get("sa_held_" + side, 0) or 0) > int(pre["sa"].get(side, 0))),
                ("aw", side in aw)) if hit]
            for name in hits:
                self._v6_48_count("sb_overlap_" + name)
            if not hits:
                for tag in tags:
                    self._v6_48_count("sb_held_sole_" + tag)

    # ---- the state row ---------------------------------------------------------------------------------------------

    def _v6_48_snapshot(self) -> dict:
        counts = {k: 0 for k in V6_48_COUNTERS}
        counts.update(getattr(self, "_v6_48_counts", None) or {})
        fp = dict(v6_48_fprec.COUNTS)
        req = getattr(self, "_v6_48_req", None) or {}
        block = req.get("block")
        marks = getattr(self, "_v6_48_marks", None) or {}
        st = self._v6_48_cfg()
        books = sorted(getattr(self, "_v6_48_books", ()) or ())
        return {"version": V648_RULES_VERSION, "fprec_version": v6_48_fprec.V648_FPREC_VERSION,
                "switches": dict(st), "scope": int(st["sbh_scope"]),
                "treated": ("all class 0" if st["sbh"] and st["sbh_scope"] == 0 else
                            "even class 0" if st["sbh"] and st["sbh_scope"] == 1 else books),
                "treated_n": len(books), "treated_sha": getattr(self, "_v6_48_books_sha", None),
                "fprec_installed": int(v6_48_fprec.installed()),
                "fprec_states": fp["states"], "fprec_books_fv": fp["books_fv"], "fprec_books_no_fv": fp["books_no_fv"],
                "fprec_books_fv_no_row": fp["books_fv_no_row"], "fprec_errors": fp["errors"],
                "sb_block": block,
                "sb_marked_now": sum(1 for m in marks.values() for v in m.values() if block is not None and v == block),
                "sb_books_seen": len(getattr(self, "_v6_48_prev", None) or {}),
                "bound": sorted(k for k in ("_v633_book",) if k in getattr(self, "__dict__", {})),
                "counts": counts, "errors": int(getattr(self, "_v6_48_errors", 0) or 0)}

    def _v6_40_snapshot(self) -> dict:
        """V647Part's snapshot plus this part's under "v6_48" (the V6_40_STATE row).  Never raises past V647Part's."""
        out = super()._v6_40_snapshot()
        try:
            out["v6_48"] = self._v6_48_snapshot()
        except Exception:
            self._v6_48_error()
        return out
