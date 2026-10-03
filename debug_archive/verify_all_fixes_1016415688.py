"""Verify all 5 fixes work correctly in a 25s simulation."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

os.environ['SDL_VIDEODRIVER'] = 'dummy'
import pygame
pygame.display.init()
pygame.display.set_mode((1,1))

from world.world_state import WorldState, GameSubState
from world.open_play_engine import OpenPlayEngine
from ball.ball import Ball
from world.field import Field
from kick_off.kickoff_formations import apply_receiving_kickoff_formation, apply_kicking_team_formation
from game.kickoff_engine import KickoffEngine
from game.physics.collision_system import CollisionSystem

world = WorldState()
ball = Ball(None)
world.ball = ball
field = Field(1920, 1080)
cs = CollisionSystem()

world.open_play = OpenPlayEngine(world)
world.open_play_engine = world.open_play

kickoff_engine = KickoffEngine(world, ball, field)
kickoff_engine.reset_for_kickoff()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)
world.ball_owner = 'NONE'
world.ball_carrier = None
ball.possessed_by = None
kickoff_engine.kickoff()

# Defenders are the kicking team (defending after kickoff)
defenders = world.defending_team[:]
if not defenders:
    # Fallback: use kicking_team
    defenders = world.kicking_team[:]
print(f"Loaded {len(defenders)} defenders")

primary_locked = 0
secondary_locked = 0
passive_locked = 0
stagger_active_errors = 0
total_open_play_frames = 0

for i in range(1500):
    dt = 1/60.0
    ball.update(dt)
    kickoff_engine.update(dt)
    world.open_play.update(dt)
    if not world.in_kickoff and not world.tes_active:
        cs.resolve_all(world.players, world=world)
    world.frame_count += 1

    if world.sub_state == GameSubState.OPEN_PLAY and not getattr(world, 'role_governs', False) and world.ball_carrier:
        total_open_play_frames += 1
        for d in defenders:
            dc = getattr(d, 'depth_class', '')
            tl = getattr(d, 'timing_locked', False)
            if dc == 'PRIMARY' and tl:
                primary_locked += 1
            if dc == 'SECONDARY' and tl:
                secondary_locked += 1
            if dc == 'PASSIVE' and tl:
                passive_locked += 1

            ss = getattr(d, 'step_stagger', 0)
            ssa = getattr(d, 'step_stagger_active', False)
            if ss > 0 and not ssa:
                stagger_active_errors += 1

print(f"Total OPEN_PLAY frames with BC: {total_open_play_frames}")
print(f"PRIMARY timing_locked:   {primary_locked} (expect 0)")
print(f"SECONDARY timing_locked: {secondary_locked} (expect 0)")
print(f"PASSIVE timing_locked:   {passive_locked} (expect >0 but fewer than 93%)")
print(f"step_stagger_active flag errors: {stagger_active_errors} (expect 0)")

errors = []
if primary_locked > 0:
    errors.append(f"PRIMARY timing_locked should be 0, got {primary_locked}")
if secondary_locked > 0:
    errors.append(f"SECONDARY timing_locked should be 0, got {secondary_locked}")
if stagger_active_errors > 0:
    errors.append(f"step_stagger_active flag errors: {stagger_active_errors}")

class_counts = {"PRIMARY": 0, "SECONDARY": 0, "PASSIVE": 0, "MARKER": 0}
if total_open_play_frames > 0:
    for d in defenders:
        dc = getattr(d, 'depth_class', '')
        if dc in class_counts:
            class_counts[dc] += 1
    print(f"\nFinal depth_class distribution: {class_counts}")
    if class_counts.get("SECONDARY", 0) < 2:
        errors.append("FIX B check: Fewer than 2 SECONDARY defenders - threshold may still be too small")

if errors:
    print(f"\nERRORS: {'; '.join(errors)}")
    sys.exit(1)

print(f"\nALL CHECKS PASSED")
