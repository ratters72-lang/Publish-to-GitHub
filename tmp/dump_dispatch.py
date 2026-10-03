"""Print the engine's per-frame defensive dispatch sites with context."""
import io
import sys

PATH = "world/open_play_engine.py"
KEYS = (
    "run_frame_preamble",
    "update_tes_drive",
    "update_open_play",
    "defensive_pipeline",
    "run_defensive_pipeline",
)

with io.open(PATH, encoding="utf-8") as f:
    lines = f.readlines()

out = []
for i, ln in enumerate(lines):
    if any(k in ln for k in KEYS):
        lo = max(0, i - 18)
        hi = min(len(lines), i + 10)
        out.append("\n=== lines %d-%d ===" % (lo + 1, hi))
        for j in range(lo, hi):
            out.append("%d: %s" % (j + 1, lines[j].rstrip()))

sys.stdout.reconfigure(encoding="utf-8")
print("\n".join(out))
