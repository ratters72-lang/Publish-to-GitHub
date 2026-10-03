"""Headless engine capture for the CHASE X-collapse audit.

Boots the real engine exactly like run.py / verify_tes_drive_fix.py, runs
up to MAX_FRAMES, and captures the env-gated probe output:

  MMDL   per-defender target-X / anchor / snap lines  (probe_mmdl_chase)
  STATE  per-CHASE physics lines (dx/dy/dist, desired+final vx/vy,
         CHASE_X_COLLAPSE_TOO_SLOW warnings)

Because the probes only print when RLM_CHASE_PROBE=1, this script forces
the env var at the top BEFORE importing the instrumented modules.

Usage:
  python tmp/chase_capture_engine.py > tmp/chase_capture_out.txt 2>&1
"""
import os

# ── CRITICAL: enable the probes before any instrumented module imports ──
os.environ["RLM_CHASE_PROBE"] = "1"
# ── Headless SDL (same as verify_tes_drive_fix.py) ──
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod

# Silence per-frame SDEA physics spam (observational prints only).
ope_mod.DEBUG_SDEA_PHYSICS = False

from world.field import Field
from world.world_state import WorldState, GameSubState, GamePhase
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


reset_ball_and_world()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)
world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff()

MAX_FRAMES = 900
dt = 1.0 / 60.0

# -- Aggregation of CHASE_X_COLLAPSE_TOO_SLOW sightings ----------------
# keyed by player_id -> {frames, max_gap, min_vx, max_gap_frame}
slow_frames = {}
slow_count = 0
mmdl_total = 0
state_total = 0

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

    # Nothing else needed: probe prints stream to stdout as they fire.

print("\n=== CHASE CAPTURE SUMMARY ===")
print(f"frames_run={MAX_FRAMES}")
print(f"phase_final={getattr(open_play, '_phase', None)}")

# The probes did their own printing; this summary adds the aggregate view.
# Slow frames are printed live by probe_chase_velocity with the
# CHASE_X_COLLAPSE_TOO_SLOW flag; here we can only report the counts if we
# intercept stdout — the live flags in the log are the ground truth, so we
# print an end-of-run marker the analyser can grep for.

print("=== END CHASE CAPTURE (grep 'CHASE_X_COLLAPSE_TOO_SLOW') ===")
pygame.quit()
