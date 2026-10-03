with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
total = len(lines)
start = max(0, 1700)
for i in range(start, total):
    print(f'{i+1}:{lines[i]}', end='')
