"""Frame-759 diagnostic — pid 212 stuck at vx=0 with live target on an
OPEN_PLAY entry frame (patch ACTIVE, current code).

Mirrors verify_movement_reactivation exactly (incl. its entry-frame stuck
check) and dumps pid 212's full state on every frame in [748, 770] plus
both immediately before/after the first stuck entry frame.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"

import sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import random
random.seed(1234)

import pygame
pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod
ope_mod.DEBUG_SDEA_PHYSICS = False

# Instrument reactivate exactly like the verifier.
_orig_reactivate = ope_mod.reactivate_movement_after_thaw
_call_log = {"count": 0}


def _counting_reactivate(players, dt):
    _call_log["count"] += 1
    return _orig_reactivate(players, dt)


ope_mod.reactivate_movement_after_thaw = _counting_reactivate

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

kickoff_engine.reset_for_kickoff()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)
world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff_engine.kickoff()

MAX_FRAMES = 2400
dt = 1.0 / 60.0
PROBE_PID = 212
WINDOW_LO, WINDOW_HI = 748, 770


def _dump_probe(label):
    for p in world.players:
        if p.player_id == PROBE_PID:
            print(
                "[%s] pid=%s\n"
                "  x=%.3f y=%.3f target=(%.3f,%.3f) dx=%.3f dy=%.3f\n"
                "  vx=%.4f vy=%.4f frozen=%s freeze_t=%.3f\n"
                "  movement_state=%s movement_mode=%s arrival_mode=%s\n"
                "  target_source=%s movement_writer=%s moved=%s\n"
                "  defender_role=%s depth_class=%s\n"
                "  _tackle_commit_active=%s tes_attached=%s\n"
                "  _mmdl_moved=%s pre_tackle_stance=%s\n"
                % (
                    label, p.player_id,
                    p.x, p.y, p.target_x, p.target_y,
                    p.target_x - p.x, p.target_y - p.y,
                    p.vx, p.vy, p.frozen, getattr(p, "freeze_timer", 0.0),
                    getattr(p, "movement_state", None),
                    getattr(p, "movement_mode", None),
                    getattr(p, "arrival_mode", None),
                    getattr(p, "target_source", None),
                    getattr(p, "movement_writer", None),
                    getattr(p, "_moved", None),
                    getattr(p, "defender_role", None),
                    getattr(p, "depth_class", None),
                    getattr(p, "_tackle_commit_active", None),
                    getattr(p, "tes_attached", None),
                    getattr(p, "_mmdl_moved", None),
                    getattr(p, "pre_tackle_stance", None),
                )
            )
            return
    print("[%s] pid %s NOT FOUND" % (label, PROBE_PID))


def _stuck_players():
    stuck = []
    for p in world.players:
        if p.frozen:
            continue
        if (p.vx == 0.0 and p.vy == 0.0
                and (abs(p.target_x - p.x) > 0.01
                     or abs(p.target_y - p.y) > 0.01)):
            stuck.append((p.player_id,
                          getattr(p, "defender_role", None),
                          p.target_x - p.x, p.target_y - p.y))
    return stuck


saw_entry = False
for frame in range(1, MAX_FRAMES + 1):
    world.frame_count = frame
    ball.update(dt)
    entry = False

    if world.phase == GamePhase.KICKOFF:
        kickoff_engine.update(dt)
        check_for_catch(world, ball, dt)
    elif world.phase == GamePhase.CATCH:
        if world.ball_carrier is not None:
            world.open_play.start(world.ball_carrier)
        world.phase = GamePhase.OPEN_PLAY
    elif world.phase == GamePhase.OPEN_PLAY:
        prev_phase = open_play._phase
        open_play.update(dt)
        if not world.tes_active:
            collision_system.resolve_all(world.players, world=world)
        cur = open_play._phase

        if cur == "OPEN_PLAY" and not world.defence_ran_this_frame:
            entry = True
        else:
            entry = False
    else:
        entry = False

    in_window = WINDOW_LO <= frame <= WINDOW_HI
    if in_window:
        _dump_probe("f%d" % frame)

    if entry and not saw_entry:
        saw_entry = True
        stuck = _stuck_players()
        print("=== ENTRY FRAME %d (reactivate calls=%d) ==="
              % (frame, _call_log["count"]))
        print("  stuck=%s" % stuck)
        for (pid, role, dx, dy) in stuck:
            if pid == PROBE_PID:
                _dump_probe("ENTRY-stuck")

print("done  saw_entry=%s" % saw_entry)
