with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find _update_post_ruck_open_play start
capture = False
captured = []
for i, line in enumerate(lines):
    if 'def _update_post_ruck_open_play' in line:
        capture = True
    if capture:
        captured.append(f"{i:5d}: {line}")
    # Stop at next def at same level (no indent)
    if capture and len(captured) > 1 and line.startswith('    def ') and 'update_post_ruck' not in line:
        break
    if capture and len(captured) > 1 and line.strip().startswith('def ') and 'update_post_ruck' not in line:
        break

print(''.join(captured[:150]))
