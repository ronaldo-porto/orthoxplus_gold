# SPDX-License-Identifier: MIT
"""v6.10 S2: the deep layer's per-book inventory bound follows the book's pacing state.

Why:

* STRUCTURAL.  The bound and the volume line answer opposite questions.  The line says how much volume a book may
  still spend before the simulation ends; the bound says how much inventory its deep layer may carry while it
  spends it.  v6.9 ties the bound to a constant (four deep clips, 8 base at the 2-base clip) whatever the line
  says, so a book that has banked a whole quiet hour of allowance -- and whose next sweep is the one the alpha
  half pays for -- stops averaging in at the same 8 base as a book that has already overspent.  The averaging-in
  is the position the reversion is collected on: a bound that does not move is the binding constraint precisely
  on the books that have the room to use it.
* The pacing state is already the agent's own: ``PaceLine.paced`` (v6.6 S2) is True when a book's reported volume
  is above its line plus one sampling window.  A book at or below its line is BEHIND (it may still spend); a
  paced book is AHEAD and keeps v6.9's bound, so S3 (the paced book keeps only its deepest order) is unchanged.
* OBSERVED (three windows of sim 20260929_2015, all ten fleet uids, the validator's pay arithmetic): the fleet's
  score share goes 3.08 -> 3.35% busiest, 1.87 -> 4.31% quiet, 3.87 -> 4.61% recovering, sim-time blended
  2.68 -> 4.03%, at a behind-bound of 48 deep clips.  Peak per-book inventory rises ~15 -> 48-84 base.

The rule (one switch, one causal change):

* ``research_v610_behind_max_clips`` -- the bound in deep clips for a book at or behind its volume line.  0 (the
  default) is v6.9: every book keeps ``research_v65_deep_max_clips`` whatever its pacing state, so the build is
  a no-op until the switch is set.  A value at or below the v6.9 bound is likewise v6.9 (the switch may only
  add room, never take it away).
* The exposure cap follows the LARGEST bound any book may take, not the one it happens to hold (STRUCTURAL): the
  final validator charges every order its worst-case fill against ``research_max_total_abs_base``, the cap is
  re-asserted once per request for the whole universe, and a cap sized for the paced bound would refuse the new
  room as ``STRICT_EXPOSURE_HEADROOM`` -- the same arithmetic v6.5's ``caps_for`` docstring sets out.
"""
from __future__ import annotations

import math
from typing import Any

V610_PACED_BOUND_VERSION = "paced_bound_v6_10"

BEHIND_MAX_CLIPS_OFF = 0.0          # the OFF sentinel: the bound does not follow the pacing state (v6.9)
BEHIND_MAX_CLIPS_DEFAULT = 24.0     # v6.10 ships ON: 24 deep clips = 48 base for a book behind its line


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def behind_clips(value: Any) -> float:
    """The configured behind-the-line bound in deep clips; 0.0 (off) for anything unreadable or not positive."""
    v = _finite(value)
    if v is None or v <= 0.0:
        return float(BEHIND_MAX_CLIPS_OFF)
    return v


def bound_clips(base_clips: Any, behind: Any, paced: Any) -> float:
    """A book's deep bound in deep clips: the behind-the-line bound while it is at or below its volume line, the
    v6.9 bound once it is paced (ahead).  Never below the v6.9 bound -- the switch only ever adds room."""
    base = max(0.0, _finite(base_clips) or 0.0)
    extra = behind_clips(behind)
    if bool(paced) or extra <= base:
        return base
    return extra


def caps_clips(base_clips: Any, behind: Any) -> float:
    """The bound the exposure cap must be sized for: the largest bound any book may take this request."""
    base = max(0.0, _finite(base_clips) or 0.0)
    return max(base, behind_clips(behind))
