"""tmp runner — execute the glide-adjacent verifiers and report pass/fail.

Sequential, one subprocess per target so a hang or crash in one cannot mask
the others.  Prints a compact status table plus the tail of any failure.
"""
from __future__ import annotations

import os
import subprocess
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

TARGETS = [
    # ── direct units for the modules this change touched ──
    "verify_tackle_begin_glide.py",
    "verify_target_x_glide.py",
    "verify_ruck_pin_glide.py",
    "verify_tes_drive_structure_hold.py",
    "verify_seed_hold.py",
    "verify_writer_priority_bands.py",
    # ── the TES / retreat contract family around them ──
    "verify_tes_depth_authority_contract.py",
    "verify_tes_depth_obligation_contract.py",
    "verify_tes_width_obligation_contract.py",
    "verify_tes_lane_true_retreat.py",
    "verify_retreat_obligation_contract.py",
    "verify_retreat_backpedal_contract.py",
    "verify_pretackle_corridor.py",
    "verify_tackle_commit.py",
    "verify_tes_slot_recovery_ownership.py",
    "verify_def_line_ready_frozen_drive.py",
    # ── this change's own contract ──
    "verify_tes_glide_smoothing_contract.py",
]

TIMEOUT_S = 900


def main() -> int:
    rows = []
    for name in TARGETS:
        if not os.path.exists(name):
            rows.append((name, "MISSING", ""))
            continue
        # Several verifiers print non-ASCII (e.g. "→") in check names, and
        # Python defaults child stdout to the locale encoding (cp1252 here),
        # which raises UnicodeEncodeError and makes a PASSING verifier exit
        # non-zero.  Force UTF-8 on the child and decode tolerantly, so the
        # status reported is the verifier's real verdict.
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
