"""Static scan: flag any defensive movement writer that may still define its
own independent top speed via raw 9.2 / 6.5 / 5.5 constants.

The only raw constants allowed are in defensive_tuners.py (the ceiling
definition itself) and the player physical-movement layer (which is
intentionally higher so the unified ceiling is observable).
"""
import io
import os
import re
import sys

sys.path.insert(0, ".")

ROOT = "game"
RAW = ("9.2", "6.5", "5.5")
KEY_LINE_RE = re.compile(r"speed|_mps", re.IGNORECASE)

ALLOWED_FILES = {
    "game/defence/defence_lvl_1/defensive_tuners.py",
    "player/player.py",
    "player/movement.py",
    "player/movement_states.py",
    "player/collision_body.py",
    "player/arrival_controller.py",
}

hits = []
for dirpath, _dirnames, filenames in os.walk(ROOT):
    for name in filenames:
        if not name.endswith(".py"):
            continue
        path = os.path.join(dirpath, name).replace("\\", "/")
        if path in ALLOWED_FILES:
            continue
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        for lineno, line in enumerate(lines, 1):
            if not KEY_LINE_RE.search(line):
                continue
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "max_defensive_speed_mps" in line or "post_catch_cap_mps" in line:
                continue
            for token in RAW:
                if token in line:
                    hits.append((path, lineno, token, stripped))
                    break

if not hits:
    print("CLEAN: no raw hard-coded speed ceilings found in defensive writers")
else:
    print("RAW CEILING HITS (spec violations - each needs review):")
    for path, lineno, num, text in hits:
        print("%s:%d  [%s]  %s" % (path, lineno, num, text[:90]))
    sys.exit(1)
