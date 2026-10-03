with open('game/defence/defence_lvl_1/defensive_behaviour_unified.py') as f:
    lines = f.readlines()
# The _apply_open_play_unified method starts at line 1110
# Its BC-none guard is at line 1136 according to earlier output
# Let's find the exact line numbers
in_method = False
for i, line in enumerate(lines, 1):
    if 'def _apply_open_play_unified' in line:
        in_method = True
        print(f"=== Method starts at line {i} ===")
    if in_method and i >= 1130 and i <= 1150:
        print(f"L{i:4d}: {line.rstrip()}")
