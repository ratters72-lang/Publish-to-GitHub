with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

idx = content.find('def _update_post_ruck_open_play')
section = content[idx:idx+8000]
with open('tmp/post_ruck_section.txt', 'w', encoding='utf-8') as out:
    out.write(section)
print("Saved to tmp/post_ruck_section.txt")
