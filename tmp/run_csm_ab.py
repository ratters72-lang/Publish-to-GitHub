"""tmp/run_csm_ab.py — triage: run the 4 failing verifiers with the lever ON and OFF.

If a verifier fails in BOTH arms, the failure is not caused by CONTACT_SPACE_MISS.
"""

import os
import subprocess
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

TARGETS = [
    "verify_contest_preservation_contract.py",
    "verify_contest_record_schema_contract.py",
    "verify_contest_transport_contract.py",
    "verify_open_play_stall_authority.py",
]

base = dict(os.environ)
base.setdefault("SDL_VIDEODRIVER", "dummy")
base.setdefault("SDL_AUDIODRIVER", "dummy")


def run(name, lever):
    env = dict(base)
    if lever == "OFF":
        env["RLM_CONTACT_SPACE_MISS"] = "0"
    else:
        env.pop("RLM_CONTACT_SPACE_MISS", None)
    try:
        proc = subprocess.run(
            [sys.executable, name], capture_output=True, text=True,
            env=env, timeout=2400,
        )
    except subprocess.TimeoutExpired:
        return name, lever, "TIMEOUT", []
    lines = [ln.rstrip() for ln in (proc.stdout or "").splitlines()]
    fails = [ln.strip() for ln in lines if "[FAIL]" in ln or "[FAIL" in ln]
    if not fails:
        fails = [ln.strip() for ln in lines
                 if "FAIL" in ln.upper() and "PASS" not in ln.upper()]
    return name, lever, proc.returncode, fails[:8]


for name in TARGETS:
    for lever in ("ON", "OFF"):
        n, lv, rc, fails = run(name, lever)
        print("%-46s lever=%-3s rc=%s  fails=%d"
              % (n, lv, rc, len(fails)))
        for ln in fails:
            print("        %s" % ln[:160])
    print("-" * 78)
