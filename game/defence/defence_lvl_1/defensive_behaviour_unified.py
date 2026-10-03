"""
defensive_behaviour_unified.py - Unified defensive behaviour dispatcher.

Wraps Lvl-1 (freeze/markers/step-up/chase) and STEP-2 (containment/scramble/retreat)
and dispatches based on game sub-state, ball position, and per-defender depth
classification.

GLOBAL FIELD DIRECTION RULES (enforced Jan 2026)
------------------------------------------------
Attack always runs RIGHT (+X).  Defence always retreats RIGHT.
No player moves LEFT (except BC kicking).

In OPEN_PLAY (legacy scramble mode):
  - Geometry (base_line_x, lane_y) is COMPLETELY BYPASSED.
  - Defenders use role-differentiated X targets:
      PRIMARY/SECONDARY: bc.x - 1.0          (step up aggressively)
      MARKER_A/B, DUMMY_HALF: bc.x - 1.0
      PASSIVE/BACKFIELD: anchor_x
  - Speed scales with distance to actual (target_x, target_y), not just anchor_x.
    Thresholds: >20m=full, >10m=0.75x, <=10m=0.5x.

In OPEN_PLAY (structured mode, world.use_structured_open_play = True):
  - Full geometry pipeline: DefensiveGeometry.compute_line_shape()
  - Governor controls line depth, width, speed, compression
  - Role spacing adjusts per-defender
  - Decision layer affects speed/movement_state only, NOT targets
  - No retreat compliance override
  - No PASSIVE timing lock freeze
  - No role stripping

KICKOFF chase (ball in air, no BC):
  - Defenders move toward defensive_line_anchor (X) and role-anchored width Y.

Geometry movement is enabled ONLY during OPEN_PLAY.  It is DISABLED in
RELEASE, PTB_PREP, PTB_EXEC, PTB, POST_RUCK, POST_RUCK_OPEN_PLAY,
TACKLE_INIT, TACKLE_CONTACT, TACKLE_DRIVE, TACKLE_DOMINANCE — the phase
handlers own defensive targets in those phases.
"""

import math

from world.world_state import GameWorldState, GameSubState, is_tackle_phase, is_in_ruck_bubble
from game.defence.defence_lvl_1.defensive_behaviour_lvl1 import DefensiveBehaviourLvl1
from game.defence.defence_lvl_2.defensive_behaviour_lvl2 import DefensiveBehaviour
from game.debug.target_write_log import stamp_target_write
from game.defence.defence_lvl_1.defensive_geometry import (
    DefensiveGeometry,
    geometry_active,
)
from game.defence.defence_lvl_1.defensive_depth_roles import (
    assign_depth_roles,
)
from game.defence.defence_lvl_1.defensive_engagement_state import (
    update_engagement_state,
    reset_engagement_state,
)
from game.defence.defence_lvl_1.intercept_tacklers import (
    is_lane_tackler_candidate,
)
from game.defence.defence_lvl_1.defensive_width import (
    role_anchor_y,
    get_role_anchor_y,
    role_anchor_y_side,
    clamp_to_sideline,
    governed_width_m,
    depth_side_of,
    lane_x_of,
)
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets as _split_compute_targets,
    spacing_y as _spacing_y,
)
from game.defence.defence_lvl_1.defensive_pipeline import (
    frame_spacing_y as _frame_spacing_y,
    frame_geometry_is_fresh as _frame_geometry_is_fresh,
    frame_spacing_covers as _frame_spacing_covers,
    depth_roles_assigned_this_frame as _depth_roles_assigned_this_frame,
)
from game.defence.defence_lvl_1.defensive_timing import DefensiveTiming
from game.defence.defence_lvl_1.defensive_rebanding import (
    reband_after_stabilisation as _reband_after_stabilisation,
)
# TASK X BALL-SIDE COLLAPSE (2026-08-26): dynamic depth-role refit off
# ball-side + ruck context + settled positions at every ruck pivot.
from game.defence.defence_lvl_1.ball_side_collapse import (
    apply_dynamic_depth_roles as _apply_dynamic_depth_roles,
)
from game.defence.defence_lvl_1.defensive_role_spacing import DefensiveRoleSpacing
from game.defence.defence_lvl_1.defensive_line_governor import DefensiveLineGovernor
from player.player import DefenderRole, Player
from player.movement_states import (
    MovementState,
    MovementMode,
    apply_movement_state,
    lerp_movement_state,
    is_valid_transition,
)
from player.collapse_controls import should_commit
from game.defence.defence_lvl_1.movement_mode_decision import (
    decide_movement_state,
    resolve_movement_state,
    resolve_pre_tackle_stuck,
    carrier_past_defensive_line,
    _in_post_catch_chase_window,
    is_in_pursuit_commit_range,
    _is_genuine_line_defender,
)
# ── POST-MISS PURSUIT ENTITLEMENT (2026-09-26) ──
# The beaten body's chase entitlement.  Imported at module scope: the module
# carries NO project-scope imports of its own (``player.movement_states``
# only), so this cannot create a cycle in any load order.  The MMDL consults
# it below so an entitled body is handed to the engine's own integrator
# instead of being integrated here on a consumed target.
from game.defence.defence_lvl_1.post_miss_pursuit import (
    owns_post_miss_pursuit as _owns_post_miss_pursuit,
)
from game.defence.defence_lvl_1.defensive_decisions import (
    decide as ddecision,
    select_movement_state,
    compute_intercept_target,
    Decision,
)
from game.defence.defence_lvl_1.fullback_role import (
    assign_fullback_role,
    # FB SPEED HELPERS DROPPED (2026-09-27): ``fullback_approach_speed`` and
    # ``fullback_target_distance`` were imported here only to write the
    # sweeper's ``move_speed`` (the 2.0 m/s cap + its 2D arrival taper).  The
    # engine now owns only the actor's STATE and TARGET; its PACE comes from
    # the shared movement-state table inside
    # ``player.movement_states.apply_fullback_locomotion``.
    update_fullback_absorb_state,
    # B2b (2026-09-16): emergency-defender tag hold -- an engaged fullback
    # never dissolves into the line.
    is_fullback_emergency_engaged as _is_fb_emergency_engaged,
)
from game.defence.defence_lvl_1.intercept_tacklers import (
    select_lane_tacklers,
    apply_pre_tackle_stance,
    is_primary_lane_tackler,
    is_line_defender,
)
from game.defence.defence_lvl_1.pre_tackle_authority import (
    publish_pre_tackle_state as _publish_pre_tackle_state,
    apply_pre_tackle,
)
# ── FIELD BOUND ON THE COMMIT TARGET (2026-09-25) ────────────────────
# The tackler-commit writer aims a committed body at
# ``bc.x +/- ConvergenceManager offset``.  With the carrier at/past
# FIELD_MAX_X that authors a point OUTSIDE the field, which Player's
# position clamp makes unreachable: the body presses against the boundary
# while its authority keeps commanding a forward approach (the
# "stationary OPEN_PLAY body holding a live target" defect measured by
# probe_open_play_stall_authority.py; the sibling pre-tackle writer had the
# same gap).  Pure leaf import -- no cycle.
from game.defence.defence_lvl_1.target_field_bound import (
    clamp_target_to_field as _clamp_target_to_field,
)
# ── PRE-TACKLE CORRIDOR (2026-09-15) ──
# SPEED-AUTHORITY CONSOLIDATION: the corridor owns a committed tackler's
# target, move_speed, movement_state, plant verdict and position
# integration from the slowdown band down to the tackle arm, so every
# writer below YIELDS to it.  `_enter_corridor` opens the corridor and is
# called at the TOP of `_apply_tackler_commit` -- the first commit writer
# in all three OPEN_PLAY dispatch variants -- so the latch is always set
# before every writer that must see it.  `_corridor_contact_velocity` is
# the single read path for the TES handoff.
from game.defence.defence_lvl_1.pretackle_corridor import (
    enter_corridor as _enter_corridor,
    is_corridor_owned as _is_corridor_owned,
    contact_velocity as _corridor_contact_velocity,
)
# ANTICIPATION AUTHORITY WINDOW (2026-09-08): reassert the lean on the
# final Y for authority-active, non-committed defenders so the boundary
# writers (tackler-commit / pursuit-commit) cannot snap it off instantly.
from game.defence.defence_lvl_1.anticipation_layer import (
    apply_anticipation_authority as _apply_anticipation_authority,
)
from game.managers.retreat_manager import (
    apply_forced_retreat,
    apply_tes_support_retreat,
)

from game.defence.defence_lvl_1.anchor_authority import (
    compute_and_lock_anchor,
    defensive_line_anchor_m as _anchor_m,
    enforce_retreat_to_anchor,
    assert_anchor_consistency,
)

# ── RETREAT-OBLIGATION VETO (2026-09-17) ─────────────────────────────
# THE 10 m retreat contract.  This is the veto that keeps the body INSIDE
# the compliance population.  The two promotion loops below raise a
# defender from PASSIVE to SECONDARY on proximity/chase, and PASSIVE is
# the precondition of EVERY compliance writer in this module (step 9
# MANDATORY RETREAT, the MMDL step-2 retreat branch, anchor Layer 7).
# Promoting a still-retreating body to SECONDARY therefore stripped it
# out of the compliance population BEFORE any compliance writer ran --
# the load-bearing half of "the obligation was never enforced".
from game.defence.defence_lvl_1.retreat_obligation import (
    is_retreat_obligated,
)

# ── CONTACT AUTHORITY — THE SINGLE TES-ENTRY CHOKE POINT (Stage 1B) ──
# This module can ADMIT a contest (``try_pre_tackle`` -> pretackle_machine)
# but it does NOT own the handover to TES.  ``contact_authority.enter_tes``
# is the single function in the engine that calls
# ``OpenPlayEngine._start_tackle``; every engage site below routes through it
# with the channel that admitted the contest, so the authority's published
# formation record is complete.  ``contact_authority`` has no module-scope
# project imports, so this cannot create a cycle.
from game.defence.defence_lvl_1.contact_authority import (
    enter_tes as _enter_tes,
    CHANNEL_STRUCTURED_ENGAGE as _CH_STRUCTURED,
    CHANNEL_UNIFIED_ENGAGE as _CH_UNIFIED,
    CHANNEL_PURSUIT_ENGAGE as _CH_PURSUIT,
)

# ── CHASE X-COLLAPSE DIAGNOSTIC PROBE (temporary, env-gated) ──────────
# No-op unless RLM_CHASE_PROBE=1.  See game/debug/chase_probe.py.
from game.debug.chase_probe import (
    probe_mmdl_chase as _probe_mmdl_chase,
    probe_on as _probe_on,
)

__all__ = ["DefensiveBehaviourUnified"]

# Symmetric lane_idx -> def_idx lookup table (legacy pursuit Y spread).
# lane_idx -3..+3 (Y-sorted position) maps to get_defence_target indices 3..9.
# Kept for backward compatibility; pursuit Y is now role-anchored
# (2026-08-09, Task B).
lane_to_def_idx = {-3: 3, -2: 4, -1: 5, 0: 6, 1: 7, 2: 8, 3: 9}

# ─────────────────────────────────────────────────────────────────────
# TACKLER-COMMIT CONSTANTS (2026-08-13)
# ─────────────────────────────────────────────────────────────────────
# The OPEN_PLAY tackle commit writer solves the "PRIMARY/SECONDARY tackler
# runs to spacing/width/retreat targets instead of the ball carrier" gap.
# Once ``tackle_role`` is PRIMARY/SECONDARY the defender overrides
# spacing (Y), width (X), retreat, band depth, finalise blend, and anchor
# enforcement with a dedicated forward collision point.  These offsets
# are deliberately SMALLER than the TES TACKLER contract offsets
# (bc.x + 1.0 / bc.x + 2.0) is NOT required to match — the stable BC-forward
# point is what matters; try_pre_tackle / _check_pre_tackle fire on
# distance, so the committed target only needs to keep the defender
# closing on the carrier's lane with guaranteed forward velocity.
# ── FX-2 (TA8-2026-08-25): SINGLE-SOURCED from ConvergenceManager ──
# One authority owns the BC-close offsets; duplicate definitions are
# forbidden (verify_convergence_single_source fails on legacy copies).
from game.managers.convergence_manager import (
    PRIMARY_X_OFFSET_M as TACKLER_COMMIT_PRIMARY_X_OFFSET_M,
    SECONDARY_X_OFFSET_M as TACKLER_COMMIT_SECONDARY_X_OFFSET_M,
    SECONDARY_BAND_Y_OFFSET_M as TACKLER_COMMIT_BAND_Y_OFFSET_M,
)


def _fullback_tackle_candidates(defenders, already, world=None, bc=None):
    """Refresh and return standalone fullbacks eligible for emergency engage.

    The returned candidates never enter lane selection or pre-tackle
    movement ownership. Their separate authority stays active through lane
    role churn and is revoked by the fullback break/chase gates.
    """
    from game.defence.defence_lvl_1.fullback_role import (
        FB_EMERGENCY_ENGAGE_AUTHORITY_ATTR,
        fullback_emergency_engage_authority_active,
        refresh_fullback_emergency_engage_authority,
    )
    out = []
    for d in defenders or ():
        if d is None or not getattr(d, "fullback_role_active", False):
            continue
        refresh_fullback_emergency_engage_authority(world, d, bc)
        if any(d is e for e in (already or ())):
            continue
        if (
            getattr(d, FB_EMERGENCY_ENGAGE_AUTHORITY_ATTR, False)
            and fullback_emergency_engage_authority_active(world, d, bc)
        ):
            out.append(d)
    return out


def _try_primary_then_fullback(world, defenders, fullbacks, bc, tuners):
    """Try lane PRIMARY, the normal lane candidate, then standalone FBs."""
    if getattr(world, "tes_active", False):
        return None
    from game.defence.defence_lvl_1.pretackle_classifier import (
        select_primary_candidate,
    )
    from game.defence.defence_lvl_1.defensive_behaviour_lvl1 import (
        try_pre_tackle,
    )
    lane_primary = select_primary_candidate(
        world,
        [d for d in (defenders or ())
         if getattr(d, "tackle_role", None) == "PRIMARY"],
        bc,
    )
    selected = select_primary_candidate(world, defenders, bc)
    lane_candidates = []
    if lane_primary is not None:
        lane_candidates.append(lane_primary)
    if selected is not None and all(selected is not d for d in lane_candidates):
        lane_candidates.append(selected)

    previous_facing = getattr(bc, "prev_facing", None)
    had_previous_facing = bc is not None and hasattr(bc, "prev_facing")

    def restore_carrier_facing():
        if bc is None:
            return
        if had_previous_facing:
            bc.prev_facing = previous_facing
        elif hasattr(bc, "prev_facing"):
            delattr(bc, "prev_facing")

    for candidate in lane_candidates:
        restore_carrier_facing()
        if try_pre_tackle(world, candidate, bc, tuners):
            return candidate
        if getattr(world, "tes_active", False):
            return None

    if lane_candidates and bc is not None and fullbacks:
        # Every candidate must evaluate the same carrier-facing delta for this
        # frame; classifier calls update the baseline as a side effect.
        for fullback in fullbacks:
            from game.defence.defence_lvl_1.fullback_role import (
                fullback_emergency_engage_authority_active,
            )
            if not fullback_emergency_engage_authority_active(
                world, fullback, bc,
            ):
                continue
            restore_carrier_facing()
            if try_pre_tackle(world, fullback, bc, tuners):
                return fullback
            if getattr(world, "tes_active", False):
                return None
    elif not lane_candidates:
        for fullback in fullbacks:
            from game.defence.defence_lvl_1.fullback_role import (
                fullback_emergency_engage_authority_active,
            )
            if (
                fullback_emergency_engage_authority_active(world, fullback, bc)
                and try_pre_tackle(world, fullback, bc, tuners)
            ):
                return fullback
            if getattr(world, "tes_active", False):
                return None
    return None


def _apply_tackler_commit(world, defenders, tuners) -> list:
    """Tackler-commit override — PRIMARY/SECONDARY must engage the BC.

    TACKLING FIX (2026-08-13): once ``select_tacklers`` has assigned
    ``tackle_role`` PRIMARY/SECONDARY, EVERY other movement writer
    (spacing Y, width X, retreat, band depth, finalise blend, anchor
    enforcement) must be overridden with a dedicated forward collision
    target — the "tacklers never commit" bug was that tackle_role only
    gated the pre-tackle trigger while the geometry writers still drove
    the approach toward spacing/width/retreat positions.

    Writes (per committed defender):
      - ``tackle_target_x/y`` — the dedicated collision point:
            + PRIMARY   -> (bc.x + 1.0, bc.y)
            + SECONDARY -> (bc.x + 2.0, bc.y + 0.5 * depth_side)
        SECONDARY's lateral offset is the PERSISTENT role-locked
        depth_side (banded approach, never live lane) so the target is
        stable frame-to-frame.
      - ``target_x/target_y`` from that point (LAST target writer wins).
      - ``target_source = "tackler_commit"`` so audits can see the writer.
      - ``movement_state = CHASE`` (non-smooth-damp; fast accel-lerp and
        excluded from the target stabiliser).
      - ``move_speed = unified defensive ceiling`` (full commit speed).
      - ``frozen = False`` and all timing/retreat locks cleared —
        a committed tackler never stalls.
      - ``_tackle_commit_active = True`` (MMDL skip + P/S labels).

    Fallback / anti-stall §3: when the BC is None or the computed target
    is invalid, the defender is aimed directly at the BC's current
    position (or holds only if explicitly frozen later by another
    authority).  ``target_x`` is always a live BC-relative coordinate,
    so vx toward the carrier is guaranteed non-zero unless the defender
    is standing exactly on the carrier (in which case the pre-tackle
    trigger fires this very frame).

    Returns the list of committed (tackle_role PRIMARY/SECONDARY)
    defenders for the caller (MMDL skip / pre-tackle joiner scan).
    """
    bc = getattr(world, "ball_carrier", None)
    committed: list = []

    # ── PRE-TACKLE CORRIDOR GATE (2026-09-15) ──
    # Open the corridor BEFORE any commit write so every yielding writer
    # (this one, _apply_pursuit_commit_override, apply_pre_tackle_stance,
    # seed_hold, line_break_static_guard) sees the latch in the SAME frame
    # the corridor opens.  This is the single insertion point: all three
    # OPEN_PLAY dispatch variants call this writer first, so the gate
    # cannot be missed by a variant.  Bookkeeping only -- the corridor
    # never writes a target or a speed.
    _enter_corridor(world, defenders, bc, tuners)

    if bc is None or tuners is None:
        for d in defenders:
            if getattr(d, "fullback_role_active", False):
                d._tackle_commit_active = False
                continue
            if getattr(d, "tackle_role", "PASSIVE") in ("PRIMARY", "SECONDARY"):
                d._tackle_commit_active = True
                committed.append(d)
        return committed

    ceiling = getattr(tuners, "max_defensive_speed_mps", 8.3)

    # ── STAGE 3D SINGLE-SOURCE MAP ──
    # The manager-owned ConvergenceManager publishes the frame's BC-closing
    # targets; committed defenders CONSUME their entry instead of recomputing
    # the collision point inline.  Legacy math below stays as the fallback
    # for verifier stubs / frames without a published dict.
    conv_map = getattr(world, "frame_convergence_targets", None) or {}

    for d in defenders:
        if not isinstance(d, Player):
            continue
        if getattr(d, "fullback_role_active", False):
            d._tackle_commit_active = False
            continue
        if getattr(d, "tackle_role", "PASSIVE") not in ("PRIMARY", "SECONDARY"):
            # Clear any stale commit flag so the flag never leaks across
            # frames for a defender that is no longer committed.
            d._tackle_commit_active = False
            continue

        role = getattr(d, "tackle_role", "PRIMARY")
        d._tackle_commit_active = True
        committed.append(d)

        # ── CORRIDOR YIELD (2026-09-15) ──
        # The pre-tackle corridor owns this body's target, speed, movement
        # state and plant verdict this frame.  Keep the ROLE + flag stamps
        # (the tackle trigger, the MMDL commit-skip and
        # publish_pre_tackle_state all read them) and yield everything
        # else.  This is what stops the slowdown curve being overwritten at
        # full commit speed on the final frames before contact -- the
        # "tuning blind" defect.
        if _is_corridor_owned(d):
            d.tackle_target_x = float(getattr(d, "target_x", d.x))
            d.tackle_target_y = float(getattr(d, "target_y", d.y))
            d.frozen = False
            d.timing_locked = False
            d.reaction_timer = 0.0
            d.retreat_delay = 0.0
            d.lateral_hesitation = 0.0
            d.step_stagger = 0.0
            d.step_stagger_active = False
            continue

        # ── DIRECTION-AWARE ENGAGEMENT OFFSET (2026-08-15, catch fix) ──
        # A defender AHEAD of the carrier (d.x > bc.x — the middle of the
        # line at catch time) must converge TOWARD the BC, so its X target
        # is bc.x - offset.  A defender BEHIND the carrier closes on
        # bc.x + offset (the original forward-deflection point).  Without
        # this the ahead-engagement lane band would aim the central
        # defenders FURTHER past the carrier and they would keep drifting
        # upfield instead of converging.
        ahead = d.x > bc.x
        _cpt = conv_map.get(id(d))
        if _cpt is not None:
            # STAGE 3D — PURE DICT CONSUMER: the published entry IS the
            # authoritative collision point for this frame.
            tx = float(_cpt[0])
            ty = float(_cpt[1])
            if role == "PRIMARY":
                prev_ty = getattr(d, "_prev_tackle_y", None)
                if prev_ty is not None:
                    ty = prev_ty + (ty - prev_ty) * 0.7
                d._prev_tackle_y = ty
        elif role == "PRIMARY":
            # ── INTERCEPT TARGET (2026-08-15, spec §2) ──
            # X: a point slightly in front of the BC along the approach
            # (the commit stage 1.0 m ahead — attack runs +X, so the
            # deflection in front of the carrier is the +X offset for
            # behind defenders and the -X offset for ahead defenders).
            # Y: directly at BC's Y (tight lane band) — never the spacing
            # / role-anchor Y.  A per-frame smoothing factor damps the
            # carrier's lateral jinks so the tackler converges on the lane
            # without orbiting / drifting past it.
            if ahead:
                tx = bc.x - TACKLER_COMMIT_PRIMARY_X_OFFSET_M
            else:
                tx = bc.x + TACKLER_COMMIT_PRIMARY_X_OFFSET_M
            ty = bc.y
            prev_ty = getattr(d, "_prev_tackle_y", None)
            if prev_ty is not None:
                ty = prev_ty + (bc.y - prev_ty) * 0.7
            d._prev_tackle_y = ty
        else:
            # ── PERSISTED / TES-ARMED SECONDARY (spec §1/§4) ──
            # SECONDARY survives ONLY as a TES-gating concept: a genuine
            # persisted or TES-armed secondary (assist-tackle continuation)
            # still receives its BC-forward point so it can complete the
            # assist.  In normal OPEN_PLAY the lane system converts every
            # candidate SECONDARY to LINE before this writer runs.
            if ahead:
                tx = bc.x - TACKLER_COMMIT_SECONDARY_X_OFFSET_M
            else:
                tx = bc.x + TACKLER_COMMIT_SECONDARY_X_OFFSET_M
            side = getattr(d, "depth_side", None)
            if side in (-1, 1):
                ty = bc.y + TACKLER_COMMIT_BAND_Y_OFFSET_M * side
            else:
                ty = bc.y


        # ── FRONT-HOLD FALLBACK (2026-10-03) ──
        # When the manager published NO convergence entry (a frame without
        # ``frame_convergence_targets``, or a verifier stub) an AHEAD
        # committed body must still hold the corridor in FRONT of the carrier
        # on the carrier's lane instead of being aimed behind it.  Mirrors
        # ConvergenceManager.apply() exactly; gated by the shared master
        # tuner so the legacy geometry is byte-identical when off.
        if (
            _cpt is None
            and ahead
            and bool(getattr(tuners, "pretackle_front_hold_enabled", True))
        ):
            _front = float(getattr(tuners, "pretackle_front_offset_m", 0.6))
            _bracket = float(getattr(
                tuners, "pretackle_front_lateral_offset_m", 0.3))
            _side = getattr(d, "depth_side", None)
            _sd = _side if _side in (-1, 1) else (-1 if d.y < bc.y else 1)
            tx = max(float(d.x), float(bc.x) + _front)
            ty = float(bc.y) + _bracket * _sd

        # Guard against NaN/infinite target (would corrupt movement).
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (tx, ty)):
            tx, ty = bc.x, bc.y

        # ── FIELD BOUND (2026-09-25) ──
        # A committed body may only be aimed at a REACHABLE point.  When the
        # carrier is at/past the tryline the BC-forward point lands outside
        # the field and the body can never arrive: it walks into the
        # boundary, the position clamp cancels the step, and it stalls with
        # a live target.  Bounding the point keeps the commitment reachable.
        tx, ty = _clamp_target_to_field(tx, ty)

        # ── STABLE COLLISION TARGET (§2) ──
        d.tackle_target_x = tx
        d.tackle_target_y = ty
        d.target_x = tx
        d.target_y = ty
        d.target_source = "tackler_commit"

        # ── HORIZONTAL VELOCITY GUARANTEE (§3) ──
        # CHASE is the tackle-commit movement state: non-smooth-damp
        # (fast accel-lerp), not stabilised, lateral_priority 0.3 — the
        # defender always has forward velocity toward the target.
        d.movement_state = MovementState.CHASE
        d.move_speed = min(
            getattr(d, "max_speed_mps", ceiling),
            ceiling,
        )
        d.frozen = False
        d.timing_locked = False
        d.reaction_timer = 0.0
        d.retreat_delay = 0.0
        d.lateral_hesitation = 0.0
        d.step_stagger = 0.0
        d.step_stagger_active = False

    return committed


class DefensiveBehaviourUnified:
    """Wrapper that selects Lvl-1 or STEP-2 behaviour based on game sub-state and ball position."""

    # ── TASK J DEFENSIVE LINE AUDIT PROBE (2026-08-11) ──
    # Behaviour-INERT pipeline-order probe.  `_probe` appends a stage
    # marker to ``world._dbg_pipeline`` ONLY when the world carries the
    # list (installed by verify_defensive_line.py).  In real gameplay no
    # such attribute exists, so the call is a pure no-op — the production
    # pipeline is byte-identical with or without the probe.  The probe
    # marks the 8 stages Task J1 audits:
    #
    #     governor -> spacing -> width -> retreat -> close -> finalise
    #     -> anchor audit -> MMDL
    @staticmethod
    def _probe(world, stage: str) -> None:
        dbg = getattr(world, "_dbg_pipeline", None)
        if dbg is None:
            return
        dbg.append((stage, getattr(world, "frame_count", 0)))

    # ── LATERAL JIGGLE KILL (2026-08-02) ──
    # Finalise blend + per-frame delta cap applied ONCE per frame to every
    # defender's target AFTER all writers (geometry, close, retreat,
    # collapse, decisions) have run.  Kills one-frame target snaps from
    # multi-writer conflict, PRIMARY/SECONDARY re-rank jumps, the post-ruck
    # reform ~10m snap, and lane-swap Y hops.
    FINALISE_BLEND: float = 0.5
    FINALISE_MAX_DELTA_M: float = 0.4

    def __init__(self, tuners):
        """Initialise unified defensive behaviour dispatcher.

        Args:
            tuners: DefenceTuners instance.  ``tuners.max_defensive_speed_mps``
                is the single authoritative top-speed ceiling every movement
                writer must derive from — no subsystem defines its own.
        """
        self.tuners = tuners
        self.def_geometry = DefensiveGeometry(tuners)
        self.def_timing = DefensiveTiming(tuners)
        self.def_role_spacing = DefensiveRoleSpacing(tuners)
        self.line_governor = DefensiveLineGovernor(tuners, geometry=self.def_geometry)
        self.lvl1 = DefensiveBehaviourLvl1(tuners, geometry=self.def_geometry)
        self.step2 = DefensiveBehaviour(tuners, geometry=self.def_geometry)

        # ── POST-CATCH DEFENSIVE LINE CLAMP (2026-08-02) ──
        # Consecutive structured-stable OPEN_PLAY frames seen so far (depth
        # not flipping + pursuit off).  When this reaches
        # tuners.post_catch_stability_frames the clamp auto-disengages and
        # blends out via Player._defended_cap_ceiling.
        self._post_catch_stable_frames: int = 0
        # STAGE 3F: time-based twin of _post_catch_stable_frames.  The
        # frame counter drifted the post-catch clamp release point with
        # tick rate; this accumulator releases after the same wall-clock
        # window at any frame rate.
        self._post_catch_stable_time_s: float = 0.0

    def _def_speed(self, defender):
        """Authoritative defensive top speed for *defender*.

        Returns the unified ceiling (``tuners.max_defensive_speed_mps``)
        clamped by the player's physical max (``max_speed_mps``).  EVERY
        defensive movement writer derives its speed from this single source;
        no subsystem may define its own independent top speed.  Behaviours
        may scale BELOW this ceiling (e.g. pursuit 1.0x, retreat 0.85x,
        PASSIVE 0.5x) — never above it.
        """
        ceiling = getattr(
            self.tuners, "max_defensive_speed_mps", 8.3
        )
        return min(getattr(defender, "max_speed_mps", ceiling), ceiling)

    @staticmethod
    def _get_ball_x(world):
        """Extract ball X position from world, normalising between Ball and Player objects.

        When the ball is carried, use the carrier's TRUE position (not the eased ball.x_m).
        This is critical - ball.x_m lags behind the carrier by 65% each frame due to
        the easing in Ball.update(), causing defenders to target stale positions.
        """
        # Ball carrier is the authority when the ball is held
        if world.ball_carrier is not None:
            return world.ball_carrier.x
        ball = getattr(world, "ball", None)
        if ball is not None:
            # Check if ball has a possessed_by reference
            if getattr(ball, "possessed_by", None) is not None:
                return ball.possessed_by.x
            return getattr(ball, "true_x_m", getattr(ball, "x", 60.0))
        return getattr(world, "ball_x_m", 60.0)

    @staticmethod
    def _get_ball_y(world):
        """Extract ball Y position from world.

        When the ball is carried, use the carrier's TRUE position (not the eased ball.y_m).
        """
        if world.ball_carrier is not None:
            return world.ball_carrier.y
        ball = getattr(world, "ball", None)
        if ball is not None:
            if getattr(ball, "possessed_by", None) is not None:
                return ball.possessed_by.y
            return getattr(ball, "true_y_m", getattr(ball, "y", 35.0))
        return getattr(world, "ball_y_m", 35.0)

    @staticmethod
    def _is_carrier_past_anchor(world, bc) -> bool:
        """True once the ball carrier is clearly past the defensive anchor.

        TASK VI (2026-08-15): the single line-break gate used by both the
        step-7a engaged-non-lane chase writer and the MMDL LINE-gate / blend
        bypass.  Delegates to the MMDL helper so the threshold (+1.0 m past
        the locked anchor) stays single-sourced.
        """
        return carrier_past_defensive_line(world, bc)

    # ----------------------------------------------------------------
    # LANE INDEX - Y-sort position for tackle-EDGE assignment ONLY
    # ----------------------------------------------------------------

    @staticmethod
    def _compute_lane_indices(world, defenders):
        """Assign lane_index to each defender based on Y-sorted position.

        Centre defender(s) get 0, others get +/-1, +/-2, +/-3 ...
          N=7: indices = -3, -2, -1, 0, +1, +2, +3
          N=6: indices = -2, -1, 0, 0, +1, +2  (two centre-most get 0)

        Each defender gets ``d.lane_index`` (int) set as a side-effect.

        LANE INDEX IS TACKLE-EDGE ONLY (2026-08-09): it never drives
        geometry, line shape, or any Y target.  It is computed right
        before select_tacklers() so the outermost PASSIVE defenders can
        be assigned EDGE tackle roles.

        LANE HYSTERESIS (2026-08-02): lane ordering is computed with a
        bubble-sort that only swaps adjacent defenders when their Y
        separation exceeds LANE_SWAP_HYSTERESIS_M.  Two defenders trading
        Y by centimetres no longer swap lane slots, which previously
        snapped their lane-Y targets and EDGE/FULLBACK tackle roles
        left/right each frame (visible lateral jiggle).
        """
        LANE_SWAP_HYSTERESIS_M = 0.5

        n = len(defenders)
        if n == 0:
            return

        # Bubble sort with hysteresis: swap adjacent pair (a above b) only
        # when a is more than HYSTERESIS metres above b.  Within the
        # hysteresis band the previous relative order is preserved.
        # SWAP IMMUNITY (2026-08-19): defenders with a live authoritative
        # slot (drive-end compute) are NOT swapped — their assigned slot
        # position must hold for the authoritative frame.
        order = list(defenders)
        changed = True
        while changed:
            changed = False
            for i in range(n - 1):
                a = order[i]
                b = order[i + 1]
                # Skip swap if either defender is drive-end authoritative
                if (getattr(a, "authoritative_until_frame", -1) >= 0
                        or getattr(b, "authoritative_until_frame", -1) >= 0):
                    continue
                if a.y > b.y + LANE_SWAP_HYSTERESIS_M:
                    order[i], order[i + 1] = order[i + 1], order[i]
                    changed = True

        mid = (n - 1) / 2.0
        for i, d in enumerate(order):
            lane_index = int(i - mid)
            d.lane_index = lane_index
            d._prev_lane_index = lane_index

    # ----------------------------------------------------------------
    # RETREAT COMPLIANCE - mandatory 10-metre retreat check
    # ----------------------------------------------------------------

    @staticmethod
    def _is_retreat_eligible(defender, ruck_x, compliance_dist):
        """Return True if *defender* has retreated at least *compliance_dist*
        metres behind *ruck_x*, OR is a marker (always exempt).

        Markers (MARKER_A, MARKER_B) are always eligible regardless of
        their X position - they play tight to the ruck.
        """
        marker_roles = (DefenderRole.MARKER_A, DefenderRole.MARKER_B)
        if getattr(defender, "defender_role", None) in marker_roles:
            return True
        # REMOVED freeze_timer exemption (July 2026):
        # Non-marker defenders with freeze_timer > 0 must still comply
        # with retreat rules. The blanket exemption allowed frozen
        # defenders who were illegally in front of the ruck to be
        # classified as PRIMARY/SECONDARY. Markers remain exempt via
        # their own role check above.
        return defender.x >= ruck_x + compliance_dist

    # ----------------------------------------------------------------
    # DEPTH CLASSIFICATION - RANKED SELECTION (July 2026)
    #
    # Replaces the threshold-based per-defender approach.  Enforces
    # exactly 1 PRIMARY and 2 SECONDARY regardless of position.
    # All other non-marker defenders become PASSIVE.
    # ----------------------------------------------------------------

    # ----------------------------------------------------------------
    # CHASE-SCENARIO AGGRESSIVE DEPTH OVERRIDE (2026-08-03)
    #
    # TACKLE ENGINE FIX: The unified defensive speed ceiling removed the
    # legacy "turbo" bursts that used to accidentally bring PASSIVE
    # defenders into contact with the BC.  PASSIVE depth disables tackle
    # acquisition (no structured_close BC-relative targets, mandatory
    # retreat re-targets them backward, and select_tacklers never reaches
    # them inside the PRIMARY/SECONDARY distance windows).  Therefore the
    # classifier MUST NOT assign PASSIVE when the ball carrier is in an
    # active chase / tackle-eligible state.
    #
    # Any defender still PASSIVE after the ranked 1-PRIMARY/2-SECONDARY
    # assignment is promoted to SECONDARY when BOTH:
    #   1. the BC is in a chase scenario (OPEN_PLAY, sprinting, moving
    #      > 4.0 m/s, or past the defensive line), AND
    #   2. the defender is tactically engageable (within 12m of BC, or
    #      in front of BC and closing).
    #
    # The override restores: primary/secondary tackler assignment,
    # tackle acquisition, collision resolution, and PTB transition.
    # ----------------------------------------------------------------

    @staticmethod
    def _is_chase_scenario(world, bc) -> bool:
        """Return True if the ball carrier is in an active chase or
        tackle-eligible situation that forbids PASSIVE depth.

        Mirrors the engine contract: OPEN_PLAY sub-state, sprinting,
        speed > 4.0 m/s, or BC past the defensive line anchor.
        """
        if bc is None:
            return False

        # 1. BC in OPEN_PLAY (the BC.phase == OPEN_PLAY equivalent)
        if world.sub_state == GameSubState.OPEN_PLAY:
            return True

        # 2. BC explicitly sprinting
        if getattr(bc, "is_sprinting", False):
            return True

        # 3. BC moving fast
        bc_speed = math.hypot(getattr(bc, "vx", 0.0), getattr(bc, "vy", 0.0))
        if bc_speed > 4.0:
            return True

        # 4. BC has broken the defensive line
        anchor_x = getattr(world, "defensive_line_anchor", None)
        if anchor_x is not None and anchor_x > 0.0 and bc.x > anchor_x:
            return True

        return False

    @staticmethod
    def _should_be_aggressive(d, bc) -> bool:
        """Return True if *d* is tactically engageable with the BC and
        must not be classified PASSIVE.

        A defender within 12m of the BC, or in front of the BC and
        closing, is a live tackle threat.
        """
        if bc is None:
            return False

        # Within strike distance of the BC
        if math.hypot(d.x - bc.x, d.y - bc.y) < 12.0:
            return True

        # In front of the BC (defender.x > bc.x since attack runs +X)
        # and closing the gap (moving forward toward the BC).
        if bc.x - d.x < 0.0 and getattr(d, "vx", 0.0) > 0.0:
            return True

        return False

    def _is_tes_non_tackler(self, world, d) -> bool:
        """True when TES is active and this defender is NOT a registered tackler.

        During TES only the registered tacklers (primary/secondary/joiners)
        may receive chase/close writers.  Every other defender must fold
        into a TES-support role (retreat / line reset / freeze) instead of
        sprinting like they are still in open-play chase.
        """
        if not getattr(world, "tes_active", False):
            return False
        # Build the union of known TES tackler ids from all registries.
        tackler_ids = set(getattr(world, "tackler_ids", None) or ())
        pid = getattr(world, "primary_tackler_id", None)
        if pid is not None:
            tackler_ids.add(pid)
        for sid in (getattr(world, "secondary_tacklers", None) or ()):
            tackler_ids.add(sid)
        # If no tacklers are registered yet we cannot know who is exempt.
        # Fall back to no-gating so the very first TES frame is not broken.
        if not tackler_ids:
            return False
        return d.player_id not in tackler_ids

    def classify_depth_all(self, world, defenders):
        """Ranked depth classification: exactly 1 PRIMARY, 2 SECONDARY, rest PASSIVE.

        Replaces the threshold-based per-defender classify_defender_depth()
        for structured mode.  Guarantees the intended 1-2-4 distribution
        regardless of defender positions relative to BC.

        Markers (MARKER_A, MARKER_B) are preserved as "MARKER" and never
        reclassified - they always retain their role-based depth class.
        """
        bc = world.ball_carrier
        if bc is None:
            for d in defenders:
                d.depth_class = "PASSIVE"
            return

        marker_roles = (DefenderRole.MARKER_A, DefenderRole.MARKER_B)

        # ── TASK A-2: PERSISTENT ENGAGEMENT STATE ──
        # Update the hysteresis/lock-protected engagement role ONCE per frame.
        # ``role_depth_class`` holds the STABLE band posture (AGGRESSIVE /
        # BALANCED / PASSIVE); it cannot flip frame-to-frame because the
        # module enforces the 20-frame switch interval, the PASSIVE role-lock,
        # and the side-lock.  The ranked 1-PRIMARY/2-SECONDARY promotion below
        # still runs on top so the tackle pipeline is never starved.
        update_engagement_state(world, defenders)

        # First pass: markers keep "MARKER", everyone else starts from the
        # stable engagement posture.  BALANCED/AGGRESSIVE non-markers seed as
        # PASSIVE (normal ranked selection applies); PASSIVE-role defenders
        # seed as PASSIVE and can only be promoted by the ranked/chase layers
        # below — never re-flipped by drift.
        for d in defenders:
            if getattr(d, "defender_role", None) in marker_roles:
                d.depth_class = "MARKER"
            elif (
                is_lane_tackler_candidate(d, bc.x)
                # ── RETREAT-OBLIGATION VETO (2026-09-17) ──
                # This is the FIFTH PASSIVE -> SECONDARY grant in this
                # module and the easiest to miss: it routes through
                # ``is_lane_tackler_candidate`` with only ``bc.x``, so that
                # predicate cannot see ``world`` and its own obligation veto
                # (inside ``_is_eligible_lane_defender``) returns False
                # vacuously.  The obligation is therefore re-checked here
                # with the live world.
                and not is_retreat_obligated(d, world)
            ):
                d.depth_class = "SECONDARY"
            else:
                d.depth_class = "PASSIVE"

        # TES NON-TACKLER GATE (2026-08-03): during TES only registered
        # tacklers may be classified PRIMARY/SECONDARY (chase-eligible).
        # Non-tacklers stay PASSIVE so step 10 structured_close never
        # writes BC-relative chase targets for them.
        if getattr(world, "tes_active", False):
            for d in defenders:
                if getattr(d, "defender_role", None) in marker_roles:
                    continue
                if self._is_tes_non_tackler(world, d):
                    d.depth_class = "PASSIVE"

        # LINE BREAK COLLAPSE: promote PASSIVE defenders to SECONDARY when
        # the ball carrier has advanced past the defensive line anchor.
        # This is the PRIMARY fix for STUCK_NOT_FROZEN — PASSIVE defenders
        # who remain PASSIVE after line break get static anchor_x targets
        # and never move.  Promoting them to SECONDARY instantly unlocks
        # BC-relative targets, CHASE movement_state, and full speed.
        #
        # POST-CATCH PURSUIT (2026-08-16): the same promotion fires for the
        # whole post-catch window (post_catch_chase_window_s > 0) so the
        # line keeps converging on the BC immediately after a kickoff catch
        # instead of creeping at DRIFT speed (the other half of the
        # post-catch stall — together with the MMDL CHASE override).
        #
        # This runs BEFORE Euclidean ranking so the top-3 assignment below
        # still applies on top (PRIMARY is still the closest, etc.).
        # The promotion ensures ALL non-marker defenders get at least
        # SECONDARY classification when the line is broken, which is the
        # sole gate for the structured_close pipeline (step 10) that
        # produces velocity-producing BC-relative targets.
        if bc is not None:
            line_anchor = getattr(world, "defensive_line_anchor", None)
            post_catch_active = getattr(world, "post_catch_chase_window_s", 0.0) > 0.0
            if (line_anchor is not None and bc.x > line_anchor + 1.0) or post_catch_active:
                for d in defenders:
                    if getattr(d, "defender_role", None) not in marker_roles and getattr(d, "depth_class", "PASSIVE") == "PASSIVE":
                        # ── RETREAT-OBLIGATION VETO (2026-09-17) ──
                        # A body still owing its 10 m retreat stays PASSIVE
                        # here too: this is a third PASSIVE -> SECONDARY
                        # promotion path, and every one of them is a
                        # tackle-authority grant.  The body keeps its line
                        # geometry and keeps retreating; it re-qualifies the
                        # frame its deficit clears (a fraction of a second
                        # at the ~7 m/s retreat speed).
                        if is_retreat_obligated(d, world):
                            continue
                        d.depth_class = "SECONDARY"

        # ── BAND-FIRST RANKING (2026-08-12) ────────────────────────────
        # The ranked 1-PRIMARY / 2-SECONDARY assignment is sorted by depth
        # BAND first (A -> B -> C/D -> unassigned), then Euclidean distance
        # within the band.  The A/B defenders are by definition the ones
        # closest to the ball-carrier's lateral lane (Y-distance bands in
        # assign_depth_roles), so ranking them first guarantees the
        # structured-close writers (step 10) and the tackle pipeline engage
        # the SAME defenders who hold the line's inner bands — not a
        # distance-fluke C/D defender that happened to be nearest in space
        # while the real A/B closers run to spacing positions.
        def _band_rank(d):
            return {"A": 0, "B": 1, "C": 2, "D": 3}.get(
                getattr(d, "depth_role", None), 4
            )

        non_markers = [d for d in defenders
                       if getattr(d, "defender_role", None) not in marker_roles]
        # ── RETREAT-OBLIGATION VETO (2026-09-17) ──
        # The committed top-1 / top-2 assignment is ITSELF a tackle-authority
        # grant (PRIMARY/SECONDARY unlock structured_close, the MMDL CHASE
        # branches and the engage machine), so an obligated body is removed
        # from the ranking pool rather than merely being excluded from the
        # promotion loops above.  Without this the ranked assignment would
        # re-grant on the same frame exactly what `strip_tackle_authority`
        # had just revoked.
        ranked = [
            d for d in sorted(
                non_markers,
                key=lambda d: (
                    _band_rank(d),
                    math.hypot(d.x - bc.x, d.y - bc.y),
                ),
            )
            if not is_retreat_obligated(d, world)
        ]

        # Assign exactly 1 PRIMARY and 2 SECONDARY
        if ranked:
            ranked[0].depth_class = "PRIMARY"
        for d in ranked[1:3]:
            d.depth_class = "SECONDARY"

        # ─────────────────────────────────────────────────────────────
        # CHASE-SCENARIO AGGRESSIVE DEPTH OVERRIDE (MANDATORY)
        #
        # When the ball carrier is in an active chase / tackle-eligible
        # state, DO NOT leave any engageable defender PASSIVE.  PASSIVE
        # depth disables tackle acquisition entirely:
        #   - no structured_close BC-relative targets (PRIMARY/SECONDARY
        #     only in _apply_structured_open_play step 10)
        #   - mandatory retreat re-targets non-compliant PASSIVE
        #     defenders backward to ruck_x + compliance (step 9)
        #   - select_tacklers() never reaches PASSIVE defenders inside
        #     its PRIMARY/SECONDARY distance windows, so tackle_role
        #     stays PASSIVE and try_pre_tackle() rejects them
        #
        # Promoting to SECONDARY unlocks BC-relative closing targets,
        # CHASE movement_state, full ceiling speed, tackle-role
        # assignment, and the pre-tackle trigger.  Markers are never
        # reclassified (they play tight to the ruck).
        # ─────────────────────────────────────────────────────────────
        if self._is_chase_scenario(world, bc):
            for d in defenders:
                if getattr(d, "defender_role", None) in marker_roles:
                    continue
                if getattr(d, "depth_class", "PASSIVE") != "PASSIVE":
                    continue
                # ── RETREAT-OBLIGATION VETO (2026-09-17) ──
                # A body that still owes its 10 m retreat stays PASSIVE:
                # promoting it would remove it from the compliance
                # population this same frame, which is exactly how the
                # obligation stopped being enforced.
                if is_retreat_obligated(d, world):
                    continue
                if self._should_be_aggressive(d, bc):
                    d.depth_class = "SECONDARY"

        # ── 12M PURSUIT/COMMIT PROXIMITY PROMOTION (2026-09-02) ──
        # Any non-marker, non-fullback, non-tackle-state defender within
        # pursuit_commit_radius_m (default 12m) of the BC is promoted to at
        # least SECONDARY on PURE proximity — independent of the chase-scenario
        # gate above — so the structured-close/engageable system and the MMDL
        # treat it as a pursuer.  The target override + MMDL gate then drive
        # the actual CHASE/SHOOT; this only unlocks the engageable classification.
        _pc_radius = float(getattr(self.tuners, "pursuit_commit_radius_m", 12.0))
        for d in defenders:
            if getattr(d, "defender_role", None) in marker_roles:
                continue
            if getattr(d, "fullback_role_active", False):
                continue
            if getattr(d, "tackle_state_active", False):
                continue
            if getattr(d, "depth_class", "PASSIVE") != "PASSIVE":
                continue
            # ── RETREAT-OBLIGATION VETO (2026-09-17) ──
            # THE LOAD-BEARING CHECK.  This loop promotes on PURE proximity
            # (within pursuit_commit_radius_m of the carrier), which is
            # almost always TRUE for a defender that has just left a ruck at
            # the carrier's own position.  Without the veto, a returning
            # defender was promoted out of PASSIVE on the very frame it
            # regained DEF_LINE_READY — before step 9 (MANDATORY RETREAT),
            # the MMDL retreat branch or Layer-7 could see it.  It then
            # unlocked structured_close (BC-relative closing targets),
            # CHASE movement_state, full ceiling speed, lane-role
            # assignment and the pre-tackle trigger — a complete tackle
            # authority grant, obtained without ever satisfying the 10 m
            # retreat.  Keeping the body PASSIVE leaves its geometry,
            # spacing and width writers running normally (line shape is
            # unchanged) while it completes the retreat.
            if is_retreat_obligated(d, world):
                continue
            if math.hypot(d.x - bc.x, d.y - bc.y) < _pc_radius:
                d.depth_class = "SECONDARY"

    # ----------------------------------------------------------------
    # DEPTH CLASSIFICATION (BC-relative, based on chase radius tuners)
    # ----------------------------------------------------------------

    def classify_defender_depth(self, world, d):
        """Classify a defender as PRIMARY, SECONDARY, or PASSIVE based on
        lateral (Y-axis) and depth (X-axis) distance to the BALL CARRIER.

        Priority order:
          1. MARKER_A/B -> "MARKER" (preserved, never reclassified)
          2. No BC -> "PASSIVE"
          3. |dy| <= PRIMARY_LATERAL_THRESHOLD -> "PRIMARY"
          4. |dx| <= SECONDARY_DEPTH_THRESHOLD -> "SECONDARY"
          5. Otherwise -> "PASSIVE"

        The X-distance check restores PASSIVE classification for defenders
        who are far behind the BC in depth.  This is critical for:
          - line shape (deep defenders stay as PASSIVE, not SECONDARY)
          - anchor_x behaviour (PASSIVE defenders target anchor_x)
          - proper spacing (PASSIVE defenders spread on the line)
          - proper depth bands (PASSIVE defenders have different depth offsets)
          - proper tackle angles (only PRIMARY/SECONDARY chase)
        """
        # Gate: MARKER_A/B preserve their role -- never reclassified
        marker_roles = (DefenderRole.MARKER_A, DefenderRole.MARKER_B)
        if getattr(d, "defender_role", None) in marker_roles:
            return "MARKER"

        bc = world.ball_carrier
        if bc is None:
            return "PASSIVE"

        # Lateral Y distance to ball carrier -- PRIMARY
        dy = abs(d.y - bc.y)
        if dy <= self.tuners.PRIMARY_LATERAL_THRESHOLD:
            return "PRIMARY"

        # Depth X distance -- SECONDARY if close enough, otherwise PASSIVE
        dx = abs(d.x - bc.x)
        if dx <= self.tuners.SECONDARY_DEPTH_THRESHOLD:
            return "SECONDARY"

        # ─────────────────────────────────────────────────────────────
        # CHASE-SCENARIO AGGRESSIVE DEPTH OVERRIDE (MANDATORY)
        #
        # If the ball carrier is in an active chase / tackle-eligible
        # state, DO NOT classify this defender PASSIVE.  PASSIVE depth
        # disables tackle acquisition entirely (no BC-relative closing
        # targets, mandatory retreat, and tackle_roles stay PASSIVE).
        # Any engageable defender is promoted to AGGRESSIVE (SECONDARY)
        # so the tackle engine can activate.
        # ─────────────────────────────────────────────────────────────
        if self._is_chase_scenario(world, bc):
            # ── RETREAT-OBLIGATION VETO (2026-09-17) ──
            # Fourth PASSIVE -> SECONDARY promotion path.  Consistent with
            # the three loops above: an outstanding 10 m retreat obligation
            # bars the tackle-authority promotion, and nothing else about
            # the body's geometry changes.
            if is_retreat_obligated(d, world):
                return "PASSIVE"
            if self._should_be_aggressive(d, bc):
                return "SECONDARY"

        return "PASSIVE"

    # ----------------------------------------------------------------
    # MAIN ENTRY
    # ----------------------------------------------------------------

    def apply(self, world, defenders, base_line_x, lane_y):
        # STEP-03A-09B: early-return when defence is disabled
        if not getattr(world, 'defence_enabled', True):
            return

        # ── POST-RETREAT OPEN_PLAY FRAME COUNTER (2026-09-02) ──
        # The retreat writer sets target_y = retreat_spacing_y.  For the
        # first two OPEN_PLAY frames after the retreat, the split-spacing /
        # sideline-macro / width-eligibility clamps must NOT overwrite ty —
        # the retreat spacing Y stays authoritative so the line settles
        # before re-broadening.  Detect the non-OPEN_PLAY -> OPEN_PLAY edge
        # and reset the counter; increment every OPEN_PLAY frame.  Default
        # 99 (past the window) so non-OPEN_PLAY frames are never gated.
        _prev_op = getattr(world, "_prev_frame_open_play", None)
        _cur_op = (getattr(world, "sub_state", None) == GameSubState.OPEN_PLAY)
        if _prev_op is False and _cur_op:
            world._post_retreat_open_play_frames = 0
        if _cur_op:
            world._post_retreat_open_play_frames = (
                getattr(world, "_post_retreat_open_play_frames", 0) + 1
            )
        world._prev_frame_open_play = _cur_op

        # ── TASK G ANCHOR AUTHORITY: lock the frame anchor ONCE. ──
        # Layer 0/1: the anchor is always ruck_x + 10 (never BC/DH/spacing/
        # width/audit-derived) and is computed + locked once per frame,
        # once per ruck, BEFORE any defensive target writer runs.  Any
        # later attempt to CHANGE the locked anchor raises
        # AnchorAuthorityViolation (see anchor_authority.py).
        compute_and_lock_anchor(world)

        # PHASE GUARD - REMOVED (July 2026)
        # This guard blocked defence during KICKOFF phase.
        # The apply() method has robust substate branching.
        # The REMOVED from world.world_state import GamePhase + guard

        # ---------------------------------------------------------------
        # GLOBAL MODE GATE - THE ONE RULE (July 2026)
        #
        # Hard binary switch: pursuit mode XOR structured defence.
        # No overlap, no fallthrough, no mixed state.
        #
        # This gate runs BEFORE any sub-dispatch - including kickoff
        # chase, OPEN_PLAY, PTB phases, RELEASE, TACKLE phases.
        # Everything below is inside the structured branch.
        # ---------------------------------------------------------------
        if world.in_pursuit_mode:
            # -- HARD-DISABLE all structured subsystems --
            self.line_governor.disabled = True
            self.def_role_spacing.disabled = True

            # -- Declare writer and run pursuit --
            world.current_target_writer = "pursuit"
            self.apply_pursuit_mode(world, defenders)

            # -- LAW 1: assert only pursuit wrote targets --
            assert world.current_target_writer == "pursuit", (
                f"[MODE GATE] writer changed from 'pursuit' to "
                f"'{world.current_target_writer}' during pursuit dispatch"
            )
            return

        # ---------------------------------------------------------------
        # STRUCTURED MODE - all structured subsystems HARD-ENABLED
        # ---------------------------------------------------------------
        self.line_governor.disabled = False
        self.def_role_spacing.disabled = False
        world.current_target_writer = "structured"

        # ---------------------------------------------------------------
        # MODE INVARIANT ASSERT - enforces first_tackle_not_made
        # invariant in structured mode.
        #
        # If in_pursuit_mode is False (i.e. we are in structured mode),
        # first_tackle_not_made MUST also be False.  A hybrid where
        # pursuit is off but first_not_made is still True is an
        # undefined illegal state that breaks all defensive subsystems
        # (geometry, retreat, compression, anchor, ruck_x, lane_index).
        # ---------------------------------------------------------------
        if not world.in_pursuit_mode:
            assert not world.first_tackle_not_made, \
                "[MODE ERROR] pursuit=False but first_tackle_not_made=True"

        # ROLE DUMP - REMOVED (was drowning BC teleport trace; 2026-07-21 cleanup)

        # KICKOFF CHASE BRANCH REMOVED (2026-08-08): this branch was dead
        # code — OpenPlayEngine.update() (and therefore apply()) never runs
        # during GamePhase.KICKOFF; run.py dispatches only KickoffEngine +
        # catch during that phase.  The kickoff fullback is now assigned by
        # KickoffEngine._assign_kickoff_fullback(), and KMC owns all kickoff
        # player movement.  Removing this branch also eliminates the
        # first_tackle_not_made assert trap above, which would have fired
        # had apply() ever been entered during kickoff.

        # -----------------------------------------------------------
        # OPEN_PLAY - FIRST-CLASS DISPATCH (not fallthrough)
        # -----------------------------------------------------------
        if world.sub_state == GameSubState.OPEN_PLAY:
            # MARKER AUTHORITY LOCK (2026-09-04): after every OPEN_PLAY
            # defence writer (structured or unified) runs, re-assert each
            # MARKER_A/B to the single ruck-pin geometry target
            # (ruck_x + depth, ruck_y).  No freeze / HOLD / timing / retreat /
            # spacing / pin branch may leave a marker on a stale d.x/d.y
            # target; frozen/move_speed=0 still suppresses velocity.
            from game.defence.defence_lvl_1.marker_authority import (
                enforce_marker_geometry_authority as _enforce_marker_geo,
            )
            if getattr(world, 'use_structured_open_play', True):
                self._apply_structured_open_play(world, defenders)
            else:
                self._apply_open_play_unified(world, defenders)
            _enforce_marker_geo(world, defenders)
            return

        # -----------------------------------------------------------
        # STEP-03A-06C - TES tackle-phase guard (revised 2026-08-29)
        # -----------------------------------------------------------
        # Only TACKLER_1/2/3, MARKER_A/B, ATT_CARRIER and DUMMY_HALF
        # remain in TES isolation.  Every other defender keeps updating
        # depth, retreat, and spacing normally while the tackle runs.
        # The open-play defensive geometry pipeline (depth-role assignment,
        # retreat to the anchor, and split-design spacing) is re-enabled for
        # all non-tacklers; TES only owns the BC, the DH, the markers and
        # the registered tacklers.
        bc = world.ball_carrier
        if is_tackle_phase(world):
            TES_MOVER_ROLES = frozenset({
                DefenderRole.TACKLER_1,
                DefenderRole.TACKLER_2,
                DefenderRole.TACKLER_3,
                DefenderRole.MARKER_A,
                DefenderRole.MARKER_B,
            })
            # Build the non-tes-isolated defender pool.
            line_defenders = []
            for d in defenders:
                # Ball carrier / dummy half never enter defensive targeting.
                if d is bc:
                    continue
                if getattr(d, "attack_role", None) == "dummy_half":
                    continue
                # Fullback is independent backfield anchor; only
                # re-asserted at RELEASE->PTB by the handlers.
                if getattr(d, "fullback_role_active", False):
                    continue
                # Tacklers / TES markers keep TES-owned movement.
                if getattr(d, "defender_role", None) in TES_MOVER_ROLES:
                    continue
                line_defenders.append(d)

            if not line_defenders:
                return

            # ── DEPTH-ROLE ASSIGNMENT (single-assign-per-frame guard) ──
            # Persistent A/B/C/D/CENTRE/WINGER depth roles are assigned for
            # every non-tes-isolated defender (TACKLER_*/MARKER_*/DUMMY_HALF
            # and the fullback are excluded by assign_depth_roles itself).
            if not _depth_roles_assigned_this_frame(world):
                assign_depth_roles(line_defenders, world)

            # ── RETREAT + SPACING (sole forced-retreat authority) ──
            # X <- anchor authority (ruck_x + 10), Y <- frame spacing dict.
            # Frozen / freeze-timer defenders are never moved by the writer;
            # the TES-isolated roles are excluded from the pool above.
            apply_forced_retreat(
                world, line_defenders, self.tuners,
                governor=self.line_governor,
            )

            # ── SPACING for compliant defenders ──
            # apply_forced_retreat only writes non-compliant (offside)
            # retreaters.  Every compliant non-tackler still receives its
            # split-design spacing Y and its stamped role-depth X so the
            # line keeps its authoritative width while the tackle runs.
            for d in line_defenders:
                if d.frozen or getattr(d, "freeze_timer", 0.0) > 0:
                    continue
                d.target_y = _frame_spacing_y(
                    world, d, self.tuners,
                    governor=self.line_governor,
                )
                stamp_target_write(world, d, "spacing")
                _role_t = getattr(d, "depth_role", "D") or "D"
                _offsets_t = getattr(self.tuners, "WIDTH_OFFSETS", None) or {}
                if bc is not None:
                    d.target_x = float(bc.x) + _offsets_t.get(
                        _role_t, _offsets_t.get("D", 5.0)
                    )
                else:
                    rdx = getattr(d, "role_depth_target_x", None)
                    if rdx is not None:
                        d.target_x = float(rdx)
                d.frozen = False

            # TES movement is owned by the phase handlers; this dispatcher
            # only drives the non-tes-isolated line.  Return without falling
            # through to the OPEN_PLAY pipelines.
            return

        sub = world.sub_state

        # -- TACKLE_CONTACT: freeze only TACKLER_1/2/3 and MARKER_A/B --
        if sub == GameSubState.TACKLE_CONTACT:
            return self.lvl1.apply_contact(world, defenders)

        # -- TACKLE_DOMINANCE: NO marker-role assignment (MARKER LOCK,
        #    2026-09-04) --
        # Marker ROLE assignment is locked to the TES-CONTACT / RELEASE / PTB
        # window only.  The distance-based apply_markers() promoted
        # non-tacklers into MARKER_A/B during the DRIVE/DOMINANCE sequence,
        # forming markers early.  Marker roles are assigned only via
        # _assign_markers_for_new_tackle at TACKLE_CONTACT (strict tackler-
        # only) and _prepare_release (full 3-case rule).  Here we only
        # maintain non-marker retreat/spacing (the is_tackle_phase block
        # above already owns this for the normal path); no defender_role is
        # ever mutated to MARKER_A/B during DRIVE/DOMINANCE/OPEN_PLAY.
        if sub == GameSubState.TACKLE_DOMINANCE:
            ALLOWED_FREEZE_ROLES = frozenset({
                DefenderRole.TACKLER_1,
                DefenderRole.TACKLER_2,
                DefenderRole.TACKLER_3,
                DefenderRole.MARKER_A,
                DefenderRole.MARKER_B,
            })
            retreat_target_x = _anchor_m(world)
            _offsets_dom = getattr(self.tuners, "WIDTH_OFFSETS", None) or {}
            for d in defenders:
                if getattr(d, "defender_role", None) == DefenderRole.KICK_CHASE:
                    continue
                if getattr(d, "defender_role", None) in ALLOWED_FREEZE_ROLES:
                    continue
                if getattr(d, "fullback_role_active", False):
                    continue
                d.frozen = False
                _role_dom = getattr(d, "depth_role", "D") or "D"
                # DOMINANCE (drive end): non-tackler line defenders hold
                # bc.x + WIDTH_OFFSETS[depth_role] — they sit RIGHT of the
                # ball carrier (the line tracks the drive), not back at the
                # stale pre-drive ruck + 10 (the "go the wrong way" bug).
                if bc is not None:
                    d.target_x = float(bc.x) + _offsets_dom.get(
                        _role_dom, _offsets_dom.get("D", 5.0)
                    )
                else:
                    d.target_x = retreat_target_x
                d.target_y = _frame_spacing_y(
                    world, d, self.tuners,
                    governor=self.line_governor,
                )
                stamp_target_write(world, d, "spacing")
            return

        # -- TACKLE_RELEASE (TES exit): hold position for non-markers, no movement --
        # Markers (MARKER_A, MARKER_B) are intentionally skipped here.
        # OPE's _move_markers_to_targets() sets ruck_x+1/ruck_x+2 targets each
        # frame, and the phase handler moves them via Player.update(dt).
        # If we set target_x=d.x here, OPE's targets get overwritten and markers
        # never move during RELEASE.
        #
        # TES ISOLATION CONTRACT (2026-08-29): during RELEASE every non-marker
        # defender — including DEF_RETREAT_10 / DEF_CHASE / DEF_LINE_READY and
        # the fullback — is HARD-FROZEN.  No retreat/spacing/line-target update.
        if sub == GameSubState.TACKLE_RELEASE:
            MARKER_ROLES = frozenset({DefenderRole.MARKER_A, DefenderRole.MARKER_B})
            for d in defenders:
                # FULLBACK HARD-EXCLUDE (2026-08-27): the dynamic fullback
                # is an independent backfield anchor owned by
                # apply_fullback_geometry.  Never pin it to its current
                # position here -- regardless of its defender_role.
                if getattr(d, "fullback_role_active", False):
                    continue
                if d.defender_role in MARKER_ROLES:
                    continue  # OPE moves markers via _move_markers_to_targets()
                # Hard-freeze every non-marker defender: no retreat, no chase,
                # no line-target update.  The RELEASE phase handler owns markers,
                # BC, and DH movement.
                d.frozen = True
                d.target_x = d.x
                d.target_y = d.y
                d.vx = 0.0
                d.vy = 0.0
            return

        # -----------------------------------------------------------
        # PTB PHASES - structured defence via _apply_open_play_unified()
        # -----------------------------------------------------------
        if sub in (
            GameSubState.PTB_READY,
            GameSubState.PTB,
            GameSubState.POST_PTB_RESET,
        ):
            return self._apply_open_play_unified(world, defenders)

        # -- Safety fallback (unknown sub-state): hold position --
        for d in defenders:
            # FULLBACK HARD-EXCLUDE (2026-08-27): never hold the
            # backfield anchor to its current position.
            if getattr(d, "fullback_role_active", False):
                continue
            d.target_x = d.x
            d.target_y = d.y
            d.vx = 0.0
            d.vy = 0.0

    # -----------------------------------------------------------
    # INTERCEPT LANE OWNERSHIP — per-side lane-tackler selection (2026-08-15)
    # -----------------------------------------------------------

    # ---------------------------------------------------------------
    # MACRO WIDTH GOVERNOR PUBLISH (2026-08-17, spec §1/§2)
    # ---------------------------------------------------------------
    # Publishes the macro width/spacing tuners to the world every OPEN_PLAY
    # frame so any future UI / compression-curve consumer reads the live
    # values from the world (spec: tuners published every OPEN_PLAY frame).
    # Pure publish — never reads or modifies defender targets.
    @staticmethod
    def _publish_macro_width_tuners(world, tuners) -> None:
        world.line_width_target_m = getattr(
            tuners, "DEF_LINE_TARGET_WIDTH_M", 40.0
        )
        world.defender_spacing_m = getattr(
            tuners, "DEFENDER_SPACING_M", 6.0
        )
        world.depth_role_lateral_offset_m = getattr(
            tuners, "DEPTH_ROLE_LATERAL_OFFSET_M", {}
        )
        world.width_governor_alpha = getattr(
            tuners, "width_governor_alpha", 0.08
        )
        world.compression_curve_enabled = bool(getattr(
            tuners, "compression_curve_enabled", False
        ))
        world.compression_curve_strength = float(getattr(
            tuners, "compression_curve_strength", 0.0
        ))

    # -----------------------------------------------------------
    # INTERCEPT LANE OWNERSHIP — per-side lane-tackler selection (2026-08-15)
    # -----------------------------------------------------------

    def select_lane_tacklers(self, world, defenders, bc) -> list:
        """Per-side lane-tackler selection with tuner-driven constants.

        Wraps ``intercept_tacklers.select_lane_tacklers``.  That module
        reads its stance radius / swap hysteresis from ``world``
        attributes; this wrapper stamps those attributes from the tuner
        source of truth (``self.tuners``) on every call so the values are
        never detached defaults and the tuners file stays the single
        authority (spec §1/§3).  Returns the list of PRIMARY lane
        tacklers (at most one per side of the BC).
        """
        if self.tuners is not None:
            world.pre_tackle_stance_radius_m = getattr(
                self.tuners, "pre_tackle_stance_radius_m", 0.8
            )
            world.lane_tackler_switch_hysteresis_m = getattr(
                self.tuners, "lane_tackler_switch_hysteresis_m", 0.5
            )
            # ── LANE SIDE HYSTERESIS (2026-09-15) ──
            # Published alongside the switch hysteresis so
            # intercept_tacklers.select_lane_tacklers() holds a defender's
            # side of the carrier's lane instead of re-deriving it from live
            # Y every frame (a centimetre of BC Y drift used to flip the
            # per-side PRIMARY owner and re-role a whole side to PASSIVE).
            world.lane_side_hysteresis_m = getattr(
                self.tuners, "lane_side_hysteresis_m", 0.35
            )
            # ── PRE-TACKLE SLOWDOWN BAND (2026-08-16) ──
            # Published alongside the stance radius so
            # intercept_tacklers.apply_pre_tackle_stance() reads the
            # tuner-driven band (decelerate inside `<band>` m of the BC)
            # and floor scale instead of detached defaults.
            world.pre_tackle_slowdown_band_m = getattr(
                self.tuners, "pre_tackle_slowdown_band_m", 3.0
            )
            world.pre_tackle_slowdown_scale = getattr(
                self.tuners, "pre_tackle_slowdown_scale", 0.45
            )
            # ── COMMIT-SPEED BUDGET (2026-09-15) ──
            # ``pretackle_commit_speed_mps`` is the documented CLOSING speed
            # for the per-side COMMIT attempt body, and the band/scale above
            # are defined as a taper OF THAT SPEED (docs in defensive_tuners:
            # "the commit speed tapers toward the plant", "floor multiplier
            # inside the band").  apply_pre_tackle_stance() previously
            # multiplied whatever the upstream writer left on move_speed,
            # which for a PRIMARY lane tackler is the unified PURSUIT ceiling
            # (max_defensive_speed_mps = 8.3) — so the taper ran on the wrong
            # base and its floor became 0.32*8.3 = 2.66 m/s instead of
            # 0.32*4.0 = 1.28 m/s.  Publish the commit speed alongside the
            # band/scale so one tuner source drives both consumers.
            world.pre_tackle_slowdown_commit_speed_mps = getattr(
                self.tuners, "pretackle_commit_speed_mps", 4.0
            )
            # ── ANTICIPATION SLOWDOWN ANCHOR (2026-09-08) ──
            # Publish the modernised slowdown distance anchor alongside the
            # band/scale so apply_pre_tackle_stance measures the deceleration
            # against the predicted threat point (bc_predicted), the
            # role-depth cell (role_depth_x), or the live BC (bc_live)
            # instead of a noise-driven raw-BC read.
            world.pre_tackle_slowdown_anchor = getattr(
                self.tuners, "anticipation_slowdown_anchor", "bc_predicted"
            )
        return select_lane_tacklers(world, defenders, bc)

    # -----------------------------------------------------------
    # BIG BLOCK DEFENCE - TACKLE SELECTION (RULES 2-4)
    # -----------------------------------------------------------

    def select_tacklers(self, world, defenders):

        """Assign natural tackle roles by distance/angle/speed.

        Sets ``d.tackle_role`` on each defender.

        Priority order (first match wins):
          1. PRIMARY   - closest, within 3m, angle < 45, speed > 1 m/s
          2. SECONDARY - within 5m, angle < 60, speed > 1 m/s
          3. THIRD     - within 7m, angle < 75
          4. EDGE      - outermost defenders (largest |lane_index|)
          5. FULLBACK  - deepest defender (max distance behind BC)
          6. PASSIVE   - everyone else
        """
        t = self.tuners
        bc = world.ball_carrier
        if bc is None:
            for d in defenders:
                d.tackle_role = "PASSIVE"
            return

        # ── BANDED TACKLER SELECTION (2026-08-12) ──────────────────────
        # The depth-role bands (A/A/B/B/C/C, assigned by Y-distance to the
        # ball carrier in defensive_depth_roles) define WHO engages at first
        # contact.  PRIMARY is chosen from the A band first; SECONDARY from
        # the A/B bands.  When the band pools are empty — or no band
        # defender satisfies the distance/angle/speed windows — the selector
        # falls back to the distance-ranked pool so tackle initiation is
        # never starved.  This replaces the old pure-distance scan where the
        # two closest-in-space defenders could be C/D-band players running
        # to spacing positions ("tacklers go to the wrong spot").
        marker_roles = (DefenderRole.MARKER_A, DefenderRole.MARKER_B)

        def _is_marker_role(d):
            return getattr(d, "defender_role", None) in marker_roles

        def _is_fullback_role(d):
            # FULLBACK GUARD (2026-09-15): the TACKLE-ROLE PERSISTENCE
            # RESTORE loop below skips marker AND fullback actors so the
            # geometry-driven fullback can never be re-stamped with a stale
            # PRIMARY/SECONDARY tackle role (the string collision between the
            # legacy "FULLBACK" tackle role and the separate, geometry-driven
            # ``fullback_role_active`` actor -- see the FULLBACK REMOVED note
            # above).  This predicate was REFERENCED at the persistence-restore
            # loop but never DEFINED, so ``select_tacklers()`` raised
            # NameError on every call and aborted the entire OPEN_PLAY
            # defensive dispatch (no spacing/width/commit/stance writers ran,
            # and committed defenders never closed on the carrier).
            return bool(getattr(d, "fullback_role_active", False))

        def _clear_fullback_role_if_selected(d):
            # ── EMERGENCY TAG HOLD (B2b, 2026-09-16) ──
            # A tagged fullback that is engaged in an emergency tackle is a
            # TES participant: the tag is the ONLY thing keeping it out of
            # the line.  This legacy dissolve site must never strip it while
            # the engagement is live -- the engine clears the marker (and
            # restores DEF_LINE_READY) at the end of the tackle cycle.
            # -- TAG PRESERVATION (B2b, 2026-09-16) --
            # TACKLE ELIGIBILITY and LINE MEMBERSHIP are separate concepts.
            # This site used to DISSOLVE the tag (fullback_role_active =
            # False) the moment the tackle selector drafted the actor, which
            # is precisely the "absorbed into the defensive line" failure the
            # emergency path forbids.  The tagged body is only admitted to a
            # tackle role ON A GENUINE BREAK, and it must keep the tag while
            # playing that role exactly like any other defender.
            #
            # The tag has exactly ONE legitimate clearer: genuine ABSORPTION
            # in the geometry pipeline (update_fullback_absorb_state ->
            # fullback_absorb_allowed, after ABSORB_FRAMES_MIN consecutive
            # frames inside both proximity thresholds), which is asserted
            # separately by verify_fullback_absorb and
            # verify_post_catch_fb_absorb_grace.  Tackle-role selection is
            # NOT an absorption event, so this function must not touch the
            # tag at all.  Kept as a named no-op so the call sites stay
            # readable and any future dissolve attempt has to go through a
            # documented site.
            # (The dissolve body that used to live here is deliberately gone:
            # it set fullback_role_active = False, cleared the FULLBACK debug
            # identity and wiped depth_role / depth_side.  Reintroducing any
            # of it would re-break the membership half of the law.)
            return

        def _band_rank(d):
            return {"A": 0, "B": 1, "C": 2, "D": 3}.get(
                getattr(d, "depth_role", None), 4
            )

        def _in_primary_window(d):
            if _is_marker_role(d):
                return False
            dist = d.distance_to(bc)
            angle = abs(math.degrees(math.atan2(bc.y - d.y, bc.x - d.x)))
            speed = math.hypot(getattr(d, "vx", 0.0), getattr(d, "vy", 0.0))
            return (
                dist < t.tackle_primary_distance_m
                and angle < t.tackle_primary_angle_deg
                and speed > t.tackle_primary_min_speed_mps
            )

        def _in_secondary_window(d):
            if _is_marker_role(d):
                return False
            dist = d.distance_to(bc)
            angle = abs(math.degrees(math.atan2(bc.y - d.y, bc.x - d.x)))
            speed = math.hypot(getattr(d, "vx", 0.0), getattr(d, "vy", 0.0))
            return (
                dist < t.tackle_secondary_distance_m
                and angle < t.tackle_secondary_angle_deg
                and speed > t.tackle_secondary_min_speed_mps
            )

        def _fb_lane_admissible(d) -> bool:
            """Keep standalone fullbacks out of every selector-owned role."""
            return not getattr(d, "fullback_role_active", False)

        # ── RETREAT-OBLIGATION VETO (2026-09-17) ──
        # The legacy banded selector wipes EVERY role to PASSIVE and then
        # re-grants PRIMARY/SECONDARY from this pool.  A still-retreating
        # body must not be in the pool, or the wipe+regrant would hand back
        # the authority the strip revoked earlier in the same frame.
        selectable = [
            d for d in defenders
            if _fb_lane_admissible(d) and not is_retreat_obligated(d, world)
        ]

        # ── TACKLE-ROLE PERSISTENCE (§4, 2026-08-13) ──
        # Once PRIMARY/SECONDARY is assigned it must persist during the
        # approach — the tackle completes or the BC breaks free, never a
        # mid-approach flip to PASSIVE/EDGE on a fleeting distance/angle
        # window miss (the "tackler stops committing" root cause).
        # Snapshot the previous committed roles BEFORE the PASSIVE wipe
        # below; they are restored after the ranked passes when the
        # defender is still a live threat (within STRIKE_DISTANCE_M of
        # the BC, or already armed by TES as a registered tackler).
        STRIKE_DISTANCE_M: float = 10.0
        prev_roles = {
            d.player_id: getattr(d, "tackle_role", "PASSIVE")
            for d in defenders
            if getattr(d, "tackle_role", "PASSIVE") in ("PRIMARY", "SECONDARY")
        }

        # Re-initialise all tackle roles to PASSIVE for the banded pass so
        # a stale PRIMARY/SECONDARY from the previous frame cannot leak.
        for d in defenders:
            d.tackle_role = "PASSIVE"

        # ── RULE 2 — PRIMARY from the A band (closest A first) ──
        primary = None
        primary_pool = [d for d in selectable if _in_primary_window(d)]
        a_pool = [d for d in primary_pool if _band_rank(d) == 0]
        if a_pool:
            primary = min(a_pool, key=lambda d: d.distance_to(bc))
        elif primary_pool:
            primary = min(primary_pool, key=lambda d: d.distance_to(bc))
        if primary is not None:
            primary.tackle_role = "PRIMARY"
            _clear_fullback_role_if_selected(primary)

        # ── RULE 3 — SECONDARY from the A/B bands (band-first) ──
        # Every defender in the A/B bands within the secondary window is
        # SECONDARY (up to a practical cap, mirroring the 1-2-4 ranked
        # distribution).  Non-band defenders may still fill SECONDARY when
        # the A/B band pool is empty, so the tackle is never starved.
        secondary_pool = [d for d in selectable if _in_secondary_window(d)]
        ab_pool = [d for d in secondary_pool if _band_rank(d) in (0, 1)]
        ab_pool.sort(key=lambda d: (_band_rank(d), d.distance_to(bc)))
        for d in ab_pool[:2]:
            if d.tackle_role == "PASSIVE":
                d.tackle_role = "SECONDARY"
                _clear_fullback_role_if_selected(d)
        if len(ab_pool) < 2:
            fallback = sorted(
                (d for d in secondary_pool if d.tackle_role == "PASSIVE"),
                key=lambda d: (_band_rank(d), d.distance_to(bc)),
            )
            for d in fallback[: 2 - len(ab_pool)]:
                d.tackle_role = "SECONDARY"

        # ── RULE 4 — THIRD tackler (distance/angle fallback) ──
        for d in selectable:
            if d.tackle_role != "PASSIVE":
                continue
            if _is_marker_role(d):
                continue
            dist = d.distance_to(bc)
            angle = abs(math.degrees(
                math.atan2(bc.y - d.y, bc.x - d.x)
            ))
            if dist < t.tackle_third_distance_m and angle < t.tackle_third_angle_deg:
                d.tackle_role = "THIRD"

        # Second pass: assign EDGE from PASSIVE leftovers.
        # FULLBACK REMOVED (2026-08-08): the tackle-role pipeline no longer
        # stamps tackle_role="FULLBACK" on the deepest leftover.  The dynamic
        # fullback is a separate, geometry-driven role (fullback_role_active
        # via fullback_role.assign_fullback_role) and the string collision
        # between the two "fullback" concepts caused selection confusion and
        # pursuit-mode X-spread contamination (PURSUIT_X_OFFSET_BY_ROLE
        # keyed on "FULLBACK").  The deepest leftover stays PASSIVE.
        passive_lane_indices = []
        for d in selectable:
            if d.tackle_role == "PASSIVE":
                lane_idx = abs(getattr(d, "lane_index", 0))
                passive_lane_indices.append((lane_idx, d))

        if passive_lane_indices:
            # Sort by |lane_index| descending - outermost first
            passive_lane_indices.sort(key=lambda x: -x[0])

            # The two with largest |lane_index| become EDGE
            for i in range(min(2, len(passive_lane_indices))):
                _, d = passive_lane_indices[i]
                d.tackle_role = "EDGE"

        # ── TACKLE-ROLE PERSISTENCE RESTORE (§4, 2026-08-13) ──
        # A defender that held PRIMARY/SECONDARY last frame and is STILL a
        # live threat (within STRIKE_DISTANCE_M of the BC, or already armed
        # by TES as a registered tackler) is restored to its locked role
        # even if the ranked passes dropped it to PASSIVE/EDGE/THIRD this
        # frame.  This guarantees a committed tackler never flips
        # mid-approach — the tackle completes or the BC breaks free beyond
        # the strike radius.  Restored roles feed the tackle-commit writer
        # and the pre-tackle trigger later in the same frame.
        for d in defenders:
            prev_role = prev_roles.get(getattr(d, "player_id", None))
            if prev_role not in ("PRIMARY", "SECONDARY"):
                continue
            if getattr(d, "tackle_role", "PASSIVE") in ("PRIMARY", "SECONDARY"):
                continue
            if _is_marker_role(d):
                continue
            if _is_fullback_role(d):
                continue
            tes_armed = (
                getattr(d, "is_tackler", False)
                or getattr(d, "in_contact", False)
                or getattr(d, "tes_tackler_role", None) is not None
            )
            dist = math.hypot(d.x - bc.x, d.y - bc.y)
            if dist <= STRIKE_DISTANCE_M or tes_armed:
                d.tackle_role = prev_role

    # -----------------------------------------------------------
    # BIG BLOCK RULE 0 - UNIVERSAL PURSUIT MODE
    # -----------------------------------------------------------

    def apply_pursuit_mode(self, world, defenders):
        """Pure pursuit: X lane spread, Y role-anchored width, full speed.

        Y WIDTH (Task B, 2026-08-09): even in pursuit the lateral target
        comes from the defender's PERSISTENT role width anchor — NEVER
        from lane-index / get_defence_target.  PRIMARY/SECONDARY tacklers
        pin tightly to bc.y (tackle pursuit: they must converge on the
        carrier); every other defender holds the role-slot width anchor
        centred on ruck_y so the pursuit band never collapses to an
        arbitrary lane spread and never "sticks" to the 7-slot fallback.
        """
        bc = world.ball_carrier
        if bc is None:
            for d in defenders:
                d.vx = 0.0
                d.vy = 0.0
                d.target_x = d.x
                d.target_y = d.y
                d.target_source = "pursuit_no_bc"
            return

        # Depth roles first - they own pursuit Y width (idempotent).
        # TASK 2 (2026-08-14) — SINGLE-ASSIGN-PER-FRAME: the un-gated
        # preamble already assigned + published spacing from these roles
        # this frame; re-assigning mid-frame would return a stale
        # frame_spacing_targets (dual-source spacing divergence).  Guarded:
        # roles are assigned exactly once per frame by the preamble.
        if not _depth_roles_assigned_this_frame(world):
            assign_depth_roles(defenders, world)

        # Lane indices needed FIRST - select_tacklers uses them for EDGE/FULLBACK
        self._compute_lane_indices(world, defenders)
        # Depth classification (stateless, BC-centric, Euclidean distance)
        self.classify_depth_all(world, defenders)
        # ── INTERCEPT LANE OWNERSHIP (2026-08-15, spec §1) ──
        # The per-side lane system overrides the legacy banded selector:
        # PRIMARY per side owns convergence; every other candidate is
        # LINE (never the tackle-commit writer here — pursuit Y spreads
        # those by spacing).  The legacy selector still runs first so its
        # persistence/THIRD/EDGE labels remain available for debug, then
        # the lane system re-stamps the authority roles.
        self.select_tacklers(world, defenders)
        self.select_lane_tacklers(world, defenders, bc)
        for d in defenders:
            tr = getattr(d, "tackle_role", "PASSIVE")
            if tr not in ("PRIMARY", "SECONDARY"):
                d.tackle_role = "PASSIVE"
        # ── TACKLER-COMMIT OVERRIDE (2026-08-13) ──
        # PRIMARY/SECONDARY tacklers receive their dedicated BC-forward
        # collision target AFTER pursuit's lane-spread writer so the
        # tackle-commit target wins the frame (pursuit targets driven by
        # lane_index * role offset would otherwise pull the tacklers off
        # the carrier's lane).
        _apply_tackler_commit(world, defenders, self.tuners)


        pursuit_x_offsets = self.tuners.PURSUIT_X_OFFSET_BY_ROLE
        pursuit_y_off = self.tuners.PURSUIT_Y_OFFSET

        ruck_y = getattr(world, "ruck_y_m", bc.y)

        for d in defenders:
            # FULLBACK HARD-EXCLUDE (2026-08-27): the dynamic fullback is a
            # backfield anchor owned by apply_fullback_geometry, never a
            # pursuit lane actor.  Skip it before any pursuit X/Y write.
            if getattr(d, "fullback_role_active", False):
                continue
            lane_idx = getattr(d, "lane_index", 0)
            tackle_role = getattr(d, "tackle_role", "PASSIVE")

            # X spreads by lane * role factor
            x_offset = pursuit_x_offsets.get(tackle_role, 1.0)
            tx = bc.x + lane_idx * x_offset

            # Y WIDTH — split-spacing (Task F, 2026-08-10).
            # PRIMARY/SECONDARY tacklers converge onto the carrier's lane
            # (tackle pursuit); every other defender holds the split-design
            # spacing Y (per-side role anchors, governed width).
            if tackle_role in ("PRIMARY", "SECONDARY"):
                ty = bc.y + pursuit_y_off
            else:
                # 2026-08-12: consumed from the un-gated frame preamble.
                ty = _frame_spacing_y(world, d, self.tuners,
                                      governor=self.line_governor)

            d.target_x = tx
            d.target_y = ty
            d.target_source = "pursuit"
            # GLOBAL DEFENSIVE TOP-SPEED CEILING: pursuit speed is exactly
            # the unified ceiling (per-defender physical max may be higher).
            d.move_speed = self._def_speed(d)
            d.frozen = False
            # depth_class already set by classify_depth_all() above.
            # Do NOT overwrite with tackle_role - keep BC-centric ranking.
            # d.depth_class = tackle_role  -- REMOVED

        # ── PRE-TACKLE STANCE (2026-08-15, spec §3) ──
        # PRIMARY lane tacklers inside the close-range stance radius plant
        # FIRST (stop, square up, face BC) so the pursuit trigger below
        # evaluates a planted, committed tackler.
        lane_defenders = [
            d for d in defenders
            if not getattr(d, "fullback_role_active", False)
        ]
        apply_pre_tackle_stance(world, lane_defenders, bc)

        # -- PRE-TACKLE TRIGGER (multi-tackler) --
        # Find ALL eligible PRIMARY/SECONDARY/THIRD defenders within tackle range.
        # The closest starts TES. Additional eligible defenders are registered as
        # immediate joiners so they don't drift on stale targets.

        # ── PRIMARY SELECTION (2026-09-09): shared classifier predicate ──
        # The engage-role predicate (PRIMARY/SECONDARY only — THIRD is
        # joiner-only) and the slot-eligible preference are SINGLE-SOURCED
        # in pretackle_classifier, so the caller's candidate set and the
        # gate's role filter can never drift (Finding 7).  A slot-OUT nearest
        # body can no longer starve a valid slot-IN tackle (Finding 1).
        fullback_candidates = _fullback_tackle_candidates(
            defenders, lane_defenders, world, bc,
        )
        engage_winner = _try_primary_then_fullback(
            world, lane_defenders, fullback_candidates, bc, self.tuners,
        )
        secondary_joiners = []
        for d in lane_defenders:
            tackle_role = getattr(d, "tackle_role", None)
            # Any SECONDARY/THIRD defender within 2.5m of BC qualifies as joiner
            if tackle_role not in ("SECONDARY", "THIRD"):
                continue
            dist = math.hypot(d.x - bc.x, d.y - bc.y)
            if dist <= 2.5:
                secondary_joiners.append((dist, d))

        if engage_winner is not None and bc is not None:
            if not getattr(world, "tes_active", False):
                ope = world.open_play_engine
                # STAGE 2A: hand the examined population to the preservation
                # layer with the admission, so the record holds WHO was in
                # radius and WHAT each body's declared stamps were at this
                # moment -- not just the winner.  A pure data argument: the
                # admission decision above and the handover below are
                # unchanged.
                _enter_tes(world, ope, bc, engage_winner, _CH_PURSUIT,
                           defenders=defenders)
                engage_winner.target_x = engage_winner.x
                engage_winner.target_y = engage_winner.y
                engage_winner.move_speed = 0.0
                engage_winner.frozen = True

                # -- Register SECONDARY/THIRD defenders as immediate joiners --
                if ope.tes_ctx is not None:
                    from game.tes.tes_context import PlayerContext
                    max_add = ope.tes_ctx.max_joiners - len(ope.tes_ctx.engaged_tacklers)
                    added = 0
                    for _, sd in sorted(secondary_joiners, key=lambda x: x[0]):
                        if sd is engage_winner:
                            continue
                        if added >= max_add:
                            break
                        # ARM THE JOINER ATOMICALLY (contract A/B, 2026-08-08):
                        # ope._arm_tackler_role() sets movement_mode = TACKLER,
                        # collision_priority = HIGH, and registers the joiner in
                        # ctx.tackler_roles — so _update_tackler_movement() drives
                        # them into contact and _clear_tes_artefacts() exits them
                        # to RETREAT at RELEASE.  Stamping is_tackler/in_contact
                        # alone left movement_mode = None (root cause #7): the
                        # joiner never retreated, stayed wrong-side of the ruck,
                        # blocked the PTB bubble, and stalled the PTB timer.
                        if len(ope.tes_ctx.engaged_tacklers) == 1:
                            ope._arm_tackler_role(sd, DefenderRole.TACKLER_2, "TACKLER_2", ope.tes_ctx)
                        elif len(ope.tes_ctx.engaged_tacklers) == 2:
                            ope._arm_tackler_role(sd, DefenderRole.TACKLER_3, "TACKLER_3", ope.tes_ctx)
                        # CONTACT-CONTRACT HANDOFF (2026-09-15): the joiner
                        # inherits the PRE-TACKLE CORRIDOR's governed
                        # approach velocity rather than whatever another
                        # writer left on vx/vy before TACKLE_INIT.  Returns
                        # the live vx/vy when the body carries no corridor
                        # stamp or the handoff gate is off, so a
                        # non-corridor joiner is byte-identical.
                        _jvx, _jvy = _corridor_contact_velocity(sd, self.tuners)
                        sd_ctx = PlayerContext()
                        sd_ctx.player_id = sd.player_id
                        sd_ctx.x_m = sd.x
                        sd_ctx.y_m = sd.y
                        sd_ctx.vx = _jvx
                        sd_ctx.vy = _jvy
                        sd_ctx.mass = getattr(sd, "mass_kg", 90.0)
                        sd_ctx.stamina = getattr(sd, "stamina", 1.0)
                        sd_ctx.aggression = getattr(sd, "aggression", 0.5)
                        sd_ctx.tackle_skill = getattr(sd, "tackle_skill", 0.5)
                        sd_ctx.strength = getattr(sd, "strength", 0.5)
                        sd_ctx.facing = sd.facing
                        ope.tes_ctx.engagement.add(sd_ctx, world.sim_time_s)
                        ope.joiners.append(sd)
                        sd.target_x = sd.x
                        sd.target_y = sd.y
                        sd.move_speed = 0.0
                        sd.frozen = True
                        sd.tes_locked = True
                        sd.is_tackler = True
                        sd.in_contact = True
                        added += 1

    # -----------------------------------------------------------
    # HARD OVERRIDE — immediate defence reaction on line break
    # -----------------------------------------------------------

    def _force_immediate_defence_reaction(self, world, defenders):
        """Force all defenders into chase when BC is at or past the anchor.

        Called at the start of both OPEN_PLAY dispatch methods to eliminate
        the 3-second delay where PASSIVE defenders remain static on the
        retreat band while the BC runs free.  This is a hard override:
          - depth_class → SECONDARY (unlocks BC-relative targets)
          - movement_state → CHASE
          - target_x → dynamic BC-relative (not static anchor)
          - clears ALL timing locks / retreat delays

        Marker roles (MARKER_A, MARKER_B, DUMMY_HALF) are preserved
        as they play tight to the ruck and are not part of the line.
        """
        bc = getattr(world, "ball_carrier", None)
        anchor_x = getattr(world, "defensive_line_anchor", None)
        if bc is None or anchor_x is None:
            return

        # If BC is at or past the anchor, there is zero justification for
        # any defender to be PASSIVE, timing-locked, or targeting the band.
        if not (bc.x >= anchor_x):
            return

        for d in defenders:
            role = getattr(d, "defender_role", None)
            # Skip markers and DH — they play tight to the ruck
            if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B, DefenderRole.DUMMY_HALF):
                continue
            # ── RETREAT-OBLIGATION VETO (2026-09-17) ──
            # The line-break force-chase override is the single most
            # aggressive grant in the defence: it promotes EVERY non-marker
            # body to SECONDARY at full line speed with a BC-relative
            # target.  A body that still owes its 10 m retreat keeps
            # retreating instead — it is behind the anchor, so it is not
            # the body that can make this tackle, and promoting it is
            # exactly the "regain is fine or fail" case the
            # "regain tackle authority" clause forbids.
            if is_retreat_obligated(d, world):
                continue

            # Hard override any PASSIVE / retreat / timing lock
            d.depth_class = "SECONDARY"
            d.movement_state = MovementState.CHASE
            # LINE-SPEED UNIFICATION (2026-08-04): the line-break force
            # reaction derives from the governor's shared line speed (or
            # 0.95x of the ceiling as the prior-frame fallback) so it can
            # never burst above the rest of the line.
            gov_speed = getattr(self.line_governor, "line_speed", None)
            fallback = self._def_speed(d) * 0.95
            d.move_speed = min(fallback, gov_speed) if gov_speed is not None else fallback

            # Dynamic BC-relative target instead of static ruck_x + 10m band.
            #
            # PRE-TACKLE JIGGLE FIX (2026-08-02): this target MUST match
            # structured_close's SECONDARY expression exactly
            #     max(onside_x, ictx - 2.0)
            # where ictx is the time-to-intercept lead.  When structured_close
            # adopted the intercept lead (2026-08-06), the force reaction was
            # updated identically so the frame the BC crosses the anchor the
            # line target does not change — nothing to snap.
            # BC-RELATIVE CHASE RESET (2026-09-10): on a line break the
            # defender's target must be the LIVE BC, not an intercept lead
            # that can saturate into a dead static target.  Setting
            # target_x = bc.x forces immediate re-entry into pursuit.
            d.target_x = float(bc.x)

            # SPACING Y (Task F, 2026-08-10): the force reaction holds the
            # split-design spacing target so a line break never snaps the
            # whole line onto the BC's lane (inward collapse) — X only is
            # BC-relative, Y stays spacing_targets[player].y.
            # 2026-08-12: consumed from the un-gated frame preamble.
            d.target_y = _frame_spacing_y(world, d, self.tuners,
                                          governor=self.line_governor)
            stamp_target_write(world, d, "spacing")

            # Kill any residual timing locks / retreat delays
            if hasattr(d, "retreat_delay"):
                d.retreat_delay = 0.0
            if hasattr(d, "timing_locked"):
                d.timing_locked = False
            if hasattr(d, "reaction_timer"):
                d.reaction_timer = 0.0
            if hasattr(d, "lateral_hesitation"):
                d.lateral_hesitation = 0.0
            if hasattr(d, "step_stagger"):
                d.step_stagger = 0.0

            d.target_source = "line_break_force"

    # ── STAGE 3H: CLASSIFICATION GATE HYSTERESIS (2026-08-30) ──
    # The OPEN_PLAY structured pipeline writes target_x through up to
    # seven sequential writers (steps 7/7a/9/10/10a1/10b).  Which writer
    # wins is decided by transient gates - is_primary_lane_tackler and the
    # carrier-past-anchor / engaged-non-lane predicate - which flip
    # frame-to-frame as the carrier crosses a threshold, producing
    # TARGET_JIGGLE.  These helpers add a per-defender cooldown/hold so a
    # gate, once active, stays active for N frames (debounce) without
    # blurring geometry.  This is the conductor-model "one stable writer
    # per phase" enforcement (Stage 3H).
    _LANE_TACKLER_HOLD_FRAMES = 10
    _ENGAGED_HOLD_FRAMES = 10

    def _stable_is_primary_lane_tackler(self, d) -> bool:
        """Hysteresis-stabilised is_primary_lane_tackler().

        Holds the PRIMARY/SECONDARY classification True for
        ``_LANE_TACKLER_HOLD_FRAMES`` frames after the raw predicate last
        held, so step-10 / step-10a1 stays the stable winning writer
        instead of flipping back to step-7 width X frame-to-frame.
        """
        raw = is_primary_lane_tackler(d)
        hold = getattr(d, "_lh_hold", 0)
        if raw:
            d._lh_hold = self._LANE_TACKLER_HOLD_FRAMES
            return True
        if hold > 0:
            d._lh_hold = hold - 1
            return True
        return False

    def _stable_engaged_non_lane(self, d, world, bc) -> bool:
        """Hysteresis-stabilised carrier-past-anchor + engaged-non-lane gate.

        Holds the engaged-non-lane chase state True for
        ``_ENGAGED_HOLD_FRAMES`` frames after the raw predicate last held
        (carrier clearly past the anchor + PRIMARY/SECONDARY depth class +
        not a lane tackler), so step-7a BC-relative X stays the stable
        winning writer instead of flipping back to step-7 width X.
        """
        raw = (
            self._is_carrier_past_anchor(world, bc)
            and not getattr(world, "tes_active", False)
            and getattr(d, "depth_class", "PASSIVE") in ("PRIMARY", "SECONDARY")
            and not is_primary_lane_tackler(d)
        )
        hold = getattr(d, "_eh_hold", 0)
        if raw:
            d._eh_hold = self._ENGAGED_HOLD_FRAMES
            return True
        if hold > 0:
            d._eh_hold = hold - 1
            return True
        return False

    # -----------------------------------------------------------
    # 12M PURSUIT/COMMIT HARD OVERRIDE (2026-09-02)
    # -----------------------------------------------------------
    def _apply_pursuit_commit_override(self, world, defenders):
        """12m pursuit/commit hard override.

        Any non-marker, non-fullback, non-DH, non-tackle-state, non-TES line
        defender within ``tuners.pursuit_commit_radius_m`` (default 12m) of the
        ball carrier is forced onto a BC-relative pursuit/commit target
        (direction-aware X offset, tight-lane Y), CHASE/SHOOT state, full
        ceiling speed, and ALL timing/retreat/freeze locks cleared — overriding
        spacing/width/role/retreat/fallback logic.  Persists until CONTACT
        fires (TES starts / tackle phase), so it is gated on ``not tes_active``,
        not tackle phase, and not OPE ``_suppress_pre_tackle``.

        Already-committed PRIMARY/SECONDARY tacklers (``_tackle_commit_active``)
        are skipped — they already pursue the BC via the dedicated commit writer
        (and the convergence dict owns their exact collision point).
        """
        bc = getattr(world, "ball_carrier", None)
        if bc is None:
            return
        # CONTACT gate: once TES/tackle fires, TES owns movement.
        if getattr(world, "tes_active", False):
            return
        ope = getattr(world, "open_play_engine", None)
        if ope is not None and getattr(ope, "_suppress_pre_tackle", False):
            return
        if is_tackle_phase(world):
            return

        radius = float(getattr(self.tuners, "pursuit_commit_radius_m", 12.0))
        # Direction-aware offsets, mirroring the tackler-commit writer.
        primary_offset = TACKLER_COMMIT_PRIMARY_X_OFFSET_M   # 1.0
        band_offset = TACKLER_COMMIT_BAND_Y_OFFSET_M          # 0.5

        # ── TACKLER-CANDIDATE ASSIGNMENT (2026-09-02) ──
        # Any eligible defender inside the 1.0m commit rail (should_commit)
        # forced to SHOOT is a genuine tackler candidate.  Mark the closest
        # PRIMARY and the rest (within pre_tackle_secondary_radius_m)
        # SECONDARY, and set ``_tackle_commit_active`` so the pre-tackle
        # trigger / try_pre_tackle see them (TACKLE-GATE/SDEA num_tacklers >
        # 0) and real CONTACT/DRIVE can fire.  These get the committed-lane-
        # tackler onside exemption and the MMDL commit-skip path, exactly
        # like the dedicated _apply_tackler_commit PRIMARY/SECONDARY tacklers.
        commit_candidates = []
        for d in defenders:
            if not isinstance(d, Player):
                continue
            if getattr(d, "_tackle_commit_active", False):
                continue
            # CORRIDOR YIELD (2026-09-15): the pre-tackle corridor owns this
            # body -- never re-stamp it as a NEW pursuit/commit candidate.
            # A second COMMIT owner inside the plant radius is exactly the
            # "another authority determines the final approach speed"
            # failure this consolidation removes.
            if _is_corridor_owned(d):
                continue
            if d.frozen or getattr(d, "freeze_timer", 0.0) > 0.0:
                continue
            if _is_marker(d):
                continue
            if getattr(d, "fullback_role_active", False):
                continue
            if getattr(d, "defender_role", None) == DefenderRole.DUMMY_HALF:
                continue
            if getattr(d, "tackle_state_active", False):
                continue
            _cdist = math.hypot(d.x - bc.x, d.y - bc.y)
            if should_commit(_cdist):
                commit_candidates.append((_cdist, d))

        commit_candidates.sort(key=lambda x: x[0])
        sec_radius = float(getattr(
            self.tuners, "pre_tackle_secondary_radius_m", 2.5
        ))
        commit_ids = set()
        for i, (_cdist, cd) in enumerate(commit_candidates):
            # ── RETREAT-OBLIGATION VETO (2026-09-17) ──
            # The commit-rail candidates are granted PRIMARY/SECONDARY +
            # the commit latch inside the collapse radius.  That is a
            # tackle-authority grant, so an obligated body is skipped; it
            # is also left out of ``commit_ids`` so the second loop below
            # does not treat it as already-owned.
            if is_retreat_obligated(cd, world):
                continue
            cd.tackle_role = "PRIMARY" if i == 0 else "SECONDARY"
            cd._tackle_commit_active = True
            commit_ids.add(id(cd))

        for d in defenders:
            if not isinstance(d, Player):
                continue
            # Skip already-committed PRIMARY/SECONDARY lane tacklers (they own
            # the engage via the dedicated commit writer) -- EXCEPT the
            # commit-rail candidates just marked, whose SHOOT target/state the
            # override below still writes.
            if getattr(d, "_tackle_commit_active", False) and id(d) not in commit_ids:
                continue
            # CORRIDOR YIELD (2026-09-15): the corridor is the sole motion
            # authority for this body -- do not overwrite its target,
            # move_speed or movement_state with a full-ceiling pursuit
            # point.  See pretackle_corridor.
            if _is_corridor_owned(d):
                continue
            if d.frozen or getattr(d, "freeze_timer", 0.0) > 0.0:
                continue
            if _is_marker(d):
                continue
            if getattr(d, "fullback_role_active", False):
                continue
            if getattr(d, "defender_role", None) == DefenderRole.DUMMY_HALF:
                continue
            if getattr(d, "tackle_state_active", False):
                continue
            dist = math.hypot(d.x - bc.x, d.y - bc.y)
            if not is_in_pursuit_commit_range(d, bc, radius):
                # Clear any stale flag so a defender that exited the radius
                # no longer holds the override.
                d._pursuit_commit_active = False
                continue

            # ── RETREAT-OBLIGATION VETO (2026-09-17) ──
            # The pursuit/commit override is the sixth grant: it raises the
            # body to SECONDARY, forces the pursuit target, sets the
            # movement state and the commit latch.  A body that still owes
            # its 10 m retreat is excluded here so the override can never
            # re-grant on the same frame the strip revoked.
            if is_retreat_obligated(d, world):
                continue
            # Promote to engageable so no PASSIVE X-drift / DRIFT demotion.
            d.depth_class = "SECONDARY"
            # Direction-aware BC-relative X (attack runs +X): ahead defenders
            # converge toward the carrier from the front (bc.x - offset),
            # behind defenders deflect in front (bc.x + offset).
            ahead = d.x > bc.x
            tx = bc.x - primary_offset if ahead else bc.x + primary_offset
            # Tight-lane Y with a small signed band offset (assist posture).
            side = getattr(d, "depth_side", None)
            ty = bc.y + (band_offset * side if side in (-1, 1) else 0.0)

            d.tackle_target_x = tx
            d.tackle_target_y = ty
            d.target_x = tx
            d.target_y = ty
            d.target_source = "pursuit_commit_override"
            d.movement_state = (
                MovementState.SHOOT if should_commit(dist) else MovementState.CHASE
            )
            d.move_speed = self._def_speed(d)
            d.frozen = False
            d.timing_locked = False
            d.reaction_timer = 0.0
            d.retreat_delay = 0.0
            d.lateral_hesitation = 0.0
            d.step_stagger = 0.0
            d.step_stagger_active = False
            d._pursuit_commit_active = True

    # -----------------------------------------------------------
    # STRUCTURED OPEN PLAY - full geometry, governor, spacing, decision
    # -----------------------------------------------------------

    def _apply_structured_open_play(self, world, defenders):
        """Apply OPEN_PLAY with full structured defence pipeline.

        Unlike _apply_open_play_unified(), this method:
          - Uses DefensiveGeometry for role-driven Y targets
          - Uses the governor for line depth, width, speed, compression
          - Does NOT strip LINE roles
          - Does NOT hard-freeze PASSIVE timing_locked defenders
          - Does NOT apply retreat compliance override
          - Decision layer affects speed/movement_state, NOT target_x/y

        Pipeline (2026-08-09, role-driven):
          1. Depth classification (ranked selection) — lane_index removed
          2. Depth roles assigned (A/B/C/D/CENTRE/WINGER, ruck-driven)
          3. DefensiveGeometry.compute_role_targets (role X + Y, OPEN_PLAY
             gate via geometry_active)
          4. DefensiveTiming.update (no PASSIVE freeze)
          5. DefensiveLineGovernor.compute_line_state (shared line SPEED +
             governed WIDTH)
          6. DefensiveRoleSpacing.prepare + SpacingGuard (Task H, Y-only)
          7. Width recompute + WidthGuard (Task I, X-only) then apply
             role geometry targets
          8. Decision layer (speed/movement_state only)
          9. Mandatory retreat for PASSIVE non-compliant defenders
          10. Structured close for PRIMARY + SECONDARY (role-anchored width)
          11. lane_index computed (tackle-EDGE only) + select_tacklers
          12. Pre-tackle trigger
        """

        # -- BALL CARRIER NONE GUARD --
        bc = world.ball_carrier
        if bc is None:
            for d in defenders:
                d.vx = 0.0
                d.vy = 0.0
                d.target_x = d.x
                d.target_y = d.y
                d.target_source = "structured_no_bc"
            return

        # ── TASK G ANCHOR AUTHORITY (defense-in-depth for direct callers) ──
        # apply() already locked the anchor; this is idempotent for the
        # same ruck_x + 10 value so direct/verifier invocation is covered.
        compute_and_lock_anchor(world)

        # -----------------------------------------------------------
        # FULLBACK ROLE — STRUCTURED OPEN_PLAY ASSIGNMENT (2026-08-08)
        # -----------------------------------------------------------
        # Assign the dynamic fullback FIRST so its anchor target is locked
        # in before any line writer runs.  The actor is then REMOVED from
        # the line-defender list passed to every shaping pipeline below
        # (lane indices, depth classification, depth roles, geometry,
        # governor, retreat, close, MMDL, tackle selection), so:
        #   - the line shape gets the correct N-1 defender numbers,
        #   - no line writer overwrites the fullback's anchor,
        #   - the fullback is never classified PRIMARY/SECONDARY/tackler.
        #
        # MMDL is bypassed by construction: the actor is not in sorted_defs
        # when step 10b runs, so _mmdl_moved stays False and OPE's
        # Player.update(dt) accelerates toward the (stabilised) anchor.
        # We force MovementState.DRIFT so should_stabilise_target() is ON
        # (DRIFT is not in NON_STABILISED_MOVEMENT_STATES) and cap speed at
        # 0.4x the unified ceiling (the DRIFT max_speed_scale).
        # TASK VI: the fullback anchor consumes the ANCHOR AUTHORITY
        # (ruck_x + 10) — never a bare field read or ruck_x + 10 fallback.
        anchor_x = _anchor_m(world)
        fb_actor, (fb_tx, fb_ty) = assign_fullback_role(defenders, bc, anchor_x, world=world)

        # ── FULLBACK ABSORB HYSTERESIS (Task J, 2026-08-12) ──
        # BOTH X (vs defensive-line anchor) and Y (vs ball-carrier lane)
        # proximity must hold for ABSORB_FRAMES_MIN consecutive frames
        # before the fullback may dissolve into a deterministic line role
        # (spec: "requires both X and Y proximity for ABSORB_FRAMES_MIN
        # consecutive frames").  While the actor is deep (counters below
        # the minimum) it stays the independent last defender.  Once
        # fullback_absorb_allowed turns True the actor is ABSORBED: the
        # role flag is cleared so this frame's ``line_defenders`` (built
        # BELOW from fullback_role_active) includes the actor, and the
        # depth-role pipeline assigns it a deterministic line role
        # (A/B/C/D/CENTRE/WINGER) — never a list-order guess.
        if fb_actor is not None:
            lane_y = getattr(bc, "y", None)
            if lane_y is None:
                lane_y = DefensiveBehaviourUnified._get_ball_y(world)
            update_fullback_absorb_state(fb_actor, anchor_x, lane_y)
            # FB INVARIANT: the deep backfield actor never dissolves into the
            # defensive line.  If the carrier runs near the FB, the FB stays in
            # its backfield slot and closes/tackles from there; it is never
            # reclassified as a line defender.  This is the direct cause of the
            # "end of 1st tackle they join the line" bug.
            _fb_grace = getattr(world, "fb_post_catch_absorb_grace_frames", 0)
            if _fb_grace > 0:
                print(
                    f"[FB_TELEMETRY] ABSORB_SUPPRESSED post_catch_grace_frames={_fb_grace} "
                    f"fb_id={getattr(fb_actor, 'player_id', '?')} "
                    f"fb_x={fb_actor.x:.2f} anchor_x={anchor_x:.2f}"
                )
            fb_actor.fullback_role_active = True
            if getattr(fb_actor, "role", None) != "FULLBACK":
                fb_actor.role = "FULLBACK"
            fb_actor.target_source = "fullback_geometry"
            fb_actor.depth_class = "PASSIVE"

        if fb_actor is not None:
            fb_actor.depth_class = "PASSIVE"
            # ── SINGLE FULLBACK TARGET OWNER (2026-09-28) ──
            # The anchor pin below must NOT overrule a live breakaway chase.
            # MEASURED DEFECT (probe tmp_fb_line_writers, seed 20260827,
            # id 200, frames 1690-2170): this pin wrote target_x = anchor_x
            # (the STATIC line anchor, 110.00) with DRIFT while
            # apply_fullback_geometry wrote the live pursuit point
            # (bc.x - FB_CHASE_TRAIL_M, ~119.0) with CHASE.  The sweeper's
            # target therefore flipped ~9 m and the body walked BACKWARDS
            # off a STATIONARY carrier for 29 frames at a time, twice in the
            # window -- a static target after a genuine line break AND a
            # metre-scale target swing in one sequence, with the facing
            # pointing away from the carrier (a DRIFT velocity points at the
            # anchor).  While a break is live the chase owns BOTH the target
            # and the movement state; the gate reads the SAME
            # classify_fullback_break the geometry writer uses, so the two
            # can never disagree about what a break is.
            from game.defence.defence_lvl_1.fullback_controller import (
                FullbackIntent as _FbIntent,
                SOURCE_ANCHOR as _FB_SOURCE_ANCHOR,
                enter_fullback_intent as _fb_enter,
                shared_policy_state_allowed as _fb_shared_policy_enabled,
            )
            from game.defence.defence_lvl_1.fullback_role import (
                fullback_breakaway_live as _fb_breakaway_live,
            )
            # ── STAGE 2 — STATE OWNERSHIP (2026-09-30) ──
            # This is the SHARED defender state policy.  It may only name a
            # state while ``fullback_controller`` does not own the frame.
            # The gate reads the controller's OWN authority predicate, so the
            # pin and the controller can never disagree about who is in
            # charge: on a live break emergency (an armed break latch, of
            # EITHER kind) the controller owns the state and this pin yields,
            # even when ``fullback_breakaway_live`` does not admit the break
            # (it admits a DEPTH latch but not a LATERAL one -- the exact
            # CHASE/DRIFT split-brain this stage removes).
            if (
                _fb_breakaway_live(world, fb_actor)
                or not _fb_shared_policy_enabled(world, fb_actor)
            ):
                pass
            else:
                # SPEED AUTHORITY REMOVED (2026-09-27): the old 2.0 m/s
                # sweeper approach write is gone.  The engine still owns the
                # actor's STATE (DRIFT above) and its TARGET (anchor_x /
                # target_y), but the PACE now comes from the shared
                # movement-state table inside apply_fullback_locomotion --
                # the same acceleration, top speed and integration path
                # every line defender receives.
                #
                # The state itself is written through the ONE write path so
                # the shared policy never assigns a fullback state directly.
                # ``SWEEP`` resolves to the identical ``MovementState.DRIFT``
                # singleton the literal assignment above used, so this frame
                # is byte-identical whenever no emergency is live.
                _fb_enter(
                    world, fb_actor, _FbIntent.SWEEP, source=_FB_SOURCE_ANCHOR
                )
                fb_actor.target_source = "fullback"
                fb_actor.target_x = anchor_x

        # Line defenders = all non-fullback defenders.  The fullback is
        # geometry-independent and must not occupy a line lane slot.  When
        # the actor was absorbed above, fullback_role_active is now False
        # so it re-enters the line pool THIS frame (deterministic line role).
        line_defenders = [
            d for d in defenders
            if not getattr(d, "fullback_role_active", False)
        ]

        # -- HARD OVERRIDE: immediate chase when BC is at or past anchor --
        self._force_immediate_defence_reaction(world, line_defenders)

        # -----------------------------------------------------------
        # 1. LANE INDICES — REMOVED FROM GEOMETRY (2026-08-09)
        # DEFENSIVE GEOMETRY IS DRIVEN BY DEPTH ROLES ONLY.  There is NO
        # Y-ordering and NO lane-index ordering in the line-shape pipeline.
        # lane_index (via _compute_lane_indices) is computed LATER, right
        # before select_tacklers(), which still needs it for tackle-EDGE
        # assignment — pursuit and tackle-edge logic only.
        # -----------------------------------------------------------

        # -----------------------------------------------------------
        # 2. DEPTH CLASSIFICATION - ranked selection (1 PRIMARY, 2 SECONDARY)
        # -----------------------------------------------------------
        self.classify_depth_all(world, line_defenders)

        # -----------------------------------------------------------
        # 2a. CLEAR TIMING FLAGS ON RECLASSIFIED DEFENDERS
        # When a defender gets promoted (e.g. PASSIVE->PRIMARY or
        # SECONDARY->PRIMARY), all timing delays from their previous role
        # must be cleared immediately so they act on the new classification.
        # -----------------------------------------------------------
        for d in defenders:
            prev_class = getattr(d, "_prev_depth_class", None)
            new_class = getattr(d, "depth_class", "PASSIVE")
            if prev_class is not None and prev_class != new_class:
                d.reaction_timer = 0.0
                d.retreat_delay = 0.0
                d.lateral_hesitation = 0.0
                d.step_stagger = 0.0
                d.step_stagger_active = False
                d.timing_locked = False
            d._prev_depth_class = new_class

        # -----------------------------------------------------------
        # 2b. DEPTH ROLES - assign A/B/C/D/CENTRE/WINGER before geometry
        #
        # Engagement roles (PRIMARY/SECONDARY/PASSIVE) set by
        # classify_depth_all above do NOT define depth or spacing — the
        # depth roles do.  RUCK-DRIVEN ROLE SYSTEM (2026-08-08): the
        # assignment is derived from ruck geometry (ruck_x_m/ruck_y_m) —
        # each side of ruck_y assigns its widest defender WINGER, second
        # CENTRE, then ruck_x-proximity A/B/C with all surplus becoming
        # D.  No index-based roles, no raw-Y role sorts, no static role
        # lists.  The geometry chokepoint (compute_line_shape) also
        # assigns as a safety net for the other callsites; this is the
        # explicit pipeline wiring.
        #
        # TASK 2 (2026-08-14) — SINGLE-ASSIGN-PER-FRAME: the un-gated
        # preamble (OpenPlayEngine.update() top) already ran
        # assign_depth_roles this frame and computed frame_spacing_targets
        # from those SAME roles.  Re-assigning here would mutate the bands
        # mid-frame and leave the published spacing dict stale relative to
        # the post-mutation roles (the stale_depth_role / stale_target_y
        # drift > 3.0 m in the live trace).  Guarded: roles are assigned
        # exactly once per frame by the preamble; this block consumes.
        # -----------------------------------------------------------
        if not _depth_roles_assigned_this_frame(world):
            assign_depth_roles(line_defenders, world)

        # -----------------------------------------------------------
        # 3. GEOMETRY - ROLE-DRIVEN (target_x, target_y) (2026-08-09)
        # -----------------------------------------------------------
        # DEFENSIVE GEOMETRY IS DRIVEN BY DEPTH ROLES ONLY.  The working
        # order is the input order (line_defenders) — there is NO Y-sort
        # and NO lane-index ordering anywhere in the line-shape pipeline.
        # compute_role_targets() assigns depth roles (already assigned in
        # step 2b, idempotent) and returns per-defender
        #     target_x = ruck_x + WIDTH_OFFSETS[depth_role]
        #     target_y = ruck_y + depth_side * half_width
        #                * ROLE_WIDTH_OFFSETS[depth_role]
        # Geometry movement is enabled ONLY during OPEN_PLAY
        # (geometry_active() gate).  In RELEASE / PTB_PREP / PTB_EXEC /
        # PTB / POST_RUCK_OPEN_PLAY / TACKLE_DRIVE / TACKLE_DOMINANCE the
        # role targets are NOT written — those phase handlers own targets.
        anchor_x = getattr(world, "defensive_line_anchor", 60.0)
        sorted_defs = list(line_defenders)
        role_targets = {}
        if geometry_active(world):
            role_targets = self.def_geometry.compute_role_targets(world, sorted_defs)

        # -----------------------------------------------------------
        # 4. TIMING - no PASSIVE freeze override
        # -----------------------------------------------------------
        dt = getattr(world, "dt", 1.0 / 60.0)
        self.def_timing.update(world, line_defenders, dt)

        # Override: ensure PASSIVE defenders are NOT frozen by timing_locked.
        # PASSIVE defenders must always be able to move into their lane positions.
        # Timing delays still affect their acceleration/speed via decision layer.
        for d in defenders:
            if getattr(d, "depth_class", "") == "PASSIVE" and getattr(d, "timing_locked", False):
                d.timing_locked = False
                d.reaction_timer = 0.0
                d.retreat_delay = 0.0
                d.lateral_hesitation = 0.0
                d.step_stagger = 0.0

        # -----------------------------------------------------------
        # 4b. DYNAMIC DEPTH-ROLE REFIT (TASK X) + REBANDING (TASK IX)
        # -----------------------------------------------------------
        # Immediately AFTER Stage-3F timing stabilisation and BEFORE any
        # tackler selection / _start_tackle later in this method:
        #   - TASK X: at every NEW ruck pivot, refit depth roles FROM
        #     SCRATCH off ball-side + ruck context + SETTLED positions
        #     (kills the static-role inheritance that corrupted
        #     CENTRE/WINGER and middle/edge lanes), then
        #   - TASK IX: enforce the intra-ruck slot invariants on the
        #     whole line.
        # On either change, republish the frame geometry so
        # frame_spacing_targets matches the corrected roles THIS frame
        # (Task 2 single-source law).
        _dynamic_refit = _apply_dynamic_depth_roles(world, line_defenders)
        if _dynamic_refit or _reband_after_stabilisation(world, line_defenders):
            from game.defence.defence_lvl_1.defensive_pipeline import (
                run_defensive_pipeline as _republish_geometry,
            )
            _republish_geometry(
                world, list(line_defenders), self.tuners,
                governor=self.line_governor,
            )

        # -----------------------------------------------------------
        # 5. GOVERNOR - shared line state (depth/width/speed/compression)
        # -----------------------------------------------------------
        line_state = self.line_governor.compute_line_state(world, line_defenders)
        self._probe(world, "governor")

        # -----------------------------------------------------------
        # 6. ROLE SPACING PREP + SPLIT-DESIGN SPACING (Task E/F)
        # -----------------------------------------------------------
        # PREP the legacy pass-through role spacing (kept for timing holds).
        self.def_role_spacing.prepare(world)
        # SPLIT-DESIGN spacing is the official geometry target generator
        # (Task F): per-side spacing chains built from stable role anchors,
        # proportional split (short/open side), sideline override clamp.
        # compute_targets() reads role_anchor_y for every defender, so the
        # spacing IS the role spread.  Fullback actor excluded (last-man).
        from game.defence.defence_lvl_1.defensive_split_spacing import compute_targets as _split_targets
        # FRAME AUTHORITY (2026-08-21): consume the un-gated preamble's
        # published frame dict instead of re-deriving spacing here.  The
        # preamble ran earlier this frame with the THEN-current width;
        # re-running compute_targets the moment the governor widens the
        # line (activation frame) produces a second, divergent Y source
        # (writers saw governed 54.3 while the frame dict holds raw 40).
        # TASK 8 (2026-08-21): the shared ``frame_spacing_covers``
        # predicate replaces the inline fresh+coverage check — when the
        # frame dict covers this defender set, NO spacing recompute runs
        # (the dict is consumed directly); otherwise the structural
        # chain recomputes exactly once, as before.
        if _frame_spacing_covers(world, sorted_defs):
            split_targets = dict(getattr(world, "frame_spacing_targets"))
        else:
            split_targets = _split_targets(world, sorted_defs, self.tuners,
                                           governor=self.line_governor)

        # ── TASK H SPACING AUTHORITY (2026-08-11) ──
        # Spacing is a Y-only authority layer.  The guard snapshots every
        # defender's X-family fields (target_x / role_depth_target_x /
        # chase_target_* / mmdl_target_*) plus the anchor authority before
        # the chain ran and raises SpacingDepthViolation if the chain
        # touched any of them (S0/S1/S3).  The OPEN_PLAY-only gate (S4) is
        # inherent here: this block runs inside _apply_structured_open_play,
        # which the dispatcher only enters for GameSubState.OPEN_PLAY.
        from game.defence.defence_lvl_1.spacing_authority import SpacingGuard
        split_targets = SpacingGuard(world, sorted_defs).verify(split_targets)
        self._probe(world, "spacing")

        # ── TASK I WIDTH AUTHORITY (2026-08-11) ──
        # Width recompute is an X-only authority layer.  It runs AFTER
        # spacing (step 6, Task H) and BEFORE the step-7 apply loop, so
        # the dependency chain is: Width Governor (D, step 5) -> Spacing
        # (H, step 6) -> Width Recompute (I, step 7) -> Retreat/Close/
        # Finalise (steps 9/10/10a) -> Anchor Audit (G) -> MMDL (10b).
        #
        # The recompute is PURE (no writes): it produces the per-defender
        # line X from ruck_x + WIDTH_OFFSETS[depth_role] for ELIGIBLE
        # (PASSIVE, line-holding) defenders only (I5).  The guard
        # snapshots every forbidden field (Y family, anchor, spacing
        # outputs, chase/MMDL, depth roles) BEFORE and asserts NO
        # mutation (I0/I3/I4/I6).  The OPEN_PLAY-only gate (I1) is
        # inherent here: this block runs inside _apply_structured_open_play.
        #
        # The X WRITE stays in the apply loop below (step 7 owns the
        # ``d.target_x`` write); the authority layer proves the width
        # SOURCE is X-only and mutation-free before the loop consumes it.
        # ── STAGE 6 SINGLE WIDTH AUTHORITY (2026-09-13) ──
        # The governed width is READ from the frame publication, not solved
        # again.  ``publish_formation_width`` is idempotent within a frame
        # (cached by frame + scope + tuner signature), so this is a cache hit
        # when the preamble already published — and a correct first solve when
        # this pipeline is the only publisher for the frame (e.g. a phase that
        # does not run ``run_defensive_pipeline``).  Either way the value the
        # X recompute consumes is the SAME width the spacing span was built
        # from, which is what removes the two-publisher divergence.
        from game.defence.defence_lvl_1.formation_width_authority import (
            publish_formation_width as _publish_formation_width,
            formation_width_value as _formation_width_value,
        )
        _publish_formation_width(world, self.tuners)
        _governed_width = _formation_width_value(world, self.tuners)
        from game.defence.defence_lvl_1.defensive_width import (
            recompute_width_x,
        )
        from game.defence.defence_lvl_1.width_authority import WidthGuard
        width_targets = recompute_width_x(
            world, sorted_defs, self.tuners,
            governor_width=_governed_width,
            spacing_targets=split_targets,
        )
        WidthGuard(
            world, sorted_defs, self.tuners, _governed_width,
            spacing_outputs=split_targets,
        ).verify(width_targets)
        # STAGE 3C — PUBLISH BOTH FRAME DICTS so every later writer
        # (retreat / structured-close / MMDL) is a PURE CONSUMER of the
        # single spacing-Y + width-X authority outputs, never a re-compute.
        # STAGE 4B (audit-only): record that THIS frame's geometry was
        # solved by the structured OPEN_PLAY pipeline, not (or as well as)
        # the un-gated preamble.  Function-local import: this module must
        # stay free of a module-scope dependency on defensive_pipeline.
        from game.defence.defence_lvl_1.defensive_pipeline import (
            stamp_frame_geometry_publish as _stamp_publish,
        )
        _stamp_publish(world, "structured_open_play")
        world.frame_spacing_targets = dict(split_targets)
        world.frame_width_targets = dict(width_targets)
        world.frame_geometry_ready = True
        world.frame_geometry_frame = getattr(world, "frame_count", None)
        self._probe(world, "width")

        # -----------------------------------------------------------
        # 7. APPLY GEOMETRY TARGETS - structured positions
        # -----------------------------------------------------------
        for i, d in enumerate(sorted_defs):
            # Markers with active freeze_timer keep their positions
            if _is_marker(d) and getattr(d, "freeze_timer", 0.0) > 0:
                ruck_x = getattr(world, "ruck_x_m", 60.0)
                ruck_y = getattr(world, "ruck_y_m", 35.0)
                if getattr(d, "defender_role", None) == DefenderRole.MARKER_A:
                    d.target_x = ruck_x + 1.0
                    d.target_y = ruck_y
                else:
                    d.target_x = ruck_x + 2.0
                    d.target_y = ruck_y
                d.frozen = True
                d.target_source = "struct_marker_frozen"
                continue

            # Non-marker freeze_timer: hold position.
            #
            # ── PRE-TACKLE CORRIDOR YIELD (2026-09-15) ──
            # SPEED-AUTHORITY CONSOLIDATION.  A COMMITTED tackler the
            # pre-tackle corridor owns must NOT be frozen and pinned here.
            # Freezing zeroes the body's velocity while the collision
            # system keeps resolving it against the carrier, which measured
            # live as a committed tackler drifting 3.65 m -> 7.88 m AWAY
            # from the ball carrier with |v| = 0.00 and a stale
            # ``move_speed`` of 7.89 m/s — the WRONG way, at zero speed,
            # with the taper's capped value discarded.  The corridor is the
            # sole speed authority for that body across the whole approach,
            # so this writer yields to it.  Non-corridor freeze_timer
            # bodies (the genuine hold case) are byte-identical.
            if getattr(d, "freeze_timer", 0.0) > 0:
                if _is_corridor_owned(d):
                    continue
                d.frozen = True
                d.target_x = d.x
                d.target_y = d.y
                d.target_source = "struct_freeze_timer"
                continue

            # All other defenders: ROLE-DRIVEN geometry positions
            # (2026-08-09).  X = ruck_x + WIDTH_OFFSETS[depth_role] from
            # role_targets; Y = the SPLIT-DESIGN SPACING target (Task F)
            # — per-side spacing chains from stable role anchors.  There
            # is NO governor depth_x wire, NO lane_y, and NO lane index —
            # the depth role defines depth (X) and spacing defines width
            # (Y).
            #
            # ── DRIVE-AWARE X SOURCE (2026-09-06) ──
            # During the drive (OPEN_PLAY + TES) the structural role X is
            # BC-relative: ``role_targets`` (DefensiveGeometry.
            # compute_role_targets) = ``bc.x + WIDTH_OFFSETS[depth_role]``,
            # so the line tracks the driven carrier.  Prefer it over the
            # ruck-anchored width recompute (``width_targets`` =
            # ``ruck_x + WIDTH_OFFSETS[depth_role]``), which is the
            # STRUCTURAL line-depth fallback used when geometry is
            # inactive / no live BC.  The width lane X stays ruck-anchored
            # for the retreat/on-side writers; this loop is the line
            # SHAPE writer and must follow the carrier.
            role_t = role_targets.get(id(d))
            if role_t is not None:
                tx = role_t[0]
            else:
                wtx = width_targets.get(id(d))
                if wtx is not None:
                    tx = wtx
                else:
                    # geometry inactive (non-OPEN_PLAY phase) or defender
                    # missing a role target: hold current position.
                    tx = d.x

            ty = split_targets.get(id(d))
            if ty is None:
                # GEOMETRY AUTHORITY LOCK (2026-09-04): a defender absent
                # from the split-spacing dict must fall back to the frame
                # spacing authority (the documented non-chain pin), never to
                # the current d.y (which would override the geometry).
                ty = _frame_spacing_y(world, d, self.tuners,
                                      governor=self.line_governor)

            # Role-specific adjustment via role spacing
            tx, ty = self.def_role_spacing.adjust_for_role(
                d, tx, ty, world, line_state
            )

            # GEOMETRY AUTHORITY LOCK (2026-09-04): a timing-held defender
            # (retreat_delay / lateral_hesitation / timing_locked) is a
            # VELOCITY-ONLY hold — keep the geometry target, suppress speed.
            _timing_held = (
                getattr(d, "retreat_delay", 0.0) > 0.0
                or getattr(d, "lateral_hesitation", 0.0) > 0.0
                or getattr(d, "timing_locked", False)
            )
            d.target_x = tx
            d.target_y = ty
            stamp_target_write(world, d, "role_spacing", detail="adjust_for_role")
            d.target_source = "structured_open_play"

            # Governor speed for ALL defenders (will be refined by decision layer)
            if _timing_held:
                d.move_speed = 0.0
            else:
                d.move_speed = line_state["speed_mps"]

        # -----------------------------------------------------------
        # 7a. LINE BREAK COLLAPSE - dynamic re-evaluation when BC breaks the line
        # -----------------------------------------------------------
        # When the ball carrier has advanced past the defensive line anchor,
        # ALL defenders (including PASSIVE) must receive dynamic BC-relative
        # targets instead of static anchor_x.  Without this, PASSIVE defenders
        # remain parked at the static retreat band (ruck_x + 10m) with zero
        # velocity because target_x == current_x.
        #
        # This block reclassifies PASSIVE defenders to SECONDARY for the
        # purposes of target computation, and sets BC-relative targets that
        # produce velocity delta.  It does NOT modify depth_class (that is
        # preserved for the decision layer which still gates PRIMARY/SECONDARY).
        #
        # ROLE-ANCHORED COLLAPSE WIDTH (2026-08-09, Task B): collapse Y is
        # the canonical role anchor (locked depth_side, governed width) —
        # NOT lane_index, NOT raw def_line_width inline math.
        if bc is not None and anchor_x is not None and bc.x > anchor_x:
            ruck_y = getattr(world, "ruck_y_m", 35.0)
            for d in sorted_defs:
                if d.frozen or getattr(d, "freeze_timer", 0.0) > 0:
                    continue
                dc = getattr(d, "depth_class", "PASSIVE")
                if dc == "PASSIVE" and not _is_marker(d):
                    # Override target to BC-relative so the defender gets
                    # a dynamic target ahead of their current position.
                    # This produces a velocity delta even when at anchor_x.
                    # Y = split-design spacing (Task F): collapse never
                    # recomputes an inline width — spacing owns Y.
                    # BC-RELATIVE CHASE RESET (2026-09-10): the collapse target
                    # must be the LIVE BC (not a bc.x - depth offset clamped to
                    # the anchor, which can be a dead static target).
                    d.target_x = float(bc.x)
                    # 2026-08-12: consumed from the un-gated frame preamble.
                    d.target_y = _frame_spacing_y(world, d, self.tuners,
                                                  governor=self.line_governor)
                    stamp_target_write(world, d, "spacing")
                    d.target_source = "line_break_collapse"
                    d.movement_state = MovementState.CHASE
                    # GLOBAL DEFENSIVE TOP-SPEED CEILING: collapse speed
                    # derives from the unified ceiling.
                    d.move_speed = self._def_speed(d)

                # ── TASK VI (2026-08-15): ENGAGED NON-LANE CHASE ──
                # The PASSIVE-only override above leaves engaged (PRIMARY/
                # SECONDARY) defenders who are NOT the lane tackler on
                # static width/geometry X, so even forced CHASE state would
                # produce no vx — the "front three freeze until alignment"
                # symptom.  Once the BC is clearly past the line, these
                # engaged defenders also get a forward BC-relative target
                # (X only; Y stays the spacing chain) so the whole engaged
                # line chases diagonally.  Lane tacklers are excluded — they
                # already own the BC-Y convergence via the tackler-commit
                # writer in step 10a1 and jumping lanes here would fight it.
                elif self._stable_engaged_non_lane(d, world, bc):
                    # -- STAGE 3D: CONVERGENCE SINGLE-SOURCE CONSUMER --
                    # The manager-published frame_convergence_targets entry
                    # is the SOLE authority for this engaged non-lane
                    # defender's BC-closing X; the inline depth-scaled math
                    # survives only as the no-dict fallback for stubs /
                    # non-engine frames.
                    _conv_map = getattr(
                        world, "frame_convergence_targets", None
                    ) or {}
                    _cpt = _conv_map.get(id(d))
                    if _cpt is not None:
                        engaged_target_x = float(_cpt[0])
                    else:
                        engaged_target_x = max(
                            anchor_x, bc.x - self.tuners.def_line_depth_m * 0.7
                        )
                    d.target_x = engaged_target_x
                    d.target_y = _frame_spacing_y(world, d, self.tuners,
                                                  governor=self.line_governor)
                    stamp_target_write(world, d, "spacing")
                    d.target_source = "engaged_non_lane_chase"
                    d.movement_state = MovementState.CHASE
                    d.move_speed = self._def_speed(d)
                    # Facing toward the BC so the diagonal chase reads
                    # naturally and the pre-tackle square-up owns the final
                    # approach.
                    angle_to_bc = math.atan2(bc.y - d.y, bc.x - d.x)
                    d.set_facing(angle_to_bc)

        # -----------------------------------------------------------
        # 8. DECISION LAYER - speed/movement_state only, NOT targets
        # -----------------------------------------------------------
        ruck_x = getattr(world, "ruck_x_m", 60.0)
        for d in sorted_defs:
            # Skip frozen defenders
            if d.frozen or getattr(d, "freeze_timer", 0.0) > 0:
                continue

            depth_class = getattr(d, "depth_class", "PASSIVE")
            is_marker = _is_marker(d)

            decision = ddecision(
                defender=d,
                bc=bc,
                ruck_x=ruck_x,
                anchor_x=anchor_x,
                depth_class=depth_class,
                is_marker=is_marker,
                timing_locked=False,  # Already cleared above
                freeze_timer=0.0,
                retreat_compliant=True,  # Governor handles retreat, not hard compliance
            )

            # Speed scale per decision - does NOT modify target_x/target_y
            speed_scale = {
                Decision.HOLD: 0.0,
                Decision.SHOOT: 1.0,
                Decision.CHASE: 0.9,
                Decision.COMPRESS: 0.7,
                Decision.SLIDE: 0.6,
                Decision.DRIFT: 0.4,
                Decision.RETREAT: 0.7,
            }.get(decision, 0.5)

            # GLOBAL DEFENSIVE TOP-SPEED CEILING: decision speed scales the
            # unified ceiling, never the raw per-player max.
            d.move_speed = min(d.move_speed, self._def_speed(d) * speed_scale)
            # TASK III (2026-08-14) — DEFECT B RESOLVED: the legacy
            # decision layer previously wrote `movement_state` here, then
            # MMDL step 10b overwrote it with resolve_movement_state() in
            # the same frame.  The transient write was always overwritten
            # (never corrupting) but made movement_state garbage for any
            # reader between step 8 and 10b (audits, freeze audit, render,
            # stabiliser gate).  MMDL is the SOLE movement_state writer in
            # structured OPEN_PLAY; the legacy layer only scales speed.
            d.target_source = f"structured_{decision.name}"
            d.last_decision = decision.name

        # -----------------------------------------------------------
        # 9. MANDATORY RETREAT - force PASSIVE non-compliant defenders back
        #     (ALWAYS runs during OPEN_PLAY, BEFORE structured_close)
        #
        # PASSIVE defenders who have not retreated the full 10m from the
        # ruck are forced back with movement_state = RETREAT.  This ensures
        # they backpedal hard instead of hovering near the ruck on geometry
        # targets.
        #
        # WIDTH LANE PRESERVATION (2026-08-13): a non-compliant PASSIVE
        # defender holding a WIDTH LANE (width = X from WIDTH_OFFSETS,
        # lane_x = ruck_x + WIDTH_OFFSETS[depth_role]) retreats to ITS LANE
        # X -- never to ruck_x + retreat_compliance_distance_m.  The old
        # compliance pin overwrote every interior lane (A/B/C/D/CENTRE sit
        # forward of ruck+10) with ruck+10 every frame, collapsing the
        # line's X-depth spread onto the WINGER slot ("retreat overrides
        # width-X").  Non-lane PASSIVE defenders keep the compliance pin.
        # Spacing Y is never touched: retreat (X) and spacing (Y) stay
        # orthogonal.  movement_state RETREAT + retreat speed + facing are
        # unchanged.
        #
        # This override runs AFTER the decision layer so that PASSIVE
        # defenders who would otherwise get DRIFT (speed_scale=0.4) or
        # HOLD (0.0) get full RETREAT speed instead.
        #
        # AI_FIX: Also override move_speed (was 0.4 from DRIFT) so PASSIVE
        # non-compliant defenders actually cover ground, and set
        # target_source so it is clear retreat is the active writer.
        # -----------------------------------------------------------
        self._probe(world, "retreat")
        if world.sub_state == GameSubState.OPEN_PLAY:
            ruck_x = getattr(world, "ruck_x_m", 60.0)
            compliance_dist = self.tuners.retreat_compliance_distance_m
            compliance_x = _anchor_m(world)
            for d in sorted_defs:
                # Fix A - Guard by player type: only Player objects may receive retreat overrides
                if not isinstance(d, Player):
                    continue
                # FULLBACK EXEMPTION (2026-08-17): the fullback never enters
                # RETREAT — geometry-driven only.  Belt-and-braces: the actor
                # is excluded from ``line_defenders`` (and therefore
                # ``sorted_defs``) upstream, but this guard keeps RETREAT off
                # the actor even if a future edit leaks it into the line pool.
                if getattr(d, "fullback_role_active", False):
                    continue
                if d.frozen or getattr(d, "freeze_timer", 0.0) > 0:
                    continue
                if getattr(d, "depth_class", "PASSIVE") != "PASSIVE":
                    continue
                if not self._is_retreat_eligible(d, ruck_x, compliance_dist):
                    # STAGE 3C WIDTH CONSUMER: a defender holding an entry
                    # in the published width dict keeps its authoritative
                    # lane X — retreat NEVER derives/overrides width-X.
                    # Non-width defenders fall back to the anchor pin.
                    _wd = getattr(world, "frame_width_targets", None) or {}
                    _lane = _wd.get(id(d))
                    if _lane is not None:
                        d.target_x = float(_lane)
                        d.target_source = "retreat_to_lane"
                    else:
                        d.target_x = compliance_x
                        d.target_source = "retreat_non_compliant"
                    d.movement_state = MovementState.RETREAT
                    # GLOBAL DEFENSIVE TOP-SPEED CEILING: retreat < ceiling.
                    d.move_speed = self._def_speed(d) * 0.85
                    d.frozen = False
                    if bc is not None:
                        angle_to_bc = math.atan2(bc.y - d.y, bc.x - d.x)
                        d.set_facing(angle_to_bc)

        # -----------------------------------------------------------
        # 10. STRUCTURED OPEN PLAY: SAFE CLOSING FOR PRIMARY + SECONDARY
        #     (AFTER decision layer and mandatory retreat - overrides both)
        # -----------------------------------------------------------
        # Only run structured_close in true OPEN_PLAY, not while retreat
        # is active.  Clamp closing to onside + geometry so line + legality
        # survive.  Switch movement_state + flags so the governor stops
        # treating them as retreat cheats.
        #
        # WIDTH PRESERVATION (Task C, 2026-08-09): the structured-close
        # lateral target is PURE role-anchor width — computed ONLY from the
        # canonical role-anchor module (get_role_anchor_y) and world.ruck_y_m.
        # No BC-relative chase Y, no fallback width, no lane-index width, no
        # positional width, no midfield width, and NO width blending with any
        # other source.  tuners.structured_close_width_blend MUST be 0.0 and
        # OVERRIDES any blend logic: while 0.0 the final width is exactly the
        # role width anchor.  The sideline clamp (gated by
        # tuners.structured_close_clamp_active) and the governor clamp (gated
        # by tuners.governor_width_active) still apply.
        #
        # Facing is forced toward BC (structured mode normally never
        # calls set_facing() - without this, defenders don't know which
        # way to move into contact).
        #
        # This MUST run AFTER the decision layer because the decision
        # arbiter returns RETREAT for defenders ahead of BC (depth < 0),
        # which overrides closing targets with anchor_x and kills speed.
        if world.sub_state != GameSubState.OPEN_PLAY:
            # Do NOT run closing while retreat/PTB logic is active
            pass
        else:
            self._probe(world, "close")
            ruck_x = getattr(world, "ruck_x_m", 60.0)
            onside_buffer = 1.5   # metres behind ruck back-foot for legal line
            onside_x = ruck_x + onside_buffer
            bcx = bc.x
            bcy = bc.y

            ruck_y = getattr(world, "ruck_y_m", 35.0)

            for d in sorted_defs:
                if self._is_tes_non_tackler(world, d):
                    # TES SUPPORT ROLE: NEVER chase during an active TES.
                    # Fold back behind the ruck line at a controlled retreat
                    # speed instead of sprinting like open-play chase.  The
                    # OPE TES stasis block pins these players on TES-phase
                    # frames; this writer guarantees no chase vector lands
                    # on OPEN_PLAY frames where TES has already activated.
                                        # TES SUPPORT ROLE: STAGE 3B delegated to the
                    # RetreatManager (sole retreat authority).
                    apply_tes_support_retreat(
                        world, d, self.tuners,
                        governor=self.line_governor,
                    )
                    continue
                if d.frozen or getattr(d, "freeze_timer", 0.0) > 0:
                    continue
                # ── INTERCEPT OWNERSHIP GATE (2026-08-15, spec §1/§2) ──
                # Only the PRIMARY lane tackler may run the BC-driven close
                # writer.  LINE defenders (everyone else on the side) keep
                # their spacing/geometry targets — they never try to
                # intercept, so the selected converger's ownership is
                # unambiguous.  The depth_class gate below is retained for
                # flow-harness compat but the lane role is the authority.
                if not self._stable_is_primary_lane_tackler(d):
                    continue
                dc = getattr(d, "depth_class", "PASSIVE")
                if dc not in ("PRIMARY", "SECONDARY"):
                    continue

                dx = bcx - d.x
                dy = bcy - d.y


                # --- INTERCEPT LEAD (2026-08-06) ---
                # Aim at the point the BC will occupy when the defender
                # arrives (time-to-intercept solve), so the defender cuts
                # IN FRONT of the BC instead of converging on its current
                # position and arriving behind.  t_max=0.8s keeps the lead
                # bounded so a sprinting BC cannot drag the line too far
                # upfield.
                # -- STAGE 3D: CONVERGENCE SINGLE-SOURCE CONSUMER --
                # The manager-published frame_convergence_targets entry is
                # the SOLE authority for this defender's BC-closing X.
                # Legacy intercept-lead math below survives only as the
                # no-dict fallback for stubs / non-engine frames.  The
                # onside clamp still applies to whichever source wins.
                _conv_map = getattr(
                    world, "frame_convergence_targets", None
                ) or {}
                _cpt = _conv_map.get(id(d))
                if _cpt is not None:
                    target_x_raw = _cpt[0]
                else:
                    ictx, icty = compute_intercept_target(
                        d, bc, t_max=0.8
                    )
                    # --- Onside-clamp input: legacy offset math ---
                    if dc == "PRIMARY":
                        target_x_raw = ictx - 1.0
                    else:  # SECONDARY
                        target_x_raw = ictx - 2.0

                d.target_x = max(target_x_raw, onside_x)
                d.target_source = "structured_close"

                # ── PURE ROLE-ANCHOR WIDTH (Task C, 2026-08-09) ──
                # The structured-close WIDTH is EXACTLY the role width anchor
                # from the canonical module (defensive_width.get_role_anchor_y)
                # centred on ruck_y with the governed width.  There is NO
                # BC-relative chase Y, NO fallback width, NO lane-index width,
                # NO positional width, NO midfield width, and NO width blending
                # with any other source.  tuners.structured_close_width_blend is
                # the MANDATORY override gate: it MUST be 0.0 (pure anchor).
                # Any non-zero value is treated identically to 0.0.
                if self.tuners.structured_close_width_blend != 0.0:
                    # Contract flag only — the blend tuner no longer performs
                    # arithmetic; the spec requires pure role-anchor width.
                    pass
                # SINGLE WIDTH SOURCE (2026-09-03): use role_anchor_y_side so
                # the structured-close fallback consumes the SAME per-side
                # width (short-side squeeze / available-room cap) as the
                # spacing chain.  The old get_role_anchor_y(d, ruck_y,
                # line_width, self.tuners) passed the FULL governed width to
                # BOTH sides, putting the short-side WINGER at the bare
                # margin (3.0) while spacing used margin+buffer (6.0) — the
                # two-authority short-side blowout.
                final_width_y = role_anchor_y_side(
                    d, ruck_y, world, self.tuners
                )

                if self.tuners.structured_close_clamp_active:
                    final_width_y = clamp_to_sideline(final_width_y, self.tuners)

                if self.tuners.governor_width_active:
                    final_width_y = self.line_governor.apply_width_clamp(
                        final_width_y
                    )

                # ── SPACING OVERRIDE (Task E) ──
                # structured_close_target[player].y = spacing_targets[player].y.
                # The split-design spacing chain is the single geometry
                # target generator; the close writer reads the SAME
                # role-anchored spacing Y so a proportional split (short/
                # open side factors) is preserved through closing.  The
                # spacing targets ARE pure role anchors (ruck_y ± role
                # offset, clamped) — no BC-relative chase Y enters here.
                spacing_ty = split_targets.get(id(d))
                if spacing_ty is not None:
                    final_width_y = spacing_ty

                d.target_y = final_width_y

                # X-SMOOTHING FIX (2026-08-02): blend the close target
                # toward the previous frame's value so a PRIMARY/SECONDARY
                # re-rank (bc.x-1 vs bc.x-2 raw targets) or a BC lateral
                # jink never snaps the line more than a few cm per frame.
                # Mirrors the existing Y-smoothing (_prev_target_y_smooth).
                # Pursuit stays fast: a 0.7 blend against a 0.15m/frame
                # BC advance leaves only ~0.04m of lag per frame.
                prev_close_x = getattr(d, "_prev_close_x", d.target_x)
                d.target_x = prev_close_x + (d.target_x - prev_close_x) * 0.7
                d._prev_close_x = d.target_x

                # Force facing toward BC
                angle_to_bc = math.atan2(dy, dx)
                d.set_facing(angle_to_bc)

                # Switch them out of RETREAT so governor stops fighting you.
                # CHASE (not SHOOT) - CHASE has lateral_priority=0.3 (permits
                # diagonal movement) and face_bc=False (does not overwrite
                # AI-facing with velocity-facing).  SHOOT is a rail state
                # (lateral_priority=0.0, face_bc=False) that erases AI-facing.
                d.movement_state = MovementState.CHASE
                # LINE-SPEED UNIFICATION (2026-08-04): closers cap at the
                # governor's shared line speed (falling back to the unified
                # ceiling when no governor state is available).  Stops the
                # top-3 closers bursting to 8.3 while the line is at ~5.
                gov_speed = line_state.get("speed_mps")
                base = gov_speed if gov_speed is not None else self._def_speed(d)
                d.move_speed = base

        # -----------------------------------------------------------
        # 10a. POST-CATCH DEFENSIVE LINE CLAMP — FINALISE PASS
        # -----------------------------------------------------------
        # Applied AFTER every speed writer in this pipeline (governor,
        # line-break collapse, decision layer, mandatory retreat, structured
        # close) but BEFORE movement integration in OpenPlayEngine.  While
        # armed, the clamp caps each defender's effective speed at the
        # governor's max intended line speed so the non-governor writers
        # (pursuit reaction, depth reclassification, retreat compliance/SDM)
        # cannot visibly "turbo" defenders into position during the
        # kickoff-catch → first stable OPEN_PLAY transition.  The method
        # also auto-disarms the clamp once structured OPEN_PLAY is stable.
        # The Player._defended_cap_ceiling() chokepoint also covers the
        # SDM retreat writer's separate d.update(dt) integration later in
        # OpenPlayEngine.update().
        self._finalise_lateral_targets(world, sorted_defs)
        self._probe(world, "finalise")

        # ── TASK G ANCHOR AUTHORITY (Layer 7 + Layer 8) ──
        # Every line-holding defender ahead of the anchor is re-targeted
        # back to anchor_x (never pulled forward by spacing/width/audit),
        # then the depth-role stamps are checked for drift > 0.01 m.
        enforce_retreat_to_anchor(world, sorted_defs)
        assert_anchor_consistency(world, sorted_defs)
        self._probe(world, "anchor")

        # ── MACRO WIDTH GOVERNOR (2026-08-17, spec §1–§5) ──
        # Publish the macro width/spacing tuners every OPEN_PLAY frame
        # (spec §1/§2: tuners published every OPEN_PLAY frame), then apply
        # the macro line width + depth-role spacing governor.
        #
        # Integration point (spec §6): runs after PTB → retreat → reset →
        # open-play transitions and after EVERY target writer + the finalise
        # blend + the anchor audit, and BEFORE lane-tackler assignment (10a1)
        # + MMDL (10b), so:
        #   - spacing/width/anchor guards have already verified (no
        #     authority conflict),
        #   - committed tacklers may legitimately overwrite their own Y
        #     afterwards (this method never touches them),
        #   - the target stabiliser / MMDL see the macro-adjusted lateral
        #     target so the slow DC correction flows into movement.
        # It modifies ONLY target_y of macro-eligible line defenders —
        # never stabiliser alpha, geometry weights, smoothing, compression
        # weights, slowdown band, or stance radius (spec §6).
        DefensiveBehaviourUnified._publish_macro_width_tuners(world, self.tuners)
        # ── 3-POINT WIDTH TUNER AUTHORITY GATE (2026-08-17) ──
        # The macro width governor (apply_macro_width_governor) rescales
        # target_y toward a UNIFORM-SPACING reference ((n-1) *
        # DEFENDER_SPACING_M) — which would REDEFINE the line's actual
        # winger-to-winger span.  The 3-POINT WIDTH TUNERS
        # (width_left_tryline_m / width_halfway_m / width_right_tryline_m,
        # interpolated by defensive_width.interpolated_defensive_width and
        # published to world.def_line_width_m by the governor) are the
        # SINGLE width source: geometry/spacing/retreat/unified-retreat/
        # PTB/TES must all read the SAME interpolated width.  The macro
        # governor is therefore gated OFF here by default
        # (tuners.macro_width_governor_enabled = False).  The method and
        # its standalone verifier (verify_macro_width_governor.py, which
        # calls the method directly) remain fully intact — this gate only
        # stops the LIVE pipeline from invoking it.
        dt_macro = getattr(world, "dt", 1.0 / 60.0)
        if getattr(self.tuners, "macro_width_governor_enabled", False):
            # POST-RETREAT GATE (2026-09-02): never rescale lateral width
            # (sideline-macro §3 offset + closed-loop width apply) during the
            # first two OPEN_PLAY frames — the retreat spacing Y is
            # authoritative, so the line settles before re-broadening.
            if getattr(world, "_post_retreat_open_play_frames", 99) > 2:
                self.line_governor.apply_macro_width_governor(
                    world, sorted_defs, dt_macro
                )

        # -----------------------------------------------------------
        # 10a1. LANE-TACKLER ASSIGNMENT + TACKLER-COMMIT OVERRIDE
        # (2026-08-13 / 2026-08-15)
        #
        # INTERCEPT BEHAVIOUR (2026-08-15): tackle roles come from the
        # per-side lane system (intercept_tacklers.select_lane_tacklers)
        # — PRIMARY owns convergence per side of the BC, every other
        # candidate is LINE (spacing/shape only).  The legacy banded
        # select_tacklers no longer runs in structured mode: SECONDARY
        # is not a targeting role anymore, so the two old "pseudo-
        # convergers" are gone.  The committed PRIMARY tacklers then
        # receive the dedicated BC-forward collision target — overriding
        # spacing/width/retreat/band/anchor — so their approach is never
        # pulled sideways or backward.  The MMDL below SKIPS committed
        # defenders (their movement is owned by the commit writer +
        # engine integration), and the pre-tackle trigger after the MMDL
        # still registers commit-driven joiners.
        # -----------------------------------------------------------
        # LANE INDICES — TACKLE-EDGE ONLY (2026-08-09).  lane_index is
        # retained for debug labelling; lane selection does not use it.
        self._compute_lane_indices(world, sorted_defs)
        # -- FULLBACK TACKLE-CANDIDACY (B2, 2026-09-16) --
        # The designated fullback keeps fullback_role_active through
        # CHASE/SHOOT pursuit (movement state is never a demotion mechanism).
        # It joins the lane / commit SELECTION pool only while it carries
        # tackle authority (can_acquire_tackle, stamped by
        # assign_fullback_role) -- its line membership (depth_role /
        # depth_side / spacing / band / lane_index) stays excluded, and the
        # fullback geometry authority keeps owning its motion until a commit
        # writer claims it.
        eligible_commit = [d for d in sorted_defs if not d.frozen]
        # -- FULLBACK TACKLE-CANDIDACY (B2, 2026-09-16) --
        # The tackle-capable fullback joins the lane SELECTOR pool only, so it
        # can earn the PRIMARY/SECONDARY stamp that is_engage_role requires,
        # but it does NOT join the commit-writer pool: the commit writer
        # seizes a body's target and stamps target_source = "tackler_commit",
        # which would overwrite the fullback geometry authority and drag the
        # deep backfield anchor onto the carrier (verify_fullback_continuity
        # ASSERT-C / verify_kickoff_fullback ASSERT-C+D).  The fullback's
        # motion therefore stays owned by apply_fullback_geometry -- which
        # already pursues the carrier through its breakaway CHASE override --
        # and its target_source stays the fullback authority until TES arms
        # the actor (the handover in _arm_tackler_role).
        # The emergency gate needs the world (anchor authority) and the live
        # carrier, so a fullback can only join the lane pool on a genuine break.
        self.select_lane_tacklers(world, eligible_commit, bc)
        # NORMALISE non-lane roles to PASSIVE so no stale SECONDARY/THIRD
        # from the legacy selector survives into the commit writer or the
        # pre-tackle trigger (spec §4: only PRIMARY may own the engage).
        for d in eligible_commit:
            tr = getattr(d, "tackle_role", "PASSIVE")
            if tr not in ("PRIMARY", "SECONDARY"):
                d.tackle_role = "PASSIVE"
        _apply_tackler_commit(world, eligible_commit, self.tuners)
        # ── 12M PURSUIT/COMMIT HARD OVERRIDE (2026-09-02) ──
        # Run AFTER the tackler-commit writer so LINE/PASSIVE defenders
        # within pursuit_commit_radius_m of the BC also get forced onto
        # BC-relative pursuit targets (the commit writer only catches
        # PRIMARY/SECONDARY tackle roles).  The MMDL below then keeps their
        # CHASE state (bypassing the LINE-defender DRIFT demotion).
        self._apply_pursuit_commit_override(world, sorted_defs)

        # ── ANTICIPATION AUTHORITY WINDOW (2026-09-08) ──
        # Reassert the lean on the final Y for authority-active, non-committed
        # defenders so the boundary writers above (tackler-commit / pursuit-
        # commit) cannot instantly snap it off.  Yields completely once the
        # pre-tackle / TES depth-lock engages (checked inside
        # anticipation_authority_active), so the tackle chamber always owns
        # lateral once a real contact is forming.
        _ant_dt = getattr(world, "dt", 1.0 / 60.0)
        _ant_conv_map = getattr(world, "frame_convergence_targets", None) or {}
        for _ant_d in sorted_defs:
            if getattr(_ant_d, "frozen", False):
                continue
            if float(getattr(_ant_d, "freeze_timer", 0.0) or 0.0) > 0.0:
                continue
            if id(_ant_d) in _ant_conv_map:
                continue  # manager-owned closer — the manager owns its Y
            _apply_anticipation_authority(world, _ant_d, self.tuners, _ant_dt)

        # ── STAGE 4: UNIFIED PRE-TACKLE MOVEMENT REBUILD (2026-09-04) ──
        # Publish the canonical pre-tackle state BEFORE the MMDL so the
        # movement layer recognises it, then let the unified authority
        # author movement for every managed body (COMMIT closes on the live
        # BC-forward point, PLANTED freezes, SLOW converges to the frozen
        # anchor, HOLD freezes) -- replacing the legacy CHASE/HOLD/DRIFT
        # state-machine interpretation.  Movement and TES now read ONE
        # canonical pre-tackle state.  The MMDL below skips managed bodies
        # so it never restamps a legacy state over the authority.
        _pta_dt = getattr(world, "dt", 1.0 / 60.0)
        # The emergency fullback is a LINE-SHAPE exclusion (it is not in
        # ``sorted_defs``) but on a genuine break it is a tackle-capable
        # defender and must be owned by the SAME pre-tackle authority as a
        # line body: the governed COMMIT / SLOW / PLANTED contract, the
        # slowdown taper and the corridor handoff.  Re-injected here only.
        _pta_pool = [d for d in sorted_defs if not d.frozen]
        _publish_pre_tackle_state(world, _pta_pool, bc, self.tuners)
        apply_pre_tackle(world, _pta_pool, _pta_dt, self.tuners, frozen_mode=False)


        # -----------------------------------------------------------
        # 10b. MOVEMENT MODE DECISION LAYER (MMDL) — STRUCTURED OPEN_PLAY
        #
        # Runs AFTER every target writer and after FINALISE_BLEND have
        # stabilised target_x/target_y, but BEFORE tackle-role selection.
        # Decides the per-defender MovementState (RETREAT / SLIDE / SHOOT /
        # CHASE / DRIFT / HOLD) and applies the MovementState physics via
        # apply_movement_state() — the state machine's own velocity model,
        # NOT Player._accelerate_toward_target().
        #
        # TASK I-B PURE-CONSUMER GUARD (2026-08-11): the MMDL is a pure
        # consumer of spacing (Y) + width (X) + anchor (X).  The guard
        # snapshots every authority field before the block and asserts NO
        # mutation after it (movement_authority.py).  The block additionally
        # stamps each consumed target as mmdl_target_x/y (its consumption
        # record) and enforces the X-drift guard so PASSIVE line holders in
        # non-pursuit states can never consume a target far off the anchor.
        #
        # Contract notes:
        #   - This block ONLY runs in structured OPEN_PLAY (this method).
        #   - TES tacklers are left untouched (decide_movement_state returns
        #     the defender's current state when world.tes_active + is_tackler).
        #   - Pursuit mode never reaches here (gate in apply()).
        #   - d._mmdl_moved tells OpenPlayEngine._update_defence() to skip
        #     its own p.update(dt) integration so apply_movement_state's
        #     position step is not double-applied.
        # -----------------------------------------------------------
        from game.defence.defence_lvl_1.movement_authority import (
            MovementAuthorityGuard,
        )
        _mmdl_guard = MovementAuthorityGuard(world, sorted_defs, self.tuners)
        self._probe(world, "mmdl")

        # ── STAGE 3E: CONVERGENCE OWNERSHIP MAP (2026-08-25) ──
        # One read per frame.  Ids present here are manager-committed
        # BC-closing closers (ConvergenceManager.publish); their decided
        # movement_state must never be overridden by the LINE-defender
        # DRIFT gate or the lateral-priority blend below.
        _mmdl_conv_map = (
            getattr(world, "frame_convergence_targets", None) or {}
        )

        dt = getattr(world, "dt", 1.0 / 60.0)
        ball_x = self._get_ball_x(world)
        ball_y = self._get_ball_y(world)
        # RETREAT-OBLIGATION OWNERSHIP (2026-09-18): read once per frame.
        # A body that still owes its 10 m retreat is owned by the retreat
        # authority (which pins its target to the anchor compliance line as
        # the dispatch's FINAL target write).  The MMDL is a pure CONSUMER
        # that then INTEGRATES and sets ``_mmdl_moved``, so letting it run
        # on an obligated body moves it with ITS consumed target AND makes
        # the engine skip ``Player.update(dt)`` -- discarding the retreat
        # write.  Measured live: an obligated body 1.6 m from the line was
        # integrated BACKWARD at 7.8 m/s (v=(-7.78,-0.36)) and never
        # discharged for 11.5 s.
        for d in sorted_defs:
            if d.frozen or getattr(d, "freeze_timer", 0.0) > 0:
                continue
            if not isinstance(d, Player):
                continue
            # TACKLER-COMMIT SKIP (2026-08-13, corrected 2026-08-13): committed
            # PRIMARY/SECONDARY tacklers have already received their dedicated
            # BC-forward target + CHASE state + full speed from the 10a1 commit
            # writer.  The MMDL must NOT recompute movement for them — doing so
            # would re-route them onto RETREAT/SLIDE/DRIFT geometry states and
            # re-introduce spacing/width drift.
            #
            # INTEGRATION FIX (2026-08-13): the commit-skip branch MUST NOT
            # set `_mmdl_moved = True`.  `_mmdl_moved` promises the caller
            # (OpenPlayEngine._update_defence) that apply_movement_state()
            # already integrated this player's position this frame — but this
            # branch never calls apply_movement_state().  The engine then
            # SKIPS the committed tackler's Player.update(dt), freezing them
            # ~3 m from the BC at their PRIMARY/SECONDARY commit target, so
            # they never close the 2.0 m try_pre_tackle window and TES never
            # starts — the "tacklers never commit" symptom.
            #
            # With the flag removed, committed tacklers fall through to the
            # engine's own movement-integration loop (_update_defence):
            # Player.update(dt) accelerates them along the CHASE state toward
            # the stable (bc.x+1.0 / bc.x+2.0) commit target at the full
            # unified ceiling — the same integration path the unified-mode
            # and pursuit-mode commit writers already use.  CHASE is a
            # NON_STABILISED_MOVEMENT_STATE, so the raw commit target is
            # honoured (no target stabiliser lag).
            if getattr(d, "_tackle_commit_active", False):
                d.mmdl_target_x = d.target_x
                d.mmdl_target_y = d.target_y
                continue

            # ── POST-MISS PURSUIT SKIP (2026-09-26) ──
            # A defender the carrier beat outright OWNS its pursuit for a
            # bounded window after the miss (post_miss_pursuit).  Skipping
            # WITHOUT setting ``_mmdl_moved`` -- exactly like the commit-skip
            # and retreat-obligation skips -- is what makes the entitlement
            # effective: it hands the body back to the engine's own
            # movement-integration loop, which applies the entitlement's live
            # pursuit target (the dispatch's FINAL target write) instead of an
            # MMDL-consumed geometry target.  Setting ``_mmdl_moved`` here
            # would make the engine SKIP ``Player.update(dt)`` and DISCARD
            # that write -- the measured "target on the carrier, speed 0"
            # shape (§3 PURSUIT_TARGET_NO_MOTION).
            #
            # Placement: ABOVE the retreat-obligation skip, because a beaten
            # body is usually still obligated (DEF_RETREAT_10) and would
            # otherwise be skipped for the RETREAT target it no longer owes.
            if _owns_post_miss_pursuit(world, d):
                d.mmdl_target_x = d.target_x
                d.mmdl_target_y = d.target_y
                continue

            # RETREAT-OBLIGATION SKIP (2026-09-18)
            # Skipping WITHOUT setting ``_mmdl_moved`` is deliberate and
            # mirrors the commit-skip branch above: it hands the body back
            # to the engine's own movement-integration loop, which applies
            # the retreat authority's target (the anchor compliance line)
            # instead of an MMDL-consumed target.
            if is_retreat_obligated(d, world):
                d.mmdl_target_x = d.target_x
                d.mmdl_target_y = d.target_y
                continue

            # ── STAGE 4: AUTHORITY-OWNED SKIP (2026-09-04) ──
            # The unified pre-tackle authority already integrated this body
            # this frame (COMMIT/PLANTED/SLOW/HOLD -- it set _mmdl_moved
            # and authored the exact forward/frozen-point semantics).  The
            # MMDL must NEVER restamp legacy CHASE/HOLD/DRIFT over the
            # canonical state, or movement and TES diverge again.
            _pts = getattr(d, "pre_tackle_state", None)
            if _pts is not None and getattr(_pts, "name", None) != "NONE":
                d.mmdl_target_x = d.target_x
                d.mmdl_target_y = d.target_y
                continue

            # ── STAGE 3E: DICT-OWNED CLOSER FLAG ──
            # True when ConvergenceManager committed this defender to the
            # ball carrier this frame (frozen/fullback actors can never be
            # in the dict — publish-side guarantees).  decide_movement_state
            # already returned the convergence-aligned state (SHOOT inside
            # the collapse rail, CHASE otherwise); the gates below must
            # honour it instead of overriding.
            _dict_owned = id(d) in _mmdl_conv_map

            next_state = decide_movement_state(world, d, bc, self.tuners)
            current_state = getattr(d, "movement_state", MovementState.HOLD) or MovementState.HOLD

            # ── TASK VI CARRIER-PAST-LINE GATE (2026-08-15) ──
            # Once the BC is clearly past the anchor (bc.x > anchor_x + 1.0),
            # CHASE wins immediately for engaged defenders:
            #   - the LINE-defender gate must NOT demote engaged chasers to
            #     DRIFT (that turned the front three into spacing holders
            #     waiting for alignment),
            #   - the 0.5-blend must NOT stick a previous SLIDE back onto
            #     SLIDE (the blend zone picks the higher lateral-priority
            #     state — SLIDE=1.0 beats CHASE=0.3 — so a SLIDE defender
            #     can never leave SLIDE through the blender).  CHASE is
            #     resolved straight: it is a legal transition from every
            #     state and always produces velocity, so the pre-tackle
            #     stuck fallback is safely skipped.
            # TES-OWNER GATE (2026-08-15): the TASK VI chase wiring (LINE-gate
            # bypass + direct-CHASE resolve) must never activate on TES frames —
            # TES stasis pins non-tacklers to retreat targets and zeroes their
            # velocity, so stamping CHASE + _mmdl_moved there leaves them
            # vx=vy=0 with a live target on the following OPEN_PLAY entry frame
            # (the movement-reactivation ASSERT-A failure, seed 3/7/10).
            carrier_past = (
                self._is_carrier_past_anchor(world, bc)
                and not getattr(world, "tes_active", False)
            )
            # ── POST-CATCH PURSUIT (2026-08-16) ──
            # While the post-catch chase window is armed (3.0s after a
            # kickoff catch), line defenders are treated exactly like a
            # carrier-past-line break: the LINE-defender DRIFT gate is
            # bypassed and a decided CHASE is applied directly without the
            # SLIDE-returning blend.  This kills the post-catch stall where
            # the whole line crept at DRIFT/SLIDE until bc.x passed the
            # anchor.
            # TACKLE-RADIUS CHASE OVERRIDE (2026-09-05): a genuine line
            # defender that has breached the tackle radius (dist(d, bc) <
            # tackle_radius_m) is committing to the tackle.  It must keep the
            # MMDL's CHASE -- the LINE GATE below must never demote it to
            # DRIFT (wrong-side drift into contact), and the direct-resolve
            # path must fire so the state is CHASE, not a blended
            # HOLD/RETREAT.  Fullback / marker / DH / TES tacklers keep their
            # dedicated owners (the MMDL returns their owned state, so
            # chase_override and next_state==CHASE never co-fire for them).
            _tackle_radius_chase = False
            if bc is not None and _is_genuine_line_defender(d):
                _tr = float(getattr(self.tuners, "tackle_radius_m", 1.5))
                if _tr > 0.0:
                    _tackle_radius_chase = math.hypot(
                        getattr(d, "x", 0.0) - bc.x,
                        getattr(d, "y", 0.0) - bc.y,
                    ) < _tr
            chase_override = (
                carrier_past
                or _in_post_catch_chase_window(world)
                or _tackle_radius_chase
            )

            # ── LINE DEFENDER GATE (2026-08-15, spec §1) ──
            # LINE defenders hold spacing/shape ONLY — the MMDL must never
            # route them into SHOOT/CHASE convergence (that would turn a
            # spacing defender into a pseudo-converger).  Their movement
            # state is forced to the non-pursuit fallback (HOLD/DRIFT line
            # settle), exactly like a PASSIVE holder.  EXCEPT once the BC
            # is clearly past the line (TASK VI) or the post-catch chase
            # window is armed: then the engaged line chases diagonally
            # instead of waiting for alignment.
            # STAGE 3E: the LINE-defender DRIFT demotion must never fire
            # for a dict-owned closer — the manager owns its closing vector
            # and DRIFT would abandon it.
            if (is_line_defender(d) and not chase_override and not _dict_owned
                    and not getattr(d, "_pursuit_commit_active", False)):
                next_state = MovementState.DRIFT

            if ((chase_override and next_state == MovementState.CHASE)
                    or _dict_owned or getattr(d, "_pursuit_commit_active", False)):
                # Direct resolve — skip the blend so a locked/sticky SLIDE
                # cannot survive into the pursuit.  STAGE 3E: dict-owned
                # closers take the same path.  decide_movement_state
                # returned CHASE, or SHOOT inside the collapse commit rail;
                # resolve() keeps the VALID_TRANSITIONS table honest —
                # SHOOT lands only when legal from the current state,
                # otherwise the convergence-aligned CHASE (legal from
                # every state) carries the closing intent.
                blended_state = resolve_movement_state(
                    d, next_state, fallback=MovementState.CHASE
                )
            else:
                # Hysteresis: blend from current toward next, then validate
                # the transition against VALID_TRANSITIONS.  CHASE/HOLD are
                # always legal fallbacks (both reachable from every state
                # in the table).
                blended_state = lerp_movement_state(current_state, next_state, t=0.5)
                fallback = MovementState.CHASE
                if getattr(d, "depth_class", "PASSIVE") not in ("PRIMARY", "SECONDARY"):
                    fallback = MovementState.HOLD
                blended_state = resolve_movement_state(d, blended_state, fallback)

            # ── FLAT-FOOTED MOVEMENT BLOCK (2026-09-02) ──
            # A defender resolved to HOLD/DRIFT (flat-footed) while the
            # ball carrier is clearly past the anchor must NOT stand dead
            # and miss the tackle.  Force the PLANT -> STAGGER -> STEP-
            # INTO-CONTACT chain: stamp footplanting (plant), seed a
            # step_stagger wave (stagger), redirect the target onto a BC-
            # forward closing point, and set CHASE so the defender steps
            # into contact.  Runs AFTER the LINE gate / blend so a legally
            # held LINE or deep-PASSIVE defender re-engages once the line
            # is broken.  Syncing last_decision keeps the reactivity audit
            # (BAD_DECISION_ON_LINE_BREAK) reading the real movement state
            # instead of the stale HOLD/DRIFT from the step-8 arbiter.
            blended_state = _apply_flatfoot_step_in(
                world,
                d,
                bc,
                blended_state,
                carrier_past,
                self._def_speed,
            )


            # ── TASK IV PATCH 1 (2026-08-14): PRE-TACKLE STUCK_NOT_FROZEN
            # FALLBACK.  The resolve step can legitimately land on HOLD (a
            # deep PASSIVE holder) or DRIFT (a line fix) — but if the
            # defender still carries a live target it is NOT parked on and
            # has zero TOTAL velocity, HOLD/DRIFT stalls it in place and the
            # freeze audit fires STUCK_NOT_FROZEN.  This LAST-RESORT assigns
            # RETREAT (PASSIVE) or SLIDE (engaged) so the state machine
            # always produces motion.  It is a no-op for pure-lateral movers
            # (vy != 0), parked defenders, frozen / TES / committed tackler /
            # fullback / marker / DH actors — never overrides a legal state.
            blended_state = resolve_pre_tackle_stuck(world, d, blended_state)

            # ── TASK I-B DECISION LOCK STAMP (2026-08-11) ──
            # Persist oscillating states for _DECISION_LOCK_FRAMES frames
            # unless a hard anchor-band crossing overrides (handled inside
            # decide_movement_state).  Kills frame-to-frame
            # retreat↔close↔slide flapping.
            from game.defence.defence_lvl_1.movement_mode_decision import (
                _DECISION_LOCK_FRAMES,
            )
            frame_now = getattr(world, "frame_count", 0)
            d._mmdl_state_until_frame = frame_now + _DECISION_LOCK_FRAMES

            # ── CHASE X-COLLAPSE DIAGNOSTIC PROBE (temporary, env-gated) ──
            # Stamp the current frame on the defender so the STATE physics
            # probe (apply_movement_state) can correlate with this MMDL
            # decision line.  No-op unless RLM_CHASE_PROBE=1.
            if _probe_on():
                d._probe_frame = frame_now

            # ── STALE-SEED FORCE-HOLD (2026-09-09) ──
            # A line body whose spacing-Y or depth-role seed is stale must
            # NOT be re-routed by the LINE gate / blend / flat-foot / stuck
            # fallback into a pursuit state — its geometry is undefined, so
            # any moving state would jitter it.  Force HOLD here so the
            # resolved state is HOLD (the post-writer apply_seed_hold in
            # DefenceManager.update_open_play is the final word).
            from game.defence.defence_lvl_1.seed_hold import (
                is_seed_stale as _is_seed_stale,
            )
            if _is_seed_stale(d, world):
                blended_state = MovementState.HOLD

            d.movement_state = blended_state
            # SYNC last_decision (2026-09-02): the reactivity audit
            # (BAD_DECISION_ON_LINE_BREAK) reads `last_decision`, which step 8
            # stamped from the LEGACY arbiter BEFORE the MMDL ran.  The MMDL is
            # the sole movement_state writer; without this sync the audit reads
            # a stale HOLD/DRIFT and falsely fires while the defender is already
            # CHASE (the "flat-footed scoring firing without the movement
            # behaviour" bug).  Mirror the real state so the audit reflects the
            # actual movement the PIPELINE produced.
            d.last_decision = getattr(blended_state, "name", None) or str(blended_state)
            d.target_source = f"mmdl_{blended_state.name}"

            # ── TASK I-B MMDL X-DRIFT GUARD (2026-08-11) ──
            # PASSIVE line holders in non-pursuit states consume a role-anchored
            # X (width / anchor) — they must NEVER consume a target far off the
            # anchor band (the "MMDL drifting 40m off anchor" bug).  PASSIVE
            # geometries (role X targets from WIDTH_OFFSETS) sit within 10m of
            # the anchor by construction; any raw target deviating beyond the
            # guard band is snapped back to the anchor.  Engaged closers
            # (PRIMARY/SECONDARY CHASE/SHOOT) are exempt — they legally pursue
            # the BC.  Markers/DH/tacklers are not part of the line-posture
            # pipeline and never enter this block.
            #
            # WIDTH LANE SKIP (2026-08-13): a PASSIVE WIDTH-LANE holder's X
            # IS the anchor-band geometry (ruck_x + WIDTH_OFFSETS[depth_role],
            # always <= 10 m forward of anchor) — the guard is provably a
            # no-op for it.  The skip is belt-and-suspenders: it guarantees a
            # lane holder's final X can never be re-pinned to anchor_x by this
            # drift clamp, keeping the width lane the final horizontal target
            # (width = X from WIDTH_OFFSETS).
            # ── CHASE X-COLLAPSE DIAGNOSTIC PROBE (temporary, env-gated) ──
            # Snapshot the X target before/after the drift guard plus the
            # anchor for every MMDL-managed defender (role/depth/band too).
            # No-op unless RLM_CHASE_PROBE=1.
            _tx_before_guard = d.target_x
            _snap_applied = False
            if (getattr(d, "depth_class", "PASSIVE") == "PASSIVE"
                    and getattr(blended_state, "name", None) in ("RETREAT", "DRIFT", "HOLD")
                    and lane_x_of(d, world, self.tuners) is None):
                anchor_x_guard = float(anchor_x)
                drift_band = 14.0  # PASSIVE line band is WIDTH_OFFSETS <= 10m + buffer
                if abs(d.target_x - anchor_x_guard) > drift_band:
                    d.target_x = anchor_x_guard
                    _snap_applied = True
            _probe_mmdl_chase(
                world,
                d,
                target_x_before_guard=_tx_before_guard,
                target_x_after_guard=d.target_x,
                anchor_x_guard=float(anchor_x),
                state_name=getattr(blended_state, "name", None) or str(blended_state),
                snap_applied=_snap_applied,
            )

            # ── TASK I-B MMDL CONSUMPTION STAMP ──
            # Record exactly what the MMDL consumed this frame so the movement
            # authority guard can prove spacing+width+anchor inputs fed the
            # physical output (pure-consumer contract).
            d.mmdl_target_x = d.target_x
            d.mmdl_target_y = d.target_y

            # -- TARGET STABILISER (geometry-signal smoothing) ----------
            # All geometry writers (governor, spacing, width curve, ROLE
            # depth, retreat compliance, step-up, structured close) have now
            # written target_x/target_y.  This is the FINAL merge point
            # before apply_movement_state() consumes them — stabilise here
            # so sub-threshold writer conflicts (a few cm/frame of lane vs
            # depth vs width vs retreat contention) cannot reach the
            # state-machine velocity model and produce visible jiggle.
            # Uses the same shared gate as Player.update() so fast-pursuit
            # defenders (CHASE/SHOOT) and TES/PTB actors are never smoothed.
            #
            # ── WIDTH-ELIGIBLE Y-INVARIANT (2026-08-17) ──
            # SPACING OWNS Y for lane holders: the writer-produced
            # target_y (step-7 split-spacing chain + the step-9/step-10
            # retreat/close writers drawn from the SAME chain) is the
            # frame's spacing-owned authority, immutable inside this
            # movement block.  The stabiliser WRITES target_y with its
            # exponential lerp; when the 3-point interpolated width
            # (width_left_tryline_m / width_halfway_m /
            # width_right_tryline_m by BC X) is expanding continuously
            # (BC advances, WINGER targets move outward METRES over the
            # run) and the defender stays close to the smoothed target,
            # alpha sits at the base 0.15 and the smoothed Y lags metres
            # behind the live spacing Y.  That lag is movement-mode
            # writing Y off the spacing authority — the exact
            # squash/collapse mechanism the MovementAuthorityGuard
            # invariant (2026-08-17) tripped on in the live engine:
            #   id=206 RETREAT target_y=62.568 vs spacing_y=59.293,
            #   deviation=3.275m.
            #   FIX: for width-eligible lane holders in the invariant
            #   states (RETREAT/DRIFT/HOLD) stabilise X ONLY and pass the
            #   spacing-owned Y through unchanged.  The spacing Y is
            #   smooth by construction in structured mode (single-writer:
            #   the split-spacing chain from the piecewise-linear
            #   interpolated width), so no jiggle returns, and the
            #   post-movement target_y equals the guard's spacing
            #   snapshot EXACTLY (deviation 0, invariant exact).
            from player.target_stabiliser import should_stabilise_target
            from game.defence.defence_lvl_1.movement_authority import (
                _Y_INVARIANT_STATES,
            )
            from game.defence.defence_lvl_1.width_authority import (
                is_width_eligible,
            )
            # ── TASK VII: FORCE-RESEED STABILISER BYPASS ──
            # A defender that was just force-reseeded (flipped side and
            # re-bootstraped as A on its new side) must NOT be blended by
            # the target_stabiliser — the fresh spacing chain Y is the
            # authoritative target this frame.  Clear the flag after use.
            reseed_bypass = getattr(d, "_force_reseed_this_frame", False)
            if reseed_bypass:
                d._force_reseed_this_frame = False

            if should_stabilise_target(d) and not reseed_bypass:
                stab = d._get_target_stabiliser()
                _st_ms_name = (
                    getattr(blended_state, "name", None) or str(blended_state)
                )
                # POST-RETREAT GATE (2026-09-02): during the first two
                # OPEN_PLAY frames force the X-only branch so the
                # width-eligibility dual-branch never overrides the retreat
                # spacing Y — the spacing Y is authoritative until the line
                # settles.
                if (
                    is_width_eligible(d)
                    and (
                        _st_ms_name in _Y_INVARIANT_STATES
                        or getattr(world, "_post_retreat_open_play_frames", 99) <= 2
                    )
                ):
                    # Stabilise X only — the spacing-owned Y passes
                    # through untouched (immutable movement-consumer
                    # input, per the width-eligible Y-invariant).
                    stable_x, _ = stab.stabilise(
                        d.target_x, d.target_y, d.x, d.y
                    )
                    d.target_x = stable_x
                    # TARGET-WRITE STAMP (2026-09-02): attribute the
                    # stabiliser's X-only write so the jitter audit holds the
                    # stabiliser responsible for any post-retreat X jiggle.
                    stamp_target_write(world, d, "stab_stabilise",
                                       detail="stab_x_only")
                else:
                    # DUAL BRANCH — stabilises BOTH axes, which overrides the
                    # authoritative spacing Y after retreat.  This is the
                    # residual ±16-flip / short-side MK-B offset writer; stamp
                    # it distinctly so the audit attributes the flip to it.
                    d.target_x, d.target_y = stab.stabilise(
                        d.target_x, d.target_y, d.x, d.y
                    )
                    stamp_target_write(world, d, "stab_stabilise",
                                       detail="stab_dual")

            # Apply movement-state physics toward the STABLE target produced
            # by the finalised writers above (never recomputed here).
            apply_movement_state(
                d,
                blended_state,
                d.target_x,
                d.target_y,
                dt,
                bc_x=ball_x,
                bc_y=ball_y,
                # ITEM 3 (2026-08-15): feed the BC lateral velocity so
                # apply_movement_state can damp the lateral correction
                # when the carrier is stepping sideways fast
                # (|bc_vy| > 1.0 m/s -> x0.60).
                bc_vy=getattr(bc, "vy", 0.0),
            )

            # ── SPEED-CONTRACT RE-CLAMP ──
            # apply_movement_state derives target_speed from the per-player
            # physical max × state scale, which can exceed the defensive
            # writers' move_speed, the post-catch reaction governor, the
            # post-catch line clamp, and the unified ceiling (all normally
            # enforced inside Player.update).  Since OPE skips Player.update
            # for _mmdl_moved defenders, re-apply the same caps here so no
            # speed contract is punctured.
            speed_ceiling = getattr(d, "move_speed", d.max_speed_mps) or d.max_speed_mps
            if getattr(d, "defence_reaction_remaining_s", 0.0) > 0.0:
                speed_ceiling = min(speed_ceiling, d._reaction_speed_cap(speed_ceiling))
            # TASK VI (2026-08-15): engaged CHASE/SHOOT pursuit skips the
            # post-catch defensive-line clamp here too (symmetric with the
            # Player.update / _accelerate_toward_target bypass) — the clamp
            # is an alignment governor and must not throttle the diagonal
            # chase once the BC is clearly past the line.
            if getattr(d, "defended_cap_remaining_s", 0.0) > 0.0 and not d._is_chase_state():
                speed_ceiling = min(speed_ceiling, d._defended_cap_ceiling(speed_ceiling))
            if getattr(d, "max_defensive_speed_mps", 0.0) > 0.0:
                speed_ceiling = min(speed_ceiling, d.max_defensive_speed_mps)
            current_speed = math.hypot(d.vx, d.vy)
            if current_speed > speed_ceiling:
                scale = speed_ceiling / current_speed
                d.vx *= scale
                d.vy *= scale

            # Flag for OpenPlayEngine: position already integrated this frame.
            d._mmdl_moved = True

        # ── TASK I-B MMDL AUTHORITY-SAFETY (2026-08-11) ──
        # The MMDL consumed spacing (Y) + width (X) + anchor (X) as a
        # PURE CONSUMER.  The guard asserts no authority field was mutated
        # during the block — spacing/width/anchor/chase/depth-role stamps
        # are byte-identical before vs after.  Raises
        # MovementAuthorityViolation on any mutation.
        _mmdl_guard.verify()

        # -----------------------------------------------------------
        # 11. PRE-TACKLE TRIGGER (tackle roles already assigned in 10a1)
        #
        # Tackle roles were assigned BEFORE the MMDL block (10a1) so the
        # commit writer could drive the approach.  This trigger uses those
        # SAME roles: the closest PRIMARY defender starts TES; close
        # SECONDARY/THIRD defenders (TES-gating concepts / assist-tackle
        # continuity) register as joiners.  LINE/PASSIVE defenders never
        # enter — the lane system normalised them away in 10a1.
        #
        # ── PRE-TACKLE STANCE (2026-08-15, spec §3) ──
        # PRIMARY lane tacklers inside the close-range stance radius plant
        # FIRST (stop, square up, face BC), then the trigger evaluates the
        # planted defender.  This makes the pre-contact stance visible on
        # the contact frame and hands the outcome to TES.
        # -----------------------------------------------------------
        eligible = [d for d in sorted_defs if not d.frozen]
        # -- FULLBACK TACKLE-CANDIDACY (B2, 2026-09-16) --
        # Same law as the lane pool above: a tackle-capable fullback joins
        # the pre-tackle SELECTION pool (stance / canonical pre_tackle_state /
        # primary selection / joiner scan) while keeping its line-shape
        # exclusion.
        fullback_candidates = _fullback_tackle_candidates(
            defenders, eligible, world, bc,
        )
        apply_pre_tackle_stance(world, eligible, bc)
        # UNIFIED PRE-TACKLE AUTHORITY (2026-09-04): publish the single
        # canonical pre-contact state so movement and TES read ONE state.
        _publish_pre_tackle_state(world, eligible, bc, self.tuners)


        bcx = bc.x if bc is not None else 60.0
        bcy = bc.y if bc is not None else 35.0
        # ── PRIMARY SELECTION (2026-09-09): shared classifier predicate ──
        # See the pursuit-mode site above for the single-source rationale.
        engage_winner = _try_primary_then_fullback(world, eligible, fullback_candidates, bc, self.tuners)
        secondary_joiners = []
        for d in eligible:
            tackle_role = getattr(d, "tackle_role", None)
            if tackle_role not in ("SECONDARY", "THIRD"):
                continue
            dist = math.hypot(d.x - bcx, d.y - bcy)
            if dist <= 2.5:
                secondary_joiners.append((dist, d))

        if engage_winner is not None and bc is not None:
            if not getattr(world, "tes_active", False):
                ope = world.open_play_engine
                # STAGE 2A: preserve the complete formation state (see the
                # pursuit site above).  Pure data argument only.
                _enter_tes(world, ope, bc, engage_winner, _CH_STRUCTURED,
                           defenders=defenders)
                engage_winner.target_x = engage_winner.x
                engage_winner.target_y = engage_winner.y
                engage_winner.move_speed = 0.0
                engage_winner.frozen = True
                if ope.tes_ctx is not None:
                    from game.tes.tes_context import PlayerContext
                    max_add = ope.tes_ctx.max_joiners - len(ope.tes_ctx.engaged_tacklers)
                    added = 0
                    for _, sd in sorted(secondary_joiners, key=lambda x: x[0]):
                        if sd is engage_winner:
                            continue
                        if added >= max_add:
                            break
                        # ARM THE JOINER ATOMICALLY (contract A/B, 2026-08-08):
                        # ope._arm_tackler_role() sets movement_mode = TACKLER,
                        # collision_priority = HIGH, and registers the joiner in
                        # ctx.tackler_roles — so _update_tackler_movement() drives
                        # them into contact and _clear_tes_artefacts() exits them
                        # to RETREAT at RELEASE.  Stamping is_tackler/in_contact
                        # alone left movement_mode = None (root cause #7): the
                        # joiner never retreated, stayed wrong-side of the ruck,
                        # blocked the PTB bubble, and stalled the PTB timer.
                        if len(ope.tes_ctx.engaged_tacklers) == 1:
                            ope._arm_tackler_role(sd, DefenderRole.TACKLER_2, "TACKLER_2", ope.tes_ctx)
                        elif len(ope.tes_ctx.engaged_tacklers) == 2:
                            ope._arm_tackler_role(sd, DefenderRole.TACKLER_3, "TACKLER_3", ope.tes_ctx)
                        # CONTACT-CONTRACT HANDOFF (2026-09-15): the joiner
                        # inherits the PRE-TACKLE CORRIDOR's governed
                        # approach velocity rather than whatever another
                        # writer left on vx/vy before TACKLE_INIT.  Returns
                        # the live vx/vy when the body carries no corridor
                        # stamp or the handoff gate is off, so a
                        # non-corridor joiner is byte-identical.
                        _jvx, _jvy = _corridor_contact_velocity(sd, self.tuners)
                        sd_ctx = PlayerContext()
                        sd_ctx.player_id = sd.player_id
                        sd_ctx.x_m = sd.x
                        sd_ctx.y_m = sd.y
                        sd_ctx.vx = _jvx
                        sd_ctx.vy = _jvy
                        sd_ctx.mass = getattr(sd, "mass_kg", 90.0)
                        sd_ctx.stamina = getattr(sd, "stamina", 1.0)
                        sd_ctx.aggression = getattr(sd, "aggression", 0.5)
                        sd_ctx.tackle_skill = getattr(sd, "tackle_skill", 0.5)
                        sd_ctx.strength = getattr(sd, "strength", 0.5)
                        sd_ctx.facing = sd.facing
                        ope.tes_ctx.engagement.add(sd_ctx, world.sim_time_s)
                        ope.joiners.append(sd)
                        sd.target_x = sd.x
                        sd.target_y = sd.y
                        sd.move_speed = 0.0
                        sd.frozen = True
                        sd.tes_locked = True
                        sd.is_tackler = True
                        sd.in_contact = True
                        added += 1

    # -----------------------------------------------------------
    # OPEN_PLAY - Unified behaviour (geometry completely bypassed)
    # -----------------------------------------------------------

    def _apply_open_play_unified(self, world, defenders):
        """Apply OPEN_PLAY defensive behaviour - GEOMETRY COMPLETELY BYPASSED.

        AI_ENGINE_FIX: All defender positioning uses role-driven geometry.
        No bc.x, bc.y, fr.x, or fr.y is used for any defensive target
        except the PRIMARY/SECONDARY close writer.

        This method does NOT accept base_line_x or lane_y.  It has no
        access to ruck_x, def_line_depth_m, spacing, width_curve, or
        any geometry outputs.
        """

        # TARGET SOURCE DIAGNOSTIC - REMOVED (was drowning BC teleport trace; 2026-07-21 cleanup)

        # -- LINE ROLE STRIP + UNFREEZE SWEEP --
        for d in defenders:
            if getattr(d, "defender_role", None) == DefenderRole.LINE:
                d.defender_role = None
            if getattr(d, "defender_role", None) == DefenderRole.KICK_CHASE:
                continue
            if _is_marker(d) and getattr(d, "freeze_timer", 0.0) > 0:
                continue
            d.frozen = False
            d.freeze_timer = 0.0

        # -- BALL CARRIER NONE GUARD --
        if world.ball_carrier is None:
            for d in defenders:
                d.vx = 0.0
                d.vy = 0.0
                d.target_x = d.x
                d.target_y = d.y
                d.target_source = "fallback"
            return

        # -----------------------------------------------------------
        # FULLBACK REASSERTION — POST-TACKLE / PTB / RETREAT PIPELINE
        # (2026-08-12)
        # -----------------------------------------------------------
        # The retreat pipeline (this method, reached on every PTB_READY /
        # PTB / POST_PTB_RESET / POST_RUCK_OPEN_PLAY frame after a tackle)
        # cleared roles and rebuilt the defensive line but never re-asserted
        # fullback status.  OpenPlayEngine._clear_tes_artefacts() zeroes
        # ``fullback_role_active`` for every player at RELEASE entry, so the
        # actor fell into the depth/spacing/width/tackle pools and froze.
        #
        # This mirrors the structured OPEN_PLAY assignment exactly
        # (fullback_role.assign_fullback_role + update_fullback_absorb_state):
        #   - Runs BEFORE every line writer so the actor's anchor target is
        #     locked in first.
        #   - The actor is then REMOVED from the line pool fed to every
        #     shaping step below (force reaction, classify_depth_all,
        #     assign_depth_roles, timing gates, retreat compliance), so no
        #     line writer freezes it or drags it off the anchor.
        #   - fullback_role_active is ONLY cleared by absorption (genuine
        #     both-axis proximity) - NEVER by retreat.
        #   - Absorption is OPEN_PLAY-only; during PTB/retreat the hysteresis
        #     counters refresh but the role never dissolves.
        bc = world.ball_carrier
        # TASK VI: the fullback anchor consumes the ANCHOR AUTHORITY
        # (ruck_x + 10) — never a bare field read or ruck_x + 10 fallback.
        anchor_x = _anchor_m(world)
        fb_actor, (fb_tx, fb_ty) = assign_fullback_role(defenders, bc, anchor_x, world=world)

        if fb_actor is not None:
            lane_y = getattr(bc, "y", None)
            if lane_y is None:
                lane_y = DefensiveBehaviourUnified._get_ball_y(world)
            update_fullback_absorb_state(fb_actor, anchor_x, lane_y)
            # POST-CATCH ABSORB GRACE (2026-08-28): same suppression as the
            # open-play site — the kickoff FB is transiently at the line
            # anchor right after reception; withhold the OPEN_PLAY dissolve
            # while the armed grace window is burning down.
            _fb_grace = getattr(world, "fb_post_catch_absorb_grace_frames", 0)
            if (world.sub_state == GameSubState.OPEN_PLAY
                    and fb_actor.fullback_absorb_allowed
                    and _fb_grace > 0):
                print(
                    f"[FB_TELEMETRY] ABSORB_SUPPRESSED post_catch_grace_frames={_fb_grace} "
                    f"fb_id={getattr(fb_actor, 'player_id', '?')} "
                    f"fb_x={fb_actor.x:.2f} anchor_x={anchor_x:.2f}"
                )
            elif (world.sub_state == GameSubState.OPEN_PLAY
                    and fb_actor.fullback_absorb_allowed
                    and not _is_fb_emergency_engaged(fb_actor)):
                # ABSORPTION (open-play only): set fullback_role_active =
                # False and let assign_depth_roles assign the line role
                # deterministically.
                #
                # EMERGENCY TAG HOLD (B2b, 2026-09-16): NEVER dissolve while
                # the actor is engaged in an emergency tackle.  The whole
                # point of the emergency path is that the last defender
                # behaves like a normal defender on a break WITHOUT being
                # absorbed into the defensive line; dissolving here would
                # hand it a depth role mid-tackle.
                fb_actor.fullback_role_active = False
                if getattr(fb_actor, "role", None) == "FULLBACK":
                    fb_actor.role = None
                fb_actor.target_source = None
                fb_actor.depth_class = "PASSIVE"
                fb_actor = None

        if fb_actor is not None:
            fb_actor.depth_class = "PASSIVE"
            # ── SINGLE FULLBACK TARGET OWNER (2026-09-28) ──
            # Second site of the same defect as the structured-OPEN_PLAY pin
            # above: while a genuine line break is live the breakaway chase
            # owns target_x, movement_state and the source stamp, so this
            # static anchor pin must not run.  See the first site for the
            # full measured note.
            from game.defence.defence_lvl_1.fullback_controller import (
                FullbackIntent as _FbIntent,
                SOURCE_ANCHOR as _FB_SOURCE_ANCHOR,
                enter_fullback_intent as _fb_enter,
                shared_policy_state_allowed as _fb_shared_policy_enabled,
            )
            from game.defence.defence_lvl_1.fullback_role import (
                fullback_breakaway_live as _fb_breakaway_live,
            )
            # ── STAGE 2 — STATE OWNERSHIP (2026-09-30) ──
            # Second site of the same law as the structured-OPEN_PLAY pin
            # above: the shared state policy yields to the controller for as
            # long as a break emergency is live.  See that site for the full
            # note -- the gate is the controller's own authority predicate.
            if (
                _fb_breakaway_live(world, fb_actor)
                or not _fb_shared_policy_enabled(world, fb_actor)
            ):
                pass
            else:
                # SPEED AUTHORITY REMOVED (2026-09-27): see the kickoff-catch
                # branch above -- the sweeper pace is now the shared
                # movement-state table's, not a bespoke fullback write.
                # The STATE goes through the ONE write path (SWEEP -> the
                # identical DRIFT singleton the literal write used).
                _fb_enter(
                    world, fb_actor, _FbIntent.SWEEP, source=_FB_SOURCE_ANCHOR
                )
                fb_actor.target_source = "fullback"
                fb_actor.target_x = anchor_x
                # target_y was set to the ball-lateral anchor by
                # assign_fullback_role; no line writer touches it because the
                # actor is excluded from line_defenders below.

        # Line defenders = all non-fullback defenders (the actor re-enters
        # this frame if absorbed above).  The fullback is independent and
        # must never occupy a line slot in the depth/spacing/width/tackle
        # pools, so every post-tackle writer consumes line_defenders.
        line_defenders = [
            d for d in defenders
            if not getattr(d, "fullback_role_active", False)
        ]

        # -- HARD OVERRIDE: immediate chase when BC is at or past anchor --
        self._force_immediate_defence_reaction(world, line_defenders)

        # -----------------------------------------------------------
        # ROLE GOVERNANCE OVERRIDE - bypasses everything during
        # KICKOFF / KICK_RETURN / BALL_CAUGHT transition.
        # -----------------------------------------------------------
        if getattr(world, 'role_governs', False):
            bc = world.ball_carrier
            if bc is not None:
                # Replace per-defender threshold classification with ranked selection.
                # Guarantees exactly 1 PRIMARY, 2 SECONDARY, rest PASSIVE.
                # FULLBACK EXCLUSION: the actor is never part of the line pool.
                self.classify_depth_all(world, line_defenders)
                self.def_role_spacing.prepare(world)
                # ROLE-DRIVEN GEOMETRY (2026-08-09): targets flow from the
                # depth role tables (WIDTH_OFFSETS x, role width anchor y) —
                # no lane-index ordering, no get_defence_target relabelling.
                role_governs_targets = {}
                if geometry_active(world):
                    role_governs_targets = self.def_geometry.compute_role_targets(world, line_defenders)
                for d in line_defenders:
                    d.frozen = False
                    d.timing_locked = False
                    # GLOBAL DEFENSIVE TOP-SPEED CEILING: role-governance
                    # writer derives from the unified ceiling.
                    d.move_speed = self._def_speed(d)
                    rt = role_governs_targets.get(id(d))
                    if rt is not None:
                        d.target_x, d.target_y = rt
                    else:
                        d.target_x, d.target_y = d.x, d.y
                    d.target_source = "role_governs"
            return

        bc = world.ball_carrier
        ruck_x = getattr(world, "ruck_x_m", 60.0)

        # -- STEP 1 - depth roles (2026-08-09): no lane_index, no Y-sort --
        # Defensive geometry is driven by depth roles only.  lane_index is
        # computed LATER, immediately before select_tacklers(), solely for
        # tackle-EDGE assignment — it never orders geometry or Y targets.
        # The fullback actor is already excluded via line_defenders.
        # TASK 2 (2026-08-14) — SINGLE-ASSIGN-PER-FRAME: the un-gated
        # preamble already assigned + published spacing from these roles
        # this frame.  Guarded so this unified-mode consumer never re-runs
        # assign_depth_roles and invalidates the fresh frame spacing dict.
        if not _depth_roles_assigned_this_frame(world):
            assign_depth_roles(line_defenders, world)

        anchor_x = world.defensive_line_anchor
        compliance_dist = self.tuners.retreat_compliance_distance_m

        # FIXED ORDER: classify ALL defenders BEFORE retreat split.
        self.classify_depth_all(world, line_defenders)

        # Clear timing flags on reclassified defenders
        for d in line_defenders:
            prev_class = getattr(d, "_prev_depth_class", None)
            new_class = getattr(d, "depth_class", "PASSIVE")
            if prev_class is not None and prev_class != new_class:
                d.reaction_timer = 0.0
                d.retreat_delay = 0.0
                d.lateral_hesitation = 0.0
                d.step_stagger = 0.0
                d.step_stagger_active = False
                d.timing_locked = False
            d._prev_depth_class = new_class

        # -----------------------------------------------------------
        # -----------------------------------------------------------
# RETREAT COMPLIANCE - runs AFTER classification
# STAGE 3B: delegated to the DefenceManager-owned RetreatManager
# (game/managers/retreat_manager.py).  Sole forced-retreat writer:
#   X <- anchor authority, Y <- spacing authority (frame dict),
#   TES freeze contracts enforced (frozen players never move).
# -----------------------------------------------------------
        compliant, _non_compliant = apply_forced_retreat(
            world, line_defenders, self.tuners,
            governor=self.line_governor,
        )

# Now process only compliant defenders
        # -----------------------------------------------------------

        # -- STEP 2 - run timing on compliant --
        dt = getattr(world, "dt", 1.0 / 60.0)
        self.def_timing.update(world, compliant, dt)

        # -- PRIORITY GATES --
        for idx, d in enumerate(compliant):
            # Gate 1 - markers with active freeze_timer
            if _is_marker(d) and getattr(d, "freeze_timer", 0.0) > 0:
                # MARKER AUTHORITY LOCK (2026-09-04): marker target = the
                # single ruck-pin geometry authority (ruck_x + depth, ruck_y);
                # freeze is VELOCITY-ONLY (frozen=True), never d.x/d.y.
                from game.defence.defence_lvl_1.marker_authority import (
                    marker_geometry_target as _mt,
                )
                d.target_x, d.target_y = _mt(world, d)
                d.frozen = True
                d.target_source = "marker_frozen"
                continue

            # Gate 2 - freeze_timer active (non-marker)
            if getattr(d, "freeze_timer", 0.0) > 0:
                d.frozen = True
                d.target_x = d.x
                d.target_y = d.y
                d.target_source = "freeze_timer"
                continue

            # Gate 3 - timing_locked - PRIMARY/SECONDARY override it
            if getattr(d, "timing_locked", False):
                depth_class = getattr(d, "depth_class", "PASSIVE")
                if depth_class in ("PRIMARY", "SECONDARY"):
                    d.timing_locked = False
                    d.retreat_delay = 0.0
                    d.lateral_hesitation = 0.0
                else:
                    d.target_x = d.x
                    d.target_y = d.y
                    d.target_source = "timing_locked"
                    d.move_speed = 0.0
                    d.frozen = True
                    continue

        # -----------------------------------------------------------
        # DYNAMIC DEPTH-ROLE REFIT (TASK X) + REBANDING (TASK IX)
        # -----------------------------------------------------------
        # Immediately AFTER Stage-3F timing stabilisation (def_timing.update
        # above) and BEFORE any tackler selection / _start_tackle later in
        # this method:
        #   - TASK X: at every NEW ruck pivot, refit depth roles FROM
        #     SCRATCH off ball-side + ruck context + SETTLED positions
        #     (kills the static-role inheritance that corrupted
        #     CENTRE/WINGER and middle/edge lanes), then
        #   - TASK IX: enforce the intra-ruck slot invariants on the
        #     whole line.
        # On either change, republish the frame geometry so
        # frame_spacing_targets matches the corrected roles THIS frame
        # (Task 2 single-source law).
        _dynamic_refit = _apply_dynamic_depth_roles(world, line_defenders)
        if _dynamic_refit or _reband_after_stabilisation(world, line_defenders):
            from game.defence.defence_lvl_1.defensive_pipeline import (
                run_defensive_pipeline as _republish_geometry,
            )
            _republish_geometry(
                world, list(line_defenders), self.tuners,
                governor=self.line_governor,
            )

        # -----------------------------------------------------------
        # DEFENSIVE LINE GOVERNOR - shared line state
        # -----------------------------------------------------------
        line_state = self.line_governor.compute_line_state(world, compliant)

        eligible = [
            d for d in compliant
            if not d.frozen and not getattr(d, "timing_locked", False)
        ]

        # Apply geometry targets — ROLE-DRIVEN (2026-08-09), no lane_index.
        # Geometry movement is enabled ONLY during OPEN_PLAY
        # (geometry_active() gate).  In RELEASE / PTB phases role targets
        # are NOT written — those phase handlers own the targets.
        role_targets = {}
        if geometry_active(world):
            role_targets = self.def_geometry.compute_role_targets(world, eligible)
        for d in eligible:
            rt = role_targets.get(id(d))
            if rt is not None:
                tx, ty = rt

                # Y smoothing on unlock
                prev_ty = getattr(d, "_prev_target_y_smooth", d.y)
                smooth_factor = 0.8
                ty = prev_ty + (ty - prev_ty) * smooth_factor
                d._prev_target_y_smooth = ty

                d.target_x = tx
                d.target_y = ty
                stamp_target_write(world, d, "role_spacing", detail="compute_role_spacing")
            else:
                # Geometry disabled (non-OPEN_PLAY) or defender not in the
                # role set: hold position.
                d.target_x = d.x
                d.target_y = d.y

            d.move_speed = line_state["speed_mps"]
            d.depth_class = getattr(d, "depth_class", "PASSIVE")

        # UNIFIED MODE CLOSE: BC-relative targets for PRIMARY/SECONDARY
        # INTERCEPT LEAD (2026-08-06): aim at the point the BC will
        # occupy when the defender arrives so closers cut IN FRONT of
        # the BC instead of converging on its current position.
        bc_local = world.ball_carrier
        if bc_local is not None:
            ruck_x_local = getattr(world, "ruck_x_m", 60.0)
            ruck_y_local = getattr(world, "ruck_y_m", 35.0)
            for d in eligible:
                if not isinstance(d, Player):
                    continue
                dc = getattr(d, "depth_class", "PASSIVE")
                if dc not in ("PRIMARY", "SECONDARY"):
                    continue
                if d.frozen or getattr(d, "freeze_timer", 0.0) > 0:
                    continue

                if dc == "PRIMARY":
                    offset_x = -1.0
                    offset_y = 0.0
                else:
                    offset_x = -2.0
                    # ROLE-LOCKED LATERAL OFFSET (2026-08-09): no lane_index,
                    # no live d.y<ruck_y sign.  SECONDARY closers spread Y by
                    # their PERSISTENT depth_side (Â±0.5m per side).  The
                    # hysteresis-protected depth_side_of() helper returns the
                    # persistent depth_side / dead-banded fallback (2026-08-29),
                    # so a tiny Y-jiggle at the ruck line never flips the side.
                    side = depth_side_of(d, ruck_y_local)
                    offset_y = -0.5 if side == -1 else 0.5

                ictx, _icty = compute_intercept_target(d, bc_local, t_max=0.8)

                # Onside clamp: never target in front of ruck back-foot
                onside_x = ruck_x_local + 1.0
                d.target_x = max(ictx + offset_x, onside_x)
                # SPACING Y (Task F, 2026-08-10): intercept may override X
                # ONLY — Y stays spacing_targets[player].y (no icty, no
                # BC-relative chase Y, no lane-index Y).
                # 2026-08-12: consumed from the un-gated frame preamble.
                d.target_y = _frame_spacing_y(world, d, self.tuners,
                                              governor=self.line_governor)
                stamp_target_write(world, d, "spacing")
                d.target_source = "unified_close"

        # -----------------------------------------------------------
        # DECISION LAYER - per-defender behaviour arbitration
        # -----------------------------------------------------------
        # TASK VII (2026-08-20) — FULL GEOMETRY AUTHORITY MIGRATION.
        # The legacy decision arbiter previously wrote BOTH target_x and
        # target_y from get_targets_for_decision() — a non-authority
        # geometry writer that contradicted this module's own docstring
        # ("decision layer affects speed/movement_state only, NOT
        # targets") and the TASK III Defect-B fix (scoped to structured
        # mode only).  It bypassed SpacingGuard / WidthGuard /
        # MovementAuthorityGuard and could pull defenders off the spacing
        # chain / width lane.
        #
        # The decision layer is now SPEED-ONLY + narrow HOLD/RETREAT PIN:
        #   - Y: ALWAYS the frame spacing authority (`_frame_spacing_y`),
        #     exactly like every other unified writer.
        #   - X: the structured/width target as written by the geometry
        #     writer above (never re-derived here).  RETREAT additionally
        #     enforces the anchor compliance clamp — a genuinely over-
        #     compliant forward target is pinned back to the anchor
        #     (mirrors `get_targets_for_decision` RETREAT semantics); the
        #     anchor itself comes from the authority.
        #   - HOLD: pins target to current position (velocity zero) —
        #     the only other legal target write.
        #   - movement_state: resolved from the MAP so the render/freeze
        #     audits see the decision state; MMDL (structured mode) or
        #     Player.update (unified mode) integrate it.
        for d in eligible:
            depth_class = getattr(d, "depth_class", "PASSIVE")
            is_marker = _is_marker(d)
            freeze_timer = getattr(d, "freeze_timer", 0.0)
            timing_locked = getattr(d, "timing_locked", False)
            retreat_compliant = True

            decision = ddecision(
                defender=d,
                bc=bc,
                ruck_x=ruck_x,
                anchor_x=anchor_x,
                depth_class=depth_class,
                is_marker=is_marker,
                timing_locked=timing_locked,
                freeze_timer=freeze_timer,
                retreat_compliant=retreat_compliant,
            )

            # Speed scale from the decision map (single authority source:
            # the same table the structured pipeline's step 8 uses).
            speed_scale = {
                Decision.HOLD: 0.0,
                Decision.SHOOT: 1.0,
                Decision.CHASE: 0.9,
                Decision.COMPRESS: 0.7,
                Decision.SLIDE: 0.6,
                Decision.DRIFT: 0.4,
                Decision.RETREAT: 0.7,
            }.get(decision, 0.5)

            # ── TARGET AUTHORITY ──
            # Y always stays the frame spacing authority.  X keeps the
            # geometry/width target already written this frame (never
            # re-derived from the decision); only HOLD (pin to current)
            # and RETREAT (anchor compliance clamp) may rewrite X.
            # GEOMETRY AUTHORITY LOCK (2026-09-04): HOLD is a VELOCITY-ONLY
            # suppression — the geometry target (spacing Y / width X) already
            # written above stays the authority, never redefined to d.x/d.y.
            # Player._accelerate_toward_target halts on move_speed <= 0 for a
            # defensive-ceiling carrier, so holding is expressed by speed=0.
            if decision != Decision.HOLD:
                d.target_y = _frame_spacing_y(
                    world, d, self.tuners, governor=self.line_governor
                )
                stamp_target_write(world, d, "spacing")
            # RETREAT: pin an over-compliant forward target back to the
            # anchored line (authority read, mirrors get_targets_for_decision).
            if decision == Decision.RETREAT:
                d.target_x = max(d.target_x, _anchor_m(world))

            # GLOBAL DEFENSIVE TOP-SPEED CEILING: decision speed scales the
            # unified ceiling, never the raw per-player max.
            d.move_speed = self._def_speed(d) * speed_scale
            d.target_source = f"decision_{decision.name}"
            d.movement_state = select_movement_state(decision)

        # Fix B - Force RETREAT for PASSIVE non-compliant defenders
        ruck_x_actual = getattr(world, "ruck_x_m", 60.0)
        compliance_dist_actual = self.tuners.retreat_compliance_distance_m
        for d in eligible:
            if not isinstance(d, Player):
                continue
            # FULLBACK EXEMPTION (2026-08-17): the fullback never enters
            # RETREAT — geometry-driven only (belt-and-braces mirror of the
            # step-9 / forced-retreat guards upstream).
            if getattr(d, "fullback_role_active", False):
                continue
            if getattr(d, "depth_class", "PASSIVE") != "PASSIVE":
                continue
            if d.x < ruck_x_actual + compliance_dist_actual:
                d.target_x = _anchor_m(world)
                d.movement_state = MovementState.RETREAT
                d.target_source = "retreat_non_compliant"
                # SPEC 4.2 / 4.3 (2026-08-04): retreat speed derives from
                # the governor's shared line speed × 0.85 — never the
                # per-defender ceiling.  Final clamp is still the unified
                # ceiling.
                d.move_speed = self.line_governor.retreat_speed_mps()
                d.frozen = False
                bc_local = world.ball_carrier
                if bc_local is not None:
                    angle_to_bc = math.atan2(bc_local.y - d.y, bc_local.x - d.x)
                    d.set_facing(angle_to_bc)

        # FINALISE PASS — blend + cap all lateral targets after every
        # unified writer (geometry, unified_close, decisions, retreat).
        self._finalise_lateral_targets(world, eligible)

        # ── TASK G ANCHOR AUTHORITY (Layer 7 + Layer 8) ──
        # Every line-holding defender ahead of the anchor is re-targeted
        # back to anchor_x (never pulled forward by spacing/width/audit),
        # then the depth-role stamps are checked for drift > 0.01 m.
        enforce_retreat_to_anchor(world, eligible)
        assert_anchor_consistency(world, eligible)

        # TACKLE ROLE ASSIGNMENT — INTERCEPT LANE OWNERSHIP (2026-08-15)
        # LANE INDICES — TACKLE-EDGE ONLY (2026-08-09).  lane_index is
        # computed HERE purely for debug labelling.  The per-side lane
        # system (intercept_tacklers) is the tackle-role authority:
        # PRIMARY per side owns convergence, everything else LINE.
        self._compute_lane_indices(world, eligible)
        self.select_tacklers(world, eligible)
        self.select_lane_tacklers(world, eligible, bc)
        for d in eligible:
            tr = getattr(d, "tackle_role", "PASSIVE")
            if tr not in ("PRIMARY", "SECONDARY"):
                d.tackle_role = "PASSIVE"

        # ── TACKLER-COMMIT OVERRIDE (2026-08-13) ──
        # PRIMARY/SECONDARY tacklers receive their dedicated BC-forward
        # collision target AFTER every unified-mode writer (geometry,
        # unified_close, decisions, retreat, finalise, anchor) so the
        # commit wins this frame.  The pre-tackle trigger below then uses
        # the SAME roles — no re-assignment.
        _apply_tackler_commit(world, eligible, self.tuners)
        # ── 12M PURSUIT/COMMIT HARD OVERRIDE (2026-09-02) ──
        # Same override for the unified path: LINE/PASSIVE defenders within
        # pursuit_commit_radius_m of the BC get BC-relative pursuit targets
        # + CHASE so the whole line closes instead of holding spacing.
        self._apply_pursuit_commit_override(world, eligible)

        # ── PRE-TACKLE STANCE (2026-08-15, spec §3) ──
        # PRIMARY lane tacklers inside the stance radius plant: zero
        # velocity, target pinned, facing locked to the BC, HOLD state.
        # They stay planted until TES (or the carrier breaking free).
        apply_pre_tackle_stance(world, eligible, bc)
        # UNIFIED PRE-TACKLE AUTHORITY (2026-09-04): publish the single
        # canonical pre-contact state so movement and TES read ONE state.
        _publish_pre_tackle_state(world, eligible, bc, self.tuners)
        # ── STAGE 4: UNIFIED PRE-TACKLE MOVEMENT REBUILD (2026-09-04) ──
        # The unified authority authors movement for managed bodies
        # (COMMIT/PLANTED/SLOW/HOLD) so movement and TES read ONE canonical
        # state -- no more post-hoc CHASE/HOLD/DRIFT that diverges from the
        # pre-tackle state.  _mmdl_moved=True makes DefenceManager's
        # Player.update loop skip these bodies (no double-integration).
        _pta_dt = getattr(world, "dt", 1.0 / 60.0)
        apply_pre_tackle(world, eligible, _pta_dt, self.tuners, frozen_mode=False)

        fullback_candidates = _fullback_tackle_candidates(
            defenders, eligible, world, bc,
        )

        # PRE-TACKLE TRIGGER (multi-tackler)
        bcx = bc.x if bc is not None else 60.0
        bcy = bc.y if bc is not None else 35.0
        # ── PRIMARY SELECTION (2026-09-09): shared classifier predicate ──
        # See the pursuit-mode site above for the single-source rationale.
        engage_winner = _try_primary_then_fullback(
            world, eligible, fullback_candidates, bc, self.tuners,
        )
        secondary_joiners = []
        for d in eligible:
            tackle_role = getattr(d, "tackle_role", None)
            if tackle_role not in ("SECONDARY", "THIRD"):
                continue
            dist = math.hypot(d.x - bcx, d.y - bcy)
            if dist <= 2.5:
                secondary_joiners.append((dist, d))
        if engage_winner is not None and bc is not None:
            if not getattr(world, "tes_active", False):
                ope = world.open_play_engine
                # STAGE 2A: preserve the complete formation state (see the
                # pursuit site above).  Pure data argument only.
                _enter_tes(world, ope, bc, engage_winner, _CH_UNIFIED,
                           defenders=defenders)
                engage_winner.target_x = engage_winner.x
                engage_winner.target_y = engage_winner.y
                engage_winner.move_speed = 0.0
                engage_winner.frozen = True
                if ope.tes_ctx is not None:
                    from game.tes.tes_context import PlayerContext
                    max_add = ope.tes_ctx.max_joiners - len(ope.tes_ctx.engaged_tacklers)
                    added = 0
                    for _, sd in sorted(secondary_joiners, key=lambda x: x[0]):
                        if sd is engage_winner:
                            continue
                        if added >= max_add:
                            break
                        # ARM THE JOINER ATOMICALLY (contract A/B, 2026-08-08):
                        # ope._arm_tackler_role() sets movement_mode = TACKLER,
                        # collision_priority = HIGH, and registers the joiner in
                        # ctx.tackler_roles — so _update_tackler_movement() drives
                        # them into contact and _clear_tes_artefacts() exits them
                        # to RETREAT at RELEASE.  Stamping is_tackler/in_contact
                        # alone left movement_mode = None (root cause #7): the
                        # joiner never retreated, stayed wrong-side of the ruck,
                        # blocked the PTB bubble, and stalled the PTB timer.
                        if len(ope.tes_ctx.engaged_tacklers) == 1:
                            ope._arm_tackler_role(sd, DefenderRole.TACKLER_2, "TACKLER_2", ope.tes_ctx)
                        elif len(ope.tes_ctx.engaged_tacklers) == 2:
                            ope._arm_tackler_role(sd, DefenderRole.TACKLER_3, "TACKLER_3", ope.tes_ctx)
                        # CONTACT-CONTRACT HANDOFF (2026-09-15): the joiner
                        # inherits the PRE-TACKLE CORRIDOR's governed
                        # approach velocity rather than whatever another
                        # writer left on vx/vy before TACKLE_INIT.  Returns
                        # the live vx/vy when the body carries no corridor
                        # stamp or the handoff gate is off, so a
                        # non-corridor joiner is byte-identical.
                        _jvx, _jvy = _corridor_contact_velocity(sd, self.tuners)
                        sd_ctx = PlayerContext()
                        sd_ctx.player_id = sd.player_id
                        sd_ctx.x_m = sd.x
                        sd_ctx.y_m = sd.y
                        sd_ctx.vx = _jvx
                        sd_ctx.vy = _jvy
                        sd_ctx.mass = getattr(sd, "mass_kg", 90.0)
                        sd_ctx.stamina = getattr(sd, "stamina", 1.0)
                        sd_ctx.aggression = getattr(sd, "aggression", 0.5)
                        sd_ctx.tackle_skill = getattr(sd, "tackle_skill", 0.5)
                        sd_ctx.strength = getattr(sd, "strength", 0.5)
                        sd_ctx.facing = sd.facing
                        ope.tes_ctx.engagement.add(sd_ctx, world.sim_time_s)
                        ope.joiners.append(sd)
                        sd.target_x = sd.x
                        sd.target_y = sd.y
                        sd.move_speed = 0.0
                        sd.frozen = True
                        sd.tes_locked = True
                        sd.is_tackler = True
                        sd.in_contact = True
                        added += 1

    # ----------------------------------------------------------------
    # POST-CATCH DEFENSIVE LINE CLAMP — FINALISE PASS
    # ----------------------------------------------------------------
    # Applied AFTER every defence writer (pursuit reaction, depth
    # reclassification, structured close, line-break collapse, mandatory
    # retreat, decision layer) has set each defender's move_speed, but
    # BEFORE OpenPlayEngine._update_defence() calls Player.update(dt) for
    # movement integration.
    #
    # While armed (d.defended_cap_remaining_s > 0) the clamp caps each
    # defender's effective speed at the governor's maximum intended line
    # speed (tuners.post_catch_cap_mps) via Player._defended_cap_ceiling(),
    # so the two non-governor writers cannot visually "turbo" defenders
    # into position during the kickoff-catch → first stable OPEN_PLAY
    # transition.  The clamp also keeps the stability counter: once
    # pursuit is off, collapse is off, depth_class is stable, and the
    # geometry anchor is active for tuners.post_catch_stability_frames
    # consecutive frames, the clamp is disarmed and the remaining window
    # decays with a smooth blend (Player.DEFENDED_CAP_BLEND_S).
    def _finalise_lateral_targets(self, world, defenders):
        # Any defender still armed after the hard-limit window has elapsed
        # is force-disarmed by Player.step_defence_reaction() independently,
        # so this method only manages the early, stability-based release.
        #
        # DRIVE-END AUTHORITATIVE SKIP (2026-08-19): defenders with a live
        # authoritative slot (drive_end_authoritative just wrote their
        # target_x/target_y) must NOT be blended — the 0.5 blend + 0.4 m
        # delta cap would pull the authoritative snap back toward the
        # previous frame's stale position.
        current_frame = getattr(world, "frame_count", 0)
        any_armed = any(
            getattr(d, "defended_cap_remaining_s", 0.0) > 0.0
            for d in defenders
            if not d.frozen
            and getattr(d, "authoritative_until_frame", -1) < current_frame
        )
        if not any_armed:
            self._post_catch_stable_frames = 0
            self._post_catch_stable_time_s = 0.0
            return

        # ── STABILITY CHECK: pursuit off + collapse off + depth not flipping ──
        pursuit_off = not getattr(world, "in_pursuit_mode", False)
        first_not_made = getattr(world, "first_tackle_not_made", False)
        bc = getattr(world, "ball_carrier", None)
        anchor_x = getattr(world, "defensive_line_anchor", 60.0)

        # Collapse is considered active when BC is at/past the anchor or
        # any defender is currently classed SECONDARY from a line break.
        collapse_active = False
        if bc is not None and anchor_x is not None and bc.x > anchor_x:
            collapse_active = True
        depth_flipping = False
        for d in defenders:
            prev = getattr(d, "_prev_depth_class", None)
            cur = getattr(d, "depth_class", "PASSIVE")
            if prev is not None and prev != cur:
                depth_flipping = True
                break

        geometry_active = anchor_x is not None and anchor_x > 0.0

        stable = (
            pursuit_off
            and not first_not_made
            and not collapse_active
            and not depth_flipping
            and geometry_active
        )

        if stable:
            # STAGE 3F: accumulate simulated seconds instead of counting
            # frames, so the clamp-release point holds the same wall-clock
            # duration at any tick rate.  Legacy verifier fixtures without
            # world.dt fall back to the original frame counter.
            _dt = getattr(world, "dt", None)
            if _dt is not None:
                self._post_catch_stable_time_s += float(_dt)
                _required_s = getattr(
                    self.tuners, "post_catch_stability_s",
                    getattr(self.tuners, "post_catch_stability_frames", 10)
                    / 60.0,
                )
                _released = self._post_catch_stable_time_s >= _required_s
            else:
                self._post_catch_stable_frames += 1
                _required = getattr(
                    self.tuners, "post_catch_stability_frames", 10
                )
                _released = self._post_catch_stable_frames >= _required
            if _released:
                # Structured OPEN_PLAY is stable: disarm the clamp.  The
                # remaining window decays naturally with a smooth blend via
                # Player._defended_cap_ceiling() so the release is continuous.
                for d in defenders:
                    d.defended_cap_remaining_s = 0.0
                self._post_catch_stable_frames = 0
                self._post_catch_stable_time_s = 0.0
        else:
            self._post_catch_stable_frames = 0
            self._post_catch_stable_time_s = 0.0

    # ----------------------------------------------------------------
    # SUB-DISPATCH HELPERS (kept for backward compatibility)
    # ----------------------------------------------------------------

    # ── DRIVE-END AUTHORITATIVE SLOT POSITIONS (2026-08-19) ────────
    # Called ONCE at the TACKLE_DRIVE → TACKLE_DOMINANCE transition
    # (drive LERP completes, t >= 1.0).  Computes evenly-spaced Y slot
    # positions for eligible defenders, independently for each side of
    # ruck_y (left / right), clamped to field bounds, with fixed-anchor
    # reflow for clamped slots.  Writes the authoritative targets and
    # stamps ``authoritative_until_frame`` so the finalise blend,
    # stabiliser, and lane-index sorter all skip this defender for one
    # frame.
    #
    # FIELD CONSTANTS — derived from Player module-level bounds.
    _FIELD_MIN_Y: float = 0.0    # player.FIELD_MIN_Y
    _FIELD_MAX_Y: float = 70.0   # player.FIELD_MAX_Y
    _SLOT_BUFFER_M: float = 5.0  # edge buffer (metres)

    def drive_end_authoritative(self, world, defenders):
        """Compute side-partitioned authoritative slot positions at drive end.

        Partition eligible defenders by comparing each defender.y to
        ruck_y, allocate counts per side, and compute/clamp/reflow evenly
        spaced slots independently for left and right before writing
        authoritative targets.

        Excludes: frozen defenders, TES-participant defenders (primary +
        joiners + attached), markers, and the dynamic fullback.
        After computing slots, clears all blend baselines and movement
        locks on each affected defender, and stamps
        ``authoritative_until_frame = world.frame_count``.
        """
        ruck_x = getattr(world, "ruck_x_m", 60.0)
        ruck_y = getattr(world, "ruck_y_m", 35.0)
        frame = getattr(world, "frame_count", 0)
        tuners = self.tuners

        # ── 1. Collect eligible defenders ──
        # TES participants (primary + joiners) are part of the tackle
        # pile — their targets are owned by TES, not the slot compute.
        tes_ids = set()
        primary = getattr(world, "open_play_engine", None)
        if primary is not None:
            p = getattr(primary, "primary", None)
            if p is not None:
                tes_ids.add(getattr(p, "player_id", -1))
            for j in getattr(primary, "joiners", ()):
                tes_ids.add(getattr(j, "player_id", -1))
            # Also exclude attached tacklers
            for _att_pid in getattr(primary, "_attached_player_slots", {}):
                tes_ids.add(_att_pid)

        eligible = []
        for d in defenders:
            if not isinstance(d, Player):
                continue
            if d.frozen:
                continue
            if getattr(d, "player_id", -1) in tes_ids:
                continue
            if _is_marker(d):
                continue
            if getattr(d, "defender_role", None) == DefenderRole.DUMMY_HALF:
                continue
            if getattr(d, "fullback_role_active", False):
                continue
            eligible.append(d)

        if not eligible:
            return

        # ── 2. Partition by side (2026-08-29) ──
        # Sides are split by the hysteresis-protected depth_side_of()
        # (persistent depth_side / dead-banded fallback) — NEVER the raw
        # live d.y < ruck_y sign.  A left-side A anchored at ruck_y - 2.0
        # whose live Y drifts a hair over ruck_y mid-retreat keeps its LEFT
        # side instead of being re-partitioned RIGHT (which would snap its
        # spacing/width to the RIGHT A anchor, a ~4 m error).
        # Left side (depth_side_of == -1): sorted DESCENDING (closest to
        # ruck first, so slot 0 is nearest the centreline).
        # Right side (depth_side_of == +1): sorted ASCENDING (closest to
        # ruck first).
        left_side = sorted(
            [d for d in eligible if depth_side_of(d, ruck_y) == -1],
            key=lambda d: d.y,
            reverse=True,
        )
        right_side = sorted(
            [d for d in eligible if depth_side_of(d, ruck_y) == 1],
            key=lambda d: d.y,
            reverse=False,
        )

        half_w = governed_width_m(world, tuners) * 0.5
        field_min = self._FIELD_MIN_Y + self._SLOT_BUFFER_M
        field_max = self._FIELD_MAX_Y - self._SLOT_BUFFER_M

        # ── 3. Compute per-side slots ──
        def _apply_side_slots(side_defenders, side_sign):
            """Compute evenly-spaced slots for one side of ruck_y.

            side_sign: -1 for left (y < ruck_y), +1 for right (y >= ruck_y).
            Defenders in ``side_defenders`` are sorted closest-to-ruck first.
            Slot formula: ruck_y + side_sign * (i + 1) * half_w / N
            where i = 0 is the defender nearest the centreline.
            """
            n = len(side_defenders)
            if n == 0:
                return

            # ── Compute raw slots ──
            raw_slots = []
            for i, d in enumerate(side_defenders):
                slot = ruck_y + side_sign * (i + 1) * half_w / n
                clamped = max(field_min, min(field_max, slot))
                is_anchor = (clamped != slot)
                raw_slots.append((d, slot, clamped, is_anchor))

            # ── Reflow: recompute free (non-anchor) slots evenly
            #     between adjacent anchors ──
            # Find anchor boundaries; free defenders between two anchors
            # get evenly redistributed across that sub-range.
            # Collect all boundary positions (field edges + anchor slots).
            boundaries = [field_min if side_sign < 0 else field_max]
            for _, _, clamped, is_anchor in raw_slots:
                if is_anchor:
                    boundaries.append(clamped)
            boundaries.append(field_max if side_sign < 0 else field_min)
            boundaries.sort()

            # Walk through raw_slots; when a contiguous run of non-anchor
            # slots is found, reflow them evenly between the enclosing
            # anchor boundaries.
            result = []
            free_run = []
            for entry in raw_slots:
                d, raw, clamped, is_anchor = entry
                if is_anchor:
                    # Flush any pending free run
                    if free_run:
                        _reflow_free(free_run, result)
                        free_run = []
                    result.append(entry)
                else:
                    free_run.append(entry)
            if free_run:
                _reflow_free(free_run, result)

            # ── Write targets ──
            for d, raw, clamped, is_anchor in result:
                d.target_y = clamped
                # X target from role offset (depth role defines depth)
                depth_role = getattr(d, "depth_role", "D")
                role_offset = tuners.WIDTH_OFFSETS.get(depth_role, 5.0)
                d.target_x = ruck_x + role_offset
                d.target_source = "drive_end_authoritative"

                # Clear all blend baselines so the next frame starts fresh.
                # DELETE (not None): getattr(d, "_prev_close_x", d.target_x)
                # only falls back to d.target_x when the attribute is
                # absent — setting it to None would cause a TypeError.
                d.__dict__.pop("_prev_final_x", None)
                d.__dict__.pop("_prev_final_y", None)
                d.__dict__.pop("_prev_close_x", None)
                d.__dict__.pop("_prev_target_y_smooth", None)

                # Clear all movement locks
                d.timing_locked = False
                d.reaction_timer = 0.0
                d.retreat_delay = 0.0
                d.lateral_hesitation = 0.0
                d.step_stagger = 0.0
                d.step_stagger_active = False

                # Stamp authoritative flag — expires after one frame
                d.authoritative_until_frame = frame

        def _reflow_free(free_run, result):
            """Redistribute a run of free (non-anchor) defenders evenly."""
            # The free run sits between the last anchor slot (or field
            # edge) and the next anchor slot (or field edge).  We don't
            # have explicit boundary positions here; instead we
            # redistribute the N free defenders evenly across their
            # current Y-range.
            n = len(free_run)
            if n == 1:
                result.append(free_run[0])
                return
            # Evenly space across the span from first to last defender
            y_values = [d.y for d, _, _, _ in free_run]
            span_min = min(y_values)
            span_max = max(y_values)
            if abs(span_max - span_min) < 0.01:
                # All defenders at same Y — space by half_w / N around ruck_y
                mid = ruck_y
                span_min = mid - half_w * 0.5
                span_max = mid + half_w * 0.5
            for i, entry in enumerate(free_run):
                d, _, _, _ = entry
                if n > 1:
                    t = i / (n - 1)
                else:
                    t = 0.5
                slot = span_min + t * (span_max - span_min)
                clamped = max(field_min, min(field_max, slot))
                is_anchor = (clamped != slot)
                result.append((d, slot, clamped, is_anchor))

        _apply_side_slots(left_side, -1)
        _apply_side_slots(right_side, +1)

    def _apply_step_up_to_all(self, world, defenders, base_line_x, lane_y):
        """Push ALL defenders into their ball-relative role-depth bands."""
        if is_tackle_phase(world):
            return
        if not world.defensive_line_update_enabled:
            return

        ball_x = self._get_ball_x(world)
        ball_y = self._get_ball_y(world)
        for i, d in enumerate(defenders):
            # "D" is the universal bulk/fallback depth role (ruck-driven
            # role system, 2026-08-08).
            role = getattr(d, "depth_role", "D")
            role_offset = self.tuners.ROLE_DEPTH_M.get(role, 5.0)
            d._lane_y = lane_y[i]
            expected_x = ball_x + role_offset
            dx = expected_x - d.x
            max_stepup_step = 3.0
            if dx > max_stepup_step:
                d.target_x = d.x + max_stepup_step
            else:
                d.target_x = expected_x
            # 2026-08-12: consumed from the un-gated frame preamble.
            d.target_y = _frame_spacing_y(world, d, self.tuners,
                                          governor=self.line_governor)
            stamp_target_write(world, d, "spacing")

    def _apply_step_up_passive(self, world, defenders, base_line_x, lane_y):
        """Apply step-up to passive defenders with reduced engagement."""
        if is_tackle_phase(world):
            return
        if not world.defensive_line_update_enabled:
            return
        # pass-through; passive step-up is minimal in new architecture


# -----------------------------------------------------------
# MODULE-LEVEL HELPERS (no self needed)
# -----------------------------------------------------------

# ── FLAT-FOOTED TRIGGER PROXIMITY BAND (2026-09-02) ──
# Relaxed trigger: a HOLD/DRIFT (or flat-body) defender OUTSIDE the
# pursuit/commit claim (_pursuit_commit_active / _tackle_commit_active)
# but within this Euclidean band of a past-anchor carrier ALWAYS steps
# in via the flat-footed plant->stagger->CHASE chain, even when its
# target is no longer static (closing/stabilised target).  Bound values
# are inclusive.  Committed pursuers are excluded by the unclaimed gate.
_FLATFOOT_TRIGGER_BAND_MIN_M = 3.0
_FLATFOOT_TRIGGER_BAND_MAX_M = 5.0


def _apply_flatfoot_step_in(world, d, bc, blended_state, carrier_past, def_speed):
    """FORCED-FLATFOOT STEP-IN (2026-09-02).

    A defender resolved to HOLD/DRIFT (flat-footed) while the ball carrier
    is clearly past the anchor must NOT stand dead and miss the tackle.
    Force the PLANT -> STAGGER -> STEP-INTO-CONTACT chain: stamp
    footplanting (plant), seed a step_stagger wave (stagger), redirect the
    target onto a BC-forward closing point, and set CHASE so the defender
    steps into contact.  Runs AFTER the LINE gate / blend so a legally held
    LINE or deep-PASSIVE defender re-engages once the line is broken.
    Syncing last_decision keeps the reactivity audit
    (BAD_DECISION_ON_LINE_BREAK) reading the real movement state instead of
    the stale HOLD/DRIFT from the step-8 arbiter.

    Used by the MMDL step-10b writer and by verify_flatfoot_step_in.py.

    Returns the resolved MovementState (CHASE when the block fired,
    otherwise *blended_state* unchanged).
    """
    # FOOTWORK LOCK (2026-09-09): a flat-foot forced step-in is a full-speed
    # CLOSING posture, NOT a planted stance.  Clear the distinct marker at the
    # top (it is set only when the block fires below); the genuine
    # pre_tackle_stance/footplanting flags are ALWAYS left false here so the
    # machine never misclassifies a sprinting closer as a false-planted
    # FLATFOOT_FORCED and falls it over.
    d.flatfoot_forced_step = False
    _ff_state_name = getattr(blended_state, "name", None) or str(blended_state)
    _ff_static = abs(getattr(d, "target_x", d.x) - d.x) < 0.1
    # Relaxed 3-5m post-anchor proximity gate (2026-09-02): a defender
    # outside the pursuit/commit claim but within the band of a past-anchor
    # carrier always steps in, even when the target is no longer static.
    _ff_dist = math.hypot(d.x - bc.x, d.y - bc.y)
    _ff_unclaimed = (
        not getattr(d, "_pursuit_commit_active", False)
        and not getattr(d, "_tackle_commit_active", False)
    )
    _ff_band_relaxed = (
        carrier_past
        and _ff_unclaimed
        and _FLATFOOT_TRIGGER_BAND_MIN_M <= _ff_dist <= _FLATFOOT_TRIGGER_BAND_MAX_M
    )
    # ROLE-RELAXED gate (2026-09-02): a spacing-locked DEF_LINE_READY /
    # DEF_RETREAT_10 defender holding a retreat/anchored spacing role that
    # the pursuit/commit writers did NOT claim, within <=5m of a past-anchor
    # carrier, ALWAYS steps in instead of spacing-locking — dropping the 3m
    # lower bound so even a sub-3m body re-engages.  Committed pursuers are
    # excluded by the unclaimed gate (they already step in via the override).
    _ff_role = getattr(d, "defender_role", None)
    _ff_role_name = getattr(_ff_role, "name", None) if _ff_role is not None else None
    _ff_spacing_locked_role = _ff_role_name in ("DEF_LINE_READY", "DEF_RETREAT_10")
    _ff_role_relaxed = (
        carrier_past
        and _ff_unclaimed
        and _ff_spacing_locked_role
        and _ff_dist <= _FLATFOOT_TRIGGER_BAND_MAX_M
    )
    if (
        (carrier_past or _in_post_catch_chase_window(world))
        and not getattr(d, "fullback_role_active", False)
        and (
            _ff_state_name in ("HOLD", "DRIFT")
            or (_ff_state_name == "CHASE" and _ff_static)
            or _ff_band_relaxed
            or _ff_role_relaxed
        )
    ):
        d.flatfoot_forced_step = True
        d.footplanting = False
        d.pre_tackle_stance = False
        d.step_stagger = 0.05
        d.step_stagger_active = True
        _ff_ahead = d.x > bc.x
        d.target_x = bc.x - 1.0 if _ff_ahead else bc.x + 1.0
        d.target_y = bc.y
        d.target_source = "flat_footed_step_in"
        d.move_speed = def_speed(d)
        d.frozen = False
        d.timing_locked = False
        d.reaction_timer = 0.0
        d.retreat_delay = 0.0
        d.lateral_hesitation = 0.0
        blended_state = resolve_movement_state(
            d, MovementState.CHASE, fallback=MovementState.CHASE
        )
        d.last_decision = "CHASE"
    return blended_state


def _is_marker(d):
    return getattr(d, "defender_role", None) in (
        DefenderRole.MARKER_A,
        DefenderRole.MARKER_B,
    )


def _marker_offset(d):
    role = getattr(d, "defender_role", None)
    if role == DefenderRole.MARKER_A:
        return 1.0
    elif role == DefenderRole.MARKER_B:
        return 2.0
    return 1.5
