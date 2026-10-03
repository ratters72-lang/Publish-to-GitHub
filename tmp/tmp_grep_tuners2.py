"""Temp: list TES hold tuners declared in defensive_tuners.py."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "game", "defence", "defence_lvl_1",
                    "defensive_tuners.py")
PREFIXES = ("tes_", "anticipation_")

with open(PATH, "r", encoding="utf-8", errors="replace") as fh:
    lines = fh.read().splitlines()

print(f"== {PATH} ==")
for i, line in enumerate(lines):
    stripped = line.strip()
    if stripped.startswith(PREFIXES) and ":" in stripped and "=" in stripped:
        print(f"{i + 1}: {stripped}")
