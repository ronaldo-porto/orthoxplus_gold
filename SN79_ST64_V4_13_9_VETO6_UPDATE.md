# SN79 St6.4 — Adaptive V4.13.9 VETO6 Testnet Tuning

Emergency parameter-only experiment. No strategy Python logic changed.

Changed runtime parameter:

- `research_positive_maker_veto_max_failed_exits=3` → `6`

Applied consistently to the active V4.13.9 Research, Base, and Adaptive launchers and their V4.13.9 launcher snapshots.

Purpose: keep strongly positive Maker exits protected for up to six failed exit attempts before bounded liveness is allowed to release the Positive-Maker Veto. This specifically tests the Book8/Book10 failure shape observed in the V4.13.9 Adaptive continuation without introducing new runtime state or strategy complexity.

All other V4.13.9 behavior is frozen.

Verification:
- Core strategy Python hashes unchanged from verified V4.13.9.
- Active Research + Adaptive + promotion strategy tests: 552 passed.
- Research/Base/Adaptive launcher syntax: PASS.
- Research/Base/Adaptive preflight: PASS.

Testnet acceptance target for 100–150 ticks:
- positive RT ratio >= 60%;
- RT velocity >= 0.03/s;
- realized PnL > 0;
- Kappa eligible stable/rising;
- parked inventory does not grow sharply;
- absolute exposure preferably < 2.5/3.0;
- LANE_NOT_GRANTED = 0;
- repeated same-lifecycle contract reject loop = 0;
- placements/fill < 25.
