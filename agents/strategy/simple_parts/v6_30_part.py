"""v6.30 identity profile (D1 deep tier, D2 S9 look-back) -- research_v630_identity_profile

v6.39 S4 (k129): methods of Strategy1_Research_Simple moved here VERBATIM (simple_parts/join.py puts them back at their
lines: the joined text is byte-identical to the pre-split file).  A mixin: no state, no logic of its own; `self` is the agent.
"""
from __future__ import annotations

from research_v62_breadth import V62_STATE_EVERY_TICKS
from research_v630_identity_profile import (
    CLAMPED_BAND as V6_30_CLAMPED_BAND,
    CLAMPED_TOUCH as V6_30_CLAMPED_TOUCH,
    D1 as V6_30_D1,
    D2 as V6_30_D2,
    DEEPER as V6_30_DEEPER,
    KeepHorizon as V6_30KeepHorizon,
    PROFILE_VERSION as V6_30_PROFILE_VERSION,
    V6_30_IDENTITY_PROFILE_VERSION,
    addressee as v6_30_addressee,
    deeper_target as v6_30_deeper_target,
    paper_for as v6_30_paper_for,
)
from research_v6_29_touch_move import lookback_sim_s as v6_29_lookback


class V630Part:
    """v6.30 identity profile (D1 deep tier, D2 S9 look-back) -- research_v630_identity_profile"""

    def _v6_30_request(self, state) -> None:
        """v6.30, in the pass once per request before its books: is the identity profile on for this request?  The UID
        this process fixed (at init from self.uid; else here from self.uid, else the state's addressee) must still be
        self.uid and the addressee -- unknown, negative or another UID turns the profile off for the rest of the process
        (D1 and D2 then run v6.29; the state row says why).  On, every book of the state has its draw (drawn once, from
        this UID alone), and with D2 on S9's history holds what the longest look-back in use needs: the largest
        multiple x W, never shrinking while the profile is on (KeepHorizon.hold); once the profile is off, None (v6.29's
        prune).  Never raises: an error counts, the request runs v6.29 and the history's horizon is left as it was."""
        try:
            ident = self._v6_30_identity
            on = bool(ident.check(getattr(self, "uid", None), v6_30_addressee(getattr(state, "accounts", None))))
            keep = None
            if on:
                ident.extend(getattr(state, "books", None) or ())
                if bool(getattr(self, "research_v630_s9_lookback", False)):
                    keep = float(ident.max_m) * float(v6_29_lookback(getattr(self, "_v6_26_limit", None)))
            pages = getattr(self, "_v6_29_pages", None)
            if isinstance(pages, V6_30KeepHorizon):
                if ident.off is not None:
                    pages.keep_s = None                     # the profile is off for good: v6.29's prune
                elif keep is not None:
                    pages.hold(keep)                        # never shrinks while the profile is on
            self._v6_30_on = on
        except Exception:
            self._v6_30_errors = int(getattr(self, "_v6_30_errors", 0) or 0) + 1
            self._v6_30_on = False                          # this request runs v6.29; the horizon stays as it was

    def _v6_30_tier(self, book_id: int) -> int:
        """v6.30 D1 (research_v630_deep_tier): the book's deep tier this request -- its draw in the identity profile while
        the profile is on (1: one venue tick deeper), else 0 (v6.29's prices).  Never raises: an error counts and is 0."""
        try:
            if not bool(getattr(self, "_v6_30_on", False)):
                return 0
            return 1 if self._v6_30_identity.value(V6_30_D1, book_id) == 1 else 0
        except Exception:
            self._v6_30_errors = int(getattr(self, "_v6_30_errors", 0) or 0) + 1
            return 0

    def _v6_30_deeper(self, price, side: str, ticks: int, ref, depth, vacuum: bool, raw_bid, raw_ask, tick_size, dec):
        """v6.30 D1: (target, why) -- ``price``, a deep level's v6.29 target, ``ticks`` venue ticks further from the mid
        (a buy lower, a sell higher) when that stays inside v6.3.3's keep band at ``depth`` around ``ref`` (the reference
        the resting order is judged against), off its edge, and, for a ``vacuum`` order, strictly inside the touch;
        else ``price`` with why (v6_30_deeper_target).  Never raises: an error counts and is (``price``, "error")."""
        try:
            return v6_30_deeper_target(price, side, ref=ref, depth=depth, tick=tick_size, decimals=dec, ticks=ticks,
                                       inside=(raw_bid, raw_ask) if vacuum else None)
        except Exception:
            self._v6_30_errors = int(getattr(self, "_v6_30_errors", 0) or 0) + 1
            return price, "error"

    def _v6_30_placed(self, level: int, why) -> None:
        """v6.30 D1's tally, by level: a deep order of a tier-1 book placed one tick deeper, at v6.29's price because the
        band (or its edge) or the touch clamped it, or at v6.29's price because an input was unreadable or an error
        fell back.  Never raises."""
        try:
            kind = "deeper" if why == V6_30_DEEPER else "clamped" if why in (V6_30_CLAMPED_BAND, V6_30_CLAMPED_TOUCH) \
                else "error"
            self._v6_30_count("%s_l%d" % (kind, int(level)))
        except Exception:
            self._v6_30_errors = int(getattr(self, "_v6_30_errors", 0) or 0) + 1

    def _v6_30_paper(self, deep, book_id: int) -> None:
        """v6.30 D1, in the pass right before the deep layer observes the book: the book's paper record is the kind its
        tier rests -- a TierPaper, its paper orders one tick deeper, on tier 1; v6.3.3's PaperDeep otherwise; the same
        record either way (v6_30_paper_for) -- so v6.8's per-book gate judges the level the book trades.  A book the
        layer has not seen yet places no paper order on its first state (it has no depth), so it is the right kind
        before its first paper order.  Never raises: an error counts and the record stays as it is."""
        try:
            db = (getattr(deep, "books", None) or {}).get(int(book_id))
            if db is None:
                return
            ticks = self._v6_30_tier(book_id)
            paper = v6_30_paper_for(db.paper, ticks)
            if paper is not db.paper:
                db.paper = paper
                self._v6_30_count("paper_tier1" if ticks else "paper_tier0")
        except Exception:
            self._v6_30_errors = int(getattr(self, "_v6_30_errors", 0) or 0) + 1

    def _v6_30_decide(self, pages, book_id: int, fp, now_ts: int, lookback):
        """v6.30 D2 (research_v630_s9_lookback), inside v6.29's S9 decision: (side, why) of v6.29's rule on its history
        with this book's look-back, m x ``lookback`` (exactly v6.29's W), m the book's multiple in the identity profile;
        None while the profile is off this request, for a book without a draw, or a history without KeepHorizon (v6.29's
        decision at W then).  Tallies the decisions, the gates and why there is none, per m.  Never raises: an error
        counts and is None."""
        try:
            if not bool(getattr(self, "_v6_30_on", False)) or not isinstance(pages, V6_30KeepHorizon):
                return None
            m = self._v6_30_identity.value(V6_30_D2, book_id)
            if m is None:
                return None
            side, why = pages.decide(book_id, fp, now_ts, float(m) * float(lookback))
            self._v6_30_count("s9_m%d_decisions" % m)
            self._v6_30_count("s9_m%d_%s" % (m, "gated" if side is not None else why))
            return side, why
        except Exception:
            self._v6_30_errors = int(getattr(self, "_v6_30_errors", 0) or 0) + 1
            return None

    def _v6_30_count(self, key: str, n: int = 1) -> None:
        """v6.30's tally.  Never raises: an error counts."""
        try:
            counts = getattr(self, "_v6_30_counts", None)
            if counts is None:
                counts = {}
                self._v6_30_counts = counts
            counts[key] = int(counts.get(key, 0) or 0) + int(n)
        except Exception:
            self._v6_30_errors = int(getattr(self, "_v6_30_errors", 0) or 0) + 1

    def _v6_30_report(self) -> None:
        """v6.30: the V6_30_STATE row -- both switches, whether the profile is on, the UID fixed for this process and
        where it came from, why the profile is off when it is, the table (its books, sha256, D1's tier-1 and D2's m = 2
        counts, the draws), D1's placements one tick deeper / clamped by level and the paper records converted, S9's
        decisions, gates and missing references by m and the history's keep horizon, and the errors -- at the first
        pass, at the first request with an error after one without, at the request the profile turned off or the table
        grew, and every V62_STATE_EVERY_TICKS ticks; after the reply when the request defers its telemetry (v6.2.9).
        Never raises."""
        try:
            tick = int(getattr(self, "_tick", 0) or 0)
            errors = int(getattr(self, "_v6_30_errors", 0) or 0)
            erring = errors > int(getattr(self, "_v6_30_errors_seen", 0) or 0)
            was_erring = bool(getattr(self, "_v6_30_erring", False))
            self._v6_30_errors_seen, self._v6_30_erring = errors, erring
            ident = getattr(self, "_v6_30_identity", None)
            off = getattr(ident, "off", None)
            drawn = len(getattr(ident, "table", None) or {})
            if not getattr(self, "_v6_30_reported", False):
                reason = "start"
            elif erring and not was_erring:
                reason = "error"
            elif off is not None and off != getattr(self, "_v6_30_off_logged", None):
                reason = "off"
            elif drawn != int(getattr(self, "_v6_30_drawn_seen", 0) or 0):
                reason = "table"
            elif tick > 0 and tick % V62_STATE_EVERY_TICKS == 0:
                reason = "periodic"
            else:
                return
            self._v6_30_reported = True
            self._v6_30_off_logged, self._v6_30_drawn_seen = off, drawn
            row = dict(self._v6_30_snapshot(), reason=reason, tick=tick)
            work = getattr(self, "_v629_request_work", None)
            if work is not None:
                work.add("v6_30_state_row", lambda: self._emit("V6_30_STATE", force=True, **row))   # after the reply
            else:
                self._emit("V6_30_STATE", force=True, **row)
        except Exception:
            self._v6_30_errors = int(getattr(self, "_v6_30_errors", 0) or 0) + 1

    def _v6_30_snapshot(self) -> dict:
        ident = getattr(self, "_v6_30_identity", None)
        counts = dict(getattr(self, "_v6_30_counts", None) or {})
        pages = getattr(self, "_v6_29_pages", None)
        keep = getattr(pages, "keep_s", None)
        row = {"version": V6_30_IDENTITY_PROFILE_VERSION, "profile": V6_30_PROFILE_VERSION,
               "deep_tier_on": int(bool(getattr(self, "research_v630_deep_tier", False))),
               "s9_lookback_on": int(bool(getattr(self, "research_v630_s9_lookback", False))),
               "profile_on": int(bool(getattr(self, "_v6_30_on", False)))}
        row.update(ident.snapshot() if ident is not None else {})
        row.update({
            "deep_shifted": sum(int(v or 0) for k, v in counts.items() if k.startswith("deeper_l")),
            "deep_clamped": sum(int(v or 0) for k, v in counts.items() if k.startswith("clamped_l")),
            "deep_unchanged_error": sum(int(v or 0) for k, v in counts.items() if k.startswith("error_l")),
            "deep_by_level": {k: int(v or 0) for k, v in sorted(counts.items())
                              if k.startswith(("deeper_l", "clamped_l", "error_l"))},
            "paper_tier1": int(counts.get("paper_tier1", 0) or 0), "paper_tier0": int(counts.get("paper_tier0", 0) or 0),
            "s9_m1_decisions": int(counts.get("s9_m1_decisions", 0) or 0),
            "s9_m1_gated": int(counts.get("s9_m1_gated", 0) or 0),
            "s9_m1_no_history": int(counts.get("s9_m1_no_history", 0) or 0),
            "s9_m2_decisions": int(counts.get("s9_m2_decisions", 0) or 0),
            "s9_m2_gated": int(counts.get("s9_m2_gated", 0) or 0),
            "s9_m2_no_history": int(counts.get("s9_m2_no_history", 0) or 0),
            "s9_by_m": {k: int(v or 0) for k, v in sorted(counts.items()) if k.startswith("s9_m")},
            "s9_keep_sim_s": (round(float(keep), 3) if keep is not None else None),
            "errors": int(getattr(self, "_v6_30_errors", 0) or 0)})
        return row
