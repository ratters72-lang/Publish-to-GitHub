with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

sections = [
    ("init __init__", 115, 140),
    ("start() method", 340, 370),
    ("anchor compute in update()", 485, 510),
    ("LAW 5 block", 555, 575),
    ("_reset_tackle_state", 1795, 1830),
    ("_compute_defensive_anchor", 1859, 1876),
    ("_start_tackle", 680, 720),
]

for name, start, end in sections:
    print(f"\n{'='*60}")
    print(f"=== {name} (lines {start}-{end}) ===")
    print(f"{'='*60}")
    for i in range(start-1, min(end, len(lines))):
        print(f"{i+1}: {lines[i]}", end='')
