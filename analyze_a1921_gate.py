#!/usr/bin/env python3
"""A1.9.2.1 gate report for a Strategy1_Research_Simple run.

Usage:  python3 analyze_a1921_gate.py <run.jsonl> [--ticks 500]

Every metric is computed WITHIN the run.  Nothing is compared against a stored
baseline from another day: maker entry fee has swung ~115 bps across recent
runs and is the dominant outcome variable, so cross-run aggregates measure the
market rather than the strategy.  The fee <= 0 bucket is the internal control --
a rebate returns at ALLOW_REBATE_ENTRY before any A1.9.2.1 code runs, so it
must not move.

Denominator note: ENTRY_DECISION is sampled (research_every_n) while A192_*
events are force-emitted.  Suppression rates are read from the strategy's own
window_suppression_pct, never derived by dividing the two.
"""
from __future__ import annotations
import json, sys, math, statistics as st
from collections import Counter, defaultdict

# A1.9.2 @500 reference column.  These are the values THIS script computes on
# the A1.9.2 run, so the comparison is definition-for-definition.  (An earlier
# ad-hoc pass reported +0.165 / 45.9% using per-book tail alone rather than the
# composite severity used here; both say "no alignment", but only these numbers
# are comparable to what this script prints for a new run.)
A192_BASE = {
    "spearman_sev_budget": 0.032, "below_median_budget_pct": 53.5,
    "dwell_share_pct": 73.0, "ih_rts": 10, "rt_velocity": 0.100,
    "cubic_per_rt": 0.0081, "quiet_pct": 94.6,
    "fee": {"<=0": (23, 83.0, 17.0, +0.9985), "0-5": (9, 22.0, 56.0, -1.1544),
            ">5": (18, 22.0, 94.0, -2.4799)},
}

def spearman(xs, ys):
    n = len(xs)
    if n < 3: return float("nan")
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0]*len(v); i = 0
        while i < len(order):
            j = i
            while j+1 < len(order) and v[order[j+1]] == v[order[i]]: j += 1
            avg = (i+j)/2.0 + 1.0
            for k in range(i, j+1): r[order[k]] = avg
            i = j+1
        return r
    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx)/n, sum(ry)/n
    num = sum((a-mx)*(b-my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a-mx)**2 for a in rx) * sum((b-my)**2 for b in ry))
    return num/den if den else float("nan")

def severity(r):
    return max(0.0, float(r.get("book_tail_shortfall_bps") or 0.0)) \
         + abs(min(0.0, float(r.get("book_net_bps_ewma") or 0.0)))

def load(path, cut):
    recs = defaultdict(list)
    for ln in open(path, errors="replace"):
        try: r = json.loads(ln)
        except Exception: continue
        t = r.get("tick")
        if t is None or t > cut: continue
        recs[r.get("type")].append(r)
    return recs

def main():
    if len(sys.argv) < 2:
        print(__doc__); return 2
    path = sys.argv[1]
    cut = 500
    if "--ticks" in sys.argv: cut = int(sys.argv[sys.argv.index("--ticks")+1])
    R = load(path, cut)
    ed  = R.get("ENTRY_DECISION", [])
    sup = R.get("A192_ENTRY_SUPPRESSED", [])
    cap = R.get("A192_CAP_BLOCK", [])
    dfr = R.get("A192_SEVERITY_DEFER", [])
    seed= R.get("A192_SEVERITY_SEED", [])
    cs  = R.get("A192_COLDSTART_SHRINK", [])
    print("="*74); print("A1.9.2.1 GATE  --  %s  (ticks 1-%d)" % (path.split("/")[-1], cut)); print("="*74)
    ver = {str(r.get("engine_version")) for r in R.get("A192_ACTIVATION_BANNER", [])}
    print("engine:", ", ".join(sorted(v for v in ver if v != "None")) or "(no banner)")

    # ---- 0. comparability -------------------------------------------------
    fees = [float(r["current_maker_fee_bps"]) for r in ed if r.get("current_maker_fee_bps") is not None]
    print("\n-- 0. FEE ENVIRONMENT (comparability vs A1.9.2 @500: med -2.07, 45.0% >0) --")
    if fees:
        f = sorted(fees); pos = 100.0*sum(1 for x in f if x > 0)/len(f)
        print("   median %+.2f bps   %%fee>0 %.1f%%   p90 %+.2f   n=%d" % (
            st.median(f), pos, f[int(.9*(len(f)-1))], len(f)))
        drift = abs(st.median(f) - (-2.07)) > 8.0 or abs(pos - 45.0) > 20.0
        print("   %s" % ("!! fee environment differs materially from the A1.9.2 run --"
                         " cross-run PnL/velocity comparison is VOID, use the fee-stratified"
                         " table and the fee<=0 control only" if drift else
                         "OK: comparable to the A1.9.2 run"))
    else:
        print("   no ENTRY_DECISION fee data")

    # ---- 1. preflight -----------------------------------------------------
    print("\n-- 1. PREFLIGHT / ACTIVATION --")
    armed = any(int(r.get("armed", 0) or 0) for r in seed)
    print("   A192_SEVERITY_SEED      : %s%s" % (
        ("%d event(s), seeded_books=%s" % (len(seed), [r.get("seeded_books") for r in seed]))
        if seed else "ABSENT", "  ARMED" if armed else "  NOT ARMED -> warm-up blind spot"))
    print("   A192_SEVERITY_DEFER     : %d   %s" % (
        len(dfr), "PASS" if dfr else "**ABORT: prioritisation inert**"))
    print("   A192_COLDSTART_SHRINK   : %d" % len(cs))
    w = [float(r["window_suppression_pct"]) for r in sup+cap+dfr if r.get("window_suppression_pct") is not None]
    if w:
        w.sort()
        over = 100.0*sum(1 for x in w if x > 35.0)/len(w)
        print("   window_suppression_pct  : med %.1f  p90 %.1f  max %.1f   (>35%%: %.1f%% of readings)" % (
            st.median(w), w[int(.9*(len(w)-1))], w[-1], over))
        print("   volume guarantee        : %s" % ("PASS" if w[int(.9*(len(w)-1))] <= 35.0 else "**ABORT: cap breached**"))

    # ---- 2. allocation ----------------------------------------------------
    print("\n-- 2. ALLOCATION (the A1.9.2.1 mechanism) --")
    budget = Counter(int(r["book"]) for r in sup if r.get("book") is not None)
    booksev = {}
    for r in sup+cap+dfr:
        b = r.get("book")
        if b is not None: booksev.setdefault(int(b), []).append(severity(r))
    booksev = {b: st.median(v) for b, v in booksev.items()}
    tot = sum(budget.values()) or 1
    if len(booksev) >= 3:
        bs = sorted(booksev)
        rho = spearman([booksev[b] for b in bs], [budget.get(b, 0) for b in bs])
        med = st.median(list(booksev.values()))
        low = 100.0*sum(v for b, v in budget.items() if booksev.get(b, 0.0) < med)/tot
        print("   Spearman(book severity, budget share) : %+.3f   (A1.9.2 %+.3f)  target > +0.50  %s" % (
            rho, A192_BASE["spearman_sev_budget"], "PASS" if rho > 0.50 else "FAIL"))
        print("   budget on below-median-severity books : %.1f%%   (A1.9.2 %.1f%%)  target < 25%%  %s" % (
            low, A192_BASE["below_median_budget_pct"], "PASS" if low < 25.0 else "FAIL"))
    s_sup = [severity(r) for r in sup]
    s_adm = [severity(r) for r in cap+dfr]
    if s_sup and s_adm:
        print("   median severity suppressed / admitted : %.2f / %.2f   %s" % (
            st.median(s_sup), st.median(s_adm),
            "PASS (separated)" if st.median(s_sup) > st.median(s_adm)*1.10 else "FAIL (no separation)"))
    dw = sum(1 for r in sup if r.get("reason") == "SUPPRESS_DWELL_ACTIVE")
    print("   dwell share of suppressions           : %.1f%%   (A1.9.2 %.1f%%)" % (
        100.0*dw/max(1, len(sup)), A192_BASE["dwell_share_pct"]))

    # ---- 3. round trips, fee-stratified -----------------------------------
    opens, rts = {}, []
    for r in R.get("POSITION", []):
        b = r.get("book_id")
        nb, na = float(r.get("net_before") or 0.0), float(r.get("net_after") or 0.0)
        if abs(nb) < 1e-9 and abs(na) > 1e-9: opens[b] = r.get("tick")
        if r.get("round_trip"):
            rts.append((b, opens.pop(b, None), r.get("tick"), float(r.get("realized_pnl_delta") or 0.0)))
    edb = defaultdict(list)
    for r in ed: edb[r.get("book")].append(r)
    exb = defaultdict(list)
    for r in R.get("EXIT", []): exb[r.get("book")].append(r)
    rows = []
    for b, ot, ct, p in rts:
        if ot is None: continue
        c = [e for e in edb.get(b, []) if e.get("tick") <= ot]
        fee = float(c[-1].get("current_maker_fee_bps") or 0.0) if c else None
        adm = c[-1].get("a192_admission") if c else None
        x = [e for e in exb.get(b, []) if e.get("tick") <= ct]
        taker = bool(x and x[-1].get("unified_action") == "TAKER_PROTECT")
        rows.append((b, ot, p, fee, adm, taker))
    print("\n-- 3. ROUND TRIPS, FEE-STRATIFIED (fee<=0 is the internal control) --")
    print("   %-8s %-5s %-9s %-9s %-10s %s" % ("bucket", "n", "positive", "taker", "PnL", "A1.9.2 @500"))
    def bucket(f):
        if f is None: return None
        return "<=0" if f <= 0 else ("0-5" if f <= 5 else ">5")
    for name in ("<=0", "0-5", ">5"):
        g = [r for r in rows if bucket(r[3]) == name]
        pb = A192_BASE["fee"][name]
        if not g:
            print("   %-8s %-5d %-9s %-9s %-10s n=%d %.0f%% pos %.0f%% tk %+.4f" % (name, 0, "-", "-", "-", *pb))
            continue
        pos = 100.0*sum(1 for r in g if r[2] > 0)/len(g)
        tk = 100.0*sum(1 for r in g if r[5])/len(g)
        pnl = sum(r[2] for r in g)
        flag = ""
        if name == "<=0":
            flag = "  <-- CONTROL: %s" % ("OK" if pos >= 75.0 and tk <= 25.0 else "**MOVED, investigate leak**")
        print("   %-8s %-5d %-9.1f %-9.1f %+-10.4f n=%d %.0f%% pos %.0f%% tk %+.4f%s" % (
            name, len(g), pos, tk, pnl, *pb, flag))
    gp = [r for r in rows if r[3] is not None and r[3] > 0]
    if gp:
        print("   fee>0 combined: n=%d  taker %.1f%% (A1.9.2 81%%)  PnL %+.4f (A1.9.2 -3.6343)  <-- PRIMARY TARGET" % (
            len(gp), 100.0*sum(1 for r in gp if r[5])/len(gp), sum(r[2] for r in gp)))

    # ---- 4. headline ------------------------------------------------------
    print("\n-- 4. VOLUME & DOWNSIDE --")
    neg = [min(0.0, r[2]) for r in rows]
    cub = sum(abs(x)**3 for x in neg)/max(1, len(rows))
    print("   round trips %d   RT velocity %.4f/tick (A1.9.2 %.4f, floor 0.085)  %s" % (
        len(rows), len(rows)/cut, A192_BASE["rt_velocity"],
        "PASS" if len(rows)/cut >= 0.085 else "FAIL"))
    print("   total PnL %+.4f   positive %d/%d (%.1f%%)" % (
        sum(r[2] for r in rows), sum(1 for r in rows if r[2] > 0), len(rows),
        100.0*sum(1 for r in rows if r[2] > 0)/max(1, len(rows))))
    print("   cubic downside/RT %.4f (A1.9.2 %.4f)  %s" % (
        cub, A192_BASE["cubic_per_rt"], "PASS" if cub <= A192_BASE["cubic_per_rt"] else "FAIL"))
    adm = Counter(r[4] for r in rows if r[4])
    print("\n   admission bucket -> RTs:", dict(adm))
    ih = adm.get("ALLOW_INSUFFICIENT_HISTORY", 0)
    print("   ALLOW_INSUFFICIENT_HISTORY RTs: %d  (A1.9.2 %d)  target ~0  %s" % (
        ih, A192_BASE["ih_rts"], "PASS" if ih <= 2 else "FAIL"))
    print("="*74)
    return 0

if __name__ == "__main__":
    sys.exit(main())
