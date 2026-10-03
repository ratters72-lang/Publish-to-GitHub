with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Show the first ~15 lines of each remaining method body
methods = [
    (1927, '_update_ptb_prep'),
    (1996, '_update_ptb_exec'),
    (2107, '_update_ptb'),
]

for line_num, name in methods:
    print(f"\n{'='*60}")
    print(f"=== {name} (line {line_num}) ===")
    print(f"{'='*60}")
    start = line_num - 1
    for i in range(start, min(start + 25, len(lines))):
        r = lines[i]
        print(f"{i+1:5d}: {r}", end='')
