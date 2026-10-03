"""contact_authority.py — THE CONTACT AUTHORITY (Stage 1B, 2026-09-19).

THE DECLARED OWNERSHIP BOUNDARY
-------------------------------
    OPEN PLAY          free movement, line geometry, spacing, width, retreat.
                       exit: a body is GRANTED tackle candidacy.

    CONTACT AUTHORITY  contest FORMATION: who may engage, when a contest
                       exists, at what geometry.       <-- THIS MODULE.

    TES                contest EVOLUTION: drive, dominance, joiners,
                       collapse, break.

    REFEREE            contest TERMINATION.

    PTB                contest RESTART / next possession.

Sentence form:

    Open Play owns movement.  Contact Authority owns contest formation.
    TES owns contest evolution.  Referee owns termination.  PTB owns restart.

WHAT STAGE 1B IS, AND WHAT IT IS NOT
------------------------------------
Stage 1A (``CONTACT_AUTHORITY_OWNERSHIP_CONTRACT.md`` + ``project_info__71.md``)
established that contest formation was *fragmented across five writers* and
*owned by none*:

    #  Writer                      Grants today
    2  intercept_tacklers          tackle_role = "PRIMARY" / "LINE"
    4  pre_tackle_authority        pre_tackle_state COMMIT/PLANTED/SLOW/HOLD
    5  pretackle_corridor          corridor latch + contact contract
    6  pretackle_machine           12 states -> 5 outcomes (the only
                                   producer of CLEAN_ENGAGE)
    7  DefenceManager trigger      the FIRE decision (conjuncts 1..5)

Stage 1B declares ONE owner for the FIRE decision and RELOCATES the live
trigger loop into it.  All five writers keep writing exactly what they write
today; they become **evidence providers**, consumed by this module.  The
manager's trigger loop becomes a **consumer** of this module's single
published decision.

    ONE authoritative outcome per frame:

        CONTACT_FAILED
        CONTACT_FORMED

WHAT CHANGES, PRECISELY — NOTHING IN THE GAME
---------------------------------------------
This is a RELOCATION, not a redesign.  The predicate, the thresholds, the
radius, the conjunction, the iteration order, the short-circuit, and the
failed-contact carrier effects are byte-for-byte the ones that were inline in
``DefenceManager.update_open_play()``.  Specifically:

  * ``_check_pre_tackle`` is the SAME function, called on the SAME body, in
    the SAME order.  Stage 1B does NOT move its thresholds
    (``MISS_THRESHOLD = 0.3`` / ``ARM_GRAB_THRESHOLD = 0.6`` /
    ``GLANCING_THRESHOLD = 0.75`` stay exactly where they are, inside
    ``world/open_play_engine.py``).  Making the predicate geometric and
    tuner-owned is contract rule **T2**, and it is Stage 2 work.
  * ``TACKLE_RADIUS_M`` is read from the SAME owner
    (``world.open_play_engine``) as the live trigger read it — never from a
    tuner, so no tuner can silently move the contact radius.
  * The fullback conjunct is the LIVE form, which honours the
    ``can_acquire_tackle`` escape (the engine's own disabled copy of this loop
    omits it; the live one is authoritative).
  * The two failed-contact carrier effects (arm-grab damping and glancing
    displacement) are applied HERE, because they are evaluation-order
    dependent: the live loop CONTINUES after applying them, so a later,
    same-frame defender's distance is measured against the already-moved
    carrier.  Splitting "decide" from "effect" would change the game.

TES ENTRY HAS EXACTLY ONE CHOKE POINT
-------------------------------------
``enter_tes`` is the ONLY function in the engine that calls
``OpenPlayEngine._start_tackle(bc, winner)`` -> ``tes_start_tackle``.  Every
module that can admit a contest routes through it:

    CHANNEL_MANAGER_TRIGGER       DefenceManager (the relocated five-conjunct
                                  trigger) -> ``evaluate_contact`` decided it
    CHANNEL_STRUCTURED_ENGAGE     DBU._apply_structured_open_play
    CHANNEL_UNIFIED_ENGAGE        DBU._apply_open_play_unified
    CHANNEL_PURSUIT_ENGAGE        DBU.apply_pursuit_mode
    CHANNEL_LVL1_CHASE_ENGAGE     DefensiveBehaviourLvl1.apply_chase

WHY THIS REPLACED "THIS MODULE NEVER ENTERS TES".  Stage 1B first declared the
manager's trigger to be the single entry and left the four behaviour-layer
call sites in place.  A live rollout (``tmp_run_stage1b_regression.py``,
2026-09-19) measured EIGHT tackle cycles with ZERO decisions recorded by
``evaluate_contact``: every contest in that run was formed by
``try_pre_tackle`` inside the behaviour pipeline, which called
``_start_tackle`` directly.  The declaration was therefore FALSE as first
shipped — the manager's trigger is an *additional* admission path, not *the*
path.

The fix does not move the decision.  Each behaviour-layer site keeps its own
predicate (``try_pre_tackle`` -> ``pretackle_machine.should_engage``, a
STRONGER gate than ``_check_pre_tackle``'s scalar) and calls ``enter_tes`` at
exactly the line where it called ``_start_tackle`` before.  ``enter_tes``
publishes the formation (with its channel) and then delegates to the SAME
``_start_tackle`` — so the game plays identically and the authority owns the
*handover* as well as the *decision*.

This module still does not import TES, does not set ``world.tes_active``, and
does not touch ``world.tackler_ids``; it only calls the engine's own,
unchanged single entry point.

WHY THERE IS NO BEHAVIOURAL CANARY
----------------------------------
Every previous stage shipped a gate whose "off" arm restored the previous
behaviour.  Stage 1B deliberately does NOT, and the reason is the whole point
of the stage: the "off" arm would have to re-implement the trigger loop
outside this module, which would recreate a SECOND DECIDER — exactly the
defect being removed.  Byte-identity is instead proven by
``verify_contact_authority_contract.py`` section [3], a differential harness
that runs this module and an independently written copy of the original loop
over a deterministic input matrix and asserts identical outcome, identical
winner, and bit-identical carrier mutations.  The only gate here is the
AUDIT sink (off by default), which can never change a decision.

THE PUBLISHED SURFACE (for future stages)
-----------------------------------------
``evaluate_contact`` publishes, on the world:

    world.contact_authority_decision   the latest ContactDecision
    world.contact_authority_seq        the contest sequence number

That pair is the *observable ownership boundary* and the stable architectural
surface that future control modelling (T5), referee authority (T4) and the
TES exit taxonomy build on.  Nothing in the live engine reads them yet; the
audit, the probe and the verifier do.

STAGE 2B — THE DECLARATION PAYLOAD CROSSES THE TES BOUNDARY
-----------------------------------------------------------
A declaration now produces ONE canonical payload: a first-class Contest,
assembled here at the moment of declaration and published as live simulation
state.  The complete formation state is no longer represented twice — the
historical record is DERIVED FROM the payload rather than assembled beside
it, so the two cannot drift apart.

    world.contest   the live declared contest (the canonical payload)

The payload carries the contest identifier, the winner identifier, the
evaluated defender set, the in-radius defender set, the admission channel,
the declaration frame and the authority sequence number — every one of them a
fact already known at declaration time.  TES receives that object in place of
the bare winner and consumes ONLY its winning defender, so the stage changes
no behaviour.

The RECORDER is governed by ``RLM_CONTEST_PRESERVATION``; the PAYLOAD is not.
Turning the recorder off must never be able to remove the object TES is
handed, so the payload is assembled unconditionally.

EVIDENCE PROVIDERS (read, never re-derived)
-------------------------------------------
The authority consumes, per candidate body:

  * ``tackle_role``               — intercept_tacklers / pre_tackle_authority
  * ``pre_tackle_state``          — pre_tackle_authority (COMMIT/PLANTED/...)
  * ``_pta_corridor`` / frames    — pretackle_corridor (membership + streak)
  * ``_machine_state``            — pretackle_machine (LAST PUBLISHED stamp)
  * ``is_retreat_obligated``      — retreat_obligation (the 10 m contract)
  * ``is_slot_tackle_eligible``   — slot_arbitration (the slot conjunct)
  * ``is_tackle_engage_guard``    — tackle_engage_guard (forced engage)

READ-PURITY IS LOAD-BEARING.  Every predicate above is a pure read of engine
truth (verified against its source).  ``pretackle_machine.run_machine`` is
DELIBERATELY NOT called: it advances ``bc.prev_facing`` (the per-frame
wrong-foot derivative) and, via the engage guard, can stamp
``tackle_role``.  Consulting it for evidence would mutate the frame — so the
authority reads the machine's LAST PUBLISHED stamp instead.  That limitation
is recorded here and in the contract: promoting the engage machine to a
side-effect-free, re-consultable provider is contract rule **T1/T6** and is
Stage 2 work.

IMPORT SAFETY
-------------
Module-scope imports are stdlib only (``csv``, ``dataclasses``, ``enum``,
``math``, ``os``).  Every project reach is FUNCTION-LOCAL, because
``world.open_play_engine`` imports ``game.managers.defence_manager`` at module
scope and this module is imported by that manager — a top-level project
import would close a cycle.

REVERSIBILITY
-------------
There is no behavioural gate (see above).  ``RLM_CONTACT_AUTHORITY_AUDIT=1``
arms the CSV audit sink.  Deleting this module and restoring the ~45-line
inline loop in ``DefenceManager.update_open_play`` returns the engine to
Stage 1A exactly; the equivalence harness in the verifier is the machine-
checked statement that those two are the same code.
"""

from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "ContactOutcome",
    "ContactFailureReason",
    "FormationEvidence",
    "ContactDecision",
    "CONTACT_RADIUS_FALLBACK_M",
    "CONTACT_AUTHORITY_DECISION_ATTR",
    "CONTACT_AUTHORITY_SEQ_ATTR",
    "CONTACT_AUTHORITY_FRAME_ATTR",
    "CONTACT_AUTHORITY_TARGET_SOURCE_LEGACY",
    "ARM_GRAB_DAMPING_FACTOR",
    "GLANCING_LATERAL_OFFSET_M",
    "GLANCING_LATERAL_VELOCITY_MPS",
    "CONTACT_AUTHORITY_CONTEST_ATTR",
    "CHANNEL_MANAGER_TRIGGER",
    "CHANNEL_STRUCTURED_ENGAGE",
    "CHANNEL_UNIFIED_ENGAGE",
    "CHANNEL_PURSUIT_ENGAGE",
    "CHANNEL_LVL1_CHASE_ENGAGE",
    "audit_enabled",
    "declared_evidence_providers",
    "contact_radius_m",
    "evaluate_contact",
    "enter_tes",
    "last_decision",
    "last_contest",
    "recent_decisions",
    "reset_contact_authority",
    "format_contact_authority_report",
    "audit_sink_path",
    "record_decision_row",
]

# ─────────────────────────────────────────────────────────────────────
# CONSTANTS — copied verbatim from the live trigger.  These are NOT new
# tunables: each mirrors a literal that already existed inline.
# ─────────────────────────────────────────────────────────────────────

# Fallback only.  The live radius is ALWAYS read from ``world.open_play_engine``
# (the owner the inline loop read it from) so no tuner can move the contact
# boundary.  This default exists for a detached caller / verifier stub.
CONTACT_RADIUS_FALLBACK_M: float = 1.5

# Arm-grab damping applied to the carrier when the pre-tackle score lands in
# the ARM_GRAB band: ``bc.vx *= 0.85`` / ``bc.vy *= 0.85``.
ARM_GRAB_DAMPING_FACTOR: float = 0.85

# Glancing displacement: lateral push of half a metre, and a 2.0 m/s lateral
# velocity, signed by the carrier's side relative to the defender.
GLANCING_LATERAL_OFFSET_M: float = 0.5
GLANCING_LATERAL_VELOCITY_MPS: float = 2.0

# The pre-tackle verdict vocabulary produced by ``_check_pre_tackle``.  The
# authority does not invent these; it consumes them verbatim.
VERDICT_MISS: str = "MISS"
VERDICT_ARM_GRAB: str = "ARM_GRAB"
VERDICT_GLANCING: str = "GLANCING"
VERDICT_CONTACT: str = "CONTACT"

# The tackle-authority role vocabulary the trigger's conjunct 2 requires.
TACKLE_ROLE_INITIATORS: frozenset = frozenset({"PRIMARY", "SECONDARY"})


def _fb_emergency_authority_active(world, defender, bc) -> bool:
    """True when *defender* is a tagged fullback on a genuine line break.

    Delegates to ``fullback_role.fullback_emergency_defender_active`` -- the
    SAME single admission authority ``intercept_tacklers`` reads to admit the
    fullback to lane selection -- so this module never re-derives the break
    test (candidacy lever on, tag set, ``can_acquire_tackle``, unfrozen, and a
    genuine DEPTH or LATERAL break).  A pure read of engine truth, imported
    lazily because the fullback module sits deliberately outside the
    lane/role import graph.
    """
    from game.defence.defence_lvl_1.fullback_role import (
        fullback_emergency_defender_active,
    )
    return bool(fullback_emergency_defender_active(world, defender, bc))

# ─────────────────────────────────────────────────────────────────────
# PUBLISHED SURFACE — the world attributes that ARE the ownership boundary
# ─────────────────────────────────────────────────────────────────────
CONTACT_AUTHORITY_DECISION_ATTR: str = "contact_authority_decision"
CONTACT_AUTHORITY_SEQ_ATTR: str = "contact_authority_seq"
# THE CONTEST RECORD — the latest decision whose outcome was CONTACT_FORMED,
# whichever admission channel formed it.  ``contact_authority_decision`` is
# the latest decision of ANY kind (a frame can carry an engage-channel
# formation AND a manager-channel evaluation); this attribute is the
# authoritative one-contest-per-formation record, so the number of distinct
# frames carrying a contest record equals the number of TES entries.
CONTACT_AUTHORITY_CONTEST_ATTR: str = "contact_authority_contest"
# Per-frame memo guard so a second call in the same frame cannot re-apply the
# failed-contact carrier effects.
CONTACT_AUTHORITY_FRAME_ATTR: str = "_contact_authority_frame"

# ─────────────────────────────────────────────────────────────────────
# THE DECLARED ADMISSION CHANNELS
# ─────────────────────────────────────────────────────────────────────
# A contest can be admitted by more than one predicate, but every admission
# passes through ``enter_tes`` — the single TES-entry choke point this module
# owns.  The channel names WHICH predicate admitted the contest, so a contest
# can always be traced back to the authority that formed it.
CHANNEL_MANAGER_TRIGGER: str = "manager_trigger"
CHANNEL_STRUCTURED_ENGAGE: str = "structured_engage"
CHANNEL_UNIFIED_ENGAGE: str = "unified_engage"
CHANNEL_PURSUIT_ENGAGE: str = "pursuit_engage"
CHANNEL_LVL1_CHASE_ENGAGE: str = "lvl1_chase_engage"

# The attribution tag the inline loop's writers used for this boundary.  Kept
# as a name only: this module never stamps ``target_source`` (it is not a
# movement writer), and ``stamp_target_write`` must never write
# ``target_source``.
CONTACT_AUTHORITY_TARGET_SOURCE_LEGACY: str = "contact_authority"

# Audit sink.
AUDIT_ENV_VAR: str = "RLM_CONTACT_AUTHORITY_AUDIT"
AUDIT_CSV_NAME: str = "contact_authority_formation_audit.csv"

# In-memory ring buffer of the most recent decisions (never consumed by
# behaviour; read by the probe and the verifier).
DECISION_RING_FRAMES: int = 600
_DECISION_RING: list = []


# ─────────────────────────────────────────────────────────────────────
# THE TWO OUTCOMES (the whole vocabulary of contest formation)
# ─────────────────────────────────────────────────────────────────────

class ContactOutcome(Enum):
    """THE single authoritative formation outcome, one per frame.

    CONTACT_FAILED — no candidate satisfied the conjunction this frame, so
                     no contest formed.  The frame is fully described by its
                     ``reason`` plus the per-body ``FormationEvidence``.

    CONTACT_FORMED — exactly one candidate satisfied the conjunction and the
                     contest is declared.  ``ContactDecision.winner`` names
                     the body; the caller hands it to the existing TES entry.
    """
    CONTACT_FAILED = "CONTACT_FAILED"
    CONTACT_FORMED = "CONTACT_FORMED"


class ContactFailureReason(Enum):
    """WHY a contest failed to form — the declared audit vocabulary.

    One value per conjunct of the live trigger, plus the aggregate.  This is
    the audit's answer to "identify exactly why a contest formed or failed".
    """

    # Aggregate / structural.
    NO_IN_RADIUS_BODY = "no_in_radius_body"
    NOT_TACKLE_ROLE = "not_tackle_role"
    FULLBACK_WITHOUT_AUTHORITY = "fullback_without_authority"

    # Geometry: a role holder existed but was outside the contact radius.
    OUT_OF_RADIUS = "out_of_radius"

    # The scalar pre-tackle bands (``_check_pre_tackle``), in ascending order
    # of closeness to a formed contest.
    SCORE_MISS = "score_miss"
    SCORE_ARM_GRAB = "score_arm_grab"
    SCORE_GLANCING = "score_glancing"

    # Terminal.
    FORMED = "formed"


# Ordering used to pick the FRAME-level reason: the "furthest" band a
# candidate reached this frame is the most informative answer.
_BAND_RANK = {
    ContactFailureReason.SCORE_MISS: 1,
    ContactFailureReason.SCORE_ARM_GRAB: 2,
    ContactFailureReason.SCORE_GLANCING: 3,
}
_VERDICT_TO_REASON = {
    VERDICT_MISS: ContactFailureReason.SCORE_MISS,
    VERDICT_ARM_GRAB: ContactFailureReason.SCORE_ARM_GRAB,
    VERDICT_GLANCING: ContactFailureReason.SCORE_GLANCING,
}


# ─────────────────────────────────────────────────────────────────────
# EXPLICIT FORMATION DATA
# ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FormationEvidence:
    """One candidate body's formation evidence for this frame.

    Every field is either a value the authority itself measured or a stamp
    published by one of the five declared writers.  ``failed_at`` names the
    exact conjunct at which this body stopped being a contest candidate; for
    the winner it is ``FORMED``.
    """

    player_id: int
    defender_role: str
    tackle_role: str | None
    depth_class: str | None
    dist: float
    in_radius: bool
    predicate_verdict: str | None
    pre_tackle_state: str | None
    corridor_owned: bool
    corridor_frames: int
    slot_eligible: bool | None
    engage_guard: bool
    engage_guard_stamped: bool
    engage_state_last: str | None
    retreat_obligated: bool
    failed_at: ContactFailureReason


@dataclass(frozen=True)
class ContactDecision:
    """THE authoritative contest-formation decision for one frame.

    ``outcome`` is the single decision; ``reason`` is the frame-level answer;
    ``evidence`` is the full per-body formation data; ``winner`` is the body
    the caller must hand to the existing TES entry when the contest formed.

    ``arm_grab_damping`` and ``glancing_displacement`` declare the two
    failed-contact carrier effects that were applied during evaluation (both
    are part of the live behaviour and are order-dependent).
    """

    frame: int
    seq: int
    phase: str
    outcome: ContactOutcome
    reason: ContactFailureReason
    winner_id: int | None
    winner: Any
    in_radius_count: int
    role_holder_count: int
    evaluated_count: int
    arm_grab_damping: bool
    glancing_displacement: float | None
    bc_x: float
    bc_y: float
    bc_vx: float
    bc_vy: float
    evidence: tuple = field(default=())
    memoised: bool = False
    # WHICH ADMISSION CHANNEL formed this decision.  ``None`` when the
    # manager-channel evaluation formed nothing; one of the CHANNEL_*
    # constants when a named predicate admitted the contest.
    channel: str | None = None
    # THE CONTACT-SPACE MISS PATH (2026-10-03).  One entry per defender this
    # frame whose MISS resolved with the bodies INSIDE the combined body
    # radius (a genuine contact that used to resolve as a non-event).  Each
    # entry is a ``game.physics.contact_space_miss.ContactSpaceMissResult``.
    # EMPTY on a frame with no such miss, so the default IS the pre-change
    # value.  Recorded for the audit and the probe; nothing in the live loop
    # reads it to take a decision.
    contact_space_miss_events: tuple = field(default=())


# ─────────────────────────────────────────────────────────────────────
# GATES / ACCESSORS
# ─────────────────────────────────────────────────────────────────────

def audit_enabled() -> bool:
    """True when the CSV formation audit sink is armed.

    OFF by default.  This gate can never change a decision — it only decides
    whether the decision is also written to disk.
    """
    return os.environ.get(AUDIT_ENV_VAR, "") == "1"


def audit_sink_path() -> str:
    """Path of the formation audit CSV (see ``audit_enabled``)."""
    return AUDIT_CSV_NAME


def contact_radius_m() -> float:
    """THE contact radius, read from its owner (``world.open_play_engine``).

    The live trigger imported ``TACKLE_RADIUS_M`` from the engine module; this
    reads the SAME constant from the SAME owner so the contact boundary has
    exactly one source and no tuner can move it.  Falls back to the mirrored
    default only when the engine module is unreachable (detached stub).
    """
    try:
        from world.open_play_engine import TACKLE_RADIUS_M
        return float(TACKLE_RADIUS_M)
    except Exception:
        return float(CONTACT_RADIUS_FALLBACK_M)


def declared_evidence_providers() -> tuple:
    """The DECLARED input contract: who contributes what to formation.

    Declarative only — nothing reads this at run time.  It exists so the
    ownership boundary is stated in code as well as in the contract, and so
    the verifier can assert that every declared writer is still present and
    still unmodified in the respects this module depends on.
    """
    return (
        ("intercept_tacklers.select_lane_tacklers",
         "tackle_role in {PRIMARY, SECONDARY}", "candidacy grant"),
        ("pre_tackle_authority.apply_pre_tackle",
         "pre_tackle_state in {COMMIT, PLANTED, SLOW, HOLD}", "ownership + plant"),
        ("pretackle_corridor.enter_corridor",
         "_pta_corridor / _pta_corridor_frames", "approach-speed ownership"),
        ("pretackle_machine.run_machine",
         "_machine_state (last published stamp)", "engage classification"),
        ("tackle_engage_guard.is_tackle_engage_guard",
         "radial-in AND facing", "forced-engage policy"),
        ("retreat_obligation.is_retreat_obligated",
         "defender_role is DEF_RETREAT_10", "the 10 m contract"),
        ("slot_arbitration.is_slot_tackle_eligible",
         "|bc.y - defender.y| < slot threshold", "the slot conjunct"),
        ("world.open_play_engine._check_pre_tackle",
         "MISS / ARM_GRAB / GLANCING / CONTACT", "the contact predicate"),
    )


def _trace_enabled() -> bool:
    """Lazy read of the engine trace flag (mirrors the inline loop)."""
    try:
        from world.open_play_engine import DEBUG_OPE_TRACE
        return bool(DEBUG_OPE_TRACE)
    except Exception:
        return False


def _role_name(obj) -> str:
    """Return ``obj.defender_role``'s name (or ``"-"``)."""
    role = getattr(obj, "defender_role", None)
    if role is None:
        return "-"
    return getattr(role, "name", None) or str(role)


def _state_name(obj) -> str | None:
    """Return ``obj.pre_tackle_state``'s name (or ``None``)."""
    state = getattr(obj, "pre_tackle_state", None)
    if state is None:
        return None
    return getattr(state, "name", None) or str(state)


# ─────────────────────────────────────────────────────────────────────
# EVIDENCE COLLECTION — pure reads only (see the module docstring)
# ─────────────────────────────────────────────────────────────────────

def _safe_bool(fn, *args) -> bool | None:
    """Call a DECLARATIVE predicate, returning ``None`` if it cannot answer.

    Read-only evidence only.  A predicate that raises must never be able to
    change the formation decision, so a failure degrades the evidence field
    to ``None`` (an honest "not measured") rather than propagating.
    """
    try:
        return bool(fn(*args))
    except Exception:
        return None


def _evidence_for(world, bc, body, dist, in_radius, verdict,
                  failed_at: ContactFailureReason) -> FormationEvidence:
    """Assemble one body's formation evidence (declarative reads only)."""
    from game.defence.defence_lvl_1.retreat_obligation import (
        is_retreat_obligated,
    )
    from game.defence.defence_lvl_1.pretackle_corridor import (
        is_corridor_owned, corridor_frames,
    )
    from game.defence.defence_lvl_1.slot_arbitration import (
        is_slot_tackle_eligible,
    )
    from game.defence.defence_lvl_1.tackle_engage_guard import (
        is_tackle_engage_guard,
    )

    return FormationEvidence(
        player_id=int(getattr(body, "player_id", -1) or -1),
        defender_role=_role_name(body),
        tackle_role=getattr(body, "tackle_role", None),
        depth_class=getattr(body, "depth_class", None),
        dist=float(dist),
        in_radius=bool(in_radius),
        predicate_verdict=verdict,
        pre_tackle_state=_state_name(body),
        corridor_owned=bool(_safe_bool(is_corridor_owned, body) or False),
        corridor_frames=int(_safe_bool(corridor_frames, body) or 0),
        slot_eligible=_safe_bool(is_slot_tackle_eligible, bc, body, world),
        engage_guard=bool(
            _safe_bool(is_tackle_engage_guard, body, bc, world, None) or False
        ),
        engage_guard_stamped=bool(
            getattr(body, "_tackle_engage_guard", False)
        ),
        engage_state_last=getattr(body, "_machine_state", None),
        retreat_obligated=bool(_safe_bool(is_retreat_obligated, body, world)),
        failed_at=failed_at,
    )


# ─────────────────────────────────────────────────────────────────────
# THE FAILED-CONTACT CARRIER EFFECTS (verbatim from the inline loop)
# ─────────────────────────────────────────────────────────────────────

def _apply_arm_grab(bc) -> None:
    """ARM_GRAB band: damp the carrier's velocity by the damping factor."""
    bc.vx *= ARM_GRAB_DAMPING_FACTOR
    bc.vy *= ARM_GRAB_DAMPING_FACTOR


def _apply_glancing(bc, body) -> float:
    """GLANCING band: displace the carrier laterally.  Returns the sign."""
    lateral_dir = 1.0 if bc.y > body.y else -1.0
    bc.y += GLANCING_LATERAL_OFFSET_M * lateral_dir
    bc.vy = lateral_dir * GLANCING_LATERAL_VELOCITY_MPS
    return lateral_dir


# ─────────────────────────────────────────────────────────────────────
# THE CONTACT-SPACE MISS EFFECT (2026-10-03)
# ─────────────────────────────────────────────────────────────────────

def _apply_contact_space_miss(world, bc, defender, tuners=None):
    """CONTACT_SPACE_MISS: the deterministic failed-contact reaction.

    A ``MISS`` whose bodies are inside ``r_carrier + r_defender`` is a genuine
    body-to-body contact, not a whiff (measured by
    ``probe_miss_contact_space.py`` / ``project_info__87.md``).  This delegates
    to ``game.physics.contact_space_miss``, which applies the reaction to BOTH
    bodies — the carrier keeps its momentum and gains a small surge, the
    defender recoils and is briefly staggered.

    Returns the result, or ``None`` when the path is disarmed.  It writes
    nothing when the pair is NOT in contact space, so the caller's existing
    miss handling is byte-for-byte unchanged on an ordinary miss.

    Reached FUNCTION-LOCALLY: ``world.open_play_engine`` imports the manager
    that imports this module, so a top-level project reach would close a cycle.
    """
    from game.physics.contact_space_miss import (
        contact_space_miss_enabled,
        apply_contact_space_miss,
    )
    if not contact_space_miss_enabled(tuners):
        return None
    return apply_contact_space_miss(world, bc, defender, tuners)


# ─────────────────────────────────────────────────────────────────────
# THE AUTHORITY — one decision per frame
# ─────────────────────────────────────────────────────────────────────

def evaluate_contact(world, ope, defenders, bc, tuners=None) -> ContactDecision:
    """Declare contest formation for THIS frame.  THE single decider.

    This is a verbatim relocation of the trigger loop that lived at the end of
    ``DefenceManager.update_open_play``.  The conjuncts, their order, the
    short-circuit, and the failed-contact carrier effects are unchanged:

        1. body is not ``fullback_role_active`` without ``can_acquire_tackle``;
        2. ``tackle_role in {PRIMARY, SECONDARY}``;
        3. Euclidean distance to the carrier ``< TACKLE_RADIUS_M``;
        4. ``ope._check_pre_tackle(bc, body)`` — the scalar contact predicate;
        5. the FIRST body satisfying 1-4 forms the contest and the scan STOPS.

    ``tuners`` is accepted for signature symmetry with the rest of the
    defensive authorities and is deliberately NOT read: the contact radius
    comes from its owner (``world.open_play_engine``), exactly as the inline
    loop did, so no tuner can move the contact boundary in Stage 1B.

    Idempotent within a frame: a second call returns the memoised decision and
    re-applies nothing (the carrier effects must happen exactly once).
    """
    frame = int(getattr(world, "frame_count", 0) or 0)
    phase = str(getattr(ope, "_phase", "") or "")

    # ── PER-FRAME MEMO ──
    # The decision — and therefore the carrier effects — belongs to the frame.
    if (int(getattr(world, CONTACT_AUTHORITY_FRAME_ATTR, -1) or -1) == frame
            and last_decision(world) is not None):
        return last_decision(world)

    # ── NO CARRIER: conservative, never forms ──
    # The live caller returns before the loop when there is no carrier, so
    # this branch is unreachable on the live path; it exists so the surface
    # is total and can never declare a contest without a carrier.
    if bc is None:
        decision = ContactDecision(
            frame=frame, seq=_current_seq(world), phase=phase,
            outcome=ContactOutcome.CONTACT_FAILED,
            reason=ContactFailureReason.NO_IN_RADIUS_BODY,
            winner_id=None, winner=None,
            in_radius_count=0, role_holder_count=0, evaluated_count=0,
            arm_grab_damping=False, glancing_displacement=None,
            bc_x=0.0, bc_y=0.0, bc_vx=0.0, bc_vy=0.0,
        )
        return _preserve(world, bc, defenders, _publish(world, decision))

    radius = contact_radius_m()
    trace = _trace_enabled()

    evidence: list = []
    role_holder_count = 0
    in_radius_count = 0
    fullback_skipped = 0
    role_less = 0
    band_seen: ContactFailureReason | None = None
    arm_grab_damping = False
    glancing_displacement: float | None = None
    contact_space_miss_events: list = []
    winner = None

    for p in defenders:
        if p is bc:
            continue

        # ── CONJUNCT 1: fullback tackle-eligibility ──
        if getattr(p, "fullback_role_active", False) and not getattr(
            p, "can_acquire_tackle", False
        ):
            fullback_skipped += 1
            continue
        if getattr(p, "fullback_role_active", False):
            from game.defence.defence_lvl_1.fullback_role import (
                fullback_retreat_10_compliant,
            )
            if not fullback_retreat_10_compliant(world, p):
                fullback_skipped += 1
                continue

        # ── CONJUNCT 2: tackle candidacy ──
        tackle_role = getattr(p, "tackle_role", None)
        if tackle_role not in TACKLE_ROLE_INITIATORS:
            # ── EMERGENCY FULLBACK INITIATION (2026-09-30) ──
            # Conjunct 1 above is an ALLOWANCE, not a gate: a tagged
            # fullback carrying ``can_acquire_tackle`` is meant to be
            # tackle-capable while a genuine break is live.  Conjunct 2
            # contradicted that, because the only stamps it accepts are the
            # lane roles and ``select_lane_tacklers`` grants PRIMARY to the
            # smallest ``|d.x - bc.x|`` candidate per side -- so a sweeper
            # the carrier has run PAST, chasing from behind, always loses
            # that slot and is demoted to LINE.  LINE can never start a
            # tackle sequence, so the pursuer closed all the way to contact
            # and then merely travelled alongside the carrier: contact with
            # no contest, and ``tes.py`` therefore never froze the carrier.
            #
            # Admission here is the lane system's OWN authority
            # (``fullback_emergency_defender_active``), so the fullback
            # becomes tackle-capable exactly when the lane system already
            # treats it as a legitimate emergency defender -- and never
            # otherwise.  The fullback is never re-stamped: this module
            # reads roles, it does not own them.
            #
            # The tag test is repeated HERE rather than being left to the
            # delegated predicate, because CONJUNCT 1 does NOT filter
            # non-fullbacks: an untagged body whose ``tackle_role`` is unset
            # also reaches this branch, and a single guard in one place makes
            # the scope of the admission locally evident.  Same structure as
            # ``intercept_tacklers._is_eligible_lane_defender``.
            if not (
                getattr(p, "fullback_role_active", False)
                and _fb_emergency_authority_active(world, p, bc)
            ):
                role_less += 1
                continue
        role_holder_count += 1

        # ── CONJUNCT 3: contact radius ──
        dist = math.hypot(bc.x - p.x, bc.y - p.y)
        if dist >= radius:
            evidence.append(_evidence_for(
                world, bc, p, dist, False, None,
                ContactFailureReason.OUT_OF_RADIUS,
            ))
            continue
        in_radius_count += 1

        # ── CONJUNCT 4: the scalar contact predicate ──
        pre_result = ope._check_pre_tackle(bc, p)

        if pre_result == VERDICT_MISS:
            if trace:
                print(f"[PRE_TACKLE] MISS - defender {p.player_id} whiffs")
            # ── CONTACT-SPACE MISS (2026-10-03) ──
            # The scalar read only angle and relative speed, so a MISS can land
            # with the bodies' surfaces touching.  That is a contact with no
            # consequence, not a whiff: apply the deterministic reaction to BOTH
            # bodies HERE, BEFORE the existing miss handling (the ``continue``
            # back into normal open-play recovery) proceeds.  Outside contact
            # space the call is a declared no-op, so an ordinary miss is
            # byte-for-byte unchanged.
            _miss_event = _apply_contact_space_miss(world, bc, p, tuners)
            if _miss_event is not None and _miss_event.applied:
                contact_space_miss_events.append(_miss_event)
            evidence.append(_evidence_for(
                world, bc, p, dist, True, pre_result,
                ContactFailureReason.SCORE_MISS,
            ))
            band_seen = _higher_band(band_seen, ContactFailureReason.SCORE_MISS)
            continue

        if pre_result == VERDICT_ARM_GRAB:
            if trace:
                print(f"[PRE_TACKLE] ARM_GRAB - defender {p.player_id} "
                      f"grabs, BC slowed")
            _apply_arm_grab(bc)
            arm_grab_damping = True
            evidence.append(_evidence_for(
                world, bc, p, dist, True, pre_result,
                ContactFailureReason.SCORE_ARM_GRAB,
            ))
            band_seen = _higher_band(
                band_seen, ContactFailureReason.SCORE_ARM_GRAB,
            )
            continue

        if pre_result == VERDICT_GLANCING:
            if trace:
                print(f"[PRE_TACKLE] GLANCING - defender {p.player_id} "
                      f"hits at angle, BC displaced")
            glancing_displacement = _apply_glancing(bc, p)
            evidence.append(_evidence_for(
                world, bc, p, dist, True, pre_result,
                ContactFailureReason.SCORE_GLANCING,
            ))
            band_seen = _higher_band(
                band_seen, ContactFailureReason.SCORE_GLANCING,
            )
            continue

        # ── CONJUNCT 5: the contest forms; the scan stops ──
        if trace:
            print(f"[TACKLE] defender {p.player_id} hit BC at dist={dist:.2f}")
        winner = p
        evidence.append(_evidence_for(
            world, bc, p, dist, True, pre_result,
            ContactFailureReason.FORMED,
        ))
        break

    # ── FRAME-LEVEL REASON ──
    if winner is not None:
        outcome = ContactOutcome.CONTACT_FORMED
        reason = ContactFailureReason.FORMED
    else:
        outcome = ContactOutcome.CONTACT_FAILED
        reason = _frame_reason(
            band_seen=band_seen,
            in_radius_count=in_radius_count,
            role_holder_count=role_holder_count,
            fullback_skipped=fullback_skipped,
            role_less=role_less,
            total=len(defenders) if defenders else 0,
        )

    decision = ContactDecision(
        frame=frame,
        seq=_current_seq(world),
        phase=phase,
        outcome=outcome,
        reason=reason,
        winner_id=(int(getattr(winner, "player_id", -1) or -1)
                   if winner is not None else None),
        winner=winner,
        in_radius_count=in_radius_count,
        role_holder_count=role_holder_count,
        evaluated_count=len(evidence),
        arm_grab_damping=arm_grab_damping,
        glancing_displacement=glancing_displacement,
        bc_x=float(getattr(bc, "x", 0.0) or 0.0),
        bc_y=float(getattr(bc, "y", 0.0) or 0.0),
        bc_vx=float(getattr(bc, "vx", 0.0) or 0.0),
        bc_vy=float(getattr(bc, "vy", 0.0) or 0.0),
        evidence=tuple(evidence),
        contact_space_miss_events=tuple(contact_space_miss_events),
        channel=(CHANNEL_MANAGER_TRIGGER if winner is not None else None),
    )
    final = _publish(world, decision)

    # STAGE 2B: a FORMED contest assembles ONE payload, here, at the moment of
    # declaration, and publishes it as the canonical declaration payload.  A
    # FAILED frame declares no contest, so it has no payload and the record is
    # derived from the decision alone.  The payload is assembled BEFORE the
    # recorder runs so the recorder projects it instead of re-deriving the
    # same facts — one declaration, one representation.
    contest = None
    if winner is not None:
        contest = _assemble_contest(
            world, bc, final, winner, CHANNEL_MANAGER_TRIGGER, defenders,
        )
        _publish_contest(world, contest)
        # ── STAGE 2: FORM AND LATCH THE PERSISTENT TACKLE STATE ──
        # The contest EXISTS as of this declaration.  Capture its initial
        # conditions here — at the declaration geometry, before TES has touched
        # a body — and latch exactly one TackleState, transferring ownership to
        # TES, which is from here on the sole authority for evolving it.  This
        # is also the frame the forward-progress origin is anchored to.
        _form_tackle_state(
            world, bc, winner, final, CHANNEL_MANAGER_TRIGGER, True,
        )
    return _preserve(world, bc, defenders, final, contest)


def _higher_band(current, candidate):
    """Return whichever band is closer to a formed contest."""
    if current is None:
        return candidate
    return candidate if _BAND_RANK.get(candidate, 0) > _BAND_RANK.get(current, 0) \
        else current


def _frame_reason(band_seen, in_radius_count, role_holder_count,
                  fullback_skipped, role_less, total) -> ContactFailureReason:
    """Name the exact conjunct at which THIS frame's contest failed.

    Priority is "closest to forming first": a body that reached a score band
    outranks a body that was merely in radius, which outranks a role/geometry
    failure, which outranks an empty frame.  The per-body ``failed_at`` rows
    in ``evidence`` carry the detail; this is the one-line answer.
    """
    if band_seen is not None:
        return band_seen
    if in_radius_count > 0:
        # In radius, none of them produced a verdict at all: only reachable if
        # the predicate returned an unknown string.  Report the weakest band.
        return ContactFailureReason.SCORE_MISS
    if role_holder_count > 0:
        return ContactFailureReason.OUT_OF_RADIUS
    if fullback_skipped > 0 and fullback_skipped == total:
        return ContactFailureReason.FULLBACK_WITHOUT_AUTHORITY
    if role_less > 0 or fullback_skipped > 0:
        return ContactFailureReason.NOT_TACKLE_ROLE
    return ContactFailureReason.NO_IN_RADIUS_BODY


# ─────────────────────────────────────────────────────────────────────
# PUBLICATION + SURFACE READS
# ─────────────────────────────────────────────────────────────────────

def _preserve(world, bc, defenders, decision, contest=None) -> ContactDecision:
    """STAGE 2A: preserve the COMPLETE formation state, then return the decision.

    The decision is returned UNCHANGED and is published BEFORE this runs, so
    the record is taken at the moment the contest was declared on.  The reach
    is FUNCTION-LOCAL (``world.open_play_engine`` imports the manager at module
    scope, so a top-level project import would close a cycle).

    This call is BEHAVIOUR-NEUTRAL BY CONSTRUCTION.  ``contest_preservation``
    takes no decision, consults no contact predicate, calls no TES entry and
    stores nothing but the two passive world attributes it owns, so nothing it
    does can reach a winner, a tackle outcome, an engagement decision or the
    simulation flow.

    IT IS DELIBERATELY NOT WRAPPED IN A try/except.  A recorder that silently
    failed would leave the boundary losing information while every check still
    passed — precisely the "declaration false as shipped" defect Stage 1B
    found by measurement.  A recording fault must surface loudly instead.
    """
    from game.defence.defence_lvl_1.contest_preservation import (
        preserve_decider_decision,
    )
    preserve_decider_decision(world, bc, defenders, decision, contest)
    return decision


def _preserve_channel(world, bc, winner, channel, defenders) -> None:
    """STAGE 2A: preserve an ENGAGE-CHANNEL admission before the handover.

    Called at ``enter_tes`` BEFORE the engine entry, so the population is
    measured at the declaration geometry rather than after TES has frozen the
    bodies.  Stage 1B published NOTHING for these four channels — no evaluated
    bodies, no in-radius candidates, no formation evidence — because their
    admitting predicate (``try_pre_tackle`` ->
    ``pretackle_machine.should_engage``) lives outside the authority.  This is
    the call that ends that loss.
    """
    from game.defence.defence_lvl_1.contest_preservation import (
        preserve_channel_admission,
    )
    # STAGE 2B: the canonical payload for THIS admission was assembled and
    # published a few statements above, so the declaration reaches the
    # recorder WITH it instead of the recorder re-deriving the same facts.
    preserve_channel_admission(
        world, bc, winner, channel, defenders, _live_contest(world),
    )


def _publish_contest(world, contest) -> None:
    """STAGE 2B: publish the canonical declaration payload (setattr only).

    Function-local reach, like every other project reach in this module: the
    payload module is a pure leaf, but reaching it locally keeps Stage 2B out
    of the authority's import graph exactly as Stage 2A's recorder is kept out.
    """
    from game.defence.defence_lvl_1.contest import publish_contest
    publish_contest(world, contest)


def _live_contest(world):
    """STAGE 2B: the live declaration payload on *world* (or ``None``)."""
    from game.defence.defence_lvl_1.contest import live_contest
    return live_contest(world)


def _reset_contest(world) -> None:
    """STAGE 2B: drop the declaration payload at a fresh sequence."""
    from game.defence.defence_lvl_1.contest import reset_contest
    reset_contest(world)


def _form_tackle_state(world, bc, winner, decision, channel,
                       admitted_by_decider) -> None:
    """STAGE 2: FORM AND LATCH the persistent TackleState for a declaration.

    THE formation call.  Invoked at each of this module's declaration points,
    immediately AFTER the declaration has been taken, so the captured initial
    conditions are read at the DECLARATION GEOMETRY rather than after TES has
    begun to drive the bodies.

    IT CAPTURES; IT DOES NOT DECIDE.  ``game.tes.tackle_state`` reads the
    carrier and the winning defender, composes one state and latches it, and
    writes nothing else.  The callers reach this function ONLY on a FORMED
    outcome, so a failed frame can never produce a TackleState; and because the
    latch is idempotent per declaration, the manager-channel hand-over cannot
    mint a second contest for one declaration.

    Reaching the module FUNCTION-LOCALLY keeps Stage 2 out of this module's
    import graph exactly as the Stage 2A recorder and the Stage 2B payload are
    kept out: ``world.open_play_engine`` imports the manager that imports this
    module, so a top-level project reach would close a cycle.
    """
    if winner is None or decision is None or bc is None:
        return
    from game.tes.tackle_state import form_tackle_state
    form_tackle_state(
        world, bc, winner,
        frame=int(getattr(decision, "frame", 0) or 0),
        seq=int(getattr(decision, "seq", 0) or 0),
        phase=str(getattr(decision, "phase", "") or ""),
        channel=str(channel or ""),
        admitted_by_decider=bool(admitted_by_decider),
    )


def _evaluated_ids(decision, winner_id) -> tuple:
    """The ids the declaration took a contest decision about.

    The decider's own evaluated rows when it ran (the manager channel), plus
    the admitted body when a behaviour-layer predicate admitted the contest
    (the four engage channels).  Membership only — the record's view of it is
    built in candidate order.
    """
    ids = []
    for row in (getattr(decision, "evidence", None) or ()):
        pid = int(getattr(row, "player_id", -1) or -1)
        if pid not in ids:
            ids.append(pid)
    if winner_id is not None and int(winner_id) >= 0:
        if int(winner_id) not in ids:
            ids.append(int(winner_id))
    return tuple(ids)


def _in_radius_ids(world, bc, defenders) -> tuple:
    """The ids of the bodies inside the contact radius at declaration.

    Measured with the SAME radius owner the decider read (never a tuner) and
    the SAME population the frame's formation logic reasoned over, so the
    payload's in-radius set IS the declaration geometry rather than a second,
    independently derived opinion about it.
    """
    if bc is None:
        return ()
    radius = contact_radius_m()
    population = defenders
    if population is None:
        population = getattr(world, "defending_team", None)
    ids = []
    for body in (population or ()):
        if body is bc:
            continue
        dist = math.hypot(
            float(getattr(bc, "x", 0.0) or 0.0)
            - float(getattr(body, "x", 0.0) or 0.0),
            float(getattr(bc, "y", 0.0) or 0.0)
            - float(getattr(body, "y", 0.0) or 0.0),
        )
        if dist < radius:
            pid = int(getattr(body, "player_id", -1) or -1)
            if pid not in ids:
                ids.append(pid)
    return tuple(ids)


def _assemble_contest(world, bc, decision, winner, channel, defenders):
    """STAGE 2B: ASSEMBLE the canonical declaration payload.

    Contact Authority assembles ONE Contest per declaration and publishes it as
    the canonical declaration payload.  Every field is a fact already known at
    declaration time; this function reads and decides nothing new, and it
    writes to no body, so assembling a payload cannot change a frame.
    """
    from game.defence.defence_lvl_1.contest import build_contest
    winner_id = int(getattr(winner, "player_id", -1) or -1)
    outcome = getattr(decision, "outcome", None)
    reason = getattr(decision, "reason", None)
    return build_contest(
        frame=int(getattr(world, "frame_count", 0) or 0),
        seq=_current_seq(world),
        phase=str(getattr(decision, "phase", "") or "")
        or str(getattr(world, "_phase", "") or ""),
        channel=channel,
        outcome=(outcome.value if outcome is not None
                 else ContactOutcome.CONTACT_FORMED.value),
        reason=(reason.value if reason is not None
                else ContactFailureReason.FORMED.value),
        winner=winner,
        winner_id=winner_id,
        evaluated_ids=_evaluated_ids(decision, winner_id),
        in_radius_ids=_in_radius_ids(world, bc, defenders),
        radius_m=contact_radius_m(),
        arm_grab_damping=bool(getattr(decision, "arm_grab_damping", False)),
        glancing_displacement=getattr(decision, "glancing_displacement", None),
    )


def _current_seq(world) -> int:
    """The current contest sequence number (never raises)."""
    try:
        return int(getattr(world, CONTACT_AUTHORITY_SEQ_ATTR, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _publish(world, decision: ContactDecision,
             memo: bool = True) -> ContactDecision:
    """Publish the decision on the world and record it for the audit.

    The published pair (``contact_authority_decision`` /
    ``contact_authority_seq``) IS the observable ownership boundary.  Nothing
    in the live engine reads them yet.  Publishing is behaviour-neutral: it
    writes two passive world attributes and a bounded in-memory ring.

    THE SEQUENCE NUMBERS EVERY DECISION — formed OR failed — so an audit can
    line up a frame with the exactly one decision taken on it.
    """
    # ── THE SEQUENCE NUMBERS EVERY DECISION, FORMED OR FAILED ──────────
    # Numbering only the FORMED contests left a failed frame reading seq=0
    # for ever and never published ``contact_authority_seq`` at all, so an
    # audit could not line a frame up with the exactly one decision taken on
    # it.  Measured by probe_contact_authority_formation.py [A]
    # (2026-09-19): twelve consecutive MISS frames each reported seq=None.
    # Republishing is still behaviour-neutral — the two attributes and the
    # ring are read by nothing in the live loop.
    seq = _current_seq(world) + 1
    try:
        setattr(world, CONTACT_AUTHORITY_SEQ_ATTR, seq)
    except Exception:
        seq = decision.seq
    final = ContactDecision(
        frame=decision.frame, seq=seq, phase=decision.phase,
        outcome=decision.outcome, reason=decision.reason,
        winner_id=decision.winner_id, winner=decision.winner,
        in_radius_count=decision.in_radius_count,
        role_holder_count=decision.role_holder_count,
        evaluated_count=decision.evaluated_count,
        arm_grab_damping=decision.arm_grab_damping,
        glancing_displacement=decision.glancing_displacement,
        bc_x=decision.bc_x, bc_y=decision.bc_y,
        bc_vx=decision.bc_vx, bc_vy=decision.bc_vy,
        evidence=decision.evidence,
        contact_space_miss_events=decision.contact_space_miss_events,
        channel=decision.channel,
    )

    try:
        setattr(world, CONTACT_AUTHORITY_DECISION_ATTR, final)
        # ── THE MEMO IS THE MANAGER CHANNEL'S OWN ──
        # An engage-channel formation (``enter_tes`` with memo=False) must
        # NOT poison the manager-channel memo: the manager's
        # ``evaluate_contact`` still runs later in the SAME frame and must
        # still evaluate (its ARM_GRAB / GLANCING carrier effects are part
        # of the live behaviour).  Only a decision that actually owns the
        # frame — the manager's evaluation — stamps the memo.
        if memo:
            setattr(world, CONTACT_AUTHORITY_FRAME_ATTR, final.frame)
        # ── THE CONTEST RECORD ──
        # Set only by a CONTACT_FORMED publish, so exactly one record exists
        # per formed contest, whichever channel formed it.
        if final.outcome is ContactOutcome.CONTACT_FORMED:
            setattr(world, CONTACT_AUTHORITY_CONTEST_ATTR, final)
    except Exception:
        pass

    _DECISION_RING.append(final)
    if len(_DECISION_RING) > DECISION_RING_FRAMES:
        del _DECISION_RING[:-DECISION_RING_FRAMES]

    if audit_enabled():
        record_decision_row(final)

    return final


def last_decision(world) -> ContactDecision | None:
    """The most recent published decision for *world* (or ``None``)."""
    return getattr(world, CONTACT_AUTHORITY_DECISION_ATTR, None)


def recent_decisions() -> tuple:
    """The in-memory decision ring (most recent last).  Audit/probe only."""
    return tuple(_DECISION_RING)


def reset_contact_authority(world=None) -> None:
    """Drop the authority's ledger (kickoff / fresh sequence).

    Clears the ring and, when *world* is given, the published surface.  It
    introduces no engine state and is read by nothing in the live loop.
    """
    global _DECISION_RING
    _DECISION_RING = []
    _reset_contest(world)
    if world is None:
        return
    for attr in (CONTACT_AUTHORITY_DECISION_ATTR,
                 CONTACT_AUTHORITY_CONTEST_ATTR,
                 CONTACT_AUTHORITY_FRAME_ATTR):
        try:
            delattr(world, attr)
        except AttributeError:
            pass


def last_contest(world) -> ContactDecision | None:
    """The most recent decision that FORMED a contest for *world*.

    This is the authoritative one-contest-per-formation record.  Unlike
    ``last_decision`` (the latest decision of any kind, which a
    manager-channel evaluation can overwrite in the same frame), this is set
    only by a CONTACT_FORMED publish — so counting the frames that carry a
    contest is counting the contests, whichever channel admitted them.
    """
    return getattr(world, CONTACT_AUTHORITY_CONTEST_ATTR, None)


def enter_tes(world, ope, bc, winner, channel: str,
              already_decided: bool = False,
              defenders=None) -> ContactDecision | None:
    """THE single TES-entry choke point.  Every contest passes through here.

    ``winner`` is the body the admitting predicate selected.  This function
    publishes the formation with its ``channel`` (unless the caller already
    published it, i.e. the manager channel whose ``evaluate_contact`` produced
    the decision) and then delegates to the engine's UNCHANGED single entry
    point ``OpenPlayEngine._start_tackle(bc, winner)``.

    It is behaviour-preserving by construction: the only side effects are
    (a) the passive world attributes / ring / optional CSV audit this module
    already owns, and (b) exactly the ``_start_tackle`` call the caller made
    before.  No decision is taken here, no predicate is consulted, no engine
    state is written.

    ``already_decided`` is set by the manager channel, whose
    ``evaluate_contact`` has already published this frame's decision; the
    call then only performs the handover.

    Returns the published ``ContactDecision`` (or the existing one when
    ``already_decided``), for the caller's own logging; the return value is
    never required.
    """
    if already_decided:
        # The manager channel DECLARED this contest's payload inside
        # ``evaluate_contact`` above, so the hand-over REUSES that one
        # declaration instead of assembling a second representation of it.
        contest = _live_contest(world)
        if contest is None:
            contest = _assemble_contest(
                world, bc, last_decision(world), winner, channel, defenders,
            )
            _publish_contest(world, contest)
        # ── STAGE 2: THE HAND-OVER MAY NOT MINT A SECOND CONTEST ──
        # The manager channel already formed this contest inside
        # ``evaluate_contact``, so this call is idempotent per declaration and
        # returns the latched state unchanged.  It reads the last decision to
        # recover that declaration's identity; when the manager never ran (a
        # detached caller) there is no decision and nothing is formed.
        _form_tackle_state(
            world, bc, winner, last_decision(world), channel, True,
        )
        ope._start_tackle(bc, contest)
        return last_decision(world)

    frame = int(getattr(world, "frame_count", 0) or 0)
    decision = ContactDecision(
        frame=frame,
        seq=_current_seq(world),
        phase=str(getattr(ope, "_phase", "") or ""),
        outcome=ContactOutcome.CONTACT_FORMED,
        reason=ContactFailureReason.FORMED,
        winner_id=int(getattr(winner, "player_id", -1) or -1),
        winner=winner,
        in_radius_count=0,
        role_holder_count=0,
        evaluated_count=0,
        arm_grab_damping=False,
        glancing_displacement=None,
        bc_x=float(getattr(bc, "x", 0.0) or 0.0),
        bc_y=float(getattr(bc, "y", 0.0) or 0.0),
        bc_vx=float(getattr(bc, "vx", 0.0) or 0.0),
        bc_vy=float(getattr(bc, "vy", 0.0) or 0.0),
        evidence=(),
        channel=channel,
    )
    published = _publish(world, decision, memo=False)

    # STAGE 2B: an engage channel DECLARES its contest here, at the admission.
    # The payload assembled from that declaration crosses the TES boundary
    # below in place of the bare winner.
    contest = _assemble_contest(world, bc, published, winner, channel, defenders)
    _publish_contest(world, contest)
    # ── STAGE 2A: PRESERVE THE COMPLETE FORMATION STATE ──
    # Taken BEFORE the handover, while the geometry is still the declaration
    # geometry: WHO was in radius, WHAT each body's declared stamps were, and
    # WHICH predicate admitted the contest.  Recording here cannot influence
    # the handover, which is the next statement and is unchanged.
    # ── STAGE 2: FORM AND LATCH THE PERSISTENT TACKLE STATE ──
    # An ENGAGE-channel admission is a declaration too: a behaviour-layer
    # predicate (``try_pre_tackle`` -> ``pretackle_machine.should_engage``)
    # admitted the contest, so it latches exactly one TackleState here, on the
    # same terms as the manager channel.  It is taken at the ADMISSION
    # GEOMETRY, so the captured initial conditions are the ones the admitting
    # predicate reasoned about rather than the post-freeze ones.
    #
    # It is placed BEFORE the Stage 2A preservation because the two are pure
    # captures of the same instant and neither writes to a body; keeping the
    # preserve adjacent to the hand-over leaves Stage 2A's recorded
    # declaration -> handover adjacency intact.
    _form_tackle_state(world, bc, winner, published, channel, False)
    _preserve_channel(world, bc, winner, channel, defenders)
    ope._start_tackle(bc, contest)
    return published


# ─────────────────────────────────────────────────────────────────────
# AUDIT SINK — identifies exactly why a contest formed or failed
# ─────────────────────────────────────────────────────────────────────

_FRAME_COLUMNS = [
    "row_kind", "frame", "seq", "phase", "outcome", "reason",
    "winner_id", "in_radius_count", "role_holder_count", "evaluated_count",
    "arm_grab_damping", "glancing_displacement",
    "bc_x", "bc_y", "bc_vx", "bc_vy",
    "body_id", "defender_role", "tackle_role", "depth_class", "dist",
    "in_radius", "predicate_verdict", "pre_tackle_state",
    "corridor_owned", "corridor_frames", "slot_eligible",
    "engage_guard", "engage_guard_stamped", "engage_state_last",
    "retreat_obligated", "failed_at",
    "channel",
]


def _blank_row() -> dict:
    """A row with every declared column present and empty."""
    return {name: "" for name in _FRAME_COLUMNS}


def record_decision_row(decision: ContactDecision) -> None:
    """Append this decision (one frame row + one row per body) to the CSV.

    Read-only with respect to the engine: it writes the audit file only.  The
    frame row answers "did a contest form, and if not why"; the body rows
    answer "and what did each candidate look like at that moment".

    ROWS ARE COLUMN-KEYED.  Each row is built as a dict keyed by the declared
    column name and written in ``_FRAME_COLUMNS`` order through
    ``csv.DictWriter``, so a row can never drift out of alignment with the
    header and a new column cannot silently land in the wrong field.  (The
    earlier positional writer emitted one field too few on the frame row, so
    the ``channel`` value was read back as part of the previous column.)
    """
    path = audit_sink_path()
    needs_header = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_FRAME_COLUMNS)
        if needs_header:
            writer.writeheader()

        frame_row = _blank_row()
        frame_row.update({
            "row_kind": "frame",
            "frame": decision.frame,
            "seq": decision.seq,
            "phase": decision.phase,
            "outcome": decision.outcome.value,
            "reason": decision.reason.value,
            "winner_id": (decision.winner_id
                          if decision.winner_id is not None else ""),
            "in_radius_count": decision.in_radius_count,
            "role_holder_count": decision.role_holder_count,
            "evaluated_count": decision.evaluated_count,
            "arm_grab_damping": int(bool(decision.arm_grab_damping)),
            "glancing_displacement": (
                "" if decision.glancing_displacement is None
                else decision.glancing_displacement),
            "bc_x": round(decision.bc_x, 4),
            "bc_y": round(decision.bc_y, 4),
            "bc_vx": round(decision.bc_vx, 4),
            "bc_vy": round(decision.bc_vy, 4),
            "channel": decision.channel or "",
        })
        writer.writerow(frame_row)

        for row in decision.evidence:
            body_row = _blank_row()
            body_row.update({
                "row_kind": "body",
                "frame": decision.frame,
                "seq": decision.seq,
                "phase": decision.phase,
                "outcome": decision.outcome.value,
                "reason": decision.reason.value,
                "winner_id": (decision.winner_id
                              if decision.winner_id is not None else ""),
                "body_id": row.player_id,
                "defender_role": row.defender_role,
                "tackle_role": row.tackle_role or "",
                "depth_class": row.depth_class or "",
                "dist": round(row.dist, 4),
                "in_radius": int(bool(row.in_radius)),
                "predicate_verdict": row.predicate_verdict or "",
                "pre_tackle_state": row.pre_tackle_state or "",
                "corridor_owned": int(bool(row.corridor_owned)),
                "corridor_frames": row.corridor_frames,
                "slot_eligible": ("" if row.slot_eligible is None
                                  else int(bool(row.slot_eligible))),
                "engage_guard": int(bool(row.engage_guard)),
                "engage_guard_stamped": int(
                    bool(row.engage_guard_stamped)),
                "engage_state_last": row.engage_state_last or "",
                "retreat_obligated": int(bool(row.retreat_obligated)),
                "failed_at": row.failed_at.value,
                "channel": decision.channel or "",
            })
            writer.writerow(body_row)


def format_contact_authority_report(world) -> str:
    """Compact one-line rendering of the latest formation decision."""
    decision = last_decision(world)
    if decision is None:
        return "contact_authority=no_decision"
    if decision.outcome is ContactOutcome.CONTACT_FORMED:
        return (
            "contact_authority=FORMED seq=%d frame=%d winner=%s "
            "in_radius=%d role_holders=%d"
            % (decision.seq, decision.frame, decision.winner_id,
               decision.in_radius_count, decision.role_holder_count)
        )
    return (
        "contact_authority=FAILED reason=%s frame=%d in_radius=%d "
        "role_holders=%d evaluated=%d"
        % (decision.reason.value, decision.frame, decision.in_radius_count,
           decision.role_holder_count, decision.evaluated_count)
    )
