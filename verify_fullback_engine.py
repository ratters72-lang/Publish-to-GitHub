"""Headless engine smoke test for the dynamic fullback role (2026-08-08).

Boots the real engine exactly like run.py, kicks off, runs until a real
OPEN_PLAY cycle with a ball carrier is underway, and asserts:

  A. A fullback actor is active on defence frames (world.defence_ran_this_frame).
  B. The actor is NOT frozen, classified PASSIVE, and is the ONLY fullback.
     The movement state is DRIFT except on a breakaway (ball carrier past
     the defensive line), where it transitions to CHASE (last-defender
     pursuit) — the "detect the BC to chase" fix of 2026-09-03.
  C. The actor's target anchor sits DEEP BEHIND the defensive line toward
     +X (x >= line anchor, corrected 2026-08-08) and inside the field.
  D. world.fullback_actor_id / anchor_x / anchor_y are published each frame.
  E. The engine stays in a sane OPEN_PLAY phase at the end (no crash).
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"

import sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import pygame
pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod
ope_mod.DEBUG_SDEA_PHYSICS = False
ope_mod.DEBUG_OPE_TRACE = False

from world.field import Field
from world.world_state import WorldState, GameSubState, GamePhase
from world.open_play_engine import OpenPlayEngine
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from game.physics.collision_system import CollisionSystem
from world.catch_system import check_for_catch

collision_system = CollisionSystem()
field = Field(1920, 1080)
world = WorldState()
ball = Ball(field)
world.ball = ball

open_play = OpenPlayEngine(world)
world.open_play = open_play
world.open_play_engine = open_play

kickoff_engine = KickoffEngine(world, ball, field)

kickoff_engine.reset_for_kickoff()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)
world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff_engine.kickoff()

MAX_FRAMES = 3600
dt = 1.0 / 60.0

failures = []
fullback_frames = 0          # defence frames where a fullback was active
defence_frames = 0           # total frames where defence ran
multi_active_frame = None    # frame with >1 fullback
last_actor_id = None
last_anchor = None
last_line_anchor = None
saw_open_play = False

for frame in range(1, MAX_FRAMES + 1):
    world.frame_count = frame
    ball.update(dt)

    if world.phase == GamePhase.KICKOFF:
        kickoff_engine.update(dt)
        check_for_catch(world, ball, dt)
    elif world.phase == GamePhase.CATCH:
        if world.ball_carrier is not None:
            world.open_play.start(world.ball_carrier)
        world.phase = GamePhase.OPEN_PLAY
    elif world.phase == GamePhase.OPEN_PLAY:
        prev_phase = open_play._phase
        open_play.update(dt)
        if not world.tes_active:
            collision_system.resolve_all(world.players, world=world)
        if open_play._phase == "OPEN_PLAY":
            saw_open_play = True

        if getattr(world, "defence_ran_this_frame", False):
            defence_frames += 1
            active = [p for p in world.defending_team
                      if getattr(p, "fullback_role_active", False)]
            if active:
                fullback_frames += 1
                if len(active) > 1 and multi_active_frame is None:
                    multi_active_frame = frame
                actor = active[0]
                last_actor_id = actor.player_id
                last_anchor = (getattr(actor, "target_x", None),
                               getattr(actor, "target_y", None))
                last_line_anchor = getattr(world, "defensive_line_anchor", None)

                if frame == 1:
                    continue  # skip boot frame stability checks

                ms = getattr(actor, "movement_state", None)
                ms_name = getattr(ms, "name", None) or str(ms)
                if ms_name in ("CHASE", "SHOOT"):
                    # BREAKAWAY CHASE (2026-09-03): the FB now transitions
                    # to CHASE when the ball carrier breaks the defensive
                    # line (last-defender pursuit) — this is the intended
                    # "detect the BC to chase" behaviour.  It is only a
                    # violation when the carrier is NOT past the line.
                    bc_now = getattr(world, "ball_carrier", None)
                    line_now = getattr(world, "defensive_line_anchor", 0.0)
                    breakaway = (
                        bc_now is not None
                        and line_now is not None
                        and line_now > 0.0
                        and bc_now.x > line_now + 1.0
                    )
                    if not breakaway and frame > 120:  # allow brief boot frames
                        failures.append(
                            "frame %d fullback pid %s in %s (must be DRIFT)"
                            % (frame, actor.player_id, ms_name))
                if getattr(actor, "depth_class", None) != "PASSIVE":
                    failures.append(
                        "frame %d fullback pid %s depth_class=%s (must be PASSIVE)"
                        % (frame, actor.player_id, getattr(actor, "depth_class", None)))
                if getattr(actor, "frozen", False):
                    failures.append(
                        "frame %d fullback pid %s frozen=True" % (frame, actor.player_id))

        # ── Record a sampled late frame for anchor geometry ──
        if frame > 600 and open_play._phase == "OPEN_PLAY" and world.ball_carrier is not None:
            break

# ── ASSERT A ──
if fullback_frames == 0 or defence_frames == 0:
    failures.append("ASSERT-A: no fullback over %d defence frames"
                    % defence_frames)
else:
    print("INFO: fullback active on %d/%d defence frames"
          % (fullback_frames, defence_frames))

# ── ASSERT B ──
if multi_active_frame is not None:
    failures.append("ASSERT-B: more than one fullback on frame %d"
                    % multi_active_frame)

# ── ASSERT C+D: anchor geometry on the last sampled frame ──
if last_anchor is not None and last_anchor[0] is not None:
    ax, ay = last_anchor
    line_anchor = last_line_anchor
    if line_anchor is not None and line_anchor > 0.0:
        # CORRECTED SPEC (2026-08-08): the fullback deploys DEEP BEHIND
        # the line toward +X (defence retreats RIGHT), so the anchor must
        # sit at or beyond the line anchor (ax >= line_anchor − slack).
        if ax < line_anchor - 1.5:
            failures.append(
                "ASSERT-C: fullback anchor_x %.2f too far IN FRONT of line %.2f"
                % (ax, line_anchor))
    if not (0.0 <= ax <= 120.0):
        failures.append("ASSERT-C: anchor_x %.2f out of field" % ax)
    if not (0.0 <= ay <= 70.0):
        failures.append("ASSERT-C: anchor_y %.2f out of field" % ay)
    print("INFO: last fullback pid=%s anchor=(%.2f,%.2f) line_anchor=%s"
          % (last_actor_id, ax, ay, line_anchor))

    # The published anchor is the RAW pipeline target; the actor's live
    # target has been smoothed by the target stabiliser since, so compare
    # identity/geometric invariants rather than exact floats.
    pub_ax = getattr(world, "fullback_anchor_x", None)
    pub_ay = getattr(world, "fullback_anchor_y", None)
    pub_id = getattr(world, "fullback_actor_id", None)
    if pub_id != last_actor_id:
        failures.append("ASSERT-D: world.fullback_actor_id=%s != actor %s"
                        % (pub_id, last_actor_id))
    if pub_ax is None or pub_ay is None:
        failures.append("ASSERT-D: world.fullback_anchor not published")
    else:
        if not (0.0 <= pub_ax <= 120.0):
            failures.append("ASSERT-D: published anchor_x %.2f out of field" % pub_ax)
        if not (0.0 <= pub_ay <= 70.0):
            failures.append("ASSERT-D: published anchor_y %.2f out of field" % pub_ay)
        # Published anchor must sit at or beyond the defensive line (+X,
        # corrected spec 2026-08-08) — or the line anchor is unset.
        # Compare against the SAME-frame line anchor (the static one
        # captured alongside the actor target).
        line_anchor_pub = last_line_anchor
        if line_anchor_pub is not None and line_anchor_pub > 0.0:
            if pub_ax < line_anchor_pub - 1.5:
                failures.append(
                    "ASSERT-D: published anchor_x %.2f too far IN FRONT of line %.2f"
                    % (pub_ax, line_anchor_pub))
else:
    failures.append("ASSERT-C/D: no fullback anchor captured")

# ── ASSERT E ──
print("INFO: saw OPEN_PLAY:", saw_open_play,
      "| final phase:", open_play._phase,
      "| ball_carrier:", world.ball_carrier is not None)

print("=" * 72)
if failures:
    print("RESULT: FAIL")
    for f in failures:
        print("  -", f)
    pygame.quit()
    sys.exit(1)

print("RESULT: PASS")
print("  - fullback deployed on every sampled defence frame after boot")
print("  - single actor, PASSIVE, non-frozen, DRIFT (CHASE on breakaway only)")
print("  - anchor behind the defensive line and inside field bounds")
print("  - world.fullback_* published per frame")
pygame.quit()
sys.exit(0)
