with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines, 1):
    if 'FREEZE-STATE COMPLIANCE ENFORCEMENT' in line:
        print(f"--- FREEZE-STATE --- lines {i-3} to {i+8} ---")
        for j in range(max(1,i-3), min(len(lines)+1, i+8)):
            print(f"{j}:{lines[j-1]}", end='')
    if 'handler = phase_map.get' in line:
        print(f"\n--- PHASE MAP --- line {i-2} to {i+4} ---")
        for j in range(max(1,i-2), min(len(lines)+1, i+5)):
            print(f"{j}:{lines[j-1]}", end='')
    if 'SDEA phase invariants' in line or 'SDEA movement audit' in line:
        print(f"\n--- SDEA --- lines {i-2} to {i+3} ---")
        for j in range(max(1,i-2), min(len(lines)+1, i+4)):
            print(f"{j}:{lines[j-1]}", end='')
    if '_apply_defence_sdm' in line:
        print(f"\n--- SDM --- line {i}")
        print(f"{i}:{lines[i-1]}", end='')
