"""v6.32 fee-aware making (S14) and the state fundamental (P1) -- research_v6_32_fee_making / research_v6_32_state_fp

v6.39 S4 (k129): methods of Strategy1_Research_Simple moved here VERBATIM (simple_parts/join.py puts them back at their
lines: the joined text is byte-identical to the pre-split file).  A mixin: no state, no logic of its own; `self` is the agent.
"""
from __future__ import annotations

import time

from research_v623_validator_063 import validator_spec as v623_validator_spec
from research_v626_feed_clock import state_sim_id as v626_state_sim_id
from research_v62_breadth import V62_STATE_EVERY_TICKS
from research_v63_trend_target import SIDE_BUY as V63_SIDE_BUY, SIDE_SELL as V63_SIDE_SELL
from research_v6_32_fee_making import (
    RealizedTracker as V6_32RealizedTracker,
    V632_FEE_MAKING_VERSION as V6_32_FEE_MAKING_VERSION,
    adds as v6_32_adds,
    deep_pairs as v6_32_deep_pairs,
    fee_gated as v6_32_fee_gated,
    gate_active as v6_32_gate_active,
    maker_fee_rate as v6_32_maker_fee_rate,
    min_sizes as v6_32_min_sizes,
)
from research_v6_32_state_fp import (
    V632_STATE_FUNDAMENTAL_VERSION as V6_32_STATE_FUNDAMENTAL_VERSION,
    book_value as v6_32_book_value,
    fundamental_value as v6_32_fundamental_value,
    s9_snapshot as v6_32_s9_snapshot,
)


class V632Part:
    """v6.32 fee-aware making (S14) and the state fundamental (P1) -- research_v6_32_fee_making / research_v6_32_state_fp"""

    def _v6_32_request(self, state) -> None:
        """v6.32, in respond() once per request right after v6.23 read the state's release, before any decision.

        S14 (research_v632_fee_making): the gate's activity (a state from a 0.6.4 validator, a positive weight) is read
        first and handed to the realized tracker -- which counts our fills only while it is active and starts its window
        empty at each inactive -> active transition -- then the tracker takes every book's trade prints of the state in
        arrival order (the lazy raw mapping's wire dicts, nothing parsed) with the book's minimum order size and releases
        our maker fills that are ready; the request's pooled estimate is set for the books below.  A state clock that
        ran back starts the tracker's streams over.  P1 (research_v632_state_fundamental): the map {book: f} of the
        books whose own fundamental is a finite number > 0, with this state's time and the S9 snapshot built from it,
        for every reader below.  The time it takes is tallied.  Never raises: an error counts, no book is gated this
        request and every reader keeps the page."""
        started = time.perf_counter()
        fee_on = bool(getattr(self, "research_v632_fee_making", False))
        fp_on = bool(getattr(self, "research_v632_state_fundamental", False))
        self._v6_32_active, self._v6_32_book, self._v6_32_fp, self._v6_32_est = False, None, None, None
        req = {"gated_books": 0, "no_fee_rate_books": 0, "state_fp_books": 0}
        self._v6_32_req = req
        try:
            now = int(getattr(state, "timestamp", 0) or 0)
            cfg = getattr(state, "config", None)
            spec = v623_validator_spec(getattr(state, "version", None))
            self._v6_32_spec = spec
            books = getattr(state, "books", None)
            raw = getattr(books, "_raw_books", None)          # LazyBooks' wire dicts: no LazyBook built, nothing parsed
            items = list(raw.items()) if isinstance(raw, dict) else list((books or {}).items())
            tracker = None
            active = False
            top, per_book = 0.0, {}
            if fee_on:
                tracker = self._v6_32_tracker_ref()
                if getattr(self, "research_v640_s14_flush_spec", False):
                    self._v6_40_flush(tracker, spec)            # v6.40 B4(6): the flush of this state's release
                if tracker.begin(now):
                    self._v6_32_count("tracker_resets")     # a clock that ran back: the streams start over
                active = bool(v6_32_gate_active(fee_on, spec, getattr(self, "research_v632_fee_weight", 0.0)))
                if tracker.set_active(active, now):
                    self._v6_32_count("window_starts")      # inactive -> active: the window starts empty here
                top, per_book = self._v6_32_min_sizes(cfg)
            uid = getattr(self, "uid", None)
            fps: dict[int, float] = {}
            v6_34_route = getattr(self, "_v6_34_route", None) or frozenset()   # v6.34: class 1's books (own estimate)
            for raw_id, book in items:
                book_id = int(raw_id)
                rb = book if isinstance(book, dict) else getattr(book, "_raw", None)
                if fp_on:
                    f = v6_32_fundamental_value(rb if isinstance(rb, dict) else book)
                    if f is not None:
                        fps[book_id] = f
                if v6_34_route and book_id in v6_34_route:
                    continue        # v6.34: class 0 keeps its own pooled estimate (research_v634_class1_entry)
                if tracker is not None:
                    if isinstance(rb, dict):
                        if getattr(self, "research_v641_speed", False):
                            # v6.41 D: the raw trade dicts the P5 pass recorded for this very list (None: not recorded)
                            prints = self._v6_41_raw_trades(rb.get("e"))
                            if prints is None:
                                prints = [e for e in (rb.get("e") or ()) if isinstance(e, dict) and e.get("y") == "t"]
                        else:
                            prints = [e for e in (rb.get("e") or ()) if isinstance(e, dict) and e.get("y") == "t"]
                    else:
                        prints = [e for e in (getattr(book, "events", None) or ())
                                  if (e.get("y") if isinstance(e, dict) else getattr(e, "y", None)) == "t"]
                    if prints:
                        tracker.observe(book_id, prints, uid, per_book.get(book_id, top), now)
            if tracker is not None:
                tracker.release(now)
                self._v6_32_est = tracker.estimate()
                self._v6_32_active = active
                if active:
                    self._v6_32_count("active_requests")
            if fp_on:
                self._v6_32_fp = (now, fps, v6_32_s9_snapshot(fps, v626_state_sim_id(cfg), now))
                req["state_fp_books"] = len(fps)
                self._v6_32_count("state_fp_requests" if fps else "no_state_fp_requests")
        except Exception:
            self._v6_32_errors = int(getattr(self, "_v6_32_errors", 0) or 0) + 1
            self._v6_32_active, self._v6_32_book, self._v6_32_fp, self._v6_32_est = False, None, None, None
        try:
            samples = getattr(self, "_v6_32_ms", None)
            if samples is None:
                samples = []
                self._v6_32_ms = samples
            samples.append((time.perf_counter() - started) * 1000.0)
            if len(samples) > 1024:
                del samples[:-512]                      # the last 512 requests' times, for the row's p50 / p99
        except Exception:
            self._v6_32_errors = int(getattr(self, "_v6_32_errors", 0) or 0) + 1

    def _v6_32_tracker_ref(self):
        """v6.32 S14: the realized tracker, made on first use."""
        tracker = getattr(self, "_v6_32_tracker", None)
        if not isinstance(tracker, V6_32RealizedTracker):
            tracker = V6_32RealizedTracker()
            self._v6_32_tracker = tracker
        return tracker

    def _v6_32_min_sizes(self, cfg):
        """v6.32 S14: (the top-level minimum order size, {book: its class's}) -- the dust floor's voting bar the validator
        reads per book -- read once per simulation."""
        key = v626_state_sim_id(cfg)
        if getattr(self, "research_v640_s14_class_key", False):
            key = self._v6_40_s14_key(cfg, key)          # v6.40 B4(5): + the class list's digest
        cached = getattr(self, "_v6_32_mins", None)
        if cached is not None and key and cached[0] == key:
            return cached[1], cached[2]
        top, per_book = v6_32_min_sizes(cfg)
        self._v6_32_mins = (key, top, per_book)
        return top, per_book

    def _v6_32_note(self, book_id: int, inv) -> None:
        """v6.32 S14, in the pass right after the book's ledger rows are read: while the gate is active this request,
        the sides that hold no adding order on this book -- when the pooled estimate (our realized spread per notional
        over the window, MIN_FILLS fills at least) does not exceed W x the book's current maker fee rate (its account's,
        a fraction), every side whose fill would not reduce the book's TRUE inventory ``inv`` (v6.23 S1's adds: a flat
        book both sides), for the deep layer and the v6.17 touch quote alike.  No rate: no gate (counted).  Never
        raises: an error counts and the book has no gate."""
        self._v6_32_book = None
        try:
            if not bool(getattr(self, "_v6_32_active", False)):
                return
            est = getattr(self, "_v6_32_est", None)
            if est is None:
                return                                  # fewer than MIN_FILLS fills released yet: nothing can gate
            req = getattr(self, "_v6_32_req", None)
            if req is None:
                req = {}
                self._v6_32_req = req
            rate = v6_32_maker_fee_rate((getattr(self, "accounts", None) or {}).get(int(book_id)))
            if rate is None:
                req["no_fee_rate_books"] = int(req.get("no_fee_rate_books", 0) or 0) + 1
                return
            if not v6_32_fee_gated(est, rate, getattr(self, "research_v632_fee_weight", 0.0)):
                return
            sides = frozenset(s for s in (V63_SIDE_BUY, V63_SIDE_SELL) if v6_32_adds(s, inv))
            req["gated_books"] = int(req.get("gated_books", 0) or 0) + 1
            self._v6_32_book = (int(book_id), int(getattr(self, "_tick", 0) or 0), sides)
        except Exception:
            self._v6_32_errors = int(getattr(self, "_v6_32_errors", 0) or 0) + 1
            self._v6_32_book = None

    def _v6_32_deep_pairs(self, book_id: int) -> frozenset:
        """v6.32 S14, in the deep layer: the (side, level) pairs _v6_32_note withheld on this book this request -- every
        level of each gated adding side (v6.23 S1's ``deep`` shape) -- else none.  Never raises: an error counts, none."""
        try:
            got = getattr(self, "_v6_32_book", None)
            if got is None or got[0] != int(book_id) or got[1] != int(getattr(self, "_tick", 0) or 0):
                return frozenset()
            return v6_32_deep_pairs(got[2])
        except Exception:
            self._v6_32_errors = int(getattr(self, "_v6_32_errors", 0) or 0) + 1
            return frozenset()

    def _v6_32_touch_sides(self, book_id: int) -> frozenset:
        """v6.32 S14, in the v6.17 touch quote: the adding side(s) _v6_32_note withheld on this book this request, else
        none.  Never raises: an error counts, none."""
        try:
            got = getattr(self, "_v6_32_book", None)
            if got is None or got[0] != int(book_id) or got[1] != int(getattr(self, "_tick", 0) or 0):
                return frozenset()
            return frozenset(got[2])
        except Exception:
            self._v6_32_errors = int(getattr(self, "_v6_32_errors", 0) or 0) + 1
            return frozenset()

    def _v6_32_state_fp(self, book_id: int, raw_bid, raw_ask):
        """v6.32 P1: (fundamental, "state") of this book from this request's state map, after v6.16's book sanity -- or
        None, and the reader takes the page: no map this request (a state without fundamentals, or the map of another
        request), no usable value for the book, or a book that is not sane.  Never raises: an error counts, None."""
        try:
            got = getattr(self, "_v6_32_fp", None)
            if got is None or not got[1] or got[0] != int(getattr(self, "_direct_current_state_timestamp_ns", 0) or 0):
                return None
            out = v6_32_book_value(got[1], book_id, raw_bid, raw_ask)
            if out is None:
                self._v6_32_count("fp_page_fallbacks")
            return out
        except Exception:
            self._v6_32_errors = int(getattr(self, "_v6_32_errors", 0) or 0) + 1
            return None

    def _v6_32_fundamental(self, book_id: int, raw_bid, raw_ask, fp, reason):
        """v6.32 P1, in v6.16's centre and the v6.17 touch quote right after the page's (fp, reason): the state's own
        (fundamental, "state") when the request's map has a usable one for the book, else the page's unchanged."""
        got = self._v6_32_state_fp(book_id, raw_bid, raw_ask)
        return (fp, reason) if got is None else got

    def _v6_32_s9_snapshot(self):
        """v6.32 P1, in S9's decision: this request's snapshot of the state's own fundamentals for S9's page history
        ({fp, sim_id, sim_ts_ns}, built once per request), or None -- the page's snapshot, as v6.31.  Never raises."""
        try:
            got = getattr(self, "_v6_32_fp", None)
            if got is None or got[0] != int(getattr(self, "_direct_current_state_timestamp_ns", 0) or 0):
                return None
            return got[2]
        except Exception:
            self._v6_32_errors = int(getattr(self, "_v6_32_errors", 0) or 0) + 1
            return None

    def _v6_32_count(self, key: str, n: int = 1) -> None:
        """v6.32's tally.  Never raises: an error counts."""
        try:
            counts = getattr(self, "_v6_32_counts", None)
            if counts is None:
                counts = {}
                self._v6_32_counts = counts
            counts[key] = int(counts.get(key, 0) or 0) + int(n)
        except Exception:
            self._v6_32_errors = int(getattr(self, "_v6_32_errors", 0) or 0) + 1

    def _v6_32_report(self) -> None:
        """v6.32: the V6_32_STATE row -- both switches and versions, the gate's activity, spec and weight, the pooled
        estimate (bps) and its window (its start, the gate's transitions, the fills counted / dropped while inactive),
        the tracker's tally (prints, our fills, pending, released, stale, evicted) and time, the request's gated books,
        the withheld / cancelled orders, P1's state-fundamental tally and the errors --
        at the first pass, at the first request with an error after one without, and every V62_STATE_EVERY_TICKS ticks;
        after the reply when the request defers its telemetry (v6.2.9).  Never raises."""
        try:
            tick = int(getattr(self, "_tick", 0) or 0)
            errors = int(getattr(self, "_v6_32_errors", 0) or 0)
            erring = errors > int(getattr(self, "_v6_32_errors_seen", 0) or 0)
            was_erring = bool(getattr(self, "_v6_32_erring", False))
            self._v6_32_errors_seen, self._v6_32_erring = errors, erring
            if not getattr(self, "_v6_32_reported", False):
                reason = "start"
            elif erring and not was_erring:
                reason = "error"
            elif tick > 0 and tick % V62_STATE_EVERY_TICKS == 0:
                reason = "periodic"
            else:
                return
            self._v6_32_reported = True
            row = dict(self._v6_32_snapshot(), reason=reason, tick=tick)
            work = getattr(self, "_v629_request_work", None)
            if work is not None:
                work.add("v6_32_state_row", lambda: self._emit("V6_32_STATE", force=True, **row))   # after the reply
            else:
                self._emit("V6_32_STATE", force=True, **row)
        except Exception:
            self._v6_32_errors = int(getattr(self, "_v6_32_errors", 0) or 0) + 1

    def _v6_32_snapshot(self) -> dict:
        counts = dict(getattr(self, "_v6_32_counts", None) or {})
        req = dict(getattr(self, "_v6_32_req", None) or {})
        tracker = getattr(self, "_v6_32_tracker", None)
        snap = tracker.snapshot() if tracker is not None else {}
        ms = sorted(float(x) for x in (getattr(self, "_v6_32_ms", None) or ()))
        row = {"version": V6_32_FEE_MAKING_VERSION, "state_fp_version": V6_32_STATE_FUNDAMENTAL_VERSION,
               "fee_making_on": int(bool(getattr(self, "research_v632_fee_making", False))),
               "state_fundamental_on": int(bool(getattr(self, "research_v632_state_fundamental", False))),
               "active": int(bool(getattr(self, "_v6_32_active", False))), "spec": getattr(self, "_v6_32_spec", None),
               "fee_weight": float(getattr(self, "research_v632_fee_weight", 0.0) or 0.0),
               "estimate_bps": snap.get("estimate_bps"), "window_start_ns": snap.get("window_start_ns")}
        for key in ("window_fills", "activations", "deactivations", "counted", "dropped_inactive", "pending", "prints",
                    "own_fills", "released", "stale", "evicted", "malformed"):
            row[key] = int(snap.get(key, 0) or 0)
        for key in ("gated_books", "no_fee_rate_books", "state_fp_books"):
            row[key] = int(req.get(key, 0) or 0)
        for key in ("active_requests", "window_starts", "deep_cancels", "deep_withheld", "touch_cancels",
                    "touch_withheld", "tracker_resets", "state_fp_requests", "no_state_fp_requests", "fp_page_fallbacks"):
            row[key] = int(counts.get(key, 0) or 0)
        row["tracker_ms_p50"] = round(ms[len(ms) // 2], 3) if ms else None
        row["tracker_ms_p99"] = round(ms[min(len(ms) - 1, int(0.99 * len(ms)))], 3) if ms else None
        row["errors"] = int(getattr(self, "_v6_32_errors", 0) or 0)
        return row
