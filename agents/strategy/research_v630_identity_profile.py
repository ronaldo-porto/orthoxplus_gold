# SPDX-License-Identifier: MIT
"""v6.30: the identity profile -- each UID draws its own per-book profile from its OWN UID, so our UIDs behave as
independent, different agents instead of one: D1 research_v630_deep_tier, D2 research_v630_s9_lookback.

Why (mainnet 10-14 JST, all six of our UIDs on v6.29; scratchpad k79/overlap an79*, k79/critic_vf static79; memory
fleet-overlap-identity-1014):

* Identical code on identical inputs places identical orders: the agent has no random-number generator and every UID
  ships the same PARAMS, so our UIDs rest on the same book, side and price -- 91-97% of resting L1 orders shared with
  another of ours, 87.5-89.7% of the older UIDs' deep order-states (1.61 effective agents of 6 at L1); 45% of the
  taker groups that fill us hit two or more of ours; S9's same-state co-withdrawal is 59-74% (base rate 8%).
* The user's directive (10-14 ~04:40 JST): every UID acts as a different agent -- by independent behaviour, never by
  coordination.  Approved spec 10-14 ~06:40 JST: D1 + D2, each its own default-on switch.

The profile (PRE-REGISTERED 10-14 06:08 JST before any replay output: scratchpad k79/spec/profile_v1.py, sha256
31d08ace5b99b5e3ac33e567a06ad08ef914d0edae275e7168e2246ee264168c; frozen -- any change is a new version string):

    value = VALUES[dim][ int(SHA-256('orthox-profile-v1|' + dim + '|' + str(uid) + '|' + str(book)).hexdigest()[:8], 16)
                         mod k ]

    D1  the deep-ladder tier, in extra venue ticks: (0, 1)         research_v630_deep_tier
    D2  the S9 look-back multiple of the S6 limit W: (1, 2)       research_v630_s9_lookback

D3-D6 of the spec are not built.  Index 0 of each value set is v6.29's behaviour.

Independence (STRUCTURAL -- the track's binding rules):

* The only inputs are this agent's OWN UID, the book id, the dimension name and the frozen version string.  There is no
  list, count or index of our UIDs anywhere, no reading of another agent's orders, fills, client ids or files, no host
  cache, lock, socket or IPC (this module imports only the standard library's hashlib, math and operator, typing and
  v6.3.3's deep layer -- none of which loads fcntl, a socket or a cache), and no runtime RNG.  Draws of different UIDs
  are independent: two UIDs share a book's value with probability 1/k, and nothing aligns them.
* The UID is the one the agent was constructed with (taos SimulationAgent.__init__ sets self.uid before initialize();
  the miner passes metagraph.hotkeys.index(its own hotkey), -1 when that hotkey is not in the metagraph) -- fixed ONCE
  per process: at init, else at the first request from self.uid, else from the state's addressee (the validator gives
  each miner's state {its uid: its account}: taos/im/validator/query.py create_axon_synapse).  Unknown or negative:
  the profile stays off.  A later request whose self.uid or addressee is another UID (or self.uid no longer a UID)
  turns it off for the rest of the process.  Off, both switches run v6.29, and the state row says why.
* The table (book -> D1, D2) is drawn once, at the first request, for every book of that state; a book first seen later
  is drawn once when it appears; no value is ever drawn again or changed.  Its digest is logged (V6_30_STATE):
  sha256 of "\\n".join(["orthox-profile-v1|uid=<uid>"] + ["<book>|D1=<d1>|D2=<d2>" for each book ascending]) --
  reproducible offline from the spec alone (table_text / table_digest below).

D1 (research_v630_deep_tier): on a tier-1 book every DEEP level targets exactly ONE venue tick further from the mid
than v6.29 would -- level 0 (the vacuum order in a blown-out spread included), the v6.12 reducing half-depth order,
the ladder's levels 1 and 2, the target v6.6's spacing judges, and the v6.8 paper record that gates the layer
(TierPaper), so the gate judges the level the book trades.  Never shallower: the moved price is kept only while it
stays inside v6.3.3's keep band, [REPRICE_LOW, REPRICE_HIGH] x the depth the order is judged against, around the
reference it is judged against (the mid; v6.16's centre where one applies), with a margin: a moved price ON the band's
outer edge (REPRICE_HIGH x depth, to 1e-9 tick) would be judged by its rounding noise, so it is clamped too (the float
noise of a distance in ticks is ~4e-16 x price / tick: under 1e-9 tick up to price / tick ~ 2e6, SN79's 300 / 0.01 being
3e4; real distances step by half a tick) -- and, for a vacuum order, strictly inside the touch as v6.4 places it;
otherwise the order keeps v6.29's price (clamped).  The tick is the venue's: 10 ** -price
decimals, as the pass derives it.  Resting orders are judged by v6.29's band unchanged, so they move at their normal
reprice -- no cancel storm.  The touch quote, the S5 lean, pace, caps and S9 are unchanged.

D2 (research_v630_s9_lookback): on a book with multiple m, S9's look-back is W_book = m x W, W being exactly v6.29's
(this request's S6 limit, v6.16's MAX_STALE_SIM_S without one); m = 1 is v6.29.  The reference rule is v6.29's,
unchanged (user-confirmed 10-14 ~06:40 JST): the newest page this agent FIRST SAW at or before (state time - W_book);
the rewind guard and the new-simulation resets are v6.29's own (the history is v6.29's PageHistory with KeepHorizon
mixed in).  v6.29's lookup drops every page older than the reference it returns; with two look-backs in one request
that would drop the page a 2W book needs after a W lookup, so the history keeps what the longest look-back in use needs:
keep_s = max m x W, set once per request and never shrinking while the profile is on (KeepHorizon.hold: a W that
shrinks and grows back still finds its pages; an errored request leaves it as it was), None -- v6.29's prune -- once
the profile is off.  The first growth of W past what was ever held cannot bring back a page already dropped (no
history keeps pages for a look-back it has not seen): that request has no reference, so no gate -- v6.29's own case
at W.

STRUCTURAL: the venue tick and v6.3.3's band (D1), S6's limit (D2), the hash (independence).  Design choices: k = 2 and
D1's deeper-only direction (its reason OBSERVED: v6.7's grid, the shallower p90 lost 24% against p97); D2's m = 2 is
the smallest integer change of the horizon and coincides with k71's OBSERVED 60-s optimum -- OBSERVED unless the user
takes the design-choice reading.  The value of the profile is identity and fairness; its pay value is about zero for
the fleet (pay is linear in shares) -- k75/decor_final.txt.
"""
from __future__ import annotations

import hashlib
import math
import operator
from typing import Any, Iterable

from research_v633_deep_layer import REPRICE_HIGH, SIDE_BUY, SIDE_SELL, PaperDeep, needs_reprice

V6_30_IDENTITY_PROFILE_VERSION = "identity_profile_v6_30"
PROFILE_VERSION = "orthox-profile-v1"            # the pre-registered spec's version string (frozen)
D1, D2 = "D1", "D2"
VALUES = {D1: (0, 1), D2: (1, 2)}                # the pre-registered value sets; index 0 is v6.29's behaviour

# Where the UID came from, and why the profile is off for the rest of the process.
SOURCE_AGENT, SOURCE_STATE = "agent_uid", "state_addressee"
OFF_UNKNOWN, OFF_NEGATIVE, OFF_CHANGED = "uid_unknown", "uid_negative", "uid_changed"
# Where a deep target ends (D1): one tick deeper, or v6.29's price -- the band (or its edge) or the touch would not hold
# it, or an input could not be read.
DEEPER, CLAMPED_BAND, CLAMPED_TOUCH, UNREADABLE = "deeper", "band", "touch", "unreadable"

__all__ = ["CLAMPED_BAND", "CLAMPED_TOUCH", "D1", "D2", "DEEPER", "Identity", "KeepHorizon", "OFF_CHANGED",
           "OFF_NEGATIVE", "OFF_UNKNOWN", "PROFILE_VERSION", "SOURCE_AGENT", "SOURCE_STATE", "TierPaper", "UNREADABLE",
           "V6_30_IDENTITY_PROFILE_VERSION", "VALUES", "addressee", "deeper_price", "deeper_target", "draw",
           "page_history", "paper_for", "profile_table", "table_digest", "table_text", "uid_of"]


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def _ns(value: Any) -> int | None:
    """A state time in ns: an int (not a bool), or None (v6.29's reading)."""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _integer(value: Any) -> int | None:
    """An integer of an integer type (not a bool, not a float or a text), or None."""
    if isinstance(value, bool):
        return None
    try:
        return operator.index(value)
    except Exception:
        return None


def uid_of(value: Any) -> int | None:
    """A UID: a non-negative integer of an integer type, else None."""
    u = _integer(value)
    return u if u is not None and u >= 0 else None


def _negative(value: Any) -> bool:
    u = _integer(value)
    return u is not None and u < 0


def addressee(accounts: Any) -> int | None:
    """The UID the validator addressed this state to: the only key of the state's accounts map (query.py gives each
    miner {its uid: its account}); None when the map is absent, empty or holds several keys."""
    if not isinstance(accounts, dict) or len(accounts) != 1:
        return None
    return uid_of(next(iter(accounts)))


def draw(dim: str, uid: int, book: int) -> int:
    """The pre-registered draw: VALUES[dim][int(SHA-256('orthox-profile-v1|dim|uid|book')[:8], 16) mod k]."""
    values = VALUES[dim]
    h = hashlib.sha256(f"{PROFILE_VERSION}|{dim}|{uid}|{book}".encode()).hexdigest()
    return values[int(h[:8], 16) % len(values)]


def profile_table(uid: int, books: Iterable[int]) -> dict[int, tuple[int, int]]:
    """book -> (D1 tier, D2 multiple) for this UID."""
    return {int(b): (draw(D1, uid, int(b)), draw(D2, uid, int(b))) for b in books}


def table_text(uid: int, table: dict) -> str:
    """The canonical text the logged digest is taken over (one line per book, ascending)."""
    lines = [f"{PROFILE_VERSION}|uid={uid}"]
    lines += [f"{book}|D1={d1}|D2={d2}" for book, (d1, d2) in sorted(table.items())]
    return "\n".join(lines)


def table_digest(uid: int, table: dict) -> str:
    return hashlib.sha256(table_text(uid, table).encode()).hexdigest()


class Identity:
    """This agent's identity profile: its UID, fixed once per process, the per-book table drawn from it once, and why
    the profile is off when it is.  Pure: it never raises and reads nothing but the values it is handed."""

    def __init__(self) -> None:
        self.uid: int | None = None
        self.source: str | None = None
        self.off: str | None = None               # why the profile is off for the rest of the process
        self.table: dict[int, tuple[int, int]] = {}
        self.digest: str | None = None
        self.max_m = 1
        self.extended = 0                         # books drawn after the first request's table

    def fix(self, agent_uid: Any) -> None:
        """At init: the UID the agent was constructed with.  Unknown: decided at the first request instead."""
        if self.uid is not None or self.off is not None:
            return
        if _negative(agent_uid):
            self.off = OFF_NEGATIVE
            return
        u = uid_of(agent_uid)
        if u is not None:
            self.uid, self.source = u, SOURCE_AGENT

    def check(self, agent_uid: Any, state_uid: Any) -> bool:
        """Once per request, before the books: True when the profile applies.  A UID not fixed yet is this request's
        self.uid, else the state's addressee (none: off, unknown; negative: off).  A fixed UID must still be self.uid
        (when the agent had it) and the addressee (when the state names one); otherwise the profile turns off for the
        rest of the process."""
        if self.off is not None:
            return False
        if self.uid is None:
            self.fix(agent_uid)
            if self.off is not None:
                return False
            if self.uid is None:
                s = uid_of(state_uid)
                if s is None:
                    self.off = OFF_UNKNOWN
                    return False
                self.uid, self.source = s, SOURCE_STATE
        if agent_uid is not None and uid_of(agent_uid) != self.uid:
            self.off = OFF_CHANGED
            return False
        if agent_uid is None and self.source == SOURCE_AGENT:
            self.off = OFF_CHANGED
            return False
        s = uid_of(state_uid)
        if s is not None and s != self.uid:
            self.off = OFF_CHANGED
            return False
        return True

    def extend(self, book_ids: Iterable[Any]) -> int:
        """Draws every book of ``book_ids`` not yet in the table -- once each, never again.  Returns how many."""
        if self.uid is None or self.off is not None:
            return 0
        first = not self.table
        added = 0
        for raw in book_ids or ():
            b = _integer(raw)
            if b is None:
                try:
                    b = int(str(raw))
                except (TypeError, ValueError):
                    continue
            if b < 0 or b in self.table:
                continue
            self.table[b] = (draw(D1, self.uid, b), draw(D2, self.uid, b))
            added += 1
        if added:
            if not first:
                self.extended += added
            self.digest = table_digest(self.uid, self.table)
            self.max_m = max(m for _d1, m in self.table.values())
        return added

    def value(self, dim: str, book: Any) -> int | None:
        """The book's drawn value of ``dim``, or None (no UID, off, or a book without a draw)."""
        if self.uid is None or self.off is not None:
            return None
        b = _integer(book)
        row = self.table.get(b) if b is not None else None
        if row is None:
            return None
        return row[0] if dim == D1 else row[1] if dim == D2 else None

    def snapshot(self) -> dict[str, Any]:
        books = sorted(self.table)
        return {"uid": self.uid, "uid_source": self.source, "off_reason": self.off, "books": len(books),
                "book_min": books[0] if books else None, "book_max": books[-1] if books else None,
                "table_sha256": self.digest, "d1_tier1_books": sum(1 for b in books if self.table[b][0] == 1),
                "d2_m2_books": sum(1 for b in books if self.table[b][1] == 2), "max_m": self.max_m,
                "d1": "".join(str(self.table[b][0]) for b in books), "d2": "".join(str(self.table[b][1]) for b in books),
                "books_drawn_later": self.extended}


# ---- D1: one venue tick deeper ------------------------------------------------------------------------------------

def deeper_target(price: Any, side: Any, *, ref: Any, depth: Any, tick: Any, decimals: Any, ticks: Any = 1,
                  inside: tuple[Any, Any] | None = None) -> tuple[Any, str]:
    """(target, why): ``price`` moved ``ticks`` venue ticks further from the mid on its side (a buy lower, a sell
    higher), DEEPER -- kept only while the moved price is inside v6.3.3's keep band (needs_reprice: [REPRICE_LOW,
    REPRICE_HIGH] x ``depth`` from ``ref``, the reference its resting order is judged against) and off its outer edge:
    at REPRICE_HIGH x depth (to 1e-9 tick) the rounding noise of the two prices would decide, so the edge clamps too
    (CLAMPED_BAND); with ``inside`` = (bid, ask) (a vacuum order) also strictly inside the touch where v6.4 places it
    (else CLAMPED_TOUCH).  Clamped, or anything unreadable (UNREADABLE): ``price`` unchanged -- never shallower.  Never
    raises."""
    try:
        k = _integer(ticks)
        p, r, d, t = _finite(price), _finite(ref), _finite(depth), _finite(tick)
        if k is None or k <= 0 or p is None or r is None or d is None or t is None or t <= 0.0 or d < 0.0:
            return price, UNREADABLE
        dec = _integer(decimals)
        if dec is None or dec < 0:
            return price, UNREADABLE
        if side == SIDE_BUY:
            q = round(p - k * t, dec)
        elif side == SIDE_SELL:
            q = round(p + k * t, dec)
        else:
            return price, UNREADABLE
        if needs_reprice(q, r, d, t):
            return price, CLAMPED_BAND                  # +ticks would leave the keep band: v6.29's price
        if abs(q - r) / t > REPRICE_HIGH * d - 1e-9:
            return price, CLAMPED_BAND                  # on the band's edge: rounding noise would decide -- clamped
        if inside is not None:
            lo, hi = _finite(inside[0]), _finite(inside[1])
            if lo is None or hi is None:
                return price, UNREADABLE
            eps = 1e-6 * t
            if side == SIDE_BUY and q < round(lo + t, dec) - eps:
                return price, CLAMPED_TOUCH             # a vacuum bid stays strictly inside the touch
            if side == SIDE_SELL and q > round(hi - t, dec) + eps:
                return price, CLAMPED_TOUCH
        return q, DEEPER
    except Exception:
        return price, UNREADABLE


def deeper_price(price: Any, side: Any, *, ref: Any, depth: Any, tick: Any, decimals: Any, ticks: Any = 1,
                 inside: tuple[Any, Any] | None = None) -> Any:
    """deeper_target's price alone (the paper record's use)."""
    return deeper_target(price, side, ref=ref, depth=depth, tick=tick, decimals=decimals, ticks=ticks, inside=inside)[0]


class TierPaper(PaperDeep):
    """v6.3.3's paper record of a tier-1 book: each paper order it (re)places rests ``ticks`` venue ticks further from
    the mid, where the live level 0 goes (deeper_price around the mid, against the paper's own depth); everything else
    -- the fills, inventory, buckets, alpha and v6.8's saved record -- is PaperDeep's."""

    __slots__ = ("ticks",)

    def __init__(self, ticks: int = 1) -> None:
        super().__init__()
        self.ticks = ticks

    def place(self, mid: float, depth: float, *, bid: float, ask: float, tick: float, decimals: int) -> None:
        rebid = self.bid is None or needs_reprice(self.bid, mid, depth, tick)
        reask = self.ask is None or needs_reprice(self.ask, mid, depth, tick)
        super().place(mid, depth, bid=bid, ask=ask, tick=tick, decimals=decimals)
        if rebid and self.bid is not None:
            self.bid = deeper_price(self.bid, SIDE_BUY, ref=mid, depth=depth, tick=tick, decimals=decimals,
                                    ticks=self.ticks)
        if reask and self.ask is not None:
            self.ask = deeper_price(self.ask, SIDE_SELL, ref=mid, depth=depth, tick=tick, decimals=decimals,
                                    ticks=self.ticks)


def paper_for(paper: Any, ticks: Any) -> Any:
    """The paper record a book of this tier keeps: a TierPaper (``ticks`` 1) or v6.3.3's plain PaperDeep (``ticks``
    0) carrying the same orders, fills, inventory, last print and buckets.  ``paper`` itself when it already is that
    kind, or when it is not a paper record at all."""
    if not isinstance(paper, PaperDeep):
        return paper
    k = _integer(ticks)
    k = k if k is not None and k > 0 else 0
    if k:
        if isinstance(paper, TierPaper) and paper.ticks == k:
            return paper
        new = TierPaper(k)
    else:
        if type(paper) is PaperDeep:
            return paper
        new = PaperDeep()
    for name in PaperDeep.__slots__:
        setattr(new, name, getattr(paper, name))
    return new


# ---- D2: the history keeps what the longest look-back needs ----------------------------------------------------------

class KeepHorizon:
    """Mixed into v6.29's PageHistory (research_v6_29_touch_move; page_history below): reference(now, lookback) returns
    exactly v6.29's page -- the newest one first seen at or before now - lookback -- but drops only the pages older
    than the reference of the longest look-back in use, ``keep_s``.  ``keep_s`` None: v6.29's own prune.  The rest
    (observe, its rewind guard and resets, decide) is v6.29's, inherited."""

    def __init__(self) -> None:
        super().__init__()
        self.keep_s: float | None = None

    def hold(self, keep_s: Any) -> None:
        """The request's longest look-back, held: keep_s never shrinks while the agent keeps calling this (the max of
        what it held and ``keep_s``), so a look-back that shrinks and grows back finds the pages it needs again.
        Anything unreadable or negative leaves it as it is."""
        k = _finite(keep_s)
        if k is None or k < 0.0:
            return
        held = _finite(getattr(self, "keep_s", None))
        self.keep_s = k if held is None else max(held, k)

    def reference(self, now_ns: Any, lookback_s: Any):
        keep = getattr(self, "keep_s", None)
        if keep is None:
            return super().reference(now_ns, lookback_s)
        now, w, k = _ns(now_ns), _finite(lookback_s), _finite(keep)
        if now is None or w is None or w < 0.0:
            return None
        if k is None or k < w:
            k = w                                       # never drop the lookup's own reference
        cut, keep_cut = now - w * 1e9, now - k * 1e9
        pages = self.pages
        ref = None
        for i in range(len(pages) - 1, -1, -1):
            if pages[i][0] <= cut:
                ref = i
                break
        if ref is None:
            return None
        for j in range(ref, -1, -1):
            if pages[j][0] <= keep_cut:
                del pages[:j]
                return pages[ref - j]
        return pages[ref]


_HISTORIES: dict = {}


def page_history(base: type) -> Any:
    """A new, empty v6.29 page history (``base``: research_v6_29_touch_move.PageHistory, handed in by the agent) with
    KeepHorizon mixed in.  The class is built once per base."""
    cls = _HISTORIES.get(base)
    if cls is None:
        cls = type("ProfilePageHistory", (KeepHorizon, base), {"__module__": __name__, "__doc__": KeepHorizon.__doc__})
        _HISTORIES[base] = cls
    return cls()
