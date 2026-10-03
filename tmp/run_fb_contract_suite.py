"""Compact FB/defence contract suite runner.

The full-output verifier runs are long enough that a terminal dump truncates
before the verdict line, which makes "did this contract pass?" needlessly hard
to answer in bulk.  This runner executes each verifier as a subprocess,
captures its output, and prints exactly one summary line per verifier:

    PASS  verify_fullback_geometry.py            RESULT: ALL CHECKS PASSED
    FAIL  verify_x.py                            3 check(s)
          - the specific failing assertion

so a whole sweep fits in one readable screen and any failure names itself.

Usage:
    python tmp/run_fb_contract_suite.py [verifier.py ...]

With no arguments it runs the declared DEFAULT_SUITE.
"""
from __future__ import annotations

import os
import subprocess
import sys

# The summary is ALSO written here, because a long sweep's stdout does not
# reliably survive a terminal dump -- and a contract verdict that cannot be
# read is not a verdict.
SUMMARY_PATH = "tmp/fb_contract_suite.txt"

DEFAULT_SUITE = [
    # fullback role / geometry
    "verify_fullback_continuity.py",
    "verify_fullback_geometry.py",
    "verify_fullback_role.py",
    "verify_fullback_priority.py",
    "verify_kickoff_fullback.py",
    "verify_fullback_absorb.py",
    "verify_fullback_invariant_audit.py",
    "verify_post_catch_fb_absorb_grace.py",
    "verify_fullback_emergency_defender.py",
    "verify_fullback_recover_contract.py",
    "verify_fullback_recover_live.py",
    "verify_fullback_lateral_priority_exclusion.py",
    "verify_fullback_lateral_open_play_contract.py",
    "verify_fullback_line_break_chase.py",
    # post-miss / pursuit / tackle lifecycle
    "verify_post_miss_pursuit_contract.py",
    "verify_freeze_expiry_pursuit_contract.py",
    "verify_bump_clearance_contract.py",
    "verify_tackle_exchange_multi_body_contract.py",
    "verify_contest_record_schema_contract.py",
]

# A verifier that prints one of these has FAILED even if it exits 0, and a
# verifier that exits non-zero has FAILED even if it prints nothing.  The two
# signals are combined, and a green verdict requires BOTH to be clean -- so a
# partial failure can never report green (the rule run_fb_verifiers.py uses).
FAIL_PREFIXES = ("FAIL", "FAILED", "ERROR", "TRACEBACK")


def _fail_marker(text: str) -> str | None:
    """Return the first line that marks a failure, or None.

    Deliberately does NOT treat a bare "- " bullet as a failure: several
    verifiers bullet their PASS reasons ("- single fullback on every frame"),
    so that rule flags a green contract.  The exit code is the primary signal;
    this check is the corroborating one.
    """
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper.startswith(FAIL_PREFIXES):
            return stripped
        if "RESULT: FAIL" in upper or "CHECKS FAILED" in upper:
            return stripped
    return None


def _summarise(returncode: int, text: str) -> tuple[bool, str, list[str], list[str]]:
    """Return (passed, headline, failing_lines, tail)."""
    verdict = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("RESULT:"):
            verdict = stripped
    marker = _fail_marker(text)
    # Primary signal is the exit code; the text is the corroborating signal.
    # Some verifiers simply do not emit a "RESULT:" line, and treating that as
    # unknown would flag a perfectly green contract as a failure.
    passed = returncode == 0 and marker is None
    if not verdict:
        verdict = ("RESULT: PASS (exit 0; verifier emits no RESULT line)"
                   if passed else f"exit {returncode}; no RESULT line")
    failures = [marker] if marker else []
    tail = [ln.rstrip() for ln in text.splitlines() if ln.strip()][-14:]
    return passed, verdict, failures, tail


def main(argv: list[str]) -> int:
    names = argv[1:] or DEFAULT_SUITE
    failed: list[str] = []
    missing: list[str] = []
    report: list[str] = []

    def emit(line: str) -> None:
        report.append(line)
        print(line)

    for name in names:
        if not os.path.exists(name):
            missing.append(name)
            emit(f"SKIP  {name:<52} not found")
            continue
        proc = subprocess.run(
            [sys.executable, name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        passed, verdict, failures, tail = _summarise(proc.returncode, proc.stdout)
        emit(f"{'PASS' if passed else 'FAIL'}  {name:<52} prc={proc.returncode}")
        emit(f"      {verdict}")
        if not passed:
            failed.append(name)
            for line in failures:
                emit(f"      {line}")
            for line in tail:
                emit(f"      | {line}")

    emit("-" * 78)
    emit(f"suite: {len(names)} verifier(s), {len(failed)} failed, "
         f"{len(missing)} not found")
    if failed:
        emit("failed:")
        for name in failed:
            emit(f"  - {name}")
    # The machine-readable copy: a verdict nobody can read is not a verdict.
    try:
        with open(SUMMARY_PATH, "w", encoding="utf-8") as fh:
            fh.write("\n".join(report) + "\n")
    except OSError:
        pass
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
