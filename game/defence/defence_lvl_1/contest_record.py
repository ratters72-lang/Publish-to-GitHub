"""contest_record.py — THE COMPLETE CONTEST RECORD (Stage 2A, 2026-09-20).

WHAT THIS FILE IS
-----------------
The PURE DATA of the Stage 2A preservation layer.  It declares:

  * ``CandidateDisposition`` — the closed vocabulary that says, for every body
    present in a formation event, WHAT THE AUTHORITY DID WITH IT;
  * ``CandidateEvidence``   — one formation-state row per body;
  * ``CarrierSnapshot``     — the carrier's state at the moment of declaration;
  * ``ContestRecord``       — THE COMPLETE RECORD: winner, evaluated bodies,
    in-radius candidates, formation evidence, admission channel, frame,
    sequence and decision outcome, all on one object.

WHY THE DISPOSITIONS ARE A SEPARATE VOCABULARY
----------------------------------------------
Stage 1B's ``ContactFailureReason`` is the vocabulary of THE DECIDER: it names
the conjunct at which a candidate stopped being a contest candidate.  It can
only describe a body the decider actually EVALUATED.

Stage 2A exists precisely because some bodies are NEVER EVALUATED.  Stage 1B's
decider short-circuits on the first satisfying candidate (``break``), so every
other in-radius body in that frame is discarded before the authority transfer —
and the engage channels form contests whose evaluating predicate lives outside
the authority, so they publish NO per-body data at all.  A body the authority
never reached cannot honestly be described by the decider's vocabulary.

Stage 2A therefore declares its OWN closed vocabulary, and the decider's stays
untouched.  That is deliberate: adding a member to ``ContactFailureReason``
would have changed the "which conjunct stopped this body" contract that
Stage 1B's differential-equivalence matrix and reason-coverage probe are built
on.  Preservation ADDS a layer; it does not re-word the one underneath it.

THE CONTAINMENT LAWS (checked by verify_contest_preservation_contract.py)
-----------------------------------------------------------------------
  L1  every body row carries a disposition from the closed vocabulary;
  L2  a FORMED record has exactly one winner row; its disposition is a member
      of ``WINNER_DISPOSITIONS`` and it is present in ``record.evaluated``.
      A record whose ``decider`` is the authority's OWN decider additionally
      has its winner in ``record.in_radius``, because the decider's radius
      conjunct is what admitted it.  A record admitted by a CHANNEL PREDICATE
      is NOT bound by that: the admitting predicate is not the decider and
      need not share its contact radius — the engage gate admits on a wider
      radius than the decider's contact radius — so an engage winner may sit
      legitimately outside ``in_radius``.  ``admission_predicate`` names the
      predicate that admitted it, so the geometry is interpretable;
  L3  the views and the counts agree with the rows:
      ``candidate_count == len(candidates)``,
      ``evaluated_count == len(evaluated)`` and
      ``in_radius_count == len(in_radius)``;
  L4  ``in_radius`` is GEOMETRY ONLY — ``dist < radius_m``, measured against
      the radius declared by the authority — and is otherwise independent of
      the disposition.  Exactly two dispositions imply geometry:
      ``IN_RADIUS_NOT_EVALUATED`` implies ``in_radius`` is True, and
      ``ROLE_HOLDER_OUT_OF_RADIUS`` implies it is False;
  L5  ``record.un_evaluated_in_radius`` is exactly the information Stage 1B
      lost: the in-radius bodies the authority never evaluated.  It holds the
      ``IN_RADIUS_NOT_EVALUATED`` rows and nothing else.

  NOT A LAW, and the reason this block was rewritten: ``record.evaluated`` is
  NOT a subset of ``record.in_radius``.  The decider evaluates a role holder
  that is OUTSIDE the radius in order to report ``out_of_radius`` as the
  reason it failed, so an evaluated body can legitimately sit outside the
  radius.  An earlier draft of this file asserted the subset relation; the
  Stage 2A guard falsified it against the live engine, and the law was
  corrected rather than the measurement.  Geometry and decision are different
  facts and this record keeps them apart.

This module has NO project imports and NO engine reach.  It reads nothing,
writes nothing and decides nothing; it is the shape of the record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "CandidateDisposition",
    "EVALUATED_DISPOSITIONS",
    "WINNER_DISPOSITIONS",
    "IN_RADIUS_ONLY_DISPOSITION",
    "OUT_OF_RADIUS_DISPOSITION",
    "CarrierSnapshot",
    "CandidateEvidence",
    "ContestRecord",
]


# ─────────────────────────────────────────────────────────────────────
# THE CLOSED DISPOSITION VOCABULARY
# ─────────────────────────────────────────────────────────────────────

class CandidateDisposition(Enum):
    """WHAT THE AUTHORITY DID WITH A BODY, at the moment of declaration.

    EVALUATED_WINNER
        The decider evaluated this body and it FORMED the contest.  One per
        record, at most.

    EVALUATED_REJECTED
        The decider evaluated this body and a conjunct rejected it (MISS /
        ARM_GRAB / GLANCING / out of radius).  ``failed_at`` names the
        conjunct, in the decider's own vocabulary.

    IN_RADIUS_NOT_EVALUATED
        The body was INSIDE the contact radius at the moment of declaration
        and the authority NEVER EVALUATED IT.  This is the short-circuit: the
        decider's scan stopped on an earlier winner.  IT IS THE INFORMATION
        STAGE 1B LOST, and it is the single most important row Stage 2A
        preserves.

    ADMITTED_BY_CHANNEL_PREDICATE
        The contest was admitted by a behaviour-layer predicate that lives
        OUTSIDE the decider (``try_pre_tackle`` ->
        ``pretackle_machine.should_engage``) and this body is the one it
        handed to the authority transfer.  The decider did not evaluate it;
        the declared admission predicate did.  ``admission_predicate`` on the
        record names that predicate.

    ROLE_HOLDER_OUT_OF_RADIUS
        The body held a tackle role but was OUTSIDE the contact radius, so the
        radius conjunct — not the role gate — was what excluded it.  Recorded
        so "close but not close enough" is measurable rather than assumed.

    NOT_A_CANDIDATE
        The body was examined and was never in the running: no tackle role, or
        a fullback without tackle authority.  ``failed_at`` names which.
    """

    EVALUATED_WINNER = "evaluated_winner"
    EVALUATED_REJECTED = "evaluated_rejected"
    IN_RADIUS_NOT_EVALUATED = "in_radius_not_evaluated"
    ADMITTED_BY_CHANNEL_PREDICATE = "admitted_by_channel_predicate"
    ROLE_HOLDER_OUT_OF_RADIUS = "role_holder_out_of_radius"
    NOT_A_CANDIDATE = "not_a_candidate"


# The dispositions that mean "the authority (or the declared admission
# predicate) put this body through a contest decision".
EVALUATED_DISPOSITIONS: frozenset = frozenset({
    CandidateDisposition.EVALUATED_WINNER,
    CandidateDisposition.EVALUATED_REJECTED,
    CandidateDisposition.ADMITTED_BY_CHANNEL_PREDICATE,
})

# The ONE disposition that IMPLIES the body was inside the contact radius.  A
# body can only be described as "never evaluated" if it was in radius to begin
# with, so this disposition carries geometric information on its own.
#
# The converse does NOT hold, and that is deliberate: a body can be inside the
# radius while carrying any other disposition — notably NOT_A_CANDIDATE, for a
# body that stands within the radius but holds no tackle role, and
# EVALUATED_REJECTED for a role holder the decider evaluated and turned down.
# Radius is geometry; the disposition says what was done about the body.
IN_RADIUS_ONLY_DISPOSITION: CandidateDisposition = (
    CandidateDisposition.IN_RADIUS_NOT_EVALUATED
)

# The dispositions that mark the ONE body the contest was declared on.
WINNER_DISPOSITIONS: frozenset = frozenset({
    CandidateDisposition.EVALUATED_WINNER,
    CandidateDisposition.ADMITTED_BY_CHANNEL_PREDICATE,
})

# The single disposition that means "held a tackle role, but outside the
# declared contact radius".
OUT_OF_RADIUS_DISPOSITION: CandidateDisposition = (
    CandidateDisposition.ROLE_HOLDER_OUT_OF_RADIUS
)


# ─────────────────────────────────────────────────────────────────────
# THE CARRIER, AT THE MOMENT OF DECLARATION
# ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CarrierSnapshot:
    """The carrier's measured state when the contest was declared.

    Recorded so a future stage can ask what the carrier was DOING at
    declaration — position and velocity — without reconstructing it from a
    frame log.  Nothing in the live engine reads it.

    THE TWO CARRIER SKILL-TRAIT PLACEHOLDERS (Stage 3.1)
    ---------------------------------------------------
    ``fend_trait`` and ``tackle_break_trait`` are the carrier's ESCAPE
    multipliers — a Fend or Tackle-Break rating expressed as a percentage
    multiplier on its baseline momentum.  Nothing factors them in yet: they
    are declared here so the record carries the slots a later stage consumes,
    and they are NEUTRAL today.

    NEUTRAL IS THE IDENTITY OF A MULTIPLIER, ``1.0``, and it is produced by
    the EXTRACTOR rather than by the dataclass: this leaf's law is that every
    ``CarrierSnapshot`` field is MEASURED, never defaulted, so no field
    default is declared.  ``of()`` reads each trait off the body when the body
    supplies one and reports ``1.0`` when it does not — and no body currently
    supplies either, so every snapshot taken from the live engine reads
    exactly 1.0 and this pair cannot move a frame.
    """

    player_id: int
    x: float
    y: float
    vx: float
    vy: float
    fend_trait: float
    tackle_break_trait: float

    @classmethod
    def of(cls, bc) -> "CarrierSnapshot":
        """Snapshot *bc* (or an all-zero, id-less snapshot when bc is None).

        The two trait placeholders are extracted exactly like the kinematics
        — ``float(getattr(...) or neutral)`` — so a body that declares no
        trait, or declares a non-positive one, reports the NEUTRAL multiplier
        ``1.0`` and never a value that would silently scale a later stage's
        momentum term to zero.
        """
        if bc is None:
            return cls(player_id=-1, x=0.0, y=0.0, vx=0.0, vy=0.0,
                       fend_trait=1.0, tackle_break_trait=1.0)
        return cls(
            player_id=int(getattr(bc, "player_id", -1) or -1),
            x=float(getattr(bc, "x", 0.0) or 0.0),
            y=float(getattr(bc, "y", 0.0) or 0.0),
            vx=float(getattr(bc, "vx", 0.0) or 0.0),
            vy=float(getattr(bc, "vy", 0.0) or 0.0),
            fend_trait=float(getattr(bc, "fend_trait", 1.0) or 1.0),
            tackle_break_trait=float(
                getattr(bc, "tackle_break_trait", 1.0) or 1.0
            ),
        )


# ─────────────────────────────────────────────────────────────────────
# ONE BODY'S FORMATION STATE
# ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CandidateEvidence:
    """One body's formation state at the moment the contest was declared.

    Every field is either a value the preservation layer itself measured (the
    distance, the radius test) or a stamp published by one of the five
    declared evidence providers, read through the pure predicates Stage 1B
    already consumes.  Nothing here is inferred.

    THE TWO BODY SKILL-TRAIT PLACEHOLDERS (Stage 3.1)
    ------------------------------------------------
    ``tackle_skill_trait`` and ``core_strength_trait`` are the DEFENDER's
    contribution multipliers — an Effective Tackle or Core Strength rating
    expressed as a percentage multiplier on the body's wrapping resistance, so
    a highly skilled but lighter defender can be modelled punching above its
    weight class.  Nothing factors them in yet: they are declared here so the
    row carries the slots a later stage consumes, and they are NEUTRAL today.

    They are appended LAST, after ``is_winner``, so every field a Stage 2A
    reader already addresses keeps its position.  As on ``CarrierSnapshot``,
    neutral is the identity of a multiplier — ``1.0`` — and it is produced by
    the ROW BUILDER in ``contest_preservation.py``, not by the dataclass: this
    leaf's law is that a body row is MEASURED, never defaulted.
    """

    player_id: int
    defender_role: str
    tackle_role: str | None
    depth_class: str | None
    dist: float
    in_radius: bool
    disposition: CandidateDisposition
    # The DECIDER's conjunct name when it evaluated this body (its own
    # vocabulary, e.g. "score_miss"), else None: an honest "the decider never
    # described this body".
    failed_at: str | None
    predicate_verdict: str | None
    pre_tackle_state: str | None
    corridor_owned: bool
    corridor_frames: int
    slot_eligible: bool | None
    engage_guard: bool
    engage_guard_stamped: bool
    engage_state_last: str | None
    retreat_obligated: bool
    is_winner: bool
    tackle_skill_trait: float
    core_strength_trait: float

    @property
    def evaluated(self) -> bool:
        """True when a contest decision was taken about this body."""
        return self.disposition in EVALUATED_DISPOSITIONS

    @property
    def never_evaluated(self) -> bool:
        """True for an in-radius body the authority never reached."""
        return self.disposition is (
            CandidateDisposition.IN_RADIUS_NOT_EVALUATED
        )


# ─────────────────────────────────────────────────────────────────────
# THE COMPLETE RECORD
# ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ContestRecord:
    """THE COMPLETE FORMATION STATE at the moment a contest was declared.

    This is the object Stage 1B's boundary could not produce: it carries the
    winner AND every evaluated body AND every in-radius candidate, with the
    admission channel, the frame, the sequence number and the decision
    outcome, so that no future stage has to reconstruct a multi-body formation
    event from a single winning defender id.

    ``candidates`` holds one row per body examined in the frame's defender
    population.  The three views the contract names are derived from it, so
    they can never disagree with it:

        record.winner          the body the contest was declared on
        record.evaluated       every body a contest decision was taken about
        record.in_radius       every body inside the contact radius
    """

    # ── the decision ──
    frame: int
    seq: int
    phase: str
    outcome: str
    reason: str

    # ── the admission ──
    channel: str
    admission_predicate: str
    admission_site: str
    decider: str

    # ── the winner ──
    winner_id: int | None

    # ── the geometry ──
    radius_m: float
    candidate_count: int
    role_holder_count: int
    in_radius_count: int
    evaluated_count: int

    # ── the bodies ──
    candidates: tuple = field(default=())

    # ── the carrier, at declaration ──
    # The declared fallback is the all-zero, id-less snapshot — the SAME value
    # ``CarrierSnapshot.of(None)`` produces.  It is spelled out rather than
    # passed as ``default_factory=CarrierSnapshot`` because every
    # ``CarrierSnapshot`` field is measured, never defaulted, so the bare class
    # cannot be constructed with no arguments: the factory raised the moment a
    # consumer built a record without an explicit carrier.  The schema is
    # unchanged (SEVEN required fields, no defaults); only the fallback VALUE
    # is now constructible, and it carries the SAME neutral trait placeholders
    # ``CarrierSnapshot.of(None)`` produces — 1.0, the identity of a
    # percentage multiplier.
    carrier: CarrierSnapshot = field(
        default_factory=lambda: CarrierSnapshot(
            player_id=-1, x=0.0, y=0.0, vx=0.0, vy=0.0,
            fend_trait=1.0, tackle_break_trait=1.0,
        )
    )

    # ── the failed-contact effects the decision applied ──
    arm_grab_damping: bool = False
    glancing_displacement: float | None = None

    # ── provenance ──
    population_source: str = "none"
    record_index: int = 0

    # ── derived views ──

    @property
    def formed(self) -> bool:
        """True when this record describes a DECLARED contest."""
        return self.winner_id is not None

    @property
    def evaluated(self) -> tuple:
        """Every body a contest decision was taken about (winner included)."""
        return tuple(row for row in self.candidates if row.evaluated)

    @property
    def in_radius(self) -> tuple:
        """Every body inside the contact radius at declaration."""
        return tuple(row for row in self.candidates if row.in_radius)

    @property
    def un_evaluated_in_radius(self) -> tuple:
        """The in-radius bodies the authority NEVER evaluated.

        This is the precise quantity Stage 1B destroyed at the boundary.  A
        record with a non-empty value here is proof that the preservation
        layer did its job: that information no longer exists only in the
        frame's past.
        """
        return tuple(row for row in self.candidates if row.never_evaluated)

    @property
    def winner(self) -> CandidateEvidence | None:
        """The declared winner's row (or None)."""
        for row in self.candidates:
            if row.is_winner:
                return row
        return None

    @property
    def evaluated_ids(self) -> tuple:
        """The player ids of every evaluated body, in evaluation order."""
        return tuple(row.player_id for row in self.evaluated)

    @property
    def in_radius_ids(self) -> tuple:
        """The player ids of every in-radius body, in exam order."""
        return tuple(row.player_id for row in self.in_radius)

    @property
    def un_evaluated_in_radius_ids(self) -> tuple:
        """The player ids the boundary used to discard."""
        return tuple(row.player_id for row in self.un_evaluated_in_radius)

    def row_for(self, player_id: int) -> CandidateEvidence | None:
        """The row for *player_id* (or None when the body was not examined)."""
        for row in self.candidates:
            if row.player_id == player_id:
                return row
        return None
