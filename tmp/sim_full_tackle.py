"""
Headless end-to-end sim: mirror run.py's main loop (kickoff -> catch ->
open play -> tackle) WITHOUT Pygame display, and assert the new TES attach
system completes a real tackle cycle (CONTACT attach -> DRIVE -> DOMINANCE
-> RELEASE detach) using the REAL OpenPlayEngine.update() wiring.
"""
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

sys.path.insert(0, ".")

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

SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080
field = Field(SCREEN_WIDTH, SCREEN_HEIGHT)

world = WorldState()
ball = Ball(field)
world.ball = ball
open_play = OpenPlayEngine(world)
world.open_play = open_play
world.open_play_engine = open_play
kickoff_engine = KickoffEngine(world, ball, field)
collision_system = CollisionSystem()

# Kickoff + catch + chase (mirrors run.py main())
kickoff_engine.reset_for_kickoff()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)
world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff_engine.kickoff()

DT = 1.0 / 60.0
MAX_FRAMES = 60 * 60  # 60 sim-seconds
phases_seen = set()
tackle_started = False
contact_attach = False
drive_completed = False
release_detached = False


def check_world_attachments():
    """True if any attached tacklers/offsets are tracked in TES ctx."""
    ctx = open_play.tes_ctx
    if ctx is not None and len(ctx.attached_ids) > 0:
        return True
    return False


for frame in range(MAX_FRAMES):
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
        if open_play._phase in ("TACKLE_INIT", "TACKLE_CONTACT",
                                "TACKLE_DRIVE", "TACKLE_DOMINANCE"):
            tackle_started = True
            if open_play._phase == "TACKLE_CONTACT" and check_world_attachments():
                contact_attach = True
            if open_play._phase == "TACKLE_DRIVE" and check_world_attachments():
                drive_completed = True
        elif open_play._phase == "RELEASE":
            ctx = open_play.tes_ctx
            if ctx is not None and len(ctx.attached_ids) == 0:
                release_detached = True
        phases_seen.add(open_play._phase)
        if not world.tes_active:
            collision_system.resolve_all(world.players, world=world)

    if release_detached and "PTB_PREP" in phases_seen:
        break

print(f"phases seen: {sorted(phases_seen)}")
print(f"tackle_started={tackle_started} contact_attach={contact_attach} "
      f"drive_completed={drive_completed} release_detached={release_detached}")

ok = (
    tackle_started
    and contact_attach
    and drive_completed
    and release_detached
    and "RELEASE" in phases_seen
)
print("END-TO-END TACKLE:", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
