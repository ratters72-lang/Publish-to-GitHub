with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

ranges = [
    (1537, 1570),   # _prepare_release
    (1738, 1760),   # _update_release
    (1927, 1960),   # _update_ptb_prep
    (1996, 2030),   # _update_ptb_exec
    (2107, 2140),   # _update_ptb
]

for start, end in ranges:
    print(f"\n{'='*60}")
    for i in range(start-1, min(end, len(lines))):
        print(f"{i+1:5d}: {lines[i]}", end='')
