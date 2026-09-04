# Strategy1-Direct V4.16.2 A1.6.3 — Exposure Liveness Closure

A1.6.3 is a deterministic execution-liveness patch on top of A1.6.2. It does not change the observable Maker entry model, FastPath ranking, sizing, TTL, exit economics, or Taker-entry policy.

## Runtime defects closed

The A1.6.2 validation run showed that filled absolute BASE could exceed the 2.0 cap because unresolved orders were not reserved. Once over cap, the inherited final validator then rejected genuine risk-reducing exits with `EXPOSURE_HEADROOM`, freezing the portfolio. The same run also exposed same-book races between stale entry/exit/compaction orders.

## A1.6.3 changes

1. **Directional final exposure validation**
   - Uses signed inventory and signed order direction.
   - Risk-increasing batches must fit absolute BASE/open-book/active-book limits.
   - Risk-reducing orders remain legal even if the current portfolio is already above the absolute BASE cap.
   - Headroom is not assumed restored until a later state confirms the fill.

2. **In-flight exposure reservation**
   - Current account orders reserve worst-case absolute BASE before new-entry admission.
   - Symmetric BUY+SELL Maker pairs reserve the worst one-sided fill rather than incorrectly netting to zero.
   - Flat/dust books with live orders reserve productive open-book capacity when those orders can create a tradable position.

3. **One unresolved order batch per book**
   - If the latest account snapshot still exposes an open order, no new exposure-changing placement is allowed on that book.
   - A same-request cancel does not clear the guard; a later state must show the old order gone.

4. **Dust compaction isolation**
   - Dust compaction runs only when no older account order remains on the book.
   - Prevents an old exit plus a new compaction order from crossing the position and creating new exposure.

5. **Pure exposure math module**
   - `research_direct_exposure.py` contains deterministic, dependency-free worst-case reservation math.
   - No learned or predictive authority was added.

## Frozen behavior

- FastPath candidates: 20
- Deep candidates: 16
- Maker acquisition size: 0.25 BASE
- Absolute BASE cap: 2.0
- Productive total-open cap: 8
- Active-open cap: 6
- Maker touch improvement: 6 bps
- Maker TTL: 75 ms
- Directional Taker entry: OFF
- Observable Maker entry economics: unchanged
- NORMAL/DEFENSIVE exit logic: unchanged
- HARD_ESCAPE / ABSOLUTE protection: unchanged

## Verification

- A1.6.3 targeted exposure tests: 12/12 PASS
- Launcher preflight: 82/82 PASS
- Research regression: 523 PASS, 9 superseded historical contract tests SKIPPED
- `compileall`: PASS
- shell syntax: PASS

## Runtime gate

First run 500–1,000 ticks. Required evidence:

- no sustained over-cap freeze;
- risk-reducing exits survive while over cap;
- `EXPOSURE_HEADROOM` applies only to risk-increasing placements;
- no repeated same-book stale-order stacking;
- dust compaction no longer races another live order;
- RT velocity > 0.08/s, target > 0.10/s;
- positive RT >= 60%;
- Maker→Maker positive >= 85%;
- p95 latency < 120 ms.

If that gate passes, continue the same binary to 2,000–4,000 ticks before any persistent-Maker or larger-sizing work.
