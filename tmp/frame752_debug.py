"""Frame-752 diagnostic — why pid 212 is unfrozen with vx=vy=0 + live target
on an OPEN_PLAY entry frame under TASK VI (patch ACTIVE).

Copies verify_movement_reactivation's boot exactly, but on frame 752 prints
pid 212's full movement state and which writer stamped its target.
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

from world.field import Field
from world.world_state import WorldState, GameWorldState, GameSubState, GamePhase
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

TARGET_FRAME = 752
PROBE_PID = 212


def _dump_212(label):
    for p in world.players:
        if p.player_id == PROBE_PID:
            print(
                "[%s] pid=%s\n"
                "  phase=%s sub=%s\n"
                "  x=%.3f y=%.3f target=(%.3f,%.3f) dx=%.3f dy=%.3f\n"
                "  vx=%.4f vy=%.4f frozen=%s freeze_t=%.3f\n"
                "  movement_state=%s movement_mode=%s arrival_mode=%s\n"
                "  target_source=%s movement_writer=%s\n"
                "  defender_role=%s depth_class=%s\n"
                "  _tackle_commit_active=%s tes_attached=%s\n"
                "  _mmdl_moved=%s pre_tackle_stance=%s\n"
                "  facing_override=%s\n"
                % (
                    label, p.player_id,
                    world.phase, getattr(world, "sub_state", None),
                    p.x, p.y, p.target_x, p.target_y,
                    p.target_x - p.x, p.target_y - p.y,
                    p.vx, p.vy, p.frozen, getattr(p, "freeze_timer", 0.0),
                    getattr(p, "movement_state", None),
                    getattr(p, "movement_mode", None),
                    getattr(p, "arrival_mode", None),
                    getattr(p, "target_source", None),
                    getattr(p, "movement_writer", None),
                    getattr(p, "defender_role", None),
                    getattr(p, "depth_class", None),
                    getattr(p, "_tackle_commit_active", None),
                    getattr(p, "tes_attached", None),
                    getattr(p, "_mmdl_moved", None),
                    getattr(p, "pre_tackle_stance", None),
                    getattr(p, "facing_override", None),
                )
            )
            return
    print("[%s] pid %s NOT FOUND in world.players" % (label, PROBE_PID))


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

    if frame == TARGET_FRAME:
        print("=== frame %d ===" % frame)
        print("open_play._phase=%s defence_ran_this_frame=%s tes_active=%s"
              % (getattr(open_play, "_phase", "?"),
                 getattr(world, "defence_ran_this_frame", "?"),
                 getattr(world, "tes_active", "?")))
        print("ruck_x_m=%s defensive_line_anchor=%s bc=%s"
              % (getattr(world, "ruck_x_m", None),
                 getattr(world, "defensive_line_anchor", None),
                 getattr(world, "ball_carrier", None)))
        _dump_212("after-frame")

print("done")
