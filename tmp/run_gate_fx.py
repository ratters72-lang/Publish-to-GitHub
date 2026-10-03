import subprocess, sys, os

verifiers = [
    "verify_tackle_commit.py",
    "verify_tackle_commit_integration.py",
    "verify_convergence_single_source.py",
    "verify_tes_drive_depth_shadow.py",
    "verify_ruck_adjacent_spacing.py",
    "verify_defensive_pipeline.py",
    "verify_stage3e_transition_ownership.py",
    "verify_stage3f_bc_timing_determinism.py",
    "verify_movement_chain.py",
    "verify_one_a_per_side.py",
]

results = []
for v in verifiers:
    if not os.path.exists(v):
        results.append((v, "MISSING"))
        continue
    r = subprocess.run([sys.executable, v], capture_output=True, text=True)
    code = r.returncode
    tail = (r.stdout or r.stderr or "").strip()[-200:]
    results.append((v, code, tail))
    print(f"=== {v} -> exit {code} ===")
    if tail:
        print(tail)
    print()

print("\n===== SUMMARY =====")
for row in results:
    if len(row) == 2:
        print(f"{row[0]}: {row[1]}")
    else:
        print(f"{row[0]}: exit {row[1]}")
