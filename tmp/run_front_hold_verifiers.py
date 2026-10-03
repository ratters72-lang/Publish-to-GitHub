"""Run the defence verifiers relevant to the FRONT-HOLD / HEAD-ON change.

Writes a compact report (PASS/FAIL per verifier + only the failing lines of
each failure) to tmp/fh_report.txt as UTF-8 so the result survives the
terminal buffer.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(ROOT, "tmp", "fh_report.txt")

VERIFIERS = [
    "verify_tackle_commit.py",
    "verify_tackle_commit_integration.py",
    "verify_convergence_single_source.py",
    "verify_pretackle_corridor.py",
    "verify_pretackle_attempt.py",
    "verify_pretackle_machine.py",
    "verify_pretackle_classifier.py",
    "verify_flatfoot_step_in.py",
    "verify_stage5_phase_gated_tuning.py",
    "verify_stage6_tackle_radius_gating.py",
    "verify_stage7_role_gated_authority.py",
    "verify_tackle_engage_guard.py",
    "verify_one_a_per_side.py",
    "verify_open_play_stall_authority.py",
    "verify_rollout_slot_arbitration.py",
    "verify_pretackle_no_clean_engage_without_tes.py",
    "verify_anticipation_connected.py",
    "verify_arrival_preserves_state.py",
]


def _interesting(out: str):
    keep = []
    for raw in out.splitlines():
        line = raw.rstrip()
        low = line.lower()
        if "fail" in low or "result" in low or "error" in low \
                or "traceback" in low or "assert" in low:
            keep.append(line)
    return keep


def main() -> int:
    lines = []
    passed = 0
    checked = 0
    for name in VERIFIERS:
        path = os.path.join(ROOT, name)
        if not os.path.isfile(path):
            lines.append("SKIP  %s (missing)" % name)
            continue
        checked += 1
        proc = subprocess.run(
            [sys.executable, path],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=900,
        )
        ok = proc.returncode == 0
        if ok:
            passed += 1
        lines.append("%s  %s (rc=%d)"
                     % ("PASS" if ok else "FAIL", name, proc.returncode))
        if not ok:
            out = (proc.stdout or "") + (proc.stderr or "")
            for det in _interesting(out)[-20:]:
                lines.append("      " + det)
    lines.append("")
    lines.append("%d/%d verifiers passed" % (passed, checked))
    text = "\n".join(lines) + "\n"
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("wrote %s" % REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
