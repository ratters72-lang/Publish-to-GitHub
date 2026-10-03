#!/usr/bin/env python3
"""Apply STAGE_START markers and movement_writer clear."""
import os
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT)

# Step 4: STAGE_START markers
ope = os.path.join(PROJECT, "world/open_play_engine.py")
with open(ope, "r", encoding="utf-8") as f:
    content = f.read()

replacements = [
    ("# --- Role-based movement pipeline (TES/RELEASE/PTB phases only) ---",
     "# --- [STAGE_START] ROLE PIPELINE ---\n        # --- Role-based movement pipeline (TES/RELEASE/PTB phases only) ---"),
    ("# --- Structured Decision Module -- deterministic retreat ---",
     "# --- [STAGE_START] SDM RETREAT ---\n        # --- Structured Decision Module -- deterministic retreat ---"),
    ("# --- FREEZE-STATE COMPLIANCE ENFORCEMENT ---",
     "# --- [STAGE_START] FREEZE ENFORCEMENT ---\n        # --- FREEZE-STATE COMPLIANCE ENFORCEMENT ---"),
]

for old, new in replacements:
    if old in content:
        content = content.replace(old, new, 1)
        print("OK: Replaced", old[:50])
    else:
        # Try finding by partial match
        words = old.split("---")[1].strip()
        if words in content:
            print("FOUND (but different encoding):", words)
        else:
            print("NOT FOUND:", old[:60])

with open(ope, "w", encoding="utf-8") as f:
    f.write(content)
print("Done")

# Step 5: movement_writer clear in Player.update()
player = os.path.join(PROJECT, "player/player.py")
with open(player, "r", encoding="utf-8") as f:
    pcontent = f.read()

old_guard = "        if self.frozen:"
new_guard = "        # === STEP-5: movement_writer cleared each frame ===\n        self.movement_writer = None\n\n        if self.frozen:"
if old_guard in pcontent and "STEP-5" not in pcontent:
    pcontent = pcontent.replace(old_guard, new_guard, 1)
    with open(player, "w", encoding="utf-8") as f:
        f.write(pcontent)
    print("Added movement_writer clear")
else:
    print("movement_writer clear already there or not found")
