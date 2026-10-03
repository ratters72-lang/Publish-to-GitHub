"""Run the verifier set that covers the drive un-gating task and print a
compact PASS/FAIL summary."""
import subprocess
import sys

VERIFIERS = [
    "verify_def_line_ready_frozen_drive.py",
    "verify_frozen_velocity_pin.py",
    "verify_release_clears_tackler_commit.py",
    "verify_tackle_drive_out_of_band.py",
    "verify_defensive_state_reset.py",
    "verify_stage6_tackle_radius_gating.py",
    "verify_no_tackle_no_defence.py",
    "verify_tes_seal.py",
    "verify_preamble_reset_audit.py",
]

sys.stdout.reconfigure(encoding="utf-8")

failures = 0
for name in VERIFIERS:
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", name],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    tail = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT:")]
    summary = tail[-1] if tail else "(no RESULT line)"
    status = "PASS" if proc.returncode == 0 else "FAIL"
    if proc.returncode != 0:
        failures += 1
    print(f"{status}  {name}  ->  {summary}")

print(f"\nTOTAL: {len(VERIFIERS) - failures}/{len(VERIFIERS)} passed")
sys.exit(1 if failures else 0)
