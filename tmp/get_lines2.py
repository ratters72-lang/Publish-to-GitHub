with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Print lines 2395-2470
for i in range(2395, min(2470, len(lines))):
    print(f"{i}:{lines[i]}", end='')
