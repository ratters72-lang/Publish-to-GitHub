"""Run the width / lane-geometry verifier set. Operational only."""

import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

ROOT = os.path.abspath(".")
TIMEOUT_S = 300

SCRIPTS = (
    "verify_width_tuner_responsiveness.py",
    "verify_width_tuners.py",
    "verify_split_spacing.py",
    "verify_spacing.py",
    "verify_count_authority_lane_spacing.py",
    "verify_ruck_adjacent_spacing.py",
    "verify_depth_roles_side_width_consistency.py",
    "verify_phase_count_geometry_unity.py",
    "verify_governor_width.py",
    "verify_short_side_ptb_spacing.py",
    "verify_line_compression_no_duplication.py",
    "verify_structured_close_width.py",
    "verify_retreat_width_consistency.py",
    "verify_compute_targets_structural_anchor.py",
    "verify_geometry_authority_lock.py",
    "verify_ball_side_collapse.py",
    "verify_collapse_geometry.py",
    "verify_banded_roles.py",
    "verify_tes_line_width_hold.py",
    "verify_tes_lane_true_retreat.py",
    "verify_role_width_order.py",
    "verify_macro_width_governor.py",
    "verify_defensive_geometry.py",
    "verify_structured_close.py",
    "verify_line_participation_completeness.py",
    "verify_no_tackle_no_defence.py",
    "verify_pretackle_machine.py",
    "verify_seed_hold.py",
)


def main():
    print("=" * 78)
    print("WIDTH / LANE VERIFIER SET")
    print("=" * 78)

    passed, failed, missing = [], [], []
    for name in SCRIPTS:
        if not os.path.exists(os.path.join(ROOT, name)):
            missing.append(name)
            print(f"  [SKIP] {name} (not present)")
            continue
        try:
            proc = subprocess.run(
                [sys.executable, name],
                cwd=ROOT, capture_output=True, text=True,
                timeout=TIMEOUT_S, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            failed.append((name, f"TIMEOUT after {TIMEOUT_S}s"))
            print(f"  [TIMEOUT] {name}")
            continue
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0:
            passed.append(name)
            print(f"  [PASS] {name}")
        else:
            tail = "\n".join(out.strip().splitlines()[-16:])
            failed.append((name, tail))
            print(f"  [FAIL] {name}")

    print("\n" + "=" * 78)
    print(f"TOTALS: {len(passed)} pass / {len(failed)} fail / "
          f"{len(missing)} absent")
    print("=" * 78)

    if failed:
        print("\n---- FAILURES ----")
        for name, tail in failed:
            print(f"\n### {name}\n{tail}")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
