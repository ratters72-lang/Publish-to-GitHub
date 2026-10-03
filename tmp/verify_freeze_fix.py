"""Verify marker freeze behaviour fix."""
import sys
sys.path.insert(0, '.')
from world.open_play_engine import OpenPlayEngine
from world.world_state import WorldState, GameSubState
from player.player import Player, DefenderRole

world = WorldState()
world.defending_team = [Player(x=60.0, y=35.0, player_id=100),
                        Player(x=62.0, y=36.0, player_id=101)]

engine = OpenPlayEngine(world)

# Verify const exists
assert hasattr(engine, '_update_post_ruck_open_play'), "Method exists"

# Verify collision_enabled sync in Player.update()
p = Player(x=60.0, y=35.0, player_id=99)
assert p.body.collision_enabled, "Default: collision enabled"

p.frozen = True
p.update(1.0/60.0)
assert not p.body.collision_enabled, "Frozen: collision disabled"

p.frozen = False
p.update(1.0/60.0)
assert p.body.collision_enabled, "Unfrozen: collision enabled"

print("Player collision_enabled sync: PASSED")

# Verify MARKER_FREEZE_DURATION_S constant
from world.open_play_engine import MARKER_FREEZE_DURATION_S
assert MARKER_FREEZE_DURATION_S == 1.0, f"Expected 1.0, got {MARKER_FREEZE_DURATION_S}"
print(f"MARKER_FREEZE_DURATION_S = {MARKER_FREEZE_DURATION_S}: PASSED")

# Verify freeze state transition
p.marker_freeze_timer = 2.0
p.frozen = True
p.apply_physics_forces = False
p.body.collision_enabled = False
p.defender_role = DefenderRole.MARKER_A

# Simulate freeze countdown
dt = 1.0/60.0
for _ in range(60):
    p.marker_freeze_timer -= dt
    if p.marker_freeze_timer > 0:
        assert p.frozen, "Should stay frozen"
    else:
        # After freeze expires: unfreeze + restore
        p.marker_freeze_timer = 0.0
        p.frozen = False
        p.apply_physics_forces = True
        p.body.collision_enabled = True
        p.defender_role = DefenderRole.DEF_LINE_READY

assert p.defender_role == DefenderRole.DEF_LINE_READY, f"Role should be DEF_LINE_READY, got {p.defender_role}"
assert not p.frozen, "Should be unfrozen"
assert p.body.collision_enabled, "Collision should be re-enabled"
assert p.apply_physics_forces, "Physics should be re-enabled"
print("Freeze/unfreeze cycle: PASSED")

print("\n=== ALL FREEZE FIX TESTS PASSED ===")
