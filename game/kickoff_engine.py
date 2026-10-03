"""KickoffEngine — owns all kickoff setup, kick, ball physics, and chase logic.

Phase 1: pure extraction from run.py with zero behavioural changes.
Phase 2: own ball physics during kickoff.
Phase 3: own chase phase (KO_CHASE).

REDESIGN (Phase Audit 2026-07-18):
  - Catch lifecycle (CATCH_ATTEMPT, CATCH_CONFIRMED, EXIT) removed.
    check_for_catch() in world/catch_system.py is the sole catch detector.
  - world.phase is the top-level phase gate; world.in_kickoff is no longer
    written by this engine.
  - After CHASE is "ready" (catcher near landing point, ball descending),
    the engine stays in CHASE and lets the main loop's check_for_catch()
    detect the catch.
  - internal `_active` flag replaces world.in_kickoff for self-guarding.

KMC (Kickoff Movement Controller) — 2026-07-18:
  - Replaces direct-velocity _approach_landing() with target-based movement
    through Player.update() for ALL players during KICKOFF.
  - Moves primary catcher toward landing point (speed = max_speed_mps * 0.9).
  - Moves kicking-team chasers in chase lanes (speed = max_speed_mps * 1.0,
    capped by the shared post-catch clamp ceiling — see
    _kickoff_retreat_speed_mps()).
  - Moves support receivers behind catcher (speed = max_speed_mps * 0.7).
  - Players are unfrozen in kickoff() after formations are set.
  - Facing is driven by velocity through update_facing_unified().
  - No OPE/defence/attack/TES dependencies.
"""

from enum import Enum, auto
from world.world_state import GameWorldState, GameSubState, set_ball_carrier, GamePhase
from world.ruck_state import RuckState
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
    KICKING_LINE_OFFSET_M,
    KICKER_JERSEY_NUMBER,
)
from kick_off.placement_writer import apply as apply_fb_placement
from game.defence.defence_lvl_1.fullback_role import (
    assign_fullback_role,
    apply_fullback_geometry,
    clear_fullback_post_tackle_return_for,
    KICKOFF_FB_JERSEY,
)
from player.player import Player, PlayerState, Team as PlayerTeam
from player.positional_traits import apply_positional_traits
from player.movement_constants import (
    MASTER_MOVEMENT_SCALER,
    VELOCITY_DELTA_MAX,
    EMA_SMOOTHING_FACTOR,
)
from game.debug.fullback_audit import audit_fullback_state
from game.debug.defender_state_audit import run_defender_state_audit
from game.defence.defence_lvl_1.anchor_authority import (
    set_pre_ruck_anchor,
    reset_anchor_authority,
)
from player.movement_states import MovementState
from ball.kick_types import KickType
import math
import random


class KOPhase(Enum):
    SETUP = auto()
    PRE_KICK = auto()
    KICK = auto()
    IN_AIR = auto()
    CHASE = auto()


# ── KMC Constants ──────────────────────────────────────────────────────
CATCHER_SPEED_FACTOR    = 0.9   # Primary receiver speed multiplier
# CHASE_SPEED_FACTOR raised 0.8 -> 1.0 (2026-08-04): with post_catch_cap_mps
# at 8.0 the chaser speed is min(max_speed_mps * 1.0, 8.0) = 8.0, identical to
# the post-catch defensive line clamp.  Previously the 0.8 floor capped the
# chase band at 7.36, visibly slower than the 8.0+ open-play line after catch.
CHASE_SPEED_FACTOR      = 1.0   # Kicking-team chasers
SUPPORT_SPEED_FACTOR    = 0.7   # Support receivers behind catcher
CATCHER_STOP_RADIUS     = 0.5   # Distance to target before slow‑down
SUPPORT_OFFSET_X        = 5.0   # Support behind landing point
CHASE_OFFSET_X          = 3.0   # Chase lane ahead of ball
# ── end KMC constants ──────────────────────────────────────────────────

# ── PRE-KICK RUSH (2026-09-09) ─────────────────────────────────────────
# The kicking line sits a little back behind halfway (halfway + 2 m) and the
# 11 non-fullback defenders begin moving FORWARD before the ball is kicked so
# the chasers have a rolling start.  The rush lasts KICKOFF_PRE_KICK_RUSH_S;
# at KICKOFF_PRE_KICK_SPEED_FACTOR of the post-catch chase ceiling the chasers
# cross halfway (~2 m) at ~half speed.  The kicker stays frozen on the ball;
# the fullback moves via fullback geometry only (never a rush target).
KICKOFF_PRE_KICK_RUSH_S: float = 0.5
KICKOFF_PRE_KICK_SPEED_FACTOR: float = 0.5
# How far ahead of the ball the rush target sits.  With the line at
# halfway + 2.0 and a 0.5 s rush at half the 8.0 m/s ceiling (4.0 m/s), the
# chasers cover ~2.0 m, crossing halfway at half speed.
KICKOFF_PRE_KICK_LEAD_M: float = 5.0


class KickoffEngine:
    def __init__(self, world, ball, field):
        self.world = world
        self.ball = ball
        self.field = field
        self.phase = KOPhase.SETUP
        self.chase_timer = 0.0
        self.wait_timer = 0.0
        self.catch_delay = 0.0
        # ── PRE-KICK RUSH STATE (2026-09-09) ──
        # kickoff() arms the rush timer and stores the launch params; the
        # ball is NOT kicked until _perform_kick() fires once the timer
        # elapses.  The non-fullback defenders rush forward during the window.
        self.pre_kick_timer = 0.0
        self._kick_power = 0.0
        self._kick_angle_deg = 0.0
        self._kick_direction_deg = 0.0
        self._active = False   # internal flag: True from kickoff() until catch
        # KICKOFF FORMATION LINE (2026-08-11): the kickoff fullback's deep
        # anchor derives from the FLAT FORMATION LINE (halfway + offset),
        # never from the ball-derived defensive_line_anchor.  Re-stamped in
        # reset_for_kickoff(); initialised here so direct kickoff() calls
        # without a prior reset still have a valid value.
        self.kickoff_line_x = self.field.halfway_m + KICKING_LINE_OFFSET_M
        # Change-detection marker for the one-shot FB_SELECT kickoff log.
        self._logged_fb_selected_id = None

    def create_teams(self):
        """Create two teams of 13 players each on the halfway line."""
        kickers = []
        receivers = []

        self.world.attacking_direction = 1
        self.world.defending_left = False

        for i in range(13):
            y = 5 + i * (self.field.width_m - 10) / 12
            p = Player(self.field.halfway_m, y, player_id=200 + i)
            # Stable jersey identity (jersey 1 = first defender #200) so
            # fullback_role.KICKOFF_FB_JERSEY can pin the kickoff FB
            # without depending on render/list order.
            p.jersey_number = i + 1
            # ── POSITIONAL PHYSICS PROFILE (Stage 4) ──
            # The jersey IS the body's position, so the mapper is applied
            # HERE, immediately after the jersey is stamped — its whole input
            # is the jersey number.  It fills the weight / fend / tackle-skill
            # slots the contact model already reads (mass_kg / fend_trait /
            # tackle_skill_trait) so CarrierSnapshot.of(bc) and the
            # preservation layer's row builder find a live positional profile
            # instead of their neutral 1.0 fallbacks.  A body the mapper does
            # not map (no jersey / a jersey outside every declared band) is
            # left completely untouched.
            apply_positional_traits(p)
            p.team = PlayerTeam.KICKING
            p.facing = math.pi
            p.state = PlayerState.IDLE
            kickers.append(p)

        for i in range(13):
            y = 5 + i * (self.field.width_m - 10) / 12
            p = Player(self.field.halfway_m, y, player_id=100 + i)
            p.jersey_number = i + 1
            # POSITIONAL PHYSICS PROFILE (Stage 4) — see the kicking-team loop
            # above: both teams are real 13-man sides, so both take the
            # positional profile their jersey number declares.
            apply_positional_traits(p)
            p.team = PlayerTeam.RECEIVING
            p.facing = 0
            p.state = PlayerState.IDLE
            receivers.append(p)

        self.world.kicking_team = kickers
        self.world.receiving_team = receivers
        self.world.attacking_team = receivers
        self.world.defending_team = kickers
        self.world.players = kickers + receivers

        # ── DEFENDER FACING FLAG (2026-08-04) ──
        # Kicking team = defending team.  This flag gates the PTB-window
        # defender-facing override in update_facing_unified() — defenders
        # re-aim at the ball carrier when nearly stationary; attackers
        # always keep velocity-driven facing.  Re-stamped every kickoff.
        for p in kickers:
            p.is_defender = True
        for p in receivers:
            p.is_defender = False

    def reset_for_kickoff(self):
        """Reset ball, world, teams, and apply formations for a new kickoff."""
        b = self.ball
        w = self.world

        # ═══ FULL KICKOFF RESET — clear stale state carry-over from previous kickoff ═══
        # anchor and ruck_x MUST be None so the catch handler computes fresh values
        # from the new BC position.  Stale values from kickoff #1 cause BC teleport
        # on kickoff #2 when geometry snaps to old positions.
        # TASK G ANCHOR AUTHORITY (Layer 6): a kickoff reset is a fresh play —
        # the anchor lock is dropped and the anchor nulled so the PRE-RUCK
        # (unlocked, ball-derived) writer takes over until catch.
        reset_anchor_authority(w)
        w.defensive_line_anchor = None
        w.ruck_x_m = None
        w.first_tackle_not_made = True
        # STAGE 3B RUCK-PIVOT TIMING: a fresh kickoff has NO ruck.  Band
        # formation re-arms only when TES enters TACKLE_INIT.
        w.ruck_established = False
        w.in_pursuit_mode = False
        w.phase = GamePhase.KICKOFF
        w.sub_state = GameSubState.KICKOFF_START

        # ── FULLBACK STATE RESET (2026-08-08) ──
        # No stale actor / anchor may survive from the previous sequence into
        # the next kickoff.  Players are rebuilt (each reset clears
        # fullback_role_active); this clears the published world fields.
        w.fullback_actor_id = None
        w.fullback_anchor_x = None
        w.fullback_anchor_y = None
        # TASK J AUDIT FIELDS (2026-08-12): publish fullback absorb/arrival
        # state from the kickoff side too — zeroed during flight because
        # absorption is an OPEN_PLAY-only concept (the kickoff FB sits ON
        # the flat line, which would otherwise self-absorb mid-flight).
        w.fullback_role_active = False
        w.fullback_absorb_allowed = False
        w.fullback_frames_within_absorb_x = 0
        w.fullback_frames_within_absorb_y = 0
        w.fullback_arrived = False
        # HIGH-VELOCITY RECOVERY LEVER (2026-09-21): a fresh kickoff has no
        # owed-ground debt and the airborne FB is owned by the kickoff flight
        # geometry, so the OPEN_PLAY recovery lever starts disarmed; it is
        # re-armed by DefenceManager.update_open_play once open play is live.
        w.fullback_recover_allowed = False
        # ── POST-TACKLE RETURN WINDOW: ENDED BY THE RESET (2026-10-02) ──
        # The post-tackle return is TACKLE-CYCLE state, and a kickoff reset
        # ends the cycle.  Sweep every defending body so no actor carries the
        # window across the restart -- before the lifecycle existed the flag
        # lived forever, and a restart is the one boundary where a body that
        # was mid-return is guaranteed not to be mid-return any more.
        clear_fullback_post_tackle_return_for(w)

        # ═══ OPEN-PLAY ENGINE RESET — hard-clear all engine-owned and ═══
        # world-level play state before the next kickoff.  The players and
        # ball are rebuilt below, but OpenPlayEngine is a singleton whose
        # FSM (self._phase), TES context (tes_ctx/primary/joiners/dh),
        # PTB manager state, governor smoothed width, deferred D-series
        # writes, collision, and world-level TES/PTB/geometry fields would
        # otherwise leak into the next cycle — corrupting the first OPEN_PLAY
        # after catch (defender speed spike, BC freeze, DH jiggle, teleports,
        # clamp disarming).  This runs ONLY on the SPACE/R restart path.
        if self.world.open_play_engine is not None:
            self.world.open_play_engine.reset_for_kickoff()
        elif self.world.open_play is not None:
            self.world.open_play.reset_for_kickoff()

        w.in_kickoff = True   # legacy compat flag
        self._active = False

        b.x_m = self.field.halfway_m
        b.y_m = self.field.centre_m
        b.z_m = 1.0
        b.vx = b.vy = b.vz = 0.0
        b.grounded = False
        b.kicked = False
        b.possessed_by = None

        w.game_state = GameWorldState.OPEN_PLAY
        w.ball_live = False
        w.ball_in_play = False
        w.ball_owner = "NONE"
        w.ball_carrier = None
        w.kickoff_landing_x = 0.0
        w.kickoff_landing_y = 0.0

        w.tackle_count = 0
        w.ruck_y_m = 35.0
        w.ruck_state = RuckState.NONE
        w.ruck_timer = 0.0
        w.just_caught_this_frame = False
        w.frame_count = 0
        w.just_left_kickoff_timer = 0.0

        w.primary_catcher = None

        # KICKOFF FORMATION LINE (2026-08-11): stamped once per reset so
        # fullback selection and anchor geometry agree for the whole
        # sequence.  The kicking line = halfway + KICKING_LINE_OFFSET_M
        # (60 + 2 = 62); the kickoff fullback occupies the SAME line.
        self.kickoff_line_x = self.field.halfway_m + KICKING_LINE_OFFSET_M
        w.kickoff_engine_line_x = self.kickoff_line_x
        self._logged_fb_selected_id = None

        # KICK SIDE (2026-08-11): sign(kick_target_y - centre), default +1.
        # Re-seeded every reset so a stale side from the previous sequence
        # can never leak into the new kickoff.
        self.side_sign = 1

        # KICKOFF FORMATION / PLACEMENT STATE (2026-08-11)
        # Kicker identity and the FB placement stamp are re-stamped by
        # apply_kicking_team_formation() / the kickoff FB selection each
        # sequence.  Reset here so no stale values survive a restart.
        w.kickoff_kicker_id = None
        w.kickoff_side_sign = 1
        w.fb_placement_stamp = None
        w.fb_placement_applied = False

        self.chase_timer = 0.0
        self.wait_timer = 0.0
        self.catch_delay = 0.0

        self.create_teams()

        for p in w.players:
            p.reset_for_kickoff(world=w)

        apply_receiving_kickoff_formation(w, self.field)
        apply_kicking_team_formation(
            w, self.field,
            formation_line_x=self.kickoff_line_x,
            side_sign=self.side_sign,
        )

        for p in w.receiving_team:
            p.team = PlayerTeam.RECEIVING
        for p in w.kicking_team:
            p.team = PlayerTeam.KICKING

    def _def_top_speed(self):
        """Unified defensive top-speed ceiling for KMC chasers (m/s).

        Kicking-team chasers are the defending team; their chase speed must
        derive from ``tuners.max_defensive_speed_mps`` (the single source of
        truth), never the raw per-player max.  Uses the OPE engine tuners
        when available.
        """
        ope = getattr(self.world, "open_play_engine", None)
        tuners = getattr(ope, "def_tuners", None) if ope is not None else None
        if tuners is None:
            from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
            tuners = DefenceTuners()
        return getattr(tuners, "max_defensive_speed_mps", 8.3)

    def _seed_line_governor(self, anchor_x: float) -> float:
        """Seed the defensive line governor from the kickoff anchor (SPEC 2).

        During KICKOFF there is no ball carrier, so the governor cannot ramp
        normally.  ``seed_for_kickoff(anchor_x)`` sets the shared depth/width
        and a baseline line speed (base_line_speed_mps, clamped).  Returns
        the seeded line speed so the pre-catch retreat writer derives from
        the governor's line ramp instead of an independent scale.
        """
        ope = getattr(self.world, "open_play_engine", None)
        if ope is not None:
            ope.def_behaviour.line_governor.seed_for_kickoff(anchor_x)
            seeded = ope.def_behaviour.line_governor.line_speed
            if seeded is not None:
                return seeded
        # No OPE: fall back to the unified ceiling × 0.8 (kickoff chase band).
        return self._def_top_speed() * 0.8

    def _kickoff_retreat_speed_mps(self) -> float:
        """Shared chase ceiling for the pre-catch KMC chasers (m/s).

        NOTE (2026-08-04): this is deliberately NOT the governor's retreat
        scale (line_speed × 0.85).  The post-catch defensive line clamp
        (catch_system + OPE _stamp_defensive_speed_caps) caps every
        defender to ``tuners.post_catch_cap_mps`` (8.0 m/s) for the first
        OPEN_PLAY frames after reception.  Returning the same post-catch cap
        makes the pre-catch chase and the post-catch line speed identical,
        so there is no snap at the catch boundary.

        Still respects the unified defensive ceiling (max_defensive_speed_mps)
        and the per-player CHASE_SPEED_FACTOR floor applied by the caller, so
        slow defenders never exceed their physical max.
        """
        ope = getattr(self.world, "open_play_engine", None)
        tuners = getattr(ope, "def_tuners", None) if ope is not None else None
        if tuners is None:
            from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
            tuners = DefenceTuners()
        cap = getattr(tuners, "post_catch_cap_mps", 5.0)
        ceiling = getattr(tuners, "max_defensive_speed_mps", 8.3)
        return min(cap, ceiling)

    def _assign_kickoff_fullback(self, anchor_x: float):
        """Assign the dynamic fullback actor during KICKOFF (in flight).

        Mirrors the (now-deleted) kickoff branch of DefensiveBehaviourUnified
        that ran the shallow central fullback override: the actor sits ON the
        flat formation line offset toward the kick side (formation_line_x,
        centre + side_sign * 3.0), moves at 85% of the unified defensive
        ceiling, and is classified PASSIVE.  Publishing
        world.fullback_actor_id here keeps the audit / tooling in sync with
        the OPEN_PLAY publish in OpenPlayEngine.

        The actor is excluded from KMC chase movement by _update_chasers(),
        so the anchor is not clobbered for the duration of the kickoff.

        ANCHOR SOURCE (2026-08-11): the anchor is the FLAT FORMATION LINE
        (self.kickoff_line_x = halfway + KICKING_LINE_OFFSET_M = 62), NOT
        the ball-derived defensive_line_anchor passed in by the caller.  The
        FB anchor = (formation_line_x, centre + side_sign * 3.0) — the FB
        occupies the SAME flat line as the 12-man defensive line.

        PLACEMENT ORDERING (2026-08-11): selection -> emit stamp ->
        placement_writer.apply -> movement/render.  The stamp is emitted
        IMMEDIATELY after selection below and consumed synchronously by
        the placement writer, so no render or movement consumer can read
        a stale pre-kickoff position for the fullback actor.
        """
        w = self.world
        ball_obj = getattr(w, "ball", None)
        actors = [p for p in w.kicking_team if not getattr(p, "frozen", False)]
        fb_actor, (fb_tx, fb_ty) = assign_fullback_role(
            actors,
            ball_obj,
            self.kickoff_line_x,
            in_kickoff=True,
            formation_line_x=self.kickoff_line_x,
            side_sign=self.side_sign,
            kicker_id=getattr(w, "kickoff_kicker_id", None),
            world=w,
        )
        # FB_SELECT kickoff log (change-detection so it fires once per
        # sequence, or again if selection ever changes mid-flight).
        _fb_selected_id = fb_actor.player_id if fb_actor is not None else None
        if _fb_selected_id != self._logged_fb_selected_id:
            self._logged_fb_selected_id = _fb_selected_id
            _fb_log_x = f"{fb_tx:.2f}" if fb_tx is not None else "None"
            _fb_log_y = f"{fb_ty:.2f}" if fb_ty is not None else "None"
            print(
                f"FB_SELECT kickoff -> selected_id={_fb_selected_id} "
                f"formation_line_x={self.kickoff_line_x:.2f} "
                f"side_sign={self.side_sign} "
                f"fb_anchor=({_fb_log_x}, {_fb_log_y})"
            )
        if fb_actor is not None:
            fb_actor.target_x = fb_tx
            fb_actor.target_y = fb_ty
            # APPROACH-SPEED WRITE: REMOVED (2026-09-27)
            # The fullback now moves under the SHARED movement-state laws
            # (same acceleration table, same top-speed table, same turning,
            # same integration path) inside Player.update().  Its commanded
            # speed is derived from its MovementState by apply_movement_state
            # exactly as every other defender's is, so this fullback-only
            # scalar speed authority is gone.  The state stamp below
            # (MovementState.DRIFT) is what now selects the gait.
            fb_actor.depth_class = "PASSIVE"
            fb_actor.target_source = "fullback"
            # INTENT OWNERSHIP (Stage 1): the kickoff in-flight contract is
            # FLIGHT.  The geometry authority owns this actor's movement state
            # in every phase, so the assignment states the INTENT and the
            # controller resolves it to the same MovementState.DRIFT singleton
            # this line always wrote.
            from game.defence.defence_lvl_1.fullback_controller import (
                FullbackIntent,
                enter_fullback_intent,
                SOURCE_FLIGHT,
            )
            enter_fullback_intent(
                w,
                fb_actor,
                FullbackIntent.FLIGHT,
                source=SOURCE_FLIGHT,
            )
        if fb_tx is not None:
            w.fullback_actor_id = fb_actor.player_id if fb_actor is not None else None
            w.fullback_anchor_x = fb_tx
            w.fullback_anchor_y = fb_ty

            # ── PLACEMENT STAMP (2026-08-11) ──
            # Emit immediately after selection; the placement writer
            # consumes it before any movement/render consumer.
            w.fb_placement_stamp = {
                "player_id": _fb_selected_id,
                "x": fb_tx,
                "y": fb_ty,
            }
        else:
            w.fullback_actor_id = None
            w.fullback_anchor_x = None
            w.fullback_anchor_y = None
            w.fb_placement_stamp = None

        # ── CONSUME THE PLACEMENT STAMP (ordering barrier) ──
        # One-shot per sequence: hard-places the actor at the stamp
        # position and clears the stamp, so a later no-op call never
        # re-places the actor mid-flight.
        apply_fb_placement(w)

    def _apply_flight_fullback_geometry(self):
        """Re-assert the fullback geometry authority during ball flight.

        FLIGHT AUTHORITY (2026-09-21)
        -----------------------------
        During the kickoff the defensive pipeline is deliberately NOT run
        (KMC owns all kickoff movement), so the in-flight fullback target was
        written by this engine's ad-hoc ``_assign_kickoff_fullback`` block
        rather than by ``fullback_role.apply_fullback_geometry`` — the writer
        that owns fullback geometry in every other phase.  Calling the real
        authority here with ``kickoff_flight=True`` makes the airborne actor
        the SAME owned object it is in open play: one writer, one source of
        truth for its target, target_source, movement_state and move_speed.

        CONTRACT PRESERVED: the flight anchor is still
        ``(kickoff_engine_line_x, centre + kickoff_side_sign * 3.0)`` (the
        same values ``assign_fullback_role(in_kickoff=True)`` stamps), the
        state is still DRIFT, the approach-speed cap is still applied, and
        the actor is never frozen or re-tagged.  The one-shot
        ``placement_writer`` stamp behaviour is untouched — it is consumed by
        ``_assign_kickoff_fullback`` before this runs, so the hard placement
        still happens exactly once per sequence.

        Selection is NOT performed here: ``_assign_kickoff_fullback`` runs
        first in ``update()`` and owns who the actor is.  This method only
        re-asserts the geometry contract for whichever body carries the tag,
        including the case where the tag was already live from the previous
        frame.
        """
        w = self.world
        for p in getattr(w, "kicking_team", []) or []:
            if getattr(p, "fullback_role_active", False):
                apply_fullback_geometry(w, p, kickoff_flight=True)

    def _refresh_kickoff_anchor(self):
        """Keep the defensive line anchor tracking the ball during flight.

        ``kickoff()`` writes the anchor once from the initial ball x; each
        update frame the ball moves, so the anchor (and the fullback, which
        derives from it) must follow.  Always keep the anchor stable when the
        ball has stopped progressing (caught / grounded) — the catch handler
        recomputes it from the new BC anyway.
        """
        w = self.world
        if w.defensive_line_anchor is None or self.ball is None:
            return
        # TASK VII (2026-08-20) — AUTHORITY-ROUTED: the pre-ruck unlocked
        # anchor may ONLY be mutated through the pre-ruck writer.  The raw
        # field write bypassed set_pre_ruck_anchor() — the one anchor
        # mutation in the engine that did not go through an authority
        # function.  set_pre_ruck_anchor refuses to run once ruck_x_m is
        # set, and KICKOFF keeps ruck_x_m None by reset, so this is safe.
        set_pre_ruck_anchor(w, self.ball.x_m + 10.0)

    def update(self, dt):
        """Update kickoff lifecycle.

        Only runs while _active is True (set by kickoff(), cleared on catch).
        Manages: KICK → IN_AIR → CHASE (ball flight + all player movement via KMC).
        Catch detection is handled externally by check_for_catch().
        """
        if not self._active:
            return

        w = self.world

        # ── LIVE ANCHOR (2026-08-08) ──
        # The defensive line anchor tracks the ball during flight so the
        # kickoff fullback and the chase governor always read a fresh line
        # position.  The catch handler recomputes it from the new BC.
        self._refresh_kickoff_anchor()

        # ── KICKOFF FULLBACK ASSIGNMENT (2026-08-08) ──
        # The defensive pipeline (DefensiveBehaviourUnified.apply) is
        # deliberately NOT run during KICKOFF (freeze/assert contract and
        # KMC owns all kickoff movement).  Instead the kickoff fullback is
        # assigned HERE, from the same fullback_role module the structured
        # OPEN_PLAY pipeline uses, so the fullback geometry exists from the
        # moment the ball is in the air.
        #
        # ORDERING (2026-08-11): selection -> emit placement stamp ->
        # placement_writer.apply -> KMC phase handlers -> movement
        # integration.  The FB is assigned and PLACED BEFORE the phase
        # handlers below run, so _update_chasers()' fullback exclusion
        # (`fullback_role_active`) and every render/movement consumer
        # always see the placed kickoff FB — never a stale pre-kickoff
        # position or a one-frame chase-lane leak.
        anchor_x = w.defensive_line_anchor
        if anchor_x is None:
            # TASK VII (2026-08-20) — AUTHORITY-ROUTED: same pre-ruck
            # unlocked writer (_refresh_kickoff_anchor path above).  The
            # raw field write is eliminated; the authority refuses to run
            # once ruck_x_m is set (kickoff keeps it None), so this is
            # safe and identical in behaviour.
            anchor_x = self.ball.x_m + 10.0
            set_pre_ruck_anchor(w, anchor_x)
        self._assign_kickoff_fullback(anchor_x)

        if self.phase == KOPhase.SETUP:
            # SETUP does nothing yet — kickoff() arms the PRE_KICK rush
            self.phase = KOPhase.PRE_KICK

        elif self.phase == KOPhase.PRE_KICK:
            # PRE-KICK RUSH: the non-fullback kicking defenders move forward
            # at ~half speed for KICKOFF_PRE_KICK_RUSH_S; when the timer
            # elapses _perform_kick() kicks the ball and hands off to
            # KICK -> IN_AIR -> CHASE.
            self._update_pre_kick_rush(dt)

        elif self.phase == KOPhase.KICK:
            # ball has just been kicked — move to IN_AIR
            self.phase = KOPhase.IN_AIR

        elif self.phase == KOPhase.IN_AIR:
            # AIRBORNE FLIGHT — FULLBACK GEOMETRY AUTHORITY (2026-09-21).
            self._apply_flight_fullback_geometry()
            # KMC: move all players during ball flight
            self._update_primary_catcher(dt)
            self._update_chasers(dt)
            self._update_support_receivers(dt)
            # Transition to CHASE when ball is descending
            if self.ball.vz < 0:
                self.phase = KOPhase.CHASE

        elif self.phase == KOPhase.CHASE:
            self.chase_timer += dt

            # AIRBORNE FLIGHT — FULLBACK GEOMETRY AUTHORITY (2026-09-21).
            self._apply_flight_fullback_geometry()
            # KMC: continue moving all players
            self._update_primary_catcher(dt)
            self._update_chasers(dt)
            self._update_support_receivers(dt)

        # ── FULLBACK MOVEMENT INTEGRATION (2026-08-08) ──
        # KMC's chase loop skips the fullback actor (it holds the anchor,
        # not a chase lane), so the actor's position must be integrated
        # here via the same Player.update() accelerator the OPE pipeline
        # uses.  The target stabiliser smooths the anchor drift (DRIFT
        # movement_state), so the actor glides into the deep central
        # kickoff position without oscillation.
        for p in w.kicking_team:
            if getattr(p, "fullback_role_active", False) and not p.frozen:
                p.update(dt)

        # ── FULLBACK + DEFENCE AUDIT (self-limiting) ──
        # Frame-accurate snapshot of the KICKOFF state: in_kickoff flag,
        # per-defender flags, the assigned fullback actor + WHY candidates
        # are rejected.
        audit_fullback_state(w)

        # ── DEFENDER-STATE AUDIT — KICKOFF LANES (self-limiting) ──
        # Verifies the fullback actor holds the flat-line anchor
        # (target_source == "fullback" + world.fullback_anchor_*) and every
        # KMC chaser keeps its inertial lane (target_y ≈ own y).  Defence AI
        # never runs during KICKOFF, so there is no BC-lane targeting to
        # expect — a deviation here means a writer leaked into KMC's lanes.
        run_defender_state_audit(w)

    # ── KMC: Primary catcher ──────────────────────────────────────────

    def _update_primary_catcher(self, dt):
        """Move the designated catcher toward the landing point.

        Uses player.update() for proper acceleration/deceleration and
        velocity-driven facing.  Decelerates when close to landing point
        to avoid running past before the ball descends.
        """
        catcher = self.world.primary_catcher
        if catcher is None:
            return

        lx = self.world.kickoff_landing_x
        ly = self.world.kickoff_landing_y
        dx = lx - catcher.x
        dy = ly - catcher.y
        dist = math.hypot(dx, dy)

        # Set target to landing point
        catcher.target_x = lx
        catcher.target_y = ly

        if dist > CATCHER_STOP_RADIUS:
            # Full approach speed
            catcher.move_speed = catcher.max_speed_mps * CATCHER_SPEED_FACTOR
        else:
            # Near landing point: slow to walking speed so we don't
            # run past and out of catch radius before ball descends
            catcher.move_speed = catcher.max_speed_mps * 0.3

        catcher.update(dt)

    # ── KMC: Kicking-team chasers ────────────────────────────────────

    def _update_chasers(self, dt):
        """Move kicking-team players in chase lanes toward the ball.

        Each chaser's target Y is their current Y (inertial lane), with
        target X set ahead of the ball.  This creates a realistic spread
        of chasers converging on the receiver zone.
        """
        ball_x = self.ball.x_m
        chase_x = ball_x + CHASE_OFFSET_X

        # Seed the line governor from the kickoff anchor (idempotent per
        # frame) so the pre-catch chase shares the governor's seeded line
        # ramp — the same ramp the structured line uses after the catch.
        # The speed CEILING itself comes from _kickoff_retreat_speed_mps(),
        # which returns the post-catch clamp ceiling (8.0) so chasers move
        # at the same speed before and after the catch.
        anchor_x = self.world.defensive_line_anchor
        if anchor_x is None:
            anchor_x = getattr(self.world, "ruck_x_m", None) or self.ball.x_m
            anchor_x = anchor_x + 10.0
        self._seed_line_governor(anchor_x)
        retreat_speed = self._kickoff_retreat_speed_mps()
        # GLOBAL DEFENSIVE TOP-SPEED CEILING: kickoff chase speed is capped
        # by the shared post-catch clamp ceiling — never the raw per-player
        # max, never an independent cap.
        for p in self.world.kicking_team:
            # FULLBACK EXCLUSION (2026-08-08): the dynamic kickoff fullback
            # holds its deep central anchor — KMC must not clobber it
            # with a chase-lane target.  Its movement is integrated by
            # _assign_kickoff_fullback() after the phase handlers.
            if getattr(p, "fullback_role_active", False):
                continue
            p.target_x = chase_x
            p.target_y = p.y
            p.move_speed = min(
                p.max_speed_mps * CHASE_SPEED_FACTOR,
                retreat_speed,
            )
            p.update(dt)

    # ── KMC: Support receivers ───────────────────────────────────────

    def _update_support_receivers(self, dt):
        """Move non-primary receivers into support positions behind the catcher.

        Support players move to a position behind the landing point, spread
        laterally relative to their starting position for natural spacing.
        """
        catcher = self.world.primary_catcher
        landing_x = self.world.kickoff_landing_x
        landing_y = self.world.kickoff_landing_y

        for p in self.world.receiving_team:
            if p is catcher:
                continue

            support_x = landing_x - SUPPORT_OFFSET_X
            # Lateral spread: maintain relative Y spacing but converge
            # toward the landing lane
            support_y = landing_y + (p.y - landing_y) * 0.5

            p.target_x = support_x
            p.target_y = support_y
            p.move_speed = p.max_speed_mps * SUPPORT_SPEED_FACTOR
            p.update(dt)

    # ── Kickoff execution ────────────────────────────────────────────

    def kickoff(self):
        """Execute the kickoff: set formations, kick the ball, activate KMC.

        Sets world.phase = KICKOFF, kickoff_engine._active = True.
        Ball trajectory and KMC player movement run during KICKOFF phase;
        check_for_catch() in the main loop detects the catch.

        After the ball is kicked and the primary catcher is selected, all
        relevant players are unfrozen and given movement targets via KMC.
        """
        w = self.world
        b = self.ball
        f = self.field

        w.phase = GamePhase.KICKOFF
        w.in_kickoff = True   # legacy compat flag
        self._active = True

        w.game_state = GameWorldState.OPEN_PLAY
        w.sub_state = GameSubState.OPEN_PLAY

        # ── KICK TARGET + SIDE SIGN (2026-08-11) ──
        # The kick landing target is chosen BEFORE the formations so the
        # flat line / fullback can bias the 6-defender side and the FB's
        # 3 m lateral offset toward the kick side.
        forward_dir = -1

        kick_distance = random.uniform(50.0, 55.0)
        target_x = b.x_m + forward_dir * kick_distance
        target_y = random.uniform(10.0, f.width_m - 10.0)

        # side_sign = sign(kick_target_y - centre); default +1 on centre.
        if target_y > f.centre_m:
            self.side_sign = 1
        elif target_y < f.centre_m:
            self.side_sign = -1
        else:
            self.side_sign = 1

        apply_receiving_kickoff_formation(w, f)
        apply_kicking_team_formation(
            w, f,
            formation_line_x=self.kickoff_line_x,
            side_sign=self.side_sign,
        )

        print(
            f"AUDIT kickoff_line_x={self.kickoff_line_x:.2f} "
            f"side_sign={self.side_sign:+d} target_y={target_y:.2f} "
            f"kicker_id={getattr(w, 'kickoff_kicker_id', None)}"
        )

        # ── KICKOFF DEFENSIVE LINE ANCHOR (2026-08-08) ──
        # The football is kicked from halfway (x=60) toward the LEFT tryline.
        # During KICKOFF there is no ball carrier, so the defensive line anchor
        # is derived from the BALL's live x (ball_x + depth).  reset_for_kickoff()
        # nulls the anchor so the catch handler recomputes it from the new BC;
        # writing it here (and refreshing each update frame) gives the kickoff
        # fullback assignment and the chase a valid anchor geometry for the
        # entire in-flight period.
        set_pre_ruck_anchor(w, b.x_m + 10.0)
        w.fullback_actor_id = None
        w.fullback_anchor_x = None
        w.fullback_anchor_y = None

        for p in w.receiving_team:
            p.team = PlayerTeam.RECEIVING
        for p in w.kicking_team:
            p.team = PlayerTeam.KICKING

        sx = b.x_m
        sy = b.y_m
        dx = target_x - sx
        dy = target_y - sy
        distance = math.hypot(dx, dy)
        direction_deg = math.degrees(math.atan2(dy, dx))

        g = 9.8
        # HANG TIME (2026-09-20): raised 2.8 -> 3.1 s.  T sets both the
        # launch angle (vertical_speed = 0.5 g T) and the pace
        # (horizontal_speed = distance / T), so a longer T is simply more
        # loft onto the SAME target distance -- the ball hangs an extra
        # beat and the chase gets longer to compress underneath it before
        # the reception.
        T = 3.1
        horizontal_speed = distance / T
        vertical_speed = 0.5 * g * T
        power = math.hypot(horizontal_speed, vertical_speed)
        angle_rad = math.atan2(vertical_speed, horizontal_speed)
        angle_deg = math.degrees(angle_rad)

        angle_deg += random.uniform(-2.0, 2.0)
        power *= random.uniform(0.95, 1.05)
        power = min(power, 28.0)

        print(
            f"AI KICK: dist={distance:.2f}m, T~{T:.2f}s, "
            f"power={power:.2f}, angle={angle_deg:.2f}, dir={direction_deg:.2f}"
        )

        # ── STORE LAUNCH PARAMS (consumed by _perform_kick() when the
        #    pre-kick rush completes) ──
        self._kick_power = power
        self._kick_angle_deg = angle_deg
        self._kick_direction_deg = direction_deg

        # ── PRE-KICK RUSH (2026-09-09) ──
        # Seed the line governor + rush ceiling exactly as the chase does so
        # the rush speed matches the post-catch clamp ceiling, then unfreeze
        # the 11 non-fullback defenders and give them a forward target so they
        # cross halfway at ~half speed BEFORE the ball is kicked.  The kicker
        # stays frozen on the ball; the fullback is excluded (it moves via
        # fullback geometry only, assigned by _assign_kickoff_fullback on the
        # first update frame).
        anchor_x = w.defensive_line_anchor
        if anchor_x is None:
            anchor_x = getattr(w, "ruck_x_m", None) or b.x_m
            anchor_x = anchor_x + 10.0
        self._seed_line_governor(anchor_x)
        retreat_speed = self._kickoff_retreat_speed_mps()
        rush_speed = retreat_speed * KICKOFF_PRE_KICK_SPEED_FACTOR
        for p in w.kicking_team:
            if getattr(p, "jersey_number", 0) == KICKOFF_FB_JERSEY:
                # FB must be UNFROZEN so _assign_kickoff_fullback (which only
                # selects from unfrozen bodies) picks it on the first update.
                # It gets NO rush target — it moves via fullback geometry only.
                p.frozen = False
                continue
            if getattr(p, "is_kicker", False):
                continue   # kicker stays frozen on the ball
            p.frozen = False
            p.target_x = b.x_m - KICKOFF_PRE_KICK_LEAD_M
            p.target_y = p.y
            p.move_speed = rush_speed

        # ═══ PURSUIT MODE ════════════════════════════════════════════
        # Activate pursuit mode for kickoff chase.
        if self.world.open_play_engine is not None:
            self.world.open_play_engine.on_kickoff()

        # ── ENTER PRE_KICK RUSH PHASE (ball NOT yet kicked) ──
        self.pre_kick_timer = KICKOFF_PRE_KICK_RUSH_S
        self.phase = KOPhase.PRE_KICK
        print(f"[KICKOFF] Pre-kick rush armed: {KICKOFF_PRE_KICK_RUSH_S:.2f}s "
              f"@ {rush_speed:.2f} m/s, line_x={self.kickoff_line_x:.2f}, "
              f"kicker_id={getattr(w, 'kickoff_kicker_id', None)}")

    def _update_pre_kick_rush(self, dt):
        """Move the non-fullback kicking defenders forward at ~half speed
        during the 0.5 s window before the ball is kicked, so the chasers
        cross halfway with a rolling start.

        The kicker stays frozen on the ball; the fullback is excluded (it
        moves via fullback geometry only, assigned by _assign_kickoff_fullback
        on each update frame).  When the timer elapses the ball is kicked by
        _perform_kick().
        """
        if not self._active:
            return
        self.pre_kick_timer -= dt
        w = self.world
        b = self.ball
        retreat_speed = self._kickoff_retreat_speed_mps()
        rush_speed = retreat_speed * KICKOFF_PRE_KICK_SPEED_FACTOR
        for p in w.kicking_team:
            if getattr(p, "fullback_role_active", False):
                continue
            if getattr(p, "jersey_number", 0) == KICKOFF_FB_JERSEY:
                continue
            if getattr(p, "is_kicker", False):
                continue
            p.target_x = b.x_m - KICKOFF_PRE_KICK_LEAD_M
            p.target_y = p.y
            p.move_speed = rush_speed
            if not p.frozen:
                p.update(dt)
        if self.pre_kick_timer <= 0.0:
            self._perform_kick()

    def _perform_kick(self):
        """Kick the ball and set up the catcher/chase once the pre-kick rush
        window elapses.  The ball is launched from the halfway line using the
        params stored in kickoff(); the primary catcher and support receivers
        are unfrozen and aimed at the landing point, and the KMC chasers get
        their chase targets so IN_AIR / CHASE proceeds normally.
        """
        w = self.world
        b = self.ball

        # ── LAUNCH RESET (2026-09-10) ──
        # The ball sat at (halfway, centre, z=1.0) grounded=False, vz=0 through
        # the whole PRE_KICK window, and run.py calls ball.update() every frame.
        # Ball.update() applies gravity to an airborne stationary ball, so it
        # free-fell and bounced for the 0.5 s PRE_KICK delay.  Ball.kick() only
        # overwrites velocity + grounded — it never resets z_m — so the ball
        # launched from whatever height it drifted to (0 or mid-bounce), shifting
        # the descent-to-catch point away from the landing point the KMC catcher
        # runs to (the "catcher too deep / catch never happens" regression).
        # Reset to a clean 1.0 m launch BEFORE kicking.
        b.z_m = 1.0
        b.grounded = False
        b.vx = 0.0
        b.vy = 0.0
        b.vz = 0.0
        # KICK TYPE (2026-09-20): a kickoff is a spiral punt, and it is now
        # named explicitly rather than being whatever the ball's default
        # happened to be.  The ball carries the matching spin and attitude,
        # so the strike reads on screen as a real spiral.
        b.kick(
            self._kick_power,
            self._kick_angle_deg,
            self._kick_direction_deg,
            kick_type=KickType.SPIRAL_PUNT,
        )

        landing_x = b.landing_x_m
        landing_y = b.landing_y_m
        w.kickoff_landing_x = landing_x
        w.kickoff_landing_y = landing_y

        primary = None
        best_d2 = 999999.0
        for p in w.receiving_team:
            dx = p.x - landing_x
            dy = p.y - landing_y
            d2 = dx * dx + dy * dy
            if d2 < best_d2:
                best_d2 = d2
                primary = p

        if primary is not None:
            primary.catch_x = landing_x
            primary.catch_y = landing_y
            primary.catch_time = None
            w.primary_catcher = primary
            print(f"[KICKOFF] Primary catcher = Player #{primary.player_id}")
            print("[KICKOFF] Primary catcher set, waiting for chase phase.")
            primary.frozen = False
            primary.target_x = landing_x
            primary.target_y = landing_y
            primary.move_speed = primary.max_speed_mps * CATCHER_SPEED_FACTOR

        # ── KMC: Unfreeze and initialise all kicking-team chasers ──
        anchor_x = w.defensive_line_anchor
        if anchor_x is None:
            anchor_x = getattr(w, "ruck_x_m", None) or b.x_m
            anchor_x = anchor_x + 10.0
        self._seed_line_governor(anchor_x)
        retreat_speed = self._kickoff_retreat_speed_mps()
        for p in w.kicking_team:
            if getattr(p, "fullback_role_active", False):
                continue
            p.frozen = False
            p.target_x = b.x_m + CHASE_OFFSET_X
            p.target_y = p.y
            p.move_speed = min(
                p.max_speed_mps * CHASE_SPEED_FACTOR,
                retreat_speed,
            )

        # ── KMC: Unfreeze and initialise all support receivers ──
        for p in w.receiving_team:
            if p is primary:
                continue
            p.frozen = False
            p.target_x = landing_x - SUPPORT_OFFSET_X
            p.target_y = landing_y + (p.y - landing_y) * 0.5
            p.move_speed = p.max_speed_mps * SUPPORT_SPEED_FACTOR

        self.phase = KOPhase.KICK
        print(f"[KICKOFF] Landing point = ({landing_x:.1f}, {landing_y:.1f})")

    def on_catch_complete(self):
        """Called by _perform_catch() after a successful catch.
        
        Cleans up kickoff engine state: marks inactive, clears chase references.
        """
        self._active = False
        self.phase = KOPhase.SETUP
        w = self.world
        if w.kickoff_kickchase is not None:
            w.kickoff_kickchase.active = False
