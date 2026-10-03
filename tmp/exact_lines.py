with open('game/defence/defence_lvl_1/defensive_behaviour_unified.py') as f:
    lines = f.readlines()
for i in range(1134, 1152):
    print(f"L{i:4d}: {repr(lines[i-1])}")
