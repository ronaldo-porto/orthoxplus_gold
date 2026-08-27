# SN79 St6.4 — V4.13.8 Base + Adaptive Promotion

## Status

Research `simplified_kappa_productivity_v4_13_8` is frozen and promoted through the required chain:

`Strategy1_Research V4.13.8 -> BaseStrategy V4.13.8 champion -> AdaptiveAgent V4.13.8 realtime`

## BaseStrategy

Policy: `base_v4_13_8_champion`

BaseStrategy is a frozen production wrapper over the exact verified V4.13.8 Research engine. It does not reimplement the engine, avoiding behavioral drift during the emergency sprint.

Promoted contracts include:
- authoritative completion/coverage lane propagation;
- Positive-Maker exit authority and Maker grace;
- density scheduler and deep-EV completion prefilter;
- qualified-Core exact-min recycle and Core STALE TTL rescue;
- profitable Maker-exit persistence / queue-priority hold;
- all existing hard Score-EV, inventory, post-only, contract, volume and rescue safety.

The Base launcher carries the same V4.13.8 Testnet parameter surface, including 6 active quote books and all density/Core/exit-persistence flags.

## AdaptiveAgent

Version: `adaptive_v4_13_8_realtime`

The old Adaptive V3 was rebased because it referenced pre-V4.13 Base output maps and disabled Kappa completion during OBSERVE. The realtime promotion now:

- consumes `_research_hazard_last`, `_research_score_ev_last`, `_research_market_regime`, `_research_score_regime`, and current Research microstructure outputs;
- keeps Base rolling Kappa state authoritative; Adaptive session memory cannot inflate eligibility;
- never disables Base Kappa completion/density policy during OBSERVE or BOOTSTRAP;
- preserves Base activity up to 6 MM books / 10 managed books; only DRIFT may reduce intensity;
- uses fast phases: OBSERVE 100 requests, NORMAL after 400 requests;
- retains bounded Adaptive EV / regime / size / rank / HJB overlays; raw HJB prices remain disabled;
- retains persistent environment-isolated adaptive memory.

## Verification

- Python compile: PASS.
- Base launcher bash syntax: PASS.
- Base V4.13.8 preflight: PASS.
- Adaptive launcher bash syntax: PASS.
- Adaptive realtime preflight: PASS.
- Research + Adaptive focused regression: **540 passed**.
- Versioned Base and Adaptive snapshots frozen.

## Next live gate

Run AdaptiveAgent on Testnet for roughly 150–250 ticks first. Emergency promotion gates:

- positive RT ratio >=65%;
- realized PnL >0;
- Kappa eligible stable/rising;
- RT velocity >=0.012 initially, preferably >=0.015;
- no regression in `LANE_NOT_GRANTED` or negative-Taker-over-positive-Maker behavior;
- parked inventory does not explode;
- placements/fill <25;
- Adaptive telemetry confirms OBSERVE -> BOOTSTRAP transition without disabling Kappa completion.

If these hold, package the same tree as `ADAPTIVE_REALNET_CANDIDATE_1` and deploy one miner first.
