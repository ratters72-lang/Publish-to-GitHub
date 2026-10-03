"""Run the FB battery and print a compact per-verifier verdict."""
import json
import re
import subprocess
import sys

proc = subprocess.run(
    [sys.executable, "run_fb_verifiers.py"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
)
match = re.search(r"FULLBACK_TELEMETRY_JSON: (.*)", proc.stdout)
if not match:
    print("NO JSON LINE; exit=%s" % proc.returncode)
    print(proc.stdout[-3000:])
    sys.exit(2)
data = json.loads(match.group(1))
ok = [k for k, v in data.items() if v.get("result") == "PASS"]
bad = {k: v for k, v in data.items() if v.get("result") != "PASS"}
print("BATTERY_EXIT=%s TOTAL=%d PASS=%d FAIL=%d"
      % (proc.returncode, len(data), len(ok), len(bad)))
for name in sorted(bad):
    print("  FAIL %s -> %s" % (name, bad[name]))
for name in sorted(data):
    print("  %-58s %s" % (name, data[name].get("result")))
