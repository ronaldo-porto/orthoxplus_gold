# Strategy1-Direct V4.16.2 A1.3 — Agent-68 Runtime-History Patch

## Scope

A1.3 is a narrow continuation of A1.2 based only on Agent-68's A1.2 runtime history. UID239 remains observation-only; no inventory-reservoir, micro-harvest, score-freeze, or fee-rebate behavior from UID239 is implemented in this branch.

## A1.2 evidence that drives A1.3

A1.2 solved entry participation and reduced Maker->Taker conversion from ~71.8% to ~56.8%, but the run still showed:

- 164 completed RTs, 68 positive / 96 negative (~41.5% positive)
- closed-RT PnL about -4.60 QUOTE
- profitable Maker->Maker path: 63 RT, ~73% positive, +5.99 QUOTE
- destructive Maker->Taker path: 83 RT, ~19% positive, -10.35 QUOTE
- fresh/moderately-passive Maker fills were strongly better than stale/aggressive fills
- profitable observed region: quote age <~75 ms and touch improvement no more than ~6 bps
- eight sub-minimum dust books totaling only ~0.085 BASE saturated the total-open cap and stopped all new entries after tick ~1007
- zero-sample Maker-quality books were the weakest cohort; quality improved materially after repeated lifecycle observations

## A1.3 changes

### 1. Dust-capacity liveness

Sub-minimum inventory (`flat_epsilon < abs(net_base) < exchange_min_order`) remains real inventory and still counts toward aggregate absolute BASE risk. It no longer consumes productive total-open book capacity up to a bounded dust exemption of 8 books.

A1.3 also stops repeatedly sending untradeable dust to PositionExitController, preventing impossible `<0.25` reduction attempts and their INVALID_QTY churn.

The active-book cap and aggregate BASE cap are unchanged and remain authoritative. The inherited final contract validator is still used; A1.3 only adjusts its total-open book-count cap for bounded untradeable dust.

### 2. Maker execution freshness

Direct Maker GTT is capped at 75 ms:

- baseline `mm_expiry_period_ns` can remain 500 ms
- Direct A1.3 uses `min(base_ttl, 75 ms)`

This is based on Agent-68 A1.2 history where fills below roughly 75 ms were materially more productive and stale fills were strongly adverse-selected.

### 3. Maker quote aggressiveness

A1.3 caps inside-touch improvement at 6 bps per side. Quotes that are already more passive than touch are not pulled inward.

The purpose is to prevent the high-loss region observed when Direct Maker chased ~9–15+ bps inside the spread. Final post-only sanitization remains authoritative.

### 4. Hierarchical + session-persistent Maker-quality learning

A1.2 book-specific Maker lifecycle learning is retained, but A1.3 adds:

- weak cold-start Maker->Taker prior = 0.55
- prior strength = 4 pseudo-observations
- current-run global Maker lifecycle evidence blended into the prior
- book-specific evidence gradually takes control
- Direct Maker-quality state is stored in the existing simulation session JSON and restored on miner reload within the same simulation
- simulation/session reset clears Direct quality state, preventing stale cross-simulation execution behavior

The prior does not create a hard gate and does not create a drift penalty without observed adverse drift.

## Intentionally unchanged

- `Strategy1.py`
- V4.16.2 `Strategy1_Research.py`
- Direct A1.2 Maker/Taker economics (`research_direct_economics.py`)
- Maker minimum EV margin = 0.030
- Taker directional model
- Kappa ranking / TotalScore target
- PositionExitController
- latency hard penalty remains OFF
- duplicate adverse hard penalty remains OFF
- Kappa cannot subsidize negative Taker entries
- BaseStrategy / AdaptiveAgent
- validator trading/scoring code

## New telemetry

`DIRECT_MAKER_GEOMETRY` records:

- raw/capped bid and ask prices
- raw/capped touch improvement bps
- Direct Maker TTL
- execution-quality version

`SIMPLE_CONFIG` now records:

- `maker_max_touch_improvement_bps=6.0`
- `maker_max_ttl_ms=75.0`
- `dust_exempt_cap=8`
- `cold_start_taker_rate=0.55`

Direct session restoration emits `DIRECT_QUALITY_RESTORE`.

## Validation

- A1.3 direct tests: 28/28 PASS
- A1.3 + V4.16 focused preflight: 98/98 PASS
- all `tests/test_research_*.py`: 470/470 PASS
- Python compile: PASS
- launcher preflight: PASS

The repository-wide suite cannot be fully collected in this sandbox because optional runtime dependencies such as `bittensor` and `loky` are unavailable.

## Next runtime gates

A1.3 should be judged primarily on:

1. no recurrence of dust-induced global inactivity
2. Maker fill age distribution concentrated below 75 ms
3. touch improvement concentrated at <=6 bps
4. Maker->Taker rate below A1.2's ~56.8%
5. positive RT ratio >50%, preferably >55%
6. realized PnL >=0 and preferably clearly positive
7. continued growth from ONE_AWAY/TWO_AWAY to qualified books
8. INVALID_QTY / exposure-headroom rejects sharply lower
9. Maker-quality state restores after same-simulation miner restart

