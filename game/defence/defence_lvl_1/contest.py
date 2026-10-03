"""contest.py — THE FIRST-CLASS CONTEST OBJECT (Stage 2B, 2026-09-20).

WHAT THIS FILE IS
-----------------
The PURE DATA of the Stage 2B declaration payload.  It declares:

  * ``Contest`` — THE canonical declared contest: the single object a
    declaration produces and the single object that crosses the TES boundary.
  * ``winner_of`` — the ONE consumption point.  TES reads the winning
    defender through this function and reads nothing else off the Contest.

WHY THE CONTEST IS THE DECLARATION PAYLOAD
------------------------------------------
Stage 1B made contest declaration owned; Stage 2A made it complete; Stage 2B
makes it transportable.  Before this stage the thing that crossed the TES
boundary was a bare winning defender, and the complete formation state lived
only in Stage 2A's ``ContestRecord``.

Stage 2B promotes that state to ONE canonical payload assembled at the moment
of declaration by Contact Authority, and derives the historical record FROM
it.  One declaration therefore never produces two separately-populated
representations of the same facts — the divergence class this removes cannot
exist.

    Contest        LIVE simulation state: what contest currently exists.
    ContestRecord  HISTORICAL artefact: what happened.

The record is a projection of the payload; the payload is not a projection of
the record.

WHAT TES MAY CONSUME
--------------------
Exactly one field: ``winner``.  Everything else the payload carries — the
evaluated defender set, the in-radius defender set, the admission channel,
the declaration frame, the authority sequence number — is advisory and
unused by TES, whose behaviour is therefore identical to the stage before
this one.

The payload is NOT a new authority.  Contact Authority remains the sole
authority responsible for determining whether a contest exists and who wins
it; the Contest is a transport object carrying that decision.

READ-PURITY
-----------
This module takes no decision, forms no contest and selects no winner.  It
performs no attribute store on any object: publication is ``setattr`` on the
world only, so no body and no engine subsystem can be perturbed by a
declaration.  It has NO project imports and NO engine reach — it is the shape
of the payload and the single accessor TES is allowed to use.

NOT GATED
---------
``RLM_CONTEST_PRESERVATION`` governs the RECORDER (Stage 2A), and turning it
off is how 2A proves the recorder is a pure read.  The Contest is the
TRANSPORT, so it is declared unconditionally: a detached recorder must never
be able to remove the object TES is handed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CONTEST_ID_PREFIX",
    "CONTEST_ATTR",
    "CONTEST_RING_LIMIT",
    "Contest",
    "is_contest",
    "winner_of",
    "contest_id_for",
    "build_contest",
    "publish_contest",
    "live_contest",
    "recent_contests",
    "reset_contest",
    "format_contest_report",
    "declared_facts",
]

# ── THE IDENTITY SCHEME ──
# A contest identifier is derived ONLY from facts already known at declaration
# time — the declaration frame and the authority sequence number — so an
# identifier can never require a measurement the declaration did not take.
CONTEST_ID_PREFIX: str = "contest"

# ── THE PUBLISHED SURFACE ──
# Written with ``setattr`` on the WORLD only.  This is the live-contest
# surface, the counterpart of Stage 2A's ``contest_record``: that attribute
# answers "what happened", this one answers "what contest currently exists".
CONTEST_ATTR: str = "contest"

# The in-memory ring of the most recent payloads (never consumed by
# behaviour; read by probes so two declarations in ONE frame are not missed).
CONTEST_RING_LIMIT: int = 600
_CONTEST_RING: list = []


# ─────────────────────────────────────────────────────────────────────
# THE CANONICAL DECLARED CONTEST
# ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Contest:
    """THE canonical declared contest — the single declaration payload.

    Every field is a fact ALREADY KNOWN at declaration time.  The payload is
    assembled once, by Contact Authority, and then:

      * handed to TES, which consumes only ``winner``; and
      * projected into the historical ``ContestRecord``, which copies these
        facts instead of re-deriving them.

    A Contest exists only where a contest was actually declared: Contact
    Authority forms one on ``CONTACT_FORMED`` and never on ``CONTACT_FAILED``,
    so a failed frame declares no contest and has no payload to diverge from.
    """

    # ── the declared facts ──
    contest_id: str
    frame: int
    seq: int
    phase: str
    channel: str
    outcome: str
    reason: str

    # ── the winner ──
    winner_id: int

    # ── the declared membership (a SET; the record's views derive from it) ──
    evaluated_ids: tuple = field(default=())
    in_radius_ids: tuple = field(default=())

    # ── the declared geometry and effects ──
    radius_m: float = 0.0
    arm_grab_damping: bool = False
    glancing_displacement: float | None = None

    # ── THE ONE FIELD TES MAY CONSUME ──
    # The winning defender, carried across the boundary in place of a
    # standalone winning defender identifier.
    winner: Any = None

    @property
    def player_id(self) -> int:
        """The winning defender's identifier.

        Exposed under the name the engine's single TES entry reads when it
        identifies the primary tackler, so the boundary's shape is unchanged:
        the entry still identifies the winner by ``player_id`` — it simply
        reads it off the Contest now.
        """
        return self.winner_id

    @property
    def formed(self) -> bool:
        """True when the payload carries a declared contest with a defender."""
        return self.winner_id is not None and self.winner is not None

    def carries(self, player_id: int) -> bool:
        """True when *player_id* is in the DECLARED in-radius set."""
        return int(player_id) in self.in_radius_ids

    def decided_about(self, player_id: int) -> bool:
        """True when the declaration took a contest decision about *player_id*."""
        return int(player_id) in self.evaluated_ids


# ─────────────────────────────────────────────────────────────────────
# THE ONE CONSUMPTION POINT
# ─────────────────────────────────────────────────────────────────────

def is_contest(value: Any) -> bool:
    """True when *value* is a Contest payload (never a bare defender)."""
    return isinstance(value, Contest)


def winner_of(value: Any) -> Any:
    """THE winning defender *value* carries — the single consumption point.

    For a Contest this is ``contest.winner``.  For anything else it is the
    value itself: the engine keeps an unreachable historical copy of the
    defence dispatch that still calls the TES entry with a bare defender, and
    a detached caller may do the same.  Returning the value unchanged keeps
    that path byte-identical, so the pre-Stage-2B behaviour is preserved
    rather than special-cased.
    """
    if isinstance(value, Contest):
        return value.winner
    return value


# ─────────────────────────────────────────────────────────────────────
# ASSEMBLY
# ─────────────────────────────────────────────────────────────────────

def contest_id_for(frame: int, seq: int) -> str:
    """The contest identifier for a declaration (frame + authority sequence)."""
    return "%s-%d-%d" % (CONTEST_ID_PREFIX, int(frame), int(seq))


def build_contest(*, frame: int, seq: int, phase: str, channel: str,
                  outcome: str, reason: str, winner: Any, winner_id: int,
                  evaluated_ids=(), in_radius_ids=(), radius_m: float = 0.0,
                  arm_grab_damping: bool = False,
                  glancing_displacement: float | None = None) -> Contest:
    """Assemble THE canonical declaration payload.

    A pure constructor: it reads nothing from the world and writes nothing to
    any object.  The caller supplies facts it already knows — that is the
    whole point of assembling the payload AT the declaration.
    """
    return Contest(
        contest_id=contest_id_for(frame, seq),
        frame=int(frame),
        seq=int(seq),
        phase=str(phase),
        channel=str(channel),
        outcome=str(outcome),
        reason=str(reason),
        winner_id=int(winner_id),
        evaluated_ids=tuple(int(pid) for pid in evaluated_ids),
        in_radius_ids=tuple(int(pid) for pid in in_radius_ids),
        radius_m=float(radius_m),
        arm_grab_damping=bool(arm_grab_damping),
        glancing_displacement=glancing_displacement,
        winner=winner,
    )


# ─────────────────────────────────────────────────────────────────────
# PUBLICATION (setattr on the world only)
# ─────────────────────────────────────────────────────────────────────

def publish_contest(world, contest: Contest) -> Contest:
    """Publish *contest* as the LIVE contest on *world* and ring it.

    Publication is ``setattr`` on the world ONLY, so no body, no engine
    subsystem and no authority state can be perturbed by declaring a contest.
    The optional in-memory ring exists so a probe cannot miss a payload when
    two declarations land in one frame; nothing in the live engine reads it.
    """
    if world is None or contest is None:
        return contest
    try:
        setattr(world, CONTEST_ATTR, contest)
    except Exception:
        pass

    _CONTEST_RING.append(contest)
    if len(_CONTEST_RING) > CONTEST_RING_LIMIT:
        del _CONTEST_RING[:-CONTEST_RING_LIMIT]

    return contest


def live_contest(world) -> Contest | None:
    """The live contest *world* currently has, or ``None``."""
    return getattr(world, CONTEST_ATTR, None) if world is not None else None


def recent_contests() -> tuple:
    """The in-memory payload ring (most recent last).  Probe/audit only."""
    return tuple(_CONTEST_RING)


def reset_contest(world=None) -> None:
    """Drop the payload ledger (kickoff / fresh sequence).

    Clears the ring and, when *world* is given, the published surface.  It
    introduces no engine state and is read by nothing in the live loop.
    """
    global _CONTEST_RING
    _CONTEST_RING = []
    if world is None:
        return
    try:
        delattr(world, CONTEST_ATTR)
    except AttributeError:
        pass


def format_contest_report(world) -> str:
    """Compact one-line rendering of the live contest."""
    contest = live_contest(world)
    if contest is None:
        return "contest=no_declaration"
    return (
        "contest=%s frame=%d seq=%d channel=%s winner=%s "
        "evaluated=%d in_radius=%d"
        % (contest.contest_id, contest.frame, contest.seq, contest.channel,
           contest.winner_id, len(contest.evaluated_ids),
           len(contest.in_radius_ids))
    )


def declared_facts() -> tuple:
    """The DECLARED fact contract: what a Contest is assembled from.

    Declarative only — nothing reads this at run time.  It exists so the
    payload's contract is stated in code as well as in the gate.
    """
    return (
        ("contest_id", "the contest identifier"),
        ("winner_id", "the winner identifier"),
        ("evaluated_ids", "the evaluated defender set"),
        ("in_radius_ids", "the in-radius defender set"),
        ("channel", "the admission channel"),
        ("frame", "the declaration frame"),
        ("seq", "the authority sequence number"),
        ("winner", "the winning defender TES consumes"),
    )
