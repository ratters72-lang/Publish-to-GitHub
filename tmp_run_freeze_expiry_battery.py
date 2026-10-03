"""Temp regression runner: the freeze-expiry-pursuit change surface.

Writes its own results file (``tmp_freeze_battery.txt``) line-buffered, so
the run can be inspected without depending on terminal capture.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(ROOT, "tmp_freeze_battery_run2.txt")

SCRIPTS = (
    "verify_freeze_expiry_pursuit_contract.py",
    "verify_freeze_clock_contract.py",
    "verify_target_x_glide.py",
    "verify_open_play_stall_authority.py",
    "verify_retreat_obligation_contract.py",
    "verify_bump_clearance_contract.py",
    "verify_mmdl_fullback_exclusion.py",
    "verify_fullback_line_break_chase.py",
    "verify_fullback_geometry.py",
    "verify_fullback_role.py",
    "verify_fullback_emergency_defender.py",
    "verify_fullback_can_tackle.py",
    "verify_fullback_recover_contract.py",
    "verify_fullback_continuity.py",
    "verify_fullback_lateral_open_play_contract.py",
    "verify_pretackle_corridor.py",
    "verify_tackle_commit.py",
    "verify_tes_glide_smoothing_contract.py",
    "verify_post_tes_corridor_contract.py",
    "verify_depth_boundary_invariant.py",
    "verify_retreat_backpedal_contract.py",
    "verify_retreat_fullback_contracts.py",
    "verify_contest_preservation_contract.py",
    "verify_tackle_formation_contract.py",
    "verify_contact_authority_contract.py",
    "verify_release_target_field_clamp.py",
    "verify_tes_depth_authority_contract.py",
    "verify_tes_depth_obligation_contract.py",
    "verify_tes_width_authority_contract.py",
    "verify_tes_width_obligation_contract.py",
    "verify_exit_boundary_ownership_contract.py",
)


def emit(handle, line):
    handle.write(line + "\n")
    handle.flush()
    print(line)


def main() -> int:
    failed = []
    skipped = []
    with open(RESULTS, "w", encoding="utf-8") as out:
        for name in SCRIPTS:
            if not os.path.exists(os.path.join(ROOT, name)):
                skipped.append(name)
                emit(out, "SKIP  %s" % name)
                continue
            try:
                proc = subprocess.run(
                    [sys.executable, name],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=900,
                )
            except subprocess.TimeoutExpired:
                failed.append(name)
                emit(out, "FAIL  %s (timeout)" % name)
                continue
            if proc.returncode == 0:
                emit(out, "PASS  %s" % name)
                continue
            failed.append(name)
            emit(out, "FAIL  %s (exit %d)" % (name, proc.returncode))
            for line in (proc.stdout or "").strip().splitlines()[-12:]:
                emit(out, "        | %s" % line)
            for line in (proc.stderr or "").strip().splitlines()[-6:]:
                emit(out, "        ! %s" % line)
        emit(out, "-" * 70)
        emit(out, "ran %d, failed %d, skipped %d"
             % (len(SCRIPTS) - len(skipped), len(failed), len(skipped)))
        if failed:
            emit(out, "FAILED: %s" % ", ".join(failed))
        else:
            emit(out, "BATTERY GREEN")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
