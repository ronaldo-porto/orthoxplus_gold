# SN79 Three-Agent Deployment Verification

## Verdict

**Research V4.13: STATIC PASS / Testnet runtime validation pending.** BaseStrategy and AdaptiveAgent are unchanged.

## V4.13 Research objective

V4.13 simplifies the Research hot path around Kappa productivity. It keeps V4.12.18 inventory safety and replaces scheduler emphasis with explicit CORE recycling and execution-efficiency demotion.

Key contracts:
- Book115-like productive books continue after 3 observations as CORE candidates.
- Book98-like high placements/RT or reject-heavy books are demoted even with strong rebate/EV.
- Restored historical RTs cannot falsely create CORE without fresh V4.13 quote/fill evidence.
- all-book Stage-1 screening is top-of-book only; event parsing is deferred to selected/open books.
- persistent valid Maker orders survive alpha/OFI/regime-only changes.
- post-only safety uses a 2-tick default buffer.
- V4.12.18 protected parking and -8/-12 bps liveness rescue remain unchanged.

## Frozen components

Byte-for-byte unchanged from V4.12.18:
- `BaseStrategy.py`
- `AdaptiveAgent.py`
- `research_inventory_liveness.py`
- `research_contract_guard.py`
- `research_unified_exit.py`

## Regression

- Research: **431 passed**
- Base/Adaptive: **133 passed**
- Shared strategy: **90 passed**
- **654 passed / 0 failed**
- Focused V4.13: **11 passed**
- Python compile: PASS
- launcher `bash -n`: PASS
- `RESEARCH_PREFLIGHT_ONLY=1`: PASS

## First Testnet gate

Run roughly 600–900 simulation ticks. Do not promote until logs show:
- qualified productive books continue beyond 3 observations;
- CORE books receive repeated cycles instead of immediate rotation away;
- inefficient books no longer monopolize placements;
- placements/fill trends below 25 and placements/RT materially improve;
- contract rejection remains <0.5%;
- no V4.12.18 inventory deadlock regression;
- p95 latency moves materially toward <120 ms;
- acquisition breadth continues while CORE density grows.
