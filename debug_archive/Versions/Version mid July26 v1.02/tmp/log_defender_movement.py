"""
Headless defender movement diagnostic.
Captures and prints Player.update() output for first 120 frames.
Bypasses pygame entirely by patching at the import level.
"""
import sys, os, math

# Patch pygame before anything imports it
import pygame
if not pygame.display.get_init():
    os.environ["SDL_VIDEODRIVER"] = "dummy"

sys.path.insert(0, os.getcwd())

from world.field import Field
from world.world_state import WorldState, GameWorldState, GameSubState
from world.open_play_engine import OpenPlayEngine
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball

field = Field(1920, 1080)
world = WorldState()
ball = Ball(field)
world.ball = ball

open_play = OpenPlayEngine(world)
world.open_play = open_play

kickoff_engine = KickoffEngine(world, ball, field)

# Kickoff setup
kickoff_engine.reset_for_kickoff()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)
world.ball.possessed_by = None
kickoff_engine.kickoff()

# Run for 120 frames, capture only the DEF prints
for frame in range(120):
    dt = 1.0 / 60.0
    ball.update(dt)
    kickoff_engine.update(dt)
    open_play.update(dt)

print("=== DONE ===")
