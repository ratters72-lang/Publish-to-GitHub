"""Same-seed A/B — is the OPEN_PLAY entry-frame stuck (vx=vy=0 + live
target) caused by TASK VI?

- active:  carrer_past_defensive_line = True-normal
- bypass:  carrier_past_defensive_line -> False (TASK VI chase wiring off)

Usage: python tmp/seed_ab.py <seed> <active|bypass>

Also dumps the full state of the first stuck player on the first failing
entry frame so we can see the writer/state at fault.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
import sys

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 3
mode = sys.argv[2] if len(sys.argv) > 2 else "active"
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import random
random.seed(seed)

# Bypass wiring BEFORE anything binds carrier_past_defensive_line.
if mode == "bypass":
    import game.defence.defence_lvl_1.movement_mode_decision as mmd
    mmd.carrier_past_defensive_line = lambda world, bc, extra_m=1.0: False
    import importlib
    dbu = importlib.import_module(
        "game.defence.defence_lvl_1.defensive_behaviour_unified")
    importlib.reload(dbu)

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
found = []
dumped = False


def _dump(p):
    print("  STUCK pid=%s role=%s depth=%s x=%.3f y=%.3f\n"
          "    target=(%.3f,%.3f) dx=%.3f dy=%.3f vx=%.4f vy=%.4f\n"
          "    state=%s mode=%s target_source=%s writer=%s\n"
          "    _mmdl_moved=%s tackle_commit=%s tes=%s frozen=%s"
          % (p.player_id, getattr(p, "defender_role", None),
             getattr(p, "depth_class", None),
             p.x, p.y, p.target_x, p.target_y,
             p.target_x - p.x, p.target_y - p.y, p.vx, p.vy,
             getattr(p, "movement_state", None),
             getattr(p, "movement_mode", None),
             getattr(p, "target_source", None),
             getattr(p, "movement_writer", None),
             getattr(p, "_mmdl_moved", None),
             getattr(p, "_tackle_commit_active", None),
             getattr(p, "tes_attached", None),
             p.frozen))


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
        cur = open_play._phase
        if cur == "OPEN_PLAY" and not world.defence_ran_this_frame:
            for p in world.players:
                if p.frozen:
                    continue
                if (p.vx == 0.0 and p.vy == 0.0
                        and (abs(p.target_x - p.x) > 0.01
                             or abs(p.target_y - p.y) > 0.01)):
                    found.append(
                        "%d,%d,%s,%.3f,%.3f" % (
                            frame, p.player_id,
                            getattr(p, "defender_role", None),
                            p.target_x - p.x, p.target_y - p.y))
                    if not dumped:
                        _dump(p)
                        dumped = True

if found:
    print("SEED %d %s FAIL %s" % (seed, mode, ";".join(found)))
else:
    print("SEED %d %s PASS" % (seed, mode))
pygame.quit()
sys.exit(0)
