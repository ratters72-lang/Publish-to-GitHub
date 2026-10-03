with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Show lines around 1111
for i in range(1100, 1120):
    if i <= len(lines):
        print(f"{i}: {lines[i-1].rstrip()}")
