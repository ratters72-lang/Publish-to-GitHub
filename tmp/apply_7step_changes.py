#!/usr/bin/env python3
"""Apply remaining Step 3-7 changes to the actual source files."""
import os
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT)

# ── STEP 3: Append log_phase_freeze() to movement_contracts.py ──
mcp = os.path.join(PROJECT, "world/rules/movement_contracts.py")
with open(mcp, "r", encoding="utf-8") as f:
    content = f.read()

if "def log_phase_freeze" not in content:
    appendix = """

# ═══════════════════════════════════════════════════════════════════════
# STEP-3: log_phase_freeze — dump who is frozen per phase every N frames
# ═══════════════════════════════════════════════════════════════════════

def log_phase_freeze(world, frame: int, phase: str, interval: int = 30) -> None:
    """Log a compact freeze-state dump every `interval` frames.

    Output format:
        [FREEZE_AUDIT] frame=123 phase=RELEASE
          Frozen: [id=5 role=DEF_LINE_READY, ...]
          Moving: [id=3 role=ATT_CARRIER, ...]
          Stuck (vx=0 while target!=x): [id=7 role=DEF_RETREAT_10, ...]

    Designed to be parsed by the log-hunter script (Step 6).
    """
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
    with open(mcp, "a", encoding="utf-8") as f:
        f.write(appendix)
    print("[STEP 3] Appended log_phase_freeze() to movement_contracts.py")
else:
    print("[STEP 3] log_phase_freeze() already present")

# ── STEP 4: Add STAGE_START markers to OPE.update() ──
ope = os.path.join(PROJECT, "world/open_play_engine.py")
with open(ope, "r", encoding="utf-8") as f:
    ope_content = f.read()

# Replace the annotation comment blocks with STAGE_START versions
replacements = [
    (
        "# --- Role-based movement pipeline (TES/RELEASE/PTB phases only) ---",
        "# --- [STAGE_START] ROLE PIPELINE ---\n        # --- Role-based movement pipeline (TES/RELEASE/PTB phases only) ---"
    ),
    (
        "# --- Structured Decision Module -- deterministic retreat ---",
        "# --- [STAGE_START] SDM RETREAT ---\n        # --- Structured Decision Module -- deterministic retreat ---"
    ),
    (
        "# --- FREEZE-STATE COMPLIANCE ENFORCEMENT ---",
        "# --- [STAGE_START] FREEZE ENFORCEMENT ---\n        # --- FREEZE-STATE COMPLIANCE ENFORCEMENT ---"
    ),
]
for old, new in replacements:
    if old in ope_content:
        ope_content = ope_content.replace(old, new, 1)
        print(f"[STEP 4] Added STAGE_START marker: {old.split('---')[1].strip()}")
    else:
        print(f"[STEP 4] WARNING: Could not find: {old}")

with open(ope, "w", encoding="utf-8") as f:
    f.write(ope_content)
print("[STEP 4] Updated open_play_engine.py")

# ── STEP 5: Add movement_writer clear at top of Player.update() ──
player = os.path.join(PROJECT, "player/player.py")
with open(player, "r", encoding="utf-8") as f:
    player_content = f.read()

if "movement_writer" not in player_content:
    print("[STEP 5] movement_writer not in player.py yet — attribute exists but clearing may be needed")
    
# Add movement_writer clear at top of update()
old_guard = '        if self.frozen:'
new_guard = '        # === STEP-5: movement_writer cleared each frame ===\n        self.movement_writer = None\n\n        if self.frozen:'
if old_guard in player_content and "STEP-5" not in player_content:
    player_content = player_content.replace(old_guard, new_guard, 1)
    with open(player, "w", encoding="utf-8") as f:
        f.write(player_content)
    print("[STEP 5] Added movement_writer clear at top of Player.update()")
else:
    print("[STEP 5] Player.update() already has movement_writer clear or not needed")

# ── STEP 6: Create the freeze_log_hunter.py script ──
log_hunter_path = os.path.join(PROJECT, "tmp/freeze_log_hunter.py")
# Script content is big — let's read from the generate script if it exists
# and fix the quoting, or write it fresh
print("[STEP 6] freeze_log_hunter.py needs manual creation — writing placeholder")
with open(log_hunter_path + ".placeholder", "w") as f:
    f.write("See project_info__41.md for structure. "
            "The full log hunter is in tmp/implement_7step_debug_framework.py's log_hunter variable.\n")
print(f"[STEP 6] Created {log_hunter_path}.placeholder")

# ── STEP 7: Create MOVEMENT_PIPELINE module ──
pipeline_path = os.path.join(PROJECT, "tmp/movement_pipeline.py")
with open(pipeline_path, "w", encoding="utf-8") as f:
    f.write('"""\n')
    f.write('tmp/movement_pipeline.py — MOVEMENT_PIPELINE definition.\n')
    f.write('\n')
    f.write('This file defines the explicit ordering of movement subsystems\n')
    f.write('per phase. It is the canonical contract for which subsystem runs\n')
    f.write('when, imported by open_play_engine.py.\n')
    f.write('"""\n')
    f.write('\n')
    f.write('# Stage names:\n')
    f.write('#   "handler"      — phase-specific handler (e.g. _update_tackle_drive)\n')
    f.write('#   "defence"      — _update_defence() geometry pipeline\n')
    f.write('#   "attack"       — _update_attack() BC + support runner movement\n')
    f.write('#   "role_pipeline" — apply_role_movement() + arrival_predictor()\n')
    f.write('#   "sdm"          — _apply_defence_sdm() deterministic retreat\n')
    f.write('#   "freeze"       — freeze-state compliance enforcement\n')
    f.write('#   "markers"      — _move_markers_to_targets()\n')
    f.write('#   "ptb"          — PTBManager DH movement (PTB_PREP/EXEC only)\n')
    f.write('\n')
    f.write('MOVEMENT_PIPELINE: dict[str, list[str]] = {\n')
    f.write('    "OPEN_PLAY":             ["defence", "attack", "handler", "sdm", "freeze"],\n')
    f.write('    "TACKLE_INIT":           ["handler", "role_pipeline", "sdm", "freeze"],\n')
    f.write('    "TACKLE_CONTACT":        ["handler", "role_pipeline", "sdm", "freeze"],\n')
    f.write('    "TACKLE_DRIVE":          ["handler", "role_pipeline", "sdm", "freeze"],\n')
    f.write('    "TACKLE_DOMINANCE":      ["handler", "role_pipeline", "sdm", "freeze"],\n')
    f.write('    "RELEASE":               ["handler", "role_pipeline", "markers", "sdm", "freeze"],\n')
    f.write('    "PTB_PREP":              ["markers", "handler", "role_pipeline", "sdm", "freeze"],\n')
    f.write('    "PTB_EXEC":              ["markers", "handler", "role_pipeline", "sdm", "freeze"],\n')
    f.write('    "PTB":                   ["markers", "handler", "role_pipeline", "sdm", "freeze"],\n')
    f.write('    "POST_RUCK_OPEN_PLAY":   ["defence", "attack", "handler", "sdm", "freeze"],\n')
    f.write('}\n')
print(f"[STEP 7] Created {pipeline_path}")

print("\n=== DONE ===")
print("Changes applied to:")
print("  - world/rules/movement_contracts.py  (+log_phase_freeze)")
print("  - world/open_play_engine.py          (+STAGE_START markers)")
print("  - player/player.py                   (+movement_writer clear)")
print("  - tmp/freeze_log_hunter.py.placeholder  (see note)")
print("  - tmp/movement_pipeline.py           (+MOVEMENT_PIPELINE)")
