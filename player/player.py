"""
player/player.py — Simplified.

The OpenPlayEngine is the sole authority for all movement, freeze, and state.
Player.update() only checks the `frozen` flag (set by OPE). No self-managed
state machines, no apply_state(), no tackle_locked/in_contact/is_tackler
branches — those are all replaced by `frozen` and OPE's phase logic.

The `try_catch()` method has been removed — OPE's _handle_catch() does
all catch logic directly.

FREEZE CONTRACT (macro-manager era, 2026-08-24):
  Players are dumb executors. They accept roles, target positions,
  movement modes and speeds issued by the macro layer (DefenceManager /
  AttackManager, called through OpenPlayEngine) and integrate them
  physically. No player module may contain open-play decision logic;
  new behaviour is added to the owning manager or it fails review.

  Dormant exception (audited 2026-08-24): player/receiver_selector.py
  is a kickoff-era selection utility with ZERO callers in the live
  engine. Documented as bypassed legacy (Stage 5 deletion candidate);
  it is never invoked at runtime and must stay that way.
"""

import math
from enum import Enum, auto
from typing import Optional

from player.collision_body import CollisionBody


class Team(Enum):
    KICKING = "KICKING"
    RECEIVING = "RECEIVING"


class PlayerState(Enum):
    IDLE = "IDLE"
    KICKER = "KICKER"
    CHASE_BALL = "CHASE_BALL"
    BALL_CARRIER = "BALL_CARRIER"
    SUPPORT_RUN = "SUPPORT_RUN"
    TACKLING = "TACKLING"
    TACKLE_APPROACH = "TACKLE_APPROACH"
    TACKLED = "TACKLED"
    PTB_PLAYER = "PTB_PLAYER"


class DefenderRole(Enum):
    # Existing roles
    MARKER_A = auto()
    MARKER_B = auto()
    LINE = auto()
    KICK_CHASE = auto()
    DUMMY_HALF = auto()
    # TES tackler roles (assigned at TES_START, cleared after RELEASE)
    TACKLER_1 = auto()
    TACKLER_2 = auto()
    TACKLER_3 = auto()
    # Retreat / line / chase roles (assigned at TES_START through OPEN_PLAY)
    DEF_RETREAT_10 = auto()
    DEF_LINE_READY = auto()
    DEF_CHASE = auto()


FIELD_MIN_X = 0.0
FIELD_MAX_X = 120.0
FIELD_MIN_Y = 0.0
FIELD_MAX_Y = 70.0


class Player:
    """
    A rugby league player.

    Movement: OPE sets target_x/target_y.  Player.update() accelerates
    toward that target if not frozen.

    Freeze: ONLY OPE may set frozen=True/False.  No other subsystem
    touches this flag.

    State: PlayerState is informational (for rendering/debug).  OPE
    sets ball_carrier.state = BALL_CARRIER during start_open_play.

    attack_role: one of "none", "first_receiver", "support_runner".
    Managed by OpenPlayEngine._build_attacking_line(), reset at tackle start.

    Facing Override:
      set_facing(angle) — locks facing to angle, bypasses velocity-driven rotation.
      clear_facing_override() — resumes normal velocity-derived facing.
      Must be explicitly cleared by the setting system — never auto-cleared.

    Marker Offside (Engine-Spec X-Axis Alignment):
      marker_offside: True when the marker has not reached its X-axis target
        position by the time the dummy-half picks up the ball.
      marker_offside_retreat_x: The X coordinate this marker must reach
        (ruck_x + 10) to regain onside status.
    """

    # ── GLOBAL DEFENDER REACTION GOVERNOR (post-catch speed blend) ──
    # After the ball is caught from KICKOFF, defenders must NOT jump from
    # their kickoff chase speed to full defensive speed in one frame.  They
    # are capped at their kickoff chase speed for DEFENCE_REACTION_HOLD_S,
    # then blend linearly to the commanded defence speed over
    # DEFENCE_REACTION_BLEND_S.  Enforced in _accelerate_toward_target()
    # so it wins over every subsystem that rewrites move_speed.
    DEFENCE_REACTION_HOLD_S: float = 0.4
    DEFENCE_REACTION_BLEND_S: float = 0.6

    # ── POST-CATCH DEFENSIVE LINE CLAMP (2026-08-02) ──
    # During the kickoff-catch → first stable OPEN_PLAY transition, the three
    # non-governor speed writers (pursuit reaction, depth reclassification,
    # retreat compliance/SDM) can spike defender speed past the structured
    # line governor's intended maximum.  While `defended_cap_remaining_s > 0`
    # every defender's effective speed is capped at `defended_speed_cap_mps`
    # (the governor's maximum intended line speed), enforced in
    # _accelerate_toward_target() — AFTER every writer, BEFORE movement
    # integration — so TES/PTB/pursuit/compliance logic is untouched.
    # The clamp auto-disengages via _finalise_lateral_targets() once
    # structured OPEN_PLAY is stable (pursuit off, collapse off, depth not
    # flipping, line geometry active), and has a hard time ceiling so it can
    # never outlive the transition window.
    DEFENDED_CAP_HARD_LIMIT_S: float = 3.0
    DEFENDED_CAP_BLEND_S: float = 0.5

    # ── PTB-ERA SMOOTH-DAMP DISABLE SUB-STATES (2026-08-16) ──
    # Smooth-damp (Unity-style critically damped convergence) is the
    # geometry integration for OPEN_PLAY role-slot movement.  During
    # TACKLE_RELEASE / PTB_READY / PTB / POST_PTB_RESET /
    # POST_RUCK_OPEN_PLAY / RESET_LINE the phase handlers and the P7
    # retreat writers own movement: the flat blanket retreat to ruck+10
    # must run on the fast accel-lerp path so it COMPLETES before
    # OPEN_PLAY re-shapes the line into role depths.  Leaving smooth-damp
    # on in these phases produces the "98% done -> jittery crawl" as the
    # retreaters settle a few cm per frame inside the PTB window.
    PTB_SMOOTH_DAMP_DISABLED_SUB_STATES = frozenset({
        "tackle_release",
        "ptb_ready",
        "ptb",
        "post_ptb_reset",
        "POST_RUCK_OPEN_PLAY",
        "reset_line",
    })

    def __init__(self, x: float, y: float, player_id: int = -1):
        self._x = x
        self.y = y
        self.target_x = x
        self.target_y = y

        self.body = CollisionBody(self)
        self.body.player_id = player_id

        self.kickoff_kicked = False
        self._kickoff_internal_state = "init"

        self.vel_x = 0.0
        self.vel_y = 0.0
        self.max_speed = 6.5
        self.accel = 0.70
        self.decel = 0.35
        self.input_smooth_x = 0.0
        self.input_smooth_y = 0.0

        self.vx = 0.0
        self.vy = 0.0
        self.max_speed_mps = 9.5
        self.move_speed = self.max_speed_mps

        # ── Global defender reaction governor (armed at kickoff catch) ──
        # kickoff_chase_speed: speed ceiling captured at catch time
        #   (before the OPEN_PLAY cleanup overwrites move_speed to 1.0x).
        # defence_reaction_remaining_s: seconds of hold+blend window left;
        #   > 0 means this defender is reaction-limited.
        self.kickoff_chase_speed: float = 0.0
        self.defence_reaction_remaining_s: float = 0.0

        # ── Post-catch defensive line clamp (armed at kickoff catch) ──
        # defended_speed_cap_mps:   clamp ceiling (governor max line speed)
        # defended_cap_remaining_s: seconds of clamp window left; > 0 means
        #   this defender is clamp-limited.  Armed in catch_system and
        #   auto-disengaged by DefensiveBehaviourUnified._finalise_lateral_targets
        #   once structured OPEN_PLAY is stable.  `defended_cap_anchor_x` is
        #   the defensive line anchor captured at catch time, used for the
        #   geometry-stability check; `defended_cap_blend_s` tracks elapsed
        #   clamp time so the release blends out smoothly.
        self.defended_speed_cap_mps: float = 0.0
        self.defended_cap_remaining_s: float = 0.0
        self.defended_cap_anchor_x: float = 0.0
        self.defended_cap_blend_s: float = 0.0

        # ── GLOBAL DEFENSIVE TOP-SPEED CEILING (stamped per frame) ──
        # max_defensive_speed_mps: the unified defensive top speed
        # (DefenceTuners.max_defensive_speed_mps), stamped onto every
        # defender every frame by OpenPlayEngine._update_defence() and at
        # kickoff catch by catch_system.  0.0 = not a defender -> no cap
        # (attackers, BC, DH are unaffected).  Enforced as the FINAL hard
        # ceiling in _accelerate_toward_target() and in the post-facing
        # clamp inside update(), so no writer in any phase can move a
        # defender faster than the unified ceiling.
        self.max_defensive_speed_mps: float = 0.0

        self.facing = 0.0
        self.facing_len = 0.8
        self.facing_override = False
        self.facing_override_angle = 0.0

        self.jump_reach_m = 3.0
        self.catch_high_m = 1.5
        self.CATCH_RADIUS = 1.5

        self.player_id = player_id
        self.team: Optional[Team] = None
        self.role = None

        self.frozen = False
        self.freeze_timer: float = 0.0
        # ── TES DEPTH-LOCK (2026-09-06) ──
        # depth_locked=True: the body may slide in Y (lateral/lane) but is
        # pinned in X (depth — toward/away from the ruck) to depth_lock_x,
        # which is captured once at TES entry and held for the chamber.
        #
        # DRIVE-DEPTH TRACK (2026-09-06): during the TES drive
        # (CONTACT/DRIVE/DOMINANCE) depth_lock_x is refreshed each frame to
        # the LIVE BC-tracked value (bc.x + WIDTH_OFFSETS[depth_role]) so the
        # line moves back with the driven carrier.  depth_lock_y is the
        # drive-lateral hold: when set (not None), the body's LATERAL (Y)
        # axis is ALSO pinned to depth_lock_y so the line stays on the Y it
        # had at drive entry (no lateral blow-out).  When None, Y stays free
        # (DEF_RETREAT_10 — the wall-reforming role keeps its lateral slide).
        self.depth_locked: bool = False
        self.depth_lock_x: float = 0.0
        self.depth_lock_y: float | None = None
        self.jersey_number: int = 0

        self.can_be_primary: bool = True
        self.can_be_secondary: bool = True
        self.possessed = False

        self.kick_lane = 0
        self.kick_lane_offset_y = 0.0
        self.kick_stagger_x = 0.0

        self.state = PlayerState.IDLE

        self.support_leader: Optional[Player] = None
        self.support_offset = 5.0

        self.defender_role: Optional[DefenderRole] = None
        # DEPTH ROLE (A/B/C/D/CENTRE/WINGER) — assigned by
        # defensive_depth_roles.assign_depth_roles() BEFORE geometry runs.
        # Distinct from defender_role (engagement/positional) and
        # depth_class (PRIMARY/SECONDARY/PASSIVE).  Consumed by
        # DefensiveGeometry width curve and ROLE_STEER_FACTOR.
        self.depth_role: Optional[str] = None
        # DEPTH SIDE (-1 = LEFT, +1 = RIGHT) — PERSISTENT role-locked side
        # (2026-08-09, Task A).  Set by defensive_depth_roles on bootstrap,
        # preserved across frames and tackle cycles, and changed only when
        # the defender crosses ruck_y by more than
        # defensive_depth_roles.SIDE_SWAP_HYSTERESIS_M.  Never derived from
        # live position during steady state.
        self.depth_side: Optional[int] = None

        # ── FULLBACK ROLE (dynamic last-defender, 2026-08-08) ──
        # True while THIS player is the active fullback actor for the
        # current frame, assigned by
        # game.defence.defence_lvl_1.fullback_role.assign_fullback_role().
        # Deliberately a boolean attribute and NOT a DefenderRole entry:
        # apply_phase_invariants() freezes any role absent from
        # MOVEMENT_RULES, and a new DefenderRole.FULLBACK would map to
        # Role.NONE and pin the actor in every phase.  The actor keeps
        # defender_role = DEF_LINE_READY (always a legal mover) and
        # exposes role = "FULLBACK" for debug overlays only.
        self.fullback_role_active: bool = False

        # ── FULLBACK TACKLE-ELIGIBILITY SIGNAL (2026-08-17) ──
        # The fullback ALWAYS retains tackle acquisition — it is a special
        # defensive role, never a "RETREAT ghost".  Every consumer that
        # would block tackle acquisition for RETREAT-state actors must skip
        # the actor's RETREAT flag when this is True.  Stamped True by
        # fullback_role.assign_fullback_role() while the actor holds the
        # role; cleared False at kickoff reset / role de-assignment so a
        # line defender is never mislabelled.
        self.can_acquire_tackle: bool = False

        # ── FULLBACK ABSORB HYSTERESIS (2026-08-12, Task J) ──
        # Absorption into the defensive line requires BOTH |x - anchor|
        # and |y - kick_lane| proximity for ABSORB_FRAMES_MIN consecutive
        # frames.  frames_within_absorb_x / frames_within_absorb_y count
        # those consecutive frames (reset to 0 the moment proximity
        # breaks).  fullback_absorb_allowed = True only when BOTH counters
        # reach ABSORB_FRAMES_MIN — the gate the OPEN_PLAY pipeline checks
        # before dissolving the fullback role into a deterministic line
        # role.  fullback_arrived = True once the actor is within
        # FB_ARRIVED_PROXIMITY_M of its anchor on the same frame (audit
        # visibility: reached the backfield slot vs still gliding).
        # Updated by fullback_role.update_fullback_absorb_state(); cleared
        # by reset_for_kickoff() so no stale counters survive a kickoff.
        self.frames_within_absorb_x: int = 0
        self.frames_within_absorb_y: int = 0
        self.fullback_absorb_allowed: bool = False
        self.fullback_arrived: bool = False

        self.retreating = False
        self.is_defender: bool = False

        self.mass = 1.0
        self.momentum = 0.0

        self.in_contact: bool = False
        self.is_tackler: bool = False
        self.tackle_locked: bool = False
        self.pre_tackle_event: str | None = None
        self.reach_event: str | None = None
        self.wrap_event: str | None = None
        self.apply_physics_forces: bool = False

        # ── PHASE 1: ATTACH flags (set by OpenPlayEngine._attach_defender) ──
        # tes_attached: True while this defender is mechanically stuck to the
        #   BC (CONTACT/DRIVE/DOMINANCE).  Cleared at RELEASE.
        # attach_slot:  0 = left, 1 = right, 2 = back (order of arrival).
        self.tes_attached: bool = False
        self.attach_slot: int | None = None

        # ── TES TACKLER ROLE (selected for the current tackle) ──
        # tes_tackler_role: "TACKLER_1"/"TACKLER_2"/"TACKLER_3" when this
        #   defender was selected by OpenPlayEngine._start_tackle() from the
        #   combined marker + defensive-line candidate pool.  Locked for the
        #   whole TES and cleared at RELEASE / open-play reset.  `defender_role`
        #   TACKLER_* remains the source of truth for movement contracts and
        #   marker assignment; this field records the explicit selection so it
        #   can be wiped globally without disturbing role assignment.
        self.tes_tackler_role: Optional[str] = None

        # ── TACKLER MOVEMENT CONTRACT (spec A–H, 2026-08-05) ──
        # movement_mode is the movement-contract switch.  TACKLER overrides
        # DEF_LINE_READY / DEF_RETREAT_10 / MARKER_A/B / spacing / line-shape
        # and any freeze except the TES freeze.  Set by OPE._arm_tackler_role()
        # at tackler selection, cleared/reset to RETREAT at RELEASE (contract G).
        # vx_target/vy_target = BC velocity being matched; heading_target =
        # angle toward BC; speed_target = commanded tackle speed (bumped to
        # 4.0/5.0 m/s inside 3.0/1.5 m).  lunge_* drive the open-play lunge.
        # collision_priority = HIGH marks the tackler as a collision initiator.
        self.movement_mode = None  # MovementMode enum (imported lazily)
        self.speed_target: float = 0.0
        self.vx_target: float = 0.0
        self.vy_target: float = 0.0
        self.heading_target: float = 0.0
        self.lunge_active: bool = False
        self.lunge_speed: float = 0.0
        self.lunge_duration: float = 0.0
        self.collision_priority = None  # CollisionPriority enum (imported lazily)


        self.tuners: "AttackTuners" = None  # set externally by OpenPlayEngine
        self.intent: str | None = None

        self.solid: bool = True
        self.is_kicking: bool = False
        # KICKOFF KICKER (2026-08-11): True while this player is the
        # designated kickoff kicker (KICKER_JERSEY_NUMBER in
        # kick_off/kickoff_formations.py).  Stamped by
        # apply_kicking_team_formation() so fullback_role can skip a
        # jersey-1-as-kicker override deterministically.
        self.is_kicker: bool = False

        self.anim_state: str = ""
        self.tes_locked: bool = False
        self.attack_role: str = "none"
        self.played_ball: bool = False
        self.movement_state = None
        self.position_snapped: bool = False

        # ── DEBT-AWARE RETREAT GAIT (Retreat Geometry Phase 1) ──
        # ``retreat_locomotion`` is the immutable per-frame gait profile
        # issued by the retreat authority
        # (game/defence/defence_lvl_1/retreat_locomotion.py) and consumed
        # by _accelerate_toward_target() / update().  It is FRAME-SCOPED:
        # cleared at the end of every update(), so a stale gait can never
        # leak onto a frame the retreat authority did not stamp.
        #
        # ``retreat_locomotion_mode`` / ``retreat_debt_m`` /
        # ``retreat_mode_changed_frame`` persist for the lifetime of the
        # obligation — they are the gait's hysteresis + dwell clock and its
        # audit record — and are cleared by the retreat authority and on
        # kickoff reset.  Player never imports the locomotion module; it
        # reads the issued profile duck-typed, exactly like every other
        # manager-issued movement field.
        #
        # ``retreat_facing_policy`` is the BACKPEDAL LATCH: the retreat
        # authority's latched verdict on whether this body is still in the
        # small-debt backpedal (eyes on the play, reverse travel along X) or
        # has earned the turn-and-run.  It lives across the frames of one
        # obligation so the facing gate's hold zone is honest, and it is
        # cleared with the rest of the gait.
        self.retreat_locomotion = None
        self.retreat_locomotion_mode = None
        self.retreat_debt_m = None
        self.retreat_mode_changed_frame = None
        self.retreat_facing_policy = None

        # -- Target stabiliser (geometry-signal smoothing) --
        # Wraps every AI target-writer (SLIDE lanes, SECONDARY depth,
        # width curve, retreat line, threat model, team spacing) before
        # the accelerator consumes target_x/target_y.  Applied ONLY when
        # the per-frame gate in update() allows (never during TES/PTB
        # arrival mode, never to attached tacklers or TACKLER pursuit).
        # Created lazily in update() — see _get_target_stabiliser().
        self._target_stabiliser = None

        # -- Marker freeze timer (1s after PTB) --
        self.marker_freeze_timer: float = 0.0

        # -- Movement writer audit tag (stamped every frame by OPE) --
        self.movement_writer: str | None = None

        # -- Arrival predictor mode (used by OPE during TES/RELEASE/PTB) --
        self.arrival_mode: bool = False
        self.reached_target: bool = False

        # -- Previous-frame x (for position-overwrite detection) --
        self.prev_x: float = 0.0

        # -- DRIVE-END AUTHORITATIVE SLOT (2026-08-19) --
        # When authoritative_until_frame >= world.frame_count the defender's
        # target_x/target_y are owned by the drive-end slot compute and must
        # NOT be blended (finalise), smoothed (stabiliser), or swapped
        # (lane-index reorder).  Set once at TACKLE_DRIVE -> TACKLE_DOMINANCE
        # transition; expires after one frame so the normal pipeline resumes.
        self.authoritative_until_frame: int = -1

        # ── GEOMETRY SMOOTH DAMP (2026-08-09) ──
        # Unity-style smooth_damp velocity state for geometry-driven
        # defensive movement (HOLD/DRIFT/SLIDE/RETREAT in OPEN_PLAY).
        # Movement uses smooth_damp — NOT proportional gain — so the
        # defender converges onto its role-slot target with critically
        # damped motion instead of accel-lerp snapping.  CHASE/SHOOT
        # pursuit keeps the existing acceleration physics.  Reset on
        # kickoff so no stale damp velocity survives the sequence.
        self._sd_vx: float = 0.0
        self._sd_vy: float = 0.0
        self._geometry_smooth_time_s: float = 0.12

    # ═══ PROPERTY SETTER — BC.x teleport diagnostic DISABLED (Block 1). ═══
    # Legacy tackle-audit contamination removed: the per-frame [BC_X_WRITE]
    # print drowned the signal for the new TACKLE SDEA.  Re-enable by
    # setting DEBUG_BC_X_WRITES = True below.
    DEBUG_BC_X_WRITES: bool = False

    @property
    def role_tag(self) -> str:
        """Short debug tag for the current defender role (visual overlay).

        # FULLBACK OVERLAY (2026-08-11): the dynamic fullback is the
        # LAST defender — it must render as "FB", never a stale line
        # role from a previous assignment.

        Maps DefenderRole -> 2-3 char label so role transitions are easy to
        read on screen during TES / RELEASE / retreat-10 / joiner logging:

            TACKLER_1/2/3  -> T1 / T2 / T3
            MARKER_A/B     -> MA / MB
            DEF_LINE_READY -> DLR
            DEF_RETREAT_10 -> DR10
            DUMMY_HALF     -> DH
            DEF_CHASE      -> DCS
            KICK_CHASE     -> KCS

        Updates automatically because it reads self.defender_role live.
        Falls back to tes_tackler_role (TES selection metadata) when the
        movement contract is TACKLER but defender_role is momentarily None,
        then to a raw string for any un-mapped role, then "-" for None.
        """
        if getattr(self, "fullback_role_active", False):
            return "FB"
        r = getattr(self, "defender_role", None)
        if r is not None:
            name = getattr(r, "name", None) or str(r)
            mapping = {
                "TACKLER_1": "T1",
                "TACKLER_2": "T2",
                "TACKLER_3": "T3",
                "MARKER_A": "MA",
                "MARKER_B": "MB",
                "DEF_LINE_READY": "DLR",
                "DEF_RETREAT_10": "DR10",
                "DUMMY_HALF": "DH",
                "DEF_CHASE": "DCS",
                "KICK_CHASE": "KCS",
                "LINE": "LN",
            }
            return mapping.get(name, name)
        tr = getattr(self, "tes_tackler_role", None)
        if tr is not None:
            return str(tr).upper()
        return "-"

    @property
    def depth_role_tag(self) -> str:
        """Short debug tag for the depth role (A/B/C/D/CENTRE/WINGER).

        Returns '-' when no depth role is assigned yet.  Used by the
        render overlay alongside role_tag so depth identity is visible
        per defender.  The dynamic fullback renders as "FB" (the depth-role
        system excludes the last defender from A/B/C/D/CENTRE/WINGER).
        """
        if getattr(self, "fullback_role_active", False):
            return "FB"
        return getattr(self, "depth_role", None) or "-"

    @property
    def world(self):
        """Back-reference to the GameWorldState, set by reset_for_kickoff().

        Returns None before the player has been attached to a world.
        """
        return getattr(self, "_world_ref", None)

    @property
    def x(self):
        return self._x

    @x.setter
    def x(self, value):
        old = self._x
        self.prev_x = old
        self._x = value
        if Player.DEBUG_BC_X_WRITES:
            world = getattr(self, "world", None)
            if world and self is world.ball_carrier:
                print(
                    f"[BC_X_WRITE] id={self.player_id} "
                    f"frame={world.frame_count} "
                    f"phase={world.phase} sub={world.sub_state} "
                    f"old={old:.3f} new={value:.3f} delta={value-old:.3f}",
                    flush=True,
                )

    def update(self, dt: float, attacking_direction: int = 1) -> None:
        """Simple movement: if frozen, stop; else accelerate toward target.

        ONLY OPE may freeze/unfreeze this player.

        When self.arrival_mode is True, uses arrival_predictor() instead of
        _accelerate_toward_target().  The arrival predictor scales velocity
        proportional to remaining distance to target_x/target_y, eliminating
        overshoot and oscillation.

        Synchronises collision body's collision_enabled with frozen state:
          - frozen=True  → collision disabled (no physics pushback)
          - frozen=False → collision enabled  (normal physics)
        """
        # === STEP-5: movement_writer cleared each frame ===
        self.movement_writer = None

        # ── TARGET FIELD BOUND (2026-09-25) ────────────────────────────
        # A target outside the playable field can NEVER be reached:
        # ``_clamp_position`` refuses the displacement, so the body is
        # commanded forward every frame while its position never changes --
        # it sits still holding a live target it cannot satisfy.  That is
        # the reported "agents remain stationary in OPEN_PLAY with static
        # targets" defect (measured by probe_open_play_stall_authority,
        # seed 20260827: ``x == 120.001`` pinned on the boundary,
        # ``tx == 121.0``, ``spd == 0.0``, ``move_speed == 0.80``, the
        # commanded approach cancelled by the position clamp every frame).
        #
        # Bounded at the TOP of update(), before the frozen early-return and
        # before the arrival predictor / acceleration lerp read it, so EVERY
        # path that integrates a body is covered -- OPEN_PLAY, the TES drive,
        # release, PTB and frozen bodies alike -- including writers that run
        # after the defensive dispatch.
        #
        # It uses this module's own FIELD_* constants (no import, so it
        # cannot create a cycle) and is the IDENTITY for an in-field target,
        # so a compliant frame stays byte-identical.
        if self.target_x < FIELD_MIN_X:
            self.target_x = FIELD_MIN_X
        elif self.target_x > FIELD_MAX_X:
            self.target_x = FIELD_MAX_X
        if self.target_y < FIELD_MIN_Y:
            self.target_y = FIELD_MIN_Y
        elif self.target_y > FIELD_MAX_Y:
            self.target_y = FIELD_MAX_Y

        # -- CONTACT_SPACE_MISS BALLISTIC REBOUND ---------------------
        # A body thrown out of a body-to-body contact that did NOT stick
        # travels its knock-back as a short SCRIPTED ARC owned by
        # game/physics/contact_space_miss.py, not as a velocity impulse.
        # An impulse cannot work here: _accelerate_toward_target below
        # drags velocity 70%/frame back to the AI's commanded velocity and
        # then hard-clamps it to move_speed, so even a 10 m/s impulse is
        # gone in about three frames having moved the body ~0.12 m.
        #
        # While the arc is live the AI's own seeking is SUSPENDED (this
        # branch returns before the frozen check, the target stabiliser,
        # the arrival predictor and the acceleration lerp), so the arc is
        # the sole displacement authority and nothing opposes it.  The arc
        # deliberately OUTRANKS the freeze: a body thrown by a collision
        # must not be pinned by the stagger that same collision applied.
        # The step is applied before _clamp_position, so the field bound
        # still holds, and the arc's frames are capped at its start, so the
        # travel cannot run past its declared ceiling.
        #
        # IDENTITY for every body without the state -- which is every body
        # in every scenario that does not produce a CONTACT_SPACE_MISS.
        csm_rebound = getattr(self, "_csm_rebound", None)
        if csm_rebound is not None:
            world = getattr(self, "world", None)
            csm_frame = getattr(world, "frame_count", None)
            step_dx, step_dy = csm_rebound.step(dt, csm_frame)
            self.vx = csm_rebound.vx
            self.vy = csm_rebound.vy
            self.vel_x = csm_rebound.vx
            self.vel_y = csm_rebound.vy
            self.x += step_dx
            self.y += step_dy
            self.body.collision_enabled = True
            from player.movement import update_facing_unified
            update_facing_unified(self, dt)
            self._update_physics(dt)
            self._clamp_position()
            self.body.sync()
            self.retreat_locomotion = None
            if csm_rebound.done:
                self._csm_rebound = None
            return

        if self.frozen:
            self.body.collision_enabled = False
            self.vx = self.vy = 0.0
            self.vel_x = self.vel_y = 0.0
            from player.movement import update_facing_unified
            update_facing_unified(self, dt)
            self._update_physics(dt)
            self.body.sync()
            # A frozen body never integrates a gait, so the profile is
            # consumed by nobody this frame — drop it so it cannot survive
            # onto the frame the body thaws on.
            self.retreat_locomotion = None
            return

        self.body.collision_enabled = True

        # -- TARGET STABILISER (geometry-signal smoothing) -------------
        # Wraps the raw AI target before EITHER the arrival predictor or the
        # lerp accelerator consumes target_x/target_y.  Gate is the shared
        # should_stabilise_target() from player/target_stabiliser.py so this
        # matches the MMDL consumer exactly (never fires during TES/PTB
        # arrival mode, on attached/tackler-mode actors, or on CHASE/SHOOT
        # pursuit defenders).  First-stabilise writes the raw target back
        # untouched, so there is no boot-lag frame.
        from player.target_stabiliser import should_stabilise_target
        if should_stabilise_target(self):
            stab = self._get_target_stabiliser()
            self.target_x, self.target_y = stab.stabilise(
                self.target_x, self.target_y, self.x, self.y
            )

        if self.arrival_mode:
            # Arrival predictor: velocity proportional to remaining error.
            # Handles velocity directly (no acceleration lerp).
            from player.arrival_controller import arrival_predictor
            arrival_predictor(self)
            self.x += self.vx * dt
            self.y += self.vy * dt
        else:
            # ── FULLBACK: SAME LOCOMOTION LAWS AS EVERYONE ELSE (2026-09-27) ──
            # The tagged fullback is deliberately excluded from the MMDL (its
            # DRIFT/CHASE state policy and its target generation are its own),
            # so it has no other consumer of the shared movement-state model.
            # Integrate it through that model -- the SAME acceleration table,
            # the SAME top-speed table, the SAME lateral-priority blend and
            # the SAME facing policy a line defender receives -- instead of
            # the legacy accel-lerp.  That is the whole of the change: the
            # fullback now DECIDES differently but MOVES identically.
            #
            # ``apply_fullback_locomotion`` returns False for a body with no
            # usable MovementState (a fresh or stub actor), so the legacy
            # accelerator remains the fallback and nothing can regress to a
            # missing-state crash.  The post-facing unified ceiling below is
            # what bounds the fullback's CHASE, exactly as it bounds every
            # other defender's.
            from player.movement_states import apply_fullback_locomotion
            if not (
                getattr(self, "fullback_role_active", False)
                and apply_fullback_locomotion(self, dt)
            ):
                # Normal lerp-based acceleration
                self._accelerate_toward_target(dt)

        # Facing integrates from the PREVIOUS frame's facing, captured here
        # so a backpedal gait can rotate from a known origin instead of from
        # the velocity-driven result (see the gait facing policy below).
        _facing_before_ufu = self.facing
        _vx_before_ufu = self.vx
        _vy_before_ufu = self.vy

        from player.movement import update_facing_unified
        update_facing_unified(self, dt)

        # ── RETREAT GAIT FACING POLICY (Retreat Geometry Phase 1) ──
        # A gait that "faces the threat" holds the eyes on the play while
        # the body travels — a true backpedal.  Letting facing follow the
        # velocity is what spins a retreating body about whenever it
        # reverses or jinks, so for these gaits the velocity-driven
        # rotation is discarded and replaced by a bounded rotation toward
        # the threat: the facing can never snap, and it never turns away.
        # The "travel" policy keeps update_facing_unified's result (a
        # sprinter faces where it is going).
        _loco = getattr(self, "retreat_locomotion", None)
        if _loco is not None and getattr(_loco, "faces_threat", False):
            self.facing = _facing_before_ufu
            # ── BACKPEDAL VELOCITY PIN (the other half of the contract) ──
            # update_facing_unified also nudges VELOCITY toward facing
            # (``vx += cos(facing) * 0.02``).  While the body is inside the
            # backpedal threshold it is looking BACK down -X, so that nudge
            # bleeds a small NEGATIVE vx into an X closure that has to stay
            # positive — the very direction the no-reverse clamp forbids.
            # Restore the pre-UFU velocity (which already carries the clamp)
            # so the held facing can never fight the compliance travel.
            if getattr(_loco, "backpedal_active", False):
                self.vx = _vx_before_ufu
                self.vy = _vy_before_ufu
            _threat = self._retreat_threat_angle()
            if _threat is not None:
                _diff = (
                    (_threat - self.facing + math.pi) % (2.0 * math.pi)
                ) - math.pi
                _rate = math.radians(
                    float(getattr(_loco, "facing_rate_deg_s", 0.0) or 0.0)
                )
                _max_step = _rate * dt
                if _max_step > 0.0:
                    if _diff > _max_step:
                        _diff = _max_step
                    elif _diff < -_max_step:
                        _diff = -_max_step
                self.facing += _diff

        # ── REACTION GOVERNOR HARD CAP (post-facing) ──
        # update_facing_unified() nudges velocity toward facing every frame,
        # which would otherwise puncture the reaction ceiling.  Enforce the
        # air-tight cap here, AFTER facing coupling, so the rule "defenders
        # must not exceed their kickoff speed during the reaction hold"
        # holds exactly.
        if self.defence_reaction_remaining_s > 0.0:
            cap = self._reaction_speed_cap(self.max_speed_mps)
            current_speed = math.hypot(self.vx, self.vy)
            if current_speed > cap:
                self.vx *= cap / current_speed
                self.vy *= cap / current_speed

        # ── DEFENSIVE LINE CLAMP HARD CAP (post-facing) ──
        # Same air-tight guarantee for the post-catch defensive line clamp:
        # the facing coupling must not move the defender faster than the
        # governor's maximum intended line speed.  TASK VI (2026-08-15):
        # engaged CHASE/SHOOT pursuit is exempt — the clamp is an alignment
        # governor and must not throttle the diagonal chase once the BC is
        # clearly past the line.
        if self.defended_cap_remaining_s > 0.0 and not self._is_chase_state():
            cap = self._defended_cap_ceiling(self.max_speed_mps)
            current_speed = math.hypot(self.vx, self.vy)
            if current_speed > cap:
                self.vx *= cap / current_speed
                self.vy *= cap / current_speed

        # ── GLOBAL DEFENSIVE TOP-SPEED CEILING (post-facing hard cap) ──
        # Covers the arrival-predictor movement path (PTB role movement,
        # which bypasses _accelerate_toward_target) and any facing coupling.
        # Guarantees no defender can exceed the unified ceiling even after
        # facing nudges velocity.
        if self.max_defensive_speed_mps > 0.0:
            current_speed = math.hypot(self.vx, self.vy)
            if current_speed > self.max_defensive_speed_mps:
                scale = self.max_defensive_speed_mps / current_speed
                self.vx *= scale
                self.vy *= scale

        # ── FULLBACK APPROACH-SPEED HARD CAP: DELETED (2026-09-27) ──
        # The FB-only 2.0 m/s sweeper cap used to run HERE — after the
        # movement integration and AFTER the position step had already been
        # applied — so it clamped the STORED velocity while the displacement
        # that frame had moved at the un-capped value.  That post-hoc clamp
        # was the second half of the fullback "zoom": the body's motion and
        # its reported vx/vy disagreed, on the FB and only the FB.
        #
        # It is gone.  The fullback now integrates through the shared
        # movement-state model above (same acceleration, same top-speed table,
        # same turning, same integration), and the unified
        # ``max_defensive_speed_mps`` ceiling immediately above bounds it
        # exactly as it bounds every other defender.  There is no longer any
        # fullback-specific speed authority for this backstop to police.

        # ── TES DEPTH-LOCK (2026-09-06; glide 2026-09-08) ──
        # Constrain the DEPTH axis (X) during TES while leaving the LATERAL
        # axis (Y) defined by the lock.  Runs AFTER every velocity cap so X
        # is pinned no matter which writer moved it this frame.
        #
        # DRIVE-DEPTH TRACK (2026-09-06): during the TES drive
        # (CONTACT/DRIVE/DOMINANCE) ``depth_lock_x`` is refreshed each frame
        # to the LIVE BC-tracked value (bc.x + WIDTH_OFFSETS[depth_role]) so
        # the line moves back with the driven carrier.  ``depth_lock_y`` is
        # the drive-lateral hold: when set (not None) the LATERAL (Y) axis
        # is held so the line keeps the Y it had at drive entry (no lateral
        # blow-out); when None (DEF_RETREAT_10 — the wall-reforming retreat
        # role) Y stays free so the body can slide to its lane.
        #
        # DEPTH-LOCK GLIDE (2026-09-08): the pin is a DESTINATION, not a
        # hard-set.  The X (and Y when depth_lock_y is set) glide toward the
        # pin at ≤ DEPTH_LOCK_GLIDE_SPEED_MPS * dt per frame, so the line
        # CONVERGES smoothly to the tracked depth instead of being SNAPPED
        # onto a vibrating carrier (the DH + line jitter).  Player.update is
        # the non-TES integrator — the TES drive authority no longer writes
        # line x/y.  Velocity is zeroed on both axes so the movement
        # authority audit (DEPTH_LOCK_MOVING) stays clean while the position
        # still tracks the role depth.
        if getattr(self, "depth_locked", False):
            # ── OUT-OF-RADIUS HOLD GATE (2026-09-07) ──
            # Live-play half of the out-of-band gate: a defender beyond
            # R_tackle that is NOT a registered TES tackler is NEVER
            # depth-locked during the TES drive.  Skip the X/Y glide so
            # Player.update cannot snap these bodies forward/outward (the
            # update_tes_drive writer half is the counterpart).  Local import
            # avoids a module-load cycle; when no world is attached
            # (verification stubs) the gate is a no-op and legacy behaviour
            # is preserved.
            _hold = False
            _wrld = getattr(self, "world", None)
            if _wrld is not None:
                from game.defence.defence_lvl_1.tackle_state_gate import (
                    is_tes_drive_hold as _is_tes_drive_hold,
                )
                _hold = _is_tes_drive_hold(_wrld, self)
            if not _hold:
                try:
                    from game.managers.defence_manager import (
                        DEPTH_LOCK_GLIDE_SPEED_MPS as _dl_glide,
                    )
                except Exception:
                    _dl_glide = 6.0
                _dl_max_step = _dl_glide * dt
                _dlx = float(self.depth_lock_x)
                # GLIDE X toward the pin (never a direct snap).
                _dx = _dlx - self.x
                if abs(_dx) > _dl_max_step:
                    self.x += (1.0 if _dx > 0 else -1.0) * _dl_max_step
                else:
                    self.x = _dlx
                self.vx = 0.0
                self.target_x = _dlx
                self._sd_vx = 0.0
                self.vel_x = 0.0
                _dly = getattr(self, "depth_lock_y", None)
                if _dly is not None:
                    _dlyf = float(_dly)
                    # GLIDE Y toward the pin (never a direct snap).
                    _dy = _dlyf - self.y
                    if abs(_dy) > _dl_max_step:
                        self.y += (1.0 if _dy > 0 else -1.0) * _dl_max_step
                    else:
                        self.y = _dlyf
                    self.vy = 0.0
                    self.target_y = _dlyf
                    self._sd_vy = 0.0
                    self.vel_y = 0.0

        # ── FRAME-SCOPED RETREAT GAIT (Retreat Geometry Phase 1) ──
        # The locomotion profile is an issue for THIS frame only: the
        # retreat authority stamps it, this integration consumes it, and
        # clearing it here means a stale gait can never survive onto a
        # frame the authority did not stamp.
        self.retreat_locomotion = None

        self._clamp_position()
        self.body.sync()

    def _retreat_threat_angle(self):
        """Angle from this body to the play a retreat gait keeps in view.

        A backpedal gait holds the eyes on the threat while the body
        travels — the ball carrier when there is one, otherwise the ruck
        origin, otherwise the +X direction the attack runs in.  Returns
        ``None`` only when there is no world at all (verification stubs),
        in which case the gait leaves facing untouched.
        """
        world = getattr(self, "world", None)
        if world is None:
            return None
        bc = getattr(world, "ball_carrier", None)
        if bc is not None:
            return math.atan2(bc.y - self.y, bc.x - self.x)
        ruck_x = getattr(world, "ruck_x_m", None)
        ruck_y = getattr(world, "ruck_y_m", None)
        if ruck_x is not None and ruck_y is not None:
            return math.atan2(ruck_y - self.y, ruck_x - self.x)
        return 0.0

    def _get_target_stabiliser(self):
        """Lazily create the per-player target stabiliser.

        Creation is deferred so the heavy import only happens on the first
        frame a player's targets need stabilising.  Reset on kickoff via
        reset_for_kickoff() so no stale geometry memory survives.

        GAP-PROPORTIONAL ALPHA (2026-08-16): when a live OpenPlayEngine +
        DefenceTuners is reachable (via world.open_play_engine.def_tuners),
        the stabiliser is created with the tuner-driven base/max alpha and
        fast-band so the post-PTB role-depth settle converges fast while
        the near-target jitter filter keeps the base alpha.
        """
        if self._target_stabiliser is None:
            from player.target_stabiliser import (
                TargetStabiliser,
                DEFAULT_STABILISER_ALPHA,
                DEFAULT_STABILISER_MAX_ALPHA,
                DEFAULT_STABILISER_FAST_BAND_M,
            )
            world = getattr(self, "world", None)
            ope = getattr(world, "open_play_engine", None) if world is not None else None
            tuners = getattr(ope, "def_tuners", None) if ope is not None else None
            if tuners is not None:
                self._target_stabiliser = TargetStabiliser(
                    alpha=getattr(tuners, "stabiliser_base_alpha", DEFAULT_STABILISER_ALPHA),
                )
                self._target_stabiliser.max_alpha = getattr(
                    tuners, "stabiliser_max_alpha", DEFAULT_STABILISER_MAX_ALPHA
                )
                self._target_stabiliser.fast_band_m = getattr(
                    tuners, "stabiliser_fast_band_m", DEFAULT_STABILISER_FAST_BAND_M
                )
            else:
                self._target_stabiliser = TargetStabiliser()
        return self._target_stabiliser

    def set_facing(self, angle: float) -> None:
        """Override facing to a specific angle.

        When set, update_facing_unified() locks facing to *angle*
        and bypasses velocity-driven rotation.  Caller MUST explicitly
        clear ``self.facing_override = False`` to resume normal
        velocity-derived facing.
        """
        self.facing_override = True
        self.facing_override_angle = angle

    def clear_facing_override(self) -> None:
        """Resume normal velocity-driven facing.

        After calling this, update_facing_unified() will derive facing
        from (vx, vy) again with smoothing and shortest-path rotation.
        """
        self.facing_override = False

    def set_state(self, state: PlayerState, **kwargs) -> None:
        """Set player state (informational only)."""
        self.state = state
        for k, v in kwargs.items():
            setattr(self, k, v)

    def reset_for_kickoff(self, world=None) -> None:
        """Reset player state for a new kickoff."""
        self.state = PlayerState.IDLE
        self.retreating = False
        self.frozen = True

        self.target_x = self.x
        self.target_y = self.y

        self.vx *= 0.35
        self.vy *= 0.35
        self.vel_x *= 0.35
        self.vel_y *= 0.35

        self.body.reset_collision_state()

        if world is not None:
            self._world_ref = world
            self.body.apply_kickoff_properties()

        self.possessed = False
        self.freeze_timer = 0.0
        self.can_be_primary = True
        self.can_be_secondary = True
        self.defender_role = None
        self.depth_role = None
        # Reset the persistent depth-role/side seed (Task A, 2026-08-09):
        # a reused Player object must re-bootstrap its depth roles for the
        # new kickoff instead of entering persistent maintenance with stale
        # side data and no roles.
        self.depth_side = None
        self._depth_seeded = False
        # ── DEFENSIVE ENGINE INVARIANT SHEET (2026-08-12) ──
        # Clear the stamped role-depth anchors too — a reused Player must
        # not carry role_depth_target_x/y from the previous sequence into
        # the first catch re-seed (force_reseed_depth_roles re-stamps them
        # on the catch frame).  Cleared inline (NOT via
        # defensive_depth_roles.reset_depth_role_state) to avoid a circular
        # import: that module imports Player at module level.
        self.role_depth_target_x = None
        self.role_depth_target_y = None
        # Reset the dynamic fullback role (assigned per frame by
        # game.defence.defence_lvl_1.fullback_role.assign_fullback_role()).
        # No stale actor may survive into the next kickoff.
        self.fullback_role_active = False
        if getattr(self, "role", None) == "FULLBACK":
            self.role = None
        # Reset fullback absorb/arrival state — no stale proximity
        # counters or absorb gate may survive into the next kickoff.
        self.frames_within_absorb_x = 0
        self.frames_within_absorb_y = 0
        self.fullback_absorb_allowed = False
        self.fullback_arrived = False
        # Reset the fullback tackle-eligibility signal — the actor is not
        # a fullback at kickoff, so a stale True must not survive.  It is
        # re-stamped while the actor holds the role each frame.
        self.can_acquire_tackle = False
        self.support_leader = None
        self.kick_lane = 0
        self.kick_lane_offset_y = 0.0
        self.kick_stagger_x = 0.0

        self.in_contact = False
        self.is_tackler = False
        self.tackle_locked = False
        self.pre_tackle_event = None
        self.reach_event = None
        self.wrap_event = None

        self.solid = True
        self.anim_state = ""
        self.tes_locked = False
        self.attack_role = "none"
        self.facing_override = False
        self.facing_override_angle = 0.0

        # Reset PHASE 1 attach flags (cleared by _detach_all_tacklers too)
        self.tes_attached = False
        self.attach_slot = None

        # Reset TES tackler role (selected Tackler_1/2/3 for previous tackle)
        self.tes_tackler_role = None

        # ── TACKLER MOVEMENT CONTRACT reset (spec G) ──
        # Kickoff reset returns every player to the NORMAL movement
        # contract: no stale TACKLER mode / lunge / target fields may
        # survive into the next kickoff.
        self.movement_mode = None
        self.speed_target = 0.0
        self.vx_target = 0.0
        self.vy_target = 0.0
        self.heading_target = 0.0
        self.lunge_active = False
        self.lunge_speed = 0.0
        self.lunge_duration = 0.0
        self.collision_priority = None

        # Reset marker freeze timer
        self.marker_freeze_timer = 0.0

        # Reset kicker designation (re-stamped by formation application)
        self.is_kicker = False

        # Reset target stabiliser memory (no stale geometry targets from
        # the previous sequence may survive into the next kickoff).
        if self._target_stabiliser is not None:
            self._target_stabiliser.reset()

        # Reset global defender reaction governor
        self.kickoff_chase_speed = 0.0
        self.defence_reaction_remaining_s = 0.0

        # Reset post-catch defensive line clamp
        self.defended_speed_cap_mps = 0.0
        self.defended_cap_remaining_s = 0.0
        self.defended_cap_anchor_x = 0.0
        self.defended_cap_blend_s = 0.0

        # Reset global defensive top-speed ceiling (re-stamped by OPE)
        self.max_defensive_speed_mps = 0.0

        # Reset geometry smooth-damp velocity state (no stale damp
        # momentum may survive into the next kickoff).
        self._sd_vx = 0.0
        self._sd_vy = 0.0

        # Reset prev_x
        self.prev_x = self.x

        # Reset TES depth-lock (no stale depth pin may survive a kickoff)
        self.depth_locked = False
        self.depth_lock_x = 0.0
        self.depth_lock_y = None

        # Reset drive-end authoritative slot flag
        self.authoritative_until_frame = -1

        # Reset the debt-aware retreat gait (Retreat Geometry Phase 1): no
        # stale gait, mode, dwell clock or backpedal facing latch may survive
        # into the next kickoff — a reused Player must re-bootstrap its gait
        # and its turn-and-run permission from its live debt.
        self.retreat_locomotion = None
        self.retreat_locomotion_mode = None
        self.retreat_debt_m = None
        self.retreat_mode_changed_frame = None
        self.retreat_facing_policy = None

    def update_movement(self, raw_input_x: float, raw_input_y: float, dt: float) -> None:
        """Direct input-based movement (not used during gameplay — OPE sets targets)."""
        self._accelerate_toward_target(dt)
        self._clamp_position()
        self.body.sync()

    def set_pos(self, x: float, y: float, tag: str = "") -> None:
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0
        self._clamp_position()

    def _reaction_speed_cap(self, commanded_speed: float) -> float:
        """Effective speed ceiling imposed by the post-catch reaction governor.

        Uses the CURRENT stored remaining time (does not decrement).  During
        the hold phase the ceiling is exactly the captured kickoff chase
        speed; during the blend phase it lerps linearly to the commanded
        defence speed.  Returns commanded_speed when not reaction-limited.
        """
        remaining = self.defence_reaction_remaining_s
        if remaining <= 0.0:
            return commanded_speed
        total = self.DEFENCE_REACTION_HOLD_S + self.DEFENCE_REACTION_BLEND_S
        elapsed = total - remaining
        if elapsed <= self.DEFENCE_REACTION_HOLD_S:
            blend_t = 0.0
        else:
            blend_t = (elapsed - self.DEFENCE_REACTION_HOLD_S) / self.DEFENCE_REACTION_BLEND_S
        blend_t = max(0.0, min(1.0, blend_t))
        ceiling = self.kickoff_chase_speed + (commanded_speed - self.kickoff_chase_speed) * blend_t
        return min(commanded_speed, ceiling)

    def _defended_cap_ceiling(self, commanded_speed: float) -> float:
        """Effective speed ceiling imposed by the post-catch defensive line clamp.

        While ``defended_cap_remaining_s > 0``, caps at
        ``defended_speed_cap_mps``, then blends to the commanded speed over
        the final ``DEFENDED_CAP_BLEND_S`` seconds of the clamp window so the
        release is continuous.  Returns commanded_speed when not clamp-limited.
        """
        remaining = self.defended_cap_remaining_s
        if remaining <= 0.0 or self.defended_speed_cap_mps <= 0.0:
            return commanded_speed
        if remaining <= self.DEFENDED_CAP_BLEND_S:
            blend_t = remaining / self.DEFENDED_CAP_BLEND_S
            blend_t = max(0.0, min(1.0, blend_t))
            return min(commanded_speed, self.defended_speed_cap_mps +
                       (commanded_speed - self.defended_speed_cap_mps) * (1.0 - blend_t))
        return min(commanded_speed, self.defended_speed_cap_mps)

    def step_defence_reaction(self, dt: float) -> None:
        """Advance the post-catch reaction governor clock by exactly ONCE per frame.

        ALSO advances the post-catch defensive line clamp clock so the clamp
        hard-limit cannot outlive DEFENDED_CAP_HARD_LIMIT_S of wall-clock
        time, even if stability is never detected.  Called exactly once per
        OPEN_PLAY frame by OpenPlayEngine._update_defence().
        """
        if self.defence_reaction_remaining_s > 0.0:
            self.defence_reaction_remaining_s = max(
                0.0, self.defence_reaction_remaining_s - dt
            )
        if self.defended_cap_remaining_s > 0.0:
            self.defended_cap_remaining_s = max(
                0.0, self.defended_cap_remaining_s - dt
            )

    def distance_to(self, other) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def distance_to_point(self, x: float, y: float) -> float:
        return math.hypot(self.x - x, self.y - y)

    def _is_chase_state(self) -> bool:
        """True when this player's current MovementState is CHASE/SHOOT/RECOVER.

        TASK VI CLAMP BYPASS (2026-08-15): once the ball carrier is clearly
        past the defensive line, engaged defenders are forced to CHASE and
        must NOT be throttled by the post-catch defensive-line clamp — that
        clamp exists to slow the line during the kickoff-catch → first stable
        OPEN_PLAY alignment, and would otherwise kill the diagonal pursuit's
        forward velocity (the "line freezes until alignment" symptom).  The
        unified defensive top-speed ceiling (`max_defensive_speed_mps`) is
        still enforced as the final backstop, so the bypass can never allow
        a runaway.

        RECOVER (2026-09-21) joins the bypass for the same structural reason:
        it is a HIGH-VELOCITY re-covery of owed ground.  The alignment clamp
        is a kickoff-catch line-shape governor, and letting it slow a body
        that is trying to get back onto its slot is precisely the "the
        fullback never gets home" immobility the state removes.  The unified
        defensive ceiling still bounds it exactly as it bounds CHASE.
        """
        ms = getattr(self, "movement_state", None)
        if ms is None:
            return False
        name = getattr(ms, "name", None) or str(ms)
        return name in ("CHASE", "SHOOT", "RECOVER")

    def _uses_smooth_damp(self) -> bool:
        """True when this player integrates geometry movement with smooth_damp.

        Geometry-driven defensive movement (HOLD/DRIFT/SLIDE/RETREAT in
        OPEN_PLAY) uses Unity-style smooth_damp — NOT proportional gain.
        CHASE/SHOOT pursuit keeps the existing acceleration physics, and
        non-defenders (no stamped defensive ceiling) keep the lerp path.

        PTB-ERA GATE (2026-08-16): smooth-damp is DISABLED during
        TACKLE_RELEASE / PTB_READY / PTB / POST_PTB_RESET /
        POST_RUCK_OPEN_PLAY / RESET_LINE so the phase-owned P7 blanket
        retreat to ruck+10 runs on the fast accel-lerp path and COMPLETES
        before OPEN_PLAY re-shapes the line into role depths.  Leaving the
        critically-damped glide on in those phases is what produced the
        "98% done -> jittery crawl" as retreaters settled a few cm/frame
        inside the PTB window.
        """
        if self.max_defensive_speed_mps <= 0.0:
            return False
        ms = getattr(self, "movement_state", None)
        if ms is not None:
            name = getattr(ms, "name", None) or str(ms)
            # ── RETREAT IS BULK REPOSITIONING, NOT A GEOMETRY GLIDE ──
            # (2026-09-18, measured) RETREAT drives a body ten metres to the
            # anchor-compliance line; it is not a role-slot glide.  Running it
            # through the critically-damped smooth-damp port converts the
            # remaining distance into a tiny fraction of the commanded speed
            # -- measured over 19k obligated body-frames: OPEN_PLAY closed at
            # 0.360 m/s against a commanded 7.071 m/s (5.1%), with 83% of
            # frames below half command, so the body never reached the
            # completion threshold (`x >= anchor`) and the 10 m obligation ran
            # 5-19 s.  The accel-lerp path reaches the commanded speed within
            # a few frames and CONVERGES -- RELEASE, which already runs on it
            # (the PTB-era gate below disables smooth-damp there), closed at
            # 6.613 of 7.001 m/s (94.4%).  RETREAT therefore takes the same
            # fast path, exactly as the PTB-era rationale below requires: the
            # blanket retreat to the anchor line must COMPLETE.
            # RECOVER (2026-09-21): the same reasoning as RETREAT — RECOVER
            # is bulk owed-ground repositioning, not a role-slot geometry
            # glide.  It exists precisely to take the fast accel-lerp path,
            # so the critically-damped port (which converts remaining
            # distance into a small fraction of the commanded speed) must
            # never capture it.
            if name in ("CHASE", "SHOOT", "RETREAT", "RECOVER"):
                return False
        world = getattr(self, "world", None)
        if world is not None:
            sub = getattr(world, "sub_state", None)
            if sub is not None:
                phase_name = getattr(sub, "value", sub)
                if phase_name in self.PTB_SMOOTH_DAMP_DISABLED_SUB_STATES:
                    return False
        return True

    def _accelerate_toward_target(self, dt: float) -> None:
        """Move toward target_x/target_y using vx/vy.

        Geometry-driven defensive movement (HOLD/DRIFT/SLIDE/RETREAT) uses
        Unity-style smooth_damp integration (2026-08-09) — NOT proportional
        gain — so the defender converges onto its role-slot target with
        critically damped motion and zero per-frame target snapping.
        CHASE/SHOOT pursuit and non-defenders keep the existing accel-lerp
        physics.
        """
        dx = self.target_x - self.x
        dy = self.target_y - self.y
        dist = math.hypot(dx, dy)

        speed = self.move_speed
        if speed <= 0.0:
            # GEOMETRY AUTHORITY LOCK / BOUNDED TIMING (2026-09-04): a
            # defensive-ceiling carrier with move_speed <= 0 is in a
            # velocity-hold state (HOLD / timing_locked / freeze) — it must
            # HALT, never fall back to max_speed_mps and race toward the
            # (authority) geometry target.  The fallback is for non-defenders
            # only (no defensive ceiling).
            if self.max_defensive_speed_mps > 0.0:
                self.vx = 0.0
                self.vy = 0.0
                return
            speed = self.max_speed_mps

        # ── GLOBAL DEFENDER REACTION GOVERNOR (cap-only) ──
        # Caps effective speed after a KICKOFF catch so defenders blend from
        # kickoff chase speed → defence speed instead of jumping 0.8x→1.0x in
        # one frame.  Only armed on defenders by catch_system._perform_catch();
        # every other actor has remaining == 0 and passes straight through.
        # NOTE: The remaining-time decrement is NOT done here — Player.update()
        # may be called MULTIPLE times per frame (e.g. `_update_defence` plus
        # `_apply_defence_sdm` retreat updates).  The clock is advanced exactly
        # once per OPEN_PLAY frame by OpenPlayEngine._update_defence() via
        # step_defence_reaction().  This method only applies the cap so the
        # hold window cannot be burned at 2x speed by double updates.
        if self.defence_reaction_remaining_s > 0.0:
            speed = self._reaction_speed_cap(speed)

        # ── POST-CATCH DEFENSIVE LINE CLAMP (cap-only) ──
        # Applied AFTER every writer (pursuit reaction, depth reclassification,
        # retreat compliance/SDM) has set move_speed, but BEFORE movement
        # integration.  While armed (defended_cap_remaining_s > 0) the
        # effective speed can never exceed the governor's maximum intended
        # defensive line speed, so the pursuit/depth/retreat writers cannot
        # visually "turbo" defenders into position during the kickoff-catch →
        # first stable OPEN_PLAY transition.  The cap auto-disengages once
        # structured OPEN_PLAY is stable (managed in DefensiveBehaviourUnified)
        # and has a hard time ceiling so it cannot outlive the transition window.
        # The clock decrement is NOT done here — it advances once per frame
        # in step_defence_reaction() so double-updated defenders (line +
        # SDM retreat) do not burn the window at 2x speed.
        # TASK VI (2026-08-15): engaged CHASE/SHOOT pursuit is exempt from
        # the clamp — it is an ALIGNMENT governor, and once the BC is clearly
        # past the line engaged defenders must chase diagonally at pursuit
        # speed, not wait for the front three.  The unified ceiling below
        # still caps every writer.
        if self.defended_cap_remaining_s > 0.0 and not self._is_chase_state():
            speed = self._defended_cap_ceiling(speed)

        # ── GLOBAL DEFENSIVE TOP-SPEED CEILING (FINAL HARD CAP) ──
        # The unified ceiling (DefenceTuners.max_defensive_speed_mps,
        # stamped onto this defender) is the authoritative maximum for
        # EVERY defensive writer.  This cap runs last, AFTER every writer
        # and after the governor/clamp/reaction caps, so no phase (kickoff
        # chase, pursuit, collapse, retreat, SDM, structured OPEN_PLAY,
        # post-catch window) can accelerate a defender beyond the ceiling.
        # 0.0 = not a defender -> bypass.
        if self.max_defensive_speed_mps > 0.0:
            speed = min(speed, self.max_defensive_speed_mps)

        # ── SMOOTH DAMP PATH (geometry-driven defensive movement) ──
        # Movement uses smooth_damp, not proportional gain: each axis is a
        # critically-damped ODE toward the target, so a role-slot swap or a
        # sub-metre writer conflict produces a smooth glide instead of a
        # velocity spike / overshoot.  The per-player velocity state
        # (_sd_vx/_sd_vy) carries the damp momentum across frames.
        if self._uses_smooth_damp():
            smooth_time = getattr(self, "_geometry_smooth_time_s", 0.12)
            omega = 2.0 / max(0.0001, smooth_time)
            x = omega * dt
            exp_factor = 1.0 / (1.0 + x + 0.48 * x * x + 0.235 * x * x * x)
            max_velocity = max(0.0, speed)

            # X axis
            change = self.target_x - self.x
            temp = (self._sd_vx + omega * change) * dt
            self._sd_vx = (self._sd_vx - omega * temp) * exp_factor
            vx_damp = temp * exp_factor
            if abs(vx_damp) > max_velocity:
                vx_damp = math.copysign(max_velocity, vx_damp)
            self._sd_vx = vx_damp

            # Y axis
            change_y = self.target_y - self.y
            temp_y = (self._sd_vy + omega * change_y) * dt
            self._sd_vy = (self._sd_vy - omega * temp_y) * exp_factor
            vy_damp = temp_y * exp_factor
            if abs(vy_damp) > max_velocity:
                vy_damp = math.copysign(max_velocity, vy_damp)
            self._sd_vy = vy_damp

            self.vx = self._sd_vx
            self.vy = self._sd_vy
            self.x += self.vx * dt
            self.y += self.vy * dt
            return

        # ── LEGACY LERP PATH (CHASE/SHOOT pursuit + non-defenders) ──
        # ── DEBT-AWARE RETREAT GAIT (Retreat Geometry Phase 1) ──
        # A body carrying a retreat-locomotion profile integrates the gait
        # the retreat authority issued: its own accel/decel lerp factors,
        # its own X/Y direction weighting (lateral priority) and the
        # no-reverse X clamp.  Absent a profile every value below is the
        # legacy one, so a body that is not retreating is byte-identical.
        _loco = getattr(self, "retreat_locomotion", None)
        if _loco is not None:
            accel_rate = _loco.accel
            decel_rate = _loco.decel
        else:
            accel_rate = self.accel
            decel_rate = self.decel

        if dist < 0.05:
            factor = min(decel_rate * dt * 60.0, 1.0)
            self.vx *= (1.0 - factor)
            self.vy *= (1.0 - factor)
            if math.hypot(self.vx, self.vy) < 0.01:
                self.vx = 0.0
                self.vy = 0.0
        else:
            nx = dx / dist
            ny = dy / dist
            if _loco is not None:
                # Direction blend: lateral_priority 0 keeps the gait on X
                # (drive straight back), 1 settles the lateral slot while
                # holding X.  Normalised, so the weighting re-aims the
                # heading without changing the commanded speed — the gait's
                # speed_scale is what encodes "how fast".
                _lat = _loco.lateral_priority
                _dir_x = nx * (1.0 - _lat)
                _dir_y = ny * _lat
                _dir_len = math.hypot(_dir_x, _dir_y)
                if _dir_len > 0.001:
                    nx = _dir_x / _dir_len
                    ny = _dir_y / _dir_len
            desired_vx = nx * speed
            desired_vy = ny * speed
            factor = min(accel_rate * dt * 60.0, 1.0)
            self.vx += (desired_vx - self.vx) * factor
            self.vy += (desired_vy - self.vy) * factor
            current_speed = math.hypot(self.vx, self.vy)
            if current_speed > speed:
                self.vx *= speed / current_speed
                self.vy *= speed / current_speed

        # ── NO-REVERSE X CLAMP (retreat gait) ──
        # While the body still owes ground to its retreat target it may
        # never gain it back toward the ruck: a negative vx IS the
        # overshoot -> travel-direction reversal -> spin-turn mechanism the
        # gait removes.  Applied after the speed clamp so it cannot be
        # undone, and only while the issued profile says ground is owed.
        if _loco is not None and _loco.no_reverse and self.vx < 0.0:
            self.vx = 0.0

        self.x += self.vx * dt
        self.y += self.vy * dt

    def _update_facing(self) -> None:
        return

    def _update_physics(self, dt: float) -> None:
        speed = math.hypot(self.vx, self.vy)
        self.momentum = self.mass * speed

    def _clamp_position(self) -> None:
        # ── BC POSITION OVERRIDE LOCK (2026-08-05) ──
        # During TACKLE_DRIVE / TACKLE_DOMINANCE the ball carrier's x is
        # owned exclusively by TES physics (deterministic drive step +
        # variation in _update_tackle_drive, dominance push in
        # _update_tackle_dominance).  No later system may overwrite it.
        # This clamp is the only unconditional post-physics `self.x =` write
        # in Player, so skip it for the BC while TES is driving so a clamp
        # can never snap the BC back and cancel boosted drive metres.
        world = getattr(self, "world", None)
        if world is not None and self is getattr(world, "ball_carrier", None):
            sub = getattr(world, "sub_state", None)
            phase_name = getattr(sub, "value", sub)
            if getattr(world, "tes_active", False) and phase_name in (
                "tackle_drive",
                "tackle_dominance",
            ):
                return
        self.x = max(FIELD_MIN_X, min(FIELD_MAX_X, self.x))
        self.y = max(FIELD_MIN_Y, min(FIELD_MAX_Y, self.y))
