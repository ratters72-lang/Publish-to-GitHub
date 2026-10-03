with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines, 1):
    if i >= 605 and i <= 635:
        print(f"{i}:{lines[i-1]}", end='')
