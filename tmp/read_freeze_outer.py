with open('world/open_play_engine.py', 'rb') as f:
    data = f.read()

lines = data.split(b'\n')

# Print lines 2405-2425 to see the outer freeze loop guard
for i in range(2405, min(2425, len(lines))):
    line = lines[i].decode('utf-8', errors='replace')
    safe = ''.join(ch if ord(ch) < 128 else '?' for ch in line)
    print(f"{i:4d}: {safe}")
