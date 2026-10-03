"""Run the COMPLETE fullback emergency-defender verification battery.

Covers every verifier that asserts the B2b controlled-fullback contract:
tag identity, absorption, continuity, kickoff, the emergency path itself,
the two-way tackle-admission contract, the MMDL DRIFT-or-CHASE contract and
the retreat contracts.

Prints one line per verifier plus a final tally, and exits non-zero if any
feature verifier is not green.

Usage:  python -u tmp/run_feature_battery.py
"""
import os
import subprocess
import sys

BATTERY = [
    # ── core role / identity ──
    "verify_fullback_role.py",
    # ── the emergency path itself ──
    "verify_fullback_emergency_defender.py",
    # ── two-way tackle admission ──
    "verify_fullback_can_tackle.py",
    "verify_fullback_never_tackler_or_frozen.py",
    # ── movement-state + retreat contracts ──
    "verify_mmdl_fullback_exclusion.py",
    "verify_retreat_fullback_contracts.py",
    # ── continuity / phase transitions ──
    "verify_fullback_continuity.py",
    "verify_kickoff_fullback.py",
    # ── absorption (genuine absorption is the ONLY tag clearer) ──
    "verify_fullback_absorb.py",
    "verify_post_catch_fb_absorb_grace.py",
    # ── engine-level FB sweeps, when present ──
    "verify_fullback_engine.py",
    "verify_pretackle_corridor.py",
]

FAIL_TOKENS = ("RESULT: FAIL", "FAIL:", "[FAIL]", "VERDICT: FAIL")
PASS_TOKENS = (
    "ALL CHECKS PASSED", "ALL CONTRACTS HOLD", "VERIFY: OK",
    "EXCLUSION VERIFY: OK", "VERDICT: PASS",
)


def verdict(text):
    for line in text.splitlines():
        if any(tok in line for tok in FAIL_TOKENS):
            return "FAIL", line.strip()[:120]
    upper = text.upper()
    for tok in PASS_TOKENS:
        if tok in upper:
            return "PASS", ""
    if "PASSED" in upper and "FAIL" not in upper:
        return "PASS", ""
    return "UNKNOWN", ""


def main():
    print("=" * 78)
    print("FULLBACK EMERGENCY-DEFENDER — COMPLETE FEATURE BATTERY")
    print("=" * 78)
    results = []
    for name in BATTERY:
        if not os.path.exists(name):
            print(f"{name:<44} SKIP      (not present)")
            continue
        proc = subprocess.run(
            [sys.executable, name],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        text = proc.stdout.decode("utf-8", errors="replace")
        state, detail = verdict(text)
        results.append((name, state))
        print(f"{name:<44} {state:<9} {detail}")

    print("-" * 78)
    feature = [(n, s) for n, s in results]
    bad = [(n, s) for n, s in feature if s != "PASS"]
    print(f"feature verifiers run : {len(feature)}")
    print(f"green                 : {len(feature) - len(bad)}")
    if bad:
        print("NOT GREEN:")
        for n, s in bad:
            print(f"  - {n}: {s}")
        return 1
    print("RESULT: ALL FEATURE VERIFIERS PASSED")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
