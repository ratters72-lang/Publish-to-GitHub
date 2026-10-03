with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

print(f"Total lines: {len(lines)}")

# Find all calls to _enforce_attack_directions
for i, line in enumerate(lines):
    if '_enforce_attack_directions' in line:
        print(f"{i+1}: {lines[i]}", end='')
    
    if '_enforce_defence_directions' in line:
        print(f"{i+1}: {lines[i]}", end='')
