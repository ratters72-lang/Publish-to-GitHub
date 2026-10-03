import py_compile
import sys

files = [
    'player/arrival_controller.py',
    'game/defence/defence_lvl_1/defensive_role_movement.py',
    'player/player.py',
    'world/open_play_engine.py',
]

for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f"OK: {f}")
    except py_compile.PyCompileError as e:
        print(f"FAIL: {f}: {e}")
        sys.exit(1)

print("All files compile successfully")
