import py_compile
import sys

files = [
    "game/tes/tes_context.py",
    "player/player.py",
    "world/open_play_engine.py",
]
ok = True
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f"OK    {f}")
    except py_compile.PyCompileError as e:
        ok = False
        print(f"FAIL  {f}: {e}")
sys.exit(0 if ok else 1)
