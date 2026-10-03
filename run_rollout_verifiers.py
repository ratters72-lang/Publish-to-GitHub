"""run_rollout_verifiers.py — CI harness for the rollout verifier suite.

Runs each rollout guard script as a subprocess and hard-fails (non-zero exit)
if ANY of them fails.  This is the "keep the baseline green" CI entry point
for the 2026-09-07 rollout:

  - verify_tes_diagnostics.py        (TES Y-slot lock + diagnostics shape)
  - verify_stage6_tackle_radius_gating.py (Stage 6 radial gate)
  - verify_open_play_tackle_radius_chase.py (OPEN_PLAY CHASE switch)
  - verify_rollout_slot_arbitration.py (soft-lock / override / tackle-gating / ownership)
  - self-test of game/debug/tes_diagnostics.py (sys.path fix baseline)

Diagnostics smoke is included: RLM_TES_DIAGNOSTICS=1 is armed for
verify_tes_diagnostics.py (which asserts CSV parseability + expected columns).

Run:  python run_rollout_verifiers.py
Exit 0 when every script exits 0; prints a per-script PASS/FAIL summary.
"""
import os
import subprocess
import sys

VERIFIERS = [
    "verify_tes_diagnostics.py",
    "verify_stage6_tackle_radius_gating.py",
    "verify_open_play_tackle_radius_chase.py",
    "verify_rollout_slot_arbitration.py",
]


def run_one(script: str, env=None) -> int:
    """Run *script* in a subprocess; return its exit code."""
    print(f"\n{'=' * 60}\nRUN: {script}\n{'=' * 60}")
    env = dict(os.environ, **(env or {}))
    # Prepend the project root so direct `python <script>` resolves imports.
    root = os.path.abspath(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, script],
        cwd=root,
        env=env,
    )
    return proc.returncode


def main() -> int:
    failures = []
    for script in VERIFIERS:
        code = run_one(script)
        status = "PASS" if code == 0 else "FAIL"
        if code != 0:
            failures.append(script)
        print(f"\n  [{status}] {script} (exit {code})")

    # ── Diagnostics self-test smoke (sys.path fix baseline) ──
    print(f"\n{'=' * 60}\nRUN: game/debug/tes_diagnostics.py (self-test)\n{'=' * 60}")
    code = run_one("game/debug/tes_diagnostics.py")
    status = "PASS" if code == 0 else "FAIL"
    if code != 0:
        failures.append("game/debug/tes_diagnostics.py")
    print(f"\n  [{status}] game/debug/tes_diagnostics.py (exit {code})")

    print(f"\n{'=' * 60}\nROLLOUT VERIFIER SUITE RESULT\n{'=' * 60}")
    if failures:
        print(f"FAILURE(S): {failures}")
        return 1
    print("RESULT: ALL VERIFIERS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
