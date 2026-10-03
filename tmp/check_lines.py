with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the function start
for i, line in enumerate(lines):
    if 'def _update_defenders_tes_phases' in line:
        print(f"Function at line {i+1}")
        for j in range(i, min(i+10, len(lines))):
            print(f"{j+1}: {lines[j].rstrip()}")
        break
