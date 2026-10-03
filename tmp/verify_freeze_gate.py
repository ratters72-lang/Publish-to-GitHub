with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the marker freeze loop section
for i, line in enumerate(lines):
    if 'marker_freeze_timer' in line and 'MARKER_A' in line:
        print(f"=== Freeze gate starting at line {i+1} ===")
        for j in range(i, min(i+30, len(lines))):
            print(f'{j+1:4d}: {lines[j]}', end='')
        print()
        break

# Check if the else clause has correct values
for i, line in enumerate(lines):
    if 'maker_freeze_timer' in line and 'MARKER_A' in line:
        pass  # not needed

# Search for the else clause specifically
for i, line in enumerate(lines):
    stripped = line.strip()
    if stripped == 'else:':
        # Check if next lines have apply_physics_forces = True
        if i+1 < len(lines) and 'apply_physics_forces' in lines[i+1]:
            print(f"\n=== Found else clause at line {i+1} ===")
            for j in range(i, min(i+8, len(lines))):
                print(f'{j+1:4d}: {lines[j]}', end='')
            print()

# Check for duplicate retreat blocks
print("\n=== Searching for duplicate retreat blocks ===")
count = 0
for i, line in enumerate(lines):
    if 'RETREAT ENFORCEMENT' in line:
        count += 1
        print(f"Found 'RETREAT ENFORCEMENT' at line {i+1}")

print(f"\nTotal duplicate retreat blocks: {count}")
