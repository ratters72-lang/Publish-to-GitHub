"""
world/open_play_engine.py -- Single-FSM OpenPlayEngine (rewrite).

Owns the open-play -> tackle -> PTB -> post-ruck -> open-play lifecycle via
a single internal phase machine mapped to GameSubState for downstream systems.

Key design rules:
  1. TES runs ONLY during TACKLE_INIT through TACKLE_DOMINANCE.
     At DOMINANCE->RELEASE, ALL TES geometry/containment/retreat stops.
  2. PTBManager is the SOLE authority for DH movement during PTB_PREP and PTB_EXEC.
     dh_controller.move_to_ruck() MUST NOT be called in those phases.
  3. DH gets defender_role = DefenderRole.DUMMY_HALF at RELEASE and keeps it
     through PTB.  DH is unfrozen throughout RELEASE -> PTB.

Engine-Spec X-Axis Marker Alignment (MARKER BEHAVIOUR):
  - Markers do NOT use Y-offsets.  They stand directly behind ruck on X-axis.
  - Marker A target: (ruck_x + 1.0, ruck_y)
  - Marker B target: (ruck_x + 2.0, ruck_y)
  - Markers unfreeze on RELEASE entry, move to targets each frame via Player.update(dt).
  - On PTB->POST_RUCK: markers frozen for 1s (marker_freeze_timer = 1.0).
  - POST_RUCK_OPEN_PLAY: freeze timer counts down; when expired role clears to DEF_LINE_READY.
  - TES/PTBManager do not control markers Ã¢â‚¬â€ only OPE does.
  - No offside. No retreat. No defensive behaviour for markers.
"""
print(__file__)

import math
import os
import random

from world.world_state import (
    WorldState,
    GameWorldState,
    GameSubState,
    set_ball_carrier,
    is_in_ruck_bubble,
    is_tackle_phase,
)
from player.player import Player, DefenderRole, FIELD_MIN_X, FIELD_MAX_X

from game.tes.tes import tes_start_tackle, _make_ctx
from game.tes.tes_context import (
    TESContext,
    PlayerContext,
    TackleEngagement,
    ATTACH_SLOT_LEFT_OFFSET_M,
    ATTACH_SLOT_RIGHT_OFFSET_M,
    ATTACH_SLOT_BACK_OFFSET_M,
)
from game.tes.tes_outcomes import TESOutcome
from game.tes.tes_geometry import can_join
from game.tes.tes_dominance import (
    update_waveform,
    compute_dominance,
    compute_drive_distance,
    compute_drive_target_x,
    compute_tackle_duration,
    compute_ptb_time,
)
from game.tes.tes_drive_variation import (
    DRIVE_DECAY,
    DEF_RESISTANCE_SCALE,
    DRIVE_MOMENTUM_CLAMP,
    BASE_SIGMA,
    MOMENTUM_SCALE,
    ATTACKER_BIAS,
    DEFENDER_BIAS,
    MAX_VARIATION,
    DRIVE_DETERMINISTIC_BOOST,
    DOMINANT_DET_SUPPRESS,
    compute_drive_force,
    compute_dominance_push,
    compute_geometric_factor,
    defender_strength_sum,
    dominance_hold_for,
    compute_drive_variation_step,
    compute_dominance_push_result,
    investigate_tes_forward_momentum,
    DOMINANCE_SETTLE_S,
    DOMINANT_X_REBALANCE,
    DOMINANT_SIDE_FOLD,
    SIDE_MIN_WEAK_NEUTRAL,
    NET_BACKWARD_CAP_M,
    NET_BACKWARD_DAMP,
)

from game.tes.tes_checks import (
    check_offload,
    check_strip,
    check_fumble,
    check_penalty,
    check_six_again,
    check_line_break,
)

# Ã¢â€â‚¬Ã¢â€â‚¬ TACKLER MOVEMENT CONTRACT enums (spec B/C/G, 2026-08-05) Ã¢â€â‚¬Ã¢â€â‚¬
from player.movement_states import MovementMode, MovementState, CollisionPriority
from game.tes.ptb_manager import (
    PTBManager,
    PTB_PREP_MAX_S,
    PTB_EXEC_MAX_S,
)
# ── CONVERGENCE-FIRST RELEASE EXIT (2026-09-17) ──
# RELEASE ends when every mover it owns has CONVERGED, not when a clock
# expires.  The predicate (DH + marker pair + recovering ball carrier),
# its tolerances and its window bounds are owned SOLELY by
# world/rules/release_gate.py -- this engine only consumes the verdict.
from world.rules.release_gate import (
    RELEASE_MIN_CONVERGENCE_S,
    RELEASE_MAX_DURATION_S,
    release_convergence,
)
# -- STAGE 4B (2026-09-19) EXIT-ERA TIMING BUDGET ----------------------------
# The PTB-era bounds used to be FUNCTION-LOCAL literals re-created on every
# call (PTB_TIMEOUT_S / DUCKOUT_DISTANCE in _update_ptb, and
# POST_RUCK_DURATION_S in _update_post_ruck_open_play), so the exit
# envelope could not be audited.  They are now owned by
# world/rules/exit_timing_budget.py.  BEHAVIOUR-NEUTRAL RELOCATION: the
# values are byte-identical and every call site below is unchanged.
#
# Imported under aliases so the handlers can bind their LOCAL names from
# the module constants -- `PTB_TIMEOUT_S = PTB_TIMEOUT_S` would make the
# name local and raise UnboundLocalError.
from world.rules.exit_timing_budget import (
    PTB_TIMEOUT_S as _EXIT_PTB_TIMEOUT_S,
    PTB_DUCKOUT_DISTANCE_M as _EXIT_PTB_DUCKOUT_DISTANCE_M,
    POST_RUCK_DURATION_S as _EXIT_POST_RUCK_DURATION_S,
)
from game.attack.attack_lvl_4.dummy_half import DummyHalfController
from game.attack.attack_lvl_1.attack_geometry import AttackGeometry
from game.attack.attack_lvl_1.attack_perception import AttackPerception
from game.attack.attack_lvl_1.attack_tuners import AttackTuners
from game.attack.attack_lvl_2.ball_carrier_intent import BallCarrierIntent
from game.attack.attack_lvl_3.run_logic import RunLogic
from game.attack.attack_lvl_4.attack_controller import AttackController
from game.debug.freeze_audit import FreezeAndMovementAuditAgent
from game.debug.defence_reactivity_audit import audit_defence_reactivity
from game.debug.fullback_audit import audit_fullback_state
from game.debug.fullback_invariant_audit import (
    audit_fullback_invariants,
    rearm_fullback_invariant_audit,
)
from game.debug.movement_contract_audit import (
    snapshot_defensive_state as mca_snapshot,
    check_for_overwrites as mca_check,
    print_movement_contract_report as mca_report,
)
from game.debug.target_write_log import (
    record_final_targets,
    stamp_target_write,
)
from game.defence.defence_lvl_1.fullback_role import (
    apply_fullback_geometry,
    publish_fullback_state,
    # B2b (2026-09-16): emergency-defender engagement marker.  An emergency
    # fullback keeps the tag through TES arming; the marker is cleared here
    # at the end of the tackle cycle so the actor resumes its sweeper role.
    arm_fullback_post_tackle_return as _fb_arm_post_tackle_return,
    is_fullback_emergency_engaged as _fb_emergency_engaged,
    reset_fullback_emergency_engagement as _fb_reset_engagement,
    # THE BREAK LATCH (2026-09-28): the explicit end-of-tackle-cycle release
    # for the per-actor break latch.  A chase that a FORWARD ruck relock
    # cannot un-break is latched for the duration of the cycle, so the cycle
    # end is the only place it may be dropped -- see the BREAK LATCH block in
    # fullback_role.py for the measured frame-383 defect this removes.
    clear_fullback_chase_state as _fb_clear_chase_state,
)
from game.defence.defence_lvl_1.pretackle_attempt import assign_def_line_ready
# -- RETREAT-OBLIGATION AUTHORITY (2026-09-17) -------------------------------
# THE 10 m retreat-compliance contract for non-marker defenders: a body
# converted out of DEF_RETREAT_10 while still short of the anchor line
# keeps its geometry but may NOT regain tackle authority until the
# obligation is satisfied.  See the module docstring for the full trace.
from game.defence.defence_lvl_1.retreat_obligation import (
    is_retreat_obligated,
    strip_tackle_authority,
)
from game.debug.defender_state_audit import run_defender_state_audit
from game.defence.defence_lvl_1.ruck_adjacent_pin import (
    enforce_ruck_adjacent_pin,
)
from game.defence.defence_lvl_1.defensive_behaviour_unified import DefensiveBehaviourUnified
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_geometry import DefensiveGeometry
from game.defence.defence_lvl_1.defensive_line_manager import DefensiveLineManager
from game.defence.defence_lvl_1.defensive_width import governed_width_m
from game.defence.defence_lvl_1.defensive_engagement_state import reset_engagement_state
from game.defence.defence_lvl_1.defensive_pipeline import (
    run_defensive_pipeline,
    frame_spacing_y as _frame_spacing_y,
    clear_frame_geometry,
    reset_defensive_drive_state,
    no_tackle_no_defence_active as _ntnd_active,
)
from game.defence.defence_lvl_1.anchor_authority import (
    compute_and_lock_anchor,
    defensive_line_anchor_m as _anchor_m,
    reset_anchor_authority,
    set_anchor,
)

# -- MACRO MANAGERS (sole-order authorities, 2026-08-24) ---------------------
from game.managers.defence_manager import DefenceManager
from game.managers.attack_manager import AttackManager

# -- TASK XII: attack-structure authority + structured audit chain ----------
from game.attack.attack_lvl_1.attack_structure_authority import (
    reset_attack_structure,
)
from game.debug.attack_structure_audit import reset_events
# -- TASK XIII: BC steering stabilisation authority ----------
from game.attack.attack_lvl_1.bc_steering_authority import reset_bc_steering

# -- TACKLE-STATE GATE (2026-08-23) ------------------------------------------
# Radius-gated hold/release authority: a defender stays in TACKLE-STATE
# for the entire TES/PTB while inside the tackle-radius and only exits
# into spacing/depth roles when it leaves that radius.
from game.defence.defence_lvl_1.tackle_state_gate import (
    stamp_tackle_state,
    update_tackle_state,
    reset_tackle_state,
)

# Ã¢â€â‚¬Ã¢â€â‚¬ Movement contracts (SDEA invariants) Ã¢â€â‚¬Ã¢â€â‚¬
from world.rules.movement_contracts import (
    apply_phase_invariants,
    audit_movement,
    enforce_bc_backward_invariant,
    enforce_movement_authority_invariant,
    reactivate_movement_after_thaw,
    zero_frozen_velocity,
)

# Ã¢â€â‚¬Ã¢â€â‚¬ Unified TES phase controller Ã¢â€â‚¬Ã¢â€â‚¬
from world.rules.release_controller import ai_tes_controller

# Ã¢â€â‚¬Ã¢â€â‚¬ FrameContext builder (read-only per-frame state projection) Ã¢â€â‚¬Ã¢â€â‚¬
from world.frame_context import build_frame_context

# Ã¢â€â‚¬Ã¢â€â‚¬ Structured Decision Module (deterministic TES/RELEASE/PTB retreat) Ã¢â€â‚¬Ã¢â€â‚¬
from world.rules.defence_sdm import decide_for_player


# -- Constants -----------------------------------------------------------
TACKLE_RADIUS_M: float = 1.5
TACKLE_ANGLE_THRESHOLD_DEG: float = 60.0
# RUCK vs OPEN-PLAY TACKLER SELECTION ZONES (2026-08-05):
# RUCK_ZONE_RADIUS_M: BC is "in the ruck zone" when abs(bc.x - ruck_x) <=
#   this; otherwise the BC is "in open play" (bc.x > ruck_x + radius).
#   These zones weight marker vs defensive-line tackler priority.
# MARKER_TACKLE_RADIUS_M: max BC distance for a marker to be an eligible
#   tackler candidate (markers play tight to the ruck by design).
# LINE_TACKLE_RADIUS_M: max BC distance for a defensive-line defender to
#   be an eligible tackler candidate.
RUCK_ZONE_RADIUS_M: float = 5.0
MARKER_TACKLE_RADIUS_M: float = 3.0
LINE_TACKLE_RADIUS_M: float = 6.5

# ==========================================================================
# OPERATION ATTACKTIVATE -- PHASE 1: FOUNDATIONAL DNA MAPPING
# ==========================================================================
# The attacking side's PERMANENT structural DNA, declared once here as static
# reference data (no per-frame cost, no engine state).  The per-actor copy is
# made lazily, the first time a body reaches an attack DNA reader, by
# OpenPlayEngine._lazy_initialize_attack_dna(); nothing walks the roster at
# boot.  These maps are the sole source of truth for a player's structural
# group and spatial hemisphere.
#
# POSITION_GROUP_MAP: jersey -> structural group.
#     SPINE    : 6, 7, 9        (the central decision-makers)
#     FB       : 1              (the deep sweeper / fullback)
#     BACKS    : 2, 3, 4, 5     (the edge backs)
#     FORWARDS : 8, 10, 11, 12, 13 (the forward pack)
# POSITION_HEMISPHERE_MAP: jersey -> spatial hemisphere.
#     CENTRAL  : 9, 13
#     DYNAMIC  : 1, 6, 7
#     LEFT     : 2, 3, 8, 11
#     RIGHT    : 4, 5, 10, 12
POSITION_GROUP_MAP: dict[int, str] = {
    1: "FB",
    2: "BACKS", 3: "BACKS", 4: "BACKS", 5: "BACKS",
    6: "SPINE", 7: "SPINE", 9: "SPINE",
    8: "FORWARDS", 10: "FORWARDS", 11: "FORWARDS",
    12: "FORWARDS", 13: "FORWARDS",
}

POSITION_HEMISPHERE_MAP: dict[int, str] = {
    9: "CENTRAL", 13: "CENTRAL",
    1: "DYNAMIC", 6: "DYNAMIC", 7: "DYNAMIC",
    2: "LEFT", 3: "LEFT", 8: "LEFT", 11: "LEFT",
    4: "RIGHT", 5: "RIGHT", 10: "RIGHT", 12: "RIGHT",
}

# -- ROLLOUT FLAGS ---------------------------------------------------------
# Group-level rollout gates for the attacking AI.  A group whose flag is True
# is eligible for group AI (Phase 1 only leaves the SPINE hook open); a group
# whose flag is False falls back to the passive background trail.
ATTACK_ROLLOUT_SPINE_ACTIVE: bool = True
ATTACK_ROLLOUT_FORWARDS_ACTIVE: bool = False
ATTACK_ROLLOUT_BACKS_ACTIVE: bool = False

# group_id -> rollout gate.  FB has no dedicated flag in Phase 1: it is a
# passive group and shares the BACKS gate, so both fall back in lockstep.
ATTACK_GROUP_ROLLOUT_FLAGS: dict[str, bool] = {
    "SPINE": ATTACK_ROLLOUT_SPINE_ACTIVE,
    "FORWARDS": ATTACK_ROLLOUT_FORWARDS_ACTIVE,
    "BACKS": ATTACK_ROLLOUT_BACKS_ACTIVE,
    "FB": ATTACK_ROLLOUT_BACKS_ACTIVE,
}



# PHASE 1 â€” ATTACH: distance within which a valid tackler attaches to the
# ball carrier during TACKLE_CONTACT only.  See game/tes/tes_context.py
# ATTACH_RADIUS for the canonical value (kept in sync).
ATTACH_RADIUS_M: float = 0.7
# Velocity damping factor applied to a defender the moment they attach
# (spec: "strongly damp defender's velocity").
ATTACH_VELOCITY_DAMP: float = 0.1
# PHASE 1 â€” ATTACH LUNGE: closing speed (m/s) applied to selected tacklers
# during TACKLE_CONTACT.  A tackle triggers at TACKLE_RADIUS_M (1.5 m),
# which is outside ATTACH_RADIUS_M (0.7 m); without this closing step a
# tackler would sit 0.7-1.5 m from the BC for the whole 0.15 s CONTACT and
# never attach.  The lunge pulls none-attached TACKLERs toward the BC until
# they cross ATTACH_RADIUS, where _update_attachments() snaps them in.
ATTACH_LUNGE_SPEED_MPS: float = 7.0
# TES-ATTACH GLIDE (2026-09-07): the attach blocks placed a joiner directly
# onto the BC slot (d.x/player.x = bc.x + dx) in ONE frame — the "at initial
# contact" teleport.  Both attach blocks now GLIDE toward the slot at at
# most this speed * dt per frame, so joiners converge instead of punching
# across the field.  Mirrors DEPTH_LOCK_GLIDE_SPEED_MPS / RUCK_PIN_GLIDE.
_TES_ATTACH_GLIDE_SPEED_MPS: float = 6.0


def _resolve_attach_radii(ope) -> tuple[float, float]:
    """Resolve (R_assist, R_tackle) from the defensive tuners.

    R_assist (default 5.0) is the reserve + inward-nudge band; R_tackle
    (default 2.0) is the sole snap/attach radius.  Reads
    ``ope.def_tuners.tackle_assist_radius_m`` / ``tackle_snap_radius_m``.
    """
    tuners = getattr(ope, "def_tuners", None)
    if tuners is None:
        return 5.0, 2.0
    return (
        float(getattr(tuners, "tackle_assist_radius_m", 5.0)),
        float(getattr(tuners, "tackle_snap_radius_m", 2.0)),
    )


def repair_line_population_if_drifted(world, tuners) -> bool:
    """Republish the ONE line population when membership drifted this frame.

    STAGE 5 (2026-09-13).  The tackle-state gate runs AFTER the per-frame
    defensive preamble, and it can ADD a holder (a body that came inside the
    tackle radius leaves the line) as well as release one.  Either direction
    changes the authoritative line population after the preamble published
    it, so the published count, the ``depth_side`` stamps, the cleared-stamp
    set and the population-keyed spacing/width dicts would all describe a
    defender set that no longer exists — the Stage 4 divergence signature.

    When (and only when) the live signature differs from the published one,
    this re-runs the SINGLE authority so count / side / spacing ownership are
    republished for the population that actually exists this frame.  It does
    NOT re-run the geometry pipeline (that stays owned by the preamble and by
    the dedicated release repair), so it is safe inside the sealed TES
    chamber where the line geometry is frozen.

    Returns True when a repair was performed.
    """
    from game.defence.defence_lvl_1.population_authority import (
        line_population,
        publish_frame_is_fresh,
        publish_line_population,
    )
    if not publish_frame_is_fresh(world):
        return False
    team = getattr(world, "defending_team", None) or []
    live = frozenset(id(d) for d in line_population(team))
    published = getattr(world, "line_population_ids", None) or frozenset()
    if live == published:
        return False
    publish_line_population(world, team, tuners, force=True)
    return True


# The phases in which the defensive geometry pipeline must NOT write (the
# sealed TES chamber: the line is frozen and TES owns all movement).
_SEALED_TES_PHASES = (
    "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE",
)


def finalise_line_population(world, tuners, phase, governor=None) -> None:
    """Close the frame with ONE population for every owner.

    STAGE 5 (2026-09-13).  Runs as the LAST population write of the frame,
    after every producer (phase handlers, the tackle-state gate, the ruck pin
    and the freeze authority), and guarantees the frame boundary holds:

        published count  ==  line population  ==  spacing population

    Three ordered steps:

      1. If membership drifted after the frame's publication, republish the
         ONE authority (count + side + stamps) for the live population.
      2. If a POPULATION member has no spacing entry, the spacing producer
         ran before that body (re-)entered the line, so the frame's geometry
         is rebuilt for the population.  Suppressed inside the sealed TES
         chamber, where the line geometry is frozen by contract.
      3. Trim the population-keyed observer dicts to the population, dropping
         keys producers added for non-line actors (the TES drive structure
         writes an entry for its TACKLER_* bodies during TACKLE_CONTACT).
    """
    from game.defence.defence_lvl_1.population_authority import (
        line_population,
        publish_frame_is_fresh,
        publish_line_population,
        trim_observer_dicts,
        observer_dicts_missing_members,
    )
    if not publish_frame_is_fresh(world):
        return
    team = getattr(world, "defending_team", None) or []
    live = frozenset(id(d) for d in line_population(team))
    published = getattr(world, "line_population_ids", None) or frozenset()
    if live != published:
        publish_line_population(world, team, tuners, force=True)
    if (phase not in _SEALED_TES_PHASES
            and observer_dicts_missing_members(world)):
        run_defensive_pipeline(
            world, team, tuners, governor=governor,
            # STAGE 4B (audit-only): the frame-boundary rebuild is a THIRD
            # solver of the same frame's geometry, distinct from the
            # preamble and the release repair.
            publish_source="finalise",
        )
    trim_observer_dicts(world)

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# TACKLER MOVEMENT CONTRACT (spec C, 2026-08-05)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# These constants drive OPE._update_tackler_movement() during TACKLE_INIT
# so every TACKLER_1/2/3 accelerates into the collision, matches BC
# velocity, faces BC directly, and lunges in open play (no ghosting).
#   TACKLER_ACCEL_RADIUS_M:  inside this distance speed_target floors at 4.0
#   TACKLER_CONTACT_RADIUS_M: inside this distance speed_target floors at 5.0
#   TACKLER_HEADING_RADIUS_M: inside this distance heading is forced onto BC
#   TACKLER_OPEN_PLAY_LUNGE_X_M: bc.x > ruck_x + this => open-play lunge active
#   TACKLER_LUNGE_SPEED_MPS / TACKLER_LUNGE_DURATION_S: lunge pull parameters
#   TACKLER_MAX_CLOSE_SPEED_MPS: hard ceiling on tackler-mode close speed so
#   the move can never exceed the player's physical max (9.2 m/s).
TACKLER_ACCEL_RADIUS_M: float = 3.0
TACKLER_CONTACT_RADIUS_M: float = 1.5
TACKLER_ACCEL_FLOOR_MPS: float = 4.0
TACKLER_CONTACT_FLOOR_MPS: float = 5.0
TACKLER_HEADING_RADIUS_M: float = 4.0
TACKLER_OPEN_PLAY_LUNGE_X_M: float = 5.0
TACKLER_LUNGE_SPEED_MPS: float = 6.0
TACKLER_LUNGE_DURATION_S: float = 0.20
TACKLER_MAX_CLOSE_SPEED_MPS: float = 9.0
# PHASE 2 â€” DRIVE STRENGTH tuning
# Base drive_strength per dominance result (WEAK < NEUTRAL < DOMINANT).
DRIVE_STRENGTH_WEAK: float = 0.7
DRIVE_STRENGTH_NEUTRAL: float = 1.3
DRIVE_STRENGTH_DOMINANT: float = 1.6
# Extra drive_strength per additional attached tackler beyond the primary.
DRIVE_STRENGTH_PER_TACKLER: float = 0.12
# Duration modifiers applied on top of compute_tackle_duration()
# (DOMINANT shorter, NEUTRAL medium, WEAK longer â€” spec 2.5).
DRIVE_DURATION_WEAK: float = 1.4
DRIVE_DURATION_NEUTRAL: float = 1.2
DRIVE_DURATION_DOMINANT: float = 0.85
# -- DYNAMIC DRIVE MODEL (2026-10-04) --------------------------------------
# The drive phase used a flat momentum ceiling (DRIVE_MOMENTUM_CLAMP upper
# bound 2.6) that bound the momentum accumulator on EVERY frame, so the
# microscopic per-frame variation could never move bc.x and the carrier
# crawled at a uniform rate.  These constants replace that flat cap with a
# ceiling scaled by the drive's own entry context, and add a coarse
# frame-based stride so the carrier visibly surges and bogs down.
#
#   DRIVE_MOMENTUM_BASE_CEILING:    base of the dynamic momentum ceiling (m/s)
#   DRIVE_MOMENTUM_STRENGTH_GAIN:   ceiling added per unit drive_strength
#                                   (dynamic_max = BASE + drive_strength * GAIN)
#   STRIDE_WAVE_RATE:               stride sine rate, radians per simulation
#                                   frame (~2.4 Hz at 60 fps -> a deliberate
#                                   NRL leg pump; raise for a faster flutter)
#   STRIDE_AMPLITUDE_PER_MOMENTUM:  stride metres-per-frame per unit of
#                                   accumulated drive momentum
DRIVE_MOMENTUM_BASE_CEILING: float = 2.20
DRIVE_MOMENTUM_STRENGTH_GAIN: float = 1.50
STRIDE_WAVE_RATE: float = 0.25
STRIDE_AMPLITUDE_PER_MOMENTUM: float = 0.9
# Minimum forward drive magnitude (metres) that counts as a genuine
# tackle break, gating _check_broken_tackle_scoring().  Small drives
# (e.g. 0.32 m pushes) are normal tackle momentum, not breaks.
BREAK_DRIVE_THRESHOLD_M: float = 1.5
# -- STAGE 3.5 BUMP CLEARANCE ----------------------------------------------
# A contest whose declaration resolved wrap_collision_state == "BUMP_OFF"
# (the carrier's momentum cleared the defensive wrap by more than the TES
# leaf's declared threshold) tears the wrap down instead of grinding the
# carrier through a pre-baked tackle.  The lever is read at consumption time,
# so flipping it off restores the pre-Stage-3.5 engine exactly.
BUMP_CLEARANCE_ENV_VAR: str = "RLM_TES_BUMP_CLEARANCE"
# How long a bumped-off primary defender stays on the ground.  Longer than
# the plain broken-tackle 1.0 s because the defender was beaten outright.
BUMP_OFF_DEFENDER_FREEZE_S: float = 1.2
# How long the carrier keeps the burst that carries it clear of the contact.
BUMP_OFF_CARRIER_BURST_S: float = 0.4
# Burst speed as a multiple of the carrier's own top speed.  Matches the
# escape-window multiplier the attack controller already uses (1.20x).
BUMP_OFF_CARRIER_BURST_SCALE: float = 1.20

# -- STAGE 3.1 PROGRESSIVE_DRAG VELOCITY DECAY -----------------------------
# A contest whose declaration resolved wrap_collision_state ==
# "PROGRESSIVE_DRAG" is a GRIND: the margin between the carrier's momentum
# and the whole defensive wrap cleared the leaf's threshold in NEITHER
# direction, so neither side won the collision -- the traditional
# rugby-league tackle, where the carrier is slowly dragged to a stop and a
# PTB ruck forms.
#
# The engine delivers that grind as ONE decay step per DRIVE frame, applied
# by _apply_progressive_drag() from the intercept in _update_tackle_drive().
# Same lever as the Stage 3.5 clearance (RLM_TES_BUMP_CLEARANCE, via
# bump_clearance_enabled()): disarmed, the predicate is False everywhere and
# every DRIVE frame is bit-identical to the engine before this stage.
#
# 0.95 compounds to ~0.05x of the original authority over a one-second drive
# (60 frames), which is the "brought to a stop" half of the law; Step 3.2's
# terminal floor ends the drive outright once the decayed rate is spent.
PROGRESSIVE_DRAG_DECAY: float = 0.95

# STAGE 3.2: THE TERMINAL FLOOR -- the forward authority (metres per second)
# below which the grind is SPENT and the drive ends.  The decay above shrinks
# ctx.drive_step_mps by 0.95 per sustained DRIVE frame; once it crosses this
# floor the carrier is no longer going anywhere, so the drive loop aborts and
# the tackle completes as a standard ruck instead of grinding out the rest of
# the phase window.  It is expressed on the SAME quantity the decay writes --
# the drive's own motion authority -- so "the forward speed dropped below
# 0.4 m/s" is literally what the engine tests.  At 0.95/frame the floor is
# reached after ~19.5*ln(v0/0.4) frames (about 36 frames from 2.5 m/s).
PROGRESSIVE_DRAG_TERMINAL_FLOOR_MPS: float = 0.4


def bump_clearance_enabled() -> bool:
    """STAGE 3.5 lever: is the BUMP CLEARANCE path armed?

    Default ON, matching every other stage lever in this codebase.  Set
    RLM_TES_BUMP_CLEARANCE=0 to disarm: _bump_off_pending() then always
    returns False, the collision-state token is ignored, and every frame is
    bit-identical to the pre-Stage-3.5 engine.
    """
    return os.environ.get(
        BUMP_CLEARANCE_ENV_VAR, "1"
    ).strip().lower() not in ("0", "false", "no", "off", "")


# ── FREEZE CLOCK (2026-09-26) ────────────────────────────────────────
# ``freeze_timer`` is declared as a duration (player/player.py) but
# behaved as a LEVEL: written by three writers (the miss stun in
# pretackle_machine.apply_outcome, _handle_broken_tackle's 1.0/1.2 s, and
# marker assignment's 0.3 s), read as a boolean by ~40 gates plus the
# ``struct_freeze_timer`` pin, and NEVER decremented anywhere -- so its
# only release was a lifecycle clear at RELEASE.  A missed or beaten
# defender therefore stayed pinned to its own position until the next
# tackle reached RELEASE.
#
# This clock makes the field an ENVELOPE, exactly like the neighbouring
# ``marker_freeze_timer`` (which IS decremented, in
# _update_post_ruck_open_play).  It is the ONE decrement site for the
# whole engine, so every existing writer gains automatic expiry with no
# change to the hold readers, the pin, or the RELEASE clears.
#
# The lever is read at consumption time, so flipping it off restores the
# pre-clock engine exactly.
FREEZE_CLOCK_ENV_VAR: str = "RLM_TES_FREEZE_CLOCK"


def freeze_clock_enabled() -> bool:
    """Whether the per-frame ``freeze_timer`` decay runs.

    Default ON.  ``RLM_TES_FREEZE_CLOCK=0`` (or false/no/off/empty)
    disarms it: every ``freeze_timer`` write reverts to a permanent latch
    and each frame is bit-identical to the pre-clock engine.
    """
    return os.environ.get(
        FREEZE_CLOCK_ENV_VAR, "1"
    ).strip().lower() not in ("0", "false", "no", "off", "")


# ── BROKEN-TACKLE ATTACHMENT RELEASE (2026-09-30) ─────────────────────
# THE OWNERSHIP DEFECT THIS REMOVES ("the fullback makes contact, then just
# travels with the ball carrier").
#
# A defender becomes a TES participant by being ARMED as a TACKLER_* body,
# and ``_update_attachments`` then admits it (role gate TACKLER_1/2/3 plus
# the snap radius) and stamps ``tes_attached = True`` onto the BODY while
# registering it in ``ctx.attached_ids``.  The attachment is therefore
# TACKLE-CYCLE state carried on the body.
#
# RELEASE entry tears it down: ``_update_release``'s first frame runs
# ``_clear_tes_artefacts()`` + ``_detach_all_tacklers()``, and the latter is
# the ONLY routine that clears ``tes_attached`` (it walks the context's
# ``attached_ids``).  A BROKEN TACKLE never enters RELEASE -- it exits
# straight to OPEN_PLAY -- and the plain path DELIBERATELY skips
# ``_detach_all_tacklers`` in favour of "the next ``_update_tackle_init``
# re-syncs the stale flags".  That repair assumes a NEXT tackle, which on a
# clean line break may never come.
#
# What survives is a body holding ``tes_attached = True`` with no contest to
# belong to, and that one flag is load-bearing for the last defender:
#
#   * ``fullback_role.apply_fullback_geometry`` tests ``tes_attached``
#     FIRST (its TES-OWNERSHIP-SAFETY guard) and RETURNS -- no target, no
#     movement state, no speed, no unfreeze, on every subsequent frame;
#   * ``fullback_role.fullback_tackle_side_owner_live`` reads it, so
#     ``establish_fullback_live_break_chase`` declines and the emergency
#     yield also writes nothing;
#   * ``fullback_recovery_active`` requires ``not tes_attached``, so RECOVER
#     is refused too;
#   * ``_is_available`` / ``_is_available_simple`` reject the body, so
#     ``assign_fullback_role`` hands the fullback tag to a DIFFERENT
#     defender and the original body is left with no owner at all.
#
# The result is a body with no movement writer whatsoever, coasting beside
# the carrier -- the reported "attached to the BC" symptom.
#
# THE RULE: the teardown that ends the contest must release the contest's
# ATTACHMENT, exactly as it already releases ``in_contact`` / ``is_tackler``
# / ``tes_locked``.  The attachment is tackle-cycle state; a broken tackle
# ends the cycle.
#
# A-B / rollback lever: ``RLM_RELEASE_ATTACH_ON_MISS=0`` restores the
# historical engine byte-for-byte (the flag survives the teardown), the same
# pattern as every other runtime lever in this module.
RELEASE_ATTACH_ON_MISS_ENV_VAR: str = "RLM_RELEASE_ATTACH_ON_MISS"


def release_attach_on_miss_enabled() -> bool:
    """Whether the broken-tackle teardown also releases the TES attachment.

    Default ON.  Read at CONSUMPTION time (the teardown), not at import, so
    a rollout can be A/B'd from one process.
    """
    return os.environ.get(
        RELEASE_ATTACH_ON_MISS_ENV_VAR, "1"
    ).strip().lower() not in ("0", "false", "no", "off", "")


def release_broken_tackle_attachment(body) -> bool:
    """Drop *body*'s TES attachment flags at the end of a broken tackle.

    Clears the two per-body stamps ``_update_attachments`` / ``_attach_defender``
    wrote -- ``tes_attached`` and ``attach_slot`` -- so the body is handed back
    to its OWN movement authority the frame the contest ends.  The context-side
    entries need no attention here: the teardown drops ``self.tes_ctx``
    outright a few lines later, so ``attach_offsets`` / ``attached_ids`` die
    with it.

    Total and side-effect-free for a body that was never attached, and gated
    on the A/B lever so the disarmed arm is the pre-change engine exactly.

    Returns True when a stamp was actually cleared.
    """
    if body is None or not release_attach_on_miss_enabled():
        return False
    cleared = False
    if bool(getattr(body, "tes_attached", False)):
        body.tes_attached = False
        cleared = True
    if getattr(body, "attach_slot", None) is not None:
        body.attach_slot = None
        cleared = True
    return cleared


def decay_freeze_timers(world, dt: float) -> int:
    """Burn down every live ``freeze_timer`` by *dt*; return bodies ticked.

    Called ONCE per frame at the frame boundary, BEFORE the phase handler,
    so a value written earlier in a frame is never partially consumed and
    a value written later in the same frame is honoured in full on the
    next tick.  Clamped at 0.0 so the field never goes negative and the
    ``> 0`` readers see a clean release instead of a stale latch.

    Only bodies already carrying a positive timer are touched, so a body
    sitting at 0.0 is never written and the disarmed path stays
    byte-identical.

    LIVE-BREAK HAND-BACK (2026-09-26)
    ---------------------------------
    The legacy expiry branch re-pinned the target to the body's own position
    because a bare decay handed a body back holding a STALE, possibly
    out-of-field target (the measured regression recorded in the comment
    below).  That self-pin is correct for a body nobody owns -- but it is
    the WRONG answer for a defender beaten by a line break: the pin is a
    measurement of where the body fell over, and re-writing it on expiry
    swaps one static target for another, so the defender keeps "sprinting
    to where it already stands" while the carrier runs clear (the
    STATIC_TARGET_AFTER_LINE_BREAK / post-miss hesitation defect).

    On the expiry tick a genuine line defender with a GENUINE line break
    live is therefore handed back as a LIVE pursuer by
    ``freeze_expiry_pursuit.hand_back_expiry_pursuit``; the historical
    self-pin runs unchanged in every other case (no break, owned body,
    ineligible role, or ``RLM_FREEZE_EXPIRY_PURSUIT=0``).
    """
    if dt <= 0.0 or not freeze_clock_enabled():
        return 0
    ticked = 0
    for p in getattr(world, "players", None) or ():
        timer = getattr(p, "freeze_timer", None)
        if timer is None:
            continue
        timer = float(timer)
        if timer <= 0.0:
            continue
        remaining = timer - dt
        if remaining > 0.0:
            p.freeze_timer = remaining
        else:
            # HAND-BACK ON EXPIRY (2026-09-26, MEASURED REGRESSION).
            # The step-7 pin was masking a second defect: while the timer
            # ran, the body's target was re-pinned to its own position every
            # frame, so a body that no writer re-targeted still looked
            # self-consistent.  Expiring the timer without re-pinning hands
            # it back holding a STALE, possibly out-of-field target with
            # (often) move_speed == 0 -- zero velocity plus a live offset
            # target, which is precisely the shape the stall audit forbids.
            # Measured: 20 stalled body-frames disarmed vs 420 with a bare
            # decay, plus a new out-of-field-target failure.
            #
            # So expiry must leave the body SELF-CONSISTENT: target pinned
            # to its current position.  This runs BEFORE the phase handler,
            # so any live writer overwrites the pin later in the same frame;
            # the pin only survives when nobody owns the body, which is
            # exactly the case that used to dangle.
            p.freeze_timer = 0.0
            # ── LIVE-BREAK HAND-BACK (2026-09-26) ──
            # A beaten defender's expiry must leave it a PURSUER, not a
            # self-pin: the pin records where it fell over, and re-writing it
            # here is exactly the target-generation defect the reactivity
            # audit reports as STATIC_TARGET_AFTER_LINE_BREAK.  Delegated to
            # ``freeze_expiry_pursuit``, which reads the SAME exemption set
            # as ``line_break_static_guard`` and the SAME line-break
            # predicate as the MMDL, so the three writers can never disagree.
            # It returns False -- leaving the historical self-pin below
            # byte-identical -- when the lever is disarmed, no break is live,
            # a tackle-side owner is actually driving the body, or the body
            # is a dedicated owner (fullback / marker / DH / tackler /
            # committed / corridor / retreat-obligated).
            from game.defence.defence_lvl_1.freeze_expiry_pursuit import (
                hand_back_expiry_pursuit as _hand_back_expiry_pursuit,
            )
            _handed_back = bool(
                _hand_back_expiry_pursuit(p, world)
            )
            if not _handed_back and hasattr(p, "target_x"):
                p.target_x = float(getattr(p, "x", 0.0))
                p.target_y = float(getattr(p, "y", 0.0))
        ticked += 1
    return ticked


POST_PTB_IMMUNITY_FRAMES: int = 8
RETREAT_DISTANCE_M: float = 10.0
# ── PHASE WATCHDOG WINDOWS (frames @ 60 Hz) ──
# The gate-driven phases (RELEASE / PTB_PREP / PTB_EXEC) used to share a
# single 300-frame (5.0 s) force-transition countdown, so one failed
# readiness gate could add ~5 s to each phase and the tackle chain tail
# reached 13.72 s.  Each phase now has its own explicit, named window.
#
# RELEASE: 114 frames (~1.90 s) AFTER the 0.10 s convergence floor, so the
# phase can never outlive RELEASE_MAX_DURATION_S (2.00 s).  Measured worst
# convergence is 1.233 s, so this only ever fires on a genuine outlier.
# The RELEASE exit itself is the convergence predicate; this is the
# watchdog.
RELEASE_WATCHDOG_FRAMES: int = max(
    1,
    int(round((RELEASE_MAX_DURATION_S - RELEASE_MIN_CONVERGENCE_S) * 60.0)),
)
# PTB_PREP / PTB_EXEC windows are derived from the PTBManager constants so
# there is exactly one source of truth for each phase's budget.
PTB_PREP_WATCHDOG_FRAMES: int = max(1, int(round(PTB_PREP_MAX_S * 60.0)))
PTB_EXEC_WATCHDOG_FRAMES: int = max(1, int(round(PTB_EXEC_MAX_S * 60.0)))
# NOTE: the old RELEASE_MIN_DURATION_S (1.54 s wall) and the dead
# RELEASE_PAUSE_S (0.15 s, referenced nowhere) are gone.  RELEASE's floor
# is now RELEASE_MIN_CONVERGENCE_S (imported from world.rules.release_gate)
# and its cap is RELEASE_MAX_DURATION_S / RELEASE_WATCHDOG_FRAMES above.
RELEASE_RECOVERY_SPEED_MPS: float = 1.5  # BC stand-up recovery speed during RELEASE (m/s)
RECOVERY_BACKWARD_DISTANCE_M: float = 0.15  # BC stand-up backward distance from ruck (m). MEDIAN-3m (2026-08-06): was 0.5, reduced so the ruck/PTB anchors sit at the driven position instead of erasing ~0.35 m of forward drive at RELEASE.
POST_PTB_DELAY_S: float = 0.2        # brief pause before POST_RUCK_OPEN_PLAY
RETREAT_DURATION_S: float = 1.0      # how long defenders retreat in POST_RUCK
POST_PTB_COOLDOWN_S: float = 0.5     # tackle immunity after PTB

# -- Section D: TES-phase defender retreat ------------------------------------
RUCK_BUBBLE_RADIUS_M: float = 3.5           # radius of the ruck bubble (m)
RETREAT_COMPLIANCE_DISTANCE_M: float = 10.0 # how far behind ruck defenders retreat
RETREAT_SPEED_FACTOR: float = 0.05          # lerp factor per frame for retreat

# -- Diagnostic tag: SDM/TES retreat movement was not integrated per frame ---
# During TACKLE_DRIVE the role pipeline targets stale ruck_x + 10 while the
# SDM targets live bc_x + 10; the two update() calls cancelled each frame,
# leaving vx != 0 but x unchanged -> freeze audit STUCK_NOT_FROZEN.  The
# TES_RETREAT_INTEGRATION block in update() guarantees retreaters move.
TES_RETREAT_NOT_INTEGRATED: str = "TES_RETREAT_NOT_INTEGRATED"

# -- TES-drive soft-follow gain (2026-08-16) --------------------------------
# During TACKLE_DRIVE / TACKLE_DOMINANCE non-tackler defenders glide toward
# their role-depth targets with a proportional follow:
#     vx = (target_x - x) * TES_DRIVE_FOLLOW_K
#     vy = (target_y - y) * TES_DRIVE_FOLLOW_K
# K is a per-second proportional gain: a 1.0 m gap produces a 3.0 m/s chase
# velocity (soft glide, capped at the player max + defensive ceiling).
TES_DRIVE_FOLLOW_K: float = 3.0

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# LEGACY TACKLE-AUDIT DIAGNOSTICS (Block 1 cleanup)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# The old tackle/PTB/marker/ruck prints were built for legacy geometry and
# produced misleading readings for the new TACKLE SDEA.  Disabled by default.
# Flip to True ONLY for isolated diagnosis.
DEBUG_OPE_TRACE: bool = False

# -- SDEA PHYSICS PIPELINE MINI-AUDIT (CONTACT -> DRIVE -> DOMINANCE) --------
# Per-frame prints for the TES physics pipeline: ENTER_DRIVE baseline, DRIVE
# variation (raw force / momentum / variation_step / bc.x / defender strength
# resistance), DOMINANCE pushback, DRIVE_TOTAL final score, and BC movement
# overwrite checks.  Observational only -- never modifies physics, constants,
# or phase ordering.  Flip to False to silence ALL SDEA_PHYSICS prints.
DEBUG_SDEA_PHYSICS: bool = True
# -- PTB_PREP Gate constants (Chunk 3) ----------------------------------------
DH_BEHIND_DISTANCE_M: float = 1.0          # DH must be exactly 1m behind BC
DH_POSITION_TOLERANCE_M: float = 0.55      # how close DH must be to 1m target
DH_LATERAL_TOLERANCE_M: float = 0.60       # max lateral offset from ruck_y
DH_VELOCITY_STABLE_THRESHOLD_MPS: float = 1.2  # max DH speed to be considered stable
DH_VELOCITY_STABLE_EXEC_THRESHOLD_MPS: float = 0.9  # slightly looser exec gate so PTB resolves faster

# -- DH PRE-POSITION during the tackle (2026-09-16) ---------------------------
# The DH is selected at tackle entry (_assign_dh_for_tackle) and the movement
# contract already grants Role.DUMMY_HALF movement authority in every TES
# phase, but NO writer ever integrated it: `dh.update(dt)` ran only in
# _update_release and _update_ptb.  The DH therefore stood still for the whole
# tackle and had to cover its entire approach INSIDE RELEASE at the fixed
# 4.0 m/s RELEASE writer speed.  Because _check_dh_ready_for_ptb() also
# requires |speed| <= DH_VELOCITY_STABLE_THRESHOLD_MPS, RELEASE could not exit
# until the DH had both ARRIVED and DECAYED, so the phase length became
# (travel / 4.0) + decay -- ~3.1-3.5 s normally, 6.4 s when the nearest
# attacker was ~25 m from the ruck.
# These constants drive the DH toward bc.x - DH_BEHIND_DISTANCE_M while the
# tackle is still resolving, so the approach is complete before RELEASE.
DH_PREPOSITION_SPEED_MPS: float = 4.0          # base DH approach speed during the tackle
DH_PREPOSITION_SPRINT_MPS: float = 6.5         # cap while the DH is still far out
DH_PREPOSITION_SPRINT_DISTANCE_M: float = 6.0  # gap above which the sprint cap applies
DH_PREPOSITION_TAPER_M: float = 2.0            # start easing the approach inside this gap
DH_PREPOSITION_TAPER_MPS: float = 1.0          # approach speed at the target (gate-friendly)
DH_PREPOSITION_TARGET_SOURCE: str = "dh_preposition"  # audit tag for the writer

# -- DH OPEN_PLAY SUPPORT (minimum change) ------------------------------------
# While the play is live in OPEN_PLAY and no TES / RELEASE / PTB authority is
# active, the DH (an attacker, exempt from every defensive recovery writer)
# must keep trailing the live ball carrier.  OPEN_PLAY is exactly where a
# missed tackle lands: _handle_broken_tackle() exits straight to OPEN_PLAY and
# NEVER runs the RELEASE teardowns, so a stale tackle-era "dh_preposition"
# target can survive the miss and leave the DH holding a dead point.  This
# writer re-owns the DH target as the FINAL write of each OPEN_PLAY frame.
DH_OPEN_PLAY_SUPPORT_TARGET_SOURCE: str = "dh_open_play_support"

# A/B / rollback lever, read at consumption time so one process can A/B it.
DH_OPEN_PLAY_SUPPORT_ENV_VAR: str = "RLM_DH_OPEN_PLAY_SUPPORT"


def dh_open_play_support_enabled() -> bool:
    """Whether the OPEN_PLAY DH support owner runs.

    Default ON.  ``RLM_DH_OPEN_PLAY_SUPPORT=0`` (or false/no/off/empty)
    disarms it, restoring the pre-change engine exactly.
    """
    return os.environ.get(
        DH_OPEN_PLAY_SUPPORT_ENV_VAR, "1"
    ).strip().lower() not in ("0", "false", "no", "off", "")

# -- Marker constants (Engine-Spec X-Axis Alignment) --------------------------
MARKER_A_DEPTH_M: float = 1.0    # Marker A target = ruck_x + 1
MARKER_B_DEPTH_M: float = 2.0    # Marker B target = ruck_x + 2
MARKER_MOVE_SPEED_MPS: float = 4.0      # speed when moving to X-axis position

# -- Marker freeze duration (seconds after PTB) -------------------------------
# Frozen markers: no movement, no physics forces, no collision.
# REDUCED 1.0 -> 0.3 (2026-08-16): the phase handlers pin
# marker_freeze_timer to this value at PTB completion.  0.3s keeps markers
# from collapsing the ruck on the DH escape step while letting the peel
# begin almost immediately (DH-readiness/peel-timing tests confirmed safe).
MARKER_FREEZE_DURATION_S: float = 0.3

# -- Defender post-catch speed cap window (seconds) ---------------------------
# The post-catch clamp is armed at reception (catch_system, 3.0s) and can be
# TOPPED-UP by _stamp_defensive_speed_caps during PTB-era phases while the
# window is still burning down.  TOP-UP-ONLY (2026-08-15, measured): the
# previous unconditional re-arm refreshed the window every PTB-era frame, so
# it never expired during a match (probe: armed from catch frame 162 through
# frame 2081).  With the live cap (8.0 vs ceiling 8.3) that was invisible,
# but a return to a lower cap value would throttle the line permanently.
# step_defence_reaction() only runs inside OPEN_PLAY, so the window burns
# down to zero after the catch and stays zero.
PTB_SPEED_CAP_WINDOW_S: float = 3.0


def _move_toward(p: Player, tx: float, ty: float, spd: float, dt: float) -> None:
    """Simple velocity-based movement toward (tx, ty)."""
    dx = tx - p.x
    dy = ty - p.y
    dist = math.hypot(dx, dy)
    if dist > 0.01:
        p.vx = (dx / dist) * spd
        p.vy = (dy / dist) * spd
        p.x += p.vx * dt
        p.y += p.vy * dt
    else:
        p.vx = 0.0
        p.vy = 0.0


# ------------------------------------------------------------------
# PHASE 1 â€” ATTACH GEOMETRY HELPERS
# ------------------------------------------------------------------

def _slot_dir(slot: int) -> str:
    """Return the slot direction key for an attach slot index.

    slot 0 -> "left", slot 1 -> "right", slot 2 -> "back".
    """
    if slot == 0:
        return "left"
    elif slot == 1:
        return "right"
    return "back"


def _slot_magnitude(slot: int) -> float:
    """Return the base offset magnitude for an attach slot (metres)."""
    if slot == 0:
        return ATTACH_SLOT_LEFT_OFFSET_M
    elif slot == 1:
        return ATTACH_SLOT_RIGHT_OFFSET_M
    return ATTACH_SLOT_BACK_OFFSET_M


def _rotate_offset(slot_dir: str, mag: float, facing: float):
    """Rotate a slot offset (relative to BC facing) into world space.

    Returns (dx, dy).  Left = facing +90Â°, right = facing -90Â°,
    back = facing +180Â°.  This keeps "left/right/back" tracking the
    BC's actual orientation during CONTACT/DRIVE.
    """
    if slot_dir == "left":
        angle = facing + math.pi / 2.0
    elif slot_dir == "right":
        angle = facing - math.pi / 2.0
    else:  # back
        angle = facing + math.pi
    return (math.cos(angle) * mag, math.sin(angle) * mag)


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# OpenPlayEngine
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

class OpenPlayEngine:
    """
    Single FSM for open-play -> tackle -> PTB -> post-ruck -> open-play.

    Internal phases (self._phase):
        OPEN_PLAY,
        TACKLE_INIT, TACKLE_CONTACT, TACKLE_DRIVE, TACKLE_DOMINANCE,
        RELEASE,
        PTB_PREP, PTB_EXEC, PTB,
        POST_RUCK_OPEN_PLAY

    Each maps to a GameSubState for downstream consumption (defence, markers, etc.).
    DEFENCE DISPATCH CONTRACT (ENGINE LAW): defence AI runs ONLY during OPEN_PLAY.
    The call to _update_defence() in update() is gated behind
    `if self._phase == "OPEN_PLAY"`.  During every TES phase (TACKLE_INIT /
    CONTACT / DRIVE / DOMINANCE), RELEASE, PTB_PREP/EXEC/PTB, and
    POST_RUCK_OPEN_PLAY, the defence subsystems are dormant: TES owns all
    movement until the RELEASE -> OPEN_PLAY transition, and the phase handlers
    (_update_release, _update_ptb_prep, etc.) move only their owned actors
    (BC, DH, markers).
    """

    def __init__(self, world: WorldState, seed=None):
        self.world = world
        self.rng = random.Random(seed)

        # -- Delegates --
        self.ptb_manager = PTBManager()
        self.dh_controller = DummyHalfController()

        # -- Internal phase machine: always OPEN_PLAY from frame 0 --
        self._phase = "OPEN_PLAY"
        self._phase_timer: float = 0.0

        # -- Tackle / TES state --
        self.ball_carrier: Player | None = None
        self.primary: Player | None = None       # primary tackler
        self.joiners: list[Player] = []           # secondary tacklers
        self.tes_ctx: TESContext | None = None
        self.tes_outcome: TESOutcome | None = None

        # -- Cooldown / immunity --
        self._cooldown_timer: float = 0.0
        # STAGE 3.5: remaining seconds of the carrier's post-bump-off burst.
        # 0.0 whenever no BUMP_OFF clearance is in flight, so the tick that
        # reads it is a no-op on every other frame.
        self._bump_carrier_burst_s: float = 0.0
        self.frames_since_ptb: int = 0
        # STAGE 3F: time-based twin of frames_since_ptb.  Frame-count gates
        # made spacing/pacing inherit nondeterministic frame drift; every
        # consumer of this clock now reads the seconds accumulator.
        self.seconds_since_ptb: float = 0.0

        # -- Post-PTB scoot system: reset on PTB complete --
        self._ptb_complete_handled: bool = False

        # -- PTB gate safety timeout: force-transition after N frames if gate blocks --
        self._ptb_gate_override_frames: int = 999999  # reset per phase entry

        # -- Phase-4D-2: tackle approach micro-state flag --
        self.bc_in_approach = False

        # -- PHASE 1: ATTACH state (seeded at _start_tackle) --
        # Tracks whether the attach system has been armed for the current
        # tackle.  Reset at _start_tackle().
        self._attach_armed: bool = False
        # Maps a tackler player_id -> the attach slot (0/1/2) they hold.
        # Mirrors TESContext.attached for fast per-player lookups during
        # the DRIVE-follow step.
        self._attached_player_slots: dict[int, int] = {}

        # --- Phase 4 HS Attack Modules -------------------------------------------
        self.attack_geometry = AttackGeometry(self)
        self.attack_perception = AttackPerception(self.attack_geometry)
        self.ball_carrier_intent = BallCarrierIntent()
        self.run_logic = RunLogic()

        # -- Phase-2 defence tuners --
        self.def_tuners = DefenceTuners()
        self.def_tuners.assert_radii()

        # -- Phase-2 defence (unified LvlÃ¢â‚¬â€˜1 / STEPÃ¢â‚¬â€˜2 behaviour) --
        self.def_behaviour = DefensiveBehaviourUnified(self.def_tuners)

        # -- Phase-E STEPÃ¢â‚¬â€˜1 geometry layer --
        self.def_geometry = DefensiveGeometry(self.def_tuners)

        # -- DH reference (set during RELEASE, used through PTB) --
        self.dh: Player | None = None

        # -- D-Series: Defensive line manager --
        self.defensive_line_manager = DefensiveLineManager(self.world)

        # -- RELEASE first-frame detection flag --
        self._just_entered_release: bool = False

        # -- Freeze audit agent (debug diagnostics) --
        self.freeze_audit = FreezeAndMovementAuditAgent()

        # -- Line-break detection flag --
        self._line_break_active: bool = False

        # -- Deferred D-series state moved to DefenceManager (2026-08-24) --
        # pending_anchor_x / pending_spacing_y / pending_retreat_reset now
        # live on self.defence_manager (game/managers/defence_manager.py).

        # -- MACRO MANAGERS (2026-08-24): sole-order authorities. --
        # DefenceManager owns ALL defensive order issuance; AttackManager
        # owns attacking orders. Players remain dumb executors.
        # See game/managers/.
        self.defence_manager = DefenceManager(self.def_tuners, self.def_behaviour)
        self.attack_manager = AttackManager()

        # -- Attack controller (geometry-driven steering, replaces AttackerAI + DecisionEngine) --
        # TASK XIII: the controller shares the manager's structure tuners so
        # the BC-steering stabiliser (cone hysteresis / lane deadband / gap
        # commit / escape-exit ramp / vector-flip audit) is fed the SAME
        # gate + parameters as the authored-lane authority.
        self.attack_controller = AttackController(
            self.attack_geometry,
            self.attack_perception,
            self.run_logic,
            steer_tuners=self.attack_manager.structure_tuners,
        )

    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
    # MARKER HELPERS (Engine-Spec X-Axis Alignment)
    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

    def _get_ruck_x(self) -> float:
        """Return current ruck X or a fallback."""
        ruck_x = getattr(self.world, "ruck_x_m", None)
        if ruck_x is not None:
            return ruck_x
        bc = self.ball_carrier
        if bc is not None:
            return bc.x
        return 60.0

    def _get_ruck_y(self) -> float:
        """Return current ruck Y or a fallback."""
        ruck_y = getattr(self.world, "ruck_y_m", None)
        if ruck_y is not None:
            return ruck_y
        bc = self.ball_carrier
        if bc is not None:
            return bc.y
        return 35.0

    def _marker_target_a(self) -> tuple[float, float]:
        """Target position for Marker A: the ruck-pin geometry authority.

        MARKER AUTHORITY LOCK (2026-09-04): Marker A = the single X-depth
        ruck pin (ruck_x + MARKER_A_DEPTH_M, ruck_y) — never BC-relative.
        """
        from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
        _proxy = type("_MarkerA", (), {"defender_role": DefenderRole.MARKER_A})
        return _mt(self.world, _proxy())

    def _marker_target_b(self) -> tuple[float, float]:
        """Target position for Marker B: the ruck-pin geometry authority.

        MARKER AUTHORITY LOCK (2026-09-04): Marker B = the single X-depth
        ruck pin (ruck_x + MARKER_B_DEPTH_M, ruck_y) — never BC-relative.
        """
        from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
        _proxy = type("_MarkerB", (), {"defender_role": DefenderRole.MARKER_B})
        return _mt(self.world, _proxy())

    def _get_marker_target(self, player: Player) -> tuple[float, float] | None:
        """Return (target_x, target_y) for a marker, or None if not a marker."""
        role = getattr(player, "defender_role", None)
        if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
            # MARKER AUTHORITY LOCK (2026-09-04): route through the single
            # ruck-pin geometry authority — never BC-relative.
            from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
            return _mt(self.world, player)
        return None

    def _get_markers(self) -> list[Player]:
        """Return list of current marker players from defending team."""
        markers = []
        for d in self.world.defending_team:
            role = getattr(d, "defender_role", None)
            if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                markers.append(d)
        return markers

    def _def_tes_line_speed(self) -> float:
        """Unified speed cap for non-tackler line movement during TES.

        The line retreats at the same pace the rest of the engine uses
        (85% of the unified defensive ceiling, matching the mandatory
        retreat writer in DefensiveBehaviourUnified).  Each defender's
        physical max is additionally respected in the caller via
        Player.update()'s own caps.
        """
        ceiling = getattr(
            self.def_tuners, "max_defensive_speed_mps", 8.3
        )
        return ceiling * 0.85

    # ------------------------------------------------------------------
    # RUCK vs OPEN-PLAY TACKLER SELECTION â€” CANDIDATE POOL (2026-08-05)
    # ------------------------------------------------------------------

    def _build_tackler_candidates(self, bc: Player) -> list[tuple[Player, float, float, str]]:
        """Build the ranked tackler candidate pool for the NEXT tackle.

        Spec (RUCK vs OPEN-PLAY PRIORITY):
          - Markers (MARKER_A/B) are eligible when within MARKER_TACKLE_RADIUS_M
            of BC, not frozen, not in RELEASE/PTB freeze, and not clearly
            behind BC.
          - Defensive-line defenders are eligible when within
            LINE_TACKLE_RADIUS_M of BC, moving toward BC, not retreating,
            not frozen, and not markers.
          - Priority weight depends on BC location relative to the ruck:
              * Ruck zone (|bc.x - ruck_x| <= RUCK_ZONE_RADIUS_M) favours
                markers.
              * Open play (bc.x > ruck_x + RUCK_ZONE_RADIUS_M) favours line
                defenders.
          - Neither group is ever exclusive.

        Returns a list of tuples:
            (player, priority_score, distance_to_bc, kind)
        sorted by priority_score ascending (lower = higher priority).
        """
        ruck_x = self._get_ruck_x()
        in_ruck_zone = abs(bc.x - ruck_x) <= RUCK_ZONE_RADIUS_M
        candidates: list[tuple[Player, float, float, str]] = []

        for d in self.world.defending_team:
            if d is bc:
                continue
            if getattr(d, "fullback_role_active", False):
                # FULLBACK EXCLUSION (2026-08-13): the dynamic fullback is a
                # backfield anchor â€” it never enters the tackler candidate
                # pool, so it can never be pulled into a ruck/tackle.
                continue
            if getattr(d, "defender_role", None) == DefenderRole.DUMMY_HALF:
                continue
            if getattr(d, "frozen", False):
                continue
            dist = math.hypot(d.x - bc.x, d.y - bc.y)
            role = getattr(d, "defender_role", None)

            # â”€â”€ MARKER POOL â”€â”€
            if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                # Not in RELEASE/PTB freeze (marker lock window).
                if getattr(d, "marker_freeze_timer", 0.0) > 0.0:
                    continue
                # Not clearly behind BC.
                if bc.x - d.x > 0.5:
                    continue
                if dist > MARKER_TACKLE_RADIUS_M:
                    continue
                zone_weight = -2.0 if in_ruck_zone else -0.5
                movement_bonus = self._candidate_movement_bonus(d, bc, dist)
                score = dist + zone_weight + movement_bonus
                candidates.append((d, score, dist, "MARKER"))
                continue

            # â”€â”€ LINE POOL (all other non-marker defenders) â”€â”€
            if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                continue
            if getattr(d, "retreating", False):
                continue
            if role == DefenderRole.DEF_RETREAT_10:
                continue
            if dist > LINE_TACKLE_RADIUS_M:
                continue
            if not self._is_moving_toward_bc(d, bc, dist):
                continue
            zone_weight = -0.5 if in_ruck_zone else -2.0
            movement_bonus = self._candidate_movement_bonus(d, bc, dist)
            score = dist + zone_weight + movement_bonus
            candidates.append((d, score, dist, "LINE"))

        candidates.sort(key=lambda item: (item[1], item[2]))
        return candidates

    @staticmethod
    def _is_moving_toward_bc(d: Player, bc: Player, dist: float) -> bool:
        """True if *d* has a velocity component toward BC.

        DELIBERATELY LENIENT so the line pool is never starved:
          - A defender already very close to BC (< 1.0 m) is accepted
            regardless of velocity (they are in the tackle window).
          - A nearly-stationary defender (speed < 0.3 m/s) is accepted â€”
            too slow to judge direction, and post-PTB line defenders are
            often just standing in position during the hold window.
        """
        if dist < 1.0:
            return True
        spd = math.hypot(getattr(d, "vx", 0.0), getattr(d, "vy", 0.0))
        if spd < 0.3:
            return True
        dot = (
            (d.vx / spd) * ((bc.x - d.x) / dist)
            + (d.vy / spd) * ((bc.y - d.y) / dist)
        )
        return dot > 0.0

    @staticmethod
    def _candidate_movement_bonus(d: Player, bc: Player, dist: float) -> float:
        """Movement bonus for the priority score (lower is better).

        +1.0 if the defender is closing hard toward BC,
        +0.5 if already moving toward BC,
        +0.0 otherwise.
        """
        spd = math.hypot(getattr(d, "vx", 0.0), getattr(d, "vy", 0.0))
        if spd < 0.3 or dist < 0.01:
            return 0.0
        dot = (
            (d.vx / spd) * ((bc.x - d.x) / dist)
            + (d.vy / spd) * ((bc.y - d.y) / dist)
        )
        if dot <= 0.0:
            return 0.0
        if spd >= 5.0 and dot > 0.3:
            return 1.0
        return 0.5

    def _move_markers_to_targets(self, dt: float) -> None:
        """Set target_x/target_y for both markers every frame.

        Marker A: (ruck_x + 1.0, ruck_y)
        Marker B: (ruck_x + 2.0, ruck_y)

        Only sets targets Ã¢â‚¬â€ Player.update(dt) moves them.
        Called from RELEASE, PTB_PREP, PTB_EXEC every frame.
        """
        for p in self._get_markers():
            target = self._get_marker_target(p)
            if target is not None:
                p.target_x = target[0]
                p.target_y = target[1]
                stamp_target_write(self.world, p, "ope_markers")
            p.move_speed = MARKER_MOVE_SPEED_MPS

    def _unfreeze_markers(self) -> None:
        """Unfreeze all markers so they can move to X-axis positions.

        NOTE: After this runs, ``apply_phase_invariants()`` will re-evaluate
        freeze state for the RELEASE phase.  This method is only needed to
        signal intent Ã¢â‚¬â€ the freeze authority is ``apply_phase_invariants()``.
        """
        # Legacy unfreeze removed Ã¢â‚¬â€ apply_phase_invariants() owns freeze now.
        pass

    def _clear_marker_roles(self) -> None:
        """Convert remaining MARKER_A/B to DEF_LINE_READY, then release the
        deferred DEF_RETREAT_10 flip.

        Called at POST_RUCK_OPEN_PLAY entry after freeze timer expires.
        After converting the markers, any body flagged _defer_retreat_10
        (old markers / non-selected tacklers held as DEF_LINE_READY through
        RELEASE -> PTB so the ruck-pin could own them) now flips to
        DEF_RETREAT_10 — the retreat-to-10m rollout delayed until after PTB.
        """
        for d in self.world.defending_team:
            if d.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                d.defender_role = DefenderRole.DEF_LINE_READY
            # DEFERRED DEF_RETREAT_10 FLIP (2026-09-03)
            if getattr(d, "_defer_retreat_10", False):
                d.defender_role = DefenderRole.DEF_RETREAT_10
                d._defer_retreat_10 = False
        # STAGE 1 MARKER DECISION LOCK CLEANUP (2026-09-05): markers are
        # converting to line roles — drop their lock / snap state so stale
        # entries never leak across the marker → line-role boundary.
        from game.defence.defence_lvl_1.marker_decision_lock import clear_marker_lock
        clear_marker_lock(self.world, self.world.defending_team)

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # POST-PTB SCOOT SYSTEM â€” marker lock + counter reset
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

    def _on_ptb_complete(self) -> None:
        """Called when PTB completes â€” start marker lock timer and reset frame counter.

        Resets world.marker_freeze_timer to 0.0 so markers are re-frozen
        for `marker_lock_duration_s` seconds.  Resets self.frames_since_ptb
        and self.seconds_since_ptb (Stage 3F) so the escape window in
        AttackController is correctly timed.
        """
        self.world.marker_freeze_timer = 0.0
        self.frames_since_ptb = 0
        self.seconds_since_ptb = 0.0
        self._ptb_complete_handled = True
        if DEBUG_OPE_TRACE:
            print(f"[SCOOT] PTB complete â€” marker freeze timer reset, frames_since_ptb=0")

    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
    # TES ARTEFACT CLEARANCE Ã¢â‚¬â€ wipe all TES state before RELEASE
    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

    def _clear_tes_artefacts(self) -> None:
        """Wipe ALL TES artefacts from world and players at RELEASE entry.

        Called ONCE when RELEASE phase begins (from _update_release first
        frame, via _just_entered_release path).  Ensures:

          1. No TES movement targets survive into RELEASE
          2. No TES freeze flags remain on non-mover players
             (movers will be set correctly by the RELEASE freeze controller)
          3. All TES soft-freeze counters reset to zero
          4. All TES retreat targets cleared from world
          5. All TES defensive line anchors cleared
          6. All TES marker roles cleared (fresh assignment was done by
             _assign_markers_for_new_tackle in _prepare_release())
          7. All TES tackler roles cleared
          8. All TES freeze timers reset
          9. world.tes_active = False (TES shuts down)
        """
        # 1-2. Clear per-player TES artefacts
        for p in self.world.players:
            # Clear TES movement targets Ã¢â‚¬â€ NOT pinned here; RELEASE/attack
            # controllers set fresh targets each frame.
            # p.target_x and p.target_y intentionally NOT pinned.
            # Clear TES freeze flags Ã¢â‚¬â€ will be re-applied by RELEASE freeze controller
            # Do NOT set frozen=False here Ã¢â‚¬â€ the RELEASE freeze controller will decide
            # Clear any lingering TES flags
            p.is_tackler = False
            p.in_contact = False
            p.tackle_locked = False
            p.tes_locked = False
            # TASK III (2026-08-14): wipe the OPEN_PLAY tackler-commit
            # flag at RELEASE.  The commit writer stamps _tackle_commit_active
            # on PRIMARY/SECONDARY tacklers in the OPEN_PLAY / TES-DRIVE
            # structured frames; without a wipe the flag survives into
            # RELEASE where the role pipeline (apply_role_movement) and the
            # P7 retreat writers must own the ex-tacklers.  A stale commit
            # flag made the new TASK_III guards (post-dispatch overwrite
            # block, stabiliser skip, reactivate skip) keep treating the
            # ex-tackler as a live committer through the whole PTB era.
            p._tackle_commit_active = False
            # PHASE 3 â€” TACKLER ROLE RESET (spec G): no defender keeps a
            # TES tackler role into the next tackle cycle.  Markers revert
            # to MARKER_A/B (assigned in _prepare_release) and defensive-line
            # defenders become eligible again on the next OPEN_PLAY.
            p.tes_tackler_role = None
            # NOTE (2026-08-07, corrected spec): the per-tackle TES
            # snapshot/latch artefacts (tes_role_depth_m, tes_ruck_x_snapshot,
            # tes_retreat_active) no longer exist â€” the TES-specific defensive
            # movement spec was removed from this engine.  Non-ruck defenders
            # keep their open-play roles through the drive and only retreat at
            # RELEASE via DEF_RETREAT_10.  No artefact clearing is needed.
            # CONTRACT G â€” MOVEMENT MODE RESET: TACKLER mode exits at
            # RELEASE.  Every ex-tackler returns to RETREAT movement
            # (contract: defender.movement_mode = RETREAT, speed_target =
            # retreat_speed, lunge_active = False).  collision_priority is
            # cleared so the defender is no longer a collision initiator.
            # Non-tackler players whose mode is already NORMAL/None are
            # left untouched.
            if getattr(p, "movement_mode", None) is MovementMode.TACKLER:
                p.movement_mode = MovementMode.RETREAT
                p.speed_target = 0.0
                p.lunge_active = False
                p.lunge_speed = 0.0
                p.lunge_duration = 0.0
            p.collision_priority = None
            # Clear freeze timers
            p.freeze_timer = 0.0
            # Clear soft-freeze stuck counter attribute if it exists
            if hasattr(p, "_soft_freeze_counter"):
                p._soft_freeze_counter = 0
            # ─ CONSUME FLIP MARKER → force innermost re-seed ─
            # Any defender flagged _flipped_in_release was flipped to the
            # other side of the ruck at RELEASE entry.  Force it into the
            # A band (innermost) on its new side on the next OPEN_PLAY frame.
            if getattr(p, '_flipped_in_release', False):
                p._flipped_in_release = False   # single-use: clear immediately
                p._force_innermost_seed = True
                p._depth_seeded = False
                p.depth_role = None
                # Clear all target/movement locks so the flipped
                # defender's first OPEN_PLAY frame is authored entirely
                # by the spacing/geometry pipeline — no stale pre-TES
                # baselines survive into the reseed.
                p.target_x = p.x
                p.target_y = p.y
                p.vx = 0.0
                p.vy = 0.0
                p.movement_state = None
                p._mmdl_state_until_frame = -1
                p._mmdl_moved = False
                p.timing_locked = False
                p.reaction_timer = 0.0
                p.retreat_delay = 0.0
                p.lateral_hesitation = 0.0
                p.step_stagger = 0.0
                p.step_stagger_active = False
                # Clear all finalise / close / stabiliser blend
                # baselines so the target_stabiliser starts from the
                # current position — no half-blended lag.  Delete the
                # attributes so getattr fallbacks (e.g. the close
                # writer's `_prev_close_x, d.target_x`) work correctly.
                for _attr in (
                    "_prev_final_x", "_prev_final_y",
                    "_prev_close_x", "_prev_target_y_smooth",
                ):
                    if hasattr(p, _attr):
                        delattr(p, _attr)
                if hasattr(p, '_target_stabiliser') and p._target_stabiliser is not None:
                    p._target_stabiliser.reset()
                # Flag so the MMDL target_stabiliser knows this defender
                # was reseeded this frame — spacing Y is authoritative.
                p._force_reseed_this_frame = True
                if DEBUG_OPE_TRACE:
                    print(f'[RELEASE] consumed flip marker id={getattr(p, "player_id", "?")}'
                          f' -> _force_innermost_seed=True _depth_seeded=False')
            # Clear any TES-era attack_role overrides (keep BC/DH)
            if getattr(p, "attack_role", "none") in ("none",):
                pass  # keep as-is
            # FULLBACK IDENTITY PERSISTS (2026-08-13): the dynamic fullback
            # role is NOT cleared at RELEASE.  Wiping ``fullback_role_active``
            # here made the engine forget who the FB was through RELEASE ->
            # PTB -> POST_RUCK, so the un-gated depth-role preamble stamped
            # a line role on the actor and the P7 retreat writers dragged it
            # onto the line.  The identity now survives the whole tackle
            # cycle; every line/retreat/marker/tackler writer is FB-guarded
            # and the phase handlers re-assert the deep anchor each frame.
            # The flag is only reset by kickoff player recreation or by
            # absorption into the line.

        # ── MOVEMENT-STATE ASSIGNMENT (RELEASE ENTRY, 2026-09-01) ──
        # Any defender surviving TES into RELEASE carries a stale-or-None
        # movement_state from the last OPEN_PLAY MMDL frame (or a flip-marker
        # None).  That stale state is the residual STUCK source in the
        # TES→RELEASE→PTB window: the retreat/marker writers set fresh
        # target_x/target_y but never movement_state, so a body not routed
        # through apply_forced_retreat (markers, already-compliant
        # DEF_RETREAT_10/DEF_LINE_READY, parked ex-tacklers) keeps HOLD/CHASE
        # residue and can fail to produce motion.  Stamp an explicit
        # movement_state per role here so no defender enters RELEASE/PTB with
        # stale state.  The fullback keeps its geometry-owned DRIFT identity
        # (its anchor writer owns positioning); BC/DH are untouched (their
        # RELEASE writers own them).
        # ── FULLBACK EMERGENCY ENGAGEMENT RESET (B2b, 2026-09-16) ──
        # The emergency fullback was armed as a TES tackler on a genuine line
        # break and KEPT its tag (line membership is never forfeited).  The
        # tackle cycle is now over, so the engagement marker is dropped and
        # the body is handed back to its sweeper contract: DEF_LINE_READY (a
        # legal mover in every phase) plus a cleared tackle-state hold.  The
        # TAG IS NOT CLEARED -- the actor is still the fullback and will be
        # re-asserted onto its deep anchor by _reassert_fullback below.
        for _p in self.world.defending_team:
            _fb_arm_post_tackle_return(_p)
            _fb_reset_engagement(_p)
        # THE BREAK LATCH IS TACKLE-CYCLE STATE (2026-09-28): drop it for every
        # tagged fullback at the SAME explicit cycle end that releases the
        # engagement.  ``reset_fullback_emergency_engagement`` covers a single
        # body and only when it was engaged; this sweep covers the whole tagged
        # population, so a latched-but-never-engaged chase is released too and
        # no latch can survive the cycle it was armed in.
        _fb_clear_chase_state(self.world)

        for _p in self.world.defending_team:
            if getattr(_p, "fullback_role_active", False):
                _p.movement_state = MovementState.DRIFT
                continue
            _p_role = getattr(_p, "defender_role", None)
            if _p_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                _p.movement_state = MovementState.RETREAT
            elif _p_role == DefenderRole.DEF_CHASE:
                _p.movement_state = MovementState.CHASE
            elif _p_role == DefenderRole.DUMMY_HALF:
                # DH owned by PTBManager during RELEASE→PTB; leave as-is
                pass
            else:
                # DEF_RETREAT_10 / DEF_LINE_READY / any converted ex-tackler
                # or line body: everything retreats to the anchor, so RETREAT
                # is the state for the whole transition window.
                _p.movement_state = MovementState.RETREAT

        # 3. Reset soft-freeze stuck counters
        if hasattr(self, "stuck_counters"):
            self.stuck_counters.clear()

        # 4. REPUBLISH the defensive line anchor (FIX 2026-08-08): zeroing it
        # here left world.defensive_line_anchor = 0.0 for the entire
        # RELEASE/PTB era â€” every PTB-era reader (SDM line depth, geometry,
        # fullback anchor) sees 0.0, collapsing spacing/depth and making the
        # fullback anchor misread (root cause #6).  _prepare_release() has
        # already published ruck_x + 10 before this runs; re-publish the live
        # ruck anchor instead of zeroing.  The OPEN_PLAY-only republish in
        # _update_defence() cannot fix PTB-era reads.
        # TASK G ANCHOR AUTHORITY: this republish goes through the authority
        # (idempotent â€” same ruck_x + 10 â€” so the locked anchor is not
        # mutated; a different value would raise AnchorAuthorityViolation).
        ruck_x_clear = getattr(self.world, "ruck_x_m", None)
        compute_and_lock_anchor(self.world)
        if ruck_x_clear is None and getattr(self.world, "defensive_line_anchor", None) is None:
            self.world.defensive_line_anchor = 0.0
            self.world.anchor_locked = False

        # 5. Clear defensive line anchors (already done above)

        # 6. TES tackler roles already cleared per-player above
        # 7. TES freeze timers already cleared per-player above

        # 8. Clear the TES tackler registry (tacklers become markers/retreaters)
        if hasattr(self.world, "tackler_ids"):
            self.world.tackler_ids.clear()

        # 9. Shut down TES
        self.world.tes_active = False

        if DEBUG_OPE_TRACE:
            print("[TES] cleared TES artefacts at RELEASE entry")
    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

    def _assign_markers_for_new_tackle(self, strict_tacklers_only: bool = False) -> None:
        """DELEGATE (2026-08-24): implementation owned by DefenceManager.

        MARKER LOCK (2026-09-04): when ``strict_tacklers_only`` is True
        (used at TACKLE_CONTACT), only the CASE-1 pair (2+ confirmed TES
        tacklers) is assigned — a non-tackler is never promoted into a
        marker role early.  If fewer than 2 tacklers are confirmed,
        nothing is assigned here and the full 3-case rule runs at RELEASE
        (_prepare_release) instead.
        """
        self.defence_manager.assign_markers_for_new_tackle(
            self, strict_tacklers_only=strict_tacklers_only
        )

    def _disabled_assign_markers_impl(self) -> None:
        """Assign fresh markers for this tackle cycle.

        Clears ALL existing MARKER_A/B roles first, then applies the
        3-case rugby-league rule:

          CASE 1 (2+ tacklers):  Marker A = tackler[0], Marker B = tackler[1]
          CASE 2 (1 tackler):    Marker A = tackler,   Marker B = closest defender
          CASE 3 (0 tacklers):   Marker A/B = two closest defenders to ruck

        Old markers not selected become DEF_RETREAT_10 (unfrozen).
        Called ONCE from _prepare_release() at DOMINANCE -> RELEASE boundary.

        This replaces the old persistent-marker pattern where markers from
        Tackle N survived into Tackle N+1 and overwrote fresh assignment.
        """
        defenders = self.world.defending_team
        ruck_x = self.world.ruck_x_m
        ruck_y = self.world.ruck_y_m

        # Ã¢â€â‚¬Ã¢â€â‚¬ Step 1: Strip ALL stale MARKER_A/B roles Ã¢â€â‚¬Ã¢â€â‚¬
        for d in defenders:
            if d.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                d.defender_role = DefenderRole.DEF_RETREAT_10
                d.freeze_timer = 0.0

        # Ã¢â€â‚¬Ã¢â€â‚¬ Step 2: Identify tacklers (TACKLER_1/2/3 roles from TES) Ã¢â€â‚¬Ã¢â€â‚¬
        tacklers = [d for d in defenders
                    if d.defender_role in (DefenderRole.TACKLER_1,
                                           DefenderRole.TACKLER_2,
                                           DefenderRole.TACKLER_3)]

        m_a = None
        m_b = None

        # Ã¢â€â‚¬Ã¢â€â‚¬ CASE 1: Two or more tacklers Ã¢â€ â€™ Marker A/B = first two Ã¢â€â‚¬Ã¢â€â‚¬
        if len(tacklers) >= 2:
            m_a = tacklers[0]
            m_b = tacklers[1]

        # Ã¢â€â‚¬Ã¢â€â‚¬ CASE 2: One tackler Ã¢â€ â€™ Marker A = tackler, Marker B = closest defender Ã¢â€â‚¬Ã¢â€â‚¬
        elif len(tacklers) == 1:
            m_a = tacklers[0]
            others = [d for d in defenders if d is not m_a]
            if others:
                m_b = min(others, key=lambda d: abs(d.x - ruck_x))

        # Ã¢â€â‚¬Ã¢â€â‚¬ CASE 3: No tacklers Ã¢â€ â€™ Marker A/B = two closest defenders to ruck Ã¢â€â‚¬Ã¢â€â‚¬
        else:
            sorted_by_ruck = sorted(defenders,
                                    key=lambda d: (d.x - ruck_x) ** 2 + (d.y - ruck_y) ** 2)
            if len(sorted_by_ruck) >= 1:
                m_a = sorted_by_ruck[0]
            if len(sorted_by_ruck) >= 2:
                m_b = sorted_by_ruck[1]

        # Ã¢â€â‚¬Ã¢â€â‚¬ Step 3: Clear stale TACKLER frozen state on non-selected tacklers Ã¢â€â‚¬Ã¢â€â‚¬
        # Tacklers (TACKLER_1/2/3) may still be frozen from TES.
        # If they are NOT selected as a marker, they must be unfrozen
        # and given a proper retreat role.
        for d in tacklers:
            if d is m_a or d is m_b:
                continue  # Will be set to MARKER_* below
            d.defender_role = DefenderRole.DEF_RETREAT_10
            d.freeze_timer = 0.0
            # TACKLE-STATE GATE (2026-08-23): this ex-tackler holds
            # TACKLE-STATE through RELEASE/PTB while inside the
            # tackle-radius; the gate releases it on radius exit.
            stamp_tackle_state(d)

        # ── FLIP MARKER (DOMINANCE→RELEASE) ──
        # Ex-tacklers becoming DEF_RETREAT_10 will re-enter the line on
        # the next OPEN_PLAY frame.  Stamp a single-use flip marker so
        # _clear_tes_artefacts() can force them into the A band (innermost)
        # on their current side.  Idempotent: never double-stamp.
        for d in tacklers:
            if d is m_a or d is m_b:
                continue
            if getattr(d, '_flipped_in_release', False):
                continue  # already stamped
            if getattr(d, 'fullback_role_active', False):
                continue  # never flag fullback
            d._flipped_in_release = True
            # Clear movement/target locks at flip time
            d.target_x = d.x
            d.target_y = d.y
            d.vx = 0.0
            d.vy = 0.0
            d._mmdl_state_until_frame = -1
            d._mmdl_moved = False
            if DEBUG_OPE_TRACE:
                print(f'[RELEASE] flipped defender id={getattr(d, "player_id", "?")}'
                      f' y={d.y:.1f} -> _flipped_in_release=True')

        # Ã¢â€â‚¬Ã¢â€â‚¬ Step 4: Assign roles with temporary freeze Ã¢â€â‚¬Ã¢â€â‚¬
        # Both markers start frozen so OPE's _update_release() can unfreeze
        # them on the first RELEASE frame (same as current behaviour).
        if m_a is not None:
            # MARKER-AUTHORITY SINGLE-SOURCE (2026-09-04): marker ROLE
            # assignment is owned SOLELY by
            # DefenceManager.assign_markers_for_new_tackle.  This dead
            # _disabled_ method MUST NOT write a marker role.
            m_a.freeze_timer = self.def_tuners.MARKER_A_FREEZE_AFTER_PTB
            m_a.target_x = ruck_x + MARKER_A_DEPTH_M
            m_a.target_y = ruck_y
            m_a.move_speed = MARKER_MOVE_SPEED_MPS
            # TACKLE-STATE GATE (2026-08-23): the marker pair holds
            # TACKLE-STATE through PTB while inside the tackle-radius.
            stamp_tackle_state(m_a)

        if m_b is not None:
            # MARKER-AUTHORITY SINGLE-SOURCE (2026-09-04): see m_a above.
            # This dead _disabled_ method MUST NOT write a marker role.
            m_b.freeze_timer = self.def_tuners.MARKER_B_FREEZE_AFTER_PTB
            m_b.target_x = ruck_x + MARKER_B_DEPTH_M
            m_b.target_y = ruck_y
            m_b.move_speed = MARKER_MOVE_SPEED_MPS
            # TACKLE-STATE GATE (2026-08-23): see m_a above.
            stamp_tackle_state(m_b)

        # Ã¢â€â‚¬Ã¢â€â‚¬ Step 5: Sweep Ã¢â‚¬â€ ensure all non-marker defenders are unfrozen Ã¢â€â‚¬Ã¢â€â‚¬
        # Any defender not selected as a marker must be unfrozen so they
        # can move into their retreat/line positions during RELEASE.
        for d in defenders:
            if d.defender_role not in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                d.freeze_timer = 0.0

    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
    # Kickoff hooks
    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

    def reset_for_kickoff(self):
        """Hard-reset ALL engine-owned and world-level play state before a new kickoff.

        Called by KickoffEngine.reset_for_kickoff() on SPACE/R restart.
        The kickoff reset RE-CREATES every Player object, but this OpenPlayEngine
        singleton and its delegates survive â€” without a reset here, stale FSM
        phase, TES context, PTB manager state, governor smoothed width, and
        world-level TES/PTB/geometry fields leak into the next kickoff and
        corrupt the first OPEN_PLAY cycle (defender speed spike, BC freeze,
        DH jiggle, teleports, collapsed clamp).

        This clears ONLY kickoff-relevant state.  It does NOT touch TES/PTB
        logic or timing locks during live play â€” it is called exclusively on
        the SPACE/R restart path.
        """

        # â”€â”€ Engine internal FSM â”€â”€
        self._phase = "OPEN_PLAY"
        self._phase_timer = 0.0
        self._just_entered_release = False
        self._ptb_gate_override_frames = 999999
        self._cooldown_timer = 0.0
        self._bump_carrier_burst_s = 0.0
        self.frames_since_ptb = 0
        self.seconds_since_ptb = 0.0
        self._ptb_complete_handled = False
        self._line_break_active = False
        self.bc_in_approach = False

        # â”€â”€ TES / tackle references (stale Player objects) â”€â”€
        self.ball_carrier = None
        self.primary = None
        self.joiners = []
        self.tes_ctx = None
        self.tes_outcome = None
        self.dh = None

        # Drive-LERP joiner snapshots + broken-check flags
        self._joiner_x0 = {}
        self._joiner_y0 = {}
        if hasattr(self, "_broken_checked_this_contact"):
            self._broken_checked_this_contact = False
        if hasattr(self, "_dominance_computed_for_broken"):
            del self._dominance_computed_for_broken

        # â”€â”€ Delegate caches â”€â”€
        self.ptb_manager.reset()
        self.def_behaviour.line_governor.reset()
        self.defensive_line_manager.reset_retreat_state()
        self.def_behaviour._post_catch_stable_frames = 0

        # â”€â”€ Deferred D-series writes â”€â”€
        self.defence_manager.reset_pending_state()

        # â”€â”€ World-level TES / PTB / SCOREBOARD state â”€â”€
        w = self.world
        w.tes_locked = False
        w.tes_active = False
        if hasattr(w, "tackler_ids"):
            w.tackler_ids.clear()
        # ── CATCH X-AUTHORITY LOCK CLEAR (2026-09-10) ──
        # A fresh kickoff starts with no chase-to-catch transition, so the
        # sole-X-authority lock is dropped before the new sequence.
        from game.defence.defence_lvl_1.catch_x_authority import (
            clear_catch_x_authority as _cxa_clear,
        )
        _cxa_clear(w)

        w.primary_tackler_id = None
        w.secondary_tacklers = []
        w.frames_since_ptb = max(
            w.frames_since_ptb, w.escape_window_frames
        )
        # STAGE 3F: mirror the escape-window force-expiry in seconds so
        # time-based readers see the window as expired too (20 frames @60).
        _escape_window_s = float(getattr(w, "escape_window_frames", 20)) / 60.0
        w.seconds_since_ptb = max(
            getattr(w, "seconds_since_ptb", 999.0), _escape_window_s
        )
        w.marker_freeze_timer = w.marker_lock_duration_s

        # TASK XIV: clear any live pre-tackle attempt state (frozen drive
        # anchors / attempt states / owner registry) so no stale drive
        # contract leaks into the next kickoff's first tackle cycle.
        from game.defence.defence_lvl_1.pretackle_attempt import (
            reset_pretackle_attempts,
        )
        reset_pretackle_attempts(w)

        # RETREAT-OBLIGATION RESET (2026-09-18): nothing to reset.  The
        # obligation is the LIVE ROLE (``DEF_RETREAT_10``), which
        # ``reset_for_kickoff`` has already cleared above by re-roleing
        # every player, so no per-body latch exists to sweep here.

        # TASK XIII ADDENDUM: re-arm the self-limiting DEF_LINE_READY
        # per-frame audit dump so a fresh kickoff captures a fresh drive
        # window (mirrors rearm_defender_state_audit reset law).
        from game.debug.def_line_ready_audit import rearm_def_line_ready_audit
        rearm_def_line_ready_audit()
        # TASK XV: a fresh sequence re-arms the fullback-invariant audit so
        # it captures fresh tackle-entry frames (mirrors the rearm law above).
        rearm_fullback_invariant_audit()
        w.ptb_anchor_x = None
        w.ptb_anchor_y = None
        w.last_contact_x = None
        w.last_contact_y = None
        w.role_governs = False
        w.current_target_writer = ""
        w.first_receiver = None
        # TASK XII: a fresh sequence drops any residual attack-structure
        # state (identity locks, settle window, lane map) and the audit
        # event ring so no stale cycle leaks into the new one.
        reset_attack_structure(w)
        reset_events()
        # TASK XIII: a fresh sequence also clears the BC-steering state
        # (cone holder, lane direction, gap pair, escape ramp, vector-flip
        # memory) so no stale steering latches leak into the new cycle.
        reset_bc_steering(w)
        w.defence_enabled = True
        w.disable_contact_frames = 0
        w.ctx = None

        # POST-TES SETTLE WINDOW (2026-09-07): a fresh kickoff drops any
        # stale settle latch / counter so the new cycle starts un-gated.
        w.post_tes_settle_frames = 0
        w.post_tes_settle_enabled = False

        # TASK G ANCHOR AUTHORITY (Layer 6): a kickoff reset is a fresh
        # play â€” the anchor lock from any previous set must be dropped so
        # the anchor recomputes from the NEW ruck/ball next frame.
        reset_anchor_authority(w)

        # â”€â”€ GEOMETRY FRAME-STAMP RESET (2026-08-12) â”€â”€
        # clear_frame_geometry(w) drops world.frame_geometry_ready /
        # frame_geometry_frame / frame_spacing_targets / frame_width_targets
        # so a dict published in the previous kickoff sequence can never
        # read as "fresh" against the new frame_count=0 cycle (the
        # invariant-audit staleness gate).  GEOMETRY FRAME-STAMP RESET FOR
        # INVARIANT AUDIT; NO BEHAVIOURAL EFFECT â€” do not remove as "dead":
        # without it the defender-state audit's frame_geometry_is_fresh()
        # can false-positive on the first frames of a new sequence.
        clear_frame_geometry(w)

        # TACKLE-STATE GATE (2026-08-23): a fresh sequence starts with no
        # holders -- clear every stale hold flag.
        reset_tackle_state(w)

        # Restore all derived subsystem enable flags (idempotent with defaults)
        w.update_tes_lock_flags()
        # -- STAGE 2: A KICKOFF DISCARDS THE CONTEST LEDGER --
        # A fresh kickoff re-creates every Player, so a TackleState naming the
        # old bodies is stale by construction.  Drop the whole ledger alongside
        # every other carried-over play state cleared above.
        from game.tes.tackle_state import reset_tackle_states
        reset_tackle_states(w)

    def on_kickoff(self):
        """
        Compatibility hook for kickoff_engine.
        Resets pursuit mode and defensive anchor for kickoff chase.
        """
        # Reset pursuit mode flag if present
        if hasattr(self.world, "in_pursuit_mode"):
            self.world.in_pursuit_mode = True

        # TASK VI (2026-08-16): the legacy `defensive_anchor_x` cached
        # anchor field was a dead kickoff-era write with no readers.  All
        # anchor state now lives in world.defensive_line_anchor, owned by
        # anchor_authority (ruck_x + 10).  Removed per directive #2.
        pass

        # Freeze managed by apply_phase_invariants() on next frame
        pass

    def on_ball_secured(self):
        """
        Compatibility hook for kickoff_engine.
        Called when the ball is caught after kickoff.
        Ensures open play systems are active.
        """
        self.world.first_tackle_not_made = False
        # Reset pursuit mode
        if hasattr(self.world, "in_pursuit_mode"):
            self.world.in_pursuit_mode = False

        # Freeze managed by apply_phase_invariants() on next frame
        pass

        # Ensure ball carrier is registered
        bc = self.world.ball_carrier
        if bc is not None:
            pass  # freeze managed by apply_phase_invariants()

    # -- Backward-compat property: run.py and debug scripts read open_play.tes_phase.value --
    @property
    def tes_phase(self):
        """Return a compat object with .value matching the current internal phase string."""
        class _CompatPhase:
            def __init__(self, phase_str):
                self.value = phase_str
        return _CompatPhase(self._phase)

    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
    # Main entry -- called once per frame
    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

    def update(self, dt: float) -> None:
        """Main update -- dispatch to current phase handler.

        DEFENCE DISPATCH CONTRACT (ENGINE LAW): defence AI runs ONLY during
        OPEN_PLAY.  Attack pipeline runs only during OPEN_PLAY /
        POST_RUCK_OPEN_PLAY when a ball carrier exists.  In every other
        phase the phase handlers (TES / RELEASE / PTB) own movement.
        """
        self.world.frame_count += 1
        self.world.dt = dt
        self.world.defence_ran_this_frame = False

        # ── POST-TES DRIVE-END SETTLE WINDOW COUNTER (2026-09-06) ──
        # Decay the settle window one frame at a time.  Armed at
        # DOMINANCE→RELEASE (drive end); while > 0 the post-TES snap writers
        # (ruck-pin position snap, spacing/width clamps) are gated so the
        # defenders glide to the ruck-relative target instead of teleporting.
        # Reaches 0 -> writers resume normal behaviour (retreat/reform).
        if getattr(self.world, "post_tes_settle_frames", 0) > 0:
            self.world.post_tes_settle_frames -= 1

        # ── CATCH X-AUTHORITY LOCK CLEAR (2026-09-10) ──
        # The sole-X-authority lock spans the chase-to-catch transition.  It
        # is armed by catch_system._perform_catch() and cleared here once the
        # post-catch pursuit window burns down, so normal multi-writer
        # formation (spacing/width/MMDL) resumes after the transition.  The
        # lock is cleared BEFORE the phase handler so the very first frame
        # after the window expires runs the standard pipeline.
        from game.defence.defence_lvl_1.catch_x_authority import (
            catch_x_authority_locked as _cxa_locked,
            clear_catch_x_authority as _cxa_clear,
        )
        if (_cxa_locked(self.world)
                and getattr(self.world, "post_catch_chase_window_s", 0.0) <= 0.0):
            _cxa_clear(self.world)


        # ── STAGE 1 MARKER DECISION LOCK (2026-09-05; gated 2026-09-06) ──
        # ONLY FORM MARKERS AT END OF DRIVE: the decision lock is armed ONLY
        # on the canonical Drive→Release transition (phase bucket tes →
        # release).  Markers are NOT re-armed at every phase-bucket
        # transition — re-arming during TES (open_play → tes) or the other
        # intermediate buckets (release → ptb, ptb → post_ruck,
        # post_ruck → open_play) produced premature marker formation during
        # TES.  The lock is captured HERE (before the new phase handler
        # overwrites the target) so the marker re-anchors to its drive-end
        # lane at the boundary.  The lock clock advances every frame.
        from game.defence.defence_lvl_1.marker_decision_lock import (
            advance_marker_lock_clock,
            arm_marker_lock,
            phase_bucket,
        )
        _prev_bucket = getattr(self, "_last_marker_phase_bucket", None)
        _bucket = phase_bucket(self._phase)
        if _prev_bucket == "tes" and _bucket == "release":
            arm_marker_lock(self.world, self.world.defending_team)
        self._last_marker_phase_bucket = _bucket
        advance_marker_lock_clock(self.world, dt)

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â SDEA phase invariants Ã¢â‚¬â€ set frozen flags BEFORE handler Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        apply_phase_invariants(self.world, self._phase)

        # â”€â”€ UN-GATED DEFENSIVE GEOMETRY PREAMBLE (2026-08-12) â”€â”€
        # Depth-Role -> Spacing -> Width runs EVERY frame in EVERY phase,
        # BEFORE any target writer (invariant sheet 1.2/2.4/5.1/5.2).  The
        # pure dicts are published as world.frame_spacing_targets /
        # world.frame_width_targets so the P7 retreat loops, PTB role
        # movement, POST_RUCK handlers, and the OPEN_PLAY pipelines all
        # consume THIS frame's structural geometry.  Compute only â€” never
        # writes target_x/target_y (the write stays phase-owned: TES/PTB
        # handlers, then the OPEN_PLAY structured/unified pipelines, then
        # MMDL/Player.update movement integration).
        # DRIVE GEOMETRY LIVE (2026-09-10): the un-gated preamble is NO LONGER
        # blocked during the TES drive (TACKLE_CONTACT / TACKLE_DRIVE /
        # TACKLE_DOMINANCE).  No defender is frozen in those phases
        # (MOVEMENT_RULES grants DEF_LINE_READY / DEF_CHASE / DEF_RETREAT_10
        # movement authority), so the depth-role -> spacing -> width geometry
        # must be recomputed and republished EVERY drive frame against the
        # LIVE ball carrier — that is what keeps every defender on its
        # depth-role lane (Y) relative to the BC instead of being pinned.
        #
        # STAGE 11 (2026-09-09): engaged-only no-tackle / no-defence gate.
        # When no body is in contact and no defender is within the engagement
        # radius of the BC (the idle "zero-tackle" frames from kickoff
        # onwards), skip the un-gated depth-role / spacing / width preamble
        # entirely so the framerate recovers.  The frame dicts are NOT
        # republished this frame; every downstream frame_spacing_y reader
        # already falls back to the stamped / hold path (d.frame_spacing_y /
        # ruck_y) when the dict is stale or absent, so no recompute is
        # triggered and the authority contract is preserved.
        if not _ntnd_active(self.world, self.def_tuners, phase=self._phase):
            self.defence_manager.run_frame_preamble(self.world)

        # -- Post-catch insurance re-arm + PTB-era defender speed caps. --
        # 1. _stamp_post_catch_speed_caps(): one-shot belt-and-suspenders for
        #    the post-catch clamp on the first OPEN_PLAY frame after reception.
        #    No-op in normal operation (catch_system already armed the full
        #    3.0s window); consumes world.just_caught_this_frame so it can
        #    never fire on normal open-play frames.
        # 2. _stamp_defensive_speed_caps(): clamps PTB-era defenders to
        #    post_catch_cap_mps.  Runs AFTER apply_phase_invariants() but
        #    BEFORE the phase handler, so the P7 retreat moves inside the PTB
        #    handlers are already clamped.  OPEN_PLAY is intentionally NOT
        #    stamped here (would clobber the kickoff-catch clamp armed by
        #    catch_system).
        self._stamp_post_catch_speed_caps()
        self._stamp_defensive_speed_caps()

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â FrameContext Ã¢â‚¬â€ build read-only per-frame state projection Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        # Must run AFTER apply_phase_invariants() so frozen flags are authoritative.
        ctx = build_frame_context(self.world, self)
        self.world.ctx = ctx  # available for debug/audit access by subsystems

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â Defence + attack run only during open-play phases Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        # During TES phases (TACKLE_INIT/CONTACT/DRIVE/DOMINANCE), RELEASE,
        # PTB_PREP/EXEC/PTB, and POST_RUCK_OPEN_PLAY, the defence/attack
        # subsystems are suppressed Ã¢â‚¬â€ freeze is managed by apply_phase_invariants()
        # and movement is handled by the phase handlers directly.
        # DEFENCE DISPATCH CONTRACT (ENGINE LAW) -- defence AI runs ONLY
        # during OPEN_PLAY.  It is dormant in every TES phase (TACKLE_INIT /
        # CONTACT / DRIVE / DOMINANCE), RELEASE, and every PTB phase, as
        # well as POST_RUCK_OPEN_PLAY.  Movement outside OPEN_PLAY is owned
        # by the phase handlers (TES / RELEASE / PTB handler logic).
        # is_tackle_phase() is defense-in-depth so a TES sub-state can
        # never enter the defence pipeline even if the phase list drifts.
        if (self._phase == "OPEN_PLAY"
                and not is_tackle_phase(self.world)):
            # ── POST-CATCH PURSUIT WINDOW DECREMENT (2026-08-16) ──
            # Burn down the post-catch chase window each OPEN_PLAY frame so
            # defenders return to normal SLIDE/DRIFT line behaviour once the
            # 3.0s pursuit window after a kickoff catch expires.  Guarded by
            # getattr so audio/headless harnesses with a partial world never
            # crash on the missing field.
            if getattr(self.world, "post_catch_chase_window_s", 0.0) > 0.0:
                self.world.post_catch_chase_window_s = max(
                    0.0,
                    self.world.post_catch_chase_window_s - dt,
                )
            # STAGE 11 (2026-09-09): engaged-only no-tackle / no-defence gate.
            # When OPEN_PLAY and no body is in contact and no defender is
            # within the engagement radius of the BC (idle "zero-tackle"
            # frames), the full defensive dispatch is suppressed and a cheap
            # idle pursuit keeps the line converging on the carrier so a
            # tackle can still form the instant any defender closes into
            # range.  This is the framerate-recovery branch.
            if _ntnd_active(self.world, self.def_tuners, phase=self._phase):
                self._run_idle_open_play_pursuit(dt)
            else:
                self.defence_manager.update_open_play(self, dt)
            # Flag AFTER the call: _update_defence() may trigger _start_tackle()
            # and flip phase to TACKLE_INIT mid-call.  Only claim defence ran
            # this frame while still in OPEN_PLAY so the freeze audit does not
            # raise DEFENCE_GATE_VIOLATION on the OPEN_PLAY->TACKLE_INIT frame.
            if self._phase == "OPEN_PLAY":
                self.world.defence_ran_this_frame = True

                # TASK III — MOVEMENT CONTRACT STABILITY AUDIT (no-op unless
                # DEBUG_MOVEMENT_CONTRACT_AUDIT=True): the defensive pipeline
                # has stamped every defender now, so record the FRAME TRUTH
                # (tackle_role / movement_state / target_source / target_x /
                # target_y) before any later writer can overwrite it.
                mca_snapshot(self.world)

        if (self._phase in ("OPEN_PLAY", "POST_RUCK_OPEN_PLAY")
                and not is_tackle_phase(self.world)):
            self.attack_manager.update(self, dt)

        bc = self.world.ball_carrier
        if bc is not None:
            self.ball_carrier = bc
            self.frames_since_ptb += 1
            # STAGE 3F: accumulate real elapsed time alongside the frame
            # counter — the quiet-window / escape-window gates key off this
            # so they hold the same wall-clock length at any tick rate.
            self.seconds_since_ptb += dt
        else:
            self.ball_carrier = None

        # -- FREEZE CLOCK (2026-09-26) -----------------------------------
        # ONE decrement of every live ``freeze_timer`` for the frame,
        # applied BEFORE the phase handler so a value written earlier in a
        # frame is never partially consumed and a value written later in
        # this same frame is honoured in full on the next tick.  This is
        # the only decrement of the field in the engine: every writer (the
        # miss stun, _handle_broken_tackle, marker assignment) gains
        # automatic expiry and the ``struct_freeze_timer`` pin releases on
        # its own, exactly as ``marker_freeze_timer`` already does.  The
        # RELEASE clears are NOT removed -- they still fire as a
        # belt-and-braces reset.  No-op when the lever is disarmed.
        decay_freeze_timers(self.world, dt)

        # Dispatch
        phase_map = {
            "OPEN_PLAY":             self._update_open_play,
            "TACKLE_INIT":           self._update_tackle_init,
            "TACKLE_CONTACT":        self._update_tackle_contact,
            "TACKLE_DRIVE":          self._update_tackle_drive,
            "TACKLE_DOMINANCE":      self._update_tackle_dominance,
            "RELEASE":               self._update_release,
            "PTB_PREP":              self._update_ptb_prep,
            "PTB_EXEC":              self._update_ptb_exec,
            "PTB":                   self._update_ptb,
            "POST_RUCK_OPEN_PLAY":   self._update_post_ruck_open_play,
        }
        handler = phase_map.get(self._phase)
        if handler:
            handler(dt)
        else:
            # Safety fallback -- reset to open play
            self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "FALLBACK")

        # -- STAGE 3: THE ACTIVE CONTEST'S PER-CYCLE EVOLUTION -------------
        # TES Drive v1 owns the frame-by-frame evolution of the formed
        # contest.  ONE call per simulation cycle, AFTER the phase handler has
        # produced the frame's geometry, so the drive samples the same state
        # every producer in the frame has already written.
        #
        # The call RESOLVES the active contest through the Stage 2 module's own
        # reader.  With no contest it is a LOOKUP, not a branch: it creates
        # nothing, advances nothing and decides nothing — it only drops a drive
        # whose contest has already been closed by this engine's own lifecycle
        # boundary earlier in this same cycle.  Stage 3 never terminates a
        # contest and never returns the engine to open play.
        from game.tes.tes_drive_v1 import advance_tes_drive_v1
        advance_tes_drive_v1(self.world, dt)

        # -- DH PRE-POSITION (2026-09-16) ---------------------------------
        # Integrate the DH toward (bc.x - 1.0, bc.y) for the whole tackle so
        # its approach is complete BEFORE RELEASE begins, instead of being
        # walked entirely inside RELEASE at the fixed 4.0 m/s writer speed.
        # Runs AFTER the phase handler so it owns the DH's final target for
        # the frame, and is gated to the TES chamber: RELEASE and the PTB
        # phases keep their own DH writers (_update_release / PTBManager).
        # The movement contract already lists Role.DUMMY_HALF in
        # allowed_to_move for all four TES phases, so the body stays unfrozen
        # and the movement audit accepts it.
        if self._phase in (
            "TACKLE_INIT", "TACKLE_CONTACT",
            "TACKLE_DRIVE", "TACKLE_DOMINANCE",
        ):
            self._drive_dh_preposition(dt)

        # POST-DISPATCH TES-SHADOW STRIP (2026-08-16): the TES-drive shadow
        # writer runs inside _update_defence_during_tes_drive, which executes
        # BEFORE the drive handler in this dispatch -- a defender can be
        # shadowed, then join the tackle during _update_tackle_drive and end
        # the frame as a TES participant while still carrying the stale
        # tes_drive_shadow tag (verifier ASSERT-E).  Strip the tag from any
        # defender who is now in the LIVE TES set (primary + joiners).  Their
        # position/target were already re-owned by TES physics; only the
        # attribution tag is cleared.
        if self._phase in ("TACKLE_DRIVE", "TACKLE_DOMINANCE"):
            _final_tes = set()
            if self.primary is not None:
                _final_tes.add(self.primary.player_id)
            for _fj in self.joiners:
                _final_tes.add(_fj.player_id)
            if _final_tes:
                for _fd in self.world.defending_team:
                    if _fd.player_id in _final_tes and getattr(
                            _fd, "target_source", None) == "tes_drive_shadow":
                        _fd.target_source = None

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â RE-APPLY PHASE INVARIANTS after handler dispatch Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        # The handler may have changed self._phase (e.g. DOMINANCE->RELEASE).
        # apply_phase_invariants at the top of update() ran with the OLD phase,
        # leaving stale frozen flags. Re-run with the current phase so frozen
        # flags are correct for the role pipeline and SDM that follow.
        apply_phase_invariants(self.world, self._phase)

        # -- Post-catch insurance re-arm + defender speed caps: re-arm on
        #    phase-transition frames. --
        # 1. _stamp_post_catch_speed_caps() again (belt-and-suspenders):
        #    normally a no-op here because the pre-handler call already
        #    consumed world.just_caught_this_frame; re-run in case the flag
        #    was re-armed by the handler or defence triggered _start_tackle()
        #    mid-call (phase guard then returns early).
        # 2. _stamp_defensive_speed_caps(): the handler may have just changed
        #    phase (e.g. PTB -> POST_RUCK); _set_phase() zeroed
        #    defended_cap_remaining_s on entry, so re-apply the 5.0 m/s clamp
        #    BEFORE the PTB role-movement pipeline runs.
        self._stamp_post_catch_speed_caps()
        self._stamp_defensive_speed_caps()

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â Role-based movement pipeline (PTB phases only) Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        # Maps each defender's existing DefenderRole to a target position
        # and max_speed via apply_role_movement().  Then runs arrival_predictor()
        # on unfrozen defenders to compute velocity proportional to remaining
        # error distance â€” eliminating overshoot and oscillation.
        #
        # DEFENCE DISPATCH CONTRACT (ENGINE LAW): role movement IS defence AI.
        # It is FORBIDDEN during every TES phase (TACKLE_INIT / CONTACT /
        # DRIVE / DOMINANCE) and during RELEASE.  TES owns all movement until
        # RELEASE -> OPEN_PLAY.  During RELEASE/PTB the phase handlers set
        # marker/DH/BC targets directly.  This role pipeline runs ONLY in
        # PTB_PREP / PTB_EXEC / PTB so DEF_RETREAT_10 defenders retreat once
        # the ruck is being played â€” POST_RUCK_OPEN_PLAY uses the geometry
        # pipeline and is covered by _update_defence.
        # -- MOVEMENT REACTIVATION after thaw (OPEN_PLAY entry frames) --
        # When the phase handler flipped to OPEN_PLAY this frame
        # (POST_RUCK->OPEN_PLAY, BROKEN_TACKLE->OPEN_PLAY), the defence /
        # attack pipelines already ran under the OLD phase and did not
        # integrate movement for just-thawed players.  Force immediate
        # re-entry so nothing sits at vx=0 for 1-2 frames (PTB delay, DH
        # jitter, line jitter, BC step-forward lag, defender retreat lag).
        # No-op on every normal OPEN_PLAY frame (defence_ran_this_frame is
        # True there, set by _update_defence).
        if self._phase == "OPEN_PLAY" and not self.world.defence_ran_this_frame:
            reactivate_movement_after_thaw(self.world.players, dt)

        if self._phase in ("PTB_PREP", "PTB_EXEC", "PTB"):
            # PTB phase handlers are the sole authority for marker, retreat, DH,
            # and BC motion in this window.  The generic post-dispatch movement
            # loop below was re-integrating defenders against targets the PTB
            # phase already wrote, causing the same-frame target churn that
            # manifested as DH + defensive-line jitter and the PTB readiness
            # stall.  Hard-stop all generic role movement in PTB so the phase-
            # owned writers are the only integrators.
            for d in self.world.defending_team:
                # The fullback re-assert above is its PTB phase owner and
                # already integrated it. Preserve that velocity instead of
                # treating the backfield anchor as a generic line defender.
                if getattr(d, "fullback_role_active", False):
                    continue
                if getattr(d, "frozen", False):
                    d.vx = 0.0
                    d.vy = 0.0
                    continue
                role = getattr(d, "defender_role", None)
                # ── RETREAT-LINE VELOCITY PRESERVATION (2026-09-18) ──
                # The P7 retreat writer INTEGRATES the DEF_RETREAT_10 body
                # inside the phase handler (apply_forced_retreat, then
                # update(dt)) and the generic loop below is hard-stopped for
                # it, so nothing else can move it.  Zeroing its velocity here
                # destroyed the only state the accel-lerp ramp needs, so the
                # retreater re-accelerated from rest EVERY frame and its
                # effective closure collapsed to a single frame's accel
                # fraction of the commanded speed -- measured live: 0.272 m/s
                # against a commanded 7.055 m/s (3.9%) across 658 obligated
                # body-frames of the PTB era, while RELEASE (not covered by
                # this pin) closed at 94.4%.  Leave the velocity of the
                # retreat role intact; it is a legal mover for every PTB
                # phase in MOVEMENT_RULES, so the movement audit accepts it.
                if role == DefenderRole.DEF_RETREAT_10:
                    continue
                # DH and markers are phase-owned here.
                # Do not re-integrate them via the generic movement pipeline.
                if role in (
                    DefenderRole.DUMMY_HALF,
                    DefenderRole.MARKER_A,
                    DefenderRole.MARKER_B,
                ):
                    d.vx = 0.0
                    d.vy = 0.0
                    continue
                # Any remaining body must remain static until the next legal
                # OPEN_PLAY dispatch; keeping the PTB line settled is more
                # important than allowing a second hidden writer to re-animate it.
                d.vx = 0.0
                d.vy = 0.0

            # TASK III — check the role-movement writers against the frame
            # snapshot (catches stale role-movement overwrites of the
            # defensive pipeline's tackle_role / movement_state / targets).
            mca_check(self.world, phase=self._phase, writer_label="role_movement")


            # â”€â”€ PTB-WINDOW DEFENDER FACING PASS (2026-08-04) â”€â”€
            # Frozen line defenders (DEF_LINE_READY / DEF_CHASE / TACKLER_*)
            # never enter Player.update(), so update_facing_unified() â€” and
            # the PTB-window defender-aiming override inside it â€” never
            # executes for them.  Their facing freezes mid-backpedal for the
            # whole PTB window.  Re-aim them at the ball carrier manually
            # using the same unified facing logic (smoothed shortest-path
            # rotation toward the BC).  FACING-ONLY: never touches vx/vy, so
            # it cannot trip the SDEA movement audit.
            from player.movement import update_facing_unified
            for d in self.world.defending_team:
                role = getattr(d, "defender_role", None)
                if not d.frozen:
                    continue
                if not getattr(d, "is_defender", False):
                    continue
                if role not in (
                        DefenderRole.DEF_LINE_READY,
                        DefenderRole.DEF_CHASE,
                        DefenderRole.TACKLER_1,
                        DefenderRole.TACKLER_2,
                        DefenderRole.TACKLER_3):
                    continue
                update_facing_unified(d, self.world.dt)



        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â Structured Decision Module â€” deterministic retreat Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        # Runs AFTER the phase handler so the frame context reflects
        # post-handler state, but BEFORE freeze enforcement so targets
        # set by the SDM are not zeroed.
        # DRIVE SDM LIVE (2026-09-10): the structured-retreat / structured-
        # close SDM writer is NO LONGER blocked during the TES drive
        # (TACKLE_CONTACT / TACKLE_DRIVE / TACKLE_DOMINANCE).  No defender is
        # frozen there, so the retreat / close writers keep running for every
        # non-TES-participant body.
        self._apply_defence_sdm(dt)

        # TASK III — check the SDM retreat writer against the frame
        # snapshot (catches SDM overwriting CHASE / tackler_commit targets).
        mca_check(self.world, phase=self._phase, writer_label="sdm")


        # -- TES PHASE STASIS (ENGINE LAW) --
        # During any TES phase the ONLY movers allowed are TES participants
        # (BC, tacklers, joiners) and the DH mover set, all of which are
        # animated by the TES phase logic itself.
        #
        # TES DRIVE BEHAVIOUR (2026-08-07, corrected spec):
        # During TACKLE_CONTACT / TACKLE_DRIVE / TACKLE_DOMINANCE non-tackler
        # defenders run the defensive pipeline — depth-role assignment +
        # retreat + spacing — so they behave EXACTLY like before the tackle.
        # TES only affects the ball carrier and the tackle participants.
        # Only when TES ends (RELEASE entry) does retreat-10 apply, owned by
        # the RELEASE/PTB role-movement pipeline.
        #
        # TACKLE_INIT keeps the full stasis pin: the BC has not driven yet
        # and the attach window must stay clean.
        if self._phase in ("TACKLE_INIT", "TACKLE_CONTACT",
                           "TACKLE_DRIVE", "TACKLE_DOMINANCE"):
            if self._phase == "TACKLE_INIT":
                for d in self.world.defending_team:
                    d.vx = 0.0
                    d.vy = 0.0
                    d.target_x = d.x
                    d.target_y = d.y
            else:
                self.defence_manager.update_tes_drive(self, self.world.dt)

            # ── FULLBACK GEOMETRY AUTHORITY — TES FRAME-END RE-ASSERT ──
            # The fullback is excluded from update_tes_drive's pool, and the
            # TACKLE_INIT stasis pin above overwrites its target. Re-assert
            # and integrate it once at the end of TES; RELEASE/PTB handlers
            # already do their own phase-specific re-assert. Leaving this
            # block outside the TES guard caused a second integration after
            # their phase handler, with the general anchor writer replacing
            # the ruck-relative PTB target and repeatedly resetting recovery.
            for _d in self.world.defending_team:
                if getattr(_d, "fullback_role_active", False):
                    apply_fullback_geometry(self.world, _d)
                    if not _d.frozen:
                        _d.update(dt)

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â FREEZE-STATE COMPLIANCE ENFORCEMENT Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        # After the phase handler runs, guarantee that any player with
        # frozen=True has zero velocity and target pinned to current position.
        # This prevents "soft-freeze" violations where frozen players have
        # stale movement targets or non-zero velocity from TES-era state.
        for p in self.world.players:
            if p.frozen:
                p.vx = 0.0
                p.vy = 0.0
                # FREEZE-PIN LATCH + GATE (2026-09-02): do NOT follow the live
                # p.x/p.y every frame.  Re-pinning to the live position made
                # the freeze-pin a transmission belt: any upstream writer that
                # jitters the ACTUAL y (e.g. the ruck-adjacent slot-2 position
                # clamp, which runs AFTER this loop) was copied straight into
                # target_y, and the audit attributed every frozen-player jump
                # to "freeze_pin".  Now the target is LATCHED on the frame the
                # player BECOMES frozen and held there; "freeze_pin" is only
                # stamped on latch entry or when an upstream writer overwrote
                # the latched target (gated).  The latch is cleared on thaw.
                if not getattr(p, "_freeze_pin_active", False):
                    p._freeze_pin_x = p.x
                    p._freeze_pin_y = p.y
                    p._freeze_pin_active = True
                    p.target_x = p._freeze_pin_x
                    p.target_y = p._freeze_pin_y
                    stamp_target_write(self.world, p, "freeze_pin")
                elif (abs(getattr(p, "target_x", p._freeze_pin_x) - p._freeze_pin_x) > 1e-6
                      or abs(getattr(p, "target_y", p._freeze_pin_y) - p._freeze_pin_y) > 1e-6):
                    p.target_x = p._freeze_pin_x
                    p.target_y = p._freeze_pin_y
                    stamp_target_write(self.world, p, "freeze_pin")
            else:
                if getattr(p, "_freeze_pin_active", False):
                    p._freeze_pin_active = False
                    if hasattr(p, "_freeze_pin_x"):
                        delattr(p, "_freeze_pin_x")
                    if hasattr(p, "_freeze_pin_y"):
                        delattr(p, "_freeze_pin_y")
                    # THAW TAG CLEAR (2026-09-02): the freeze-pin's own
                    # _target_writer tag was left stale after thaw, so the
                    # audit attributed the post-thaw target change (written
                    # by an unfrozen writer, e.g. the retreat/spacing
                    # pipeline) to "freeze_pin".  Clear only a stale
                    # freeze_pin tag so the post-thaw jump is attributed to
                    # the real writer (or "unknown") instead.
                    if getattr(p, "_target_writer", None) == "freeze_pin":
                        p._target_writer = None

        # TASK III — check the freeze pin (legal exception: pins frozen
        # players' targets, reported but not counted as an overwrite).
        mca_check(self.world, phase=self._phase, writer_label="freeze_pin")


        # --- BC X TES LOCK (2026-08-05) ---
        # During TACKLE_DRIVE / TACKLE_DOMINANCE the ball carrier's x is
        # owned EXCLUSIVELY by TES physics (deterministic drive step +
        # variation in _update_tackle_drive, dominance push in
        # _update_tackle_dominance).  The freeze-enforcement loop above only
        # touches FROZEN players â€” the BC is unfrozen during TES, so it never
        # enters that loop.  This guard pins the BC's movement TARGETS to its
        # live TES-owned position (never writing bc.x) so a stale OPEN_PLAY-era
        # target can never drag the BC backwards or cancel boosted drive
        # metres on the next RELEASE / OPEN_PLAY frame.
        if self._phase in ("TACKLE_DRIVE", "TACKLE_DOMINANCE"):
            bc_lock = self.ball_carrier
            if bc_lock is not None:
                bc_lock.target_x = bc_lock.x
                bc_lock.target_y = bc_lock.y
                stamp_target_write(self.world, bc_lock, "tes_bc_lock")


        # RUCK-ADJACENT FINAL-AUTHORITY PIN (2026-08-22): last writer of the frame.
        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â POST-PTB SCOOT: advance marker freeze timer each frame Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        # Pins each side first defender to EXACTLY ruck_y minus/plus 2.0 m (no exceptions)
        if self.world.marker_freeze_timer < self.world.marker_lock_duration_s:
        # and prints the measured Y-distance every frame. See
            self.world.marker_freeze_timer += dt
        # game/defence/defence_lvl_1/ruck_adjacent_pin.py.
        # Sync frames_since_ptb to world so AttackController reads the real value
        self.world._ope_phase_label = self._phase
        self.world.frames_since_ptb = self.frames_since_ptb
        self.world.seconds_since_ptb = self.seconds_since_ptb
        # -- TACKLE-STATE GATE SWEEP (2026-08-23) -------------------------
        # Sole release authority: holders leave TACKLE-STATE ONLY by
        # exiting the tackle-radius (+hysteresis).  Runs AFTER the phase
        # handlers so positions are final; released defenders re-enter
        # spacing/depth roles on the NEXT frame's preamble.  Also peels
        # expired-freeze holders outward so the exit can happen.
        # SLOT-INVARIANT REPAIR AT RELEASE (2026-08-24): the preamble
        # earlier this frame allocated roles WITHOUT the held defenders
        # (they were tackle-state-excluded), so a release leaves the
        # allocation half-repaired for the ruck-adjacent pin and any
        # late reader in THIS frame.  Re-run the pipeline IMMEDIATELY
        # when a release fires: released holders re-enter with
        # depth_role=None, so the fresh-invalidated exemption lets them
        # reclaim interior slots, de-dup + one-A enforcement guarantee
        # exactly one A per side, and the frame spacing/width dicts are
        # republished from the SAME repaired roles (closing the stale
        # marker-pin entry that mispositions an ex-MARKER_B).  Runs
        # BEFORE enforce_ruck_adjacent_pin so the pin sees consistent
        # geometry.
        _released_this_frame = self.defence_manager.update_tackle_gate(self.world, dt)
        # STAGE 10 SEAL (2026-09-06): the SLOT-INVARIANT REPAIR re-run of the
        # un-gated pipeline is suppressed inside the sealed TES chamber so a
        # tackle-gate release can never re-publish spacing / width geometry
        # onto the frozen line.  The final apply_phase_invariants below
        # re-freezes any released DEF_LINE_READY body (the "can't be released
        # mid-TES" backstop); the republished dict is deferred until RELEASE /
        # PTB re-opens the line.
        if _released_this_frame and self._phase not in (
                "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE"):
            run_defensive_pipeline(
                self.world,
                self.world.defending_team,
                self.def_tuners,
                governor=self.def_behaviour.line_governor,
                # STAGE 4B (audit-only): label this second solve of the SAME
                # frame so the exit-boundary audit can tell the release
                # slot-invariant repair apart from the frame preamble.
                publish_source="repair",
            )
        # ── STAGE 5 POPULATION-DRIFT REPAIR (2026-09-13) ──
        # The gate above can ADD a holder as well as release one, and a
        # release inside the sealed TES chamber deliberately skips the
        # geometry re-run.  In both cases the authoritative line population
        # changed AFTER this frame's preamble published it, leaving the
        # published count / depth_side stamps / population-keyed spacing and
        # width dicts describing a defender set that no longer exists.  This
        # republishes the ONE authority for the population that actually
        # exists this frame (no geometry writes, so the sealed TES chamber is
        # untouched); it is a no-op when the signature already matches.
        repair_line_population_if_drifted(self.world, self.def_tuners)
        self.defence_manager.enforce_ruck_pin(self.world)

        # ── FINAL FREEZE AUTHORITY RUN (2026-09-01) ──
        # The tackle-state gate sweep + ruck pin run AFTER the RE-APPLY of
        # apply_phase_invariants above, so a released / re-roled defender can
        # end the frame non-compliant with the movement-authority <-> frozen
        # invariant (e.g. a DEF_LINE_READY defender left unfrozen in RELEASE
        # -- the MOVEMENT_AUTHORITY_VIOLATION lines from
        # enforce_movement_authority_invariant).  Run the SOLE freeze
        # authority once more as the LAST writer (gated to non-OPEN_PLAY so it
        # never un-freezes legitimately gate-held defenders during open play)
        # so the final frozen state is always contract-compliant ("no third
        # state") by construction.
        if self._phase != "OPEN_PLAY":
            apply_phase_invariants(self.world, self._phase)

        # ── FROZEN-VELOCITY PIN BEFORE AUDIT (2026-09-07) ──
        # Belt-and-suspenders: the FINAL FREEZE AUTHORITY RUN above is gated
        # to non-OPEN_PLAY, so in OPEN_PLAY a frozen body (e.g. the POST-PTB
        # marker-lock window) could carry velocity into audit_movement — the
        # same-frame velocity leak.  Zero every frozen body immediately before
        # the audit so a frozen player can never report MUST_BE_FROZEN_MOVING /
        # ILLEGAL_MOVE.  Idempotent with the apply_phase_invariants guarantee.
        zero_frozen_velocity(self.world)

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â SDEA movement audit Ã¢â‚¬â€ log violations AFTER handler Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        violations = audit_movement(self.world, self._phase)
        for v in violations:
            print(f"[SDEA_MOVEMENT_VIOLATION] {v}")

        # MOVEMENT-AUTHORITY / FREEZE INVARIANT (2026-08-31) - "NO THIRD STATE"
        # Any defender whose movement authority is suppressed must be frozen;
        # any defender who is unfrozen must have movement authority.  The
        # consolidation pass inside apply_phase_invariants already guarantees
        # this by construction; this audit surfaces any subsystem that
        # unfroze a suppressed defender AFTER the sole freeze authority ran.
        ma_violations = enforce_movement_authority_invariant(self.world, self._phase)
        for v in ma_violations:
            print(f"[MOVEMENT_AUTHORITY_VIOLATION] {v}")

        # TASK III — periodic corruption report (every 60 frames).
        if self.world.frame_count % 60 == 0:
            mca_report(self.world)


        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â Freeze audit (debug diagnostics, every 10 frames) Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        self.freeze_audit.run(self.world, self.world.frame_count, self._phase, self._phase_timer)
        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â Defence reactivity audit (validates line-break response) Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        audit_defence_reactivity(self.world)
        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â Defender-state audit (depth roles / target_y / ruck-side) Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        # Frame-accurate depth-role correctness, spacing-Y drift, and
        # ruck-side-lock report (self-limiting).  Runs for every OPE frame;
        # the module's phase gate is world.phase == OPEN_PLAY here.
        run_defender_state_audit(self.world)

        # ── TARGET-WRITE AUDIT CAPTURE (diagnostic, env-gated) ──
        # Snapshot EVERY player's FINAL applied target + writer tag after ALL
        # writers (including the freeze-pin).  Reads player.target_source
        # (the last stamp_target_write that ran) so the consecutive-frame
        # diff attributes each target_y jump to the exact subsystem.  NO-OP
        # unless the audit is armed (world._target_write_log / RLM_JITTER_AUDIT=1).
        # ── STAGE 5 FRAME-END POPULATION FINALISATION (2026-09-13) ──
        # The LAST population write of the frame: republish the ONE authority
        # if membership drifted, rebuild the frame geometry when a population
        # member has no spacing entry, then trim the population-keyed observer
        # dicts to the population.  After this, only audits run, so the frame
        # boundary is guaranteed to hold
        #     published count == line population == spacing population
        # (see population_authority and finalise_line_population above).
        finalise_line_population(
            self.world, self.def_tuners, self._phase,
            governor=self.def_behaviour.line_governor,
        )
        record_final_targets(self.world, self.world.frame_count, self.world.players)

    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
    # Phase helpers
    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

    def _stamp_defensive_speed_caps(self) -> None:
        """Clamp defenders to post_catch_cap_mps (5.0 m/s) during PTB-era phases.

        The post-catch defensive line clamp (Player._defended_cap_ceiling)
        only caps speed while `defended_cap_remaining_s > 0`.  Stamping only
        `defended_speed_cap_mps` would therefore have no effect â€” and
        `_set_phase()` zeroes `defended_cap_remaining_s` whenever the engine
        leaves OPEN_PLAY.  This stamp re-arms BOTH fields every frame in
        PTB_PREP / PTB_EXEC / PTB / POST_RUCK_OPEN_PLAY, so defenders stay
        clamped to 5.0 m/s until the first true OPEN_PLAY frame.

        Called after BOTH apply_phase_invariants() call sites in update():
          - the pre-handler call protects the P7 retreat movements inside the
            PTB phase handlers, and
          - the post-handler call re-arms the window on phase-transition
            frames, after _set_phase() has disarmed it.

        The window never decrements outside OPEN_PLAY (step_defence_reaction()
        only runs inside _update_defence), so the re-armed 3.0s ceiling cannot
        expire mid-phase.  OPEN_PLAY itself is intentionally NOT stamped: the
        unified max_defensive_speed_mps ceiling already governs open-play
        chase, and re-stamping defended_speed_cap_mps there would clobber the
        kickoff-catch clamp armed by catch_system.
        """
        if self._phase not in ("PTB_PREP", "PTB_EXEC", "PTB", "POST_RUCK_OPEN_PLAY"):
            return
        cap = self.def_tuners.post_catch_cap_mps
        for d in self.world.defending_team:
            d.defended_speed_cap_mps = cap
            # TOP-UP-ONLY RE-ARM (2026-08-15, measured): the post-catch
            # clamp is a wall-clock window armed at reception (3.0s).  The
            # previous unconditional re-arm refreshed the window to 3.0s on
            # EVERY PTB-era frame, so it never expired (probe: armed from
            # catch frame 162 through 2081 — the entire run).  With the live
            # cap (8.0 ≈ ceiling 8.3) this was invisible, but if the cap were
            # ever restored to its stale 5.0 value the line would be
            # throttled for the whole match.  Now the window can only be
            # topped up while still active: once it burns down after the
            # catch, PTB-era defenders run under the unified ceiling.
            if getattr(d, "defended_cap_remaining_s", 0.0) <= 0.0:
                continue
            d.defended_cap_remaining_s = PTB_SPEED_CAP_WINDOW_S

    def _stamp_post_catch_speed_caps(self) -> None:
        """One-shot insurance re-arm of the post-catch defensive line clamp.

        catch_system._perform_catch() arms the clamp (cap =
        post_catch_cap_mps 5.0, window = DEFENDED_CAP_HARD_LIMIT_S 3.0s) on
        every defender at the moment of reception, and that arming survives
        into the first OPEN_PLAY frame (verified headless: window is still
        3.0s at OPEN_PLAY entry and defenders are capped at exactly 5.0 m/s).
        This stamp is therefore a no-op in normal operation.

        It exists as belt-and-suspenders: if a future change ever re-orders
        the catch path (e.g. OPE.start() runs between arming and the first
        OPEN_PLAY frame, or the catch_system arming block moves), the clamp
        window is re-stamped here on the very first post-catch OPEN_PLAY
        frame so defenders are ALWAYS capped at post_catch_cap_mps on
        reception exit.

        SAFETY (why this can never permanently clamp normal open play):
          1. The gate is world.just_caught_this_frame, set True by
             catch_system on the catch frame.  Nothing else reads it during
             play (verified by repo audit) and kickoff_engine.reset_for_kickoff()
             zeroes it on restart.  This method CONSUMES the flag immediately
             after the first stamp, so it fires at most once per catch.
          2. Defenders whose clamp window already hit 0 (defended_cap_remaining_s
             <= 0) are skipped.  A non-kickoff mid-play catch sets the flag
             but never arms the clamp, so this stamp is a no-op there â€” it
             can only top up an already-armed window, never create a fresh
             clamp after a release.

        Called after BOTH apply_phase_invariants() call sites in update(),
        right before _stamp_defensive_speed_caps() (mirrors the task's
        requested placement).
        """
        if self._phase != "OPEN_PLAY":
            return
        if not getattr(self.world, "just_caught_this_frame", False):
            return
        # Consume the flag FIRST so the stamp is strictly one-shot.
        self.world.just_caught_this_frame = False

        cap = self.def_tuners.post_catch_cap_mps
        window = PTB_SPEED_CAP_WINDOW_S
        for d in self.world.defending_team:
            if getattr(d, "defended_cap_remaining_s", 0.0) <= 0.0:
                continue
            d.defended_speed_cap_mps = cap
            d.defended_cap_remaining_s = window

    def _set_phase(self, phase: str, sub_state: GameSubState, reason: str) -> None:
        """Transition internal phase and set world sub_state."""
        self._phase = phase
        self._phase_timer = 0.0
        # Reset PTB gate safety timeout on any phase entry
        # (set high; decremented each frame in PTB phase handlers)
        self._ptb_gate_override_frames = 999999
        self.world.set_sub_state(sub_state, reason)

        # ── FULL DEFENSIVE-STATE RESET (2026-09-07) ──
        # Drive-path state (depth-lock, spacing-frame, retreat-support, role-
        # depth targets) leaks from one tackle into the next; the first tackle
        # starts clean but the second inherits stale state, producing the out-
        # of-band motion / N/A-pivot persistence / mid-line instability.  A
        # full defensive-state reset at RELEASE / PTB / TACKLE_INIT clears it
        # so all defenders rejoin the line fresh before the next tackle.  The
        # persistent depth-role bands are preserved (the RELEASE/PTB recovery
        # seed + POST_RUCK re-lane own band assignment).
        if phase in ("RELEASE", "TACKLE_INIT") or phase.startswith("PTB"):
            reset_defensive_drive_state(self.world)

        # ── POST-TES SETTLE WINDOW CLEAR ON OPEN-PLAY ENTRY (2026-09-07) ──
        # The settle window spans DOMINANCE→RELEASE through POST_RUCK_OPEN_PLAY
        # and must disengage on the first TRUE OPEN_PLAY frame so the spacing /
        # width / ruck-pin authorities resume normally.  Clear the latch and the
        # counter on ANY OPEN_PLAY entry (POST_RUCK→OPEN_PLAY, BROKEN_TACKLE,
        # ABORT, FALLBACK) so no stale window leaks into the next cycle.
        if phase == "OPEN_PLAY":
            self.world.post_tes_settle_enabled = False
            self.world.post_tes_settle_frames = 0
            # -- STAGE 2: A CONTEST CANNOT SURVIVE BACK INTO OPEN PLAY --
            # This is the engine's OWN lifecycle boundary, so this is where the
            # latched TackleState is closed and dropped.  The module records the
            # reason the CALLER supplies; the state takes no termination
            # decision of its own and nothing reads it back.
            from game.tes.tackle_state import clear_tackle_state
            clear_tackle_state(self.world)

        # -- UNFREEZE BC + RESET TARGETS ON OPEN-PLAY ENTRY --
        # When transitioning into OPEN_PLAY or POST_RUCK_OPEN_PLAY, ensure BC
        # is unfrozen and has valid targets so AttackController steering works.
        if phase in ("OPEN_PLAY", "POST_RUCK_OPEN_PLAY"):
            # -- GOVERNOR ANCHOR RESET ON NEW TES (2026-08-04) --
            # The defensive line governor's smoothed state (line_depth,
            # line_width, compression, step_up) is anchored to the PREVIOUS
            # ruck.  If it is not cleared on the PTB -> new open-play
            # transition, the line re-forms on the OLD anchor and stays
            # "stuck" until BC movement re-triggers a spacing recalculation.
            # reset() re-seeds all smoothed state so the next
            # compute_line_state() call builds the line from the NEW ruck
            # anchor.  Also clears _prev_line_width so the width delta
            # clamp cannot compare against the old set's width.
            self.def_behaviour.line_governor.reset()

            # -- PTB-ERA ARRIVAL-MODE CLEAR (movement reactivation) --
            # The PTB role pipeline sets arrival_mode=True on every defender
            # and nothing ever resets it, so after the first PTB the arrival
            # predictor (snap-to-target at 0.1 m, ignores move_speed, zeroes
            # vx inside stop radius) runs for the whole match.  Clear it on
            # every open-play entry so OPEN_PLAY always uses the lerp
            # accelerator.  (This also covers POST_RUCK_OPEN_PLAY entries so
            # the flag is dead before the eventual POST_RUCK -> OPEN_PLAY
            # transition, which is the frame reactivate_movement_after_thaw
            # integrates on.)
            for _p in getattr(self.world, "players", []):
                if getattr(_p, "arrival_mode", False) or getattr(_p, "reached_target", False):
                    _p.arrival_mode = False
                    _p.reached_target = False

            bc = self.world.ball_carrier
            if bc is not None:
                bc.frozen = False
                bc.freeze_timer = 0.0
                bc.target_x = bc.x
                bc.target_y = bc.y
                bc.move_speed = bc.max_speed_mps
                if DEBUG_OPE_TRACE:
                    print(f"[PHASE_ENTRY] BC unfrozen for {phase} id={bc.player_id}")
            # -- RELEASE THE ENGINE'S OWN CARRIER TOO --
            # tes_start_tackle() freezes the body the ENGINE holds
            # (self.ball_carrier).  The PTB DH swap re-points that attribute
            # (self.ball_carrier = self.dh) while world.ball_carrier keeps
            # naming whoever the attack/kickoff code last set, so unfreezing
            # only the world's carrier left the engine's carrier frozen for the
            # rest of the match: the permanent missed-tackle freeze.  Both
            # handles name an attacker by construction, so on an open-play
            # entry neither may stay frozen.
            engine_bc = self.ball_carrier
            if engine_bc is not None and engine_bc is not bc:
                engine_bc.frozen = False
                engine_bc.freeze_timer = 0.0
                if DEBUG_OPE_TRACE:
                    print(f"[PHASE_ENTRY] engine carrier unfrozen for {phase} "
                          f"id={engine_bc.player_id}")

        # -- POST-CATCH DEFENSIVE LINE CLAMP: DISARM ON OPEN-PLAY EXIT --
        # The clamp is armed at kickoff catch for the transition window only.
        # Any time the engine leaves OPEN_PLAY (tackle, release, PTB, etc.)
        # the clamp is disarmed so no stale room chases into the next set.
        if phase != "OPEN_PLAY":
            for d in self.world.defending_team:
                if getattr(d, "defended_cap_remaining_s", 0.0) > 0.0:
                    d.defended_cap_remaining_s = 0.0

        # -- POST-RUCK FRESH TARGET KICK --
        # When transitioning from POST_RUCK_OPEN_PLAY to OPEN_PLAY, defenders
        # may be parked on their last retreat line (e.g. ruck_x + 10) with
        # target_x == current_x, causing the defence pipeline to see them as
        # "already compliant" and never issue new movement orders.
        #
        # Fix: force every non-marker, non-DH defender to target the anchor
        # line on the first OPEN_PLAY frame.  On subsequent frames the normal
        # depth-classification/targeting pipeline will re-fine positions.
        #
        # FULLBACK EXCLUSION (2026-08-13): the dynamic fullback actor is
        # geometry-independent (last-writer authority is apply_fullback_geometry,
        # which runs later in _update_defence).  If any path ever re-stamps
        # fullback_role_active while the prior set's line is still parked, this
        # kick would clobber the actor's deep backfield anchor on the
        # POST_RUCK -> OPEN_PLAY transition frame â€” the one target writer that
        # runs outside both def_behaviour.apply() and the FB geometry authority.
        # Skip the actor here so the anchor can never be line-pinned on entry.
        if phase == "OPEN_PLAY" and "POST_RUCK" in reason:
            # TASK VI: POST_RUCK re-arm consumes the ANCHOR AUTHORITY
            # (locked ruck_x + 10) — never an inline ruck_x +
            # def_line_depth_m derivation.
            anchor_x = _anchor_m(self.world)
            for d in self.world.defending_team:
                if d.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B, DefenderRole.DUMMY_HALF):
                    continue
                if getattr(d, "fullback_role_active", False):
                    continue
                # Set target to anchor so next defence frame has a non-zero delta
                d.target_x = anchor_x
                # SPACING Y (Task F, 2026-08-10): POST_RUCK re-arm keeps the
                # split-design spacing Y, never the parked current Y.  Consumes
                # the un-gated preamble's fresh frame dict when available.
                d.target_y = _frame_spacing_y(self.world, d, self.def_tuners)
                d.move_speed = d.max_speed_mps
                d.freeze_timer = 0.0
                # MOVEMENT AUTHORITY RECLAIM (2026-09-01): the un-gated
                # preamble may have snapped fresh spacing targets against the
                # new ruck while this defender was still pinned by the TES-era
                # freeze.  Reclaim movement authority explicitly here (not via
                # the later post-dispatch apply_phase_invariants) so the
                # snapped spacing targets can actually be followed on the
                # first OPEN_PLAY frame.
                d.frozen = False
                # MOVEMENT-STATE RE-OWN (2026-09-01): the TES→RELEASE→PTB
                # window stamped RETREAT onto every defender (see
                # _clear_tes_artefacts).  At POST_RUCK→OPEN_PLAY the first
                # true OPEN_PLAY frame's MMDL re-assigns movement_state per
                # frame; clear it here so no stale RETREAT/HOLD from the
                # transition window fights the MMDL on the entry frame.
                d.movement_state = None
                # Clear any timing-stagger flags that may suppress movement
                if hasattr(d, "timing_locked"):
                    d.timing_locked = False
                if hasattr(d, "reaction_timer"):
                    d.reaction_timer = 0.0
                if hasattr(d, "retreat_delay"):
                    d.retreat_delay = 0.0
    # -- Public entry point (called from catch_system after kickoff catch) --

    def start(self, player: Player) -> None:
        """Register ball carrier after kickoff catch.  No phase change --
        the engine is already in OPEN_PLAY since boot."""
        self.ball_carrier = player
        self.world.game_state = GameWorldState.OPEN_PLAY
        self.world.set_sub_state(GameSubState.OPEN_PLAY, "OpenPlayEngine.start()")
        if DEBUG_OPE_TRACE:
            print(f"[OPEN_PLAY] start() registered Player #{player.player_id}")

    # -- OPEN_PLAY ------------------------------------------------------

    def _run_idle_open_play_pursuit(self, dt: float) -> None:
        """STAGE 11 (2026-09-09): engaged-only idle pursuit.

        When ``_ntnd_active()`` is True (OPEN_PLAY, no body in contact, no
        defender within ``no_tackle_engagement_radius_m`` of the ball
        carrier) the full defensive pipeline is suppressed so the framerate
        recovers.  This cheap pass keeps every unfrozen line defender
        converging on the carrier so a tackle can still form the instant any
        defender closes into range, WITHOUT the expensive spacing / width /
        depth-lock / investigation / TES-pretackle machinery.

        Contract: one cheap target assignment per defender + Player.update()
        integration.  Never writes depth_role / depth_class / tackle_role /
        movement_state, never touches the frame dicts.  Fullback geometry is
        re-asserted so the deep backfield anchor is never dropped.
        """
        bc = self.world.ball_carrier
        if bc is None:
            return
        _ceiling = self.def_tuners.max_defensive_speed_mps
        for d in self.world.defending_team:
            if getattr(d, "fullback_role_active", False):
                continue
            if d.frozen or d is bc:
                continue
            # Cheap pursuit: drive depth toward the carrier, hold lateral.
            d.target_x = float(getattr(bc, "x", d.x)) - 1.0
            d.target_y = float(getattr(d, "y", d.y))
            d.move_speed = min(
                float(getattr(d, "max_speed_mps", _ceiling)), _ceiling,
            )
            d.max_defensive_speed_mps = _ceiling
            if getattr(d, "_mmdl_moved", False):
                d._mmdl_moved = False
            d.update(dt)
        # Fullback geometry re-assert (deep backfield anchor never drops).
        for d in self.world.defending_team:
            if getattr(d, "fullback_role_active", False):
                apply_fullback_geometry(self.world, d)
                if not d.frozen:
                    d.update(dt)

    def _update_open_play(self, dt: float) -> None:
        """Normal open play: BC runs, defence tracks, check for tackle initiation."""
        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â OPEN_PLAY role controller Ã¢â‚¬â€ MUST RUN BEFORE ANY FREEZE OR MOVEMENT Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        # Role fixing via apply_phase_invariants()

        # Advance the open-play phase clock.  OPEN_PLAY was the only phase
        # handler that never incremented _phase_timer, so the timer stayed
        # at 0.000 for the entire phase (audit: timer=0.000 every OPEN_PLAY
        # frame 55-129).  Any time-gated defence logic (pursuit entry,
        # reaction windows) that reads the open-play clock could therefore
        # never activate.  Every other handler increments at the top of the
        # frame (e.g. TACKLE_INIT self._phase_timer += dt), so OPEN_PLAY now
        # matches the convention.
        self._phase_timer += dt

        bc = self.ball_carrier
        if bc is None:
            return
        bc.attack_role = "ball_carrier"

        # Cooldown
        if self._cooldown_timer > 0.0:
            self._cooldown_timer -= dt

        # STAGE 3.5: keep the post-bump-off carrier burst alive.  This runs
        # AFTER the attack pipeline has had its say (it writes
        # bc.move_speed = max_speed_mps * 0.9 every frame), so the burst is
        # re-stamped for each frame of its declared window instead of being
        # overwritten the very next frame.
        self._tick_bump_carrier_burst(dt)

        # Ensure BC has tuners
        if bc.tuners is None:
            bc.tuners = AttackTuners()

        # BC steering is handled by _update_attack() which runs before dispatch
        # in update().  No further attack pipeline needed here.

        # [STEP-02A-01] Legacy tackle trigger disabled.
        # Lvl-1 chase is now the sole authority for initiating tackles.

        # -- DH OPEN_PLAY SUPPORT (final write of the OPEN_PLAY frame) -----
        # While no TES / RELEASE / PTB authority is active, keep the DH
        # trailing the live ball carrier so a stale tackle-era
        # "dh_preposition" target cannot survive a BROKEN_TACKLE exit.
        self._drive_dh_open_play_support(dt)

    def _update_bc_run(self, bc: Player, dt: float) -> None:
        """Ball carrier forward movement -- simple version."""
        bc.vx = 5.0
        bc.x += bc.vx * dt

    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
    # ATTACK UPDATE Ã¢â‚¬â€ gives all attackers movement targets every frame
    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

    def _update_attack(self, dt: float) -> None:
        """DELEGATE (2026-08-24): implementation owned by AttackManager."""
        self.attack_manager.update(self, dt)

    def _disabled_update_attack_impl(self, dt: float) -> None:
        """Give every attacker movement targets each OPEN_PLAY/POST_RUCK_OPEN_PLAY frame.

        Overrides any stale TES pinning.  BC is handled by attack_controller;
        support runners get full X+Y vector movement toward the BC so they
        converge with defenders' tackle corridors.
        """
        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â HARD GATE: attack runs ONLY during OPEN_PLAY / POST_RUCK_OPEN_PLAY Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        # This catches all call paths including hidden/implicit call sites
        # that bypass the gate in update().
        if self._phase not in ("OPEN_PLAY", "POST_RUCK_OPEN_PLAY"):
            return
        # DEFENCE DISPATCH CONTRACT -- no attack during TES tackle.
        if is_tackle_phase(self.world):
            return

        bc = self.world.ball_carrier
        if bc is None:
            # No BC registered yet (kickoff/early frames): pin movement
            # targets to current positions so no attacker is flagged as
            # STUCK_NOT_FROZEN with stale/None/invalid targets.
            for p in self.world.attacking_team:
                tx = getattr(p, "target_x", None)
                ty = getattr(p, "target_y", None)
                invalid = (
                    tx is None
                    or ty is None
                    or not math.isfinite(float(tx))
                    or not math.isfinite(float(ty))
                )
                if invalid:
                    p.target_x = p.x
                    p.target_y = p.y
                    p.vx = 0.0
                    p.vy = 0.0
            return

        # Ã¢â€â‚¬Ã¢â€â‚¬ BC steering via AttackController Ã¢â€â‚¬Ã¢â€â‚¬
        self.attack_controller.apply_vector(bc, self.world, dt)

        # Ã¢â€â‚¬Ã¢â€â‚¬ CRITICAL: BC physics step resolves target_x/target_y into movement Ã¢â€â‚¬Ã¢â€â‚¬
        bc.update(dt)

        # Ã¢â€â‚¬Ã¢â€â‚¬ Support runners Ã¢â€â‚¬Ã¢â€â‚¬
        for p in self.world.attacking_team:
            if p is bc:
                continue
            # Support runners: move toward BC on both axes
            dx = bc.x - p.x
            dy = bc.y - p.y
            dist = math.hypot(dx, dy)
            if dist > 0.01:
                speed = 4.0
                p.vx = (dx / dist) * speed
                p.vy = (dy / dist) * speed
                p.target_x = p.x + (dx / dist) * 5.0
                p.target_y = p.y + (dy / dist) * 5.0
            else:
                p.vx = 0.0
                p.vy = 0.0
                p.target_x = p.x
                p.target_y = p.y
            p.frozen = False
            p.freeze_timer = 0.0
            # Integrate movement through the existing physics step so the
            # attacker's position advances toward its target.  Without this,
            # support runners were assigned targets + velocity but never moved.
            p.update(dt)
            if DEBUG_OPE_TRACE:
                print(f"ATTACK UPDATE id={p.player_id} x={p.x:.2f} y={p.y:.2f} "
                  f"vx={p.vx:.2f} vy={p.vy:.2f}")

    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
    # STEPÃ¢â‚¬â€˜03AÃ¢â‚¬â€˜08 Ã¢â‚¬â€ Defensive line anchor
    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
    # LINE-BREAK HANDLING Ã¢â‚¬â€ when BC passes the defensive line
    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

    def _handle_line_break(self, bc: Player, dt: float) -> bool:
        """NEUTRALIZED (TASK VII, 2026-08-20).

        This legacy line-break geometry writer has ZERO callers anywhere
        in the engine (verified repo-wide).  Line-break behaviour is
        owned today by the MMDL / structured-close / line-break collapse
        in DefensiveBehaviourUnified and the _is_carrier_past_anchor()
        gate — all of which write through the spacing / width / anchor
        authorities.  The dormant body could never execute; it is now
        hard-dead so a future re-wiring can never resurrect a
        non-authority writer that set target_x / target_y / move_speed /
        depth_class directly on every defender.

        Always returns False so any (hypothetical) caller falls through
        to normal defence.
        """
        return False
        # ── (dead body below — unreachable legacy writer) ──
        # Compute the defensive line anchor
        # TASK VI: consume the ANCHOR AUTHORITY (ruck_x + 10) — never a
        # bare field read or an inline ruck_x + def_line_depth_m fallback.
        defensive_anchor = _anchor_m(self.world)

        LINE_BREAK_THRESHOLD_M = 3.0  # BC must be 3m past anchor to trigger

        bc_ahead = bc.x - defensive_anchor

        if bc_ahead > LINE_BREAK_THRESHOLD_M:
            if not self._line_break_active:
                if DEBUG_OPE_TRACE:
                    print(f"[LINE_BREAK] BC #{bc.player_id} x={bc.x:.1f} past anchor {defensive_anchor:.1f} by {bc_ahead:.1f}m")
                self._line_break_active = True
        else:
            # Deactivate if BC drops back behind anchor
            if self._line_break_active and bc_ahead < -1.0:
                self._line_break_active = False
                if DEBUG_OPE_TRACE:
                    print(f"[LINE_BREAK] BC returned behind anchor Ã¢â‚¬â€ deactivating")

        if not self._line_break_active:
            return False

        # Ã¢â€â‚¬Ã¢â€â‚¬ Line-break reaction: scramble/chase Ã¢â€â‚¬Ã¢â€â‚¬
        defenders = self.world.defending_team
        chase_speed = bc.max_speed_mps * 0.9 if hasattr(bc, "max_speed_mps") else 6.0
        # ROLE-ANCHORED SCRAMBLE WIDTH (Task B, 2026-08-09): the scramble
        # spread uses the SAME role-slot width anchors as the geometry
        # pipeline â€” NOT get_defence_target lane Y.  Depth roles are
        # assigned once (idempotent), then every defender's Y target is
        # the canonical role anchor from defensive_width.py, so the
        # scramble preserves the line's authoritative width even in chaos.
        ordered = list(defenders)
        self.defence_manager.assign_bands(ordered, self.world)
        _tuners = getattr(self, "def_tuners", None)
        if _tuners is None:
            from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
            _tuners = DefenceTuners()
        for d in ordered:
            if d.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B, DefenderRole.DUMMY_HALF):
                continue
            # ── RETREAT-OBLIGATION VETO (2026-09-17) ──
            # The scramble path is the ENGINE-side authority writer: it
            # promotes every line body to depth_class PRIMARY at chase speed
            # with a BC-relative target ("Reclassify as ACTIVE for chase
            # behaviour").  A body that still owes its 10 m retreat is left
            # out of it, so the scramble can never hand back the authority
            # the manager's post-writer revocation just stripped.
            if is_retreat_obligated(d, self.world):
                continue
            # Target: behind the BC; X = scramble depth, Y = split-design
            # spacing target (Task F) so the line keeps its governed,
            # per-side spacing even in chaos.
            d.target_x = bc.x - 2.0  # scramble depth behind BC
            # FRAME AUTHORITY: consume the frame-published count-split
            # targets via _frame_spacing_y (module import) — never the
            # structural spacing_y helper, which re-derives from the
            # live width and bypasses the fresh frame dict.
            d.target_y = _frame_spacing_y(self.world, d, _tuners)
            d.move_speed = chase_speed
            d.frozen = False
            d.freeze_timer = 0.0
            # Reclassify as ACTIVE for chase behaviour
            d.depth_class = "PRIMARY"
            d.timing_locked = False
            d.reaction_timer = 0.0
            d.retreat_delay = 0.0

        # Apply movement
        for d in defenders:
            d.update(dt)

        return True

    def _update_defence(self, dt):
        """DELEGATE (2026-08-24): implementation owned by DefenceManager."""
        self.defence_manager.update_open_play(self, dt)

    def _disabled_update_defence_impl(self, dt):
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # HARD GATE: defence AI runs ONLY during OPEN_PLAY (ENGINE LAW).
        # Dormant during all TES phases, RELEASE, PTB, and POST_RUCK.
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        if self._phase != "OPEN_PLAY":
            return
        # DEFENCE DISPATCH CONTRACT -- no defence during TES tackle.
        if is_tackle_phase(self.world):
            return

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # FIX: assign default roles to any defender missing one
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        for p in self.world.defending_team:
            if p.defender_role is None:
                p.defender_role = DefenderRole.DEF_LINE_READY

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â GLOBAL DEFENSIVE TOP-SPEED CEILING Ã¢â€¢Â STAMP Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        # Stamp the single authoritative defensive top speed
        # (DefenceTuners.max_defensive_speed_mps) onto every defender every
        # frame BEFORE any movement writer runs.  Player.update() enforces
        # this value as a hard ceiling, so no subsystem (kickoff chase,
        # pursuit, collapse, retreat, SDM, structured OPEN_PLAY, governor,
        # clamp) can move a defender faster than the unified maximum.  Runs
        # even when ball_carrier is None (kickoff in-air frames) so kickoff
        # chase shares the same ceiling.
        _def_ceiling = self.def_tuners.max_defensive_speed_mps
        for d in self.world.defending_team:
            d.max_defensive_speed_mps = _def_ceiling

        # ---- GEOMETRY SMOOTH-TIME STAMP (2026-08-16) ----
        # WIRE LIVE: DefenceTuners.geometry_smooth_time_s was previously a
        # dead tuner (Player.defaulted to 0.12; nothing stamped from tuners).
        # Stamp the tuner value onto every defender every OPEN_PLAY frame
        # alongside the ceiling stamp so tuners is the single source of
        # truth for the smooth-damp time consumed in
        # Player._accelerate_toward_target().  Do NOT zero this value —
        # PTB-era suppression is handled by the phase gate inside
        # Player._uses_smooth_damp(), never by zeroing the tuner.
        _smooth_time = self.def_tuners.geometry_smooth_time_s
        for d in self.world.defending_team:
            d._geometry_smooth_time_s = _smooth_time

        bc = self.world.ball_carrier
        if bc is None:
            return

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # DELEGATE TO DefensiveBehaviourUnified â€” the authoritative
        # defensive targeting pipeline (geometry, governor, role spacing,
        # decisions, pursuit mode, line break collapse).
        #
        # The OLD code bypassed this pipeline entirely by computing vx/vy
        # directly toward BC, overriding width-based target_y every frame.
        # Now defenders get width-distributed target_y from the pipeline.
        #
        # Player.update(dt) then resolves target_x/target_y into vx/vy.
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        defenders = [p for p in self.world.defending_team if not p.frozen]

        # â”€â”€ GLOBAL DEFENDER REACTION GOVERNOR â€” advance clock ONCE per frame â”€â”€
        # Player.update() may run twice per OPEN_PLAY frame for SDM-retreat
        # defenders (`_apply_defence_sdm` calls d.update(dt) again).  The
        # reaction window must be wall-clock time, so the hold+blend timer is
        # decremented exactly once here, never inside Player.update().
        for d in defenders:
            d.step_defence_reaction(dt)

        # Compute the defensive line anchor and lane_y
        ruck_x = getattr(self.world, "ruck_x_m", bc.x if bc else 60.0)
        ruck_y = getattr(self.world, "ruck_y_m", bc.y if bc else 35.0)
        # TASK VI (2026-08-16): anchor_x consumes the ANCHOR AUTHORITY
        # (world.ruck_x + 10) — NEVER a bare world.defensive_line_anchor
        # field read or an inline ruck_x + RETREAT_DISTANCE_M derivation.
        # The authority returns the locked/published value (ruck_x + 10)
        # and falls back to the default only pre-ruck (kickoff/catch era).
        anchor_x = _anchor_m(self.world)

        # Compute even Y-spread across the GOVERNED width centred on ruck_y
        # (Task D: all width consumers read the governor's live width).
        def_line_width = governed_width_m(self.world, self.def_tuners)
        n = len(defenders)
        lane_y = []
        half_width = def_line_width * 0.5
        for i in range(n):
            if n > 1:
                t = (i / (n - 1)) * 2.0 - 1.0  # -1..1
            else:
                t = 0.0
            lane_y.append(ruck_y + t * half_width)

        # Deferred D-series pending state (from PTB transitions)
        if self._pending_anchor_x is not None:
            anchor_x = self._pending_anchor_x
            self._pending_anchor_x = None
        if self._pending_spacing_y is not None:
            ruck_y = self._pending_spacing_y
            self._pending_spacing_y = None
        if self._pending_retreat_reset:
            self.defensive_line_manager.reset_retreat_state()
            self._pending_retreat_reset = False

        # â”€â”€ DEFENSIVE LINE ANCHOR RE-PUBLISH (2026-08-08) â”€â”€
        # After a tackle cycle _clear_tes_artefacts() zeroes
        # world.defensive_line_anchor at RELEASE entry, and nothing is
        # guaranteed to restore it before later subsystems read it
        # (fullback anchor, SDM line depth, geometry) â€” it can sit at
        # 0.0 for the rest of the set.  Re-publish the authoritative
        # local anchor_x EVERY OPEN_PLAY defence frame (after the
        # pending-state override so the deferred value wins when set).
        # TASK G ANCHOR AUTHORITY: the write goes through set_anchor so a
        # lock is never silently overwritten â€” republishing the identical
        # ruck_x + 10 value is a no-op; a differing value raises
        # AnchorAuthorityViolation immediately.
        set_anchor(self.world, anchor_x)

        # Dispatch to DefensiveBehaviourUnified
        self.def_behaviour.apply(self.world, defenders, anchor_x, lane_y)

        # â”€â”€ MODULE 1: FULLBACK GEOMETRY AUTHORITY (2026-08-13) â”€â”€
        # Authoritative LAST target writer of the frame.  Re-asserts the
        # fullback's deep backfield anchor AFTER every defensive line
        # writer (spacing / width / retreat / structured-close / depth
        # roles / MMDL / anchor enforcement) in every dispatch branch
        # (pursuit, structured OPEN_PLAY, unified OPEN_PLAY, PTB,
        # tackle-phase retreat, contact, dominance).  The actor is then
        # published + integrated below; Player.update(dt) consumes the
        # STABLE geometry, never a line-collapsed target.  Self-gates on
        # fullback_role_active â€” line defenders are untouched.
        for _d in self.world.defending_team:
            apply_fullback_geometry(self.world, _d)

        # â”€â”€ FULLBACK STATE PUBLISH (2026-08-08) â”€â”€
        # Expose the per-frame fullback actor + anchor target for debug
        # overlays / tooling after the defensive pipeline has run.
        # TASK J AUDIT FIELDS (2026-08-12): publish fullback_role_active,
        # frames_within_absorb_x/y, fullback_absorb_allowed and
        # fullback_arrived alongside the actor + anchor so the debug
        # overlay and kickoff audit logs can show FB state each frame.
        _fb_actor = None
        _fb_tx = None
        _fb_ty = None
        _fb_obj = None
        for _d in self.world.defending_team:
            if getattr(_d, "fullback_role_active", False):
                _fb_actor = _d.player_id
                _fb_tx = getattr(_d, "target_x", None)
                _fb_ty = getattr(_d, "target_y", None)
                _fb_obj = _d
                break
        self.world.fullback_actor_id = _fb_actor
        self.world.fullback_anchor_x = _fb_tx
        self.world.fullback_anchor_y = _fb_ty
        if _fb_obj is not None:
            self.world.fullback_role_active = _fb_obj.fullback_role_active
            self.world.fullback_absorb_allowed = _fb_obj.fullback_absorb_allowed
            self.world.fullback_frames_within_absorb_x = _fb_obj.frames_within_absorb_x
            self.world.fullback_frames_within_absorb_y = _fb_obj.frames_within_absorb_y
            self.world.fullback_arrived = _fb_obj.fullback_arrived
        else:
            self.world.fullback_role_active = False
            self.world.fullback_absorb_allowed = False
            self.world.fullback_frames_within_absorb_x = 0
            self.world.fullback_frames_within_absorb_y = 0
            self.world.fullback_arrived = False

        # â”€â”€ DEFENCE + FULLBACK AUDIT (self-limiting) â”€â”€
        # Frame-accurate snapshot of the OPEN_PLAY defensive state: who the
        # engine selected as fullback, what anchor it was fed, and per-defender
        # flags (frozen / arrival / TES artefacts / lane / writer).  Catches
        # the moment a contaminated defender blocks fullback selection.
        audit_fullback_state(self.world)

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # Apply movement: each defender's Player.update(dt) resolves
        # target_x/target_y set by the pipeline above.
        #
        # MMDL EXCEPTION (2026-08-05): in structured OPEN_PLAY the Movement
        # Mode Decision Layer (DefensiveBehaviourUnified step 10b) has
        # ALREADY integrated position for defenders it moved this frame via
        # apply_movement_state() (MovementState physics, including facing).
        # Running Player.update(dt) again would DOUBLE-INTEGRATE their
        # velocity (~2x speed).  Those defenders are flagged _mmdl_moved;
        # clear the frame-scoped flag here and skip.  All other phases and
        # all ordinary-target defenders behave exactly as before.
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        for p in defenders:
            if p.frozen:
                continue
            if getattr(p, "_mmdl_moved", False):
                p._mmdl_moved = False
                continue
            p.update(dt)

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # Tackle trigger with pre-tackle check (MISS / ARM_GRAB / CONTACT)
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        for p in defenders:
            if p is bc:
                continue
            # ── TACKLE AUTHORITY GATE (2026-08-15, spec §4) ──
            # Only PRIMARY (and any persisted/TES-armed SECONDARY) may
            # start a tackle through physical contact.  LINE / PASSIVE /
            # scramble defenders — even when within TACKLE_RADIUS — can
            # never trigger TES here: the per-side lane system selected
            # the converger, and that defender is the one that engages.
            # The lane pipeline's try_pre_tackle fast path is the other
            # (equivalent) authority route; this gate closes the physical
            # bump path so a random spacing defender cannot steal the
            # tackle.
            tackle_role = getattr(p, "tackle_role", None)
            if tackle_role not in ("PRIMARY", "SECONDARY"):
                continue
            dist = math.hypot(bc.x - p.x, bc.y - p.y)

            if dist < TACKLE_RADIUS_M:
                pre_result = self._check_pre_tackle(bc, p)
                if pre_result == "MISS":
                    if DEBUG_OPE_TRACE:
                        print(f"[PRE_TACKLE] MISS - defender {p.player_id} whiffs")
                    continue
                elif pre_result == "ARM_GRAB":
                    if DEBUG_OPE_TRACE:
                        print(f"[PRE_TACKLE] ARM_GRAB - defender {p.player_id} grabs, BC slowed")
                    bc.vx *= 0.85
                    bc.vy *= 0.85
                    continue
                elif pre_result == "GLANCING":
                    if DEBUG_OPE_TRACE:
                        print(f"[PRE_TACKLE] GLANCING - defender {p.player_id} hits at angle, BC displaced")
                    # Lateral displacement â€” push BC sideways, no TES entry
                    lateral_dir = 1.0 if bc.y > p.y else -1.0
                    bc.y += 0.5 * lateral_dir
                    bc.vy = lateral_dir * 2.0
                    continue
                else:
                    if DEBUG_OPE_TRACE:
                        print(f"[TACKLE] defender {p.player_id} hit BC at dist={dist:.2f}")
                    self._start_tackle(bc, p)
                    return

    def _update_defence_during_tes_drive(self, dt: float) -> None:
        """DELEGATE (2026-08-24): implementation owned by DefenceManager."""
        self.defence_manager.update_tes_drive(self, dt)

    def _disabled_update_defence_during_tes_drive_impl(self, dt: float) -> None:
        """Run the open-play defensive AI for non-ruck defenders during the
        TES drive period (TACKLE_DRIVE / TACKLE_DOMINANCE).

        Corrected governing rule (2026-08-07): non-ruck defenders behave
        EXACTLY like they do in OPEN PLAY while the tackle drive is in
        progress.  They keep their pre-tackle defensive roles and the
        open-play defensive AI (spacing, lanes, role depths, closing,
        decision layer, MMDL) drives their movement.  TES only affects the
        ball carrier and the tackle participants.

        The unified defensive pipeline (``def_behaviour.apply``) dispatches
        on ``world.sub_state`` and gates its TES-specific writers (the TES
        non-tackler PASSIVE gate in classify_depth_all and the
        tes_support_retreat writer in structured_close) on
        ``world.tes_active``.  To reuse the pipeline VERBATIM we temporarily
        project OPEN_PLAY semantics onto the world for the duration of this
        call, then restore the real TES state in ``finally``.  The
        pre-tackle trigger inside the pipeline is suppressed via
        ``self._suppress_pre_tackle`` (checked by ``try_pre_tackle``), and
        ``self._phase`` is not OPEN_PLAY so a second tackle can never start
        mid-TES.
        """
        if self._phase not in ("TACKLE_DRIVE", "TACKLE_DOMINANCE"):
            return
        bc = self.ball_carrier
        if bc is None:
            return

        # TES participants (primary + joiners + attach-slot registry) are
        # owned by TES â€” never run the open-play pipeline on them.  The
        # attach-slot registry mirrors TESContext.attached (the drive-valid
        # participant set) because primary/joiners can churn at the
        # DRIVE/DOMINANCE boundary; excluding by player_id everywhere (head
        # filter, shadow writer, soft-follow tail) keeps every signal in
        # agreement.  Frozen defenders are pinned by apply_phase_invariants
        # + the freeze-compliance loop in update().
        tes_ids = set()
        if self.primary is not None:
            tes_ids.add(self.primary.player_id)
        for j in self.joiners:
            tes_ids.add(j.player_id)
        for _att_pid in getattr(self, "_attached_player_slots", {}):
            tes_ids.add(_att_pid)
        # Canonical TESContext participant registry (2026-08-16): the
        # attach-slot mirror can lag / churn vs TESContext.attached /
        # attached_ids (the verifier's authoritative tes=True source).
        # Merge both so a drive-valid participant can never be shadowed.
        _ctx_attached = getattr(self.tes_ctx, "attached", None)
        if _ctx_attached:
            for _cid in _ctx_attached:
                tes_ids.add(_cid)
        _ctx_attached_ids = getattr(self.tes_ctx, "attached_ids", None)
        if _ctx_attached_ids:
            for _cid in _ctx_attached_ids:
                tes_ids.add(_cid)
        defenders = [
            p for p in self.world.defending_team
            if not p.frozen and p.player_id not in tes_ids
        ]
        if not defenders:
            return

        # Pre-frame position snapshot (2026-08-16): the open-play pipeline
        # below (def_behaviour.apply -> MMDL) integrates these defenders
        # toward the STALE ruck-anchored targets BEFORE the module-2 shadow
        # re-anchors them to the live BC.  Shadowed defenders get their
        # pre-frame position restored in the tail loop so the TES-drive
        # soft-follow is the SINGLE integrator this frame -- otherwise the
        # line visibly rushes to ruck_x + 10 during the drive (the observed
        # -5.5 m backward run while the BC drives +3.5 m forward).
        pre_pos = {d.player_id: (d.x, d.y) for d in defenders}

        # â”€â”€ GLOBAL DEFENSIVE TOP-SPEED CEILING stamp (mirrors _update_defence) â”€â”€
        _def_ceiling = self.def_tuners.max_defensive_speed_mps
        for d in defenders:
            d.max_defensive_speed_mps = _def_ceiling

        ruck_x = getattr(self.world, "ruck_x_m", bc.x)
        ruck_y = getattr(self.world, "ruck_y_m", bc.y)
        # TASK VI: anchor consumes the ANCHOR AUTHORITY (ruck_x + 10),
        # never a bare field read or inline ruck_x + 10 derivation.
        anchor_x = _anchor_m(self.world)

        # Even Y-spread across the GOVERNED width centred on ruck_y
        # (mirrors _update_defence â€” Task D governed width).
        def_line_width = governed_width_m(self.world, self.def_tuners)
        n = len(defenders)
        lane_y = []
        half_width = def_line_width * 0.5
        for i in range(n):
            if n > 1:
                t = (i / (n - 1)) * 2.0 - 1.0  # -1..1
            else:
                t = 0.0
            lane_y.append(ruck_y + t * half_width)

        # â”€â”€ Temporary OPEN_PLAY projection, then restore in finally â”€â”€
        prev_sub_state = self.world.sub_state
        prev_tes_active = self.world.tes_active
        prev_suppress = getattr(self, "_suppress_pre_tackle", False)
        self.world.sub_state = GameSubState.OPEN_PLAY
        self.world.tes_active = False
        self._suppress_pre_tackle = True
        try:
            self.def_behaviour.apply(self.world, defenders, anchor_x, lane_y)
        finally:
            self.world.sub_state = prev_sub_state
            self.world.tes_active = prev_tes_active
            self._suppress_pre_tackle = prev_suppress

        # â”€â”€ MODULE 1: FULLBACK GEOMETRY AUTHORITY (2026-08-13) â”€â”€
        # Same last-writer as _update_defence: the TES-drive projection
        # re-assigned the fullback and ran the open-play writers, so
        # re-assert the deep anchor AFTER the apply (before the movement
        # loop below).  TES-attached / in-contact / commit-armed guards
        # inside apply_fullback_geometry protect the pile geometry.
        for _d in self.world.defending_team:
            apply_fullback_geometry(self.world, _d)

        # â”€â”€ Apply movement (mirrors _update_defence) â”€â”€
        # -- MODULE 2: BC-SHADOW ROLE-DEPTH RE-ANCHOR (2026-08-16) --
        # During TACKLE_DRIVE / TACKLE_DOMINANCE the ball carrier is
        # driven forward (drive LERP) while the pipeline above anchored
        # every line defender to the STALE pre-drive ruck_x
        # (target_x = ruck_x + WIDTH_OFFSETS[depth_role]).  The defensive
        # line therefore held its role depth behind the old ruck while the
        # BC moved forward -- "defenders don't respect their role depth"
        # during the drive.
        #
        # This writer re-anchors every depth-role holder to the LIVE BC:
        #     target_x = bc.x + WIDTH_OFFSETS[depth_role]
        # (the SAME role-depth X table OPEN_PLAY geometry uses).  If the
        # BC drives 2 m forward, every A/B/C/D/CENTRE/WINGER target shifts
        # 2 m in the same direction -- the line steps back WITH the carrier
        # while the drive is in progress instead of rushing to the 10 m.
        #
        # Exempt (their X is owned elsewhere):
        #   - dynamic fullback          (fullback_role.py already re-asserted)
        #   - committed tacklers         (BC-forward tackler_commit target)
        #   - markers / DH / no depth role (play tight to the ruck by design)
        #
        # No Y write, no anchor/authority write: defensive_line_anchor_m
        # stays locked at ruck_x + 10 and the full retreat to the 10 m
        # remains the RELEASE writer's job once the drive completes.
        # TES participants (primary / joiners / attach-slot registry) are
        # part of the tackle -- their X is owned by the TES attach/commit
        # geometry.  Exclude them by PLAYER_ID (tes_ids, built at the head)
        # from the shadow writer AND the soft follow below (verified by
        # ASSERT-E in the shadow verifier).
        _drive_offsets = getattr(getattr(self, "def_tuners", None),
                                 "WIDTH_OFFSETS", None) or {}
        for _s in defenders:
            if _s.player_id in tes_ids:
                continue
            if getattr(_s, "fullback_role_active", False):
                continue
            if getattr(_s, "_tackle_commit_active", False):
                continue
            _role = getattr(_s, "depth_role", None)
            if _role is None or _role not in _drive_offsets:
                continue
            _s.target_x = bc.x + _drive_offsets[_role]
            _s.target_source = "tes_drive_shadow"

        # MMDL already integrated position for defenders it moved via
        # apply_movement_state() (_mmdl_moved); skip those so their velocity
        # is not double-integrated.  Frozen defenders are pinned.
        # SHADOWED DEFENDERS (2026-08-16): the open-play pipeline above
        # integrated them toward the STALE ruck-anchored targets BEFORE this
        # module re-anchored to the live BC.  Restore their pre-frame
        # position so the TES-drive soft-follow is the SINGLE integrator --
        # otherwise the line visibly rushes backward to ruck_x + 10 during
        # the drive (observed -5.5 m while the BC drove +3.5 m forward).
        _drive_ceiling = getattr(
            self.def_tuners, "max_defensive_speed_mps", 8.3
        )
        # POST-WRITER TES-ESCALATION GUARD (2026-08-16): a defender could be
        # shadowed by the writer, then become a TES participant mid-frame
        # (joiner attach / commit escalation during DRIVE).  Their shadow
        # target is now stale and they must be re-owned by TES.  Strip the
        # shadow tag and restore the pre-frame position so the TES motion
        # (p.update below takes them from their real position, not the
        # shadowed step) is the only integrator.  The membership test is
        # re-read LIVE at tail time (entry-time tes_ids can be stale if a
        # joiner was added after the writer ran); the verifier snapshots
        # open_play.joiners AFTER update() returns, so the end-of-frame
        # participant set is the authoritative one.
        _live_tes_ids = set()
        if self.primary is not None:
            _live_tes_ids.add(self.primary.player_id)
        for _lj in self.joiners:
            _live_tes_ids.add(_lj.player_id)
        for _att_pid in getattr(self, "_attached_player_slots", {}):
            _live_tes_ids.add(_att_pid)
        _ctx_attached = getattr(self.tes_ctx, "attached", None)
        if _ctx_attached:
            for _cid in _ctx_attached:
                _live_tes_ids.add(_cid)
        _ctx_attached_ids = getattr(self.tes_ctx, "attached_ids", None)
        if _ctx_attached_ids:
            for _cid in _ctx_attached_ids:
                _live_tes_ids.add(_cid)
        for p in defenders:
            if p.player_id in _live_tes_ids and getattr(
                    p, "target_source", None) == "tes_drive_shadow":
                p.target_source = None
                if p.player_id in pre_pos:
                    p.x, p.y = pre_pos[p.player_id]
                p._mmdl_moved = False
        for p in defenders:
            if p.frozen:
                p.vx = 0.0
                p.vy = 0.0
                p.target_x = p.x
                p.target_y = p.y
                continue
            if p.player_id in tes_ids:
                # TES participants (primary / joiners / attach-slot registry)
                # keep their attach / commit integration -- never soft-follow
                # them off the pile.
                p.update(dt)
                continue
            if getattr(p, "target_source", None) != "tes_drive_shadow":
                # Not shadowed (fullback / markers / DH / no depth role):
                # keep the open-play pipeline's own integration.
                if getattr(p, "_mmdl_moved", False):
                    p._mmdl_moved = False
                else:
                    p.update(dt)
                continue
            if p.player_id in pre_pos:
                _px, _py = pre_pos[p.player_id]
                p.x = _px
                p.y = _py
            if getattr(p, "_mmdl_moved", False):
                p._mmdl_moved = False
            # TES-DRIVE SOFT FOLLOW (2026-08-16): non-tackler line
            # defenders glide toward their role-depth targets with a
            # proportional follow so the line VISIBLY tracks the carrier
            # while the drive is in progress (Committed tacklers and
            # frozen defenders were skipped above -- TES stasis preserved
            # for the tackle economy).
            vx_f = (p.target_x - p.x) * TES_DRIVE_FOLLOW_K
            vy_f = (p.target_y - p.y) * TES_DRIVE_FOLLOW_K
            spd_f = math.hypot(vx_f, vy_f)
            spd_cap = min(
                getattr(p, "max_speed_mps", _drive_ceiling),
                _drive_ceiling,
            )
            if spd_f > spd_cap:
                vx_f = vx_f / spd_f * spd_cap
                vy_f = vy_f / spd_f * spd_cap
            p.vx = vx_f
            p.vy = vy_f
            p.x += p.vx * dt
            p.y += p.vy * dt

    def _apply_defence_sdm(self, dt: float) -> None:
        # DEFENCE DISPATCH CONTRACT (ENGINE LAW) -- structured defence SDM
        # runs during OPEN_PLAY and during the TES drive (TACKLE_CONTACT /
        # TACKLE_DRIVE / TACKLE_DOMINANCE), where no defender is frozen and
        # every non-TES body must keep its depth-role targets.  It stays
        # dormant in TACKLE_INIT, RELEASE, PTB_PREP/EXEC/PTB and
        # POST_RUCK_OPEN_PLAY; is_tackle_phase() stays defense-in-depth for
        # the OPEN_PLAY sub-states.
        if self._phase not in ("OPEN_PLAY", "TACKLE_CONTACT", "TACKLE_DRIVE",
                               "TACKLE_DOMINANCE"):
            return
        if self._phase == "OPEN_PLAY" and is_tackle_phase(self.world):
            return

        # STRUCTURED OPEN-PLAY GATE (2026-08-04): the SDM's OPEN_PLAY
        # branch re-pins every DEF_LINE_READY / DEF_RETREAT_10 defender to
        # bc_x + 10.0 and integrates movement a SECOND time on every OPEN_PLAY
        # frame â€” clobbering the structured pipeline's line-depth step-up
        # (whole line forward) and PRIMARY/SECONDARY close targets before the
        # frame ends.  That is the post-PTB "line doesn't advance" behaviour:
        # after PTB cleanup every defender is DEF_LINE_READY, so the SDM
        # overwrote ALL of them to bc_x + 10 every frame.
        #
        # _apply_structured_open_play() is the sole authoritative writer
        # during structured OPEN_PLAY â€” it already does exactly the intended
        # behaviour: whole line steps up to line_depth_m as one unit (step 7),
        # closest 3 classify PRIMARY/SECONDARY and close at bc.x-1/-2 at line
        # speed (step 10), the rest stay PASSIVE at role depth, non-compliant
        # retreaters go back at line_speedÃ—0.85 (step 9).  The SDM's TES /
        # RELEASE / PTB branches are unreachable here anyway (this method is
        # gated to OPEN_PLAY) â€” the phase handlers own those phases directly.
        if getattr(self.world, "use_structured_open_play", True):
            return

        """Apply deterministic role-based movement for ALL phases.

        Reads the frame context (already built in update()), builds the
        agent_input dict, and calls decide_for_player() for each defender.
        Runs every frame â€” TES/RELEASE/PTB/OPEN_PLAY/POST_RUCK.

        During OPEN_PLAY the SDM assigns role-appropriate depth targets
        relative to the BC (e.g. DEF_RETREAT_10 â†’ bc_x + 10.0).
        The geometry pipeline in _update_defence() handles Y-spread;
        the SDM overrides the X targets with role-appropriate depth.
        """
        # Build agent_input from the frame context stored on world
        ctx = getattr(self.world, "ctx", None)
        if ctx is None:
            return

        frame = ctx["frame"]
        phase_name = frame["phase"]["name"]
        ruck_x = frame["ruck"]["anchor_x"]
        players_raw = frame.get("players", {})

        if not players_raw:
            return  # no player data â€” nothing to decide

        bc = self.world.ball_carrier
        bc_x = bc.x if bc is not None else None

        # SPEC 4.2 (2026-08-04): expose the governor's shared line depth so
        # the SDM's OPEN_PLAY retreat writer targets the line (line_depth_m)
        # instead of inventing a BC-relative bc_x + 10.  Only the legacy
        # scramble branch reaches this â€” structured OPEN_PLAY returns early
        # at the gate above.
        gov_depth = self.def_behaviour.line_governor.line_depth
        agent_input = {
            "phase": phase_name,
            "is_tes": frame["phase"]["is_tes"],
            "is_release": frame["phase"]["is_release"],
            "is_ptb": frame["phase"].get("is_ptb", False),
            "is_open_play": frame["phase"]["is_open_play"],
            "ruck_x": ruck_x,
            "bc_x": bc_x,
            "line_depth_m": gov_depth,
            "players": players_raw,
        }

        # Decide for every defender this frame
        for d in self.world.defending_team:
            pid = d.player_id
            decision = decide_for_player(agent_input, pid)

            if decision["action"] == "retreat_10":
                d.target_x = decision["target_x"]
                # SPACING Y (Task F, 2026-08-10 + 2026-08-12): SDM retreat
                # never overrides spacing Y â€” retreat_target[player].y =
                # spacing_targets[player].y, consumed from the un-gated frame
                # preamble's fresh dict when available.
                d.target_y = _frame_spacing_y(self.world, d, self.def_tuners)
                # LINE-SPEED UNIFICATION (2026-08-04): SDM retreat derives
                # from the governor's shared line speed (falling back to
                # 0.85x of the unified ceiling when no governor state is
                # available).  This stops the SDM re-pinning retreaters to
                # a different pace than the structured line writers.
                gov_speed = getattr(
                    self.def_behaviour.line_governor, "line_speed", None
                )
                ceiling = min(
                    d.max_speed_mps,
                    self.def_tuners.max_defensive_speed_mps,
                ) * 0.85
                d.move_speed = min(gov_speed, ceiling) if gov_speed is not None else ceiling
                # Apply movement â€” never block unfrozen defenders from retreat
                if not d.frozen:
                    d.update(dt)
                if __debug__:
                    if DEBUG_OPE_TRACE:
                        print(f"[SDM] retreat_10 id={pid} x={d.x:.2f} -> {decision['target_x']:.2f} {decision['reason']}")

            # action == "hold" â€” do nothing, position/target is managed
            # by the phase handler or freeze enforcement.

    def _check_pre_tackle(self, bc, defender) -> str:
        """Pre-tackle check: returns 'MISS', 'ARM_GRAB', or 'CONTACT'.

        Evaluates defender approach angle and relative speed to determine
        whether the defender makes solid contact, an arm-grab, or whiffs entirely.
        """
        dx = bc.x - defender.x
        dy = bc.y - defender.y
        dist = math.hypot(dx, dy)
        if dist < 0.01:
            return "CONTACT"  # too close to miss

        # Defender approach direction
        def_spd = math.hypot(defender.vx, defender.vy)
        if def_spd > 0.1:
            def_dx = defender.vx / def_spd
            def_dy = defender.vy / def_spd
        else:
            def_dx = math.cos(defender.facing)
            def_dy = math.sin(defender.facing)

        # Direction from defender to BC
        to_bc_dx = dx / dist
        to_bc_dy = dy / dist

        # Dot product: 1.0 = head-on, 0.0 = side-on, -1.0 = chasing away
        dot = def_dx * to_bc_dx + def_dy * to_bc_dy

        # Relative speed: defender must have speed advantage for solid contact
        bc_spd = math.hypot(bc.vx, bc.vy)
        rel_speed = def_spd - bc_spd * 0.5

        # Composite tackle score (0..1)
        angle_score = (dot * 0.5 + 0.5)  # 0.0 (worst) to 1.0 (head-on)
        speed_score = max(0.0, min(1.0, rel_speed / 10.0))
        tackle_score = angle_score * 0.6 + speed_score * 0.4

        MISS_THRESHOLD = 0.3
        ARM_GRAB_THRESHOLD = 0.6
        GLANCING_THRESHOLD = 0.75

        if tackle_score < MISS_THRESHOLD:
            return "MISS"
        elif tackle_score < ARM_GRAB_THRESHOLD:
            return "ARM_GRAB"
        elif tackle_score < GLANCING_THRESHOLD:
            return "GLANCING"
        else:
            return "CONTACT"

    def _check_broken_tackle_scoring(self, ctx, outcome) -> bool:
        """Check if BC overpowers the defender based on computed dominance.

        Returns True for a broken tackle: the attacker is winning the
        dominance battle (WEAK result) AND the scripted drive is large
        enough to be a genuine break.

        DRIVE-BASED GATE (2026-08-04): the old momentum-ratio check
        (bc_mom > def_mom * 1.5) was unreachable because both sides used
        stale pre-tackle approach speeds (probe: bc_mom=743 vs
        def_mom=1452, requiring 2179) â€” so the break path could never
        fire.  A WEAK outcome with a large forward drive is the real
        signal that the BC is breaking the tackle.
        """
        from game.tes.tes_state import TackleResult

        if outcome.tackle_result != TackleResult.WEAK:
            return False

        # Forward drive magnitude (team_attack_dir applied, so the signed
        # value is positive when the BC is driven toward the attacking
        # tryline = attacker winning).
        drive_m = getattr(outcome, "drive_distance_m", 0.0)

        # ── MULTI-BODY BREAK WINDOW (2026-09-23) ──
        # The contest's declaration may have OPENED a tackle-break window: an
        # ARM_GRAB that left the carrier with a single in-radius body means the
        # wrap was incomplete, and a break calculation is owed.  The window does
        # not force a break — the drive test below still decides — it LOWERS the
        # drive threshold that test runs against, by a factor the TES leaf
        # declares.
        #
        # With the window closed this returns BREAK_DRIVE_THRESHOLD_M UNCHANGED.
        # That is every contest whose declaration opened no window, every
        # context built outside tes_start_tackle(), and every run with the
        # stage's lever switched off — so the comparison is bit-identical to
        # the stage before this one in all of them.
        from game.tes.tes_multi_body import (
            break_drive_threshold as _break_drive_threshold,
        )
        threshold_m = _break_drive_threshold(
            BREAK_DRIVE_THRESHOLD_M, getattr(ctx, "wrap_model", None),
        )
        return drive_m > threshold_m

    # ------------------------------------------------------------------
    # STAGE 3.5 -- BUMP CLEARANCE PATH
    # ------------------------------------------------------------------

    def _bump_off_pending(self) -> bool:
        """STAGE 3.5: has this contest's declaration resolved to BUMP_OFF?

        The collision state is derived ONCE, from the declaration, by
        game.tes.tes_multi_body.resolve_collision_state() and mirrored onto
        the frame's TESContext by tes.tes_start_tackle() (see
        _attach_wrap_model).  BUMP_OFF means the carrier's momentum cleared
        the whole defensive wrap by more than the leaf's declared threshold --
        the carrier won the collision outright, so the wrap is torn down
        instead of grinding the carrier through a pre-baked tackle.

        Returns False whenever the lever is off, no context is attached, or
        the token is anything else at all (including the undecided identity),
        so a neutral or dominant declaration never reaches this path.
        """
        if not bump_clearance_enabled():
            return False
        ctx = self.tes_ctx
        if ctx is None:
            return False
        from game.tes.tes_multi_body import COLLISION_STATE_BUMP_OFF
        return getattr(ctx, "wrap_collision_state", None) == COLLISION_STATE_BUMP_OFF

    def _progressive_drag_pending(self) -> bool:
        """STAGE 3.1: has this contest's declaration resolved to a GRIND?

        PROGRESSIVE_DRAG is the CONTEST state of the collision
        discriminator: the margin between the carrier's momentum and the
        whole defensive wrap cleared the leaf's declared threshold in
        NEITHER direction, so neither side won the collision and the
        contact is a grind -- the carrier is dragged to a stop instead of
        running through the wrap or being smashed by it.

        TWO guards beyond the token itself, both deliberate:

          1. ``ctx.wrap_model is not None`` -- the token's own DEFAULT is
             PROGRESSIVE_DRAG (TESContext declares the leaf's undecided
             identity), and Stage 3.4 documented that every UNMEASURABLE
             margin (no record, no model, NaN, +/-inf) resolves to that
             same token.  Testing the token alone would therefore drag
             every tackle that never DECLARED a contest -- single-body
             contacts, contexts built outside tes_start_tackle(), and the
             ``RLM_TES_MULTI_BODY=0`` rollback arm.  A grind is a MEASURED
             contest, so it requires a resolved model.
          2. the family lever -- disarmed, this is False everywhere and the
             DRIVE frame is bit-identical to the pre-Stage-3.1 engine.

        Never raises: an absent context, an absent token, a non-string token
        and a context whose ``wrap_model`` attribute is missing all return
        False.  Mirrors the shape of _bump_off_pending() above.
        """
        if not bump_clearance_enabled():
            return False
        ctx = self.tes_ctx
        if ctx is None:
            return False
        if getattr(ctx, "wrap_model", None) is None:
            return False
        from game.tes.tes_multi_body import (
            COLLISION_STATE_PROGRESSIVE_DRAG,
        )
        return (
            getattr(ctx, "wrap_collision_state", None)
            == COLLISION_STATE_PROGRESSIVE_DRAG
        )

    def _apply_progressive_drag(self, bc: Player, ctx: TESContext) -> None:
        """Apply ONE frame of the PROGRESSIVE_DRAG grind.

        A grind must DECELERATE the carrier, but TACKLE_DRIVE is KINEMATIC:
        bc.x is written from ``ctx.drive_det_x`` -- the accumulated
        ``ctx.drive_step_mps * dt`` -- and bc.y is a straight lerp on the
        drive fraction, so the drive never integrates bc.vx/bc.vy into
        position at all.  Decaying only the velocity vector would therefore
        be invisible: the carrier would march the full drive at full
        authority while its velocity vector quietly shrank.  The decay
        lands on BOTH quantities:

          * ``ctx.drive_step_mps`` -- the DRIVE's own motion authority, the
            code's declared "metres per second of BC forward movement".
            This is what actually slows the carrier, and it is the SAME
            quantity Step 3.2's terminal floor reads when deciding that the
            grind is spent.
          * ``bc.vx`` / ``bc.vy`` -- the carrier's velocity vector, so every
            consumer of live velocity sees the same grind (the per-frame
            momentum diagnostic, and the momentum the post-teardown frame
            inherits when the drive ends).

        ONE multiplicative step per call, so the authority after n frames is
        exactly ``v0 * PROGRESSIVE_DRAG_DECAY ** n``.  The caller is the
        intercept in _update_tackle_drive(), and it is guarded by
        _progressive_drag_pending() -- this helper is a pure scale, not a
        gate, so it is never reached with the lever off or with no model.
        """
        ctx.drive_step_mps *= PROGRESSIVE_DRAG_DECAY
        bc.vx *= PROGRESSIVE_DRAG_DECAY
        bc.vy *= PROGRESSIVE_DRAG_DECAY

    def _progressive_drag_spent(self, ctx: TESContext) -> bool:
        """STAGE 3.2: has this grind run out of forward steam?

        True only when the family lever is armed, a RESOLVED drag is live
        (see _progressive_drag_pending) and the drive's own forward authority
        has decayed BELOW PROGRESSIVE_DRAG_TERMINAL_FLOOR_MPS.

        This is the terminal half of the law the decay opens: a grind is
        supposed to BRING THE CARRIER TO A STOP, and a drive that is still
        running once the carrier has stopped is a drive holding twenty-two
        bodies still for no reason.  Below the floor the tackle is complete,
        so the caller converts the contact into a standard ruck.

        Never raises: _progressive_drag_pending() is the gate and it is
        total, and a context with no readable ``drive_step_mps`` is treated
        as NOT spent (the drive keeps its normal timer exit) rather than as
        spent.
        """
        if not self._progressive_drag_pending():
            return False
        step = getattr(ctx, "drive_step_mps", None)
        if not isinstance(step, (int, float)):
            return False
        return step < PROGRESSIVE_DRAG_TERMINAL_FLOOR_MPS

    def _exit_progressive_drag_to_ruck(
        self, bc: Player, ctx: TESContext
    ) -> None:
        """STAGE 3.2: end a spent grind and form the ruck.

        The drive is abandoned mid-phase, so this performs the two steps the
        normal exits would otherwise have performed either side of it:

          1. the DRIVE-END LOCK, verbatim from the DRIVE -> DOMINANCE
             transition.  The engine's law is that BC and the ruck/PTB
             anchors derive from the DRIVE END and never from the contact
             point; skipping the lock would leave ``ctx.drive_target_x``
             -- and so every downstream reader of "where the drive finished"
             -- still holding the drive's ENTRY value.
          2. the DOMINANCE -> RELEASE handover, verbatim.
             ``_prepare_release()`` is the engine's own ruck formation: it
             anchors ruck_x / ruck_y / ptb_anchor / last_contact on bc.x,
             resets the PTBManager cycle, assigns markers, converts every
             non-marker, non-DH defender to DEF_RETREAT_10 and clears the
             TES commit locks.  Setting ``_just_entered_release`` lets
             ``_update_release()``'s own first frame run
             ``_clear_tes_artefacts()`` + ``_detach_all_tacklers()``, which
             is what actually drops the tackle context, the attachments and
             the TES locks.  Hand-clearing any of that here would
             double-teardown and desync the audit's freeze mask.

        The phase entered is RELEASE -- the head of the standard Play-the-Ball
        chain (RELEASE -> PTB_PREP -> PTB_EXEC -> PTB -> POST_RUCK_OPEN_PLAY)
        and the only ruck entry this engine has.  There is no
        "RUCK_FORMATION" phase or sub-state to transition to.
        """
        drive_end_x = bc.x
        ctx.drive_target_x = drive_end_x
        bc.target_x = drive_end_x
        self.world.ruck_x_m = drive_end_x
        self._follow_attached_tacklers()

        self._prepare_release()
        self._just_entered_release = True
        self.world.post_tes_settle_frames = getattr(
            self.def_tuners, "post_tes_settle_frames", 0
        )
        self.world.post_tes_settle_enabled = (
            getattr(self.def_tuners, "post_tes_settle_frames", 0) > 0
        )
        self._set_phase(
            "RELEASE",
            GameSubState.TACKLE_RELEASE,
            "PROGRESSIVE_DRAG_COMPLETE->RELEASE",
        )

    def _arm_bump_carrier_burst(self, bc: Player) -> None:
        """Keep the carrier's momentum: no damp, plus a short forward burst.

        The attack pipeline writes bc.move_speed = bc.max_speed_mps * 0.9
        every frame, so a one-off bump here would be overwritten on the very
        next frame.  The window is therefore re-stamped each frame by
        _tick_bump_carrier_burst() (called from _update_open_play).

        The velocity VECTOR itself is never touched -- only the scalar speed
        authority is raised -- so the carrier keeps heading where its steering
        authority was already taking it.
        """
        self._bump_carrier_burst_s = BUMP_OFF_CARRIER_BURST_S
        top_speed = getattr(bc, "max_speed_mps", None)
        if top_speed is not None:
            bc.move_speed = top_speed * BUMP_OFF_CARRIER_BURST_SCALE

    def _tick_bump_carrier_burst(self, dt: float) -> None:
        """Re-apply the post-bump-off carrier burst for its declared window.

        Only ever armed by _arm_bump_carrier_burst().  With no clearance in
        flight the window is 0.0 and this returns immediately, so it costs
        nothing on every other frame of the match.
        """
        if self._bump_carrier_burst_s <= 0.0:
            return
        self._bump_carrier_burst_s = max(0.0, self._bump_carrier_burst_s - dt)
        if self._bump_carrier_burst_s <= 0.0:
            return
        bc = self.ball_carrier
        if bc is None:
            return
        top_speed = getattr(bc, "max_speed_mps", None)
        if top_speed is not None:
            bc.move_speed = top_speed * BUMP_OFF_CARRIER_BURST_SCALE

    @staticmethod
    def _clear_bump_commit_lock(player: Player) -> None:
        """Drop the TES-drive commit locks a bumped-off tackler would keep.

        _prepare_release() and its DOMINANCE->RELEASE sweep clear
        _tackle_commit_active / _pursuit_commit_active on the normal exit, but
        the plain broken-tackle path leaves them set -- and a body still
        holding a commit lock is skipped by the MMDL / target stabiliser /
        reactivate_movement_after_thaw, so it stands rooted instead of
        rejoining the line (see the STALE COMMIT-LOCK WIPE note in
        _prepare_release).  A bumped-off tackler is BEATEN: it must re-enter
        the line clean, and it must not be able to re-tackle the carrier from
        behind the moment its freeze expires.
        """
        for attr in ("_tackle_commit_active", "_pursuit_commit_active"):
            if hasattr(player, attr):
                setattr(player, attr, False)
        if hasattr(player, "tackle_role"):
            player.tackle_role = None

    def _handle_broken_tackle(self, *, bump_off: bool = False):
        """Broken tackle: defender falls, BC continues in OPEN_PLAY.

        No RELEASE, no PTB, no ruck formed.  The BC gets a slight speed
        penalty but remains in open play.  The defender(s) go to ground.

        STAGE 3.5 -- ``bump_off=True`` is the BUMP CLEARANCE variant, entered
        when the declaration resolved ``wrap_collision_state == "BUMP_OFF"``.
        It is the SAME teardown -- same phase exit, same role/depth
        invalidation, same chase re-arming -- with three declared deltas:

          1. the carrier KEEPS its momentum: the ``*0.9`` damp is replaced by
             the short forward burst armed by _arm_bump_carrier_burst();
          2. the wrap is torn down explicitly at the source via
             _detach_all_tacklers() -- the plain path deliberately skips it
             and lets the next _update_tackle_init re-sync stale
             ``tes_attached`` flags (the ATTACH-FLAG / REGISTRY SYNC note
             there), which is a repair rather than a teardown;
          3. the primary defender is knocked down for longer
             (BUMP_OFF_DEFENDER_FREEZE_S, not 1.0 s) and its TES commit locks
             are cleared, so it cannot instantly re-tackle from behind.

        Every existing call site passes ``bump_off`` unset, so the plain path
        is bit-identical to the pre-Stage-3.5 engine.
        """
        bc = self.ball_carrier
        d = self.primary

        # ── POST-MISS PURSUIT ENTITLEMENT (2026-09-26) ──
        # The bodies the carrier beat OUTRIGHT are recorded here, before the
        # teardown drops every handle to them, so they can be ENTITLED to
        # pursue once their freeze expires.  The engine's own BROKEN-TACKLE
        # CHASE RESET at the end of this method already re-arms every
        # UNFROZEN line defender; the beaten body is skipped by it solely
        # because it is set ``frozen`` a few lines below.  The entitlement is
        # what carries that same intent across the freeze, and it is what
        # stops the obligated-body retreat authority re-seizing the body the
        # frame after expiry (the measured ``retreat_non_compliant`` drive
        # AWAY from the carrier).
        _beaten = []
        if d is not None:
            _beaten.append(d)
        if bump_off:
            for _joiner in self.joiners:
                if _joiner is not None and _joiner is not d:
                    _beaten.append(_joiner)

        # Grip teardown BEFORE the context is dropped: _detach_all_tacklers()
        # reads self.tes_ctx, so it has to run while the context is still
        # alive.  This is what makes the clearance a teardown rather than the
        # repair the plain broken-tackle path relies on.
        if bump_off:
            self._detach_all_tacklers()

        # BC: continues in open play
        if bc:
            if bump_off:
                # BUMP CLEARANCE: momentum kept, plus a burst away.
                self._arm_bump_carrier_burst(bc)
            else:
                bc.vx *= 0.9
                bc.vy *= 0.9
            # -- CARRIER RELEASE IS OWNED HERE (missed-tackle freeze fix) --
            # tes_start_tackle() froze THIS body (bc.frozen = True).  The only
            # other carrier unfreeze lives in _set_phase(), and that one
            # targets self.world.ball_carrier -- a DIFFERENT reference.
            # Whenever the two diverge (the PTB DH swap re-points
            # engine.ball_carrier, the attack/kickoff code re-points
            # world.ball_carrier) the tackled carrier was never released and
            # stayed frozen for the rest of the match.  Release the carrier
            # this teardown actually owns, unconditionally, so the exit no
            # longer depends on which handle the phase entry happens to read.
            bc.frozen = False
            bc.freeze_timer = 0.0
            bc.tes_locked = False
            bc.in_contact = False
            bc.anim_state = ""

        # Primary defender: goes to ground
        if d:
            # ── FULLBACK FREEZE EXEMPTION (2026-09-29) ──
            # THE DEFECT THIS REMOVES ("the fullback chases for a bit and
            # then freezes").  The tagged fullback is the EMERGENCY DEFENDER:
            # on a genuine line break it is the only body between the carrier
            # and the try line, and it is admitted to tackle selection for
            # exactly that reason.  Freezing it on the miss stalled the LAST
            # defender for 1.0 s / 1.2 s at the precise moment the carrier ran
            # clear, and the freeze pin then rewrote its target to its own
            # position every frame (target_x = x) while movement_state stayed
            # CHASE -- the body was ordered to sprint to where it already
            # stood, so Player.update produced ZERO velocity.
            #
            # Nothing could rescue it either: EVERY recovery writer hard-
            # exempts the fullback.  line_break_static_guard.
            # is_static_guard_recovery_eligible returns False for
            # ``fullback_role_active``, post_miss_pursuit.is_exempt_role
            # returns True for it, and freeze_expiry_pursuit.is_expiry_
            # pursuit_eligible delegates to that guard -- so
            # decay_freeze_timers never handed the body back and fell through
            # to its legacy self-pin (target_x = x, target_y = y), leaving the
            # last defender a stationary plant at contact range.
            #
            # The sweeper's own geometry authority owns this body and re-owns
            # it on the next frame (the teardown below clears the engagement /
            # commit / lane stamps, so apply_fullback_geometry stops yielding
            # and re-asserts the breakaway CHASE).  A body that is never
            # frozen needs no rescue.
            if getattr(d, "fullback_role_active", False):
                # Keep the last defender mobile and clear any stale pin so it
                # integrates this frame instead of being held on the ground.
                d.frozen = False
                d.freeze_timer = 0.0
                for _pin_attr in ("_freeze_pin_active", "_freeze_pin_x",
                                  "_freeze_pin_y"):
                    if hasattr(d, _pin_attr):
                        try:
                            delattr(d, _pin_attr)
                        except AttributeError:
                            pass
            else:
                d.frozen = True
                d.freeze_timer = (
                    BUMP_OFF_DEFENDER_FREEZE_S if bump_off else 1.0
                )
            d.is_tackler = False
            d.in_contact = False
            # ATTACHMENT RELEASE (2026-09-30): the contest is over, so the
            # TES attachment is released here too.  ``in_contact`` alone was
            # never enough: ``tes_attached`` is the flag the geometry
            # authority tests first, and leaving it set gave the body no
            # movement writer until the NEXT tackle's repair pass.  See
            # ``release_broken_tackle_attachment``.
            release_broken_tackle_attachment(d)
            d.tes_locked = False
            d.tes_tackler_role = None
            d.anim_state = "FALLEN"
            # BEATEN-BODY COMMIT-LATCH RELEASE (2026-09-26, both paths)
            # ``_clear_bump_commit_lock``'s own docstring states the defect:
            # "the plain broken-tackle path leaves them set -- and a body
            # still holding a commit lock is skipped by the MMDL / target
            # stabiliser / reactivate_movement_after_thaw, so it stands
            # rooted instead of rejoining the line".  Measured live as
            # EXEMPT_BY_COMMIT_LATCH: 9.6% of post-miss frames, in which no
            # proposal reaches the writer-priority arbiter at all.  A body
            # the carrier beat outright is beaten on BOTH paths, so the
            # release is issued on both.  Gated on the pursuit lever, so the
            # disarmed arm stays the pre-change engine byte for byte.
            from game.defence.defence_lvl_1.post_miss_pursuit import (
                post_miss_pursuit_enabled as _pmp_enabled,
            )
            if _pmp_enabled():
                self._clear_bump_commit_lock(d)

        # Joiners released
        for j in self.joiners:
            j.tes_locked = False
            j.in_contact = False
            # ATTACHMENT RELEASE (2026-09-30): a joiner is attached exactly
            # like the primary, so it is released by the same teardown.
            release_broken_tackle_attachment(j)
            j.is_tackler = False
            j.tes_tackler_role = None
            if bump_off:
                self._clear_bump_commit_lock(j)

        # Reset TES state
        self.tes_ctx = None
        self.tes_outcome = None
        self.world.tes_active = False
        if hasattr(self.world, "tackler_ids"):
            self.world.tackler_ids.clear()
        self.primary = None
        self.joiners.clear()
        # Reset broken-check flag for next tackle cycle
        self._broken_checked_this_contact = False
        # Clear dominance-computed flag if present
        if hasattr(self, '_dominance_computed_for_broken'):
            del self._dominance_computed_for_broken

        # ── BROKEN-TACKLE LINE RE-ENTRY (2026-08-30) ──
        # Mirror _prepare_release's role/depth invalidation so a broken
        # tackle joins the line as a normal line defender, not a stale
        # TACKLER_*/MARKER_* ghost.  Without this sweep, the ex-tackler
        # still carries TACKLER_* (EXCLUDED_ROLES in assign_depth_roles),
        # so its role_depth_target_x / depth_role are NEVER re-stamped:
        #   (1) the DRIVE-ENTRY moved the ruck (ruck_x_m = bc.x) forward,
        #       so the stale pre-drive role_depth_target_x no longer equals
        #       the post-drive anchor -> assert_anchor_consistency raises
        #       AnchorAuthorityViolation (Layer 8), and
        #   (2) the stale depth_role persists as a ruck-adjacent A/D
        #       claimant alongside the fresh A -> two A's stacking at the
        #       pivot (the exact defect _dedupe_single_capacity_claims /
        #       _ensure_exactly_one_a were added for).
        # Clearing defender_role (TACKLER_*/MARKER_*) lets
        # assign_def_line_ready promote them into the line next frame;
        # clearing tackle_state_active drops the tackle-state gate so they
        # re-enter the depth pool immediately; clearing the depth stamps
        # forces a fresh re-seed against the post-drive ruck.
        for _p in self.world.defending_team:
            if getattr(_p, "fullback_role_active", False):
                continue
            if _p.defender_role in (
                DefenderRole.TACKLER_1,
                DefenderRole.TACKLER_2,
                DefenderRole.TACKLER_3,
                DefenderRole.MARKER_A,
                DefenderRole.MARKER_B,
            ):
                _p.defender_role = None
                _p.tackle_state_active = False
            _p.depth_role = None
            _p.depth_side = None
            _p._depth_seeded = False
            _p.role_depth_target_x = None
            _p.role_depth_target_y = None

        # ---- BROKEN-TACKLE CHASE RESET (2026-09-10) ----
        # On a failed tackle the BC is back in OPEN_PLAY but the remaining
        # defenders carry stale TES-era target_x / movement_state, so they'd
        # sit still on a dead static target instead of re-entering pursuit.
        # Force every unfrozen line defender to chase the LIVE BC: target_x
        # = bc.x and movement_state = CHASE.
        _bc = getattr(self.world, "ball_carrier", None)
        if _bc is not None:
            for _d in self.world.defending_team:
                if getattr(_d, "fullback_role_active", False):
                    continue
                if getattr(_d, "defender_role", None) in (
                    DefenderRole.MARKER_A,
                    DefenderRole.MARKER_B,
                    DefenderRole.DUMMY_HALF,
                ):
                    continue
                if getattr(_d, "frozen", False):
                    continue
                _d.target_x = float(_bc.x)
                _d.movement_state = MovementState.CHASE
                if hasattr(_d, "retreat_delay"):
                    _d.retreat_delay = 0.0
                if hasattr(_d, "timing_locked"):
                    _d.timing_locked = False
                if hasattr(_d, "reaction_timer"):
                    _d.reaction_timer = 0.0
                if hasattr(_d, "lateral_hesitation"):
                    _d.lateral_hesitation = 0.0
                if hasattr(_d, "step_stagger"):
                    _d.step_stagger = 0.0

        # ── ARM THE POST-MISS PURSUIT ENTITLEMENT (2026-09-26) ──
        # Runs LAST, after the role/depth invalidation sweep above, so the
        # beaten body's role is already normalised (a stale TACKLER_*/MARKER_*
        # role would make it an exempt dedicated owner and refuse the arm).
        # The body is NOT unfrozen here: it serves its 1.0 s / 1.2 s stun
        # exactly as before.  What changes is the EXPIRY hand-back, which
        # ``decay_freeze_timers`` performs -- and which the entitlement makes
        # survive, because the retreat authority, the MMDL and the recovery
        # writers all consult it.
        if _beaten:
            from game.defence.defence_lvl_1.post_miss_pursuit import (
                arm_post_miss_pursuit as _arm_pursuit,
            )
            for _beaten_body in _beaten:
                _arm_pursuit(self.world, _beaten_body, bump_off=bump_off)

        # ── FULLBACK EMERGENCY ENGAGEMENT RELEASE ──
        # The broken tackle ENDS the tackle cycle, but it never enters
        # RELEASE -- and RELEASE entry (_clear_tes_artefacts) is the only
        # site that used to clear the fullback emergency markers.  A tagged
        # fullback this tackle armed therefore kept ``_fb_emergency_engaged``
        # and ``defender_role = TACKLER_1`` for the rest of the match, and
        # that leaked pair makes ``fullback_tackle_authority_active`` (hence
        # ``fullback_tackle_side_owner_live``) permanently True: the geometry
        # authority yields every frame, ``establish_fullback_live_break_chase``
        # declines, and no target / state / speed is ever written -- the
        # sweeper never resumes its chase.  The engagement is tackle-cycle
        # state, so the teardown that ends the cycle must release it.
        # ``release_fullback_emergency_on_miss`` iterates the WHOLE defending
        # team (a fullback that joined the pile as TACKLER_2/3 is released by
        # the same teardown) and is gated on RLM_FB_EMERGENCY_RELEASE_ON_MISS
        # (default ON) so RLM_FB_EMERGENCY_RELEASE_ON_MISS=0 restores the
        # pre-change engine with no code revert.
        from game.defence.defence_lvl_1.fullback_role import (
            release_fullback_emergency_on_miss as _fb_release_on_miss,
        )
        _fb_release_on_miss(self.world)
        # ── THE BREAK LATCH SURVIVES THE MISS (2026-10-01) ──
        # A BROKEN TACKLE IS NOT A COMPLETED DRIVE, so it must not end
        # EMERGENCY_CHASE.  This site used to capture every tagged fullback's
        # latch, blanket-clear it with ``clear_fullback_chase_state``, and
        # then re-arm it -- always stamping the synthetic kind "DEPTH", which
        # also threw away a LATERAL break's identity.  The latch is now
        # simply LEFT STANDING across the miss: the teardown above releases
        # the missed body's ROLE with ``clear_latch=False``, so the last
        # defender keeps its pursuit state, its anchor record and its live
        # chase through the broken tackle and into the following frames.
        # The latch is tackle-cycle state and only the RELEASE-entry cycle
        # end (``_clear_tes_artefacts``) drops it.

        # Return to OPEN_PLAY - no RELEASE, no PTB, no ruck
        self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "BROKEN_TACKLE")
        if DEBUG_OPE_TRACE:
            print(f"[BROKEN_TACKLE] BC #{bc.player_id if bc else '?'} breaks tackle - back to OPEN_PLAY")

    def _lazy_initialize_attack_dna(self, player: Player) -> None:
        """Stamp the permanent attack DNA onto *player* on first sight.

        OPERATION ATTACKTIVATE -- PHASE 1 (LAZY INITIALISATION).

        The engine never walks the roster at boot.  The first frame a body
        reaches an attack DNA reader, its PERMANENT ``group_id`` /
        ``hemisphere`` are copied from the static registries
        (POSITION_GROUP_MAP / POSITION_HEMISPHERE_MAP) and it is seeded as a
        passive background trailer.

        The presence of ``group_id`` is the sentinel: once stamped, a later
        call is a no-op, so this never clobbers a role that the master
        override (or, in a later phase, the one-frame role lock) assigned.

        The jersey is read from ``player.jersey_number`` -- the value
        KickoffEngine.create_teams() stamps 1..13 -- NOT ``player_id``.  Any
        unmapped jersey (bench, an unstamped body, a non-integer) fails
        gracefully: ``group_id`` / ``hemisphere`` become None and the body
        simply never matches a rollout gate.
        """
        if hasattr(player, "group_id"):
            return
        jersey = getattr(player, "jersey_number", 0)
        try:
            jersey = int(jersey)
        except (TypeError, ValueError):
            jersey = 0
        player.group_id = POSITION_GROUP_MAP.get(jersey)
        player.hemisphere = POSITION_HEMISPHERE_MAP.get(jersey)
        player.runtime_role = "PASSIVE_TRAIL"

    def _assign_dh_for_tackle(self) -> None:
        """Assign the closest non-BC attacker as the DH at tackle entry.

        This is intentionally early: the DH should exist through the TES drive so
        its later PTB approach begins from a closer, live position behind the BC
        rather than being created only at DOMINANCE -> RELEASE.

        HOOK 2 (Operation Attacktivate, Phase 1): every attacker processed
        here is first passed through the lazy DNA injector, then the selected
        DH / every non-BC attacker is role-gated.  Attack-side role logic is
        owned HERE and is never mixed into the defensive per-frame loops
        (e.g. _run_idle_open_play_pursuit).
        """
        bc = self.ball_carrier
        if bc is None:
            return
        team = getattr(self.world, "attacking_team", []) or []
        if not team:
            return

        # -- HOOK 2 INTERCEPT: lazy DNA on every processed attacker --------
        # Every body on the attacking team is stamped before any role logic
        # reads it, so a first-sight attacker is never gated on missing DNA.
        for player in team:
            self._lazy_initialize_attack_dna(player)

        candidate = self.dh
        if candidate is None or candidate is bc:
            candidate = self._find_dh(team, bc.x, bc.y, bc)
        if candidate is None:
            return

        self.dh = candidate
        self.dh.attack_role = "dummy_half"
        self.dh.defender_role = DefenderRole.DUMMY_HALF
        self.dh.frozen = False
        self.dh.tackle_locked = False
        self.dh.target_x = bc.x - 1.0
        self.dh.target_y = getattr(self.world, "ruck_y_m", bc.y)

        # -- MASTER OVERRIDE -----------------------------------------------
        # The ball carrier is the master role: it is NEVER gated by a group
        # rollout flag and it skips every group AI assignment below.
        bc.runtime_role = "BC"

        # -- ATTACK FALLBACK GATING + SPINE HOOK ---------------------------
        for player in team:
            if player is bc:
                continue

            group = getattr(player, "group_id", None)
            group_active = ATTACK_GROUP_ROLLOUT_FLAGS.get(group, False)

            if not group_active:
                # FALLBACK GATING: FORWARDS / BACKS / FB rollouts are off, so
                # the body is held in PASSIVE_TRAIL.  No new locomotion
                # authority is created here -- the existing passive background
                # trailing relative to the ball and ruck (the open-play chase
                # and the DH preposition writers) is preserved untouched.
                player.runtime_role = "PASSIVE_TRAIL"
                continue

            if group == "SPINE" and ATTACK_ROLLOUT_SPINE_ACTIVE:
                # SPINE HOOK (placeholder): the upcoming ONE-FRAME ROLE LOCK
                # lands HERE.  Phase 1 leaves the role unstamped so current
                # behaviour is preserved.
                pass


    def _dh_approach_speed(self, gap: float) -> float:
        """Gap-graded DH approach speed (m/s) -- the ONE DH speed policy.

        Shared by the TES preposition writer (_drive_dh_preposition) and the
        RELEASE DH writer so a DH that has to finish its approach inside
        RELEASE closes the gap at exactly the rate it would have during the
        tackle, instead of the old hard-coded 4.0 m/s RELEASE crawl:

            gap > DH_PREPOSITION_SPRINT_DISTANCE_M -> sprint cap, so a DH
                picked far from the ruck can still arrive in time.
            gap < DH_PREPOSITION_TAPER_M -> linear ease to the taper floor,
                so arrival velocity is already below
                DH_VELOCITY_STABLE_THRESHOLD_MPS (the RELEASE/PTB gate) and
                there is no post-arrival decay tail to wait out.
        """
        if gap > DH_PREPOSITION_SPRINT_DISTANCE_M:
            return DH_PREPOSITION_SPRINT_MPS
        if gap > DH_PREPOSITION_TAPER_M:
            return DH_PREPOSITION_SPEED_MPS
        ratio = gap / max(DH_PREPOSITION_TAPER_M, 1e-6)
        return DH_PREPOSITION_TAPER_MPS + (
            DH_PREPOSITION_SPEED_MPS - DH_PREPOSITION_TAPER_MPS
        ) * ratio

    def _drive_dh_preposition(self, dt: float) -> None:
        """Integrate the DH toward (bc.x - 1.0, bc.y) while the tackle runs.

        WHY THIS EXISTS (2026-09-16)
            The DH is selected at tackle entry by _assign_dh_for_tackle(),
            which writes target_x / target_y but never integrates the body.
            Before this writer, ``dh.update(dt)`` was called ONLY from
            _update_release and _update_ptb, so the DH stood still for the
            entire TES chamber and then had to cover its whole approach
            INSIDE RELEASE at the fixed 4.0 m/s RELEASE writer speed.
            _check_dh_ready_for_ptb() additionally requires
            |speed| <= DH_VELOCITY_STABLE_THRESHOLD_MPS, so RELEASE could not
            exit until the DH had both ARRIVED and DECAYED -- making the phase
            length (travel / 4.0) + decay: ~3.1-3.5 s typically and 6.4 s when
            the nearest attacker started ~25 m from the ruck.

        CONTRACT
            Role.DUMMY_HALF already appears in
            MOVEMENT_RULES[...]["allowed_to_move"] for TACKLE_INIT /
            TACKLE_CONTACT / TACKLE_DRIVE / TACKLE_DOMINANCE, so the DH is
            unfrozen and holds movement authority for the whole tackle.  This
            writer only supplies the missing integrator.  It never touches a
            defender, and it runs AFTER the phase handler so it owns the DH's
            final target for the frame.

        SPEED (distance-aware taper)
            * gap > DH_PREPOSITION_SPRINT_DISTANCE_M -> sprint cap, so a DH
              picked far from the ruck still closes the gap in time.
            * gap < DH_PREPOSITION_TAPER_M -> ease linearly down to the taper
              floor, so arrival velocity is already near the RELEASE gate and
              the post-arrival decay tail is short.
        """
        dh = self.dh
        bc = self.ball_carrier
        if dh is None or bc is None or dh is bc:
            return
        if getattr(dh, "fullback_role_active", False):
            return

        target_x = bc.x - DH_BEHIND_DISTANCE_M
        target_y = bc.y
        dh.target_x = target_x
        dh.target_y = target_y
        stamp_target_write(self.world, dh, DH_PREPOSITION_TARGET_SOURCE)

        gap = math.hypot(target_x - dh.x, target_y - dh.y)
        dh.move_speed = self._dh_approach_speed(gap)
        dh.update(dt)

    def _drive_dh_open_play_support(self, dt: float) -> None:
        """Re-own the DH target in OPEN_PLAY while no tackle authority runs.

        WHILE the play is live and no TES / RELEASE / PTB authority is active,
        the DH trails the live ball carrier at
        ``(bc.x - DH_BEHIND_DISTANCE_M, bc.y)`` with target source
        ``dh_open_play_support``.  This overrides any stale tackle-era
        ``dh_preposition`` target that survived a BROKEN_TACKLE (the miss path
        exits straight to OPEN_PLAY without the RELEASE teardowns).

        Scope / contract (minimum):
          * OPEN_PLAY only -- this method is called ONLY from
            _update_open_play(), so RELEASE / PTB_PREP / PTB_EXEC / PTB and
            POST_RUCK_OPEN_PLAY keep their own DH writers;
          * TES inactive only;
          * uses the existing ``self.dh`` (never re-selects) and the existing
            ``_dh_approach_speed()`` policy (no new locomotion);
          * ``movement_state = CHASE``;
          * called as the FINAL write of _update_open_play, so it owns the
            DH's end-of-frame target after the attack manager has run;
          * it does NOT integrate the DH (no extra ``dh.update(dt)``) -- the
            OPEN_PLAY attack manager already integrates every attacker,
            including the DH.
        """
        if not dh_open_play_support_enabled():
            return
        if self._phase != "OPEN_PLAY":
            return
        if bool(getattr(self.world, "tes_active", False)):
            return

        dh = self.dh
        bc = self.ball_carrier
        if dh is None or bc is None or dh is bc:
            return

        target_x = bc.x - DH_BEHIND_DISTANCE_M
        target_y = bc.y
        dh.target_x = target_x
        dh.target_y = target_y
        dh.movement_state = MovementState.CHASE
        gap = math.hypot(target_x - dh.x, target_y - dh.y)
        dh.move_speed = self._dh_approach_speed(gap)
        dh.target_source = DH_OPEN_PLAY_SUPPORT_TARGET_SOURCE
        stamp_target_write(
            self.world, dh, DH_OPEN_PLAY_SUPPORT_TARGET_SOURCE
        )

    def _prepare_bc_dh_for_tackle(self):
        """
        STEP 4A: Pre-tackle movement reset.

        No speed/velocity clamping â€” physics values are preserved so
        TES compute_drive_distance() receives real momentum deltas.
        Called at the start of _start_tackle() before any TES entry logic.
        """
        bc = self.ball_carrier
        dh = self.dh

        if bc is not None:
            # Clear sprint/chase flags if they exist (safe hasattr guard)
            if hasattr(bc, "is_sprinting"):
                bc.is_sprinting = False
            if hasattr(bc, "is_chasing"):
                bc.is_chasing = False

        # DH is assigned at tackle entry, then the release/PTB controller takes
        # over the final 1m-behind-BC approach as the tackle resolves.
        self._assign_dh_for_tackle()

    # [STEP-02A-01] _maybe_start_tackle disabled.
    # Lvl-1 chase now calls _start_tackle() directly after pre-tackle logic.
    # def _maybe_start_tackle(self, bc: Player) -> None:
    #     """Check proximity and angle to start a tackle."""
    #     if self._phase != "OPEN_PLAY":
    #         return
    #
    #     closest = None
    #     cd = 9999.0
    #     for d in self.world.defending_team:
    #         if d.defender_role == DefenderRole.DUMMY_HALF:
    #             continue
    #         dist = math.hypot(bc.x - d.x, bc.y - d.y)
    #         if dist < cd:
    #             cd = dist
    #             closest = d
    #
    #     if closest is None:
    #         return
    #     if cd > TACKLE_RADIUS_M:
    #         return
    #     if _approach_angle(bc, closest) > TACKLE_ANGLE_THRESHOLD_DEG:
    #         return
    #
    #     # Phase-4D-2: enter tackle approach for one frame
    #     self._enter_tackle_approach(bc, closest)
    #
    #     # Phase-4D-1: momentum lock
    #     self._lock_bc_momentum_for_tackle(bc, closest)
    #
    #     # Initiate tackle
    #     self.bc_in_approach = False
    #     self._start_tackle(bc, closest)

    def _arm_tackler_role(self, d: "Player", role_enum, role_name: str,
                          ctx: TESContext) -> None:
        """Arm a defender as a TACKLER (contract A/B).

        Called at tackler selection in _start_tackle() for the primary
        (TACKLER_1) and the two joiners (TACKLER_2/3).  This is the ONLY
        place movement_mode = TACKLER may be set.  It makes the role
        switch + movement-contract switch atomic and immediate, BEFORE
        any movement update:

          - defender_role   = TACKLER_*   (movement contract switch)
          - tes_tackler_role              (selection metadata)
          - movement_mode   = TACKLER     (tackle-intent movement contract)
          - collision_priority = HIGH     (collision initiator)
          - lunge/target fields reset so no stale NORMAL-mode values leak
          - retreating = False + targets unpinned so TACKLER movement â€”
            which overrides DEF_LINE_READY / DEF_RETREAT_10 / MARKER_A/B /
            spacing / line-shape â€” actually applies.

        INVARIANT (contract H): once TACKLER_* is set, no mid-TES role or
        movement-mode changes are allowed.  RELEASE (_clear_tes_artefacts)
        is the sole exit, resetting movement_mode to RETREAT.
        """
        # -- FULLBACK EMERGENCY -> TACKLER (B2b, 2026-09-16) --
        # The designated fullback keeps fullback_role_active through
        # CHASE/SHOOT pursuit: movement state is never a role-demotion
        # mechanism (the 2026-09-15 CHASE-STATE DEMOTION stays deleted), and
        # tackle eligibility is owned by can_acquire_tackle plus the genuine
        # line-break gate.  When the actor is ACTUALLY armed as a TES tackler
        # the sweeper BEHAVIOUR ends (TES owns the body: TACKLER_* role,
        # TACKLER movement mode, HIGH collision priority) but the FULLBACK
        # TAG IS NOT REVOKED.  The tag is the LINE-MEMBERSHIP marker and the
        # two concepts are separate by law: the actor must keep failing every
        # line-shape test (population / depth role / side / spacing / width /
        # span / formation slot) while it behaves like any other committed
        # defender on the break.  This method is the SINGLE tackler-arming
        # point (primary via _start_tackle, joiners via _maybe_add_joiners,
        # assists via the FX-1 band), so the engagement marker can never be
        # missed.  ``_fb_emergency_engaged`` is cleared at the end of the
        # tackle cycle (``_clear_tes_artefacts`` / the post-tackle role
        # reset), which is also where ``defender_role`` returns to
        # DEF_LINE_READY -- the tag was never removed, so the actor resumes
        # its sweeper anchor.
        #
        # ROLLBACK / A-B LEVER: RLM_FULLBACK_TACKLE_CANDIDACY=0 restores the
        # historical "tag revoked at arming" behaviour verbatim.
        from game.defence.defence_lvl_1.fullback_role import (
            FULLBACK_TACKLE_CANDIDACY as _fb_candidacy,
            clear_fullback_emergency_engage_authority as _fb_clear_engage_authority,
            set_fullback_emergency_engaged as _fb_set_engaged,
        )
        if getattr(d, "fullback_role_active", False):
            _fb_clear_engage_authority(d)
            if _fb_candidacy:
                _fb_set_engaged(d)  # tag preserved: emergency engagement
            else:
                d.fullback_role_active = False
                d.can_acquire_tackle = False
                if getattr(d, "role", None) == "FULLBACK":
                    d.role = None
            if getattr(self, "world", None) is not None:
                publish_fullback_state(self.world)
        d.defender_role = role_enum
        d.tes_tackler_role = role_name
        # TACKLE-STATE GATE (2026-08-23): an armed tackler holds
        # TACKLE-STATE for the entire TES/PTB while inside the
        # tackle-radius; only the gate's radius exit releases it back
        # into spacing/depth roles.
        stamp_tackle_state(d)
        d.movement_mode = MovementMode.TACKLER
        d.collision_priority = CollisionPriority.HIGH
        # Reset per-tackle contract targets/lunge from any stale state.
        d.speed_target = 0.0
        d.vx_target = 0.0
        d.vy_target = 0.0
        d.heading_target = 0.0
        d.lunge_active = False
        d.lunge_speed = 0.0
        d.lunge_duration = 0.0
        # TACKLER overrides retreat/line/marker contracts.
        d.retreating = False
        ctx.tackler_roles[d.player_id] = role_name
        # -- STAGE 2: THE FORMED CONTEST'S CONTACT SET EXTENDS --
        # A body armed as a TES tackler has JOINED the contest -- arming is
        # exactly the point at which it reaches the carrier's attach band -- so
        # both declared growth events are recorded on the TackleState TES owns.
        # Both calls are no-ops when no contest is active and they write only to
        # the state, so neither can perturb a body or a frame.
        from game.tes.tackle_state import append_attachment, mark_attached
        append_attachment(self.world, d, role_name)
        mark_attached(self.world, d)

    def _update_tackler_movement(self, dt: float) -> None:
        """TACKLER movement contract (spec C), drives every TACKLE_INIT frame.

        For every selected tackler (primary + joiners) not yet attached to
        the BC, applies the dedicated tackle-intent movement model:

          1. ACCELERATION INTO CONTACT
             dist <= 3.0 m  -> speed_target floor 4.0 m/s
             dist <= 1.5 m  -> speed_target floor 5.0 m/s
          2. MATCH BC VELOCITY           vx_target = bc.vx, vy_target = bc.vy
          3. ANGLE CORRECTION            heading_target = angle to BC
          4. LUNGE (open play)           bc.x > ruck_x + 5.0 -> lunge active
          5. COLLISION PRIORITY          already HIGH via _arm_tackler_role

        The resulting velocity is the BC-matched velocity re-aimed toward
        BC at speed_target (never slower than BC, capped at
        TACKLER_MAX_CLOSE_SPEED_MPS).  Position is integrated directly,
        matching the existing TACKLE_INIT pattern.  Attached tacklers are
        skipped (their position is owned by _follow_attached_tacklers â€”
        contract F).  Frozen tacklers are skipped (TES freeze only; the
        freeze authority is apply_phase_invariants).

        OUT OF SCOPE: TACKLE_CONTACT uses _lunge_tacklers_to_bc() (snap
        lunge onto attach radius); this method only runs during TACKLE_INIT.
        """
        bc = self.ball_carrier
        if bc is None:
            return
        ruck_x = self._get_ruck_x()

        candidates = []
        if self.primary is not None:
            candidates.append(self.primary)
        for j in self.joiners:
            candidates.append(j)

        for d in candidates:
            if d is None:
                continue
            if getattr(d, "movement_mode", None) is not MovementMode.TACKLER:
                continue
            if getattr(d, "tes_attached", False):
                continue          # contract F: TES owns attached position
            if d.frozen:
                continue          # TES freeze (apply_phase_invariants) wins
            dist = math.hypot(bc.x - d.x, bc.y - d.y)

            # --- 1. Distance-based speed target (spec C1) ---
            speed_target = max(getattr(bc, "max_speed_mps", 9.2), 0.0)
            if dist <= TACKLER_CONTACT_RADIUS_M:
                speed_target = max(speed_target, TACKLER_CONTACT_FLOOR_MPS)
            elif dist <= TACKLER_ACCEL_RADIUS_M:
                speed_target = max(speed_target, TACKLER_ACCEL_FLOOR_MPS)
            d.speed_target = speed_target

            # --- 2. Match BC velocity (spec C2) ---
            d.vx_target = bc.vx
            d.vy_target = bc.vy

            # --- 3. Angle correction: face BC directly (spec C3) ---
            d.heading_target = math.atan2(bc.y - d.y, bc.x - d.x)

            # --- 4. Open-play lunge (spec C4) ---
            if bc.x > ruck_x + TACKLER_OPEN_PLAY_LUNGE_X_M:
                d.lunge_active = True
                d.lunge_speed = TACKLER_LUNGE_SPEED_MPS
                d.lunge_duration = TACKLER_LUNGE_DURATION_S
            else:
                d.lunge_active = False
                d.lunge_speed = 0.0
                d.lunge_duration = 0.0

            # --- Integrate: BC-matched velocity re-aimed at BC ---
            # Base direction is the BC-matched velocity; if the defender is
            # nearly stationary while BC runs, that vector alone would still
            # close nothing, so blend toward the BC heading at close range
            # (heading locks on inside TACKLER_HEADING_RADIUS_M) and always
            # run at speed_target so the tackler is NOT slower than BC at
            # collision moment (contract C2 invariant).
            bc_spd = math.hypot(bc.vx, bc.vy)
            if dist > 0.01:
                to_bc_dx = (bc.x - d.x) / dist
                to_bc_dy = (bc.y - d.y) / dist
                if dist <= TACKLER_HEADING_RADIUS_M or bc_spd < 0.3:
                    dir_x, dir_y = to_bc_dx, to_bc_dy
                else:
                    # Preserve BC-matched direction unless BC is stationary;
                    # blend 50/50 with the direct heading so the tackler
                    # intercepts rather than chasing the BC's tail.
                    bc_dx = bc.vx / bc_spd if bc_spd > 0.01 else to_bc_dx
                    bc_dy = bc.vy / bc_spd if bc_spd > 0.01 else to_bc_dy
                    dir_x = (bc_dx + to_bc_dx) * 0.5
                    dir_y = (bc_dy + to_bc_dy) * 0.5
                    nl = math.hypot(dir_x, dir_y)
                    if nl > 0.001:
                        dir_x /= nl
                        dir_y /= nl
                    else:
                        dir_x, dir_y = to_bc_dx, to_bc_dy

                # Tacklers must NOT be slower than BC at collision
                # (contract C2 invariant).  speed_target already floors at
                # BC speed; cap only by the physical max.
                close_spd = min(speed_target, TACKLER_MAX_CLOSE_SPEED_MPS)
                d.vx = dir_x * close_spd
                d.vy = dir_y * close_spd
                d.x += d.vx * dt
                d.y += d.vy * dt
                # Angle correction: face BC directly (spec C3).
                d.set_facing(math.atan2(bc.y - d.y, bc.x - d.x))
            else:
                d.vx = 0.0
                d.vy = 0.0

        if DEBUG_OPE_TRACE:
            for d in candidates:
                if d is None or d.defender_role not in (DefenderRole.TACKLER_1,
                                                        DefenderRole.TACKLER_2,
                                                        DefenderRole.TACKLER_3):
                    continue
                print(f"[TACKLER_MOVE] id={d.player_id} role={d.defender_role.name} "
                      f"mode={d.movement_mode} spd_tgt={d.speed_target:.2f} "
                      f"vx_tgt={d.vx_target:.2f} vy_tgt={d.vy_target:.2f} "
                      f"lunge={d.lunge_active} dist={math.hypot((self.ball_carrier.x if self.ball_carrier else d.x)-d.x, (self.ball_carrier.y if self.ball_carrier else d.y)-d.y):.2f}")

    def _start_tackle(self, bc: Player, d: Player) -> None:
        """Create TES context and enter TACKLE_INIT.

        Per spec 3.2 + 3.3 (2026-08-07, corrected spec):
          1. primary_defender â†’ TACKLER_1
          2. Scan for immediate-near defenders â†’ TACKLER_2, TACKLER_3
          3. All other defenders KEEP their pre-tackle defender_role â€”
             they are NOT re-rolled to DEF_RETREAT_10 here.  The open-play
             defensive AI keeps driving them through the TES drive
             (TACKLE_DRIVE / TACKLE_DOMINANCE), and DEF_RETREAT_10 is
             assigned ONCE at RELEASE entry by _prepare_release() where the
             existing RELEASE/PTB retreat-10 pipeline takes over.

        Delegates TES entry physics to tes_start_tackle().

        STAGE 2B (2026-09-20) -- THE TES ENTRY RECEIVES THE FIRST-CLASS
        CONTEST.  ``d`` carries the Contest object Contact Authority
        assembled at the declaration, in place of a standalone winning
        defender identifier.  TES consumes ONLY the winning defender it
        carries; every other contest fact (the evaluated set, the in-radius
        set, the admission channel, the frame, the sequence) is advisory and
        unused.  The unwrap below is a pure read, so everything after it is
        the pre-Stage-2B code, byte for byte, and the object handed to
        ``tes_start_tackle`` is exactly the defender the old path passed.
        """
        # STAGE 2B: consume the winning defender the payload carries.  A
        # bare defender (the engine's own unreachable historical copy of the
        # defence dispatch, or a detached caller) passes straight through.
        from game.defence.defence_lvl_1.contest import winner_of
        d = winner_of(d)
        # ── TACKLE IDENTITY (2026-09-11) ──
        # Bump the chamber sequence HERE, at the SINGLE engine entry point
        # for every tackle.  The TES drive structure-hold authority keys
        # its lane latches to this id, so each tackle re-seeds its lanes
        # LANE-TRUE from the position the line actually holds and no latch
        # can survive into the next tackle.
        #
        # WHY THE ENGINE OWNS IT: keying the boundary on a defence-manager
        # signal failed twice over — the manager is not invoked at all on
        # some post-tackle paths (so a "clear on exit" flag was never
        # cleared), and ``is_tackle_phase`` does not return False between
        # tackles in this engine (so a rising-edge test never re-fired).
        # Measured live, either way a single chamber spanned ~1000 drive
        # frames across several tackles, and every body read 5–23 m "off
        # lane" once a later tackle moved the line.
        try:
            self.world._tes_tackle_seq = (
                int(getattr(self.world, "_tes_tackle_seq", 0) or 0) + 1
            )
        except (TypeError, ValueError):
            self.world._tes_tackle_seq = 1
        # Drop the world-side latch stamp so the first drive frame of the
        # new tackle re-captures the drive-entry ruck X and the lateral
        # band reference instead of reusing the previous tackle's.
        try:
            delattr(self.world, "_tes_latch_seq")
        except AttributeError:
            pass
        # STEP 4A: stabilise BC/DH before TES Ã¢â‚¬â€ clamp speed, clear sprint/chase flags
        self._prepare_bc_dh_for_tackle()


        self.primary = d
        self.joiners = []

        # Clear stale per-frame tackle_role so PRIMARY/SECONDARY from the
        # previous cycle cannot leak into TES or marker assignment.
        for defender in self.world.defending_team:
            defender.tackle_role = None
            # â”€â”€ TIMING-LOCK CLEARANCE ON TES ENTRY â”€â”€
            # Post-ruck / OPEN_PLAY timing locks (reaction_timer, retreat_delay,
            # lateral_hesitation, step_stagger -> timing_locked) must NEVER
            # survive the OPEN_PLAY -> TACKLE_INIT boundary.  If a defender is
            # still timing_locked when TES starts, the reactivity audit reads
            # the stale lock as TIMING_LOCK_POST_RUCK and the freeze audit's
            # 10-frame rolling window mixes OPEN_PLAY target history with TES
            # stasis targets -> TARGET_JIGGLE for every defender.
            # DefensiveTiming.update() only runs in OPEN_PLAY, so these stale
            # flags would otherwise persist for the entire TES cycle.
            # TES owns all movement now; timing locks are meaningless here.
            defender.reaction_timer = 0.0
            defender.retreat_delay = 0.0
            defender.lateral_hesitation = 0.0
            defender.step_stagger = 0.0
            defender.step_stagger_active = False
            defender.timing_locked = False
            defender._prev_depth_class = None
            defender.dh_defender_delay = 0.0
            defender._retreat_hesitation_fired = False
            # LATERAL JIGGLE KILL (2026-08-02): clear the finalise blend
            # baselines so a stale pre-TES target snapshot never survives
            # the OPEN_PLAY -> TACKLE_INIT boundary (would otherwise
            # produce a TARGET_JIGGLE snap on the first OPEN_PLAY frame
            # after TES).
            defender._prev_final_x = None
            defender._prev_final_y = None
            defender._prev_lane_index = None

        # Delegate all TES entry setup to the central function
        ctx = tes_start_tackle(bc, d, self.world)

        self.tes_ctx = ctx
        self.tes_outcome = TESOutcome()

        # MOMENTUM-GATE DIAGNOSTIC WIRING (2026-08-06): stash the TES
        # context on the primary tackler so investigate_tes_forward_momentum()
        # can read bc_momentum_factor / bc_flatfooted each TES frame
        # without a module-level lookup.
        d._tes_ctx = ctx

        # Ã¢â€â‚¬Ã¢â€â‚¬ Spec 3.2 (contract A/B): Assign tackler role + switch the
        # movement contract to TACKLER mode ATOMICALLY, BEFORE any movement
        # update.  _arm_tackler_role() is the ONLY place movement_mode may
        # become TACKLER (contract B/H).  d.is_tackler is already True from
        # tes_start_tackle(); freeze is set by apply_phase_invariants()
        # (TACKLER_1 allowed to move in TES phases).
        self._arm_tackler_role(d, DefenderRole.TACKLER_1, "TACKLER_1", ctx)

        # â”€â”€ CORRECTED TACKLE MODEL (2026-08-07): a tackle begins with
        # EXACTLY ONE defender (TACKLER_1).  TACKLER_2 / TACKLER_3 are NOT
        # pre-selected here â€” up to two additional defenders join the
        # existing tackle dynamically via _maybe_add_joiners() during
        # TACKLE_CONTACT / TACKLE_DRIVE / TACKLE_DOMINANCE (assist tackle).
        # Joining never restarts the TES cycle: DRIVE/DOMINANCE timers and
        # targets are untouched, and the joiner is attached into the pile
        # immediately.  All other defenders keep their pre-tackle roles and
        # behave EXACTLY as in OPEN PLAY (handled below, Spec 3.3).

        # â”€â”€ Spec 3.3 (2026-08-07, corrected spec): ALL other defenders KEEP
        # their pre-tackle roles.  DEF_RETREAT_10 is NOT forced here â€” the
        # old TES-specific static-depth snapshot (tes_role_depth_m /
        # tes_ruck_x_snapshot) + re-role to DEF_RETREAT_10 was removed
        # because it destroyed the ability for defenders to behave EXACTLY
        # like they do in OPEN PLAY during the TES drive.  Non-ruck
        # defenders retain their open-play roles (spacing, lanes, role
        # depths), and _update_defence_during_tes_drive() runs the open-play
        # defensive AI for them during TACKLE_DRIVE / TACKLE_DOMINANCE.
        # DEF_RETREAT_10 is assigned ONCE at RELEASE entry by
        # _prepare_release(), where the existing RELEASE/PTB retreat-10
        # pipeline takes over.
        #
        # (Formerly: snapshot tes_role_depth_m/tes_ruck_x_snapshot at TES
        # entry, then force every non-tackler â€” including old markers â€” to
        # DEF_RETREAT_10.)
        TACKLER_ROLES = frozenset({
            DefenderRole.TACKLER_1,
            DefenderRole.TACKLER_2,
            DefenderRole.TACKLER_3,
        })
        # MARKER_ROLES REMOVED (July 2026):
        # Old markers must NOT be preserved through the next tackle cycle.
        # They are now cleared and reassigned fresh in _prepare_release()
        # via _assign_markers_for_new_tackle().
        for defender in self.world.defending_team:
            if defender.defender_role in TACKLER_ROLES:
                continue
            if defender.defender_role == DefenderRole.DUMMY_HALF:
                continue
            # NO-OP (corrected spec): preserve the pre-tackle role so the
            # open-play defensive AI keeps driving this defender during the
            # TES drive.  intentionally does nothing.
            pass

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â Suppress non-BC attackers during TES Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        # Support runners, first receivers, and any other attacker who is
        # not the ball carrier must resolve to Role.NONE Ã¢â€ â€™ must_be_frozen.
        # This prevents freeze violations where `_update_attack()` or other
        # systems try to move them during TES phases.
        for p in self.world.attacking_team:
            # DH PRE-POSITION (2026-09-16): the DH is deliberately EXEMPT from
            # this suppression.  It is not a support runner -- it is the actor
            # that must be standing behind the ruck when the ball is played,
            # and the engine drives it toward the ruck for the whole tackle
            # (see _drive_dh_preposition).  Clearing its attack_role was
            # incidental rather than intentional, and it stripped the
            # "dummy_half" tag that the belt-and-suspenders unfreeze paths and
            # has_movement_authority() look for.
            if p is not bc and p is not self.dh:
                p.attack_role = "none"

        # Ensure ball ownership is BC (belt + suspenders)
        self.world.ball_owner = "BC"

        # â”€â”€ PHASE 1/3: Seed the ATTACH system for TACKLE_CONTACT â”€â”€
        # attach_enabled is ONLY true during TACKLE_CONTACT (set here and
        # cleared at RELEASE via _detach_all_tacklers).  _update_attachments()
        # additionally guards on self._phase == "TACKLE_CONTACT", so attach
        # can never occur in TACKLE_INIT / DRIVE / DOMINANCE (spec 1.5).
        ctx.attach_enabled = True
        self._attach_armed = True
        self._attached_player_slots.clear()
        # ATTACH-FLAG / REGISTRY SYNC (2026-09-11): clearing the slot
        # registry alone left any per-player ``tes_attached`` flag from a
        # tackle that ended WITHOUT ``_detach_all_tacklers`` (the broken
        # tackle / turnover path) still set on the body.  ``update_tes_drive``
        # excludes only the slot-registry keys, so such a stale-flagged body
        # was adopted by the defensive-line authority and moved as a line
        # body while still reported as attached.  Reset the flags WITH the
        # registry so the two can never disagree.
        for _ad in self.world.defending_team:
            if getattr(_ad, "tes_attached", False):
                _ad.tes_attached = False
                if hasattr(_ad, "attach_slot"):
                    _ad.attach_slot = None
        # Selected-tackler list starts with the primary ONLY (corrected
        # tackle model).  Dynamic joiners append via _maybe_add_joiners().
        ctx.tacklers = [ctx.engagement.tacklers[ctx.engagement.primary_id]]

        # ── RUCK-PIVOT TIMING (Stage 3B) ──
        # Band formation arms ONLY here: TES entering TACKLE_INIT is the
        # single legitimate ruck-pivot trigger.  CATCH / OPEN_PLAY never
        # form A/B/C bands around a fake ruck point.
        if not getattr(self.world, "ruck_established", False):
            self.world.ruck_established = True
            print(
                f"[RUCK_PIVOT] TACKLE_INIT -> band formation armed "
                f"(ruck_x={getattr(self.world, 'ruck_x_m', 0.0):.1f})"
            )
        # ---- DEF_LINE_READY PRE-TACKLE_INIT PROMOTION ----
        # Promote EVERY eligible line body (None / DEF_RETREAT_10 /
        # DEF_CHASE) to DEF_LINE_READY BEFORE the phase flips to
        # TACKLE_INIT, so the pretackle-attempt authority's eligibility
        # predicate (defender_role == DEF_LINE_READY) holds for the whole
        # line the moment the drive begins.  Markers / DH / tacklers /
        # fullback / frozen bodies are never touched (idempotent).
        #
        # RETREAT-OBLIGATION STAMP (2026-09-17): a body promoted out of
        # DEF_RETREAT_10 while still short of the anchor line keeps the
        # role (geometry unchanged) but carries an explicit authority
        # obligation, so this promotion can never hand it tackle authority
        # it has not earned.
        for _pb in self.world.defending_team:
            assign_def_line_ready(_pb)

        # ── FULLBACK-INVARIANT AUDIT AT TACKLE ENTRY ──
        # The actor must survive the OPEN_PLAY -> TACKLE_INIT boundary as a
        # deep, unfrozen, geometry-owned anchor (depth_role None, frozen
        # False, target_source "fullback_geometry").  This is the frame
        # where timing locks are wiped / roles are re-armed, so it is the
        # most likely to leak a line role or freeze onto the last defender.
        # Observational only -- never modifies any state.
        audit_fullback_invariants(self.world)

        self._set_phase("TACKLE_INIT", GameSubState.TACKLE_INIT, "TACKLE_INIT")

    # ── FX-3 (TA8-2026-08-25): NARROWED TES FREEZE-MASK HELPER ──

    def _stamp_tes_freeze_mask(self, include_idle_line: bool) -> None:
        """Audit-only freeze-mask, narrowed to genuinely-idle actors.

        Masks for the freeze/movement audits:

          - every player whose REAL ``frozen`` flag is set, and
          - live TES participants (primary + joiners + attached ids), plus
          - during TACKLE_INIT / TACKLE_CONTACT (``include_idle_line=True``)
            ALL defenders — CORRECT there: engine law velocity-stasis pins
            every non-participant defender in those phases, so their
            idleness is by design and STUCK_NOT_FROZEN would be noise.

        During TACKLE_DRIVE / DOMINANCE / RELEASE
        (``include_idle_line=False``) non-participant line defenders are
        ACTIVELY driven (TES-drive soft-follow, P7 retreat writers) and are
        audited normally again — real band-formation jitter resurfaces
        instead of being blanket-suppressed.
        """
        world_ctx = getattr(self.world, "ctx", None)
        if not isinstance(world_ctx, dict):
            return
        masked = set()
        if include_idle_line:
            masked.update(
                p.player_id for p in self.world.players
                if getattr(p, "player_id", None) is not None
            )
        else:
            for p in self.world.players:
                pid = getattr(p, "player_id", None)
                if pid is None or not getattr(p, "frozen", False):
                    continue
                masked.add(pid)
        if self.primary is not None:
            masked.add(self.primary.player_id)
        for j in self.joiners:
            masked.add(j.player_id)
        tes_ctx = self.tes_ctx
        if tes_ctx is not None:
            for pid in (getattr(tes_ctx, "attached_ids", None) or ()):
                masked.add(pid)
        world_ctx["frozen_ids"] = list(masked)

    # -- TACKLE_INIT ----------------------------------------------------

    def _update_tackle_init(self, dt: float) -> None:
        """First contact frame -- defender closes to BC, then enter contact."""
        self._phase_timer += dt

        # -- TES FREEZE-MASK (audit quieting) --
        # Same contract as TACKLE_DRIVE: during TES phases every player is
        # treated as FROZEN for AUDIT PURPOSES ONLY, even when their real
        # frozen flag is False (DEF_RETREAT_10 / TACKLERs are unfrozen by
        # MOVEMENT_RULES but idle by design).  This suppresses false
        # STUCK_NOT_FROZEN / VX_ZERO_JIGGLE / TARGET_JIGGLE violations.
        # It does NOT modify any player/world frozen flag or physics.
        # FX-3: stasis phases pin every non-participant by engine law —
        # their idleness is by design, so the wide mask stays correct here.
        self._stamp_tes_freeze_mask(True)

        # SDM handles DEF_RETREAT_10 retreat targets for TES phases
        # (called from update() after handler dispatch)

        bc = self.ball_carrier
        d = self.primary
        if bc is None or d is None:
            self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "TACKLE_INIT_ABORT")
            return

        # CONTRACT C: every TACKLER (primary + joiners) runs the dedicated
        # tackle-intent movement model this frame â€” accelerate into contact,
        # match BC velocity, face BC, lunge in open play.  Replaces the old
        # primary-only 3.0 m/s pull (which let a fast BC ghost the pile).
        self._update_tackler_movement(dt)

        # CONTRACT D/E: distance-only TES activation.  Once tackler movement
        # is live, velocity/angle checks are OPTIONAL â€” any TACKLER within
        # TACKLE_RADIUS_M triggers TACKLE_CONTACT immediately (no fixed
        # 0.10 s engagement window).
        any_within = False
        if self.tes_ctx is not None:
            for cand in ([self.primary] if self.primary is not None else []) + list(self.joiners):
                if cand is None:
                    continue
                if math.hypot(bc.x - cand.x, bc.y - cand.y) <= TACKLE_RADIUS_M:
                    any_within = True
                    break
        if any_within or self._phase_timer >= 0.10:
            self._set_phase("TACKLE_CONTACT", GameSubState.TACKLE_CONTACT, "TACKLE_INIT->TACKLE_CONTACT")

    # ------------------------------------------------------------------
    # PHASE 1 â€” TACKLER ATTACH (TACKLE_CONTACT only)
    # ------------------------------------------------------------------

    def _lunge_tacklers_to_bc(self, dt: float) -> None:
        """PHASE 1 â€” ATTACH LUNGE: close selected tacklers onto the BC.

        A tackle triggers at TACKLE_RADIUS_M (1.5 m), but attach requires
        distance < ATTACH_RADIUS_M (0.7 m).  Each TACKLE_CONTACT frame this
        step pulls every selected-but-not-yet-attached TACKLER toward the
        BC at ATTACH_LUNGE_SPEED_MPS.  As soon as a tackler crosses
        ATTACH_RADIUS, _update_attachments() (run right after this in the
        contact handler) snaps them in and dampens their velocity.

        Selected TES tacklers (primary/joiners with TACKLER_* roles) plus,
        since FX-1 (TA8-2026-08-25), the nearest lane-committed assist
        candidate are lunged.  Other defenders remain untouched.
        """
        bc = self.ball_carrier
        if bc is None or self.tes_ctx is None:
            return
        if self._phase != "TACKLE_CONTACT":
            return

        candidates = []
        if self.primary is not None:
            candidates.append(self.primary)
        for j in self.joiners:
            candidates.append(j)

        # ── FX-1 (TA8-2026-08-25): SECONDARY ASSIST LUNGE ──
        # Close the nearest lane-committed assist candidate onto the BC so
        # it crosses ATTACH_RADIUS_M inside the 0.15 s CONTACT window.
        # Without this only already-armed tacklers were lunged, so the
        # widened joiner band could never be crossed in time and the joiner
        # attached late (or never) — drive strength never aggregated.
        # Eligibility mirrors the FX-1 joiner band and excludes every role
        # TES owns outright. depth_class survives the tackle-start wipe.
        _assist = None
        _assist_best = None
        for p in self.world.defending_team:
            if p is self.primary or p in self.joiners or p is bc:
                continue
            if p.defender_role in (
                DefenderRole.TACKLER_1,
                DefenderRole.TACKLER_2,
                DefenderRole.TACKLER_3,
                DefenderRole.DEF_RETREAT_10,
                DefenderRole.DUMMY_HALF,
            ):
                continue
            if getattr(p, "frozen", False) or getattr(p, "in_contact", False):
                continue
            if getattr(p, "fullback_role_active", False):
                continue
            if getattr(p, "depth_class", None) not in ("PRIMARY", "SECONDARY"):
                continue
            _d = math.hypot(bc.x - p.x, bc.y - p.y)
            if _d > LINE_TACKLE_RADIUS_M:
                continue
            if _assist_best is None or _d < _assist_best:
                _assist_best = _d
                _assist = p
        # FX-1 (TA8-2026-08-25): ARM the assist candidate as a real TES
        # tackler so the lunge + attach below own it.  Previously the assist
        # was only appended to `candidates` and then rejected by the
        # TACKLER_* role gate below (dead code): it was never lunged, never
        # armed, never attached, and crossed CONTACT->DRIVE as an orphaned
        # defender with no TES role and no movement writer (STUCK_NOT_FROZEN).
        # Arm it atomically via _arm_tackler_role() (TACKLER_2, or TACKLER_3
        # when one joiner already exists), register it in the TES registry,
        # respect the 3-tackler cap, and only then lunge it.
        if _assist is not None and len(self.joiners) < 2:
            a_ctx = self.tes_ctx
            a_sim_time = getattr(self.world, "sim_time_s", 0.0)
            if len(self.joiners) == 0:
                a_role_name = "TACKLER_2"
                a_role_enum = DefenderRole.TACKLER_2
            else:
                a_role_name = "TACKLER_3"
                a_role_enum = DefenderRole.TACKLER_3
            self._arm_tackler_role(_assist, a_role_enum, a_role_name, a_ctx)
            _assist.is_tackler = True
            _assist.in_contact = True
            _assist.tes_locked = True
            self.joiners.append(_assist)
            if not hasattr(self.world, "tackler_ids"):
                self.world.tackler_ids = set()
            self.world.tackler_ids.add(_assist.player_id)
            a_j_ctx = _make_ctx(_assist)
            a_ctx.engagement.add(a_j_ctx, a_sim_time)
            a_ctx.tacklers.append(a_j_ctx)
            candidates.append(_assist)

        for d in candidates:
            if d is None:
                continue
            if getattr(d, "tes_attached", False):
                continue
            if d.player_id in self.tes_ctx.attached_ids:
                continue
            if d.defender_role not in (
                DefenderRole.TACKLER_1,
                DefenderRole.TACKLER_2,
                DefenderRole.TACKLER_3,
            ):
                continue
            dist = math.hypot(bc.x - d.x, bc.y - d.y)
            if dist >= ATTACH_RADIUS_M and dist > 0.01:
                step = min(ATTACH_LUNGE_SPEED_MPS * dt, dist - ATTACH_RADIUS_M + 0.01)
                d.x += (bc.x - d.x) / dist * step
                d.y += (bc.y - d.y) / dist * step
                # The tackler is closing â€” give them the closing velocity so
                # TES dominance sees the momentum of the hit (and revert
                # once the attach snap dampens it).
                d.vx = (bc.x - d.x) / dist * ATTACH_LUNGE_SPEED_MPS
                d.vy = (bc.y - d.y) / dist * ATTACH_LUNGE_SPEED_MPS

    def _update_attachments(self) -> None:
        """PHASE 1/3: make eligible tacklers ATTACH to the BC.

        Runs EVERY frame of TACKLE_CONTACT.  A defender attaches when ALL
        of these hold (spec Phase 1.2):
          - role is a valid tackler (TACKLER_1/2/3 â€” primary or secondary),
          - distance to BC < ATTACH_RADIUS_M (0.7 m),
          - phase is TACKLE_CONTACT (guaranteed by the caller),
          - not already attached,
          - fewer than 3 tacklers already attached (spec: max 3 total).

        On attach (spec Phase 1.3):
          - defender.tes_attached = True
          - attach slot index (0/1/2) by order of arrival
          - fixed offset relative to BC FACING (left/right/back)
          - snap defender to BC + offset, align facing to BC, damp velocity
          - add defender mass/momentum to TES context totals

        Once attached (spec Phase 1.4):
          - defender stops moving independently (TES owns its position)
          - follows BC every frame (handled in drive/contact follow step)
          - keeps BC facing
          - never detaches until RELEASE (detach happens in _update_release)
        """
        ctx = self.tes_ctx
        bc = self.ball_carrier
        if ctx is None or bc is None:
            return
        if self._phase != "TACKLE_CONTACT":
            return
        if not ctx.attach_enabled:
            return

        candidates = []
        if self.primary is not None:
            candidates.append(self.primary)
        for j in self.joiners:
            candidates.append(j)

        for d in candidates:
            if d is None:
                continue
            if getattr(d, "tes_attached", False):
                continue
            if d.player_id in ctx.attached_ids:
                continue
            # Valid tackler role gate (primary/secondary for now)
            if d.defender_role not in (
                DefenderRole.TACKLER_1,
                DefenderRole.TACKLER_2,
                DefenderRole.TACKLER_3,
            ):
                continue
            dist = math.hypot(bc.x - d.x, bc.y - d.y)
            _r_assist_att, _r_tackle_att = _resolve_attach_radii(self)
            if dist >= _r_tackle_att:
                continue
            # Cap at 3 attached tacklers (spec Phase 3 global constraint)
            if len(ctx.attached_ids) >= 3:
                break
            self._attach_defender(d, bc, ctx)

        self._refresh_attachment_context(ctx)

    def _attach_defender(self, d: "Player", bc: "Player", ctx: TESContext) -> None:
        """Attach a single eligible defender to the BC (spec Phase 1.3)."""
        next_slot = len(ctx.attached_ids)
        if next_slot >= 3:
            return

        # Fixed offset relative to BC's FACING (left/right/back).
        # Rotated per-frame by the follow step so "left/right/back" tracks
        # the BC's actual orientation during CONTACT/DRIVE.
        if next_slot == 0:
            mag = ATTACH_SLOT_LEFT_OFFSET_M
        elif next_slot == 1:
            mag = ATTACH_SLOT_RIGHT_OFFSET_M
        else:
            mag = ATTACH_SLOT_BACK_OFFSET_M
        ctx.attach_offsets[d.player_id] = (_slot_dir(next_slot), mag)

        # Immediately move defender to BC + offset (spec 1.3)
        dx, dy = _rotate_offset(_slot_dir(next_slot), mag, bc.facing)
        _tx, _ty = bc.x + dx, bc.y + dy
        # TES-ATTACH GLIDE (2026-09-07): the direct `d.x/_tx` placed a joiner
        # onto the BC slot in ONE frame (the "at initial contact" teleport).
        # Glide toward the slot at at most _TES_ATTACH_GLIDE_SPEED_MPS so the
        # joiner converges instead of punching across the field.
        _att_dt = float(getattr(self, "dt", getattr(self.world, "dt", 1.0 / 60.0)))
        _att_step = _TES_ATTACH_GLIDE_SPEED_MPS * _att_dt
        _att_dx = _tx - float(d.x)
        _att_dy = _ty - float(d.y)
        if (abs(_att_dx) > 0.8 or abs(_att_dy) > 0.8):
            print(
                f"[TES-POS-WRITE] attach_slot GLIDE id={getattr(d, 'player_id', '?')}"
                f" f={getattr(self.world, 'frame_count', 0)} phase={getattr(self, '_phase', '')}"
                f" dx={_att_dx:.2f} dy={_att_dy:.2f}"
            )
        if abs(_att_dx) > _att_step:
            d.x = float(d.x) + (1.0 if _att_dx > 0 else -1.0) * _att_step
        else:
            d.x = _tx
        if abs(_att_dy) > _att_step:
            d.y = float(d.y) + (1.0 if _att_dy > 0 else -1.0) * _att_step
        else:
            d.y = _ty
        # Align facing with BC
        d.facing = bc.facing
        # Strongly damp velocity (spec 1.3)
        d.vx *= ATTACH_VELOCITY_DAMP
        d.vy *= ATTACH_VELOCITY_DAMP

        # Flag + registries
        d.tes_attached = True
        d.attach_slot = next_slot
        ctx.attached[d.player_id] = next_slot
        ctx.attached_ids.add(d.player_id)
        self._attached_player_slots[d.player_id] = next_slot
        # -- STAGE 2: THE ATTACH EVENT --
        # The body has physically reached the carrier, so stamp the frame on its
        # existing attachment record.  A no-op without an active contest, and a
        # no-op for a body that is not on the list (the primary defender).
        from game.tes.tackle_state import mark_attached
        mark_attached(self.world, d)

        if DEBUG_OPE_TRACE:
            print(f"[ATTACH] tackler #{d.player_id} slot={next_slot} "
                  f"at ({d.x:.2f},{d.y:.2f})")

    def _refresh_attachment_context(self, ctx: TESContext) -> None:
        """Recompute total_tackler_mass/momentum from attached Players.

        Reads the LIVE Player objects (not stale PlayerContexts) so the
        totals reflect actual in-world mass and velocity at attachment.
        """
        total_mass = 0.0
        total_momentum = 0.0
        for pid in ctx.attached_ids:
            player = self._find_player_by_id(pid)
            if player is None:
                continue
            mass = getattr(player, "mass_kg", 90.0)
            speed = math.hypot(getattr(player, "vx", 0.0),
                               getattr(player, "vy", 0.0))
            total_mass += mass
            total_momentum += mass * speed
        ctx.total_tackler_mass = total_mass
        ctx.total_tackler_momentum = total_momentum

    def _follow_attached_tacklers(self) -> None:
        """PHASE 1.4/2.5: attached tacklers follow BC every frame.

        For each attached tackler:
          - BC.x + stored offset (offset rotated by BC's current facing)
          - keep BC's facing direction
          - inherit BC's forward velocity (vx/vy copied from BC)

        Once attached the defender no longer moves independently â€” TES
        owns its position for CONTACT / DRIVE / DOMINANCE until RELEASE.
        """
        ctx = self.tes_ctx
        bc = self.ball_carrier
        if ctx is None or bc is None:
            return
        for pid in list(ctx.attached_ids):
            player = self._find_player_by_id(pid)
            if player is None:
                continue
            offset = ctx.attach_offsets.get(pid)
            if offset is None:
                continue
            slot_dir, mag = offset
            dx, dy = _rotate_offset(slot_dir, mag, bc.facing)
            _tx2, _ty2 = bc.x + dx, bc.y + dy
            # TES-ATTACH GLIDE (2026-09-07): same one-frame-snap guard as the
            # attach block above — every attached joiner converges to its
            # BC slot instead of teleporting.
            _att_dt2 = float(getattr(self, "dt", getattr(self.world, "dt", 1.0 / 60.0)))
            _att_step2 = _TES_ATTACH_GLIDE_SPEED_MPS * _att_dt2
            _adx = _tx2 - float(player.x)
            _ady = _ty2 - float(player.y)
            if (abs(_adx) > 0.8 or abs(_ady) > 0.8):
                print(
                    f"[TES-POS-WRITE] attach_reassert GLIDE id={getattr(player, 'player_id', '?')}"
                    f" f={getattr(self.world, 'frame_count', 0)} phase={getattr(self, '_phase', '')}"
                    f" dx={_adx:.2f} dy={_ady:.2f}"
                )
            if abs(_adx) > _att_step2:
                player.x = float(player.x) + (1.0 if _adx > 0 else -1.0) * _att_step2
            else:
                player.x = _tx2
            if abs(_ady) > _att_step2:
                player.y = float(player.y) + (1.0 if _ady > 0 else -1.0) * _att_step2
            else:
                player.y = _ty2
            player.facing = bc.facing
            player.vx = bc.vx
            player.vy = bc.vy



    def _find_player_by_id(self, player_id: int):
        """Return the Player with the given id, or None."""
        for p in self.world.players:
            if getattr(p, "player_id", None) == player_id:
                return p
        return None

    def _detach_all_tacklers(self) -> None:
        """PHASE 3.6: detach every attached tackler, clear all offsets/roles.

        Called once at RELEASE entry (from _update_release first-frame
        block).  Clears per-player attach flags AND TES context fields.
        """
        ctx = self.tes_ctx
        if ctx is not None:
            for pid in list(ctx.attached_ids):
                player = self._find_player_by_id(pid)
                if player is not None:
                    player.tes_attached = False
                    if hasattr(player, "attach_slot"):
                        player.attach_slot = None
                ctx.attach_offsets.pop(pid, None)
            ctx.attached.clear()
            ctx.attached_ids.clear()
            ctx.attach_enabled = False
            ctx.tacklers.clear()
            # PHASE 3 â€” TACKLER ROLE RESET (spec A/F/G): clear the TES
            # selection registry so no marker/line defender keeps a tackler
            # role into the next tackle cycle.
            ctx.tackler_roles.clear()
            ctx.total_tackler_mass = 0.0
            ctx.total_tackler_momentum = 0.0
            ctx.drive_strength = 1.0
            ctx.drive_step_mps = 0.0
            ctx.dominance_result = None
        self._attached_player_slots.clear()
        self._attach_armed = False

    def _maybe_add_joiners(self) -> None:
        """Assist-tackle join path (CORRECTED MODEL, 2026-08-07).

        A tackle begins with exactly ONE defender (TACKLER_1).  Up to TWO
        additional defenders may join the EXISTING tackle as assist
        tacklers (TACKLER_2, TACKLER_3).  Joining an existing tackle:

          * NEVER starts a new TES cycle,
          * NEVER restarts DRIVE / DOMINANCE (no timer or target writes),
          * reuses the atomic _arm_tackler_role() contract switch, and
          * in DRIVE/DOMINANCE attaches the joiner into the pile
            immediately so the follow step keeps them glued to the BC.

        Mandatory exclusions (matches the old no-op guard):
          - primary (already TACKLER_1), existing joiners, and any
            defender already in the TES registry,
          - frozen defenders (apply_phase_invariants pins them),
          - DEF_RETREAT_10 defenders (would get tes_locked and break the
            SDM retreat update),
          - DUMMY_HALF, and
          - a full 3-tackler pile (len(joiners) >= 2 -> cap).
        """
        bc = self.ball_carrier
        if bc is None:
            return
        if self.tes_ctx is None:
            return
        if len(self.joiners) >= 2:
            return  # cap: TACKLER_1 + up to 2 assist tacklers = 3 total

        ctx = self.tes_ctx
        sim_time = getattr(self.world, "sim_time_s", 0.0)

        for p in self.world.defending_team:
            if len(self.joiners) >= 2:
                break
            if p is self.primary:
                continue
            if p in self.joiners:
                continue
            if p.player_id in ctx.engaged_tacklers or p.player_id in ctx.tackler_roles:
                continue
            if getattr(p, "frozen", False):
                continue
            if getattr(p, "in_contact", False):
                continue
            if p.defender_role == DefenderRole.DEF_RETREAT_10:
                continue
            if p.defender_role == DefenderRole.DUMMY_HALF:
                continue

            dx = bc.x - p.x
            dy = bc.y - p.y
            dist = math.hypot(dx, dy)

            # ── JOINER ELIGIBILITY TIGHTENED (2026-08-29) ──
            # Only defenders already inside the tight 2x-TACKLE_RADIUS band
            # (genuine tacklers entering the window) may join the pile.  The
            # FX-1 lane-committed widening (depth_class PRIMARY/SECONDARY →
            # LINE_TACKLE_RADIUS_M, 6.5 m) was removed: it pulled line
            # defenders into the TES registry as tacklers, which split the
            # defensive line into left/mid/right clusters and caused the
            # deep-edge drift.  Every candidate uses the tight band.
            _r_assist_j, _r_tackle_j = _resolve_attach_radii(self)
            if dist < _r_assist_j:
                cand = _make_ctx(p)
                bc_ctx = _make_ctx(bc)
                engaged_ctx = [ctx.engagement.tacklers[i] for i in ctx.engaged_tacklers]
                if not can_join(cand, bc_ctx, engaged_ctx):
                    continue
            else:
                continue

            # â”€â”€ Assist tackle registration (does NOT restart TES) â”€â”€
            if len(self.joiners) == 0:
                role_name = "TACKLER_2"
                role_enum = DefenderRole.TACKLER_2
            else:
                role_name = "TACKLER_3"
                role_enum = DefenderRole.TACKLER_3
            self._arm_tackler_role(p, role_enum, role_name, ctx)
            p.is_tackler = True
            p.in_contact = True
            p.tes_locked = True
            self.joiners.append(p)

            # Register in the TES tackler registry so defensive behaviour
            # treats them as tacklers and marker assignment at RELEASE sees
            # them as TACKLER_* candidates.
            if not hasattr(self.world, "tackler_ids"):
                self.world.tackler_ids = set()
            self.world.tackler_ids.add(p.player_id)

            # Register in the TES engagement + selected-tackler list.
            j_ctx = _make_ctx(p)
            ctx.engagement.add(j_ctx, sim_time)
            ctx.tacklers.append(j_ctx)

            # Mid-TES attach: outside TACKLE_CONTACT the normal attach
            # system does not run (guarded to CONTACT only).  Attach the
            # joiner immediately so DRIVE/DOMINANCE follow keeps them in
            # the pile instead of the fallback LERP teleport.
            if self._phase in ("TACKLE_DRIVE", "TACKLE_DOMINANCE"):
                self._attach_defender(p, bc, ctx)
                self._refresh_attachment_context(ctx)

    # -- TACKLE_CONTACT -------------------------------------------------

    def _update_tackle_contact(self, dt: float) -> None:
        """Contact phase: update waveform, check for joiners."""
        self._phase_timer += dt

        # -- TES FREEZE-MASK (audit quieting) --
        # Same contract as TACKLE_DRIVE: during TES phases every player is
        # treated as FROZEN for AUDIT PURPOSES ONLY, even when their real
        # frozen flag is False (DEF_RETREAT_10 / TACKLERs are unfrozen by
        # MOVEMENT_RULES but idle by design).  This suppresses false
        # STUCK_NOT_FROZEN / VX_ZERO_JIGGLE / TARGET_JIGGLE violations.
        # It does NOT modify any player/world frozen flag or physics.
        # FX-3: stasis phases pin every non-participant by engine law —
        # their idleness is by design, so the wide mask stays correct here.
        self._stamp_tes_freeze_mask(True)


        bc = self.ball_carrier
        d = self.primary
        if bc is None or d is None:
            self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "TACKLE_CONTACT_ABORT")
            return

        if self.tes_ctx is not None:
            # Refresh context snapshots
            self.tes_ctx.attacker = _make_ctx(bc)
            self.tes_ctx.defender = _make_ctx(d)
            # Update waveform (TES runs here)
            update_waveform(self.tes_ctx, self.rng)
            # PHASE 1: Close selected tacklers onto the BC so they cross
            # ATTACH_RADIUS_M during CONTACT (tackles trigger at 1.5 m,
            # attach needs < 0.7 m).  Runs BEFORE attach each frame.
            self._lunge_tacklers_to_bc(dt)
            # PHASE 1: Attach eligible tacklers (CONTACT only).
            # _update_attachments() internally guards on
            # self._phase == "TACKLE_CONTACT" and ctx.attach_enabled,
            # so no attach can occur in INIT/DRIVE/DOMINANCE (spec 1.5).
            self._update_attachments()
            # Follow step: attached tacklers track BC every frame during
            # CONTACT (spec 1.4 â€” BC position + stored offset, BC facing).
            self._follow_attached_tacklers()
            # STAGE 3.5: a BUMP_OFF declaration tears the wrap down before
            # the momentum gates get to look at it.  Checked FIRST so the
            # carrier's outright win takes precedence over the raw-momentum
            # and drive-distance break paths.
            if self._bump_off_pending():
                self._handle_broken_tackle(bump_off=True)
                return
            # Spec 7.1: Check for break-tackle (BC overpowering defenders)
            if self._check_break_tackle(dt):
                self._handle_broken_tackle()
                return
            # Check for additional tacklers joining
            self._maybe_add_joiners()

            # MARKER ASSIGNMENT AT TACKLE_CONTACT (2026-09-01): assign
            # markers once contact is established so MARKER_A/B exist
            # through CONTACT/DRIVE/DOMINANCE — previously marker roles
            # were only stamped at DOMINANCE→RELEASE, so CONTACT/DRIVE/
            # DOMINANCE ran with markers=0, poisoning downstream geometry.
            # Guarded to assign ONCE (skips once markers are present).
            # ONLY FORM MARKERS AT END OF DRIVE (2026-09-06): when
            # marker_assign_at_release_only is True (canary), markers are
            # NOT role-assigned here — they are assigned ONLY at the
            # Drive→Release transition (the "tackle end").  Set the tuner
            # to False for the legacy CONTACT-time assignment.
            if (
                not getattr(
                    self.def_tuners, "marker_assign_at_release_only", True
                )
                and not any(
                    d.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B)
                    for d in self.world.defending_team
                )
            ):
                self._assign_markers_for_new_tackle()

        if self._phase_timer >= 0.15:
            # DRIVE-ENTRY RUCK UPDATE (2026-08-28): anchor the drive-time
            # defensive line to the CURRENT tackle point, not the stale
            # pre-tackle ruck.  world.ruck_x_m is otherwise frozen at the
            # pre-contact ruck through the whole drive, so the open-play
            # projection (and the depth-role anchors it seeds) lands the
            # line ~18 m left of the TES — the "non-tacklers end left /
            # ignore the BC" defect.  Update the ruck here and drop the
            # anchor lock so the first DRIVE frame re-locks ruck_x + 10.
            if bc is not None:
                self.world.ruck_x_m = bc.x
                self.world.ruck_y_m = bc.y
                reset_anchor_authority(self.world)
            self._set_phase("TACKLE_DRIVE", GameSubState.TACKLE_DRIVE, "TACKLE_CONTACT->TACKLE_DRIVE")

    # -- TACKLE_DRIVE ---------------------------------------------------

    def _update_tackle_drive(self, dt: float) -> None:
        """Drive phase: LERP-based drive movement.

        On the first frame: compute dominance/distance/duration and
        initialise the LERP snapshot ``drive_target_x``.

        Each subsequent frame: advance the elapsed drive timer and
        interpolate BC, primary, and joiner X positions from their
        DRIVE-entry snapshots to the target.  When the LERP completes
        (t >= 1.0), transition to TACKLE_DOMINANCE.

        NOTE: ruck_x_m is NOT written here Ã¢â‚¬â€ it is an immutable snapshot
        written only during _prepare_release() at the DOMINANCE->RELEASE
        boundary.
        """
        bc = self.ball_carrier
        d = self.primary
        if bc is None or d is None or self.tes_ctx is None or self.tes_outcome is None:
            self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "TACKLE_DRIVE_ABORT")
            return

        ctx = self.tes_ctx

        # â”€â”€ TES FREEZE-MASK CONTRACT (audit quieting, DRIVE only) â”€â”€
        # During TACKLE_DRIVE, TES freezes only BC + tacklers.  All other
        # players remain unfrozen but idle (vx=0), which made freeze_audit.py
        # emit false STUCK_NOT_FROZEN / STUCK_SOFT_FREEZE / VX_ZERO_JIGGLE
        # violations for every idle attacker/defender.
        #
        # LAW: During TACKLE_DRIVE, ALL players are treated as FROZEN for
        # AUDIT PURPOSES ONLY.  This mask does NOT modify real frozen flags
        # in any player/world object, does NOT affect physics, movement, TES
        # logic, or gameplay â€” it purely suppresses false freeze-audit
        # violations.  The mask lives in this frame's ctx (rebuilt each frame
        # by build_frame_context) so it never leaks into RELEASE/PTB/OPEN_PLAY.
        # FX-3: drive-era line defenders are ACTIVELY driven (soft-follow /
        # retreat writers) — un-mask them so real jitter resurfaces.
        self._stamp_tes_freeze_mask(False)

        # First frame of drive: compute all metrics, set up targets
        if self._phase_timer == 0.0:
            compute_dominance(ctx, self.tes_outcome, self.rng)
            compute_drive_distance(ctx, self.tes_outcome, self.rng)
            compute_tackle_duration(ctx, self.tes_outcome, self.rng)
            compute_ptb_time(ctx, self.tes_outcome, self.rng)

            # --- PHASE 2: TES-context DRIVE setup (not hardcoded LERP only) ---
            # Aggregate mass/momentum over ALL attached tacklers (spec 2.2).
            # Fallback: if no tackler managed to attach during CONTACT (e.g.
            # nobody got within ATTACH_RADIUS), fall back to the engagement
            # registry so DRIVE never runs on zero mass.
            if len(ctx.attached_ids) > 0:
                self._refresh_attachment_context(ctx)
            else:
                ctx.total_tackler_mass = ctx.engagement.total_mass()
                ctx.total_tackler_momentum = ctx.engagement.total_momentum()

            # Cache dominance classification (WEAK / NEUTRAL / DOMINANT)
            from game.tes.tes_state import TackleResult
            dom = self.tes_outcome.tackle_result
            if dom == TackleResult.WEAK:
                ctx.dominance_result = "WEAK"
            elif dom == TackleResult.DOMINANT:
                ctx.dominance_result = "DOMINANT"
            else:
                ctx.dominance_result = "NEUTRAL"

            # -- NRL DRIVE GEOMETRY (computed once at DRIVE entry) --
            # contact_angle: defender approach angle, 0 = perfect front-on.
            ctx.contact_angle = ctx.approach_angle_deg
            # bc_lean_forward: fraction of BC velocity along team_attack_dir
            # (1 = fully leaning into the drive, 0 = upright/backwards).
            bc_spd = math.hypot(bc.vx, bc.vy)
            if bc_spd > 0.01:
                bc_forward = (bc.vx * ctx.team_attack_dir) / bc_spd
            else:
                bc_forward = 1.0
            ctx.bc_lean_forward = max(0.0, min(1.0, bc_forward))
            # tackler_alignment: mean normalised angular deviation of engaged
            # tacklers from the approach into the BC (0 = aligned, 1 = opposing).
            tackler_pcs = list(ctx.engagement.tacklers.values())
            if len(tackler_pcs) > 1:
                # Reference direction pulled at the primary tackler (or facing).
                p0 = tackler_pcs[0]
                s0 = math.hypot(p0.vx, p0.vy)
                if s0 > 0.01:
                    ref_dir = (p0.vx / s0, p0.vy / s0)
                else:
                    ref_dir = (math.cos(p0.facing), math.sin(p0.facing))
                deviations = []
                for pc in tackler_pcs:
                    s_k = math.hypot(pc.vx, pc.vy)
                    if s_k > 0.01:
                        d_k = (pc.vx / s_k, pc.vy / s_k)
                    else:
                        d_k = (math.cos(pc.facing), math.sin(pc.facing))
                    dot_k = max(-1.0, min(1.0, d_k[0] * ref_dir[0] + d_k[1] * ref_dir[1]))
                    deviations.append(math.degrees(math.acos(dot_k)) / 90.0)
                ctx.tackler_alignment = max(0.0, min(1.0, sum(deviations) / len(deviations)))
            else:
                ctx.tackler_alignment = 0.0

            # drive_strength from mass + momentum + dominance_result (spec 2.3)
            # More attached tacklers => stronger drive (spec 3.4).
            base = {
                "WEAK": DRIVE_STRENGTH_WEAK,
                "NEUTRAL": DRIVE_STRENGTH_NEUTRAL,
                "DOMINANT": DRIVE_STRENGTH_DOMINANT,
            }.get(ctx.dominance_result, DRIVE_STRENGTH_NEUTRAL)
            tackler_bonus = (
                max(0, len(ctx.attached_ids) - 1) * DRIVE_STRENGTH_PER_TACKLER
            )
            mass_factor = max(0.9, min(1.4, ctx.total_tackler_mass / 90.0))
            ctx.drive_strength = max(
                0.5, min(2.0, base * (1.0 + tackler_bonus) * mass_factor)
            )

            # --- 2D DRIVE: decompose signed drive distance into X/Y components ---
            # Use approach_angle_deg (already computed at TES entry) to give
            # drive a lateral component based on the defender's approach vector.
            drive_mag_signed = self.tes_outcome.drive_distance_m
            drive_mag_abs = abs(drive_mag_signed)
            approach_rad = math.radians(ctx.approach_angle_deg)
            dir_y = math.sin(approach_rad)

            # Forward X drive: ALWAYS along team_attack_dir by the magnitude.
            # compute_drive_target_x() is the single source of truth:
            #   drive_target_x = drive_start_x + attack_dir * drive_magnitude
            ctx.drive_target_x = compute_drive_target_x(
                ctx.drive_start_x, ctx.team_attack_dir, drive_mag_abs)

            # FORWARD-DRIVE REBALANCE (2026-08-06):
            # 1. Reset the per-tackle backward-metre accumulator â€” the cap
            #    spans DRIVE + DOMINANCE combined, reset once per tackle.
            # 2. Pick the side-fold direction once, from the defender->BC
            #    approach side (geometry, not noise).  Random Â±1 only when
            #    the pair is level (< 0.3 m apart).
            ctx.net_backward_m = 0.0
            y_approach = ctx.bc_y0 - ctx.def_y0
            if abs(y_approach) < 0.3:
                ctx.side_fold_sign = 1.0 if self.rng.random() < 0.5 else -1.0
            else:
                ctx.side_fold_sign = 1.0 if y_approach >= 0.0 else -1.0

            # Y drive is always absolute magnitude â€” lateral direction from dir_y.
            # WEAK/NEUTRAL (attacker-win) additionally get a guaranteed minimum
            # sideways fold (SIDE_MIN_WEAK_NEUTRAL x bc_momentum_factor) so no
            # tackle reads as a pure linear shove.
            ctx.drive_target_y = ctx.bc_y0 + drive_mag_abs * dir_y
            if ctx.dominance_result in ("WEAK", "NEUTRAL"):
                ctx.drive_target_y += (
                    SIDE_MIN_WEAK_NEUTRAL * ctx.bc_momentum_factor * ctx.side_fold_sign
                )

            # Per-second forward drive step (spec 2.4): metres per second of
            # BC forward movement, scaled by drive_strength.
            # BOOST TACKLE MOMENTUM (2026-08-05): baseline metres x1.5 so
            # the BC moves further along team_attack_dir even before the
            # per-frame variation is added.
            dur0 = max(0.1, self.tes_outcome.tackle_duration_s)
            # MOMENTUM GATE (2026-08-06): multiply the deterministic drive
            # step by the BC's pre-contact momentum factor (0.35 flat-footed
            # -> 1.0 full sprint) so a flat-footed BC cannot drive 3 m.
            ctx.drive_step_mps = (
                ((drive_mag_abs / dur0) * ctx.drive_strength) * 1.5
                * ctx.bc_momentum_factor
            )

            # Initialise elapsed --- first frame is zero so BC stays at bc_x0
            ctx.drive_elapsed_s = 0.0
            # Deterministic metre accumulator (NRL DRIVE, 2026-08-05).
            ctx.drive_det_x = 0.0

            # Snapshot joiner starting positions (addressed by player_id)
            # so each joiner can LERP individually during the drive.
            self._joiner_x0: dict[int, float] = {}
            self._joiner_y0: dict[int, float] = {}
            for j in self.joiners:
                self._joiner_x0[j.player_id] = j.x
                self._joiner_y0[j.player_id] = j.y

            # PHASE 2.5: duration driven by TES context + dominance.
            # DOMINANT shorter, NEUTRAL medium, WEAK longer (spec 2.5).
            dur_mod = {
                "WEAK": DRIVE_DURATION_WEAK,
                "NEUTRAL": DRIVE_DURATION_NEUTRAL,
                "DOMINANT": DRIVE_DURATION_DOMINANT,
            }.get(ctx.dominance_result, DRIVE_DURATION_NEUTRAL)
            # BOOST TACKLE MOMENTUM (2026-08-05): duration ceiling raised
            # 1.5 s -> 1.95 s so the extended DRIVE phase (x1.3 in
            # compute_tackle_duration) is not cut short here â€” more DRIVE
            # frames = more accumulated variation = more forward metres.
            self.tes_outcome.tackle_duration_s = max(
                0.4, min(1.95, self.tes_outcome.tackle_duration_s * dur_mod)
            )

            # [1] CONTACT -> DRIVE ENTRY AUDIT (first DRIVE frame)
            if DEBUG_SDEA_PHYSICS:
                print(f"[SDEA_PHYSICS] ENTER_DRIVE "
                      f"bc_start_x={bc.x:.3f} "
                      f"drive_target_x={ctx.drive_target_x:.3f} "
                      f"drive_strength={ctx.drive_strength:.3f} "
                      f"duration_mod={dur_mod:.3f}")

        self._phase_timer += dt

        # Advance the drive timer
        ctx.drive_elapsed_s += dt
        dur = max(0.1, self.tes_outcome.tackle_duration_s)
        t = max(0.0, min(1.0, ctx.drive_elapsed_s / dur))

        # -- DRIVE WATCHDOG (2026-08-22) --
        # The t >= 1.0 LERP-completion branch below is the ONLY legal exit
        # from TACKLE_DRIVE into TACKLE_DOMINANCE.  If timer drift ever
        # leaves t short of 1.0 once the drive duration has fully elapsed
        # (+0.5 s margin), force completion so the phase machine can never
        # wedge in TACKLE_DRIVE - a wedged drive never reaches RELEASE, so
        # no new ruck forms and the defensive pipeline runs on stale state.
        if t < 1.0 and self._phase_timer > dur + 0.5:
            t = 1.0

        # -- STAGE 3.1 PROGRESSIVE_DRAG: THE DECAY INTERCEPT --
        # Placed AFTER the one-shot DRIVE-entry setup above (which writes
        # ctx.drive_step_mps exactly once, when _phase_timer == 0.0) and
        # BEFORE the dx_step write below, so on every frame:
        #   * the freshly computed entry authority is decayed too, and
        #   * the REDUCED authority shapes THIS frame's metres.
        # A PROGRESSIVE_DRAG contest is a measured grind, so one decay step
        # is applied per sustained DRIVE frame until the drive ends -- or,
        # from Step 3.2, until the decayed rate hits the terminal floor.
        # _progressive_drag_pending() is the gate: lever armed AND a
        # resolved wrap model AND the CONTEST token.  With the lever off it
        # is False and this frame is bit-identical to the pre-Stage-3.1
        # engine.
        if self._progressive_drag_pending():
            self._apply_progressive_drag(bc, ctx)
            # -- STAGE 3.2: THE TERMINAL FLOOR --
            # The decay above just shrank this frame's forward authority.
            # Once it is below the floor the carrier is no longer going
            # anywhere: abandon the drive on THIS frame, before the dx_step
            # write below can spend the dead authority on another metre, and
            # complete the tackle as a standard ruck.
            if self._progressive_drag_spent(ctx):
                self._exit_progressive_drag_to_ruck(bc, ctx)
                return

        # ---- PHASE 2.4: drive-strength-based BC movement ----
        # BC x advances per frame by drive_step_mps * dt along team_attack_dir.
        # If the defending team DOMINATES, the BC is driven BACKWARD
        # (opposite team_attack_dir) â€” spec 2.4 "BC.x must decrease if
        # defending team dominates".
        #
        # NRL DRIVE (2026-08-05) â€” Step 1: deterministic base movement with
        # attacker-win boost (Step 5: x1.8 for WEAK/NEUTRAL) and geometric
        # correction (Step 6).
        if ctx.dominance_result == "DOMINANT":
            dx_step = -ctx.drive_step_mps * dt
            # Defender-win suppression (spec Step 5: defender wins).
            dx_step *= DOMINANT_DET_SUPPRESS
        else:
            dx_step = ctx.drive_step_mps * dt
            # Attacker-win deterministic boost (spec Step 5).
            dx_step *= DRIVE_DETERMINISTIC_BOOST
        # Geometric correction factor (spec Step 6): reduces effective drive
        # when contact geometry is poor.  Always in [0, 1], never modifies
        # TES state, clamp logic, or tackler-follow logic.
        geometric_factor = compute_geometric_factor(ctx)
        dx_step *= geometric_factor

        # FORWARD-DRIVE REBALANCE (2026-08-06):
        # DOMINANT steps are split â€” half the backward impulse stays on X,
        # the other half is converted to a sideways fold (applied to bc.y
        # after the Y lerp below).  Backward metres actually applied are
        # accumulated into ctx.net_backward_m for the per-tackle cap; once
        # the cap is hit, further backward X is heavily damped while the
        # sideways fold keeps its full magnitude (wrestle turns sideways).
        back_mag = abs(dx_step)
        if ctx.dominance_result == "DOMINANT":
            dx_step *= DOMINANT_X_REBALANCE
            if (dx_step * ctx.team_attack_dir) < 0.0:
                ctx.net_backward_m += abs(dx_step)
                if ctx.net_backward_m > NET_BACKWARD_CAP_M:
                    dx_step *= NET_BACKWARD_DAMP

        # Accumulate the signed per-frame step (metres) so the drive reaches
        # full arc over the duration: dx_step already carries team_attack_dir,
        # the DOMINANT back-drive sign, the attacker boost, and geometry.
        ctx.drive_det_x += dx_step
        bc.x = ctx.drive_start_x + ctx.drive_det_x
        # Y drive is a straight lerp (lateral component, no strength scaling)
        bc.y = ctx.bc_y0 + (ctx.drive_target_y - ctx.bc_y0) * t
        # Sideways fold: DOMINANT converts ~0.9x of the (pre-rebalance)
        # backward magnitude into lateral movement, away from the primary
        # tackler's approach side (side_fold_sign).
        if ctx.dominance_result == "DOMINANT":
            bc.y += back_mag * DOMINANT_SIDE_FOLD * ctx.side_fold_sign

        # ---- TES DRIVE VARIATION (extra metres / wrestle) ----
        # Contract B: BC gains controlled forward impulse layered ON TOP of
        # the deterministic LERP, with per-frame variation, defender
        # strength resistance, and momentum decay.  BC movement ALWAYS
        # happens BEFORE _follow_attached_tacklers() so the pile stays
        # glued to the BC (contract F).
        if ctx.drive_elapsed_s is not None:
            strength_sum = defender_strength_sum(ctx)

            # [6] DEFENDER INTERACTION AUDIT (defender suppression check)
            if DEBUG_SDEA_PHYSICS:
                print(f"[SDEA_PHYSICS] DEF_INTERACT "
                      f"num_tacklers={len(ctx.attached)} "
                      f"strength_sum={strength_sum:.3f} "
                      f"resistance={strength_sum * DEF_RESISTANCE_SCALE:.3f}")

            raw_force = compute_drive_force(self.rng, strength_sum)

            # -- DYNAMIC MOMENTUM CEILING (2026-10-04) --
            # Replace the flat 2.6 cap with a ceiling scaled by the drive's
            # own entry context: high-strength drives punch well past 2.6,
            # weaker drives cap much lower, so the per-frame force is no
            # longer saturated and the momentum channel stays alive.
            dynamic_max = DRIVE_MOMENTUM_BASE_CEILING + (
                ctx.drive_strength * DRIVE_MOMENTUM_STRENGTH_GAIN
            )

            # -- DEFENSIVE DRAG (2026-10-04) --
            # strength_sum -> resistance.  Applying exp(-resistance * dt)
            # every frame compounds over the phase to exp(-resistance *
            # elapsed): the accumulated momentum bleeds off exponentially the
            # longer the carrier is held up, a real bog-down rather than a
            # flat reduction.
            resistance = strength_sum * DEF_RESISTANCE_SCALE
            drag = math.exp(-resistance * dt)

            ctx.drive_momentum = max(
                DRIVE_MOMENTUM_CLAMP[0],
                min(
                    dynamic_max,
                    (ctx.drive_momentum * DRIVE_DECAY + raw_force) * drag,
                ),
            )

            # [7] MOMENTUM CLAMP AUDIT (dynamic ceiling + drag)
            if DEBUG_SDEA_PHYSICS:
                print(f"[SDEA_PHYSICS] MOMENTUM_CLAMP "
                      f"momentum={ctx.drive_momentum:.3f} "
                      f"dynamic_max={dynamic_max:.3f} "
                      f"resistance={resistance:.3f} "
                      f"drag={drag:.4f}")

            # -- STRIDE CADENCE (2026-10-04) --
            # Replace the micro-noise drift (which contributed ~0 net metres)
            # with a coarse, frame-based leg pump: a standard sine wave over
            # the simulation frame counter modulates bc.x directly, so the
            # carrier visibly surges and stalls as it struggles forward
            # instead of sliding at a uniform rate.  Amplitude scales with
            # the accumulated drive momentum, the BC pre-contact momentum
            # factor and the contact geometry; the wave is deterministic
            # (world.frame_count is the sim cycle key).
            current_frame = int(getattr(self.world, "frame_count", 0) or 0)
            stride_mod = math.sin(current_frame * STRIDE_WAVE_RATE)
            stride_step = stride_mod * (
                ctx.drive_momentum * STRIDE_AMPLITUDE_PER_MOMENTUM * dt
                * ctx.bc_momentum_factor * geometric_factor
                * ctx.team_attack_dir
            )
            variation_step = stride_step
            bc.x += variation_step
            ctx.drive_variation_x += variation_step

            # [8] TEAM ATTACK DIR AUDIT
            if DEBUG_SDEA_PHYSICS:
                print(f"[SDEA_PHYSICS] ATTACK_DIR {ctx.team_attack_dir}")

            # [2] DRIVE PHYSICS SCORE AUDIT (EVERY FRAME)
            if DEBUG_SDEA_PHYSICS:
                print(f"[SDEA_PHYSICS] DRIVE "
                      f"raw={raw_force:.3f} "
                      f"momentum={ctx.drive_momentum:.3f} "
                      f"stride_mod={stride_mod:.3f} "
                      f"variation_step={variation_step:.3f} "
                      f"bc.x={bc.x:.3f} "
                      f"def_strength={strength_sum:.3f}")

            # TES MOMENTUM DIAGNOSTIC (2026-08-06): log per-frame BC forward
            # momentum behaviour (observational only â€” never modifies physics).
            if ctx is not None:
                _fl = getattr(ctx, "frame_log", None)
                if _fl is None:
                    _fl = []
                    ctx.frame_log = _fl
                if len(_fl) >= 500:
                    _fl.pop(0)
                _fl.append(investigate_tes_forward_momentum(
                    bc, d, self._phase, {}))

            # Attached tacklers follow BC exactly (spec 2.4/2.5) â€” they inherit
            # BC's forward velocity and never move independently.
            self._follow_attached_tacklers()
        # [9] FOLLOW OVERWRITE AUDIT (does BC.x jump back after follow?)
        if DEBUG_SDEA_PHYSICS:
            print(f"[SDEA_PHYSICS] FOLLOW bc.x={bc.x:.3f}")
        # Non-attached primary/joiners (e.g. nobody attached in CONTACT) fall
        # back to the old relative LERP so the pile still moves with the BC.
        if d.player_id not in ctx.attached_ids:
            d.x = ctx.def_x0 + ((ctx.drive_target_x - 0.5) - ctx.def_x0) * t
            d.y = ctx.def_y0 + (ctx.drive_target_y - ctx.bc_y0) * t
        for j in self.joiners:
            if j.player_id in ctx.attached_ids:
                continue
            j_x0 = self._joiner_x0.get(j.player_id, j.x)
            j_y0 = self._joiner_y0.get(j.player_id, j.y)
            j.x = j_x0 + ((ctx.drive_target_x - 0.3) - j_x0) * t
            j.y = j_y0 + (ctx.drive_target_y - ctx.bc_y0) * t

        # STAGE 3.5: BUMP_OFF clearance (see _update_tackle_contact).
        if self._bump_off_pending():
            self._handle_broken_tackle(bump_off=True)
            return
        # Spec 7.1: Check for break-tackle during drive (raw momentum check)
        if self._check_break_tackle(dt):
            self._handle_broken_tackle()
            return
        # Spec 7.1: Check for broken tackle using scoring system.
        # -- DOMINANCE LATCH (2026-08-22) --
        # The outcome classification is computed ONCE at DRIVE entry and
        # then FROZEN for the rest of the drive.  Re-running
        # compute_dominance() every frame let a live result flip (e.g.
        # NEUTRAL -> WEAK as engaged-tackler velocities changed) re-arm
        # _check_broken_tackle_scoring() MID-DRIVE against a
        # drive_distance_m that was fixed at entry - a break exit the
        # entry-time classification never authorised.  A mid-drive break
        # also skips the DRIVE-END lock (drive_end_x / ruck_x_m pinning),
        # leaving ruck state stale for the next cycle.  Fresh contexts per
        # tackle make the latch self-resetting (attribute absent -> False).
        if self.tes_ctx is not None and self.tes_outcome is not None:
            if not getattr(self.tes_ctx, "drive_dominance_latched", False):
                compute_dominance(self.tes_ctx, self.tes_outcome, self.rng)
                self.tes_ctx.drive_dominance_latched = True
            if self._check_broken_tackle_scoring(self.tes_ctx, self.tes_outcome):
                self._handle_broken_tackle()
                return
        # ASSIST TACKLE (corrected model): a defender may join the existing
        # tackle mid-DRIVE.  Joining never restarts DRIVE â€” the elapsed timer,
        # targets, and dominance remain untouched; the joiner is attached into
        # the pile immediately by _maybe_add_joiners().
        self._maybe_add_joiners()
        # Transition when LERP completes
        if t >= 1.0:
            # [3] DRIVE -> DOMINANCE TRANSITION AUDIT
            if DEBUG_SDEA_PHYSICS:
                print(f"[SDEA_PHYSICS] DRIVE_END "
                      f"drive_total_variation={ctx.drive_variation_x:.3f} "
                      f"bc_end_x={bc.x:.3f}")
            # â”€â”€ DRIVE-END LOCK (LAW) â”€â”€
            # BC + ruck anchor derive ONLY from drive_target_x, never from
            # contact_x / bc_x0.  This pins the physical drive end at the
            # instant the LERP completes, so DOMINANCE/RELEASE/PTB all see
            # the forward-driven X.  _prepare_release() re-writes ruck_x_m
            # with the same value (idempotent).
            # DRIVE-END (variation-aware): BC's LIVE position IS the drive
            # end.  The deterministic LERP target is only a baseline --
            # variation already moved BC smoothly forward each frame, and
            # the LERP + strength/duration modifiers end short of the raw
            # target.  Pinning bc.x to target+variation would SNAP BC (a
            # visible teleport), so we anchor on the live position and
            # NEVER overwrite bc.x.  DOMINANCE/RELEASE/PTB all read bc.x
            # live, so all downstream anchors are correct.
            drive_end_x = bc.x
            ctx.drive_target_x = drive_end_x
            bc.target_x = drive_end_x
            self.world.ruck_x_m = drive_end_x
            # Re-sync attached tacklers so the pile holds BC + offset
            # through DOMINANCE until RELEASE.
            self._follow_attached_tacklers()

            # ── DRIVE-END AUTHORITATIVE SLOTS (2026-08-19) ──
            # Compute evenly-spaced Y slot positions for eligible
            # defenders (partitioned by side of ruck_y), clear their
            # blend baselines and movement locks, and stamp
            # authoritative_until_frame so the finalise blend,
            # stabiliser, and lane-index sorter all skip them for
            # one frame.
            if hasattr(self.def_behaviour, "drive_end_authoritative"):
                self.def_behaviour.drive_end_authoritative(
                    self.world, self.world.defending_team,
                )

            # DOMINANCE VARIATION: pick the hold duration once per tackle
            # based on the dominance result (WEAK < NEUTRAL < DOMINANT),
            # giving the backward pushback time to be visible ("wrestle").
            ctx.dominance_hold_s = dominance_hold_for(
                ctx.dominance_result or "NEUTRAL"
            )
            ctx.dominance_elapsed_s = 0.0
            self._set_phase("TACKLE_DOMINANCE", GameSubState.TACKLE_DOMINANCE, "TACKLE_DRIVE->TACKLE_DOMINANCE")

    # -- TACKLE_DOMINANCE -----------------------------------------------

    def _update_tackle_dominance(self, dt: float) -> None:
        """Dominance phase: run outcome checks, then release."""
        self._phase_timer += dt

        # -- TES FREEZE-MASK (audit quieting) --
        # Same contract as TACKLE_DRIVE: during TES phases every player is
        # treated as FROZEN for AUDIT PURPOSES ONLY, even when their real
        # frozen flag is False (DEF_RETREAT_10 / TACKLERs are unfrozen by
        # MOVEMENT_RULES but idle by design).  This suppresses false
        # STUCK_NOT_FROZEN / VX_ZERO_JIGGLE / TARGET_JIGGLE violations.
        # It does NOT modify any player/world frozen flag or physics.
        # FX-3: dominance-era line defenders are actively driven — audit.
        self._stamp_tes_freeze_mask(False)


        if self.tes_ctx is not None and self.tes_outcome is not None:
            check_offload(self.tes_ctx, self.tes_outcome, self.rng)
            check_strip(self.tes_ctx, self.tes_outcome, self.rng)
            check_fumble(self.tes_ctx, self.tes_outcome, self.rng)
            check_penalty(self.tes_ctx, self.tes_outcome, self.rng)
            check_six_again(self.tes_ctx, self.tes_outcome, self.rng)
            check_line_break(self.tes_ctx, self.tes_outcome, self.rng)

        # STAGE 3.5: BUMP_OFF clearance (see _update_tackle_contact).
        if self._bump_off_pending():
            self._handle_broken_tackle(bump_off=True)
            return
        # Spec 7.1: Check for break-tackle during dominance (raw momentum check)
        if self._check_break_tackle(dt):
            self._handle_broken_tackle()
            return
        # Spec 7.1: Check for broken tackle using scoring system (re-evaluated each frame)
        # ---- TES DOMINANCE PUSHBACK (wrestle / dominance) ----
        # Contract D: each DOMINANCE frame the defending tacklers push the
        # BC backwards along -team_attack_dir.  BC moves FIRST, then the
        # attached pile follows so everything stays glued (contract F).
        if self.tes_ctx is not None:
            ctx = self.tes_ctx
            push = compute_dominance_push_result(self.rng, ctx)
            bc = self.ball_carrier
            if bc is not None:
                # [4] DOMINANCE PHYSICS AUDIT (EVERY FRAME)
                # RESULT-AWARE (2026-08-06): push_step is only applied
                # backward (sign -1) on DOMINANT; WEAK gets 0 and NEUTRAL
                # a tiny settle computed by compute_dominance_push_result,
                # so attacker-wins no longer go backwards every tackle.
                push_step = push * dt * ctx.team_attack_dir * -1  # backwards

                # FORWARD-DRIVE REBALANCE (2026-08-06): same split as the
                # DRIVE phase â€” halve the backward X, convert ~0.9x of the
                # pre-rebalance magnitude into a sideways fold, and damp
                # further backward push once the per-tackle cap is hit.
                push_back_mag = abs(push_step)
                push_step *= DOMINANT_X_REBALANCE
                if (push_step * ctx.team_attack_dir) < 0.0:
                    ctx.net_backward_m += abs(push_step)
                    if ctx.net_backward_m > NET_BACKWARD_CAP_M:
                        push_step *= NET_BACKWARD_DAMP
                bc.x += push_step
                bc.y += push_back_mag * DOMINANT_SIDE_FOLD * ctx.side_fold_sign
                if DEBUG_SDEA_PHYSICS:
                    print(f"[SDEA_PHYSICS] DOMINANCE "
                          f"push={push:.3f} "
                          f"push_step={push_step:.3f} "
                          f"bc.x={bc.x:.3f} "
                          f"bc.y={bc.y:.3f} "
                          f"net_backward={ctx.net_backward_m:.3f} "
                          f"hold_s={ctx.dominance_hold_s:.2f}")
            self._follow_attached_tacklers()
            # [9] FOLLOW OVERWRITE AUDIT (dominance)
            if DEBUG_SDEA_PHYSICS and bc is not None:
                print(f"[SDEA_PHYSICS] FOLLOW bc.x={bc.x:.3f}")

        # ASSIST TACKLE (corrected model): a defender may join the existing
        # tackle during DOMINANCE.  Joining never restarts DOMINANCE â€” the
        # hold timer and dominance result are untouched; the joiner is
        # attached into the pile immediately by _maybe_add_joiners().
        self._maybe_add_joiners()

        if self.tes_ctx is not None and self.tes_outcome is not None:
            compute_dominance(self.tes_ctx, self.tes_outcome, self.rng)
            if self._check_broken_tackle_scoring(self.tes_ctx, self.tes_outcome):
                self._handle_broken_tackle()
                return
        if self.tes_ctx is not None:
            self.tes_ctx.dominance_elapsed_s += dt
            dominance_done = (
                self.tes_ctx.dominance_elapsed_s >= self.tes_ctx.dominance_hold_s
            )
        else:
            dominance_done = self._phase_timer >= DOMINANCE_SETTLE_S
        if dominance_done:
            # [5] DOMINANCE -> RELEASE EXIT AUDIT + [10] FINAL DRIVE METRES
            if DEBUG_SDEA_PHYSICS:
                bc_end = self.ball_carrier
                if self.tes_ctx is not None and bc_end is not None:
                    print(f"[SDEA_PHYSICS] DOMINANCE_END bc.x={bc_end.x:.3f}")
                    print(f"[SDEA_PHYSICS] DRIVE_TOTAL "
                          f"variation={self.tes_ctx.drive_variation_x:.3f} "
                          f"bc_end_x={bc_end.x:.3f}")

            # â”€â”€ TES MOMENTUM DIAGNOSTIC CSV DUMP (2026-08-06) â”€â”€
            # investigate_tes_forward_momentum() accumulated one record per
            # TES frame into ctx.frame_log.  Dump to
            # tes_momentum_<sim_time>.csv on the DOMINANCE->RELEASE boundary
            # so the "BC goes backwards / jiggles" data is preserved for
            # analysis without touching gameplay.
            if self.tes_ctx is not None and self.tes_ctx.frame_log:
                try:
                    import csv, os
                    _log = self.tes_ctx.frame_log
                    _fname = "tes_momentum_%.1f.csv" % getattr(
                        self.world, "sim_time_s", 0.0)
                    _feat = list(_log[0].keys())
                    with open(_fname, "w", newline="", encoding="utf-8") as fh:
                        w = csv.DictWriter(fh, fieldnames=_feat)
                        w.writeheader()
                        for rec in _log:
                            w.writerow(rec)
                    self.tes_ctx.frame_log = []
                except Exception:
                    pass  # diagnostics only â€” never break the tackle

            # =========================================================
            # DOMINANCE -> RELEASE: TES shuts down, DH assigned
            # =========================================================
            self._prepare_release()
            self._just_entered_release = True
            # ── POST-TES DRIVE-END SETTLE WINDOW (2026-09-06) ──
            # At DOMINANCE→RELEASE the ruck forms at (ruck_x, ruck_y) and the
            # post-TES writers (ruck-pin position snap, spacing/width clamps)
            # would otherwise snap bodies from their drive-era BC-relative
            # positions back to ruck-relative targets on the very first
            # RELEASE frame (the teleporting-around-the-ruck symptom).  Arm
            # the settle counter so those snaps are gated for a few frames and
            # the defenders glide to the ruck target before retreat/reform
            # takes over.  0 = disabled (legacy behaviour).
            self.world.post_tes_settle_frames = getattr(
                self.def_tuners, "post_tes_settle_frames", 0
            )
            # PHASE-AWARE SETTLE WINDOW (2026-09-07): the settle window now
            # covers the whole post-drive reform span (RELEASE → PTB_* →
            # POST_RUCK_OPEN_PLAY).  Arm the "enabled this cycle" latch so the
            # phase-set gate in post_tes_settle_active() stays live even after
            # the frame counter burns down to 0.  0 = disabled (latch False).
            self.world.post_tes_settle_enabled = (
                getattr(self.def_tuners, "post_tes_settle_frames", 0) > 0
            )
            self._set_phase("RELEASE", GameSubState.TACKLE_RELEASE, "TACKLE_DOMINANCE->RELEASE")

    def _prepare_release(self) -> None:
        """Set up ruck position and assign dummy half on DOMINANCE->RELEASE.

        Per spec 4.1 (Marker Assignment):
          - TACKLER_1 Ã¢â€ â€™ MARKER_A
          - TACKLER_2 Ã¢â€ â€™ MARKER_B
          - TACKLER_3 + any extras Ã¢â€ â€™ DEF_RETREAT_10 (never markers)
          - All other DEF_RETREAT_10 defenders stay in retreat

        Engine-Spec X-Axis Marker Alignment:
          - Markers are assigned roles but NOT frozen Ã¢â‚¬â€ they will be unfrozen
            immediately at RELEASE entry so they can move to X-axis positions.
          - Marker A target: (ruck_x + 1, ruck_y)
          - Marker B target: (ruck_x + 2, ruck_y)
          - No Y-offsets are used.

        (Section B) TES stays active through RELEASE Ã¢â‚¬â€ tes_active is cleared
        at the END of _update_release() and in _reset_tackle_state().
        Player tes_locks are cleared here so players can move during RELEASE.

        OPE's self.joiners is the authoritative tracking Ã¢â‚¬â€ no scanning of
        world.players for "tackle_role" attribute.
        """
        bc = self.ball_carrier
        if bc is None:
            return
        bc.attack_role = "ball_carrier"

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â Clear player TES locks (so players can move during RELEASE) Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        if bc is not None:
            bc.tes_locked = False
        if self.primary is not None:
            self.primary.tes_locked = False
        for j in self.joiners:
            j.tes_locked = False

        # -- STALE COMMIT-LOCK WIPE (2026-09-02) --
        # The TES-drive open-play projection stamps _tackle_commit_active /
        # _pursuit_commit_active on PRIMARY/SECONDARY tacklers
        # (_apply_tackler_commit / _apply_pursuit_commit_override).
        # _clear_tes_artefacts() clears them on the first RELEASE frame, but
        # the UN-GATED spacing preamble at the TOP of that frame runs BEFORE
        # the clear.  A stale committed body - now DEF_RETREAT_10
        # (chain-eligible, NOT in _SPACING_EXCLUDED_ROLES) and often the
        # physically-nearest body on the short side - is then used by
        # _side_hinge_body() as the spacing hinge / inner anchor.  Because
        # _tackle_commit_active makes the MMDL / target_stabiliser /
        # reactivate_movement_after_thaw all skip it, the defender stays
        # "stuck" in place, so short-side spacing anchors off a stuck
        # defender.  Clear the locks HERE, at the DOMINANCE->RELEASE
        # transition, so the very next frame's preamble sees clean flags
        # (idempotent with _clear_tes_artefacts' own wipe).
        for _p in self.world.defending_team:
            _p._tackle_commit_active = False
            if hasattr(_p, "_pursuit_commit_active"):
                _p._pursuit_commit_active = False
            _p.tackle_role = None

        # DRIVE-END ANCHOR (LAW): ruck_x / ptb_anchor / last_contact derive
        # from the BC drive END X (bc.x == ctx.drive_target_x since the
        # DRIVE->DOMINANCE transition).  NEVER the contact/entry X
        # (bc_x0 / contact_x_m) -- contact is only valid pre-DRIVE.
        self.world.ruck_x_m = bc.x
        self.world.ruck_y_m = bc.y

        # RELEASE the emergency fullback at the drive-end boundary, not on
        # the following RELEASE handler frame. Otherwise TES attachment/
        # contact still owns the body after the drive has finished, preventing
        # its depth-return geometry from running until later in the PTB cycle.
        _fb = next(
            (
                d for d in self.world.defending_team
                if getattr(d, "fullback_role_active", False)
                and _fb_emergency_engaged(d)
            ),
            None,
        )
        if _fb is not None:
            _fb.tes_attached = False
            _fb.attach_slot = None
            _fb.in_contact = False
            _fb.tackle_locked = False
            _fb.tes_locked = False
            _fb.frozen = False
            # The drive depth authority's final Player.update() stage can
            # still glide a depth-locked body back to its carrier-relative
            # pin after fullback geometry has commanded the ruck-depth return.
            # End that tackle-only positional ownership before integrating
            # the fullback on this drive-end frame.
            _fb.depth_locked = False
            _fb.depth_lock_x = float(_fb.x)
            _fb.depth_lock_y = None
            _pid = getattr(_fb, "player_id", None)
            if _pid is not None:
                self._attached_player_slots.pop(_pid, None)
            if self.tes_ctx is not None:
                if _pid is not None:
                    self.tes_ctx.attached_ids.discard(_pid)
                    self.tes_ctx.attached.pop(_pid, None)
                    self.tes_ctx.attach_offsets.pop(_pid, None)
            _fb_arm_post_tackle_return(_fb)
            _fb_reset_engagement(_fb)
            _fb_clear_chase_state(self.world)
            self.world.fullback_recover_allowed = True
            apply_fullback_geometry(
                self.world, _fb, post_tackle_ruck_anchor=True
            )
            if not _fb.frozen:
                _fb.update(self.world.dt)

        # PTB anchor = DRIVE END (same locked X as ruck_x), NOT the
        # contact point.  PTBManager targets live bc.x - 1.0 each frame
        # and never reads contact_x.
        self.world.ptb_anchor_x = bc.x
        self.world.ptb_anchor_y = bc.y
        self.world.last_contact_x = bc.x
        self.world.last_contact_y = bc.y

        # ── PTB STATE-MACHINE CYCLE RESET (2026-09-17) ──
        # A new tackle cycle begins at DOMINANCE->RELEASE: drop the previous
        # cycle's PTBManager state/timer here so PTB_PREP can never inherit
        # a stale "complete" state.  Belt-and-braces with the reset in
        # _enter_ptb_prep().
        self.ptb_manager.reset()

        # Ã¢â€â‚¬Ã¢â€â‚¬ Spec 4.1: Fresh marker assignment per tackle cycle Ã¢â€â‚¬Ã¢â€â‚¬
        # Delegates to dedicated method that clears old markers, applies
        # the 3-case rugby-league rule (tacklers -> closest -> default),
        # and sets targets + temporary freeze (unfrozen in _update_release).
        self._assign_markers_for_new_tackle()

        # â”€â”€ RETREAT-10 FEED AT RELEASE (2026-08-07, corrected spec) â”€â”€
        # DEF_RETREAT_10 is assigned ONLY here â€” NOT at _start_tackle.
        # During the TES drive (TACKLE_DRIVE / TACKLE_DOMINANCE) every
        # non-tackler defender kept its pre-tackle role and ran the
        # open-play defensive AI unchanged.  At RELEASE entry every
        # non-marker, non-DH defender is converted to DEF_RETREAT_10 so
        # the existing RELEASE/PTB/POST_RUCK retreat pipeline (P7 handlers,
        # role movement, POST_RUCK compliance) retreats them 10 m exactly
        # as before.  _assign_markers_for_new_tackle() already converted
        # stale markers and non-selected tacklers; this normalises every
        # other surviving role (DEF_LINE_READY / DEF_CHASE) into retreat.
        #
        # FULLBACK EXCLUSION (2026-08-13): the dynamic fullback is NOT a
        # line/retreat actor.  It keeps DEF_LINE_READY (a legal mover) and
        # its own backfield anchor is re-asserted by the phase handlers;
        # feeding it into the retreat-10 sweep would drag it onto the line.
        for d in self.world.defending_team:
            if getattr(d, "fullback_role_active", False):
                continue
            if d.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                continue
            if d.defender_role == DefenderRole.DUMMY_HALF:
                continue
            d.defender_role = DefenderRole.DEF_RETREAT_10
            d.freeze_timer = 0.0
            # ── RETREAT-OBLIGATION: REVOKE ON CREATION (2026-09-18) ──
            # This sweep is where the obligation is BORN, and the tail of
            # ``_update_release`` (the P7 retreat write, which carries the
            # shared retreat-contract enforcement) does not run until the
            # NEXT frame.  Without this, a body kept the tackle authority
            # it held on the previous OPEN_PLAY frame — measured as
            # ``depth_class=SECONDARY`` on exactly 10 bodies for exactly
            # one frame, and 1 frame x 10 bodies was the ONLY violation
            # left in a 1200-frame rollout after the retreat-era
            # enforcement landed.
            #
            # The contract is "an obligated body holds no tackle
            # authority", and it must hold from the instant the role is
            # assigned, not one frame later.
            strip_tackle_authority(self.world, d)

        # ── POST-DRIVE SIDE REBALANCE (2026-08-18, task spec §1) ──
        # After a drive one side may have >1 extra defender.  Pick the
        # closest interior defender on the overloaded side (exclude
        # winger/fullback), flip its depth_side, and clear _depth_seeded
        # so the next frame's un-gated defensive pipeline re-seeds it
        # into the correct side's role/spacing chain.
        _REBAL_EXCLUDED = frozenset({
            DefenderRole.TACKLER_1, DefenderRole.TACKLER_2,
            DefenderRole.TACKLER_3, DefenderRole.MARKER_A,
            DefenderRole.MARKER_B, DefenderRole.DUMMY_HALF,
            DefenderRole.KICK_CHASE,
        })
        _INTERIOR_ROLES = frozenset({"A", "B", "C", "D", "CENTRE"})
        _rebal_left = 0
        _rebal_right = 0
        _rebal_candidates = []
        for d in self.world.defending_team:
            if getattr(d, "fullback_role_active", False):
                continue
            if d.defender_role in _REBAL_EXCLUDED:
                continue
            _side = getattr(d, "depth_side", None)
            if _side == -1:
                _rebal_left += 1
            elif _side == 1:
                _rebal_right += 1
            if getattr(d, "depth_role", None) in _INTERIOR_ROLES:
                _rebal_candidates.append(d)
        _rebal_diff = _rebal_left - _rebal_right
        _overloaded_side = None
        if _rebal_diff > 1:
            _overloaded_side = -1   # LEFT has too many
        elif _rebal_diff < -1:
            _overloaded_side = 1    # RIGHT has too many
        if _overloaded_side is not None:
            _flip_pool = [
                d for d in _rebal_candidates
                if getattr(d, "depth_side", None) == _overloaded_side
            ]
            if _flip_pool:
                _ruck_y_rebal = self.world.ruck_y_m
                _flipper = min(
                    _flip_pool,
                    key=lambda d: abs(d.y - _ruck_y_rebal),
                )
                # FULLBACK LOCK (2026-08-27): the active fullback's depth_side
                # is owned by fullback_role / apply_fullback_geometry (jersey-1
                # slot).  Never flip it off its locked deep-field side — the FB
                # is excluded from the interior-role flip pool, but this guard
                # is belt-and-suspenders in case it ever reaches here.
                if getattr(_flipper, "fullback_role_active", False):
                    pass
                else:
                    _flipper.depth_side = -_overloaded_side
                # SLOT ALLOCATOR (2026-08-24): clear the stale role too.
                # The flipper crosses sides carrying its old depth_role;
                # the triggered re-bootstrap only assigns A/B/C/D to
                # None-role defenders, so a kept role could duplicate a
                # single-capacity slot (two A's — stacking) on the new
                # side or leave the vacated side with zero A's (gap).
                _flipper.depth_role = None
                _flipper._depth_seeded = False

        # STEP 4 — Update anchor every time ruck_x_m changes
        # TASK G ANCHOR AUTHORITY (Layer 6): the new ruck has just been
        # written, so the anchor lock is reset and the anchor recomputed
        # + locked from the NEW ruck (ruck_x + 10).  No subsystem may
        # modify it again this frame.
        reset_anchor_authority(self.world)
        compute_and_lock_anchor(self.world)

        # ── DEPTH-ROLE INVALIDATION ON NEW ANCHOR CONTEXT (2026-08-23) ──
        # The ruck was rewritten above (world.ruck_x_m = bc.x) and the
        # anchor authority has just recomputed + locked to the NEW ruck
        # (ruck_x + 10).  Only the anchor-derived role-depth X/Y are stale
        # for the NEW anchor — clear those; assign_depth_roles re-stamps
        # them from the CURRENT ruck next frame.
        # RELEASE AUTHORITY GATE (2026-08-30): the pre-tackle width-band
        # (depth_role A/B/C/D) is CARVED THROUGH the tackle finish.  Do NOT
        # wipe depth_role here — a defender that already holds a proper
        # line spot keeps it through RELEASE/PTB/POST_RUCK, and the clamp /
        # recovery writer may only move band-less defenders.  Wiping it
        # here made assign_depth_roles re-band the line from moving,
        # unsettled defenders every RELEASE frame (band collisions / width
        # collapse).  Side flips are handled by the POST-DRIVE SIDE
        # REBALANCE block above and the _flipped_in_release /
        # _force_innermost_seed path in _clear_tes_artefacts — both
        # deliberate, targeted re-seeds.
        for _d in self.world.defending_team:
            _d.role_depth_target_x = None
            _d.role_depth_target_y = None

        # -- GOVERNOR PER-SET RESET + RE-ANCHOR (2026-08-04) --
        # SPEC 2 / 5: on DOMINANCE -> RELEASE the new ruck's anchor is
        # known, so the line governor's smoothed state is cleared NOW and
        # re-seeded from the NEW anchor.  Previously the reset only ran on
        # the PTB->OPEN_PLAY entry in _set_phase(); clearing here means the
        # RELEASE/PTB-era retreat writers and the SDM all derive from the
        # new set's line state immediately.  The OPEN_PLAY-entry reset in
        # _set_phase() remains as an idempotent backstop.
        self.def_behaviour.line_governor.reset()
        self.def_behaviour.line_governor.set_anchor(
            self.world.defensive_line_anchor
        )

        # Assign DH from attacking team (closest to ruck who is NOT the BC).
        # DH SELECTION STABILITY (2026-09-16): keep the body chosen at tackle
        # entry by _assign_dh_for_tackle() whenever it is still a valid non-BC
        # attacker.  Re-running _find_dh() here can pick a DIFFERENT (and
        # potentially far farther) body than the one that has spent the whole
        # tackle walking toward the ruck, discarding that approach -- measured
        # as a 25.5 m / 6.40 s RELEASE in the release-gate tail probe.
        _kept_dh = self.dh
        if (_kept_dh is None or _kept_dh is bc
                or _kept_dh not in self.world.attacking_team):
            self.dh = self._find_dh(self.world.attacking_team, bc.x, bc.y, bc)

        if self.dh is not None:
            self.dh.defender_role = DefenderRole.DUMMY_HALF
            # Frozen state managed by apply_phase_invariants() Ã¢â‚¬â€ not set here
            self.dh.tackle_locked = False

        # P13: Clear OPE tackler references so they don't leak into next cycle.
        # Markers stay frozen via _update_defence() logic, not via
        # self.primary/joiners referencing.
        self.joiners.clear()
        self.primary = None

    def _reassert_fullback(self, dt: float) -> None:
        """Re-assert the deep backfield anchor for the fullback actor in ANY
        non-OPEN_PLAY phase (RELEASE / PTB_PREP / PTB_EXEC / PTB /
        POST_RUCK_OPEN_PLAY).

        The unified defensive pipeline (``def_behaviour.apply``) only runs
        during OPEN_PLAY, so the FB geometry authority
        (``apply_fullback_geometry``) does NOT fire in those phases.  Without
        this, once RELEASE entry stopped clearing ``fullback_role_active`` the
        actor would be re-stamped with a depth role by the un-gated preamble
        (its exclusion flag is now True, so it is excluded) and left on its
        last line target â€” visibly joining the line through the PTB era.

        This method is the same-slot re-assert used by the OPEN_PLAY pipeline:
          - ``apply_fullback_geometry`` writes the stable deep anchor, DRIFT,
            unfreeze, approach speed (self-gated on fullback_role_active).
          - ``actor.update(dt)`` integrates the anchor so the actor glides to
            its backfield slot every phase frame (PTB-era speed caps apply
            via Player's own update path).
        """
        self.defence_manager.reassert_fullback(self, dt)

    @staticmethod
    def _find_dh(team: list, ruck_x: float, ruck_y: float, bc: Player) -> Player | None:
        """Return the closest player to the ruck who is NOT the BC (Dummy Half)."""
        candidates = [p for p in team if p is not bc]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda p: (p.x - ruck_x) ** 2 + (p.y - ruck_y) ** 2,
        )

    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
    # D1/D2 Ã¢â‚¬â€ Marker Peel Alignment
    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

    def _trigger_marker_peel(self) -> None:
        """Unfreeze markers and tacklers at PTB completion.

        Called when PTBManager.state == "complete", signalling that the
        ball has been transferred from the tackled BC to the DH.

        Clears MARKER_A, MARKER_B, TACKLER_1, and TACKLER_2 roles:
          - Role set to None so the defence system reclassifies them
          - frozen set to False so they can move into the line
          - suppressed cleared (attribute may not exist Ã¢â‚¬â€ hasattr guard)
          - marker_offside cleared

        D1: Trigger marker peel when PTB completes.
        D2: Also clear TACKLER_1 and TACKLER_2 roles (tacklers already
            released; this is belt-and-suspenders).
        """
        # NOTE: marker_freeze_timer and p.frozen are NOT cleared here.
        # apply_phase_invariants() is the sole freeze authority.
        # The per-player freeze timer is managed by _update_post_ruck_open_play().
        for p in self.world.defending_team:
            if p.defender_role in (
                DefenderRole.MARKER_A,
                DefenderRole.MARKER_B,
                DefenderRole.TACKLER_1,
                DefenderRole.TACKLER_2,
            ):
                p.defender_role = None
                if hasattr(p, "suppressed"):
                    p.suppressed = False

    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
    # P1 Ã¢â‚¬â€ PTB_PREP Gate: DH readiness check
    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

    def _check_dh_ready_for_ptb(self, for_exec: bool = False) -> bool:
        """Check whether the DH is fully positioned and stable for PTB.

        Used to gate RELEASE Ã¢â€ â€™ PTB_PREP and PTB_PREP Ã¢â€ â€™ PTB_EXEC transitions.
        All conditions must pass before PTB can begin.

        Args:
            for_exec: If True, applies stricter velocity threshold (PTB_EXEC gate).

        Returns:
            True if DH is ready for PTB, False otherwise.
        """
        dh = self.dh
        bc = self.world.ball_carrier
        phase = self._phase

        # 1. DH must exist
        if dh is None:
            if self.world.frame_count % 30 == 0:
                if DEBUG_OPE_TRACE:
                    print(f"[PTB_GATE] {phase}: DH is None Ã¢â‚¬â€ blocking")
            return False

        # 2. BC must exist
        if bc is None:
            if self.world.frame_count % 30 == 0:
                if DEBUG_OPE_TRACE:
                    print(f"[PTB_GATE] {phase}: BC is None Ã¢â‚¬â€ blocking")
            return False

        ruck_y = getattr(self.world, "ruck_y_m", bc.y)

        # 3. DH must be behind BC (allow slight forward offset with hysteresis)
        min_behind_x = bc.x + 0.3  # allow DH slightly ahead
        if dh.x >= min_behind_x:
            if self.world.frame_count % 30 == 0:
                if DEBUG_OPE_TRACE:
                    print(f"[PTB_GATE] {phase}: DH x={dh.x:.2f} >= bc.x+0.3={min_behind_x:.2f} Ã¢â‚¬â€ blocking")
            return False

        # 4. DH must be within 1m Ã‚Â± tolerance behind BC (relaxed tolerance)
        target_x = bc.x - DH_BEHIND_DISTANCE_M
        pos_tolerance = DH_POSITION_TOLERANCE_M + 0.1  # relaxed to 0.4m
        if abs(dh.x - target_x) > pos_tolerance:
            if self.world.frame_count % 30 == 0:
                if DEBUG_OPE_TRACE:
                    print(f"[PTB_GATE] {phase}: DH dist to 1m target={abs(dh.x - target_x):.2f} > {pos_tolerance:.2f} Ã¢â‚¬â€ blocking")
            return False

        # 5. DH lateral offset from ruck_y (relaxed tolerance)
        lateral_tolerance = DH_LATERAL_TOLERANCE_M + 0.3  # relaxed to 0.6m
        if abs(dh.y - ruck_y) > lateral_tolerance:
            if self.world.frame_count % 30 == 0:
                if DEBUG_OPE_TRACE:
                    print(f"[PTB_GATE] {phase}: DH lateral offset={abs(dh.y - ruck_y):.2f} > {lateral_tolerance:.2f} Ã¢â‚¬â€ blocking")
            return False

        # 6. DH velocity magnitude must be below stability threshold
        vel_threshold = (DH_VELOCITY_STABLE_EXEC_THRESHOLD_MPS
                         if for_exec
                         else DH_VELOCITY_STABLE_THRESHOLD_MPS)
        dh_speed = math.hypot(dh.vx, dh.vy)
        if dh_speed > vel_threshold:
            if self.world.frame_count % 30 == 0:
                if DEBUG_OPE_TRACE:
                    print(f"[PTB_GATE] {phase}: DH speed={dh_speed:.2f} > {vel_threshold:.2f} Ã¢â‚¬â€ blocking")
            return False

        # 7. DH must not be frozen
        if dh.frozen:
            if self.world.frame_count % 30 == 0:
                if DEBUG_OPE_TRACE:
                    print(f"[PTB_GATE] {phase}: DH frozen=True Ã¢â‚¬â€ blocking")
            return False

        # 8. DH must not be a tackler or marker
        if dh.defender_role in (
            DefenderRole.TACKLER_1,
            DefenderRole.TACKLER_2,
            DefenderRole.TACKLER_3,
            DefenderRole.MARKER_A,
            DefenderRole.MARKER_B,
        ):
            if self.world.frame_count % 30 == 0:
                if DEBUG_OPE_TRACE:
                    print(f"[PTB_GATE] {phase}: DH role={dh.defender_role} Ã¢â‚¬â€ blocking")
            return False

        return True

    # -- RELEASE --------------------------------------------------------

    def _reassert_release_stand_up_target(self, bc: Player) -> None:
        """Clamp the carrier's RELEASE stand-up target into the field.

        RELEASE's stand-up target is::

            ruck_x + recovery_dir * RECOVERY_BACKWARD_DISTANCE_M

        The ruck anchor is the DRIVE END x, and the BC's own field clamp is
        deliberately SKIPPED while he is being driven (so a clamp can never
        cancel boosted drive metres).  The anchor can therefore sit past
        ``FIELD_MAX_X``, which projects the stand-up target past the dead-ball
        line -- AHEAD of a carrier the clamp holds at the line, and inside the
        zone ``enforce_bc_backward_invariant`` forbids him to enter.  He can
        never reach the target he was given, so he sits motionless with
        ``frozen`` False until the RELEASE watchdog expires: the live "carrier
        stops moving after the tackle and it is NOT a freeze" symptom.

        Clamping the target here keeps every RELEASE mover inside the field.
        The value is IDEMPOTENT for the phase (``ruck_x_m`` does not move
        during RELEASE), so writing it every frame matches the entry-time
        write exactly and leaves the normal path unchanged.
        """
        ruck_x = self.world.ruck_x_m if self.world.ruck_x_m is not None else bc.x
        recovery_dir = -self.world.attacking_direction
        stand_up_x = ruck_x + recovery_dir * RECOVERY_BACKWARD_DISTANCE_M
        bc.target_x = max(FIELD_MIN_X, min(FIELD_MAX_X, stand_up_x))

    def _update_release(self, dt: float) -> None:
        """
        Release phase: PTB wind-up timer.

        TES is fully shut down -- no containment, no retreat, no tackle freeze.
        DH is unfrozen with DefenderRole.DUMMY_HALF.

        Engine-Spec X-Axis Marker Alignment:
          - Markers unfreeze immediately on RELEASE entry.
          - They begin moving toward their X-axis positions (ruck_x + 1, ruck_y)
            for Marker A and (ruck_x + 2, ruck_y) for Marker B.
          - TES/PTBManager do not control marker movement.
        """
        # -- TES FREEZE-MASK (audit quieting) --
        # Same contract as TACKLE_DRIVE: during RELEASE every player is
        # treated as FROZEN for AUDIT PURPOSES ONLY, even when their real
        # frozen flag is False (DEF_RETREAT_10 / MARKERs are unfrozen but
        # idle by design at this point).  The 10-frame rolling window mixes
        # pre-tackle OPEN_PLAY retreat targets with RELEASE targets, which
        # falsely triggers STUCK_NOT_FROZEN / VX_ZERO_JIGGLE / TARGET_JIGGLE.
        # This suppression is audit-only â€” it does NOT modify any player or
        # world frozen flag, nor any physics or gameplay state.
        # FX-3: RELEASE movers (markers, DEF_RETREAT_10 retreat writers,
        # BC recovery) are active — only genuinely-frozen stay masked.
        self._stamp_tes_freeze_mask(False)

        bc = self.world.ball_carrier
        if bc is not None:
            bc.attack_role = "ball_carrier"
            # -- PER-FRAME STAND-UP TARGET RE-ASSERT (RELEASE deadlock fix) --
            # The entry teardown below only runs while `_just_entered_release`
            # is armed.  The DRIVE stamps `bc.target_x = drive_end_x` as its
            # last act, and that value is NOT field-clamped, so a RELEASE that
            # starts with the flag already cleared would otherwise carry the
            # drive-end value -- possibly past FIELD_MAX_X -- as the carrier's
            # stand-up target for the whole phase.  Re-asserting RELEASE's own
            # (clamped) target every frame removes that possibility; because
            # the value is idempotent this is a no-op on the normal path.
            self._reassert_release_stand_up_target(bc)
        if DEBUG_OPE_TRACE:
            print("_update_release FIRING phase=", self._phase, "timer=", self._phase_timer)

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â DETACH ALL TACKLERS IMMEDIATELY ON RELEASE ENTRY Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        # BC must be freed from all tackle constraints before timer can
        # advance, markers can move, or any PTB transition can occur.
        if self._just_entered_release:
            # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â WIPE ALL TES ARTEFACTS FIRST Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
            # This clears movement targets, freeze flags, soft-freeze counters,
            # retreat targets, defensive line anchors, and tes_active Ã¢â‚¬â€ ensuring
            # RELEASE starts with a completely clean slate.
            self._clear_tes_artefacts()

            # PHASE 3.6 â€” DETACH ALL ATTACHED TACKLERS at RELEASE entry.
            # Clears per-player tes_attached/attach_slot, clears all attach
            # offsets, the selected tacklers list, drive-strength totals, and
            # disarms the attach gate.  Tacklers freeze for the RELEASE
            # duration via apply_phase_invariants() (TACKLER_* in
            # MOVEMENT_RULES["RELEASE"].must_be_frozen).
            self._detach_all_tacklers()


            bc = self.ball_carrier
            if bc is not None:
                bc.in_contact = False
                bc.tackle_locked = False
                bc.tes_locked = False
                bc.apply_physics_forces = False
                # Clear any physics constraint/force flags that may exist
                if hasattr(bc, "tackle_constraints_active"):
                    bc.tackle_constraints_active = False
                if hasattr(bc, "tackle_forces_active"):
                    bc.tackle_forces_active = False
                # Clear velocity from tackle to allow standing up
                bc.vx = 0.0
                bc.vy = 0.0
                # BC recovery: stand up and move backward from the ruck.
                # The stand-up TARGET is owned by
                # _reassert_release_stand_up_target() -- one authority, one
                # field clamp (see that method for the full trace).
                ruck_x = self.world.ruck_x_m if self.world.ruck_x_m is not None else bc.x
                ruck_y = self.world.ruck_y_m if self.world.ruck_y_m is not None else bc.y
                recovery_dir = -self.world.attacking_direction  # opposite of attack direction
                self._reassert_release_stand_up_target(bc)
                bc.target_y = ruck_y
                bc.move_speed = RELEASE_RECOVERY_SPEED_MPS      # e.g. 1.5 m/s
            # Clean sweep: every defender loses ALL tackle flags
            # CRITICAL: Do NOT clear apply_physics_forces here Ã¢â‚¬â€ that would
            # disable normal movement for ALL defenders (including markers)
            # for the entire RELEASE phase until _reset_tackle_state() runs.
            for p in self.world.defending_team:
                p.is_tackler = False
                p.in_contact = False
                p.tackle_locked = False
                p.tes_locked = False
                # apply_physics_forces intentionally NOT touched Ã¢â‚¬â€ must stay True
                # p.frozen NOT set here Ã¢â‚¬â€ apply_phase_invariants() is the sole freeze authority
            self._just_entered_release = False
            if DEBUG_OPE_TRACE:
                print(f"[DETACH] Clean sweep Ã¢â‚¬â€ all defenders detached at RELEASE entry")
            # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â Per-frame enforcement: clear any lingering tackle flags during RELEASE Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
            # Some subsystems (TES engagement, marker logic) may re-set tackler flags
            # even after the first-frame detach.  Enforce cleared state every frame.
            # CRITICAL: Do NOT set p.frozen = False here Ã¢â‚¬â€ the RELEASE freeze controller
            # (ai_tes_controller with phase="RELEASE" -> _enforce_release_freeze) handles
            # freeze state correctly for each role.  Overriding freeze here would allow
            # non-mover roles (DEF_LINE_READY, DEF_CHASE, ATT_SUPPORT) to move during RELEASE.
            for p in self.world.defending_team:
                if p.is_tackler or p.in_contact or p.tackle_locked or p.tes_locked:
                    p.is_tackler = False
                    p.in_contact = False
                    p.tackle_locked = False
                    p.tes_locked = False
                    # apply_physics_forces intentionally NOT touched Ã¢â‚¬â€ must stay True
                    # so markers and all defenders can move during RELEASE
                    # p.frozen intentionally NOT set here Ã¢â‚¬â€ freeze controlled by
                    # ai_tes_controller("RELEASE") at end of _update_release()

        self._phase_timer += dt

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â RELEASE Protection Gate Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        # Hard minimum-duration wall: markers need time to reach X-axis
        # targets (ruck_x+1 for MARKER_A, ruck_x+2 for MARKER_B).
        # Block ALL transitions (convergence exit, PTB_PREP) until the
        # convergence floor has elapsed (RELEASE_MIN_CONVERGENCE_S).
        if self._phase_timer < RELEASE_MIN_CONVERGENCE_S:
            # Markers still moving Ã¢â‚¬â€ no transitions allowed
            pass
        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

        # RELEASE WATCHDOG: force-transition when the bounded window has
        # elapsed without the convergence exit firing.
        if self._phase_timer < RELEASE_MIN_CONVERGENCE_S:
            # Block override Ã¢â‚¬â€ protection gate still active
            pass
        else:
            if self._ptb_gate_override_frames >= RELEASE_WATCHDOG_FRAMES:
                self._ptb_gate_override_frames = RELEASE_WATCHDOG_FRAMES
            self._ptb_gate_override_frames -= 1
            if self._ptb_gate_override_frames <= 0:
                if DEBUG_OPE_TRACE:
                    print(f"[PTB_OVERRIDE] RELEASE stuck Ã¢â‚¬â€ forcing PTB_PREP")
                self._enter_ptb_prep()
                return

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â Engine-Spec: Move markers toward X-axis targets each frame Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        self._move_markers_to_targets(dt)
        for _m in self._get_markers():
            _m.update(dt)

        # Unfreeze BC and apply RELEASE movement toward ruck target
        bc = self.ball_carrier
        if bc is not None:
            # BC already has target_x = ruck_x, target_y = ruck_y, move_speed = RELEASE_RECOVERY_SPEED_MPS
            # set during detachment on frame 1. Apply movement every frame.
            if not bc.frozen:
                bc.update(dt)
                # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â SDEA BC backward invariant Ã¢â‚¬â€ no forward movement during RELEASE Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
                enforce_bc_backward_invariant(bc, self.world)

        # Scan entire defending team for any remaining tackler flags
        # (self.primary and self.joiners are already cleared by _prepare_release)
        # CRITICAL: Do NOT set frozen=False here Ã¢â‚¬â€ the RELEASE freeze controller
        # (ai_tes_controller with phase="RELEASE" -> _enforce_release_freeze) handles
        # freeze state correctly for each role per the compliance checklist.
        for tackler in self.world.defending_team:
            if tackler.is_tackler or tackler.in_contact or tackler.tes_locked or tackler.tackle_locked:
                tackler.is_tackler = False
                tackler.in_contact = False
                tackler.tackle_locked = False
                tackler.tes_locked = False

        # P7: DH approach to 1m behind the BC during RELEASE.
        #
        # SHARED SPEED POLICY (2026-09-17): this used to hard-code 4.0 m/s,
        # which is HALF the TES preposition sprint cap.  A DH that was still
        # 7 m out when RELEASE began therefore needed 1.75 s of travel plus
        # a decay tail -- beyond the 2.00 s RELEASE watchdog, so it exited
        # unconverged and dragged PTB_PREP to its own cap.  It now consumes
        # the same gap-graded policy as _drive_dh_preposition, so the
        # approach finishes at the same rate it would have during the
        # tackle and the taper guarantees a gate-passing arrival speed.
        if self.dh is not None and bc is not None:
            from game.tes.ptb_manager import _DH_BEHIND_DISTANCE_M
            dh_target_x = bc.x - _DH_BEHIND_DISTANCE_M
            dh_target_y = self.world.ruck_y_m
            self.dh.target_x = dh_target_x
            self.dh.target_y = dh_target_y
            _dh_gap = math.hypot(
                dh_target_x - self.dh.x, dh_target_y - self.dh.y
            )
            self.dh.move_speed = self._dh_approach_speed(_dh_gap)
            stamp_target_write(
                self.world, self.dh, DH_PREPOSITION_TARGET_SOURCE
            )
            self.dh.update(dt)

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â Clear tes_active at end of RELEASE (Section B) Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        self.world.tes_active = False
        if hasattr(self.world, "tackler_ids"):
            self.world.tackler_ids.clear()
        # P7: Continuous retreat for DEF_RETREAT_10 during RELEASE
        # TASK VI: the retreat anchor consumes the ANCHOR AUTHORITY
        # (ruck_x + 10), never a bare ruck read or inline derivation.
        # TASK: route spacing lanes (Task F spacing Y, RELEASE retreat)
        # through the unified forced-retreat writer (sole retreat
        # authority) instead of the inline bypass.
        # DRIVE-RELEASE RETREAT (2026-08-30): the full forced-retreat
        # writer (force_anchor_x=True) owns the line the instant DRIVE
        # concludes — it drops TES isolation and retreats to ruck_x + 10
        # immediately, without waiting for DH arrival or PTB.  The TES
        # support fold-back (tes_retreat_priority=True) is no longer used
        # here; the line retreats at full retreat speed.
        _retreat_defs = [
            _d for _d in self.world.defending_team
            if _d.defender_role == DefenderRole.DEF_RETREAT_10
            and not getattr(_d, "fullback_role_active", False)
        ]
        if _retreat_defs:
            _, _non_compliant = self.defence_manager.apply_forced_retreat(
                self.world, _retreat_defs, governor=None,
                tes_retreat_priority=False,
                force_anchor_x=True,
            )
            for _d in _non_compliant:
                _d.update(dt)  # P4: integrate retreat movement

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â RELEASE FREEZE ENFORCEMENT Ã¢â‚¬â€ delegated to unified controller Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        ai_tes_controller(self.world, self.dh, "RELEASE")

        # FULLBACK RE-ASSERT (2026-08-13): re-write the deep backfield anchor
        # and integrate the actor THIS frame so RELEASE never leaves it on a
        # line/retreat target.  Runs after every RELEASE writer (BC, DH,
        # markers, P7 retreat, freeze controller).
        self._reassert_fullback(dt)

        # ── FULLBACK-INVARIANT AUDIT AT RELEASE (2026-08-27) ──
        # The actor must survive RELEASE as a deep, unfrozen, geometry-owned
        # anchor (depth_role None, frozen False, target_source
        # "fullback_geometry").  This phase is a high-risk site for line-role
        # leakage, freeze artefacts, or target-source overrides due to TES
        # wipes and P7 retreat/marker/DH writers.  Runs AFTER
        # _reassert_fullback so the FINAL frame state (not a mid-write state)
        # is captured.  Observational only -- never modifies state.
        audit_fullback_invariants(self.world)

        # Wait for PTB time (from tes_outcome) AND DH readiness before transitioning
        # Minimum 1.54s ensures markers have time to reach X-axis targets:
        #   MARKER_A needs 0.94s to reach ruck_x + 1.0
        #   MARKER_B needs 1.44s to reach ruck_x + 2.0
        #   + 0.10s buffer = 1.54s
        # ── CONVERGENCE-FIRST EXIT (2026-09-17) ──
        # RELEASE ends when every mover it owns has CONVERGED: the DH
        # (positioned, stable, behind the BC), the marker pair (on their
        # ruck-pin targets) and the recovering ball carrier.  The verdict,
        # its tolerances and the window bounds are owned by
        # world/rules/release_gate.py; this handler only consumes it.
        #
        # `ptb_t` is the threshold for the exit test below: 0.0 when
        # converged (so `_phase_timer >= ptb_t` holds and the existing DH
        # re-check + _enter_ptb_prep() run), +inf otherwise (so the test
        # can never pass and the phase keeps working).  The
        # RELEASE_WATCHDOG_FRAMES countdown above is the only other exit.
        #
        # tes_outcome.ptb_time_s is deliberately NO LONGER consumed as the
        # gate: it is a uniform 2.2-3.2 s WEAK draw against a 0.10 s
        # readiness requirement, i.e. pure dead clock.  The draw is still
        # computed (compute_ptb_time) so the engine's per-tackle RNG
        # sequence — and therefore every seeded rollout — is unchanged.
        _release_dh_ready = (
            self.dh is None or bc is None
            or self._check_dh_ready_for_ptb(for_exec=False)
        )
        _release_report = release_convergence(
            self, self.world, _release_dh_ready
        )
        if DEBUG_OPE_TRACE and not _release_report["all_converged"]:
            from world.rules.release_gate import format_convergence_report
            print("[RELEASE_CONVERGENCE] t=%.2f %s"
                  % (self._phase_timer, format_convergence_report(_release_report)))
        ptb_t = 0.0 if _release_report["all_converged"] else float("inf")
        if self._phase_timer >= ptb_t:
            # P1: Gate PTB_PREP entry on DH readiness Ã¢â‚¬â€ DH must be positioned,
            # stable, and behind BC before PTB can begin.
            if self.dh is not None and bc is not None:
                if self._check_dh_ready_for_ptb(for_exec=False):
                    self._enter_ptb_prep()
                else:
                    pass
            else:
                self._enter_ptb_prep()

    def _enter_ptb_prep(self) -> None:
        """RELEASE -> PTB_PREP: hand control of DH to PTBManager.

        CRITICAL: PTB_PREP still needs the tackled BC's position (bc.x)
        for PTBManager to compute DH targets (bc.x - 1.0).  The old BC
        is NOT cleared here Ã¢â‚¬â€ it stays valid until PTB_EXEC->PTB handover
        where set_ball_carrier(self.world, self.dh) transfers ownership.
        """
        bc = self.world.ball_carrier

        # ── DH RESCUE (2026-09-17) ──
        # The convergence exit normally guarantees a positioned DH, but the
        # RELEASE watchdog can force through with none selected.  Re-select
        # one here rather than falling into _update_ptb_prep's "no DH" abort,
        # which abandoned the ruck entirely.
        if self.dh is None or self.dh is bc:
            self.dh = self._find_dh(
                getattr(self.world, "attacking_team", []) or [],
                self._get_ruck_x(),
                self._get_ruck_y(),
                bc,
            )
            if self.dh is not None:
                self.dh.defender_role = DefenderRole.DUMMY_HALF
                self.dh.attack_role = "dummy_half"
                self.dh.frozen = False
                self.dh.tackle_locked = False

        # ── CYCLE-ENTRY STATE RESET (2026-09-17) ──
        # The manager's state machine MUST begin every tackle cycle from
        # idle.  start_prep() used to be gated on `state == "idle"`, but
        # nothing reset the state once a PTB completed, so from the second
        # tackle onward that guard silently SKIPPED the call: PTB_PREP
        # inherited a stale "complete" state plus a leftover 0.15 s timer
        # and its designed >= 0.18 s window collapsed to ~0.03 s.
        # Reset first, then ALWAYS start prep, so an inherited state can
        # never be read.
        self.ptb_manager.reset()
        if self.dh is not None:
            self.ptb_manager.start_prep(self.dh, bc, self.world.ruck_y_m)
        self._set_phase("PTB_PREP", GameSubState.PTB, "RELEASE->PTB_PREP")

    # -- PTB_PREP -------------------------------------------------------

    def _update_ptb_prep(self, dt: float) -> None:
        """
        PTB_PREP: DH moves to behind-ball position via PTBManager.

        CRITICAL: No dh_controller.move_to_ruck() here.
        No geometry/containment/retreat touches DH.
        PTBManager is the sole authority for DH movement.

        Engine-Spec X-Axis: Markers continue moving to X-axis targets.
        They are NOT frozen and do NOT use Y-offsets.
        """
        bc = self.world.ball_carrier
        if bc is not None:
            bc.attack_role = "ball_carrier"
        # PTB_PREP WATCHDOG (2026-09-17): bounded window, not the old
        # 300-frame (5.0 s) countdown.  The gate normally resolves in
        # ~0.2 s; this only fires on a genuine outlier.
        if self._ptb_gate_override_frames >= PTB_PREP_WATCHDOG_FRAMES:
            self._ptb_gate_override_frames = PTB_PREP_WATCHDOG_FRAMES
        self._ptb_gate_override_frames -= 1
        if self._ptb_gate_override_frames <= 0:
            if DEBUG_OPE_TRACE:
                print(f"[PTB_OVERRIDE] PTB_PREP stuck Ã¢â‚¬â€ forcing PTB_EXEC")
            if self.dh is not None and self.ball_carrier is not None:
                self.ptb_manager.start_exec(self.dh, self.ball_carrier,
                                            getattr(self.world, "ruck_y_m", 0.0))
            self._set_phase("PTB_EXEC", GameSubState.PTB, "PTB_PREP_OVERRIDE->PTB_EXEC")
            return

        if self.dh is None:
            self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "PTB_PREP_NO_DH")
            return

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â Engine-Spec: Move markers toward X-axis targets each frame Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        self._move_markers_to_targets(dt)
        # Ensure markers actually move this frame Ã¢â‚¬â€ their movement runs here
        # because _update_defence() returns early during PTB sub-state.
        for _m in self._get_markers():
            _m.update(dt)

        # PTBManager drives DH movement -- NO other system touches DH here
        bc = self.world.ball_carrier
        ready = self.ptb_manager.update_prep(
            dt,
            self.dh,
            bc,
            self.world.ruck_y_m,
        )

        # Freeze managed by apply_phase_invariants() Ã¢â‚¬â€ not set here

        # Engine-Spec: REPLACED old P16 block that froze markers at Y-offsets.
        # Markers are now moved by _move_markers_to_targets() above.
        # No freeze or Y-offset assignment here.

        # P7: Continuous retreat for DEF_RETREAT_10 during PTB_PREP
        # FULLBACK EXCLUSION (2026-08-13): the fullback never retreats with
        # the line — its deep backfield anchor is re-asserted below.
        # TASK: route spacing lanes through the unified forced-retreat
        # writer (sole retreat authority) instead of the inline bypass.
        # TES-priority weighting makes the TES retreat target win when a
        # tackle-escape is active (RELEASE/PTB).
        _retreat_defs = [
            _d for _d in self.world.defending_team
            if _d.defender_role == DefenderRole.DEF_RETREAT_10
            and not getattr(_d, "fullback_role_active", False)
        ]
        if _retreat_defs:
            _, _non_compliant = self.defence_manager.apply_forced_retreat(
                self.world, _retreat_defs, governor=None,
                tes_retreat_priority=False,
                force_anchor_x=True,
            )
            for _d in _non_compliant:
                _d.update(dt)  # P4: integrate retreat movement

        # P3: Gate PTB_PREP -> PTB_EXEC on OPE-side DH readiness checks
        if ready and self._check_dh_ready_for_ptb(for_exec=False):
            self.ptb_manager.start_exec(
                self.dh,
                self.world.ball_carrier,
                self.world.ruck_y_m,
            )
            self._set_phase("PTB_EXEC", GameSubState.PTB, "PTB_PREP->PTB_EXEC")

        # FULLBACK RE-ASSERT (2026-08-13): PTB_PREP writers (markers, DH,
        # P7 retreat) ran this frame â€” re-assert the deep backfield anchor
        # LAST so the fullback is never left on a line/retreat target.
        self._reassert_fullback(dt)

        # ── FULLBACK-INVARIANT AUDIT AT PTB_PREP (2026-08-27) ──
        # The actor must survive PTB_PREP as a deep, unfrozen, geometry-owned
        # anchor (depth_role None, frozen False, target_source
        # "fullback_geometry").  This phase is a high-risk site for line-role
        # leakage, freeze artefacts, or target-source overrides due to TES
        # wipes and P7 retreat/marker/DH writers.  Runs AFTER
        # _reassert_fullback so the FINAL frame state (not a mid-write state)
        # is captured.  Observational only -- never modifies state.
        audit_fullback_invariants(self.world)

    # -- PTB_EXEC -------------------------------------------------------

    def _update_ptb_exec(self, dt: float) -> None:
        """
        PTB_EXEC: DH moves to scoop position via PTBManager.

        CRITICAL: No dh_controller.move_to_ruck() here.
        PTBManager is the sole authority for DH movement.

        Engine-Spec X-Axis: Markers continue moving to X-axis targets.
        At PTB handover (PTB_EXEC -> PTB), offside check runs.
        """
        bc = self.world.ball_carrier
        if bc is not None:
            bc.attack_role = "ball_carrier"
        # PTB_EXEC WATCHDOG (2026-09-17): bounded window, not the old
        # 300-frame (5.0 s) countdown.
        if self._ptb_gate_override_frames >= PTB_EXEC_WATCHDOG_FRAMES:
            self._ptb_gate_override_frames = PTB_EXEC_WATCHDOG_FRAMES
        self._ptb_gate_override_frames -= 1
        if self._ptb_gate_override_frames <= 0:
            if DEBUG_OPE_TRACE:
                print(f"[PTB_OVERRIDE] PTB_EXEC stuck â€” forcing PTB")
            if self.dh is not None and self.ball_carrier is not None:
                self.ptb_manager.complete(self.dh, self.ball_carrier)
            # Post-PTB scoot: reset marker lock + frame counter
            self._on_ptb_complete()
            # NO OFFSIDE ADJUDICATION HERE (2026-09-17).
            # This line called self._check_marker_offside(), which is
            # defined NOWHERE in the codebase -- a dangling reference left
            # over from the pre-X-Axis design.  It was unreachable for
            # months because the force-transition countdown was 300 frames
            # (5.0 s) while the gate always resolved in ~0.15 s; giving
            # PTB_EXEC a real 36-frame bound (PTB_EXEC_WATCHDOG_FRAMES) made
            # the branch reachable and it raised AttributeError, aborting
            # the entire rollout mid-match.
            # Marker "offside" state (Player.marker_offside /
            # marker_offside_retreat_x) is owned by the marker authority
            # and the PTB handler, and this engine's own contract is
            # "No offside. No retreat." -- the PTB_EXEC phase must not
            # adjudicate one.
            self._set_phase("PTB", GameSubState.PTB, "PTB_EXEC_OVERRIDE->PTB")
            return

        if self.dh is None:
            self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "PTB_EXEC_NO_DH")
            return

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â Engine-Spec: Move markers toward X-axis targets each frame Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        self._move_markers_to_targets(dt)
        # Ensure markers actually move this frame Ã¢â‚¬â€ _update_defence() is skipped
        # during PTB sub-state.
        for _m in self._get_markers():
            _m.update(dt)

        # PTBManager drives DH movement (targets bc.x - 1.0 each frame)
        bc = self.world.ball_carrier
        ready = self.ptb_manager.update_exec(
            dt,
            self.dh,
            bc,
            self.world.ruck_y_m,
        )

        # P7: Continuous retreat for DEF_RETREAT_10 during PTB_EXEC
        # FULLBACK EXCLUSION (2026-08-13): the fullback never retreats with
        # the line — its deep backfield anchor is re-asserted below.
        # TASK: route spacing lanes through the unified forced-retreat
        # writer (sole retreat authority) instead of the inline bypass.
        # TES-priority weighting makes the TES retreat target win when a
        # tackle-escape is active (RELEASE/PTB).
        _retreat_defs = [
            _d for _d in self.world.defending_team
            if _d.defender_role == DefenderRole.DEF_RETREAT_10
            and not getattr(_d, "fullback_role_active", False)
        ]
        if _retreat_defs:
            _, _non_compliant = self.defence_manager.apply_forced_retreat(
                self.world, _retreat_defs, governor=None,
                tes_retreat_priority=False,
                force_anchor_x=True,
            )
            for _d in _non_compliant:
                _d.update(dt)  # P4: integrate retreat movement

        # Freeze managed by apply_phase_invariants() Ã¢â‚¬â€ not set here

        # P6/P8: Gate PTB_EXEC -> PTB on DH readiness with exec-level checks
        dh_ready = self._check_dh_ready_for_ptb(for_exec=True)

        if ready and dh_ready:
            # PTB_EXEC -> PTB: ball transfer
            bc = self.ball_carrier
            self.ptb_manager.complete(self.dh, bc)

            # Markers frozen for MARKER_FREEZE_DURATION_S after PTB handover
            for _m in self._get_markers():
                _m.frozen = True
                _m.marker_freeze_timer = MARKER_FREEZE_DURATION_S

            # Post-PTB scoot: reset marker lock + frame counter
            # NOTE: _on_ptb_complete() resets world.marker_freeze_timer
            # so applied_phase_invariants() can re-freeze markers during
            # the lock window.  Marker roles are NOT cleared here â€”
            # they survive through PTB and POST_RUCK_OPEN_PLAY so the
            # freeze system can find MARKER_A/B roles to freeze.
            self._on_ptb_complete()
            if self.ptb_manager.state == "complete":
                # D3/D4/D6: Defer D-series state writes through _update_defence()
                # to avoid calling defence subsystems outside _update_defence().
                # ANCHOR AUTHORITY (Task A-2): the pending anchor MUST be
                # ruck_x + 10 â€” NEVER dh.x/BC-derived.  dh.x tracks the ball
                # carrier and lags, so dh.x + 10 drifted the defensive line
                # behind BC and collapsed width (verify_anchor_integrity).
                # TASK G ANCHOR AUTHORITY (Layer 6): PTB complete is a legal
                # anchor reset point â€” the lock is dropped so the next
                # _update_defence() set_anchor () re-locks the fresh anchor
                # from the ruck (identical value here; the reset guarantees
                # the authority sequence every PTB cycle).
                reset_anchor_authority(self.world)
                # TASK VI: the PTB-complete pending anchor consumes the ANCHOR
                # AUTHORITY (ruck_x + 10), never _get_ruck_x() + 10.0.
                self.defence_manager.pending_anchor_x = _anchor_m(self.world)
                self.defence_manager.pending_retreat_reset = True
                self.defence_manager.pending_spacing_y = self.world.ruck_y_m

            # Update internal reference
            self.ball_carrier = self.dh

            # D5: BC facing + initial run vector (DH becomes new BC)
            new_bc = self.dh
            new_bc.facing = 0.0   # Attack direction (right, +X)
            new_bc.vx = 2.0
            new_bc.vy = 0.0

            # DH must start PTB from DH's actual physical position
            set_ball_carrier(self.world, self.dh)

            # Ensure DH is unfrozen and ready
            self.dh.freeze_timer = 0.0

            # DH must receive tuners + intent + perception
            if self.dh.tuners is None:
                self.dh.tuners = AttackTuners()

            # Re-initialise DH perception/intent
            dh_per = self.attack_perception.compute(self.dh)
            self.dh.intent = self.ball_carrier_intent.decide(self.dh, dh_per)

            # Transition to PTB phase
            self._set_phase("PTB", GameSubState.PTB, "PTB_EXEC->PTB")

        # FULLBACK RE-ASSERT (2026-08-13): PTB_EXEC writers (markers, DH,
        # P7 retreat) ran this frame â€” re-assert the deep backfield anchor
        # LAST so the fullback is never left on a line/retreat target.
        self._reassert_fullback(dt)

        # ── FULLBACK-INVARIANT AUDIT AT PTB_EXEC (2026-08-27) ──
        # The actor must survive PTB_EXEC as a deep, unfrozen, geometry-owned
        # anchor (depth_role None, frozen False, target_source
        # "fullback_geometry").  This phase is a high-risk site for line-role
        # leakage, freeze artefacts, or target-source overrides due to TES
        # wipes and P7 retreat/marker/DH writers.  Runs AFTER
        # _reassert_fullback so the FINAL frame state (not a mid-write state)
        # is captured.  Observational only -- never modifies state.
        audit_fullback_invariants(self.world)

    # -- PTB ------------------------------------------------------------

    def _update_ptb(self, dt: float) -> None:
        """
        PTB phase Ã¢â‚¬â€ dummy half runs with ball.
        Attack controller manages DH movement.

        Spec 6.3: DH is the ball carrier, defence is suppressed,
        markers peel.  DH runs, then PTB kicks.

        Engine-Spec X-Axis: Offside markers must be retreating to
        ruck_x + 10 to regain onside.  Onside markers continue to
        move to their X-axis targets (or peel if _trigger_marker_peel
        has already run).
        """
        bc = self.world.ball_carrier
        if bc is not None:
            bc.attack_role = "ball_carrier"
        dh = self.dh
        if dh is None:
            self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "PTB_NO_DH")
            return

        # P1: increment phase timer so timeout safety can fire
        self._phase_timer += dt

        # Engine-Spec: Move markers toward X-axis targets (if still MARKER_A/B)
        self._move_markers_to_targets(dt)
        # Ensure markers actually move this frame Ã¢â‚¬â€ _update_defence() is skipped
        # during PTB sub-state.
        for _m in self._get_markers():
            _m.update(dt)

        # P8: Gate attack_controller on ptb_manager.state == "complete"
        if dh.tuners is None:
            dh.tuners = AttackTuners()
        if self.ptb_manager.state == "complete":
            # Ball transferred â€” DH can now move under attack controller
            self.attack_controller.apply_vector(dh, self.world, dt)
            # CRITICAL: execute DH physics step to resolve target_x/y into movement.
            # Without this, apply_vector() sets targets but DH never moves.
            dh.update(dt)
        else:
            # Ball NOT yet transferred â€” suppress attack_controller's random vector.
            dh.vx = 0.0
            dh.vy = 0.0

        # P10: Runtime diagnostic Ã¢â‚¬â€ log DH distance to 1m target every frame
        if self.world.frame_count % 10 == 0:
            from game.tes.ptb_manager import _DH_BEHIND_DISTANCE_M
            actual_dist = dh.x - self.world.ruck_x_m
            if abs(actual_dist - _DH_BEHIND_DISTANCE_M) > 0.5:
                print(f"[DH_POS] DH #{dh.player_id} is {actual_dist:.2f}m behind ruck "
                      f"(target: {_DH_BEHIND_DISTANCE_M:.1f}m) phase={self._phase}")

        # P7: Continuous retreat for DEF_RETREAT_10 during PTB
        # FULLBACK EXCLUSION (2026-08-13): the fullback never retreats with
        # the line — its deep backfield anchor is re-asserted below.
        # TASK: route spacing lanes through the unified forced-retreat
        # writer (sole retreat authority) instead of the inline bypass.
        # TES-priority weighting makes the TES retreat target win when a
        # tackle-escape is active (RELEASE/PTB).
        _retreat_defs = [
            _d for _d in self.world.defending_team
            if _d.defender_role == DefenderRole.DEF_RETREAT_10
            and not getattr(_d, "fullback_role_active", False)
        ]
        if _retreat_defs:
            # INTEGRATION (2026-09-18): mirror _update_ptb_prep /
            # _update_ptb_exec -- the retreat writer only issues the order
            # (target_x + RETREAT + move_speed); the phase handler is
            # responsible for integrating the body.  The PTB handler used to
            # discard the returned non-compliant list, so the retreat line
            # was never advanced in this sub-phase: measured 0.272 m/s of a
            # commanded 7.055 m/s (3.9%) and 69.3% of its obligated frames
            # below half command.
            _, _non_compliant = self.defence_manager.apply_forced_retreat(
                self.world, _retreat_defs, governor=None,
                tes_retreat_priority=False,
                force_anchor_x=True,
            )
            for _d in _non_compliant:
                _d.update(dt)  # P4: integrate retreat movement

        # Spec 6.4: PTB completes when DH has been in PTB long enough.
        # STAGE 4B (2026-09-19): both bounds now come from
        # world/rules/exit_timing_budget.py instead of being re-created as
        # literals here.  The local names are kept so the exit tests below
        # are untouched (behaviour-neutral relocation).
        DUCKOUT_DISTANCE = _EXIT_PTB_DUCKOUT_DISTANCE_M
        PTB_TIMEOUT_S = _EXIT_PTB_TIMEOUT_S

        if self.ptb_manager.state == "complete":
            dx = abs(dh.x - self.world.ruck_x_m)
            dy = abs(dh.y - self.world.ruck_y_m)
            if dx > DUCKOUT_DISTANCE or dy > DUCKOUT_DISTANCE:
                # Engine-Spec: Freeze markers for MARKER_FREEZE_DURATION_S
                # on PTB completion (reduced 1s -> 0.3s, 2026-08-16).
                for _m in self._get_markers():
                    _m.frozen = True
                    _m.marker_freeze_timer = MARKER_FREEZE_DURATION_S
                self._set_phase("POST_RUCK_OPEN_PLAY",
                                GameSubState.POST_RUCK_OPEN_PLAY,
                                "PTB->POST_RUCK")
        else:
            pass

        # Timeout safety: force PTB completion after timeout
        if self._phase_timer >= PTB_TIMEOUT_S:
            # Post-PTB scoot: reset marker lock + frame counter
            self._on_ptb_complete()
            # Engine-Spec: Freeze markers for MARKER_FREEZE_DURATION_S
            # on PTB completion (reduced 1s -> 0.3s, 2026-08-16).
            for _m in self._get_markers():
                _m.frozen = True
                _m.marker_freeze_timer = MARKER_FREEZE_DURATION_S
            self._set_phase("POST_RUCK_OPEN_PLAY",
                            GameSubState.POST_RUCK_OPEN_PLAY,
                            "PTB->POST_RUCK (timeout)")

        # FULLBACK RE-ASSERT (2026-08-13): PTB writers (markers, DH,
        # P7 retreat) ran this frame â€” re-assert the deep backfield anchor
        # LAST so the fullback is never left on a line/retreat target.
        self._reassert_fullback(dt)

        # ── FULLBACK-INVARIANT AUDIT AT PTB (2026-08-27) ──
        # The actor must survive PTB as a deep, unfrozen, geometry-owned
        # anchor (depth_role None, frozen False, target_source
        # "fullback_geometry").  This phase is a high-risk site for line-role
        # leakage, freeze artefacts, or target-source overrides due to TES
        # wipes and P7 retreat/marker/DH writers.  Runs AFTER
        # _reassert_fullback so the FINAL frame state (not a mid-write state)
        # is captured.  Observational only -- never modifies state.
        audit_fullback_invariants(self.world)

    # -- POST_RUCK_OPEN_PLAY --------------------------------------------

    def _update_post_ruck_open_play(self, dt: float) -> None:
        """
        POST_RUCK_OPEN_PLAY: defenders retreat individually,
        transitioning to DEF_LINE_READY once compliant.

        BC continues to move under the attack controller every frame,
        exactly like PTB and OPEN_PLAY phases.  No freeze.

        Spec 9: Per-defender DEF_RETREAT_10->DEF_LINE_READY.

        Engine-Spec: Markers are frozen for 1s after PTB handover.
        Freeze timer counts down each frame.  When expired:
          - d.frozen = False
          - d.marker_freeze_timer = 0.0
          - d.defender_role = DEF_LINE_READY
          - d.apply_physics_forces = True (restore normal physics)

        No offside. No retreat. No defensive behaviour for markers.
        """
        self._phase_timer += dt
        ruck_x = self.world.ruck_x_m

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â BC continues to move under attack controller Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        bc = self.world.ball_carrier
        if bc is not None:
            self.attack_controller.apply_vector(bc, self.world, dt)

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â Engine-Spec: Marker freeze timer countdown Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        # Frozen markers: no movement, no physics forces, no collision.
        # When freeze expires: restore physics, collision, and transition to line.
        for d in self.world.defending_team:
            if d.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                d.marker_freeze_timer -= dt
                if d.marker_freeze_timer > 0.0:
                    # Active freeze: apply_phase_invariants() handles frozen
                    d.apply_physics_forces = False
                    d.body.collision_enabled = False
                else:
                    # Freeze expired: restore everything.  TACKLE-STATE
                    # GATE (2026-08-23): the ROLE may convert here (the
                    # marker role must not survive into OPEN_PLAY where
                    # the phase contracts would freeze it), but the
                    # tackle_state_active FLAG persists -- the holder
                    # stays excluded from spacing/depth roles until the
                    # gate releases it on radius exit.
                    d.marker_freeze_timer = 0.0
                    d.apply_physics_forces = True
                    d.body.collision_enabled = True
                    d.defender_role = DefenderRole.DEF_LINE_READY

        # P8: Per-defender retreat + transition
        # NOTE: Every non-marker defender gets a retreat target each frame
        # during POST_RUCK.  This prevents the "frozen" appearance where
        # non-DEF_RETREAT_10 defenders (DEF_LINE_READY, DEF_CHASE, etc.)
        # stand still for the entire ~2s window because _update_defence()
        # is gated out.
        all_compliant = True
        retreat_speed = self.def_tuners.retreat_compliance_distance_m * 0.6 if hasattr(self.def_tuners, 'retreat_compliance_distance_m') else 6.0
        # TASK VI: the POST_RUCK retreat target consumes the ANCHOR AUTHORITY
        # (ruck_x + 10), never an inline ruck_x + derivation.
        target_x = _anchor_m(self.world)
        for d in self.world.defending_team:
            if getattr(d, "fullback_role_active", False):
                # FULLBACK EXCLUSION (2026-08-13): the fullback never
                # retreats with the line during POST_RUCK â€” its deep
                # backfield anchor is re-asserted below.
                continue
            if d.defender_role == DefenderRole.DUMMY_HALF:
                continue
            if d.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                # Markers do NOT retreat Ã¢â‚¬â€ they hold at X-axis positions
                # (their freeze timer is handled by the marker block above)
                continue
            # ALL non-marker, non-DH defenders get a retreat target every frame.
            # This keeps them moving during the entire POST_RUCK phase
            # instead of standing still when not yet in DEF_RETREAT_10.
            # This direct-write path bypasses Player.update() (so the
            # defended_cap clamp there cannot bite) â€” the hardcoded 6.0 was
            # replaced by post_catch_cap_mps so POST_RUCK retreat obeys the
            # same 5.0 m/s ceiling as the rest of the PTB era.
            # POST_RUCK: geometry restricted to retreat/collapse + depth/width
            # (Stage 3H conductor model).  Write the target — never _move_toward
            # (which mutates x/y directly and leaves target_x/target_y stale,
            # tripping the STUCK_NOT_FROZEN predicate).  The target is always
            # set so it can never be stale; d.update(dt) integrates the retreat
            # ONLY for a defender still behind the anchor (preserving the
            # original "don't pull a compliant defender backward" behaviour).
            d.target_x = target_x
            d.target_y = _frame_spacing_y(self.world, d, self.def_tuners)
            d.move_speed = self.def_tuners.post_catch_cap_mps
            d.frozen = False
            d.movement_state = MovementState.RETREAT
            if d.x < target_x:
                d.update(dt)
            if d.defender_role == DefenderRole.DEF_RETREAT_10:
                if d.x >= target_x:
                    d.defender_role = DefenderRole.DEF_LINE_READY
                    d.frozen = False
                else:
                    all_compliant = False

        # Transition to OPEN_PLAY when all compliant OR after timeout
        # POST_RUCK_DURATION_S set to 0.05 so engine flips to OPEN_PLAY
        # almost instantly after PTB finishes (~3 frames at 60fps).
        # STAGE 4B (2026-09-19): bound from world/rules/exit_timing_budget.py
        # (was a literal here).  Behaviour-neutral relocation.
        POST_RUCK_DURATION_S: float = _EXIT_POST_RUCK_DURATION_S
        if all_compliant or self._phase_timer >= POST_RUCK_DURATION_S:
            # =============================================================
            # POST_RUCK -> OPEN_PLAY: final marker role clear + state restore
            # =============================================================
            # -- DISARM PTB-ERA SPEED CAP ON FIRST TRUE OPEN_PLAY FRAME --
            # POST_RUCK_OPEN_PLAY -> OPEN_PLAY is the boundary at which
            # defenders may accelerate toward max_defensive_speed_mps (8.3).
            # Zero the clamp window so the next OPEN_PLAY frame moves under
            # the normal open-play ceiling.  This is safe: the kickoff-catch
            # clamp can never survive a full tackle -> release -> PTB cycle
            # (max 3.0s hard ceiling), so any armed window here is ours.
            for d in self.world.defending_team:
                d.defended_cap_remaining_s = 0.0
            self._post_ruck_marker_cleanup()
            # PER-SET GOVERNOR RESET (2026-08-04): clear the shared line
            # state (depth/width/speed/step-up/width-clamp baseline) when a
            # play-the-ball completes.  The old set's line state belongs to
            # the previous ruck; carrying it forward made the rebuilt line
            # start from a stale smoothed snapshot instead of the new anchor.
            self.def_behaviour.line_governor.reset()
            bc = self.world.ball_carrier
            if bc is not None:
                self.attack_controller.apply_vector(bc, self.world, dt)
            # -- CLEAR STALE ARRIVAL-MODE AT POST_RUCK->OPEN_PLAY (2026-09-01) --
            # The PTB role pipeline stamps arrival_mode=True on defenders; if it
            # survives into OPEN_PLAY the arrival predictor (snap-to-target at
            # 0.1m, ignores move_speed, zeroes vx inside stop radius) freezes
            # them on static targets instead of accelerating toward live ones.
            # Belt-and-suspenders: clear it right at the boundary so the first
            # OPEN_PLAY frame always uses the lerp accelerator.
            for _p in getattr(self.world, "players", []):
                if getattr(_p, "arrival_mode", False) or getattr(_p, "reached_target", False):
                    _p.arrival_mode = False
                    _p.reached_target = False
            self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "POST_RUCK->OPEN_PLAY")
            # ENGAGEMENT STATE RESET (Task A-2): PTB reset re-bootstraps the
            # persistent depth-role/engagement state so no stale PASSIVE
            # role-lock or side-lock survives from the previous set into the
            # new open-play phase.
            reset_engagement_state(self.world)

        # FULLBACK RE-ASSERT (2026-08-13): POST_RUCK writers (markers, BC,
        # per-defender retreat) ran this frame â€” re-assert the deep backfield
        # anchor LAST so the fullback is never left on a line/retreat target.
        self._reassert_fullback(dt)

        # ── FULLBACK-INVARIANT AUDIT AT POST_RUCK (2026-08-27) ──
        # The actor must survive POST_RUCK_OPEN_PLAY as a deep, unfrozen,
        # geometry-owned anchor (depth_role None, frozen False, target_source
        # "fullback_geometry").  This phase is a high-risk site for line-role
        # leakage, freeze artefacts, or target-source overrides due to TES
        # wipes and P7 retreat/marker/DH writers.  Runs AFTER
        # _reassert_fullback so the FINAL frame state (not a mid-write state)
        # is captured.  Observational only -- never modifies state.
        audit_fullback_invariants(self.world)

    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
    # POST_RUCK_MARKER_CLEANUP Ã¢â‚¬â€ Final marker role clear + state restore
    # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

    def _assign_defensive_line_targets(self) -> None:
        """Assign immediate defensive line targets for all non-marker defenders.

        Called from _post_ruck_marker_cleanup() to ensure defenders have
        valid target positions on the first OPEN_PLAY frame.

        Sets target_x = ruck_x + def_line_depth_m for each defender that
        is not a marker, DUMMY_HALF, or already past the line.
        """
        ruck_x = getattr(self.world, "ruck_x_m", None)
        if ruck_x is None:
            return
        # TASK VI: line target consumes the ANCHOR AUTHORITY (ruck_x + 10),
        # never an inline ruck_x + def_line_depth_m derivation.
        line_x = _anchor_m(self.world)
        for d in self.world.defending_team:
            if d.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                continue
            if d.defender_role == DefenderRole.DUMMY_HALF:
                continue
            d.target_x = line_x
            # SPACING Y (Task F, 2026-08-10): line-target assignment keeps the
            # split-design spacing Y, never the defender's current Y.
            # SPACING Y (Task F + 2026-08-12): POST_RUCK line target uses the
            # un-gated frame preamble's fresh spacing dict.
            d.target_y = _frame_spacing_y(self.world, d, self.def_tuners)

    def _post_ruck_marker_cleanup(self) -> None:
        """Final cleanup when transitioning POST_RUCK_OPEN_PLAY -> OPEN_PLAY.

        1.  Clears MARKER_A/B roles Ã¢â€ â€™ DEF_LINE_READY
        2.  Restores normal defender physics (apply_physics_forces, frozen)
        3.  Purges ALL TES flags (is_tackler, in_contact, tackle_locked, tes_locked)
        4.  Assigns defensive line targets immediately
        5.  Calls _reset_tackle_state() AFTER roles are cleared
        6.  STAMPS the 10 m retreat obligation (2026-09-17) on every body
            converted out of DEF_RETREAT_10 that is still short of the
            anchor line, so this forced role conversion can no longer hand
            back tackle authority the body has not earned.  The ROLE write
            is unchanged, so spacing / width / hinge geometry are
            untouched -- only the authority acquisition is blocked.
        """
        for p in self.world.defending_team:
            # 1. Clear stale marker roles (critical)
            # TACKLE-STATE GATE (2026-08-23): the ROLE converts here
            # unconditionally (a marker role must not survive into
            # OPEN_PLAY where the phase contracts would freeze it), but
            # the tackle_state_active FLAG is intentionally preserved --
            # the holder stays excluded from spacing/depth roles until
            # the gate releases it on radius exit.
            # TACKLE-STATE GATE (2026-08-23) + TES ROLE CLEAR (2026-09-01):
            # clear EVERY TES-era role so no TACKLER_*/MARKER_*/DUMMY_HALF/
            # DEF_RETREAT_10 survives into OPEN_PLAY.  The spacing chain
            # rejects TACKLER_*/MARKER_*/DUMMY_HALF via
            # _SPACING_EXCLUDED_ROLES, so an ex-tackler/DH still carrying
            # one when OPEN_PLAY resumes is silently dropped from the
            # per-side list -> short count -> broken spacing.  Convert them
            # all to DEF_LINE_READY so the line re-forms cleanly.
            # RETREAT-OBLIGATION STAMP (2026-09-17): capture the pre-write
            # role so the DEF_RETREAT_10 -> DEF_LINE_READY conversion (the
            # exact boundary at which the 10 m contract used to expire
            # silently) can stamp the outstanding obligation.  Ex-tacklers
            # and old markers reach this sweep as DEF_RETREAT_10 (assigned
            # at RELEASE entry by _prepare_release), so this covers every
            # non-marker defender that owes the 10 m retreat.
            # RETREAT ROLE SURVIVES THIS SWEEP (2026-09-18).  DEF_RETREAT_10
            # is deliberately ABSENT from the tuple below: it IS the engine's
            # retreat-completion signal (``_update_post_ruck_open_play`` flips
            # it to DEF_LINE_READY once ``d.x >= target_x``), and converting
            # it here -- three frames after the post-ruck phase, and again at
            # every OPEN_PLAY frame via ``assign_def_line_ready`` -- is what
            # destroyed the state before any retreat veto could observe it.
            # It does not need converting for spacing: DEF_RETREAT_10 is not
            # in ``_SPACING_EXCLUDED_ROLES``.
            if p.defender_role in (
                DefenderRole.MARKER_A,
                DefenderRole.MARKER_B,
                DefenderRole.TACKLER_1,
                DefenderRole.TACKLER_2,
                DefenderRole.TACKLER_3,
                DefenderRole.DUMMY_HALF,
                DefenderRole.DEF_CHASE,
                DefenderRole.KICK_CHASE,
            ):
                p.defender_role = DefenderRole.DEF_LINE_READY

            # 2. Restore normal defender physics
            p.apply_physics_forces = True

            # 3. Purge ALL TES flags (belt-and-suspenders)
            p.is_tackler = False
            p.in_contact = False
            p.tackle_locked = False
            p.tes_locked = False
            p.tes_tackler_role = None

            # 4. Purge ALL cached defence-subsystem state so the next
            #    _update_defence() runs on a clean slate.  Without this,
            #    stale depth_class, timing_locked, reaction_timer, freeze_timer
            #    and other per-defender cached values from the previous tackle
            #    cycle survive into OPEN_PLAY and can freeze defenders on the
            #    first OPEN_PLAY frame.
            p.depth_class = "PASSIVE"        # forces fresh classification
            p.timing_locked = False
            p.reaction_timer = 0.0
            p.retreat_delay = 0.0
            p.lateral_hesitation = 0.0
            p.step_stagger = 0.0
            p.step_stagger_active = False
            p._prev_depth_class = None
            p.lane_index = 0
            p.tackle_role = "PASSIVE"
            p.freeze_timer = 0.0
            p.marker_freeze_timer = 0.0

        # 5. Assign defensive line targets IMMEDIATELY
        self._assign_defensive_line_targets()

        # 6. Reset tackle state AFTER roles are cleared
        self._reset_tackle_state()

    def _reset_tackle_state(self):
        """
        Reset all tackle-related state after POST_RUCK_OPEN_PLAY completes.
        This ensures OPEN_PLAY starts clean with no leftover TES/marker roles.

        P14: Clear ALL TACKLER_* and MARKER_* roles from all defenders,
        converting them to DEF_LINE_READY.  This is a belt-and-suspenders
        sweep so no defender remains frozen or in a stale role after PTB.
        """
        # Clear TES context
        self.tes_ctx = None
        self.tes_outcome = None

        # P14: Sweep all defenders for stale TACKLER_* / MARKER_* roles
        for d in self.world.defending_team:
            if d.defender_role in (
                DefenderRole.TACKLER_1,
                DefenderRole.TACKLER_2,
                DefenderRole.TACKLER_3,
                DefenderRole.MARKER_A,
                DefenderRole.MARKER_B,
            ):
                # TACKLE-STATE GATE (2026-08-23): the ROLE converts
                # unconditionally; the tackle_state_active FLAG is
                # preserved so the holder stays excluded from
                # spacing/depth roles until the gate releases it on
                # radius exit.
                d.defender_role = DefenderRole.DEF_LINE_READY
                d.is_tackler = False
                d.in_contact = False
                d.tes_locked = False
                d.tackle_locked = False
                d.marker_freeze_timer = 0.0
                d.freeze_timer = 0.0
            # Fix H: restore normal physics for ALL defenders unconditionally
            # (RELEASE per-frame protection sets apply_physics_forces=False for
            # everyone each frame, so we must clear it for everyone on reset)
            d.apply_physics_forces = True

        # Unconditional sweep: clear ALL freeze/stall timers on every defender.
        # MARKER_A/B freeze_timer was set by _assign_markers_for_new_tackle() and
        # survives role clear in _post_ruck_marker_cleanup(), causing
        # _apply_structured_open_play() to re-freeze former markers on OPEN_PLAY entry.
        for d in self.world.defending_team:
            d.freeze_timer = 0.0
            d.marker_freeze_timer = 0.0
        # P2: Clear old DUMMY_HALF role from all attackers so the previous
        # cycle's DH doesn't wander around with a stale defender_role.
        for p in self.world.attacking_team:
            if getattr(p, "defender_role", None) == DefenderRole.DUMMY_HALF:
                p.defender_role = None

        # Clear OPE tackler references (belt-and-suspenders)
        self.primary = None
        self.joiners.clear()

        # Clear any TES flags
        self.world.tes_active = False
        if hasattr(self.world, "tackler_ids"):
            self.world.tackler_ids.clear()

    def _check_break_tackle(self, dt: float) -> bool:
        """
        Spec 7.1: During TES_DRIVE/CONTACT/DOMINANCE, if BC drive_force or
        speed exceeds defender_resistance threshold:
          - Cancel TES, cancel markers, cancel PTB.
          - Switch to OPEN_PLAY immediately.
          - Former tacklers within chase_radius -> DEF_CHASE.
          - Former tacklers offside -> DEF_RETREAT_10.
        Returns True if break-tackle occurred (TES cancelled).
        """
        bc = self.ball_carrier
        if bc is None or self.tes_ctx is None:
            return False
        # PHASE GUARD: the raw momentum gate only has a valid signal during
        # TACKLE_DRIVE, where drive_start_x/drive_target_x are populated by the
        # DRIVE-LERP setup.  In INIT/CONTACT/DOMINANCE the BC is position-gripped
        # and those drive fields are unset â€” getattr falls back to bc.x for
        # drive_target_x while drive_start_x exists as a class field default 0.0,
        # yielding drive_dist = bc.x (~60 m -> ~40 m/s) and firing the break gate
        # on EVERY frame (probe-confirmed: raw_break fired 290-296).  The broken-
        # tackle SCORING gate (_check_broken_tackle_scoring) covers CONTACT and
        # DOMINANCE; the raw gate exists only to catch an overpowering DRIVE.
        if self._phase != "TACKLE_DRIVE":
            return False

        # DRIVE-INIT GUARD: the LERP setup at the top of _update_tackle_drive()
        # writes ctx.drive_target_x/drive_start_x.  The CONTACT handler flips
        # self._phase to TACKLE_DRIVE at timer 0.15 BEFORE that setup runs on
        # the next frame, so a TACKLE_DRIVE frame can still carry the class
        # defaults (drive_target_x=0.0, drive_start_x=25.18) â€” deriving speed
        # there yields drive_dist ~= 25 m -> ~20 m/s and fires a spurious break
        # on the boundary frame (probe-confirmed: CONTACT->OPEN_PLAY at 305).
        # Only trust the raw gate once the drive fields are actually populated.
        drive_target_x = getattr(self.tes_ctx, "drive_target_x", 0.0)
        drive_start_x = getattr(self.tes_ctx, "drive_start_x", 0.0)
        if drive_target_x == 0.0 or drive_start_x == 0.0:
            return False

        if self.tes_ctx is not None:
            drive_dist = abs(drive_target_x - drive_start_x)
            dur = max(0.1, self.tes_outcome.tackle_duration_s if self.tes_outcome else 1.0)
            bc_speed = drive_dist / dur
        else:
            bc_speed = math.hypot(bc.vx, bc.vy)
        bc_mass = getattr(bc, "mass_kg", 90.0)
        bc_momentum = bc_mass * bc_speed
        # -- FALLBACK POISON FIX (2026-08-22) --
        # When the engagement registry carries NO measurable defender
        # momentum (e.g. an all-zero velocity snapshot), the old
        # ``bc_momentum * 0.5`` fallback made
        # ``bc_momentum > def_resistance * 1.4`` trivially TRUE - arming
        # the raw break gate on every frame from the momentum side alone
        # (a spurious break exits to OPEN_PLAY with NO ruck formed).  With
        # no measurable resistance the ratio is UNDEFINED: skip the
        # momentum condition entirely and let the explicit speed-burst
        # branch (scripted drive average > 7.0 m/s) remain the only fire
        # path for that case.
        total_def_momentum = self.tes_ctx.engagement.total_momentum()
        momentum_break = (
            bc_momentum > total_def_momentum * 1.4
            if total_def_momentum > 0
            else False
        )
        if momentum_break or bc_speed > 7.0:
            if DEBUG_OPE_TRACE:
                print(f"[BREAK_TACKLE] BC #{bc.player_id} momentum={bc_momentum:.1f} > def_resistance={def_resistance:.1f} x 1.4 -- BREAK!")
            return True
        return False
