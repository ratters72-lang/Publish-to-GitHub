"""Print the verdict block of a verifier log given on argv[1]."""
import sys

path = sys.argv[1]
with open(path, encoding="utf-8", errors="replace") as fh:
    lines = fh.read().splitlines()

print("PATH=%s LINES=%d" % (path, len(lines)))
keys = ("RESULT", "INFO:", "ASSERT", "FAIL", "ERROR", "Traceback")
hits = [(i, l) for i, l in enumerate(lines)
        if any(k in l for k in keys)]
for i, ln in hits[-30:]:
    print("%6d | %s" % (i, ln[:240]))
print("--- tail ---")
for i, ln in list(enumerate(lines))[-12:]:
    print("%6d | %s" % (i, ln[:240]))
