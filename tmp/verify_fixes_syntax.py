"""Verify syntax of all modified files."""
import py_compile
import sys

files = [
    "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
    "game/defence/defence_lvl_1/defensive_timing.py",
    "game/defence/defence_lvl_1/defensive_decisions.py",
]

all_ok = True
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f"OK: {f}")
    except py_compile.PyCompileError as e:
        print(f"ERROR: {f}: {e}")
        all_ok = False

sys.exit(0 if all_ok else 1)
