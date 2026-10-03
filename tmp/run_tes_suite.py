"""Temp: run the TES verifier set and report pass/fail per file."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VERIFIERS = [
    "verify_tes_slot_recovery_ownership.py",
    "verify_tes_drive_structure_hold.py",
    "verify_tes_lateral_authority_phase_gate.py",
    "verify_tes_line_width_hold.py",
    "verify_tes_lane_true_retreat.py",
    "verify_tes_entry_lane_geometry.py",
    "verify_tes_entry_side_authority.py",
    "verify_tes_unified_drive_pipeline.py",
    "verify_tes_drive_xy_writers.py",
    "verify_tes_diagnostics.py",
    "verify_tes_seal.py",
]

env = dict(os.environ)
env["PYTHONIOENCODING"] = "utf-8"

results = []
for name in VERIFIERS:
    path = os.path.join(ROOT, name)
    if not os.path.exists(path):
        results.append((name, "MISSING", ""))
        continue
    proc = subprocess.run(
        [sys.executable, name],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=900,
    )
    tail = [ln for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
    last = tail[-1] if tail else ""
    verdict = "PASS" if proc.returncode == 0 else f"FAIL({proc.returncode})"
    results.append((name, verdict, last))

print("=" * 78)
print("TES VERIFIER SUITE")
print("=" * 78)
for name, verdict, last in results:
    print(f"{verdict:>11}  {name}")
    if last:
        print(f"             {last[:150]}")

fails = [r for r in results if r[1] != "PASS"]
print("-" * 78)
print(f"total={len(results)} pass={len(results) - len(fails)} fail={len(fails)}")
sys.exit(1 if fails else 0)
