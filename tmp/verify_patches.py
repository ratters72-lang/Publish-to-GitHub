with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the 5 methods again to check inserted code
methods = {}
for i, line in enumerate(lines, 1):
    stripped = line.strip()
    for name in ['_prepare_release', '_update_release', '_update_ptb_prep', '_update_ptb_exec', '_update_ptb']:
        if stripped.startswith(f'def {name}('):
            methods[name] = i

for name, start in sorted(methods.items(), key=lambda x: x[1]):
    print(f"\n{'='*60}")
    print(f"=== {name} (line {start}) - checking inserted code ===")
    # Show ~15 lines starting from method def
    for i in range(start-1, min(start+20, len(lines))):
        print(f"{i+1:5d}: {lines[i]}", end='')
