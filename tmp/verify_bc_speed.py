"""
Verify the non-compounding BC speed-reduction guard in set_ball_carrier:

  1. First time a player becomes BC  -> max_speed_mps *= 0.95 (9.2 -> 8.74)
  2. Same player already BC          -> reduction NOT re-applied (no compounding)
  3. Repeated calls with same BC     -> value stays 8.74
  4. New player becoming BC          -> that player reduced from 9.2 -> 8.74
  5. Ball sync (true_x/y, possessed_by) still happens on every call
"""
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, ".")

import pygame
pygame.init()
pygame.display.set_mode((64, 64))

from world.world_state import WorldState, set_ball_carrier
from player.player import Player
from ball.ball import Ball
from world.field import Field

FAIL = []

world = WorldState()
field = Field(1920, 1080)
ball = Ball(field)
world.ball = ball

a = Player(10.0, 35.0, player_id=1)
b = Player(20.0, 35.0, player_id=2)

# 1. First BC assignment: a reduced 9.2 -> 8.74
set_ball_carrier(world, a)
if abs(a.max_speed_mps - 8.74) > 1e-9:
    FAIL.append(f"first BC: expected 8.74, got {a.max_speed_mps}")
if world.ball_carrier is not a:
    FAIL.append("first BC: ball_carrier not set to a")
expect_ball_sync = (abs(world.ball.true_x_m - a.x) < 1e-9) and (world.ball.possessed_by is a)
if not expect_ball_sync:
    FAIL.append("first BC: ball not synced to a")

# 2/3. Same BC called again: no compounding, stays 8.74
for _ in range(4):
    set_ball_carrier(world, a)
if abs(a.max_speed_mps - 8.74) > 1e-9:
    FAIL.append(f"repeated same-BC: expected no compounding (8.74), got {a.max_speed_mps}")

# 4. New BC (identity change): b reduced 9.2 -> 8.74; outgoing a restored to 9.2
set_ball_carrier(world, b)
if abs(b.max_speed_mps - 8.74) > 1e-9:
    FAIL.append(f"new BC: expected 8.74, got {b.max_speed_mps}")
if abs(a.max_speed_mps - 9.2) > 1e-9:
    FAIL.append(f"new BC: outgoing carrier a should be restored to base 9.2, got {a.max_speed_mps}")
if world.ball_carrier is not b:
    FAIL.append("new BC: ball_carrier not switched to b")
if not (abs(world.ball.true_x_m - b.x) < 1e-9 and world.ball.possessed_by is b):
    FAIL.append("new BC: ball not re-synced to b")

# 5. Back to a: a restored to 9.2 so new possession = 8.74 again (no ratchet to 8.303)
set_ball_carrier(world, a)
if abs(a.max_speed_mps - 8.74) > 1e-9:
    FAIL.append(f"returning carrier: expected 8.74 (base 9.2 * 0.95, no compounding), got {a.max_speed_mps}")
if abs(b.max_speed_mps - 9.2) > 1e-9:
    FAIL.append(f"returning carrier: b should be restored to base 9.2, got {b.max_speed_mps}")

# 6. Full possession-chain no-ratchet check: a carry -> b -> a -> b repeatedly
#    (3 full cycles).  Chain ends with b as the current BC.  Invariant:
#      - current BC  = base * 0.95 (8.74), never lower -> no ratchet
#      - non-BC      = full base speed (9.2)          -> no permanent degradation
for _ in range(3):
    set_ball_carrier(world, a)
    set_ball_carrier(world, b)
if abs(b.max_speed_mps - 8.74) > 1e-9:
    FAIL.append(
        f"possession chain: current BC b should be 8.74 (no ratchet), got {b.max_speed_mps}"
    )
if abs(a.max_speed_mps - 9.2) > 1e-9:
    FAIL.append(
        f"possession chain: non-BC a should be restored to base 9.2, "
        f"got {a.max_speed_mps}"
    )

print("=== BC SPEED GUARD ===")
for f in FAIL:
    print("  FAIL:", f)
print(f"failures: {len(FAIL)}")
print("BC SPEED GUARD:", "OK" if not FAIL else "FAILED")
sys.exit(0 if not FAIL else 1)
