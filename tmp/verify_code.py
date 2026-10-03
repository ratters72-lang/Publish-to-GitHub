with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    data = f.read()

idx = data.find('def _update_post_ruck_open_play')
if idx < 0:
    print("NOT FOUND")
else:
    end = data.find('\n    def ', idx + 5)
    if end < 0:
        end = len(data)
    method = data[idx:end]
    print(method)
