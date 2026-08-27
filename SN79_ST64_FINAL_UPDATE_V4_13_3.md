# SN79 St6.4 Research V4.13.3 — CORE_PROBE Lane Consistency

## Verdict that triggered this patch

The first V4.13.2 Testnet validation ran 311 ticks / ~27 minutes. Kappa quality improved sharply (4/5 positive RTs, +0.296 QUOTE, no one-tick negative Taker rescues), but CORE/RECYCLING stayed at zero and RT velocity fell to ~0.016/s.

The runtime log isolated one exact blocker: screening reserved one qualified fresh-UNKNOWN `CORE_PROBE` in `KAPPA_COMPLETION`, but execution recomputed the same already-Kappa-eligible book as `COVERAGE`. The book then failed the lane grant check as `LANE_NOT_GRANTED`.

## Only behavioral change

V4.13.3 preserves the selected CORE_PROBE's completion-lane authority through execution.

At execution:

```text
flat CORE_PROBE selected by screening
+ legacy completion predicate may be false because kappa.eligible == true
=> completion_candidate remains true
=> scheduler_lane remains KAPPA_COMPLETION
=> the book is checked against the completion grant it actually received
```

A small pure helper, `execution_completion_candidate()`, encodes this contract and is used by `Strategy1_Research._place_skewed_quotes()`.

This fixes only the screen -> execution identity mismatch. It does not broaden CORE_PROBE eligibility or add another probe slot.

## Frozen behavior

Unchanged from V4.13.2:
- fresh-position Maker Grace and its V4.12.18 rescue/parking precedence;
- exactly one CORE_PROBE candidate;
- minimum-size probe entry;
- first positive fresh RT -> existing RECYCLING bridge;
- first negative fresh RT -> demotion / no density privilege;
- Book92-like INEFFICIENT sink detection;
- Score-EV floors and alpha;
- lane budgets, sizing, inventory caps, persistent Maker, contract guard;
- active concurrency;
- ranking/latency path.

`fresh_maker_grace_v4_13_2` remains unchanged.

## Version

```text
RESEARCH_POLICY_VERSION = simplified_kappa_productivity_v4_13_3
KAPPA_PRODUCTIVITY_VERSION = simplified_kappa_productivity_v4_13_3
```

The productivity helper logic is unchanged apart from its release-version metadata.

## Regression coverage

The new regression reproduces the exact V4.13.2 mismatch:

```text
inventory_flat = true
core_probe_candidate = true
legacy_completion_candidate = false
=> execution completion candidate MUST be true
```

It also verifies that a normal non-probe/non-completion book remains false and an inventory book cannot become a completion entry.

## Verification

- focused V4.13.x lane/productivity tests: **32 passed / 0 failed**
- all Research tests: **445 passed / 0 failed**
- Python compile: PASS
- launcher shell syntax: PASS
- `RESEARCH_PREFLIGHT_ONLY=1` launcher contract: PASS
- active source vs V4.13.3 archive snapshots: PASS
- ZIP integrity: PASS

## Immediate Testnet step

Run V4.13.3 and inspect the first ~300 ticks. The first required runtime proof is no longer just `productivity_core_probe_books=1`; we must see the selected probe actually submit a `KAPPA_COMPLETION` Maker quote / fresh RT.

Continue toward 600–900 ticks only if:

```text
CORE_PROBE actual quote > 0
LANE_NOT_GRANTED on selected probe ~= 0
RECYCLING > 0 or clear fresh-probe progression
positive RT ratio > 60%
placements/fill < 15
contract rejects = 0
RT velocity recovering toward > 0.03/s
```
