with open('world/open_play_engine.py', 'rb') as f:
    data = f.read()

# Find lines around apply_physics_forces = False (line ~2424)
lines = data.split(b'\n')

# Print lines 2420-2440
for i in range(2420, min(2440, len(lines))):
    line = lines[i].decode('utf-8', errors='replace')
    # Filter non-ASCII
    safe = ''.join(ch if ord(ch) < 128 else '?' for ch in line)
    print(f"{i:4d}: {safe}")
