"""locate_conv.py - find convergence writer regions in DBU + MMDL."""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

f = "game/defence/defence_lvl_1/defensive_behaviour_unified.py"
s = open(f, encoding="utf-8", errors="replace").read()

print("== DBU tackler_commit + structured close markers ==")
for pat in ("_apply_tackler_commit", "def _apply_structured_open_play",
            "STRUCTURED_CLOSE", "tackler_commit", "def _apply_step10",
            "structured_close"):
    for m in re.finditer(re.escape(pat), s):
        ln = s[:m.start()].count(chr(10)) + 1
        print(f"  L{ln}: {pat}")

f2 = "game/defence/defence_lvl_1/movement_mode_decision.py"
s2 = open(f2, encoding="utf-8", errors="replace").read()
print("== MMDL markers ==")
for pat in ("def decide_movement_state", "def resolve_movement_state",
            "_mmdl_moved", "mmdl_SLIDE", "mmdl_CHASE"):
    for m in re.finditer(re.escape(pat), s2):
        ln = s2[:m.start()].count(chr(10)) + 1
        print(f"  L{ln}: {pat}")
