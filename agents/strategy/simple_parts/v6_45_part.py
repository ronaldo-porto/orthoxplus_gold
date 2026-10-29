"""v6.45 (USER 10-30 "build 3.5x"; Round 4 follow-up D_F3.5, SP/round4/f35/RESULT.md: DEV CARRIED vs CAND S +48.4 %,
U +36.8 %): v6.44 + six arms of the tested replay spec, each behind its own switch (code default 0: v6.44 on every path),
all on in PARAMS --

1. BSL   research_v645_bsl = 2     the v6.25 lean sized from the net expected convergence g - Pt (research_v645_bsl);
2. C2BC  research_v645_c2bc = 1    class 2's C2T layer centred on the lean of (g - gbar) - Pt (needs C2T, bsl 2);
3. C2SUR research_v645_c2sur = 1   class 2's adding C2T quote withheld on the making window's capture-surplus side;
4. LBW   research_v645_lbw = 3     the lookback means over the observed span only while a ring is younger than 10,800 s;
5. C1P   research_v640_traded_classes = 0,1,2  class 1 on v6.40's class-0 machinery in its own units (no new code);
6. F3.5  research_v645_s3kf = 3.5, s3tf = 8752, s3kh = 2, s3cl = 1, s3c = 0  the class-0 front-load
         (research_v645_frontload).

This mixin, first among Simple's bases, carries the agent side WITHOUT a line in Simple's body:

* the lean: Simple's pass calls ``self._v6_25_lean(...)`` once per book (its only caller).  With BSL (or C2BC) on, the
  _v6_40_init override binds ``_v6_45_lean`` on the instance under that name (an instance attribute is found before the
  class's method), so the pass calls this part's lean, which runs v6.25's own method (``type(self)._v6_25_lean``) on
  every book it leaves as v6.44 has it.  Off, nothing is bound: v6.44's call exactly.  Each call pushes the book's mid
  into its lookback ring (every book with a two-sided L1, every state: the engine's mid grid); a class-0 / class-1 book
  takes the BSL lean (v6.25's tally and tau store, so v6.27's touch centre and v6.42's L1T read it); a class-2 book that
  C2T takes keeps v6.25's lean (C2T quotes on its TRUE inventory; only v6.19's hold reads that lean, as in v6.44) and,
  with C2BC, gets its class-2 centre for this request;
* C2BC / C2SUR: _v6_44_c2t_book overrides V644Part's -- the inventory handed to C2T's plan is inventory - tau2, and the
  capture-surplus side joins the held set (C2T's plan holds a held side only while it adds);
* the front-load: _v6_43_line overrides V643Part's (with the switch on: research_v645_frontload's FrontLoadLine, a
  RescueLine), _v6_43_clip too (a front-loaded book's deep clip x its front factor instead of the rescue's);
* the rings' session: _v6_43_save / _v6_43_restore override V643Part's -- the rings ride in the v6.21 pace state
  (research_v645_bsl.SESSION_KEY) and come back only into the same simulation (v6.21's 'restored');
* the V6_40_STATE row carries this part's snapshot under "v6_45" (_v6_40_snapshot override).

Every path never raises: an error counts (_v6_45_errors, in the row) and the book runs v6.44's (the lean, the line, the
clip) or, inside C2T, places nothing this request (C2T's own fail-closed rule).  No taking.  STRUCTURAL."""
from __future__ import annotations

import math

from research_v621_young_window import RESTORE_RESTORED as V6_45_RESTORED
from research_v625_lean import note_book as v6_45_lean_note
from research_v63_trend_target import SIDE_BUY as V6_45_BUY, SIDE_SELL as V6_45_SELL
from research_v645_bsl import SESSION_KEY as V6_45_SESSION_KEY
from research_v645_bsl import V645_BSL_VERSION
from research_v645_bsl import LeanRings as V6_45LeanRings
from research_v645_bsl import bsl_tau as v6_45_bsl_tau
from research_v645_bsl import c2bc_tau as v6_45_c2bc_tau
from research_v645_bsl import lbw_on as v6_45_lbw_on
from research_v645_bsl import parse_level as v6_45_parse_level
from research_v645_bsl import pt_bps as v6_45_pt_bps
from research_v645_bsl import surplus_side as v6_45_surplus_side
from research_v645_frontload import V645_FRONTLOAD_VERSION
from research_v645_frontload import FrontLoadLine as V6_45FrontLoadLine
from research_v645_frontload import parse_classes as v6_45_parse_classes
from research_v645_frontload import parse_number as v6_45_parse_number
from research_v6_40_classes import HOME_CLASS as V6_45_HOME
from research_v6_40_classes import STATUS_OK as V6_45_STATUS_OK
from research_v6_40_classes import STATUS_SINGLE as V6_45_STATUS_SINGLE

V6_45_SWITCHES = ("research_v645_bsl", "research_v645_lbw", "research_v645_c2bc", "research_v645_c2sur",
                  "research_v645_s3kf", "research_v645_s3tf", "research_v645_s3kh", "research_v645_s3cl",
                  "research_v645_s3c")


class V645Part:
    """v6.45 (research_v645_*): BSL + LBW on the lean, C2BC + C2SUR on class 2's C2T layer, the class-0 front-load"""

    # ---- switches and counters -----------------------------------------------------------------------------------

    def _v6_45_count(self, key: str, n: int = 1) -> None:
        counts = getattr(self, "_v6_45_counts", None)
        if counts is None:
            counts = {}
            self._v6_45_counts = counts
        counts[key] = int(counts.get(key, 0)) + int(n)

    def _v6_45_error(self) -> None:
        self._v6_45_errors = int(getattr(self, "_v6_45_errors", 0) or 0) + 1

    def _v6_45_cfg(self) -> dict:
        """Every v6.45 switch, read once from the config (code default: off).  An unreadable value, or one whose
        dependency is off (lbw / c2bc need bsl -- c2bc bsl 2 -- c2bc / c2sur need C2T, the front-load v6.43's rescue
        line), runs off and counts an error."""
        st = getattr(self, "_v6_45_state", None)
        if st is not None:
            return st
        cfg = getattr(self, "config", None)

        def level(name, allowed):
            v = v6_45_parse_level(getattr(cfg, name, 0), allowed)
            if v is None:
                self._v6_45_error()
                self._v6_45_count("unreadable_" + name)
                return 0
            return int(v)

        def number(name):
            v = v6_45_parse_number(getattr(cfg, name, 0))
            if v is None:
                self._v6_45_error()
                self._v6_45_count("unreadable_" + name)
                return 0.0
            return float(v)

        st = {"bsl": level("research_v645_bsl", (0, 1, 2)), "lbw": level("research_v645_lbw", (0, 1, 2, 3)),
              "c2bc": level("research_v645_c2bc", (0, 1)), "c2sur": level("research_v645_c2sur", (0, 1)),
              "s3kf": number("research_v645_s3kf"), "s3tf": number("research_v645_s3tf"),
              "s3kh": number("research_v645_s3kh"), "s3cl": level("research_v645_s3cl", (0, 1)),
              "s3c": frozenset({V6_45_HOME})}
        raw_c = getattr(cfg, "research_v645_s3c", None)
        if st["s3kf"] > 0.0:
            classes = v6_45_parse_classes(raw_c if raw_c is not None else V6_45_HOME)
            if classes is None:
                self._v6_45_error()
                self._v6_45_count("unreadable_research_v645_s3c")
                st["s3kf"] = 0.0
            else:
                st["s3c"] = classes
        c2t = bool(getattr(self, "_v6_44_switch", None) and self._v6_44_switch("research_v644_c2t"))
        if st["lbw"] and not st["bsl"]:
            self._v6_45_error()
            self._v6_45_count("refused_lbw_without_bsl")
            st["lbw"] = 0
        if st["c2bc"] and not (st["bsl"] == 2 and c2t):
            self._v6_45_error()
            self._v6_45_count("refused_c2bc_without_bsl2_c2t")
            st["c2bc"] = 0
        if st["c2sur"] and not c2t:
            self._v6_45_error()
            self._v6_45_count("refused_c2sur_without_c2t")
            st["c2sur"] = 0
        st["front"] = bool(st["s3kf"] > 0.0)
        if st["front"] and not (getattr(self, "_v6_43_on", None) and self._v6_43_on()):
            self._v6_45_error()
            self._v6_45_count("refused_frontload_without_rescue")
            st["front"] = False
        self._v6_45_state = st
        return st

    # ---- init: the lean binding --------------------------------------------------------------------------------------

    def _v6_40_init(self) -> None:
        """V644Part's init (V640Part's, FGK's tracker), then v6.45's switches; with BSL on (C2BC needs it) this part's lean
        is bound on the instance as ``_v6_25_lean`` (see the module docstring).  Never raises past V644Part's."""
        super()._v6_40_init()
        try:
            self._v6_45_state = None
            self._v6_45_c2tau = {}
            st = self._v6_45_cfg()
            if st["bsl"] and callable(getattr(type(self), "_v6_25_lean", None)):
                self._v6_25_lean = self._v6_45_lean
                self._v6_45_count("lean_bound")
        except Exception:
            self._v6_45_error()

    def _v6_45_rings_ref(self):
        rings = getattr(self, "_v6_45_rings", None)
        if rings is None:
            rings = V6_45LeanRings()
            self._v6_45_rings = rings
        return rings

    def _v6_45_sim(self):
        """The simulation the rings belong to: the pace line's (v6.21 sets it every request, before its restore), else
        v6.40's read of the configuration."""
        pace = getattr(self, "_v66_pace", None)
        sim = getattr(pace, "sim_id", None) if pace is not None else None
        return sim if sim else getattr(self, "_v6_40_sim_id", None)

    def _v6_45_class2(self, book_id) -> bool:
        g = (getattr(self, "_v6_40_grids", None) or {}).get(int(book_id))
        return g is not None and int(g.c) == 2

    def _v6_45_class_of(self, book_id):
        """The book's class by v6.40's class map: its class (None for a book outside it), the home class on a
        single-class layout, None while the map is unread or unreadable."""
        table = getattr(self, "_v6_40_table", None)
        if not isinstance(table, dict):
            return None
        status = table.get("status")
        if status == V6_45_STATUS_SINGLE:
            return V6_45_HOME
        if status != V6_45_STATUS_OK:
            return None
        return (table.get("book_class") or {}).get(int(book_id))

    # ---- the lean (BSL, LBW, C2BC's centre) ---------------------------------------------------------------------------

    def _v6_45_lean(self, book_id: int, raw_bid, raw_ask, cfg, inv, now_ts: int, req) -> tuple:
        """v6.25's lean with BSL (bound as ``_v6_25_lean`` while research_v645_bsl is on): (inv - tau, inv - tau_prev).
        The book's mid enters its ring first; a book C2T takes keeps v6.25's lean (and C2BC's centre is computed); every
        other book's tau is BSL's -- the engine's _k162_tau on g = log(fp / mid) and Pt from its ring (LBW: observed
        span) in v6.25's form, 0 without a fresh fundamental.  v6.25's tau store and tally are kept.  Never raises: an
        error counts and the book takes v6.25's lean."""
        base = type(self)._v6_25_lean
        st = self._v6_45_cfg()
        b = int(book_id)
        try:
            rings = self._v6_45_rings_ref()
            if rings.begin(self._v6_45_sim()):
                self._v6_45_count("rings_new_sim")
            bid, ask = float(raw_bid), float(raw_ask)
            mid = 0.5 * (bid + ask)
            if bid > 0.0 and ask > 0.0:
                rings.push_mid(b, now_ts, mid)                      # the engine's 1-s mid grid, every book, every state
            c2t = bool(getattr(self, "_v6_44_c2t_on", None) and self._v6_44_c2t_on(b))
        except Exception:
            self._v6_45_error()
            return base(self, book_id, raw_bid, raw_ask, cfg, inv, now_ts, req)
        if c2t:
            out = base(self, book_id, raw_bid, raw_ask, cfg, inv, now_ts, req)
            if st["c2bc"]:
                self._v6_45_c2bc(b, raw_bid, raw_ask, cfg, now_ts, mid)
            return out
        if getattr(self, "_v6_38_l3_on", False):
            self._v6_45_count("lean_l3_kept")                       # v6.38 L3's lean is not combined with BSL
            return base(self, book_id, raw_bid, raw_ask, cfg, inv, now_ts, req)
        tau = prev = 0.0
        fresh = False
        try:
            taus = getattr(self, "_v6_25_taus", None)
            if taus is None:
                taus = {}
                self._v6_25_taus = taus
            last = taus.get(b)
            if last is not None and int(last[1]) <= int(now_ts):
                prev = float(last[0])
            taus[b] = (0.0, int(now_ts))                           # an error below leaves this request's lean at 0
            fp, _reason = self._v6_23_fundamental(book_id, raw_bid, raw_ask, cfg)
            if fp is not None:
                fresh = True
                f = float(fp)
                ring = rings.mid.get(b)
                if f > 0.0 and mid > 0.0 and ring is not None and ring.started:
                    g = math.log(f / mid) * 1e4
                    pt = v6_45_pt_bps(ring, v6_45_lbw_on(st["lbw"], self._v6_45_class2(b)))
                    tau = float(v6_45_bsl_tau(g, pt, st["bsl"], getattr(self, "research_v623_against_gap_bps", 0.0),
                                              getattr(self, "research_v625_lean_slope", 0.0),
                                              getattr(self, "research_v625_lean_cap", 0.0)))
                    self._v6_45_count("bsl_evals")
                    if tau:
                        self._v6_45_count("bsl_armed")
            taus[b] = (tau, int(now_ts))
        except Exception:
            self._v6_45_error()
            tau = 0.0
        v6_45_lean_note(req, fresh=fresh, tau=tau, inventory=inv)
        return (inv - tau if tau else inv), (inv - prev if prev else inv)

    def _v6_45_c2bc(self, book_id: int, raw_bid, raw_ask, cfg, now_ts: int, mid: float) -> None:
        """C2BC's centre of a C2T class-2 book this request (0 without a fresh fundamental): its fresh gap g enters the
        book's gap ring, tau2 = v6.25's form on (g - gbar) - Pt (both lookback means LBW's on class 2).  Never raises: an
        error counts and the centre is 0."""
        b = int(book_id)
        tau2 = 0.0
        try:
            st = self._v6_45_cfg()
            rings = self._v6_45_rings_ref()
            fp, _reason = self._v6_23_fundamental(book_id, raw_bid, raw_ask, cfg)
            ring = rings.mid.get(b)
            if fp is not None and float(fp) > 0.0 and mid > 0.0 and ring is not None and ring.started:
                g = math.log(float(fp) / mid) * 1e4
                young = v6_45_lbw_on(st["lbw"], True)
                gring = rings.push_gap(b, now_ts, g)
                tau2 = float(v6_45_c2bc_tau(g, gring.mean(young), v6_45_pt_bps(ring, young),
                                            getattr(self, "research_v623_against_gap_bps", 0.0),
                                            getattr(self, "research_v625_lean_slope", 0.0),
                                            getattr(self, "research_v625_lean_cap", 0.0)))
                self._v6_45_count("c2bc_evals")
                if tau2:
                    self._v6_45_count("c2bc_armed")
        except Exception:
            self._v6_45_error()
            tau2 = 0.0
        store = getattr(self, "_v6_45_c2tau", None)
        if store is None:
            store = {}
            self._v6_45_c2tau = store
        store[b] = (tau2, int(now_ts))

    # ---- C2BC / C2SUR on C2T's layer ----------------------------------------------------------------------------------

    def _v6_45_surplus(self, book_id: int):
        """C2SUR's side: the book's capture-surplus side over the making window from the agent's making mirror (v6.19's
        source; the engine's _k105_sur), or None (balanced, no mirror)."""
        mirror = getattr(self, "_v62_mirror", None)
        if mirror is None:
            return None
        buy, sell = mirror.book_capture(int(book_id))
        return v6_45_surplus_side(buy, sell)

    def _v6_44_c2t_book(self, response, state, book_id: int, deep, raw_bid, raw_ask, inv, other_rows, touch_rows,
                        already, req, *, paced: bool, held: frozenset, tick_size, min_order, budget: int, expiry: int,
                        cfg) -> int:
        """V644Part's C2T book with v6.45's class-2 arms: C2BC hands C2T's plan inventory - tau2 (this request's centre),
        C2SUR adds the capture-surplus side to the held set (C2T's plan holds it only while it adds).  Both off: V644Part's
        call as is.  Never raises: an error counts and the book places nothing this request (C2T's fail-closed rule)."""
        st = self._v6_45_cfg()
        if st["c2bc"] or st["c2sur"]:
            try:
                b = int(book_id)
                now = int(getattr(state, "timestamp", 0) or 0)
                if st["c2bc"]:
                    got = (getattr(self, "_v6_45_c2tau", None) or {}).get(b)
                    tau2 = float(got[0]) if got is not None and int(got[1]) == now else 0.0
                    if tau2:
                        inv = float(inv or 0.0) - tau2
                        self._v6_45_count("c2bc_centred_states")
                if st["c2sur"]:
                    side = self._v6_45_surplus(b)
                    if side in (V6_45_BUY, V6_45_SELL):
                        held = frozenset(held or ()) | {side}
                        self._v6_45_count("c2sur_side_" + str(side))
            except Exception:
                self._v6_45_error()
                return 0
        return super()._v6_44_c2t_book(response, state, book_id, deep, raw_bid, raw_ask, inv, other_rows, touch_rows,
                                       already, req, paced=paced, held=held, tick_size=tick_size, min_order=min_order,
                                       budget=budget, expiry=expiry, cfg=cfg)

    # ---- the front-load (the pace line and the deep clip) ------------------------------------------------------------

    def _v6_43_line(self, pace, flat_cls):
        """V643Part's line; with the front-load on (and v6.43's rescue): research_v645_frontload's FrontLoadLine, a
        RescueLine with the same switches and rate -- an error there counts and falls back to V643Part's."""
        try:
            st = self._v6_45_cfg()
            if st["front"] and self._v6_43_on():
                line = V6_45FrontLoadLine(front_kf=st["s3kf"], front_tf_s=st["s3tf"], front_kh=st["s3kh"],
                                          front_clip=st["s3cl"], front_classes=st["s3c"], class_of=self._v6_45_class_of,
                                          launch=pace.launch_on, guard=pace.guard_on, even=pace.even_on,
                                          rate=pace.rate)
                self._v6_45_count("frontload_line_made")
                return line
        except Exception:
            self._v6_45_error()
        return super()._v6_43_line(pace, flat_cls)

    def _v6_43_clip(self, got, pace, book_id, now_ts):
        """V643Part's clip, except a front-loaded book (clip mode 1): v6.28's clip ``got`` x its front factor (the
        engine's _s3_k on its launch line, 1 off it) in place of the rescue's.  Never raises: V643Part's."""
        try:
            if isinstance(pace, V6_45FrontLoadLine):
                f = pace.front_clip_factor(book_id, now_ts)
                if f is not None:
                    if f > 1.0:
                        self._v6_45_count("front_clip_book_states")
                    return float(got) * float(f)
        except Exception:
            self._v6_45_error()
        return super()._v6_43_clip(got, pace, book_id, now_ts)

    # ---- the rings' session ----------------------------------------------------------------------------------------

    def _v6_43_save(self, pace, state, rows):
        """V643Part's save (the rescue start), then the lean rings into the session's pace ``state`` (SESSION_KEY) while
        BSL is on.  Returns ``rows``.  Never raises."""
        rows = super()._v6_43_save(pace, state, rows)
        try:
            rings = getattr(self, "_v6_45_rings", None)
            if self._v6_45_cfg()["bsl"] and rings is not None and isinstance(state, dict) and (rings.mid or rings.gap):
                state[V6_45_SESSION_KEY] = rings.state()
                self._v6_45_count("rings_saved")
        except Exception:
            self._v6_45_error()
        return rows

    def _v6_43_restore(self, pace, payload, how, tallies):
        """V643Part's restore, then (v6.21 restored the same simulation's state) the saved lean rings.  Returns
        ``tallies``.  Never raises."""
        tallies = super()._v6_43_restore(pace, payload, how, tallies)
        try:
            if self._v6_45_cfg()["bsl"] and how == V6_45_RESTORED and isinstance(payload, dict):
                got = self._v6_45_rings_ref().restore(payload.get(V6_45_SESSION_KEY), self._v6_45_sim())
                self._v6_45_count("rings_restore_" + str(got))
        except Exception:
            self._v6_45_error()
        return tallies

    # ---- the state row ---------------------------------------------------------------------------------------------

    def _v6_45_snapshot(self) -> dict:
        st = dict(self._v6_45_cfg())
        st["s3c"] = sorted(st.get("s3c") or ())
        rings = getattr(self, "_v6_45_rings", None)
        pace = getattr(self, "_v66_pace", None)
        out = {"version": V645_BSL_VERSION, "frontload_version": V645_FRONTLOAD_VERSION, "switches": st,
               "lean_bound": int("_v6_25_lean" in getattr(self, "__dict__", {})),
               "rings": ({"mid": len(rings.mid), "gap": len(rings.gap), "sim": rings.sim, "restored": rings.restored,
                          "resets": int(rings.resets), "rewinds": int(rings.rewinds)} if rings is not None else None),
               "frontload_line": int(isinstance(pace, V6_45FrontLoadLine)),
               "counts": dict(getattr(self, "_v6_45_counts", None) or {}),
               "errors": int(getattr(self, "_v6_45_errors", 0) or 0)}
        return out

    def _v6_40_snapshot(self) -> dict:
        """V644Part's snapshot plus this part's under "v6_45" (the V6_40_STATE row).  Never raises past V644Part's."""
        out = super()._v6_40_snapshot()
        try:
            out["v6_45"] = self._v6_45_snapshot()
        except Exception:
            self._v6_45_error()
        return out
