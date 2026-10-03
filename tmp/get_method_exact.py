with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    data = f.read()

idx = data.find('def _update_post_ruck_open_play')
if idx < 0:
    print("METHOD NOT FOUND")
else:
    # Find end: next '    def ' at same indent level or end of class
    rest = data[idx+len('def _update_post_ruck_open_play'):]
    # Find next method at class level
    end = len(data)
    for marker in ['\n    def ', '\n    # ═════']:
        pos = data.find(marker, idx + 1)
        if pos > 0 and pos < end:
            end = pos
    method_text = data[idx:end].rstrip()
    print(f"LENGTH={len(method_text)}")
    print(method_text)
