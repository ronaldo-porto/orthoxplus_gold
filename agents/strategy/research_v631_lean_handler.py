# SPDX-License-Identifier: MIT
"""v6.3.1 S2: under v6.3 the request no longer computes what only the retired entry/exit pipeline reads.

Why (live UID 94 on v6.3.0, RESPOND_TIMING p50 over ticks 200-1,656, measured 09-24):

* The handler takes ~260 ms of the ~1.3 s the validator times; its order delay rises with that time.
* v6.3's pass sets every order from the mid history, the venue position, the A1.9 ledger, the side ownership and
  the own-alpha mirror.  Yet each request still runs the frozen decision pipeline in ``Strategy1.respond``:
  a direction forecast for ~109 candidate books (full_predict 42.8 ms), book profiles and ranking (9.0 ms) and a
  market-regime classification (~0.9 ms plus two rows).  Their readers are the frozen acquisition branch, the frozen
  inventory/exit loop (which iterates nothing under v6.3), selection and regime themselves, and telemetry labels.
* The same response is registered in the quote store twice (once in ``_log_submitted_instructions``, again in
  ``Strategy1_Research.respond``): 9.8 ms, and the second record replaces the first at age 0, which hands the fill
  hazard a false censored observation per quote (74 QUOTE rows for 37 distinct quotes at tick 1,500).

What still runs, because something on the v6.3 path reads it: the fast screen (its inventory census drives the
A1.7.3 dust normalizer's admission, the v6.0.0 lot classes and V62_STATE), the markout evaluation (its ~34 ms is the
first parse of each book's touch, which the next reader would pay), ``update`` (it feeds the alpha mirror the book
stop reads), every pre- and post-pass, and one quote registration per response.

With the switch on, predictions are empty, the selection is empty and the regime is a fixed idle one; the frozen
build loop is given exactly what it already ignores under v6.3.
"""

from __future__ import annotations

V631_LEAN_HANDLER_VERSION = "lean_handler_v6_3_1"

IDLE_REGIME_MODE = "QUIET"      # the frozen classifier's own label for most live requests under v6.3


def empty_selection_fields() -> dict:
    """``BookSelection`` fields with no alpha, maintenance or avoid books and no profiles.

    Field dicts, not the dataclasses: this module stays importable without the miner framework (the strategy,
    which already imports ``DetailedTemplateAgent``, builds the objects).
    """
    return {"alpha_books": [], "maintenance_books": [], "avoid_books": [], "tier_counts": {}, "profiles": []}


def idle_regime_fields() -> dict:
    """``MarketRegime`` fields with the mode ``get_regime_params`` reads and no market claim."""
    return {
        "mode": IDLE_REGIME_MODE, "hold_frac": 0.0, "up_frac": 0.0, "down_frac": 0.0, "mean_score": 0.0,
        "mean_abs_score": 0.0, "mean_volatility": 0.0, "mean_trade_rate": 0.0, "mean_spread_bps": None,
        "mean_imbalance": 0.0, "mean_log_return": None, "return_dispersion": None, "direction_dispersion": 0.0,
        "tier_counts": {}, "inactive_frac": 0.0, "red_frac": 0.0, "green_frac": 0.0, "scoring_overlay": None,
        "confidence": 0.0, "book_count": 0,
    }
