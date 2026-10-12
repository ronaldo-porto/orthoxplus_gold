# SPDX-License-Identifier: MIT
"""v6.25 S5: the fundamental-lean inventory target -- research_v625_lean.

Why (10-10 JST; scratchpad k57 phase 2: the real-fp replay vm657, switch ltau, scored with the validator's own skill
arithmetic against the same window's field; memory fleet-status-drop-1010 and realized-gap-root-cause-1010):

* The validator pays trading score = 0.5 x making rank + 0.5 x SKILL rank (positive scope), then the track-record
  EMA, the soft floor at the median standing and the Pareto by rank.  Skill is debeta's kappa_of_alpha over the books
  whose |alpha| clears the 2.3-bps hurdle of their filled notional (at least 20 such books); a book's alpha is the
  mark-to-market path integral minus mean inventory x drift.  All eleven of our UIDs score NEGATIVE skill
  (-0.15..-0.74), so their skill rank is 0; aged fp-anchored makers with our making (83 / 48 / 168 / 40: making
  170-267 against our 194-215) earn about twice our emission (0.66-0.89 against 0.35-0.37 per tempo) through skill
  rank 0.47-0.57 (board 06:06 JST 10-10).
* The drift strip makes a constant lean worth nothing: only the covariance of the inventory with the NEXT price move
  pays, and the expected move toward the published fundamental grows with its distance from the mid (v6.16: the mid
  follows ~0.2 x the gap per 60 sim-s).  An inventory proportional to the gap earns it.  The best makers do exactly
  that: +6..+14 base held toward the fundamental, growing with the gap up to 50+ bps (slope 0.33-0.41 base/bps; 0.20
  for UID 83); our books hold no lean (0.1 base per fp side).
* Replayed (vm657 ltau=2 = this rule; window 76,398-82,930 sim-s = 1.8 h, the real fundamental pages, S1 on / off,
  ranks against the same window's field):
    UID 237 (aged)   skill -0.274 -> +0.325 (77 kept books, 66% positive), trading 0.288 -> 0.435, making62 +22%
                     (S1 on); S1 off skill +0.372, trading 0.441, making62 +13%;
    UID 209 (young)  skill -0.351 -> +1.33 (72 kept, 90% positive), trading 0.396 -> 0.660, making62 +20..+26%;
    robustness (latency -10 / +10 ms, our own prints as the field's, S1 on / off, both UIDs): skill > 0 in 12 of 12
    cases (16 of 16 with the main runs), trading +0.13..+0.30, making62 +15..+26%.
  The round trip survives: the touch quote becomes the toward-ADDING leg (237: 212k -> 1,011k notional, R/C
  +0.93..+0.97), the deep ladder against the fundamental stays the reducing leg, against-adding fills vanish.
  Refuted on the same tape: a fixed lean (8 / 16 base: skill stays negative), twice the room (-28% realized making),
  withholding the reducing against side (-16..-54%).  The replay's realized quality is optimistic (237 q 0.091 against
  live 0.041): the gains are relative.

The rule (one switch, ``research_v625_lean``, default on, and two values):

* Per book and request, with a fresh fundamental of this simulation (v6.16's feed and freshness rule, read exactly as
  v6.23 S1 reads it) at least ``research_v623_against_gap_bps`` (2.3, S1's gate) from the mid, the book's inventory
  centre is
      tau = sign(fp - mid) x min(cap, slope x |log(fp / mid)| x 1e4 / gap_bps)   base,
  positive (long) when the fundamental is above the mid; 0 without a fresh fundamental, inside the gap, on a book of
  another asset class (v6.23 S2) or on an error.  The fundamental flips: tau flips.
* Every MAKING decision of the pass reads (inventory - tau) where it read the inventory: the touch quote's adding test
  and bound (v6.17), S1's adding test (v6.23), the cap-bound surplus hold (v6.19), pacing's adding side (v6.4.1 under
  the v6.20 room line / v6.22 cap-window pacing, v6.9 S3's deepest level), the deep bound (v6.10, v6.6.1's scaled
  bound), the reducing side's half depth (v6.12) and the add spacing (v6.6) -- its fill-side bookkeeping with the tau
  those fills rested under (the previous request's).  So the side toward the fundamental builds the position up to tau
  plus its usual room, and the side against it unwinds only down to tau (vm657 ltau=2, ltc = slope, ltk = cap / slope).
* The TRUE inventory stays everywhere else: the final validator's exposure cap and book caps, the venue's free base and
  quote, the legacy A1.9.1 exit layer (its size-shortfall cancel of a touch quote reducing the true position, as vm657
  a19x), the partial-fill and dust paths (positions below the minimum order), the v6.3 fallback and the v6.3.3
  inventory gate (both inert under v6.4 / v6.8), and every ledger, lot and log.
* Off, or on a book whose tau is 0 now and was 0 at the previous request, every decision is v6.24's.

STRUCTURAL: the validator's drift strip (only gap-proportional inventory earns), the gap unit (S1's gate = the touch
quote's 2.3-bps threshold = the validator's alpha hurdle), the cap (LEAN_CAP = the deep layer's bound behind its
volume line: v6.10's 24 deep clips x 2.0 base = 48 base, the touch quote's bound there -- vm657's ftb and deepmax x
deepc: no book leans further than it may already hold).  OBSERVED: the slope (LEAN_SLOPE, 1.0 base per gap unit =
0.43 base/bps -- the best makers' measured lean 0.33-0.41 base/bps, k57 C1K48; 1.0 base is this agent's v6.3 clip
base, but no live order rests at that size: the deep, touch and ladder-level-1 clip is 2.0 base, level 2 4.0).  The
structural alternative is 2.0 base per unit (the deep / touch clip) to the same 48 base (k57 G24: skill > 0 in 12 of
12, about half C1K48's gain).
"""
from __future__ import annotations

import math
from typing import Any

V625_LEAN_VERSION = "lean_v6_25"
LEAN_SLOPE = 1.0     # OBSERVED: base of lean per gap unit (k57 C1K48: 0.43 base/bps; the best makers 0.33-0.41)
LEAN_CAP = 48.0      # STRUCTURAL: the most |tau| may be -- the deep / touch bound behind the line (24 x 2.0 base)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


# ---- the switches' values --------------------------------------------------------------------------------------------

def parse_slope(value: Any) -> float:
    """research_v625_lean_slope: base of lean per gap unit, a finite number > 0; anything else is 0 (no lean)."""
    v = _finite(value)
    return v if v is not None and v > 0.0 else 0.0


def parse_cap(value: Any) -> float:
    """research_v625_lean_cap: the most |tau| may be, in base, a finite number > 0; anything else is 0 (no lean)."""
    v = _finite(value)
    return v if v is not None and v > 0.0 else 0.0


# ---- S5: the rule ----------------------------------------------------------------------------------------------------

def lean_target(mid: Any, fundamental: Any, gap_bps: Any, slope: Any, cap: Any) -> float:
    """The book's inventory centre tau in base: sign(fp - mid) x min(cap, slope x |gap| / gap_bps), gap = log(fp / mid)
    in bps (+: the fundamental above the mid, a LONG target).  0.0 inside the gap (|gap| < gap_bps), at the mid, and
    without a usable input (a mid or fundamental that is not a positive number; a gap unit, slope or cap not > 0)."""
    m, f, g, k, c = _finite(mid), _finite(fundamental), _finite(gap_bps), _finite(slope), _finite(cap)
    if m is None or f is None or g is None or k is None or c is None:
        return 0.0
    if m <= 0.0 or f <= 0.0 or g <= 0.0 or k <= 0.0 or c <= 0.0:
        return 0.0
    gap = math.log(f / m) * 1e4
    if not math.isfinite(gap) or abs(gap) < g:
        return 0.0
    return math.copysign(min(c, k * abs(gap) / g), gap)


# ---- the state row ---------------------------------------------------------------------------------------------------

def new_request() -> dict[str, Any]:
    """One request's tally of the books the rule read."""
    return {"books": 0, "fresh_fp_books": 0, "tau_books": 0, "abs_tau_sum": 0.0, "abs_inv_minus_tau_sum": 0.0,
            "max_abs_tau": 0.0}


def note_book(req: Any, *, fresh: Any, tau: Any, inventory: Any) -> None:
    """Count one book: read, with a fresh fundamental, and -- with a nonzero tau -- its |tau| and |inventory - tau|.
    Never raises (a tally that is not one, or an unreadable value, counts nothing)."""
    if not isinstance(req, dict):
        return
    try:
        req["books"] += 1
        if fresh:
            req["fresh_fp_books"] += 1
        t = _finite(tau)
        if t is None or t == 0.0:
            return
        req["tau_books"] += 1
        req["abs_tau_sum"] += abs(t)
        req["max_abs_tau"] = max(float(req["max_abs_tau"]), abs(t))
        inv = _finite(inventory)
        if inv is not None:
            req["abs_inv_minus_tau_sum"] += abs(inv - t)
    except Exception:
        return


def summary(req: Any) -> dict[str, Any]:
    """The row's view of one request: books read, books with a nonzero tau, their mean |tau|, mean |inventory - tau|
    and max |tau| (base), and the share of the books read with a fresh fundamental.  Never raises."""
    out: dict[str, Any] = {"books": 0, "tau_books": 0, "mean_abs_tau": None, "mean_abs_inv_minus_tau": None,
                           "max_abs_tau": 0.0, "fresh_fp_share": None}
    try:
        if not isinstance(req, dict):
            return out
        books, lean = int(req.get("books", 0) or 0), int(req.get("tau_books", 0) or 0)
        out["books"], out["tau_books"] = books, lean
        out["max_abs_tau"] = round(float(req.get("max_abs_tau", 0.0) or 0.0), 4)
        if lean > 0:
            out["mean_abs_tau"] = round(float(req.get("abs_tau_sum", 0.0) or 0.0) / lean, 4)
            out["mean_abs_inv_minus_tau"] = round(float(req.get("abs_inv_minus_tau_sum", 0.0) or 0.0) / lean, 4)
        if books > 0:
            out["fresh_fp_share"] = round(float(req.get("fresh_fp_books", 0) or 0) / books, 4)
    except Exception:
        return out
    return out


__all__ = ["LEAN_CAP", "LEAN_SLOPE", "V625_LEAN_VERSION", "lean_target", "new_request", "note_book", "parse_cap",
           "parse_slope", "summary"]
