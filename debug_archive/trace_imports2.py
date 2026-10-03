"""
Deep import trace to find the duplicate execution path.
"""
import sys
import os

cwd = os.getcwd()
if cwd not in sys.path:
    sys.path.insert(0, cwd)

# Record ALL import resolutions under game/ and world/
all_imports = {}

original_import = __builtins__.__import__ if isinstance(__builtins__, dict) else __builtins__.__import__

def tracing_import(name, *args, **kwargs):
    result = original_import(name, *args, **kwargs)
    all_imports[name] = getattr(result, '__file__', 'NO_FILE')
    return result

if isinstance(__builtins__, dict):
    __builtins__['__import__'] = tracing_import
else:
    __builtins__.__import__ = tracing_import

# Now do EXACTLY what run.py does
import world.field
from world.world_state import WorldState, GameWorldState, GameSubState
from world.ruck_state import RuckState
from world.open_play_engine import OpenPlayEngine
from player.player import Player, PlayerState, DefenderRole
from player.receiver_selector import ReceiverSelector
from kick_off.controller import KickoffController, KOState
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from kickoff_kickchase import KickoffKickChaseController
from ball.ball import Ball
from game.physics.collision_system import CollisionSystem
from game.tackle.contact_detector import ContactDetector
from game.tackle.tackle_solidification import TackleSolidifier

# Now trigger all game.* imports that happen when OpenPlayEngine is constructed
from game.defence.defence_lvl_2.defence_state import DefenceContext
from game.defence.defence_lvl_2.marker_controller import MarkerController
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defence_controller import DefenceController
from game.defence.defence_lvl_2.defensive_behaviour_lvl2 import DefensiveBehaviour
from game.defence.defence_lvl_1.defensive_behaviour import DefensiveBehaviour as L1DefensiveBehaviour
from game.attack.dummy_half import DummyHalfController
from game.tackle.tackle_pipeline import TacklePipeline
from game.tes.tes_context import TESContext, PlayerContext as TESPlayerContext, TackleEngagement
from world.defence_utils import assign_markers_and_retreat
from player.movement import apply_tackle_solidification
from game.physics.solid_collision import SolidCollisionResolver
from game.tes.tes_state import TESPhase
from game.engine import GameEngine

print("=" * 70)
print("COMPLETE IMPORT TRACE (game.* and world.* only)")
print("=" * 70)
for name, path in sorted(all_imports.items()):
    if name.startswith('world') or name.startswith('game.') or name.startswith('player') or name.startswith('ball') or name.startswith('kick_off') or name.startswith('world.'):
        print(f"  {name:55s} → {path}")

print()
print("=" * 70)
print("CRITICAL IDENTITY CHECKS")
print("=" * 70)

# 1) Check WorldState identity
import world.world_state
print(f"\n[1] world.world_state module: {world.world_state.__file__}")
print(f"    Has GameSubState: {hasattr(world.world_state, 'GameSubState')}")
print(f"    Has GameWorldState: {hasattr(world.world_state, 'GameWorldState')}")
print(f"    Has RuckCyclePhase: {hasattr(world.world_state, 'RuckCyclePhase')}")

# 2) Does anything import game.world.world_state?
if 'game.world.world_state' in sys.modules:
    m = sys.modules['game.world.world_state']
    print(f"\n[2] game.world.world_state IS in sys.modules! File: {m.__file__}")
    print(f"    Has WorldState: {hasattr(m, 'WorldState')}")
    print(f"    Has GameSubState: {hasattr(m, 'GameSubState')}")
else:
    print(f"\n[2] game.world.world_state NOT in sys.modules (good)")

# 3) Check what WorldState class is actually used by OpenPlayEngine
from world.open_play_engine import OpenPlayEngine as OPE
print(f"\n[3] OpenPlayEngine module: {__import__('world.open_play_engine').__file__}")
print(f"    OPE init signature (first arg): check source code")

# 4) Check for game/world/world_state.py presence
print(f"\n[4] game/world/world_state.py exists: {os.path.exists('game/world/world_state.py')}")

# 5) Check for stale __pycache__
print(f"\n[5] Checking all __pycache__ dirs for staleness:")
for root, dirs, files in os.walk(cwd):
    if '__pycache__' in root:
        for f in files:
            if f.endswith('.pyc'):
                full = os.path.join(root, f)
                # Get corresponding source file
                base = f.split('.')[0]
                parent = os.path.dirname(full)
                src_dir = os.path.dirname(parent)  # parent is __pycache__
                src_file = os.path.join(src_dir, base + '.py')
                if os.path.exists(src_file):
                    src_mtime = os.path.getmtime(src_file)
                    pyc_mtime = os.path.getmtime(full)
                    if src_mtime > pyc_mtime:
                        print(f"    STALE: {full} (src newer)")
                    elif abs(src_mtime - pyc_mtime) > 86400:  # > 1 day old
                        print(f"    OLD:   {full} (src: {src_mtime:.0f}, pyc: {pyc_mtime:.0f})")

# 6) Check actual source of GameEngine
print(f"\n[6] GameEngine module file: {__import__('game.engine').__file__}")
from game.engine import GameEngine
ge = GameEngine()
print(f"    GameEngine instance created OK")
# Does it have update?
print(f"    Has update(): {hasattr(ge, 'update')}")

# 7) Check that the WorldState class has all our methods
print(f"\n[7] WorldState from world.world_state:")
ws = WorldState()
print(f"    Has set_sub_state: {hasattr(ws, 'set_sub_state')}")
print(f"    Has freeze_bc: {hasattr(ws, 'freeze_bc')}")
print(f"    Has freeze_ball: {hasattr(ws, 'freeze_ball')}")
print(f"    Has set_ruck: {hasattr(ws, 'set_ruck')}")
print(f"    Has ruck_cycle: {hasattr(ws, 'ruck_cycle')}")
print(f"    Has just_left_kickoff_timer: {hasattr(ws, 'just_left_kickoff_timer')}")

print()
print("=" * 70)
print("=== DONE ===")
print("=" * 70)
