"""Probe: which target_source does the tagged fullback carry, and under what
authority state, on the frames that trip verify_fullback_continuity ASSERT-C?

Mirrors the boot + loop of verify_fullback_continuity.py exactly, then
classifies every frame where the single tagged actor's ``target_source`` is
not a fullback-authority source.  The classification decides whether
ASSERT-C is (a) an out-of-date contract that must be scoped to "the sweeper
contract holds only while the actor is NOT a live emergency defender", or
(b) a real bug in which the sweeper's target is stolen without authority.

Run with a fixed PYTHONHASHSEED for a reproducible set/dict iteration order:
    set PYTHONHASHSEED=0 && python tmp/probe_fb_ts.py
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"

import sys
# The engine's freeze audit prints non-ASCII (check marks); a redirected
# stdout defaults to cp1252 on Windows and raises UnicodeEncodeError, which
# killed the first version of this probe at frame 201.  Force UTF-8 exactly
# like the verifiers do.
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import pygame
pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod
ope_mod.DEBUG_SDEA_PHYSICS = False
ope_mod.DEBUG_OPE_TRACE = False

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

from game.defence.defence_lvl_1.fullback_role import (
    fullback_tackle_authority_active,
    is_fullback_emergency_engaged,
    line_break_active,
)

ALLOWED = ("fullback_geometry", "fullback")
MAX_FRAMES = 6000
MAX_DUMPS = 18

collision_system = CollisionSystem()
field = Field(1920, 1080)
world = WorldState()
ball = Ball(field)
world.ball = ball

open_play = OpenPlayEngine(world)
world.open_play = open_play
world.open_play_engine = open_play

kickoff_engine = KickoffEngine(world, ball, field)

import random
random.seed(20260827)

kickoff_engine.reset_for_kickoff()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)
world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff_engine.kickoff()

dt = 1.0 / 60.0

# Buckets: reason -> count
from collections import Counter
buckets = Counter()
first_frames = {}
dumps = 0
authority_false_violations = []


def _sub_state_name():
    try:
        return world.sub_state.name
    except Exception:
        return str(getattr(world, "sub_state", None))


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
    else:
        continue

    active_all = [p for p in world.defending_team
                  if getattr(p, "fullback_role_active", False)]
    if len(active_all) != 1:
        continue
    a = active_all[0]
    ts = getattr(a, "target_source", None)
    pinned = getattr(a, "tes_attached", False) or getattr(a, "in_contact", False)
    if pinned or ts in ALLOWED:
        continue

    auth = fullback_tackle_authority_active(a)
    engaged = is_fullback_emergency_engaged(a)
    brk = line_break_active(world, world.ball_carrier)
    reason = (
        "AUTH_ACTIVE(legit hand-off: %s)"
        % (
            "engaged" if engaged else
            "tackle_role=%s" % getattr(a, "tackle_role", None) if getattr(a, "tackle_role", None) else
            "commit" if getattr(a, "_tackle_commit_active", False) else
            "stance" if getattr(a, "pre_tackle_stance", False) else
            "?"
        )
        if auth else
        "AUTH_FALSE(BUG CANDIDATE)"
    )
    buckets[reason] += 1
    first_frames.setdefault(reason, frame)
    if not auth:
        authority_false_violations.append(frame)

    if dumps < MAX_DUMPS:
        dumps += 1
        print(
            "[FB-TS] f=%d ts=%s writer=%s auth=%s reason=%s\n"
            "        role=%s tackle_role=%s tes_role=%s engaged=%s "
            "commit=%s stance=%s attached=%s contact=%s frozen=%s ms=%s\n"
            "        break=%s bc_x=%s can_tackle=%s sub=%s ope_phase=%s tes_active=%s"
            % (
                frame, ts, getattr(a, "_target_writer", None), auth, reason,
                getattr(a, "defender_role", None),
                getattr(a, "tackle_role", None),
                getattr(a, "tes_tackler_role", None),
                engaged,
                getattr(a, "_tackle_commit_active", False),
                getattr(a, "pre_tackle_stance", False),
                getattr(a, "tes_attached", False),
                getattr(a, "in_contact", False),
                getattr(a, "frozen", False),
                getattr(getattr(a, "movement_state", None), "name", None),
                brk,
                getattr(world.ball_carrier, "x", None),
                getattr(a, "can_acquire_tackle", None),
                _sub_state_name(),
                getattr(open_play, "_phase", None),
                getattr(world, "tes_active", None),
            )
        )

print("=" * 72)
print("PROBE RESULT: non-authority target_source frames by reason:")
for reason, count in buckets.most_common():
    print("   %-40s count=%-6d first_frame=%s"
          % (reason, count, first_frames.get(reason)))
print("AUTH_FALSE frames: %d %s"
      % (len(authority_false_violations), authority_false_violations[:20]))
print("VERDICT: %s"
      % ("OK - every hand-off was authority-granted" if not authority_false_violations
         else "BUG - target stolen without tackle authority"))

pygame.quit()
