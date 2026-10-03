with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Check _prepare_release (should be ~line 1560 area)
print("=== Around _prepare_release() ===")
for i in range(1555, min(1575, len(lines))):
    print(f"{i+1:5d}: {lines[i]}", end='')

print("\n=== Around _update_open_play() ===")
# Find _update_open_play
for i, line in enumerate(lines):
    if 'def _update_open_play' in line:
        for j in range(i, min(i+20, len(lines))):
            print(f"{j+1:5d}: {lines[j]}", end='')
        break
