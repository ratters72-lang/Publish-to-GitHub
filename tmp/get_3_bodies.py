with open('world/open_play_engine.py','r',encoding='utf-8') as f:
    lines = f.readlines()

# _prepare_release starts at line 1538 (0-indexed 1537)
# Show lines 1537 to 1565
print("=== _prepare_release body start ===")
for i in range(1537, min(1565, len(lines))):
    print(f"{i+1:4}: {lines[i]}", end='')

# _update_ptb_prep starts at line 1932 (0-indexed 1931)
print("\n=== _update_ptb_prep body start ===")
for i in range(1931, min(1960, len(lines))):
    print(f"{i+1:4}: {lines[i]}", end='')

# _update_ptb starts at line 2118 (0-indexed 2117)
print("\n=== _update_ptb body start ===")
for i in range(2117, min(2145, len(lines))):
    print(f"{i+1:4}: {lines[i]}", end='')
