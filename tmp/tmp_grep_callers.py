"""Temp: locate the apply_tes_drive_structure call site and its defenders arg."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATTERNS = ("apply_tes_drive_structure", "clear_tes_drive_structure")
SKIP = ("__pycache__", ".git", "node_modules", "debug_archive", "frontend", "tmp")

hits = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in SKIP]
    for fn in filenames:
        if not fn.endswith(".py"):
            continue
        path = os.path.join(dirpath, fn)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            if any(p in line for p in PATTERNS):
                rel = os.path.relpath(path, ROOT)
                hits.append(f"{rel}:{i + 1}: {line.strip()}")

print(f"== {len(hits)} hit(s) ==")
for h in hits:
    print(h)
