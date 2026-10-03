"""Check if _apply_open_play_unified has the override call."""
import re

with open("game/defence/defence_lvl_1/defensive_behaviour_unified.py") as f:
    content = f.read()

# Find _force_immediate_defence_reaction calls
for i, line in enumerate(content.split('\n'), 1):
    if 'force_immediate' in line:
        print(f"Line {i}: {line.strip()}")
