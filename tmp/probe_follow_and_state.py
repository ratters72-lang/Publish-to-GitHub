"""Probe _follow_attached_tacklers() and search for Y-side / backward-metre state."""

import re

path = "world/open_play_engine.py"
with open(path, encoding="utf-8") as f:
    text = f.read()
    lines = text.splitlines()

# 1. Find _follow_attached_tacklers and print its body.
start = None
for i, line in enumerate(lines):
    if line.strip().startswith("def _follow_attached_tacklers"):
        start = i
        break

if start is None:
    print("### _follow_attached_tacklers NOT FOUND")
else:
    end = start + 1
    while end < len(lines):
        # stop at next top-level def (no indent) or class
        if lines[end] and not lines[end][0].isspace() and lines[end].strip():
            break
        end += 1
    print(f"### _follow_attached_tacklers (lines {start+1}-{end})")
    for j in range(start, end):
        print(f"{j+1:5d}  {lines[j]}")

print("\n### Global search for Y-side / backward-metre vocabulary")
for term in ("net_backward", "push_y", "side_sign", "side_fold", "drive_target_y"):
    hits = [i + 1 for i, l in enumerate(lines) if term in l]
    print(f"{term}: {hits}")

# 2. Also search the whole game/tes tree for the same terms.
import os
print("\n### game/tes tree search")
for root, _, files in os.walk("game/tes"):
    for fn in sorted(files):
        if not fn.endswith(".py"):
            continue
        p = os.path.join(root, fn)
        with open(p, encoding="utf-8") as fh:
            for ln, l in enumerate(fh, 1):
                for term in ("net_backward", "push_y", "side_sign", "side_fold"):
                    if term in l:
                        print(f"{p}:{ln}: {l.rstrip()}")
