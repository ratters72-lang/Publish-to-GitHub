"""tmp/ab_csm_off.py — run the 4 perturbed verifiers with the lever OFF.

If they pass with RLM_CONTACT_SPACE_MISS=0, the CONTACT_SPACE_MISS change is
what moved them.  If they fail with the lever OFF too, the breakage predates it.
"""

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
env["RLM_CONTACT_SPACE_MISS"] = "0"
env.setdefault("SDL_VIDEODRIVER", "dummy")
env.setdefault("SDL_AUDIODRIVER", "dummy")

for name in VERIFIERS:
    proc = subprocess.run([sys.executable, name], capture_output=True,
                          text=True, env=env, timeout=1800)
    out = [ln.rstrip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    print("")
    print("### %s  rc=%s  (lever OFF)" % (name, proc.returncode))
    bad = [ln for ln in out if "FAIL" in ln or "Traceback" in ln or "Error" in ln]
    for ln in bad[:8]:
        print("    %s" % ln[:170])
    for ln in out[-3:]:
        print("    tail: %s" % ln[:170])
