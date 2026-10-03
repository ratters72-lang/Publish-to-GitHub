"""tmp/run_v.py -- run the fullback contract verifiers, capture a summary."""

import os
import subprocess
import sys

VERIFIERS = (
    "verify_fullback_post_tackle_anchor.py",
    "verify_fullback_recover_live.py",
    "verify_fullback_locomotion_ownership_contract.py",
    "verify_fullback_state_ownership_contract.py",
    "verify_fullback_recovery_ownership_contract.py",
    "verify_fullback_shared_locomotion.py",
    "verify_fullback_chase_hold_contract.py",
    "verify_fullback_emergency_defender.py",
    "verify_fullback_break_latch_contract.py",
    "verify_fullback_absorb.py",
    "verify_fullback_role.py",
    "verify_fullback_geometry.py",
    "verify_fullback_continuity.py",
)

env = dict(os.environ)
env["SDL_VIDEODRIVER"] = "dummy"

lines = []
for name in VERIFIERS:
    if not os.path.exists(name):
        lines.append("%-52s MISSING" % name)
        continue
    try:
        proc = subprocess.run(
            [sys.executable, name],
            capture_output=True, text=True, timeout=600, env=env,
        )
    except subprocess.TimeoutExpired:
        lines.append("%-52s TIMEOUT" % name)
        continue
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
    verdict = tail[-1] if tail else "(no output)"
    lines.append("%-52s rc=%d | %s" % (name, proc.returncode, verdict))

text = "\n".join(lines) + "\n"
with open("tmp/fbsum.txt", "w", encoding="utf-8") as fh:
    fh.write(text)
print(text)
