with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

capture = False
captured = []
for i, line in enumerate(lines):
    if 'def _update_post_ruck_open_play' in line:
        capture = True
    if capture:
        captured.append(line)
    # Stop at next def at class level (4-space indent)
    if capture and len(captured) > 1 and line.startswith('    def ') and 'update_post_ruck' not in line:
        # Remove this "next def" line from capture
        captured.pop()
        break

print('FULL METHOD:')
print(f'({len(captured)} lines)')
print(''.join(captured))
