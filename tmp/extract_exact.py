with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Search for the post_ruck section
idx = content.find('def _update_post_ruck_open_play')
if idx == -1:
    print("ERROR: _update_post_ruck_open_play not found")
else:
    # Show the lines after
    section = content[idx:idx+3000]
    print(section)
