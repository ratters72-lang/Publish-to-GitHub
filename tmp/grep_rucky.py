"""Temp grep helper: find writes to ruck_y_m / ruck_x_m and lane seeding."""
import os
import re
import sys

ROOTS = ["game", "world", "player", "kick_off", "ball"]
PATTERNS = [
    r"ruck_y_m\s*=",
    r"ruck_x_m\s*=",
    r"frame_spacing_y\s*=",
    r"frame_spacing_targets\s*=",
]

rx = re.compile("|".join(PATTERNS))

hits = 0
for root in ROOTS:
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    for i, line in enumerate(fh, 1):
                        if rx.search(line):
                            hits += 1
                            if hits <= 200:
                                print(f"{path}:{i}: {line.rstrip()}")
            except Exception as exc:  # noqa: BLE001
                print(f"!! {path}: {exc}", file=sys.stderr)

print(f"\nTOTAL HITS: {hits}")
