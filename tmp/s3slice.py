"""s3slice.py - dump targeted regions for Stage 3B planning."""
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

REGIONS = [
    ("world/open_play_engine.py", 1928, 1990),
    ("world/open_play_engine.py", 4150, 4270),
    ("world/open_play_engine.py", 4270, 4340),
]

SEP = "=" * 20
for path, start, end in REGIONS:
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
        lines = fh.readlines()
        fh.close()
    except OSError as e:
        print("!! %s: %s" % (path, e))
        continue
    print("")
    print(SEP + " %s [%d..%d] " % (path, start, end) + SEP)
    for i in range(start - 1, min(end, len(lines))):
        print("%d: %s" % (i + 1, lines[i].rstrip()[:150]))
print("")
print("SLICE DONE")
