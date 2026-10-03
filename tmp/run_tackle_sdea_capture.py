"""
tmp/run_tackle_sdea_capture.py -- Controlled single-tackle TACKLE SDEA capture.

Boots the full sim chain (kickoff -> catch -> OPEN_PLAY -> first tackle),
then runs the read-only TackleSDEA observer every frame until the first
full tackle cycle returns to OPEN_PLAY.

Outputs (written by the SDEA):
    audit_tackle_sdea_frames_{ts}.csv
    audit_tackle_sdea_tackles_{ts}.csv
    audit_tackle_sdea_issues_{ts}.txt

Usage:
    python tmp/run_tackle_sdea_capture.py [--max-frames N]

Exit 0 = at least one full tackle cycle observed and finalize() written.
Exit 1 = never completed a tackle cycle within the frame cap.
"""

import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
os.environ["SDL_VIDEODRIVER"] = "dummy"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import random  # noqa: E402

random.seed(42)

import pygame  # noqa: E402

pygame.init()
pygame.display.set_mode((64, 64))

from world.field import Field  # noqa: E402
from world.world_state import WorldState, GamePhase  # noqa: E402
from world.open_play_engine import OpenPlayEngine  # noqa: E402
from kick_off.kickoff_formations import (  # noqa: E402
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from game.kickoff_engine import KickoffEngine  # noqa: E402
from ball.ball import Ball  # noqa: E402
from game.physics.collision_system import CollisionSystem  # noqa: E402
from world.catch_system import check_for_catch  # noqa: E402
from ai_agents.defence_reactivity_agent import DefenceReactivityAgent  # noqa: E402

from game.audit.tackle_sdea import TackleSDEA  # noqa: E402


parser = argparse.ArgumentParser()
parser.add_argument("--max-frames", type=int, default=12000)
args = parser.parse_args()

DT = 1.0 / 60.0
MAX_FRAMES = args.max_frames

collision_system = CollisionSystem()

field = Field(1920, 1080)
world = WorldState()
ball = Ball(field)
world.ball = ball

open_play = OpenPlayEngine(world, seed=42)
world.open_play = open_play
world.open_play_engine = open_play

kickoff_engine = KickoffEngine(world, ball, field)
reactivity_agent = DefenceReactivityAgent(world)


def reset_ball_and_world():
    kickoff_engine.reset_for_kickoff()


def kickoff():
    kickoff_engine.kickoff()


reset_ball_and_world()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)

world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff()

# -- TACKLE SDEA: read-only observer, audited AFTER open_play.update(dt) --
sdea = TackleSDEA()

seen_tackle_init = False
back_in_open_play = False
completed = False

for _ in range(MAX_FRAMES):
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
        reactivity_agent.update()
        if not world.tes_active:
            collision_system.resolve_all(world.players, world=world)

        # -- SDEA observes the post-update frame state --
        sdea.audit(world, open_play)

        phase_now = open_play._phase

        if phase_now == "TACKLE_INIT":
            seen_tackle_init = True

        # Cycle complete: we exited a tackle phase back into OPEN_PLAY.
        if seen_tackle_init and phase_now == "OPEN_PLAY":
            back_in_open_play = True

        # Allow the cycle to settle a few frames in OPEN_PLAY before finalize,
        # so the SDEA's OPEN_PLAY close-detection runs inside audit().
        if back_in_open_play:
            back_in_open_play = False  # only once
            completed = True
            break

    else:
        # Any other world phase: keep ticking engine updates if needed.
        if hasattr(world, "open_play") and world.open_play is not None:
            world.open_play.update(DT)

if not completed:
    print(f"[SDEA_CAPTURE] FAIL: no full tackle cycle within {MAX_FRAMES} frames "
          f"(last ope phase={open_play._phase}, frames={world.frame_count})")
    for name in (sdea._frames_path, sdea._tackles_path, sdea._issues_path):
        if os.path.exists(name):
            print(f"[SDEA_CAPTURE] partial output left at {name}")
    sys.exit(1)

sdea.finalize()

print(f"[SDEA_CAPTURE] OK: full tackle cycle observed. frames={world.frame_count}")
for name in (sdea._frames_path, sdea._tackles_path, sdea._issues_path):
    print(f"[SDEA_CAPTURE] {name}")
sys.exit(0)
