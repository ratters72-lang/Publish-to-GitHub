"""tmp_run_release_regressions.py — regression set for the RELEASE geometry fix.

The change under test touches three places:
  * ``game/defence/defence_lvl_1/marker_authority.py`` -- the marker ruck-pin
    target is now clamped into the field;
  * ``world/open_play_engine.py`` -- the BC stand-up target is clamped, and
    ``_reassert_release_stand_up_target()`` re-stamps it every RELEASE frame;
  * the RELEASE phase's geometry as a whole.

So the regression set is every verifier that owns one of those touch points:
the freeze/bump clearance contract, marker authority, ruck pin geometry,
release/edge conversion, convergence, anchor integrity, BC drive depth.

Run:  python tmp_run_release_regressions.py
"""

import subprocess
import sys

VERIFIERS = [
    # the same phase / the same contract family
    "verify_bump_clearance_contract.py",
    "verify_release_target_field_clamp.py",
    "verify_release_clears_tackler_commit.py",
    "verify_edge_release_conversion.py",
    "verify_convergence_single_source.py",
    # marker authority (fix 1 clamps marker_geometry_target)
    "verify_marker_drive_gate.py",
    "verify_marker_drive_lock.py",
    # ruck pin geometry derived from the same anchor
    "verify_ruck_pin_glide.py",
    "verify_ruck_pin_role_aware.py",
    "verify_ruck_adjacent_spacing.py",
    # the anchor itself and the BC drive that stamps it
    "verify_anchor_authority.py",
    "verify_anchor_integrity.py",
    "verify_bc_drive_depth.py",
    # the "no movement authority may pin the carrier" family
    "verify_frozen_velocity_pin.py",
    "verify_stuck_fallback.py",
    "verify_geometry_authority_lock.py",
    # phase ownership of the carrier
    "verify_stage3e_transition_ownership.py",
]


def _result_line(text):
    for line in reversed(text.splitlines()):
        if line.startswith("RESULT:"):
            return line.strip()
    return ""


def main():
    results = []
    for index, name in enumerate(VERIFIERS, start=1):
        proc = subprocess.run(
            [sys.executable, name],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        verdict = _result_line(proc.stdout or "")
        results.append((name, proc.returncode, verdict))
        print("[%2d/%2d] %-50s exit=%s  %s"
              % (index, len(VERIFIERS), name, proc.returncode, verdict),
              flush=True)

    failed = [r for r in results if r[1] != 0]
    print("")
    print("TOTAL=%d  PASSED=%d  FAILED=%d"
          % (len(results), len(results) - len(failed), len(failed)))
    for name, code, verdict in failed:
        print("  FAIL %s (exit=%s) %s" % (name, code, verdict))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
