with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

targets = ['_update_ptb_prep', '_update_ptb_exec', '_update_ptb']
for name in targets:
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith(f'def {name}('):
            print(f"\n=== {name} (line {i}) ===")
            for j in range(i-1, min(i+15, len(lines))):
                r = lines[j].rstrip()
                m = ' <<<' if 'attack_role' in r else ''
                print(f"  {j+1:4d}: {r}{m}")
            break
