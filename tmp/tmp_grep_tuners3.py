"""Temp: locate TES hold tuner declarations in defensive_tuners.py."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "game", "defence", "defence_lvl_1",
                    "defensive_tuners.py")
NEEDLES = ("tes_hold", "tes_lane", "tes_band", "tes_lateral", "tes_slot",
           "anticipation_")

with open(PATH, "r", encoding="utf-8", errors="replace") as fh:
    lines = fh.read().splitlines()

print(f"== {PATH} ({len(lines)} lines) ==")
hits = 0
for i, line in enumerate(lines):
    if any(n in line for n in NEEDLES):
        print(f"{i + 1}: {line.rstrip()}")
        hits += 1
print(f"-- {hits} hit(s)")
