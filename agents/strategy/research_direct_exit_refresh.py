# SPDX-License-Identifier: MIT
"""Strategy1-Direct A1.8 maker-exit refresh helpers.

A1.8 is intentionally structural: it does not loosen Taker authority, entry
quality, size, exposure, or book-count limits.  It only prevents a profitable
Maker exit from being kept alive for several market publish cycles.

The existing Research path already computes one-cycle exit TTLs (QUIET and
ONE_AWAY).  A1.8 reuses those existing TTLs as the upper bound for Direct
profitable-exit persistence so the quote can be refreshed from the next market
state instead of remaining stale for the legacy 3s persistence window.
"""
from __future__ import annotations

import math
from typing import Any

DIRECT_EXIT_REFRESH_VERSION = "direct_exit_refresh_v4_16_2_a1_8"
DIRECT_A18_LEGACY_PROFITABLE_EXIT_TTL_MS = 3000.0


def _finite(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def cycle_bounded_profitable_exit_ttl_ms(
    *,
    legacy_persistent_ttl_ms: float,
    quiet_exit_ttl_ms: float,
    one_away_exit_ttl_ms: float,
) -> float:
    """Return the Direct profitable-exit persistence TTL for A1.8.

    Reuse the already-established exit-cycle TTLs rather than inventing a new
    observed threshold.  The result can only shorten the legacy persistence
    window; it can never lengthen it.
    """
    legacy = max(1.0, _finite(legacy_persistent_ttl_ms, DIRECT_A18_LEGACY_PROFITABLE_EXIT_TTL_MS))
    quiet = max(1.0, _finite(quiet_exit_ttl_ms, legacy))
    one_away = max(1.0, _finite(one_away_exit_ttl_ms, quiet))
    cycle_cap = max(quiet, one_away)
    return min(legacy, cycle_cap)
