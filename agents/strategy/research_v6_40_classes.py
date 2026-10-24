# SPDX-License-Identifier: MIT
"""v6.40: the three-class layout of SN79 0.6.5 -- research_v640_* (scratchpad k139; arms from k137's replay engine vm739).

Why (10-24 JST; memory sn79-065-three-classes-1023, scratchpad k137 / k138):

* 0.6.5 (upstream c36caa9) makes the default layout THREE asset classes, one per background in document order: books
  0-63 ``simulation_0`` (300, 2 price / 4 volume decimals, minimum 0.25), 64-95 ``simulation_1`` (29.57, minimum
  2.5364), 96-127 ``simulation_2`` (313.0114, 4 price decimals, minimum 0.2396), paid at the class weights 0.50 / 0.25 /
  0.25 with ONE markout quality q per uid over all books.  v6.23 S2 holds every book of another class (foreign), so the
  agent trades class 0 alone.
* This module is the agent side of k137's class arms, every one shipped OFF (the build trades as v6.39): the class map
  and each class's own grid from the state configuration (never from book ranges), the traded-classes switch, the
  depth unit, the arms' rules (C2D, C2J, C2Q, C1W; C2P = the class-0 machinery on class 2), and four safety fixes
  from k138's risk table that are ON (they change nothing on a readable two- or three-class state of today).

The class map (``class_table``): the configuration's ``classes`` list, class c = its c-th entry (taos 0.6.5
config._classes_from_backgrounds; reward.class_map: document order), each with its books, price decimals (the entry's,
else its configuration's), volume decimals (likewise), minimum order and initial price (its configuration's).  The
home class is class 0 = the top-level configuration's (the representative background).  The DEPTH UNIT of class c
(k137 vm738, STRUCTURAL): DU_c = max(1, round(tick_0 x p0_c / p0_0 / tick_c)) x tick_c -- the class's venue tick
multiple nearest to the home tick at the class's price, so a depth parameter tuned on class 0 keeps its RELATIVE
depth (0.6.5: 0.01 / 0.01 / 0.0104).  Without both initial prices: DU_c = tick_c.

The switches (agent defaults = run_strategy1_research_simple_multi.sh PARAMS; engine switch -> key):
* ``research_v640_traded_classes`` (ctr; default "0"): the classes the agent trades besides the ones v6.23 never held
  ("0", "0+2", "0+1+2"; class 0 always).  A traded class's books leave the foreign set and carry their class's grid
  (venue tick, price / volume decimals, minimum) through the quoting pass, the final validator, the wire-price lift and
  v6.36's drop.  Default: home only -- every book's grid is the top level's, the foreign set is v6.23's (+ B4).
* ``research_v640_c2du`` (c2du; 0): on a traded non-home book the deep layer's depth rules read the class's depth unit
  (targets, keep bands, the sweep and paper records, the vacuum spread, the v6.30 tier step, the v6.6 spacing); every
  touch / join / improve rule and every "behind the touch by a tick" margin keep the venue tick.
* ``research_v640_c2d`` (c2d; 0) C2D: class-2 books keep no touch quote (a resting one is cancelled, none placed); the
  deep layer is unchanged.
* ``research_v640_c2j`` (c2j; 0 / 1 / 2) C2J: the class-2 touch quote never improves -- 1 joins the best, 2 rests one
  depth unit behind it -- and its rest band is read in depth units (1: re-placed once the touch is more than
  REST_TICKS - 0.5 DU better; 2: REST_TICKS + 0.5, the one-behind slack).
* ``research_v640_c2q`` (c2q; 0) + ``research_v640_c2q_pf`` (c2qpf) C2Q: the pool factor PF = (w_2 / w_0)(MK_0 / MK_2) is
  read LIVE from the validator's report page (class_gauges weight and making_credit; a host-shared cache refreshed every
  5 min; the last page of this simulation at or before the state -- vm739's rule for its c2qpf series), the
  configured number only as the fallback when the page is absent / stale / without both pools; then class-2 legs only while the break-even rule holds
  on the agent's OWN mirror, q_2 / q > (1 - g_2) / (1 + g_2 x_2), g_2 = PF x beta_2 / beta_0, x_2 = C_2 / C_0 (S14's
  validator-exact realized / captured at the 20-s horizon, both net of W x the maker fee, over the making window;
  beta_c = the making mirror's balanced share of the class's captured notional), decided once per 30-s block (no
  class-2 evidence: armed, a probe).  Disarmed: every order of ours on a class-2 book is cancelled, none placed.
* ``research_v640_c1w`` (c1w; 0) C1W, with research_v634_class1_entry=1 (the engine's c2 / c1p / ftp=join / d4c1 / l3=1
  class-1 path is v6.34's entry): every order of ours on a class-1 book is withdrawn at the pre-step state (phase
  K - 1) -- resting ones cancelled, new ones dropped -- so a class-1 order never rests through a step; v6.34's S9 and
  S17-R holds are not applied (L3's join on both sides; the engine's mth = 0).
* B4 (k138's risk table, each ON): ``research_v640_guard_hold`` (2) the class guard fails CLOSED -- an unreadable class
  list or a raising observe keeps the last readable foreign set (v6.23 fell open: nothing foreign);
  ``research_v640_guard_init_price`` (3) the foreign test also compares the class's initial price;
  ``research_v640_s14_class_key`` (5) S14's minimum-size cache is keyed on the simulation id AND a digest of the class
  list (a mid-run layout change re-reads it); ``research_v640_s14_flush_spec`` (6) S14's capture flush follows the
  validator's release: 60 sim-s below 0.6.5, 300 sim-s from it (debeta CAPTURE_FLUSH_NS).

STRUCTURAL throughout: the validator's class map, grids, minimums, initial prices, class ids and release number; k137's
depth-unit rule and arm definitions.  No constant here is sized from an observed sample.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import threading
from collections import deque, namedtuple
from typing import Any, Iterable

from research_v616_fair_value import SharedFeed
from research_v6_38_keeper import CaptureTracker

V6_40_CLASSES_VERSION = "classes_v6_40"
HOME_CLASS = 0                   # the top-level configuration's class: the first background (taos representative)
CLASS1 = 1                       # the validator's class id of the second background (simulation_1 on 0.6.5)
CLASS2 = 2                       # the validator's class id of the third background (simulation_2 on 0.6.5)
FLUSH_SPEC = 65                  # taos 0.6.5's __spec_version__: debeta's CAPTURE_FLUSH_NS 60 -> 300 sim-s
FLUSH_NS_BEFORE = 60_000_000_000     # 0.6.4 debeta.CAPTURE_FLUSH_NS
FLUSH_NS_FROM = 300_000_000_000      # 0.6.5 debeta.CAPTURE_FLUSH_NS
BLOCK_NS = 30_000_000_000        # C2Q: one decision per 30-s block (the fp step period of the layout; vm739)
DEFAULT_TRADED = "0"

STATUS_SINGLE = "single"         # no classes: one market (or a validator before 0.6.3)
STATUS_OK = "ok"                 # classes read
STATUS_UNREADABLE = "unreadable"  # classes present but not readable

CANCEL_C2D = "V640_C2D_NO_TOUCH"     # C2D: a class-2 touch quote (the deep layer only)
CANCEL_C2Q = "V640_C2Q_DISARMED"     # C2Q: a class-2 order while the break-even rule does not hold

Grid = namedtuple("Grid", "c name tick dec vol_dec min_order du unit scale")   # du: the deep layer's unit; unit: the
# class DU; scale: the class's size scale (sizes in class-0 units x scale)

_EPS = 1e-9


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def _whole(value: Any) -> int | None:
    v = _finite(value)
    if v is None or v != math.floor(v):
        return None
    return int(v)


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _close(a: float | None, b: float | None) -> bool:
    return a is None or b is None or math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)


# ---- switches ----------------------------------------------------------------------------------------------------------

def parse_traded(value: Any) -> frozenset | None:
    """research_v640_traded_classes: "0", "0,1", "0,2", "0,1,2" ("+" works as ",") -> the class ids (class 0 always among them); None when the
    value cannot be read (the agent then trades the home class only and the preflight refuses the build)."""
    if value is None:
        return frozenset({HOME_CLASS})
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        w = _whole(value)                        # the launcher's parser reads a lone "0" as a number
        if w is None:
            return None
        value = str(w)
    s = str(value).strip()
    if not s:
        return None
    out = set()
    for part in s.replace(",", "+").split("+"):
        p = part.strip()
        if not p.isdigit():
            return None
        out.add(int(p))
    if HOME_CLASS not in out:
        return None
    return frozenset(out)


def parse_level(value: Any, allowed: Iterable[int]) -> int:
    """An integer switch value among ``allowed``; anything else is 0 (off)."""
    v = _whole(value)
    if v is None and isinstance(value, str):
        v = _whole(value.strip()) if value.strip() else None
    return int(v) if v is not None and int(v) in set(allowed) else 0


def parse_pf(value: Any) -> float | None:
    """research_v640_c2q_pf: a finite pool factor > 0, else None (C2Q cannot decide: the preflight refuses c2q=1)."""
    v = _finite(value)
    return v if v is not None and v > 0.0 else None


# ---- the class map -----------------------------------------------------------------------------------------------------

def _class_row(index: int, entry: Any) -> dict[str, Any]:
    books = _get(entry, "books")
    if books is None or isinstance(books, (str, bytes, dict)):
        raise TypeError("a class without books")
    ids = frozenset(int(b) for b in books)
    cfg = _get(entry, "config")
    pd = _whole(_get(entry, "priceDecimals"))
    if pd is None:
        pd = _whole(_get(cfg, "priceDecimals"))
    vd = _whole(_get(entry, "volumeDecimals"))
    if vd is None:
        vd = _whole(_get(cfg, "volumeDecimals"))
    return {"c": int(index), "name": str(_get(entry, "name") or ""), "books": ids, "price_decimals": pd,
            "volume_decimals": vd, "tick": (round(10.0 ** -pd, pd) if pd is not None and pd >= 0 else None),
            "min_order": _finite(_get(cfg, "min_order_size")), "init_price": _finite(_get(cfg, "init_price"))}


def depth_unit(tick0: Any, p0_home: Any, tick_c: Any, p0_c: Any, decimals_c: Any) -> float | None:
    """k137's depth unit of a class: max(1, round(tick_0 x p0_c / p0_0 / tick_c)) x tick_c on the class's decimals;
    tick_c when an initial price is missing; None without the class's own tick."""
    t0, p0, tc, pc, dc = _finite(tick0), _finite(p0_home), _finite(tick_c), _finite(p0_c), _whole(decimals_c)
    if tc is None or tc <= 0.0 or dc is None or dc < 0:
        return None
    if t0 is None or p0 is None or pc is None or t0 <= 0.0 or p0 <= 0.0 or pc <= 0.0:
        return tc
    n = max(1, int(round(t0 * pc / p0 / tc)))
    return round(n * tc, dc)


def class_table(config: Any) -> dict[str, Any]:
    """{status, classes: [row], book_class: {book: c}} of a state's configuration (see the module docstring); each row
    carries ``du`` (the depth unit).  No classes: single; a list that cannot be read: unreadable.  Never raises."""
    out: dict[str, Any] = {"status": STATUS_SINGLE, "classes": [], "book_class": {}}
    classes = _get(config, "classes")
    if not classes:
        return out
    try:
        if isinstance(classes, (str, bytes, dict)):
            raise TypeError("classes is not a list")
        rows = [_class_row(i, e) for i, e in enumerate(list(classes))]
        book_class: dict[int, int] = {}
        for r in rows:
            for b in r["books"]:
                if b in book_class:
                    raise ValueError("two classes share a book")
                book_class[b] = r["c"]
        home = rows[0]
        for r in rows:
            r["du"] = depth_unit(home["tick"], home["init_price"], r["tick"], r["init_price"], r["price_decimals"])
    except Exception:
        out["status"] = STATUS_UNREADABLE
        return out
    out.update(status=STATUS_OK, classes=rows, book_class=book_class)
    return out


def layout_key(config: Any) -> tuple | None:
    """A cheap identity of the configuration's class list (names, first / last book, count, grids, minimum, initial
    price per class) for a per-request cache; None without a readable list.  Never raises."""
    classes = _get(config, "classes")
    if not classes or isinstance(classes, (str, bytes, dict)):
        return None
    try:
        key = []
        for e in list(classes):
            books = list(_get(e, "books") or ())
            cfg = _get(e, "config")
            key.append((str(_get(e, "name")), len(books), int(books[0]) if books else None,
                        int(books[-1]) if books else None, _get(e, "priceDecimals"), _get(e, "volumeDecimals"),
                        _get(cfg, "min_order_size"), _get(cfg, "init_price"), _get(cfg, "priceDecimals"),
                        _get(cfg, "volumeDecimals")))
        return tuple(key)
    except Exception:
        return None


def class_digest(config: Any) -> str:
    """B4(5): sha256 (16 hex) of the class list's layout -- per class its name, books, decimals, minimum and initial price,
    in document order; "" without classes or a readable list.  Never raises."""
    t = class_table(config)
    if t["status"] != STATUS_OK:
        return "" if t["status"] == STATUS_SINGLE else "unreadable"
    text = "|".join("%s:%s:%s:%s:%r:%r" % (r["name"], ",".join(str(b) for b in sorted(r["books"])), r["price_decimals"],
                                           r["volume_decimals"], r["min_order"], r["init_price"]) for r in t["classes"])
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def init_price_foreign(config: Any, table: dict[str, Any] | None = None) -> frozenset:
    """B4(3): the books of each class whose initial price (its configuration's) differs from the top level's -- a value
    either side does not carry is no difference.  Empty without a readable class list.  Never raises."""
    try:
        t = table if table is not None else class_table(config)
        if t["status"] != STATUS_OK:
            return frozenset()
        top = _finite(_get(config, "init_price"))
        out: set[int] = set()
        for r in t["classes"]:
            if not _close(r["init_price"], top):
                out.update(r["books"])
        return frozenset(out)
    except Exception:
        return frozenset()


def traded_books(table: dict[str, Any], traded: Iterable[int] | None) -> frozenset:
    """The books of the traded classes other than the home class (empty: home only, or no readable class list)."""
    if not traded or table.get("status") != STATUS_OK:
        return frozenset()
    want = set(int(c) for c in traded) - {HOME_CLASS}
    out: set[int] = set()
    for r in table["classes"]:
        if r["c"] in want:
            out.update(r["books"])
    return frozenset(out)


def size_scale(min_c: Any, min_0: Any) -> float:
    """k137's class size rule (STRUCTURAL, mkspec139): every base-unit size x min_c / min_0 where the class-0 sizes would
    fall below the class's minimum (class 1 on 0.6.5: x 10.1456 -- the minimums were set at equal quote notional), else
    the class-0 sizes (class 2: 0.25 >= 0.2396, x 1).  Unreadable: 1."""
    a, b = _finite(min_c), _finite(min_0)
    if a is None or b is None or a <= 0.0 or b <= 0.0 or a <= b * (1.0 + 1e-12):
        return 1.0
    return a / b


def book_grids(table: dict[str, Any], traded: Iterable[int] | None, c2du: bool) -> dict[int, Grid]:
    """{book: Grid} for every book of a traded non-home class whose units are readable (tick, decimals, minimum > 0);
    du = the class's depth unit with c2du, else its venue tick (the deep layer's unit); unit = the class's depth unit
    (C2J's band and its one-behind step read it whatever c2du).  Empty by default (home only)."""
    if not traded or table.get("status") != STATUS_OK:
        return {}
    want = set(int(c) for c in traded) - {HOME_CLASS}
    out: dict[int, Grid] = {}
    home = table["classes"][0] if table["classes"] else {}
    for r in table["classes"]:
        if r["c"] not in want:
            continue
        if r["tick"] is None or r["volume_decimals"] is None or r["min_order"] is None or r["min_order"] <= 0.0:
            continue
        du = r["du"] if (c2du and r.get("du")) else r["tick"]
        g = Grid(r["c"], r["name"], float(r["tick"]), int(r["price_decimals"]), int(r["volume_decimals"]),
                 float(r["min_order"]), float(du), float(r.get("du") or r["tick"]),
                 size_scale(r["min_order"], home.get("min_order")))
        for b in r["books"]:
            out[int(b)] = g
    return out


def class_books(table: dict[str, Any], c: int) -> frozenset:
    """The books of class ``c`` (empty when the class map has none)."""
    if table.get("status") != STATUS_OK:
        return frozenset()
    for r in table["classes"]:
        if r["c"] == int(c):
            return frozenset(r["books"])
    return frozenset()


def merge_foreign(v623_foreign: Iterable[int], table: dict[str, Any], *, init_price: frozenset = frozenset(),
                  traded: Iterable[int] | None = None) -> frozenset:
    """The foreign set of v6.40: v6.23's (+ B4(3)'s initial-price books) less every book of a traded class whose units
    are readable.  Default (home only, a readable layout of today): v6.23's set exactly."""
    out = set(int(b) for b in (v623_foreign or ())) | set(int(b) for b in (init_price or ()))
    if traded:
        grids = book_grids(table, traded, False)
        out -= set(grids)
    return frozenset(out)


def flush_ns(spec: Any) -> int:
    """B4(6): S14's capture flush for a state of validator release ``spec``: 300 sim-s from 0.6.5 (65), else 60 sim-s
    (a state without a release: 60, the tracker's own default)."""
    s = _whole(spec)
    return FLUSH_NS_FROM if s is not None and s >= FLUSH_SPEC else FLUSH_NS_BEFORE


# ---- prices on a class's grid with a depth unit (c2du) -----------------------------------------------------------------

def deep_price_du(mid: float, depth: float, side: str, *, bid: float, ask: float, du: float, tick: float,
                  decimals: int) -> float:
    """v6.3.3's deep price with the depth in depth units and the venue tick as the touch margin (vm738's deep L0 /
    ladder price: round(c -/+ D x DU) and at least one venue tick behind the best price on its side).  With du == tick:
    research_v633_deep_layer.deep_price exactly."""
    if side == "buy":
        return round(min(float(mid) - float(depth) * float(du), float(bid) - float(tick)), int(decimals))
    return round(max(float(mid) + float(depth) * float(du), float(ask) + float(tick)), int(decimals))


def vacuum_price_du(mid: float, dist: float, side: str, *, bid: float, ask: float, du: float, tick: float,
                    decimals: int) -> float:
    """v6.4's vacuum price with the distance in depth units and the inside-the-touch margins in venue ticks (vm738's
    live vacuum order).  With du == tick: research_v64_board.vacuum_price exactly."""
    d, t, dec = float(du), float(tick), int(decimals)
    if side == "buy":
        px = round(float(mid) - float(dist) * d, dec)
        return round(min(max(px, round(float(bid) + t, dec)), round(float(ask) - t, dec)), dec)
    px = round(float(mid) + float(dist) * d, dec)
    return round(max(min(px, round(float(ask) - t, dec)), round(float(bid) + t, dec)), dec)


# ---- C2J ------------------------------------------------------------------------------------------------------------------

def c2j_rest_ticks(c2j: int, rest_ticks: Any) -> float:
    """C2J: the rest band in depth units for v6.17's verdict (re-placed once the touch is more than rest - 0.5 units
    better): c2j=1 the band itself (ftk - 0.5), c2j=2 one more unit (ftk + 0.5, vm739's one-behind slack)."""
    r = float(rest_ticks)
    return r + 1.0 if int(c2j) == 2 else r


def c2j_behind_price(side: str, *, bid: Any, ask: Any, du: Any, decimals: Any) -> float | None:
    """C2J c2j=2: one depth unit behind the best price on the side (vm739 _c2j_behind: the venue grid, no cushion);
    None on a crossed / one-sided book or a price <= 0."""
    b, a, d, dec = _finite(bid), _finite(ask), _finite(du), _whole(decimals)
    if b is None or a is None or d is None or dec is None or b >= a or d <= 0.0:
        return None
    p = round(b - d, dec) if side == "buy" else round(a + d, dec)
    return p if p > 0.0 else None


# ---- C2Q -------------------------------------------------------------------------------------------------------------------

def making_beta(buy: dict, sell: dict, books: Iterable[int]) -> float:
    """beta_c of a class: sum over its books of 2 x min(buy, sell) / sum of (buy + sell) -- the making mirror's balanced
    share of the class's captured notional (vm739: mm = 2 min(wb, ws), cc = wb + ws over the making window); NaN when
    the class captured nothing."""
    num = den = 0.0
    for b in books:
        wb = _finite((buy or {}).get(int(b))) or 0.0
        ws = _finite((sell or {}).get(int(b))) or 0.0
        num += 2.0 * min(wb, ws)
        den += wb + ws
    if den <= 0.0:
        return float("nan")
    return max(0.0, num) / den


def c2q_decision(r_all: Any, c_all: Any, r0: Any, c0: Any, r2: Any, c2: Any, beta0: Any, beta2: Any,
                 pf: Any) -> dict[str, Any]:
    """C2Q's break-even rule (vm739 _c2q_update): q = clip(R / C, 0, 1) over every class; with class-2 evidence
    (C_2 > 0, C_0 > 0, beta_0 > 0 and beta_2 finite) armed iff q_2 > thr x q, q_2 = R_2 / C_2, thr = (1 - g) / (1 + g x),
    g = PF x beta_2 / beta_0, x = C_2 / C_0; without it armed (a probe).  Returns {armed, probe, q, q2, x, g, thr}."""
    R, C = _finite(r_all) or 0.0, _finite(c_all) or 0.0
    q = min(1.0, max(0.0, R / C)) if C > 0.0 else 0.0
    R2, C2c, C0 = _finite(r2) or 0.0, _finite(c2) or 0.0, _finite(c0) or 0.0
    b0, b2, p = _finite(beta0), _finite(beta2), _finite(pf)
    out = {"armed": True, "probe": True, "q": q, "q2": float("nan"), "x": float("nan"), "g": float("nan"),
           "thr": float("nan")}
    if C2c <= 0.0 or C0 <= 0.0 or b0 is None or b0 <= 0.0 or b2 is None or p is None:
        return out
    g = p * b2 / b0
    x = C2c / C0
    thr = (1.0 - g) / (1.0 + g * x)
    q2 = R2 / C2c
    out.update(armed=bool(q2 > thr * q), probe=False, q2=q2, x=x, g=g, thr=thr)
    return out


class ClassCaptureTracker(CaptureTracker):
    """C2Q's estimator: k124's K3Q CaptureTracker (S14's validator-exact realized at the 20-s horizon, its release and
    window rules, plus each counted fill's captured spread, both net of W x the book's maker fee) with its sums split
    by the fill's class (``class_of``: {book: class id}, set by the agent from the class map; an unmapped book counts as
    class -1).  Every base result (estimate, counts, K3Q's sums) is the base classes' exactly: this class only reads
    what they computed and keeps a parallel window of (fill ts, class, R, C)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.class_of: dict[int, int] = {}
        self.v640: deque = deque()               # (fill ts, class, realized - fee, captured - fee)
        self.v640_sums: dict[int, list] = {}

    def _release_head(self, st: dict, now, *, forced: bool = False) -> bool:
        n = len(self.k3q)
        done = super()._release_head(st, now, forced=forced)
        if len(self.k3q) > n:
            ts, r, c = self.k3q[-1]
            cls = int(self.class_of.get(int(st.get("k3q_book", -1)), -1))
            self.v640.append((int(ts), cls, float(r), float(c)))
            acc = self.v640_sums.setdefault(cls, [0.0, 0.0])
            acc[0] += float(r)
            acc[1] += float(c)
        return done

    def _k3q_expire(self, cutoff: int) -> None:
        super()._k3q_expire(cutoff)
        while self.v640 and self.v640[0][0] < cutoff:
            _ts, cls, r, c = self.v640.popleft()
            acc = self.v640_sums.setdefault(cls, [0.0, 0.0])
            acc[0] -= r
            acc[1] -= c
        if not self.v640:
            self.v640_sums = {}

    def set_active(self, active, now_ns) -> bool:
        started = super().set_active(active, now_ns)
        if started:
            self.v640, self.v640_sums = deque(), {}
        return started

    def begin(self, now_ns) -> bool:
        last = self.last_now
        reset = super().begin(now_ns)
        now = _finite(now_ns)
        if reset and last is not None and now is not None:
            delta = int(now) - int(last)
            self.v640 = deque((ts + delta, cls, r, c) for ts, cls, r, c in self.v640)
        return reset

    def class_sums(self, c: int) -> tuple[float, float]:
        """(R, C) of class ``c``'s fills over the window."""
        acc = self.v640_sums.get(int(c)) or (0.0, 0.0)
        return float(acc[0]), float(acc[1])


# ---- C2Q's live pool factor: the validator's published class pools ------------------------------------------------------

GAUGE_URL = "http://84.32.70.8:9091/metrics/simulation"   # the validator's public report page (class_gauges; GET only)
GAUGE_CACHE_DIR = "/tmp/orthoxplus_class_gauges"         # one cache per host, shared by every agent on it
GAUGE_PERIOD_S = 300.0      # STRUCTURAL: the class pools are the scoring report's (minutes apart); one fetch per 5 min per host
GAUGE_TIMEOUT_S = 30.0      # the page is ~1.3 MB (5 s from the Chicago host, k122's recorder)
GAUGE_POLL_S = 15.0
_GAUGE_PREFIX = "class_gauges{"
_SIM_PREFIX = "simulation_gauges{"
_GAUGE_LABEL = re.compile(r'(\w+)="([^"]*)"')


def parse_class_gauges(text: Any) -> dict[str, Any] | None:
    """The page's class pools: {"sim_id", "sim_ts_ns", "classes": {asset_class: {gauge name: value}}} from its
    class_gauges lines and the report's sim timestamp (a
    prefix test per line; the 0.6.5 report: weight, bar, n_books, making_credit, skill_credit, ...); None without any."""
    if not isinstance(text, str):
        return None
    classes: dict[str, dict[str, float]] = {}
    sim_id, sim_ts = None, None
    for line in text.splitlines():
        if line.startswith(_SIM_PREFIX) and 'simulation_gauge_name="timestamp"' in line:
            v = _finite(line.rpartition("}")[2].strip())
            sim_ts = int(v) if v is not None else sim_ts            # the report's sim time (ns)
            continue
        if not line.startswith(_GAUGE_PREFIX):
            continue
        head, sep, value = line.rpartition("}")
        if not sep:
            continue
        lab = dict(_GAUGE_LABEL.findall(head))
        v = _finite(value.strip())
        name, gauge = lab.get("asset_class"), lab.get("class_gauge_name")
        if v is None or not name or not gauge:
            continue
        classes.setdefault(name, {})[gauge] = v
        sim_id = lab.get("sim_id") or sim_id
    if not classes:
        return None
    return {"sim_id": sim_id, "sim_ts_ns": sim_ts, "classes": classes}


def pool_factor(gauges: Any, name0: str, name_k: str) -> float | None:
    """C2Q's PF from the published class pools: (w_k / w_0) x (MK_0 / MK_k), w = the class weight, MK = its making credit
    (== the sum of the class's positive per-uid making gauges, k139 check on k122's 03:46 JST page) -- k137's p3board137
    pool factor; None when a class or a value is missing or a pool is not positive."""
    try:
        cl = (gauges or {}).get("classes") or {}
        c0, ck = cl.get(name0) or {}, cl.get(name_k) or {}
        w0, wk = _finite(c0.get("weight")), _finite(ck.get("weight"))
        m0, mk = _finite(c0.get("making_credit")), _finite(ck.get("making_credit"))
        if None in (w0, wk, m0, mk) or w0 <= 0.0 or wk <= 0.0 or m0 <= 0.0 or mk <= 0.0:
            return None
        return (wk / w0) * (m0 / mk)
    except Exception:
        return None


class ClassGaugeFeed(SharedFeed):
    """The page's class pools for every agent on the host -- v6.16's SharedFeed (a daemon thread, a host cache file, one
    fetch per period by whichever agent wins the lock; snapshot() never blocks) on the report page, its own cache file and
    parser.  Started only by C2Q (research_v640_c2q=1): with the switch off nothing is fetched."""

    def __init__(self, **kw) -> None:
        kw.setdefault("url", GAUGE_URL)
        kw.setdefault("cache_dir", GAUGE_CACHE_DIR)
        kw.setdefault("period_s", GAUGE_PERIOD_S)
        kw.setdefault("poll_s", GAUGE_POLL_S)
        kw.setdefault("timeout_s", GAUGE_TIMEOUT_S)
        super().__init__(**kw)

    def _paths(self) -> tuple[str, str]:
        return os.path.join(self.cache_dir, "gauges.json"), os.path.join(self.cache_dir, "fetch.lock")

    def _read_cache(self) -> dict[str, Any] | None:
        path, _lock = self._paths()
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return None
        except Exception as exc:
            self.read_errors += 1
            self.last_error = "read: %s" % type(exc).__name__
            return None
        if not isinstance(data, dict) or not isinstance(data.get("classes"), dict) or _finite(data.get("wall")) is None:
            return None
        return data

    def _write_cache(self, data: dict[str, Any]) -> None:
        path, _lock = self._paths()
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)

    def step(self) -> None:
        """One cycle: refresh the cache when due and this process holds the lock (v6.16's step with this page's parser)."""
        os.makedirs(self.cache_dir, exist_ok=True)
        now = float(self._clock())
        cached = self._read_cache()
        if cached is None or now - float(cached["wall"]) >= self.period_s:
            _path, lock_path = self._paths()
            with open(lock_path, "a") as lock:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held = True
                except OSError:
                    held = False
                if held:
                    try:
                        cached = self._read_cache()
                        if cached is None or now - float(cached["wall"]) >= self.period_s:
                            self.fetches += 1
                            try:
                                page = parse_class_gauges(self._fetch(self.url, self.timeout_s))
                            except Exception as exc:
                                page = None
                                self.last_error = "fetch: %s" % type(exc).__name__
                            if page is None:
                                self.fetch_errors += 1
                            else:
                                self._write_cache({**page, "wall": now})
                    finally:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            cached = self._read_cache()
        if cached is not None:
            self.reads += 1
            self._snap = cached

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="v640-class-gauges", daemon=True)
            self._thread.start()

    def status(self) -> dict[str, Any]:
        snap = self._snap or {}
        wall = _finite(snap.get("wall"))
        return {"fetches": self.fetches, "fetch_errors": self.fetch_errors, "reads": self.reads,
                "read_errors": self.read_errors, "last_error": self.last_error,
                "cache_age_s": (round(float(self._clock()) - wall, 1) if wall is not None else None),
                "sim_id": snap.get("sim_id"), "classes": sorted((snap.get("classes") or {}).keys())}


def live_pool_factor(snap: Any, state_ts_ns: Any, sim_id: Any, name0: str, name_k: str,
                     fallback: Any) -> tuple[float | None, str]:
    """(PF, source) for C2Q's decision -- vm739's rule, the LAST PAGE AT OR BEFORE THE STATE: the host's cached report
    when it is this simulation's (its sim_id label == the state's) and its report time is at or before the state's,
    with both pools; otherwise the configured fallback (research_v640_c2q_pf).  source: "page" | "fallback_<why>"."""
    why = "no_page"
    if isinstance(snap, dict):
        pts, sts = _finite(snap.get("sim_ts_ns")), _finite(state_ts_ns)
        if sim_id and snap.get("sim_id") and str(snap.get("sim_id")) != str(sim_id):
            why = "other_sim"
        elif pts is not None and sts is not None and pts > sts:
            why = "after_state"
        else:
            pf = pool_factor(snap, name0, name_k)
            if pf is not None:
                return pf, "page"
            why = "no_pool"
    fb = parse_pf(fallback)
    return fb, "fallback_" + why


def block_of(ts_ns: Any) -> int | None:
    """The 30-s block of a state time (vm739: (ts // 1e9) // 30)."""
    t = _whole(ts_ns)
    if t is None:
        return None
    return int((t // 1_000_000_000) // 30)


__all__ = ["BLOCK_NS", "CANCEL_C2D", "CANCEL_C2Q", "CLASS1", "CLASS2", "ClassCaptureTracker", "ClassGaugeFeed",
           "GAUGE_URL", "live_pool_factor", "parse_class_gauges", "pool_factor", "DEFAULT_TRADED", "FLUSH_NS_BEFORE",
           "FLUSH_NS_FROM", "FLUSH_SPEC", "Grid", "HOME_CLASS", "STATUS_OK", "STATUS_SINGLE", "STATUS_UNREADABLE",
           "V6_40_CLASSES_VERSION", "block_of", "size_scale", "book_grids", "c2j_behind_price", "c2j_rest_ticks", "c2q_decision",
           "class_books", "class_digest", "class_table", "deep_price_du", "depth_unit", "flush_ns",
           "init_price_foreign", "layout_key", "making_beta", "merge_foreign", "parse_level", "parse_pf",
           "parse_traded", "traded_books", "vacuum_price_du"]
