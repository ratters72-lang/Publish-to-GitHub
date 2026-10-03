"""
UNIFIED CATCH SYSTEM
====================

The ONLY catch entry point is check_for_catch() — no other code path
may set ball.possessed_by or call ko.on_catch_complete().

Single legal flow:

    touched_by = check_for_catch(world, ball, dt)
    ko.update(..., touched_by=touched_by)

Inside check_for_catch:
  1. Find nearest eligible catcher (z-height + xy-radius + vz guard)
  2. Call _perform_catch() -> sets ball possession, ball_carrier
  3. Call ko.on_catch_complete() (if KICKOFF)
  4. Call open_play.start() (if KICKOFF)
  5. Return "RCVR" or "KICKER"

Catch eligibility rules
-----------------------
1. Ball must be descending (vz <= 0.5 m/s) or at apex.
2. Ball must be within the player's vertical reach:
   - standing catch:  ball.z_m <= player.catch_high_m  (1.5m)
   - jump catch:      ball.z_m <= player.jump_reach_m  (3.0m)
3. Ball must be within horizontal catch radius (1.5m default).
4. Ball must NOT be possessed by anyone.
5. Team eligibility: any player (nearest wins, both teams).
6. Ball must be airborne - no catches on rolling balls.
7. Nearest eligible catcher wins (contested catches).

State transitions
-----------------
On catch:
  ball.possessed_by = catcher
  ball.vx = ball.vy = ball.vz = 0
  ball.z_m = 1.0 (carrying height)
  catcher.state = BALL_CARRIER
  catcher.possessed = True
  world.ball_carrier = catcher
  world.just_caught_this_frame = True
  world.phase = GamePhase.CATCH  (one-frame transition before OPEN_PLAY)

  KICKOFF -> ko.on_catch_complete() (shuts down kickoff chase)
            -> open_play.start() happens on the next frame's CATCH branch
  OPEN_PLAY -> possession handover (engine resumes naturally)


HEIGHT-AWARE CATCH POINT PREDICTOR
=============================

predict_catch_point(ball, catch_height=1.5)
  Solves the projectile z(t) quadratic to find when the ball first
  becomes catchable (falls to <= catch_height).  Returns the (x, y, t)
  of that future point so receivers can run to the correct spot instead
  of chasing the ball's current position and overshooting.

  Uses linear horizontal motion (no drag) — accurate enough for kickoff
  flight times (~3s) with error < 2-3m.
"""

import math
import logging
import inspect
from player.player import Team, PlayerState, DefenderRole, Player
from world.world_state import GameWorldState, GamePhase, set_ball_carrier, get_defence_target


# ═══════════════════════════════════════════════════════════════
# HEIGHT-AWARE CATCH POINT PREDICTOR
# ═══════════════════════════════════════════════════════════════

def predict_catch_point(ball, catch_height=1.5):
    """
    Predict where the ball will be when it first becomes catchable
    (z <= catch_height).

    Solves the quadratic:  z(t) = z0 + vz0*t - 0.5*g*t^2 = catch_height
    for the descending (positive) root.

    Parameters
    ----------
    ball : Ball
        Must have x_m, y_m, z_m, vx, vy, vz, gravity attributes.
    catch_height : float
        Height threshold (default 1.5m — standing catch height).

    Returns
    -------
    tuple of (float, float, float) | (None, None, None)
        (x_drop, y_drop, t_drop)
        x_drop, y_drop : metres — predicted horizontal position when ball
                         reaches catch_height.
        t_drop         : seconds — time from now until that moment.
        None values if the ball will never reach catch_height
        (e.g. kicked too high and still rising, or on the ground).
    """
    # Already catchable right now → treat drop as immediate
    if ball.z_m <= catch_height and ball.vz <= 0:
        return ball.x_m, ball.y_m, 0.0

    # Ball on the ground / rolling → not in flight
    if ball.grounded and abs(ball.vz) < 0.01:
        return ball.x_m, ball.y_m, 0.0

    g = ball.gravity
    z0 = ball.z_m
    vz0 = ball.vz

    # Coefficients:  0.5*g*t^2 - vz0*t + (z0 - catch_height) = 0
    a = 0.5 * g
    b = -vz0
    c = z0 - catch_height

    disc = b*b - 4*a*c
    if disc < 0:
        return None, None, None

    sqrt_disc = math.sqrt(disc)
    t1 = (b - sqrt_disc) / (2*a)
    t2 = (b + sqrt_disc) / (2*a)

    # We want the first positive time in the future (descending phase)
    candidates = [t for t in (t1, t2) if t > 0]
    if not candidates:
        return None, None, None

    t_drop = min(candidates)

    # Horizontal position at that time (linear motion, no drag)
    x_drop = ball.x_m + ball.vx * t_drop
    y_drop = ball.y_m + ball.vy * t_drop

    # Safety clamp — ball should still be on the field
    return x_drop, y_drop, t_drop


# ═══════════════════════════════════════════════════════════════
# CATCH CHECK — UNIFIED ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def check_for_catch(world, ball, dt=0.016):
    """
    Unified catch check - the ONLY entry point for catches during KICKOFF
    and OPEN_PLAY.

    Uses the same full 3D catch logic (height checks, nearest-eligible wins)
    for both phases.

    Returns
    -------
    str | None
        "RCVR"   if a receiving-team player caught the ball
        "KICKER" if a kicking-team player caught the ball
        None     if no catch happened

    Side effects when a catch occurs:
        - ball possession established
        - world.ball_carrier set
        - world.phase -> GamePhase.CATCH (one-frame transition)
        - ko.on_catch_complete() called (if was KICKOFF)
        - world.in_kickoff = False (kickoff chase shuts down)
    """
    # Guards
    if ball is None:
        return None
    if ball.possessed_by is not None:
        return None
    if ball.grounded and ball.z_m == 0 and abs(ball.vz) < 0.01:
        return None
    # Prevent catch attempts when ball is directly overhead at apex
    if ball.vz > 0:
        return None
    if ball.vz > 0.5:
        return None

    # Players to check
    players = world.players if world.players else world.receiving_team + world.kicking_team
    if not players:
        return None

    # Find nearest eligible catcher
    best_catcher = None
    best_dist = float("inf")

    for p in players:
        if p.frozen:
            continue
        if getattr(p, "state", None) in (PlayerState.TACKLED,):
            continue

        # Vertical reach check
        if ball.z_m > p.jump_reach_m:
            continue

        # Horizontal distance
        dx = ball.x_m - p.x
        dy = ball.y_m - p.y
        dist_xy = math.hypot(dx, dy)
        if dist_xy > p.CATCH_RADIUS:
            continue

        # Catch type: standing or jump
        if ball.z_m <= p.catch_high_m:
            pass
        elif ball.z_m <= p.jump_reach_m:
            pass
        else:
            continue

        if dist_xy < best_dist:
            best_dist = dist_xy
            best_catcher = p

    if best_catcher is None:
        return None

    # Perform the catch
    _perform_catch(best_catcher, ball, world)

    # Determine who touched it
    if best_catcher.team == Team.RECEIVING:
        return "RCVR"
    else:
        return "KICKER"


def _perform_catch(player, ball, world, dt=0.016):
    """
    Execute the catch - set possession only; Ball.update's lerp handles
    position/velocity/height smoothly.  This is the ONLY place that
    calls ko.on_catch_complete().

    Sets world.phase = GamePhase.CATCH (one-frame transition before OPEN_PLAY).
    """
    # Set possession only -- Ball.update handles smooth lerp to carrier
    ball.possessed_by = player

    # Player state
    player.possessed = True
    player.state = PlayerState.BALL_CARRIER
    player.frozen = False
    # Zero any residual catch/chase velocity so the BC starts clean
    player.vx = 0.0
    player.vy = 0.0

    # ═══ RESET FACING IMMEDIATELY AFTER CATCH ═══
    player.clear_facing_override()
    player.facing = 0.0

    # ═══ FORCE BC TARGET ═══
    RIGHT_TRYLINE_X = 110
    player.target_x = max(10.0, min(RIGHT_TRYLINE_X - 1, player.x + 4.0))
    player.target_y = player.y

    # ── Phase 2D: unified facing physics ──
    from player.movement import update_facing_unified
    update_facing_unified(player, dt)

    # World state — set ball ownership to BC via unified API
    set_ball_carrier(world, player)
    # Explicit ball_owner sync (belt + suspenders with set_ball_carrier)
    world.ball_owner = "BC"
    world.just_caught_this_frame = True
    world.ball_live = True
    world.ball_in_play = True

    # Capture phase before transitioning (used to gate kickoff cleanup)
    _was_kickoff = world.phase == GamePhase.KICKOFF

    # ═══ SET PHASE TO CATCH (one-frame transition) ═══
    # This defers open_play.start() to the next frame's CATCH branch.
    # During CATCH phase, only ball update runs; no OPE, no defence/attack.
    world.phase = GamePhase.CATCH

    # ── CATCH X-AUTHORITY LOCK (2026-09-10) ──
    # Tacklers were freezing because multiple X-writers fought during the
    # chase-to-catch transition: the KMC chase writer (ball_x + CHASE_OFFSET_X),
    # the catch cleanup (p.target_x = p.x), on_ball_secured()'s fullback re-
    # assert + depth reseed, and then the full OPEN_PLAY pipeline all wrote
    # target_x for the same defenders across consecutive frames, so the
    # target never settled and the tackler froze at vx=0.  Arm the sole X-
    # authority lock here so the catch cleanup below is the ONLY writer of
    # target_x for the non-BC players this frame, and the catch_x_authority
    # module (invoked as the LAST X write in the OPEN_PLAY dispatch) stays
    # the sole X owner through the post-catch pursuit window.
    from game.defence.defence_lvl_1.catch_x_authority import (
        arm_catch_x_authority as _arm_catch_x,
    )
    _arm_catch_x(world)


    print(
        f"[CATCH] Player #{getattr(player, 'player_id', '?')} "
        f"caught ball at ({ball.x_m:.1f}, {ball.y_m:.1f}, {ball.z_m:.1f})"
        f"  team={player.team} phase={world.phase}"
    )

    # ── Shut down kickoff systems (only if we were in KICKOFF phase) ──
    if _was_kickoff:
        # RULE 15: Post-kickoff collision delay
        world.just_left_kickoff_timer = world.KICKOFF_COLLISION_DELAY_S

        # Restore normal collision body properties
        for p in world.players:
            if hasattr(p, 'body'):
                p.body.restore_normal_properties()

        # Shut down kickoff chase
        if world.kickoff_kickchase is not None:
            world.kickoff_kickchase.active = False

        # Notify kickoff controller (stops _active, clears chase refs)
        if world.kickoff_controller is not None:
            world.kickoff_controller.on_catch_complete()

        # Clear in_kickoff flag — collision system checks this
        world.in_kickoff = False

        # ═══ POST-CATCH PURSUIT WINDOW — ARM AT CATCH (2026-08-16) ═══
        # The post-catch stall (engaged defenders stop moving forward on X
        # until bc.x passes anchor_x + 10) is caused by the MMDL switching
        # defenders to SLIDE/DRIFT at the catch moment.  Arm a pursuit
        # window here so the MMDL keeps engaged defenders in CHASE right
        # after the catch; OpenPlayEngine decrements it each OPEN_PLAY
        # frame and the MMDL + depth classification read it.
        world.post_catch_chase_window_s = world.POST_CATCH_CHASE_WINDOW_S

        # ═══ GLOBAL DEFENDER REACTION GOVERNOR — ARM AT CATCH ═══
        # Snapshot each defender's kickoff chase speed BEFORE the cleanup
        # below overwrites move_speed to max_speed_mps.  From the catch
        # moment the defender may not exceed this ceiling for
        # HOLD + BLEND seconds, then blends to full defence speed.
        # This eliminates the 0.8x→1.0x speed jump on kickoff catch.
        #
        # POST-CATCH DEFENSIVE LINE CLAMP — ARM AT CATCH (2026-08-02)
        # Also arms the governor-max line-speed clamp on every defender.
        # While armed, Player._accelerate_toward_target() caps effective
        # speed at tuners.post_catch_cap_mps (the governor's maximum
        # intended line speed), so the three non-governor writers (pursuit
        # reaction, depth reclassification, retreat compliance/SDM) cannot
        # visibly "turbo" defenders into position during the post-catch
        # transition window.  `defended_cap_anchor_x` records the current
        # defensive line anchor for the geometry-stability check in
        # DefensiveBehaviourUnified._finalise_lateral_targets().  The clamp
        # is a hard time ceiling (Player.DEFENDED_CAP_HARD_LIMIT_S) and is
        # additionally auto-disarmed once structured OPEN_PLAY is stable.
        # Reuse the engine's tuners when available (avoid a fresh tuner
        # instance on the catch frame); fall back to a default cap.
        #
        # 2026-08-16: DEFENDERS KEEP THEIR PRE-CATCH MOMENTUM.  The two
        # velocity-wipe lines (d.vx = 0.0 / d.vy = 0.0) that used to live
        # in the OPEN PLAY ENTRY CLEANUP block below have been REMOVED —
        # zeroing velocity at catch was the other half of the stall (the
        # line physically stopped dead the frame the ball was caught).
        # The kickoff-chase speed snapshot must therefore run BEFORE the
        # cleanup, which it already does.
        _cap_tuners = None
        _engine = getattr(world, "open_play_engine", None)
        if _engine is not None:
            _cap_tuners = getattr(_engine, "def_tuners", None)
        if _cap_tuners is None:
            from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
            _cap_tuners = DefenceTuners()
        _cap_mps = getattr(_cap_tuners, "post_catch_cap_mps", 5.5)
        _cap_anchor = getattr(world, "defensive_line_anchor", player.x + 10.0)
        for d in world.defending_team:
            actual_speed = math.hypot(d.vx, d.vy)
            d.kickoff_chase_speed = max(d.move_speed, actual_speed)
            d.kickoff_chase_speed = min(d.kickoff_chase_speed, d.max_speed_mps)
            d.defence_reaction_remaining_s = (
                Player.DEFENCE_REACTION_HOLD_S + Player.DEFENCE_REACTION_BLEND_S
            )
            d.defended_speed_cap_mps = _cap_mps
            d.defended_cap_remaining_s = Player.DEFENDED_CAP_HARD_LIMIT_S
            d.defended_cap_anchor_x = _cap_anchor
            d.defended_cap_blend_s = 0.0

        # ═══ OPEN PLAY ENTRY CLEANUP ═══
        # Clear all players' stale state from kickoff/catch
        # (frozen, facing_override, move_speed, tackle flags, TES flags, anim_state)
        for p in world.players:
            p.frozen = False
            p.clear_facing_override()
            p.move_speed = p.max_speed_mps
            p.in_contact = False
            p.is_tackler = False
            p.tackle_locked = False
            p.anim_state = ""
            p.tes_locked = False
            p.defender_role = None
            # Clear stale target positions (never overwrite the caught BC's
            # forward target — it must stay stable into open-play steering)
            if p is not player:
                p.target_x = p.x
                p.target_y = p.y


        # ═══ POST-CATCH LINE-DEFENDER CHASE FORCE (2026-09-10) ═══
        # Force every genuine line defender into CHASE on the catch frame
        # (post_catch_chase_window_s > 0 + movement_state = CHASE) so the
        # pre-tackle classifier sees a closing posture and tackling switches
        # back on immediately.  Arms the pursuit window + stamps CHASE on
        # every genuine line defender via catch_x_authority.force_line_defenders_chase.
        from game.defence.defence_lvl_1.catch_x_authority import (
            force_line_defenders_chase as _force_line_chase,
        )
        _forced = _force_line_chase(world)
        print(
            f"[CATCH_CHASE_FORCE] frame={getattr(world, 'frame_count', '?')} "
            f"forced={_forced} line defender(s) to CHASE"
        )


        # ═══ PURSUIT MODE — KICKOFF CATCH: CLEAR pursuit ═══════

        # Catch is complete: structured defence resumes immediately.
        # on_ball_secured() clears in_pursuit_mode and re-enables
        # the governor, role spacing, geometry, and decision layer.
        # Set ruck_x and anchor to current BC position so defensive
        # geometry works from frame 0 of OPEN_PLAY.

        # ═══ RUCK ORIGIN AND ANCHOR — SET FRESH FROM BC POSITION ═══
        # These MUST be unconditional: the BC is authoritative regardless of
        # whether the engine reference exists.  Setting them here guarantees
        # no stale values carry over between kickoffs.
        #
        # KICKOFF ORIGIN RULE (2026-08-09, Task A): at kickoff there is NO
        # ruck/PTB/defensive line — the ONLY anchor is the BC.  The defensive
        # line must form relative to the BC, NOT field centre.  This seeds
        # BOTH ruck_x_m and ruck_y_m from the caught BC's position, so the
        # first OPEN_PLAY frame's depth-role bootstrap, side split
        # (d.y < ruck_y) and DefensiveGeometry width anchors are all
        # BC-relative (e.g. BC caught near the corner line → the line form is
        # WINGER/CENTRE/A/B/C/D fanning out from BC.y, never from 35.0).
        world.ruck_x_m = player.x
        world.ruck_y_m = player.y
        # TASK G ANCHOR AUTHORITY (Layer 6): the kickoff catch writes a
        # fresh ruck (from the BC).  This is a legal anchor reset point —
        # the lock is dropped and the anchor recomputed + locked from the
        # NEW ruck (ruck_x + 10), never from any other actor.
        from game.defence.defence_lvl_1.anchor_authority import (
            reset_anchor_authority,
            compute_and_lock_anchor,
        )
        reset_anchor_authority(world)
        compute_and_lock_anchor(world)
        print(f"[RUCK_ORIGIN_WRITE] catch -> ruck_x={world.ruck_x_m:.3f} ruck_y={world.ruck_y_m:.3f} anchor={world.defensive_line_anchor:.3f} bc.x={player.x:.3f} bc.y={player.y:.3f}")

        # ── FULLBACK ROLE RE-ASSERT AT RECEPTION (2026-08-27) ──
        # The kickoff FB was assigned by KickoffEngine during flight at the
        # flat-line kickoff anchor (target_source="fullback").  The ball has
        # now been caught and a NEW BC-derived anchor (bc.x + 10) is
        # authoritative.  Re-run the world-aware assigner against the WHOLE
        # defending team BEFORE any subset pipeline runs, so the single-FB
        # invariant is enforced and the chosen FB (jersey-1 priority preserved
        # via existing-actor stickiness) is re-anchored to the post-catch
        # geometry.  The actor is then stamped with the fullback geometry
        # authority so no line writer can re-tag it as a line/winger source
        # in this or subsequent frames.
        from game.defence.defence_lvl_1.fullback_role import (
            assign_fullback_role,
            publish_fullback_state,
            FB_POST_CATCH_ABSORB_GRACE_FRAMES,
            is_fullback_emergency_engaged,
        )
        # KICKOFF-SPECIFIC ROOT CAUSE: the kickoff flight assigned a flat-line
        # fullback anchor, but once the ball is secured the first tackle must
        # re-anchor to the normal open-play deep backfield geometry.  A catch-time
        # reassert that keeps ``in_kickoff=True`` preserves the kickoff flat-line
        # anchor and leaves the FB in the wrong branch long enough to re-enter the
        # defensive line on the first tackle.  Clear the kickoff FB state and
        # reselect as a normal post-catch fullback using the new BC-derived anchor.
        # EMERGENCY-DEFENDER TAG HOLD (B2b, 2026-09-16): a tagged fullback
        # that is ENGAGED in an emergency tackle is a live TES participant.
        # The tag is the ONLY thing keeping it out of the defensive line, so
        # this catch-time re-anchor must never dissolve it mid-tackle -- the
        # re-selection below would otherwise hand it a line identity while
        # TES still owns its body.  The engine clears the engagement marker
        # (and restores DEF_LINE_READY) at the end of the tackle cycle.
        for _d in world.defending_team:
            if getattr(_d, "fullback_role_active", False):
                if is_fullback_emergency_engaged(_d):
                    continue
                _d.fullback_role_active = False
                if getattr(_d, "role", None) == "FULLBACK":
                    _d.role = None
                _d.depth_role = None
                _d.depth_side = None
                _d.target_source = None
                _d.can_acquire_tackle = False

        _fb_anchor_x = getattr(world, "defensive_line_anchor", player.x + 10.0) or (player.x + 10.0)
        _fb_actor, (_fb_tx, _fb_ty) = assign_fullback_role(
            list(world.defending_team),
            ball,
            _fb_anchor_x,
            in_kickoff=False,
            formation_line_x=None,
            side_sign=getattr(world, "kickoff_side_sign", 1),
            world=world,
            stamp_geometry=True,
        )
        if _fb_actor is not None:
            # ── POST-CATCH ABSORB GRACE ARM (2026-08-28) ──
            # The kickoff FB is hard-placed ON the flat formation line; the
            # post-catch line anchor (bc.x + 10) can land within the absorb
            # X threshold of the FB's live position, tripping the absorption
            # dissolve on the first OPEN_PLAY frames.  Arm the frame-count
            # grace window here (decremented each OPEN_PLAY FB-authority pass)
            # so the absorb sites withhold the dissolve until the FB has
            # glided to the ball-derived deep anchor.
            world.fb_post_catch_absorb_grace_frames = FB_POST_CATCH_ABSORB_GRACE_FRAMES
            # TELEMETRY (2026-08-27 hotfix): emit a machine-readable reception
            # line so CI can assert the reception frame published the correct
            # actor/geometry (regressions fail fast).  The publish already
            # happened atomically inside assign_fullback_role(stamp_geometry=True);
            # a redundant publish is harmless but omitted for a single source.
            print(
                f"[FB_RECEPTION] frame={getattr(world, 'frame_count', '?')} "
                f"fb_id={getattr(_fb_actor, 'player_id', '?')} "
                f"anchor=({_fb_tx if _fb_tx is not None else getattr(_fb_actor, 'target_x', 0.0):.2f}, "
                f"{_fb_ty if _fb_ty is not None else getattr(_fb_actor, 'target_y', 0.0):.2f}) "
                f"target_source={getattr(_fb_actor, 'target_source', None)} "
                f"absorb_grace_frames={world.fb_post_catch_absorb_grace_frames}"
            )

        if hasattr(world, "open_play_engine") and world.open_play_engine is not None:
            world.open_play_engine.on_ball_secured()

            # ROLE-ANCHORED KICKOFF CATCH WIDTH (Task B, 2026-08-09):
            # The post-kickoff line forms from the SAME role-slot width
            # anchors DefensiveGeometry uses — NEVER the legacy fixed
            # get_defence_target lane spread (which made kickoff width
            # "stick" at 7 fixed slots regardless of the line).
            #
            # assign_depth_roles() seeds the persistent depth_role +
            # depth_side pair BC-relative (ruck_y = player.y), then every
            # defender's Y target is the canonical role anchor:
            #
            #     target_y = role_anchor_y(d, ruck_y, def_line_width, tuners)
            #
            # INPUT ORDER (2026-08-09): defending_team input order — no
            # Y-sort, no lane-index ordering.
            from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners as _DT
            _cap_tuners = None
            _engine_local = getattr(world, "open_play_engine", None)
            if _engine_local is not None:
                _cap_tuners = getattr(_engine_local, "def_tuners", None)
            if _cap_tuners is None:
                _cap_tuners = _DT()
            # GOVERNED WIDTH (Task D, 2026-08-09): the post-kickoff line
            # forms from the LIVE governed width (governor compression /
            # sideline clamp) rather than the raw static def_line_width_m.
            sorted_defs = list(world.defending_team)

            # INVARIANT SHEET 1.3 / 6.4 (2026-08-12): the catch -> OPEN_PLAY
            # transition must HARD-RESEED depth roles BC-relative.  The OLD
            # Task B block used assign_depth_roles() directly, which runs
            # persistent maintenance — if any defender carried a depth_role /
            # depth_side from a previous sequence (or from the OPEN_PLAY
            # preamble on an earlier kickoff), the post-catch line would
            # maintain against the OLD flight-era ruck instead of re-seeding
            # around the caught BC.  force_reseed_depth_roles() clears the
            # persistent seed on every LINE defender (fullback excluded),
            # then re-bootstraps positionally against world.ruck_y_m (== BC.y
            # written above) — so the first OPEN_PLAY preamble picks up the
            # BC-relative roles.
            #
            # FULLBACK EXCLUSION (2026-08-12): the dynamic fullback actor
            # must NOT take a line slot at catch.  It anchors at
            # (formation_line_x, centre + side_sign * 3.0) via fullback_role;
            # the reseed loop below skips it so its kickoff-era anchor
            # (target_source == "fullback") survives the CATCH frame and the
            # OPEN_PLAY re-assignment.
            # STAGE 3B: NO band formation and NO target writes on CATCH.
            # Band formation (A/B/C ruck-pivot) fires ONLY when TES
            # enters TACKLE_INIT; defender positioning is owned by the
            # DefenceManager pipeline.  Keep pre-catch momentum only.
            for d in sorted_defs:
                if getattr(d, 'fullback_role_active', False):
                    continue
                d.move_speed = d.max_speed_mps

            print(
                "[KICKOFF_CATCH] pursuit=", world.in_pursuit_mode,
                " first_not_made=", world.first_tackle_not_made,
                " ruck_x=", world.ruck_x_m,
                " anchor=", world.defensive_line_anchor,
                " bc.x=", player.x,
            )


def is_airborne_catchable(ball, player):
    """
    Quick predicate - can *this* player catch *this* ball right now?
    Used by chase AI to decide whether to converge or wait.
    """
    if ball.possessed_by is not None:
        return False
    if ball.vz > 1.0:
        return False
    if ball.z_m > player.jump_reach_m:
        return False
    dx = ball.x_m - player.x
    dy = ball.y_m - player.y
    if math.hypot(dx, dy) > player.CATCH_RADIUS:
        return False
    return True
