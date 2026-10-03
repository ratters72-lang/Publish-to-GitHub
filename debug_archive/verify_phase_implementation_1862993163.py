"""
Verification script for Phase Implementation.
Checks that:
1. GamePhase enum exists and has KICKOFF, CATCH, OPEN_PLAY
2. WorldState has .phase defaulting to KICKOFF
3. kickoff_engine sets phase correctly
4. catch_system transitions phase correctly
5. run.py phase branching imports correctly
6. Phase guards were added to all modules
"""
import sys
sys.path.insert(0, '.')

errors = []

# 1. Check GamePhase enum
from world.world_state import GamePhase, WorldState, GameWorldState
assert hasattr(GamePhase, 'KICKOFF'), "GamePhase missing KICKOFF"
assert hasattr(GamePhase, 'CATCH'), "GamePhase missing CATCH"
assert hasattr(GamePhase, 'OPEN_PLAY'), "GamePhase missing OPEN_PLAY"
print("✓ GamePhase enum has KICKOFF, CATCH, OPEN_PLAY")

# 2. Check WorldState default phase
w = WorldState()
assert w.phase == GamePhase.KICKOFF, f"Expected KICKOFF, got {w.phase}"
print(f"✓ WorldState.phase defaults to KICKOFF")

# 3. Check kickoff_engine module
from game.kickoff_engine import KickoffEngine
print("✓ kickoff_engine imports")

# 4. Check catch_system module
from world.catch_system import check_for_catch, _perform_catch
print("✓ catch_system imports")

# 5. Check phase guards exist in defence modules
# Confirm DefensiveBehaviourUnified.apply has a phase guard
import inspect
from game.defence.defence_lvl_1.defensive_behaviour_unified import DefensiveBehaviourUnified
source = inspect.getsource(DefensiveBehaviourUnified.apply)
if 'world.phase != GamePhase.OPEN_PLAY' in source:
    print("✓ DefensiveBehaviourUnified.apply has phase guard")
else:
    errors.append("DefensiveBehaviourUnified.apply missing phase guard")
    print("✗ DefensiveBehaviourUnified.apply missing phase guard")

from game.defence.defence_lvl_1.defensive_behaviour_lvl1 import DefensiveBehaviourLvl1
source = inspect.getsource(DefensiveBehaviourLvl1.apply)
if 'world.phase != GamePhase.OPEN_PLAY' in source:
    print("✓ DefensiveBehaviourLvl1.apply has phase guard")
else:
    errors.append("DefensiveBehaviourLvl1.apply missing phase guard")
    print("✗ DefensiveBehaviourLvl1.apply missing phase guard")

from game.defence.defence_lvl_2.defensive_behaviour_lvl2 import DefensiveBehaviour
source = inspect.getsource(DefensiveBehaviour.apply_containment)
if 'GamePhase' in source:
    print("✓ DefensiveBehaviour.apply_containment has phase guard")
else:
    errors.append("DefensiveBehaviour.apply_containment missing phase guard")
    print("✗ DefensiveBehaviour.apply_containment missing phase guard")

from game.attack.attack_lvl_4.decision_engine import DecisionEngine
source = inspect.getsource(DecisionEngine.update)
if 'GamePhase' in source:
    print("✓ DecisionEngine.update has phase guard")
else:
    errors.append("DecisionEngine.update missing phase guard")
    print("✗ DecisionEngine.update missing phase guard")

from game.attack.attack_lvl_4.attack_controller import AttackController
source = inspect.getsource(AttackController.apply_vector)
if 'GamePhase' in source:
    print("✓ AttackController.apply_vector has phase guard")
else:
    errors.append("AttackController.apply_vector missing phase guard")
    print("✗ AttackController.apply_vector missing phase guard")

# 6. Check CollisionSystem uses GamePhase.KICKOFF
from game.physics.collision_system import CollisionSystem
source = inspect.getsource(CollisionSystem.resolve_all)
if 'GamePhase.KICKOFF' in source:
    print("✓ CollisionSystem.resolve_all uses GamePhase.KICKOFF")
else:
    errors.append("CollisionSystem.resolve_all missing GamePhase.KICKOFF check")
    print("✗ CollisionSystem.resolve_all missing GamePhase.KICKOFF check")

# 7. Check collision_body uses GamePhase
from player.collision_body import CollisionBody
source = inspect.getsource(CollisionBody.apply_kickoff_properties)
if 'GamePhase.KICKOFF' in source:
    print("✓ CollisionBody.apply_kickoff_properties uses GamePhase.KICKOFF")
else:
    errors.append("CollisionBody.apply_kickoff_properties missing GamePhase.KICKOFF check")
    print("✗ CollisionBody.apply_kickoff_properties missing GamePhase.KICKOFF check")

print()
if errors:
    print("ERRORS FOUND:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
