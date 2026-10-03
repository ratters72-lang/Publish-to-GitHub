"""Run compileall + a set of verifier scripts, printing a compact summary."""
import subprocess
import sys

PY = sys.executable

COMPILE = [PY, "-m", "compileall", "-q", "game", "world", "player",
           "kick_off", "ball", "ai_agents"]

CHECKS = [
    "verify_tes_lateral_authority_phase_gate.py",
    "verify_tes_line_width_hold.py",
    "verify_tes_drive_structure_hold.py",
    "verify_tes_unified_drive_pipeline.py",
    "verify_tes_drive_xy_writers.py",
    "verify_def_line_ready_frozen_drive.py",
    "verify_tes_seal.py",
    "verify_anticipation_reconnected.py",
    "verify_marker_drive_lock.py",
    "verify_line_break_static_guard.py",
    "verify_pretackle_machine.py",
]

print("=== compileall ===")
r = subprocess.run(COMPILE, capture_output=True, text=True)
print("exit", r.returncode)
if r.stdout.strip():
    print(r.stdout.strip()[:4000])
if r.stderr.strip():
    print("STDERR:", r.stderr.strip()[:4000])

print("\n=== verifiers ===")
for name in CHECKS:
    try:
        r = subprocess.run([PY, name], capture_output=True, text=True)
        tail = (r.stdout or "").strip().splitlines()[-3:]
        print(f"{name}: exit={r.returncode} | " + " / ".join(tail))
        if r.returncode not in (0, 1):
            print("  stderr:", (r.stderr or "").strip()[:1500])
    except FileNotFoundError:
        print(f"{name}: MISSING")
