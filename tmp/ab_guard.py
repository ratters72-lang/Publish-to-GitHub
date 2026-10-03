"""A/B the emergency-fullback engage admission: tackles with the lever off vs on."""
import os
import subprocess
import sys

FRAMES = "12000"
KEYS = ("fb_tackle_events", "in_tackler_ids", "chase ")
OUT = []


def arm(tag, lever_on):
    env = dict(os.environ)
    env["RLM_FB_EMERGENCY_ENGAGE"] = "1" if lever_on else "0"
    env["RLM_DIAG_FRAMES"] = FRAMES
    subprocess.run(
        [sys.executable, "tmp/fb_chase_diag.py"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with open("tmp/fb_chase_diag.txt", encoding="utf-8") as handle:
        text = handle.read()
    OUT.append("== %s ==" % tag)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(KEYS):
            OUT.append("  " + stripped)


arm("LEVER OFF (baseline)", False)
arm("LEVER ON  (fix)", True)

report = "\n".join(OUT) + "\n"
print(report)
with open("tmp/ab_guard_out.txt", "w", encoding="utf-8") as handle:
    handle.write(report)
