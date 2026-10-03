path = "world/open_play_engine.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()

def show(start, end, label):
    print(f"\n===== {label} (lines {start}-{end}) =====")
    for i in range(start, end + 1):
        print(f"{i:5d}  {lines[i-1].rstrip()}")

# scan for _start_tackle / _classify / classification usage
import re
for i, line in enumerate(lines, 1):
    s = line.strip()
    if (s.startswith("def _start_tackle") or s.startswith("def _classify")
            or "classify" in s.lower() and "def " in s
            or "TACKLE_INIT" in s and "set_phase" in s
            or "TACKLE_CONTACT" in s and "set_phase" in s):
        print(f"{i:5d}  {line.rstrip()}")
