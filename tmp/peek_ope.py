"""Print specific line ranges of world/open_play_engine.py."""
import sys

PATH = "world/open_play_engine.py"
RANGES = [(4270, 4300), (4690, 4720), (4925, 4955), (5045, 5070), (3165, 3195)]

lines = open(PATH, "r", encoding="utf-8").read().splitlines()
for lo, hi in RANGES:
    print(f"===== {PATH}:{lo}-{hi} =====")
    for i in range(lo, hi + 1):
        if 1 <= i <= len(lines):
            print(f"{i:6d}| {lines[i-1]}")
    print()
