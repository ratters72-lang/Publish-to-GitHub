with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find _update_post_ruck_open_play start line
start_idx = None
for i, line in enumerate(lines):
    if 'def _update_post_ruck_open_play' in line:
        start_idx = i
        break

# Print from that line
if start_idx:
    for i in range(start_idx, min(start_idx + 100, len(lines))):
        print(f"{i}:{lines[i]}", end='')
else:
    print("NOT FOUND")
