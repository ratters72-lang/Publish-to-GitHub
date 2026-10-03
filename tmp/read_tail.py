with open('world/open_play_engine.py', 'r') as f:
    lines = f.readlines()
total = len(lines)

# Print lines 1500 to end (these cover _update_post_ruck_open_play and _reset_tackle_state)
start = max(0, 1700)
for i in range(start, total):
    print(f'{i+1}:{lines[i]}', end='')
