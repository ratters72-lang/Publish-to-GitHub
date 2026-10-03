"""Temp A/B: is verify_open_play_stall_authority's failure mine?

Runs the stall verifier with RLM_FREEZE_EXPIRY_PURSUIT ON (default) and OFF.
The OFF leg makes the new expiry hand-back inert, so an identical verdict in
both legs proves the failure is pre-existing and not introduced here.
Writes a compact comparison to tmp_ab_stall.txt.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPT = "verify_open_play_stall_authority.py"
RESULTS = os.path.join(ROOT, "tmp_ab_stall.txt")
KEY = "RLM_FREEZE_EXPIRY_PURSUIT"


def leg(value):
    env = dict(os.environ)
    if value is None:
        env.pop(KEY, None)
    else:
        env[KEY] = value
    proc = subprocess.run(
        [sys.executable, SCRIPT], cwd=ROOT, capture_output=True,
        text=True, timeout=1800, env=env,
    )
    lines = (proc.stdout or "").splitlines()
    keep = [
        ln for ln in lines
        if ("RESULT" in ln
            or "stalled body-frames" in ln
            or "longest stall run" in ln
            or "freeze_expiry_pursuit" in ln
            or "struct_freeze_timer" in ln
            or "freeze_pin" in ln)
    ]
    return proc.returncode, keep


def main() -> int:
    out = []
    for label, value in (("ON (default)", None), ("OFF (=0)", "0")):
        code, keep = leg(value)
        out.append("=" * 70)
        out.append("LEVER %s -> exit %d" % (label, code))
        out.extend(keep)
    text = "\n".join(out)
    with open(RESULTS, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
