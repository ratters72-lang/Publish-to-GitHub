with open('world/open_play_engine.py', 'r') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if 'PTB_PREP' in line or 'PTB_EXEC' in line:
        print(f'Line {i+1}: {line.rstrip()[:200]}')
    if '_set_phase' in line and 'PTB' in line:
        print(f'Line {i+1}: {line.rstrip()[:200]}')
    if 'GameSubState.' in line and 'PTB' in line:
        print(f'Line {i+1}: {line.rstrip()[:200]}')
