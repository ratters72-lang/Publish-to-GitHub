with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
in_method = False
for i, line in enumerate(lines):
    if 'def _update_tackle_drive' in line:
        in_method = True
    if in_method:
        print(f'{i:5d}: {line.rstrip()}')
    if in_method and i > 1176:
        break
