"""
Trace which files Python actually loads at runtime for key modules.
"""
import sys
import os

# The CWD when running from project root is c:\Users\User\Desktop\rlm
# Ensure it's on path
cwd = os.getcwd()
if cwd not in sys.path:
    sys.path.insert(0, cwd)

# Record module sources
imports = {}

original_import = __builtins__.__import__ if isinstance(__builtins__, dict) else __builtins__.__import__

def tracing_import(name, *args, **kwargs):
    result = original_import(name, *args, **kwargs)
    if (name.startswith('world') or name.startswith('game.world') or 
        name.startswith('game.defence') or name.startswith('game.tes') or 
        name.startswith('game.tackle') or name.startswith('game.attack') or 
        name.startswith('game.physics') or name.startswith('game.engine') or
        name.startswith('player') or name.startswith('ball') or 
        name.startswith('kick_off')):
        imports[name] = getattr(result, '__file__', 'NO_FILE')
    return result

if isinstance(__builtins__, dict):
    __builtins__['__import__'] = tracing_import
else:
    __builtins__.__import__ = tracing_import

# Simulate run.py imports exactly
import world.field
import world.world_state
import world.ruck_state
import world.open_play_engine
import game.engine
import player.player
import ball.ball
import kick_off.controller

# Try to import game.world modules  
for mod_name in ['game.world', 'game.world.world_state', 'game.world.ruck_state']:
    try:
        __import__(mod_name)
        imports[mod_name] = getattr(sys.modules[mod_name], '__file__', 'NO_FILE')
    except ImportError:
        imports[mod_name] = 'IMPORT_ERROR'

# Trigger all game.defence imports
import game.defence.defence_lvl_2.defence_state
import game.defence.defence_lvl_2.defensive_behaviour_lvl2
import game.defence.defence_lvl_1.defensive_tuners
import game.defence.defence_lvl_1.defence_controller
import game.defence.defence_lvl_1.defensive_behaviour
import game.defence.defence_lvl_2.marker_controller
import game.tackle.tackle_pipeline
import game.tackle.contact_detector
import game.tes.tes_context
import game.tes.tes_state
import game.tes.tes
import game.physics.solid_collision
import game.attack.dummy_half

# Check all WorldState imports
print("=" * 70)
print("ALL TRACED IMPORTS")
print("=" * 70)
for name, path in sorted(imports.items()):
    print(f"  {name:55s} → {path}")

print()
print("=" * 70)
print("WORLD DUPLICATION DETECTION")
print("=" * 70)

# Get the WorldState class from every possible path
from world.world_state import WorldState as WS_world
print(f"  world.world_state.WorldState:  {__import__('world.world_state').__file__}")

# Check if game.world has a separate WorldState
try:
    import game.world.world_state
    m = game.world.world_state
    print(f"  game.world.world_state:       {m.__file__}")
    if hasattr(m, 'WorldState'):
        print(f"  -> HAS ITS OWN WorldState class!")
    else:
        print(f"  -> does NOT have WorldState")
except Exception as e:
    print(f"  game.world.world_state:       ERROR - {e}")

# Check if game/world/world_state.py has WorldState
print()
print("  --- Checking game/world/* files ---")
for root, dirs, files in os.walk('game'):
    if root.endswith('game\\world') or root.endswith('game/world'):
        for f in files:
            full = os.path.join(root, f)
            print(f"    EXISTS: {full}")

# Check GameSubState identity - see if there are TWO GameSubState enums
from world.world_state import GameSubState as GS1
import world.world_state as wws_mod
gs_members1 = dict(GS1.__members__)

# Check game defence modules for imports from game.world
print()
print("  --- Searching game.defence imports for game.world references ---")
for root, dirs, files in os.walk('game/defence'):
    for f in files:
        if f.endswith('.py') and f != '__init__.py':
            full = os.path.join(root, f)
            try:
                with open(full) as fh:
                    content = fh.read()
                    if 'game.world' in content or 'from game.world' in content:
                        print(f"    🔴 FOUND game.world import in {full}")
            except:
                pass

# Check game.tes imports too
print()
print("  --- Searching game.tes imports for game.world references ---")
for root, dirs, files in os.walk('game/tes'):
    for f in files:
        if f.endswith('.py') and f != '__init__.py':
            full = os.path.join(root, f)
            try:
                with open(full) as fh:
                    content = fh.read()
                    if 'game.world' in content or 'from game.world' in content:
                        print(f"    🔴 FOUND game.world import in {full}")
            except:
                pass

# Check game.attack imports too
print()
print("  --- Searching game.attack imports for game.world references ---")
for root, dirs, files in os.walk('game/attack'):
    for f in files:
        if f.endswith('.py') and f != '__init__.py':
            full = os.path.join(root, f)
            try:
                with open(full) as fh:
                    content = fh.read()
                    if 'game.world' in content or 'from game.world' in content:
                        print(f"    🔴 FOUND game.world import in {full}")
            except:
                pass

# Check game.tackle imports too
print()
print("  --- Searching game.tackle imports for game.world references ---")
for root, dirs, files in os.walk('game/tackle'):
    for f in files:
        if f.endswith('.py') and f != '__init__.py':
            full = os.path.join(root, f)
            try:
                with open(full) as fh:
                    content = fh.read()
                    if 'game.world' in content or 'from game.world' in content:
                        print(f"    🔴 FOUND game.world import in {full}")
            except:
                pass

# Check game.physics imports too
print()
print("  --- Searching game.physics imports for game.world references ---")
for root, dirs, files in os.walk('game/physics'):
    for f in files:
        if f.endswith('.py') and f != '__init__.py':
            full = os.path.join(root, f)
            try:
                with open(full) as fh:
                    content = fh.read()
                    if 'game.world' in content or 'from game.world' in content:
                        print(f"    🔴 FOUND game.world import in {full}")
            except:
                pass

print()
print("  --- Checking OpenPlayEngine world identity ---")
e = __import__('world.open_play_engine')
print(f"  world.open_play_engine loaded from: {e.__file__}")

print()
print("  === DONE ===")
