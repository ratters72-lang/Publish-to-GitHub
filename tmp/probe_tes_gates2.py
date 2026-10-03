"""tmp/probe_tes_gates2.py -- Phase-transition tracer.

Boots the same sim chain as probe_tes_gates.py, but logs EVERY frame from
the first TACKLE phase to (and past) the exit back to OPEN_PLAY, capturing:
  - each transition: frame, from_phase -> to_phase
  - which handler was dispatched (handler_before -> handler_after)
  - whether DRIVE/DOMINANCE/RELEASE were ever entered
  - TES ctx/outcome state at the exit frame

Output: tmp/_tes_gate2_summary.txt
"""

import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
os.environ["SDL_VIDEODRIVER"] = "dummy"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import random
random.seed(42)

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
from game.physics.collision_system import CollisionSystem
from world.catch_system import check_for_catch

DT = 1.0 / 60.0
MAX_FRAMES = 2000

collision_system = CollisionSystem()
field = Field(1920, 1080)
world = WorldState()
ball = Ball(field)
world.ball = ball

open_play = OpenPlayEngine(world, seed=42)
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

TES_PHASES = ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE", "RELEASE", "PTB_PREP", "PTB_EXEC", "PTB")

transitions = []
last_phase = "OPEN_PLAY"
in_tes = False
exit_frame = None
exit_capture = {}
phase_counts = {}

for frame in range(1, MAX_FRAMES + 1):
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
        handler_before = open_play._phase
        open_play.update(DT)
        phase_now = open_play._phase

        phase_counts[phase_now] = phase_counts.get(phase_now, 0) + 1

        if phase_now != last_phase:
            transitions.append((frame, last_phase, phase_now))
            last_phase = phase_now

        if phase_now in TES_PHASES:
            in_tes = True

        # Capture the transition AWAY from a TES-bearing phase chain back to OPEN_PLAY.
        # Careful: RELEASE/PTB are in TES_PHASES here; we only want the FIRST exit
        # that happens after any tackle phase begins.
        if in_tes and phase_now == "OPEN_PLAY":
            exit_frame = frame
            ctx = open_play.tes_ctx
            outcome = open_play.tes_outcome
            exit_capture = {
                "handler_before": handler_before,
                "tes_ctx_is_none": ctx is None,
                "outcome_is_none": outcome is None,
                "primary_is_none": open_play.primary is None,
            }
            if ctx is not None:
                exit_capture["drive_start_x"] = getattr(ctx, "drive_start_x", None)
                exit_capture["drive_target_x"] = getattr(ctx, "drive_target_x", None)
            if outcome is not None:
                result = getattr(outcome, "tackle_result", None)
                exit_capture["result"] = (
                    result.name if hasattr(result, "name") else str(result)
                )
                exit_capture["drive_dist"] = round(getattr(outcome, "drive_distance_m", 0.0), 3)
            break
    else:
        if hasattr(world, "open_play") and world.open_play is not None:
            world.open_play.update(DT)

summary = ["== TES GATE2 TRACER =="]
summary.append(f"frames_run={world.frame_count}")
summary.append("")
summary.append("Phase transitions (frame, from, to):")
for fr, frm, to in transitions:
    summary.append(f"  frame {fr}: {frm} -> {to}")
summary.append("")
summary.append("Phase frame counts:")
for ph, cnt in phase_counts.items():
    summary.append(f"  {ph}: {cnt}")
summary.append("")
summary.append(f"TES exit back to OPEN_PLAY at frame: {exit_frame}")
summary.append(f"TES exit capture: {exit_capture}")

tmp_path = "tmp/_tes_gate2_summary.txt"
with open(tmp_path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(summary))
print("\n".join(summary))
sys.exit(0)
