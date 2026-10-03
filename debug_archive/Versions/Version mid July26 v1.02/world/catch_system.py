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

  KICKOFF -> open_play.start(catcher)
  OPEN_PLAY -> possession handover (engine resumes naturally)


HEIGHT-AWARE CATCH PREDICTOR
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
from player.player import Team, PlayerState, DefenderRole
from world.world_state import GameWorldState


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
        - world.game_state -> OPEN_PLAY (if was KICKOFF)
        - ko.on_catch_complete() called (if was KICKOFF)
        - open_play.start() called (if was KICKOFF)
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


def _perform_catch(player, ball, world):
    """
    Execute the catch - set possession only; Ball.update's lerp handles
    position/velocity/height smoothly.  This is the ONLY place that
    calls ko.on_catch_complete().
    """
    # Set possession only -- Ball.update handles smooth lerp to carrier
    ball.possessed_by = player

    # Player state
    player.possessed = True
    player.state = PlayerState.BALL_CARRIER
    player.frozen = False

    # ═══ FORCE BC TARGET ═══
    # The catcher must have a forward movement target on the very first
    # frame of open play.  Without this, if the old target was the kickoff
    # landing point or something stale, the BC may sit still — especially
    # near the tryline where the default BC movement logic can get stuck.
    # Attack runs RIGHT → target_x = catcher.x + 50 (forward), clamped
    # to [10, 109] so we never overshoot the tryline.
    RIGHT_TRYLINE_X = 110
    # BC should aim 3–6m ahead, not 50m
    player.target_x = max(10.0, min(RIGHT_TRYLINE_X - 1, player.x + 4.0))
    player.target_y = player.y

    # ── Phase 2D: unified facing physics ──
    from player.movement import update_facing_unified
    update_facing_unified(player, dt)

    # World state
    world.ball_carrier = player
    world.just_caught_this_frame = True
    world.ball_live = True
    world.ball_in_play = True

    print(
        f"[CATCH] Player #{getattr(player, 'player_id', '?')} "
        f"caught ball at ({ball.x_m:.1f}, {ball.y_m:.1f}, {ball.z_m:.1f})"
        f"  team={player.team} state={world.game_state}"
    )

    # Post-catch: collision delay + body restore (only applies during kickoff).
    # game_state is always OPEN_PLAY in the new continuous model — defence
    # was already running, so no phase transition or defender reset needed.
    if world.in_kickoff:
        # RULE 15: Post-kickoff collision delay
        world.just_left_kickoff_timer = world.KICKOFF_COLLISION_DELAY_S

        # Restore normal collision body properties
        for p in world.players:
            if hasattr(p, 'body'):
                p.body.restore_normal_properties()

        # Shut down kickoff chase
        if world.kickoff_kickchase is not None:
            world.kickoff_kickchase.active = False

        # Notify kickoff controller & open play engine (set BC ref)
        if world.kickoff_controller is not None:
            world.kickoff_controller.on_catch_complete()
        if world.open_play is not None:
            world.open_play.start(player)


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
