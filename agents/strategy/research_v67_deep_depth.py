# SPDX-License-Identifier: MIT
"""v6.7: the deep layer rests at the 97th percentile of each book's own sweep depths, not the 90th.

Why (mainnet, sim 20260924_1653; validator restarted ~00:26 JST 09-29 on upstream 77fcb30):

* The validator no longer pays a rank ladder.  ``making_pool = proportional_both`` (upstream c05768c) pays each uid half
  by its share of the field's captured spread (making_raw, post-P11) and half by its share of net alpha (the sum of
  its per-book alpha over every filled book) times its counterparty factor, among uids with positive skill on at least
  20 books.  Verified on the 09:18 JST scrape: the formula matches every uid's score share (corr 0.958); UID 94 with a
  trading score of 0.836 held 1.0% of emission at placement #32 because its making and net alpha were small.
* Volume is capped per book per simulation: ``capital_turnover_cap`` 10 x wealth (~500k quote), summed over
  ``trade_volume_assessment_period`` 86,400 sim-s (query.py drops every non-cancel instruction beyond it).  Pay is
  measured over the trailing 10,800 s, so what counts is pay per unit of volume with the budget spent evenly.
* Replay with that arithmetic (validator-exact making, net alpha, 2.3-bps hurdle eligibility and per-window P11 from
  the recorded takers; the cap enforced as the validator drops orders), two independent stretches of this simulation:
  at 2 base, resting at p97 instead of p90 raised the pay share +24.1% (latest 31 h) and +25.4% (09-27/28), ahead of
  the live 1-base p90 setting in every window (+13..+37%); p99 +22.5% / +23.8%; 1-base deeper +4-5% (budget left
  unused); larger clips overspend the cap; vacuum orders stay (turning them off cost 40-80%).  Deeper fills come more
  from the sweepers, so P11 fell ~3-5% -- already inside those numbers.

The rule (one switch, ``research_v67_deep_depth``): the deep layer's depth -- live orders and the paper record that
gates them alike, exactly as replayed -- is DEEP_QUANTILE of the book's own sweep depths.  STRUCTURAL direction: with a
fixed volume budget per simulation the value of a fill per unit of volume rises with its depth, so rest deeper and let
the clip (2 base, the launcher's default) spend the budget.  OBSERVED value: 0.97 from the grid above (p97 and p99
within noise; p97 higher on both stretches).  Rejected in the same grid: a budget-line depth controller (it quoted
closer whenever a book was behind its line and spent the budget on cheap fills) and a one-sided one (inert: vacuum
orders, which are not depth-quantile orders, carry ~94% of the volume).
"""
from __future__ import annotations

import math
from typing import Any

from research_v633_deep_layer import SWEEP_QUANTILE as V633_QUANTILE

V67_DEEP_DEPTH_VERSION = "deep_depth_v6_7"
DEEP_QUANTILE = 0.97          # OBSERVED: v6.7 grid r67b-r67i (p97 vs p90/p95/p99 at 1-2.5 base, two stretches)


def sweep_quantile(on: Any, quantile: Any = DEEP_QUANTILE) -> float:
    """The sweep-depth quantile the deep layer rests at: ``quantile`` when the switch is on, v6.3.3's p90 otherwise.
    Anything outside (0, 1) falls back to p90."""
    if not bool(on):
        return float(V633_QUANTILE)
    try:
        q = float(quantile)
    except (TypeError, ValueError):
        return float(V633_QUANTILE)
    if not math.isfinite(q) or not (0.0 < q < 1.0):
        return float(V633_QUANTILE)
    return q
