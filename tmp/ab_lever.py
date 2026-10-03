"""A/B the non-green verifiers against the fullback-tackle-candidacy lever.

WHY: `RLM_FULLBACK_TACKLE_CANDIDACY=0` restores the HISTORICAL behaviour
(the dynamic fullback never enters tackle selection) with no code revert.
Running each suspect verifier ONCE with the lever on (default) and ONCE with
it off therefore isolates the contribution of the B2b controlled path:

  lever ON  = FAIL, lever OFF = PASS  -> the failure is caused by B2b and
                                        the verifier's contract must be
                                        scoped (or a real bug fixed)
  lever ON  = FAIL, lever OFF = FAIL  -> PRE-EXISTING failure, unrelated to
                                        the fullback path

Usage:  python -u tmp/ab_lever.py
"""
import os
import subprocess
import sys

VERIFIERS = [
    "verify_fb_determinism.py",
    "verify_mmdl_fullback_exclusion.py",
    "verify_retreat_fullback_contracts.py",
    "verify_line_participation_completeness.py",
    "verify_depth_roles.py",
    "verify_intercept_lane.py",
    "verify_intercept_catch_transition.py",
    "verify_one_a_per_side.py",
    "verify_stage5_population_authority.py",
]

FAIL_TOKENS = ("RESULT: FAIL", "FAIL:", "[FAIL]")
PASS_TOKENS = ("ALL CHECKS PASSED", "RESULT: PASS", "ALL CONTRACTS HOLD",
               "VERIFY: OK", "EXCLUSION VERIFY: OK")


def verdict(text):
    upper = text.upper()
    # "RESULT: N passed, 0 failed" style summaries are PASS.
    for line in text.splitlines():
        up = line.upper()
        if "RESULT:" in up and "FAIL" in up:
            return "FAIL"
        if any(tok in line for tok in FAIL_TOKENS):
            return "FAIL"
    for tok in PASS_TOKENS:
        if tok in upper:
            return "PASS"
    # Fallback: any non-zero "N FAILURE(S)" text.
    if "FAILURE" in upper:
        return "FAIL"
    return "UNKNOWN"


def run(name, lever_on):
    env = dict(os.environ)
    env["RLM_FULLBACK_TACKLE_CANDIDACY"] = "1" if lever_on else "0"
    if not os.path.exists(name):
        return "MISSING", ""
    proc = subprocess.run(
        [sys.executable, name],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=False, env=env,
    )
    text = proc.stdout.decode("utf-8", errors="replace")
    detail = ""
    for line in text.splitlines():
        if any(tok in line for tok in FAIL_TOKENS) or "FAIL:" in line:
            detail = line.strip()[:150]
            break
    return verdict(text), detail


def main():
    print(f"{'verifier':<46} {'lever=1':<9} {'lever=0':<9} attribution")
    print("-" * 100)
    caused_by_b2b = []
    preexisting = []
    for name in VERIFIERS:
        v_on, d_on = run(name, True)
        v_off, d_off = run(name, False)
        if v_on == "PASS" and v_off == "PASS":
            attr = "green"
        elif v_on == "FAIL" and v_off == "PASS":
            attr = "CAUSED BY B2b"
            caused_by_b2b.append((name, d_on))
        elif v_on == "FAIL" and v_off == "FAIL":
            attr = "PRE-EXISTING"
            preexisting.append((name, d_on))
        else:
            attr = f"? on={v_on} off={v_off}"
        print(f"{name:<46} {v_on:<9} {v_off:<9} {attr}")

    print("-" * 100)
    if caused_by_b2b:
        print("CAUSED BY B2b (scope the verifier contract or fix the bug):")
        for name, detail in caused_by_b2b:
            print(f"  - {name}: {detail}")
    if preexisting:
        print("PRE-EXISTING (unrelated to the fullback path):")
        for name, detail in preexisting:
            print(f"  - {name}: {detail}")
    if not caused_by_b2b and not preexisting:
        print("ALL GREEN BOTH WAYS")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
