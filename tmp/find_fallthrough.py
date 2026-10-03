with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if 'allthrough' in line:
        print(f'{i}: {line.rstrip()}')
# Also search for 'Fallthrough' with capital F
for i, line in enumerate(lines):
    if 'Fallthrough' in line:
        print(f'{i}: {line.rstrip()}')
