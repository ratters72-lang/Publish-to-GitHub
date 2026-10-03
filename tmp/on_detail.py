"""tmp/on_detail.py — run the 4 perturbed verifiers with the lever ON (default)
and print every FAIL line plus the final RESULT line."""

import os
import subprocess
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

VERIFIERS = [
    "verify_contact_authority_contract.py",
    "verify_contest_preservation_contract.py",
    "verify_contest_record_schema_contract.py",
    "verify_contest_transport_contract.py",
]

env = dict(os.environ)
env.pop("RLM_CONTACT_SPACE_MISS", None)
env.setdefault("SDL_VIDEODRIVER", "dummy")
env.setdefault("SDL_AUDIODRIVER", "dummy")

for name in VERIFIERS:
    proc = subprocess.run([sys.executable, name], capture_output=True,
                          text=True, env=env, timeout=1800)
    out = [ln.rstrip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    print("")
    print("### %s  rc=%s  (lever ON)" % (name, proc.returncode))
    for ln in out:
        if "FAIL" in ln:
            print("    %s" % ln[:200])
    for ln in out[-2:]:
        print("    tail: %s" % ln[:200])
