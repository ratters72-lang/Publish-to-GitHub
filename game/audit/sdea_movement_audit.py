"""
game/audit/sdea_movement_audit.py — SDEA Movement Contract Auditor.

Per-frame CSV logger + end-of-simulation violation report for the movement
contracts defined in world/rules/movement_contracts.py.

Logs per-frame: frame, phase, for each player → (player_id, role, frozen, vx, vy, x, y, target_x, target_y)

At finalize(): scans all logged frames for:
  - MUST_BE_FROZEN_MOVING: role in must_be_frozen but has non-zero velocity
  - ILLEGAL_MOVE: role not in allowed_to_move but has non-zero velocity
  - MUST_BE_FROZEN_NOT_FROZEN: role in must_be_frozen but frozen=False

Usage (in run.py with --sdeaaudit flag):
    from game.audit.sdea_movement_audit import SDEAMovementAudit
    auditor = SDEAMovementAudit()
    # each frame: auditor.log_frame(world, phase_str)
    # at end:    auditor.finalize()
"""

import csv
import os
from datetime import datetime

from world.rules.movement_contracts import (
    MOVEMENT_RULES,
    get_role,
    Role,
    _all_players,
)


class SDEAMovementAudit:
    """Offline movement contract auditor: logs frames, checks compliance at end."""

    def __init__(self):
        self.frame = 0
        self._frames: list[dict] = []
        self._violations: list[str] = []

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._frames_path = f"audit_sdea_frames_{ts}.csv"
        self._issues_path = f"audit_sdea_issues_{ts}.txt"

        self._headers = [
            "frame",
            "phase",
            "player_id",
            "role",
            "frozen",
            "vx",
            "vy",
            "x",
            "y",
            "target_x",
            "target_y",
        ]
        with open(self._frames_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(self._headers)

        with open(self._issues_path, "w") as f:
            f.write(f"SDEA MOVEMENT AUDIT — {ts}\n")
            f.write("=" * 80 + "\n")
            f.write("Validates per-frame movement against MOVEMENT_RULES.\n\n")

    def log_frame(self, world, phase_str: str) -> None:
        """Log one frame worth of player data and check for violations."""
        self.frame += 1
        rules = MOVEMENT_RULES.get(phase_str)
        if rules is None:
            return

        must_frozen = rules["must_be_frozen"]
        allowed = rules["allowed_to_move"]

        for p in _all_players(world):
            role = get_role(p)
            vx = getattr(p, "vx", 0.0)
            vy = getattr(p, "vy", 0.0)
            frozen = getattr(p, "frozen", False)

            # Write CSV row
            row = {
                "frame": self.frame,
                "phase": phase_str,
                "player_id": getattr(p, "player_id", -1),
                "role": role.name,
                "frozen": frozen,
                "vx": round(vx, 4),
                "vy": round(vy, 4),
                "x": round(getattr(p, "x", 0.0), 3),
                "y": round(getattr(p, "y", 0.0), 3),
                "target_x": round(getattr(p, "target_x", 0.0), 3),
                "target_y": round(getattr(p, "target_y", 0.0), 3),
            }
            self._write_row(row)

            # Check violations
            if role in must_frozen and (abs(vx) > 1e-3 or abs(vy) > 1e-3):
                v = (
                    f"[FRAME {self.frame}] MUST_BE_FROZEN_MOVING "
                    f"id={p.player_id} role={role.name} vx={vx:.3f} vy={vy:.3f} phase={phase_str}"
                )
                self._violations.append(v)

            if role not in allowed and (abs(vx) > 1e-3 or abs(vy) > 1e-3):
                v = (
                    f"[FRAME {self.frame}] ILLEGAL_MOVE "
                    f"id={p.player_id} role={role.name} vx={vx:.3f} vy={vy:.3f} phase={phase_str}"
                )
                self._violations.append(v)

            if role in must_frozen and not frozen:
                v = (
                    f"[FRAME {self.frame}] MUST_BE_FROZEN_NOT_FROZEN "
                    f"id={p.player_id} role={role.name} frozen=False phase={phase_str}"
                )
                self._violations.append(v)

    def _write_row(self, row: dict) -> None:
        with open(self._frames_path, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow([row.get(h, "") for h in self._headers])

    def finalize(self) -> None:
        """Write summary and list all violations to the issues file."""
        with open(self._issues_path, "a") as f:
            f.write("\n" + "=" * 80 + "\n")
            f.write(f"SUMMARY — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Total frames audited: {self.frame}\n")
            f.write(f"Total violations:     {len(self._violations)}\n")
            f.write("=" * 80 + "\n\n")

            if self._violations:
                f.write("VIOLATION LIST:\n")
                f.write("-" * 60 + "\n")
                for v in self._violations:
                    f.write(f"  {v}\n")
            else:
                f.write("  ✅ No violations found — all movement contract rules satisfied.\n")

            f.write("\n" + "=" * 80 + "\n")
            f.write(f"Frames: {self._frames_path}\n")
            f.write(f"Issues: {self._issues_path}\n")

        print(f"[SDEA AUDIT] {self.frame} frames, {len(self._violations)} violations logged")
        print(f"[SDEA AUDIT] Frames: {self._frames_path}")
        print(f"[SDEA AUDIT] Issues: {self._issues_path}")
