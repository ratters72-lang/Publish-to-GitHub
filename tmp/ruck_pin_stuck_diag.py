"""Focused diagnostic: why is the left-side first defender PARKED?

Runs the engine headless; every 30 OPEN_PLAY frames prints the FULL
movement-relevant state of every defending_team member (plus TES
ownership registries) so we can see who owns the parked defender's
position (attachments, shadow writer, freeze, arrival mode, etc.).
"""
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
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

MAX_FRAMES = 1000
dt = 1.0 / 60.0

for frame in range(1, MAX_FRAMES + 1):
    world.frame_count = frame
    ball.update(dt)

    if world.phase == GamePhase.KICKOFF:
        kickoff_engine.update(dt)
        check_for_catch(world, ball, dt)
        continue
    elif world.phase == GamePhase.CATCH:
        if world.ball_carrier is not None:
            world.open_play.start(world.ball_carrier)
        world.phase = GamePhase.OPEN_PLAY
        continue

    open_play.update(dt)
    if not world.tes_active:
        collision_system.resolve_all(world.players, world=world)

    if frame % 30 != 0:
        continue

    ruck_y = getattr(world, "ruck_y_m", None)
    sub = str(getattr(world, "sub_state", "?")).split(".")[-1]
    print(f"\n=== DIAG f={frame} sub={sub} ruck={ruck_y} ===")
    print(
        f"  tackler_ids={sorted(getattr(world, 'tackler_ids', set()) or set())} "
        f"primary={getattr(world, 'primary_tackler_id', None)} "
        f"secondary={getattr(world, 'secondary_tacklers', [])} "
        f"tes_active={getattr(world, 'tes_active', False)}"
    )
    for d in getattr(world, "defending_team", []) or []:
        if d is None:
            continue
        print(
            f"  id={getattr(d, 'player_id', '?'):>3} "
            f"pos=({float(d.x):6.2f},{float(d.y):6.2f}) "
            f"v=({float(getattr(d, 'vx', 0.0)):5.2f},{float(getattr(d, 'vy', 0.0)):5.2f}) "
            f"tgt=({float(getattr(d, 'target_x', float('nan'))):6.2f},"
            f"{float(getattr(d, 'target_y', float('nan'))):6.2f}) "
            f"frozen={int(bool(getattr(d, 'frozen', False)))} "
            f"ftimer={float(getattr(d, 'freeze_timer', 0.0)):.2f} "
            f"drole={getattr(d, 'defender_role', None)} "
            f"depth={getattr(d, 'depth_role', None)} "
            f"side={getattr(d, 'depth_side', None)} "
            f"tsrc={getattr(d, 'target_source', None)} "
            f"mstate={getattr(d, 'movement_state', None)} "
            f"commit={int(bool(getattr(d, '_tackle_commit_active', False)))} "
            f"shadow={getattr(d, 'tes_drive_shadow', None)} "
            f"arr={int(bool(getattr(d, 'arrival_mode', False)))} "
            f"reach={int(bool(getattr(d, 'reached_target', False)))}"
        )

print("\n=== END DIAG ===")
pygame.quit()
