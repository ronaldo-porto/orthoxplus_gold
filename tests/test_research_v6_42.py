"""v6.42: research_v642_l1t -- k149's L1T rule as a live port (scratchpad k150; rule k149 PREREG sections 1, 3, 4) -- and
research_v642_l1c, k151's bound of its carried size (k151 PREREG sections 3, 4; vm751 = vm749 + l1c).

What is pinned here: the pure rule of research_v6_42_l1t (the gate of the three modes on the TRUE position and tau, the
current block's step, L1's size after the ladder's caps, the unfold) against vm749's OWN lines (embedded verbatim below:
scratchpad k149/vm749.py, its L1T gate, its reducing test and its L1 size; k150 eq/l1tparity.py checks the copy against
the file); the part on a stub agent (default 0 = v6.41 on every path; class 0 only; the note of the true position); the
deep layer (t69's ladder harness, the real joined _v633_book with the hooks: a new level 0 carries L1's size, L1 rests
nowhere while it does, a resting L1 goes with it, the unfold, no forced re-fold, caps before the fold, total resting size
per side unchanged); the v6.3 pass (t623's harness: the note reads the true position, not v6.25's inv - tau); the hook
edits and their exact undo (tests/_v642_undo.py: the final v6.41's joined Simple and launcher byte for byte; the AST undo
== the text undo); the launcher's PARAMS key and "# v6.42." block.  No test reads the scratchpad, the network or a live
tree."""
import ast
import hashlib
import json
import math
import random
import sys
import textwrap
import types
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
PARTS = STRATEGY / "simple_parts"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(PARTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v6_42_l1t as lt  # noqa: E402
import research_v633_deep_layer as dl  # noqa: E402
import research_v6_36_step_withdrawal as sw  # noqa: E402
import _v641_undo  # noqa: E402
import _v642_undo  # noqa: E402
import join  # noqa: E402

RAW_SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
PART = (PARTS / "v6_42_part.py").read_text()
RULE = (STRATEGY / "research_v6_42_l1t.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
V641_JOINED_SHA256 = "cb95f54e2c47673af6e8d2546a944b9738559501909b5f84bf71745dd8d9351e"   # the final v6.41's joined Simple
BUY, SELL = dl.SIDE_BUY, dl.SIDE_SELL
S = 10 ** 9
_E2A = {"bid": BUY, "ask": SELL}
_A2E = {BUY: "bid", SELL: "ask"}
_A2E_INV = _A2E

# vm749 (k149/mk_vm749.py's edits on k148 vm748), verbatim: its reducing test, the L1T gate (once per class-0 book side) ...
ENGINE_REDUCING = '                    reducing_ = (side == "bid" and _INV < -EPS) or (side == "ask" and _INV > EPS)\n'
ENGINE_GATE = textwrap.dedent('''\
                    _l1t_here = False                                                   # k149 vm749 L1T gate
                    if L1T and CLS_B[b] == 0 and not vac_on and _tau != 0.0:
                        _tw_t = (side == "bid") == (_tau > 0.0)
                        _st_t = float(STEPDIR[b]) if (STEP_BLK[0] is not None and STEP_BLK[0] == int((int(ts) // 10**9) // 30)) else 0.0
                        if L1T == 1: _l1t_here = bool(reducing_ and _tw_t)
                        elif L1T == 2: _l1t_here = bool(reducing_ and (_tw_t or (_st_t != 0.0 and (_st_t > 0.0) == (side == "bid"))))
                        else: _l1t_here = bool(_tw_t and (reducing_ or (_st_t != 0.0 and (_st_t > 0.0) == (_tau > 0.0))))
                        K149["gate_open_side_states"] += int(_l1t_here); K149["gate_eval_side_states"] += 1
''')
# ... and its L1 size inside the fold (BASE: dz 1, no cp / dflat / ps 1-2: the launch clip and _psf apply)
ENGINE_Q1 = textwrap.dedent('''\
                        if _Dl1_ > (_Dk_pre if _ag_dp else Dk) + 0.5:
                            _q1_ = _ls1_ * dz_ * ((clip_ / float(P["deepc"])) if (P["cp"] and paced_b) or _lopen else 1.0)
                            if flat_: _q1_ = min(_q1_, abs(_INV))
                            if _psf is not None and _psf < 1.0 - EPS and _q1_ >= MINQ - EPS: _q1_ = max(MINQ, _q1_ * _psf)
                            if _psq is not None: _q1_ = min(_q1_, _psq)
''')
ENGINE_UNFOLD = ('                        if _l1f_q <= 0.0 and o is not None and o.l1f: B.deep[side] = None; '
                 'cancels["l1t_l0_unfold"] += 1; o = None\n')
# vm751 (k151/mk_vm751.py on vm749), verbatim: the l1c bound inside the fold, and the re-place of a larger carrying level 0
ENGINE_L1C = textwrap.dedent('''\
                            if L1C and _l1t_here and (L1C != 3 or not _tw_t):        # k151 vm751 L1C: the carried size within the gap to tau
                                _l1c_here = True; K151["eval_side_states"] += 1
                                if L1C in (1, 3):
                                    _q1c_ = min(_q1_, max(0.0, abs(_INV) - q0_)); K151["cap_bound_side_states"] += int(_q1c_ < _q1_ - EPS); _q1_ = _q1c_
                                elif abs(_INV) < q0_ + _q1_ - EPS:
                                    K151["fit_closed_side_states"] += 1; _q1_ = 0.0
                                if _q1_ < MINQ - EPS: K151["no_fold_side_states"] += 1
                            if _q1_ >= MINQ - EPS: _l1f_q = _q1_; K145["l1f_side_states"] += 1
''')
# vm753 (k153/mk_vm753.py on vm751), verbatim: the l1c bound with k153's toward-side modes (only 4 is ported)
ENGINE_L1C_753 = textwrap.dedent('''\
                            if L1C and _l1t_here and (L1C != 3 or not _tw_t) and not (L1C >= 4 and _tw_t):   # k151 vm751 L1C (k153: modes 4-6 = the far cap here)
                                _l1c_here = True; K151["eval_side_states"] += 1
                                if L1C in (1, 3, 4, 5, 6):
                                    _q1c_ = min(_q1_, max(0.0, abs(_INV) - q0_)); K151["cap_bound_side_states"] += int(_q1c_ < _q1_ - EPS); _q1_ = _q1c_
                                elif abs(_INV) < q0_ + _q1_ - EPS:
                                    K151["fit_closed_side_states"] += 1; _q1_ = 0.0
                                if _q1_ < MINQ - EPS: K151["no_fold_side_states"] += 1
                            if L1C >= 4 and _l1t_here and _tw_t:                      # k153 vm753: the toward side's bound (modes 4 / 5 / 6)
                                _ag_t = _st_t != 0.0 and (_st_t > 0.0) != (side == "bid"); K153["tw_eval_side_states"] += 1; K153["tw_against_step_side_states"] += int(_ag_t)
                                if L1C == 4 and _ag_t:
                                    _l1c_here = True; _q1c_ = min(_q1_, max(0.0, abs(_INV) - q0_)); K153["tw_cap_bound_side_states"] += int(_q1c_ < _q1_ - EPS); _q1_ = _q1c_
                                elif L1C == 5:
                                    _l1c_here = True; _q1c_ = min(_q1_, max(0.0, abs(_INV))); K153["tw_clip_bound_side_states"] += int(_q1c_ < _q1_ - EPS); _q1_ = _q1c_
                                elif L1C == 6 and _ag_t:
                                    _l1c_here = True; K153["tw_unfold_side_states"] += 1; _q1_ = 0.0
                                if _l1c_here and _q1_ < MINQ - EPS: K153["tw_no_fold_side_states"] += 1
                            if _q1_ >= MINQ - EPS: _l1f_q = _q1_; K145["l1f_side_states"] += 1
''')
ENGINE_RESIZE = ('                        if _l1c_here and _l1f_q > 0.0 and o is not None and o.l1f and o.rem > q0_ + _l1f_q + EPS: '
                 'B.deep[side] = None; cancels["l1c_l0_resize"] += 1; o = None   # k151\n')
_C_L1C = compile(ENGINE_L1C, "<vm751 l1c>", "exec")
_C_L1C_753 = compile(ENGINE_L1C_753, "<vm753 l1c>", "exec")
_C_RED = compile(ENGINE_REDUCING.strip() + "\n", "<vm749 reducing_>", "exec")
_C_GATE = compile(ENGINE_GATE, "<vm749 L1T gate>", "exec")
_C_Q1 = compile("if True:\n" + textwrap.indent(ENGINE_Q1, "    "), "<vm749 L1 size>", "exec")


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _engine_gate(mode, side, inv, tau, step, *, ts=30 * 1000 * S, step_here=True, cls=0, vac=False):
    """vm749's own lines for one class-0 book side: (reducing_, _l1t_here)."""
    ns = {"side": _A2E[side], "_INV": inv - tau, "EPS": 1e-9, "L1T": mode, "CLS_B": [cls], "b": 0, "vac_on": vac,
          "_tau": tau, "STEPDIR": [float(step)], "STEP_BLK": [int((ts // S) // 30) if step_here else None], "ts": ts,
          "K149": defaultdict(float)}
    exec(_C_RED, ns)
    exec(_C_GATE, ns)
    return ns["reducing_"], ns["_l1t_here"]


def _engine_l1c(l1c, here, tw, inv, tau, q0, q1, minq):
    """vm751's l1c lines on one folded side: (_l1c_here, _l1f_q) -- the carried size after the bound (0.0: no fold)."""
    ns = {"L1C": l1c, "_l1t_here": here, "_tw_t": tw, "_INV": inv - tau, "q0_": q0, "_q1_": q1, "MINQ": minq, "EPS": 1e-9,
          "_l1c_here": False, "_l1f_q": 0.0, "K151": defaultdict(float), "K145": defaultdict(float)}
    exec(_C_L1C, ns)
    return ns["_l1c_here"], ns["_l1f_q"]


def _engine_l1c753(l1c, here, side, step, inv, tau, q0, q1, minq):
    """vm753's l1c lines on one folded side (the toward side's AG-CAP reads the block's step _st_t): (_l1c_here, _l1f_q)."""
    tw = (side == BUY) == (tau > 0.0)
    ns = {"L1C": l1c, "_l1t_here": here, "_tw_t": tw, "_st_t": float(step), "side": _A2E_INV[side], "_INV": inv - tau,
          "q0_": q0, "_q1_": q1, "MINQ": minq, "EPS": 1e-9, "_l1c_here": False, "_l1f_q": 0.0, "K151": defaultdict(float),
          "K145": defaultdict(float), "K153": defaultdict(float)}
    exec(_C_L1C_753, ns)
    return ns["_l1c_here"], ns["_l1f_q"]


def _engine_q1(clip, psf, minq, l1_present=True):
    """vm749's L1 size (deepc 2, lsz 2 = one deep clip at the 2-base clip; the launch clip clip_; _psf the v6.20 scale)."""
    ns = {"_Dl1_": 45.0 if l1_present else 30.0, "_Dk_pre": 30.0, "_ag_dp": False, "Dk": 30.0, "_ls1_": 2.0, "dz_": 1.0,
          "clip_": clip, "P": {"deepc": 2.0, "cp": 0}, "paced_b": False, "_lopen": abs(clip - 2.0) > 1e-12, "flat_": False,
          "_INV": 0.0, "_psf": psf, "EPS": 1e-9, "MINQ": minq, "_psq": None, "_q1_": None}
    exec(_C_Q1, ns)
    q = ns["_q1_"]
    return q if (q is not None and q >= minq - 1e-9) else 0.0


# ---- 1. the pure rule --------------------------------------------------------------------------------------------------------

def test_the_mode_parses_to_0_1_2_3_only():
    for v, want in ((0, 0), (1, 1), (2, 2), (3, 3), ("0", 0), (" 2 ", 2), ("3", 3), (1.0, 1), (0.0, 0), ("1.0", 1)):
        assert lt.parse_mode(v) == want, v                      # taos ParseKwargs hands PARAMS numbers over as floats
    for v in (4, -1, "4", "x", "", None, True, False, 1.5, "1.5", 4.0, float("nan"), float("inf"), [1]):
        assert lt.parse_mode(v) is None, v
    for v, want in ((0, 0), (1, 1), (3, 3), (4, 4), (1.0, 1), (3.0, 3), (4.0, 4), ("0", 0), (" 3 ", 3)):
        assert lt.parse_l1c(v) == want, v
    for v in (2, 2.0, "2", 5, 6, -1, 1.5, True, None, "x"):     # the engines' FIT / ONE-CLIP / AG-UNFOLD: not arms, not ported
        assert lt.parse_l1c(v) is None, v
    assert lt.L1C_MODES == (0, 1, 3, 4) and lt.L1C_GATES == (1, 2) and lt.L1C_NAMES[3] == "FAR_CAP"
    assert lt.L1C_NAMES[4] == "AG_CAP" and lt.L1C_STEP_GATES == {4: (2,)}
    assert lt.MODES == (0, 1, 2, 3) and lt.MODE_NAMES == {0: "off", 1: "L1T", 2: "L1TS", 3: "L1TI"}
    assert lt.STEP_MODES == (2, 3) and lt.FOLD_LEVEL == 1 and lt.SIDES == (BUY, SELL)


def test_toward_is_the_side_tau_leans_to_and_none_at_tau_0():
    assert lt.toward(BUY, 2.0) and not lt.toward(SELL, 2.0) and lt.toward(SELL, -2.0) and not lt.toward(BUY, -2.0)
    for bad in ((BUY, 0.0), (SELL, 0.0), (BUY, None), ("both", 2.0), (BUY, float("nan")), (BUY, True)):
        assert not lt.toward(*bad), bad


def test_reducing_reads_the_true_position_against_tau():
    """reducing = the side's fill moves the TRUE inventory toward tau (inv - tau), v6.41 pace's eps."""
    assert lt.reducing(BUY, 0.0, 5.0) and not lt.reducing(SELL, 0.0, 5.0)          # flat, tau long: the buy moves toward it
    assert lt.reducing(BUY, 3.0, 5.0) and not lt.reducing(BUY, 6.0, 5.0)           # long 6 beyond tau 5: the buy does not
    assert lt.reducing(SELL, 6.0, 5.0) and lt.reducing(SELL, 2.0, 0.0)             # tau 0: plain unwinding
    assert not lt.reducing(BUY, 5.0, 5.0) and not lt.reducing(SELL, 5.0 + 1e-10, 5.0)   # at tau (within eps): neither
    assert lt.reducing(SELL, 5.0 + 2e-9, 5.0)
    for bad in ((BUY, None, 1.0), (BUY, 1.0, None), ("x", 1.0, 2.0), (BUY, float("nan"), 2.0)):
        assert not lt.reducing(*bad), bad


def test_the_gate_is_closed_at_tau_0_in_every_mode():
    for mode in (1, 2, 3):
        for side in (BUY, SELL):
            for inv in (-3.0, 0.0, 3.0):
                for step in (-1, 0, 1):
                    assert not lt.gate(mode, side, inv, 0.0, step)
                    assert _engine_gate(mode, side, inv, 0.0, step)[1] is False
    assert not lt.gate(0, BUY, 0.0, 5.0, 1) and not lt.gate(None, BUY, 0.0, 5.0, 1)


def test_the_three_modes_on_their_own_cases():
    # tau long 5: buy = toward; flat inv: buy reduces (toward tau), sell does not
    assert lt.gate(1, BUY, 0.0, 5.0) and not lt.gate(1, SELL, 0.0, 5.0)
    assert not lt.gate(1, SELL, 8.0, 5.0)                       # sell unwinds beyond tau: reducing, not toward -> L1T no
    assert lt.gate(2, SELL, 8.0, 5.0, -1) and not lt.gate(2, SELL, 8.0, 5.0, 1)   # L1TS: with the step (down) yes
    assert not lt.gate(2, SELL, 8.0, 5.0, 0)
    assert lt.gate(3, BUY, 8.0, 5.0, 1) and not lt.gate(3, BUY, 8.0, 5.0, -1)     # L1TI: toward, adding beyond tau, step with lean
    assert not lt.gate(3, SELL, 8.0, 5.0, -1)                   # L1TI: never the far side
    assert lt.gate(3, BUY, 0.0, 5.0, -1)                        # L1TI: toward and reducing, whatever the step


def test_the_gate_is_one_pure_function_per_mode_and_a_new_mode_is_one_entry():
    assert lt.GATES == {1: lt.gate_l1t, 2: lt.gate_l1ts, 3: lt.gate_l1ti}
    rng = random.Random(6422)
    for _ in range(3000):
        side, inv = rng.choice((BUY, SELL)), rng.uniform(-9, 9)
        tau, step = rng.choice((-5.0, -1.0, 1.0, 5.0)), rng.choice((-1, 0, 1))
        for m, fn in lt.GATES.items():
            assert lt.gate(m, side, inv, tau, step) == fn(side, inv, tau, step)
    seen = []
    lt.GATES[9] = lambda side, inv, tau, step=0, **ctx: seen.append(ctx) or True       # a later round's mode (not parsed)
    try:
        assert not lt.gate(9, BUY, 0.0, 1.0)                    # parse_mode refuses it: the table alone opens nothing
    finally:
        del lt.GATES[9]
    assert lt.CARRIED == {1: lt.carried_cap, 3: lt.carried_far_cap, 4: lt.carried_ag_cap}   # one pure function per l1c mode


def test_l1c_the_carried_size_within_the_gap_to_tau():
    # CAP: min(q1, max(0, gap - q0)) on every folded side
    assert lt.carried_size(1, 2.0, 2.0, 5.0, True) == 2.0 and lt.carried_size(1, 2.0, 2.0, 3.0, True) == 1.0
    assert lt.carried_size(1, 2.0, 2.0, 2.0, False) == 0.0 and lt.carried_size(1, 2.0, 2.0, 1.0, False) == 0.0   # gap <= q0
    # FAR-CAP: the far side bounded, the toward side carries q1
    assert lt.carried_size(3, 2.0, 2.0, 3.0, False) == 1.0 and lt.carried_size(3, 2.0, 2.0, 3.0, True) == 2.0
    assert lt.carried_size(0, 2.0, 2.0, 0.5, False) == 2.0 and lt.carried_size(2, 2.0, 2.0, 0.5, False) == 2.0   # off / FIT
    assert lt.bounded(1, True) and lt.bounded(1, False) and lt.bounded(3, False) and not lt.bounded(3, True)
    assert not lt.bounded(0, False) and not lt.bounded(2, False)
    # below the minimum: no fold (gap < q0 + MINQ); on the grid, floored
    assert lt.bounded_fold(1, 2.0, 2.0, 2.2, True, 0.25, 4) == 0.0                     # 0.2 < 0.25
    assert lt.bounded_fold(1, 2.0, 2.0, 2.25, True, 0.25, 4) == 0.25                    # exactly the minimum
    assert lt.bounded_fold(1, 2.0, 2.0, 3.333333, True, 0.25, 4) == 1.3333              # floored, never past tau
    assert lt.bounded_fold(3, 2.0, 2.0, 2.2, True, 0.25, 4) == 2.0                      # toward under FAR-CAP: q1
    assert lt.bounded_fold(1, 2.0, 0.0, 9.0, True, 0.25, 4) == 0.0
    assert lt.resizes(4.0, 2.0, 1.0) and not lt.resizes(3.0, 2.0, 1.0) and not lt.resizes(3.0 + 1e-10, 2.0, 1.0)
    assert not lt.resizes(4.0, 2.0, 0.0) and not lt.resizes(None, 2.0, 1.0)


def test_l1c_ag_cap_bounds_the_far_side_and_the_toward_side_only_against_the_step():
    assert lt.against_step(BUY, -1) and lt.against_step(SELL, 1) and not lt.against_step(BUY, 1)
    assert not lt.against_step(BUY, 0) and not lt.against_step(SELL, 0) and not lt.against_step("x", 1)
    assert lt.carried_ag_cap(2.0, 2.0, 3.0, True, False) == 2.0                 # toward, with the step (or none): q1
    assert lt.carried_ag_cap(2.0, 2.0, 3.0, True, True) == 1.0                  # toward, against: capped at the gap
    assert lt.carried_ag_cap(2.0, 2.0, 1.5, True, True) == 0.0                  # gap <= q0: nothing carried
    assert lt.carried_ag_cap(2.0, 2.0, 3.0, False, False) == 1.0                # far side: FAR-CAP
    assert lt.bounded(4, True, True) and not lt.bounded(4, True, False) and lt.bounded(4, False, False)
    assert lt.bounded(3, True, True) is False and lt.bounded(1, True) and not lt.bounded(0, False, True)
    assert lt.carried_size(3, 2.0, 2.0, 3.0, True, True) == 2.0                 # mode 3 ignores against (unchanged)
    assert lt.bounded_fold(4, 2.0, 2.0, 2.2, True, 0.25, 4, True) == 0.0        # below the minimum against the step: no fold
    assert lt.bounded_fold(4, 2.0, 2.0, 2.2, True, 0.25, 4, False) == 2.0       # with the step: uncapped


def test_l1c_ag_cap_matches_vm753s_own_lines_on_random_folded_sides():
    """vm753's l1c lines (embedded verbatim above: k151's far cap + k153's toward-side bound, mode 4) vs gate + bounded_fold
    on 30,000 random class-0 sides of L1TSXA (l1t 2, l1c 4), and vm753 == vm751 on modes 1 / 3."""
    rng = random.Random(753)
    for l1t, l1c in ((2, 4), (2, 3), (1, 1), (2, 1)):
        bad = n_tw_cap = n_tw_free = n_far = 0
        for _ in range(30000 if l1c == 4 else 8000):
            side = rng.choice((BUY, SELL))
            tau = rng.choice((2.0, -2.0, 5.0, -5.0, rng.uniform(-9, 9)))
            inv = tau + rng.choice((0.0, 1e-10, 1.0, -1.0, 2.2, -2.2, 3.0, -3.0, 4.25, -4.25, rng.uniform(-8, 8)))
            step = rng.choice((-1, 0, 1))
            q0, q1 = rng.choice((2.0, 0.6, 3.0)), rng.choice((2.0, 0.6, 3.0))
            minq = rng.choice((0.25, 0.7))
            if q1 < minq:
                continue
            _red, here = _engine_gate(l1t, side, inv, tau, step)
            if not here:
                continue
            eh, eq = _engine_l1c753(l1c, here, side, step, inv, tau, q0, q1, minq)
            tw, ag = lt.toward(side, tau), lt.against_step(side, step)
            lb = lt.bounded(l1c, tw, ag)
            lq = lt.bounded_fold(l1c, q0, q1, abs(inv - tau), tw, minq, 4, ag)
            if eh != lb or not (eq - 1e-4 - 1e-9 <= lq <= eq + 1e-9) or ((eq > 0) != (lq > 0)):
                bad += 1
            n_tw_cap += int(tw and lb); n_tw_free += int(tw and not lb); n_far += int(not tw)
        assert bad == 0, (l1t, l1c, bad)
        if l1c == 4:
            assert n_tw_cap > 1000 and n_tw_free > 1000 and n_far > 1000, (n_tw_cap, n_tw_free, n_far)


def test_l1c_ag_cap_in_the_deep_layer_against_with_and_no_step_and_its_replace():
    def run(step, inv, tau, l1c=4, ag=None):
        a = _ladder(2, l1c=l1c)
        a._v6_36_record.step_block, a._v6_36_record.direction = 1000, {3: step}
        a._v6_36_req = {"block": 1000}
        return a, _deep(a, inv=inv, tau=tau)
    # tau 5, true inventory 3: the buy is the toward side and reduces; gap 2 = q0 -> capped carries nothing
    assert _pl(run(-1, 3.0, 5.0)[1])[(BUY, 0)] == 2.0                           # against the step (down): capped -> no fold
    assert _pl(run(1, 3.0, 5.0)[1])[(BUY, 0)] == 4.0                            # with the step: uncapped
    assert _pl(run(0, 3.0, 5.0)[1])[(BUY, 0)] == 4.0                            # no step this block: uncapped
    assert _pl(run(-1, 1.0, 4.5)[1])[(BUY, 0)] == 3.5                           # gap 3.5 against: 2 + 1.5
    assert _pl(run(-1, 8.0, 5.0)[1])[(SELL, 0)] == 3.0                          # far side (with the step): FAR-CAP 2 + 1
    a, _r = run(1, 0.0, 5.0)                                                    # a carrying level 0 of 4 rests ...
    _rest(a, 11, 0, 0, qty=4.0)
    a._v6_36_record.direction = {3: -1}                                         # ... the step turns against it: gap 3.5
    resp = _deep(a, inv=1.0, tau=4.5)
    assert 11 in _cx(resp) and a._v6_42_counts["l1c_resize_cancels"] == 1 and a._v6_42_refold == {(3, BUY): True}
    a._v6_36_record.direction = {3: 1}                                          # with the step again: the hold drops
    b, _r = run(1, 0.0, 5.0)
    _rest(b, 11, 0, 0, qty=4.0)
    b._v6_36_record.direction = {3: -1}
    resp = _deep(b, inv=2.9, tau=5.0)                                           # against, gap 2.1: below the minimum -> unfold
    assert 11 in _cx(resp) and b._v6_42_counts["unfold_cancels"] == 1


def test_l1c_matches_vm751s_own_lines_on_random_folded_sides():
    """vm751's l1c lines (embedded verbatim above) vs gate + bounded_fold, 3 x 20,000 random class-0 sides of the three k151
    pairs: the same bounded flag and the same carried size (live floored to the volume grid: within one grid step below)."""
    rng = random.Random(751)
    for l1t, l1c in ((1, 1), (2, 1), (2, 3)):
        bad = n_b = n_no = 0
        for _ in range(20000):
            side = rng.choice((BUY, SELL))
            tau = rng.choice((2.0, -2.0, 5.0, -5.0, rng.uniform(-9, 9)))
            inv = tau + rng.choice((0.0, 1e-10, 1.0, -1.0, 2.2, -2.2, 3.0, -3.0, 4.25, -4.25, rng.uniform(-8, 8)))
            step = rng.choice((-1, 0, 1))
            q0 = rng.choice((2.0, 2.0, 0.6, 3.0))
            q1 = rng.choice((2.0, 2.0, 0.6, 3.0))
            minq = rng.choice((0.25, 0.7))
            _red, here = _engine_gate(l1t, side, inv, tau, step)
            tw = lt.toward(side, tau)
            eh, eq = _engine_l1c(l1c, here, tw, inv, tau, q0, q1, minq)
            if not here:
                continue
            lb = lt.bounded(l1c, tw)
            # the live L1 size already met the minimum (fold_quantity) before the bound
            lq = lt.bounded_fold(l1c, q0, q1, abs(inv - tau), tw, minq, 4) if q1 >= minq else 0.0
            if eh != lb or not (eq - 1e-4 - 1e-9 <= lq <= eq + 1e-9) or ((eq > 0) != (lq > 0)):
                bad += 1
            n_b += int(lb and lq < q1)
            n_no += int(lq == 0.0)
        assert bad == 0, (l1t, l1c, bad)
        assert n_b > 1000 and n_no > 300, (l1t, l1c, n_b, n_no)


def test_with_step_0_modes_2_and_3_are_mode_1():
    rng = random.Random(6421)
    for _ in range(4000):
        side = rng.choice((BUY, SELL))
        inv, tau = rng.choice((-6.0, -2.0, 0.0, 1e-12, 2.0, 6.0)), rng.choice((-5.0, -1.0, 1.0, 5.0))
        g1 = lt.gate(1, side, inv, tau, 0)
        assert lt.gate(2, side, inv, tau, 0) == g1 == lt.gate(3, side, inv, tau, 0)


def test_the_gate_matches_vm749s_own_lines_on_random_states():
    """vm749's reducing test and L1T gate (embedded verbatim above) vs research_v6_42_l1t on 3 x 20,000 random class-0 side
    states: the same reducing_ and the same gate on every one."""
    rng = random.Random(749)
    for mode in (1, 2, 3):
        bad = 0
        n_open = 0
        for _ in range(20000):
            side = rng.choice((BUY, SELL))
            tau = rng.choice((0.0, 2.0, -2.0, 4.0, -4.0, rng.uniform(-8, 8)))
            inv = rng.choice((tau, tau + 1e-10, tau - 1e-10, tau + 3e-9, 0.0, 2.0, -2.0, rng.uniform(-12, 12)))
            step = rng.choice((-1, 0, 1))
            here = rng.random() < 0.8
            red_e, gate_e = _engine_gate(mode, side, inv, tau, step, step_here=here)
            st = step if here else 0
            if tau != 0.0 and red_e != lt.reducing(side, inv, tau):
                bad += 1
            if gate_e != lt.gate(mode, side, inv, tau, st):
                bad += 1
            n_open += int(gate_e)
        assert bad == 0, (mode, bad)
        assert n_open > 2000, (mode, n_open)


def test_step_sign_is_the_current_blocks_only():
    assert lt.step_sign({3: 1}, 100, 100, 3) == 1 and lt.step_sign({3: -1}, 100, 100, 3) == -1
    assert lt.step_sign({3: 1}, 99, 100, 3) == 0 and lt.step_sign({3: 1}, None, 100, 3) == 0
    assert lt.step_sign({3: 1}, 100, None, 3) == 0 and lt.step_sign({}, 100, 100, 3) == 0
    assert lt.step_sign({3: 0}, 100, 100, 3) == 0 and lt.step_sign(None, 100, 100, 3) == 0


def test_the_step_rolls_over_with_v636s_record_block_by_block():
    """v6.36's StepRecord (the agent's own record): the step of block n is read on block n's states only; a state of the
    next block before its step (a gap), or without fundamentals, reads 0."""
    rec = sw.StepRecord()
    grid = {"status": sw.GRID_OK, "interval_ns": S, "k": 30}
    t0 = 30 * 1000 * S
    blk = lambda ts: sw.block_phase(ts, grid)[0]
    rec.observe(t0, blk(t0), {3: 100.0})
    assert lt.step_sign(rec.direction, rec.step_block, blk(t0), 3) == 0          # first state: no step yet
    t1 = t0 + 30 * S
    rec.observe(t1, blk(t1), {3: 101.0})
    assert lt.step_sign(rec.direction, rec.step_block, blk(t1), 3) == 1
    t1b = t1 + 29 * S
    rec.observe(t1b, blk(t1b), {3: 101.0})
    assert lt.step_sign(rec.direction, rec.step_block, blk(t1b), 3) == 1          # the whole block reads its step
    t2 = t1 + 30 * S
    assert lt.step_sign(rec.direction, rec.step_block, blk(t2), 3) == 0           # the next block, before its state
    rec.observe(t2, blk(t2), {3: 100.5})
    assert lt.step_sign(rec.direction, rec.step_block, blk(t2), 3) == -1
    t4 = t2 + 60 * S                                                              # a skipped block: a gap, no step
    rec.observe(t4, blk(t4), {3: 100.0})
    assert lt.step_sign(rec.direction, rec.step_block, blk(t4), 3) == 0
    rec.observe(t4 + S, blk(t4 + S), None)                                        # no fundamentals: the record breaks
    assert lt.step_sign(rec.direction, rec.step_block, blk(t4 + S), 3) == 0


def test_l1s_size_is_the_ladders_own_after_its_caps_and_matches_vm749():
    one = [(1, 45.0, 1.0), (2, 60.0, 2.0)]
    assert lt.fold_quantity(one, 2.0, 1.0, 0.25, 4) == 2.0
    assert lt.fold_quantity([(2, 60.0, 2.0)], 2.0, 1.0, 0.25, 4) == 0.0           # no L1 level this request
    assert lt.fold_quantity([], 2.0, 1.0, 0.25, 4) == 0.0 and lt.fold_quantity(None, 2.0, 1.0, 0.25, 4) == 0.0
    assert lt.fold_quantity(one, 3.0, 1.0, 0.25, 4) == 3.0                        # the launch clip
    assert lt.fold_quantity(one, 2.0, 0.3, 0.25, 4) == 0.6                        # v6.20's scale
    assert lt.fold_quantity(one, 2.0, 0.01, 0.25, 4) == 0.25                      # never below the minimum
    assert lt.fold_quantity(one, 0.2, 1.0, 0.25, 4) == 0.0                        # below the minimum: nothing carried
    assert lt.fold_quantity(one, 2.123456, 1.0, 0.25, 4) == 2.1235                # the volume grid
    rng = random.Random(7491)
    for _ in range(3000):
        clip = rng.choice((2.0, 2.0, 3.0, 4.0, 0.2, 2.5))
        psf = rng.choice((None, 1.0, 0.5, 0.3, 0.05, rng.uniform(0.0, 1.0)))
        minq = rng.choice((0.25, 0.25, 1.0, 2.5364))
        pres = rng.random() < 0.85
        e = _engine_q1(clip, psf, minq, pres)
        lv = lt.fold_quantity(one if pres else [(2, 60.0, 2.0)], clip, 1.0 if psf is None else psf, minq, 4)
        assert abs(lv - round(e, 4)) < 1e-12, (clip, psf, minq, pres, e, lv)


def test_the_carried_size_is_level_0s_plus_l1s_on_the_grid_and_the_unfold():
    assert lt.carried_quantity(2.0, 2.0, 4) == 4.0 and lt.carried_quantity(0.6, 0.6, 4) == 1.2
    assert lt.carried_quantity(0.1 + 0.2, 0.0, 4) == 0.3
    assert lt.unfolds(True, 0.0) and not lt.unfolds(True, 2.0) and not lt.unfolds(False, 0.0) and lt.unfolds(True, None)


# ---- 2. the part on a stub agent ---------------------------------------------------------------------------------------------

def _part_module():
    import importlib
    return importlib.import_module("simple_parts.v6_42_part")


def _agent(**cfg):
    mod = _part_module()

    class Base:
        def __init__(self):
            self.calls = []

        def _v6_41_init(self):
            self.calls.append(("v641_init",))

        def _v6_40_request(self, state):
            self.calls.append(("v640_request",))

        def _emit(self, kind, **row):
            self.calls.append(("emit", kind, row))

    class Agent(mod.V642Part, Base):
        def __init__(self, config):
            Base.__init__(self)
            self.config = types.SimpleNamespace(**config)
            self._tick = 0
            self._v6_40_table = None

    a = Agent(cfg)
    a._v6_41_init()
    return a


def test_default_is_off_and_an_unreadable_value_runs_off_with_an_error():
    a = _agent()
    assert a.research_v642_l1t == 0 and a.calls == [("v641_init",)] and a._v6_42_errors == 0
    assert a._v6_42_books == {} and a._v6_42_carry == {}
    for mode in ("1", 2, " 3 "):
        assert _agent(research_v642_l1t=mode).research_v642_l1t == int(str(mode).strip())
    for bad in ("4", "x", 1.5, None, True):
        b = _agent(research_v642_l1t=bad)
        assert b.research_v642_l1t == 0 and b._v6_42_errors == 1, bad


def test_the_note_keeps_the_true_position_and_this_requests_tau_on_class_0_books_only():
    a = _agent(research_v642_l1t=1)
    a._v6_25_taus = {3: (5.0, 100), 4: (5.0, 99)}
    a._v6_42_note(3, 100, 2.0, True)
    a._v6_42_note(4, 100, 2.0, True)                    # the lean was set at another state time: tau 0
    a._v6_42_note(5, 100, 2.0, False)                   # foreign / a class grid: not class 0
    assert a._v6_42_books == {3: (2.0, 5.0, True), 4: (2.0, 0.0, True), 5: (2.0, 0.0, False)}
    a._v6_40_table = {"status": "ok", "book_class": {3: 0, 70: 1, 100: 2}}
    a._v6_42_note(70, 100, 1.0, True)
    a._v6_42_note(3, 100, 1.0, True)
    assert a._v6_42_books[70][2] is False and a._v6_42_books[3][2] is True
    a._v6_40_table = {"status": "unreadable"}
    a._v6_42_note(3, 100, 1.0, True)
    assert a._v6_42_books[3][2] is False                # fail closed
    a._v6_40_table = {"status": "single"}
    assert a._v6_42_home(70)


def test_l1c_reads_its_key_and_runs_off_without_l1t_1_or_2():
    for t, c, want_c, err in ((1, 1, 1, 0), (2, 3, 3, 0), (2, 1.0, 1, 0), (1, "3", 3, 0), (3, 1, 0, 1), (0, 1, 0, 1),
                              (1, 2, 0, 1), (1, 0, 0, 0), (2, "x", 0, 1), (2, 4, 4, 0), (2, 4.0, 4, 0), (1, 4, 0, 1),
                              (3, 4, 0, 1), (2, 5, 0, 1)):
        a = _agent(research_v642_l1t=t, research_v642_l1c=c)
        assert a.research_v642_l1c == want_c and a._v6_42_errors == err, (t, c)
    assert _agent().research_v642_l1c == 0


def test_the_telemetry_row_follows_v641s_request_with_the_mode():
    a = _agent(research_v642_l1t=2)
    a._v6_40_request(None)
    assert a.calls[-2][0] == "v640_request" and a.calls[-1][:2] == ("emit", "V6_42_STATE")
    row = a.calls[-1][2]
    assert row["reason"] == "start" and row["l1t"] == 2 and row["l1t_name"] == "L1TS" and row["version"] == "l1t_v6_42"
    assert row["carrying_sides"] == 0 and row["errors"] == 0 and row["l1c"] == 0 and row["l1c_name"] == "off"
    b = _agent(research_v642_l1t=2.0, research_v642_l1c=3.0)                # as the launcher's PARAMS arrive: floats
    b._v6_40_request(None)
    row = b.calls[-1][2]
    assert (row["l1t"], row["l1t_name"], row["l1c"], row["l1c_name"], row["errors"]) == (2, "L1TS", 3, "FAR_CAP", 0)
    a._v6_40_request(None)
    assert a.calls[-1] == ("v640_request",)                              # once, then periodic / on a new error


# ---- 3. the deep layer: t69's ladder harness (the real joined _v633_book with v6.42's hooks) ----------------------------------

def _ladder(mode, *, live=None, l1c=0):
    import test_research_v6_23 as t623
    mod = _part_module()
    import research_v620_room_pace as rp
    agent = t623._ladder_agent()
    fn = type(agent)._v633_book
    g = dict(fn.__globals__, v620_scaled_quantity=rp.scaled_quantity)   # v6.20's scale (the harness copy lacks it)
    book = types.FunctionType(fn.__code__, g, fn.__name__, fn.__defaults__, fn.__closure__)
    book.__kwdefaults__ = fn.__kwdefaults__
    agent.__class__ = type("P642", (mod.V642Part, type(agent)), {"_v633_book": book})
    agent._v6_42_init()
    agent.research_v642_l1t = mode
    agent.research_v642_l1c = l1c
    agent._v6_40_table = None
    agent._v6_36_record = sw.StepRecord()
    agent._v6_36_req = {"block": None}
    for side, cid in (live or ()):
        agent.slots.setdefault(3, []).append((side, cid))
    return agent


def _deep(agent, *, inv=0.0, tau=0.0, note=True, eligible=True, clip=2.0, size_scale=1.0, budget=5, bid=100.00,
          ask=100.03, min_order=0.25, against=()):
    """One request on book 3 (depth 30, L1 45, L2 60 ticks): the pass's note (true inv, tau), then the deep layer with
    v6.25's inv - tau."""
    import test_research_v6_3_0_trend_target as t63
    import test_research_v6_9 as t69
    if note:
        agent._v6_42_books[3] = (float(inv), float(tau), bool(eligible))
    resp = t63._Resp()
    req = {"deep_books": 0, "placed_deep": 0, "cancels": 0}
    rows = [r for r in agent.ledger.live_orders(3) if dl.is_deep_client_id(r.client_id)]
    agent._v633_book(resp, 3, agent._v633_deep, 30.0, bid, ask, (inv - tau) if tau else inv, [], rows, set(), req,
                     tick_size=t69.TICK, dec=2, clip=clip, min_order=min_order, budget=budget, expiry=t69.EXPIRY,
                     cfg=types.SimpleNamespace(volumeDecimals=4), paced=False, bound_scale=1.0, held=frozenset(),
                     size_scale=size_scale, cap_skip=False, against=frozenset(against))
    return resp


def _pl(resp):
    import test_research_v6_3_0_trend_target as t63
    return {({"BUY": BUY, "SELL": SELL}[p[1]], dl.deep_level(p[2])): p[4] for p in t63._placed(resp)}


def _cx(resp):
    import test_research_v6_3_0_trend_target as t63
    return {oid for _b, oid in t63._cancelled(resp)}


def _rest(agent, oid, side, level, qty=None):
    import test_research_v6_9 as t69
    t69._rest(agent, oid, side, level)
    if qty is not None:
        for r in agent.ledger.live_orders(3):
            if int(r.order_id) == oid:
                r.quantity = qty
                r.remaining = qty
    return oid


def _gone(agent, oid):
    """Our order oid left the book (filled): neither the ledger nor the live slots hold it."""
    cid = next(r.client_id for r in agent.ledger.live_orders(3) if int(r.order_id) == oid)
    agent.ledger.note_removed(oid, cause="FILLED")
    agent.slots[3] = [x for x in agent.slots.get(3, []) if x[1] != cid]


def test_off_the_deep_layer_is_v641s_exactly():
    import test_research_v6_23 as t623
    import test_research_v6_9 as t69
    for setup in (lambda a: None, t69._rest_all):
        plain, off = t623._ladder_agent(), _ladder(0)
        setup(plain)
        setup(off)
        for inv, tau in ((0.0, 5.0), (8.0, 5.0), (0.0, 0.0)):
            r1 = t623._deep(plain, (), inv=(inv - tau) if tau else inv)
            r2 = _deep(off, inv=inv, tau=tau)
            assert [vars(i) for i in r1.instructions] == [vars(i) for i in r2.instructions]
        assert off._v6_42_counts == {} and off._v6_42_carry == {}


def test_a_new_level_0_carries_l1s_size_on_the_open_side_only():
    for mode in (1, 2, 3):
        a = _ladder(mode)
        resp = _deep(a, inv=0.0, tau=5.0)                  # tau long: the buy is toward and reduces (inv - tau = -5)
        assert _pl(resp) == {(BUY, 0): 4.0, (SELL, 0): 2.0}, mode
        assert a._v6_42_carry == {(3, BUY): True} and a._v6_42_counts["carried_l0_placed"] == 1
        assert a._v6_42_counts["carried_q"] == 2.0


def test_the_gate_reads_the_true_position_not_inv_minus_tau():
    a = _ladder(1)
    resp = _deep(a, inv=6.0, tau=5.0)                      # the layer gets inv - tau = +1: the buy does NOT move toward tau
    assert _pl(resp) == {(BUY, 0): 2.0, (SELL, 0): 2.0} and a._v6_42_carry == {}
    a = _ladder(1)
    assert _pl(_deep(a, inv=3.0, tau=5.0)) == {(BUY, 0): 4.0, (SELL, 0): 2.0}   # inv - tau = -2: it does
    a = _ladder(1)                                         # a note that does not give back the layer's inv: closed
    a._v6_42_books[3] = (3.0, 5.0, True)
    import test_research_v6_3_0_trend_target as t63
    import test_research_v6_9 as t69
    resp = t63._Resp()
    a._v633_book(resp, 3, a._v633_deep, 30.0, 100.00, 100.03, 1.0, [], [], set(),
                 {"deep_books": 0, "placed_deep": 0, "cancels": 0}, tick_size=t69.TICK, dec=2, clip=2.0, min_order=0.25,
                 budget=5, expiry=t69.EXPIRY, cfg=types.SimpleNamespace(volumeDecimals=4), paced=False, bound_scale=1.0,
                 held=frozenset(), size_scale=1.0, cap_skip=False, against=frozenset())
    assert _pl(resp) == {(BUY, 0): 2.0, (SELL, 0): 2.0} and a._v6_42_counts["lean_mismatch_book_states"] == 1


def test_tau_0_a_missing_note_a_foreign_book_and_a_vacuum_carry_nothing():
    for kw in ({"tau": 0.0, "inv": 3.0}, {"tau": 5.0, "note": False}, {"tau": 5.0, "eligible": False},
               {"tau": 5.0, "bid": 100.00, "ask": 100.40}):
        a = _ladder(1)
        resp = _deep(a, **kw)
        assert all(q in (2.0,) for (s, lv), q in _pl(resp).items() if lv == 0), kw
        assert a._v6_42_carry == {}, kw


def test_the_step_modes_read_the_current_blocks_step():
    t0 = 30 * 1000 * S
    for step, mode, want in ((-1, 2, 4.0), (1, 2, 2.0), (0, 2, 2.0), (1, 3, 2.0)):
        a = _ladder(mode)
        rec = a._v6_36_record
        rec.step_block, rec.direction = 1000, {3: step}
        a._v6_36_req = {"block": 1000}
        resp = _deep(a, inv=8.0, tau=5.0)                  # the sell unwinds beyond tau (reducing, not toward)
        assert _pl(resp)[(SELL, 0)] == want, (step, mode)
        a = _ladder(mode)
        a._v6_36_record.step_block, a._v6_36_record.direction = 999, {3: step}   # last block's step: not read
        a._v6_36_req = {"block": 1000}
        assert _pl(_deep(a, inv=8.0, tau=5.0))[(SELL, 0)] == 2.0
    a = _ladder(3)                                          # L1TI: the buy adds beyond tau while the step is up (with lean)
    a._v6_36_record.step_block, a._v6_36_record.direction = 1000, {3: 1}
    a._v6_36_req = {"block": 1000}
    assert _pl(_deep(a, inv=8.0, tau=5.0))[(BUY, 0)] == 4.0
    assert t0 // S // 30 == 1000


def test_caps_apply_before_the_fold_size_scale_launch_clip_and_minimum():
    a = _ladder(1)
    assert _pl(_deep(a, inv=0.0, tau=5.0, size_scale=0.3))[(BUY, 0)] == 1.2            # 0.6 + 0.6
    a = _ladder(1)
    assert _pl(_deep(a, inv=0.0, tau=5.0, clip=3.0))[(BUY, 0)] == 6.0                  # the launch clip: 3 + 3
    a = _ladder(1)                                         # L1's size below a larger minimum: level 0 at its own size
    assert _pl(_deep(a, inv=0.0, tau=5.0, size_scale=0.3, min_order=0.7)) == {(BUY, 0): 1.4, (SELL, 0): 0.7}   # both floored
    a = _ladder(1)                                         # L1's size below the minimum while level 0's is not: nothing carried
    a._v633_deep.clip = 2.0
    assert lt.fold_quantity([(1, 45.0, 1.0)], 0.5, 1.0, 0.7, 4) == 0.0 and lt.level0_quantity(0.7, 1.0, 0.7, 4) == 0.7
    a = _ladder(1)
    resp = _deep(a, inv=0.0, tau=5.0, clip=0.2)            # level 0 itself below the minimum: no level 0, nothing carried
    assert (BUY, 0) not in _pl(resp) and a._v6_42_carry == {}


def test_l1_is_held_only_while_the_carrying_level_0_rests_and_total_size_is_unchanged():
    import test_research_v6_9 as t69
    a = _ladder(1)
    assert _pl(_deep(a, inv=0.0, tau=5.0)) == {(BUY, 0): 4.0, (SELL, 0): 2.0}
    _rest(a, 11, 0, 0, qty=4.0)
    _rest(a, 12, 1, 0)
    resp = _deep(a, inv=0.0, tau=5.0)                      # the buy's L1 is carried by level 0: its L2 is next
    assert _pl(resp) == {(BUY, 2): 4.0, (SELL, 1): 2.0} and _cx(resp) == set()
    _rest(a, 14, 1, 1)
    _rest(a, 15, 0, 2)
    resp = _deep(a, inv=0.0, tau=5.0)
    assert _pl(resp) == {(SELL, 2): 4.0} and _cx(resp) == set()
    _rest(a, 16, 1, 2)
    resting = {(int(r.side), dl.deep_level(r.client_id)): float(r.quantity) for r in a.ledger.live_orders(3)}
    assert resting[(0, 0)] + resting[(0, 2)] == sum(t69.SIZE.values())                 # 4 + 4 == 2 + 2 + 4
    assert sum(q for (s, _l), q in resting.items() if s == 1) == sum(t69.SIZE.values())
    assert _deep(a, inv=0.0, tau=5.0).instructions == []                               # steady: nothing moves
    _gone(a, 11)                                           # the carrying level 0 filled: the tag goes, L1 may rest again
    resp = _deep(a, inv=-0.0, tau=0.0)                     # gate closed now: a plain level 0, and L1 is not held
    assert _pl(resp) == {(BUY, 0): 2.0} and a._v6_42_carry == {}
    assert a._v6_42_counts["carry_gone_side_states"] == 1


def test_a_filled_carrying_level_0_releases_l1_even_when_no_new_level_0_follows():
    a = _ladder(1)
    _deep(a, inv=0.0, tau=5.0)                                 # a carrying level 0 placed (tagged)
    _rest(a, 11, 0, 0, qty=4.0)
    assert (BUY, 1) not in _pl(_deep(a, inv=0.0, tau=5.0))   # L1 held while it rests
    _gone(a, 11)                                               # filled; v6.23 S1 withholds a new level 0 this request
    resp = _deep(a, inv=0.0, tau=5.0, against=((BUY, 0),))
    assert (BUY, 0) not in _pl(resp) and _pl(resp).get((BUY, 1)) == 2.0 and a._v6_42_carry == {}
    assert a._v6_42_counts["carry_gone_side_states"] == 1


def test_a_resting_l1_is_cancelled_with_a_new_carrying_level_0():
    a = _ladder(1)
    _rest(a, 13, 0, 1)                                     # the buy's L1 rests, its level 0 does not
    resp = _deep(a, inv=0.0, tau=5.0)
    assert _pl(resp) == {(BUY, 0): 4.0, (SELL, 0): 2.0} and _cx(resp) == {13}
    assert a._v6_42_counts["l1_cancels_with_fold"] == 1
    a = _ladder(1)
    _rest(a, 13, 0, 1)
    resp = _deep(a, inv=0.0, tau=5.0, budget=1)            # no instruction left for the cancel: it waits a request
    assert _pl(resp) == {(BUY, 0): 4.0} and _cx(resp) == set()
    assert a._v6_42_counts["l1_cancel_deferred"] == 1
    a.slots.setdefault(3, []).append((0, dl.level_client_ids(3, 0)[0]))               # the new level 0 in flight
    resp = _deep(a, inv=0.0, tau=5.0)
    assert _cx(resp) == {13} and a._v6_42_counts["l1_held_cancels"] == 1


def test_the_unfold_when_the_gate_closes_and_l1_returns():
    a = _ladder(1)
    _deep(a, inv=0.0, tau=5.0)
    _rest(a, 11, 0, 0, qty=4.0)
    _rest(a, 12, 1, 0)
    resp = _deep(a, inv=0.0, tau=-5.0)                     # the lean flipped: the buy is no longer toward
    assert _cx(resp) == {11} and a._v6_42_counts["unfold_cancels"] == 1 and a._v6_42_carry == {}
    assert _pl(resp) == {(BUY, 1): 2.0, (SELL, 1): 2.0}                              # L1 back at once (both sides' next)
    assert a._v633_counts.get("cancel_" + lt.CANCEL_UNFOLD.lower()) == 1
    a = _ladder(1)                                         # gate open but no L1 level this request (record too short)
    _deep(a, inv=0.0, tau=5.0)
    _rest(a, 11, 0, 0, qty=4.0)
    a._v633_deep.books[3].sweeps.clear()
    a._v633_deep.books[3].sweeps.extend([30.0] * 400)
    resp = _deep(a, inv=0.0, tau=5.0)
    assert 11 in _cx(resp) and a._v6_42_carry == {}


def test_no_forced_refold_a_plain_level_0_keeps_resting_when_the_gate_opens():
    a = _ladder(1)
    _rest(a, 11, 0, 0)                                     # a level 0 placed before the gate opened (own size)
    _rest(a, 13, 0, 1)
    resp = _deep(a, inv=0.0, tau=5.0)
    assert 11 not in _cx(resp) and 13 not in _cx(resp) and a._v6_42_carry == {}


def test_a_carrying_level_0_cancelled_for_another_reason_holds_no_l1():
    a = _ladder(1)
    _deep(a, inv=0.0, tau=5.0)
    import test_research_v6_9 as t69
    t69.t63.t14._rest(a, 11, 3, 0, 99.95, cid=dl.level_client_ids(3, 0)[0], qty=4.0)   # 6.5 ticks from the mid: repriced
    a.slots.setdefault(3, []).append((0, dl.level_client_ids(3, 0)[0]))
    resp = _deep(a, inv=0.0, tau=5.0)
    assert 11 in _cx(resp) and a._v6_42_carry == {} and (BUY, 1) in _pl(resp)
    a = _ladder(1)                                          # with an L1 resting beside it (deferred earlier): L1 stays
    _deep(a, inv=0.0, tau=5.0)
    t69.t63.t14._rest(a, 11, 3, 0, 99.95, cid=dl.level_client_ids(3, 0)[0], qty=4.0)
    a.slots.setdefault(3, []).append((0, dl.level_client_ids(3, 0)[0]))
    _rest(a, 13, 0, 1)
    resp = _deep(a, inv=0.0, tau=5.0)
    assert _cx(resp) == {11} and a._v6_42_carry == {}


def test_after_a_restart_a_resting_level_0_placed_larger_is_taken_as_carrying_at_the_first_look_only():
    a = _ladder(1)                                          # a new process: no tags; a level 0 of 4 (q0 2 + L1 2) rests
    _rest(a, 11, 0, 0, qty=4.0)
    _rest(a, 13, 0, 1)                                      # and an L1 beside it (placed by the old process before the fold)
    resp = _deep(a, inv=0.0, tau=5.0)
    assert 13 in _cx(resp) and a._v6_42_carry == {(3, BUY): True} and a._v6_42_counts["carry_inferred_at_start"] == 1
    b = _ladder(1)                                          # a plain level 0 (2): not carrying
    _rest(b, 11, 0, 0)
    _rest(b, 13, 0, 1)
    resp = _deep(b, inv=0.0, tau=5.0)
    assert _cx(resp) == set() and b._v6_42_carry == {}
    c = _ladder(1)                                          # after the first look, the size never sets a tag (paced: q0 0.6)
    _rest(c, 11, 0, 0)
    _deep(c, inv=0.0, tau=5.0)
    _rest(c, 13, 0, 1)
    resp = _deep(c, inv=0.0, tau=5.0, size_scale=0.3)
    assert _cx(resp) == set() and c._v6_42_carry == {} and "carry_inferred_at_start" not in c._v6_42_counts


def test_l1c_bounds_the_carried_size_in_the_deep_layer():
    assert _pl(_deep(_ladder(1, l1c=1), inv=0.0, tau=5.0)) == {(BUY, 0): 4.0, (SELL, 0): 2.0}     # gap 5: 2 + min(2, 3)
    assert _pl(_deep(_ladder(1, l1c=1), inv=1.0, tau=4.5)) == {(BUY, 0): 3.5, (SELL, 0): 2.0}     # gap 3.5: 2 + 1.5
    a = _ladder(1, l1c=1)
    assert _pl(_deep(a, inv=2.9, tau=5.0)) == {(BUY, 0): 2.0, (SELL, 0): 2.0}                  # gap 2.1: 0.1 < min: no fold
    assert a._v6_42_carry == {} and a._v6_42_counts["l1c_no_fold_side_states"] == 1
    a = _ladder(1, l1c=0)
    assert _pl(_deep(a, inv=2.9, tau=5.0)) == {(BUY, 0): 4.0, (SELL, 0): 2.0}                  # l1t alone: unbounded
    for l1c, want in ((1, 3.0), (3, 3.0)):                     # the far side (sell unwinds beyond tau with the step): bounded
        a = _ladder(2, l1c=l1c)
        a._v6_36_record.step_block, a._v6_36_record.direction = 1000, {3: -1}
        a._v6_36_req = {"block": 1000}
        assert _pl(_deep(a, inv=8.0, tau=5.0))[(SELL, 0)] == want, l1c                          # gap 3: 2 + 1
    a = _ladder(2, l1c=3)                                      # FAR-CAP leaves the toward side's fold whole
    assert _pl(_deep(a, inv=2.9, tau=5.0))[(BUY, 0)] == 4.0
    a = _ladder(2, l1c=1)
    assert _pl(_deep(a, inv=2.9, tau=5.0))[(BUY, 0)] == 2.0


def test_l1c_a_larger_carrying_level_0_is_replaced_and_l1_stays_held_until_the_new_one():
    a = _ladder(1, l1c=1)
    _deep(a, inv=0.0, tau=5.0)                                 # carries 2: level 0 of 4
    _rest(a, 11, 0, 0, qty=4.0)
    _rest(a, 12, 1, 0)
    _rest(a, 13, 0, 1)                                         # an L1 left from before (held: cancelled)
    resp = _deep(a, inv=1.0, tau=4.5)                          # gap 3.5: allowed 2 + 1.5 < 4 -> re-placed
    assert {11, 13} <= _cx(resp) and (BUY, 1) not in _pl(resp)
    assert a._v6_42_counts["l1c_resize_cancels"] == 1 and a._v6_42_refold == {(3, BUY): True}
    assert a._v633_counts.get("cancel_" + lt.CANCEL_RESIZE.lower()) == 1
    resp = _deep(a, inv=1.0, tau=4.5)                          # the cancel not seen yet: no new level 0, L1 held (L2: its own)
    assert (BUY, 0) not in _pl(resp) and (BUY, 1) not in _pl(resp) and a._v6_42_counts.get("l1c_resize_cancels") == 1
    _gone(a, 11)
    _gone(a, 13)
    resp = _deep(a, inv=1.0, tau=4.5)                          # the new carrying level 0 at the bound
    assert _pl(resp).get((BUY, 0)) == 3.5 and a._v6_42_refold == {} and a._v6_42_carry == {(3, BUY): True}
    b = _ladder(1, l1c=1)                                      # a carrying level 0 within the bound keeps resting
    _deep(b, inv=1.0, tau=4.5)
    _rest(b, 11, 0, 0, qty=3.5)
    assert 11 not in _cx(_deep(b, inv=1.0, tau=4.5))
    c = _ladder(1, l1c=1)                                      # the bound falls below the minimum: the k149 unfold
    _deep(c, inv=0.0, tau=5.0)
    _rest(c, 11, 0, 0, qty=4.0)
    resp = _deep(c, inv=2.9, tau=5.0)
    assert 11 in _cx(resp) and c._v6_42_counts["unfold_cancels"] == 1 and c._v6_42_refold == {}


def test_l1c_with_l1s_slot_free_l1_stays_held_while_the_replace_waits_and_returns_when_the_gate_closes():
    import test_research_v6_9 as t69
    a = _ladder(1, l1c=1)
    _deep(a, inv=0.0, tau=5.0)                                 # a carrying level 0 of 4 (tagged): L1 never placed
    _rest(a, 11, 0, 0, qty=4.0)
    _rest(a, 12, 1, 0)
    resp = _deep(a, inv=1.0, tau=4.5)                          # gap 3.5: re-placed; L1's slot is free and L1 stays held
    assert _cx(resp) == {11} and _pl(resp) == {(BUY, 2): 4.0, (SELL, 1): 2.0}                 # (L2: its own level)
    assert a._v6_42_carry == {} and a._v6_42_refold == {(3, BUY): True}
    _rest(a, 14, 0, 2)
    _rest(a, 15, 1, 1)
    buy = sum(float(r.quantity) for r in a.ledger.live_orders(3) if int(r.side) == 0)
    assert buy == sum(t69.SIZE.values())                       # the old level 0 in flight + L2: never above v6.41's ladder
    resp = _deep(a, inv=1.0, tau=4.5)                          # the cancel not seen yet: no new level 0, L1 still held
    assert _pl(resp) == {(SELL, 2): 4.0} and _cx(resp) == set() and a._v6_42_refold == {(3, BUY): True}
    _rest(a, 16, 1, 2)
    _gone(a, 11)
    resp = _deep(a, inv=1.0, tau=4.5)                          # the new carrying level 0 at the bound; its tag holds L1 now
    assert _pl(resp) == {(BUY, 0): 3.5} and a._v6_42_refold == {} and a._v6_42_carry == {(3, BUY): True}
    b = _ladder(1, l1c=1)                                      # the gate closes while the re-place waits: L1 returns at once
    _deep(b, inv=0.0, tau=5.0)
    _rest(b, 11, 0, 0, qty=4.0)
    _rest(b, 12, 1, 0)
    _deep(b, inv=1.0, tau=4.5)
    resp = _deep(b, inv=1.0, tau=-4.5)
    assert _pl(resp).get((BUY, 1)) == 2.0 and b._v6_42_refold == {} and b._v6_42_carry == {}


def test_an_error_in_the_plan_closes_the_gate_and_counts():
    a = _ladder(1)
    a._v6_42_carry[(3, BUY)] = True

    def boom(*x, **k):
        raise RuntimeError("boom")
    a._v69_live_slots = boom
    resp = _deep(a, inv=0.0, tau=5.0)
    assert a._v6_42_errors == 1 and all(q == 2.0 for (s, lv), q in _pl(resp).items() if lv == 0)


# ---- 4. the v6.3 pass: t623's harness, the real joined _v63_pass with v6.42's note --------------------------------------------

def _pass_agent(mode, *, inv=None, tau=None):
    import research_v625_lean as lean
    import test_research_v6_23 as t623
    mod = _part_module()
    a = t623._agent(levels="deep", adding=True, inv=inv)
    names = dict(t623.EXTRA, v625_lean_request=lean.new_request)
    a.__class__ = type("P642", (mod.V642Part, type(a)), {"_v63_pass": t623._with23(type(a)._v63_pass, names)})
    a._v6_42_init()
    a.research_v642_l1t = mode
    a._v6_40_table = None
    a._v6_36_record, a._v6_36_req = sw.StepRecord(), {"block": None}
    a.noted = []
    orig = a._v6_42_note
    a._v6_42_note = lambda b, ts, inv_, el: (a.noted.append((b, ts, inv_, el)), orig(b, ts, inv_, el))
    if tau is not None:
        a.research_v625_lean = True

        def lean_(self, b, rb, ra, cfg, inv_, now, req):
            self._v6_25_taus = {b: (tau, now)}
            return inv_ - tau, inv_ - tau
        a._v6_25_lean = types.MethodType(lean_, a)
        a._v6_25_report = types.MethodType(lambda self, *x, **k: None, a)
    return a


def test_off_the_pass_notes_nothing():
    import test_research_v6_23 as t623
    a = _pass_agent(0)
    t623._run(a)
    assert a.noted == [] and a._v6_42_books == {}


def test_the_pass_notes_the_true_position_and_tau_for_the_gate():
    import test_research_v6_23 as t623
    a = _pass_agent(1, inv=-3.0, tau=-8.0)                 # true short 3, lean -8: the layer reads inv - tau = +5
    t623._run(a)
    assert a.noted and a.noted[0][0] == 3 and a.noted[0][2] == -3.0 and a.noted[0][3] is True
    assert a._v6_42_books[3] == (-3.0, -8.0, True)          # the TRUE position and tau (the layer reads inv - tau = +5)
    b = _pass_agent(1, inv=-3.0, tau=-8.0)
    b._v6_23_observe = types.MethodType(lambda self, state: None, b)
    b._v6_23_active, b._v6_23_foreign = True, frozenset({3})
    t623._run(b)
    assert b.noted and b.noted[0][3] is False               # a foreign book: not class 0


# ---- 5. the hook edits, their exact undo, the launcher --------------------------------------------------------------------------

def test_the_joined_simple_undoes_to_v641s_byte_for_byte_and_the_ast_undo_matches():
    joined = join.joined_text(str(STRATEGY))
    assert _sha(joined) == _v642_undo.JOINED_SHA256
    manifest = json.loads((PARTS / "MANIFEST.json").read_text())
    assert manifest["pre_split_sha256"] == _v642_undo.JOINED_SHA256 and manifest["v642_base_sha256"] == V641_JOINED_SHA256
    back = _v642_undo.joined_v641(joined)
    assert _sha(back) == V641_JOINED_SHA256 == _v642_undo.BASE_JOINED_SHA256 == _v641_undo.JOINED_SHA256
    assert _v642_undo.joined_v641(back) is back                                         # idempotent
    assert ast.dump(_v642_undo.undo_tree(ast.parse(joined))) == ast.dump(ast.parse(back))
    assert len(_v642_undo.JOINED_EDITS) == 7 and all(joined.count(new) == 1 for _o, new in _v642_undo.JOINED_EDITS)
    assert _v641_undo.joined_v640(joined) == _v641_undo.joined_v640(back)              # the chain: v6.42 first


def test_the_hooks_sit_in_the_pass_and_the_deep_ladders_placement():
    tree = ast.parse(join.joined_text(str(STRATEGY)))
    fn = {n.name: ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
          and n.name in ("_v63_pass", "_v633_book")}
    p = fn["_v63_pass"]
    note = p.index("self._v6_42_note(book_id, now_ts, inv, v6_40_g is None and book_id not in v6_23_foreign)")
    assert p.index("= self._v6_25_lean(") < note < p.index("deep_open = False")      # 3.10's unparse brackets the targets
    assert p.count("self._v6_42_") == 1
    d = fn["_v633_book"]
    order = [d.index(x) for x in (
        "levels_side = {s_: v69_ladder_levels(depths, eff_side[s_])",
        "v6_42_fold = self._v6_42_fold(book_id, inv, vac, levels_side, clip, size_scale, min_order, cfg)",
        "doomed = [(row, V633_CANCEL_OWNS_BOOK)",
        "self._v6_42_doom(book_id, v6_42_fold, doomed, resting, deep_rows, already)",
        "cancelled: set = set()",
        "self._v6_42_cancelled(book_id, v6_42_fold, cancelled)",
        "if cap_skip:",
        "if v6_42_fold is not None and level == 1 and v6_42_fold.holds_l1(side):",
        "q = v6_42_fold.carried(side, q)",
        "if self._v691_free_base_on():",
        "self._v6_42_placed(response, book_id, side, v6_42_fold, resting, budget, req)",
    )]
    assert order == sorted(order)
    assert "if getattr(self, 'research_v642_l1t', 0) and ladder:" in d


def test_the_split_puts_v642part_first_and_the_part_overrides_only_what_it_names():
    assert RAW_SIMPLE.count("from simple_parts.v6_42_part import V642Part\n") == 1
    assert "class Strategy1_Research_Simple(V642Part, V641Part, V640Part, V636Part, " in RAW_SIMPLE
    manifest = json.loads((PARTS / "MANIFEST.json").read_text())
    assert manifest["simple_edits"][:2] == [list(e) for e in _v642_undo.EDITS["split_edits"]]
    tree = ast.parse(PART)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "V642Part")
    names = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
    assert all(n.startswith("_v6_42_") or n in {"_v6_41_init", "_v6_40_request"} for n in names)
    assert len(set(names)) == len(names)
    joined_defs = {n.name for n in ast.walk(ast.parse(join.joined_text(str(STRATEGY)))) if isinstance(n, ast.FunctionDef)}
    assert not set(names) & joined_defs                                   # Simple defines none of them
    assert "super()._v6_41_init()" in PART and "super()._v6_40_request(state)" in PART


def test_the_new_modules_bind_every_global_they_use():
    import test_module_globals_resolve as g
    for rel in ("research_v6_42_l1t.py", "simple_parts/v6_42_part.py"):
        assert g._unresolved(STRATEGY / rel) == set(), rel


def test_no_new_constant_the_rule_reads_the_ladder_the_pace_and_the_step_record():
    import research_v641_pace as pace
    import research_v69_deep_ladder as d69
    assert "from research_v641_pace import adds as _adds" in RULE and pace.adds(BUY, -1e-9) and not pace.adds(BUY, -2e-9)
    assert d69.LADDER_CLIPS[lt.FOLD_LEVEL - 1] == 1.0 and d69.level_quantity(2.0, lt.FOLD_LEVEL) == 2.0
    assert "research_v642_tmg" not in RULE + PART + LAUNCHER + RAW_SIMPLE


def test_the_launcher_ships_research_v642_l1t_once_and_its_undo_is_v641s_launcher():
    vals = _v642_undo.params_of(LAUNCHER)
    start = LAUNCHER.index('PARAMS="')
    body = LAUNCHER[start + 8:LAUNCHER.index('"\n', start + 8)].replace("\\\n", " ").split()
    for key in _v642_undo.SWITCHES:
        assert [t for t in body if t.startswith(key + "=")] == [key + "=" + vals[key]] and vals[key] == _v642_undo.SHIPPED[key]
    assert [t.split("=")[0] for t in body[-2:]] == list(_v642_undo.SWITCHES)
    assert _sha(_v642_undo.launcher_v641(LAUNCHER)) == _v642_undo.LAUNCHER_BASE_SHA256
    assert _v642_undo.launcher_v641(_v642_undo.launcher_v641(LAUNCHER)) == _v642_undo.launcher_v641(LAUNCHER)
    assert "# v6.42." in LAUNCHER and "      tests/test_research_v6_41.py \\\n      tests/test_research_v6_42.py \\\n" in LAUNCHER
    assert LAUNCHER.index('echo "[preflight] v6.41 PASS') < LAUNCHER.index("# v6.42.") < \
        LAUNCHER.index('echo "[preflight] v6.42 PASS') < LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')


def test_each_documented_flip_is_one_params_edit_and_its_undo_is_v641s_launcher():
    base = _v642_undo._unflip(LAUNCHER)
    sh = (_v642_undo.SHIPPED["research_v642_l1t"], _v642_undo.SHIPPED["research_v642_l1c"])
    shipped = [n for n, pair in _v642_undo._PAIRS.items() if pair == sh]
    assert _v642_undo.flip_of(base) == "" and set(_v642_undo.FLIPS) == set(_v642_undo._PAIRS) - set(shipped)
    assert set(_v642_undo._PAIRS) == {"OFF", "L1T", "L1TS", "L1TI", "L1TC", "L1TSC", "L1TSX", "L1TSXA"}
    old = "\nresearch_v642_l1t=%s \\\nresearch_v642_l1c=%s\"\n" % sh
    assert base.count(old) == 1
    for name, flip in _v642_undo.FLIPS.items():
        flipped = base.replace(old, "\nresearch_v642_l1t=%s \\\nresearch_v642_l1c=%s\"\n" % (
            flip["research_v642_l1t"], flip["research_v642_l1c"]))
        assert _v642_undo.flip_of(flipped) == name and _v642_undo._unflip(flipped) == base
        assert _sha(_v642_undo.launcher_v641(flipped)) == _v642_undo.LAUNCHER_BASE_SHA256
    for t, c in (("3", "1"), ("1", "7"), ("7", "0")):
        assert _v642_undo.flip_of(base.replace(old, "\nresearch_v642_l1t=%s \\\nresearch_v642_l1c=%s\"\n" % (t, c))) is None
    s9 = base.replace("\nresearch_v641_s9_page=1 \\", "\nresearch_v641_s9_page=0 \\")      # a v6.41 flip beside it
    assert s9 != base and _v642_undo.launcher_v641(s9) == _v642_undo.launcher_v641(base).replace(
        "\nresearch_v641_s9_page=1\"", "\nresearch_v641_s9_page=0\"")


def test_the_v642_block_checks_the_files_the_hooks_the_value_the_ladder_and_the_step_record():
    block = LAUNCHER[LAUNCHER.index("# v6.42."):LAUNCHER.index('echo "[preflight] v6.42 PASS')]
    for pat in ("research_v6_42_l1t.py simple_parts/v6_42_part.py",
                "'self._v6_42_note(book_id, now_ts, inv, v6_40_g is None and book_id not in v6_23_foreign)'",
                "'q = v6_42_fold.carried(side, q)'",
                "'class Strategy1_Research_Simple(V642Part, V641Part, '",
                "[[ \"$_v642_l1t\" =~ ^(0|1|2|3)$ ]]", "grep '^research_v69_deep_ladder='",
                "grep '^research_v636_step_withdrawal='", "[[ \"$_v642_l1c\" =~ ^(0|1|3|4)$ ]]",
                '[[ "$_v642_l1c" == "4" && "$_v642_l1t" != "2" ]]',
                '[[ "$_v642_l1c" != "0" && "$_v642_l1t" != "1" && "$_v642_l1t" != "2" ]]',
                "'def carried_size(mode: Any, q0: Any, q1: Any, gap: Any, toward: Any, against: Any = False) -> float:'",
                'echo "[preflight] v6.42: l1t ${_v642_l1t} l1c ${_v642_l1c} (${_v642_name} / ${_v642_cname})"'):
        assert pat in block, pat
    for e in _v642_undo.JOINED_EDITS:
        assert join.joined_text(str(STRATEGY)).count(e[1]) == 1
    assert "grep -qE -- '^class Strategy1_Research_Simple\\((V642Part, )?(V641Part, )?V640Part, V636Part, '" in LAUNCHER
    assert "grep -qE -- '^class Strategy1_Research_Simple\\((V642Part, )?V641Part, V640Part, '" in LAUNCHER
