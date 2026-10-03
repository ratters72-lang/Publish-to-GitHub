import re
import sys

path = sys.argv[1]
pattern = sys.argv[2]
width = int(sys.argv[3]) if len(sys.argv) > 3 else 0

lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
rx = re.compile(pattern)
for i, line in enumerate(lines, 1):
    if rx.search(line):
        lo = max(0, i - 1 - width)
        hi = min(len(lines), i + width)
        print("--- match at line %d ---" % i)
        for j in range(lo, hi):
            print("%5d| %s" % (j + 1, lines[j]))
        print()
