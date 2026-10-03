"""Task XIV — dark-safe regression: gate OFF => legacy verifiers stay green."""
import subprocess
import sys
import os

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or ".")

VERIFIERS = [
    "verify_tes_drive_depth_shadow.py",
    "verify_defensive_pipeline.py",
    "verify_movement_chain.py",
    "verify_tackle_state_gate.py",
    "verify_convergence_single_source.py",
    "verify_stage3f_bc_timing_determinism.py",
]

fail = False
for v in VERIFIERS:
    if not os.path.exists(v):
        print(f"SKIP (missing) {v}")
        continue
    r = subprocess.run([sys.executable, v], capture_output=True, text=True)
    tail = (r.stdout or "").strip().splitlines()
    last = tail[-1] if tail else ""
    ok = r.returncode == 0
    if not ok:
        fail = True
    print(f"{'PASS' if ok else 'FAIL'} exit={r.returncode}  {v}  ::  {last}")
    if r.stderr and r.returncode != 0:
        print("    stderr:", (r.stderr or "").strip().splitlines()[-1])

print("RESULT:", "ALL REGRESSION GREEN" if not fail else "REGRESSION FAILURES")
sys.exit(1 if fail else 0)
