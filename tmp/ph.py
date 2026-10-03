"""ph.py - locate clear_frame_geometry / frame_width_targets lifecycle in DBU."""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

s = open("game/defence/defence_lvl_1/defensive_behaviour_unified.py",
         encoding="utf-8", errors="replace").read()

for pat in ("clear_frame_geometry", "frame_width_targets",
            "frame_geometry_ready", "run_defensive_pipeline"):
    print(f"===== {pat} =====")
    for m in re.finditer(pat, s):
        st = s.rfind(chr(10), 0, m.start()) + 1
        en = s.find(chr(10), m.end())
        ln = s[st:en].strip()
        # include one preceding comment-ish char marker
        print(f"{m.start():8d}: {ln[:120]}")
