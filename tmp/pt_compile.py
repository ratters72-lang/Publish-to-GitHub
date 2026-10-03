"""Post-tackle-return lifecycle: import + bytecode compile check."""
import compileall
import sys

TARGETS = [
    "game/defence/defence_lvl_1/fullback_role.py",
    "game/managers/defence_manager.py",
    "game/kickoff_engine.py",
    "world/open_play_engine.py",
]

ok = True
for path in TARGETS:
    if not compileall.compile_file(path, quiet=1, force=True):
        ok = False
        print("COMPILE FAIL:", path)

if not ok:
    sys.exit(1)

from game.defence.defence_lvl_1 import fullback_role as fb

names = [
    "arm_fullback_post_tackle_return",
    "clear_fullback_post_tackle_return",
    "clear_fullback_post_tackle_return_for",
    "is_fullback_post_tackle_return_active",
    "fullback_post_tackle_return_live",
    "fullback_post_tackle_return_release_reason",
    "record_fullback_return_target",
    "assigned_fullback_return_target",
    "fullback_post_tackle_return_enabled",
]
missing = [n for n in names if not hasattr(fb, n)]
print("MISSING:", missing)

dm = __import__("game.managers.defence_manager", fromlist=["x"])
ke = __import__("game.kickoff_engine", fromlist=["x"])
print("dm imports:",
      [hasattr(dm, n) for n in (
          "clear_fullback_post_tackle_return",
          "compute_fullback_ruck_anchor",
          "fullback_post_tackle_return_live",
          "is_fullback_post_tackle_return_active")])
print("ke imports:", hasattr(ke, "clear_fullback_post_tackle_return_for"))
print("OK" if not missing else "FAIL")
