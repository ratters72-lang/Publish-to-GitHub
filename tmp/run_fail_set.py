"""Re-run only the scripts that FAILED in the first full sweep, so the delta
after fixes is visible quickly."""
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILED = [
    "verify_attack_geometry_stability.py",
    "verify_banded_roles.py",
    "verify_bc_steering_stability.py",
    "verify_catch_line_speed.py",
    "verify_collapse_geometry.py",
    "verify_convergence_single_source.py",
    "verify_def_line_ready_audit.py",
    "verify_defensive_line.py",
    "verify_defensive_pipeline.py",
    "verify_defensive_spacing.py",
    "verify_depth_roles.py",
    "verify_edge_release_conversion.py",
    "verify_fb_determinism.py",
    "verify_fullback_continuity.py",
    "verify_fullback_engine.py",
    "verify_kickoff_fullback.py",
    "verify_mmdl_fullback_exclusion.py",
    "verify_movement_chain.py",
    "verify_movement_reactivation.py",
    "verify_movement_y_authority.py",
    "verify_movement_y_sources.py",
    "verify_one_a_per_side.py",
    "verify_retreat_fullback_contracts.py",
    "verify_role_width_order.py",
    "verify_ruck_role_system.py",
    "verify_stage3e_transition_ownership.py",
    "verify_stage6_tackle_radius_gating.py",
    "verify_tackle_commit.py",
    "verify_task6_chase.py",
    "verify_tes_diagnostics.py",
    "verify_tes_drive_fix.py",
    "probe_anticipation_teleport.py",
    "probe_tackle_arming.py",
    "probe_writer_bands_jitter.py",
]


def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    still = []
    fixed = []
    for script in FAILED:
        if not os.path.exists(os.path.join(ROOT, script)):
            print(f"[SKIP] {script} (not found)")
            continue
        try:
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", script],
                cwd=ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900,
            )
            code = proc.returncode
            out = (proc.stdout or b"").decode("utf-8", "replace")
            tail = [ln for ln in out.strip().splitlines() if ln.strip()]
            tail = tail[-1] if tail else ""
        except subprocess.TimeoutExpired:
            code, tail = 124, "TIMEOUT"
        status = "PASS" if code == 0 else "FAIL"
        (fixed if code == 0 else still).append(script)
        print(f"[{status}] {script}   {tail[:120]}")
    print("\n" + "=" * 72)
    print(f"NOW PASSING: {len(fixed)}   STILL FAILING: {len(still)}")
    for f in still:
        print("  -", f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
