"""Verify that defenders are unfrozen by the fix in world/open_play_engine.py."""
import sys
sys.path.insert(0, '.')
import math
from world.open_play_engine import OpenPlayEngine
from world.world_state import WorldState, GameWorldState, GameSubState

# Create world and engine
w = WorldState()
ope = OpenPlayEngine(w)

# Defenders start frozen (from reset_for_kickoff)
for d in w.defending_team:
    d.frozen = True

print(f"Before update:")
print(f"  in_kickoff: {w.in_kickoff}")
print(f"  defenders frozen: {[d.frozen for d in w.defending_team]}")

# Set up so we're not in kickoff and have a ball carrier
w.in_kickoff = False
w.ball_carrier = w.attacking_team[0]
w.ball_carrier.x = 60.0
w.ball_carrier.y = 35.0

# Store initial x positions
initial_x = [d.x for d in w.defending_team]

# Run one frame
ope.update(1/60)

print(f"\nAfter update:")
print(f"  defenders frozen: {[d.frozen for d in w.defending_team]}")
print(f"  any frozen: {any(d.frozen for d in w.defending_team)}")
print(f"  initial x: {[round(x,1) for x in initial_x]}")
print(f"  new x:     {[round(d.x,1) for d in w.defending_team]}")
print(f"  x changed: {[round(d.x - initial_x[i], 2) for i, d in enumerate(w.defending_team)]}")

# Check if any defender moved
moved = any(abs(d.x - initial_x[i]) > 0.01 for i, d in enumerate(w.defending_team))
print(f"\n  >>> DEFENDERS MOVED: {moved} <<<")

if moved:
    print("SUCCESS: Defenders are now unfrozen and moving.")
    sys.exit(0)
else:
    # Check if they're frozen
    if any(d.frozen for d in w.defending_team):
        print("FAIL: Defenders still frozen.")
    else:
        print("WARN: Defenders are unfrozen but didn't move. Check target_x / speeds.")
    sys.exit(1)
