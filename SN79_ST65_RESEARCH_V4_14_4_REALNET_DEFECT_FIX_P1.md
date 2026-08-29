# SN79 St6.5 Research V4.14.4 — RealNet Defect Fix P1

## Full-project baseline

Built directly from the exact uploaded `sn-79_St6.4_RESEARCH_v4.14.3_ENGINE_CLEANUP_P1` project.

- Previous policy: `wide_kappa_wave_v4_14_3`
- New policy: `realnet_authority_rotation_v4_14_4`
- Engine revision: `lean_engine_p1_realnet_fix_v4_14_4`

The V4.14.3 cleanup remains intact. This release intentionally changes only the two RealNet defects that cleanup P1 left unresolved, plus deployment/test wiring required to fail closed on the new modules.

## Fix 1 — Single non-catastrophic exit authority

V4.14.3 had overlapping authority between inventory-liveness rescue (historical `-8/-12` corridor) and the newer bounded-loss controller (`-8/-18/-25`). V4.14.4 makes one final arbiter authoritative for non-catastrophic bounded-loss decisions:

- `taker_net > -8 bps`: normal unified-exit economics remain authoritative.
- `-8 >= taker_net > -18 bps`: SOFT corridor.
  - Preserve an executable Maker exit when Maker net is at least `+1 bps` and the bounded profitable-Maker veto is still young.
  - Once the bounded veto expires / adverse evidence arms the escape, a bounded Taker recycle is allowed.
- `-18 >= taker_net >= -25 bps`: HARD_ESCAPE corridor.
  - Price crossing is sufficient authority.
  - Legacy `-12 bps` liveness floor, minimum-age gating, and positive-Maker veto cannot block it.
- `taker_net < -25 bps`: PARK; the normal bounded-loss path never becomes an unbounded market dump.
- Catastrophic hard-risk handling remains separate.

The arbiter is applied before old liveness side effects and again immediately before unified-exit persistence, preventing legacy stale-bridge/liveness logic from regaining final non-catastrophic authority.

Telemetry: `REALNET_EXIT_AUTHORITY`.

## Fix 2 — Scheduler retry rotation

Flat candidates hard-rejected as `NEGATIVE_EV`, `TOXIC`, or `AVOID` now enter a cross-tick retry quarantine before execution-lane allocation.

Default cooldowns:

- `NEGATIVE_EV`: 8 ticks
- `TOXIC`: 16 ticks
- `AVOID`: 16 ticks
- repeated unchanged rejection: exponential backoff, capped at 64 ticks

Safety/rotation behavior:

- Quarantined flat candidates do not consume COVERAGE/COMPLETION grants.
- Existing lane backfill can immediately use released capacity for fresh executable books.
- Inventory, dust, and hard-risk books are never blocked by this entry retry policy.
- A material EV/toxicity fingerprint change reopens a candidate immediately.
- A successful quote clears the book's backoff.
- Simulation/session transitions reset the entire retry quarantine so state cannot leak across runs.
- Wide-Kappa ranking weights and lane budgets remain unchanged.

Telemetry: `SCHEDULER_RETRY phase=HARD_REJECT|ROTATE_OUT`.

## Changed / added active files

- `agents/strategy/Strategy1_Research.py`
- `agents/strategy/research_realnet_exit_authority.py` (new)
- `agents/strategy/research_scheduler_retry.py` (new)
- `run_strategy1_research_test_multi.sh`
- 25 existing Research contract tests updated to the V4.14.4 policy identity
- `tests/test_research_v4_14_4_realnet_defects.py` (new)
- `tests/test_research_v4_14_4_integration_contract.py` (new)

Validator accounting, signal/alpha logic, Wide-Kappa ranking weights, Kappa observation targets, hard total-book/BASE caps, and catastrophic hard-risk behavior were not changed.

## Verification on the exact cleanup project

- Dedicated active Research regression: **509/509 PASS**
  - original cleanup suite: 496 tests
  - new V4.14.4 defect tests: +10
  - new V4.14.4 integration/deployment tests: +3
- Python compilation of active strategy files: PASS
- Active strategy tree compileall: PASS
- `run_strategy1_research_test_multi.sh` shell syntax: PASS
- `RESEARCH_PREFLIGHT_ONLY=1 ./run_strategy1_research_test_multi.sh`: PASS
- V4.14.4 helper preflight: PASS
  - `realnet_exit_authority_v4_14_4`
  - `scheduler_retry_rotation_v4_14_4`
- Validator source was not modified.

Repository-wide pytest is intentionally not the acceptance signal for this project because the baseline itself documents unrelated optional GenTRX/validator dependencies (`transformers`, `pyarrow`, `bittensor`, `loky`) and archived `__ver_st1_log__` test shadowing. The dedicated active Research suite is the cleanup lineage's acceptance gate.

## Runtime acceptance before RealNet promotion

Require Testnet logs to show:

1. `version=realnet_authority_rotation_v4_14_4`.
2. `REALNET_EXIT_AUTHORITY` keeps profitable young Maker exits in the soft corridor.
3. `-18..-25 bps` hard events cannot be blocked/parked by the legacy `-12 bps` liveness floor.
4. No normal bounded-loss Taker executes below `-25 bps`.
5. Repeated `NEGATIVE_EV/TOXIC/AVOID` candidates emit `SCHEDULER_RETRY ... ROTATE_OUT` and stop consuming lane grants during cooldown.
6. Fresh COVERAGE/COMPLETION books backfill released grants.
7. Inventory/risk exits are never suppressed by scheduler retry quarantine.
8. Retry quarantine clears across simulation/session transitions.
9. Validator accounting and hard-cap behavior remain unchanged.
