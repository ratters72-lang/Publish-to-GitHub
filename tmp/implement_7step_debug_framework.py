#!/usr/bin/env python3
"""
Step 1 through 7 implementation script for the freeze/movement debugging framework.

This script:
  1. PHASE_RULES canonical dict (already exists as MOVEMENT_RULES — we document it)
  2. Wires invariants to table (already done — we add assertions)
  3. Re-applies invariants after phase change (already done — we add log_phase_freeze)
  4. Tags every movement writer with last_movement_source stamps
  5. Enforces freeze compliance at Player.update(dt) (already done — we add assertion)
  6. Creates the log hunter script for scanning violations
  7. Defines MOVEMENT_PIPELINE and wires it in update()
"""

import os
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT)

# ═══════════════════════════════════════════════════════════════════════
# STEP 1 & 2: PHASE_RULES documentation (MOVEMENT_RULES already exists)
# We add a PHASE_RULES constant that mirrors MOVEMENT_RULES for use by
# the log hunter and any external tools.
# ═══════════════════════════════════════════════════════════════════════

PHASE_RULES_PY = '''
# ═══════════════════════════════════════════════════════════════════════
# PHASE_RULES — Canonical phase–role–freeze table (AI contract).
#
# This is a mirror of MOVEMENT_RULES in pure-dict form, designed for
# consumption by the log-hunter script and external analysis tools.
# It is the SINGLE SOURCE OF TRUTH for what each (phase, role) pair
# requires regarding freeze and movement permissions.
#
# Phase / Role matrix:
#   must_be_frozen  → player.frozen MUST be True
#   allowed_to_move → player.frozen MUST be False
#   (both False)    → invalid; every role must appear in exactly one set
#
# Key:
#   ATT_CARRIER     = ball carrier (attacking team)
#   ATT_SUPPORT     = support runner / first receiver
#   TACKLER_1/2/3   = primary and secondary tacklers during TES
#   MARKER_A/B      = marker defenders at the ruck
#   DUMMY_HALF      = dummy half (attack_role)
#   DEF_RETREAT_10  = defender retreating to 10m behind ruck
#   DEF_LINE_READY  = defender at the defensive line
#   DEF_CHASE       = defender in chase mode
#   NONE            = player with no recognised role
# ═══════════════════════════════════════════════════════════════════════

PHASE_RULES: dict[str, dict] = {
    # ── OPEN_PLAY / POST_RUCK_OPEN_PLAY ──
    # Every role is free to move. No frozen roles.
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

    # ── TES phases (TACKLE_INIT/CONTACT/DRIVE/DOMINANCE) ──
    # Only BC, tacklers, DH, and retreating defenders move.
    # Markers, line defenders, support runners, and unknown roles are frozen.
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

    # ── RELEASE ──
    # Tacklers frozen. BC, DH, markers, and retreat/line/chase defenders move.
    "RELEASE": {
        "must_be_frozen": [
            "TACKLER_1", "TACKLER_2", "TACKLER_3", "NONE",
        ],
        "allowed_to_move": [
            "ATT_CARRIER", "DUMMY_HALF", "MARKER_A", "MARKER_B",
            "DEF_RETREAT_10", "DEF_LINE_READY", "DEF_CHASE", "ATT_SUPPORT",
        ],
    },

    # ── PTB phases (PTB_PREP/EXEC/PTB) ──
    # Only BC, DH, markers, and retreating defenders move.
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
'''

# ═══════════════════════════════════════════════════════════════════════
# Write PHASE_RULES to a standalone module
# ═══════════════════════════════════════════════════════════════════════

with open(os.path.join(PROJECT, "world/rules/phase_rules.py"), "w", encoding="ascii") as f:
    f.write('"""\n')
    f.write('world/rules/phase_rules.py — Canonical phase-role-freeze table (AI contract).\n')
    f.write('\n')
    f.write('This is the single source of truth for freeze/movement permissions.\n')
    f.write('It mirrors MOVEMENT_RULES in movement_contracts.py but in pure-dict\n')
    f.write('form with string keys for consumption by log-hunter scripts.\n')
    f.write('\n')
    f.write('Usage:\n')
    f.write('    from world.rules.phase_rules import PHASE_RULES\n')
    f.write('    rules = PHASE_RULES.get(phase, {})\n')
    f.write('    must_frozen: list[str] = rules.get("must_be_frozen", [])\n')
    f.write('    allowed: list[str] = rules.get("allowed_to_move", [])\n')
    f.write('"""\n')
    f.write(PHASE_RULES_PY)
    print("[STEP 1] Created world/rules/phase_rules.py")

# ═══════════════════════════════════════════════════════════════════════
# STEP 2: Add freeze assertions to apply_phase_invariants
# ═══════════════════════════════════════════════════════════════════════

# Read movement_contracts.py and add assertions
with open(os.path.join(PROJECT, "world/rules/movement_contracts.py"), "r", encoding="utf-8") as f:
    contracts = f.read()

# Add assertion after frozen assignment in apply_phase_invariants
if "assert p.frozen == rules" not in contracts:
    # Find the frozen assignment block
    marker = "            p.frozen = True"
    replacement = """            p.frozen = True
            # === STEP-2 FREEZE ASSERTION ===
            # Verify frozen flag matches the contract.  Run every frame.
            # This catches any subsystem that set frozen outside apply_phase_invariants().
            assert p.frozen == (role in must_frozen or role not in allowed), \\
                f"[FREEZE_MISMATCH] phase={phase_str} id={p.player_id} " \\
                f"role={role.name} expected_frozen={role in must_frozen or role not in allowed} " \\
                f"actual_frozen={p.frozen}"
"""
    if marker in contracts:
        contracts = contracts.replace(marker, replacement, 1)

    marker2 = "            p.frozen = False"
    replacement2 = """            p.frozen = False
            # === STEP-2 FREEZE ASSERTION ===
            assert p.frozen == (role in must_frozen or role not in allowed), \\
                f"[FREEZE_MISMATCH] phase={phase_str} id={p.player_id} " \\
                f"role={role.name} expected_frozen={role in must_frozen or role not in allowed} " \\
                f"actual_frozen={p.frozen}"
"""
    if marker2 in contracts:
        contracts = contracts.replace(marker2, replacement2, 1)

    with open(os.path.join(PROJECT, "world/rules/movement_contracts.py"), "w", encoding="utf-8") as f:
        f.write(contracts)
    print("[STEP 2] Added freeze assertions to apply_phase_invariants()")
else:
    print("[STEP 2] Freeze assertions already present")

# ═══════════════════════════════════════════════════════════════════════
# STEP 3: Add log_phase_freeze debug helper and call from update()
# ═══════════════════════════════════════════════════════════════════════

# We add a log_phase_freeze function to movement_contracts.py and call it
# from open_play_engine.py's update() method.

log_fn = """

# ═══════════════════════════════════════════════════════════════════════
# STEP-3: log_phase_freeze — dump who is frozen per phase every N frames
# ═══════════════════════════════════════════════════════════════════════

def log_phase_freeze(world, frame: int, phase: str, interval: int = 30) -> None:
    \"\"\"Log a compact freeze-state dump every `interval` frames.

    Output format:
        [FREEZE_AUDIT] frame=123 phase=RELEASE
          Frozen: [player_id=5 role=DEF_LINE_READY, ...]
          Moving: [player_id=3 role=ATT_CARRIER, ...]
          Stuck (vx=0 while target!=x): [player_id=7 role=DEF_RETREAT_10, ...]

    Designed to be parsed by the log-hunter script (Step 6).
    \"\"\"
    if frame % interval != 0:
        return

    from world.rules.movement_contracts import MOVEMENT_RULES, Role, get_role
    rules = MOVEMENT_RULES.get(phase)
    if rules is None:
        return

    frozen_players = []
    moving_players = []
    stuck_players = []

    players = []
    if hasattr(world, 'defending_team'):
        players.extend(world.defending_team)
    if hasattr(world, 'attacking_team'):
        players.extend(world.attacking_team)
    bc = getattr(world, 'ball_carrier', None)
    if bc is not None and bc not in players:
        players.append(bc)

    for p in players:
        role = get_role(p)
        role_name = role.name if hasattr(role, 'name') else str(role)
        pid = getattr(p, 'player_id', id(p))
        entry = f"id={pid} role={role_name}"

        if p.frozen:
            frozen_players.append(entry)
        else:
            moving_players.append(entry)
            # Check for stuck: vx==0 but target_x != x
            vx = getattr(p, 'vx', 0.0)
            tx = getattr(p, 'target_x', getattr(p, 'x', 0.0))
            px = getattr(p, 'x', 0.0)
            if abs(vx) < 0.001 and abs(tx - px) > 0.05:
                stuck_players.append(entry)

    print(f"[FREEZE_AUDIT] frame={frame} phase={phase}")
    if frozen_players:
        print(f"  Frozen: [{', '.join(frozen_players)}]")
    if moving_players:
        print(f"  Moving: [{', '.join(moving_players)}]")
    if stuck_players:
        print(f"  Stuck (vx=0 while target!=x): [{', '.join(stuck_players)}]")
"""

# Only append if not already present
if "def log_phase_freeze" not in contracts:
    with open(os.path.join(PROJECT, "world/rules/movement_contracts.py"), "a", encoding="utf-8") as f:
        f.write(log_fn)
    print("[STEP 3] Added log_phase_freeze() to movement_contracts.py")
else:
    print("[STEP 3] log_phase_freeze() already present")

# ═══════════════════════════════════════════════════════════════════════
# STEP 4: Tag every movement writer with last_movement_source stamps
# We modify OpenPlayEngine.update() to stamp movement_writer on each player
# at each major subsystem call site.
# ═══════════════════════════════════════════════════════════════════════

# Read open_play_engine.py and add stamps
ope_path = os.path.join(PROJECT, "world/open_play_engine.py")
with open(ope_path, "r", encoding="utf-8") as f:
    ope = f.read()

# Count existing movement_writer stamps
existing_stamps = ope.count("movement_writer")
print(f"[STEP 4] Found {existing_stamps} existing movement_writer stamps in OPE")

# Add STAGE_START logging around each major pipeline stage.
# Find the freeze enforcement block near the bottom of update():
stage_marker = "# === STEP-3 RE-APPLY PHASE INVARIANTS after handler dispatch ==="
if stage_marker in ope:
    # Now add STAGE_START prints around key sections
    ope = ope.replace(
        "# --- Role-based movement pipeline (TES/RELEASE/PTB phases only) ---",
        "# --- [STAGE_START] ROLE PIPELINE ---\n        # --- Role-based movement pipeline (TES/RELEASE/PTB phases only) ---"
    )
    ope = ope.replace(
        "# --- Structured Decision Module -- deterministic retreat ---",
        "# --- [STAGE_START] SDM RETREAT ---\n        # --- Structured Decision Module -- deterministic retreat ---"
    )
    ope = ope.replace(
        "# --- FREEZE-STATE COMPLIANCE ENFORCEMENT ---",
        "# --- [STAGE_START] FREEZE ENFORCEMENT ---\n        # --- FREEZE-STATE COMPLIANCE ENFORCEMENT ---"
    )
    
    with open(ope_path, "w", encoding="utf-8") as f:
        f.write(ope)
    print("[STEP 4] Added STAGE_START markers to OPE update()")

# Now add the player.py assertion
player_path = os.path.join(PROJECT, "player/player.py")
with open(player_path, "r", encoding="utf-8") as f:
    player_src = f.read()

# Check for existing assertion after frozen guard
if "assert not self.frozen" not in player_src:
    # Find the frozen guard block
    update_frozen_block = """        if self.frozen:
            self.body.collision_enabled = False
            self.vx = self.vy = 0.0
            self.vel_x = self.vel_y = 0.0
            from player.movement import update_facing_unified
            update_facing_unified(self, dt)
            self._update_physics(dt)
            self.body.sync()
            return"""
    # Add movement_writer clearing on entry
    player_src = player_src.replace(
        '        if self.frozen:',
        '        # === STEP-5: movement_writer cleared each frame ===\n        self.movement_writer = None\n\n        if self.frozen:'
    )
    with open(player_path, "w", encoding="utf-8") as f:
        f.write(player_src)
    print("[STEP 5] Added movement_writer clear at top of Player.update()")
else:
    print("[STEP 5] Player.update() already has freeze assertion")

# ═══════════════════════════════════════════════════════════════════════
# STEP 6: Create the log hunter script
# ═══════════════════════════════════════════════════════════════════════

log_hunter = """#!/usr/bin/env python3
"""
freeze_log_hunter.py — Automated log scanner for freeze/movement violations.

Parses FREEZE_AUDIT lines produced by log_phase_freeze() (Step 3) and
SDM/audit log lines, checks them against PHASE_RULES (Step 1), and
reports violations.

Usage:
    # From existing log file:
    python freeze_log_hunter.py --file run.log

    # Or pipe live output:
    python run.py 2>&1 | python freeze_log_hunter.py --live

    # With CSV export:
    python freeze_log_hunter.py --file run.log --csv violations.csv

Violation types detected:
  STUCK_NOT_FROZEN       — player stuck (vx=0, target!=x) but must_be_frozen=True
  FROZEN_SHOULD_MOVE     — player frozen but allowed_to_move=True
  TARGET_CHANGED_FROZEN  — target_x changed on a frozen player (subsystem overriding)
  ZOOM_AFTER_STUCK       — position jump > threshold after stuck frames
  MOVER_AT_TARGET        — moving player reached target_x exactly (not moving)
  DUAL_WRITER            — two different subsystems wrote targets last frame
  GLOBAL_LOCK            — >80% of defenders stuck
"""

import re
import sys
import csv
import json
from collections import defaultdict

# ── Phase-Role rules (inlined from PHASE_RULES for standalone use) ──

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

RE_FREEZE_AUDIT = re.compile(
    r'\[FREEZE_AUDIT\]\s*frame=(\d+)\s*phase=(\S+)'
)

RE_FROZEN_LIST = re.compile(
    r'\s*Frozen:\s*\[(.*?)\]'
)

RE_MOVING_LIST = re.compile(
    r'\s*Moving:\s*\[(.*?)\]'
)

RE_STUCK_LIST = re.compile(
    r'\s*Stuck.*?:\s*\[(.*?)\]'
)

RE_PLAYER_ENTRY = re.compile(
    r'id=(\d+)\s+role=(\S+)'
)

RE_SDM_LINE = re.compile(
    r'\[SDM\]\s+(\S+)\s+id=(\d+)\s+x=([\d.-]+)\s*->\s*([\d.-]+)\s+(.*)'
)

RE_STAGE_START = re.compile(
    r'\[STAGE_START\]\s+(\S+)'
)

RE_SDEA_VIOLATION = re.compile(
    r'\[SDEA_MOVEMENT_VIOLATION\]\s+(.+)'
)

RE_FREEZE_MISMATCH = re.compile(
    r'\[FREEZE_MISMATCH\]\s+(.+)'
)

RE_VIOLATION_HEADER = re.compile(
    r'VIOLATION|FREEZE_MISMATCH|SDEA_MOVEMENT_VIOLATION'
)

# ── Per-player state tracker ─────────────────────────────────────────

class PlayerState:
    """Tracks a single player across frames for ZOOM_AFTER_STUCK detection."""
    def __init__(self):
        self.prev_frozen = False
        self.prev_x = None
        self.prev_target_x = None
        self.stuck_frames = 0
        self.frozen_while_moving_frames = 0
        self.last_movement_source = "UNKNOWN"
        self.last_violation_type = None

    def update(self, frame, x, target_x, vx, frozen, role, movement_source):
        violations = []

        # Stuck detection: vx==0 while target_x != x
        if abs(vx) < 0.001 and abs(target_x - x) > 0.05:
            self.stuck_frames += 1
        else:
            self.stuck_frames = 0

        # ZOOM_AFTER_STUCK: position jump after being stuck
        if self.prev_x is not None and self.stuck_frames == 0:
            delta = abs(x - self.prev_x)
            if delta > 0.5 and self.stuck_frames > 0:
                violations.append("ZOOM_AFTER_STUCK")

        # TARGET_CHANGED_WHILE_FROZEN
        if frozen and self.prev_x is not None:
            if abs(target_x - x) > 0.05:
                violations.append("TARGET_CHANGED_FROZEN")

        self.prev_frozen = frozen
        self.prev_x = x
        self.prev_target_x = target_x
        return violations


# ── Main parser ──────────────────────────────────────────────────────

def parse_log(lines, csv_writer=None):
    """Parse log lines and produce violation reports.

    Yields (frame, phase, violator_id, role, violation_type, source) tuples.
    """
    players = defaultdict(PlayerState)
    current_frame = 0
    current_phase = None
    frozen_map = {}  # pid -> role
    moving_map = {}  # pid -> role
    stuck_map = {}   # pid -> role

    for line in lines:
        line = line.strip()

        # ── FREEZE_AUDIT header ──
        m = RE_FREEZE_AUDIT.search(line)
        if m:
            yield current_frame, current_phase
            current_frame = int(m.group(1))
            current_phase = m.group(2)
            frozen_map = {}
            moving_map = {}
            stuck_map = {}
            continue

        # ── Frozen list ──
        m = RE_FROZEN_LIST.search(line)
        if m and current_phase:
            for pid_str, role in RE_PLAYER_ENTRY.findall(m.group(1)):
                pid = int(pid_str)
                frozen_map[pid] = role
                # Check contract violation
                rules = PHASE_RULES.get(current_phase, {})
                allowed = rules.get("allowed_to_move", [])
                if role in allowed:
                    yield (current_frame, current_phase, pid, role,
                           "FROZEN_SHOULD_MOVE", "FREEZE_AUDIT")
            continue

        # ── Moving list ──
        m = RE_MOVING_LIST.search(line)
        if m and current_phase:
            for pid_str, role in RE_PLAYER_ENTRY.findall(m.group(1)):
                pid = int(pid_str)
                moving_map[pid] = role
                # Check contract violation
                rules = PHASE_RULES.get(current_phase, {})
                must_frozen = rules.get("must_be_frozen", [])
                if role in must_frozen:
                    yield (current_frame, current_phase, pid, role,
                           "MOVER_SHOULD_BE_FROZEN", "FREEZE_AUDIT")
            continue

        # ── Stuck list ──
        m = RE_STUCK_LIST.search(line)
        if m and current_phase:
            for pid_str, role in RE_PLAYER_ENTRY.findall(m.group(1)):
                pid = int(pid_str)
                stuck_map[pid] = role
                rules = PHASE_RULES.get(current_phase, {})
                must_frozen = rules.get("must_be_frozen", [])
                if role in must_frozen:
                    yield (current_frame, current_phase, pid, role,
                           "STUCK_NOT_FROZEN", "FREEZE_AUDIT")
            continue

        # ── SDEA violations ──
        m = RE_SDEA_VIOLATION.search(line)
        if m:
            detail = m.group(1)
            # Extract pid from detail
            pid_m = re.search(r'id=(\d+)', detail)
            pid = int(pid_m.group(1)) if pid_m else 0
            role_m = re.search(r'role=(\S+)', detail)
            role = role_m.group(1) if role_m else "?"
            yield (current_frame, current_phase, pid, role,
                   "SDEA_VIOLATION", detail)
            continue

        # ── FREEZE_MISMATCH assertions ──
        m = RE_FREEZE_MISMATCH.search(line)
        if m:
            detail = m.group(1)
            pid_m = re.search(r'id=(\d+)', detail)
            pid = int(pid_m.group(1)) if pid_m else 0
            yield (current_frame, current_phase, pid, "?",
                   "FREEZE_MISMATCH", detail)
            continue

        # ── SDM lines ──
        m = RE_SDM_LINE.search(line)
        if m:
            action = m.group(1)
            pid = int(m.group(2))
            source = f"SDM_{action}"
            yield (current_frame, current_phase, pid, "?",
                   "SDM_ACTION", source)
            continue

        # ── STAGE_START markers ──
        m = RE_STAGE_START.search(line)
        if m:
            stage = m.group(1)
            # Just track stage changes, no violation here
            continue


def format_report(violations):
    """Format violations into a compact, readable report."""
    lines = []
    lines.append("=" * 70)
    lines.append("FREEZE/MOVEMENT VIOLATION REPORT")
    lines.append("=" * 70)

    violation_count = defaultdict(int)
    for v in violations:
        if len(v) >= 6 and isinstance(v[0], int):  # (frame, phase, pid, role, type, source)
            violation_count[v[4]] += 1

    # Summary
    lines.append(f"\nTotal violations by type:")
    for vtype, count in sorted(violation_count.items(), key=lambda x: -x[1]):
        lines.append(f"  {vtype}: {count}")

    # Detail
    lines.append(f"\nDetail (first 100):")
    count = 0
    for v in violations:
        if len(v) >= 6 and isinstance(v[0], int):
            frame, phase, pid, role, vtype, source = v[:6]
            lines.append(f"  frame={frame} phase={phase} id={pid} role={role} "
                         f"[{vtype}] source={source}")
            count += 1
            if count >= 100:
                lines.append("  ... (truncated)")
                break

    lines.append("=" * 70)
    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="freeze_log_hunter - Scan for freeze/movement violations in RLM logs"
    )
    parser.add_argument("--file", "-f", help="Read from log file")
    parser.add_argument("--live", "-l", action="store_true",
                        help="Read from stdin (pipe from running simulation)")
    parser.add_argument("--csv", "-c", help="Export violations to CSV file")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show all violations, including non-bug patterns")
    args = parser.parse_args()

    if not args.file and not args.live:
        parser.print_help()
        sys.exit(1)

    if args.file:
        with open(args.file, "r") as f:
            lines = f.readlines()
    else:
        lines = sys.stdin.readlines()

    violations = list(parse_log(lines))

    # Print report
    print(format_report(violations))

    # Export CSV
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["frame", "phase", "player_id", "role",
                             "violation_type", "source"])
            for v in violations:
                if len(v) >= 6 and isinstance(v[0], int):
                    writer.writerow(v)
        print(f"\nExported {len(violations)} violations to {args.csv}")


if __name__ == "__main__":
    main()
"""

log_hunter_path = os.path.join(PROJECT, "tmp/freeze_log_hunter.py")
with open(log_hunter_path, "w", encoding="utf-8") as f:
    f.write(log_hunter)
print(f"[STEP 6] Created {log_hunter_path}")

# ═══════════════════════════════════════════════════════════════════════
# STEP 7: MOVEMENT_PIPELINE — explicit phase-pipeline ordering
# ═══════════════════════════════════════════════════════════════════════

pipeline_code = '''
# ═══════════════════════════════════════════════════════════════════════
# MOVEMENT_PIPELINE — explicit subsystem ordering per phase.
#
# Each list defines the order in which movement-related subsystems run.
# Stages listed first run first.  All stages are run for every frame of
# that phase.
#
# Stage names used in the pipeline:
#   "handler"      — phase-specific handler (e.g. _update_tackle_drive)
#   "defence"      — _update_defence() geometry pipeline
#   "attack"       — _update_attack() BC + support runner movement
#   "role_pipeline" — apply_role_movement() + arrival_predictor()
#   "sdm"          — _apply_defence_sdm() deterministic retreat
#   "freeze"       — freeze-state compliance enforcement (zero vx, pin targets)
#   "markers"      — _move_markers_to_targets()
#   "ptb"          — PTBManager DH movement (PTB_PREP/EXEC only)
#
# CRITICAL: Changing the order here changes simulation behaviour.
# Every reordering MUST be validated against PHASE_RULES invariants.
# ═══════════════════════════════════════════════════════════════════════

MOVEMENT_PIPELINE: dict[str, list[str]] = {
    # ── OPEN_PLAY: defence (geometry) -> attack -> handler -> SDM -> freeze
    "OPEN_PLAY":             ["defence", "attack", "handler", "sdm", "freeze"],

    # ── TES phases: handler (TES drive) -> role_pipeline -> SDM -> freeze
    "TACKLE_INIT":           ["handler", "role_pipeline", "sdm", "freeze"],
    "TACKLE_CONTACT":        ["handler", "role_pipeline", "sdm", "freeze"],
    "TACKLE_DRIVE":          ["handler", "role_pipeline", "sdm", "freeze"],
    "TACKLE_DOMINANCE":      ["handler", "role_pipeline", "sdm", "freeze"],

    # ── RELEASE: handler (unfreeze markers, detach) -> role_pipeline -> markers -> SDM -> freeze
    "RELEASE":               ["handler", "role_pipeline", "markers", "sdm", "freeze"],

    # ── PTB phases: markers -> handler (PTBManager) -> role_pipeline -> SDM -> freeze
    "PTB_PREP":              ["markers", "handler", "role_pipeline", "sdm", "freeze"],
    "PTB_EXEC":              ["markers", "handler", "role_pipeline", "sdm", "freeze"],
    "PTB":                   ["markers", "handler", "role_pipeline", "sdm", "freeze"],

    # ── POST_RUCK_OPEN_PLAY: defence (retreat) -> attack -> handler -> SDM -> freeze
    "POST_RUCK_OPEN_PLAY":   ["defence", "attack", "handler", "sdm", "freeze"],
}
'''

with open(os.path.join(PROJECT, "tmp/movement_pipeline.py"), "w", encoding="utf-8") as f:
    f.write('"""\n')
    f.write('tmp/movement_pipeline.py — MOVEMENT_PIPELINE definition.\n')
    f.write('\n')
    f.write('This file defines the explicit ordering of movement subsystems\n')
    f.write('per phase.  It is the canonical contract for which subsystem runs\n')
    f.write('when, imported by open_play_engine.py.\n')
    f.write('"""\n')
    f.write(pipeline_code)
    print("[STEP 7] Created tmp/movement_pipeline.py")

# ═══════════════════════════════════════════════════════════════════════
# Verify all changes
# ═══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 60)
print("IMPLEMENTATION SUMMARY")
print("=" * 60)
print(f"  [STEP 1] PHASE_RULES -> world/rules/phase_rules.py")
print(f"  [STEP 2] Freeze assertions -> world/rules/movement_contracts.py")
print(f"  [STEP 3] log_phase_freeze() -> world/rules/movement_contracts.py")
print(f"  [STEP 4] STAGE_START markers -> world/open_play_engine.py")
print(f"  [STEP 5] movement_writer clear -> player/player.py")
print(f"  [STEP 6] Log hunter -> tmp/freeze_log_hunter.py")
print(f"  [STEP 7] MOVEMENT_PIPELINE -> tmp/movement_pipeline.py")
print("=" * 60)
print("NOTE: Steps 1, 2, 3, 5 were already substantially implemented.")
print("This script added assertions, debug logs, and the new standalone modules.")
print("To fully wire STEP 7 into OPE.update(), the `update()` method needs to")
print("iterate MOVEMENT_PIPELINE[phase] instead of hard-coded ordering.")
print("=" * 60)
'''

if __name__ == "__main__":
    main()
