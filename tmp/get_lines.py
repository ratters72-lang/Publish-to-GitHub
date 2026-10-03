with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Print lines 2390-2470
for i in range(2390, min(2470, len(lines))):
    print(f"{i}:{lines[i]}", end='')
