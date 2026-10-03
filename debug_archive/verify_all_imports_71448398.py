import sys
sys.path.insert(0, '.')

# Test all modified imports
from world.world_state import WorldState, GamePhase, GameSubState, GameWorldState
print("world_state OK: GamePhase =", list(GamePhase))

from game.kickoff_engine import KickoffEngine, KOPhase
print("kickoff_engine OK")

from world.catch_system import check_for_catch
print("catch_system OK")

from game.defence.defence_lvl_1.defensive_behaviour_unified import DefensiveBehaviourUnified
print("defensive_behaviour_unified OK")

from game.defence.defence_lvl_1.defensive_behaviour_lvl1 import DefensiveBehaviourLvl1
print("defensive_behaviour_lvl1 OK")

from game.defence.defence_lvl_2.defensive_behaviour_lvl2 import DefensiveBehaviour
print("defensive_behaviour_lvl2 OK")

from game.attack.attack_lvl_4.attack_controller import AttackController
print("attack_controller OK")

from game.attack.attack_lvl_4.decision_engine import DecisionEngine
print("decision_engine OK")

from game.attack.attack_lvl_4.dummy_half import DummyHalfController
print("dummy_half OK")

from game.physics.collision_system import CollisionSystem
print("collision_system OK")

from player.collision_body import CollisionBody
print("collision_body OK")

print()
print("ALL IMPORTS OK")
