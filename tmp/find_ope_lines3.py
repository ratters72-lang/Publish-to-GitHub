import sys
with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i, line in enumerate(lines, 1):
    if 610 <= i <= 640:
        sys.stdout.write(f"{i}:{lines[i-1]}")
