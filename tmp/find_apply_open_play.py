with open('game/defence/defence_lvl_1/defensive_behaviour_unified.py') as f:
    lines = f.readlines()
for i, line in enumerate(lines, 1):
    if 'def _apply_open_play_unified' in line:
        print(f"Line {i}: {line.rstrip()}")
        # Print next 30 lines
        for j in range(i, min(i+30, len(lines)+1)):
            print(f"  {j}: {lines[j-1].rstrip()}")
