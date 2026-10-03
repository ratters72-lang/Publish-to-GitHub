"""Find ALL ball_carrier writes in OPE."""
with open('world/open_play_engine.py', encoding='utf-8') as f:
    lines = f.readlines()
for i, line in enumerate(lines, 1):
    l = line.strip()
    if 'world.ball_carrier' in l and '=' in l:
        print(f"L{i}: WRITE world.ball_carrier: {l}")
    if 'ptb_manager.' in l and ('bc' in l or 'ball_carrier' in l):
        print(f"L{i}: PTBMANAGER: {l}")
    if 'set_ball_carrier' in l:
        print(f"L{i}: SET_BC: {l}")
    if 'self.ball_carrier' in l and '=' in l:
        print(f"L{i}: WRITE self.ball_carrier (OPE internal): {l}")
