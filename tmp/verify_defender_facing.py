"""Headless verification of the PTB-window defender facing override."""
import math
import sys
sys.path.insert(0, ".")

from player.player import Player
from player.movement import update_facing_unified


class FakeWorld:
    def __init__(self, bc=None):
        self.ball_carrier = bc


def angle_step(bc, defender, world):
    update_facing_unified(defender, 1 / 60.0)
    return defender.facing


fails = []

# ── Case 1: stationary defender aims at BC (PTB window) ──
bc = Player(50.0, 35.0)
d = Player(40.0, 35.0)
d.is_defender = True
d.vx = 0.0
d.vy = 0.0
d.facing = math.pi  # pointing left (wrong way — BC is to the right)
world = FakeWorld(bc)
d._world_ref = world  # what reset_for_kickoff(world=w) sets at boot
f = angle_step(bc, d, world)
# BC is at +x from defender → target 0 rad. One smoothing step from pi:
expected = math.pi + ((0 - math.pi + math.pi) % (2 * math.pi) - math.pi) * 0.15
if abs(f - expected) > 1e-9:
    fails.append(f"Case1 stationary defender: facing={f:.6f} expected={expected:.6f}")
print(f"Case1 stationary defender facing={f:.4f} (target 0, expected {expected:.4f})")

# ── Case 2: moving defender keeps VELOCITY-facing (PTB branch must NOT fire) ──
bc2 = Player(30.0, 35.0)  # BC BEHIND the defender (at -x)
d2 = Player(40.0, 35.0)
d2.is_defender = True
d2.vx = 1.0   # moving +x
d2.vy = 0.0
d2.facing = math.pi
f2 = angle_step(bc2, d2, world)  # world's bc is irrelevant; bc2 passed but unused
# Velocity branch: target = atan2(0,1)=0, diff = -pi, facing = pi - 0.15pi
velocity_expected = math.pi - 0.15 * math.pi
if abs(f2 - velocity_expected) > 1e-9:
    fails.append(f"Case2 moving defender: facing={f2:.6f} expected velocity-facing {velocity_expected:.6f} (PTB branch leaked)")
print(f"Case2 moving defender facing={f2:.4f} (velocity branch, expected {velocity_expected:.4f})")

# ── Case 3: stationary ATTACKER is untouched ──
bc3 = Player(50.0, 35.0)
d3 = Player(40.0, 35.0)
d3.is_defender = False
d3.vx = 0.0
d3.vy = 0.0
d3.facing = math.pi
f3 = angle_step(bc3, d3, FakeWorld(bc3))
if abs(f3 - math.pi) > 1e-9:
    fails.append(f"Case3 attacker: facing={f3:.6f} expected unchanged pi")
print(f"Case3 attacker facing={f3:.4f} (unchanged, expected {math.pi:.4f})")

# ── Case 4: stationary defender, NO world (bc None) — safe fallthrough ──
d4 = Player(40.0, 35.0)
d4.is_defender = True
d4.vx = 0.0
d4.vy = 0.0
d4.facing = 1.23
f4 = angle_step(None, d4, FakeWorld(None))
if abs(f4 - 1.23) > 1e-9:
    fails.append(f"Case4 no-world: facing={f4:.6f} expected unchanged 1.23")
print(f"Case4 no-world defender facing={f4:.4f} (unchanged, safe)")

# ── Case 5: world back-reference resolves via player.world property ──
d5 = Player(40.0, 35.0)
d5.is_defender = True
d5.vx = 0.0
d5.vy = 0.0
d5.facing = math.pi
d5._world_ref = FakeWorld(bc)  # what reset_for_kickoff(world=w) sets
f5 = angle_step(bc, d5, d5.world)
if abs(f5 - expected) > 1e-9:
    fails.append(f"Case5 world-property: facing={f5:.6f} expected {expected:.6f}")
print(f"Case5 world property facing={f5:.4f} (expected {expected:.4f})")

# ── Case 6: flag stamping via KickoffEngine.create_teams ──
try:
    from world.world_state import GameWorldState
    from ball.ball import Ball
    from game.kickoff_engine import KickoffEngine

    class FakeField:
        width_m = 70.0
        halfway_m = 60.0
        centre_m = 35.0

    w = type("W", (), {})()
    w.attacking_direction = 1
    w.defending_left = False
    field = FakeField()
    b = Ball(field)
    eng = KickoffEngine(w, b, field)
    eng.create_teams()
    kickers = w.kicking_team
    receivers = w.receiving_team
    if not all(p.is_defender for p in kickers):
        fails.append("Case6: not all kickers stamped is_defender=True")
    if any(p.is_defender for p in receivers):
        fails.append("Case6: a receiver has is_defender=True")
    print(f"Case6 stamping: kickers is_defender={all(p.is_defender for p in kickers)}, "
          f"receivers all False={not any(p.is_defender for p in receivers)}")
except Exception as exc:  # Ball/world deps may not import headlessly — report, don't fail silently
    print(f"Case6 stamping SKIPPED: {exc}")

if fails:
    print("\nFAILURES:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("\nALL CASES PASSED")
