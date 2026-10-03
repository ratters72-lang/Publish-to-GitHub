"""Summarise the fullback-continuity log without loading huge lines blindly."""
import sys

PATH = "tmp/fbcont.txt"
with open(PATH, encoding="utf-8", errors="replace") as fh:
    lines = fh.read().splitlines()

print("LINES=%d" % len(lines))
print("MAXLINE=%d" % (max(len(l) for l in lines) if lines else 0))

def show(tag, rows, limit=300):
    print("--- %s (%d) ---" % (tag, len(rows)))
    for i, ln in rows:
        print("  %6d | %s" % (i, ln[:limit]))

fails = [(i, l) for i, l in enumerate(lines) if "FAIL" in l.upper()]
show("FAIL lines", fails[:60])

results = [(i, l) for i, l in enumerate(lines) if "RESULT" in l.upper()]
show("RESULT lines", results[-10:])

metric = [(i, l) for i, l in enumerate(lines)
          if "active_fb_frames" in l or "multi_active_frame" in l]
show("METRIC lines", metric[-10:])

show("TAIL", list(enumerate(lines))[-25:], limit=400)
