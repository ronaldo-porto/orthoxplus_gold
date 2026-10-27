"""v6.43 (USER emergency 10-27): the volume rescue -- research_v643_rescue in {0, 1}, code default 0 (off: v6.42 on every
path), PARAMS 1.  The rule is research_v643_rescue's (RescueLine: v6.28's flat-launch line plus one making window of
extra room at RESCUE_RATE from the rescue start, paid back evenly to each book's roll-off, and the deep clip x
RESCUE_RATE while that window is open).  This mixin, first among Simple's bases, carries the agent side; Simple's four
v6.28 hooks call it:

1. _v6_28_line (where the pace line is built): _v6_43_line -- a RescueLine with the same switches and rate when the
   switch is on, else v6.28's V628FlatLaunchLine exactly as v6.42 builds it;
2. _v6_28_restore (the first request after v6.21 S2 put the saved pace state back): _v6_43_restore -- the saved rescue
   start (re-keyed with the state), so a restart never starts a second rescue; v6.28's tallies pass through;
3. _v6_28_save (the session save right after v6.21 S2's pace state): _v6_43_save -- the rescue start in the state,
   after v6.28's rates;
4. _v6_28_clip (the deep clip of a book): _v6_43_clip -- v6.28's clip x RESCUE_RATE while the rescue window is open.
Each hook replaces one line of Simple (simple_parts/join.py puts the parts' chunks back by line number).

Every hook never raises: an error counts (V6_43 errors in the pace snapshot / _v6_43_errors) and the agent keeps v6.42's
line, state and clip.  Unchanged: tau, D4, the touch quote, A_r1 + D, l1t / l1c, the band, the aim, the hard cap, every
class.  No taking.  STRUCTURAL (rate: USER directive)."""
from __future__ import annotations

from research_v643_rescue import RescueLine as V643RescueLine
from research_v643_rescue import parse_switch as v6_43_parse_switch
from research_v643_rescue import restore_rescue as v6_43_restore_rescue
from research_v643_rescue import save_rescue as v6_43_save_rescue


class V643Part:
    """v6.43 (research_v643_rescue): one making window of extra volume room from the deploy, paid back to the roll-off"""

    def _v6_43_on(self) -> bool:
        """The switch, read once from the config (code default 0); an unreadable value runs off and counts."""
        on = getattr(self, "_v6_43_switch", None)
        if on is None:
            try:
                got = v6_43_parse_switch(getattr(getattr(self, "config", None), "research_v643_rescue", 0))
                if got is None:
                    self._v6_43_errors = int(getattr(self, "_v6_43_errors", 0) or 0) + 1
                on = bool(got)
            except Exception:
                self._v6_43_errors = int(getattr(self, "_v6_43_errors", 0) or 0) + 1
                on = False
            self._v6_43_switch = on
        return bool(on)

    def _v6_43_line(self, pace, flat_cls):
        """The pace line built from v6.21's ``pace``: a RescueLine with its switches and rate when the switch is on (an
        error there counts and falls back), else ``flat_cls`` (v6.28's V628FlatLaunchLine) exactly as v6.42 builds it --
        whose own error Simple's v6.28 hook handles as before."""
        if self._v6_43_on():
            try:
                return V643RescueLine(launch=pace.launch_on, guard=pace.guard_on, even=pace.even_on, rate=pace.rate)
            except Exception:
                self._v6_43_errors = int(getattr(self, "_v6_43_errors", 0) or 0) + 1
        return flat_cls(launch=pace.launch_on, guard=pace.guard_on, even=pace.even_on, rate=pace.rate)

    def _v6_43_restore(self, pace, payload, how, tallies):
        """The saved rescue start back into ``pace`` (re-keyed with the state); returns v6.28's ``tallies``.  Never
        raises."""
        try:
            if isinstance(pace, V643RescueLine):
                v6_43_restore_rescue(pace, payload, how)
        except Exception:
            self._v6_43_errors = int(getattr(self, "_v6_43_errors", 0) or 0) + 1
        return tallies

    def _v6_43_save(self, pace, state, rows):
        """The rescue start into the session's pace ``state`` (after v6.28's ``rows``); returns ``rows``.  Never raises."""
        try:
            if isinstance(pace, V643RescueLine):
                v6_43_save_rescue(pace, state)
        except Exception:
            self._v6_43_errors = int(getattr(self, "_v6_43_errors", 0) or 0) + 1
        return rows

    def _v6_43_clip(self, got, pace, book_id, now_ts):
        """v6.28's deep clip ``got`` x RESCUE_RATE while the book's rescue window is open.  Never raises: ``got``."""
        try:
            if not isinstance(pace, V643RescueLine):
                return got
            return float(got) * float(pace.clip_factor(book_id, now_ts))
        except Exception:
            self._v6_43_errors = int(getattr(self, "_v6_43_errors", 0) or 0) + 1
            return got
