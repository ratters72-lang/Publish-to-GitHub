"""
run_stage6_regression_sweep.py — run every root-level verifier and report.

Stage 6 changed how formation WIDTH is owned: ``formation_width_authority``
publishes the frame's span once, and the lane ladder, spacing span, X target
recompute, span contract, governor accessor and the role-spacing geometry
helper all consume it.  This sweep re-runs the WHOLE verifier corpus at the
repository root so any verifier that silently depended on a second,
independent width source is caught rather than assumed fixed.

Each verifier is run as a subprocess with the repository root as the working
directory (several use ``sys.path.insert(0, ".")``) and a hard timeout, so a
hung verifier cannot stall the sweep.

Usage:
    python run_stage6_regression_sweep.py            # all verifiers
    python run_stage6_regression_sweep.py width      # only names matching
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

# ``write_through`` + line buffering so the sweep streams to the console AND
# to a redirected log file.  Without write_through, redirecting the sweep to
# a file block-buffers the output and a reader sees only the header until the
# process exits.
try:
    sys.stdout.reconfigure(
        encoding="utf-8", line_buffering=True, write_through=True,
    )
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
TIMEOUT_S = 300

# Verifiers that need a live engine / long rollout and are not part of the
# unit-level width sweep.  Kept explicit so the exclusion is visible rather
# than an accident of filename matching.
EXCLUDE_PREFIXES = (
    "run_rollout_verifiers",
    "run_fb_verifiers",
)


def discover(needle: str | None) -> list[str]:
    names = []
    for fn in sorted(os.listdir(ROOT)):
        if not fn.endswith(".py"):
            continue
        if not (fn.startswith("verify_") or fn.startswith("run_")):
            continue
        if fn == os.path.basename(__file__):
            continue
        if fn.startswith(EXCLUDE_PREFIXES):
            continue
        if needle and needle.lower() not in fn.lower():
            continue
        names.append(fn)
    return names


def run_one(fn: str) -> tuple[int, float, str]:
    """Run *fn*; return (exit_code, seconds, tail_of_output)."""
    start = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, fn],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            errors="replace",
        )
        elapsed = time.time() - start
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, elapsed, out
    except subprocess.TimeoutExpired:
        return 124, time.time() - start, "TIMEOUT after %ds" % TIMEOUT_S


def tail(text: str, lines: int = 6) -> str:
    rows = [r for r in text.strip().splitlines() if r.strip()]
    return "\n".join(rows[-lines:])


def main() -> int:
    needle = sys.argv[1] if len(sys.argv) > 1 else None
    names = discover(needle)
    if not names:
        print("no verifiers matched")
        return 1

    print("=" * 78)
    print("STAGE 6 REGRESSION SWEEP — %d verifier(s)" % len(names))
    print("=" * 78)

    passed: list[str] = []
    failed: list[tuple[str, int, str]] = []

    for i, fn in enumerate(names, 1):
        code, secs, out = run_one(fn)
        mark = "PASS" if code == 0 else "FAIL"
        print("\n[%2d/%2d] [%s] %-58s %5.1fs"
              % (i, len(names), mark, fn, secs))
        if code != 0:
            print("        exit=%d" % code)
            for row in tail(out).splitlines():
                print("        | %s" % row)
            failed.append((fn, code, tail(out)))
        else:
            passed.append(fn)
        # Flush per verifier so a redirected log shows live progress and a
        # truncated run still records every verifier completed so far.
        sys.stdout.flush()

    print("\n" + "=" * 78)
    print("SWEEP RESULT: %d passed, %d failed, %d total"
          % (len(passed), len(failed), len(names)))
    print("=" * 78)
    if failed:
        print("\nFAILING VERIFIERS:")
        for fn, code, _ in failed:
            print("  - %-58s exit=%d" % (fn, code))
        return 1
    print("ALL VERIFIERS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
