#!/usr/bin/env python3
"""
freeze_log_hunter.py — Scan RLM logs for freeze/movement violations.

Parses FREEZE_AUDIT, SDEA, and FREEZE_MISMATCH log lines, checks them
against PHASE_RULES, and reports violations.

Usage:
    # Pipe from running simulation:
    python run.py 2>&1 | python tmp/freeze_log_hunter.py --live

    # From saved log file:
    python tmp/freeze_log_hunter.py --file run.log

    # Export to CSV:
    python tmp/freeze_log_hunter.py --file run.log --csv violations.csv
"""

import re
import sys
import csv
from collections import defaultdict

# ── Phase-Role rules (inlined from world/rules/phase_rules.py for standalone use) ──

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

# ── Log line regexes ─────────────────────────────────────────────────

RE_FREEZE_AUDIT = re.compile(r'\[FREEZE_AUDIT\]\s*frame=(\d+)\s*phase=(\S+)')
RE_FROZEN_LIST = re.compile(r'\s*Frozen:\s*\[(.*?)\]')
RE_MOVING_LIST = re.compile(r'\s*Moving:\s*\[(.*?)\]')
RE_STUCK_LIST = re.compile(r'\s*Stuck.*?:\s*\[(.*?)\]')
RE_PLAYER_ENTRY = re.compile(r'id=(\d+)\s+role=(\S+)')
RE_SDEA_VIOLATION = re.compile(r'\[SDEA_MOVEMENT_VIOLATION\]\s+(.+)')
RE_FREEZE_MISMATCH = re.compile(r'\[FREEZE_MISMATCH\]\s+(.+)')
RE_SDM_LINE = re.compile(r'\[SDM\]\s+(\S+)\s+id=(\d+)\s+x=([\d.-]+)\s*->\s*([\d.-]+)\s+(.*)')
RE_STAGE_START = re.compile(r'\[STAGE_START\]\s+(\S+)')


class PlayerState:
    """Tracks a single player across frames for ZOOM_AFTER_STUCK detection."""
    def __init__(self):
        self.prev_x = None
        self.stuck_frames = 0

    def update(self, frame, x, target_x, vx, frozen):
        violations = []
        if abs(vx) < 0.001 and abs(target_x - x) > 0.05:
            self.stuck_frames += 1
        else:
            self.stuck_frames = 0
        if self.prev_x is not None and self.stuck_frames == 0:
            delta = abs(x - self.prev_x)
            if delta > 0.5 and self.stuck_frames > 0:
                violations.append("ZOOM_AFTER_STUCK")
        self.prev_x = x
        return violations


def parse_log(lines):
    """Yield (frame, phase, pid, role, vtype, source) tuples."""
    players = defaultdict(PlayerState)
    current_frame = 0
    current_phase = None

    for line in lines:
        line = line.strip()

        m = RE_FREEZE_AUDIT.search(line)
        if m:
            current_frame = int(m.group(1))
            current_phase = m.group(2)
            continue

        m = RE_STUCK_LIST.search(line)
        if m and current_phase:
            for pid_str, role in RE_PLAYER_ENTRY.findall(m.group(1)):
                pid = int(pid_str)
                rules = PHASE_RULES.get(current_phase, {})
                if role in rules.get("must_be_frozen", []):
                    yield (current_frame, current_phase, pid, role,
                           "STUCK_NOT_FROZEN", "FREEZE_AUDIT")
            continue

        m = RE_FROZEN_LIST.search(line)
        if m and current_phase:
            for pid_str, role in RE_PLAYER_ENTRY.findall(m.group(1)):
                pid = int(pid_str)
                rules = PHASE_RULES.get(current_phase, {})
                if role in rules.get("allowed_to_move", []):
                    yield (current_frame, current_phase, pid, role,
                           "FROZEN_SHOULD_MOVE", "FREEZE_AUDIT")
            continue

        m = RE_SDEA_VIOLATION.search(line)
        if m:
            detail = m.group(1)
            pid_m = re.search(r'id=(\d+)', detail)
            pid = int(pid_m.group(1)) if pid_m else 0
            yield (current_frame, current_phase, pid, "?",
                   "SDEA_VIOLATION", detail)
            continue

        m = RE_FREEZE_MISMATCH.search(line)
        if m:
            detail = m.group(1)
            pid_m = re.search(r'id=(\d+)', detail)
            pid = int(pid_m.group(1)) if pid_m else 0
            yield (current_frame, current_phase, pid, "?",
                   "FREEZE_MISMATCH", detail)
            continue


def format_report(violations):
    lines = []
    lines.append("=" * 70)
    lines.append("FREEZE/MOVEMENT VIOLATION REPORT")
    lines.append("=" * 70)

    vcount = defaultdict(int)
    for v in violations:
        if len(v) >= 6:
            vcount[v[4]] += 1

    lines.append(f"\nTotal violations by type:")
    for vtype, count in sorted(vcount.items(), key=lambda x: -x[1]):
        lines.append(f"  {vtype}: {count}")

    lines.append(f"\nDetail:")
    for v in violations[:100]:
        if len(v) >= 6:
            frame, phase, pid, role, vtype, src = v[:6]
            lines.append(f"  frame={frame} phase={phase} id={pid} role={role}"
                         f" [{vtype}] src={src}")
    if len(violations) > 100:
        lines.append(f"  ... +{len(violations)-100} more (truncated)")

    lines.append("=" * 70)
    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="freeze_log_hunter - Scan for freeze/movement violations in RLM logs")
    parser.add_argument("--file", "-f", help="Read from log file")
    parser.add_argument("--live", "-l", action="store_true",
                        help="Read from stdin")
    parser.add_argument("--csv", "-c", help="Export to CSV")
    args = parser.parse_args()

    if not args.file and not args.live:
        parser.print_help()
        sys.exit(1)

    lines = []
    if args.file:
        with open(args.file, "r") as f:
            lines = f.readlines()
    else:
        lines = sys.stdin.readlines()

    violations = list(parse_log(lines))
    print(format_report(violations))

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["frame", "phase", "player_id", "role",
                        "violation_type", "source"])
            for v in violations:
                if len(v) >= 6:
                    w.writerow(v[:6])
        print(f"Exported {len(violations)} to {args.csv}")


if __name__ == "__main__":
    main()
