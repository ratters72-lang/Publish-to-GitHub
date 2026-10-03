"""Run the sibling contest/formation gates and report their verdicts."""
import re
import subprocess
import sys

GATES = [
    "verify_contest_preservation_contract.py",
    "verify_contest_transport_contract.py",
    "verify_tackle_formation_contract.py",
    "verify_contest_record_schema_contract.py",
]

bad = 0
for name in GATES:
    proc = subprocess.run(
        [sys.executable, name],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    verdict = "?"
    for line in out.splitlines():
        if "RESULT:" in line:
            verdict = line.strip().split("RESULT:", 1)[1].strip()
    summary = ""
    for line in out.splitlines():
        if re.search(r"checks=\d+", line):
            summary = line.strip()
    failures = [l.strip() for l in out.splitlines() if "FAIL" in l.upper()]
    print("%-48s exit=%s verdict=%s %s"
          % (name, proc.returncode, verdict, summary))
    for f in failures[-5:]:
        print("      %s" % f[:200])
    if proc.returncode != 0 or verdict.upper().startswith("FAIL"):
        bad += 1
print("SIBLING_FAILURES=%d" % bad)
