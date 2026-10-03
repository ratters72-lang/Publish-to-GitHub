"""Temp helper: print player.py method bodies we need (no truncation)."""
import re
import sys

SRC = "player/player.py"
NEEDED = [
    "def update(",
    "def _uses_smooth_damp(",
    "def _is_chase_state(",
    "def _accelerate_toward_target(",
    "def _update_physics(",
    "def _clamp_position(",
]

with open(SRC, "r", encoding="utf-8") as fh:
    lines = fh.readlines()


def body_start(marker):
    for i, ln in enumerate(lines):
        if ln.strip().startswith(marker.strip()):
            return i
    return None


for marker in NEEDED:
    i = body_start(marker)
    if i is None:
        print(f"### {marker} NOT FOUND\n")
        continue
    indent = len(lines[i]) - len(lines[i].lstrip())
    j = i + 1
    while j < len(lines):
        s = lines[j]
        stripped = s.lstrip()
        if stripped and (len(s) - len(stripped)) <= indent and (
            stripped.startswith("def ") or stripped.startswith("class ")
        ):
            break
        j += 1
    print(f"### {marker}  (lines {i + 1}..{j})")
    sys.stdout.write("".join(lines[i:j]))
    print()
