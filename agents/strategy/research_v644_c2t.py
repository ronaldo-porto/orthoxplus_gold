# SPDX-License-Identifier: MIT
"""v6.44: the class-2 two-sided touch layer (C2T), the fee gate's estimate per class (FGK) and C2Q's guard on q2's
confidence bound (C2QB) -- research_v644_c2t / research_v644_fgk / research_v644_c2qb, code default 0 (off: v6.43 on
every path), PARAMS 1 (scratchpad k168 / k170 / k171 / k173; the replay engine vm771 716a45ab keys c2t=1 fgk=1 c2qb=1).

Why (10-27 / 10-28 JST; memory class2-leg-k167-1027, k173 EVENTS line 110):

* 0.6.5 pays class 2 a quarter of both pools with ONE markout quality q per uid.  Under the realized clamp a class-2
  fill reached by a WALK through the visible touch books q ~0 for every maker group; at or inside the touch every group
  books 0.42-0.53 (k168 S0).  v6.43's class 2 (G2: C2D deep layer only, C2Q guard) fills 83-86 % behind the touch, its
  lean is pinned long by a +280..+400 bp class-2 fp offset (k167), so class 2 is one-sided and makes ~0.
* The live S14 fee gate pools every class-0 and class-2 maker fill into ONE estimate (v6.32; v6.34 keeps class 1
  apart): armed class-2 fills at -0.1..-0.22 bp push it to ~0 and shut most class-0 adding quotes for 1-3 h (k169 / k170).
* k173 (EVENTS lines 110-111, dev / spent data, screen173j f40c3cfc on the v6.43 BASE): C2T+FGK DEV CARRIED -- joint
  three-class true PP +33.4 % (t +3.86 %), class 0 +3.0 %, half-haircut +21.9 %, whole span +28.0 %, carry +14.9 %;
  C2T+FGK+C2QB DEV CARRIED -- +44.1 %, class 0 +5.0 %, half-haircut +30.4 %, whole span +36.8 %, carry +21.2 % (C2QB
  over C2T+FGK on the same seeds: W +10.8 %, whole span +8.8 %, carry +9.5 %).  C2QB ships only with C2T+FGK (k171: on
  v6.43's one-sided class 2 it costs class 0 -24.6 %).

C2T (research_v644_c2t; engine c2t=1): every traded class-2 book, while C2Q has class 2 armed or probing, takes this ONE
two-sided touch layer in place of v6.3.3's deep layer and v6.17's one-sided touch quote:
* no lean (the TRUE inventory), no fp-level hurdle, no deep layer (an order of ours of another layer is cancelled);
* each side: an ADDING side (v6.23 S1's adds: a flat book adds on both) is quoted at CLIP (v6.17's touch clip) unless
  |inventory| is at the bound (v6.10's), the book is paced (its pace line, v6.43's rescue included), S14 gates the side,
  or v6.19 / v6.41 B2b hold it; a REDUCING side at max(CLIP, |inventory|) (the size the A1.9 exit layer keeps);
* D4 (v6.33): a D4-off book keeps no touch quote on either side; at the book's cap: none;
* price: v6.17's join (its post-only cushion) and v6.31 S12b's one-tick improve where v6.2.10's geometry accepts it,
  without the fp-edge test (the class-2 fp carries no making-horizon information, k167);
* a resting quote stays while its side still wants one and the touch is less than REST_TICKS better than it.
Unchanged: C2Q (disarmed: v6.41 B2b's exit as before), pacing / the hard cap, the A1.9 exit layer, class 0, class 1.
FGK (research_v644_fgk; engine fgk=1): S14's realized-spread estimate is kept per class -- S14's tracker (C2Q's class
tracker, which still sees every fill: C2Q's evidence and q are unchanged) also splits its window by the fill's class
(ClassFeeTracker: the same estimator, release, window and MIN_FILLS per class), and a book's gate reads its own class's
estimate (a book outside the class map: the pooled one).  Off: v6.32's one pooled estimate.
C2QB (research_v644_c2qb; engine c2qb=1, k171): C2Q's evidence decision disarms class 2 only when the one-sided upper
bound of D = sum_k (R_k - thr q C_k) lies below 0 -- k over the window's class-2 fills grouped by their 30-s fill-time
block, s_D cluster-robust (G / (G - 1)), Student t with G - 1 df at alpha = 0.05 / (window / 30 s) (5 % family-wise
over the window's looks); G < 2: armed.  The point rule's armed blocks and every probe are unchanged (D > 0 there).

STRUCTURAL throughout: the validator's clamp and class map, the engine keys' rules; every size, bound, band and rest
rule is the live agent's own (no constant sized from a sample).  No taking.  No coordination between UIDs."""
from __future__ import annotations

import math
from typing import Any

from research_v617_touch_quote import REST_TICKS, SIDE_BUY, SIDE_SELL
from research_v6_32_fee_making import WINDOW_NS
from research_v6_32_fee_making import _Window
from research_v6_40_classes import ClassCaptureTracker
from research_v6210_touch_improve import improved_ask, improved_bid
from research_v631_touch_improve import IMPROVED_BUY, IMPROVED_SELL, JOIN_NO_VIEW, JOIN_NOT_INSIDE, JOIN_OWN_BEST

V644_C2T_VERSION = "c2t_v6_44"

WHY_CAPPED = "C2T_CAPPED"                 # the book's rolling volume is at the cap: the validator drops every placement
WHY_BOUND = "C2T_BOUND"                   # the side adds with |inventory| at or past the bound
WHY_PACED = "C2T_PACED"                   # the side adds while the book runs above its pace line
WHY_FEE_GATE = "C2T_FEE_GATE"             # S14 (class 2's own estimate with FGK) withholds the adding side
WHY_HELD = "C2T_HELD"                     # v6.19's cap-bound leading side / v6.41 B2b's probe hold
WHY_TOUCH_IDENTITY = "C2T_TOUCH_IDENTITY"  # D4: this UID keeps no touch quote on the book
WHYS = (WHY_CAPPED, WHY_BOUND, WHY_PACED, WHY_FEE_GATE, WHY_HELD, WHY_TOUCH_IDENTITY)
CANCEL_LEFT = "C2T_LEFT"                  # the touch moved REST_TICKS or more past the resting quote
CANCEL_OTHER = "C2T_OTHER_ORDER"          # an order of another layer of ours (deep layer, v6.3 roles) on the book
CANCELS = WHYS + (CANCEL_LEFT, CANCEL_OTHER)

_EPS = 1e-9
C2QB_BLOCK_NS = 30_000_000_000             # C2Q decides once per 30-s block; C2QB clusters the fills by the same blocks
C2QB_FAMILY = 0.05                         # STRUCTURAL: 5 % family-wise over the window's looks (Bonferroni)

__all__ = ["C2QB_BLOCK_NS", "CANCELS", "CANCEL_LEFT", "CANCEL_OTHER", "ClassFeeTracker", "V644_C2T_VERSION", "c2qb_alpha",
           "c2qb_bound", "t_quantile", "WHYS", "WHY_BOUND", "WHY_CAPPED",
           "WHY_FEE_GATE", "WHY_HELD", "WHY_PACED", "WHY_TOUCH_IDENTITY", "adds", "parse_switch", "resting_verdict",
           "side_plan", "touch_price"]


def _finite(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def parse_switch(value: Any) -> int | None:
    """A switch value as 0 / 1 ("0", "1", 0, 1, True, False); None when it is neither."""
    if isinstance(value, bool):
        return int(value)
    text = str(value).strip() if value is not None else "0"
    if text in ("0", "1"):
        return int(text)
    x = _finite(text)
    if x in (0.0, 1.0):
        return int(x)
    return None


def adds(side: str, inventory: Any) -> bool:
    """v6.23 S1's rule: a buy adds at inventory >= 0, a sell at inventory <= 0 (a flat book adds on both sides)."""
    inv = _finite(inventory) or 0.0
    if side == SIDE_BUY:
        return inv >= -_EPS
    if side == SIDE_SELL:
        return inv <= _EPS
    return False


def side_plan(side: str, *, inventory: Any, clip: Any, bound: Any, capped: bool, paced: bool, fee_gated: bool,
              held: bool, d4_off: bool) -> tuple[float, str | None]:
    """(size in class-0 units, None) for a side that takes a quote, else (0, why) -- the engine's c2t order: the cap,
    the bound on the adding side, its pace hold, S14, D4; then the live agent's v6.19 / v6.41 B2b hold on the adding side.
    Adding: ``clip``; reducing: max(``clip``, |inventory|)."""
    c, lim = _finite(clip), _finite(bound)
    inv = _finite(inventory) or 0.0
    if c is None or c <= 0.0 or lim is None:
        return 0.0, WHY_BOUND
    if capped:
        return 0.0, WHY_CAPPED
    add = adds(side, inv)
    if add and abs(inv) >= lim - _EPS:
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


def resting_verdict(side: str, price: Any, *, want: float, why: str | None, bid: Any, ask: Any, tick: Any,
                    rest_ticks: Any = REST_TICKS) -> str | None:
    """None when a resting touch quote of ours stays; otherwise why it is cancelled: its side takes no quote (the plan's
    reason) or the touch moved ``rest_ticks`` - 0.5 ticks or more past it (v6.17's band; the engine's ftk chase)."""
    if want <= 0.0:
        return why or WHY_BOUND
    p, b, a, t = _finite(price), _finite(bid), _finite(ask), _finite(tick)
    if p is None or b is None or a is None or t is None or t <= 0.0:
        return CANCEL_LEFT
    ahead = (b - p) / t if side == SIDE_BUY else (p - a) / t      # ticks the touch is better than the quote
    if ahead > float(rest_ticks) - 0.5:
        return CANCEL_LEFT
    return None


def touch_price(side: str, view: Any, tick: Any, *, join_price: Any) -> tuple[float | None, str]:
    """(price, reason) of a new C2T quote on ``side``: the others' best one tick better when v6.2.10's geometry accepts it
    (strictly inside every opposite order, ours included, and strictly on our side of the others' mid) and it beats
    ``join_price``; else ``join_price`` -- v6.31 S12b's rule without its fp-edge test.  Pure; never raises."""
    t, join = _finite(tick), _finite(join_price)
    if join is None or join <= 0.0:
        return None, JOIN_NO_VIEW
    if view is None or t is None or t <= 0.0:
        return join, JOIN_NO_VIEW
    try:
        ob, oa = getattr(view, "others_bid", None), getattr(view, "others_ask", None)
        if side == SIDE_BUY:
            cand = improved_bid(others_bid=ob, others_ask=oa, raw_ask=getattr(view, "raw_ask", None), tick=t)
        elif side == SIDE_SELL:
            cand = improved_ask(others_bid=ob, others_ask=oa, raw_bid=getattr(view, "raw_bid", None), tick=t)
        else:
            return None, JOIN_NO_VIEW
    except Exception:
        return join, JOIN_NO_VIEW
    if cand is None:
        return join, JOIN_NOT_INSIDE
    better = cand > join + 0.5 * t if side == SIDE_BUY else cand < join - 0.5 * t
    if not better:
        return join, JOIN_OWN_BEST
    return float(cand), (IMPROVED_BUY if side == SIDE_BUY else IMPROVED_SELL)


class ClassFeeTracker(ClassCaptureTracker):
    """FGK's tracker: C2Q's ClassCaptureTracker (every base result -- S14's pooled estimate, counts, K3Q's sums and
    C2Q's class sums -- the base classes' exactly) plus S14's window split by the fill's class: each counted fill's
    (fill ts, realized, notional), as the base window took it, also enters its class's window, which expires, shifts
    and restarts with the base window.  ``class_estimate(c)`` is S14's estimator on class c's window alone (MIN_FILLS of
    that class's fills before it reads anything)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fgk: dict[int, _Window] = {}

    def _release_head(self, st: dict, now, *, forced: bool = False) -> bool:
        counted = self.counts["counted"]
        done = super()._release_head(st, now, forced=forced)
        if done and self.counts["counted"] > counted and self.window.entries:
            ts, realized, notional = self.window.entries[-1]
            cls = int(self.class_of.get(int(st.get("k3q_book", -1)), -1))
            self.fgk.setdefault(cls, _Window()).add(ts, realized, notional)
        return done

    def release(self, now_ns, *, force: bool = False) -> int:
        out = super().release(now_ns, force=force)
        now = _finite(now_ns)
        if now is not None:
            for w in self.fgk.values():
                w.expire(int(now) - self.window_ns)
        return out

    def set_active(self, active, now_ns) -> bool:
        started = super().set_active(active, now_ns)
        if started:
            self.fgk = {}                       # the base window starts empty: so does each class's
        return started

    def begin(self, now_ns) -> bool:
        last = self.last_now
        reset = super().begin(now_ns)
        now = _finite(now_ns)
        if reset and last is not None and now is not None:
            for w in self.fgk.values():
                w.shift(int(now) - int(last))   # the base shifts its window: so does each class's
        return reset

    def class_estimate(self, c: int) -> float | None:
        """S14's estimate on class ``c``'s fills alone: realized / notional over its window, None below MIN_FILLS."""
        w = self.fgk.get(int(c))
        return None if w is None else w.estimate(self.min_fills)

    def class_fills(self, c: int) -> int:
        w = self.fgk.get(int(c))
        return 0 if w is None else len(w.entries)


# ---- C2QB: the bound (k171 vm771 _c2qb_betacf / _c2qb_tsf / _c2qb_tq / _c2qb_arm, the same arithmetic) --------------

def _betacf(a: float, b: float, x: float) -> float:
    """The continued fraction of the regularized incomplete beta (Lentz), as vm771's _c2qb_betacf."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 401):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c if abs(c) > 1e-300 else 1.0 + aa / 1e-300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c if abs(c) > 1e-300 else 1.0 + aa / 1e-300
        de = d * c
        h *= de
        if abs(de - 1.0) < 3e-16:
            break
    return h


def t_sf(x: float, nu: float) -> float:
    """P(T_nu > x) for x >= 0 (vm771's _c2qb_tsf)."""
    z = nu / (nu + x * x)
    a, b = nu / 2.0, 0.5
    if z <= 0.0:
        return 0.0
    if z >= 1.0:
        return 0.5
    lb = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(z) + b * math.log(1.0 - z)
    if z < (a + 1.0) / (a + b + 2.0):
        ib = math.exp(lb) * _betacf(a, b, z) / a
    else:
        ib = 1.0 - math.exp(lb) * _betacf(b, a, 1.0 - z) / b
    return 0.5 * ib


_TQ: dict = {}


def t_quantile(df: int, alpha: float) -> float:
    """The (1 - alpha) quantile of Student t with ``df`` degrees of freedom (bisection; cached), vm771's _c2qb_tq."""
    key = (int(df), float(alpha))
    v = _TQ.get(key)
    if v is None:
        lo, hi = 0.0, 1.0
        while t_sf(hi, float(df)) > alpha:
            hi *= 2.0
        for _i in range(200):
            mid = 0.5 * (lo + hi)
            if t_sf(mid, float(df)) > alpha:
                lo = mid
            else:
                hi = mid
        v = _TQ[key] = 0.5 * (lo + hi)
    return v


def c2qb_alpha(window_ns: Any = WINDOW_NS) -> float:
    """alpha = 0.05 / (the window / 30 s): vm771's C2QB_ALPHA at fgwin = the window."""
    w = _finite(window_ns)
    looks = (w / C2QB_BLOCK_NS) if w is not None and w > 0.0 else (WINDOW_NS / C2QB_BLOCK_NS)
    return C2QB_FAMILY / looks


def c2qb_bound(entries: Any, cls: int, th: Any, alpha: float) -> dict[str, Any]:
    """C2QB on the window's fills ``entries`` ((fill ts, class, R, C), ClassCaptureTracker.v640): class ``cls``'s fills
    clustered by their 30-s fill-time block; D_k = R_k - th C_k, D = sum D_k, s_D = sqrt(G / (G - 1) sum (D_k - D / G)^2),
    ub = D + t_{G-1}(1 - alpha) s_D; armed iff not ub < 0 (G < 2: armed, no bound).  Returns {armed, G, D, sD, tq, ub}."""
    t = _finite(th)
    out: dict[str, Any] = {"armed": True, "G": 0, "D": None, "sD": None, "tq": None, "ub": None}
    if t is None:
        return out
    cl: dict[int, list] = {}
    for ts, c, r, k in entries or ():
        if int(c) != int(cls):
            continue
        e = cl.get(int(ts) // C2QB_BLOCK_NS)
        if e is None:
            e = cl[int(ts) // C2QB_BLOCK_NS] = [0.0, 0.0]
        e[0] += float(r)
        e[1] += float(k)
    G = len(cl)
    out["G"] = G
    if G < 2:
        return out
    dk = [e[0] - t * e[1] for e in cl.values()]
    D = math.fsum(dk)
    m = D / G
    sD = math.sqrt(G / (G - 1.0) * math.fsum([(x - m) ** 2 for x in dk]))
    tq = t_quantile(G - 1, alpha)
    ub = D + tq * sD
    out.update(armed=not (ub < 0.0), D=D, sD=sD, tq=tq, ub=ub)
    return out
