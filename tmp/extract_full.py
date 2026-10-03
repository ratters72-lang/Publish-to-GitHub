with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

idx = content.find('def _update_post_ruck_open_play')
section = content[idx:idx+10000]
print(section)
