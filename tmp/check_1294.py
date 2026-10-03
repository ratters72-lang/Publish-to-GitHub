with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i in range(1285, 1315):
    if i <= len(lines):
        print(f"{i}: {repr(lines[i-1].rstrip())}")
