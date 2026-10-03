with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    data = f.read()

idx = data.find('def _update_post_ruck_open_play')
if idx >= 0:
    section = data[idx:idx+5000]
    # Print only ASCII-safe part
    for ch in section:
        if ord(ch) < 128:
            print(ch, end='')
        else:
            print('?', end='')
