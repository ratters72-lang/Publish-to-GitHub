"""
Isolated test: directly test _accelerate_toward_target on a Player.
No pygame, no world, no engine import needed at all.
"""
import sys, os, math
sys.path.insert(0, os.getcwd())

# Direct import — no pygame dependency in player module
from player.player import Player

p = Player(62.0, 35.0, player_id=99)
p.target_x = 5.0
p.target_y = 35.0
p.frozen = False
p.accel = 0.45
p.max_speed_mps = 9.2

dt = 1.0 / 60.0

print("=== ISOLATED PLAYER MOVEMENT TEST ===")
print(f"Start: x={p.x:.4f}, vx={p.vx:.4f}, target_x={p.target_x:.2f}")
print()

for frame in range(120):
    old_x = p.x
    p._accelerate_toward_target(dt)
    dx = p.x - old_x
    if frame < 10 or frame % 10 == 0:
        print(f"Frame {frame:3d}: x={p.x:.4f}, vx={p.vx:.4f}, dx={dx:.6f}, dist={p.target_x - p.x:.2f}")

print()
print(f"After 120 frames: x={p.x:.4f}, vx={p.vx:.4f}")
print(f"Distance covered: {62.0 - p.x:.2f}m")

# Now test with higher accel
p2 = Player(62.0, 35.0, player_id=100)
p2.target_x = 5.0
p2.target_y = 35.0
p2.frozen = False
p2.accel = 1.5
p2.max_speed_mps = 9.2

for frame in range(120):
    p2._accelerate_toward_target(dt)

print()
print(f"With accel=1.5: x={p2.x:.4f}, vx={p2.vx:.4f}")
print(f"Distance covered: {62.0 - p2.x:.2f}m")
