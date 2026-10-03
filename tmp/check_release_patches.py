with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Check _prepare_release around line 1560-1566
print("=== _prepare_release (insert after return guard) ===")
for i in range(1558, min(1568, len(lines))):
    r = lines[i].rstrip()
    m = ' <<<' if 'attack_role' in r else ''
    print(f"{i+1:4d}: {r}{m}")

# Check _update_release around line 1750-1756
print("\n=== _update_release (insert after docstring) ===")
for i in range(1749, min(1758, len(lines))):
    r = lines[i].rstrip()
    m = ' <<<' if 'attack_role' in r else ''
    print(f"{i+1:4d}: {r}{m}")
