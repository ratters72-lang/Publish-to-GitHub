with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

capture = False
captured = []
depth = None
for i, line in enumerate(lines):
    if 'def _update_post_ruck_open_play' in line:
        capture = True
        depth = len(line) - len(line.lstrip())
    if capture:
        captured.append(f"{i:5d}: {line}")
    # Stop at next def at same indentation level or less
    if capture and len(captured) > 1 and not line.startswith(' ') and 'def ' in line and 'update_post_ruck' not in line:
        break
    if capture and len(captured) > 1 and line.startswith('class '):
        break

print(''.join(captured[:200]))
