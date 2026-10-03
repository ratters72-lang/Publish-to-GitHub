import subprocess
import sys

NAMES = (
    "verify_fb_chase_intercept_tracking.py",
    "verify_kickoff_fullback.py",
)

lines = []
for name in NAMES:
    proc = subprocess.run(
        [sys.executable, name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    text = proc.stdout.decode("utf-8", errors="replace")
    lines.append("=" * 70)
    lines.append("%s  rc=%d" % (name, proc.returncode))
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("FAIL") or "RESULT" in stripped:
            lines.append("    " + stripped)

with open("tmp/s4_two.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
print("\n".join(lines))
