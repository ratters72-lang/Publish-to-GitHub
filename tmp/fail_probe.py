"""Seed fail-frame probe — scan ALL OPEN_PLAY frames for stuck defending
players (unfrozen, v==0, target far from pos) and dump full state on the
first occurrence after frame 350.  Also records whether defence ran that
frame, what wrote the target, movement state, _mmdl_moved, and phase.

No monkeypatching: OPE imports reactivate_movement_after_thaw by name, so
patching world.rules.movement_contracts is inert.  This is pure
observation.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
import sys

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 3
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import random
random.seed(seed)

import pygame
pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod
ope_mod.DEBUG_SDEA_PHYSICS = False

from world.field import Field
from world.world_state import WorldState, GamePhase
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

MAX_FRAMES = 2400
dt = 1.0 / 60.0


def _dump(frame, p, world):
    bc = world.ball_carrier
    print("=== STUCK @ frame %d (defence_ran=%s phase=%s sub=%s tes=%s"
          % (frame, getattr(world, "defence_ran_this_frame", None),
             open_play._phase, world.sub_state, getattr(world, "tes_active", None)))
    print("  pid=%s role=%s depth=%s tackle_role=%s\n"
          "  x=%.3f y=%.3f target=(%.3f,%.3f) dx=%.3f dy=%.3f\n"
          "  vx=%.4f vy=%.4f frozen=%s freeze_t=%.3f\n"
          "  state=%s mode=%s source=%s writer=%s\n"
          "  _mmdl_moved=%s commit=%s anchor=%.3f ruck_x=%s\n"
          "  bc=(%.3f,%.3f) bc_speed=%.2f carrier_past_anchor=%s\n"
          % (p.player_id, getattr(p, "defender_role", None),
             getattr(p, "depth_class", None),
             getattr(p, "tackle_role", None),
             p.x, p.y, p.target_x, p.target_y,
             p.target_x - p.x, p.target_y - p.y,
             p.vx, p.vy, p.frozen, getattr(p, "freeze_timer", 0.0),
             getattr(p, "movement_state", None),
             getattr(p, "movement_mode", None),
             getattr(p, "target_source", None),
             getattr(p, "movement_writer", None),
             getattr(p, "_mmdl_moved", None),
             getattr(p, "_tackle_commit_active", None),
             getattr(world, "defensive_line_anchor", None),
             getattr(world, "ruck_x_m", None),
             bc.x if bc else None, bc.y if bc else None,
             math_hypot(bc.vx, bc.vy) if bc else 0.0,
             bc.x > getattr(world, "defensive_line_anchor", 0) + 1.0 if bc else None))


import math as _math
math_hypot = _math.hypot

found = False
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
        open_play.update(dt)
        if not world.tes_active:
            collision_system.resolve_all(world.players, world=world)
        if open_play._phase == "OPEN_PLAY" and frame > 350:
            for p in getattr(world, "defending_team", []):
                if p.frozen:
                    continue
                if (p.vx == 0.0 and p.vy == 0.0
                        and (abs(p.target_x - p.x) > 0.1
                             or abs(p.target_y - p.y) > 0.1)):
                    _dump(frame, p, world)
                    found = True
                    break
        if found:
            break
        if frame >= 900:
            break

if not found:
    print("NO STUCK DEFENDER through frame 900")
print("done")
