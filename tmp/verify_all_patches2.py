with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Search for ALL occurrences of bc-related role assignment contexts
targets = ['_prepare_release', '_update_release', '_update_ptb_prep', '_update_ptb_exec', '_update_ptb', '_update_open_play']

for name in targets:
    found = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith(f'def {name}('):
            # Show the method header and next 20 lines
            print(f"\n=== {name} (line {i}) ===")
            for j in range(i-1, min(i+22, len(lines))):
                r = lines[j].rstrip()
                marker = " <<<" if 'attack_role' in r else ""
                print(f"  {j+1:4d}: {r}{marker}")
            found = True
            break
    if not found:
        print(f"\n=== {name} NOT FOUND ===")
