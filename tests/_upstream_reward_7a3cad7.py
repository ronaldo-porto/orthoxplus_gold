# SPDX-License-Identifier: MIT
"""The validator's track-record EMA, VERBATIM from taos-im/sn-79 main commit 7a3cad7
("20260921 - 0.6.1 final rung", taos/im/validator/reward.py).  The oracle for the v6.2.11 standing
projection.  Do not edit: copy again from upstream.
"""
from typing import Dict


def apply_track_record_ema(scores: Dict, all_uids, deregs, ts: int, halflife: int,
                           ema: Dict, ema_n: Dict, last_ts, scorable=None):
    """Age-annealed track-record EMA on the trading score, applied BEFORE floor+Pareto.

    Standing is earned over multiple periods rather than from a single window, the way real
    allocators assess performance: multi-period track records / deferred compensation, so a
    single strong window converts into standing only gradually. alpha derives from
    the sim-time gap (cadence-independent half-life); the age-annealed floor
    alpha_eff = max(alpha_dt, 1/(k+1)) makes a young miner's standing track its live
    performance inside the immunity window (k=1 puts >=50% weight on the new window),
    converging to the half-life EMA as a track record accumulates, with every window
    counting from the start.

    Mutates `ema`/`ema_n` in place (per-UID value and application count); returns
    (smoothed_scores, new_last_ts). Deregistered UIDs are reset so a new occupant of the
    slot starts a fresh track record. Shared by main (get_rewards) and the scoring
    shadow/cutover child (shadow_score) so both sides stay in exact parity.

    Args:
        scores: This round's trading scores, mutated toward the EMA.
        all_uids: Uids the vector is aligned to.
        deregs: Uids deregistered this round (their standing resets).
        ts: Round timestamp.
        halflife: EMA halflife in seconds.
        ema: Carried EMA state.
        ema_n: Carried per-uid observation counts.
        last_ts: Carried per-uid last-update timestamps.
        scorable: Which uids are scorable this round.

    Returns:
        The age-annealed scores.
    """
    # Deregistered/vacant slots (uid in deregs until re-registration: the exchange engine
    # appends on dereg, removes on re-register) are excluded from the EMA entirely: cleared here
    # AND skipped in the update below, so the slot stays empty (no value, k=0) through its
    # vacancy. Otherwise a vacant slot would keep accruing k on neutral scores, and a miner
    # later registering at the reused slot would inherit a large k -> a tiny annealing alpha ->
    # under-scored through its immunity window (newcomer protection defeated). With this, the
    # re-registered miner's first scored round seeds at k=0 = full protection.
    dereg = set(deregs or [])
    for uid in dereg:
        ema.pop(uid, None)
        ema_n.pop(uid, None)
    # Simulation-boundary handling. Scores are NOT reset on a new sim run (by design —
    # scoring is continuous across runs): the assessment window spans the boundary until the
    # new run exceeds the lookback, then narrows to the new run (see shift_simulation_histories,
    # which rebases old-run history timestamps into the negative region). The EMA state persists
    # the same way. But simulation_timestamp itself RESETS to ~0 at the seam (on_start:
    # new_simulation_timestamp = 0), so a naive `ts > last_ts` guard would freeze the EMA for a
    # whole sim-day and the time-based alpha (ts-last_ts) would go negative. Detect the seam
    # (ts < last_ts) and treat it as a normal continuous step: drop the time term (alpha_dt=0 →
    # each UID updates at its annealing weight 1/(k+1), so established miners barely move and a
    # young miner stays responsive), keep the persisted values, and rebase the clock. This keeps
    # scoring continuous across the boundary rather than skipping the seam round.
    seam = last_ts is not None and ts < last_ts
    if last_ts is None or ts > last_ts or seam:
        if seam or last_ts is None:
            alpha_dt = 0.0 if seam else 1.0
        else:
            alpha_dt = 1.0 - 0.5 ** ((ts - last_ts) / halflife)
        for uid in all_uids:
            if uid in dereg:
                continue
            cur = scores[uid]
            # Newcomer warmup: skip the annealing-counter advance while a UID's incoming
            # score is still 0 AND it has never been scored (ema_n 0). This subsumes BOTH
            # warmup cases: a UID inside its min_lookback window (no valid Kappa yet =>
            # score 0) AND a scorable-but-poor newcomer whose early Kappa normalizes to 0.
            # In either case advancing k would burn the 1/(k+1) newcomer protection before
            # the first REAL (nonzero) score, leaving the standing to crawl up from 0 across
            # the whole immunity window while the live Kappa is already high => an
            # excellent-but-young miner floored below median and culled. Seeding instead at
            # the first nonzero score (k=0 => alpha 1 => standing = live score) gives a
            # genuine newcomer its real standing immediately. The guard is ema_n 0: once a
            # UID has been scored it always updates, so an established miner going silent
            # still decays toward 0, so the multi-period property is preserved. A skipped round
            # earns nothing, and a seeded standing requires a genuine nonzero Kappa that decays
            # away unless it is sustained. (The `scorable` arg is retained for
            # signature/caller stability; the score-based guard supersedes it.)
            if cur == 0 and ema_n.get(uid, 0) == 0:
                continue
            k = ema_n.get(uid, 0)
            alpha = max(alpha_dt, 1.0 / (k + 1.0))
            ema[uid] = alpha * cur + (1.0 - alpha) * ema.get(uid, cur)
            ema_n[uid] = k + 1
        last_ts = ts
    return {uid: ema.get(uid, scores[uid]) for uid in all_uids}, last_ts
