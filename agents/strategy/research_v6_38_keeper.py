# SPDX-License-Identifier: MIT
"""v6.38 layer 3, piece 3: the k118 class-1 keepers K1 / K3 / K3B / K3Q on v6.34's class-1 entry -- research_v638_c1
(mode) with research_v638_c1_arm, shipped 0.

Why (scratchpads k107, k118; k118/PREREG.md, sha256 e7cef6ce...): class 1 carries a quarter of both pay pools and v6.36
trades none; v6.34's join-at-the-touch entry (C3f) failed live (back of the queue, filled when swept).  Every class-1
touch quote lies inside v6.36's A radius, so A pulls it at second 29 and it never keeps a queue place across a step; the
keeper rests at the first price A does not pull.  The arms, as the engine k118/vm718.py runs them (k107 vm717 k1 + k118
k3 / k3b / k3q; definitions k107/PROGRESS.md 11:39 JST 10-19 + 11:43, k118/PROGRESS.md 05:33 JST 10-21):

* K1 (k1=1): on every class-1 book both sides (v6.29 S9 and S17-R's miner hold NOT applied); every other C3f gate kept
  (the cap, the inventory bound on the adding side, the c1p pace line and its paced unwind, S14 on the class-1 estimate,
  D4's class-1 draw, the legacy A1.9 size-shortfall cancel, post-only, the cancel-seen wait); sizes as C3f (adding the
  class-1 clip, reducing max(clip, |inv|)); price T = the price nearest the mid A would not pull -- buy: the largest tick
  price <= the best bid with mid - p > r x mid; sell: the smallest >= the best ask with p - mid > r x mid -- r the book's
  v6.36 A radius (its own median |fp step| from 30 steps, else the pooled one; none yet: no class-1 quote); a resting
  keeper stays while at most ONE tick farther from the mid than the current T (closer: kept), else it is cancelled and
  re-placed at T; life 3,600 s (the deep layer's, no 50-s backstop), no chase rule; A and D act on it as on every order.
* K3 (k1=1,k3=1): K1 on the S9 WITH-MOVE side only (the side the book's fundamental moved toward over S9's look-back);
  no fresh move: no keeper on either side; a move flip: the old side's keeper cancelled as unwanted.
* K3B (+k3b=1): no keeper on the book's capture-SURPLUS side over the making window (the agent's own making mirror,
  v6.19 S2m's estimator: k105 _k105_sur); balanced: no restriction.
* K3Q (+k3q=1): the keeper's ADDING side held while R1 - q0 x C1 < 0 -- R1 / C1 the UID's own class-1 fills' realized /
  captured, q0 = clip(R0 / C0, 0, 1) of its own class-0 fills, both net of W x the book's maker fee, over the making
  window (q_joint - q0 = (R1 - q0 C1) / (C0 + C1): the class-1 leg never lowers the shared q).  Cold start: admitted.
Per book: the canary (research_v6_38_canary, dim c1) over the UID's 32 class-1 books; an OFF book takes no class-1
quote (v6.36).  The live path additionally keeps the keeper out of v6.2.14's touch-life cancels and of the A1.9.1
reprice reasons other than the size shortfall: the engine's keeper has neither (its keep rule and 3,600-s life alone),
so without the exemption v6.2.14 S1 would cancel every keeper resting behind the touch within one state.

STRUCTURAL: v6.36's radius, the venue tick, the making window, the validator's horizon and W; PRE-REGISTERED: the one
tick of the keep rule, the 3,600-s life.  No constant here is sized from an observed metric.
"""
from __future__ import annotations

import math
from typing import Any

from collections import deque

from research_direct_exit_refresh import REASON_SIZE_SHORTFALL as SIZE_SHORTFALL
from research_v617_touch_quote import SIDE_BUY, SIDE_SELL
from research_v6_32_fee_making import RealizedTracker, _floored_mid_at
from research_v6_34_class1 import CANCEL_RESIZE, WHY_BOUND
from research_v6_38_canary import ARM_K1, ARM_K3, ARM_K3B, ARM_K3Q

V6_38_KEEPER_VERSION = "keeper_v6_38_l3"
LIFE_NS = 3_600_000_000_000      # PRE-REGISTERED (k107 K1): life 3,600 s, the deep layer's (no 50-s touch backstop)
KEEP_TICKS = 1                   # PRE-REGISTERED (k107 K1): kept while at most one tick farther from the mid than T
RADIUS_EPS = 1e-12               # vm711 / vm717's slack: |p - mid| <= r x mid + 1e-12 is inside the radius
EPS = 1e-9                       # vm717's float slack in the keep rule
WHY_NO_RADIUS = "K1_NO_RADIUS"           # no A radius yet: no class-1 quote
WHY_NO_MOVE = "K3_NO_MOVE"               # K3: no fresh S9 move -- no keeper on either side
WHY_AGAINST_MOVE = "K3_AGAINST_MOVE"     # K3: the side the move is against
WHY_SURPLUS = "K3B_SURPLUS"              # K3B: the book's capture-surplus side
WHY_PARITY = "K3Q_PARITY"                # K3Q: the adding side while the class-1 leg would lower the shared q
CANCEL_AWAY = "K1_AWAY"                  # a resting keeper more than one tick farther than T (or no T)

__all__ = ["CANCEL_AWAY", "CaptureTracker", "EPS", "KEEP_TICKS", "LIFE_NS", "RADIUS_EPS", "SIZE_SHORTFALL",
           "V6_38_KEEPER_VERSION", "WHY_AGAINST_MOVE",
           "WHY_NO_MOVE", "WHY_NO_RADIUS", "WHY_PARITY", "WHY_SURPLUS", "arm_parts", "keeps", "move_side",
           "parity_hold", "q0_of", "side_gate", "target", "verdict"]


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def arm_parts(arm: Any) -> dict[str, bool]:
    """What an arm runs: keeper (every arm), k3 (K3, K3B, K3Q), k3b (K3B), k3q (K3Q)."""
    return {"keeper": arm in (ARM_K1, ARM_K3, ARM_K3B, ARM_K3Q), "k3": arm in (ARM_K3, ARM_K3B, ARM_K3Q),
            "k3b": arm == ARM_K3B, "k3q": arm == ARM_K3Q}


def target(side: str, bid: Any, ask: Any, radius: Any, tick: Any, decimals: Any = 2) -> float | None:
    """vm717 _k1_target(B, side, r, 0): buy -- the largest tick price <= the best bid with mid - p > r x mid; sell --
    the smallest tick price >= the best ask with p - mid > r x mid; None without a radius or a two-sided book."""
    b, a, r, t = _finite(bid), _finite(ask), _finite(radius), _finite(tick)
    if r is None or b is None or a is None or t is None or t <= 0.0 or not b < a:
        return None
    try:
        d = int(decimals)
    except (TypeError, ValueError):
        d = 2
    mid = 0.5 * (b + a)
    if side == SIDE_BUY:
        p = round(math.floor(mid * (1.0 - r) / t + 1e-9) * t, d)
        while mid - p <= r * mid + RADIUS_EPS:
            p = round(p - t, d)
        return round(min(p, b), d)
    if side == SIDE_SELL:
        p = round(math.ceil(mid * (1.0 + r) / t - 1e-9) * t, d)
        while p - mid <= r * mid + RADIUS_EPS:
            p = round(p + t, d)
        return round(max(p, a), d)
    return None


def keeps(side: str, price: Any, target_price: Any, tick: Any) -> bool:
    """K1's keep rule: a resting keeper stays while at most KEEP_TICKS farther from the mid than the current target
    (closer: kept); no target or an unreadable price: not kept."""
    p, tp, t = _finite(price), _finite(target_price), _finite(tick)
    if p is None or tp is None or t is None:
        return False
    if side == SIDE_BUY:
        return not p < tp - KEEP_TICKS * t - EPS
    if side == SIDE_SELL:
        return not p > tp + KEEP_TICKS * t + EPS
    return False


def verdict(side: str, price: Any, remaining: Any, want: Any, why_unwanted: Any, *, is_paced: bool, target_price: Any,
            tick: Any) -> str | None:
    """A resting keeper's verdict: its side takes no quote (the side's reason), paced and larger than the unwind size
    (v6.34's C1_RESIZE), or away from the target (CANCEL_AWAY); None: it stays.  No chase rule (v6.34's REST_TICKS)."""
    w = _finite(want)
    if w is None or w <= EPS:
        return str(why_unwanted) if why_unwanted else WHY_BOUND
    r = _finite(remaining)
    if is_paced and r is not None and r > w + EPS:
        return CANCEL_RESIZE
    if not keeps(side, price, target_price, tick):
        return CANCEL_AWAY
    return None


def move_side(s9_held: Any) -> str | None:
    """K3's with-move side: the side opposite the one v6.29 S9 holds (S9 holds the side the move is against); None
    without a fresh move (S9 holds nothing)."""
    if s9_held == SIDE_BUY:
        return SIDE_SELL
    if s9_held == SIDE_SELL:
        return SIDE_BUY
    return None


def q0_of(r0: Any, c0: Any) -> float:
    """The UID's class-0 quality q0 = clip(R0 / C0, 0, 1); C0 <= 0 (or unreadable): 0."""
    r, c = _finite(r0), _finite(c0)
    if r is None or c is None or c <= 0.0:
        return 0.0
    return min(1.0, max(0.0, r / c))


def parity_hold(r1: Any, c1: Any, q0: Any) -> bool:
    """K3Q: hold the adding side while R1 - q0 x C1 < 0 (both sums 0 at a cold start: 0 < 0 is false -- admitted)."""
    r, c, q = _finite(r1), _finite(c1), _finite(q0)
    if r is None or c is None or q is None:
        return False
    return (r - q * c) < 0.0


def side_gate(side: str, *, arm: Any, move: Any, surplus: Any, adding: bool, hold: bool) -> str | None:
    """The keeper's own side rules after v6.34's plan (S9 / S17-R not applied): K3 -- the with-move side only (no move:
    neither); K3B -- not the capture-surplus side; K3Q -- not the adding side while ``hold``.  None: the plan stands."""
    parts = arm_parts(arm)
    if parts["k3"]:
        if move is None:
            return WHY_NO_MOVE
        if side != move:
            return WHY_AGAINST_MOVE
    if parts["k3b"] and surplus is not None and surplus == side:
        return WHY_SURPLUS
    if parts["k3q"] and adding and hold:
        return WHY_PARITY
    return None


class CaptureTracker(RealizedTracker):
    """K3Q's estimator: v6.32's RealizedTracker (S14's validator-exact realized at the 20-s horizon, its release and
    window rules) that also keeps, for every fill it counts, the fill's captured spread against the same capture mid the
    realized is clamped to (the tracker's floored mid), and nets both of W x the book's maker fee rate (``rates``, set
    by the agent each request from its accounts).  R = sum(realized - fee), C = sum(captured - fee) over the making
    window (entries leave with the tracker's own window).  Every base result (estimate, counts) is the base class's
    exactly: the base methods run unchanged and this class only reads what they computed."""

    def __init__(self, *args, weight: float = 0.0, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.weight = float(weight)
        self.rates: dict[int, float] = {}
        self.k3q: deque = deque()            # (fill ts, realized - fee, captured - fee)
        self.k3q_r = 0.0
        self.k3q_c = 0.0

    def _book(self, book_id: int) -> dict:
        st = super()._book(book_id)
        st.setdefault("k3q_book", int(book_id))
        return st

    def _release_head(self, st: dict, now, *, forced: bool = False) -> bool:
        rpend = st["rpend"]
        if not rpend:
            return super()._release_head(st, now, forced=forced)
        idx, _arrive, t, bought, _period = rpend[0]
        cap_mid = None
        try:
            cap_mid = _floored_mid_at(st, max(st["base"], idx), self.W)
        except Exception:
            cap_mid = None
        counted = self.counts["counted"]
        done = super()._release_head(st, now, forced=forced)
        if done and self.counts["counted"] > counted and self.window.entries:
            ts, realized, notional = self.window.entries[-1]
            p, q = float(t["p"]), float(t["q"])
            mid = float(cap_mid) if cap_mid is not None else p
            captured = (mid - p) * q if bought else (p - mid) * q
            fee = self.weight * float(self.rates.get(int(st.get("k3q_book", -1)), 0.0) or 0.0) * float(notional)
            self.k3q.append((int(ts), float(realized) - fee, captured - fee))
            self.k3q_r += float(realized) - fee
            self.k3q_c += captured - fee
        return done

    def _k3q_expire(self, cutoff: int) -> None:
        while self.k3q and self.k3q[0][0] < cutoff:
            _ts, r, c = self.k3q.popleft()
            self.k3q_r -= r
            self.k3q_c -= c
        if not self.k3q:
            self.k3q_r = self.k3q_c = 0.0

    def release(self, now_ns, *, force: bool = False) -> int:
        out = super().release(now_ns, force=force)
        now = _finite(now_ns)
        if now is not None:
            self._k3q_expire(int(now) - self.window_ns)
        return out

    def set_active(self, active, now_ns) -> bool:
        started = super().set_active(active, now_ns)
        if started:
            self.k3q, self.k3q_r, self.k3q_c = deque(), 0.0, 0.0     # the base window starts empty: so does K3Q's
        return started

    def begin(self, now_ns) -> bool:
        last = self.last_now
        reset = super().begin(now_ns)
        now = _finite(now_ns)
        if reset and last is not None and now is not None:
            delta = int(now) - int(last)
            self.k3q = deque((ts + delta, r, c) for ts, r, c in self.k3q)   # the base shifts its window: so does K3Q's
        return reset

    def sums(self) -> tuple[float, float]:
        """(R, C) of K3Q: realized and captured, net of W x fee, over the window."""
        return float(self.k3q_r), float(self.k3q_c)
