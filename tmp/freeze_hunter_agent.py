#!/usr/bin/env python3
"""
freeze_hunter_agent.py — Contract-aware freeze/movement violation AI.

Wires PHASE_RULES (ground truth) against FREEZE_AUDIT log output and
SDM retreat lines to produce concrete, per-frame, per-player verdicts.

Detection modes:
  CONTRACT BUG:  expected_frozen != actual frozen_flag  (invariants wrong)
  PIPELINE BUG:  expected_frozen=False but player is stuck for >N frames
                 (movement pipeline not advancing an allowed mover)

Focus: TACKLE_DRIVE retreat_10 defenders stuck while SDM issues targets.
"""

import re
import sys
import csv
from collections import defaultdict

# ── Ground truth: canonical phase/role rules ──────────────────────

PHASE_RULES = {
    "OPEN_PLAY": {
        "must_be_frozen": [],
        "allowed_to_move": [
            "DEF_LINE_READY", "DEF_CHASE", "ATT_CARRIER", "ATT_SUPPORT",
            "MARKER_A", "MARKER_B", "DUMMY_HALF", "DEF_RETREAT_10",
            "TACKLER_1", "TACKLER_2", "TACKLER_3", "NONE",
        ],
    },
    "POST_RUCK_OPEN_PLAY": {
        "must_be_frozen": [],
        "allowed_to_move": [
            "DEF_LINE_READY", "DEF_CHASE", "ATT_CARRIER", "ATT_SUPPORT",
            "MARKER_A", "MARKER_B", "DUMMY_HALF", "DEF_RETREAT_10",
            "TACKLER_1", "TACKLER_2", "TACKLER_3", "NONE",
        ],
    },
    "TACKLE_INIT": {
        "must_be_frozen": [
            "MARKER_A", "MARKER_B", "DEF_LINE_READY", "DEF_CHASE",
            "ATT_SUPPORT", "NONE",
        ],
        "allowed_to_move": [
            "ATT_CARRIER", "TACKLER_1", "TACKLER_2", "TACKLER_3",
            "DUMMY_HALF", "DEF_RETREAT_10",
        ],
    },
    "TACKLE_CONTACT": {
        "must_be_frozen": [
            "MARKER_A", "MARKER_B", "DEF_LINE_READY", "DEF_CHASE",
            "ATT_SUPPORT", "NONE",
        ],
        "allowed_to_move": [
            "ATT_CARRIER", "TACKLER_1", "TACKLER_2", "TACKLER_3",
            "DUMMY_HALF", "DEF_RETREAT_10",
        ],
    },
    "TACKLE_DRIVE": {
        "must_be_frozen": [
            "MARKER_A", "MARKER_B", "DEF_LINE_READY", "DEF_CHASE",
            "ATT_SUPPORT", "NONE",
        ],
        "allowed_to_move": [
            "ATT_CARRIER", "TACKLER_1", "TACKLER_2", "TACKLER_3",
            "DUMMY_HALF", "DEF_RETREAT_10",
        ],
    },
    "TACKLE_DOMINANCE": {
        "must_be_frozen": [
            "MARKER_A", "MARKER_B", "DEF_LINE_READY", "DEF_CHASE",
            "ATT_SUPPORT", "NONE",
        ],
        "allowed_to_move": [
            "ATT_CARRIER", "TACKLER_1", "TACKLER_2", "TACKLER_3",
            "DUMMY_HALF", "DEF_RETREAT_10",
        ],
    },
    "RELEASE": {
        "must_be_frozen": [
            "TACKLER_1", "TACKLER_2", "TACKLER_3", "NONE",
        ],
        "allowed_to_move": [
            "ATT_CARRIER", "DUMMY_HALF", "MARKER_A", "MARKER_B",
            "DEF_RETREAT_10", "DEF_LINE_READY", "DEF_CHASE", "ATT_SUPPORT",
        ],
    },
    "PTB_PREP": {
        "must_be_frozen": [
            "DEF_LINE_READY", "DEF_CHASE", "ATT_SUPPORT",
            "TACKLER_1", "TACKLER_2", "TACKLER_3", "NONE",
        ],
        "allowed_to_move": [
            "ATT_CARRIER", "DUMMY_HALF", "MARKER_A", "MARKER_B",
            "DEF_RETREAT_10",
        ],
    },
    "PTB_EXEC": {
        "must_be_frozen": [
            "DEF_LINE_READY", "DEF_CHASE", "ATT_SUPPORT",
            "TACKLER_1", "TACKLER_2", "TACKLER_3", "NONE",
        ],
        "allowed_to_move": [
            "ATT_CARRIER", "DUMMY_HALF", "MARKER_A", "MARKER_B",
            "DEF_RETREAT_10",
        ],
    },
    "PTB": {
        "must_be_frozen": [
            "DEF_LINE_READY", "DEF_CHASE", "ATT_SUPPORT",
            "TACKLER_1", "TACKLER_2", "TACKLER_3", "NONE",
        ],
        "allowed_to_move": [
            "ATT_CARRIER", "DUMMY_HALF", "MARKER_A", "MARKER_B",
            "DEF_RETREAT_10",
        ],
    },
}

# Role inference map: SDM reason snippet → role string
SDM_REASON_ROLE = {
    "tes_retreat_10": "DEF_RETREAT_10",
    "release_retreat_10": "DEF_RETREAT_10",
    "ptb_retreat_10": "DEF_RETREAT_10",
    "open_play_retreat_10": "DEF_RETREAT_10",
    "open_play_line_ready": "DEF_LINE_READY",
    "open_play_chase": "DEF_CHASE",
    "open_play_dh": "DUMMY_HALF",
    "open_play_marker_a": "MARKER_A",
    "open_play_marker_b": "MARKER_B",
}

# ── Log line regexes ──────────────────────────────────────────────

RE_FREEZE_HEADER = re.compile(
    r'\[FREEZE_AUDIT @ frame (\d+)\] phase=(\S+) sub=(\S+) timer=([\d.]+)'
)
RE_FROZEN_LIST = re.compile(r'\s*Frozen -> markers: \[(.*?)\]\s+defenders: \[(.*?)\]\s+attackers: \[(.*?)\]')
RE_STUCK_NOT_FROZEN = re.compile(r'\s*Stuck \(not frozen\): \[(.*?)\]')
RE_STICK = re.compile(r'STUCK_NOT_FROZEN id=(\d+) x=([\d.-]+) y=([\d.-]+)')
RE_SDM = re.compile(r'\[SDM\]\s+(\S+)\s+id=(\d+)\s+x=([\d.-]+)\s*->\s*([\d.-]+)\s+(.*)')
RE_SOFT_STUCK = re.compile(r'STUCK_SOFT_FREEZE id=(\d+).*tx=([\d.-]+)\s+x=([\d.-]+)')
RE_SDEA = re.compile(r'\[SDEA_MOVEMENT_VIOLATION\]\s+(.+)')
RE_MISMATCH = re.compile(r'\[FREEZE_MISMATCH\]\s+(.+)')

STUCK_THRESHOLD = 10  # frames


class FrameState:
    """Snapshots per-frame player state from FREEZE_AUDIT output."""
    def __init__(self):
        self.frame = 0
        self.phase = ""
        self.sub_state = ""
        self.timer = 0.0
        self.frozen_defenders: list[int] = []
        self.frozen_attackers: list[int] = []
        self.frozen_markers: list[int] = []
        self.stuck_not_frozen: list[int] = []
        self.stuck_positions: dict[int, tuple[float, float]] = {}
        self.sdm_targets: dict[int, dict] = {}  # id -> {action, x0, xt, reason}
        self.violations: list[str] = []

    def is_frozen(self, pid: int) -> bool | None:
        if pid in self.frozen_defenders:
            return True
        if pid in self.frozen_markers:
            return True
        if pid in self.frozen_attackers:
            return True
        return False

    def is_stuck(self, pid: int) -> bool:
        return pid in self.stuck_not_frozen


class HuntViolation:
    """A structured violation block (contiguous frames, same ids, same verdict)."""
    def __init__(self):
        self.frame_start = 0
        self.frame_end = 0
        self.phase = ""
        self.sub_state = ""
        self.ids: list[int] = []
        self.roles: dict[int, str] = {}
        self.expected_frozen: dict[int, bool] = {}
        self.actual_frozen: dict[int, bool] = {}
        self.stuck_flags: dict[int, bool] = {}
        self.verdict = ""
        self.suspected_subsystem = ""
        self.sdm_targets_seen: bool = False

    def format(self) -> str:
        lines = []
        lines.append(f"  [{self.verdict}] frames {self.frame_start}-{self.frame_end}  "
                      f"phase={self.phase} sub={self.sub_state}")
        for pid in self.ids:
            role = self.roles.get(pid, "?")
            expected = self.expected_frozen.get(pid, None)
            actual = self.actual_frozen.get(pid, None)
            stuck = self.stuck_flags.get(pid, False)
            lines.append(f"    id={pid} role={role}  "
                          f"expected_frozen={expected} actual_frozen={actual} "
                          f"stuck={stuck}")
        if self.suspected_subsystem:
            lines.append(f"    -> Suspected: {self.suspected_subsystem}")
        return "\n".join(lines)

    def to_csv_row(self):
        ids_str = ",".join(str(i) for i in self.ids)
        roles_str = ",".join(self.roles.get(i, "?") for i in self.ids)
        expected_s = ",".join(str(self.expected_frozen.get(i, "?")) for i in self.ids)
        actual_s = ",".join(str(self.actual_frozen.get(i, "?")) for i in self.ids)
        stuck_s = ",".join(str(self.stuck_flags.get(i, False)) for i in self.ids)
        return [self.frame_start, self.frame_end, self.phase, self.sub_state,
                ids_str, roles_str, expected_s, actual_s, stuck_s,
                self.verdict, self.suspected_subsystem]


# ── Parser ────────────────────────────────────────────────────────

def infer_role_from_sdm(reason: str) -> str | None:
    return SDM_REASON_ROLE.get(reason)


def parse_log(lines) -> list[HuntViolation]:
    """Parse lines into HuntViolation blocks."""
    frames: list[FrameState] = []
    current = FrameState()

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # FREEZE_AUDIT header
        m = RE_FREEZE_HEADER.search(line)
        if m:
            if current.frame > 0:
                frames.append(current)
            current = FrameState()
            current.frame = int(m.group(1))
            current.phase = m.group(2)
            current.sub_state = m.group(3)
            current.timer = float(m.group(4))
            continue

        # Frozen lists
        m = RE_FROZEN_LIST.search(line)
        if m:
            markers_str = m.group(1).strip()
            def_str = m.group(2).strip()
            att_str = m.group(3).strip()
            if markers_str:
                current.frozen_markers = [int(x.strip()) for x in markers_str.split(",") if x.strip()]
            if def_str:
                current.frozen_defenders = [int(x.strip()) for x in def_str.split(",") if x.strip()]
            if att_str:
                current.frozen_attackers = [int(x.strip()) for x in att_str.split(",") if x.strip()]
            continue

        # Stuck (not frozen) list
        m = RE_STUCK_NOT_FROZEN.search(line)
        if m:
            stuck_str = m.group(1).strip()
            if stuck_str:
                current.stuck_not_frozen = [int(x.strip()) for x in stuck_str.split(",") if x.strip()]
            continue

        # STUCK_NOT_FROZEN detail
        m = RE_STICK.search(line)
        if m:
            pid = int(m.group(1))
            x = float(m.group(2))
            y = float(m.group(3))
            current.stuck_positions[pid] = (x, y)
            if pid not in current.stuck_not_frozen:
                current.stuck_not_frozen.append(pid)
            continue

        # SDM retreat lines
        m = RE_SDM.search(line)
        if m:
            action = m.group(1)
            pid = int(m.group(2))
            x0 = float(m.group(3))
            xt = float(m.group(4))
            reason = m.group(5).strip()
            current.sdm_targets[pid] = {
                "action": action,
                "x_before": x0,
                "x_target": xt,
                "reason": reason,
            }
            continue

        # SDEA violations
        m = RE_SDEA.search(line)
        if m:
            current.violations.append(m.group(1))
            continue

        # FREEZE_MISMATCH assertions
        m = RE_MISMATCH.search(line)
        if m:
            current.violations.append("MISMATCH: " + m.group(1))
            continue

    if current.frame > 0:
        frames.append(current)

    # ── Now analyse: group into violation blocks ──
    return _analyse_frames(frames)


def _guess_role(pid: int, frame: FrameState) -> str:
    """Guess role from SDM reason or context."""
    sdm = frame.sdm_targets.get(pid)
    if sdm:
        role = infer_role_from_sdm(sdm["reason"])
        if role:
            return role
    # Default: if in frozen_defenders, they are a defender
    if pid in frame.frozen_defenders or pid in frame.stuck_not_frozen:
        return "DEF_RETREAT_10"  # best guess for stuck retreat
    return "NONE"


def _analyse_frames(frames: list[FrameState]) -> list[HuntViolation]:
    """Analyse frame sequence and produce verdict blocks."""
    violations: list[HuntViolation] = []

    # Track per-player stuck duration across frames
    stuck_durations: dict[int, int] = defaultdict(int)

    for i, frame in enumerate(frames):
        phase = frame.phase
        rules = PHASE_RULES.get(phase, {})
        must_frozen_set = set(rules.get("must_be_frozen", []))
        allowed_set = set(rules.get("allowed_to_move", []))

        # Check each stuck player
        for pid in frame.stuck_not_frozen:
            stuck_durations[pid] += 1
        # Reset for players no longer stuck
        for pid in list(stuck_durations.keys()):
            if pid not in frame.stuck_not_frozen:
                stuck_durations[pid] = 0

        for pid in frame.stuck_not_frozen:
            role = _guess_role(pid, frame)
            actual_frozen = frame.is_frozen(pid) or False
            expected_frozen = (role in must_frozen_set) or (role not in allowed_set)

            # Check for SDM targets
            sdm_target = frame.sdm_targets.get(pid, None)

            # Determine verdict
            if expected_frozen and not actual_frozen:
                verdict = "CONTRACT BUG"
                subsystem = "apply_phase_invariants"
            elif not expected_frozen and actual_frozen:
                verdict = "CONTRACT BUG"
                subsystem = "apply_phase_invariants"
            elif not expected_frozen and not actual_frozen:
                # Allowed mover but stuck
                if stuck_durations[pid] >= STUCK_THRESHOLD:
                    # Determine which subsystem likely at fault
                    if sdm_target and abs(sdm_target["x_target"] - sdm_target["x_before"]) > 0.1:
                        # SDM is writing targets but position is not advancing
                        verdict = "PIPELINE BUG"
                        subsystem = "arrival_predictor or Player.update(dt) not advancing allowed mover"
                    else:
                        verdict = "PIPELINE BUG"
                        subsystem = "SDM not issuing targets or role mapping missing"
                else:
                    continue  # not stuck long enough
            else:
                # expected_frozen == True and actual_frozen == True → frozen correctly
                continue

            # Build or extend violation block
            last = violations[-1] if violations else None
            if (last and last.verdict == verdict
                    and last.phase == phase
                    and pid in last.ids
                    and last.frame_end == frame.frame - 1):
                # Extend existing block
                last.frame_end = frame.frame
            else:
                v = HuntViolation()
                v.frame_start = frame.frame
                v.frame_end = frame.frame
                v.phase = phase
                v.sub_state = frame.sub_state
                v.ids = [pid]
                v.roles = {pid: role}
                v.expected_frozen = {pid: expected_frozen}
                v.actual_frozen = {pid: actual_frozen}
                v.stuck_flags = {pid: True}
                v.verdict = verdict
                v.suspected_subsystem = subsystem
                v.sdm_targets_seen = sdm_target is not None
                violations.append(v)

    return violations


def format_report(violations: list[HuntViolation]) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append("FREEZE HUNTER AGENT — Contract Violation Report")
    lines.append("=" * 70)

    if not violations:
        lines.append("\nNo violations detected.")
        return "\n".join(lines)

    # Count by verdict
    by_verdict = defaultdict(int)
    for v in violations:
        by_verdict[v.verdict] += 1

    lines.append(f"\nSummary:")
    for vt, count in sorted(by_verdict.items(), key=lambda x: -x[1]):
        lines.append(f"  {vt}: {count}")

    # Detail
    lines.append(f"\nDetail ({len(violations)} blocks):")
    for v in violations:
        lines.append("")
        lines.append(v.format())

    lines.append("\n" + "=" * 70)
    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="freeze_hunter_agent - Contract-aware freeze/movement violation AI")
    parser.add_argument("--file", "-f", help="Read from log file")
    parser.add_argument("--live", "-l", action="store_true", help="Read from stdin")
    parser.add_argument("--csv", "-c", help="Export to CSV")
    parser.add_argument("--stuck-threshold", type=int, default=10,
                        help="Frames before stuck is flagged (default 10)")
    args = parser.parse_args()

    global STUCK_THRESHOLD
    STUCK_THRESHOLD = args.stuck_threshold

    if not args.file and not args.live:
        parser.print_help()
        sys.exit(1)

    lines = []
    if args.file:
        with open(args.file, "r") as f:
            lines = f.readlines()
    else:
        lines = sys.stdin.readlines()

    violations = parse_log(lines)
    print(format_report(violations))

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["frame_start", "frame_end", "phase", "sub_state",
                         "player_ids", "roles", "expected_frozen",
                         "actual_frozen", "stuck", "verdict", "suspected_subsystem"])
            for v in violations:
                w.writerow(v.to_csv_row())
        print(f"Exported {len(violations)} violations to {args.csv}")


if __name__ == "__main__":
    main()
