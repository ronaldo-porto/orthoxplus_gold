# SPDX-License-Identifier: MIT
"""v6.3.1 S1: v6.3's per-simulation state starts over when the validator starts a new simulation.

Why (found 2026-09-24 before the first mainnet sim reset under v6.3; sim 20260918_2028 ends at 86,400 sim-s):

* The v6.3 book stop (R5) pauses a book while its windowed alpha, read from the v6.2.11 own-alpha mirror, is below
  -0.5 x floor, and releases it above -0.25 x floor.  With no alpha for the book, ``stop_state`` keeps the old flag.
* At a new simulation the mirror discards its window (``OwnAlphaMirror.ingest_state`` resets on a clock that goes
  back more than REBASE_MIN_JUMP_NS), and ``book_alphas`` lists only books with own fills in the window.
* A paused book targets zero and quotes no making layer.  Flat at the start of the new simulation, it places
  nothing, never fills and never gets an alpha, so it stays paused for the rest of the process: 28 of 128 books
  were paused at tick 1,000 of the live run.
* The 120-state mid history spans the reset, so for ~120 states each book's target follows the jump between the
  two simulations' prices, not a trend.

The frozen SIM_ID_CHANGE transition clears a fixed list of runtime maps; v6.3's are not on it.  Neither a pause nor
a mid history means anything once the window it was measured over is gone, so both restart with the simulation.

The detector is the mirror's own rule (the same constant), plus a change of simulation id when both are known,
so the pauses reset exactly when the alpha window they were set from does.
"""

from __future__ import annotations

from typing import Any

from research_v6211_score_logic import REBASE_MIN_JUMP_NS

V631_SIM_RESET_VERSION = "sim_reset_v6_3_1"

REASON_SIM_ID_CHANGE = "SIM_ID_CHANGE"
REASON_CLOCK_REWIND = "CLOCK_REWIND"


def _int(value: Any) -> int | None:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def new_simulation(*, last_ts: Any, ts: Any, last_sim_id: Any, sim_id: Any,
                   min_jump_ns: int = REBASE_MIN_JUMP_NS) -> str | None:
    """Why this state belongs to a new simulation, or None when it continues the last one.

    A change of simulation id (both known) is a new simulation.  Otherwise a clock that went back more than
    ``min_jump_ns`` is one; a shorter rewind is a checkpoint rewind inside the same simulation (A1.9.9 owns it)
    and keeps the state.  The first state, and a state without a valid timestamp, never reset.
    """
    if last_sim_id and sim_id and str(last_sim_id) != str(sim_id):
        return REASON_SIM_ID_CHANGE
    now, last = _int(ts), _int(last_ts)
    if now is None or last is None:
        return None
    if now < last - int(min_jump_ns):
        return REASON_CLOCK_REWIND
    return None
