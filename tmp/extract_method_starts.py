with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Methods to inspect: line numbers and how many lines to show
methods = [
    (1537, '_prepare_release', 30),
    (1738, '_update_release', 30),
    (1927, '_update_ptb_prep', 30),
    (1996, '_update_ptb_exec', 30),
    (2107, '_update_ptb', 30),
]

for line_num, name, count in methods:
    print(f"\n{'='*60}")
    print(f"=== {name} (line {line_num}) ===")
    print(f"{'='*60}")
    start = line_num - 1  # 0-indexed
    for i in range(start, min(start + count, len(lines))):
        print(f"{i+1:5d}: {lines[i]}", end='')
