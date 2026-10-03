"""Probe the governor leak: why governed defender exceeds the 7.38 hold ceiling."""
import os
import sys
import math

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame
if not pygame.display.get_init():
    pygame.display.set_mode((64, 64))

sys.path.insert(0, os.getcwd())

from player.player import Player

HOLD = Player.DEFENCE_REACTION_HOLD_S
BLEND = Player.DEFENCE_REACTION_BLEND_S
TOTAL = HOLD + BLEND
DT = 1.0 / 60.0

d = Player(x=10.0, y=35.0, player_id=1)
d.move_speed = d.max_speed_mps * 0.8
d.target_x = 40.0
d.target_y = 35.0
for _ in range(120):
    d.update(DT)

kickoff_chase_speed = min(max(d.move_speed, math.hypot(d.vx, d.vy)), d.max_speed_mps)
print(f"kickoff_chase_speed={kickoff_chase_speed:.4f}  facing={d.facing:.3f}")

d.vx = 0.0
d.vy = 0.0
d.move_speed = d.max_speed_mps
d.kickoff_chase_speed = kickoff_chase_speed
d.defence_reaction_remaining_s = TOTAL

print(f"{'t':>6} {'vx':>8} {'vy':>8} {'spd':>8} {'remaining':>10} {'cap':>8} {'facing':>8}")
for i in range(30):
    t = i * DT
    remaining_before = d.defence_reaction_remaining_s
    d.update(DT)
    spd = math.hypot(d.vx, d.vy)
    cap = d._reaction_speed_cap(d.max_speed_mps)
    # did the update leave the speed above the cap?
    leak = spd - cap
    marker = "  <-- LEAK" if leak > 1e-6 else ""
    if i < 30:
        print(f"{t:6.3f} {d.vx:8.3f} {d.vy:8.3f} {spd:8.3f} {d.defence_reaction_remaining_s:10.4f} {cap:8.3f} {d.facing:8.3f}{marker}")
