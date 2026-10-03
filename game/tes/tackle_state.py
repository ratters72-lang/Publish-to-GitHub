"""tackle_state.py — THE PERSISTENT TACKLE CONTEST (Stage 2, 2026-09-21).

THE DECLARED OWNERSHIP BOUNDARY
-------------------------------
    OPEN PLAY          free movement, line geometry, spacing, width, retreat.
                       exit: a body is GRANTED tackle candidacy.

    CONTACT AUTHORITY  contest FORMATION: who may engage, when a contest
                       exists, at what geometry.

    TES                contest EVOLUTION: drive, dominance, joiners,
                       collapse, break.        <-- OWNS THIS OBJECT.

    REFEREE            contest TERMINATION.

    PTB                contest RESTART / next possession.

Sentence form:

    Open Play owns movement.  Contact Authority owns contest formation.
    TES owns contest evolution.  Referee owns termination.  PTB owns restart.

WHAT STAGE 2 IS
---------------
Stage 1B made contest formation ONE owned decision with ONE TES-entry choke
point.  Stage 2A made that decision COMPLETE (the winner, every evaluated body
and every in-radius body it did not).  Stage 2B made it TRANSPORTABLE: a frozen
``Contest`` payload crosses the TES boundary.

What none of them produced is the object this stage declares: a PERSISTENT
contest — latched at the declaration, owned and EVOLVED by TES, carrying the
INITIAL CONDITIONS of the contest in one place.  A ``Contest`` is a frozen
transport value that describes a declaration; a ``TESContext`` is a per-cycle
physics working set that dies at RELEASE.  The TackleState is neither.

THE TRANSITION THIS STAGE IS
----------------------------
When Contact Authority declares a VALID FORMED CONTACT the contest is created
and LATCHED — exactly one active TackleState — and ownership is transferred to
TES.  A FAILED declaration creates nothing and the engine simply stays in open
play.

    CONTACT_FAILED  ->  no TackleState, no handover, open play continues.
    CONTACT_FORMED  ->  EXACTLY ONE active TackleState, ownership -> TES.

WHAT THE STATE CAPTURES (initial conditions ONLY)
-------------------------------------------------
    the carrier                       carrier / carrier_id / contact_carrier
    the primary defender              primary_defender / .._id / contact_defender
    the contact timestamp             contact_frame / contact_time_s
    initial carrier kinematics        contact_carrier  (x, y, vx, vy, facing)
    initial defender kinematics       contact_defender (x, y, vx, vy, facing,
                                                       defender_role, tackle_role)
    contact quality and type          contact_quality / contact_type
    contact angle and geometry        separation_m / contact_angle_deg /
                                      contact_angle_to_attack_deg /
                                      lateral_offset_m / closing_speed_mps
    initial momentum / relative       carrier_momentum_kgmps /
      movement                        defender_momentum_kgmps /
                                      relative_speed_mps /
                                      relative_momentum_kgmps /
                                      relative_vx / relative_vy
    the forward-progress origin       forward_origin_x / forward_origin_y /
                                      attack_dir
    an initially EMPTY attachment     attachments == () beyond the primary
      list beyond the primary         (the primary is a FIELD, never an
                                      attachment)

WHAT STAGE 2 DELIBERATELY DOES NOT DO
-------------------------------------
This stage CAPTURES the initial conditions of the contest.  It does not
RESOLVE it.  Forming a TackleState:

  * does NOT move the carrier to the ground;
  * does NOT award a tackle;
  * does NOT trigger a play-the-ball;
  * does NOT perform tackle-bust logic;
  * does NOT freeze, thaw, reposition or write ANY body;
  * does NOT decide termination, restart or any referee question;
  * does NOT take a decision at all.

TES becomes the sole authority for evolving the formed contest FROM these
initial conditions.  The evolution declared here is exactly two events —
a body JOINING the contest (``append_attachment``) and a body physically
ATTACHING to the carrier (``mark_attached``) — plus the one handover record
(``mark_tes_ownership``).  Nothing else in this module mutates the state.

FORMATION IS A PURE READ
------------------------
``form_tackle_state`` reads positions, velocities, facing, roles and masses,
composes one object and latches it.  It writes to NOTHING except the state it
builds and the one published world attribute.  In particular the carrier and
the primary defender are NOT touched: measured on a real line, every field of
both bodies is identical across the formation call.

The two failed-contact carrier effects (ARM_GRAB damping, GLANCING
displacement) belong to ``contact_authority`` and fire ONLY on a FAILED band.
A formed declaration never applies them, so no formation can move a carrier.

ONLY CONTACT AUTHORITY MAY FORM; TES MAY ONLY TAKE OVER
-------------------------------------------------------
``form_tackle_state`` is called by Contact Authority at its declaration points.
``mark_tes_ownership`` is called by the single TES entry and is a NO-OP when no
contest has been formed — a handover can never CREATE a contest, because that
would make TES a second formation authority.  That is the load-bearing clause
of this contract:

    TES does not create contests.  A contest exists before TES begins.

THE LATCH
---------
``world.tackle_state`` is the ONE published attribute and the LATCH.
``active_tackle_state(world)`` is the only question a consumer asks: which
contest is live.  The latch is dropped on every entry into Open Play, so an
active TackleState can never survive back into open play; and a new formation
that arrives while an older state is still open CLOSES the older one with the
declared reason ``superseded``, so there is never more than one OPEN state.

NOT GATED
---------
There is no runtime switch.  Stage 1B refused a behavioural canary for the
DECIDER because its "off" arm would have re-created a second decider.  The same
argument applies with more force here: the TackleState is what TES is handed at
entry, so a switch that could remove it would be a switch that could break the
engine.  Neutrality is proven instead by
``verify_tackle_formation_contract.py`` (nothing reads the state to take a
decision), by ``probe_tackle_state_formation.py`` (formation mutates nothing)
and by ``tmp_s2_neutralise_probe.py`` (the formation can be removed without a
trace).

READ PURITY OF THIS MODULE
--------------------------
Every attribute store in this module targets the TackleState it owns or one of
that state's own Attachment records; the only other write is the single
``setattr(world, TACKLE_STATE_ATTR, ...)`` that publishes the latch.  No body,
no engine subsystem and no other world attribute is ever written, so forming,
extending or closing a contest cannot perturb a frame.

Naming note: this module is the TACKLE CONTEST.  It is NOT
``defence_lvl_1.tackle_state_gate``, which is a per-defender radius HOLD flag
(``d.tackle_state_active``) with its own ``stamp_tackle_state`` /
``update_tackle_state`` / ``reset_tackle_state`` API.  The two concepts are
unrelated and share no state.

IMPORT SAFETY
-------------
Module-scope imports are stdlib only (``dataclasses``, ``enum``, ``math``,
``typing``) at EVERY nesting depth: this module is a pure leaf.  It has no
project reach at all, so it can be imported from anywhere in any load order
without closing a cycle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "TACKLE_STATE_ATTR",
    "TACKLE_STATE_ID_PREFIX",
    "TACKLE_STATE_RING_LIMIT",
    "OWNER_CONTACT_AUTHORITY",
    "OWNER_TES",
    "CLOSE_OPEN_PLAY_ENTERED",
    "CLOSE_KICKOFF",
    "CLOSE_SUPERSEDED",
    "CLOSE_REASONS",
    "ROLE_PRIMARY",
    "QUALITY_FRONT_ON_MAX_DEG",
    "QUALITY_SHOULDER_MAX_DEG",
    "QUALITY_SIDE_ON_MAX_DEG",
    "HEADING_SPEED_EPS_MPS",
    "DEFAULT_MASS_KG",
    "SEPARATION_EPS_M",
    "ContactQuality",
    "ContactKind",
    "Kinematics",
    "Attachment",
    "TackleState",
    "tackle_state_id_for",
    "form_tackle_state",
    "mark_tes_ownership",
    "append_attachment",
    "mark_attached",
    "active_tackle_state",
    "published_tackle_state",
    "recent_tackle_states",
    "reset_tackle_states",
    "clear_tackle_state",
    "format_tackle_state_report",
    "declared_content",
]


# ─────────────────────────────────────────────────────────────────────
# THE PUBLISHED SURFACE — the one attribute that IS the latch
# ─────────────────────────────────────────────────────────────────────
# Written with ``setattr`` on the WORLD only.  It is the live contest: the
# object TES is handed, the object the evolution events extend, and the object
# a future contact-resolution stage reads instead of reconstructing.
TACKLE_STATE_ATTR: str = "tackle_state"

# The identity scheme.  A contest identifier is derived ONLY from facts already
# known at the declaration — the frame and the authority sequence number — so
# an identifier can never require a measurement the declaration did not take.
TACKLE_STATE_ID_PREFIX: str = "tackle"

# The bounded in-memory ring of every state this process has latched.  It
# exists so a probe can see the whole sequence of contests (two declarations
# can land in ONE frame) and so "exactly one OPEN state at a time" is a
# measurable property of the ring rather than a claim about the latch.  Nothing
# in the live engine reads it.
TACKLE_STATE_RING_LIMIT: int = 600

# ─────────────────────────────────────────────────────────────────────
# OWNERSHIP — the two owners a contest ever has, in order
# ─────────────────────────────────────────────────────────────────────
# A contest is FORMED by Contact Authority and then EVOLVED by TES.  The
# handover is the single transition; there is no third owner.
OWNER_CONTACT_AUTHORITY: str = "contact_authority"
OWNER_TES: str = "tes"

# ─────────────────────────────────────────────────────────────────────
# THE CLOSED LIFECYCLE VOCABULARY
# ─────────────────────────────────────────────────────────────────────
# WHY the contest stopped being active.  Each of these is an OBSERVED engine
# lifecycle event, never an adjudication: the caller passes the reason for a
# transition the engine has already taken.  This module decides none of them —
# in particular it never decides that a tackle is complete, and it has no way
# to terminate a contest by itself.
CLOSE_OPEN_PLAY_ENTERED: str = "open_play_entered"
CLOSE_KICKOFF: str = "kickoff"
CLOSE_SUPERSEDED: str = "superseded"
CLOSE_REASONS: frozenset = frozenset({
    CLOSE_OPEN_PLAY_ENTERED,
    CLOSE_KICKOFF,
    CLOSE_SUPERSEDED,
})

# ─────────────────────────────────────────────────────────────────────
# THE MEASUREMENT CONSTANTS
# ─────────────────────────────────────────────────────────────────────
# The primary defender's declared role in the contest.  The primary is a FIELD
# and is never listed as an attachment.
ROLE_PRIMARY: str = "PRIMARY"

# The contact-quality bands, in degrees of the defender's approach angle
# (0 = head-on).  THESE ARE DESCRIPTIVE, NOT A PREDICATE: nothing in this
# module or the engine takes a decision from them, so moving a band changes a
# LABEL and nothing else.  They exist so "what kind of contact was this" is a
# closed vocabulary rather than prose.
QUALITY_FRONT_ON_MAX_DEG: float = 30.0
QUALITY_SHOULDER_MAX_DEG: float = 60.0
QUALITY_SIDE_ON_MAX_DEG: float = 90.0

# The heading convention is the CONTACT PREDICATE'S OWN (see
# ``world/open_play_engine._check_pre_tackle``): a body with speed above this
# epsilon is headed along its velocity, otherwise along its facing.  Mirroring
# it here means the recorded geometry is the same geometry the predicate
# already reasoned about, not a second opinion about it.
HEADING_SPEED_EPS_MPS: float = 0.1

# Fallback body mass, mirroring ``game/tes/tes.py::_make_ctx``.
DEFAULT_MASS_KG: float = 90.0

# Below this separation the carrier-to-defender direction is undefined, so the
# declared fallback is the attack axis.
SEPARATION_EPS_M: float = 0.01

# The module-level ring (probe/audit only — never consumed by behaviour).
_STATE_RING: list = []


# ─────────────────────────────────────────────────────────────────────
# THE CLOSED CONTACT VOCABULARIES
# ─────────────────────────────────────────────────────────────────────

class ContactQuality(Enum):
    """HOW the defender arrived, as a closed label on the measured angle.

    FRONT_ON     the defender is driving into the carrier (angle < 30 deg)
    SHOULDER     an angled hit (30..60 deg)
    SIDE_ON      a side-on hit (60..90 deg)
    FROM_BEHIND  the defender is being carried or is moving away (> 90 deg)

    A CLASSIFICATION of geometry that was already measured, never a verdict.
    """

    FRONT_ON = "front_on"
    SHOULDER = "shoulder"
    SIDE_ON = "side_on"
    FROM_BEHIND = "from_behind"


class ContactKind(Enum):
    """WHAT formed the contest — the declared contact type.

    DIRECT  the authority's OWN decider formed it (its scalar contact
            predicate declared CONTACT).
    ENGAGE  a behaviour-layer predicate outside the decider admitted it
            (the four declared engage channels).

    This is the same distinction the ownership contract already draws, stated
    as a vocabulary instead of being re-derived from channel string names:
    the caller says which path it is, so this module cannot drift from the
    authority's channel set.
    """

    DIRECT = "direct"
    ENGAGE = "engage"


# ─────────────────────────────────────────────────────────────────────
# THE INITIAL-CONDITION BLOCKS
# ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Kinematics:
    """ONE body's measured kinematics, frozen at the instant it was taken.

    Used for four declared snapshots: the carrier and the primary defender at
    FORMATION (Contact Authority's read), and the same pair at the TES
    HANDOVER (TES's read).  Frozen, because an initial condition is a value:
    nothing may edit what a body was doing at contact after the fact.
    """

    player_id: int = -1
    x: float = 0.0
    y: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    facing: float = 0.0
    defender_role: str | None = None
    tackle_role: str | None = None

    @classmethod
    def of(cls, body) -> "Kinematics":
        """Snapshot *body* (or an all-zero, id-less snapshot when None)."""
        if body is None:
            return cls()
        role = getattr(body, "defender_role", None)
        return cls(
            player_id=int(getattr(body, "player_id", -1) or -1),
            x=float(getattr(body, "x", 0.0) or 0.0),
            y=float(getattr(body, "y", 0.0) or 0.0),
            vx=float(getattr(body, "vx", 0.0) or 0.0),
            vy=float(getattr(body, "vy", 0.0) or 0.0),
            facing=float(getattr(body, "facing", 0.0) or 0.0),
            defender_role=(getattr(role, "name", None) or str(role)
                           if role is not None else None),
            tackle_role=getattr(body, "tackle_role", None),
        )

    @property
    def speed(self) -> float:
        """Horizontal speed (magnitude of velocity)."""
        return math.hypot(self.vx, self.vy)


@dataclass
class Attachment:
    """ONE body that has JOINED the contest beyond the primary defender.

    The attachment list is the contest's CONTACT SET.  It starts EMPTY — the
    primary defender is a field of the state, not a member of this list — and
    is extended by the joiner system as bodies join.  ``attached_frame`` is the
    frame the body physically reached the carrier's attach band; it stays None
    for a body that joined the contest but never attached.
    """

    player_id: int
    tackler_role: str | None
    join_frame: int
    join_time_s: float
    join_x: float
    join_y: float
    join_separation_m: float
    attached_frame: int | None = None

    @property
    def attached(self) -> bool:
        """True once the body has physically attached to the carrier."""
        return self.attached_frame is not None
# ─────────────────────────────────────────────────────────────────────
# THE PERSISTENT CONTEST
# ─────────────────────────────────────────────────────────────────────

@dataclass
class TackleState:
    """THE persistent tackle contest: latched at the declaration, evolved by TES.

    Formed ONCE per declaration by Contact Authority (``form_tackle_state``),
    handed to TES (``mark_tes_ownership``), and then EVOLVED by TES through the
    declared growth events (``append_attachment`` / ``mark_attached``) until the
    engine's own lifecycle closes it (``clear_tackle_state``).

    The state is MUTABLE because TES owns its evolution.  The INITIAL
    CONDITIONS inside it are not: ``contact_carrier`` / ``contact_defender``
    are frozen ``Kinematics`` values taken at formation and are never rewritten,
    so what the contest looked like at contact is preserved exactly, however
    the contest later evolves.
    """

    # ── identity ──
    tackle_id: str
    frame: int
    seq: int
    phase: str
    channel: str
    contact_type: ContactKind

    # ── the parties ──
    carrier_id: int
    primary_defender_id: int
    carrier: Any = None
    primary_defender: Any = None

    # ── the contact timestamp ──
    contact_frame: int = 0
    contact_time_s: float = 0.0

    # ── the initial conditions (frozen at formation, never rewritten) ──
    contact_carrier: Kinematics = field(default_factory=Kinematics)
    contact_defender: Kinematics = field(default_factory=Kinematics)
    # The TES handover's own read of the same pair (None until the handover).
    # Recorded so the governed contact velocity TES actually inherits is
    # preserved as an initial condition rather than being lost at the boundary.
    entry_carrier: Kinematics | None = None
    entry_defender: Kinematics | None = None

    # ── the contact geometry ──
    separation_m: float = 0.0
    contact_angle_deg: float = 0.0
    contact_angle_to_attack_deg: float = 0.0
    lateral_offset_m: float = 0.0
    closing_speed_mps: float = 0.0
    contact_quality: ContactQuality = ContactQuality.FRONT_ON

    # ── the initial momentum / relative movement ──
    carrier_speed_mps: float = 0.0
    defender_speed_mps: float = 0.0
    carrier_momentum_kgmps: float = 0.0
    defender_momentum_kgmps: float = 0.0
    relative_speed_mps: float = 0.0
    relative_momentum_kgmps: float = 0.0
    relative_vx: float = 0.0
    relative_vy: float = 0.0

    # ── the forward-progress origin ──
    # The carrier's position at the instant of formation, and the axis forward
    # progress is measured along.  This IS the datum every later forward-metre
    # question is asked against.
    forward_origin_x: float = 0.0
    forward_origin_y: float = 0.0
    attack_dir: int = 1

    # ── the attachment list (the contest's contact set) ──
    # EMPTY beyond the primary defender at formation: the primary is named by
    # ``primary_defender_id`` and is never a member of this tuple.
    attachments: tuple = ()

    # ── ownership ──
    owner: str = OWNER_CONTACT_AUTHORITY
    handover_frame: int | None = None
    handover_time_s: float | None = None
    handoff_defender_vx: float | None = None
    handoff_defender_vy: float | None = None
    handoff_differs_from_contact: bool = False

    # ── lifecycle ──
    closed: bool = False
    closed_frame: int | None = None
    closed_reason: str | None = None

    # ── derived readers ──

    @property
    def declaration_key(self) -> tuple:
        """``(frame, seq)`` — the declaration this contest was formed on.

        The declaration payload that produced this contest is identified by
        this key, so the state and its ``Contest`` can always be lined up
        without duplicating the payload's identity string.
        """
        return (int(self.frame), int(self.seq))

    @property
    def owned_by_tes(self) -> bool:
        """True once TES has taken ownership of the contest."""
        return self.owner == OWNER_TES

    @property
    def open(self) -> bool:
        """True while the contest is still active (not closed)."""
        return not self.closed

    @property
    def attachment_ids(self) -> tuple:
        """The player ids on the attachment list, in join order."""
        return tuple(int(one.player_id) for one in self.attachments)

    @property
    def contact_set_ids(self) -> tuple:
        """The whole contact set: the PRIMARY first, then the attachments.

        The primary defender is part of the contest's contact set by
        definition, and is never repeated in the attachment list.
        """
        return (int(self.primary_defender_id),) + self.attachment_ids

    @property
    def attached_ids(self) -> tuple:
        """The ids on the attachment list that have PHYSICALLY attached."""
        return tuple(int(one.player_id) for one in self.attachments
                     if one.attached)

    @property
    def attachment_count(self) -> int:
        """How many bodies have joined the contest beyond the primary."""
        return len(self.attachments)

    @property
    def contact_set_size(self) -> int:
        """How many bodies the contest currently contains."""
        return 1 + self.attachment_count

    def has_attachment(self, player_id: int) -> bool:
        """True when *player_id* is on the attachment list."""
        return int(player_id) in self.attachment_ids

    def attachment_for(self, player_id: int) -> "Attachment | None":
        """The attachment record for *player_id* (or None)."""
        for one in self.attachments:
            if int(one.player_id) == int(player_id):
                return one
        return None

    def forward_progress_m(self, x: float) -> float:
        """Metres of forward progress *x* represents from the declared origin.

        POSITIVE means the carrier has moved toward the tryline it was
        attacking when the contest was formed.  A pure read of the declared
        origin and axis — it never writes anything.
        """
        return (float(x) - float(self.forward_origin_x)) * float(self.attack_dir)
# ─────────────────────────────────────────────────────────────────────
# SMALL READS (never raise)
# ─────────────────────────────────────────────────────────────────────

def _frame_of(world) -> int:
    """The world's frame counter, or 0."""
    try:
        return int(getattr(world, "frame_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _time_of(world) -> float:
    """The world's simulation clock in seconds, or 0.0."""
    try:
        return float(getattr(world, "sim_time_s", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _attack_dir_of(world) -> int:
    """The attacking team's direction along X (+1 / -1), or +1."""
    try:
        return -1 if int(getattr(world, "attacking_direction", 1) or 1) < 0 \
            else 1
    except (TypeError, ValueError):
        return 1


def _mass_of(body) -> float:
    """A body's mass in kg (the declared fallback when it has none)."""
    try:
        value = float(getattr(body, "mass_kg", DEFAULT_MASS_KG)
                      or DEFAULT_MASS_KG)
    except (TypeError, ValueError):
        return DEFAULT_MASS_KG
    return value if value > 0.0 else DEFAULT_MASS_KG


def tackle_state_id_for(frame: int, seq: int) -> str:
    """The contest identifier for a declaration (frame + authority sequence)."""
    return "%s-%d-%d" % (TACKLE_STATE_ID_PREFIX, int(frame), int(seq))


# ─────────────────────────────────────────────────────────────────────
# THE MEASUREMENT (pure reads of the formation instant)
# ─────────────────────────────────────────────────────────────────────

def _heading_vector(body) -> tuple:
    """The heading the CONTACT PREDICATE ITSELF uses for *body*.

    Velocity when the body is moving (speed > ``HEADING_SPEED_EPS_MPS``),
    otherwise its facing — byte-for-byte the convention
    ``world/open_play_engine._check_pre_tackle`` applies when it scores the
    contact.  Mirroring it keeps the recorded geometry on the same basis as
    the decision that formed the contest.
    """
    vx = float(getattr(body, "vx", 0.0) or 0.0)
    vy = float(getattr(body, "vy", 0.0) or 0.0)
    speed = math.hypot(vx, vy)
    if speed > HEADING_SPEED_EPS_MPS:
        return (vx / speed, vy / speed)
    facing = float(getattr(body, "facing", 0.0) or 0.0)
    return (math.cos(facing), math.sin(facing))


def quality_for(angle_deg: float) -> ContactQuality:
    """The declared contact-quality label for an approach angle in degrees."""
    angle = abs(float(angle_deg))
    if angle < QUALITY_FRONT_ON_MAX_DEG:
        return ContactQuality.FRONT_ON
    if angle < QUALITY_SHOULDER_MAX_DEG:
        return ContactQuality.SHOULDER
    if angle < QUALITY_SIDE_ON_MAX_DEG:
        return ContactQuality.SIDE_ON
    return ContactQuality.FROM_BEHIND


def measure_contact(carrier, primary_defender, attack_dir: int) -> dict:
    """MEASURE the contact: geometry, quality and momentum.  A pure read.

    Reads positions, velocities, facing and masses; returns the declared
    initial-condition numbers.  It writes to NOTHING, so it can be called on a
    live body without perturbing the frame.

    DECLARED DEFINITIONS:
      separation_m               centre distance at the instant of formation.
      contact_angle_deg          angle between the defender's HEADING and the
                                 direction to the carrier (0 = head-on).
      contact_angle_to_attack_deg  |angle| of the defender->carrier vector
                                 relative to the ATTACK axis (0 = straight
                                 ahead).  The same geometry TES's dominant gate
                                 snapshots at entry.
      lateral_offset_m           carrier.y - defender.y (raw, signed).
      closing_speed_mps          rate at which the separation is shrinking:
                                 -(relative velocity projected on the
                                 defender->carrier axis).  Positive = closing.
    """
    cx = float(getattr(carrier, "x", 0.0) or 0.0)
    cy = float(getattr(carrier, "y", 0.0) or 0.0)
    dx = float(getattr(primary_defender, "x", 0.0) or 0.0)
    dy = float(getattr(primary_defender, "y", 0.0) or 0.0)
    cvx = float(getattr(carrier, "vx", 0.0) or 0.0)
    cvy = float(getattr(carrier, "vy", 0.0) or 0.0)
    dvx = float(getattr(primary_defender, "vx", 0.0) or 0.0)
    dvy = float(getattr(primary_defender, "vy", 0.0) or 0.0)

    gap_x = cx - dx
    gap_y = cy - dy
    separation = math.hypot(gap_x, gap_y)

    if separation > SEPARATION_EPS_M:
        ux = gap_x / separation
        uy = gap_y / separation
    else:
        # Degenerate separation: the declared fallback direction is the attack
        # axis, so the angle is well defined rather than arbitrary.
        ux = 1.0 if int(attack_dir) >= 0 else -1.0
        uy = 0.0

    head_x, head_y = _heading_vector(primary_defender)
    dot = head_x * ux + head_y * uy
    dot = max(-1.0, min(1.0, dot))
    contact_angle_deg = math.degrees(math.acos(dot))

    axis_x = gap_x * float(attack_dir)
    contact_angle_to_attack_deg = abs(
        math.degrees(math.atan2(gap_y, axis_x))
    )

    rel_vx = cvx - dvx
    rel_vy = cvy - dvy
    closing_speed = -(rel_vx * ux + rel_vy * uy)

    mass_carrier = _mass_of(carrier)
    mass_defender = _mass_of(primary_defender)
    carrier_speed = math.hypot(cvx, cvy)
    defender_speed = math.hypot(dvx, dvy)

    return {
        "separation_m": separation,
        "contact_angle_deg": contact_angle_deg,
        "contact_angle_to_attack_deg": contact_angle_to_attack_deg,
        "lateral_offset_m": gap_y,
        "closing_speed_mps": closing_speed,
        "contact_quality": quality_for(contact_angle_deg),
        "carrier_speed_mps": carrier_speed,
        "defender_speed_mps": defender_speed,
        "carrier_momentum_kgmps": mass_carrier * carrier_speed,
        "defender_momentum_kgmps": mass_defender * defender_speed,
        "relative_speed_mps": math.hypot(rel_vx, rel_vy),
        "relative_momentum_kgmps": math.hypot(
            mass_carrier * cvx - mass_defender * dvx,
            mass_carrier * cvy - mass_defender * dvy,
        ),
        "relative_vx": rel_vx,
        "relative_vy": rel_vy,
    }
# ─────────────────────────────────────────────────────────────────────
# THE LATCH: publication, reads, and the one close path
# ─────────────────────────────────────────────────────────────────────

def _publish(world, state: TackleState) -> TackleState:
    """Latch *state* as the live contest on *world* and ring it.

    Publication is the single ``setattr`` this module owns, on the WORLD only,
    so no body and no engine subsystem can be perturbed by a declaration.  The
    ring exists so a probe can see two declarations that land in one frame;
    nothing in the live engine reads it.
    """
    if world is not None:
        try:
            setattr(world, TACKLE_STATE_ATTR, state)
        except Exception:
            pass

    _STATE_RING.append(state)
    if len(_STATE_RING) > TACKLE_STATE_RING_LIMIT:
        del _STATE_RING[:-TACKLE_STATE_RING_LIMIT]

    return state


def _unpublish(world) -> None:
    """Drop the latch (the state stays on the ring as history)."""
    if world is None:
        return
    try:
        delattr(world, TACKLE_STATE_ATTR)
    except AttributeError:
        pass


def published_tackle_state(world) -> "TackleState | None":
    """The state currently latched on *world*, CLOSED or not (or None).

    This is the raw latch read.  ``active_tackle_state`` is the question a
    consumer asks; this one is for lifecycle auditing, where a just-closed
    state must still be observable.
    """
    if world is None:
        return None
    return getattr(world, TACKLE_STATE_ATTR, None)


def active_tackle_state(world) -> "TackleState | None":
    """THE LIVE CONTEST: the latched state, or None when none is active.

    An active contest is one that has been formed and not yet closed.  This is
    the single question the rest of the engine asks about the latch, so
    "exactly one active TackleState" is a property of one function.
    """
    state = published_tackle_state(world)
    if state is None or bool(getattr(state, "closed", False)):
        return None
    return state


def recent_tackle_states() -> tuple:
    """The in-memory ring of every state latched this process (oldest first)."""
    return tuple(_STATE_RING)


def _close_state(state: TackleState, reason: str, frame: int) -> None:
    """Close *state* with a declared reason.  Idempotent."""
    if bool(getattr(state, "closed", False)):
        return
    state.closed = True
    state.closed_frame = int(frame)
    state.closed_reason = str(reason)


def clear_tackle_state(world, reason: str = CLOSE_OPEN_PLAY_ENTERED
                       ) -> "TackleState | None":
    """Close and drop the latched contest.  Returns it, or None.

    Called by the ENGINE's own lifecycle boundaries (an entry into Open Play,
    a kickoff reset) — this module never decides that a contest is over.  The
    reason is recorded from the caller's vocabulary so the lifecycle is
    auditable.
    """
    state = published_tackle_state(world)
    if state is None:
        return None
    _close_state(state, reason, _frame_of(world))
    _unpublish(world)
    return state


def reset_tackle_states(world=None) -> None:
    """Drop the whole ledger (kickoff / fresh sequence).

    Clears the ring and, when *world* is given, the published latch.  It
    introduces no engine state and is read by nothing in the live loop.
    """
    global _STATE_RING
    _STATE_RING = []
    _unpublish(world)


# ─────────────────────────────────────────────────────────────────────
# FORMATION — Contact Authority's declaration becomes a contest
# ─────────────────────────────────────────────────────────────────────

def form_tackle_state(world, carrier, primary_defender, *, frame: int,
                      seq: int, phase: str = "", channel: str = "",
                      admitted_by_decider: bool = True
                      ) -> "TackleState | None":
    """CREATE and LATCH the persistent contest for a formed declaration.

    Called by Contact Authority the moment it declares a VALID FORMED
    CONTACT.  It captures the initial conditions — the parties, the contact
    timestamp, both bodies' kinematics, the contact quality/type, the contact
    angle/geometry, the initial momentum and relative movement, and the
    forward-progress origin — latches exactly one active TackleState, and
    leaves the attachment list EMPTY beyond the primary defender.

    IT FORMS NOTHING WHEN THERE IS NOTHING TO FORM: a carrier-less or
    defender-less call returns None, so a frame that declares no contest
    cannot produce a state.

    IDEMPOTENT PER DECLARATION: a second call for the SAME ``(frame, seq)``
    returns the already-latched state unchanged, so the hand-over path cannot
    mint a second contest for one declaration.  A call for a DIFFERENT
    declaration while an older state is still open closes the older one with
    the declared reason ``superseded``, so there is never more than one OPEN
    state.

    IT MUTATES NO BODY.  Every read is a position, a velocity, a facing, a role
    or a mass; the only writes are the state it builds and the one published
    world attribute.
    """
    if world is None or carrier is None or primary_defender is None:
        return None

    frame = int(frame)
    seq = int(seq)
    key = (frame, seq)

    current = active_tackle_state(world)
    if current is not None:
        if current.declaration_key == key:
            return current
        _close_state(current, CLOSE_SUPERSEDED, frame)

    attack_dir = _attack_dir_of(world)
    measured = measure_contact(carrier, primary_defender, attack_dir)
    carrier_id = int(getattr(carrier, "player_id", -1) or -1)
    defender_id = int(getattr(primary_defender, "player_id", -1) or -1)

    state = TackleState(
        tackle_id=tackle_state_id_for(frame, seq),
        frame=frame,
        seq=seq,
        phase=str(phase or ""),
        channel=str(channel or ""),
        contact_type=(ContactKind.DIRECT if admitted_by_decider
                      else ContactKind.ENGAGE),
        carrier_id=carrier_id,
        primary_defender_id=defender_id,
        carrier=carrier,
        primary_defender=primary_defender,
        contact_frame=frame,
        contact_time_s=_time_of(world),
        contact_carrier=Kinematics.of(carrier),
        contact_defender=Kinematics.of(primary_defender),
        separation_m=measured["separation_m"],
        contact_angle_deg=measured["contact_angle_deg"],
        contact_angle_to_attack_deg=measured["contact_angle_to_attack_deg"],
        lateral_offset_m=measured["lateral_offset_m"],
        closing_speed_mps=measured["closing_speed_mps"],
        contact_quality=measured["contact_quality"],
        carrier_speed_mps=measured["carrier_speed_mps"],
        defender_speed_mps=measured["defender_speed_mps"],
        carrier_momentum_kgmps=measured["carrier_momentum_kgmps"],
        defender_momentum_kgmps=measured["defender_momentum_kgmps"],
        relative_speed_mps=measured["relative_speed_mps"],
        relative_momentum_kgmps=measured["relative_momentum_kgmps"],
        relative_vx=measured["relative_vx"],
        relative_vy=measured["relative_vy"],
        forward_origin_x=float(getattr(carrier, "x", 0.0) or 0.0),
        forward_origin_y=float(getattr(carrier, "y", 0.0) or 0.0),
        attack_dir=attack_dir,
        attachments=(),
        owner=OWNER_CONTACT_AUTHORITY,
    )
    return _publish(world, state)


# ─────────────────────────────────────────────────────────────────────
# THE HANDOVER — TES takes ownership (and can never create one)
# ─────────────────────────────────────────────────────────────────────

def mark_tes_ownership(world, carrier, defender,
                       defender_velocity=None) -> "TackleState | None":
    """TES TAKES OWNERSHIP of the formed contest.  THE single handover.

    Called by the engine's one TES entry.  From this call on, TES is the sole
    authority for evolving the formed contest from the captured initial
    conditions.

    IT CANNOT CREATE A CONTEST: with no active state this is a NO-OP returning
    None, so the hand-over path can never become a second formation authority.
    That is what makes the historical caller — the engine's own unreachable copy
    of the defence dispatch, which enters TES without going through Contact
    Authority — behave exactly as it did before this stage.

    It records what TES is about to drive from: its own read of the carrier and
    the primary defender at entry, and — when the caller supplies it — the
    GOVERNED contact velocity TES inherits for the primary tackler (the
    pre-tackle corridor's stamped approach velocity, which is not necessarily
    the live one).  Whether that velocity differs from the one measured at
    formation is recorded, so the hand-over's effect on the contest is visible
    rather than silent.
    """
    state = active_tackle_state(world)
    if state is None:
        return None
    if state.owned_by_tes:
        return state

    state.owner = OWNER_TES
    state.handover_frame = _frame_of(world)
    state.handover_time_s = _time_of(world)
    state.entry_carrier = Kinematics.of(carrier)
    state.entry_defender = Kinematics.of(defender)

    if defender_velocity is not None:
        try:
            entry_vx = float(defender_velocity[0])
            entry_vy = float(defender_velocity[1])
        except (TypeError, ValueError, IndexError):
            entry_vx = None
            entry_vy = None
        if entry_vx is not None:
            state.handoff_defender_vx = entry_vx
            state.handoff_defender_vy = entry_vy
            state.handoff_differs_from_contact = (
                entry_vx != state.contact_defender.vx
                or entry_vy != state.contact_defender.vy
            )
    return state


# ─────────────────────────────────────────────────────────────────────
# EVOLUTION — the two declared growth events (TES-owned)
# ─────────────────────────────────────────────────────────────────────

def append_attachment(world, body, role=None) -> "Attachment | None":
    """Record that ONE body has JOINED the contest beyond the primary.

    THE first growth event: the contest's contact set EXTENDS as the joiner
    system brings bodies in.  A body is appended ONCE (a repeat call returns
    the existing record), the PRIMARY DEFENDER is never appended (it is a
    field of the state, by definition), and a call with no active contest is a
    no-op returning None.

    It writes to nothing but the state's own attachment list, so extending the
    contest cannot perturb a body or a frame.
    """
    state = active_tackle_state(world)
    if state is None or body is None:
        return None

    player_id = int(getattr(body, "player_id", -1) or -1)
    if player_id < 0 or player_id == int(state.primary_defender_id):
        return None

    existing = state.attachment_for(player_id)
    if existing is not None:
        return existing

    carrier = state.carrier
    if carrier is None:
        separation = 0.0
    else:
        separation = math.hypot(
            float(getattr(carrier, "x", 0.0) or 0.0)
            - float(getattr(body, "x", 0.0) or 0.0),
            float(getattr(carrier, "y", 0.0) or 0.0)
            - float(getattr(body, "y", 0.0) or 0.0),
        )

    attachment = Attachment(
        player_id=player_id,
        tackler_role=(str(role) if role is not None
                      else getattr(body, "tackle_role", None)),
        join_frame=_frame_of(world),
        join_time_s=_time_of(world),
        join_x=float(getattr(body, "x", 0.0) or 0.0),
        join_y=float(getattr(body, "y", 0.0) or 0.0),
        join_separation_m=separation,
    )
    state.attachments = state.attachments + (attachment,)
    return attachment


def mark_attached(world, body) -> "Attachment | None":
    """Record that an attachment has PHYSICALLY ATTACHED to the carrier.

    THE second growth event.  It stamps the frame on an EXISTING attachment
    record; a body that is not on the attachment list (notably the primary
    defender) is not added by this call, so attachment membership and the
    attach event can never disagree about who is in the contest.
    """
    state = active_tackle_state(world)
    if state is None or body is None:
        return None

    player_id = int(getattr(body, "player_id", -1) or -1)
    if player_id < 0:
        return None

    for attachment in state.attachments:
        if int(attachment.player_id) != player_id:
            continue
        if attachment.attached_frame is None:
            attachment.attached_frame = _frame_of(world)
        return attachment
    return None


# ─────────────────────────────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────────────────────────────

def format_tackle_state_report(world) -> str:
    """Compact one-line rendering of the live contest."""
    state = published_tackle_state(world)
    if state is None:
        return "tackle_state=no_contest"
    return (
        "tackle_state=%s frame=%d seq=%d owner=%s type=%s quality=%s "
        "winner=%s attachments=%d attached=%d closed=%s"
        % (state.tackle_id, state.frame, state.seq, state.owner,
           state.contact_type.value, state.contact_quality.value,
           state.primary_defender_id, state.attachment_count,
           len(state.attached_ids), state.closed_reason or "-")
    )


def declared_content() -> tuple:
    """The DECLARED content contract: what a TackleState captures at formation.

    Declarative only — nothing reads this at run time.  It exists so the
    contract is stated in code as well as in the document, and so the verifier
    can assert that every declared item is present as a FIELD of the state
    rather than only in prose.
    """
    return (
        ("carrier", ("carrier", "carrier_id")),
        ("primary_defender",
         ("primary_defender", "primary_defender_id")),
        ("contact_timestamp", ("contact_frame", "contact_time_s")),
        ("initial_carrier_kinematics", ("contact_carrier",)),
        ("initial_defender_kinematics", ("contact_defender",)),
        ("contact_quality", ("contact_quality",)),
        ("contact_type", ("contact_type",)),
        ("contact_angle_and_geometry",
         ("separation_m", "contact_angle_deg",
          "contact_angle_to_attack_deg", "lateral_offset_m",
          "closing_speed_mps")),
        ("initial_momentum_and_relative_movement",
         ("carrier_speed_mps", "defender_speed_mps",
          "carrier_momentum_kgmps", "defender_momentum_kgmps",
          "relative_speed_mps", "relative_momentum_kgmps",
          "relative_vx", "relative_vy")),
        ("forward_progress_origin",
         ("forward_origin_x", "forward_origin_y", "attack_dir")),
        ("attachment_list_beyond_the_primary", ("attachments",)),
    )
