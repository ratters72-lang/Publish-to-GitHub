with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

start = None
for i, line in enumerate(lines):
    if 'def _update_post_ruck_open_play' in line:
        start = i
        break

if start:
    for i in range(start, min(start + 90, len(lines))):
        print(f"{i:5d}:{lines[i]}", end='')
