"""tmp/run_csm_regressions.py — run the contact/tackle/contest regression battery.

Runs each existing verifier as a subprocess and reports its exit code, so the
CONTACT_SPACE_MISS change is checked against the contracts it could disturb.
"""

import os
import subprocess
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

VERIFIERS = [
    "verify_contact_space_miss_contract.py",
    "verify_contact_authority_contract.py",
    "verify_contest_preservation_contract.py",
    "verify_contest_record_schema_contract.py",
    "verify_contest_transport_contract.py",
    "verify_tackle_formation_contract.py",
    "verify_tackle_exchange_multi_body_contract.py",
    "verify_post_tackle_return_lifecycle_contract.py",
    "verify_freeze_clock_contract.py",
    "verify_freeze_expiry_pursuit_contract.py",
    "verify_post_miss_pursuit_contract.py",
    "verify_bump_clearance_contract.py",
    "verify_open_play_stall_authority.py",
]

env = dict(os.environ)
env.setdefault("SDL_VIDEODRIVER", "dummy")
env.setdefault("SDL_AUDIODRIVER", "dummy")

failures = []
for name in VERIFIERS:
    if not os.path.exists(name):
        print("  [SKIP] %s (not present)" % name)
        continue
    proc = subprocess.run(
        [sys.executable, name],
        capture_output=True, text=True, env=env, timeout=1800,
    )
    tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    last = tail[-1] if tail else ""
    fails = [ln.strip() for ln in tail if "[FAIL]" in ln]
    ok = proc.returncode == 0
    if not ok:
        failures.append(name)
    print("  [%s] %s  rc=%s  %s"
          % ("PASS" if ok else "FAIL", name, proc.returncode, last[:90]))
    for ln in fails[:6]:
        print("         %s" % ln[:150])

print("")
print("=" * 74)
if failures:
    print("BATTERY FAILED: %s" % ", ".join(failures))
else:
    print("BATTERY PASSED: all %d verifiers green" % len(VERIFIERS))
print("=" * 74)
sys.exit(1 if failures else 0)
