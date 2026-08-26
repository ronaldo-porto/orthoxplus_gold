# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research same-side suppression starts in CAUTION, disables by DEFENSIVE."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)
SKEWED = RESEARCH_SRC.split("def _place_skewed_quotes(")[1].split(
    "def _place_directional_round_trip("
)[0]
PRICES = RESEARCH_SRC.split("def skewed_quote_prices(")[1].split(
    "def _compute_close_score("
)[0]

from research_inventory_state import (
    CAUTION_ENTRY_MULT,
    STATE_CAUTION,
    STATE_DEFENSIVE,
    STATE_EMERGENCY,
    STATE_EXIT_ONLY,
    STATE_NORMAL,
    inventory_state_policy,
    side_size_multiplier,
)
from research_same_side import (
    CAUTION_EXIT_PRIORITY,
    CAUTION_SAME_PRIORITY,
    SAME_SIDE_VERSION,
    apply_exit_competitiveness,
    apply_fill_priority,
    same_side_suppression,
    side_is_suppressed,
)


def test_caution_starts_same_side_suppression():
    long_s = same_side_suppression(STATE_CAUTION)
    assert long_s.same_side_disabled is False
    assert long_s.same_side_size_mult == CAUTION_ENTRY_MULT
    assert long_s.same_side_priority == CAUTION_SAME_PRIORITY
    assert long_s.exit_side_priority == CAUTION_EXIT_PRIORITY
    assert long_s.exit_side_priority > long_s.same_side_priority
    assert long_s.exit_improve_ticks >= 1.0
    buy, sell = apply_fill_priority(
        buy_fill=0.40, sell_fill=0.40, inventory_sign=1.0, suppression=long_s,
    )
    assert buy < 0.40
    assert sell > 0.40
    short_buy, short_sell = apply_fill_priority(
        buy_fill=0.40, sell_fill=0.40, inventory_sign=-1.0, suppression=long_s,
    )
    assert short_sell < 0.40
    assert short_buy > 0.40


def test_defensive_disables_same_side_before_emergency():
    defensive = same_side_suppression(STATE_DEFENSIVE)
    emergency = same_side_suppression(STATE_EMERGENCY)
    assert defensive.same_side_disabled is True
    assert defensive.same_side_priority == 0.0
    assert defensive.same_side_size_mult == 0.0
    assert emergency.same_side_disabled is True
    policy = inventory_state_policy(STATE_DEFENSIVE)
    assert policy.allow_same_side_entry is False
    assert policy.allow_inventory_increase is False
    assert side_size_multiplier(side="buy", inventory_sign=1.0, policy=policy) == 0.0
    assert side_size_multiplier(side="sell", inventory_sign=1.0, policy=policy) > 0.0
    assert side_is_suppressed(
        side="buy", inventory_sign=1.0, suppression=defensive,
    ) is True
    assert side_is_suppressed(
        side="sell", inventory_sign=1.0, suppression=defensive,
    ) is False
    buy, sell = apply_fill_priority(
        buy_fill=0.90, sell_fill=0.20, inventory_sign=1.0, suppression=defensive,
    )
    assert buy == 0.0
    assert sell >= 0.20


def test_exit_quote_becomes_more_competitive():
    caution = same_side_suppression(STATE_CAUTION)
    defensive = same_side_suppression(STATE_DEFENSIVE)
    bid, ask = apply_exit_competitiveness(
        bid_px=99.90, ask_px=100.20, best_bid=100.00, best_ask=100.10,
        tick_size=0.01, inventory_sign=1.0, suppression=caution, price_decimals=2,
    )
    assert ask <= 100.09
    assert ask < 100.20
    d_bid, d_ask = apply_exit_competitiveness(
        bid_px=99.90, ask_px=100.20, best_bid=100.00, best_ask=100.10,
        tick_size=0.01, inventory_sign=1.0, suppression=defensive, price_decimals=2,
    )
    assert d_ask <= ask
    short_bid, short_ask = apply_exit_competitiveness(
        bid_px=99.80, ask_px=100.20, best_bid=100.00, best_ask=100.10,
        tick_size=0.01, inventory_sign=-1.0, suppression=caution, price_decimals=2,
    )
    assert short_bid >= 100.01
    assert short_bid > 99.80
    normal = same_side_suppression(STATE_NORMAL)
    n_bid, n_ask = apply_exit_competitiveness(
        bid_px=99.90, ask_px=100.20, best_bid=100.00, best_ask=100.10,
        tick_size=0.01, inventory_sign=1.0, suppression=normal, price_decimals=2,
    )
    assert (n_bid, n_ask) == (99.90, 100.20)


def test_normal_does_not_suppress():
    normal = same_side_suppression(STATE_NORMAL)
    assert normal.same_side_disabled is False
    assert normal.same_side_priority == 1.0
    assert normal.exit_side_priority == 1.0
    buy, sell = apply_fill_priority(
        buy_fill=0.33, sell_fill=0.41, inventory_sign=1.0, suppression=normal,
    )
    assert (buy, sell) == (0.33, 0.41)
    exit_only = same_side_suppression(STATE_EXIT_ONLY)
    assert exit_only.same_side_disabled is True


def test_research_wires_same_side_suppression():
    assert "RESEARCH_SAME_SIDE_VERSION" in RESEARCH_SRC
    assert ("same_side_v2_effective_exposure" in RESEARCH_SRC or "same_side_v3_pending_guard" in RESEARCH_SRC)
    assert "research_enable_same_side_suppression" in RESEARCH_SRC
    assert "apply_fill_priority(" in SKEWED
    assert "same_side_suppression(" in SKEWED
    assert "apply_exit_competitiveness(" in PRICES
    assert "[S1R_SAME_SIDE]" in RESEARCH_SRC
    assert "SAME_SIDE" in RESEARCH_SRC
