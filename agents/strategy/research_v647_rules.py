# SPDX-License-Identifier: MIT
"""v6.47: v6.46 as built + the Round 5 Stage 4 pass 10 REPORTED arm S4P10P (USER 11-02 ~10:2x JST "I decided option C")
-- the switches and the class-0 rule R11SAG, function for function from the replay engine (scratchpad round5/vm_r5c12.py
7f3b39e4, classes 0 + 2 = round5/s1r7/q/q_R7C2RQ.txt: the R11SAG spec ',s5sa=2' + ',c2rq=1,c2dfc=1'), each behind its own
research_v647_* switch, code default 0 (off: v6.46 on every path).  The class-2 rule (C2S2 in option-P order, the live form
of c2rq) is research_v647_c2s2's.

Class 0 -- R11SAG (research_v647_s5sa, engine s5sa; round5/stage3_winner.txt, STAGE3.md sec. r11):
* a class-0 book's deep side (level 0 + the ladder) holds its orders for the rest of the 30-s fp block while (a) it is the
  k48 AGAINST side (|ln(fp / mid)| >= aggap: the sell side when fp is above the mid, the buy side below -- the live v6.23 S1
  against side at research_v623_against_gap_bps = the engine's aggap 2.3 bp), (b) the block's fp step (the agent's own
  step record, v6.36's StepRecord = the engine's _k101_steps) moved the fundamental >= aggap against it (an ask after a
  step up: step >= aggap; a bid after a step down: step <= -aggap), and with value 1 only (c) it REDUCES the TRUE inventory
  (an ask while inv > EPS, a bid while inv < -EPS).  Value 2 (SAG, PARAMS) drops (c).  The engine's s5saf (front phase only)
  is NOT built.  Engine prerequisites: the k48 against side (AG_ON) and the step record (pst / ssd).

STRUCTURAL: the engine's arithmetic (EPS 1e-9, the >= at the gap and at the step), the validator-published fundamental, the
simulator's 30-s fp step and the agent's own step record; no constant here is sized from a sample."""
from __future__ import annotations

import math
from typing import Any

from research_v63_trend_target import SIDE_BUY, SIDE_SELL

V647_RULES_VERSION = "rules_v6_47"
EPS = 1e-9                                   # the engine's EPS (vm_r5c12)

SWITCHES = ("research_v647_s5sa", "research_v647_c2s2", "research_v647_c2s2p")
VALUES = {"research_v647_s5sa": (0, 1, 2), "research_v647_c2s2": (0, 1, 2), "research_v647_c2s2p": (0, 1)}

__all__ = ["EPS", "SWITCHES", "V647_RULES_VERSION", "VALUES", "parse_value", "sa_sides", "signed_step", "step_adverse"]


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def parse_value(value: Any, allowed: tuple = (0, 1)) -> int | None:
    """A switch value among ``allowed`` ("2", 2, 2.0, True / False for 1 / 0; missing / empty = 0); None otherwise."""
    if value is None:
        return 0
    if isinstance(value, bool):
        v = int(value)
        return v if v in allowed else None
    text = str(value).strip()
    if text == "":
        return 0
    x = _finite(text)
    if x is None or x != int(x) or int(x) not in allowed:
        return None
    return int(x)


# ---- class 0: R11SAG ---------------------------------------------------------------------------------------------

def signed_step(record: Any, book_id: Any, block: Any) -> float | None:
    """This block's signed fp step of ``book_id`` in bps (the engine's STEPV[b] while STEP_BLK == the state's block), read
    from v6.36's StepRecord: the record's step block must be ``block`` and the book must have a step there (its sign in
    ``direction``, its |step| the newest entry of its trailing record, both written at that step); else None (the
    engine's 0: no hold).  Never raises."""
    try:
        if record is None or block is None or getattr(record, "step_block", None) is None:
            return None
        if int(record.step_block) != int(block):
            return None
        b = int(book_id)
        d = (getattr(record, "direction", None) or {}).get(b)
        if d is None:
            return None
        if int(d) == 0:
            return 0.0
        q = (getattr(record, "steps", None) or {}).get(b)
        if not q:
            return None
        mag = _finite(q[-1])
        if mag is None:
            return None
        return float(mag) if int(d) > 0 else -float(mag)
    except Exception:
        return None


def step_adverse(side: str, step_bps: Any, aggap_bps: Any) -> bool:
    """The block's step moved the fundamental >= aggap AGAINST ``side``: an ask after step >= aggap, a bid after
    step <= -aggap (the engine: ``(_sv11 >= _ga11) if side == "ask" else (_sv11 <= -_ga11)``)."""
    s, g = _finite(step_bps), _finite(aggap_bps)
    if s is None or g is None:
        return False
    if side == SIDE_SELL:
        return s >= g
    if side == SIDE_BUY:
        return s <= -g
    return False


def sa_sides(against_side: Any, step_bps: Any, aggap_bps: Any, true_inventory: Any, mode: int) -> frozenset:
    """R11SAG / R11SAR: the deep sides held this state -- the k48 against side when the block's step is adverse to it
    (step_adverse) and, with mode 1, it reduces the TRUE inventory; mode 2 holds it either way; mode 0 holds nothing."""
    if int(mode or 0) not in (1, 2) or against_side not in (SIDE_BUY, SIDE_SELL):
        return frozenset()
    if not step_adverse(against_side, step_bps, aggap_bps):
        return frozenset()
    if int(mode) == 1:
        inv = _finite(true_inventory)
        inv = 0.0 if inv is None else inv
        reducing = (against_side == SIDE_BUY and inv < -EPS) or (against_side == SIDE_SELL and inv > EPS)
        if not reducing:
            return frozenset()
    return frozenset((against_side,))
