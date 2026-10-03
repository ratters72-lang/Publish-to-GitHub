"""pm.py - dump DBU width-recompute + publish region."""
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

s = open("game/defence/defence_lvl_1/defensive_behaviour_unified.py",
         encoding="utf-8", errors="replace").read()

i = s.find("width_targets = recompute_width_x(")
print("at", i)
print(s[i - 900:i + 1400])
