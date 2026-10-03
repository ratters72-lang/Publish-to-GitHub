"""tmp_sum_stuck.py — extract ONLY the probe's own signal from a noisy log.

The probe's stdout also carries the engine's per-frame diagnostics, so the
captured log is megabytes.  This script keeps the probe's own report lines
(they are indented with two spaces and carry a fixed vocabulary) and prints
a bounded extract, plus a histogram of whatever else is flooding the file.
"""

import collections
import sys

PATH = "tmp_stuck_carrier.log"

with open(PATH, encoding="utf-8", errors="replace") as fh:
    lines = fh.read().splitlines()

print("TOTAL LINES: %d" % len(lines))

# ── what is flooding the file? ──
hist = collections.Counter()
for ln in lines:
    hist[ln[:70]] += 1
print("")
print("TOP 20 REPEATED / DOMINANT LINE PREFIXES (count, prefix):")
for prefix, n in hist.most_common(20):
    print("  %7d  %r" % (n, prefix))

# ── the probe's own report lines ──
WANT = (
    "frames stepped",
    "frames with a ball carrier",
    "forced sub_state writes",
    "SCORE_MISS",
    "SCORE_ARM_GRAB",
    "SCORE_GLANCING",
    "FORMED",
    "variant=",
    "STUCK frames",
    "FROZEN frames",
    "FROZEN and stationary",
    "attack gate was CLOSED",
    "engine.ball_carrier is not",
    "consecutive stuck runs",
    "RUN ",
    "VERDICT",
    "-> ",
    "A/B",
    "GATE ATTRIBUTION",
    "ARM_GRAB DECAY",
    "MATCHES the damping law",
    "decayed but NOT",
    "not damped this frame",
    "identity engine==world",
    "TEARDOWNS",
    "stuck predicate",
    "attack gate     :",
    "seed            :",
    "[1]",
    "[2]",
    "[3]",
    "[4]",
    "Evidence collected",
    "gate attribution",
)

print("")
print("=" * 96)
print("PROBE REPORT EXTRACT")
print("=" * 96)
shown = 0
for ln in lines:
    if any(w in ln for w in WANT):
        print(ln)
        shown += 1
        if shown > 900:
            print("... (extract capped at 900 lines)")
            break

print("")
print("=" * 96)
print("LAST 120 LINES OF THE RAW LOG")
print("=" * 96)
for ln in lines[-120:]:
    print(ln)

sys.exit(0)
