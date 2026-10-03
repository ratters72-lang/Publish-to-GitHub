with open('world/open_play_engine.py', 'rb') as f:
    raw = f.read()

# Find the method start
idx = raw.find(b'def _update_post_ruck_open_play')
if idx >= 0:
    section = raw[idx:idx+5500]
    # Decode with replacement for non-ASCII
    text = section.decode('utf-8', errors='replace')
    for ch in text:
        if ord(ch) < 128 or ch == '\n':
            print(ch, end='')
        elif ch == '\t':
            print('    ', end='')
        else:
            print('?', end='')
