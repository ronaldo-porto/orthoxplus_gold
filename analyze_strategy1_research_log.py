#!/usr/bin/env python3
"""Analyze Strategy1_Research V2 JSONL: policy, archetypes, lifecycle and round trips."""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    return xs[lo] if lo == hi else xs[lo] * (hi - k) + xs[hi] * (k - lo)


def fmt(x):
    return "-" if x is None else f"{x:.3f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    reasons = Counter()
    actions = Counter()
    archetypes = Counter()
    tiers = Counter()
    arch_sources = Counter()
    events = Counter()
    bybook = defaultdict(Counter)
    submit = Counter()
    fills = Counter()
    rejects = Counter()
    transitions = Counter()
    roundtrip_books = Counter()
    totals = []
    responds = []
    regimes = []
    configs = []
    req = zero = malformed = 0

    path = Path(args.jsonl)
    for raw in path.open(encoding="utf-8", errors="replace"):
        try:
            r = json.loads(raw)
        except json.JSONDecodeError:
            malformed += 1
            continue

        typ = str(r.get("type", "UNKNOWN"))
        events[typ] += 1

        if typ == "RESEARCH_CONFIG":
            configs.append(r)
        elif typ == "REGIME":
            regimes.append(r)
        elif typ == "DECISION":
            action = str(r.get("action", "UNKNOWN"))
            reason = str(r.get("reason", "UNKNOWN"))
            book = r.get("book_id")
            actions[action] += 1
            reasons[reason] += 1
            archetypes[str(r.get("archetype", "UNKNOWN"))] += 1
            tiers[str(r.get("tier", "UNKNOWN"))] += 1
            arch_sources[str(r.get("archetype_source", "UNKNOWN"))] += 1
            bybook[book][reason] += 1
        elif typ == "ORDER_LIFECYCLE":
            ph = str(r.get("phase", "UNKNOWN")).upper()
            book = r.get("book_id")
            events[f"LIFECYCLE:{ph}"] += 1
            if ph == "SUBMITTED":
                submit[book] += 1
            if "TRADE" in ph or "FILL" in ph:
                fills[book] += 1
            if "REJECT" in ph or "FAIL" in ph:
                rejects[book] += 1
        elif typ == "POSITION":
            transition = str(r.get("transition", "UNKNOWN")).upper()
            book = r.get("book_id")
            transitions[transition] += 1
            if bool(r.get("round_trip")) or transition == "FLAT":
                roundtrip_books[book] += 1
        elif typ == "TIMING":
            req += 1
            try:
                totals.append(float(r.get("total_ms")))
            except (TypeError, ValueError):
                pass
            try:
                responds.append(float(r.get("respond_ms")))
            except (TypeError, ValueError):
                pass
            if int(r.get("instructions", 0) or 0) == 0:
                zero += 1

    print("Strategy1_Research V2 diagnosis")
    print("=" * 78)
    print(f"file: {path}")
    print(f"malformed: {malformed}")
    print(f"sampled requests: {req}")
    if req:
        print(f"zero-instruction requests: {zero}/{req} ({100 * zero / req:.1f}%)")
    print(
        "latency total ms: "
        f"p50={fmt(pct(totals, .50))} p95={fmt(pct(totals, .95))} "
        f"max={fmt(max(totals) if totals else None)}"
    )
    print(
        "latency respond ms: "
        f"p50={fmt(pct(responds, .50))} p95={fmt(pct(responds, .95))} "
        f"max={fmt(max(responds) if responds else None)}"
    )

    if configs:
        c = configs[-1]
        print("\nPolicy:")
        for key in (
            "policy_version", "run_id", "inactive_bootstrap",
            "bootstrap_dead_as_mm", "fix_inventory_util",
            "fix_quote_reservation", "bootstrap_manage_min_clip",
            "bootstrap_force_close_ticks", "bootstrap_hard_close_ticks",
            "dust_safe_close", "sync_min_order", "output_file",
        ):
            print(f"  {key}: {c.get(key)}")

    if regimes:
        r = regimes[-1]
        print("\nLatest regime:")
        for key in (
            "mode", "overlay", "book_count", "active", "inactive",
            "spread_med", "spread_p90", "stress_spread_bps",
            "toxic_spread_bps", "trade_rate_med", "low_trade_ratio",
            "liquid_ratio", "stressed_ratio",
        ):
            print(f"  {key}: {r.get(key)}")

    print("\nArchetypes:")
    for k, v in archetypes.most_common(args.top):
        print(f"{v:8d}  {k}")

    print("\nArchetype sources:")
    for k, v in arch_sources.most_common(args.top):
        print(f"{v:8d}  {k}")

    print("\nTiers:")
    for k, v in tiers.most_common(args.top):
        print(f"{v:8d}  {k}")

    print("\nTop reasons:")
    for k, v in reasons.most_common(args.top):
        print(f"{v:8d}  {k}")

    print("\nPosition transitions:")
    for k, v in transitions.most_common(args.top):
        print(f"{v:8d}  {k}")
    print(f"confirmed round-trip closes: {sum(roundtrip_books.values())}")
    if roundtrip_books:
        print("round-trip books: " + ", ".join(
            f"{book}:{count}" for book, count in roundtrip_books.most_common(args.top)
        ))

    print("\nBooks:")
    for book, c in sorted(bybook.items(), key=lambda kv: -sum(kv[1].values()))[:args.top]:
        top = ", ".join(f"{k}:{v}" for k, v in c.most_common(4))
        print(
            f"book={str(book):>4} decisions={sum(c.values()):6d} "
            f"submitted={submit[book]:5d} fills={fills[book]:5d} "
            f"rejects={rejects[book]:5d} roundtrips={roundtrip_books[book]:4d} "
            f"top=[{top}]"
        )

    print("\nLifecycle:")
    lifecycle = ((k, v) for k, v in events.items() if k.startswith("LIFECYCLE:"))
    for k, v in sorted(lifecycle, key=lambda kv: (-kv[1], kv[0]))[:args.top]:
        print(f"{v:8d}  {k}")


if __name__ == "__main__":
    main()
