"""pj.py - find retreat-manager call sites + post-MMDL X writers in DBU."""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

s = open("game/defence/defence_lvl_1/defensive_behaviour_unified.py",
         encoding="utf-8", errors="replace").read()

for pat in ("retreat_manager", "RetreatManager", "apply_forced_retreat",
            "_split_retreat_targets", "defence_manager"):
    print(f"===== {pat} =====")
    for m in re.finditer(pat, s):
        st = s.rfind(chr(10), 0, m.start()) + 1
        en = s.find(chr(10), m.end())
        print(f"{m.start():8d}: {s[st:en].strip()[:120]}")
    print()
