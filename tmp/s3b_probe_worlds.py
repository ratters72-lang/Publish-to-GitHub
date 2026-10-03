"""s3b_probe_worlds.py - locate WorldState fixtures in failing verifiers."""
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

FILES = [
    "verify_one_a_per_side.py",
    "verify_tackle_state_gate.py",
    "verify_defensive_pipeline.py",
    "verify_preamble_reset_audit.py",
    "verify_tes_drive_depth_shadow.py",
    "verify_width_lanes.py",
    "verify_fb_determinism.py",
]

for v in FILES:
    print("=" * 60)
    print(v)
    print("=" * 60)
    try:
        lines = open(v, encoding="utf-8", errors="replace").read().splitlines()
    except OSError as e:
        print("  READ ERROR:", e)
        continue
    for i, ln in enumerate(lines, 1):
        if "WorldState" in ln and "=" not in ln.split("#")[0][:2]:
            pass  # import lines also match; show them all for context
        if "WorldState" in ln:
            print(f"{i:5d}: {ln}")
