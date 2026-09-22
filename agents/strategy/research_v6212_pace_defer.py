"""v6.2.12: a held book defers its pace step, it does not discard the sample.

v6.2.11 R2 re-seeds a held book's pace sample on every request, and ``observed_rate`` needs a whole
sampling interval (600 sim-s) since that seed, so a book that is never flat for 600 uninterrupted
seconds never steps at all.  At the v6.2.11.1 tick-3,000 read (UID 67, 2026-09-23 04:00 JST) that was
every book: all 128 clips still at the 0.25 minimum, ``pace_ratio_p50`` null and ``pace_held``
300,517, with 3 books flat, 40 holding dust below one minimum order and 85 holding one working clip.
The cap counts every second a book trades, held or flat, so the sample has to keep running; what kept
the v6.2.5 martingale out is that the STEP waits for the book to be flat, and that is what this build
keeps: a held book still quotes its adding side at one minimum order and still never steps.
"""
from typing import Any

V6212_PACE_DEFER_VERSION = "pace_defer_v6_2_12"


def sample_after_hold(*, defer: Any, sampled_ns: Any, volume: Any, now_ns: Any, used: Any) -> tuple:
    """This book's pace sample after a held request.

    Deferred (v6.2.12): the running sample is kept, so the interval that decides the next clip spans
    the held time as the cap does.  Otherwise (v6.2.11): held time is discarded and the sample starts
    again at this request.
    """
    if bool(defer):
        return sampled_ns, volume
    return now_ns, used
