"""tmp/probe_dh_role.py - report the DH body's identity and role tag.

Prints, for every RELEASE entry in a short run:
  * open_play.dh.player_id and its defender_role
  * its x / y / target_x / target_y / vx / frozen
  * the ball carrier's x / y
  * which players carry DefenderRole.DUMMY_HALF

Used to confirm how the freeze audit should identify "the parked DH".

Run:  python tmp/probe_dh_role.py
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import sys

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
from player.player import DefenderRole

MAX_FRAMES = 9000
MAX_REPORTS = 6
TES_PHASES = ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
              "TACKLE_DOMINANCE")


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


def collect_lines():
    """Run the sim and return the report lines (engine noise discarded)."""
    lines = []
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system = boot_world()
        dt = 1.0 / 60.0
        prev_phase = None
        seen = 0
        for _ in range(MAX_FRAMES):
            world.frame_count += 1
            ball.update(dt)
            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(dt)
                check_for_catch(world, ball, dt)
                continue
            if world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    open_play.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
                continue
            if world.phase != GamePhase.OPEN_PLAY:
                continue
            open_play.update(dt)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

            phase = open_play._phase
            if phase == "RELEASE" and prev_phase not in ("RELEASE",):
                seen += 1
                if seen <= MAX_REPORTS:
                    dh = open_play.dh
                    bc = world.ball_carrier
                    lines.append("--- RELEASE entry #%d ---" % seen)
                    if dh is None:
                        lines.append("  dh = None")
                    else:
                        lines.append("  dh id=%s role=%s" % (
                            getattr(dh, "player_id", None),
                            getattr(dh, "defender_role", None)))
                        lines.append(
                            "  dh x=%.2f y=%.2f tx=%s ty=%s vx=%.3f frozen=%s"
                            % (dh.x, dh.y,
                               getattr(dh, "target_x", None),
                               getattr(dh, "target_y", None),
                               getattr(dh, "vx", 0.0),
                               getattr(dh, "frozen", None)))
                    if bc is not None:
                        lines.append("  bc id=%s x=%.2f y=%.2f" % (
                            getattr(bc, "player_id", None), bc.x, bc.y))
                    holders = [
                        p.player_id for p in
                        (list(getattr(world, "attacking_team", []) or [])
                         + list(getattr(world, "defending_team", []) or []))
                        if getattr(p, "defender_role", None)
                        == DefenderRole.DUMMY_HALF
                    ]
                    lines.append("  DUMMY_HALF role holders: %s" % (holders,))
            prev_phase = phase
    return lines


def main():
    for line in collect_lines():
        print(line)
    return 0


if __name__ == "__main__":
    code = 0
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
