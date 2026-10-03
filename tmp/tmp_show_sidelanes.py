"""Temp: print defensive_split_spacing.side_lanes source."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "game", "defence", "defence_lvl_1",
                    "defensive_split_spacing.py")

with open(PATH, "r", encoding="utf-8", errors="replace") as fh:
    lines = fh.read().splitlines()

start = None
for i, ln in enumerate(lines):
    if ln.startswith("def side_lanes"):
        start = i
        break
if start is None:
    print("side_lanes not found")
else:
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("def "):
            end = j
            break
    print(f"== side_lanes (lines {start + 1}-{end}) ==")
    for i in range(start, min(end, start + 80)):
        print(f"{i + 1}: "
              f"{lines[i].rstrip().encode('ascii', 'replace').decode('ascii')}")
