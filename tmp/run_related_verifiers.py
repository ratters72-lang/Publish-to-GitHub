"""Run the verifiers whose contracts touch the controlled emergency-defender
path (B2b, 2026-09-16), plus the fullback suite, and print one line per file.

WHY: the project has ~136 standalone verifier scripts and each boots the
engine.  Running them all on every iteration is wasteful; this script runs
the set that can actually observe a change to the fullback's tackle
eligibility / line membership, and prints a compact PASS/FAIL verdict with
the first failure line when something breaks.

Usage:  python tmp/run_related_verifiers.py
"""
import os
import subprocess
import sys

VERIFIERS = [
    # --- the controlled path itself ---------------------------------
    "verify_fullback_emergency_defender.py",
    "verify_fullback_role.py",
    "verify_fullback_continuity.py",
    "verify_kickoff_fullback.py",
    "verify_fullback_can_tackle.py",
    "verify_fullback_engine.py",
    "verify_fullback_never_tackler_or_frozen.py",
    "verify_fullback_invariant_audit.py",
    "verify_fb_determinism.py",
    "verify_fb_movement_state_stability.py",
    # --- line membership / exclusion --------------------------------
    "verify_mmdl_fullback_exclusion.py",
    "verify_retreat_fullback_contracts.py",
    "verify_line_participation_completeness.py",
    "verify_depth_roles.py",
    "verify_depth_roles_side_width_consistency.py",
    "verify_one_a_per_side.py",
    "verify_stage5_population_authority.py",
    # --- tackle eligibility / corridor / commit ---------------------
    "verify_intercept_lane.py",
    "verify_intercept_catch_transition.py",
    "verify_pretackle_corridor.py",
    "verify_pretackle_attempt.py",
    "verify_pretackle_classifier.py",
    "verify_pretackle_machine.py",
    "verify_tackle_commit.py",
    "verify_tackle_commit_integration.py",
    "verify_tackle_state_gate.py",
    "verify_tackle_engage_guard.py",
    "verify_stage6_tackle_radius_gating.py",
    "verify_task6_chase.py",
    # --- freeze / movement contracts --------------------------------
    "verify_release_clears_tackler_commit.py",
    "verify_movement_reactivation.py",
    "verify_frozen_velocity_pin.py",
    "verify_def_line_ready_frozen_drive.py",
    "verify_phase_count_geometry_unity.py",
]

FAIL_TOKENS = ("RESULT: FAIL", "FAIL:", "[FAIL]")


def _verdict(text):
    """Return (verdict, first_failure_line) for a verifier's stdout."""
    upper = text.upper()
    for marker in ("ALL CHECKS PASSED", "RESULT: PASS", "ALL CONTRACTS HOLD"):
        if marker in upper:
            return "PASS", None
    for line in text.splitlines():
        if any(tok in line for tok in FAIL_TOKENS):
            return "FAIL", line.strip()[:180]
        if "RESULT:" in line.upper():
            return "FAIL", line.strip()[:180]
    return "UNKNOWN", None


def main():
    failures = []
    missing = []
    for name in VERIFIERS:
        if not os.path.exists(name):
            missing.append(name)
            print(f"  SKIP  {name} (not found)")
            continue
        proc = subprocess.run(
            [sys.executable, name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        text = proc.stdout.decode("utf-8", errors="replace")
        verdict, detail = _verdict(text)
        if verdict != "PASS":
            failures.append((name, verdict, detail))
        print(f"  {verdict:<7} {name}"
              + (f"   <- {detail}" if detail else ""))

    print("-" * 72)
    if missing:
        print(f"MISSING: {len(missing)} -> {missing}")
    if failures:
        print(f"RESULT: {len(failures)} NON-GREEN VERIFIER(S)")
        for name, verdict, detail in failures:
            print(f"  - {name}: {verdict} {detail or ''}")
        return 1
    print(f"RESULT: ALL {len(VERIFIERS) - len(missing)} VERIFIERS GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
