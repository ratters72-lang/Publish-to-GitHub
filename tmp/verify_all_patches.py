with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Check each patch by looking for "attack_role = "ball_carrier"" occurrences
import re
matches = []
for i, line in enumerate(lines, 1):
    if 'attack_role' in line and 'ball_carrier' in line:
        # Show context
        start = max(0, i - 4)
        context = ''.join(lines[start:i+2])
        print(f"--- Line {i} ---")
        print(context.strip())
        print()
