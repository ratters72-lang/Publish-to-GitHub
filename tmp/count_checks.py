"""tmp/count_checks.py — count a verifier's PASS/FAIL lines in a captured log."""

import sys

path = sys.argv[1] if len(sys.argv) > 1 else "tmp/vcsm_run4.txt"
total = 0
with open(path, "r", encoding="utf-8", errors="replace") as handle:
    for line in handle:
        if "[PASS]" in line or "[FAIL]" in line:
            total += 1
print("%s -> %d check lines" % (path, total))
