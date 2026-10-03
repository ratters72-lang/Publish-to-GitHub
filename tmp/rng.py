"""Print a 1-indexed inclusive line range of a file to stdout.

usage: python tmp/rng.py <path> <start> <end>
"""
import sys

path = sys.argv[1]
start = int(sys.argv[2])
end = int(sys.argv[3])

with open(path, encoding="utf-8", errors="replace") as fh:
    lines = fh.read().splitlines()

for i in range(start - 1, min(end, len(lines))):
    print("%5d| %s" % (i + 1, lines[i]))
