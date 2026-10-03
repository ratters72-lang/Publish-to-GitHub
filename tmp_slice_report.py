"""tmp_slice_report.py — pull the FULL LEDGER blocks out of the probe report.

The full ledger is what names the authority holding locomotion: it carries
sub_state, the attack gate verdict, the contact verdict, the movement state
and every carrier flag at the instant the carrier is unfrozen, holds a live
target, and is not moving.
"""

import sys

PATH = "tmp_stuck_carrier_report.txt"

with open(PATH, encoding="utf-8", errors="replace") as fh:
    lines = fh.read().splitlines()

print("REPORT LINES: %d" % len(lines))

# ── 1. Every FULL LEDGER block ──
print("")
print("=" * 100)
print("ALL FULL LEDGER BLOCKS (block = header + indented body)")
print("=" * 100)
i = 0
blocks = 0
while i < len(lines):
    if "FULL LEDGER (frame" in lines[i]:
        blocks += 1
        print("")
        print(">>> BLOCK %d" % blocks)
        print(lines[i])
        j = i + 1
        while j < len(lines) and lines[j].startswith("        "):
            print(lines[j])
            j += 1
        i = j
        if blocks >= 40:
            print("... (capped at 40 blocks)")
            break
    else:
        i += 1

# ── 2. The worst-stall window in full ──
print("")
print("=" * 100)
print("THE WORST-STALL WINDOW (raw lines)")
print("=" * 100)
start = None
for idx, ln in enumerate(lines):
    if "THE WORST STALL" in ln:
        start = idx
        break
if start is None:
    print("  (no worst-stall window found)")
else:
    for ln in lines[start:start + 260]:
        print(ln)

sys.exit(0)
