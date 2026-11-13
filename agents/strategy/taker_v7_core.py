# SPDX-License-Identifier: MIT
"""v7.0 pure-taker core: the class-0 target-inventory rule of the mainnet class-0 winners (Round 21 track P).

Stdlib only -- no bittensor, taos or pydantic import -- so the tests run on any interpreter and the request path
touches nothing heavy.  TakerV7.py (the agent class) turns this module's actions into the validator's instructions.

Labels: STRUCTURAL = the validator / simulator code or config; COPIED = the mainnet winners' observed constants
(USER-allowed copy, Round 21 P REPORT.md section 2); MODEL = our own choice / inference; OBSERVED = recorded data.

The rule, per class-0 book and state (COPIED, P section 2 C0.1-C0.8):
  mid    = (best bid + best ask) / 2 from the state's book; the book is skipped without a fundamental f, without a
           two-sided book, or with a crossed / locked one (STRUCTURAL sanity of research_v6_32_state_fp.py: f finite
           > 0, a bid > 0 below the ask).
  gap_bp = 1e4 x (f - mid) / mid
  W      = quote_balance.initial + base_balance.initial x mid  (P's definition, coordinator-verified; W ~49.6-49.9k).
           Fallback: simulation_config.miner_wealth, which IS the per-book figure (STRUCTURAL: src067 query.py:505
           volume_cap = round(capital_turnover_cap x miner_wealth) is applied per book at query.py:586).
  inv    = base_balance.total - base_balance.initial  (units)
  T      = clip(K x gap_bp, -L, +L) x W / mid   (units; K 0.0035 per bp, L 0.55, COPIED)
  d      = T - inv;  |d| >= CLIP (7) -> side = sign(d), qty = BIG (15) if |d| >= BIG else CLIP (COPIED)
  clamp  : a buy to what free quote pays at the ask with a fee + rounding margin; a sell to free base; never leverage,
           never a loan; floor to the class's volume decimals; below the class's minimum order -> no order (STRUCTURAL)
  order  : IOC limit at the opposite touch (BUY at the best ask, SELL at the best bid), postOnly False, leverage 0,
           no loan settlement, requested delay 0; ONE instruction per book per state (STRUCTURAL: a second one on the
           same book is delayed +5-25 ms, src067 taos/im/validator/delays.py:129-138).

Pace guard (STRUCTURAL, the validator's cap; Round 21 R6's pace variant): per book a token bucket refilled at
CAP / 86,400 per sim-s with depth CAP x 10,800 / 86,400 (an order needs tokens >= its notional; fills spend them),
plus a rolling 86,400-sim-s ledger of our own filled notional (an order is not sent when ledger + notional > CAP).
The ledger is floored by the account's traded_volume, which the validator stamps from its own rolling volume_sums
(STRUCTURAL: src067 taos/im/neurons/validator.py:3258; trade value q x p, src067 taos/im/validator/trade.py:701-705).
Fills come from the state's own notices (TradeEvent 'ET' with takerAgentId / makerAgentId = our uid), the same
stream FinanceSimulationAgent.update dispatches to onTrade (taos/im/agents/__init__.py:846-855).

Class map (STRUCTURAL, src067 simulate/trading/run/config/multiasset_simulation_0.xml and
multiasset_simulation_0_64_32_32.xml): 256 books -> c0 0-127, c1 128-191, c2 192-255; 128 books -> c0 0-63, c1 64-95,
c2 96-127; any other count -> no orders.  When the state's config carries the validator's class list (the miner-side
``classes`` field, models.py:244-249, read as v6.40's class_table does, research_v6_40_classes.py:170-223), its first
class's books must equal the layout's class 0, else no orders (fail closed).  Class 0's grid (price / volume decimals,
minimum order) comes from that first class (entry, else its config), else the top-level config (v6.40: the home class
is the top-level configuration's).

Start-up cleanup (MODEL, the brief's rule): a book whose account shows resting orders gets ONE cancel instruction for
all of them in that state and no take; every class (testnet UID 68 inherits v6.47's resting orders).  Positions of
every class are left alone; class-1 / class-2 books are never traded.

v7.2 maker leg (Round 24; USER 11-13: "Build v7.2", taker core + maker leg side by side, split by CLASS as the mainnet
class-0 rule runners 5EA8Kgeh / 5HQqn3W9 run it): the classes named in v7_maker_classes ("none", "1" or "1,2") are
never taken; each of their books gets a flat two-sided post-only quote instead:
  quote  : ONE bid at the best bid and ONE ask at the best ask (COPIED: one order a side at the own-side touch),
           each v7_mk_notional of quote notional (COPIED 1,800: 5F1F2JMu's class-1 / class-2 order), postOnly, GTT
           with a v7_mk_expiry_s life (MODEL safety net: a dead miner leaves no quote behind for long).
  guard  : no bid priced more than v7_mk_guard_bp ABOVE f and no ask more than that BELOW f (COPIED 10 bp: the class-1
           winners have 0-1 % of their fills more than 10 bp on the wrong side of f, Round 24 M3g).
  flat   : inv x mid >= +v7_mk_band_clips x notional -> no bid; <= -that -> no ask (MODEL from OBSERVED: the
           runners' class-1 inventory stays flat, |posW| <= 0.012).
  pull   : on the last state before each fundamental step (phase K - 1 of the fp_update_period / publish_interval
           grid, STRUCTURAL as v6.36 reads it) every resting order is cancelled and none placed (COPIED: the step-pull
           makers have 0 fills in the last second before a step); the next state quotes the new touch.
  follow : a resting order that is not at this state's wanted price / side is cancelled and replaced in the same
           reply (a further instruction on one book is delayed +5-25 ms, STRUCTURAL delays.py:129-138).
  cap    : the same per-book pace guard; a maker fill spends the same ledger (one cap per book, STRUCTURAL).
A maker class needs the validator class list to confirm its books and grid, else that class is OFF and its books get
the start-up cleanup like any other (fail closed).  Maker books are disjoint from the taker classes (refused at start
otherwise): one inventory per (uid, book), so a taker on the same book would take every maker fill back.

v7.3 contrarian target of the maker leg (Round 25 track S; USER 11-13: "Build v7.3"), behind v7_mk_target (0 = the
v7.2 flat band, the code default; the launcher passes 1).  The band of the maker leg is centred on a target instead of
on zero; nothing else of the leg changes (quote, guard, pull, follow, cap as above):
  ref    : a slow reference price per maker book: an exponential average of the mid with time constant
           v7_mk_ref_s on the pace clock (STRUCTURAL 10,800 sim-s = the scoring lookback: an inventory offset that
           is constant over the window earns nothing, debeta.py:1646-1670 alpha = MTM - mean inventory x drift, so
           the reference only has to be slow against the window).  It follows the mid on every state with a
           two-sided book, pull states included.
  target : target = clip(-v7_mk_k x ret_bp, -v7_mk_l, +v7_mk_l) x W in quote notional, ret_bp = 1e4 x (mid - ref)
           / ref and W as the taker reads it (COPIED: the added-book winners 5CFFZ7SE / 5CwEDyDy / 5F4QZ4iP /
           5CAXJT4t / 5GCSdbyd hold each book against its own mid move, 0.0040-0.0070 W per bp, limit 0.42-0.50 W).
           STRUCTURAL: for inventory = -b x (P - P0) the validator's alpha is b/2 x the quadratic variation of the
           mid plus a zero-mean path term, on any path.
  grid   : dev = inv x mid - target.  dev <= -v7_mk_band_clips x notional -> ONE bid at the best bid; dev >= +that
           -> ONE ask at the best ask; inside the band NO quote at all (a resting order is cancelled).  The leg stays
           passive: it reaches the target only through fills of its own post-only quotes (COPIED: 5CFFZ7SE fills
           at the touch with no taking, quotes one side on a part of its books, and needs no speed).
           Why no two-sided quote inside the band (STRUCTURAL): a book's alpha counts toward skill only when
           |alpha| >= 2.3 bp of OUR filled notional on it (debeta.py:1918-1930 hurdle_filter).  A fill inside the
           band adds notional and no alpha; a grid step of one clip = notional / (k x W) = 7.2 bp of mid move earns
           half of it, 3.6 bp, per unit of notional.  Making still accrues: the grid's own round trips are maker
           fills on both sides (making = 2 x min(buy, sell) maker volume per book).
  seed   : the first state of a book sets ref so that the target equals the inventory found (clipped to the limit)
           (MODEL from the STRUCTURAL offset fact: an inherited position is adopted, not dumped, and decays with
           the reference; a restart is seamless; a new uid starts with ref = mid).  A new simulation or a
           book-count change clears the references (fresh endowments / moved book ids).
  fail   : without a wealth figure the target is 0 (the book is only worked flat) and counted.

v7.4 (Round 27 tracks X0 / X1; USER 11-13: "build v7.4 by your recommendation"): three legs beside the class-0 taker,
each behind its own switch (the code defaults are v7.3; the launcher turns them on).  Every replay value is MODEL on
one market state (SP/round27/solution/x0/REPORT.md, x1/REPORT.md).  The tracks fitted their rates per "hour" = 529.2
sim-s (HOUR_SIM_S); the rules below run on the pace clock in sim-s.
  class 2 : v7_rel_classes ("none" | "2") = a class-relative stepping TAKER, the form read off the class-2 leaders
            5GCSdbyd (COPIED: fit on 16 uids x 21 hourly frames; their fill side is the side of a 4-"hour" average of
            the class-relative price on 0.98 of 5,084 fills in sample and 0.914 of 839 out of sample).  Per book:
              x      : 1e4 x (ln mid - ln mid at the leg's first state of the book) minus the mean of the same over
                       the class's books (a book without a two-sided book keeps its last value in the mean);
              avg    : exponential average of x with HALF-LIFE v7_rel_hl_s (COPIED 2,116.8 sim-s = 4 "hours");
              target : while |avg - x| > v7_step_thr_bp the target moves v7_rel_rate_h x dt / 529.2 toward the side
                       of (avg - x), i.e. up (buy) while the book is below its average, clipped to +-v7_rel_l
                       (COPIED 0.0209 W per "hour", limit 0.45 W; the 3 bp threshold is the replay's MODEL
                       placeholder, which earns the same without it).  dt counts at most v7_step_max_dt_s per state
                       (MODEL: over a gap the side is unknown, so there is no blind step).
              order  : the taker's own decision on that target: ONE IOC clip of v7_rel_clip_notional (COPIED 1,800,
                       the replayed clip) at the opposite touch once the inventory is a full clip away; the clamps
                       (never a loan: a sell stops at the free base), one instruction per book and the pace guard
                       as class 0.  Never a resting order, and the fundamental is not read (class-2 f sits far
                       from the mid): a book without one is traded like the others.
              seed   : the first state sets the target to the inventory found (clipped) and avg = x; while the
                       inventory cannot be read (no account, no initial balance, no wealth figure) the seed
                       WAITS, so an inherited position is never worked toward 0 by mistake.  A new simulation
                       or a book-count change clears the state.
            The class needs the validator class list for its books and grid (fail closed, as the maker leg).  A
            class is taken by the gap rule, made, or stepped: never two of them (refused at start).
  class 0 : v7_c0_contra (0 | 1) = a second, contrarian term INSIDE the taker's own target (track X0, MODEL: it adds
            after costs in 24 of 24 replay cells with a slow or fixed reference and loses with a 1-"hour" one; the
            validator's alpha is linear in the inventory path, so the two terms add in one inventory, STRUCTURAL):
              ref    : exponential average of the mid with time constant v7_c0_ref_s (STRUCTURAL 10,800 sim-s = the
                       scoring lookback, as the v7.3 maker reference), seeded at the mid of the book's first state:
                       the term starts at 0 (a restart closes an inherited contrarian part by the taker's trigger);
              target : T = clip(clip(K x gap_bp, +-L) + clip(-v7_c0_kc x ret_bp, +-v7_c0_lc), +-L) x W / mid with
                       ret_bp = 1e4 x (mid - ref) / ref (kc 0.005 and lc 0.45 = the v7.3 maker constants, COPIED;
                       the common limit L is the taker's).  Trigger, sizes, IOC at the touch, clamps and pace are
                       unchanged: the agent stays a taker and no class-0 maker fill enters the making quality.
  class 1 : v7_mk_target = 2 = the maker leg's band centred on a STEPPING target on the book's OWN price instead of
            the v7.3 level target (track X1 lever "dynamic 2-h", COPIED from 5CFFZ7SE: 0.03 W per "hour" toward the
            side of a 2-"hour" average, half-life v7_mk_dyn_hl_s 1,058.4 sim-s; MODEL 1.7 x the v7.3 leg on the new
            run and 2.5 x on the old run).  x = 1e4 x (ln mid - ln mid at the book's first state), no class mean;
            grid, guard, pull, follow, cap and the seed (target = the inventory found; it waits while the
            inventory cannot be read) as v7.3.  v7_mk_target = 1 is the v7.3 level target, 0 the v7.2 flat band.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any

V7_VERSION = "v7.4"
CORE_VERSION = "taker_v7_core_6"   # v7.4 = v7.3 (core_5.1) + class-2 stepping taker, class-0 contrarian term, class-1 stepping target

# ---- COPIED constants (Round 21 P REPORT.md section 2; R6 arm_p.py) ----------------------------------------------
K_DEFAULT = 0.0035        # target slope per bp of (f - mid) / mid
L_DEFAULT = 0.55          # target limit as a fraction of W
CLIP_DEFAULT = 7.0        # trigger and normal order size (units)
BIG_DEFAULT = 15.0        # order size when |T - inv| >= BIG (units)

# ---- COPIED class-1 constants (Round 22 option A = mainnet uid 68 / 5F4chNve; SP/round22/rule/REPORT.md section 6) -----
C1_K_DEFAULT = 0.036            # class-1 target slope per bp (uid 68 fit, OOS 5 of 6 frames >= +0.34)
C1_L_DEFAULT = 0.50             # class-1 target limit as a fraction of W
C1_CLIP_NOTIONAL_DEFAULT = 3500.0   # class-1 order size in quote notional; the trigger is one clip (MODEL)

# ---- v7.2 maker-leg constants (Round 24; labels in the module docstring) ----------------------------------------------
MK_NOTIONAL_DEFAULT = 1800.0     # COPIED: 5F1F2JMu's class-1 / class-2 order size in quote notional (Round 24 M1)
MK_GUARD_BP_DEFAULT = 10.0       # COPIED: the class-1 winners' wrong-side-of-f fill bound (Round 24 M3g)
MK_BAND_CLIPS_DEFAULT = 1.0      # MODEL: flat = at most one clip of net inventory
MK_EXPIRY_S_DEFAULT = 60.0       # MODEL: the life of a quote in sim-s (GTT), a safety net above the per-step pull

# ---- v7.3 contrarian-target constants (Round 25 track S; labels in the module docstring) ------------------------------
MK_K_DEFAULT = 0.005             # COPIED: W per bp of the book's own mid move (winners 0.0040-0.0070, Round 25 S2)
MK_L_DEFAULT = 0.45              # COPIED: the target limit as a fraction of W (winners 0.42-0.50)
MK_REF_S_DEFAULT = 10_800.0      # STRUCTURAL: the reference's time constant = the 10,800 sim-s scoring lookback

# ---- v7.4 constants (Round 27 tracks X0 / X1; labels in the module docstring) -----------------------------------------
HOUR_SIM_S = 529.2               # the tracks' "hour": the sim-s per wall-hour their rates and half-lives were fitted on
REL_HL_S_DEFAULT = 4.0 * HOUR_SIM_S   # COPIED: half-life of the class-relative average, 4 "hours" = 2,116.8 sim-s
REL_RATE_H_DEFAULT = 0.0209      # COPIED: W per "hour" the class-2 target steps (5GCSdbyd fitted median)
REL_L_DEFAULT = 0.45             # COPIED: the class-2 target limit as a fraction of W
REL_CLIP_NOTIONAL_DEFAULT = 1800.0   # COPIED: the replayed clip in quote notional (= the maker clip)
STEP_THR_BP_DEFAULT = 3.0        # MODEL: no step while the price is within 3 bp of its average (the replay's placeholder)
STEP_MAX_DT_S_DEFAULT = 60.0     # MODEL: the longest dt one state may step (over a gap the side is unknown)
C0_KC_DEFAULT = MK_K_DEFAULT     # COPIED: the class-0 contrarian slope, W per bp of the book's own mid move
C0_LC_DEFAULT = MK_L_DEFAULT     # COPIED: the contrarian term's own limit as a fraction of W
C0_REF_S_DEFAULT = 10_800.0      # STRUCTURAL: the reference's time constant = the 10,800 sim-s scoring lookback
MK_DYN_HL_S_DEFAULT = 2.0 * HOUR_SIM_S   # COPIED: half-life of the class-1 own-price average, 2 "hours" = 1,058.4 sim-s
MK_DYN_RATE_H_DEFAULT = 0.03     # COPIED: W per "hour" the class-1 stepping target moves (5CFFZ7SE, Round 25 S)

# ---- STRUCTURAL constants (validator / simulator) -----------------------------------------------------------------
CAP_DEFAULT = 500_000.0               # capital_turnover_cap 10 x miner_wealth 50,000 (config :813-817, query.py:505)
CAP_MULT_DEFAULT = 10.0               # scoring.activity.capital_turnover_cap default (src067 taos/im/config:813-817)
WINDOW_NS = 86_400_000_000_000        # scoring.activity.trade_volume_assessment_period (86,400 sim-s)
DEPTH_S = 10_800.0                    # the bucket depth in sim-s of the paced rate (the 10,800-s scoring lookback)
STEP_NS = 1_000_000_000               # publish_interval: one state per sim-s (multiasset xml step)
LAYOUTS = {
    256: ((0, 128), (128, 192), (192, 256)),
    128: ((0, 64), (64, 96), (96, 128)),
}

# ---- MODEL constants ----------------------------------------------------------------------------------------------
FEE_MARGIN_DEFAULT = 0.001            # extra fraction of the ask kept back on a buy (fee changes + rounding)
LOG_EVERY_DEFAULT = 60                # states per summary line
SEEN_TRADES = 4096                    # trade ids remembered per validator for de-duplication

SIDE_BUY = 0                          # OrderDirection.BUY (models.py:2230)
SIDE_SELL = 1                         # OrderDirection.SELL

ACT_TAKE = "take"
ACT_CANCEL = "cancel"
ACT_QUOTE = "quote"                   # v7.2: a post-only resting order of the maker leg


# ======================================================================================================================
# small readers (never raise)
# ======================================================================================================================

def _num(v: Any) -> float | None:
    """A finite float, else None (bool is not a number here)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _int(v: Any) -> int | None:
    x = _num(v)
    if x is None or x != math.floor(x):
        return None
    return int(x)


# ======================================================================================================================
# parameters
# ======================================================================================================================

PARAM_KEYS = ("v7_k", "v7_l", "v7_clip", "v7_big", "v7_classes", "v7_pace", "v7_cap", "v7_cap_mult",
              "v7_fee_margin", "v7_log_every", "v7_log_dir", "v7_history_anchor", "v7_gc_every",
              "v7_c1_k", "v7_c1_l", "v7_c1_clip_notional",
              "v7_maker_classes", "v7_mk_notional", "v7_mk_guard_bp", "v7_mk_band_clips", "v7_mk_pull",
              "v7_mk_expiry_s", "v7_mk_target", "v7_mk_k", "v7_mk_l", "v7_mk_ref_s",
              "v7_mk_dyn_hl_s", "v7_mk_dyn_rate_h", "v7_rel_classes", "v7_rel_hl_s", "v7_rel_rate_h", "v7_rel_l",
              "v7_rel_clip_notional", "v7_step_thr_bp", "v7_step_max_dt_s",
              "v7_c0_contra", "v7_c0_kc", "v7_c0_lc", "v7_c0_ref_s")


def parse_params(config: Any) -> dict:
    """The agent's v7 params from the miner's --agent.params namespace (taos ParseKwargs: every value that parses as a
    float IS a float, so "0" arrives as 0.0) or a dict.  Raises ValueError on a value the build does not support; the
    agent then refuses to start (the preflight test pins the defaults)."""
    def raw(key, default):
        v = _get(config, key, None)
        return default if v is None else v

    def num(key, default, lo=None, hi=None, lo_open=False):
        v = raw(key, default)
        x = _num(v)
        if x is None:
            raise ValueError(f"{key}={v!r} is not a number")
        if lo is not None and (x < lo or (lo_open and x == lo)):
            raise ValueError(f"{key}={x} is below its bound {lo}")
        if hi is not None and x > hi:
            raise ValueError(f"{key}={x} is above its bound {hi}")
        return x

    p = {
        "k": num("v7_k", K_DEFAULT, 0.0, 1.0, lo_open=True),
        "l": num("v7_l", L_DEFAULT, 0.0, 1.0, lo_open=True),
        "clip": num("v7_clip", CLIP_DEFAULT, 0.0, 1e6, lo_open=True),
        "big": num("v7_big", BIG_DEFAULT, 0.0, 1e6, lo_open=True),
        "cap": num("v7_cap", 0.0, 0.0, 1e12),               # 0 = derive from the config (cap_mult x miner_wealth)
        "cap_mult": num("v7_cap_mult", CAP_MULT_DEFAULT, 0.0, 1e6, lo_open=True),
        "fee_margin": num("v7_fee_margin", FEE_MARGIN_DEFAULT, 0.0, 0.1),
        "log_every": int(num("v7_log_every", LOG_EVERY_DEFAULT, 1, 1e6)),
        "gc_every": int(num("v7_gc_every", 30, 0, 1e6)),
        "log_dir": str(raw("v7_log_dir", "") or ""),
        "history_anchor": str(raw("v7_history_anchor", "") or ""),
    }
    if p["big"] < p["clip"]:
        raise ValueError(f"v7_big={p['big']} is below v7_clip={p['clip']}")
    pace = raw("v7_pace", 1)
    pv = _num(pace)
    if pv not in (0.0, 1.0):
        raise ValueError(f"v7_pace={pace!r} must be 0 or 1")
    p["pace"] = bool(pv)
    classes = raw("v7_classes", "0")
    cv = _num(classes)
    cs = str(int(cv)) if cv is not None and cv == math.floor(cv) else str(classes).strip().replace(" ", "")
    if cs == "0":
        p["classes"] = (0,)
    elif cs == "0,1":
        p["classes"] = (0, 1)
    else:
        raise ValueError(f"v7_classes={classes!r}: v7.1 trades class 0 ('0') or classes 0 and 1 ('0,1'); "
                         "class 2 is OFF")
    p["c1_k"] = num("v7_c1_k", C1_K_DEFAULT, 0.0, 1.0, lo_open=True)
    p["c1_l"] = num("v7_c1_l", C1_L_DEFAULT, 0.0, 1.0, lo_open=True)
    p["c1_clip_notional"] = num("v7_c1_clip_notional", C1_CLIP_NOTIONAL_DEFAULT, 0.0, 1e6, lo_open=True)
    # v7.2 maker leg: "none" (default, = v7.1), "1" or "1,2"; ParseKwargs turns "1" into 1.0
    mk = raw("v7_maker_classes", "none")
    mv = _num(mk)
    ms = str(int(mv)) if mv is not None and mv == math.floor(mv) else str(mk).strip().replace(" ", "").lower()
    if ms in ("none", "off", ""):
        p["maker_classes"] = ()
    elif ms == "1":
        p["maker_classes"] = (1,)
    elif ms == "1,2":
        p["maker_classes"] = (1, 2)
    else:
        raise ValueError(f"v7_maker_classes={mk!r}: the maker leg runs on 'none', class 1 ('1') or classes 1 and 2 "
                         "('1,2'); class 0 is the taker's")
    both = sorted(set(p["maker_classes"]) & set(p["classes"]))
    if both:
        raise ValueError(f"v7_maker_classes={mk!r} and v7_classes={classes!r} share class {both}: one inventory per "
                         "book, a class is taken OR made")
    p["mk_notional"] = num("v7_mk_notional", MK_NOTIONAL_DEFAULT, 0.0, 1e6, lo_open=True)
    p["mk_guard_bp"] = num("v7_mk_guard_bp", MK_GUARD_BP_DEFAULT, 0.0, 1e4)
    p["mk_band_clips"] = num("v7_mk_band_clips", MK_BAND_CLIPS_DEFAULT, 0.0, 1e3, lo_open=True)
    p["mk_expiry_s"] = num("v7_mk_expiry_s", MK_EXPIRY_S_DEFAULT, 1.0, 86_400.0)
    pull = raw("v7_mk_pull", 1)
    plv = _num(pull)
    if plv not in (0.0, 1.0):
        raise ValueError(f"v7_mk_pull={pull!r} must be 0 or 1")
    p["mk_pull"] = bool(plv)
    # v7.3 contrarian target of the maker leg: 0 (default, = the v7.2 flat band) or 1; v7.4: 2 = the stepping target
    tg = raw("v7_mk_target", 0)
    tgv = _num(tg)
    if tgv not in (0.0, 1.0, 2.0):
        raise ValueError(f"v7_mk_target={tg!r} must be 0, 1 or 2")
    p["mk_target"] = bool(tgv)
    p["mk_dyn"] = tgv == 2.0                               # v7.4: the band follows the stepping target
    p["mk_k"] = num("v7_mk_k", MK_K_DEFAULT, 0.0, 1.0, lo_open=True)
    p["mk_l"] = num("v7_mk_l", MK_L_DEFAULT, 0.0, 1.0, lo_open=True)
    p["mk_ref_s"] = num("v7_mk_ref_s", MK_REF_S_DEFAULT, 1.0, 1e7)
    p["mk_dyn_hl_s"] = num("v7_mk_dyn_hl_s", MK_DYN_HL_S_DEFAULT, 1.0, 1e7)
    p["mk_dyn_rate_h"] = num("v7_mk_dyn_rate_h", MK_DYN_RATE_H_DEFAULT, 0.0, 10.0, lo_open=True)
    # v7.4 class-relative stepping taker: "none" (default, = v7.3) or "2"; ParseKwargs turns "2" into 2.0
    rl = raw("v7_rel_classes", "none")
    rv = _num(rl)
    rls = str(int(rv)) if rv is not None and rv == math.floor(rv) else str(rl).strip().replace(" ", "").lower()
    if rls in ("none", "off", ""):
        p["rel_classes"] = ()
    elif rls == "2":
        p["rel_classes"] = (2,)
    else:
        raise ValueError(f"v7_rel_classes={rl!r}: the stepping taker runs on 'none' or class 2 ('2')")
    both = sorted(set(p["rel_classes"]) & (set(p["maker_classes"]) | set(p["classes"])))
    if both:
        raise ValueError(f"v7_rel_classes={rl!r} shares class {both} with v7_maker_classes={mk!r} / "
                         f"v7_classes={classes!r}: one inventory per book, a class is taken, made OR stepped")
    p["rel_hl_s"] = num("v7_rel_hl_s", REL_HL_S_DEFAULT, 1.0, 1e7)
    p["rel_rate_h"] = num("v7_rel_rate_h", REL_RATE_H_DEFAULT, 0.0, 10.0, lo_open=True)
    p["rel_l"] = num("v7_rel_l", REL_L_DEFAULT, 0.0, 1.0, lo_open=True)
    p["rel_clip_notional"] = num("v7_rel_clip_notional", REL_CLIP_NOTIONAL_DEFAULT, 0.0, 1e6, lo_open=True)
    p["step_thr_bp"] = num("v7_step_thr_bp", STEP_THR_BP_DEFAULT, 0.0, 1e4)
    p["step_max_dt_s"] = num("v7_step_max_dt_s", STEP_MAX_DT_S_DEFAULT, 1.0, 86_400.0)
    # v7.4 contrarian term inside the class-0 taker target: 0 (default, = v7.3) or 1
    cc = raw("v7_c0_contra", 0)
    ccv = _num(cc)
    if ccv not in (0.0, 1.0):
        raise ValueError(f"v7_c0_contra={cc!r} must be 0 or 1")
    p["c0_contra"] = bool(ccv)
    p["c0_kc"] = num("v7_c0_kc", C0_KC_DEFAULT, 0.0, 1.0, lo_open=True)
    p["c0_lc"] = num("v7_c0_lc", C0_LC_DEFAULT, 0.0, 1.0, lo_open=True)
    p["c0_ref_s"] = num("v7_c0_ref_s", C0_REF_S_DEFAULT, 1.0, 1e7)
    return p


# ======================================================================================================================
# class map
# ======================================================================================================================

def _first_class(config: Any) -> tuple[str, Any]:
    """('ok', entry) for a readable first class, ('absent', None) without a class list, ('unreadable', None)."""
    classes = _get(config, "classes")
    if not classes:
        return "absent", None
    if isinstance(classes, (str, bytes, dict)):
        return "unreadable", None
    try:
        first = list(classes)[0]
    except Exception:
        return "unreadable", None
    return "ok", first


def _class_entry(config: Any, i: int) -> tuple[str, Any]:
    """('ok', entry i) of the validator class list, ('absent', None) without it / without entry i, ('unreadable', None)."""
    classes = _get(config, "classes")
    if not classes:
        return "absent", None
    if isinstance(classes, (str, bytes, dict)):
        return "unreadable", None
    try:
        lst = list(classes)
    except Exception:
        return "unreadable", None
    if len(lst) <= i:
        return "absent", None
    return "ok", lst[i]


def _entry_grid(entry: Any) -> tuple:
    """(price_decimals, volume_decimals, min_order) of one class-list entry (entry, else its config), as class 0's."""
    cfg = _get(entry, "config")
    pd = _int(_get(entry, "priceDecimals"))
    if pd is None:
        pd = _int(_get(cfg, "priceDecimals"))
    vd = _int(_get(entry, "volumeDecimals"))
    if vd is None:
        vd = _int(_get(cfg, "volumeDecimals"))
    return pd, vd, _num(_get(cfg, "min_order_size"))


def _entry_key(entry: Any) -> Any:
    try:
        books = _get(entry, "books")
        return (len(books), int(books[0]), int(books[-1]), _get(entry, "priceDecimals"), _get(entry, "volumeDecimals"),
                _get(_get(entry, "config"), "min_order_size"))
    except Exception:
        return "unreadable"


def class_layout(config: Any, book_count: Any) -> dict:
    """{ok, reason, book_count, c0 (frozenset), c1, c2, price_decimals, volume_decimals, min_order, source}.  Never
    raises.  ok False -> the agent sends no orders (fail closed) and logs the reason once per change."""
    n = _int(book_count)
    out = {"ok": False, "reason": "", "book_count": n, "c0": frozenset(), "c1": frozenset(), "c2": frozenset(),
           "price_decimals": None, "volume_decimals": None, "min_order": None, "source": "",
           "c1_ok": False, "c1_reason": "layout_off", "c1_grid": None,
           "c2_ok": False, "c2_reason": "layout_off", "c2_grid": None}
    if n not in LAYOUTS:
        out["reason"] = f"layout_unknown_book_count_{n}"
        return out
    (a0, b0), (a1, b1), (a2, b2) = LAYOUTS[n]
    out["c0"], out["c1"], out["c2"] = frozenset(range(a0, b0)), frozenset(range(a1, b1)), frozenset(range(a2, b2))
    status, first = _first_class(config)
    pd = vd = mo = None
    if status == "ok":
        try:
            books = _get(first, "books")
            ids = frozenset(int(b) for b in books)
        except Exception:
            ids = None
        if ids is None:
            status = "unreadable"
        elif ids != out["c0"]:
            out["reason"] = f"classes_mismatch_first_class_{len(ids)}_books"
            return out
        else:
            cfg = _get(first, "config")
            pd = _int(_get(first, "priceDecimals"))
            if pd is None:
                pd = _int(_get(cfg, "priceDecimals"))
            vd = _int(_get(first, "volumeDecimals"))
            if vd is None:
                vd = _int(_get(cfg, "volumeDecimals"))
            mo = _num(_get(cfg, "min_order_size"))
    out["source"] = "classes" if status == "ok" else f"layout_only_{status}"
    if pd is None:
        pd = _int(_get(config, "priceDecimals"))
    if vd is None:
        vd = _int(_get(config, "volumeDecimals"))
    if mo is None:
        mo = _num(_get(config, "min_order_size"))
    if pd is None or vd is None or pd < 0 or vd < 0:
        out["reason"] = "grid_unreadable"
        return out
    out.update(ok=True, price_decimals=pd, volume_decimals=vd, min_order=max(0.0, mo or 0.0))
    # v7.1 class 1 (STRUCTURAL, fail closed for class 1 ALONE): only when the validator class list confirms the class-1
    # books and gives their grid (src067 simulation_1.xml: min order 2.5364, not class 0's 0.25)
    for ci in (1, 2):                                       # v7.2: class 2 the same way (the maker leg's grid)
        ok_i, reason_i, grid_i = _class_grid(config, ci, out[f"c{ci}"])
        out[f"c{ci}_ok"], out[f"c{ci}_reason"], out[f"c{ci}_grid"] = ok_i, reason_i, grid_i
    return out


def _class_grid(config: Any, i: int, expect: frozenset) -> tuple:
    """(ok, reason, grid) of class i from the validator class list: its books must equal the layout's class i."""
    st, e = _class_entry(config, i)
    if st != "ok":
        return False, f"c{i}_class_list_{st}", None
    try:
        ids = frozenset(int(b) for b in _get(e, "books"))
    except Exception:
        ids = None
    if ids is None:
        return False, f"c{i}_class_list_unreadable", None
    if ids != expect:
        return False, f"c{i}_classes_mismatch_{len(ids)}_books", None
    pd, vd, mo = _entry_grid(e)
    if pd is None or vd is None or pd < 0 or vd < 0:
        return False, f"c{i}_grid_unreadable", None
    return True, "", {"price_decimals": pd, "volume_decimals": vd, "min_order": max(0.0, mo or 0.0)}


def layout_key(config: Any, book_count: Any) -> tuple:
    """A cheap per-request cache key for class_layout."""
    status, first = _first_class(config)
    fk = None
    if status == "ok":
        try:
            books = _get(first, "books")
            fk = (len(books), int(books[0]), int(books[-1]), _get(first, "priceDecimals"),
                  _get(first, "volumeDecimals"), _get(_get(first, "config"), "min_order_size"))
        except Exception:
            fk = "unreadable"
    st1, e1 = _class_entry(config, 1)
    fk1 = _entry_key(e1) if st1 == "ok" else st1
    st2, e2 = _class_entry(config, 2)
    fk2 = _entry_key(e2) if st2 == "ok" else st2
    return (_int(book_count), status, fk, _get(config, "priceDecimals"), _get(config, "volumeDecimals"),
            _get(config, "min_order_size"), _get(config, "simulation_id"), fk1, fk2)


# ======================================================================================================================
# rounding
# ======================================================================================================================

def floor_to(x: float, decimals: int) -> float:
    """x rounded DOWN to ``decimals`` (a quantity must never round up past a balance)."""
    s = 10.0 ** decimals
    return round(math.floor(x * s + 1e-7) / s, decimals)


# ======================================================================================================================
# the decision (pure)
# ======================================================================================================================

def target_units(gap_bp: float, w: float, mid: float, k: float, l: float) -> float:
    """T = clip(k x gap_bp, -l, +l) x W / mid (COPIED)."""
    frac = k * gap_bp
    if frac > l:
        frac = l
    elif frac < -l:
        frac = -l
    return frac * w / mid


def decide(bid: Any, ask: Any, f: Any, base_total: Any, base_init: Any, quote_init: Any, base_free: Any,
           quote_free: Any, taker_fee: Any, p: dict, grid: dict, miner_wealth: Any = None,
           rule: tuple | None = None, tfrac: float | None = None, add_frac: float = 0.0) -> tuple:
    """One book: (reason, order | None, inv, T).  order = (side, qty, price).  reason is "send" with an order, else
    why not.  Pure; never raises on bad numbers.  rule None = class 0 (p's k, l, clip / big in units); rule =
    (k, l, clip_notional) = class 1 (v7.1): clip = big = clip_notional / mid units, the trigger is one clip.
    v7.4: tfrac = the target as a fraction of W given from outside (the stepping taker; neither the gap nor f is read);
    add_frac = a term added to the gap term inside the common limit l (the class-0 contrarian term)."""
    fb, fa, ff = _num(bid), _num(ask), _num(f)
    if tfrac is None and (ff is None or ff <= 0.0):
        return "no_f", None, None, None
    if fb is None or fa is None or fb <= 0.0 or fa <= 0.0:
        return "one_sided", None, None, None
    if fb >= fa:
        return "crossed", None, None, None
    mid = 0.5 * (fb + fa)
    bt_, bi = _num(base_total), _num(base_init)
    if bt_ is None or bi is None:
        return "no_initial", None, None, None
    qi = _num(quote_init)
    if qi is not None:
        w = qi + bi * mid
    else:
        w = _num(miner_wealth)
        if w is None:
            return "no_wealth", None, None, None
    if w <= 0.0:
        return "no_wealth", None, None, None
    inv = bt_ - bi
    if rule is None:
        k_, l_, clip_, big_ = p["k"], p["l"], p["clip"], p["big"]
    else:
        k_, l_, notional = rule
        clip_ = big_ = notional / mid
    if tfrac is not None:                                   # v7.4: the stepping taker's own target (f not read)
        t = tfrac * w / mid
    else:
        gap_bp = 1e4 * (ff - mid) / mid
        if add_frac:                                        # v7.4: gap term + contrarian term, one common limit
            frac = k_ * gap_bp
            frac = l_ if frac > l_ else (-l_ if frac < -l_ else frac)
            frac += add_frac
            frac = l_ if frac > l_ else (-l_ if frac < -l_ else frac)
            t = frac * w / mid
        else:
            t = target_units(gap_bp, w, mid, k_, l_)
    d = t - inv
    ad = abs(d)
    if ad < clip_:
        return "in_band", None, inv, t
    qty = big_ if ad >= big_ else clip_
    vd = grid["volume_decimals"]
    pd = grid["price_decimals"]
    if d > 0.0:
        side, price = SIDE_BUY, fa
        qf = _num(quote_free)
        if qf is None or qf <= 0.0:
            return "no_quote", None, inv, t
        fee = _num(taker_fee) or 0.0
        afford = qf / (fa * (1.0 + max(fee, 0.0) + p["fee_margin"]))
        if afford < qty:
            qty = afford
    else:
        side, price = SIDE_SELL, fb
        bf = _num(base_free)
        if bf is None or bf <= 0.0:
            return "no_base", None, inv, t
        if bf < qty:
            qty = bf
    qty = floor_to(qty, vd)
    mo = grid.get("min_order") or 0.0
    if qty <= 0.0 or qty < mo:
        return "below_min", None, inv, t
    return "send", (side, qty, round(price, pd)), inv, t


# ======================================================================================================================
# pace guard
# ======================================================================================================================

class Pace:
    """Per-book token bucket + rolling ledger on a monotonic pace clock (ns).  STRUCTURAL: the validator's per-book
    cap over its 86,400-sim-s rolling window."""

    __slots__ = ("cap", "rate", "depth", "window", "tokens", "last", "ledger", "sums")

    def __init__(self, cap: float, window_ns: int = WINDOW_NS, depth_s: float = DEPTH_S):
        self.cap = float(cap)
        self.window = int(window_ns)
        self.rate = self.cap / (self.window / 1e9)          # notional per sim-s
        self.depth = self.rate * float(depth_s)             # CAP x 10,800 / 86,400
        self.tokens: dict[int, float] = {}
        self.last: dict[int, int] = {}
        self.ledger: dict[int, deque] = {}
        self.sums: dict[int, float] = {}

    def _refill(self, b: int, clock: int) -> float:
        tok = self.tokens.get(b)
        if tok is None:
            tok = self.depth                                # a fresh book starts with a full bucket
        else:
            dt = clock - self.last.get(b, clock)
            if dt > 0:
                tok = min(self.depth, tok + self.rate * dt / 1e9)
        self.tokens[b] = tok
        self.last[b] = clock
        return tok

    def _prune(self, b: int, clock: int) -> float:
        q = self.ledger.get(b)
        if not q:
            return 0.0
        edge = clock - self.window
        s = self.sums.get(b, 0.0)
        while q and q[0][0] <= edge:
            s -= q.popleft()[1]
        if not q:
            s = 0.0
        self.sums[b] = s
        return s

    def used(self, b: int, clock: int, venue_volume: Any = None) -> float:
        own = self._prune(b, clock)
        v = _num(venue_volume)
        return max(own, v) if v is not None else own

    def allow(self, b: int, notional: float, clock: int, venue_volume: Any = None) -> str:
        """'ok', 'bucket' (tokens < notional) or 'ledger' (ledger + notional > cap)."""
        if self.used(b, clock, venue_volume) + notional > self.cap:
            return "ledger"
        if self._refill(b, clock) < notional:
            return "bucket"
        return "ok"

    def spend(self, b: int, notional: float, clock: int) -> None:
        tok = self._refill(b, clock)
        self.tokens[b] = tok - notional                     # may go below 0: the overshoot is repaid by the refill
        q = self.ledger.get(b)
        if q is None:
            q = self.ledger[b] = deque()
        q.append((clock, notional))
        self.sums[b] = self.sums.get(b, 0.0) + notional


# ======================================================================================================================
# per-validator run state
# ======================================================================================================================

class RunState:
    """What one validator's stream needs: the pace clock across simulation seams, the pace guard and seen trades."""

    __slots__ = ("clock", "last_ts", "sim_id", "pace", "seen", "seen_q", "states", "mk_ref", "c0_ref", "step_st")

    def __init__(self, cap: float):
        self.clock = 0
        self.last_ts = None
        self.sim_id = None
        self.pace = Pace(cap)
        self.seen: set = set()
        self.seen_q: deque = deque()
        self.states = 0
        self.mk_ref: dict = {}          # v7.3: maker book -> [reference price, pace clock of its last update]
        self.c0_ref: dict = {}          # v7.4: class-0 book -> [reference price, pace clock of its last update]
        self.step_st: dict = {}         # v7.4: stepping book -> [ln mid at its first state, average of x (bp),
        #                                 target (fraction of W), pace clock of its last step, last ln mid]

    def advance(self, ts: Any, sim_id: Any) -> None:
        """The pace clock: + (ts - last ts); one step on a backward jump (a new simulation: the validator's volume
        window rolls across the seam, so the ledger is kept)."""
        t = _int(ts)
        if t is None:
            return
        if self.last_ts is not None:
            dt = t - self.last_ts
            self.clock += dt if dt > 0 else (STEP_NS if dt < 0 else 0)
        if self.sim_id is not None and sim_id != self.sim_id:
            self.seen.clear()                               # trade ids restart with a new simulation
            self.seen_q.clear()
            self.mk_ref.clear()                             # v7.3: fresh endowments, the references re-seed
            self.c0_ref.clear()                             # v7.4: the same for the class-0 references ...
            self.step_st.clear()                            # ... and the stepping targets
        self.last_ts = t
        self.sim_id = sim_id
        self.states += 1

    def new_trade(self, key: tuple) -> bool:
        if key in self.seen:
            return False
        self.seen.add(key)
        self.seen_q.append(key)
        if len(self.seen_q) > SEEN_TRADES:
            self.seen.discard(self.seen_q.popleft())
        return True


# ======================================================================================================================
# raw state readers (lazy wire dicts first, models second)
# ======================================================================================================================

def raw_books(books: Any) -> Any:
    """The wire dicts of a LazyBooks (models.py:2743, ``_raw_books``), else the mapping itself."""
    rb = getattr(books, "_raw_books", None)
    return rb if isinstance(rb, dict) else books


def raw_accounts(accounts: Any, uid: int) -> Any:
    """{book: account} for our uid: LazyAccounts' wire dicts (models.py:2834, ``_raw_accounts``), else the mapping."""
    if accounts is None:
        return None
    ra = getattr(accounts, "_raw_accounts", None)
    if isinstance(ra, dict):
        return ra.get(uid)
    try:
        return accounts[uid]
    except Exception:
        return None


def touch(book: Any) -> tuple:
    """(bid, ask, f) of a wire dict / LazyBook / Book; level 1 only."""
    if not isinstance(book, dict):
        raw = getattr(book, "_raw", None)
        if isinstance(raw, dict):
            book = raw
    if isinstance(book, dict):
        b, a = book.get("b"), book.get("a")
        bid = b[0].get("p") if b else None
        ask = a[0].get("p") if a else None
        return bid, ask, book.get("f")
    try:
        bids = _get(book, "b") or _get(book, "bids")
        asks = _get(book, "a") or _get(book, "asks")
        bid = (_get(bids[0], "p") or _get(bids[0], "price")) if bids else None
        ask = (_get(asks[0], "p") or _get(asks[0], "price")) if asks else None
        f = _get(book, "f")
        if f is None:
            f = _get(book, "fundamental")
        return bid, ask, f
    except Exception:
        return None, None, None


def account_view(acc: Any) -> tuple:
    """(base_total, base_free, base_init, quote_free, quote_init, taker_fee, traded_volume, order_ids) of a wire dict,
    a LazyAccount (its ``_raw`` until parsed) or an Account model."""
    if not isinstance(acc, dict):
        raw = getattr(acc, "_raw", None)
        if isinstance(raw, dict):
            acc = raw
        else:
            data = getattr(acc, "data", None)
            if data is not None and not isinstance(acc, dict):
                acc = data
    if isinstance(acc, dict):
        bb, qb, fees = acc.get("bb") or {}, acc.get("qb") or {}, acc.get("f")
        orders = acc.get("o") or ()
        ids = [o.get("i") if isinstance(o, dict) else _get(o, "i") for o in orders] if orders else []
        return (bb.get("t"), bb.get("f"), bb.get("i"), qb.get("f"), qb.get("i"),
                fees.get("t") if isinstance(fees, dict) else _get(fees, "t"), acc.get("v"), ids)
    bb, qb, fees = _get(acc, "bb"), _get(acc, "qb"), _get(acc, "f")
    orders = _get(acc, "o") or ()
    return (_get(bb, "t"), _get(bb, "f"), _get(bb, "i"), _get(qb, "f"), _get(qb, "i"), _get(fees, "t"),
            _get(acc, "v"), [_get(o, "i") for o in orders])


def account_orders(acc: Any) -> list:
    """[(id, side, price, quantity)] of our resting orders on one book (wire Order: i, s 0 = bid / 1 = ask, p, q;
    src067 taos/im/protocol/orders.py:116-135).  An order without a whole id is dropped."""
    if acc is None:
        return []
    if not isinstance(acc, dict):
        raw = getattr(acc, "_raw", None)
        if isinstance(raw, dict):
            acc = raw
        else:
            data = getattr(acc, "data", None)
            if data is not None:
                acc = data
    orders = _get(acc, "o") or ()
    out = []
    for o in orders:
        i = _int(_get(o, "i"))
        if i is None:
            continue
        out.append((i, _int(_get(o, "s")), _num(_get(o, "p")), _num(_get(o, "q"))))
    return out


def step_phase(ts: Any, config: Any) -> tuple | None:
    """(K, phase) of a state on the fundamental-step grid, None without a usable grid.  STRUCTURAL, as v6.36 reads it
    (research_v6_36_step_withdrawal.py states_per_step / block_phase): K = states per step = (fp_update_period - 1)
    / publish_interval when whole (taos adds 1 ns to the XML period), else fp_update_period / publish_interval;
    phase = (ts // publish_interval) % K; the state at phase 0 is the first with the new f."""
    p, i, t = _int(_get(config, "fp_update_period")), _int(_get(config, "publish_interval")), _int(ts)
    if p is None or i is None or t is None or p <= 0 or i <= 0 or t < 0:
        return None
    for whole in (p - 1, p):
        if whole > 0 and whole % i == 0 and whole // i >= 2:
            k = whole // i
            return k, (t // i) % k
    return None


# ======================================================================================================================
# one state -> actions
# ======================================================================================================================

class TakerCore:
    """Stateful core: per-validator run state, the cached class layout and the counters for the summary line."""

    def __init__(self, uid: int, params: dict):
        self.uid = int(uid)
        self.p = params
        self.runs: dict[str, RunState] = {}
        self._layout_key = None
        self._layouts: dict = {}
        self.layout: dict | None = None
        self.layout_changes: list = []        # (key, layout) pairs the agent logs, drained by it
        self.cap_used = None
        self.pair_flags: list | None = None   # v7.0.1 diagnostic: per summary pair, True = the cap check refuses
        self.pairs_c1: list | None = None     # v7.1: class-1 (inv, T) pairs on summary states
        self.mk_stats: dict | None = None     # v7.2: the maker leg's picture on summary states
        self.rel_stats: dict | None = None    # v7.4: the stepping taker's picture on summary states
        self.c0_stats: dict | None = None     # v7.4: the class-0 contrarian term's picture on summary states
        self._book_count = None               # v7.1: the pace state is reset when the book count changes
        self.reset_counters()

    def reset_counters(self) -> None:
        self.c: dict[str, int] = {}
        self.refusals: dict[str, int] = {}
        self.fill_notional = 0.0
        self.fill_qty_buy = 0.0
        self.fill_qty_sell = 0.0
        self.fills_taker = 0
        self.fills_maker = 0
        self.mk_buy_notional = 0.0            # v7.2: our maker fills by side (making = 2 x min(buy, sell) per book)
        self.mk_sell_notional = 0.0

    def _count(self, key: str, n: int = 1) -> None:
        self.c[key] = self.c.get(key, 0) + n

    def cap_for(self, config: Any) -> float:
        """The per-book cap: v7_cap when set, else cap_mult x miner_wealth (STRUCTURAL query.py:505), else 500k."""
        if self.p["cap"] > 0.0:
            return self.p["cap"]
        w = _num(_get(config, "miner_wealth"))
        if w is not None and w > 0.0:
            return self.p["cap_mult"] * w
        return CAP_DEFAULT

    def run_for(self, hotkey: str, config: Any) -> RunState:
        rs = self.runs.get(hotkey)
        if rs is None:
            cap = self.cap_for(config)
            self.cap_used = cap
            rs = self.runs[hotkey] = RunState(cap)
        return rs

    def ingest_notices(self, rs: RunState, notices: Any) -> None:
        """Our fills (pace spend + counters) and refusals from the state's notices for our uid."""
        if not notices:
            return
        uid = self.uid
        for ev in notices:
            y = _get(ev, "y")
            if y is None:
                y = _get(ev, "type")
            if y == "ET" or y == "EVENT_TRADE":
                ta, ma = _get(ev, "Ta"), _get(ev, "Ma")
                taker = ta == uid
                maker = ma == uid
                if not (taker or maker):
                    continue
                b = _int(_get(ev, "b"))
                q, px = _num(_get(ev, "q")), _num(_get(ev, "p"))
                if b is None or q is None or px is None:
                    self._count("fill_unreadable")
                    continue
                if not rs.new_trade((b, _get(ev, "i"))):
                    self._count("fill_dup")
                    continue
                notional = q * px
                rs.pace.spend(b, notional, rs.clock)
                self.fill_notional += notional
                s = _int(_get(ev, "s"))
                buy = (taker and s == 0) or (maker and not taker and s == 1)   # trade.py:742 is_buy
                if buy:
                    self.fill_qty_buy += q
                else:
                    self.fill_qty_sell += q
                if taker:
                    self.fills_taker += 1
                else:
                    self.fills_maker += 1
                    if buy:
                        self.mk_buy_notional += notional
                    else:
                        self.mk_sell_notional += notional
            elif y in ("ERDPOL", "ERDPOM"):
                m = str(_get(ev, "m") or _get(ev, "message") or "")[:48]
                self.refusals[m] = self.refusals.get(m, 0) + 1
            elif y in ("RDPOL", "RDPOM"):
                self._count("accepted")
            elif y == "RDCO":
                self._count("cancel_ok")
            elif y == "ERDCO":
                self._count("cancel_failed")

    def step(self, ts: Any, hotkey: str, config: Any, books: Any, accounts: Any, notices: Any,
             book_count: Any = None, collect: bool = False) -> tuple:
        """One state -> (actions, pairs).  actions: [(ACT_CANCEL, book, [ids]) | (ACT_TAKE, book, side, qty, price)],
        at most one per book; on maker books (v7.2) one cancel and up to two (ACT_QUOTE, book, side, qty, price).
        pairs: [(inv, T)] over the decided class-0 books when ``collect`` (the summary's
        position-to-target correlation), else None."""
        rs = self.run_for(hotkey, config)
        rs.advance(ts, _get(config, "simulation_id"))
        self.ingest_notices(rs, notices)
        n = _int(book_count)
        if n is None:
            n = _int(_get(config, "book_count"))
        if n is None and books is not None:
            try:
                n = len(books)
            except Exception:
                n = None
        if n is not None:
            if self._book_count is not None and n != self._book_count:
                # STRUCTURAL (v7.1): a layout change moves book records between ids by class order (src067
                # book_layout.py); our own pace state is keyed by the old ids, so it restarts; the account's volume
                # 'v' (moved by the validator with the records) still floors the ledger.
                for r in self.runs.values():
                    r.pace = Pace(r.pace.cap)
                    r.mk_ref.clear()                        # v7.3: the references are keyed by the old ids too
                    r.c0_ref.clear()                        # v7.4
                    r.step_st.clear()
                self._count("pace_reset_layout")
            self._book_count = n
        key = layout_key(config, n)
        if key != self._layout_key:
            self._layout_key = key
            lay = self._layouts.get(key)
            if lay is None:                                 # a new layout: computed and logged once
                if len(self._layouts) >= 16:
                    self._layouts.clear()
                lay = self._layouts[key] = class_layout(config, n)
                self.layout_changes.append((key, lay))
            self.layout = lay
        lay = self.layout
        actions: list = []
        pairs = [] if collect else None
        flags = [] if collect else None
        pairs1 = [] if collect else None
        if collect:
            self.pair_flags = flags
            self.pairs_c1 = pairs1
            self.mk_stats = None
            self.rel_stats = None
            self.c0_stats = None
        accs = raw_accounts(accounts, self.uid)
        if accs is None:
            self._count("no_account")
            return actions, pairs
        # v7.2: the maker books = the maker classes whose books and grid the class list confirms; the maker leg owns
        # their resting orders.  Any other case (no layout, no class list, no books) leaves them to the cleanup.
        mk_books: dict = {}
        mk_accs: dict = {}
        rb = raw_books(books)
        if self.p["maker_classes"] and lay and lay["ok"] and rb is not None:
            for ci in self.p["maker_classes"]:
                if not lay.get(f"c{ci}_ok"):
                    self._count(f"mk_c{ci}_off")
                    continue
                g_i = lay[f"c{ci}_grid"]
                for b in sorted(lay[f"c{ci}"]):
                    mk_books[b] = g_i
        # cleanup: one cancel for every book that shows resting orders (all classes), no take there this state
        busy = set()
        try:
            items = accs.items()
        except Exception:
            items = ()
        views = {}
        for b, acc in items:
            v = account_view(acc)
            ids = v[7]
            bi = _int(b)
            if bi is None:
                continue
            if bi in mk_books:
                mk_accs[bi] = acc
            elif ids:
                ids = [int(i) for i in ids if _int(i) is not None]
                if ids:
                    actions.append((ACT_CANCEL, bi, ids))
                    busy.add(bi)
                    self._count("cancel_books")
                    self._count("cancel_orders", len(ids))
            views[bi] = v
        if not lay or not lay["ok"]:
            self._count("layout_off")
            return actions, pairs
        if rb is None:
            self._count("no_books")
            return actions, pairs
        p = self.p
        pace = rs.pace if p["pace"] else None
        clock = rs.clock
        wealth = _get(config, "miner_wealth")
        c0_ref = rs.c0_ref if p["c0_contra"] else None       # v7.4: the contrarian term's references (None = off)
        tcs = [] if collect and c0_ref is not None else None
        for b in lay["c0"]:
            if b in busy:
                continue
            v = views.get(b)
            if v is None:
                self._count("no_book_account")
                continue
            try:
                book = rb[b] if isinstance(rb, dict) else rb[b]
            except Exception:
                book = None
            if book is None:
                self._count("no_book")
                continue
            bid, ask, f = touch(book)
            if c0_ref is None:
                reason, order, inv, t = decide(bid, ask, f, v[0], v[2], v[4], v[1], v[3], v[5], p, lay, wealth)
            else:
                add = self._c0_term(c0_ref, b, bid, ask, clock)
                reason, order, inv, t = decide(bid, ask, f, v[0], v[2], v[4], v[1], v[3], v[5], p, lay, wealth,
                                               add_frac=add)
                if tcs is not None and inv is not None:
                    tcs.append((add, _num(f) - 0.5 * (_num(bid) + _num(ask))))
            if collect and inv is not None:
                pairs.append((inv, t))
                # v7.0.1 diagnostic (no decision reads it): does the cap check refuse even a CLIP order here?
                # used() only prunes the ledger at this clock, as allow() below does first: same decisions.
                try:
                    capped = (pace is not None and pace.used(b, clock, v[6])
                              + p["clip"] * 0.5 * (_num(bid) + _num(ask)) > pace.cap)
                except Exception:
                    capped = None                   # unknown: in neither half of the split
                flags.append(capped)
            if order is None:
                self._count(reason)
                continue
            side, qty, price = order
            if pace is not None:
                ok = pace.allow(b, qty * price, clock, v[6])
                if ok != "ok":
                    self._count("paced_" + ok)
                    continue
            actions.append((ACT_TAKE, b, side, qty, price))
            self._count("send_buy" if side == SIDE_BUY else "send_sell")
        if tcs is not None:
            ab = [abs(a) for a, _ in tcs]
            self.c0_stats = {"books": len(tcs), "refs": len(c0_ref), "tc_abs_w_p50": pctl(ab, 0.5),
                             "tc_abs_w_max": max(ab) if ab else None, "tc_net_w": sum(a for a, _ in tcs),
                             "opposed": sum(1 for a, g_ in tcs if a * g_ < 0.0),
                             "at_own_limit": sum(1 for a in ab if a >= p["c0_lc"] - 1e-12)}
        if 1 in p["classes"]:
            # v7.1 class 1 = option A (COPIED rule, its own grid, notional clips); one instruction per book still holds
            # (class-1 ids are disjoint from class 0; cleanup books are skipped as in class 0)
            if not lay.get("c1_ok"):
                self._count("c1_off")
            else:
                g1 = lay["c1_grid"]
                rule1 = (p["c1_k"], p["c1_l"], p["c1_clip_notional"])
                for b in lay["c1"]:
                    if b in busy:
                        continue
                    v = views.get(b)
                    if v is None:
                        self._count("c1_no_book_account")
                        continue
                    try:
                        book = rb[b]
                    except Exception:
                        book = None
                    if book is None:
                        self._count("c1_no_book")
                        continue
                    bid, ask, f = touch(book)
                    reason, order, inv, t = decide(bid, ask, f, v[0], v[2], v[4], v[1], v[3], v[5], p, g1, wealth,
                                                   rule=rule1)
                    if collect and inv is not None:
                        pairs1.append((inv, t))
                    if order is None:
                        self._count("c1_" + reason)
                        continue
                    side, qty, price = order
                    if pace is not None:
                        ok = pace.allow(b, qty * price, clock, v[6])
                        if ok != "ok":
                            self._count("c1_paced_" + ok)
                            continue
                    actions.append((ACT_TAKE, b, side, qty, price))
                    self._count("c1_send_buy" if side == SIDE_BUY else "c1_send_sell")
        if p["rel_classes"]:
            self._rel(rs, rb, views, busy, lay, wealth, actions, collect)
        if mk_books:
            self._maker(rs, rb, views, mk_accs, mk_books, ts, config, actions, collect)
        return actions, pairs

    def _c0_term(self, refs: dict, b: int, bid: Any, ask: Any, clock: int) -> float:
        """v7.4: the contrarian fraction of W of one class-0 book (module docstring: class 0 ref / target).  Updates
        the book's slow reference with this state's mid; 0 on a book without a two-sided touch and on the seed state.
        Never raises on bad numbers."""
        fb, fa = _num(bid), _num(ask)
        if fb is None or fa is None or fb <= 0.0 or fa <= 0.0 or fb >= fa:
            return 0.0
        mid = 0.5 * (fb + fa)
        st = refs.get(b)
        if st is None:
            refs[b] = [mid, clock]
            self._count("c0_ref_seed")
            return 0.0
        dt = clock - st[1]
        if dt > 0:
            st[0] += (1.0 - math.exp(-dt / (self.p["c0_ref_s"] * 1e9))) * (mid - st[0])
            st[1] = clock
        ref = st[0]
        frac = -self.p["c0_kc"] * 1e4 * (mid - ref) / ref
        lc = self.p["c0_lc"]
        return lc if frac > lc else (-lc if frac < -lc else frac)

    @staticmethod
    def _inv_frac(v: tuple, mid: float, wealth: Any) -> float | None:
        """The inventory of one book as a fraction of W (W as the taker reads it), None when it cannot be read."""
        bt_, bi_, qi = _num(v[0]), _num(v[2]), _num(v[4])
        if bt_ is None or bi_ is None:
            return None
        w = qi + bi_ * mid if qi is not None else _num(wealth)
        if w is None or not 0.0 < w < math.inf:
            return None
        frac = (bt_ - bi_) * mid / w
        return frac if math.isfinite(frac) else None

    def _stepper(self, e: list, x: float, clock: int, hl_s: float, rate_h: float, lim: float,
                 seed: float | None) -> float:
        """v7.4: advance one book's stepping state e = [ln mid at its first state, average of x, target, clock of the
        last step, last ln mid] with this state's x (bp) and return the target as a fraction of W.  First state: the
        average starts at x and the target at ``seed`` (the inventory found, clipped; None = 0).  After that: the
        average moves with half-life hl_s; while |average - x| > v7_step_thr_bp the target moves rate_h x dt /
        HOUR_SIM_S toward the side of (average - x), dt at most v7_step_max_dt_s; clipped to +-lim."""
        if e[1] is None:
            e[1] = x
            f0 = seed if seed is not None else 0.0
            e[2] = lim if f0 > lim else (-lim if f0 < -lim else f0)
            e[3] = clock
            return e[2]
        dt = (clock - e[3]) / 1e9
        if dt > 0.0:
            e[1] += (1.0 - 0.5 ** (dt / hl_s)) * (x - e[1])
            d = e[1] - x
            if abs(d) > self.p["step_thr_bp"]:
                mx = self.p["step_max_dt_s"]
                step = rate_h * (dt if dt < mx else mx) / HOUR_SIM_S
                t = e[2] + (step if d > 0.0 else -step)
                e[2] = lim if t > lim else (-lim if t < -lim else t)
            e[3] = clock
        return e[2]

    def _rel(self, rs: RunState, rb: Any, views: dict, busy: set, lay: dict, wealth: Any, actions: list,
             collect: bool) -> None:
        """v7.4 class-relative stepping taker (module docstring: class 2 x / avg / target / order / seed).  Appends
        (ACT_TAKE, ...) only; never raises on bad numbers."""
        p = self.p
        pace = rs.pace if p["pace"] else None
        clock = rs.clock
        st = rs.step_st
        hl, rate, lim = p["rel_hl_s"], p["rel_rate_h"], p["rel_l"]
        rule = (0.0, lim, p["rel_clip_notional"])
        rpairs = [] if collect else None
        fracs = [] if collect else None
        for ci in p["rel_classes"]:
            if not lay.get(f"c{ci}_ok"):
                self._count(f"rel_c{ci}_off")
                continue
            g = lay[f"c{ci}_grid"]
            ids = sorted(lay[f"c{ci}"])
            seen: dict = {}
            n = 0
            s = 0.0
            for b in ids:                                   # pass 1: this state's ln mids and the class mean
                try:
                    book = rb[b]
                except Exception:
                    book = None
                e = st.get(b)
                if book is not None:
                    bid, ask, f = touch(book)
                    fb, fa = _num(bid), _num(ask)
                    if fb is not None and fa is not None and 0.0 < fb < fa and fb + fa < math.inf:
                        mid = 0.5 * (fb + fa)
                        lm = math.log(mid)
                        if e is None:
                            e = st[b] = [lm, None, 0.0, clock, lm]
                            self._count("rel_seed")
                        else:
                            e[4] = lm
                        seen[b] = (bid, ask, f, mid)
                if e is not None:                           # a book without a touch keeps its last value in the mean
                    s += e[4] - e[0]
                    n += 1
            if not n:
                self._count("rel_no_books")
                continue
            mean = s / n
            for b in ids:                                   # pass 2: step the target, then the taker's decision
                tch = seen.get(b)
                if tch is None:
                    self._count("rel_bad_book")
                    continue
                bid, ask, f, mid = tch
                e = st[b]
                v = views.get(b)
                seed = None
                if e[1] is None:                            # not seeded yet: the target starts at the inventory
                    seed = self._inv_frac(v, mid, wealth) if v is not None else None
                    if seed is None:                        # found, so it waits until that can be read
                        self._count("rel_seed_wait")
                        continue
                frac = self._stepper(e, 1e4 * (e[4] - e[0] - mean), clock, hl, rate, lim, seed)
                if fracs is not None:
                    fracs.append(frac)
                if v is None:
                    self._count("rel_no_book_account")
                    continue
                if b in busy:
                    continue
                reason, order, inv, t = decide(bid, ask, f, v[0], v[2], v[4], v[1], v[3], v[5], p, g, wealth,
                                               rule=rule, tfrac=frac)
                if rpairs is not None and inv is not None:
                    rpairs.append((inv * mid, t * mid))
                if order is None:
                    self._count("rel_" + reason)
                    continue
                side, qty, price = order
                if pace is not None:
                    ok = pace.allow(b, qty * price, clock, v[6])
                    if ok != "ok":
                        self._count("rel_paced_" + ok)
                        continue
                actions.append((ACT_TAKE, b, side, qty, price))
                self._count("rel_send_buy" if side == SIDE_BUY else "rel_send_sell")
        if collect:
            dv = [abs(i - t) for i, t in rpairs]
            ta = [abs(t) for _, t in rpairs]
            self.rel_stats = {"books": len(rpairs), "stepped": len(fracs),
                              "corr_inv_target": corr(rpairs), "dev_abs_notional_p50": pctl(dv, 0.5),
                              "dev_abs_notional_max": max(dv) if dv else None,
                              "tgt_abs_notional_p50": pctl(ta, 0.5), "tgt_abs_notional_max": max(ta) if ta else None,
                              "net_tgt_w": sum(fracs), "net_inv_notional": sum(i for i, _ in rpairs),
                              "at_limit": sum(1 for x in fracs if abs(x) >= lim - 1e-12)}

    def _maker(self, rs: RunState, rb: Any, views: dict, mk_accs: dict, mk_books: dict, ts: Any, config: Any,
               actions: list, collect: bool) -> None:
        """v7.2 maker leg: per maker book, the wanted quotes of this state against our resting orders (module
        docstring: quote / guard / flat / pull / follow / cap).  Appends to ``actions``; never raises on bad numbers."""
        p = self.p
        pace = rs.pace if p["pace"] else None
        clock = rs.clock
        pull = False
        if p["mk_pull"]:
            ph = step_phase(ts, config)
            if ph is None:
                self._count("mk_no_grid")
            elif ph[1] == ph[0] - 1:
                pull = True
                self._count("mk_pull_states")
        notional = p["mk_notional"]
        guard = p["mk_guard_bp"]
        band = p["mk_band_clips"] * notional
        margin = p["fee_margin"]
        sides_n = [0, 0, 0] if collect else None       # books with 0 / 1 / 2 quoted sides after this state
        invs = [] if collect else None
        gaps = [] if collect else None
        target_on = p["mk_target"]                     # v7.3: the band is centred on the contrarian target
        dyn = p["mk_dyn"]                              # v7.4: ... on the stepping target instead of the level target
        tpairs = [] if collect and target_on else None
        tcodes = [] if tpairs is not None else None    # core 5.1 logging only: one MK_CODE_* per pair (mk_split)
        wealth = _num(_get(config, "miner_wealth")) if target_on else None
        for b, g in mk_books.items():
            v = views.get(b)
            if v is None:
                self._count("mk_no_book_account")
                continue
            orders = account_orders(mk_accs.get(b))
            try:
                book = rb[b]
            except Exception:
                book = None
            bid, ask, f = touch(book) if book is not None else (None, None, None)
            fb, fa, ff = _num(bid), _num(ask), _num(f)
            pd, vd = g["price_decimals"], g["volume_decimals"]
            want: dict = {}
            mid = None
            tgt_n = 0.0
            if target_on and fb is not None and fa is not None and 0.0 < fb < fa:
                if dyn:
                    tgt_n = self._mk_dyn_target(rs.step_st, b, 0.5 * (fb + fa), v, clock, wealth)
                else:
                    tgt_n = self._mk_target(rs.mk_ref, b, 0.5 * (fb + fa), v, clock, wealth)
            if pull:
                pass
            elif ff is None or ff <= 0.0 or fb is None or fa is None or fb <= 0.0 or fa <= 0.0 or fb >= fa:
                self._count("mk_bad_book")
            else:
                bt_, bi_ = _num(v[0]), _num(v[2])
                if bt_ is None or bi_ is None:
                    self._count("mk_no_initial")
                else:
                    mid = 0.5 * (fb + fa)
                    inv_n = (bt_ - bi_) * mid
                    if collect:
                        invs.append(abs(inv_n))
                        gaps.append(1e4 * (ff - mid) / mid)
                        if tpairs is not None:
                            tpairs.append((inv_n, tgt_n))
                    if target_on:
                        # v7.3 grid: only the side that closes the deviation, once it is one band wide
                        dev = inv_n - tgt_n
                        code = MK_CODE_IN_BAND                     # core 5.1: logging only, no decision reads it
                        if dev <= -band:
                            if 1e4 * (fb - ff) / ff > guard:
                                self._count("mk_guard_bid")
                                code = MK_CODE_BLOCKED_BID
                            else:
                                want[SIDE_BUY] = round(fb, pd)
                                self._count("mk_out_bid")
                                code = MK_CODE_OUT_BID
                        elif dev >= band:
                            if 1e4 * (ff - fa) / ff > guard:
                                self._count("mk_guard_ask")
                                code = MK_CODE_BLOCKED_ASK
                            else:
                                want[SIDE_SELL] = round(fa, pd)
                                self._count("mk_out_ask")
                                code = MK_CODE_OUT_ASK
                        else:
                            self._count("mk_in_band")
                        if tcodes is not None:
                            tcodes.append(code)
                    elif inv_n >= band:
                        self._count("mk_band_bid")
                    elif 1e4 * (fb - ff) / ff > guard:
                        self._count("mk_guard_bid")
                    else:
                        want[SIDE_BUY] = round(fb, pd)
                    if target_on:
                        pass
                    elif inv_n <= -band:
                        self._count("mk_band_ask")
                    elif 1e4 * (ff - fa) / ff > guard:
                        self._count("mk_guard_ask")
                    else:
                        want[SIDE_SELL] = round(fa, pd)
            keep: dict = {}
            cancel = []
            half_tick = 0.5 * 10.0 ** (-pd)
            for oid, s, px, _q in orders:
                wp = want.get(s)
                if wp is not None and s not in keep and px is not None and abs(px - wp) < half_tick:
                    keep[s] = oid
                else:
                    cancel.append(oid)
            if cancel:
                actions.append((ACT_CANCEL, b, cancel))
                self._count("mk_cancel_books")
                self._count("mk_cancel_orders", len(cancel))
            quoted = len(keep)
            if keep:
                self._count("mk_keep", len(keep))
            for s, price in want.items():
                if s in keep:
                    continue
                qty = notional / mid
                if s == SIDE_BUY:
                    qf = _num(v[3])
                    if qf is None or qf <= 0.0:
                        self._count("mk_no_quote")
                        continue
                    afford = qf / (price * (1.0 + margin))
                    if afford < qty:
                        qty = afford
                else:
                    bf = _num(v[1])
                    if bf is None or bf <= 0.0:
                        self._count("mk_no_base")
                        continue
                    if bf < qty:
                        qty = bf
                qty = floor_to(qty, vd)
                mo = g.get("min_order") or 0.0
                if qty <= 0.0 or qty < mo:
                    self._count("mk_below_min")
                    continue
                if pace is not None:
                    ok = pace.allow(b, qty * price, clock, v[6])
                    if ok != "ok":
                        self._count("mk_paced_" + ok)
                        continue
                actions.append((ACT_QUOTE, b, s, qty, price))
                self._count("mk_quote_bid" if s == SIDE_BUY else "mk_quote_ask")
                quoted += 1
            if collect:
                sides_n[min(quoted, 2)] += 1
        if collect:
            self.mk_stats = {"books": len(mk_books), "pull_state": pull, "sides_0": sides_n[0], "sides_1": sides_n[1],
                             "sides_2": sides_n[2], "inv_abs_notional_p50": pctl(invs, 0.5),
                             "inv_abs_notional_max": max(invs) if invs else None,
                             "gap_bp_p50": pctl(gaps, 0.5),
                             "gap_abs_bp_p50": pctl([abs(x) for x in gaps], 0.5)}
            if tpairs is not None:
                self.mk_stats.update({
                    "target": True, "dyn": dyn,
                    "refs": sum(1 for b_ in mk_books if b_ in rs.step_st) if dyn else len(rs.mk_ref),
                    "tgt_abs_notional_p50": pctl([abs(t) for _, t in tpairs], 0.5),
                    "tgt_abs_notional_max": max((abs(t) for _, t in tpairs), default=None),
                    "dev_abs_notional_p50": pctl([abs(i - t) for i, t in tpairs], 0.5),
                    "dev_abs_notional_max": max((abs(i - t) for i, t in tpairs), default=None),
                    "corr_inv_target": corr(tpairs),
                    "split": mk_split(tpairs, tcodes)})

    def _mk_target(self, refs: dict, b: int, mid: float, v: tuple, clock: int, wealth: Any) -> float:
        """v7.3: the contrarian target of one maker book in quote notional (module docstring: ref / target / seed /
        fail).  Updates the book's slow reference with this state's mid.  Never raises on bad numbers."""
        p = self.p
        k, lim = p["mk_k"], p["mk_l"]
        bt_, bi_, qi = _num(v[0]), _num(v[2]), _num(v[4])
        w = qi + bi_ * mid if qi is not None and bi_ is not None else wealth
        if w is None or w <= 0.0:
            self._count("mk_no_wealth")
            return 0.0
        st = refs.get(b)
        if st is None:
            frac = 0.0
            if bt_ is not None and bi_ is not None:
                frac = (bt_ - bi_) * mid / w
                frac = lim if frac > lim else (-lim if frac < -lim else frac)
            den = 1.0 - frac / (k * 1e4)                    # ret = -frac / k bp puts the target at the inventory found
            ref = mid / den if den > 0.5 else mid
            refs[b] = [ref, clock]
            self._count("mk_ref_seed")
        else:
            dt = clock - st[1]
            if dt > 0:
                st[0] += (1.0 - math.exp(-dt / (p["mk_ref_s"] * 1e9))) * (mid - st[0])
                st[1] = clock
            ref = st[0]
        frac = -k * 1e4 * (mid - ref) / ref
        frac = lim if frac > lim else (-lim if frac < -lim else frac)
        return frac * w

    def _mk_dyn_target(self, st: dict, b: int, mid: float, v: tuple, clock: int, wealth: Any) -> float:
        """v7.4: the stepping target of one maker book in quote notional (module docstring: class 1).  x is the
        book's own move since its first state; the seed adopts the inventory found and waits while that cannot be
        read.  Without a (finite) wealth figure the target is 0 and counted, as v7.3.  Never raises on bad numbers."""
        p = self.p
        bt_, bi_, qi = _num(v[0]), _num(v[2]), _num(v[4])
        w = qi + bi_ * mid if qi is not None and bi_ is not None else wealth
        if w is None or not 0.0 < w < math.inf or not mid < math.inf:
            self._count("mk_no_wealth")
            return 0.0
        lm = math.log(mid)
        e = st.get(b)
        seed = None
        if e is None:
            if bt_ is not None and bi_ is not None:
                seed = (bt_ - bi_) * mid / w
            if seed is None or not math.isfinite(seed):
                self._count("mk_seed_wait")
                return 0.0
            e = st[b] = [lm, None, 0.0, clock, lm]
            self._count("mk_ref_seed")
        else:
            e[4] = lm
        frac = self._stepper(e, 1e4 * (lm - e[0]), clock, p["mk_dyn_hl_s"], p["mk_dyn_rate_h"], p["mk_l"], seed)
        return frac * w


# ======================================================================================================================
# summary helpers
# ======================================================================================================================

def pctl(xs: list, q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def split_pairs(pairs: list, flags: list) -> dict:
    """v7.0.1 diagnostic: the summary pairs split into books the cap check leaves free and books it refuses
    (flag True: venue / own rolling volume + one CLIP order at mid > cap).  n, corr(inv, T), |T - inv| p50 and the
    spread (population sd) of T, which bounds what a +-CLIP dead band lets the correlation reach."""
    if not pairs or flags is None or len(flags) != len(pairs):
        return {"error": "misaligned", "pairs": len(pairs or []), "flags": None if flags is None else len(flags)}
    out = {}
    for name, want in (("free", False), ("capped", True)):
        sub = [pr for pr, f in zip(pairs, flags) if f is want]
        d = [abs(t - i) for i, t in sub]
        ts = [t for _, t in sub]
        m = sum(ts) / len(ts) if ts else 0.0
        sd = math.sqrt(sum((x - m) ** 2 for x in ts) / len(ts)) if ts else None
        out[name] = {"n": len(sub), "corr": corr(sub), "dev_p50": pctl(d, 0.5), "t_sd": sd}
    return out


MK_CODE_IN_BAND, MK_CODE_OUT_BID, MK_CODE_OUT_ASK, MK_CODE_BLOCKED_BID, MK_CODE_BLOCKED_ASK = 0, 1, 2, 3, 4


def mk_split(tpairs: list, codes: list) -> dict:
    """core 5.1 diagnostic (logging only): the v7.3 target picture of a summary state split by the guard.
    free    = books inside the band, or outside it with the closing side allowed (the rule can act there);
    blocked = books outside the band whose closing side the 10 bp guard withholds (the rule cannot act; on testnet the
              class-1 fundamental sits ~34 bp above the mid, so asks are withheld on most books).
    Counts per state of the grid, and n / |inv - target| p50, max / corr(inv, target) for each part."""
    if tpairs is None or codes is None or len(codes) != len(tpairs):
        return {"error": "misaligned", "pairs": None if tpairs is None else len(tpairs),
                "codes": None if codes is None else len(codes)}
    n = [0, 0, 0, 0, 0]
    parts: dict = {"free": [], "blocked": []}
    for pr, c in zip(tpairs, codes):
        n[c] += 1
        parts["blocked" if c >= MK_CODE_BLOCKED_BID else "free"].append(pr)
    out: dict = {"in_band": n[0], "out_bid": n[1], "out_ask": n[2], "blocked_bid": n[3], "blocked_ask": n[4]}
    for name, sub in parts.items():
        d = [abs(i - t) for i, t in sub]
        out[name] = {"n": len(sub), "corr": corr(sub), "dev_abs_p50": pctl(d, 0.5), "dev_abs_max": max(d) if d else None}
    return out


def corr(pairs: list) -> float | None:
    """Pearson correlation of (x, y) pairs; None with fewer than 3 or no spread."""
    n = len(pairs) if pairs else 0
    if n < 3:
        return None
    mx = sum(x for x, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    syy = sum((y - my) ** 2 for _, y in pairs)
    if sxx <= 0.0 or syy <= 0.0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    return sxy / math.sqrt(sxx * syy)
