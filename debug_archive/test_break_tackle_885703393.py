"""Quick sim test: verify break-tackle events from try_pre_tackle()."""
import sys, math, random
sys.path.insert(0, '.')
from game.defence.defence_lvl_1.defensive_behaviour_lvl1 import try_pre_tackle
from player.player import Player, DefenderRole
from world.world_state import GameSubState

class MockWorld:
    ball_owner = "BC"
    ruck_x_m = 60.0
    sub_state = GameSubState.OPEN_PLAY
    defending_team = []

class MockTuners:
    retreat_compliance_distance_m = 10.0
    rng = random.Random(42)

def setup():
    """Return (bc, defender, world, tuners) with defender onside (x >= 70)."""
    bc = Player(75.0, 35.0, player_id=1)
    bc.vx = 5.0
    bc.vy = 0.0
    bc.mass = 95.0
    bc.facing = 0.0
    bc._prev_facing = 0.0
    bc.tackle_skill = 0.5
    bc.strength = 0.5

    defender = Player(73.0, 35.0, player_id=2)
    defender.vx = 3.0
    defender.vy = 0.0
    defender.mass = 90.0
    defender.tackle_skill = 0.6
    defender.tackle_role = "PRIMARY"
    
    return bc, defender, MockWorld(), MockTuners()

# Test 1: Normal head-on tackle (should pass ~90-95%)
bc, defender, world, tuners = setup()
results = {"PASS": 0, "FAIL": 0}
tuners.rng = random.Random(42)
for i in range(100):
    r = try_pre_tackle(world, defender, bc, tuners)
    if r:
        results["PASS"] += 1
    else:
        results["FAIL"] += 1
print(f"Test 1 - Head-on tackle (100 tries): {results['PASS']} passes, {results['FAIL']} fails")
print(f"  Expecting ~95% pass (5% base random). Got {results['PASS']}%.")

# Test 2: BC outrunning defender
bc, defender, world, tuners = setup()
bc.vx = 10.0
defender.vx = 3.0
r = try_pre_tackle(world, defender, bc, tuners)
print(f"Test 2 - BC outruns (vx=10 vs 3): {'PASS (BUG!)' if r else 'FAIL (correct)'}")

# Test 3: Defender overshoots
bc, defender, world, tuners = setup()
bc.vx = 2.0
defender.vx = 7.0
r = try_pre_tackle(world, defender, bc, tuners)
print(f"Test 3 - Defender overshoots (vx=2 vs 7): {'PASS (BUG!)' if r else 'FAIL (correct)'}")

# Test 4: Distance too far
bc, defender, world, tuners = setup()
defender.x = 70.0  # 5m away
r = try_pre_tackle(world, defender, bc, tuners)
print(f"Test 4 - Too far (5m): {'PASS (BUG!)' if r else 'FAIL (correct)'}")

# Test 5: Side-angle tackle slip chance
bc, defender, world, tuners = setup()
defender.y = 37.0  # 2m Y offset from BC
dx = bc.x - defender.x
dy = bc.y - defender.y
dist = math.hypot(dx, dy)
to_bc_dx = dx / dist
to_bc_dy = dy / dist
spd = math.hypot(defender.vx, defender.vy)
def_dx = defender.vx / spd
def_dy = 0.0
dot = def_dx * to_bc_dx + def_dy * to_bc_dy
angle = math.degrees(math.acos(max(-1.0, min(1.0, dot))))
tuners.rng = random.Random(99)
results_side = {"PASS": 0, "FAIL": 0}
for i in range(100):
    bc2, d2, w2, t2 = setup()
    d2.y = 37.0
    t2.rng = random.Random(i)
    r = try_pre_tackle(w2, d2, bc2, t2)
    if r:
        results_side["PASS"] += 1
    else:
        results_side["FAIL"] += 1
print(f"Test 5 - Side-angle angle~{angle:.0f}° (100 tries): {results_side['PASS']} passes, {results_side['FAIL']} fails")
print(f"  Expecting ~40-55% (50% slip + ~5% random noise)")

# Test 6: Low momentum defender
bc, defender, world, tuners = setup()
bc.vx = 8.0
bc.mass = 100.0
defender.vx = 2.0
defender.mass = 70.0
r = try_pre_tackle(world, defender, bc, tuners)
print(f"Test 6 - BC drives through (ratio=0.175): {'PASS (BUG!)' if r else 'FAIL (correct)'}")

# Test 7: BC direction change
bc, defender, world, tuners = setup()
bc.facing = 1.0
bc._prev_facing = 0.0
tuners.rng = random.Random(7)
results_wf = {"PASS": 0, "FAIL": 0}
for i in range(100):
    bc2, d2, w2, t2 = setup()
    bc2.facing = 1.0
    bc2._prev_facing = 0.0
    t2.rng = random.Random(i)
    r = try_pre_tackle(w2, d2, bc2, t2)
    if r:
        results_wf["PASS"] += 1
    else:
        results_wf["FAIL"] += 1
print(f"Test 7 - BC direction change 57° (100 tries): {results_wf['PASS']} passes, {results_wf['FAIL']} fails")
print(f"  Expecting ~30-40% (40% wrong-foot + 5%)")

# Test 8: NOT a tackler
bc, defender, world, tuners = setup()
defender.tackle_role = "PASSIVE"
r = try_pre_tackle(world, defender, bc, tuners)
print(f"Test 8 - Not PRIMARY/SECONDARY: {'PASS (BUG!)' if r else 'FAIL (correct)'}")

print("\nAll break-tackle tests completed.")
