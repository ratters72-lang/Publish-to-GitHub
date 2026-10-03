"""Verify new kickoff formations produce correct positions."""

import sys
sys.path.insert(0, 'c:/Users/User/Desktop/rlm')

from player.player import Player, PlayerState, DefenderRole, Team as PlayerTeam

FIELD_LENGTH = 120
FIELD_WIDTH = 70
HALFWAY = 60
CENTRE = 35


class MockField:
    halfway_m = HALFWAY
    width_m = FIELD_WIDTH
    centre_m = CENTRE


class MockWorld:
    kicking_team = []
    receiving_team = []
    players = []


# Import the NEW functions
from kick_off.kickoff_formations import (
    apply_kicking_team_formation,
    apply_receiving_kickoff_formation,
)

field = MockField()

# Create teams
kickers = []
for i in range(13):
    p = Player(0, 0, player_id=200 + i)
    p.team = PlayerTeam.KICKING
    kickers.append(p)

receivers = []
for i in range(13):
    p = Player(0, 0, player_id=100 + i)
    p.team = PlayerTeam.RECEIVING
    receivers.append(p)

world = MockWorld()
world.kicking_team = kickers
world.receiving_team = receivers
world.players = kickers + receivers

# Apply formations
apply_kicking_team_formation(world, field)
apply_receiving_kickoff_formation(world, field)

print("=" * 90)
print("KICKING TEAM (should be RIGHT of halfway, x > 60)")
print(f"{'ID':>5} {'X':>7} {'Y':>7} {'TARGET_X':>9} {'TARGET_Y':>9} {'FROZEN':>7}")
print("-" * 45)
for p in sorted(kickers, key=lambda p: p.player_id):
    ok = "OK" if p.x > 60 else "BUG"
    print(f"{p.player_id:>5} {p.x:>7.2f} {p.y:>7.2f} {p.target_x:>9.2f} {p.target_y:>9.2f} {p.frozen!s:>7}  [{ok}]")

print()
print("RECEIVING TEAM (should be LEFT of halfway, x < 60)")
print(f"{'ID':>5} {'X':>7} {'Y':>7} {'TARGET_X':>9} {'TARGET_Y':>9} {'FROZEN':>7}")
print("-" * 45)
for p in sorted(receivers, key=lambda p: p.player_id):
    ok = "OK" if p.x < 60 else "BUG"
    print(f"{p.player_id:>5} {p.x:>7.2f} {p.y:>7.2f} {p.target_x:>9.2f} {p.target_y:>9.2f} {p.frozen!s:>7}  [{ok}]")

# Check bounds
print()
print("=" * 90)
all_ok = True
for p in world.players:
    if not (0 <= p.x <= FIELD_LENGTH):
        print(f"  PLAYER {p.player_id} OUT OF X BOUNDS: x={p.x}")
        all_ok = False
    if not (0 <= p.y <= FIELD_WIDTH):
        print(f"  PLAYER {p.player_id} OUT OF Y BOUNDS: y={p.y}")
        all_ok = False
    if p.target_x != p.x or p.target_y != p.y:
        print(f"  PLAYER {p.player_id} TARGET MISMATCH: target=({p.target_x},{p.target_y}) != pos=({p.x},{p.y})")
        all_ok = False
    if not p.frozen:
        print(f"  PLAYER {p.player_id} NOT FROZEN")
        all_ok = False

if all_ok:
    print("  ALL PLAYERS WITHIN BOUNDS, TARGETS MATCH POSITIONS, FROZEN = TRUE")
else:
    print("  *** BUGS FOUND ***")
