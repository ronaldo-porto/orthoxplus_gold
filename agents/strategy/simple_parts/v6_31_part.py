"""v6.31 touch improve (S12b) and the single-write session save -- research_v631_touch_improve

v6.39 S4 (k129): methods of Strategy1_Research_Simple moved here VERBATIM (simple_parts/join.py puts them back at their
lines: the joined text is byte-identical to the pre-split file).  A mixin: no state, no logic of its own; `self` is the agent.
"""
from __future__ import annotations

import json
import os

from research_v5_session_identity import OWNER_KEY
from research_v617_touch_quote import client_ids as v617_client_ids
from research_v6210_touch_improve import touch_view as v6210_touch_view
from research_v6214_touch_life import exit_client_ids as v6214_exit_client_ids
from research_v62_breadth import V62_STATE_EVERY_TICKS, entry_client_ids as v62_entry_client_ids
from research_v631_touch_improve import (
    REASONS as V6_31_REASONS,
    STAGE_SUFFIX as V6_31_STAGE_SUFFIX,
    V631_SINGLE_WRITE_SAVE_VERSION as V6_31_SINGLE_WRITE_SAVE_VERSION,
    V631_TOUCH_IMPROVE_VERSION as V6_31_TOUCH_IMPROVE_VERSION,
    improved_touch_price as v6_31_improved_touch_price,
)
from research_v633_deep_layer import own_client_ids as v633_own_client_ids
from research_v63_trend_target import own_client_ids as v63_own_client_ids


class V631Part:
    """v6.31 touch improve (S12b) and the single-write session save -- research_v631_touch_improve"""

    def _v6_31_note(self, book_id: int, book, own_ids, touch_on: bool) -> None:
        """v6.31 S12b (research_v631_touch_improve), in the pass right after the book's ledger rows are read: notes the
        book as this request shows it and our live order ids on it (the ledger's), with the book and the request, for
        _v6_31_price -- which takes the others' touch from them only when a new touch quote is priced: a few books a
        request, where taking it here would walk every book's best levels on every request (latency gates making).
        Off, or without the touch quote: nothing noted.  Never raises: an error counts and leaves nothing noted."""
        try:
            if not bool(getattr(self, "research_v631_touch_improve", False)) or not touch_on:
                self._v6_31_noted = None
                return
            self._v6_31_noted = (int(book_id), int(getattr(self, "_tick", 0) or 0), book, set(own_ids or ()))
        except Exception:
            self._v6_31_errors = int(getattr(self, "_v6_31_errors", 0) or 0) + 1
            self._v6_31_noted = None

    def _v6_31_price(self, book_id: int, side: str, join_px, fp, tick_size):
        """v6.31 S12b (research_v631_touch_improve), in v6.17's touch quote right after it chose its join price: the
        price a NEW quote on ``side`` takes -- one tick better than the others' best when v6.2.10's geometry accepts it
        (strictly inside every opposite order, ours included, and strictly on our side of the others' mid), it is
        strictly better than ``join_px`` and its edge vs the fresh fundamental ``fp`` is still v6.17's hurdle
        (v6_31_improved_touch_price); else ``join_px``.  The others' touch is v6.2.10's touch view of the book
        _v6_31_note noted for this book this request (none noted: the join), every order of ours left out -- by the
        ledger's live order ids and by our client ids: v6.3's roles, v6.2's entries, v6.2.14's exits, v6.3.3's deep
        layer and v6.17's own touch quote.  Tallies why.  Off: ``join_px``.  Never raises: an error counts,
        ``join_px``."""
        try:
            if not bool(getattr(self, "research_v631_touch_improve", False)):
                return join_px
            noted = getattr(self, "_v6_31_noted", None)
            view = None
            if noted is not None and noted[0] == int(book_id) and noted[1] == int(getattr(self, "_tick", 0) or 0):
                own_cids = (
                    v63_own_client_ids(book_id) | set(v62_entry_client_ids(book_id))
                    | set(v6214_exit_client_ids(book_id)) | v633_own_client_ids(book_id) | set(v617_client_ids(book_id))
                )
                view = v6210_touch_view(noted[2], own_ids=noted[3], own_cids=own_cids)
            price, why = v6_31_improved_touch_price(side, view, fp, tick_size, join_price=join_px)
            self._v6_31_count(why)
            return join_px if price is None else price
        except Exception:
            self._v6_31_errors = int(getattr(self, "_v6_31_errors", 0) or 0) + 1
            return join_px

    def _v6_31_staged_save(self, force: bool) -> bool:
        """v6.31 (research_v631_single_write_save), first in the session save: runs v6.30's whole save -- the frozen
        base's payload write, then v6.30's re-read and owner / v6.8 deep / v6.1 lots / v6.21 pace / maker quality
        rewrite -- with every read and write of the session file on a stage file (_research_session_path returns it
        while it is set), and replaces the session file with the stage in ONE os.replace, only when the stage is whole:
        it parses, carries the owner record (with research_v504_session_per_uid) and the maker quality.  A stop before
        the rename leaves the previous whole file in place: v6.30 replaced the file twice, and pm2's SIGKILL (1.6 s
        after its SIGINT) between the two writes left a file without its owner -- UNOWNED_REFUSED and a cold start
        (UID 115, 10-14).  v6.29's _v6_29_saved now runs inside the body, microseconds before the rename.  No stage
        written this call (the cadence skipped the save): nothing is renamed.  A stage left by a stopped save is removed
        first, never renamed.  An incomplete stage or an error: counted, the stage removed, the session file untouched.
        False (v6.30's save as it is) when off, when re-entered from the staged run itself, or without a session
        identity; True otherwise.  Never raises; the stage is always reset."""
        if not bool(getattr(self, "research_v631_single_write_save", False)):
            return False
        if getattr(self, "_v6_31_stage", None) is not None:
            return False                                # re-entered: this is the staged run of v6.30's save
        identity = getattr(self, "_research_session_identity", None)
        if identity is None or not getattr(identity, "simulation_id", None):
            return False
        stage = None
        try:
            real = self._research_session_path(identity)
            stage = real + V6_31_STAGE_SUFFIX
            try:
                os.unlink(stage)                        # a stage a stopped save left behind is never renamed
            except OSError:
                pass
            self._v6_31_stage = stage
            try:
                self._research_save_session(force=force)            # v6.30's whole save, every write on the stage
            finally:
                self._v6_31_stage = None
            if not os.path.isfile(stage):
                return True                             # nothing written this call: the session file stays as it is
            try:
                with open(stage, encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, TypeError, ValueError):
                payload = None
            whole = (isinstance(payload, dict) and "direct_maker_quality_a1_5_1" in payload
                     and (OWNER_KEY in payload or not bool(getattr(self, "research_v504_session_per_uid", True))))
            if whole:
                os.replace(stage, real)                 # the one write of the session file
                self._v6_31_count("staged_saves")
                return True
            self._v6_31_count("stage_incomplete")
        except Exception:
            self._v6_31_errors = int(getattr(self, "_v6_31_errors", 0) or 0) + 1
        self._v6_31_stage = None
        if stage is not None:
            try:
                os.unlink(stage)
            except OSError:
                pass
        return True

    def _v6_31_count(self, key: str, n: int = 1) -> None:
        """v6.31's tally.  Never raises: an error counts."""
        try:
            counts = getattr(self, "_v6_31_counts", None)
            if counts is None:
                counts = {}
                self._v6_31_counts = counts
            counts[key] = int(counts.get(key, 0) or 0) + int(n)
        except Exception:
            self._v6_31_errors = int(getattr(self, "_v6_31_errors", 0) or 0) + 1

    def _v6_31_report(self) -> None:
        """v6.31: the V6_31_STATE row -- both switches and versions, S12b's new touch-quote prices by why (improved
        buy / sell; joined: not inside, our own best, edge short, no view), the staged saves renamed into place and the
        stages found incomplete, and the errors -- at the first pass, at the first request with an error after one
        without, and every V62_STATE_EVERY_TICKS ticks; after the reply when the request defers its telemetry (v6.2.9).
        Never raises."""
        try:
            tick = int(getattr(self, "_tick", 0) or 0)
            errors = int(getattr(self, "_v6_31_errors", 0) or 0)
            erring = errors > int(getattr(self, "_v6_31_errors_seen", 0) or 0)
            was_erring = bool(getattr(self, "_v6_31_erring", False))
            self._v6_31_errors_seen, self._v6_31_erring = errors, erring
            if not getattr(self, "_v6_31_reported", False):
                reason = "start"
            elif erring and not was_erring:
                reason = "error"
            elif tick > 0 and tick % V62_STATE_EVERY_TICKS == 0:
                reason = "periodic"
            else:
                return
            self._v6_31_reported = True
            row = dict(self._v6_31_snapshot(), reason=reason, tick=tick)
            work = getattr(self, "_v629_request_work", None)
            if work is not None:
                work.add("v6_31_state_row", lambda: self._emit("V6_31_STATE", force=True, **row))   # after the reply
            else:
                self._emit("V6_31_STATE", force=True, **row)
        except Exception:
            self._v6_31_errors = int(getattr(self, "_v6_31_errors", 0) or 0) + 1

    def _v6_31_snapshot(self) -> dict:
        counts = dict(getattr(self, "_v6_31_counts", None) or {})
        row = {"version": V6_31_TOUCH_IMPROVE_VERSION, "save_version": V6_31_SINGLE_WRITE_SAVE_VERSION,
               "touch_improve_on": int(bool(getattr(self, "research_v631_touch_improve", False))),
               "single_write_save_on": int(bool(getattr(self, "research_v631_single_write_save", False)))}
        row.update({why: int(counts.get(why, 0) or 0) for why in V6_31_REASONS})
        row.update({"staged_saves": int(counts.get("staged_saves", 0) or 0),
                    "stage_incomplete": int(counts.get("stage_incomplete", 0) or 0),
                    "errors": int(getattr(self, "_v6_31_errors", 0) or 0)})
        return row
