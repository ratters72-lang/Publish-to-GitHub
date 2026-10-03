"""
player/movement_states.py — Movement State Machine.

Gives defenders explicit movement states (RETREAT, SLIDE, SHOOT, CHASE,
HOLD, DRIFT) with proper acceleration/deceleration curves per state,
instead of a single lerp-to-target.

Each state defines:
  - acceleration (how fast speed changes)
  - max_speed (maximum velocity in this state)
  - lateral_priority (0-1: 0=X-only, 1=Y-only)
  - facing_behaviour (whether to face BC or direction of travel)

State transitions are computed each frame by the Decision Layer.
This module only applies the movement model for the current state.
"""

import math
import os
from enum import Enum, auto

from player.collapse_controls import (
    COLLAPSE_LATERAL_SCALE,
    collapse_commit,
    collapse_lateral_scale,
    debug_collapse_log,
    freeze_spacing_y,
)

# ── CHASE X-COLLAPSE DIAGNOSTIC PROBE (temporary, env-gated) ──────────
# No-op unless RLM_CHASE_PROBE=1.  See game/debug/chase_probe.py.
from game.debug.chase_probe import probe_chase_velocity as _probe_chase_velocity


# ═══════════════════════════════════════════════════════════════════
# TACKLER MOVEMENT CONTRACT (2026-08-05)
# ═══════════════════════════════════════════════════════════════════
# MovementMode is the movement-contract switch referenced by the AI
# tackle spec (Section A/B/G):
#
#   NORMAL  — default; all generic movement contracts apply
#             (defensive-line, marker, retreat, spacing).
#   TACKLER — dedicated tackle-intent mode.  Can ONLY be entered by
#             _arm_tackler_role() at tackler selection (TACKLER_1/2/3).
#             Overrides DEF_LINE_READY, DEF_RETREAT_10, MARKER_A/B,
#             any spacing/line-shape logic, and any freeze except the
#             TES freeze.  Driven each TACKLE_INIT frame by OPE's
#             _update_tackler_movement() until attach in CONTACT.
#   RETREAT — post-TES retreat mode assigned at RELEASE (contract G).
#
# Invariants enforced by the engine:
#   - No mid-TES role changes (TACKLER_* locked until RELEASE).
#   - No mid-TES movement-contract changes (mode locked while
#     world.tes_active).
#   - Attached tacklers never run independent movement: mode is
#     ignored during CONTACT/DRIVE/DOMINANCE once tes_attached is
#     True (position owned by _follow_attached_tacklers).
# ═══════════════════════════════════════════════════════════════════

class MovementMode(Enum):
    """Movement-contract mode.  HIGHEST priority of any contract system."""
    NORMAL = auto()
    TACKLER = auto()
    RETREAT = auto()


class CollisionPriority(Enum):
    """Collision-initiator priority for tackle resolution."""
    NORMAL = auto()
    HIGH = auto()   # tackler — treated as collision initiator


class MovementState(Enum):
    """Defender movement states."""
    HOLD = auto()      # Maintain position, zero velocity
    RETREAT = auto()   # Backpedal toward anchor (maintain facing to BC)
    SLIDE = auto()     # Lateral movement only (maintain X depth)
    SHOOT = auto()     # Explosive forward toward BC (tackle commit)
    CHASE = auto()     # Fast pursuit of BC at angle
    DRIFT = auto()     # Slow lateral movement while maintaining depth
    RECOVER = auto()   # HIGH-VELOCITY re-covery of owed ground (see below)


# ── Per-state movement parameters ─────────────────────────────────

# Acceleration rates (m/s²) — higher = faster response
ACCEL_BY_STATE = {
    MovementState.HOLD: 10.0,      # Snap to stop
    MovementState.RETREAT: 3.0,    # Steady backpedal
    MovementState.SLIDE: 4.0,      # Quick lateral shuffle
    MovementState.SHOOT: 8.0,      # Explosive burst
    MovementState.CHASE: 6.0,      # Fast pursuit
    MovementState.DRIFT: 2.0,      # Slow lateral
    # RECOVER PACING (2026-09-22): aligned with the ordinary defender states
    # (RETREAT 3.0 / SLIDE 4.0), NOT the explosive SHOOT burst.  The first cut
    # of the recovery locomotion used 7.0 m/s², which made the last defender
    # build up harder than any normal player.  NOTE: this table is consumed by
    # ``apply_movement_state``, and (2026-09-27) the fullback reaches it
    # through ``apply_fullback_locomotion`` for EVERY state it writes, so the
    # table row below IS the effective pace knob for the sweeper -- RECOVER
    # included.  ``fullback_role.FB_RECOVER_SPEED_SCALE`` is retained only as
    # the documented mirror of that row and is verified numerically equal to
    # it (verify_fullback_recover_contract.py section [1]); the engine no
    # longer writes ``move_speed`` for the actor at all.
    MovementState.RECOVER: 3.0,    # Owed-ground recovery (RETREAT-rate build-up)
}

# Deceleration rates (m/s²) — higher = faster stop
DECEL_BY_STATE = {
    MovementState.HOLD: 15.0,
    MovementState.RETREAT: 5.0,
    MovementState.SLIDE: 6.0,
    MovementState.SHOOT: 10.0,
    MovementState.CHASE: 8.0,
    MovementState.DRIFT: 4.0,
    MovementState.RECOVER: 3.0,
}

# Max speed multipliers (fraction of max_speed_mps)
# RECOVER ALIGNMENT (2026-09-22): RECOVER travels home at the same fraction
# an ordinary line defender uses to move to its slot -- the role-movement
# writer gives DEF_LINE_READY ``0.5 x`` the unified ceiling -- so the last
# defender is never a sprinter.  The first cut used the FULL ceiling (1.0),
# which matched a committed tackler / chasing carrier rather than a body
# returning to position, and read in play as "the fullback is far too fast".
# What RECOVER keeps is the DAMPENER BYPASS (see
# player/target_stabiliser.py and Player._uses_smooth_damp) -- not an
# elevated pace.  The old "lifted 2.0 m/s sweeper cap" is RETIRED
# (2026-09-27): the fullback no longer runs under any FB-only speed cap, so
# ``fullback_role.fullback_approach_cap_applies`` is now a pure-function
# remnant with no live caller on the fullback's path.
MAX_SPEED_BY_STATE = {
    MovementState.HOLD: 0.0,
    MovementState.RETREAT: 0.7,
    MovementState.SLIDE: 0.5,
    MovementState.SHOOT: 1.0,
    MovementState.CHASE: 0.9,
    MovementState.DRIFT: 0.4,
    MovementState.RECOVER: 0.5,
}

# Lateral priority: 0 = X-only, 1 = Y-only, 0.5 = equal
LATERAL_PRIORITY_BY_STATE = {
    MovementState.HOLD: 0.5,
    MovementState.RETREAT: 0.45,  # Diagonal backpedal — track spacing Y while retreating X (2026-09-02)
    MovementState.SLIDE: 1.0,     # Lateral only (Y)
    MovementState.SHOOT: 0.0,     # Forward only (X)
    MovementState.CHASE: 0.3,     # Diagonal toward BC
    MovementState.DRIFT: 0.8,     # Mostly lateral
    MovementState.RECOVER: 0.25,  # Diagonal travel home, X-dominant
}

# Facing behaviour: True = face BC, False = face movement direction
FACE_BC_BY_STATE = {
    MovementState.HOLD: True,
    MovementState.RETREAT: True,   # Face BC while backpedalling
    MovementState.SLIDE: True,     # Face BC while sliding
    MovementState.SHOOT: False,    # Face direction of charge
    MovementState.CHASE: False,    # Face direction of chase
    MovementState.DRIFT: True,     # Face BC while drifting
    MovementState.RECOVER: False,  # Face the direction of travel home
}


def get_state_params(state: MovementState) -> dict:
    """Return the parameter dict for a given state."""
    return {
        "accel": ACCEL_BY_STATE[state],
        "decel": DECEL_BY_STATE[state],
        "max_speed_scale": MAX_SPEED_BY_STATE[state],
        "lateral_priority": LATERAL_PRIORITY_BY_STATE[state],
        "face_bc": FACE_BC_BY_STATE[state],
    }


# ── Transition rules ──────────────────────────────────────────────

# Valid state transitions (current_state -> set of valid next states)
VALID_TRANSITIONS = {
    MovementState.HOLD: {
        MovementState.RETREAT, MovementState.SLIDE,
        MovementState.SHOOT, MovementState.CHASE, MovementState.DRIFT,
        # RECOVER (2026-09-21): a HELD body must be able to start the
        # high-velocity recovery run.  Without this row the only states that
        # could enter RECOVER were the moving ones, so a defender parked in
        # HOLD (stale-seed lockdown, MMDL hold, pre-tackle stance) could never
        # recover -- the state would be unreachable exactly for the bodies
        # that most obviously owe ground.
        MovementState.RECOVER,
    },
    MovementState.RETREAT: {
        MovementState.HOLD, MovementState.SLIDE,
        MovementState.CHASE, MovementState.DRIFT, MovementState.RECOVER,
    },
    MovementState.SLIDE: {
        MovementState.HOLD, MovementState.RETREAT,
        MovementState.CHASE, MovementState.DRIFT, MovementState.RECOVER,
    },
    MovementState.SHOOT: {
        MovementState.CHASE, MovementState.HOLD,
        MovementState.RETREAT, MovementState.DRIFT, MovementState.RECOVER,
    },
    MovementState.CHASE: {
        MovementState.HOLD, MovementState.RETREAT,
        MovementState.SLIDE, MovementState.SHOOT, MovementState.DRIFT,
        MovementState.RECOVER,
    },
    MovementState.DRIFT: {
        MovementState.HOLD, MovementState.RETREAT,
        MovementState.SLIDE, MovementState.CHASE, MovementState.RECOVER,
    },
    MovementState.RECOVER: {
        MovementState.HOLD, MovementState.RETREAT,
        MovementState.SLIDE, MovementState.CHASE, MovementState.DRIFT,
        MovementState.SHOOT,
    },
}


def is_valid_transition(current: MovementState, next_state: MovementState) -> bool:
    """Check if a state transition is valid."""
    if current == next_state:
        return True
    return next_state in VALID_TRANSITIONS.get(current, set())


# ── State interpolation ──────────────────────────────────────────

def lerp_movement_state(
    from_state: MovementState,
    to_state: MovementState,
    t: float,
) -> MovementState:
    """
    Blend between movement states for smooth transitions.

    Uses t as a blended parameter: at t=0.0 the output is from_state,
    at t=1.0 the output is to_state.  Intermediate values blend the
    parameter sets but always resolve to one of the two states.
    Uses hysteresis to prevent rapid oscillation.
    """
    if t < 0.33:
        return from_state
    elif t > 0.66:
        return to_state
    # Blend zone — return the one with higher lateral priority
    # for smoother Y-axis transitions
    fp = LATERAL_PRIORITY_BY_STATE.get(from_state, 0.5)
    tp = LATERAL_PRIORITY_BY_STATE.get(to_state, 0.5)
    return to_state if tp > fp else from_state


# ═══════════════════════════════════════════════════════════════════════
# FULLBACK INTERCEPT TRACKING (2026-09-29)
# ═══════════════════════════════════════════════════════════════════════
# THE DEFECT THIS REMOVES ("the fullback closes in X but never in Y, so it
# overshoots the carrier and only re-acquires it late").
#
# The chase TARGET the fullback is handed on a genuine line break is already
# an INTERCEPT POINT -- ``fullback_role.fullback_chase_point`` projects the
# carrier's own velocity over a damped, bounded horizon, applies the
# goal-side margin along the carrier's heading, and hands the sweeper the
# point where the two paths meet (see FB_EMERGENCY_CHASE_TARGET_AXIS_AUDIT.md
# and verify_fb_chase_target_axis.py).  Pointing the BODY at that point is
# therefore the whole of the interception.
#
# The CONSUMPTION did not do that.  A tagged fullback integrates through this
# module, and the shared CHASE row is X-DOMINANT by design
# (``LATERAL_PRIORITY_BY_STATE[CHASE] = 0.3`` -- correct for a line defender
# pursuing a live carrier running +X), so the steering vector was skewed 70/30
# onto the attack axis no matter how much lateral ground the intercept owed.
# On top of that the pre-tackle alignment controls in
# ``player/collapse_controls.py`` -- which exist to stop a LINE defender
# chasing a stepping carrier's lateral move -- damped the sweeper's lateral
# command by ``0.60`` whenever the carrier's own ``|vy|`` exceeded 1.0 m/s
# (i.e. exactly on a diagonal line break), by the band weight, and froze its
# lateral command entirely inside 1.5 m (item 5, which re-points ``target_y``
# at the body's OWN lane).
#
# Measured signature of the pair: ``|dx|`` collapsed while ``|dy|`` grew, the
# body crossed the carrier's lane, and the lateral component only became
# dominant once the X error had already gone to zero -- the "overshoot, then
# turn" behaviour.
#
# THE LAW.  When the tagged fullback is in CHASE its command is an
# INTERCEPT, so the body travels TO THE COMMANDED POINT:
#   * the steering vector is the direct bearing to the commanded intercept
#     point (both axes), not the X-skewed lateral-priority blend;
#   * the pre-tackle lateral ALIGNMENT controls are not applied -- they
#     govern a line defender's approach to a slot, and the fullback's
#     contact-range contract is carried by the chase point's own near-range
#     taper (inside FB_CHASE_LEAD_NEAR_M the point IS ``(bc.x - trail,
#     bc.y)``, so the 0.3 m pre-tackle slot gate is satisfied BY THE TARGET
#     and needs no velocity damping);
#   * everything else is UNCHANGED: the same ACCEL_BY_STATE ramp, the same
#     MAX_SPEED_BY_STATE top speed, the same facing policy, the same single
#     integration step, and the same post-facing ``max_defensive_speed_mps``
#     ceiling.  The collapse-commit vector override (inside 0.8 m) still
#     applies, so the collision finish is exactly what it was.
#
# The shared CHASE row is NOT touched: line defenders keep
# ``LATERAL_PRIORITY_BY_STATE[CHASE] = 0.3`` and keep the alignment clamps
# (verify_fullback_shared_locomotion.py section [6] pins the row).
#
# A-B / ROLLBACK LEVER: ``RLM_FB_CHASE_INTERCEPT_TRACK=0`` restores the
# historical steering blend AND the alignment clamps for the fullback's
# chase with no code revert -- the same pattern as every other runtime lever
# in this package.  Read at consumption time so one process can A/B it.
FB_CHASE_INTERCEPT_TRACK_ENV_VAR: str = "RLM_FB_CHASE_INTERCEPT_TRACK"
FB_CHASE_INTERCEPT_TRACK: bool = (
    os.environ.get(FB_CHASE_INTERCEPT_TRACK_ENV_VAR, "1").strip().lower()
    not in ("0", "false", "no", "off", "")
)


def fullback_chase_intercept_track_enabled() -> bool:
    """Whether the fullback's CHASE is steered at the intercept point.

    Default ON.  ``RLM_FB_CHASE_INTERCEPT_TRACK=0`` restores the historical
    behaviour for the tagged fullback's chase -- the X-dominant lateral-
    priority blend plus the pre-tackle lateral alignment clamps -- with no
    code revert.

    Read through the module boolean on purpose, so a verifier or a probe can
    A/B the two contracts IN-PROCESS by rebinding
    ``movement_states.FB_CHASE_INTERCEPT_TRACK`` -- the same pattern
    ``fullback_role.FB_CHASE_INTERCEPT`` documents.
    """
    return bool(FB_CHASE_INTERCEPT_TRACK)


# ── Apply movement state to player velocity ──────────────────────

def apply_movement_state(
    player,
    state: MovementState,
    target_x: float,
    target_y: float,
    dt: float,
    bc_x: float = None,
    bc_y: float = None,
    bc_vy: float = None,
    *,
    preserve_state: bool = False,
    intercept_track: bool = False,
    params_override: dict | None = None,
) -> None:
    """
    Apply movement state to player, setting vx/vy toward (target_x, target_y)
    with state-appropriate acceleration and speed limits.

    This is called INSTEAD of Player._accelerate_toward_target() when
    movement states are active.  It provides smoother, more realistic
    movement than the simple lerp.

    Parameters
    ----------
    player : Player
        The defender to move.
    state : MovementState
        Current movement state.
    target_x, target_y : float
        Desired target position.
    dt : float
        Delta time in seconds.
    bc_x, bc_y : float or None
        Ball carrier position (required for FACE_BC states).
    bc_vy : float or None
        Ball carrier lateral velocity.  When provided alongside the BC
        position, the pre-tackle alignment controls (2026-08-15) damp the
        lateral correction when the carrier is stepping sideways fast.
    preserve_state : bool, keyword-only
        When True the arrival branch stops the body's VELOCITY but leaves
        ``player.movement_state`` exactly as the caller authored it.  Set by
        callers whose movement state is owned by an authority writer OUTSIDE
        this integrator -- currently only ``apply_fullback_locomotion`` (the
        tagged fullback, whose DRIFT / CHASE / RECOVER is stamped by
        ``fullback_role`` and ``kickoff_engine``).  Left False for the MMDL
        line-defender path, which relies on the arrival snap to park a body
        in HOLD for the pre-tackle posture read.
    intercept_track : bool, keyword-only
        When True the body is treated as an INTERCEPTOR: the steering vector
        is the direct bearing to the commanded target on BOTH axes (not the
        X-skewed ``lateral_priority`` blend) and the pre-tackle lateral
        ALIGNMENT controls (the spacing-Y freeze and the compound lateral
        correction clamp) are not applied.  Set by
        ``apply_fullback_locomotion``, whose commanded point on a line break
        IS an interception point -- see the FULLBACK INTERCEPT TRACKING block
        above.  Left False for every other caller, which keeps the shared
        CHASE / SLIDE / RETREAT alignment contract byte-for-byte.
    params_override : dict or None, keyword-only
        STAGE 4 — LOCOMOTION OWNERSHIP.  When supplied, THIS mapping IS the
        body's locomotion parameter set: it replaces ``get_state_params(state)``
        wholesale, so a body running under a DEDICATED contract (the tagged
        fullback on a live break or an owed-ground recovery — see
        ``fullback_locomotion``) is no longer steered by the shared
        ``ACCEL_BY_STATE`` / ``MAX_SPEED_BY_STATE`` / ``LATERAL_PRIORITY_BY_
        STATE`` / ``FACE_BC_BY_STATE`` rows at all.

        The override carries the same five keys ``get_state_params`` returns
        (``accel`` / ``decel`` / ``max_speed_scale`` / ``lateral_priority`` /
        ``face_bc``) and MAY carry ``alignment_damping`` — the profile's own
        answer to "may the pre-tackle lateral alignment controls damp this
        body's lateral command?".  When that key is absent the historical
        derivation (``not intercept_track``) is used, so a caller that
        overrides only the five shared keys keeps the alignment contract it
        had.

        ``None`` -- the default -- is byte-identical to the pre-Stage-4
        engine for every caller.
    """
    if params_override is not None:
        params = params_override
    else:
        params = get_state_params(state)

    # ── PRE-TACKLE ALIGNMENT & COLLAPSE GEOMETRY (2026-08-15) ──────────
    # Task-pack mapping onto the real engine (see player/collapse_controls.py):
    #   item 5: spacing-Y freeze inside 1.5m
    #   item 4: collapse vector override inside 0.8m (straight at BC)
    #   items 1+2+3: lateral correction clamp (dist / band / bc_vy)
    #   item 8: debug logging hooks (DEBUG_COLLAPSE_GEOMETRY)
    # bc_x=None keeps every non-MMDL call site byte-identical.
    dist_to_bc = None
    band = getattr(player, "role_depth_class", None)
    if bc_x is not None and bc_y is not None:
        dist_to_bc = math.hypot(bc_x - player.x, bc_y - player.y)
    if bc_vy is None:
        bc_vy = 0.0

    # ── ALIGNMENT-DAMPING DECISION (Stage 4, 2026-09-30) ──
    # Whether the pre-tackle lateral ALIGNMENT controls (item 5 below and
    # items 1+2+3 further down) may damp THIS body's lateral command.  A
    # locomotion profile that carries the key answers for itself (the tagged
    # fullback's PURSUIT profile says no: its commanded point IS the
    # interception, so there is nothing left for the alignment clamps to
    # correct).  With no profile — every other caller, and the fullback's own
    # RECOVERY — the historical derivation stands: the clamps apply unless the
    # body is on the intercept path.
    if params_override is not None and "alignment_damping" in params_override:
        _alignment_damping = bool(params_override["alignment_damping"])
    else:
        _alignment_damping = not intercept_track

    # Item 5 — spacing-Y must not pull the defender sideways inside 1.5m.
    #
    # INTERCEPT EXEMPTION (2026-09-29): this item re-points ``target_y`` at
    # the body's OWN live lane, which is the opposite of what an interceptor
    # chasing a two-axis intercept point needs -- and it also breaks the
    # pre-tackle slot gate, which reads ``|bc.y - target_y| < 0.3 m``.  The
    # fullback's contact-range contract is carried by the chase point's own
    # near-range taper, so the freeze is skipped on the intercept path.
    spacing_y_adjust = 0.0
    if (
        dist_to_bc is not None
        and freeze_spacing_y(dist_to_bc)
        and _alignment_damping
    ):
        spacing_y_adjust = target_y - player.y
        target_y = player.y

    dx = target_x - player.x
    dy = target_y - player.y
    dist = math.hypot(dx, dy)

    if dist < 0.05:
        # Arrived — snap to hold.  The commit-zone override (item 4) must
        # never snap to hold while a live BC is inside the 0.8m collapse
        # zone: the pre-tackle trigger and collision finish own those frames.
        # AUTHORITY BOUNDARY (2026-09-27): this function INTEGRATES a body; it
        # does not AUTHOR movement state.  Historically the arrival branch also
        # stamped MovementState.HOLD, which silently overwrote whatever the
        # decision / geometry layer had authored for that frame the instant the
        # body reached its target.  On the kickoff fullback that made the
        # authored DRIFT unreachable: the placement writer parks the actor
        # exactly on its anchor (dist == 0.0 from frame 1), so the geometry
        # authority's DRIFT was rewritten to HOLD on the same frame, every
        # frame -- the actor held station reporting a state it was never
        # authored in.  ``preserve_state=True`` callers (the fullback
        # shared-locomotion path) keep their authored state.  The velocity stop
        # is identical either way, so the MOTION of every body is unchanged;
        # only the unauthorised state label is gone.
        if not (dist_to_bc is not None and collapse_commit(dist_to_bc)):
            player.vx = 0.0
            player.vy = 0.0
            if not preserve_state:
                player.movement_state = MovementState.HOLD
        return

    # Item 4 — collapse vector override inside the 0.8m commit zone:
    # aim straight at the BC with only a tiny lateral allowance.
    if dist_to_bc is not None and collapse_commit(dist_to_bc):
        bc_dx = bc_x - player.x
        bc_dy = bc_y - player.y
        bc_dist = math.hypot(bc_dx, bc_dy)
        if bc_dist < 0.001:
            nx = dx / dist
            ny = dy / dist
        else:
            nx = bc_dx / bc_dist
            ny = bc_dy / bc_dist
    else:
        nx = dx / dist
        ny = dy / dist

    # Blend direction based on lateral priority.
    #
    # INTERCEPT BEARING (2026-09-29): an interceptor travels to the commanded
    # point, so its steering vector IS the bearing to that point -- on both
    # axes, unskewed.  The lateral-priority blend below is the LINE defender's
    # contract (X-dominant, because it pursues a live carrier running +X); it
    # spends the sweeper's lateral ground on an approach the intercept point
    # has already resolved.  Everything downstream (acceleration ramp, top
    # speed, speed clamp, facing, the single position step) is untouched.
    lat = params["lateral_priority"]
    if intercept_track:
        dir_x = nx
        dir_y = ny
    else:
        dir_x = nx * (1.0 - lat)
        dir_y = ny * lat + nx * (1.0 - abs(nx)) * (1.0 - lat) * 0.3

    # Normalise blended direction
    dir_len = math.hypot(dir_x, dir_y)
    if dir_len > 0.001:
        dir_x /= dir_len
        dir_y /= dir_len
    else:
        dir_x = 0.0
        dir_y = 0.0

    target_speed = player.max_speed_mps * params["max_speed_scale"]
    desired_vx = dir_x * target_speed
    desired_vy = dir_y * target_speed

    # ── LATERAL CORRECTION CLAMP (items 1 + 2 + 3, and 4's 0.25 lateral) ─
    # The "lateral correction" is the desired_vy — the lateral component of
    # the approach vector.  Its scale is the task's compound clamp:
    #   - distance windows:  0.55 / 0.35 / 0.0 inside 2.0 / 1.2 / 0.8 m
    #   - band weight:       0.65 (AGGRESSIVE) / 0.85 (BALANCED) / 1.10 (PASSIVE)
    #   - BC lateral damp:   0.60 when |bc_vy| > 1.0 m/s
    # Inside the commit zone the collapse vector already carried the 0.25
    # lateral allowance, so the distance cascade (which would zero it) is
    # skipped there (include_distance=False).
    # INTERCEPT EXEMPTION (2026-09-29): items 1+2+3 damp the lateral component
    # of a LINE defender's approach so it keeps forward momentum into the
    # contact point instead of chasing a carrier's step.  An interceptor's
    # lateral component is the interception itself -- and the compound clamp is
    # at its harshest exactly on a line break (``bc_vy_factor`` = 0.60 whenever
    # the carrier's own |vy| exceeds 1.0 m/s, i.e. a diagonal break).  The
    # intercept path therefore takes the un-damped lateral command.  The
    # 0.3 m pre-tackle slot gate is satisfied by the near-range chase point
    # itself, not by this damping.
    lateral_corr_before = abs(desired_vy)
    if dist_to_bc is not None and _alignment_damping:
        desired_vy *= collapse_lateral_scale(
            dist_to_bc,
            band=band,
            bc_vy=bc_vy,
            include_distance=not collapse_commit(dist_to_bc),
        )
    lateral_corr_after = abs(desired_vy)

    # Item 8 — debugging hooks (no-op unless DEBUG_COLLAPSE_GEOMETRY).
    if dist_to_bc is not None:
        if collapse_commit(dist_to_bc):
            collapse_vx = dir_x
            collapse_vy = dir_y * COLLAPSE_LATERAL_SCALE
        else:
            collapse_vx = 0.0
            collapse_vy = 0.0
        debug_collapse_log(
            player,
            dist_to_bc=dist_to_bc,
            lateral_corr_before=lateral_corr_before,
            lateral_corr_after=lateral_corr_after,
            bc_vy=bc_vy,
            band=band,
            spacing_y_adjust=spacing_y_adjust,
            collapse_vx=collapse_vx,
            collapse_vy=collapse_vy,
            movement_state=state,
        )

    # Accelerate/decelerate toward desired velocity
    if state == MovementState.HOLD:
        # Hard stop
        player.vx *= (1.0 - min(1.0, params["decel"] * dt * 5.0))
        player.vy *= (1.0 - min(1.0, params["decel"] * dt * 5.0))
        if math.hypot(player.vx, player.vy) < 0.01:
            player.vx = 0.0
            player.vy = 0.0
    else:
        # Smooth acceleration toward desired
        accel = params["accel"] * dt
        player.vx += (desired_vx - player.vx) * min(1.0, accel)
        player.vy += (desired_vy - player.vy) * min(1.0, accel)

        # Clamp speed
        current_speed = math.hypot(player.vx, player.vy)
        if current_speed > target_speed:
            player.vx *= target_speed / current_speed
            player.vy *= target_speed / current_speed

    # ── CHASE X-COLLAPSE DIAGNOSTIC PROBE (temporary, env-gated) ──
    # Logs dx/dy/dist, the blended direction, desired and final vx/vy for
    # CHASE states so the x≈42 vs target_x≈12–17 audit can be explained.
    # No-op unless RLM_CHASE_PROBE=1.
    _probe_chase_velocity(
        player,
        target_x=target_x,
        target_y=target_y,
        state_name=getattr(state, "name", None) or str(state),
        dx=dx,
        dy=dy,
        dist=dist,
        dir_x=dir_x,
        dir_y=dir_y,
        desired_vx=desired_vx,
        desired_vy=desired_vy,
        final_vx=player.vx,
        final_vy=player.vy,
        ceiling=target_speed,
    )

    # Apply velocity to position
    player.x += player.vx * dt
    player.y += player.vy * dt

    # Facing
    if params["face_bc"] and bc_x is not None and bc_y is not None:
        angle_to_bc = math.atan2(bc_y - player.y, bc_x - player.x)
        player.set_facing(angle_to_bc)
    else:
        # Normal velocity-driven facing
        from player.movement import update_facing_unified
        update_facing_unified(player, dt)

# ═══════════════════════════════════════════════════════════════════════
# FULLBACK SHARED LOCOMOTION (2026-09-27)
# ═══════════════════════════════════════════════════════════════════════
# THE LAW: the fullback DECIDES differently but MOVES under exactly the same
# laws as every other defender.
#
# The tagged fullback is deliberately excluded from the MMDL (see
# verify_mmdl_fullback_exclusion.py): its DRIFT/CHASE state policy, its
# backfield anchor and its breakaway pursuit target generation are its own.
# That exclusion left it with NO consumer of this module's movement model, so
# it integrated through Player._accelerate_toward_target on the legacy
# 0.70-per-frame accel-lerp with a bespoke ``move_speed`` written by four
# separate sites (fullback_role.apply_fullback_geometry,
# defensive_behaviour_unified x2, kickoff_engine) and an FB-ONLY
# post-integration speed cap in Player.update.
#
# The result was four locomotion laws nobody else obeyed: a different
# acceleration model, a different top-speed model (a hard 2.0 m/s sweeper cap
# plus 0.5x/1.0x bespoke scales), a different turning model, and a different
# integration path.  Measured as the fullback "zoom": on the frame lane
# ownership is lost the geometry writer issued the full governed ceiling
# (``fullback_chase_speed(8.3, dist)`` = 8.3 m/s), the legacy lerp reached ~70%
# of it in one frame, and the FB-only cap then clamped the stored velocity
# AFTER the position step had already been taken at the un-capped pace -- so
# the visible motion and the reported ``vx`` disagreed, on the fullback only.
#
# This entry point puts the actor on the SAME rails as a line defender:
#   * acceleration -- ACCEL_BY_STATE[state]
#   * top speed    -- MAX_SPEED_BY_STATE[state] x max_speed_mps
#   * turning      -- LATERAL_PRIORITY_BY_STATE + FACE_BC_BY_STATE
#   * integration  -- apply_movement_state (the state machine's own velocity
#                     model and position step)
#
# It is a PURE CONSUMER: it never writes a target, a movement state or a
# speed.  The actor's own state policy (DRIFT / CHASE) and its own target
# generation remain the fullback's.  The caller keeps its existing
# post-facing ``max_defensive_speed_mps`` ceiling clamp, which bounds the
# fullback's CHASE exactly as it bounds every other defender's.
#
# STATE PURITY (2026-09-27): the "never writes a movement state" half of that
# claim was FALSE until the arrival branch stopped stamping HOLD.  A fullback
# parked on its anchor -- and the kickoff actor is hard-placed there -- had its
# authored DRIFT rewritten to HOLD by the shared arrival snap on every single
# frame, so the geometry authority's state never survived to be observed.  This
# entry point now integrates with ``preserve_state=True``, which stops velocity
# on arrival without touching the state it was handed.  The claim above is now
# true by construction rather than by assumption.

def apply_fullback_locomotion(player, dt: float) -> bool:
    """Integrate a fullback through the SHARED movement-state model.

    Reads the actor's own ``movement_state`` (its DRIFT/CHASE policy) and its
    own ``target_x``/``target_y`` (its own target generation), and integrates
    them with the same physics every defender receives.

    STAGE 4 — LOCOMOTION OWNERSHIP (2026-09-30).  The parameter set is no
    longer unconditionally the shared table's.  ``fullback_controller``
    resolves whether this frame is the FULLBACK'S to steer — a live break
    emergency (DOOR A: the emergency pursuit / the interception) OR an
    owed-ground recovery (DOOR B: the recovery steering) — and, when it is,
    ``fullback_locomotion`` supplies the dedicated profile for that intent.
    The profile is handed to ``apply_movement_state`` as ``params_override``,
    so the body is not steered by ``ACCEL_BY_STATE`` / ``MAX_SPEED_BY_STATE``
    / ``LATERAL_PRIORITY_BY_STATE`` / ``FACE_BC_BY_STATE`` at all.

    DOOR B IS THE ONE THAT MATTERS FOR CONTINUITY: a fullback that MISSED has
    its break latch released by the miss teardown but is still owed ground, so
    without it the shared tables would silently take the body over mid-
    recovery — a hole straight through the Stage-3 recovery contract.

    With NEITHER door open, or with ``RLM_FB_LOCOMOTION_CONTROLLER=0``, the
    shared tables keep the body and this entry point is byte-identical to the
    pre-stage engine (the sweeper glide, the kickoff flight, and the
    historical ``RLM_FB_CHASE_INTERCEPT_TRACK`` consumption included).

    Returns True when the shared model ran, and False when the actor has no
    usable MovementState (a stub / freshly-created body), so the caller can
    fall back to the legacy accelerator rather than raising.
    """
    state = getattr(player, "movement_state", None)
    if not isinstance(state, MovementState):
        return False
    if state not in ACCEL_BY_STATE:
        return False

    target_x = getattr(player, "target_x", None)
    if target_x is None:
        target_x = player.x
    target_y = getattr(player, "target_y", None)
    if target_y is None:
        target_y = player.y

    world = getattr(player, "world", None)
    bc = getattr(world, "ball_carrier", None) if world is not None else None
    if bc is not None:
        bc_x = float(bc.x)
        bc_y = float(bc.y)
        bc_vy = float(getattr(bc, "vy", 0.0) or 0.0)
    else:
        bc_x = None
        bc_y = None
        bc_vy = 0.0

    # STATE IS AUTHORITY-OWNED (2026-09-27): the actor's movement_state is
    # authored by its own authority (the fullback_role / kickoff_engine
    # geometry stamps), so the integrator must stop its velocity on arrival
    # WITHOUT rewriting the state it was handed.  This is what makes this
    # entry point a genuine pure consumer.
    #
    # ── STAGE 4: THE LOCOMOTION DECISION (2026-09-30) ──
    # WHO steers this frame — and with WHAT parameters — is resolved by the
    # ONE controller, not guessed here.  ``fullback_locomotion_profile``
    # returns the dedicated profile when the controller owns the frame (a
    # live break emergency, or an owed-ground recovery) and ``None`` when the
    # shared tables still own it, so the two answers cannot disagree.
    #
    # The import is FUNCTION-LOCAL and TOTAL: ``fullback_controller`` is a
    # light decision module that imports THIS module at module scope, so
    # importing it here at module scope would close the cycle — and an
    # unreachable controller must leave the body on the shared tables (the
    # safe answer) rather than raising inside an integrator.
    _profile = None
    try:
        from game.defence.defence_lvl_1.fullback_controller import (
            declare_fullback_locomotion,
            fullback_locomotion_profile,
        )
    except Exception:                                    # noqa: BLE001
        declare_fullback_locomotion = None
        fullback_locomotion_profile = None
    if fullback_locomotion_profile is not None:
        # Stamp the frame's locomotion authority at the point of CONSUMPTION,
        # so an audit reads the intent that actually steered the body.
        if declare_fullback_locomotion is not None:
            declare_fullback_locomotion(world, player)
        _profile = fullback_locomotion_profile(world, player)

    # INTERCEPT TRACKING (2026-09-29): on a line break the actor's commanded
    # point is an INTERCEPT POINT, not the carrier's live lane, so a CHASE
    # integrates as an interception -- bearing steering on both axes, no
    # pre-tackle lateral alignment damping -- instead of the X-skewed line
    # defender approach.
    #
    # STAGE 4: with a PROFILE in hand the answer is the profile's own
    # ``intercept_steer`` (TRUE for the pursuit, FALSE for the recovery —
    # which is what the shared RECOVER contract always did; the dedicated
    # profile may choose direct bearing for the fullback's owned anchor).
    # With NO profile — the sweeper, the flight, and every lever-OFF run —
    # the historical expression stands, so ``RLM_FB_CHASE_INTERCEPT_TRACK=0``
    # and ``RLM_FB_LOCOMOTION_CONTROLLER=0`` both restore the pre-stage
    # consumption byte-for-byte.
    if _profile is not None:
        _intercept_track = bool(_profile.get("intercept_steer", False))
    else:
        _intercept_track = (
            state == MovementState.CHASE
            and fullback_chase_intercept_track_enabled()
        )
    apply_movement_state(
        player,
        state,
        float(target_x),
        float(target_y),
        dt,
        bc_x=bc_x,
        bc_y=bc_y,
        bc_vy=bc_vy,
        preserve_state=True,
        intercept_track=_intercept_track,
        params_override=_profile,
    )
    return True
