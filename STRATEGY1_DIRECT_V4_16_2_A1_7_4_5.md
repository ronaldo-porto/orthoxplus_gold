# Strategy1-Direct V4.16.2 A1.7.4.5 — Quiet / Zero-Rebate Entry Quality

A1.7.4.5 is a narrow acquisition-quality correction on top of the mechanically
proven A1.7.4.3.2 ownership path and the A1.7.4.4 positive-Maker Kappa veto.

## Runtime evidence motivating the patch

After the Testnet simulator changed, the environment became predominantly QUIET:
Maker rebate was approximately zero, median spread widened to roughly 31.5 bps,
and median recent trade rate was approximately zero.  In that session the
strategy still admitted marginal Maker entries under the frozen A1.6 2.5 bps
edge floor.  Maker-ending round trips remained positive, but many positions
aged until Maker completion deteriorated and risk authority eventually crossed
as a negative Taker.  RT velocity also fell sharply.

Retrospective filtering of that session showed that an entry edge around 15 bps
improved the positive-RT mix while retaining materially more opportunities than
an aggressive global shutdown.  The patch therefore does **not** globally
retune Maker entry.

## Authority change

The normal A1.6 observable Maker edge floor remains **2.5 bps**.

Only when all four live conditions are true:

1. market regime is `QUIET`;
2. current signed Maker fee is at least **-1.0 bps** (no meaningful rebate);
3. current book trade rate is at most **0.10**;
4. current spread is at least **20 bps**;

A1.7.4.5 raises the effective Maker entry edge floor to **15 bps**.

This means a 14 bps Maker opportunity is skipped in the target regime but is
still admitted under the frozen 2.5 bps authority in non-QUIET, rebate-present,
higher-activity, or narrower-spread regimes.

## New telemetry

- `A1745_QUIET_ZERO_REBATE_GATE`
- `A1745_ENTRY_BLOCK_LOW_EDGE`
- `A1745_ENTRY_ALLOWED`
- `A1745_REGIME_BYPASS`

`ENTRY_DECISION` now also carries the A1.7.4.5 gate inputs and effective floor.

## Frozen mechanics and economics

Unchanged from A1.7.4.4:

- A1.7.4.4 positive-Maker Kappa veto;
- A1.7.4.3.2 exact exchange-order identity-safe ownership release;
- A1.7.4.3 strict in-flight 2.0 BASE aggregate exposure reservation;
- A1.7.4.2 dust Kappa guard;
- A1.7.4.1 own-TradeEvent replay de-duplication;
- A1.7.4 recovery thresholds and emergency Taker authority;
- A1.7.2 TRUE-WAIT execution semantics;
- FastPath candidate selection;
- Maker size **0.25 BASE**;
- directional Taker entry remains disabled.

There is no new learned state and no global Maker edge retune.
