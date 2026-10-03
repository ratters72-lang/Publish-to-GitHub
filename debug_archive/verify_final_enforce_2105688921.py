with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
print("=== _enforce_attack_directions ===")
for i in range(2179, 2215):
    if i < len(lines):
        print(f"{i+1}: {lines[i]}", end='')
