"""locate_s3d2.py - dump step-10 structured_close + MMDL BC-closing regions."""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

s = open("game/defence/defence_lvl_1/defensive_behaviour_unified.py",
         encoding="utf-8", errors="replace").read()

# Find the step-10 / structured-close writer block inside
# _apply_structured_open_play: look for the marker comment.
i = s.find("10. STRUCTURED_CLOSE")
if i == -1:
    i = s.find("structured_close")
print("step-10 anchor at", i)
# Print the surrounding ~2500 chars to see the whole close block.
print(s[i - 200:i + 3000])

print("\n" + "=" * 70)
print("MMDL file regions")
print("=" * 70)
m = open("game/defence/defence_lvl_1/movement_mode_decision.py",
         encoding="utf-8", errors="replace").read()
for pat in ("CHASE", "chase_target", "target_source", "_mmdl_moved",
            "def decide_movement_state", "def resolve_movement_state"):
    lines = []
    for mm in re.finditer(re.escape(pat), m):
        ln = m[:mm.start()].count(chr(10)) + 1
        lines.append(ln)
    print(f"  {pat}: {lines[:30]}")
