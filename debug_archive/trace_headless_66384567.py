"""
Headless runner: runs N frames of the simulation (no visible Pygame window).
Captures the TARGET_WRITER/GOV_DISABLED/ROLE_DISABLED trace plus the
defender trace dumps so we can diagnose who wrote each defender's target.

Usage:
    python tmp/trace_headless.py
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"

import sys
import math
import pygame
pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

sys.path.insert(0, ".")

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

def reset_ball_and_world():
    kickoff_engine.reset_for_kickoff()

def kickoff():
    kickoff_engine.kickoff()

# --- SETUP ---
reset_ball_and_world()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)

world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff()

MAX_FRAMES = 180  # 3 seconds at 60fps
FPS = 60
dt = 1.0 / FPS

for frame in range(1, MAX_FRAMES + 1):
    world.frame_count = frame

    # --- BALL UPDATE ---
    ball.update(dt)

    # --- PHASE DISPATCH ---
    if world.phase == GamePhase.KICKOFF:
        kickoff_engine.update(dt)
        check_for_catch(world, ball, dt)
    elif world.phase == GamePhase.CATCH:
        if world.ball_carrier is not None:
            ope = world.open_play
            if ope is not None:
                ope.start(world.ball_carrier)
        world.phase = GamePhase.OPEN_PLAY
        print(f"[FRAME {frame}] ENTERED OPEN_PLAY")
    elif world.phase == GamePhase.OPEN_PLAY:
        open_play.update(dt)
        if not world.tes_active:
            collision_system.resolve_all(world.players, world=world)

print(f"\n[HEADLESS] Ran {MAX_FRAMES} frames, exiting.")
pygame.quit()
