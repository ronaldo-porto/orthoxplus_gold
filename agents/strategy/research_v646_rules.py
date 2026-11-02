# SPDX-License-Identifier: MIT
"""v6.46: the Round 5 Stage 4 combined arm S4P7 (Stage 4 4.3 PASS 10-31 03:58 JST, 4.4 PASS on the held-out T251 10-31
07:43 JST; USER D3 10-30 "GO only if 4.3 AND 4.4 pass") -- the rules, function for function from the merged replay engine
(scratchpad round5/vm_r5m4.py 907cbe99, classes 0 + 2) and the class-1 engine (round5/vm_r5b5.py dd01c9f4, the class-1-only
leg R5TU), each behind its own research_v646_* switch, code default 0 (off: v6.45 on every path).

Class 2 (Stage 1 R6C2RQ = c2np, c2tb, c2rf, c2rq on C2T + C2BC; round5/stage1_winner.txt):
* C2NP (research_v646_c2np, engine c2np=1): C2BC's class-2 centre is the lean of the gap's DEVIATION alone,
  gd = g - gbar -- the Pt term (the class-2 book's own mid-ring drift) is dropped:  _gd2 = (g - gbar) - 0.
* C2TB (research_v646_c2tb, engine c2tb=1): with the inventory exactly at the class-2 centre (|inv - tau2| <= EPS) and the
  TRUE inventory not flat, the side that reduces the TRUE inventory is a REDUCING side (C2T's plan: no bound / pace / fee /
  hold test, size max(clip, |inv - tau2|) = clip); the other side still adds.  Without it both sides add at the tie.
* C2RF (research_v646_c2rf, engine c2rf=1): a resting class-2 C2T quote on the ADDING side of the TRUE inventory (a bid
  while inv >= -EPS, an ask while inv <= EPS) is cancelled every state -- a one-state life; the side is re-placed (C2T's
  plan, price and size of that state) once the cancel is seen (the live A1.7.4.3.1 rule = the engine's blocked state).
* C2RQ (engine c2rq=1, the cancel-seen gap removed: the fresh quote in the SAME response as the cancel) is NOT built: the
  live order manager admits no second order on a (book, side, touch slot) whose order is still in the state's book --
  BLOCKED, see the v6.46 hand-off (SP/v646).

Class 1 (Stage 2 R5TU = the v6.45 class-1 leg + exx + c1tu; round5/stage2_winner.txt; class-1 books only):
* uw 0.5 / deepmax 24 / pbnd 4 in class-0 units: what v6.45 already does (the deep layer's bound in clips x the class-0
  clip on a class-0-unit inventory) -- nothing new here.
* EXX (research_v646_c1exx, engine exx=1 in the class-1-only run): the class-1 TOUCH quote is exempt from the A1.9 exit
  layer's size-shortfall cancel (a reducing maker order below 0.8 x |inventory|); every other A1.9 reason stands.
* C1TU (research_v646_c1tu, engine c1tu=1): with the lean centre armed (tau != 0), the touch quote on the centre's side
  (a bid iff tau > 0) while the inventory is short of the centre on that side (inv - tau < -EPS for a bid, > EPS for an
  ask) is sized max(clip, |inv - tau|) -- before v6.20's scale, as the engine's c_f before _psf.

Class 0 (Stage 3 R10LKAW = s5tcu, s5x0, s5lkc, s5aw on the F3.5 front-load; round5/stage3_winner.txt; class-0 books only):
* S5TCU (research_v646_s5tcu): C1TU's rule on the class-0 touch quote.
* S5X0 (research_v646_s5x0): EXX's rule on the class-0 touch quote.
* S5LKC (research_v646_s5lkc): while the book's front-load multiple is above 1 on its launch line (the engine's
  _s3_k > 1 with ES_ST 'L'), the lean centre is tau x s3kh (the hold multiple; s5lkr / s5lkh off) -- applied after BSL,
  before everything that reads tau (the touch, its band, TCU, the deep layer's inventory).
* S5AW (research_v646_s5aw): the deep layer (level 0 and the ladder) holds the side that ADDS relative to the centre
  (a bid while inv - tau >= -EPS, an ask while inv - tau <= EPS) when it is the making window's capture-surplus side,
  STRICTLY (buy capture > sell capture for the bid, sell > buy for the ask; no EPS): a resting order there is cancelled,
  none is placed.  The touch quote is not held.

STRUCTURAL throughout: the engine's arithmetic, the validator's clamp / lookback / making window, the agent's own clip,
centre and making mirror; no constant here is sized from a sample (EPS is the engine's 1e-9)."""
from __future__ import annotations

import math
from typing import Any

from research_v63_trend_target import SIDE_BUY, SIDE_SELL
from research_v644_c2t import WHY_BOUND, WHY_CAPPED, WHY_FEE_GATE, WHY_HELD, WHY_PACED, WHY_TOUCH_IDENTITY
from research_v644_c2t import adds as c2t_adds
from research_v645_bsl import c2bc_tau
from research_v645_frontload import front_factor
from research_v621_young_window import EVEN_LAUNCH

V646_RULES_VERSION = "rules_v6_46"
EPS = 1e-9                                   # the engine's EPS (vm_r5m4 / vm_r5b5)
CANCEL_C2RF = "C2T_REFRESH"                  # C2RF: the adding-side class-2 quote's one-state life
EXEMPT_TAG = "V646_TOUCH_SIZE_EXEMPT"        # EXX / S5X0: the A1.9 size-shortfall reason set aside on a touch quote

SWITCHES = ("research_v646_c2np", "research_v646_c2tb", "research_v646_c2rf", "research_v646_c1exx",
            "research_v646_c1tu", "research_v646_s5tcu", "research_v646_s5x0", "research_v646_s5lkc",
            "research_v646_s5aw")

__all__ = ["CANCEL_C2RF", "EPS", "EXEMPT_TAG", "SWITCHES", "V646_RULES_VERSION", "aw_sides", "c2_adds", "c2_side_plan",
           "c2np_tau", "c2rf_refresh", "front_k", "lkc_tau", "parse_switch", "tcu_size"]


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def parse_switch(value: Any) -> int | None:
    """A 0 / 1 switch ("0", "1", 0, 1, 0.0, 1.0, True, False; missing / empty = 0); None when it is anything else."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    text = str(value).strip()
    if text == "":
        return 0
    x = _finite(text)
    if x in (0.0, 1.0):
        return int(x)
    return None


# ---- class 2 ------------------------------------------------------------------------------------------------------

def c2np_tau(g: Any, gbar: Any, gap_bps: Any, slope: Any, cap: Any) -> float:
    """C2NP: C2BC's centre without Pt -- the lean of gd = (g - gbar) - 0 (research_v645_bsl.c2bc_tau with pt = 0)."""
    return c2bc_tau(g, gbar, 0.0, gap_bps, slope, cap)


def c2_adds(side: str, inventory: Any, true_inventory: Any, tie_break: bool) -> bool:
    """C2T's adding test on the class-2 centre-relative ``inventory`` (inv - tau2; v6.23 S1's rule), with C2TB: at the
    tie (|inv - tau2| <= EPS) and a TRUE inventory not flat, the side that reduces the TRUE inventory does not add."""
    add = c2t_adds(side, inventory)
    if tie_break and add:
        iv2 = _finite(inventory) or 0.0
        tv = _finite(true_inventory) or 0.0
        if abs(iv2) <= EPS and abs(tv) > EPS and ((side == SIDE_SELL and tv > EPS) or (side == SIDE_BUY and tv < -EPS)):
            return False
    return add


def c2_side_plan(side: str, *, inventory: Any, true_inventory: Any, tie_break: bool, clip: Any, bound: Any,
                 capped: bool, paced: bool, fee_gated: bool, held: bool, d4_off: bool) -> tuple[float, str | None]:
    """research_v644_c2t.side_plan with C2TB's adding test (tie_break False: side_plan exactly)."""
    c, lim = _finite(clip), _finite(bound)
    inv = _finite(inventory) or 0.0
    if c is None or c <= 0.0 or lim is None:
        return 0.0, WHY_BOUND
    if capped:
        return 0.0, WHY_CAPPED
    add = c2_adds(side, inv, true_inventory, bool(tie_break))
    if add and abs(inv) >= lim - EPS:
        return 0.0, WHY_BOUND
    if add and paced:
        return 0.0, WHY_PACED
    if add and fee_gated:
        return 0.0, WHY_FEE_GATE
    if d4_off:
        return 0.0, WHY_TOUCH_IDENTITY
    if add and held:
        return 0.0, WHY_HELD
    return (c if add else max(c, abs(inv))), None


def c2rf_refresh(side: str, true_inventory: Any) -> bool:
    """C2RF: a resting class-2 quote on ``side`` takes its one-state life -- the side ADDS on the TRUE inventory."""
    return c2t_adds(side, true_inventory)


# ---- classes 0 / 1: the touch, the centre, the deep side ---------------------------------------------------------

def tcu_size(side: str, inventory: Any, tau: Any, clip: Any) -> float:
    """S5TCU / C1TU: the touch quote's size before v6.20's scale -- max(clip, |inv - tau|) on the centre's side (a bid iff
    tau > 0) while ``inventory`` (= inv - tau) is short of the centre there (< -EPS for a bid, > EPS for an ask); else
    ``clip``.  (The engine: ``if abs(inv) > c_f + EPS: c_f = abs(inv)``.)"""
    c = _finite(clip)
    if c is None:
        return clip
    t, iv = _finite(tau), _finite(inventory)
    if t is None or iv is None or t == 0.0:
        return c
    if (side == SIDE_BUY) != (t > 0.0):
        return c
    if (side == SIDE_BUY and iv < -EPS) or (side == SIDE_SELL and iv > EPS):
        if abs(iv) > c + EPS:
            return abs(iv)
    return c


def lkc_tau(tau: Any, k: Any, kh: Any) -> float:
    """S5LKC (s5lkr / s5lkh off): tau x kh while the front-load multiple k > 1 and kh > 1; tau otherwise."""
    t = _finite(tau)
    if t is None or t == 0.0:
        return 0.0 if t is None else t
    kv, hv = _finite(k), _finite(kh)
    if kv is None or hv is None or not kv > 1.0 or not hv > 1.0:
        return t
    return t * hv


def aw_sides(inventory: Any, buy_capture: Any, sell_capture: Any) -> frozenset:
    """S5AW: the deep sides held -- a side that ADDS relative to the centre (``inventory`` = inv - tau: a bid at
    >= -EPS, an ask at <= EPS) whose making-window capture STRICTLY leads the other side's."""
    iv = _finite(inventory)
    wb, ws = _finite(buy_capture), _finite(sell_capture)
    if iv is None or wb is None or ws is None:
        return frozenset()
    out = set()
    if iv >= -EPS and wb > ws:
        out.add(SIDE_BUY)
    if iv <= EPS and ws > wb:
        out.add(SIDE_SELL)
    return frozenset(out)


def front_k(pace: Any, book_id: int, now_ns: int) -> float | None:
    """The engine's _s3_k for S5LKC on a FrontLoadLine ``pace``: the line's multiple while the book of a front-loaded class
    runs its LAUNCH line (EVEN_LAUNCH, the engine's ES_ST 'L'), 1 on any other line, None for a book that is not
    front-loaded -- FrontLoadLine.front_clip_factor without its clip-mode test (S5LKC reads the line, not the clip)."""
    b = int(book_id)
    if not pace.front(b):
        return None
    st = pace.even.get(b) if (pace.even_on and b not in pace.aged) else None
    tw = pace.window_start_ns(b)
    if st is None or st[0] != EVEN_LAUNCH or tw is None or pace.front_cap is None or pace.front_cap <= 0.0:
        return 1.0
    return front_factor(pace.front_cap, tw, now_ns, kf=pace.front_kf, tf_s=pace.front_tf_s, kh=pace.front_kh,
                        window_ns=pace.window_ns, assessment_ns=pace.assessment_ns)
