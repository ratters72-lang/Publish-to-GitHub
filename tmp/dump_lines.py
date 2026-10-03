"""Temp helper: print a line range of a file (1-based, inclusive)."""
import sys

path, start, end = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
with open(path, "r", encoding="utf-8") as fh:
    lines = fh.readlines()
for i in range(start - 1, min(end, len(lines))):
    sys.stdout.write(f"{i + 1}: {lines[i]}")
