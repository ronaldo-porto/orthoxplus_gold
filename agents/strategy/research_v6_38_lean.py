# SPDX-License-Identifier: MIT
"""v6.38 layer 3, piece 2: the k116 lean candidates -- research_v638_lean (mode) with research_v638_lean_arm (CA / CB /
CC / CD), shipped 0.

Why (scratchpad k116 PREREG.md sections 3 and 9, sha256 4d7a15c0...; PREREG_2 / PREREG_3): the leaders' class-0 alpha is
inventory held toward the fundamental that earns the price's convergence inside each 30-s step; ours leans 1.0 base per
hurdle unit (research_v625_lean.LEAN_SLOPE, OBSERVED) and builds the lean only through the deep layer.  The candidates,
as the engine k116/vm717.py runs them (strings appended to the replayed UID's BASE string):

* CA  slope 2 = ",ltc=2,ltk=24": the v6.25 lean target tau = sign(fp - mid) x min(48, 2.0 x |gap| / 2.3 bps) -- one deep
      clip (2.0 base) per hurdle unit (CA_SLOPE, PRE-REGISTERED); LEAN_CAP 48 unchanged (STRUCTURAL).  Every making
      decision that reads (inv - tau) follows (v6.25: the touch quote's side test and bound, S7, S2m, pacing, the deep
      bound, v6.12, v6.6).
* FBL (CC) the front-built lean = ",fpt=4,fpth=2.3,fptq=0,fptc=48": while the book's lean sign is set -- at |gap| >=
      2.3 bps (the hurdle, THRESHOLD_BPS) the sign of fp - mid; held while |gap| < 2.3; 0 without a fresh fundamental --
      the touch quote's clip grows to the lean shortfall: buy (tau - inv), sell (inv - tau), at most 48 (LEAN_CAP), when
      that is more than the clip; the deep layer is unchanged (fpt=4: the target steers the touch quote only).  The sign
      is the replay's deep-block state: updated once per state where the deep layer's sweep record is long enough, AFTER
      that state's touch decision (the touch quote reads the sign the previous state left -- the engine's order:
      touch decision, then the deep block, then the touch placement).
* CB  = CA + FBL;  CD = KBL + CA + FBL (KBL = research_v6_38_kbl, on the CD books).
Per book: the canary (research_v6_38_canary, dims CA / CB / CC / CD) over the UID's class-0 books.

STRUCTURAL: LEAN_CAP, the hurdle, the pre-registered strings.  No constant here is sized from an observed metric.
"""
from __future__ import annotations

import math
from typing import Any

from research_v617_touch_quote import SIDE_BUY, SIDE_SELL, THRESHOLD_BPS
from research_v625_lean import LEAN_CAP
from research_v6_38_canary import ARM_CA, ARM_CB, ARM_CC, ARM_CD

V6_38_LEAN_VERSION = "lean_v6_38_l3"
CA_SLOPE = 2.0                 # PRE-REGISTERED (k116 PREREG section 3, CA: ltc=2, ltk=24): one deep clip per hurdle unit
FBL_THRESHOLD_BPS = THRESHOLD_BPS   # PRE-REGISTERED fpth=2.3 = the touch quote's / the validator's alpha hurdle
FBL_CAP = LEAN_CAP             # PRE-REGISTERED fptc=48 = LEAN_CAP (STRUCTURAL)
FBL_TARGET = 0.0               # PRE-REGISTERED fptq=0: the shortfall is measured to the lean centre tau itself

__all__ = ["CA_SLOPE", "FBL_CAP", "FBL_TARGET", "FBL_THRESHOLD_BPS", "V6_38_LEAN_VERSION", "arm_parts", "fbl_clip",
           "fbl_sign"]


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def arm_parts(arm: Any) -> dict[str, bool]:
    """What an arm runs: slope2 (CA, CB, CD), fbl (CB, CC, CD), kbl (CD)."""
    return {"slope2": arm in (ARM_CA, ARM_CB, ARM_CD), "fbl": arm in (ARM_CB, ARM_CC, ARM_CD), "kbl": arm == ARM_CD}


def fbl_sign(previous: Any, fundamental: Any, mid: Any, threshold_bps: Any = FBL_THRESHOLD_BPS) -> int:
    """The lean sign after a state (vm711 fpt: B.ft_sign): with a usable fundamental, +1 / -1 by the sign of
    ln(fp / mid) once |gap| >= the threshold, else the previous sign; without one (None, not finite, not > 0) 0."""
    f, m = _finite(fundamental), _finite(mid)
    try:
        prev = int(previous or 0)
    except (TypeError, ValueError):
        prev = 0
    if f is None or f <= 0.0:
        return 0
    if m is None or m <= 0.0:
        return prev
    gap = math.log(f / m) * 1e4
    if abs(gap) >= float(threshold_bps):
        return 1 if gap > 0 else -1
    return prev


def fbl_clip(side: str, inventory: Any, clip: Any, sign: Any, cap: Any = FBL_CAP, target: Any = FBL_TARGET) -> float:
    """The touch quote's clip under FBL (vm711 fpt=4 fptc): with the sign set, the shortfall to the lean centre --
    buy: target x sign - inv, sell: inv - target x sign, inv being (inventory - tau) -- when it exceeds the clip, at most
    ``cap``; else the clip."""
    c = _finite(clip)
    c = 0.0 if c is None else c
    try:
        s = int(sign or 0)
    except (TypeError, ValueError):
        s = 0
    inv = _finite(inventory)
    if s == 0 or inv is None:
        return c
    t = float(target) * s
    short = (t - inv) if side == SIDE_BUY else (inv - t) if side == SIDE_SELL else 0.0
    if short > c:
        return min(float(cap), short)
    return c
