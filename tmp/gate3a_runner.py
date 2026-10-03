"""Stage 3A final gate: run the battery, capture exit codes + key output."""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"

import subprocess
import sys

PY = sys.executable
VERIFIERS = [
    "verify_role_width_order.py",
    "verify_ruck_adjacent_spacing.py",
    "verify_banded_roles.py",
    "verify_depth_roles.py",
    "verify_spacing_chain.py",
    "verify_width_chain.py",
    "verify_one_a_per_side.py",
    "verify_defensive_pipeline.py",
    "verify_movement_chain.py",
    "verify_tackle_state_gate.py",
    "verify_anchor_integrity.py",
    "verify_fullback_role.py",
    "verify_fullback_geometry.py",
    "verify_tackle_commit_integration.py",
    "verify_preamble_reset_audit.py",
    "verify_defensive_spacing.py",
    "verify_structured_close_width.py",
    "verify_depth_stability.py",
    "verify_split_spacing.py",
    "verify_governor_width.py",
    "verify_width_sources.py",
    "verify_catch_line_speed.py",
    "verify_mmdl_fullback_exclusion.py",
    "verify_depth_role_stability.py",
    "verify_lane_labels.py",
    "verify_intercept_catch_transition.py",
    "verify_task6_chase.py",
    "verify_fb_determinism.py",
    "verify_fullback_absorb.py",
    "verify_movement_reactivation.py",
    "verify_anchor_authority.py",
    "verify_retreat_width_consistency.py",
    "verify_retreat_fullback_contracts.py",
    "verify_tes_drive_depth_shadow.py",
    "verify_tes_drive_fix.py",
    "verify_width_lanes.py",
]

out_lines = []
for v in VERIFIERS:
    try:
        proc = subprocess.run(
            [PY, v],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
        )
        code = proc.returncode
        tail = proc.stdout.strip().splitlines()[-1:] if proc.stdout.strip() else [""]
        out_lines.append("===== %s : EXIT %d =====" % (v, code))
        # capture only the RESULT: line (or traceback tail) for compactness
        result_lines = [ln for ln in proc.stdout.splitlines()
                        if "RESULT" in ln or "FAILURE" in ln or "Traceback" in ln or "Error" in ln or "Exception" in ln]
        for ln in result_lines[:3]:
            out_lines.append("  " + ln.strip())
        out_lines.append("")
    except subprocess.TimeoutExpired:
        out_lines.append("===== %s : TIMEOUT =====" % v)
        out_lines.append("")
    except Exception as exc:
        out_lines.append("===== %s : EXCEPTION %s =====" % (v, exc))
        out_lines.append("")

with open("tmp/gate3a_final.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(out_lines))

# quick summary
green = sum(1 for ln in out_lines if "EXIT 0" in ln)
print("BATTERY DONE: %d verifiers, %d exits 0" % (len(VERIFIERS), green))
print("Wrote tmp/gate3a_final.txt")
