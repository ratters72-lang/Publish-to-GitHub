"""Temp grep helper: find 'lateral authority' style terms across the codebase."""
import os
import re
import sys

ROOTS = ["game", "world", "player", "kick_off", "ball"]
PATTERNS = [
    r"lateral_authority",
    r"lateral authority",
    r"out[_-]of[_-]lane",
    r"out of lane",
    r"in[_-]lane",
    r"in lane",
    r"phase[_-]gat",
    r"lane_band",
    r"lane_tol",
]

rx = re.compile("|".join(PATTERNS), re.IGNORECASE)

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
