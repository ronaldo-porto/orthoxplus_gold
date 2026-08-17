#!/usr/bin/env python3
"""Summarize Strategy1_Research JSONL into skip, lifecycle, and latency diagnostics."""
import argparse, json, math
from collections import Counter, defaultdict
from pathlib import Path


def pct(xs, p):
    if not xs: return None
    xs = sorted(xs); k = (len(xs)-1)*p; lo = math.floor(k); hi = math.ceil(k)
    return xs[lo] if lo == hi else xs[lo]*(hi-k) + xs[hi]*(k-lo)

def f(x): return "-" if x is None else f"{x:.3f}"

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("jsonl"); ap.add_argument("--top", type=int, default=15); a = ap.parse_args()
    reasons=Counter(); actions=Counter(); events=Counter(); bybook=defaultdict(Counter)
    submit=Counter(); fills=Counter(); rejects=Counter(); totals=[]; responds=[]; req=zero=bad=0
    for raw in Path(a.jsonl).open(encoding="utf-8", errors="replace"):
        try: r=json.loads(raw)
        except json.JSONDecodeError: bad+=1; continue
        typ=str(r.get("type","UNKNOWN")); events[typ]+=1
        if typ=="DECISION":
            action=str(r.get("action","UNKNOWN")); reason=str(r.get("reason","UNKNOWN")); book=r.get("book_id")
            actions[action]+=1; reasons[reason]+=1; bybook[book][reason]+=1
        elif typ=="ORDER_LIFECYCLE":
            ph=str(r.get("phase","UNKNOWN")).upper(); book=r.get("book_id"); events[f"LIFECYCLE:{ph}"]+=1
            if ph=="SUBMITTED": submit[book]+=1
            if "TRADE" in ph or "FILL" in ph: fills[book]+=1
            if "REJECT" in ph or "FAIL" in ph: rejects[book]+=1
        elif typ=="TIMING":
            req+=1
            try: totals.append(float(r.get("total_ms")))
            except (TypeError,ValueError): pass
            try: responds.append(float(r.get("respond_ms")))
            except (TypeError,ValueError): pass
            if int(r.get("instructions",0) or 0)==0: zero+=1
    print("Strategy1_Research diagnosis\n"+"="*72)
    print(f"file: {a.jsonl}\nmalformed: {bad}\nsampled requests: {req}")
    if req: print(f"zero-instruction requests: {zero}/{req} ({100*zero/req:.1f}%)")
    print(f"latency total ms: p50={f(pct(totals,.5))} p95={f(pct(totals,.95))} max={f(max(totals) if totals else None)}")
    print(f"latency respond ms: p50={f(pct(responds,.5))} p95={f(pct(responds,.95))} max={f(max(responds) if responds else None)}")
    print("\nTop actions:")
    for k,v in actions.most_common(a.top): print(f"{v:8d}  {k}")
    print("\nTop reasons:")
    for k,v in reasons.most_common(a.top): print(f"{v:8d}  {k}")
    print("\nBooks:")
    for book,c in sorted(bybook.items(), key=lambda kv:-sum(kv[1].values()))[:a.top]:
        top=", ".join(f"{k}:{v}" for k,v in c.most_common(4))
        print(f"book={str(book):>4} decisions={sum(c.values()):6d} submitted={submit[book]:5d} fills={fills[book]:5d} rejects={rejects[book]:5d} top=[{top}]")
    print("\nLifecycle:")
    for k,v in sorted(((k,v) for k,v in events.items() if k.startswith("LIFECYCLE:")), key=lambda kv:(-kv[1],kv[0]))[:a.top]:
        print(f"{v:8d}  {k}")

if __name__ == "__main__": main()
