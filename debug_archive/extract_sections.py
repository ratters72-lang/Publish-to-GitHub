with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Print lines for the key methods
sections = [
    ("_prepare_release (line 1203-1310)", 1203, 1310),
    ("_update_release (line 1310-1399)", 1310, 1399),
    ("_update_post_ptb_reset (line 1633-1860)", 1633, 1860),
    ("_compute_defensive_anchor (line 1859-1920)", 1859, 1920),
    ("_support_lane_y (line 2084-2105)", 2084, 2105),
]

for name, start, end in sections:
    print(f"\n{'='*60}")
    print(f"=== {name} ===")
    print(f"{'='*60}")
    for i in range(start-1, min(end, len(lines))):
        print(f"{i+1}: {lines[i]}", end='')
