"""
game/defence/defence_lvl_1/fullback_controller.py — FULLBACK INTENT OWNERSHIP.

STAGE 1 OF THE FULLBACK CONTROLLER (Intent Ownership)
=====================================================
Goal: centralise DECISION ownership of the dynamic fullback without changing
behaviour.  Stage 1 introduces the owner and the contract; it does not yet
move the tag (Stage 2), the recovery contract (Stage 3) or the locomotion
(Stage 4).

THE PROBLEM
-----------
The dynamic fullback is written by TWO systems that do not know about each
other.  ``fullback_role`` decides the sweeper anchor, the breakaway chase and
the owed-ground recovery; the shared Movement Mode Decision Layer
(``movement_mode_decision``) independently decides DRIFT-or-CHASE for the
same body.  Neither is the owner, so on a live break the two can disagree
about what the last defender is doing — the split-brain the stage plan calls
"repeated conflicts in state, recovery, freeze, pursuit and locomotion
authority".

THE STAGE-1 LAW
---------------
There is exactly ONE intent vocabulary (``FullbackIntent``), exactly ONE
mapping from intent to ``MovementState`` (``INTENT_MOVEMENT_STATE``), and
exactly ONE function that may write a tagged fullback's ``movement_state``
(``enter_fullback_intent``).  Every producer of a fullback movement state —
``fullback_role.apply_fullback_geometry``, its flight branch, its breakaway
branch, its pin-clearance branch, ``establish_fullback_live_break_chase``,
``fullback_role.assign_fullback_role``, ``kickoff_engine`` and the MMDL
fullback branch — now states its INTENT and lets this module resolve the
state.

THE INTENTS
-----------
    UNOWNED   the body is not this controller's to command this frame — a
              tackle-side system, TES, a tackle commit, or a kickoff flight
              that has not opted in.  NO movement state is written.  This is
              the honest name for the five early yields the geometry writer
              has always performed: they are "not mine", not "sweeping".
    SWEEP     hold the deep backfield anchor: the sweeper contract.
    CHASE     pursue the carrier on a genuine line break: the intercept
              contract.
    RECOVER   run home to the owned slot when the actor genuinely owes
              ground: the high-velocity recovery contract.
    FLIGHT    the kickoff ball is in the air and the flight authority has
              opted in: hold the airborne anchor.

BEHAVIOUR NEUTRALITY
--------------------
``INTENT_MOVEMENT_STATE`` is a total mapping onto the SAME ``MovementState``
members the call sites wrote literally before this module existed, so every
``enter_fullback_intent(..., CHASE)`` writes the identical enum singleton a
literal ``MovementState.CHASE`` write did.  Enum members are singletons, so
the write is the same object by construction — this stage changes WHO names
the decision, not what is decided.

The telemetry stamps (``_fb_intent`` / ``_fb_intent_source`` / ...) are the
only new runtime footprint.  No production reader consumes them; the audits,
the probes and ``verify_fullback_intent_ownership_contract.py`` do.  The
``RLM_FB_INTENT_CONTROLLER=0`` lever suppresses them so the pre-stage runtime
footprint is restorable byte-for-byte with no code revert.

STAGE 2 OF THE FULLBACK CONTROLLER (State Ownership)
``RLM_FB_STATE_CONTROLLER`` (default ON) makes this controller the SOLE
authority for a tagged fullback's ``movement_state`` for as long as a BREAK
EMERGENCY is live — i.e. while the per-actor break latch
(``fullback_role.is_fullback_break_latched``) is armed.  The shared defender
state policy (the Movement Mode Decision Layer and the sweeper pin in
``defensive_behaviour_unified``) must not name a state for that body until
the emergency ends.  When the latch clears the shared policy resumes
unchanged.

THE SPLIT-BRAIN THIS REMOVES
----------------------------
``fullback_role.apply_fullback_geometry`` arms the break latch on BOTH break
kinds (DEPTH and LATERAL) and writes ``CHASE``.  The sweeper pin in
``defensive_behaviour_unified`` re-wrote ``MovementState.DRIFT`` whenever
``fullback_breakaway_live`` was False — a predicate that admits a DEPTH latch
but not a LATERAL one.  On a lateral channel breach the pin therefore
overwrote the chase the geometry authority had just ordered, leaving
``_fb_intent = CHASE`` against a live state of ``DRIFT``: the CHASE/DRIFT
split-brain the Stage-1 report measures and this stage removes.

WHAT THIS STAGE DELIBERATELY DOES NOT DO
----------------------------------------
  * It does NOT move the geometry writer out of ``fullback_role``.  Eight
    existing verifiers pin exact strings INSIDE that writer's body
    (``verify_fullback_line_break_chase.py``, ``verify_fullback_break_latch_
    contract.py``, ``verify_fullback_line_occupied_contract.py``,
    ``verify_fullback_recover_contract.py`` are the four that read the file
    as text), so relocating it would be a test-breaking move rather than a
    behaviour-neutral one.  The evidence chain therefore still runs where it
    runs; what it now does at every decision point is NAME the intent.
  * It does NOT change the tag, the freeze contract, the pursuit geometry or
    any speed.  Those are Stages 3 and 4.
"""

from __future__ import annotations

import os
from enum import Enum, auto
from typing import List, Optional

from player.movement_states import MovementState


# ═══════════════════════════════════════════════════════════════════════
# A-B / ROLLBACK LEVER
# ═══════════════════════════════════════════════════════════════════════
# Default ON.  OFF suppresses the intent telemetry stamps, restoring the
# pre-stage runtime footprint with no code revert.  The STATE WRITTEN IS
# IDENTICAL EITHER WAY — the lever gates bookkeeping, never behaviour — so it
# is a true no-op fallback rather than a behavioural lever.  Read through the
# module boolean on purpose so a verifier or a probe can A/B in-process by
# rebinding ``fullback_controller.FB_INTENT_CONTROLLER`` (the same pattern
# every other runtime lever in this package uses).
FB_INTENT_CONTROLLER_ENV_VAR: str = "RLM_FB_INTENT_CONTROLLER"
FB_INTENT_CONTROLLER: bool = (
    os.environ.get(FB_INTENT_CONTROLLER_ENV_VAR, "1").strip().lower()
    not in ("0", "false", "no", "off", "")
)


def fullback_intent_controller_enabled() -> bool:
    """Whether the controller stamps intent telemetry.  Default ON."""
    return bool(FB_INTENT_CONTROLLER)


# ═══════════════════════════════════════════════════════════════════════
# STAGE 2 — STATE OWNERSHIP LEVER
# ═══════════════════════════════════════════════════════════════════════
# Default ON.  ON makes this controller the SOLE authority for a tagged
# fullback's ``movement_state`` while a break emergency is live.  OFF restores
# the Stage-1 arrangement exactly (the shared state policy may name the state
# even on a latched break) with no code revert — the same in-process A/B
# pattern the intent lever above uses, so a verifier or a probe can rebind
# ``fullback_controller.FB_STATE_CONTROLLER``.
FB_STATE_CONTROLLER_ENV_VAR: str = "RLM_FB_STATE_CONTROLLER"
FB_STATE_CONTROLLER: bool = (
    os.environ.get(FB_STATE_CONTROLLER_ENV_VAR, "1").strip().lower()
    not in ("0", "false", "no", "off", "")
)


def fullback_state_controller_enabled() -> bool:
    """Whether the controller owns the state during a break emergency.

    Default ON.  OFF restores Stage-1 behaviour: the break emergency no
    longer suspends the shared state policy.
    """
    return bool(FB_STATE_CONTROLLER)


# ═══════════════════════════════════════════════════════════════════════
# THE INTENT VOCABULARY
# ═══════════════════════════════════════════════════════════════════════

class FullbackIntent(Enum):
    """What the dynamic fullback is being asked to DO this frame.

    This is the single vocabulary the whole fullback system speaks.  A
    producer states one of these; this module is the only thing that turns it
    into a ``MovementState``.
    """
    UNOWNED = auto()   # not this controller's body this frame; write nothing
    SWEEP = auto()     # hold the deep backfield anchor
    CHASE = auto()     # pursue the carrier on a line break (intercept)
    RECOVER = auto()   # run home to the owned slot (owed ground)
    FLIGHT = auto()    # kickoff ball airborne; hold the flight anchor


# The intent -> movement-state law.  The RIGHT-HAND SIDE is exactly the
# ``MovementState`` each call site wrote literally before this module owned
# the decision, which is what makes this stage behaviour-neutral: the mapping
# is new, the values are the historical ones.
#
# ``UNOWNED`` maps to ``None`` ON PURPOSE.  A yield is not a movement
# command — the geometry writer's five early returns have never written a
# state, and this module must not invent one on their behalf.
INTENT_MOVEMENT_STATE: dict = {
    FullbackIntent.UNOWNED: None,
    FullbackIntent.SWEEP: MovementState.DRIFT,
    FullbackIntent.CHASE: MovementState.CHASE,
    FullbackIntent.RECOVER: MovementState.RECOVER,
    FullbackIntent.FLIGHT: MovementState.DRIFT,
}

INTENTS: tuple = (
    FullbackIntent.UNOWNED,
    FullbackIntent.SWEEP,
    FullbackIntent.CHASE,
    FullbackIntent.RECOVER,
    FullbackIntent.FLIGHT,
)


def intent_movement_state(intent) -> Optional[MovementState]:
    """Resolve *intent* to the movement state it commands.

    Returns ``None`` for ``UNOWNED`` (and for anything unrecognised), which
    means "this controller writes no movement state for this intent".
    """
    return INTENT_MOVEMENT_STATE.get(intent)


def intent_name(intent) -> str:
    """Stable string name for *intent* (telemetry, logs, verifiers)."""
    return getattr(intent, "name", None) or str(intent)


def intent_for_state(state) -> FullbackIntent:
    """Classify a *live* ``MovementState`` back into an intent.

    The inverse of ``intent_movement_state``, for consumers that only have
    the actor's state (the audits, the probes, the MMDL's own read).

    ``DRIFT`` classifies as ``SWEEP``: the sweeper anchor and the kickoff
    flight anchor are the same movement state, and SWEEP is the dominant
    producer.  A caller that needs the FLIGHT reading has the kickoff context
    and should say so — see ``live_fullback_intent``, which does exactly
    that rather than guessing here.

    ``HOLD`` and every state this controller never authors classify as
    ``UNOWNED``: a held or RETREAT/SLIDE/SHOOT fullback is a body some other
    authority has taken, which is precisely what UNOWNED means.
    """
    if state is MovementState.CHASE:
        return FullbackIntent.CHASE
    if state is MovementState.RECOVER:
        return FullbackIntent.RECOVER
    if state is MovementState.DRIFT:
        return FullbackIntent.SWEEP
    return FullbackIntent.UNOWNED


# ═══════════════════════════════════════════════════════════════════════
# INTENT TRANSITIONS
# ═══════════════════════════════════════════════════════════════════════
# The legal handover graph.  Every edge the live engine can actually take is
# declared here so a future stage can assert it instead of hoping; the graph
# is deliberately permissive (it mirrors the real rollouts, not an idealised
# state machine) and its only hard rules are:
#
#   * ANY intent may fall back to UNOWNED — a tackle-side system, a TES
#     attachment or a tackle commit can take the body at any moment, and the
#     geometry writer yields when they do.
#   * UNOWNED carries no movement state, so it is the only intent that may be
#     entered without writing one.
#   * anything may be entered from UNOWNED — the controller re-asserts the
#     moment the other authority releases the body, which is the documented
#     "the sweeper anchor is re-asserted on the very next frame" contract.
INTENT_TRANSITIONS: dict = {
    FullbackIntent.UNOWNED: frozenset({
        FullbackIntent.SWEEP, FullbackIntent.CHASE,
        FullbackIntent.RECOVER, FullbackIntent.FLIGHT,
    }),
    FullbackIntent.SWEEP: frozenset({
        FullbackIntent.CHASE, FullbackIntent.RECOVER,
        FullbackIntent.FLIGHT, FullbackIntent.UNOWNED,
    }),
    FullbackIntent.CHASE: frozenset({
        FullbackIntent.SWEEP, FullbackIntent.RECOVER, FullbackIntent.UNOWNED,
    }),
    FullbackIntent.RECOVER: frozenset({
        FullbackIntent.SWEEP, FullbackIntent.CHASE, FullbackIntent.UNOWNED,
    }),
    FullbackIntent.FLIGHT: frozenset({
        FullbackIntent.SWEEP, FullbackIntent.CHASE,
        FullbackIntent.RECOVER, FullbackIntent.UNOWNED,
    }),
}


def is_legal_intent_transition(
    current: FullbackIntent, next_intent: FullbackIntent
) -> bool:
    """True when *current* -> *next_intent* is a declared handover."""
    if current is next_intent:
        return True
    return next_intent in INTENT_TRANSITIONS.get(current, frozenset())


# ═══════════════════════════════════════════════════════════════════════
# INTENT SOURCES (why the controller chose what it chose)
# ═══════════════════════════════════════════════════════════════════════
# Named constants so the producers cannot drift apart in their telemetry —
# the same discipline the rest of this package applies to its levers.  These
# are diagnostic only; nothing branches on them.
SOURCE_FLIGHT: str = "kickoff_flight"
SOURCE_LIVE_BREAK_HANDOVER: str = "live_break_handover"
SOURCE_PIN_CLEARED: str = "static_pin_cleared"
SOURCE_BREAK_DEPTH: str = "break_depth"
SOURCE_BREAK_LATERAL: str = "break_lateral"
SOURCE_RECOVERY: str = "recovery_debt"
SOURCE_ANCHOR: str = "sweeper_anchor"
SOURCE_ROLE_ASSIGN: str = "role_assignment"
SOURCE_MMDL_POLICY: str = "mmdl_policy"
# Stage 2: the controller entered the emergency state itself, because the
# shared defender state policy was suspended by the live break latch.
SOURCE_STATE_OWNERSHIP: str = "break_latch_state_ownership"
# Stage 2: the shared policy named the state on a frame the controller did
# NOT own (no break emergency live).  The gate's own stamp, so an audit can
# tell a policy frame from an emergency frame without guessing.
SOURCE_SHARED_POLICY: str = "shared_state_policy"


# ═══════════════════════════════════════════════════════════════════════
# TELEMETRY
# ═══════════════════════════════════════════════════════════════════════
INTENT_ATTR: str = "_fb_intent"
INTENT_SOURCE_ATTR: str = "_fb_intent_source"
INTENT_FRAME_ATTR: str = "_fb_intent_frame"
INTENT_PREV_ATTR: str = "_fb_intent_prev"


def stamped_fullback_intent(defender) -> Optional[FullbackIntent]:
    """The intent the controller last COMMANDED, or None.

    None means "the controller has not commanded this body" — an untagged
    actor, a fresh body, or a run with ``RLM_FB_INTENT_CONTROLLER=0``.  It is
    deliberately NOT a fallback-into-the-live-state read; use
    ``live_fullback_intent`` for that.
    """
    value = getattr(defender, INTENT_ATTR, None)
    return value if isinstance(value, FullbackIntent) else None


def stamped_fullback_intent_source(defender) -> str:
    """Why the controller last commanded what it commanded ('' when none)."""
    value = getattr(defender, INTENT_SOURCE_ATTR, None)
    return value if isinstance(value, str) else ""


def stamped_fullback_intent_frame(defender):
    """The frame the last commanded intent was stamped (None when none)."""
    return getattr(defender, INTENT_FRAME_ATTR, None)


def live_fullback_intent(world, defender) -> FullbackIntent:
    """What intent this actor is under RIGHT NOW, from live state + tag.

    Works with the lever ON or OFF, and on bodies the controller has never
    touched, because it reads the tag and the movement state rather than the
    telemetry.  This is the reading the audits should use.
    """
    if defender is None:
        return FullbackIntent.UNOWNED
    if not getattr(defender, "fullback_role_active", False):
        return FullbackIntent.UNOWNED
    if getattr(world, "in_kickoff", False):
        return FullbackIntent.FLIGHT
    return intent_for_state(getattr(defender, "movement_state", None))


# The name the stage plan uses for this reading.
def classify_fullback_intent(world, defender) -> FullbackIntent:
    """Alias of ``live_fullback_intent`` — the intent in force for *defender*."""
    return live_fullback_intent(world, defender)


# ═══════════════════════════════════════════════════════════════════════
# THE SINGLE WRITE PATH
# ═══════════════════════════════════════════════════════════════════════

def enter_fullback_intent(
    world,
    defender,
    intent: FullbackIntent,
    *,
    source: str = "",
) -> Optional[MovementState]:
    """Enter *intent* for the tagged fullback — THE only state write path.

    Every producer of a fullback movement state calls this instead of
    assigning ``defender.movement_state`` itself, so the vocabulary, the
    mapping and the write all live in one module.

    Returns the ``MovementState`` written, or ``None`` when the intent
    commands no state (``UNOWNED``) or there is no body to write to.

    ``UNOWNED`` writes NOTHING and stamps NOTHING: a yield is not a command,
    and claiming ownership on a frame where a tackle-side system owns the
    body would be exactly the split-brain this stage exists to remove.

    Behaviour neutrality: the state written is
    ``INTENT_MOVEMENT_STATE[intent]``, always one of the same
    ``MovementState`` singletons the call sites wrote literally before this
    module existed.
    """
    if defender is None:
        return None

    state = intent_movement_state(intent)
    if state is None:
        return None

    if (
        state is MovementState.CHASE
        and getattr(defender, "movement_state", None) is not MovementState.CHASE
        and getattr(defender, "facing_override", False)
    ):
        defender.clear_facing_override()

    defender.movement_state = state

    if not fullback_intent_controller_enabled():
        return state

    # Telemetry only.  No production reader branches on these.
    previous = getattr(defender, INTENT_ATTR, None)
    if isinstance(previous, FullbackIntent) and previous is not intent:
        defender.__dict__[INTENT_PREV_ATTR] = previous
    defender.__dict__[INTENT_ATTR] = intent
    defender.__dict__[INTENT_SOURCE_ATTR] = source
    defender.__dict__[INTENT_FRAME_ATTR] = getattr(world, "frame_count", None)
    return state


# ═══════════════════════════════════════════════════════════════════════
# STAGE 2 — STATE OWNERSHIP
# ═══════════════════════════════════════════════════════════════════════
# THE LAW: for as long as a tagged fullback's break emergency is LIVE, this
# controller is the SOLE authority for its ``movement_state``.  The shared
# defender state policy — the Movement Mode Decision Layer in
# ``movement_mode_decision`` and the sweeper pin in
# ``defensive_behaviour_unified`` — must not name a state for that body.
#
# THE EMERGENCY is the per-actor break latch armed by the geometry authority
# on a genuine break of EITHER kind (``FB_BREAK_DEPTH`` / ``FB_BREAK_LATERAL``).
# It ends when the latch clears: the explicit tackle-cycle sweep at RELEASE
# entry / miss teardown, tag loss, the engagement reset, or self-release when
# the carrier genuinely drops behind the latched line.  Those are exactly the
# latch contract's own release conditions, so "the break emergency ended" has
# ONE definition and this module does not invent a second one.
#
# ``fullback_role`` IS READ THROUGH ONE LAZY ACCESSOR.  ``fullback_role``
# imports THIS module (for ``enter_fullback_intent``), so a module-scope
# import here would close the cycle — and
# ``verify_fullback_intent_ownership_contract.py`` asserts the absence of that
# module-scope edge.  ``_fullback_role_api`` below is this module's ONLY import
# edge to the geometry authority: function-local, TOTAL (an unreachable
# ``fullback_role`` makes the latch read as NOT armed — the safe answer, since
# the shared policy then resumes rather than the controller seizing a body it
# cannot prove it owns), and shared by EVERY reader, so the edge cannot
# multiply.  Pinned by ``verify_fullback_state_ownership_contract.py`` §7 and
# ``verify_fullback_recovery_ownership_contract.py`` §7.

def _break_latch_armed(defender) -> bool:
    """True when *defender* carries an armed break latch.

    The emergency predicate, read through the ONE latch reader.  Totality is
    deliberate: a body with no tag, no latch attributes, or an unreachable
    ``fullback_role`` reads as NOT latched, so the shared policy resumes
    rather than the controller seizing a body it cannot prove it owns.
    """
    if defender is None:
        return False
    is_latched = _fullback_role_api().get("is_latched")
    if is_latched is None:
        return False
    try:
        return bool(is_latched(defender))
    except Exception:                                    # noqa: BLE001
        return False


_FB_ROLE_API: dict = {}


def _fullback_role_api() -> dict:
    """This module's ONE import edge to ``fullback_role``, taken lazily.

    Returns the geometry authority's own readers, resolved once and cached:

        is_latched       ``is_fullback_break_latched``      (Stage 2: latch)
        depth_break      ``fullback_depth_break_active``    (the DEPTH leg)
        lateral_break    ``fullback_lateral_break_active``  (the LATERAL leg)
        recovery_active  ``fullback_recovery_active``       (owed ground)

    FUNCTION-LOCAL ON PURPOSE.  ``fullback_role`` imports this module (Stage
    1's ``enter_fullback_intent``), so a module-scope edge here would close the
    cycle — and ``verify_fullback_intent_ownership_contract.py`` asserts the
    absence of that edge.  Keeping the ONE edge behind an accessor is what
    lets Stage 3 read the same predicates without adding a second one.

    TOTAL: an unreachable ``fullback_role`` yields an EMPTY mapping instead of
    raising, so each reader degrades to its own safe default — "not latched"
    for the break emergency, ``SWEEP`` for the recovery intent — rather than
    the controller naming a body on evidence it cannot obtain.

    Defined AFTER ``_break_latch_armed`` so the module's single import edge
    sits below the latch reader that first needed it.
    """
    if not _FB_ROLE_API:
        try:
            from game.defence.defence_lvl_1.fullback_role import (
                fullback_depth_break_active,
                fullback_lateral_break_active,
                fullback_recovery_active,
                is_fullback_break_latched,
            )
        except Exception:                                # noqa: BLE001
            return _FB_ROLE_API
        _FB_ROLE_API.update(
            is_latched=is_fullback_break_latched,
            depth_break=fullback_depth_break_active,
            lateral_break=fullback_lateral_break_active,
            recovery_active=fullback_recovery_active,
        )
    return _FB_ROLE_API


def break_emergency_live(defender) -> bool:
    """True while the controller owns *defender*'s state (latch armed).

    Requires ALL of: the lever is on, the body is a tagged fullback, and the
    break latch is armed.  A break emergency is the ONLY thing that suspends
    the shared defender state policy.
    """
    if not fullback_state_controller_enabled():
        return False
    if defender is None:
        return False
    if not getattr(defender, "fullback_role_active", False):
        return False
    return _break_latch_armed(defender)


# The stage plan's name for the same reading, with the world argument the
# policy writers already hold.  The world is not consulted today (the latch
# is per-actor), but the signature is the one every call site can satisfy
# without reaching for the body's tag itself.
def controller_owns_fullback_state(world, defender) -> bool:
    """True when this controller is the sole state authority for *defender*.

    The SINGLE predicate every shared-policy writer must consult before it
    names a fullback movement state.  See ``break_emergency_live``.
    """
    return break_emergency_live(defender)


def shared_policy_state_allowed(world, defender) -> bool:
    """True when the shared defender state policy may name a state.

    The exact negation of ``controller_owns_fullback_state``, provided as a
    positive predicate so a writer reads as ``if allowed: ...`` rather than
    as a double negative.  True for every body that is not a tagged fullback
    under a live break emergency — which is every line defender, everywhere,
    always.
    """
    return not controller_owns_fullback_state(world, defender)


def emergency_intent(defender) -> FullbackIntent:
    """The intent a live break emergency commands for *defender*.

    ``CHASE``: the only break the latch can be armed on is a genuine one, and
    every break — DEPTH or LATERAL — is an intercept.  A body with no live
    emergency resolves to ``SWEEP``, which is the shared policy's own default
    rather than an emergency command, so a caller that ignores the predicate
    still cannot be handed a state the emergency never ordered.
    """
    if break_emergency_live(defender):
        return FullbackIntent.CHASE
    return FullbackIntent.SWEEP


def emergency_fullback_state(world, defender) -> Optional[MovementState]:
    """The state the controller commands for *defender* RIGHT NOW.

    Resolves through ``intent_movement_state`` — the ONE intent -> state law —
    so this reader can never disagree with what ``command_fullback_state``
    writes.  Returns ``None`` when no break emergency is live, which is the
    controller's own way of saying "the shared policy owns this frame".
    """
    if not break_emergency_live(defender):
        return None
    return intent_movement_state(FullbackIntent.CHASE)


def command_fullback_state(
    world, defender, *, source: str = SOURCE_STATE_OWNERSHIP
) -> Optional[MovementState]:
    """THE sole-authority write for a live break emergency.

    Writes the emergency state through the ONE write path
    (``enter_fullback_intent``) and stamps the Stage-2 source, so the frame's
    authority is visible in the telemetry.  Returns the state written, or
    ``None`` when no emergency is live (nothing is written and nothing is
    stamped — the controller does not claim a body it does not own).
    """
    if not break_emergency_live(defender):
        return None
    return enter_fullback_intent(
        world, defender, FullbackIntent.CHASE, source=source
    )


# ═══════════════════════════════════════════════════════════════════════
# STAGE 3 — RECOVERY OWNERSHIP
# ═══════════════════════════════════════════════════════════════════════
# THE LAW: a tagged fullback owns ITS OWN recovery.  Misses, owed-ground
# recoveries and break re-engagement are FULLBACK behaviours, resolved here;
# the SHARED recovery pathways must not name a target, a movement state or a
# pace for that body.
#
# THE FOUR SHARED PATHWAYS, and the one gate each of them now reads:
#
#   1. SHARED RECOVERY  — ``line_break_static_guard.is_static_guard_recovery_
#      eligible``, the single definition of "a body a post-break recovery may
#      re-target".  ``freeze_expiry_pursuit`` delegates to it, and the guard
#      itself is built from it.
#   2. STATIC GUARD     — ``line_break_static_guard.enforce_line_break_static_
#      guard``, the dispatch's LAST X recovery write.
#   3. FREEZE-EXPIRY    — ``freeze_expiry_pursuit.is_expiry_pursuit_eligible``,
#      the expiry hand-back.
#   4. POST-MISS PURSUIT — ``post_miss_pursuit.is_exempt_role``, the beaten
#      body's chase entitlement.
#
# WHY IT EXISTS.  Every one of those four sites ALREADY exempted the tagged
# fullback — each with its own inline ``getattr(d, "fullback_role_active",
# False)`` copy.  The behaviour was right and the OWNERSHIP was not: one law
# ("the last defender runs its own recovery") was restated four times, so it
# could drift, could not be A/B'd, and could not be audited in one place.  The
# stage makes the controller the single owner of that law, so all four sites
# read ONE predicate and the fullback's own misses / recoveries / re-engagement
# have ONE declared contract.
#
# BEHAVIOUR NEUTRALITY.  Each site keeps its historical check AND gains the
# contract gate as an additional early return, so the set of denied bodies is
# identical with the lever ON and OFF; OFF simply leaves the historical
# per-module checks as the only arbiter.  What the lever switches is WHO
# defines the denial, which is why ``verify_fullback_recovery_ownership_
# contract.py`` proves the deference by monkeypatching this module's predicate
# rather than by measuring a behaviour delta (the same technique Stage 2 used
# to prove the MMDL carries the controller's verdict).

FB_RECOVERY_CONTROLLER_ENV_VAR: str = "RLM_FB_RECOVERY_CONTROLLER"
FB_RECOVERY_CONTROLLER: bool = (
    os.environ.get(FB_RECOVERY_CONTROLLER_ENV_VAR, "1").strip().lower()
    not in ("0", "false", "no", "off", "")
)


def fullback_recovery_controller_enabled() -> bool:
    """Whether the controller owns the fullback's recovery contract.

    Default ON.  ``RLM_FB_RECOVERY_CONTROLLER=0`` restores the pre-stage
    arrangement exactly — the four shared recovery pathways fall back to
    their own historical per-module fullback checks — with no code revert.
    Read through the module boolean so a verifier or a probe can A/B it
    in-process by rebinding ``fullback_controller.FB_RECOVERY_CONTROLLER``,
    the same pattern every other lever in this package uses.
    """
    return bool(FB_RECOVERY_CONTROLLER)


# The telemetry the FB's own recovery declaration stamps.  Diagnostic only:
# no production reader branches on them; the audits and the verifier do.
RECOVERY_INTENT_ATTR: str = "_fb_recovery_intent"
RECOVERY_SOURCE_ATTR: str = "_fb_recovery_source"
RECOVERY_FRAME_ATTR: str = "_fb_recovery_frame"

def recovery_contract_owned(world, defender) -> bool:
    """True when THIS controller owns *defender*'s recovery contract.

    THE single predicate the shared recovery pathways read.  True only when
    the lever is on AND the body carries the fullback tag: the last defender's
    misses, owed-ground recoveries and break re-engagement are resolved by
    ``fullback_role`` + this controller, never by a shared line-defender
    recovery writer.

    Totality is deliberate: a None body, an untagged body, a stub world or a
    disarmed lever all report False, so a caller that ignores the lever still
    cannot be handed a denial the contract never ordered.
    """
    if not fullback_recovery_controller_enabled():
        return False
    if defender is None:
        return False
    return bool(getattr(defender, "fullback_role_active", False))


def shared_recovery_allowed(world, defender) -> bool:
    """True when the SHARED recovery pathways may name *defender*'s recovery.

    The exact negation of ``recovery_contract_owned``, provided as a positive
    predicate so a pathway reads as ``if not allowed: yield`` rather than as a
    double negative.  True for every body that is not a tagged fullback —
    which is every line defender, everywhere, always.
    """
    return not recovery_contract_owned(world, defender)


def recovery_intent(
    world, defender, anchor_x=None, anchor_y=None
) -> FullbackIntent:
    """THE single recovery decision for *defender*.

    One vocabulary, one law, so the fullback's own behaviours can never
    disagree with each other:

      * ``UNOWNED`` — not a tagged fullback (the controller names no recovery
        for a body that is not its own).
      * ``CHASE`` — a genuine break is live (the SAME two-branch test the
        emergency-candidacy gate and the geometry writer read:
        ``fullback_depth_break_active`` OR ``fullback_lateral_break_active``,
        both of which flow through the single ``classify_fullback_break``).
      * ``RECOVER`` — the actor genuinely owes ground to its own anchor, read
        through the existing ``fullback_recovery_active`` predicate.  The
        anchors are required for this branch; without them the debt is
        unmeasurable and the answer stays ``SWEEP``.
      * ``SWEEP`` — the ordinary sweeper contract.

    Everything it reads lives in ``fullback_role`` and reaches this module
    through the SAME lazy accessor the Stage-2 latch reader uses
    (``_fullback_role_api``), so this stage adds NO import edge and no second
    definition of a break or of "owed ground".
    """
    if defender is None:
        return FullbackIntent.UNOWNED
    if not getattr(defender, "fullback_role_active", False):
        return FullbackIntent.UNOWNED
    api = _fullback_role_api()
    depth_break = api.get("depth_break")
    lateral_break = api.get("lateral_break")
    if depth_break is None or lateral_break is None:
        return FullbackIntent.SWEEP
    try:
        if depth_break(world, defender) or lateral_break(world, defender):
            return FullbackIntent.CHASE
    except Exception:                                    # noqa: BLE001
        pass
    recovery_active = api.get("recovery_active")
    if (recovery_active is not None
            and anchor_x is not None and anchor_y is not None):
        try:
            if recovery_active(world, defender, anchor_x, anchor_y):
                return FullbackIntent.RECOVER
        except Exception:                                # noqa: BLE001
            pass
    return FullbackIntent.SWEEP


def declare_fullback_recovery(
    world,
    defender,
    anchor_x=None,
    anchor_y=None,
    *,
    source: str = SOURCE_RECOVERY,
) -> FullbackIntent:
    """Declare the fullback's own recovery frame and return its intent.

    The FB-OWNED half of the contract: called by the geometry writer on the
    owed-ground RECOVER branch, so the actor's recovery is named by the
    controller instead of merely implied by the movement state it happens to
    carry.  ``recovery_intent`` supplies the decision; the MOVEMENT STATE is
    still written by the ONE write path at the call site (Stage 1's routed
    ``enter_fullback_intent``), which is why this function writes no state.

    Returns the resolved intent (also returned when the lever is off, so a
    caller can never be handed a value the contract would not have produced).
    Stamps nothing when the lever is off.
    """
    intent = recovery_intent(world, defender, anchor_x, anchor_y)
    if defender is None or not fullback_recovery_controller_enabled():
        return intent
    defender.__dict__[RECOVERY_INTENT_ATTR] = intent
    defender.__dict__[RECOVERY_SOURCE_ATTR] = source
    defender.__dict__[RECOVERY_FRAME_ATTR] = getattr(world, "frame_count", None)
    return intent


def declared_fullback_recovery(defender) -> Optional[FullbackIntent]:
    """The recovery intent the controller last declared (None when none)."""
    value = getattr(defender, RECOVERY_INTENT_ATTR, None)
    return value if isinstance(value, FullbackIntent) else None


def declared_fullback_recovery_source(defender) -> str:
    """Why the controller last declared that recovery ('' when none)."""
    value = getattr(defender, RECOVERY_SOURCE_ATTR, None)
    return value if isinstance(value, str) else ""


def declared_fullback_recovery_frame(defender):
    """The frame the last recovery declaration was stamped (None when none)."""
    return getattr(defender, RECOVERY_FRAME_ATTR, None)


# ═══════════════════════════════════════════════════════════════════════
# STAGE 4 — LOCOMOTION OWNERSHIP
# ═══════════════════════════════════════════════════════════════════════
# THE LAW: while a tagged fullback is RUNNING ITS OWN PLAY — a live break
# emergency (Stage 2) OR an owed-ground recovery (Stage 3) — its STEERING is
# a FULLBACK locomotion contract, resolved from the INTENT and INDEPENDENT of
# the shared defender tables (``ACCEL_BY_STATE`` / ``MAX_SPEED_BY_STATE`` /
# ``LATERAL_PRIORITY_BY_STATE`` / ``FACE_BC_BY_STATE``) and of the
# line-defence approach priorities.  When NEITHER door is open the shared
# tables resume unchanged, so the sweeper glide and the kickoff flight are
# byte-identical.
#
# THE TWO DOORS.  Both are defined by an EARLIER stage; this stage invents
# neither of them, and adds no second definition of a break or of "owed
# ground":
#
#   door A  BREAK EMERGENCY  ``break_emergency_live(defender)`` — Stage 2's own
#           predicate: the per-actor break latch is armed.  This is the
#           emergency PURSUIT and the INTERCEPTION.
#   door B  RECOVERY OWED    ``recovery_owed(world, defender)`` — Stage 3's own
#           LIVE predicate ``fullback_role.fullback_recovery_active``,
#           evaluated against the point the geometry authority COMMANDED.
#           This is the RECOVERY steering.
#
# THE STATE WINS WHEN IT NAMES A PROFILE (2026-09-30).  The doors decide
# WHETHER this body is running its own play; they must not overrule WHICH
# play, because the authority that owns the body's movement state — the
# geometry writer, through Stage 1's ONE write path — has already answered
# that question for this frame.  MEASURED split-brain this closes
# (``verify_fullback_recover_live.py``, seed 20260827, 2 of 6 runs): the
# writer's breakaway chase branch is guarded by the LIVE break test
# (``classify_fullback_break`` != NONE and the line occupied) and RETURNS,
# while its owed-ground branch below writes RECOVER — but door A reads the
# per-actor break LATCH, which is sticky and outlives the live break.  On
# those frames the state said RECOVER while door A handed the body the
# PURSUIT profile, so a body COMMANDED HOME sprinted at the unified
# defensive ceiling (measured 8.30 m/s against the RECOVER row's 4.75 m/s)
# for 100+ consecutive frames.  ``locomotion_intent`` therefore prefers the
# state when the state names a profile, and falls back to the doors only for
# a state that names neither (the sweeper's DRIFT), which is what keeps every
# sweeper / flight / yield frame on exactly the contract it already had.
#
# DOOR B IS NOT OPTIONAL AND IT IS NOT A FALLBACK OF A BREAK.  It is what
# stops this contract being punched through on the frame the latch clears: a
# fullback that MISSED — the miss teardown releases the break latch — is still
# owed ground and must keep its OWN locomotion.  Handing that body back to the
# shared DRIFT/SLIDE/line-defence tables mid-recovery would be a hole straight
# through the Stage-3 recovery contract, and the last defender would measure
# itself home at the shared table's pace instead of its own.
#
# THE PROFILE ITSELF lives in ``game.defence.defence_lvl_1.fullback_locomotion``
# — a pure leaf that holds the arithmetic, while THIS module holds the
# DECISION to use it.  This module must not import the leaf at module scope
# (the leaf reads this module for the intent vocabulary), so the profile is
# resolved through ONE lazy accessor (``_fullback_locomotion_api``), exactly
# like ``fullback_role``.

FB_LOCOMOTION_CONTROLLER_ENV_VAR: str = "RLM_FB_LOCOMOTION_CONTROLLER"
FB_LOCOMOTION_CONTROLLER: bool = (
    os.environ.get(FB_LOCOMOTION_CONTROLLER_ENV_VAR, "1").strip().lower()
    not in ("0", "false", "no", "off", "")
)


def fullback_locomotion_controller_enabled() -> bool:
    """Whether this controller owns a fullback's locomotion contract.

    Default ON.  ``RLM_FB_LOCOMOTION_CONTROLLER=0`` restores the pre-stage
    arrangement exactly — every tagged fullback is integrated through the
    SHARED movement tables regardless of the break / recovery state — with no
    code revert.  Read through the module boolean so a verifier or a probe can
    A/B it in-process by rebinding
    ``fullback_controller.FB_LOCOMOTION_CONTROLLER``, the same pattern every
    other lever in this package uses.
    """
    return bool(FB_LOCOMOTION_CONTROLLER)


# The telemetry the locomotion declaration stamps.  Diagnostic only: no
# production reader branches on them; the audits and the verifier do.
LOCOMOTION_INTENT_ATTR: str = "_fb_locomotion_intent"
LOCOMOTION_SOURCE_ATTR: str = "_fb_locomotion_source"
LOCOMOTION_FRAME_ATTR: str = "_fb_locomotion_frame"

# Why the controller took the locomotion frame.
SOURCE_LOCOMOTION_BREAK: str = "locomotion_break_emergency"
SOURCE_LOCOMOTION_RECOVERY: str = "locomotion_recovery"


def _locomotion_anchor_pair(defender):
    """The commanded point a recovery debt is measured against.

    The point the geometry authority COMMANDED this frame is the actor's
    ``target_x`` / ``target_y``; the committed sweeper anchor is the fallback
    for a body that has not been through that writer yet.  Returns
    ``(None, None)`` when neither is readable, which makes the recovery
    predicate report False rather than raising — the safe answer, since the
    shared tables then keep the body.
    """
    if defender is None:
        return None, None
    anchor_x = getattr(defender, "_fb_committed_anchor_x", None)
    anchor_y = getattr(defender, "_fb_committed_anchor_y", None)
    if not isinstance(anchor_x, (int, float)):
        anchor_x = getattr(defender, "target_x", None)
    if not isinstance(anchor_y, (int, float)):
        anchor_y = getattr(defender, "target_y", None)
    if not isinstance(anchor_x, (int, float)):
        return None, None
    if not isinstance(anchor_y, (int, float)):
        return None, None
    return float(anchor_x), float(anchor_y)


def recovery_owed(world, defender) -> bool:
    """True when the tagged fullback is genuinely owed ground this frame.

    Stage 3's OWN live predicate, read through the SAME lazy accessor the
    Stage-2 latch reader uses (``_fullback_role_api``), evaluated against the
    point the geometry authority commanded — so this stage adds NO import edge
    and NO second definition of "owed ground".  Totality is deliberate: an
    unreachable ``fullback_role``, a world whose recovery lever is not armed,
    or an unreadable target all report False, which leaves the body on the
    shared tables rather than claiming a recovery it cannot prove.
    """
    if defender is None:
        return False
    if not getattr(defender, "fullback_role_active", False):
        return False
    recovery_active = _fullback_role_api().get("recovery_active")
    if recovery_active is None:
        return False
    anchor_x, anchor_y = _locomotion_anchor_pair(defender)
    if anchor_x is None or anchor_y is None:
        return False
    try:
        return bool(recovery_active(world, defender, anchor_x, anchor_y))
    except Exception:                                    # noqa: BLE001
        return False


def break_locomotion_owned(world, defender) -> bool:
    """DOOR A — the locomotion frame belongs to the FB on a live break.

    Requires the lever, the tag and Stage 2's own break emergency.  The
    emergency-PURSUIT / INTERCEPTION half of the locomotion contract.
    """
    if not fullback_locomotion_controller_enabled():
        return False
    if defender is None:
        return False
    if not getattr(defender, "fullback_role_active", False):
        return False
    return break_emergency_live(defender)


def recovery_locomotion_owned(world, defender) -> bool:
    """DOOR B — the locomotion frame belongs to the FB while it owes ground.

    Requires the lever, the tag and Stage 3's own LIVE recovery predicate.
    The RECOVERY-STEERING half, and what keeps this contract intact on the
    frame a miss released the break latch.
    """
    if not fullback_locomotion_controller_enabled():
        return False
    if defender is None:
        return False
    if not getattr(defender, "fullback_role_active", False):
        return False
    return recovery_owed(world, defender)


def locomotion_contract_owned(world, defender) -> bool:
    """True when this controller owns *defender*'s locomotion this frame.

    THE single predicate the movement layer reads.  True when EITHER door is
    open — a live break emergency (pursuit / interception) OR an owed-ground
    recovery — because both are frames on which the last defender is running
    its OWN play rather than the shared line-defender contract.
    """
    return (
        break_locomotion_owned(world, defender)
        or recovery_locomotion_owned(world, defender)
    )


def shared_locomotion_allowed(world, defender) -> bool:
    """True when the SHARED movement tables may drive *defender*.

    The exact negation of ``locomotion_contract_owned``, provided as a
    positive predicate so the consumer reads as ``if allowed: ...`` rather
    than as a double negative.  True for every body that is not a tagged
    fullback running its own break or its own recovery — every line defender,
    everywhere, always, and the tagged fullback itself on every sweeper /
    kickoff-flight frame.
    """
    return not locomotion_contract_owned(world, defender)


def locomotion_intent(world, defender) -> FullbackIntent:
    """THE single locomotion decision for *defender*.

    One vocabulary, one law, so the locomotion contract can never disagree
    with the state contract about what the last defender is doing:

      * ``CHASE``   — a live break emergency: the emergency pursuit and the
                      interception run on the PURSUIT profile.
      * ``RECOVER`` — the actor genuinely owes ground: the recovery steering
                      runs on the RECOVERY profile.
      * ``UNOWNED`` — neither: the shared tables keep the body (the sweeper
                      glide and the kickoff flight are theirs).

    THE STATE WINS WHEN IT NAMES A PROFILE.  The actor's movement state IS
    the authority's decision — the geometry writer wrote it through Stage 1's
    ONE write path, which is the same writer whose chase branch RETURNS on a
    live break and whose recovery branch writes RECOVER when the live break
    test is cold.  So a body the authority has already stamped CHASE or
    RECOVER resolves to that intent, and the profile can never be the
    locomotion half of a DIFFERENT decision than the state (see THE STATE
    WINS in the Stage-4 section above for the measured split-brain this
    closes).

    The doors are still what decides WHETHER this body is running its own
    play, and they still decide the intent for a state that names neither
    profile — the sweeper's DRIFT — where door A remains FIRST, so a body
    whose state is DRIFT while a break latch is armed keeps the pursuit
    contract it has always had.  Ownership is therefore still
    ``door A OR door B``: a state that names a profile while NEITHER door is
    open is NOT this controller's to steer, and the shared tables keep it.
    """
    if defender is None:
        return FullbackIntent.UNOWNED
    if not getattr(defender, "fullback_role_active", False):
        return FullbackIntent.UNOWNED
    if not locomotion_contract_owned(world, defender):
        return FullbackIntent.UNOWNED

    state = getattr(defender, "movement_state", None)
    if state is MovementState.CHASE:
        return FullbackIntent.CHASE
    if state is MovementState.RECOVER:
        return FullbackIntent.RECOVER

    # The state names neither profile: the doors decide, as they always did.
    if break_locomotion_owned(world, defender):
        return FullbackIntent.CHASE
    if recovery_locomotion_owned(world, defender):
        return FullbackIntent.RECOVER
    return FullbackIntent.UNOWNED


def locomotion_source_for_intent(intent: FullbackIntent) -> str:
    """Why the controller took the frame, for *intent* ('' when UNOWNED)."""
    if intent is FullbackIntent.CHASE:
        return SOURCE_LOCOMOTION_BREAK
    if intent is FullbackIntent.RECOVER:
        return SOURCE_LOCOMOTION_RECOVERY
    return ""


def declare_fullback_locomotion(world, defender) -> FullbackIntent:
    """Declare the locomotion frame and return the resolved intent.

    The DECISION half of the contract, called by the movement layer at the
    point of CONSUMPTION so the frame's locomotion authority is stamped where
    it is exercised.  The PROFILE is not returned here — the caller resolves
    it through ``fullback_locomotion_profile`` — because the profile is
    arithmetic and this function is the decision record.

    Returns the resolved intent (also returned when the lever is off, so a
    caller can never be handed a value the contract would not have produced).
    Stamps nothing when the lever is off or the frame is unowned.
    """
    intent = locomotion_intent(world, defender)
    if defender is None or not fullback_locomotion_controller_enabled():
        return intent
    if intent is FullbackIntent.UNOWNED:
        return intent
    defender.__dict__[LOCOMOTION_INTENT_ATTR] = intent
    defender.__dict__[LOCOMOTION_SOURCE_ATTR] = locomotion_source_for_intent(
        intent
    )
    defender.__dict__[LOCOMOTION_FRAME_ATTR] = getattr(
        world, "frame_count", None
    )
    return intent


def declared_fullback_locomotion(defender):
    """The locomotion intent the controller last declared (None when none)."""
    value = getattr(defender, LOCOMOTION_INTENT_ATTR, None)
    return value if isinstance(value, FullbackIntent) else None


def declared_fullback_locomotion_source(defender) -> str:
    """Why the controller last declared that locomotion ('' when none)."""
    value = getattr(defender, LOCOMOTION_SOURCE_ATTR, None)
    return value if isinstance(value, str) else ""


def declared_fullback_locomotion_frame(defender):
    """The frame the last locomotion declaration was stamped (None when none)."""
    return getattr(defender, LOCOMOTION_FRAME_ATTR, None)


_FB_LOCOMOTION_API: dict = {}


def _fullback_locomotion_api() -> dict:
    """This module's ONE import edge to ``fullback_locomotion``.

    FUNCTION-LOCAL ON PURPOSE.  The leaf reads THIS module for the intent
    vocabulary, so a module-scope edge here would close the cycle.  Keeping
    the ONE edge behind an accessor is what lets every consumer resolve the
    profile without adding a second one.

    TOTAL: an unreachable ``fullback_locomotion`` yields an EMPTY mapping, so
    the resolver below degrades to ``None`` — the "shared tables keep the
    body" answer — rather than naming a profile it cannot obtain.
    """
    if not _FB_LOCOMOTION_API:
        try:
            from game.defence.defence_lvl_1.fullback_locomotion import (
                profile_for_intent,
            )
        except Exception:                                # noqa: BLE001
            return _FB_LOCOMOTION_API
        _FB_LOCOMOTION_API.update(profile_for_intent=profile_for_intent)
    return _FB_LOCOMOTION_API


def fullback_locomotion_profile(world, defender):
    """The locomotion profile for *defender*, or ``None`` for the shared tables.

    THE single resolver every locomotion consumer reads.  ``None`` means "the
    shared movement tables own this body's steering" — the honest answer for
    every untagged body, for the tagged fullback on its sweeper anchor, and
    for the kickoff flight, and therefore the answer that keeps those frames
    byte-identical.  A mapping means "the FULLBACK contract owns it", and the
    mapping is resolved from the INTENT through the leaf's own table.
    """
    if not locomotion_contract_owned(world, defender):
        return None
    resolve = _fullback_locomotion_api().get("profile_for_intent")
    if resolve is None:
        return None
    try:
        return resolve(locomotion_intent(world, defender))
    except Exception:                                    # noqa: BLE001
        return None


# ═══════════════════════════════════════════════════════════════════════
# CONTRACT READERS
# ═══════════════════════════════════════════════════════════════════════

def intent_state_agrees(defender) -> bool:
    """True when the actor's live state is the one its intent commands.

    The Stage-1 invariant, made checkable: a body the controller has
    commanded must carry the movement state that intent maps to.  Returns
    True for a body the controller has never commanded (nothing to check),
    so it is safe to call on line defenders.

    A ``UNOWNED``-equivalent live state (HOLD, RETREAT, SLIDE, SHOOT) is
    accepted whenever the LAST COMMAND's state is still in force — that is
    the yield window, where another authority legitimately owns the body.
    """
    intent = stamped_fullback_intent(defender)
    if intent is None:
        return True
    commanded = intent_movement_state(intent)
    if commanded is None:
        return True
    return getattr(defender, "movement_state", None) is commanded


def intent_transition_was_legal(defender) -> bool:
    """True when the last observed intent handover is a declared edge."""
    previous = getattr(defender, INTENT_PREV_ATTR, None)
    current = stamped_fullback_intent(defender)
    if not isinstance(previous, FullbackIntent) or current is None:
        return True
    return is_legal_intent_transition(previous, current)


# ═══════════════════════════════════════════════════════════════════════
# THE CONTROLLER FAÇADE
# ═══════════════════════════════════════════════════════════════════════
# Stage 1 is deliberately a module of pure functions plus this thin façade.
# The fullback carries all its own state on the body (the tag, the latch, the
# absorb counters), so the controller needs no per-instance state today.
# The façade exists so the later stages have ONE object to hang state on —
# Stage 2's authoritative state resolution, Stage 3's recovery contract and
# Stage 4's locomotion — without every call site having to change shape
# again.

class FullbackController:
    """The dynamic fullback's intent owner.  Stateless in Stage 1."""

    env_var: str = FB_INTENT_CONTROLLER_ENV_VAR
    state_env_var: str = FB_STATE_CONTROLLER_ENV_VAR

    # ── the vocabulary ──
    Intent = FullbackIntent

    def enter(
        self, world, defender, intent: FullbackIntent, *, source: str = ""
    ) -> Optional[MovementState]:
        """Enter *intent* for *defender*.  See ``enter_fullback_intent``."""
        return enter_fullback_intent(
            world, defender, intent, source=source
        )

    def intent(self, world, defender) -> FullbackIntent:
        """The intent in force for *defender*.  See ``live_fullback_intent``."""
        return live_fullback_intent(world, defender)

    def stamped(self, defender) -> Optional[FullbackIntent]:
        """The intent the controller last commanded."""
        return stamped_fullback_intent(defender)

    def source(self, defender) -> str:
        """Why the controller last commanded it."""
        return stamped_fullback_intent_source(defender)

    def state_for(self, intent: FullbackIntent) -> Optional[MovementState]:
        """The movement state *intent* commands."""
        return intent_movement_state(intent)

    def agrees(self, defender) -> bool:
        """Whether the actor's state matches its commanded intent."""
        return intent_state_agrees(defender)

    def enabled(self) -> bool:
        """Whether intent telemetry stamping is armed."""
        return fullback_intent_controller_enabled()

    # ── Stage 2: state ownership ──

    def state_controller_enabled(self) -> bool:
        """Whether the controller owns the state on a live break emergency."""
        return fullback_state_controller_enabled()

    def emergency_live(self, defender) -> bool:
        """Whether a break emergency is live for *defender*."""
        return break_emergency_live(defender)

    def owns_state(self, world, defender) -> bool:
        """Whether the controller is the sole state authority for *defender*."""
        return controller_owns_fullback_state(world, defender)

    def shared_policy_allowed(self, world, defender) -> bool:
        """Whether the shared state policy may name a state for *defender*."""
        return shared_policy_state_allowed(world, defender)

    def emergency_state(self, world, defender) -> Optional[MovementState]:
        """The state a live break emergency commands (None when not owned)."""
        return emergency_fullback_state(world, defender)

    def command_state(
        self, world, defender, *, source: str = SOURCE_STATE_OWNERSHIP
    ) -> Optional[MovementState]:
        """THE sole-authority write.  See ``command_fullback_state``."""
        return command_fullback_state(world, defender, source=source)

    # ── Stage 3: recovery ownership ──

    def recovery_controller_enabled(self) -> bool:
        """Whether the controller owns a fullback's recovery contract."""
        return fullback_recovery_controller_enabled()

    def owns_recovery(self, world, defender) -> bool:
        """Whether the controller owns *defender*'s recovery contract."""
        return recovery_contract_owned(world, defender)

    def shared_recovery_allowed(self, world, defender) -> bool:
        """Whether a SHARED recovery pathway may name *defender*'s recovery."""
        return shared_recovery_allowed(world, defender)

    def recovery_intent(
        self, world, defender, anchor_x=None, anchor_y=None
    ) -> FullbackIntent:
        """THE single recovery decision for *defender*."""
        return recovery_intent(world, defender, anchor_x, anchor_y)

    def declare_recovery(
        self,
        world,
        defender,
        anchor_x=None,
        anchor_y=None,
        *,
        source: str = SOURCE_RECOVERY,
    ) -> FullbackIntent:
        """Declare the fullback's own recovery for this frame."""
        return declare_fullback_recovery(
            world, defender, anchor_x, anchor_y, source=source
        )

    def declared_recovery(self, defender) -> Optional[FullbackIntent]:
        """The recovery intent the controller last declared."""
        return declared_fullback_recovery(defender)

    # ── Stage 4: locomotion ownership ──

    def locomotion_controller_enabled(self) -> bool:
        """Whether the controller owns a fullback's locomotion contract."""
        return fullback_locomotion_controller_enabled()

    def break_locomotion_owned(self, world, defender) -> bool:
        """DOOR A: the locomotion frame is the FB's on a live break."""
        return break_locomotion_owned(world, defender)

    def recovery_locomotion_owned(self, world, defender) -> bool:
        """DOOR B: the locomotion frame is the FB's while it owes ground."""
        return recovery_locomotion_owned(world, defender)

    def owns_locomotion(self, world, defender) -> bool:
        """Whether the controller owns *defender*'s locomotion this frame."""
        return locomotion_contract_owned(world, defender)

    def shared_locomotion_allowed(self, world, defender) -> bool:
        """Whether the SHARED movement tables may drive *defender*."""
        return shared_locomotion_allowed(world, defender)

    def locomotion_intent(self, world, defender) -> FullbackIntent:
        """THE single locomotion decision for *defender*."""
        return locomotion_intent(world, defender)

    def locomotion_profile(self, world, defender):
        """The FB locomotion profile, or None for the shared tables."""
        return fullback_locomotion_profile(world, defender)

    def declare_locomotion(self, world, defender) -> FullbackIntent:
        """Declare the locomotion frame and return the resolved intent."""
        return declare_fullback_locomotion(world, defender)

    def declared_locomotion(self, defender) -> Optional[FullbackIntent]:
        """The locomotion intent the controller last declared."""
        return declared_fullback_locomotion(defender)

    def recovery_owed(self, world, defender) -> bool:
        """Whether the tagged fullback genuinely owes ground this frame."""
        return recovery_owed(world, defender)


CONTROLLER: FullbackController = FullbackController()


__all__: List[str] = [
    "FB_INTENT_CONTROLLER",
    "FB_INTENT_CONTROLLER_ENV_VAR",
    "FullbackIntent",
    "FullbackController",
    "CONTROLLER",
    "INTENTS",
    "INTENT_MOVEMENT_STATE",
    "INTENT_TRANSITIONS",
    "INTENT_ATTR",
    "INTENT_SOURCE_ATTR",
    "INTENT_FRAME_ATTR",
    "INTENT_PREV_ATTR",
    "SOURCE_FLIGHT",
    "SOURCE_LIVE_BREAK_HANDOVER",
    "SOURCE_PIN_CLEARED",
    "SOURCE_BREAK_DEPTH",
    "SOURCE_BREAK_LATERAL",
    "SOURCE_RECOVERY",
    "SOURCE_ANCHOR",
    "SOURCE_ROLE_ASSIGN",
    "SOURCE_MMDL_POLICY",
    "SOURCE_STATE_OWNERSHIP",
    "SOURCE_SHARED_POLICY",
    "FB_STATE_CONTROLLER",
    "FB_STATE_CONTROLLER_ENV_VAR",
    "FB_RECOVERY_CONTROLLER",
    "FB_RECOVERY_CONTROLLER_ENV_VAR",
    "RECOVERY_INTENT_ATTR",
    "RECOVERY_SOURCE_ATTR",
    "RECOVERY_FRAME_ATTR",
    "fullback_recovery_controller_enabled",
    "recovery_contract_owned",
    "shared_recovery_allowed",
    "recovery_intent",
    "declare_fullback_recovery",
    "declared_fullback_recovery",
    "declared_fullback_recovery_source",
    "declared_fullback_recovery_frame",
    "FB_LOCOMOTION_CONTROLLER",
    "FB_LOCOMOTION_CONTROLLER_ENV_VAR",
    "LOCOMOTION_INTENT_ATTR",
    "LOCOMOTION_SOURCE_ATTR",
    "LOCOMOTION_FRAME_ATTR",
    "SOURCE_LOCOMOTION_BREAK",
    "SOURCE_LOCOMOTION_RECOVERY",
    "fullback_locomotion_controller_enabled",
    "recovery_owed",
    "break_locomotion_owned",
    "recovery_locomotion_owned",
    "locomotion_contract_owned",
    "shared_locomotion_allowed",
    "locomotion_intent",
    "locomotion_source_for_intent",
    "declare_fullback_locomotion",
    "declared_fullback_locomotion",
    "declared_fullback_locomotion_source",
    "declared_fullback_locomotion_frame",
    "fullback_locomotion_profile",
    "fullback_intent_controller_enabled",
    "fullback_state_controller_enabled",
    "break_emergency_live",
    "controller_owns_fullback_state",
    "shared_policy_state_allowed",
    "emergency_intent",
    "emergency_fullback_state",
    "command_fullback_state",
    "intent_movement_state",
    "intent_name",
    "intent_for_state",
    "is_legal_intent_transition",
    "stamped_fullback_intent",
    "stamped_fullback_intent_source",
    "stamped_fullback_intent_frame",
    "live_fullback_intent",
    "classify_fullback_intent",
    "enter_fullback_intent",
    "intent_state_agrees",
    "intent_transition_was_legal",
]
