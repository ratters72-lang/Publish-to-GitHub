with open('world/open_play_engine.py','r',encoding='utf-8') as f:
    lines = f.readlines()

# Show the 7 patched areas
checks = {
    "1. __init__ tes sets": (185, 190),
    "2. _clear_tes_artefacts clear": (406, 412),
    "3. _update_defenders_tes_phases filter": (982, 988),
    "4. _handle_broken_tackle clear": (1119, 1123),
    "5. _start_tackle populate": (1201, 1207),
    "6. _prepare_release clear": (1564, 1570),
    "7. _reset_tackle_state clear": (2425, 2429),
}

for label, (start, end) in checks.items():
    print(f"\n=== {label} (lines {start}-{end}) ===")
    for i in range(start-1, min(end, len(lines))):
        print(f"{i+1:4}: {lines[i]}", end='')
