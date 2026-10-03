with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i in range(1082, 1100):
    print(f'{i}: {lines[i-1].rstrip()}')  # line numbers are 1-indexed
