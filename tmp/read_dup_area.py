with open('world/open_play_engine.py', 'rb') as f:
    data = f.read()

lines = data.split(b'\n')

# Wider context around the duplicate retreat blocks
for i in range(2390, min(2440, len(lines))):
    line = lines[i].decode('utf-8', errors='replace')
    safe = ''.join(ch if ord(ch) < 128 else '?' for ch in line)
    print(f"{i+1:4d}: {safe}")
