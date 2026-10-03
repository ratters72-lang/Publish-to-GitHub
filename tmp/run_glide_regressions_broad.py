"""tmp runner — BROAD defence regression set around the glide authorities.

The bounded-glide change touches modules consumed from the manager's
TACKLE_DRIVE path and from the contact/contest chain, so this runner sweeps
the wider contract family (including the probes, which are slower but
exercise the live per-frame integrators) in one sequential pass.
"""
from __future__ import annotations

import os
import subprocess
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.pop("RLM_GLIDE_SMOOTH", None)

TARGETS = [
    # ── the glide change's own contract ──
    "verify_tes_glide_smoothing_contract.py",
    # ── contact / contest chain (manager TACKLE_DRIVE consumers) ──
    "verify_contact_authority_contract.py",
    "verify_contest_preservation_contract.py",
    "verify_contest_transport_contract.py",
    "verify_pretackle_corridor.py",
    "verify_tackle_commit.py",
    "verify_tackle_engage_guard.py",
    "verify_tackle_state_gate.py",
    # ── TES-window geometry ──
    "verify_tes_drive_structure_hold.py",
    "verify_tes_drive_xy_writers.py",
    "verify_tes_lane_true_retreat.py",
    "verify_tes_line_width_hold.py",
    "verify_tes_slot_recovery_ownership.py",
    "verify_tes_unified_drive_pipeline.py",
    "verify_tes_slot_recovery_ownership.py",
    # ── depth / retreat / spacing families ──
    "verify_depth_boundary_invariant.py",
    "verify_depth_role_stability.py",
    "verify_retreat_obligation_contract.py",
    "verify_retreat_backpedal_contract.py",
    "verify_retreat_fullback_contracts.py",
    "verify_retreat_width_consistency.py",
    "verify_ruck_adjacent_spacing.py",
    "verify_ruck_pin_glide.py",
    "verify_ruck_pin_role_aware.py",
    "verify_line_break_static_guard.py",
    "verify_defensive_pipeline.py",
    "verify_spacing.py",
    "verify_seed_hold.py",
    "verify_writer_priority_bands.py",
    "verify_def_line_ready_frozen_drive.py",
    # ── fullback (explicitly exempt from every glide) ──
    "verify_fullback_can_tackle.py",
    "verify_fullback_never_tackler_or_frozen.py",
    "verify_mmdl_fullback_exclusion.py",
    "verify_fullback_continuity.py",
    # ── live per-frame probes ──
    "probe_drive_non_tackler_motion.py",
    "probe_tes_lane_true.py",
    "probe_tes_hold_integrator.py",
    "probe_structure_hold_lifecycle.py",
]

TIMEOUT_S = 1200


def main() -> int:
    rows = []
    for name in TARGETS:
        if not os.path.exists(name):
            rows.append((name, "MISSING", ""))
            continue
        child_env = dict(os.environ)
        child_env["PYTHONIOENCODING"] = "utf-8"
        try:
            proc = subprocess.run(
                [sys.executable, name],
                capture_output=True, text=True, timeout=TIMEOUT_S,
                encoding="utf-8", errors="replace", env=child_env,
            )
        except subprocess.TimeoutExpired:
            rows.append((name, "TIMEOUT", ""))
            continue
        out = (proc.stdout or "").strip().splitlines()
        err = (proc.stderr or "").strip().splitlines()
        tail = out[-3:] if out else err[-3:]
        rows.append((name, f"rc={proc.returncode}", " | ".join(tail)))

    print("=" * 100)
    for name, status, tail in rows:
        flag = "OK  " if status == "rc=0" else "FAIL"
        print(f"{flag} {status:<10} {name}")
        if status != "rc=0" and tail:
            print(f"      {tail}")
    print("=" * 100)
    broken = [r for r in rows if r[1] != "rc=0"]
    print(f"{len(rows) - len(broken)}/{len(rows)} clean")
    for name, status, _ in broken:
        print(f"  NOT CLEAN: {name} ({status})")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
