with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
in_target = False
count = 0
for i, line in enumerate(lines):
    if 'RELEASE FREEZE ENFORCEMENT' in line:
        in_target = True
    if in_target:
        print(f'{i:5d}: {line.rstrip()}')
        count += 1
        if count >= 30:
            break
