ALSO CONSIDER: """Run verify_kickoff_fullback.py 12x under the current env; report per-attempt."""
import os
import subprocess
import sys

tag = os.environ.get("RLM_FB_BREAK_LATCH", "<unset>")
results = []
for attempt in range(1, 13):
    proc = subprocess.run(
        [sys.executable, "verify_kickoff_fullback.py"],
        capture_output=True, text=True,
    )
    ok = proc.returncode == 0
    results.append(ok)
    if not ok:
        for line in (proc.stdout or "").splitlines():
            if line.strip().startswith("- ASSERT"):
                print(f"  attempt {attempt}: {line.strip()}")
                break

print(f"RLM_FB_BREAK_LATCH={tag}: "
      f"{sum(results)}/{len(results)} pass, "
      f"{len(results) - sum(results)} fail")
