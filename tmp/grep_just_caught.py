"""Scan repo for all references to just_caught_this_frame (pre-change audit).

Confirms which modules WRITE (set True/False) vs READ the catch flag before
OpenPlayEngine consumes it one-shot.
"""
import io
import os
import sys

sys.path.insert(0, os.getcwd())

ROOT = "."
SKIP_DIRS = {"tmp", "debug_archive", "__pycache__", ".git", "ai_agents"}
EXT = {".py"}

hits = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
    for name in filenames:
        if not name.endswith((".py", ".md")):
            continue
        path = os.path.join(dirpath, name).replace("\\", "/")
        try:
            with io.open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            if "just_caught_this_frame" in line:
                hits.append((path, lineno, line.strip()))

if not hits:
    print("No references found.")
    sys.exit(0)

for path, lineno, line in hits:
    kind = "WRITE" if "=" in line and "just_caught_this_frame =" in line else "READ/OTHER"
    print(f"{kind}: {path}:{lineno}: {line[:120]}")
