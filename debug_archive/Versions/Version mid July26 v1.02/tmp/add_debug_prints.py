"""Surgically add debug prints to open_play_engine.py."""
import re

path = "world/open_play_engine.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add debug print after the docstring of assign_defence_targets()
# Find the docstring closure """ at end of assign_defence_targets docstring
old = '''        - Scramble: if BC is past the retreat line, nearest 2 defenders sprint to BC.
        """
        if not defending_team:'''
new = '''        - Scramble: if BC is past the retreat line, nearest 2 defenders sprint to BC.
        """
        print("[DEBUG] assign_defence_targets running")
        if not defending_team:'''
content = content.replace(old, new)

# 2. Add debug print at start of MOVE block in _update_open_play
old = '''        # ── MOVE: BC in possession, movement + AI decisions ──
        if int_state == "move":
            if bc is None or bc.state != PlayerState.BALL_CARRIER:'''
new = '''        # ── MOVE: BC in possession, movement + AI decisions ──
        if int_state == "move":
            print("[DEBUG] MOVE: _update_open_play assigning targets")
            if bc is None or bc.state != PlayerState.BALL_CARRIER:'''
content = content.replace(old, new)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("DEBUG prints added successfully")

# Verify
with open(path, "r", encoding="utf-8") as f:
    c = f.read()
for line in c.splitlines():
    if "DEBUG" in line:
        print(f"  Found: {line.strip()}")
