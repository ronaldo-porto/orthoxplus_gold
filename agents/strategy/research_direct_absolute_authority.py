# SPDX-License-Identifier: MIT
"""A1.9.8: ABSOLUTE_PROTECTION keeps its taker against the loss-recovery makers.

Measured on the A1.9.7 run (log 20260913_185830, ticks 1-4,000, 256 round
trips).  Two A1.7.x overlays can replace the frozen A1.7.2
``ABSOLUTE_PROTECTION_REDUCE`` taker with a resting maker exit priced at a loss:

* A1.7.4 ``ABSOLUTE_RECOVERY_MAKER_EXIT`` -- maker net >= -35 bps and at least
  10 bps better than the taker, while no maker exit has failed yet;
* the A1.7.5 relative arm of the positive-maker veto -- maker net >= -35 bps and
  at least 15 bps better than the taker, for up to 60 ticks.

Both compare the maker's price with the taker's price at the same instant, and
neither prices the chance that the maker fills.  In this run 26 round trips got
one at their first ABSOLUTE evaluation: none was positive, 24 still ended on a
taker, and together they carried 58% of the run's cubic downside.  The resting
exit also costs a tick of evaluation (the book holds a live order), so book 95
went from -40 bps to -110..-130 bps before the taker fired, three times in 38
ticks.

A1.9.8 restores the frozen taker in exactly those two cases.  Unchanged:

* a positive maker keeps its priority -- the A1.7.2 grace (>= +1 bps) is the
  base decision itself, and the A1.7.4.4 absolute arm (>= +10 bps) is not a
  loss-recovery arm;
* HARD_ESCAPE and DEFENSIVE keep their recovery makers and the relative arm;
* a position that cannot be reduced (``ABSOLUTE_PROTECTION_PARK``) and
  catastrophic/MAX exposure stay on the frozen path.
"""
from __future__ import annotations

from typing import Any

from research_position_exit import ACTION_MAKER_EXIT, ACTION_TAKER_EXIT, BAND_ABSOLUTE

A198_ABSOLUTE_AUTHORITY_VERSION = "direct_absolute_authority_v4_16_2_a1_9_8"

A198_BASE_REASON = "ABSOLUTE_PROTECTION_REDUCE"
A198_RECOVERY_REASON = "ABSOLUTE_RECOVERY_MAKER_EXIT"
A198_VETO_REASON = "A1744_POSITIVE_MAKER_RISK_VETO"
A198_RELATIVE_CORRIDOR = "DIRECT_POSITIVE_MAKER_KAPPA_A175_RELATIVE"

ARM_RECOVERY_MAKER = "RECOVERY_MAKER"
ARM_RELATIVE_VETO = "RELATIVE_VETO"


def _token(decision: Any, name: str) -> str:
    return str(getattr(decision, name, "") or "")


def loss_recovery_arm(decision: Any) -> str | None:
    """Which loss-recovery arm turned this decision into a maker exit, if any."""
    if _token(decision, "action") != ACTION_MAKER_EXIT:
        return None
    reason = _token(decision, "reason")
    if reason == A198_RECOVERY_REASON:
        return ARM_RECOVERY_MAKER
    if reason == A198_VETO_REASON and _token(decision, "corridor_action") == A198_RELATIVE_CORRIDOR:
        return ARM_RELATIVE_VETO
    return None


def restore_absolute_taker(
    *, base_decision: Any, decision: Any, enabled: bool = True,
) -> tuple[Any, str | None]:
    """Return the decision to ship and the arm it overrode (None when unchanged).

    Only a frozen ``ABSOLUTE_PROTECTION_REDUCE`` base is restored, and only over
    one of the two loss-recovery arms.
    """
    if not enabled or base_decision is None or decision is None:
        return decision, None
    if _token(base_decision, "risk_band") != BAND_ABSOLUTE:
        return decision, None
    if _token(base_decision, "action") != ACTION_TAKER_EXIT or _token(base_decision, "reason") != A198_BASE_REASON:
        return decision, None
    arm = loss_recovery_arm(decision)
    if arm is None:
        return decision, None
    return base_decision, arm
