"""Seed sweep probe — find a kickoff RNG seed where an OPEN_PLAY entry
frame has an unfrozen player stuck at vx=vy=0 with a live target
(patch ACTIVE).  Silent except for the compact result line.

Usage: python tmp/seed_probe.py <seed>
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
import sys

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
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
if found:
    print("SEED %d FAIL %s" % (seed, ";".join(found)))
else:
    print("SEED %d PASS" % seed)
pygame.quit()
sys.exit(0)
