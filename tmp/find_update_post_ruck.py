with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    data = f.read()

# Find _update_post_ruck_open_play
idx = data.find('def _update_post_ruck_open_play')
if idx >= 0:
    # Print from there for 500 chars
    print(data[idx:idx+5000])
