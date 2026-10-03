"""Trace ball_carrier writes through PTB lifecycle."""
import re

with open('world/open_play_engine.py') as f:
    content = f.read()

# Find all ball_carrier writes and reads
lines = content.split('\n')
for i, line in enumerate(lines, 1):
    l = line.strip()
    if 'ball_carrier' in l and ('=' in l or 'set_ball_carrier' in l):
        print(f"L{i:4d}: WRITE: {l}")
    elif 'ball_carrier' in l and l.startswith('if ') and 'is not None' in l:
        print(f"L{i:4d}: CHECK: {l}")
    elif 'ball_carrier' in l and l.startswith('if ') and 'is None' in l:
        print(f"L{i:4d}: CHECK: {l}")
