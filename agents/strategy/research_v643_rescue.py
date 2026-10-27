# SPDX-License-Identifier: MIT
"""v6.43: the volume rescue -- one making window of extra volume room and a larger deep clip from the rescue start, paid
back evenly to each book's roll-off -- research_v643_rescue.

Why (USER emergency 10-27 ~11:40 JST: "increase the trading volume around 1.5 than the current"; board 11:29 JST):

* UIDs 61 / 5 (v6.42, fresh registrations 10-26 / 10-27) rank ~16th-18th from the bottom of 257 as their immunity ends
  (~23:00 JST 10-27 / ~01:26 JST 10-28): incentive 0.00055 / 0.00037 vs the lowest non-immune 0.00235.  Their making
  per notional is above the field median, their window notional (2.5M / 2.0M) is at the field's 4th percentile.
* v6.28's flat launch holds every busy young book to the sustainable rate (the cap per assessment period) by order-size
  scaling; k165 (10-27) measured that scaling as the whole fresh-start volume gap -- a time shift under the rolling cap,
  not a loss of information.  The validator pays making over its 10,800-s making window (then an EMA of the same
  half-life), so volume moved into the next making window shows in pay while the slots are at risk; the cap takes it
  back later as a slower line.

The rule (one switch, research_v643_rescue; code default 0 = v6.42 on every path):

* The rescue start t_r is the first state at which this build's line is computed (the deploy).  It rides in the v6.21
  pace state (RESCUE_KEY) so a restart of the same UID and simulation keeps it, and it is re-keyed with that state at a
  simulation seam.  A restart therefore never starts a second rescue.
* Every young book on its v6.21 even line gets EXTRA room on top of its v6.21 / v6.28 line: rising at (RESCUE_RATE - 1)
  x the sustainable rate for one making window from t_r, then paid back evenly to zero at the book's own earliest
  roll-off -- v6.15's launch shape (a burst for one making window, an even payback), so the line still reaches v6.21's
  aim, never the cap, at t_roll.  A book whose roll-off falls inside the rescue window gets none.
* While the rescue window is open the book's deep clip is RESCUE_RATE x the clip v6.28 gives it (v6.15's launch clip
  rule: the clip follows the line's rate).
* Nothing else changes: aged books, books without a known window start, the band, the aim, the hard cap, the touch
  quote, every class, D4, l1t / l1c.  Off: v6.42.

STRUCTURAL: the sustainable rate, the making window, the roll-off, v6.15's launch shape and clip rule.  RESCUE_RATE = 1.5
is the USER's directive (not fitted).  OBSERVED (coordinator 10-27, replay vm768 on k163's fresh cells FN1 / FP, vsd as
the BASE replicate, launch=1.5 + lclip=1.5): volume x1.30 / x1.28 over [S, W_hi), fills x1.07 / x1.05.
"""
from __future__ import annotations

import math
from typing import Any

from research_v615_launch import MAKING_WINDOW_NS
from research_v621_young_window import RESTORE_REKEYED, RESTORE_RESTORED
from research_v628_flat_launch import FlatLaunchLine
from research_v641_pace import ASSESSMENT_NS

V643_RESCUE_VERSION = "volume_rescue_v6_43"
RESCUE_RATE = 1.5                # USER directive 10-27: about 1.5x the volume for one making window
RESCUE_KEY = "v643_rescue"       # the rescue's row in the v6.21 pace state (top level)


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def parse_switch(value: Any) -> bool | None:
    """research_v643_rescue: 0 / 1 (an int, an integral float -- the launcher's PARAMS reach the config as 1.0 -- or its
    text; also false / true, no / yes, off / on); anything else is None (unreadable)."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    s = str(value).strip().lower()
    if s in ("true", "yes", "on"):
        return True
    if s in ("false", "no", "off", "", "none"):
        return False
    try:
        v = float(s)
    except ValueError:
        return None
    if v == 1.0:
        return True
    if v == 0.0:
        return False
    return None


def extra_room(cap: Any, start_ns: Any, roll_ns: Any, now_ns: Any, *, rate: float = RESCUE_RATE,
               making_ns: int = MAKING_WINDOW_NS, assessment_ns: int = ASSESSMENT_NS) -> float:
    """The rescue's extra room on a book's line at ``now_ns``: (rate - 1) x the sustainable rate (cap / assessment
    period) for one making window from ``start_ns``, then an even payback to zero at the book's roll-off ``roll_ns``;
    0.0 before the start, after the roll-off, without a roll-off, or when the roll-off falls inside the window."""
    c, t0, tr, now = _finite(cap), _int(start_ns), _int(roll_ns), _int(now_ns)
    if c is None or c <= 0.0 or t0 is None or tr is None or now is None:
        return 0.0
    w = int(making_ns)
    peak = (float(rate) - 1.0) * c * w / float(max(1, int(assessment_ns)))
    end = t0 + w
    if peak <= 0.0 or w <= 0 or tr <= end or now <= t0 or now >= tr:
        return 0.0
    if now <= end:
        return peak * (now - t0) / float(w)
    return peak * (tr - now) / float(tr - end)


class RescueLine(FlatLaunchLine):
    """v6.28's flat-launch line with v6.43's rescue: each young book's even line plus ``extra_room`` from the rescue start,
    and the deep clip x ``rescue_rate`` while the rescue window is open.  Never raises out of the rescue part: an error
    counts and the book runs v6.28's line and clip."""

    def __init__(self, *, rescue_rate: float = RESCUE_RATE, **kwargs: Any):
        self.rescue_rate = float(rescue_rate)
        self.rescue_start: int | None = None
        self.rescue_errors = 0
        self.rescue_restored: str | None = None
        super().__init__(**kwargs)

    def _rekey(self, old_end: int) -> None:
        super()._rekey(old_end)
        if self.rescue_start is not None:
            self.rescue_start = int(self.rescue_start) - int(old_end)

    def _rescue_anchor(self, now_ns: int) -> int:
        if self.rescue_start is None:
            self.rescue_start = int(now_ns)
        return int(self.rescue_start)

    def rescue_extra(self, book_id: int, now_ns: int, cap: Any) -> float:
        """The extra room on this book's line now (0.0 for an aged book or one without a roll-off)."""
        try:
            b = int(book_id)
            if b in self.aged or b not in self.start:
                return 0.0
            t0 = self._rescue_anchor(now_ns)
            return extra_room(cap, t0, self.rolloff(b), now_ns, rate=self.rescue_rate, making_ns=self.making_ns,
                              assessment_ns=self.assessment_ns)
        except Exception:
            self.rescue_errors += 1
            return 0.0

    def _even_line(self, book_id: int, st: tuple, now_ns: int, cap: Any) -> tuple[float, float]:
        lv, band = super()._even_line(book_id, st, now_ns, cap)
        return lv + self.rescue_extra(book_id, now_ns, cap), band

    def rescue_open(self, book_id: int, now_ns: int) -> bool:
        """Is this young book inside the rescue window (its roll-off after the window's end)?"""
        if self.rescue_start is None:
            return False
        b = int(book_id)
        if b in self.aged or b not in self.start:
            return False
        roll = self.rolloff(b)
        t0 = int(self.rescue_start)
        return roll is not None and roll > t0 + self.making_ns and 0 <= int(now_ns) - t0 < self.making_ns

    def clip_factor(self, book_id: int, now_ns: int) -> float:
        """The factor on the deep clip v6.28 gives this book now: ``rescue_rate`` inside the rescue window, else 1."""
        try:
            return self.rescue_rate if self.rescue_open(book_id, now_ns) else 1.0
        except Exception:
            self.rescue_errors += 1
            return 1.0

    def snapshot(self) -> dict[str, Any]:
        out = super().snapshot()
        now = self.last_ts
        n_open = 0
        if now is not None and self.rescue_start is not None:
            try:
                n_open = sum(1 for b in self.start if self.rescue_open(b, now))
            except Exception:
                self.rescue_errors += 1
        out["rescue"] = {"version": V643_RESCUE_VERSION, "rate": self.rescue_rate, "start": self.rescue_start,
                         "open_books": int(n_open), "restored": self.rescue_restored, "errors": int(self.rescue_errors)}
        return out


def save_rescue(line: Any, state: Any) -> int:
    """v6.21 S2's pace state (``state``, of ``line``) with the rescue start at its top level (RESCUE_KEY); nothing
    before the start is known.  Returns the rows written (0 / 1)."""
    if not isinstance(line, RescueLine) or not isinstance(state, dict) or line.rescue_start is None:
        return 0
    state[RESCUE_KEY] = {"version": V643_RESCUE_VERSION, "start": int(line.rescue_start), "rate": line.rescue_rate}
    return 1


def restore_rescue(line: Any, payload: Any, how: Any) -> str:
    """After v6.21 S2 restored (or re-keyed) ``payload`` into ``line`` (``how``): the saved rescue start, re-keyed by the
    same shift as the state.  Returns what happened: 'restored', 'unrecorded' (no row: the rescue starts now),
    'invalid' (a row this build cannot run: the rescue starts now) or 'none'."""
    if not isinstance(line, RescueLine) or how not in (RESTORE_RESTORED, RESTORE_REKEYED) or not isinstance(payload, dict):
        return "none"
    row = payload.get(RESCUE_KEY)
    if row is None:
        line.rescue_restored = "unrecorded"
        return "unrecorded"
    start = _int(row.get("start")) if isinstance(row, dict) else None
    rate = _finite(row.get("rate")) if isinstance(row, dict) else None
    if start is None or rate != line.rescue_rate or row.get("version") != V643_RESCUE_VERSION:
        line.rescue_restored = "invalid"
        return "invalid"
    shift = 0
    if how == RESTORE_REKEYED:
        last, now_last = _int(payload.get("last_ts")), _int(line.last_ts)
        if last is None or now_last is None:
            line.rescue_restored = "invalid"
            return "invalid"
        shift = last - now_last
    line.rescue_start = start - shift
    line.rescue_restored = "restored"
    return "restored"


__all__ = ["RESCUE_KEY", "RESCUE_RATE", "RescueLine", "V643_RESCUE_VERSION", "extra_room", "parse_switch",
           "restore_rescue", "save_rescue"]
