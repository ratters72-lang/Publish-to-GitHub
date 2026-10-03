"""
Clear all __pycache__ directories to force fresh bytecode compilation.
Then verify every game.* and world.* module loads from the correct source file.
"""
import sys
import os
import shutil

cwd = os.getcwd()

print("=" * 70)
print("CLEARING ALL __pycache__ DIRECTORIES")
print("=" * 70)
count = 0
for root, dirs, files in os.walk(cwd):
    if '__pycache__' in root:
        try:
            shutil.rmtree(root)
            print(f"  DELETED: {root}")
            count += 1
        except Exception as e:
            print(f"  FAILED: {root} ({e})")

print(f"\nDeleted {count} __pycache__ directories")

print()
print("=" * 70)
print("NOW VERIFYING EVERY MODULE LOADS FROM CORRECT FILE")
print("=" * 70)

# Remove old cached modules from sys
for key in list(sys.modules.keys()):
    if key.startswith('world') or key.startswith('game') or key.startswith('player') or key.startswith('ball') or key.startswith('kick_off'):
        del sys.modules[key]

# Reimport with fresh cache
imports = {}
original_import = __builtins__.__import__ if isinstance(__builtins__, dict) else __builtins__.__import__

def tracing_import(name, *args, **kwargs):
    result = original_import(name, *args, **kwargs)
    imports[name] = getattr(result, '__file__', 'NO_FILE')
    return result

if isinstance(__builtins__, dict):
    __builtins__['__import__'] = tracing_import
else:
    __builtins__.__import__ = tracing_import

from world.world_state import WorldState, GameWorldState, GameSubState
from world.ruck_state import RuckState
from world.open_play_engine import OpenPlayEngine
import world.field
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

print()
print("=" * 70)
print("FRESH IMPORT RESOLUTION")
print("=" * 70)
for name, path in sorted(imports.items()):
    if (name.startswith('world') or name.startswith('game.') or 
        name.startswith('player') or name.startswith('ball') or 
        name.startswith('kick_off')):
        expected = name.replace('.', '/') + '.py'
        # Check if it matches
        if path and path.endswith(expected):
            status = "OK"
        elif path and path.endswith('__init__.py'):
            status = f"INFO (init)"
        else:
            status = "CHECK"
        print(f"  {status:10s} {name:50s} → {path}")

print()
print("=" * 70)
print("FINAL CHECKS")
print("=" * 70)

from world.open_play_engine import OpenPlayEngine as OPE
print(f"  OpenPlayEngine has _update_ptb:    {hasattr(OPE, '_update_ptb')}")
print(f"  OpenPlayEngine has _update_tackle_contact: {hasattr(OPE, '_update_tackle_contact')}")
print(f"  OpenPlayEngine has _check_tackle_initiation: {hasattr(OPE, '_check_tackle_initiation')}")
print(f"  OpenPlayEngine has frames_since_ptb: {hasattr(OPE, 'frames_since_ptb')}")
print(f"  OpenPlayEngine has _world_id:      {hasattr(OPE, '_world_id')}")

from world.world_state import WorldState
ws = WorldState()
print(f"  WorldState has set_sub_state:      {hasattr(ws, 'set_sub_state')}")
print(f"  WorldState has freeze_bc:          {hasattr(ws, 'freeze_bc')}")
print(f"  WorldState has freeze_ball:        {hasattr(ws, 'freeze_ball')}")
print(f"  WorldState has set_ruck:           {hasattr(ws, 'set_ruck')}")
print(f"  WorldState has ruck_cycle:         {hasattr(ws, 'ruck_cycle')}")
print(f"  WorldState has just_left_kickoff_timer: {hasattr(ws, 'just_left_kickoff_timer')}")
print(f"  WorldState has primary_tackler_id: {hasattr(ws, 'primary_tackler_id')}")
print(f"  WorldState has secondary_tacklers: {hasattr(ws, 'secondary_tacklers')}")

from world.world_state import GameSubState
print(f"  GameSubState.PTB:                  {GameSubState.PTB}")
print(f"  GameSubState.POST_RUCK_OPEN_PLAY:  {GameSubState.POST_RUCK_OPEN_PLAY}")
print(f"  GameSubState.TACKLE_CONTACT:       {GameSubState.TACKLE_CONTACT}")
print(f"  GameSubState.OPEN_PLAY:            {GameSubState.OPEN_PLAY}")

print()
print("=" * 70)
print("=== COMPLETE ===")
print("=" * 70)
