"""tmp_check_gate.py — release-gate tolerances, engine constants, marker geometry."""

import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

out = []
P = r"^%s\s*(?::\s*[^=\n]+)?=\s*([^\n#]+)"

with open("world/open_play_engine.py", encoding="utf-8", errors="replace") as fh:
    en = fh.read()

out.append("=" * 84)
out.append("ENGINE CONSTANTS (world/open_play_engine.py)")
out.append("=" * 84)
for name in ("RECOVERY_BACKWARD_DISTANCE_M", "RELEASE_RECOVERY_SPEED_MPS",
             "RELEASE_WATCHDOG_FRAMES", "RELEASE_MIN_CONVERGENCE_S",
             "RELEASE_MAX_DURATION_S", "DH_BEHIND_DISTANCE_M",
             "DH_POSITION_TOLERANCE_M", "DH_LATERAL_TOLERANCE_M",
             "DH_VELOCITY_STABLE_THRESHOLD_MPS"):
    vals = re.findall(P % name, en, re.M)
    if vals:
        for v in vals:
            out.append("  %-34s = %s" % (name, v.strip()))
    else:
        out.append("  %-34s = (not defined in this file)" % name)

out.append("")
out.append("=" * 84)
out.append("release_gate.py  (whole file)")
out.append("=" * 84)
with open("world/rules/release_gate.py", encoding="utf-8", errors="replace") as fh:
    for ln in fh.read().splitlines():
        out.append("  " + ln)

out.append("")
out.append("=" * 84)
out.append("marker_authority.marker_geometry_target")
out.append("=" * 84)
try:
    with open("game/defence/defence_lvl_1/marker_authority.py",
              encoding="utf-8", errors="replace") as fh:
        ma = fh.read()
    i = ma.find("def marker_geometry_target")
    if i == -1:
        out.append("  NOT FOUND")
    else:
        rest = ma[i:]
        j = rest.find("\ndef ", 1)
        out.append(rest[:j if j != -1 else 2500])
except FileNotFoundError:
    out.append("  FILE NOT FOUND")

with open("tmp_gate.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
print("WROTE tmp_gate.txt (%d lines)" % len(out))
