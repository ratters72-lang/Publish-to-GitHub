with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines, 1):
    if 'defender' in line and ('defender.y' in line or 'defender.x' in line or 'defender.vx' in line or 'defender.vy' in line or 'defender.player_id' in line):
        print(f"Line {i}: {line.rstrip()}")
