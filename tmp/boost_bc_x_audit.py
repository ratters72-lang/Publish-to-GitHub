"""BC.x write audit under the boost + lock: confirm DRIVE/DOMINANCE writes
come only from TES physics (small signed deltas), never a clamp snap."""
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, ".")

import pygame

pygame.init()
pygame.display.set_mode((64, 64))

from world.field import Field
from world.world_state import WorldState, GamePhase
from world.open_play_engine import OpenPlayEngine
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from world.catch_system import check_for_catch
from player.player import Player

Player.DEBUG_BC_X_WRITES = True

SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080
field = Field(SCREEN_WIDTH, SCREEN_HEIGHT)
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

DT = 1.0 / 60.0
MAX_FRAMES = 60 * 60

for frame in range(MAX_FRAMES):
    ball.update(DT)
    world.frame_count += 1

    if world.phase == GamePhase.KICKOFF:
        kickoff_engine.update(DT)
        check_for_catch(world, ball, DT)
    elif world.phase == GamePhase.CATCH:
        if world.ball_carrier is not None:
            ope = world.open_play
            if ope is not None:
                ope.start(world.ball_carrier)
        world.phase = GamePhase.OPEN_PLAY
    elif world.phase == GamePhase.OPEN_PLAY:
        open_play.update(DT)
        if open_play._phase == "RELEASE":
            break

print("=== DONE SIM ===")
