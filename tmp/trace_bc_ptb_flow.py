"""Trace BC ownership flow through PTB phases."""
with open('world/open_play_engine.py', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines, 1):
    l = line.strip()
    if 'world.ball_carrier' in l and '=' in l:
        print(f"L{i}: WRITE world.ball_carrier: {l}")
    if 'self.ball_carrier' in l and '=' in l:
        print(f"L{i}: WRITE self.ball_carrier:   {l}")
    if 'set_ball_carrier' in l:
        print(f"L{i}: SET_BC:                   {l}")
    if 'ball_owner' in l and '=' in l:
        print(f"L{i}: BALL_OWNER:               {l}")
    if 'ptb_manager.complete' in l:
        print(f"L{i}: PTB_COMPLETE:             {l}")
