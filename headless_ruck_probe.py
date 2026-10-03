"""headless_ruck_probe.py — drive a real tackle headless and capture [RUCK-PIN] lines.

Run:  python headless_ruck_probe.py > tmp_ruck_probe.txt 2>&1
Then grep the output for [RUCK-PIN] lines during TACKLE_DRIVE to confirm they
read TLOCK (target-only) rather than SNAP (position snap).
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import sys
import math

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

import pygame
pygame.init()
# Mandatory: .convert() on any Surface needs a real (or dummy) video mode set.
pygame.display.set_mode((1, 1))

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

SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080
FPS = 60

field = Field(SCREEN_WIDTH, SCREEN_HEIGHT)
world = WorldState()
ball = Ball(field)
world.ball = ball

open_play = OpenPlayEngine(world)
world.open_play = open_play
world.open_play_engine = open_play

kickoff_engine = KickoffEngine(world, ball, field)
collision_system = CollisionSystem()


def reset_ball_and_world():
    kickoff_engine.reset_for_kickoff()


def kickoff():
    kickoff_engine.kickoff()


def main():
    reset_ball_and_world()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)

    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff()

    dt = 1.0 / FPS

    seen_phases = {}

    # Run up to MAX_FRAMES; capture every [RUCK-PIN] line + phase label.
    MAX_FRAMES = 1500
    for i in range(MAX_FRAMES):
        ball.update(dt)

        if world.phase == GamePhase.KICKOFF:
            kickoff_engine.update(dt)
            check_for_catch(world, ball, dt)

        elif world.phase == GamePhase.CATCH:
            if world.ball_carrier is not None:
                ope = world.open_play
                if ope is not None:
                    ope.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY

        elif world.phase == GamePhase.OPEN_PLAY:
            open_play.update(dt)
            # Collision only when no TES engagement active
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

        phase = str(getattr(world, "_ope_phase_label", "") or "")
        seen_phases[phase] = seen_phases.get(phase, 0) + 1

        if i % 30 == 0:
            print(f"--- frame {i} phase={phase} sub={getattr(world, 'sub_state', '?')} ---")

    print("=== PHASE COUNTS ===")
    for k, v in sorted(seen_phases.items()):
        print(f"  {k}: {v}")

    pygame.quit()


if __name__ == "__main__":
    main()
