"""Run tmp/fb_chase_diag.py and print only the headline counters."""
import subprocess
import sys

KEYS = (
    "fb_tackle_events",
    "in_tackler_ids",
    "chase_gate",
    "role_counter",
    "state_counter",
    "min_bc_dist",
    "break_gate",
    "post_tackle",
)

subprocess.run(
    [sys.executable, "tmp/fb_chase_diag.py"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    check=False,
)

with open("tmp/fb_chase_diag.txt", encoding="utf-8") as handle:
    text = handle.read()

for line in text.splitlines():
    stripped = line.strip()
    if any(stripped.startswith(key) for key in KEYS):
        print(stripped)
