# SPDX-License-Identifier: MIT
"""v6.45: the class-0 front-load (F3.5c0) -- research_v645_s3kf / s3tf / s3kh / s3cl / s3c, code default s3kf 0 (off:
v6.44's line on every path).  The rule is the replay engine's Round 4 stage-3 shape (scratchpad round4/vm_r4d.py, the
vm_r4c patch set: _s3_line, _s3_k, _es_launch's S3 branch and the deep clip's S3 branch; D_F3.5 = s3kf=3.5, s3tf=8752,
s3kh=2, s3cl=1, s3c=0; SP/round4/f35/RESULT.md, USER 10-30: "build 3.5x").

Why (memory round3-result-1029 / round4-plan-1029): the first ~20 h of a fresh UID decide whether it clears the prune
line, and its making quality is built from its volume while the slot is immune.  v6.43's rescue gives every young book
1.5x the sustainable rate for one making window; Round 4 front-loads class 0 harder and longer.  The volume-cap rule is
relaxed (USER STANDING 10-29: beyond 1.5x allowed); the validator's cap (500k quote / book / 86,400 sim-s rolling) stays
the physical limit.

The rule, on a book of a front-loaded class (s3c; shipped class 0) whose v6.21 even state is its LAUNCH line (first
seen on an empty window, or restored on it):

* its line, at line clock x = now - t_w + one sampling window (600 s; t_w = the book's window start, which v6.21 S2
  keeps in the session, so a restart keeps the line), is
      line = min(cap, cap x (kf x min(x, Tf) + kh x max(0, x - Tf)) / 86,400 s)
  -- kf x the sustainable rate for Tf, then kh x it, never above the cap -- with v6.21's band (the cap's making-window
  share); it replaces v6.21's launch line AND v6.43's rescue room on that book (the engine's _es_launch S3 branch);
* its deep clip (s3cl = 1) is the layer's clip x kf in the front (x < Tf), x kh after, x 1 once the line is at the cap
  (the engine's _s3_k), in place of v6.43's rescue factor (the engine skips lclip on an S3 class);
* v6.21's first-use decision (launch line or anchored) reads the front-load line for such a book, as the engine's.
Everything else is v6.44's: the other classes' lines and clips (v6.43's rescue), an anchored book's line, the band
scale, pacing's holds, the hard cap (the validator's own, v6.22 S3 / the final validator at used >= cap), the aim.

STRUCTURAL: the sustainable rate, the making window, the sampling window, the cap; kf / Tf / kh are the tested spec's
(D_F3.5), not fitted here."""
from __future__ import annotations

import math
from typing import Any, Callable

from research_v615_launch import EMPTY_WINDOW_QUOTE
from research_v621_young_window import EVEN_ANCHORED, EVEN_LAUNCH, earliest_rolloff, no_burst_anchor
from research_v641_pace import ASSESSMENT_NS, PACE_WINDOW_NS
from research_v643_rescue import RescueLine

V645_FRONTLOAD_VERSION = "frontload_v6_45"
CLIP_MODES = (0, 1)              # s3cl: 0 the line only, 1 the line and the deep clip (2, the touch clip too: not shipped)

__all__ = ["CLIP_MODES", "FrontLoadLine", "V645_FRONTLOAD_VERSION", "front_factor", "front_line", "parse_classes",
           "parse_number"]


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def parse_number(value: Any) -> float | None:
    """A finite number >= 0 (0 / missing / empty = 0.0); None when unreadable."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return 0.0
    v = _finite(value)
    return v if v is not None and v >= 0.0 else None


def parse_classes(value: Any) -> frozenset | None:
    """research_v645_s3c: the front-loaded classes, "0" / "0,1" / "0+1+2" (a lone number as the launcher's parser reads
    it); None when unreadable or empty."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)) or float(value) != int(value):
            return None
        value = str(int(value))
    out = set()
    for part in str(value).replace(",", "+").split("+"):
        p = part.strip()
        if not p.isdigit():
            return None
        out.add(int(p))
    return frozenset(out) if out else None


def _clock(window_start_ns: Any, now_ns: Any, window_ns: int) -> float:
    """The engine's line clock x = float(now - t_w) + one sampling window (ns)."""
    return float(int(now_ns) - int(window_start_ns)) + float(window_ns)


def front_line(cap: Any, window_start_ns: Any, now_ns: Any, *, kf: float, tf_s: float, kh: float,
               window_ns: int = PACE_WINDOW_NS, assessment_ns: int = ASSESSMENT_NS) -> float:
    """_s3_line at the book's line clock: min(C, C (kf min(x, Tf) + kh max(0, x - Tf)) / P)."""
    C_ = float(cap)
    x_ = _clock(window_start_ns, now_ns, window_ns)
    Tf = float(tf_s) * 1e9
    return min(C_, C_ * (float(kf) * min(x_, Tf) + float(kh) * max(0.0, x_ - Tf)) / float(assessment_ns))


def front_factor(cap: Any, window_start_ns: Any, now_ns: Any, *, kf: float, tf_s: float, kh: float,
                 window_ns: int = PACE_WINDOW_NS, assessment_ns: int = ASSESSMENT_NS) -> float:
    """_s3_k: the line's rate multiple -- kf in the front (x < Tf), kh after, 1 once the line is at the cap; never below 1."""
    C_ = float(cap)
    x_ = _clock(window_start_ns, now_ns, window_ns)
    Tf = float(tf_s) * 1e9
    if min(C_, C_ * (float(kf) * min(x_, Tf) + float(kh) * max(0.0, x_ - Tf)) / float(assessment_ns)) >= C_ - 1e-6:
        return 1.0
    k_ = float(kf) if x_ < Tf else float(kh)
    return max(1.0, k_)


class FrontLoadLine(RescueLine):
    """v6.43's rescue line with v6.45's front-load on the books of the front-loaded classes (their launch line and deep
    clip; see the module docstring).  ``class_of(book) -> class id or None`` is the agent's class map (None: not
    front-loaded).  Never raises out of the front-load part: an error counts and the book runs v6.44's line."""

    def __init__(self, *, front_kf: float, front_tf_s: float, front_kh: float, front_clip: int = 1,
                 front_classes: frozenset = frozenset({0}), class_of: Callable[[int], Any] | None = None, **kwargs: Any):
        self.front_kf = float(front_kf)
        self.front_tf_s = float(front_tf_s)
        self.front_kh = float(front_kh)
        self.front_clip = int(front_clip)
        self.front_classes = frozenset(int(c) for c in front_classes)
        self.class_of = class_of
        self.front_cap: float | None = None          # the cap of the last line read (the clip factor's C)
        self.front_errors = 0
        super().__init__(**kwargs)

    def front(self, book_id: int) -> bool:
        """Is this book of a front-loaded class (by the agent's class map)?  Never raises: False."""
        try:
            if self.class_of is None:
                return False
            c = self.class_of(int(book_id))
            return c is not None and int(c) in self.front_classes
        except Exception:
            self.front_errors += 1
            return False

    def _front_launch(self, book_id: int, now_ns: int, cap: float) -> tuple[float, float]:
        tw = self.window_start_ns(book_id)
        c = float(cap)
        self.front_cap = c
        return (front_line(c, tw, now_ns, kf=self.front_kf, tf_s=self.front_tf_s, kh=self.front_kh,
                           window_ns=self.window_ns, assessment_ns=self.assessment_ns),
                c * float(max(0, int(self.making_ns))) / float(max(1, int(self.assessment_ns))))

    def _even_state(self, book_id: int, now_ns: int, cap: Any) -> tuple | None:
        """v6.21's even state; a front-loaded book decides launch line or anchored on the front-load line (the
        engine's _es_line first-use rule reads _es_launch, the S3 line there)."""
        b = int(book_id)
        if not self.front(b) or b in self.even:
            return super()._even_state(book_id, now_ns, cap)
        try:
            if not self.even_on or b not in self.start or b in self.aged:
                return None
            tw = self.window_start_ns(b)
            c = _finite(cap)
            if tw is None or c is None or c <= 0.0:
                return None
            used = float(self.last_used.get(b, self.start[b][1]))
            lv, band = self._front_launch(b, now_ns, c)
            launch_open = int(now_ns) - int(tw) < self.making_ns
            if used <= EMPTY_WINDOW_QUOTE or (used <= lv + band + 1e-9 and (launch_open or used >= lv - band - 1e-9)):
                st = (EVEN_LAUNCH,)
            else:
                roll = earliest_rolloff(tw, assessment_ns=self.assessment_ns, window_ns=self.window_ns)
                st = (EVEN_ANCHORED, int(now_ns), float(no_burst_anchor(c, used, now_ns, roll, making_ns=self.making_ns,
                                                                         assessment_ns=self.assessment_ns)))
            self.even[b] = st
            return st
        except Exception:
            self.front_errors += 1
            return super()._even_state(book_id, now_ns, cap)

    def _even_line(self, book_id: int, st: tuple, now_ns: int, cap: Any) -> tuple[float, float]:
        if st[0] == EVEN_LAUNCH and self.front(book_id):
            try:
                c = _finite(cap)
                if c is not None and c > 0.0 and self.window_start_ns(book_id) is not None:
                    return self._front_launch(book_id, now_ns, c)
            except Exception:
                self.front_errors += 1
        return super()._even_line(book_id, st, now_ns, cap)

    def front_clip_factor(self, book_id: int, now_ns: int) -> float | None:
        """The factor on the layer's deep clip of a front-loaded book (clip mode 1): _s3_k on its launch line, 1 off it
        (no rescue factor on an S3 class); None for a book that is not front-loaded or with clip mode 0 (v6.44's clip
        rule applies).  Never raises: None."""
        try:
            b = int(book_id)
            if self.front_clip < 1 or not self.front(b):
                return None
            st = self.even.get(b) if (self.even_on and b not in self.aged) else None
            tw = self.window_start_ns(b)
            if st is None or st[0] != EVEN_LAUNCH or tw is None or self.front_cap is None or self.front_cap <= 0.0:
                return 1.0
            return front_factor(self.front_cap, tw, now_ns, kf=self.front_kf, tf_s=self.front_tf_s, kh=self.front_kh,
                                window_ns=self.window_ns, assessment_ns=self.assessment_ns)
        except Exception:
            self.front_errors += 1
            return None

    def snapshot(self) -> dict[str, Any]:
        out = super().snapshot()
        now = self.last_ts
        n_front = n_launch = 0
        try:
            for b in self.start:
                if self.front(b):
                    n_front += 1
                    st = self.even.get(b)
                    if st is not None and st[0] == EVEN_LAUNCH:
                        n_launch += 1
        except Exception:
            self.front_errors += 1
        out["frontload"] = {"version": V645_FRONTLOAD_VERSION, "kf": self.front_kf, "tf_s": self.front_tf_s,
                            "kh": self.front_kh, "clip": self.front_clip, "classes": sorted(self.front_classes),
                            "books": int(n_front), "launch_books": int(n_launch), "cap": self.front_cap,
                            "now": now, "errors": int(self.front_errors)}
        return out
