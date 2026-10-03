"""tmp/sum_vcsm.py — print only the failing lines + result of a verifier log."""

import sys

path = sys.argv[1] if len(sys.argv) > 1 else "tmp/vcsm_run3.txt"
with open(path, encoding="utf-8", errors="replace") as fh:
    for i, line in enumerate(fh, 1):
        if "FAIL" in line or "RESULT" in line or "Traceback" in line:
            print("%4d: %s" % (i, line.rstrip()))
