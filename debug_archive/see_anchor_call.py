with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i in range(418, 450):
    print(f"{i+1}: {lines[i]}", end='')
