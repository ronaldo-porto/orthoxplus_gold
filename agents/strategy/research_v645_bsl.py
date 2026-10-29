# SPDX-License-Identifier: MIT
"""v6.45: the bridge-aware lean (BSL), its observed-span warm-up (W3 / LBW), the class-2 bridge centre (C2BC) and the
class-2 surplus-side hold (C2SUR) -- research_v645_bsl / research_v645_lbw / research_v645_c2bc / research_v645_c2sur,
code default 0 (off: v6.44 on every path).  The arithmetic is the replay engine's, function for function (scratchpad
round4/vm_r4d.py: _k162_push, _k162_pt, _k162_tau, _r4a_obs_mean, _r2_gpush, _r2_gmean, _k105_sur; Round 4 follow-up
D_F3.5, SP/round4/f35/RESULT.md: DEV CARRIED vs CAND S +48.4 % / U +36.8 %).

Why (memory round2-result-1029 / round3 / round4; k162 vm762 = the BSL form):

* The validator prices a maker's inventory at its scoring times over the 10,800-s lookback (whole span = scoring-time
  pricing).  v6.25's lean leans toward the fresh fundamental by the gap g = log(fp / mid) in bps; the part of g the
  lookback mean already holds is not convergence still to come.  BSL sizes the same lean from the NET expected
  convergence g - Pt, Pt = 1e4 (m - W) / m, W = the triangular-weighted (1 - v / H) mean of the book's own 1-s mids over
  the last H = 10,800 s (v = 0 the newest second).  research_v645_bsl = 2: signed (the engine's bsl=2); 1: never against
  the fp (bsl=1).
* LBW (research_v645_lbw, bit 1 classes 0 / 1, bit 2 class 2; the engine's lbw=3 = both): the engine pre-filled every ring
  with its first value, so a young ring's W leaned on a value nobody traded at.  With LBW the mean runs over the OBSERVED
  span only, L = min(H, last second - first second + 1), weights 1 - v / L -- identical to the plain mean once L = H.
* C2BC (research_v645_c2bc, needs C2T and bsl = 2): class 2's fp sits a persistent +280..+400 bp off its mid (k167), so
  the class-2 C2T layer, which quotes on its TRUE inventory, gets a centre from the gap's DEVIATION: tau2 from
  gd = (g - gbar) - Pt, gbar = the same triangular lookback mean of the book's own fresh-fp gap g (a second ring, pushed
  on fresh-fp states only), and C2T reads inventory - tau2 (its adding sides, its bound and its reducing size).
* C2SUR (research_v645_c2sur, needs C2T): a class-2 C2T quote that would add inventory is withheld on the side whose
  capture already leads in the making window (the agent's own making mirror: buy capture > sell capture -> the buy side).

The tau form is v6.25's (research_v625_lean.lean_target): sign(x) x min(cap, slope x |x| / gap_bps), 0 inside the gap,
with x = g - Pt (BSL) or gd (C2BC); with the shipped slope 1.0, cap 48 and gap 2.3 bps it is the engine's ltc=1, ltk=48,
ltg=2.3 bit for bit.

The ring (LookbackRing): H slots of 1-s values, newest at ``pos``; a push at second s fills every second since the last
one with the previous value and puts the new one in the newest slot (the same second: overwritten); S0 = sum of the
slots, S1 = sum v x_v; after every H pushes S0 / S1 are re-summed exactly.  A ring starts pre-filled with its first value
(the engine's), and keeps (first second, first value) for LBW.  The state of every ring rides in the session (the v6.21
pace state, SESSION_KEY) so a restart of the same UID and simulation continues it; a new simulation starts every ring
over (the engine's chain restarts its process state at a seam).

STRUCTURAL throughout: the validator's lookback and the engine's arithmetic; the sizes are v6.25's live parameters.  No
constant here is sized from a sample."""
from __future__ import annotations

import base64
import math
import zlib
from typing import Any

import numpy as np

from research_v63_trend_target import SIDE_BUY, SIDE_SELL   # C2T's plan keys (the engine's 'bid' / 'ask')

V645_BSL_VERSION = "bsl_v6_45"
LOOKBACK_S = 10_800              # STRUCTURAL: the validator's kappa lookback in seconds (the engine's BSL_H)
REWIND_S = 3_600                 # a book's clock that runs back more than this is a new simulation (REBASE_MIN_JUMP_NS)
SESSION_KEY = "v645_bsl"         # the rings' row in the v6.21 pace state (top level)
SESSION_VERSION = 1
_EPS = 1e-9                      # the engine's EPS (_k105_sur)

__all__ = ["LOOKBACK_S", "LeanRings", "LookbackRing", "REWIND_S", "SESSION_KEY", "SESSION_VERSION", "V645_BSL_VERSION",
           "bsl_tau", "c2bc_tau", "lbw_on", "parse_level", "pt_bps", "surplus_side", "tau_of"]


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def parse_level(value: Any, allowed) -> int | None:
    """An integer switch among ``allowed`` (an int, an integral float -- the launcher's PARAMS reach the config as 1.0 --
    or its text); None when it is anything else (unreadable: the agent runs it off and counts)."""
    if value is None:
        return 0
    if isinstance(value, bool):
        v = int(value)
    else:
        s = str(value).strip()
        if s == "":
            return 0
        try:
            f = float(s)
        except ValueError:
            return None
        if not math.isfinite(f) or f != int(f):
            return None
        v = int(f)
    return v if v in set(allowed) else None


# ---- the ring ----------------------------------------------------------------------------------------------------

class LookbackRing:
    """One book's ring of 1-s values over the lookback (the engine's BSL_BUF / BSL_ST row, or C2G_BUF / C2G_ST)."""

    __slots__ = ("k", "buf", "pos", "last", "s0", "s1", "n", "first")

    def __init__(self, k: int = LOOKBACK_S):
        self.k = int(k)
        self.buf = None              # np.float64[k] once started
        self.pos = 0
        self.last = 0                # the newest second
        self.s0 = 0.0
        self.s1 = 0.0
        self.n = 0                   # pushes since the last exact re-sum
        self.first = None            # (first second, first value): LBW's observed span

    @property
    def started(self) -> bool:
        return self.buf is not None

    def push(self, ts_ns: int, value: float) -> None:
        """_k162_push / _r2_gpush: advance to second ts_ns // 1e9 (gap seconds keep the previous value), the newest = value."""
        s_ = int(int(ts_ns) // 10**9)
        K_ = self.k
        m_ = value
        if self.buf is None:
            self.buf = np.full(K_, m_, dtype=np.float64)
            self.pos, self.last, self.s0, self.s1, self.n = 0, s_, K_ * m_, m_ * K_ * (K_ - 1) / 2.0, 0
            self.first = (s_, m_)
            return
        buf_ = self.buf
        pos_, last_, S0_, S1_, n_ = self.pos, self.last, self.s0, self.s1, self.n
        if s_ <= last_:
            S0_ += m_ - buf_[pos_]
            buf_[pos_] = m_
            self.s0 = S0_
            return
        gap_ = min(s_ - last_, K_)
        prev_ = buf_[pos_]
        for i_ in range(gap_):
            x_ = prev_ if i_ < gap_ - 1 else m_
            old_ = buf_[(pos_ + 1) % K_]
            S1_ = S1_ + S0_ - K_ * old_
            S0_ = S0_ + x_ - old_
            pos_ = (pos_ + 1) % K_
            buf_[pos_] = x_
            n_ += 1
        if n_ >= K_:                                     # exact re-sum (bounds float drift)
            v_ = (pos_ - np.arange(K_)) % K_
            S0_ = float(buf_.sum())
            S1_ = float((np.arange(K_) * buf_[v_]).sum())
            n_ = 0
        self.pos, self.last, self.s0, self.s1, self.n = pos_, s_, S0_, S1_, n_

    def newest(self) -> float:
        return self.buf[self.pos]

    def full_mean(self) -> float:
        """The triangular (1 - v / H) mean over the whole ring (pre-fill included)."""
        K_ = self.k
        return (self.s0 - self.s1 / K_) / ((K_ + 1) / 2.0)

    def observed_mean(self):
        """_r4a_obs_mean: the triangular (1 - v / L) mean over the observed span L = min(H, last - first + 1); None once
        L = H (the full mean applies unchanged).  The slots v >= L still hold the first value (the pre-fill), removed
        exactly."""
        if self.first is None:
            return None
        K_ = self.k
        S0_, S1_, last_ = self.s0, self.s1, self.last
        s0_, x0_ = self.first
        L_ = min(K_, last_ - s0_ + 1)
        if L_ >= K_:
            return None
        T0_ = S0_ - (K_ - L_) * x0_
        T1_ = S1_ - x0_ * (K_ * (K_ - 1) / 2.0 - L_ * (L_ - 1) / 2.0)
        return (T0_ - T1_ / L_) / ((L_ + 1) / 2.0)

    def mean(self, observed: bool) -> float:
        """The lookback mean the engine reads: the observed-span mean while young under LBW, else the full mean."""
        if observed:
            w4 = self.observed_mean()
            if w4 is not None:
                return w4
        return self.full_mean()

    # -- the session -----------------------------------------------------------------------------------------------
    def to_state(self) -> dict[str, Any] | None:
        if self.buf is None:
            return None
        raw = np.ascontiguousarray(self.buf, dtype="<f8").tobytes()
        return {"k": int(self.k), "buf": base64.b64encode(zlib.compress(raw, 6)).decode("ascii"), "pos": int(self.pos),
                "last": int(self.last), "s0": float(self.s0), "s1": float(self.s1), "n": int(self.n),
                "first": [int(self.first[0]), float(self.first[1])] if self.first is not None else None}

    @classmethod
    def from_state(cls, row: Any, k: int = LOOKBACK_S):
        """A ring back from its saved row, or None when the row is not one this build reads (wrong length, values)."""
        if not isinstance(row, dict) or int(row.get("k", -1)) != int(k):
            return None
        buf = np.frombuffer(zlib.decompress(base64.b64decode(str(row["buf"]))), dtype="<f8").astype(np.float64)
        if buf.shape != (int(k),) or not np.all(np.isfinite(buf)):
            return None
        r = cls(k)
        r.buf = buf.copy()
        r.pos, r.last, r.n = int(row["pos"]), int(row["last"]), int(row["n"])
        r.s0, r.s1 = float(row["s0"]), float(row["s1"])
        first = row.get("first")
        r.first = (int(first[0]), float(first[1])) if isinstance(first, (list, tuple)) and len(first) == 2 else None
        if not 0 <= r.pos < int(k) or not 0 <= r.n < int(k) or not math.isfinite(r.s0) or not math.isfinite(r.s1):
            return None
        return r


# ---- the rules ---------------------------------------------------------------------------------------------------

def lbw_on(lbw: Any, class2: bool) -> bool:
    """_r4a_on: LBW bit 2 for a class-2 book's rings, bit 1 for every other book's."""
    try:
        return bool(int(lbw) & (2 if class2 else 1))
    except (TypeError, ValueError):
        return False


def pt_bps(ring: LookbackRing, observed: bool) -> float:
    """_k162_pt: Pt = 1e4 (m - W) / m, m the ring's newest value, W its lookback mean."""
    m_ = ring.newest()
    W_ = ring.mean(observed)
    return 1e4 * (m_ - W_) / m_


def tau_of(x: Any, gap_bps: Any, slope: Any, cap: Any) -> float:
    """v6.25's lean form on a gap ``x`` (bps): sign(x) min(cap, slope |x| / gap_bps), 0 inside the gap or without a usable
    parameter."""
    v, g, k, c = _finite(x), _finite(gap_bps), _finite(slope), _finite(cap)
    if v is None or g is None or k is None or c is None or g <= 0.0 or k <= 0.0 or c <= 0.0:
        return 0.0
    if abs(v) < g:
        return 0.0
    return math.copysign(min(c, k * abs(v) / g), v)


def bsl_tau(g: Any, pt: Any, mode: int, gap_bps: Any, slope: Any, cap: Any) -> float:
    """_k162_tau's centre: mode 2 (signed) the lean of g - Pt; mode 1 (never against the fp) the lean of the net
    convergence sign(g) (g - Pt) toward the fp, only while |g| reaches the gap and the net does too; 0 otherwise."""
    gv, pv = _finite(g), _finite(pt)
    if gv is None or pv is None:
        return 0.0
    if int(mode) == 1:
        gg = _finite(gap_bps)
        if gg is None or abs(gv) < gg:
            return 0.0
        n_ = math.copysign(1.0, gv) * (gv - pv)          # the net convergence toward the fp
        if n_ < gg:
            return 0.0
        return math.copysign(tau_of(n_, gap_bps, slope, cap), gv)
    if int(mode) == 2:
        return tau_of(gv - pv, gap_bps, slope, cap)
    return 0.0


def c2bc_tau(g: Any, gbar: Any, pt: Any, gap_bps: Any, slope: Any, cap: Any) -> float:
    """Round 2 C2BC's class-2 centre: the lean of gd = (g - gbar) - Pt."""
    gv, bv, pv = _finite(g), _finite(gbar), _finite(pt)
    if gv is None or bv is None or pv is None:
        return 0.0
    return tau_of((gv - bv) - pv, gap_bps, slope, cap)


def surplus_side(buy: Any, sell: Any) -> str | None:
    """_k105_sur: the side whose capture leads over the making window -- SIDE_BUY when buy > sell + EPS, SIDE_SELL when
    sell > buy + EPS, None when balanced or unreadable."""
    b, s = _finite(buy), _finite(sell)
    if b is None or s is None:
        return None
    return SIDE_BUY if b > s + _EPS else (SIDE_SELL if s > b + _EPS else None)


# ---- the agent's rings -------------------------------------------------------------------------------------------

class LeanRings:
    """Every book's mid ring (BSL's Pt) and every class-2 book's gap ring (C2BC's gbar), for one simulation."""

    def __init__(self, k: int = LOOKBACK_S):
        self.k = int(k)
        self.mid: dict[int, LookbackRing] = {}
        self.gap: dict[int, LookbackRing] = {}
        self.sim: str | None = None
        self.restored: str | None = None
        self.resets = 0
        self.rewinds = 0

    def clear(self) -> None:
        self.mid, self.gap = {}, {}

    def begin(self, sim_id: Any) -> bool:
        """The request's simulation: a different one than the rings' starts every ring over (True)."""
        sim = None if sim_id is None else str(sim_id)
        if sim is None:
            return False
        if self.sim is not None and sim != self.sim:
            self.clear()
            self.resets += 1
            self.sim = sim
            return True
        self.sim = sim
        return False

    def _ring(self, table: dict, book_id: int, ts_ns: int) -> LookbackRing:
        b = int(book_id)
        r = table.get(b)
        if r is not None and r.started and int(int(ts_ns) // 10**9) < r.last - REWIND_S:
            r = None                                       # the clock ran back: a new simulation for this book
            self.rewinds += 1
        if r is None:
            r = LookbackRing(self.k)
            table[b] = r
        return r

    def push_mid(self, book_id: int, ts_ns: int, mid: float) -> LookbackRing:
        r = self._ring(self.mid, book_id, ts_ns)
        r.push(ts_ns, mid)
        return r

    def push_gap(self, book_id: int, ts_ns: int, g: float) -> LookbackRing:
        r = self._ring(self.gap, book_id, ts_ns)
        r.push(ts_ns, g)
        return r

    def state(self) -> dict[str, Any]:
        out = {"version": SESSION_VERSION, "rule": V645_BSL_VERSION, "k": self.k, "sim": self.sim, "mid": {}, "gap": {}}
        for name in ("mid", "gap"):
            for b, r in sorted(getattr(self, name).items()):
                row = r.to_state()
                if row is not None:
                    out[name][str(int(b))] = row
        return out

    def restore(self, row: Any, sim_id: Any) -> str:
        """Saved rings back into an empty store of the same simulation: 'restored', 'other_sim', 'invalid', 'live' (the
        store already holds rings) or 'none'."""
        if not isinstance(row, dict):
            self.restored = "none"
            return "none"
        if self.mid or self.gap:
            self.restored = "live"
            return "live"
        if int(row.get("version", 0) or 0) != SESSION_VERSION or int(row.get("k", -1)) != self.k:
            self.restored = "invalid"
            return "invalid"
        if sim_id is None or row.get("sim") is None or str(row.get("sim")) != str(sim_id):
            self.restored = "other_sim"
            return "other_sim"
        mid, gap = {}, {}
        for name, table in (("mid", mid), ("gap", gap)):
            rows = row.get(name)
            if not isinstance(rows, dict):
                self.restored = "invalid"
                return "invalid"
            for key, saved in rows.items():
                r = LookbackRing.from_state(saved, self.k)
                if r is None:
                    self.restored = "invalid"
                    return "invalid"
                table[int(key)] = r
        self.mid, self.gap, self.sim = mid, gap, str(sim_id)
        self.restored = "restored"
        return "restored"
