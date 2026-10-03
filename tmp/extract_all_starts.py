with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Show more lines for the remaining methods (need to see the body after docstring)
methods = [
    (1738, '_update_release', 50),
    (1927, '_update_ptb_prep', 40),
    (1996, '_update_ptb_exec', 40),
    (2107, '_update_ptb', 40),
]

for line_num, name, count in methods:
    print(f"\n{'='*60}")
    print(f"=== {name} (line {line_num}) ===")
    print(f"{'='*60}")
    start = line_num - 1
    for i in range(start, min(start + count, len(lines))):
        print(f"{i+1:5d}: {lines[i]}", end='')
