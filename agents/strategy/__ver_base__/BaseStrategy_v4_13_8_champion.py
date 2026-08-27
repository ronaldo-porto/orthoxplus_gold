# SPDX-License-Identifier: MIT
"""BaseStrategy — frozen SN79 St6.4 V4.13.8 production champion.

Promotion chain:
    Strategy1_Research V4.13.8 (verified) -> BaseStrategy -> AdaptiveAgent

This module intentionally keeps the verified Research engine intact rather than
re-implementing or re-inlining it.  BaseStrategy is the stable production name
consumed by AdaptiveAgent; AdaptiveAgent continues to import only BaseStrategy.
"""
from __future__ import annotations

import os
import sys

_AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from taos.common.agents import launch
from Strategy1 import (
    BookArchetype,
    BookMemory,
    BookProfile,
    BookSelection,
    DirectionForecast,
    FillProbabilityEstimate,
    InventorySnapshot,
    MarketRegime,
    RegimeParamSet,
)
from Strategy1_Research import Strategy1_Research


class BaseStrategy(Strategy1_Research):
    """Frozen V4.13.8 Base champion for AdaptiveAgent promotion."""

    DEPLOY_POLICY_VERSION = "base_v4_13_8_champion"
    BASE_CHAMPION = True
    BASE_CHAMPION_FROZEN = True
    BASE_CHAMPION_PARENT = "simplified_kappa_productivity_v4_13_8"

    # Explicitly pin the promoted production contracts for release/preflight.
    KAPPA_PRODUCTIVITY_POLICY_VERSION = "simplified_kappa_productivity_v4_13_8"
    LANE_POLICY_VERSION = "authoritative_execution_lane_v4_13_4"
    EXIT_AUTHORITY_VERSION = "positive_maker_veto_v4_13_5"
    DENSITY_POLICY_VERSION = "completion_density_v4_13_6"
    QUALIFIED_CORE_EXACT_MIN_VERSION = "qualified_core_exact_min_v4_13_7"
    QUALIFIED_CORE_STALE_TTL_VERSION = "qualified_core_velocity_stale_ttl_v4_13_7"
    PROFITABLE_EXIT_PERSISTENCE_VERSION = "profitable_maker_exit_persistence_v4_13_8"


if __name__ == "__main__":
    launch(BaseStrategy)
