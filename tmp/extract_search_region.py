with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    data = f.read()

# Find the method
idx = data.find('def _update_post_ruck_open_play')
if idx < 0:
    print("METHOD NOT FOUND")
else:
    # Find end of method - next method at class level
    end = data.find('\n    def ', idx + 5)
    if end < 0:
        end = len(data)
    method = data[idx:end]
    print(repr(method))
