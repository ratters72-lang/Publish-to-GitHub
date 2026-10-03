"""pi.py - dump the MMDL region of DBU around mmdl_target_x stamping."""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

s = open("game/defence/defence_lvl_1/defensive_behaviour_unified.py",
         encoding="utf-8", errors="replace").read()

i = s.find("MOVEMENT MODE DECISION LAYER (MMDL)")
print("MMDL at", i)
print(s[i:i + 6000])
