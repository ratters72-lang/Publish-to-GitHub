"""pk.py - locate DBU's internal preamble (assign_depth_roles / compute_targets / recompute_width_x / publish)."""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

s = open("game/defence/defence_lvl_1/defensive_behaviour_unified.py",
         encoding="utf-8", errors="replace").read()

for pat in ("def apply(", "run_defensive_pipeline", "compute_targets",
            "recompute_width_x", "frame_spacing_targets",
            "frame_width_targets", "assign_depth_roles", "SpacingGuard"):
    print(f"===== {pat} =====")
    for m in re.finditer(re.escape(pat), s):
        st = s.rfind(chr(10), 0, m.start()) + 1
        en = s.find(chr(10), m.end())
        print(f"{m.start():8d}: {s[st:en].strip()[:130]}")
    print()
