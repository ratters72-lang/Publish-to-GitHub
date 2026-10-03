"""Diag: instrument clear_retreat_obligation / stamp_retreat_obligation so
the exact per-frame interleaving on the stalled body is visible.

Answers: is sync clearing the latch, and if so, what re-arms it in the
same frame?
"""

import contextlib
import io
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import pygame
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
from game.defence.defence_lvl_1 import retreat_obligation as ro

DT = 1.0 / 60.0
MAX_FRAMES = 2400
TARGET_PID = 211

_real_clear = ro.clear_retreat_obligation
_real_stamp = ro.stamp_retreat_obligation
_real_sync = ro.sync_retreat_obligation

LOG = []
CURRENT_FRAME = [0]


def _pid(d):
    return getattr(d, "player_id", None)


def clear_retreat_obligation(d):
    if _pid(d) == TARGET_PID:
        LOG.append((CURRENT_FRAME[0], "clear", None))
    return _real_clear(d)


def stamp_retreat_obligation(d, world):
    result = _real_stamp(d, world)
    if _pid(d) == TARGET_PID:
        LOG.append((CURRENT_FRAME[0], "stamp", result))
    return result


def sync_retreat_obligation(world, defenders):
    gated = _real_sync(world, defenders)
    for d in (defenders or ()):
        if _pid(d) == TARGET_PID:
            LOG.append((
                CURRENT_FRAME[0], "sync-sees",
                (bool(getattr(d, ro.OBLIGATION_ATTR, False)),
                 round(ro.retreat_deficit_m(d, world), 3),
                 len(gated)),
            ))
    return gated


ro.clear_retreat_obligation = clear_retreat_obligation
ro.stamp_retreat_obligation = stamp_retreat_obligation
ro.sync_retreat_obligation = sync_retreat_obligation

# The manager imported the names directly, so patch its module bindings too.
import game.managers.defence_manager as dm_mod
if hasattr(dm_mod, "sync_retreat_obligation"):
    dm_mod.sync_retreat_obligation = sync_retreat_obligation
if hasattr(dm_mod, "stamp_retreat_obligation"):
    dm_mod.stamp_retreat_obligation = stamp_retreat_obligation
if hasattr(dm_mod, "strip_tackle_authority"):
    _real_strip = ro.strip_tackle_authority

    def strip_tackle_authority(world, d):
        if _pid(d) == TARGET_PID:
            LOG.append((CURRENT_FRAME[0], "strip", None))
        return _real_strip(world, d)

    dm_mod.strip_tackle_authority = strip_tackle_authority

# The engine also imported the stamp directly.
import world.open_play_engine as ope2
if hasattr(ope2, "stamp_retreat_obligation"):
    ope2.stamp_retreat_obligation = stamp_retreat_obligation
if hasattr(ope2, "clear_retreat_obligation"):
    ope2.clear_retreat_obligation = clear_retreat_obligation


def boot_world():
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
    return world, ball, open_play, kickoff_engine, collision_system


def main():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, ope, kickoff_engine, collision_system = boot_world()
        trace = []
        for _ in range(MAX_FRAMES):
            world.frame_count += 1
            CURRENT_FRAME[0] = int(world.frame_count)
            ball.update(DT)
            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(DT)
                check_for_catch(world, ball, DT)
                continue
            if world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    ope.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
                continue
            if world.phase != GamePhase.OPEN_PLAY:
                continue
            ope.update(DT)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

            for d in (getattr(world, "defending_team", None) or ()):
                if _pid(d) != TARGET_PID:
                    continue
                trace.append((
                    int(world.frame_count),
                    round(float(d.x), 3),
                    round(ro.retreat_deficit_m(d, world), 3),
                    bool(getattr(d, ro.OBLIGATION_ATTR, False)),
                    d.__dict__.get(ro.OBLIGATION_TARGET_ATTR),
                    getattr(getattr(d, "movement_state", None), "name", None),
                ))

    print("=" * 110)
    print("LATCH-CYCLE LOG for player_id=%s (clear / stamp / strip / sync)"
          % TARGET_PID)
    print("=" * 110)
    for frame, kind, payload in LOG[:120]:
        print("  frame=%-6d %-10s %s" % (frame, kind, payload))
    print()
    print("  total instrumented events: %d" % len(LOG))
    print("  clears=%d  stamps(result=%s)=%d"
          % (sum(1 for e in LOG if e[1] == "clear"),
             [e[2] for e in LOG if e[1] == "stamp"][:8],
             sum(1 for e in LOG if e[1] == "stamp")))
    print()
    print("=" * 110)
    print("END-OF-FRAME TRACE for player_id=%s" % TARGET_PID)
    print("=" * 110)
    print("  %6s %9s %9s %8s %9s %-10s"
          % ("frame", "x", "deficit", "latched", "stamped", "movement"))
    for row in trace[:120]:
        print("  %6d %9.3f %9.3f %8s %9s %-10s"
              % (row[0], row[1], row[2], row[3],
                 "-" if row[4] is None else "%.3f" % float(row[4]), row[5]))
    print()
    print("  end-of-frame samples: %d" % len(trace))


if __name__ == "__main__":
    try:
        main()
    finally:
        pygame.quit()
