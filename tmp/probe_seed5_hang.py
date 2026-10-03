"""Focused seed-5 DRIVE-hang probe (2026-08-22).

Runs seed 5 (the sweep run that ended parked in TACKLE_DRIVE with only 5/6
dominances reached) for an extended frame budget.  Once any single DRIVE
stretch exceeds LONG_DRIVE_FRAMES, logs every subsequent DRIVE frame with:

    frame, _phase_timer, ctx.drive_elapsed_s, dur, t,
    tes_ctx identity, tackle_duration_s

so we can see whether the timer stalls, the duration grows, or the phase
re-enters DRIVE repeatedly.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
import sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import math
import pygame

NL = chr(10)

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod
ope_mod.DEBUG_SDEA_PHYSICS = False

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

SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 5
MAX_FRAMES = int(sys.argv[2]) if len(sys.argv) > 2 else 6000
LONG_DRIVE_FRAMES = 130   # normal drives are <= ~120 frames (1.95 s)

LOG = []
STATS = {
    "tackles_started": 0,
    "broken_tackles": 0,
    "release_reached": 0,
    "dominance_reached": 0,
}
_cur = {"consec_drive": 0, "hang_logged": False}

_orig_check_raw = OpenPlayEngine._check_break_tackle
_orig_handle_break = OpenPlayEngine._handle_broken_tackle
_orig_set_phase = OpenPlayEngine._set_phase


def wrapped_check_raw(self, dt):
    fired = _orig_check_raw(self, dt)
    if fired:
        STATS["broken_tackles"] += 1
    return fired


def wrapped_handle_break(self):
    STATS["broken_tackles"] += 1
    LOG.append("BROKEN_TACKLE f=%d" % self.world.frame_count)
    return _orig_handle_break(self)


def wrapped_set_phase(self, phase, sub_state, reason):
    prev = self._phase
    if prev != phase:
        if phase == "TACKLE_INIT":
            STATS["tackles_started"] += 1
        elif phase == "TACKLE_DOMINANCE":
            STATS["dominance_reached"] += 1
        elif phase == "RELEASE":
            STATS["release_reached"] += 1
        if prev == "TACKLE_DRIVE":
            LOG.append("DRIVE_EXIT f=%d -> %s (%s) after %d consec frames"
                       % (self.world.frame_count, phase, reason,
                          _cur["consec_drive"]))
        if phase != "TACKLE_DRIVE":
            _cur["consec_drive"] = 0
            _cur["hang_logged"] = False
    return _orig_set_phase(self, phase, sub_state, reason)


OpenPlayEngine._check_break_tackle = wrapped_check_raw
OpenPlayEngine._handle_broken_tackle = wrapped_handle_break
OpenPlayEngine._set_phase = wrapped_set_phase


def run(seed, max_frames):
    import random
    random.seed(seed)
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

    collision_system = CollisionSystem()
    dt = 1.0 / 60.0
    for frame in range(1, max_frames + 1):
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

            # Long-drive detector: log every frame past the threshold.
            if open_play._phase == "TACKLE_DRIVE":
                _cur["consec_drive"] += 1
                if (_cur["consec_drive"] > LONG_DRIVE_FRAMES
                        and _cur["hang_logged"] is not False
                        or (_cur["consec_drive"] > LONG_DRIVE_FRAMES)):
                    ctx = open_play.tes_ctx
                    outcome = open_play.tes_outcome
                    if ctx is not None and outcome is not None:
                        dur = max(0.1, outcome.tackle_duration_s)
                        t = max(0.0, min(1.0, ctx.drive_elapsed_s / dur))
                        LOG.append(
                            "HANG? f=%d consec=%d phase_timer=%.3f "
                            "elapsed=%.3f dur=%.3f t=%.3f ctx=%d "
                            "bc=(%.2f,%.2f)"
                            % (frame, _cur["consec_drive"],
                               open_play._phase_timer,
                               ctx.drive_elapsed_s, dur, t, id(ctx),
                               open_play.ball_carrier.x,
                               open_play.ball_carrier.y))
    return world, open_play


world, ope = run(SEED, MAX_FRAMES)

out_path = r"c:/Users/User/Desktop/RLM/tmp/seed5_hang_out.txt"
lines = []
lines.append("SEED %d  MAX_FRAMES %d" % (SEED, MAX_FRAMES))
lines.append("STATS: %s" % STATS)
lines.append("FINAL PHASE: %s" % ope._phase)
lines.append("")
lines.extend(LOG[:400])
with open(out_path, "w", encoding="utf-8") as f:
    f.write(NL.join(lines))

print("wrote", out_path)
print("STATS:", STATS)
print("FINAL PHASE:", ope._phase)
print(NL.join(LOG[-25:]))
