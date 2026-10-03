"""Temp: import the modules touching retreat_obligation and report errors."""
import os
import sys
import traceback

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, ".")

MODULES = [
    "game.defence.defence_lvl_1.retreat_obligation",
    "game.defence.defence_lvl_1.pre_tackle_authority",
    "game.defence.defence_lvl_1.pretackle_attempt",
    "game.defence.defence_lvl_1.pretackle_machine",
    "game.defence.defence_lvl_1.pretackle_corridor",
    "game.defence.defence_lvl_1.intercept_tacklers",
    "game.managers.defence_manager",
    "world.open_play_engine",
]

failed = 0
for name in MODULES:
    try:
        __import__(name)
        print("OK    %s" % name)
    except Exception as exc:
        failed += 1
        print("FAIL  %s -> %s: %s" % (name, type(exc).__name__, exc))
        traceback.print_exc()

print()
print("FAILED=%d" % failed)
sys.exit(1 if failed else 0)
