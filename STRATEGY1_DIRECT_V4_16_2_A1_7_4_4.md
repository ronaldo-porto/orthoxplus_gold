# Strategy1-Direct V4.16.2 A1.7.4.4 — Positive-Maker Kappa Risk Veto

A1.7.4.4 is a narrow economic-risk correction on the mechanically verified
A1.7.4.3.2 candidate.

## Runtime evidence motivating the patch

The A1.7.4.3.2 Agent67 run kept the ownership/global-cap fixes healthy and the
Maker engine exceptionally strong, but all observed Taker-ending round trips
were negative. The risk-authority Takers were `HARD_ESCAPE_CLIP` or
`ABSOLUTE_PROTECTION_REDUCE`; they were not catastrophic/MAX-exposure events,
and the observed Maker completion remained strongly positive before crossing.

## Authority change

Only when all of the following are true:

1. the selected action is a risk-authority Taker (`HARD_ESCAPE_CLIP` or
   `ABSOLUTE_PROTECTION_REDUCE`);
2. Taker completion is negative;
3. Maker completion is executable and at least **+10 bps**;
4. `catastrophic_hard_risk == false`;

A1.7.4.4 converts the decision to a Maker exit and ignores failed-exit
escalation for that decision.

Catastrophic/MAX-exposure protection always bypasses the veto. Marginal Maker
values below +10 bps retain the frozen A1.7.2/A1.7.4 behavior. Recovery Taker
logic is unchanged.

## New telemetry

- `A1744_POSITIVE_MAKER_RISK_VETO`
- `A1744_TAKER_ALLOWED_CATASTROPHIC`
- `A1744_TAKER_ALLOWED_MAKER_NOT_STRONG`
- `A1744_VETO_RELEASE`

## Frozen mechanics

A1.7.4.3.2 exchange-order identity release, A1.7.4.3 global in-flight exposure
reservation, A1.7.4.2 dust Kappa guard, A1.7.4.1 TradeEvent replay de-dup,
A1.7.4 recovery thresholds, FastPath, 0.25 BASE sizing, and 2.0 BASE portfolio
cap are unchanged.
