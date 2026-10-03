with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if i >= 1629 and i < 1680:
        print(f'{i:5d}: {line.rstrip()}')
