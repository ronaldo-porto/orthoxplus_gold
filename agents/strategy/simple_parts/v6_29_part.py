"""v6.29 touch on the fp move (S9), the first save and the rewind guard -- research_v6_29_touch_move

v6.39 S4 (k129): methods of Strategy1_Research_Simple moved here VERBATIM (simple_parts/join.py puts them back at their
lines: the joined text is byte-identical to the pre-split file).  A mixin: no state, no logic of its own; `self` is the agent.
"""
from __future__ import annotations

from research_v626_feed_clock import state_sim_id as v626_state_sim_id
from research_v628_flat_launch import FlatLaunchLine as V628FlatLaunchLine
from research_v62_breadth import V62_STATE_EVERY_TICKS
from research_v6_29_touch_move import (
    PageHistory as V6_29PageHistory,
    V6_29_TOUCH_MOVE_VERSION,
    launch_records as v6_29_launch_records,
    lookback_sim_s as v6_29_lookback,
)


class V629Part:
    """v6.29 touch on the fp move (S9), the first save and the rewind guard -- research_v6_29_touch_move"""

    def _v6_29_held(self, book_id: int, fp, cfg):
        """v6.29 S9 (research_v629_touch_fp_move), in the touch quote right after v6.17 read the book's fundamental
        ``fp`` (None: its page is stale or missing): the side whose touch quote the fundamental's move over the
        look-back is against -- BUY when it fell, SELL when it rose -- or None (no gate).  The move is ``fp`` minus the
        book's fundamental on the page that was current W sim-s ago: the newest page this agent first saw at or before
        this request's state time minus W, from the page history the feed's snapshot feeds here (V6_29PageHistory:
        distinct pages by their own sim time, only what W needs).  W is this request's S6 limit (v6.16's
        MAX_STALE_SIM_S without one).  Tallies the decision and why there is no gate (no page that old, no fresh
        fundamental, no move).  Never raises: an error counts and the book has no gate (v6.28)."""
        try:
            self._v6_29_count("touch_decisions")
            pages = getattr(self, "_v6_29_pages", None)
            if pages is None:
                pages = V6_29PageHistory()
                self._v6_29_pages = pages
            now_ts = int(getattr(self, "_direct_current_state_timestamp_ns", 0) or 0)
            feed = getattr(self, "_v616_feed", None)
            v6_32_snap = None
            if bool(getattr(self, "research_v632_state_fundamental", False)):
                # v6.32 P1: with the books' own fundamentals in this state the history records those -- one entry per
                # request, at the state's time -- instead of the page (research_v632_state_fundamental)
                v6_32_snap = self._v6_32_s9_snapshot()
            if v6_32_snap is not None:
                pages.observe(v6_32_snap, now_ts, v626_state_sim_id(cfg))
            else:
                pages.observe(feed.snapshot() if feed is not None else None, now_ts, v626_state_sim_id(cfg))
            lookback = v6_29_lookback(getattr(self, "_v6_26_limit", None))
            self._v6_29_lookback = lookback
            v6_30_decision = None
            if bool(getattr(self, "research_v630_s9_lookback", False)):
                # v6.30 D2: this book's look-back is m x W, m its multiple in this UID's identity profile, by v6.29's
                # reference rule (None: no profile this request -- W, v6.29's decision) (research_v630_s9_lookback)
                v6_30_decision = self._v6_30_decide(pages, book_id, fp, now_ts, lookback)
            if v6_30_decision is not None:
                side, why = v6_30_decision
            else:
                side, why = pages.decide(book_id, fp, now_ts, lookback)
            self._v6_29_count(why)
            return side
        except Exception:
            self._v6_29_errors = int(getattr(self, "_v6_29_errors", 0) or 0) + 1
            return None

    def _v6_29_count(self, key: str, n: int = 1) -> None:
        """v6.29's tally.  Never raises: an error counts."""
        try:
            counts = getattr(self, "_v6_29_counts", None)
            if counts is None:
                counts = {}
                self._v6_29_counts = counts
            counts[key] = int(counts.get(key, 0) or 0) + int(n)
        except Exception:
            self._v6_29_errors = int(getattr(self, "_v6_29_errors", 0) or 0) + 1

    def _v6_29_launches(self, pace):
        """v6.29, S8's first save (research_v628_flat_launch), in the pass before its books are seen: the launch records
        S8's line holds (v6_29_launch_records), or None without S8's line.  The process's first pass keeps that number
        as the records already accounted for -- a restart's, restored from the session file that carries them.  Never
        raises: an error counts and is None (no save is asked for: v6.28's cadence)."""
        try:
            if not isinstance(pace, V628FlatLaunchLine):
                return None
            n = v6_29_launch_records(pace)
            if n is not None and getattr(self, "_v6_29_launches_seen", None) is None:
                self._v6_29_launches_seen = n
            return n
        except Exception:
            self._v6_29_errors = int(getattr(self, "_v6_29_errors", 0) or 0) + 1
            return None

    def _v6_29_first_launch(self, pace) -> None:
        """v6.29, S8's first save, in the pass after its books: more launch records than at the last look are launches
        S8 recorded -- flat, each a book first seen on an empty window -- and the first time there are, one session save
        is asked for (_v6_29_save_due): the request's own save path (_research_save_session, after the pass) forces it,
        then the normal cadence (research_session_save_every_n) runs on from it.  k72: a save earlier in the request
        (the session sync's on a reset, v5.0.3's gate as it opens) carries the previous tick -- respond counts the
        request's own -- so the save path after the pass skipped and the first save carrying a fresh UID's flat rates
        came a whole cadence later (~100 requests, ~8 wall-min); a restart in between rebuilt the launch at v6.15's 2x.
        Never raises: an error counts and no save is asked for (v6.28)."""
        try:
            n = v6_29_launch_records(pace) if isinstance(pace, V628FlatLaunchLine) else None
            seen = getattr(self, "_v6_29_launches_seen", None)
            if n is None or seen is None:
                return
            if n > seen and getattr(self, "_v6_29_save_asked", None) is None:
                tick = int(getattr(self, "_tick", 0) or 0)
                self._v6_29_save_due, self._v6_29_save_asked = True, tick
            self._v6_29_launches_seen = n
        except Exception:
            self._v6_29_errors = int(getattr(self, "_v6_29_errors", 0) or 0) + 1

    def _v6_29_saved(self) -> None:
        """v6.29, S8's first save: the session file was written while the save S8's first flat launch asked for was due
        -- it carries the line's rates now, so that save is done.  Never raises."""
        try:
            tick = int(getattr(self, "_tick", 0) or 0)
            self._v6_29_save_due, self._v6_29_save_done = False, tick
        except Exception:
            self._v6_29_errors = int(getattr(self, "_v6_29_errors", 0) or 0) + 1

    def _v6_29_report(self) -> None:
        """v6.29: the V6_29_STATE row -- S9's switch, the look-back it used last and the pages it keeps, the running
        tally of touch decisions, those the fundamental's move armed (it fell / rose), placements withheld and resting
        quotes cancelled, and the decisions without a gate (no page that old, no fresh fundamental, no move); S8's
        first save (asked for / done at which tick); the errors -- at the first pass, at the first request with an
        error after one without, and every V62_STATE_EVERY_TICKS ticks; after the reply when the request defers its
        telemetry (v6.2.9).  Never raises."""
        try:
            tick = int(getattr(self, "_tick", 0) or 0)
            errors = int(getattr(self, "_v6_29_errors", 0) or 0)
            erring = errors > int(getattr(self, "_v6_29_errors_seen", 0) or 0)
            was_erring = bool(getattr(self, "_v6_29_erring", False))
            self._v6_29_errors_seen, self._v6_29_erring = errors, erring
            if not getattr(self, "_v6_29_reported", False):
                reason = "start"
            elif erring and not was_erring:
                reason = "error"
            elif tick > 0 and tick % V62_STATE_EVERY_TICKS == 0:
                reason = "periodic"
            else:
                return
            self._v6_29_reported = True
            row = dict(self._v6_29_snapshot(), reason=reason, tick=tick)
            work = getattr(self, "_v629_request_work", None)
            if work is not None:
                work.add("v6_29_state_row", lambda: self._emit("V6_29_STATE", force=True, **row))   # after the reply
            else:
                self._emit("V6_29_STATE", force=True, **row)
        except Exception:
            self._v6_29_errors = int(getattr(self, "_v6_29_errors", 0) or 0) + 1

    def _v6_29_snapshot(self) -> dict:
        counts = dict(getattr(self, "_v6_29_counts", None) or {})
        pages = getattr(self, "_v6_29_pages", None)
        lookback = getattr(self, "_v6_29_lookback", None)
        return {"version": V6_29_TOUCH_MOVE_VERSION,
                "touch_fp_move_on": int(bool(getattr(self, "research_v629_touch_fp_move", False))),
                "lookback_sim_s": (round(float(lookback), 3) if lookback is not None else None),
                "pages": len(getattr(pages, "pages", None) or ()), "page_resets": int(getattr(pages, "resets", 0) or 0),
                "touch_decisions": int(counts.get("touch_decisions", 0) or 0),
                "fp_fell": int(counts.get("fp_fell", 0) or 0), "fp_rose": int(counts.get("fp_rose", 0) or 0),
                "withheld": int(counts.get("withheld", 0) or 0), "cancelled": int(counts.get("cancelled", 0) or 0),
                "no_history": int(counts.get("no_history", 0) or 0), "stale": int(counts.get("stale", 0) or 0),
                "zero_move": int(counts.get("zero_move", 0) or 0),
                "s8_save_due": int(bool(getattr(self, "_v6_29_save_due", False))),
                "s8_save_asked": getattr(self, "_v6_29_save_asked", None),
                "s8_save_done": getattr(self, "_v6_29_save_done", None),
                "errors": int(getattr(self, "_v6_29_errors", 0) or 0)}
