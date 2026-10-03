with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i in range(459, 512):
    print(f"{i+1}: {lines[i]}", end='')
